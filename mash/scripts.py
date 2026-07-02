from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from croniter import croniter

from .config import settings
from .db import db, get_config

SCRIPT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,80}$")
KNOWN_ACTIONS = {
    "search",
    "summarize",
    "mark_read",
    "mark_unread",
    "move",
    "trash",
    "draft_reply",
    "send_reply",
    "send_report",
    "add_label",
    "remove_label",
    "create_calendar_event",
    "calendar_event",
    "calendar_create",
    "create_event",
    "calendar_from_delivery",
    "create_calendar_events_from_mail",
    "list_attachments",
    "read_attachment",
    "create_forward_draft",
    "forward_draft",
    "forward",
    "forward_mail",
    "forward_email",
    "forward_to",
    "forward_message",
    "document_forward",
    "forward_attachments",
    "forward_pdf",
    "send_attachments",
    "extract_attachments",
    "forward_message_with_attachments",
    "forward_matching_attachments",
}
CALENDAR_ACTIONS = {
    "create_calendar_event",
    "calendar_event",
    "calendar_create",
    "create_event",
    "calendar_from_delivery",
    "create_calendar_events_from_mail",
}
FORWARD_ACTIONS = {
    "create_forward_draft",
    "forward_draft",
    "forward",
    "forward_mail",
    "forward_email",
    "forward_to",
    "forward_message",
    "document_forward",
    "forward_attachments",
    "forward_pdf",
    "send_attachments",
    "extract_attachments",
    "forward_message_with_attachments",
    "forward_matching_attachments",
}


def allowed_accounts() -> list[str]:
    raw = get_config("allowed_accounts", "[]")
    try:
        data = yaml.safe_load(raw) or []
    except yaml.YAMLError:
        return []
    return [str(item) for item in data if str(item).strip()]


def set_allowed_accounts(accounts: list[str]) -> list[str]:
    clean = sorted({item.strip() for item in accounts if item.strip()})
    from .db import set_config

    set_config("allowed_accounts", yaml.safe_dump(clean, sort_keys=True))
    return clean


