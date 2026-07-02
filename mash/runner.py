from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import settings
from .db import db, get_config
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
FORWARD_DRAFT_ACTIONS = {
    "create_forward_draft",
    "forward_draft",
    "forward",
    "forward_mail",
    "forward_email",
    "forward_to",
    "forward_message",
    "document_forward",
}
FORWARD_ATTACHMENT_ACTIONS = {
    "forward_attachments",
    "forward_pdf",
    "send_attachments",
    "extract_attachments",
    "forward_message_with_attachments",
    "forward_matching_attachments",
}
FORWARD_ACTIONS = FORWARD_DRAFT_ACTIONS | FORWARD_ATTACHMENT_ACTIONS
REPORT_ACTIONS = {"send_report"}
MAIL_FLAG_ACTIONS = {"mark_read", "mark_unread"}
LABEL_ACTIONS = {"add_label", "remove_label"}
REPLY_ACTIONS = {"draft_reply", "send_reply"}


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


def _message_ids(matches: list[dict[str, Any]]) -> list[int]:
    return [int(item["id"]) for item in matches if item.get("id")]


def _label_name(action: dict[str, Any]) -> str:
    return str(action.get("label") or action.get("folder") or action.get("target_folder") or "").strip()


def _reply_recipient(message: dict[str, Any]) -> str:
    sender = str(message.get("sender") or "").strip()
    _, address = parseaddr(sender)
    return address or sender


def _reply_subject(message: dict[str, Any], action: dict[str, Any]) -> str:
    subject_template = str(action.get("subject") or "").strip()
    if subject_template:
        return _render_template(subject_template, message)
    subject = str(message.get("subject") or "").strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _reply_body(message: dict[str, Any], action: dict[str, Any]) -> str:
    body_template = str(action.get("body") or action.get("body_text") or action.get("reply") or "").strip()
    if body_template:
        return _render_template(body_template, message)
    return ""


def _attachment_extensions(action: dict[str, Any]) -> list[str]:
    raw = action.get("attachment_extensions")
    if raw is None and str(action.get("type", "")) == "forward_pdf":
        raw = [".pdf"]
    if raw is None:
        raw = action.get("extensions")
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(item).strip() for item in raw]
    else:
        items = []
    return [item.lower() if item.startswith(".") else f".{item.lower()}" for item in items if item]


def _matching_attachments(attachments_result: dict[str, Any], extensions: list[str]) -> list[dict[str, Any]]:
    attachments = attachments_result.get("attachments")
    if not isinstance(attachments, list):
        return []
    if not extensions:
        return [item for item in attachments if isinstance(item, dict)]
    matches = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").lower()
        if any(filename.endswith(extension) for extension in extensions):
            matches.append(item)
    return matches


