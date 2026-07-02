from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