def parse_script(content: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("script root must be a mapping")
    return data


def validate_script_content(content: str) -> dict[str, Any]:
    data = parse_script(content)
    errors: list[str] = []
    script_id = str(data.get("id", "")).strip()
    if not SCRIPT_ID_RE.match(script_id):
        errors.append("id must be 2-81 safe characters: letters, numbers, dot, underscore, dash")
    if not str(data.get("name", "")).strip():
        errors.append("name is required")
    account = str(data.get("account", "")).strip()
    if not account:
        errors.append("account is required")
    elif account not in allowed_accounts():
        errors.append(f"account '{account}' is not in the MASH allowlist")
    if not str(data.get("query", "")).strip():
        errors.append("query is required")
    schedule = str(data.get("schedule", "")).strip()
    if schedule and not croniter.is_valid(schedule):
        errors.append("schedule is not a valid cron expression")
    on_no_matches = str(data.get("on_no_matches", "sleep")).strip().lower()
    if on_no_matches and on_no_matches not in {"sleep", "skip", "ok"}:
        errors.append("on_no_matches must be one of: sleep, skip, ok")
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append("actions must be a non-empty list")
    else:
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(f"action {index} must be a mapping")
                continue
            action_type = str(action.get("type", "")).strip()
            if action_type not in KNOWN_ACTIONS:
                errors.append(f"action {index} has unknown type '{action_type}'")
            if action_type == "move" and not str(action.get("folder", "")).strip():
                errors.append(f"action {index} move requires folder")
            if action_type in {"add_label", "remove_label"} and not str(action.get("label") or action.get("folder") or action.get("target_folder") or "").strip():
                errors.append(f"action {index} {action_type} requires label, folder, or target_folder")
            if action_type in {"draft_reply", "send_reply"} and not str(action.get("body") or action.get("body_text") or action.get("reply") or "").strip():
                errors.append(f"action {index} {action_type} requires body, body_text, or reply")
            if action_type in FORWARD_ACTIONS and not str(action.get("to") or action.get("to_recipients") or "").strip():
                errors.append(f"action {index} {action_type} requires to or to_recipients")
            if action_type in CALENDAR_ACTIONS:
                target_account = str(action.get("target_account") or action.get("calendar_account") or "").strip()
                if not target_account:
                    errors.append(f"action {index} {action_type} requires target_account")
                elif target_account not in allowed_accounts():
                    errors.append(f"action {index} target_account '{target_account}' is not in the MASH allowlist")
                if action_type in {"create_calendar_event", "calendar_event", "calendar_create", "create_event"} and not str(action.get("starts_at", "")).strip():
                    errors.append(f"action {index} {action_type} requires starts_at")
            if action_type in {"send_reply", "send_report"} and action.get("mode") != "via_mailbridge_policy":
                errors.append(f"action {index} {action_type} requires mode=via_mailbridge_policy")
            if action_type == "send_report":
                send_account = str(action.get("send_account") or action.get("from_account") or "").strip()
                if send_account and send_account not in allowed_accounts():
                    errors.append(f"action {index} send_account '{send_account}' is not in the MASH allowlist")
    if errors:
        raise ValueError("; ".join(errors))
    return data


def script_path(script_id: str) -> Path:
    return settings.scripts_dir / f"{script_id}.yaml"


def save_script(content: str, *, invalidate_validation: bool = True) -> dict[str, Any]:
    data = validate_script_content(content)
    script_id = str(data["id"])
    path = script_path(script_id)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with db() as conn:
        if invalidate_validation:
            conn.execute(
                """
                INSERT INTO scripts (
                    id, name, enabled, schedule, account, path, updated_at,
                    validated, validated_at, validated_by, validation_run_id, validation_note
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0, '', '', NULL, '')
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    schedule = excluded.schedule,
                    account = excluded.account,
                    path = excluded.path,
                    updated_at = CURRENT_TIMESTAMP,
                    validated = 0,
                    validated_at = '',
                    validated_by = '',
                    validation_run_id = NULL,
                    validation_note = ''
                """,
                (
                    script_id,
                    str(data.get("name", "")).strip(),
                    int(bool(data.get("enabled", False))),
                    str(data.get("schedule", "")).strip(),
                    str(data.get("account", "")).strip(),
                    str(path),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO scripts (id, name, enabled, schedule, account, path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    schedule = excluded.schedule,
                    account = excluded.account,
                    path = excluded.path,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    script_id,
                    str(data.get("name", "")).strip(),
                    int(bool(data.get("enabled", False))),
                    str(data.get("schedule", "")).strip(),
                    str(data.get("account", "")).strip(),
                    str(path),
                ),
            )
    return get_script(script_id)


def get_script(script_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    if not row:
        raise ValueError("script not found")
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["validated"] = bool(result.get("validated", False))
    result["content"] = Path(result["path"]).read_text(encoding="utf-8")
    return result


def list_scripts() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM scripts ORDER BY id").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["validated"] = bool(item.get("validated", False))
        result.append(item)
    return result


def set_script_enabled(script_id: str, enabled: bool) -> dict[str, Any]:
    script = get_script(script_id)
    data = parse_script(script["content"])
    data["enabled"] = bool(enabled)
    return save_script(yaml.safe_dump(data, sort_keys=False), invalidate_validation=False)


def approve_script_validation(script_id: str, validation_run_id: int, *, user_ok: bool, validated_by: str = "user", note: str = "") -> dict[str, Any]:
    if not user_ok:
        raise ValueError("user_ok must be true after explicit user approval")
    get_script(script_id)
    with db() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id = ? AND script_id = ?",
            (int(validation_run_id), script_id),
        ).fetchone()
        if not run:
            raise ValueError("validation run not found for script")
        if not bool(run["dry_run"]) or str(run["status"]) != "ok":
            raise ValueError("validation run must be a successful dry run")
        conn.execute(
            """
            UPDATE scripts
            SET validated = 1,
                validated_at = CURRENT_TIMESTAMP,
                validated_by = ?,
                validation_run_id = ?,
                validation_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (validated_by.strip() or "user", int(validation_run_id), note.strip(), script_id),
        )
    return get_script(script_id)


def revoke_script_validation(script_id: str, note: str = "") -> dict[str, Any]:
    get_script(script_id)
    with db() as conn:
        conn.execute(
            """
            UPDATE scripts
            SET validated = 0,
                validated_at = '',
                validated_by = '',
                validation_run_id = NULL,
                validation_note = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (note.strip(), script_id),
        )
    return get_script(script_id)


def delete_script(script_id: str) -> dict[str, Any]:
    script = get_script(script_id)
    Path(script["path"]).unlink(missing_ok=True)
    with db() as conn:
        conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    return {"deleted": True, "script_id": script_id}