def _dedupe_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in attachments:
        key = str(item.get("filename") or item.get("index") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _resolve_report_recipient(action: dict[str, Any]) -> str:
    recipient = str(action.get("to") or action.get("to_recipients") or "").strip()
    if recipient == "owner":
        recipient = get_config("default_report_recipient", "owner").strip()
    if not recipient or recipient == "owner":
        raise ValueError("report recipient is not configured; set a concrete recipient or initialize default_report_recipient")
    return recipient


def _resolve_report_account(
    mailbridge: MailbridgeClient,
    action: dict[str, Any],
    default_account_name: str,
    default_account_id: int,
) -> tuple[str, int]:
    account_name = str(action.get("send_account") or action.get("from_account") or default_account_name).strip()
    if not account_name:
        return default_account_name, default_account_id
    if account_name == default_account_name:
        return default_account_name, default_account_id
    return account_name, _resolve_account_id(mailbridge, account_name)


def _message_date_key(message: dict[str, Any]) -> str:
    sent_at = str(message.get("sent_at") or "")
    if not sent_at:
        return "unknown"
    try:
        return datetime.fromisoformat(sent_at.replace("Z", "+00:00")).astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    except Exception:
        return sent_at[:10] or "unknown"


def _recent_runs_for_report(window_hours: int, limit: int, *, exclude_run_id: int = 0) -> list[dict[str, Any]]:
    safe_window = max(1, min(int(window_hours), 24 * 90))
    safe_limit = max(1, min(int(limit), 500))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT r.*, s.name AS script_name, s.enabled AS script_enabled, s.schedule AS script_schedule, s.account AS script_account
            FROM runs r
            LEFT JOIN scripts s ON s.id = r.script_id
            WHERE r.id != ? AND r.started_at >= datetime('now', ?)
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (exclude_run_id, f"-{safe_window} hours", safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _run_log_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    path = Path(str(run.get("log_path") or ""))
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _run_highlights(run: dict[str, Any]) -> list[str]:
    highlights = []
    for event in _run_log_events(run):
        name = str(event.get("event") or "")
        action_type = str(event.get("action_type") or "")
        if name == "mail_search":
            highlights.append(f"search matched {event.get('matched', 0)} for `{event.get('query', '')}`")
        elif name == "no_matches_sleep":
            highlights.append("no matching messages; slept until next schedule")
        elif name == "action_executed":
            if action_type == "move":
                highlights.append(f"moved/handled messages: {event.get('result', '')}")
            elif action_type in FORWARD_ACTIONS:
                highlights.append(
                    f"created {event.get('created', 0)} forward draft(s), "
                    f"matched attachments {event.get('matched_attachments', 0)}"
                )
            elif action_type in CALENDAR_ACTIONS:
                highlights.append(f"created {event.get('created', 0)} calendar event(s), skipped {event.get('skipped', 0)}")
            else:
                highlights.append(f"executed {action_type or 'action'}")
        elif name == "report_draft_created":
            highlights.append(f"created report draft #{event.get('draft_id')} to {event.get('to', '')}")
        elif name == "would_forward_attachments":
            selected = []
            for message in event.get("selected_messages") or []:
                if isinstance(message, dict):
                    selected.extend(str(item) for item in message.get("selected_attachments") or [])
            if selected:
                highlights.append("would forward attachments: " + ", ".join(selected[:5]))
        elif name == "script_not_validated":
            highlights.append("blocked: script validation required")
        elif name == "run_failed":
            highlights.append(f"failed: {event.get('error', '')}")
    return highlights[:4]


def _build_job_overview_report(data: dict[str, Any], *, current_run_id: int = 0) -> str:
    now_local = datetime.now(ZoneInfo(settings.timezone)).replace(microsecond=0)
    send_actions = [action for action in data.get("actions", []) if isinstance(action, dict) and str(action.get("type", "")) == "send_report"]
    options = send_actions[0] if send_actions else {}
    window_hours = int(options.get("window_hours") or options.get("hours") or 24)
    limit = int(options.get("max_runs") or 100)
    runs = _recent_runs_for_report(window_hours, limit, exclude_run_id=current_run_id)

    status_counts: dict[str, int] = {}
    totals = {"matched": 0, "actions": 0, "skipped": 0}
    by_script: dict[str, dict[str, Any]] = {}
    for run in runs:
        status = str(run.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        totals["matched"] += int(run.get("matched_count") or 0)
        totals["actions"] += int(run.get("action_count") or 0)
        totals["skipped"] += int(run.get("skipped_count") or 0)
        script_id = str(run.get("script_id") or "")
        item = by_script.setdefault(
            script_id,
            {
                "name": str(run.get("script_name") or script_id),
                "runs": 0,
                "ok": 0,
                "blocked": 0,
                "error": 0,
                "matched": 0,
                "actions": 0,
                "skipped": 0,
                "last_status": status,
                "last_run_id": run.get("id"),
                "last_started": run.get("started_at"),
                "highlights": [],
            },
        )
        item["runs"] += 1
        item["matched"] += int(run.get("matched_count") or 0)
        item["actions"] += int(run.get("action_count") or 0)
        item["skipped"] += int(run.get("skipped_count") or 0)
        if status in {"ok", "blocked", "error"}:
            item[status] += 1
        if int(run.get("id") or 0) >= int(item.get("last_run_id") or 0):
            item["last_status"] = status
            item["last_run_id"] = run.get("id")
            item["last_started"] = run.get("started_at")
        for highlight in _run_highlights(run):
            if highlight not in item["highlights"]:
                item["highlights"].append(highlight)

    lines = [
        "MCP-MASH Job Overview",
        "",
        f"Generated: {now_local.isoformat()}",
        f"Window: last {window_hours} hour(s)",
        f"Runs inspected: {len(runs)}",
        "",
        "Summary:",
        f"- OK: {status_counts.get('ok', 0)}",
        f"- Blocked: {status_counts.get('blocked', 0)}",
        f"- Errors: {status_counts.get('error', 0)}",
        f"- Running/other: {len(runs) - status_counts.get('ok', 0) - status_counts.get('blocked', 0) - status_counts.get('error', 0)}",
        f"- Total matched messages: {totals['matched']}",
        f"- Total actions: {totals['actions']}",
        f"- Total skipped: {totals['skipped']}",
        "",
        "Jobs:",
    ]
    if not by_script:
        lines.append("- No MASH runs in this window.")
    for script_id, item in sorted(by_script.items(), key=lambda pair: str(pair[1].get("last_started") or ""), reverse=True):
        lines.extend(
            [
                f"- {item['name']} (`{script_id}`)",
                f"  Runs: {item['runs']} | OK: {item['ok']} | Blocked: {item['blocked']} | Errors: {item['error']}",
                f"  Processed: matched {item['matched']}, actions {item['actions']}, skipped {item['skipped']}",
                f"  Last: run #{item['last_run_id']} at {item['last_started']} ({item['last_status']})",
            ]
        )
        for highlight in item["highlights"][:3]:
            lines.append(f"  - {highlight}")

    lines.extend(["", "Generated by MCP-MASH."])
    return "\n".join(lines)


def _build_mail_report(data: dict[str, Any], matches: list[dict[str, Any]], account_name: str, query: str) -> str:
    now_local = datetime.now(ZoneInfo(settings.timezone)).replace(microsecond=0)
    send_actions = [action for action in data.get("actions", []) if isinstance(action, dict) and str(action.get("type", "")) == "send_report"]
    options = send_actions[0] if send_actions else {}
    max_examples = max(0, min(int(options.get("max_examples") or 10), 50))
    include_examples = bool(options.get("include_examples", True))

    by_sender: dict[str, int] = {}
    by_day: dict[str, int] = {}
    unread = 0
    attachment_messages = 0
    for message in matches:
        sender = str(message.get("sender") or "(unknown sender)")
        by_sender[sender] = by_sender.get(sender, 0) + 1
        day = _message_date_key(message)
        by_day[day] = by_day.get(day, 0) + 1
        flags = str(message.get("flags") or "").lower()
        if "\\seen" not in flags and "seen" not in flags:
            unread += 1
        if str(message.get("attachment_names") or ""):
            attachment_messages += 1

    lines = [
        "MCP-MASH Mail Report",
        "",
        f"Generated: {now_local.isoformat()}",
        f"Script: {data.get('id', '')} - {data.get('name', '')}",
        f"Account: {account_name}",
        f"Query: {query}",
        f"Matched messages: {len(matches)}",
        f"Unread/unknown-unread messages: {unread}",
        f"Messages with indexed attachments: {attachment_messages}",
        "",
        "Top senders:",
    ]
    if by_sender:
        for sender, count in sorted(by_sender.items(), key=lambda item: (-item[1], item[0].lower()))[:10]:
            lines.append(f"- {count} x {sender}")
    else:
        lines.append("- none")

    lines.extend(["", "Messages by day:"])
    if by_day:
        for day, count in sorted(by_day.items(), reverse=True)[:14]:
            lines.append(f"- {day}: {count}")
    else:
        lines.append("- none")

    if include_examples and max_examples:
        lines.extend(["", "Examples:"])
        for message in matches[:max_examples]:
            lines.append(
                f"- #{message.get('id')} | {message.get('sent_at') or ''} | "
                f"{message.get('sender') or ''} | {message.get('subject') or ''}"
            )

    lines.extend(["", "Generated by MCP-MASH through Mailbridge."])
    return "\n".join(lines)


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
            if action_type == "summarize":
                report_body = _build_job_overview_report(data, current_run_id=run_id)
                _write_log(
                    log_path,
                    "info",
                    "summary_created" if not dry_run else "would_create_summary",
                    index=index,
                    action_type=action_type,
                    matched=matched_count,
                    report_preview=report_body[:4000],
                )
                continue
            if dry_run and mailbridge and action_type in REPORT_ACTIONS:
                recipient = _resolve_report_recipient(action)
                report_account_name, report_account_id = _resolve_report_account(mailbridge, action, account_name, account_id)
                subject = str(action.get("subject") or "MCP-MASH mail report")
                report_body = _build_job_overview_report(data, current_run_id=run_id)
                _write_log(
                    log_path,
                    "info",
                    "would_create_report_draft",
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    account_id=account_id,
                    send_account=report_account_name,
                    send_account_id=report_account_id,
                    to=recipient,
                    cc=str(action.get("cc") or action.get("cc_recipients") or ""),
                    bcc=str(action.get("bcc") or action.get("bcc_recipients") or ""),
                    subject=subject,
                    matched=matched_count,
                    body_preview=report_body[:4000],
                    send_requested=bool(action.get("send", True)),
                    automation_consent_id=action.get("automation_consent_id"),
                )
                continue
            if dry_run and mailbridge and action_type in FORWARD_ATTACHMENT_ACTIONS:
                selected_messages = []
                skipped = 0
                selected_total = 0
                for message in matches:
                    attachment_result = mailbridge.list_attachments(int(message["id"]))
                    matching = _matching_attachments(attachment_result, _attachment_extensions(action))
                    if bool(action.get("dedupe")):
                        matching = _dedupe_attachments(matching)
                    if not matching:
                        skipped += 1
                    filenames = [str(item.get("filename") or f"attachment-{item.get('index')}") for item in matching]
                    selected_total += len(filenames)
                    selected_messages.append(
                        {
                            "message_id": int(message["id"]),
                            "subject": str(message.get("subject") or ""),
                            "sender": str(message.get("sender") or ""),
                            "selected_attachments": filenames,
                        }
                    )
                skipped_count += skipped
                _write_log(
                    log_path,
                    "info",
                    "would_forward_attachments",
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    query=query,
                    matched=matched_count,
                    selected_attachment_count=selected_total,
                    skipped=skipped,
                    to=str(action.get("to_recipients") or action.get("to") or ""),
                    selected_messages=selected_messages[:20],
                    details={key: value for key, value in action.items() if key != "body"},
                )
                continue
            if dry_run and mailbridge and action_type in MAIL_FLAG_ACTIONS | {"trash"} | LABEL_ACTIONS:
                _write_log(
                    log_path,
                    "info",
                    "would_execute_action",
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    account_id=account_id,
                    query=query,
                    matched=matched_count,
                    message_ids=_message_ids(matches)[:50],
                    label=_label_name(action),
                    target_folder=str(action.get("folder") or action.get("trash_folder") or action.get("target_folder") or ""),
                    details={key: value for key, value in action.items() if key != "body"},
                )
                continue
            if dry_run and mailbridge and action_type in REPLY_ACTIONS:
                previews = []
                for message in matches[:20]:
                    previews.append(
                        {
                            "message_id": int(message["id"]),
                            "to": _reply_recipient(message),
                            "subject": _reply_subject(message, action),
                            "body_preview": _reply_body(message, action)[:500],
                        }
                    )
                _write_log(
                    log_path,
                    "info",
                    "would_create_reply_drafts",
                    index=index,
                    action_type=action_type,
                    account=account_name,
                    account_id=account_id,
                    matched=matched_count,
                    send_requested=action_type == "send_reply",
                    automation_consent_id=action.get("automation_consent_id"),
                    previews=previews,
                )
                continue
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
            elif action_type in MAIL_FLAG_ACTIONS:
                message_ids = _message_ids(matches)
                if not message_ids:
                    skipped_count += 1
                    _write_log(log_path, "info", "action_skipped", index=index, action_type=action_type, reason="no matching messages")
                    continue
                result = mailbridge.mark_messages(
                    account_id,
                    message_ids,
                    read=action_type == "mark_read",
                    source_folder=str(action.get("source_folder", "")),
                )
                _write_log(log_path, "info", "action_executed", index=index, action_type=action_type, result=str(result)[:2000])
            elif action_type == "trash":
                message_ids = _message_ids(matches)
                if not message_ids:
                    skipped_count += 1
                    _write_log(log_path, "info", "action_skipped", index=index, action_type=action_type, reason="no matching messages")
                    continue
                result = mailbridge.trash_messages(
                    account_id,
                    message_ids,
                    trash_folder=str(action.get("trash_folder") or action.get("folder") or action.get("target_folder") or "Trash"),
                    source_folder=str(action.get("source_folder", "")),
                )
                _write_log(log_path, "info", "action_executed", index=index, action_type=action_type, result=str(result)[:2000])
            elif action_type == "add_label":
                label = _label_name(action)
                message_ids = _message_ids(matches)
                if not message_ids:
                    skipped_count += 1
                    _write_log(log_path, "info", "action_skipped", index=index, action_type=action_type, reason="no matching messages")
                    continue
                result = mailbridge.add_label_to_messages(
                    account_id,
                    message_ids,
                    label,
                    source_folder=str(action.get("source_folder", "")),
                )
                _write_log(log_path, "info", "action_executed", index=index, action_type=action_type, label=label, result=str(result)[:2000])
            elif action_type == "remove_label":
                label = _label_name(action)
                message_ids = _message_ids(matches)
                if not message_ids:
                    skipped_count += 1
                    _write_log(log_path, "info", "action_skipped", index=index, action_type=action_type, reason="no matching messages")
                    continue
                result = mailbridge.remove_label_from_messages(account_id, message_ids, label)
                _write_log(log_path, "info", "action_executed", index=index, action_type=action_type, label=label, result=str(result)[:2000])
            elif action_type in REPLY_ACTIONS:
                drafts = []
                sent = []
                skipped = 0
                for message in matches:
                    recipient = _reply_recipient(message)
                    if not recipient:
                        skipped += 1
                        continue
                    draft = mailbridge.create_draft(
                        account_id,
                        recipient,
                        _reply_subject(message, action),
                        _reply_body(message, action),
                        cc_recipients=str(action.get("cc") or action.get("cc_recipients") or ""),
                        bcc_recipients=str(action.get("bcc") or action.get("bcc_recipients") or ""),
                        in_reply_to_message_id=int(message["id"]),
                    )
                    drafts.append(draft)
                    if action_type == "send_reply":
                        consent = action.get("automation_consent_id")
                        sent.append(
                            mailbridge.send_draft(
                                int(draft["id"]),
                                automation_consent_id=int(consent) if consent not in {None, ""} else None,
                            )
                        )
                skipped_count += skipped
                _write_log(
                    log_path,
                    "info",
                    "reply_drafts_created" if action_type == "draft_reply" else "reply_drafts_sent",
                    index=index,
                    action_type=action_type,
                    created=len(drafts),
                    sent=len(sent),
                    skipped=skipped,
                    result=str({"drafts": drafts[:5], "sent": sent[:5]})[:2000],
                )
            elif action_type in REPORT_ACTIONS:
                recipient = _resolve_report_recipient(action)
                report_account_name, report_account_id = _resolve_report_account(mailbridge, action, account_name, account_id)
                subject = str(action.get("subject") or "MCP-MASH mail report")
                report_body = _build_job_overview_report(data, current_run_id=run_id)
                draft = mailbridge.create_draft(
                    report_account_id,
                    recipient,
                    subject,
                    report_body,
                    cc_recipients=str(action.get("cc") or action.get("cc_recipients") or ""),
                    bcc_recipients=str(action.get("bcc") or action.get("bcc_recipients") or ""),
                )
                send_result: dict[str, Any] | None = None
                if bool(action.get("send", True)):
                    consent = action.get("automation_consent_id")
                    send_result = mailbridge.send_draft(
                        int(draft["id"]),
                        automation_consent_id=int(consent) if consent not in {None, ""} else None,
                    )
                _write_log(
                    log_path,
                    "info",
                    "report_draft_created",
                    index=index,
                    action_type=action_type,
                    draft_id=int(draft["id"]),
                    send_account=report_account_name,
                    send_account_id=report_account_id,
                    to=recipient,
                    subject=subject,
                    matched=matched_count,
                    send_result=str(send_result or {"send_requested": False})[:2000],
                )
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
                subject_template = str(action.get("subject") or "")
                drafts = []
                skipped = 0
                inspected_attachments = 0
                for message in matches:
                    attachment_note = ""
                    attachment_indices: list[int] = []
                    attachment_filenames: list[str] = []
                    include_attachments = False
                    if action_type in FORWARD_ATTACHMENT_ACTIONS:
                        attachment_result = mailbridge.list_attachments(int(message["id"]))
                        matching = _matching_attachments(attachment_result, _attachment_extensions(action))
                        if bool(action.get("dedupe")):
                            matching = _dedupe_attachments(matching)
                        inspected_attachments += len(matching)
                        if not matching:
                            skipped += 1
                            continue
                        filenames = [str(item.get("filename") or f"attachment-{item.get('index')}") for item in matching]
                        attachment_indices = [int(item["index"]) for item in matching if item.get("index") is not None]
                        attachment_filenames = filenames
                        include_attachments = True
                        attachment_note = "Matched attachments: " + ", ".join(filenames)
                    rendered_note = _render_template(note, message) if note else ""
                    rendered_subject = ""
                    if subject_template:
                        rendered_subject = _render_template(subject_template, message)
                    if attachment_note:
                        rendered_note = (rendered_note + "\n\n" if rendered_note else "") + attachment_note
                    drafts.append(
                        mailbridge.create_forward_draft(
                            int(message["id"]),
                            to_recipients,
                            note=rendered_note,
                            cc_recipients=cc_recipients,
                            bcc_recipients=bcc_recipients,
                            subject=rendered_subject,
                            attachment_indices=attachment_indices,
                            attachment_filenames=attachment_filenames,
                            include_attachments=include_attachments,
                        )
                    )
                skipped_count += skipped
                _write_log(
                    log_path,
                    "info",
                    "action_executed",
                    index=index,
                    action_type=action_type,
                    created=len(drafts),
                    skipped=skipped,
                    attachment_passthrough=action_type in FORWARD_ATTACHMENT_ACTIONS,
                    matched_attachments=inspected_attachments,
                    message=(
                        "Forward drafts were created with matching attachments copied into the draft."
                        if action_type in FORWARD_ATTACHMENT_ACTIONS
                        else "Forward drafts were created."
                    ),
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
