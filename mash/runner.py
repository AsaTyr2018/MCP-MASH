from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import settings
from .db import db
from .mailbridge_client import MailbridgeClient
from .runtime_config import runtime_config
from .scripts import get_script, parse_script


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log_path(run_id: int, script_id: str) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    directory = settings.runs_dir / day
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"run_{run_id:06d}_{script_id}.jsonl"


def _write_log(path: Path, level: str, event: str, **payload: Any) -> None:
    row = {"ts": utc_now(), "level": level, "event": event, **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _finish_run(
    run_id: int,
    started: float,
    status: str,
    matched_count: int,
    action_count: int,
    skipped_count: int,
    error_message: str,
) -> dict[str, Any]:
    duration_ms = int((time.perf_counter() - started) * 1000)
    with db() as conn:
        conn.execute(
            """
            UPDATE runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP, duration_ms = ?,
                matched_count = ?, action_count = ?, skipped_count = ?, error_message = ?
            WHERE id = ?
            """,
            (status, duration_ms, matched_count, action_count, skipped_count, error_message, run_id),
        )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row)


CALENDAR_ACTIONS = {
    "create_calendar_event",
    "calendar_event",
    "calendar_create",
    "create_event",
    "calendar_from_delivery",
    "create_calendar_events_from_mail",
}
FORWARD_ACTIONS = {"create_forward_draft", "forward_draft", "forward"}


def _resolve_account_id(mailbridge: MailbridgeClient, account_name: str) -> int:
    config_snapshot = runtime_config.snapshot()
    mailbridge_account_name = (config_snapshot.account_aliases or {}).get(account_name, account_name)
    return mailbridge.account_id_by_name(mailbridge_account_name)


def _render_template(template: str, message: dict[str, Any]) -> str:
    values = {
        "subject": str(message.get("subject") or ""),
        "sender": str(message.get("sender") or ""),
        "sent_at": str(message.get("sent_at") or ""),
        "folder": str(message.get("folder") or ""),
        "message_id": str(message.get("id") or ""),
    }
    try:
        return template.format(**values)
    except Exception:
        return template


def _parse_delivery_window(message: dict[str, Any]) -> tuple[str, str] | None:
    snippet = str(message.get("snippet") or "")
    match = re.search(r"Zustellung\s+heute\s+([0-9]{1,2})(?::([0-9]{2}))?h?\s*-\s*([0-9]{1,2})(?::([0-9]{2}))?h?", snippet, re.IGNORECASE)
    if not match:
        return None
    sent_at = str(message.get("sent_at") or "")
    try:
        base = datetime.fromisoformat(sent_at.replace("Z", "+00:00")).astimezone(ZoneInfo(settings.timezone))
    except Exception:
        base = datetime.now(ZoneInfo(settings.timezone))
    start_hour = int(match.group(1))
    start_minute = int(match.group(2) or "0")
    end_hour = int(match.group(3))
    end_minute = int(match.group(4) or "0")
    start = base.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = base.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if end <= start:
        end += timedelta(hours=1)
    return start.isoformat(), end.isoformat()


def _calendar_event_payload(action: dict[str, Any], message: dict[str, Any]) -> dict[str, Any] | None:
    action_type = str(action.get("type", ""))
    starts_at = str(action.get("starts_at") or "").strip()
    ends_at = str(action.get("ends_at") or "").strip()
    if not starts_at and action_type in {"calendar_from_delivery", "create_calendar_events_from_mail"}:
        window = _parse_delivery_window(message)
        if window:
            starts_at, ends_at = window
    if not starts_at:
        return None
    subject = str(message.get("subject") or "")
    title_template = str(action.get("title") or action.get("summary") or "Mail: {subject}")
    description_template = str(
        action.get("description")
        or "Created by MCP-MASH from mail {message_id}.\n\nFrom: {sender}\nSubject: {subject}\nSent: {sent_at}"
    )
    return {
        "title": _render_template(title_template, message),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "location": _render_template(str(action.get("location") or ""), message),
        "description": _render_template(description_template, message),
        "attendees": str(action.get("attendees") or ""),
        "profile_id": action.get("profile_id"),
        "subject": subject,
    }


