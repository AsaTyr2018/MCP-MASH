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
            if action_type in {"send_reply", "send_report"} and action.get("mode") != "via_mailbridge_policy":
                errors.append(f"action {index} {action_type} requires mode=via_mailbridge_policy")
    if errors:
        raise ValueError("; ".join(errors))
    return data


def script_path(script_id: str) -> Path:
    return settings.scripts_dir / f"{script_id}.yaml"


def save_script(content: str) -> dict[str, Any]:
    data = validate_script_content(content)
    script_id = str(data["id"])
    path = script_path(script_id)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with db() as conn:
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
    result["content"] = Path(result["path"]).read_text(encoding="utf-8")
    return result


def list_scripts() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM scripts ORDER BY id").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        result.append(item)
    return result


def set_script_enabled(script_id: str, enabled: bool) -> dict[str, Any]:
    script = get_script(script_id)
    data = parse_script(script["content"])
    data["enabled"] = bool(enabled)
    return save_script(yaml.safe_dump(data, sort_keys=False))


def delete_script(script_id: str) -> dict[str, Any]:
    script = get_script(script_id)
    Path(script["path"]).unlink(missing_ok=True)
    with db() as conn:
        conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    return {"deleted": True, "script_id": script_id}