def run_script(script_id: str, *, dry_run: bool = False, reason: str = "manual") -> dict[str, Any]:
    script = get_script(script_id)
    data = parse_script(script["content"])
    started = time.perf_counter()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO runs (script_id, status, dry_run, log_path) VALUES (?, ?, ?, ?)",
            (script_id, "running", int(dry_run), ""),
        )
        run_id = int(cur.lastrowid)
        log_path = _log_path(run_id, script_id)
        conn.execute("UPDATE runs SET log_path = ? WHERE id = ?", (str(log_path), run_id))

    status = "ok"
    error_message = ""
    action_count = 0
    skipped_count = 0
    matched_count = 0
    try:
        _write_log(log_path, "info", "run_started", run_id=run_id, script_id=script_id, dry_run=dry_run, reason=reason)
        if not dry_run and not bool(script.get("validated")):
            status = "blocked"
            error_message = "script is not validated; run a dry run, show the result to the user, then call approve_script_validation with user_ok=true"
            _write_log(
                log_path,
                "warning",
                "script_not_validated",
                message=error_message,
                validation_required=True,
            )
            _write_log(log_path, "info", "run_finished", status=status, matched_count=matched_count, action_count=action_count, skipped_count=skipped_count)
            return _finish_run(run_id, started, status, matched_count, action_count, skipped_count, error_message)
        mailbridge = MailbridgeClient(runtime_config.snapshot())
        if not mailbridge.configured():
            _write_log(
                log_path,
                "info",
                "mailbridge_not_connected",
                message="Mailbridge execution adapter is not configured. This run records planned actions only.",
            )
            mailbridge = None
        account_name = str(data.get("account", ""))
        query = str(data.get("query", ""))
        limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
        max_messages = max(1, min(int(limits.get("max_messages") or 100), 500))
        matches: list[dict[str, Any]] = []
        account_id = 0
        if mailbridge:
            config_snapshot = runtime_config.snapshot()
            mailbridge_account_name = (config_snapshot.account_aliases or {}).get(account_name, account_name)
            account_id = mailbridge.account_id_by_name(mailbridge_account_name)
            if runtime_config.snapshot().mailbridge_sync_before_run:
                sync_result = mailbridge.sync_account(account_id, limit=max_messages)
                _write_log(log_path, "info", "mailbridge_sync", account=account_name, mailbridge_account=mailbridge_account_name, account_id=account_id, result=str(sync_result)[:1000])
            matches = mailbridge.search_mail(account_id, query, limit=max_messages)
            matched_count = len(matches)
            _write_log(log_path, "info", "mail_search", account=account_name, mailbridge_account=mailbridge_account_name, account_id=account_id, query=query, matched=matched_count)
            if matched_count == 0:
                no_match_behavior = str(data.get("on_no_matches", "sleep")).strip().lower() or "sleep"
                _write_log(
                    log_path,
                    "info",
                    "no_matches_sleep" if no_match_behavior == "sleep" else "no_matches",
                    account=account_name,
                    mailbridge_account=mailbridge_account_name,
                    query=query,
                    behavior=no_match_behavior,
                    message="No matching messages found; run completed without actions.",
                )
                _write_log(log_path, "info", "run_finished", status=status, matched_count=matched_count, action_count=action_count, skipped_count=skipped_count)
                return _finish_run(run_id, started, status, matched_count, action_count, skipped_count, error_message)
        actions = data.get("actions") if isinstance(data.get("actions"), list) else []
        for index, action in enumerate(actions):
            action_type = str(action.get("type", ""))
            action_count += 1
            if dry_run or not mailbridge:
                event = "would_execute_action" if dry_run else "action_coming_soon"
                if not dry_run:
                    skipped_count += 1
                _write_log(
                    log_path,
                    "info",
                    event,
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    query=query,
                    matched=matched_count,
                    details={key: value for key, value in action.items() if key != "body"},
                )
                continue
            if action_type == "move":
                message_ids = [int(item["id"]) for item in matches if item.get("id")]
                if not message_ids:
                    skipped_count += 1
                    _write_log(log_path, "info", "action_skipped", index=index, action_type=action_type, reason="no matching messages")
                    continue
                result = mailbridge.move_messages(
                    account_id,
                    message_ids,
                    str(action.get("folder", "")),
                    source_folder=str(action.get("source_folder", "")),
                )
                _write_log(log_path, "info", "action_executed", index=index, action_type=action_type, result=str(result)[:2000])
            elif action_type in CALENDAR_ACTIONS:
                target_account = str(action.get("target_account") or action.get("calendar_account") or "").strip()
                target_account_id = _resolve_account_id(mailbridge, target_account)
                created = 0
                skipped = 0
                results = []
                for message in matches:
                    payload = _calendar_event_payload(action, message)
                    if not payload:
                        skipped += 1
                        continue
                    result = mailbridge.create_calendar_event(
                        target_account_id,
                        payload["title"],
                        payload["starts_at"],
                        ends_at=payload["ends_at"],
                        location=payload["location"],
                        description=payload["description"],
                        attendees=payload["attendees"],
                        profile_id=int(payload["profile_id"]) if payload.get("profile_id") is not None else None,
                    )
                    created += 1
                    results.append(result)
                skipped_count += skipped
                _write_log(
                    log_path,
                    "info",
                    "action_executed",
                    index=index,
                    action_type=action_type,
                    target_account=target_account,
                    target_account_id=target_account_id,
                    created=created,
                    skipped=skipped,
                    result=str(results[:5])[:2000],
                )
            elif action_type == "list_attachments":
                inspected = []
                for message in matches:
                    inspected.append(mailbridge.list_attachments(int(message["id"])))
                _write_log(
                    log_path,
                    "info",
                    "action_executed",
                    index=index,
                    action_type=action_type,
                    inspected=len(inspected),
                    result=str(inspected[:10])[:2000],
                )
            elif action_type == "read_attachment":
                inspected = []
                max_bytes = max(1, min(int(action.get("max_bytes") or 250000), 5000000))
                filename = str(action.get("filename") or "")
                attachment_index = int(action.get("attachment_index") or 0)
                for message in matches:
                    result = mailbridge.get_attachment(int(message["id"]), attachment_index=attachment_index, filename=filename, max_bytes=max_bytes)
                    redacted = dict(result)
                    if "content_base64" in redacted:
                        redacted["content_base64"] = f"<base64 {len(result['content_base64'])} chars>"
                    inspected.append(redacted)
                _write_log(
                    log_path,
                    "info",
                    "action_executed",
                    index=index,
                    action_type=action_type,
                    inspected=len(inspected),
                    result=str(inspected[:10])[:2000],
                )
            elif action_type in FORWARD_ACTIONS:
                to_recipients = str(action.get("to_recipients") or action.get("to") or "").strip()
                note = str(action.get("note") or "")
                cc_recipients = str(action.get("cc_recipients") or action.get("cc") or "")
                bcc_recipients = str(action.get("bcc_recipients") or action.get("bcc") or "")
                drafts = []
                for message in matches:
                    drafts.append(
                        mailbridge.create_forward_draft(
                            int(message["id"]),
                            to_recipients,
                            note=note,
                            cc_recipients=cc_recipients,
                            bcc_recipients=bcc_recipients,
                        )
                    )
                _write_log(
                    log_path,
                    "info",
                    "action_executed",
                    index=index,
                    action_type=action_type,
                    created=len(drafts),
                    result=str(drafts[:5])[:2000],
                )
            else:
                event = "action_coming_soon"
                skipped_count += 1
                _write_log(
                    log_path,
                    "info",
                    event,
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    query=query,
                    matched=matched_count,
                    details={key: value for key, value in action.items() if key != "body"},
                )
        _write_log(log_path, "info", "run_finished", status=status, matched_count=matched_count, action_count=action_count, skipped_count=skipped_count)
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        _write_log(log_path, "error", "run_failed", error=error_message)
    return _finish_run(run_id, started, status, matched_count, action_count, skipped_count, error_message)


def list_runs(limit: int = 20, script_id: str = "") -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    with db() as conn:
        if script_id:
            rows = conn.execute(
                "SELECT * FROM runs WHERE script_id = ? ORDER BY id DESC LIMIT ?",
                (script_id, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
    return [dict(row) for row in rows]


def get_run(run_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise ValueError("run not found")
    return dict(row)


def get_run_log(run_id: int, max_lines: int = 200) -> dict[str, Any]:
    run = get_run(run_id)
    path = Path(run["log_path"])
    if not path.exists():
        return {"run": run, "lines": []}
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(max_lines, 1000)) :]
    return {"run": run, "lines": lines}
