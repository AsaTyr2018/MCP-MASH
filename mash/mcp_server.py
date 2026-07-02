from __future__ import annotations

from typing import Any

import json
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from . import __version__
from .config import settings
from .db import get_config, set_config
from .mailbridge_client import MailbridgeClient
from .runner import get_run, get_run_log as read_run_log, list_runs as read_runs, run_script
from .runtime_config import runtime_config
from .scheduler import scheduler
from .scripts import (
    delete_script as remove_script,
    get_script as read_script,
    list_scripts as read_scripts,
    save_script,
    set_allowed_accounts as write_allowed_accounts,
    approve_script_validation as approve_validation,
    revoke_script_validation as revoke_validation,
    set_script_enabled,
    validate_script_content,
    allowed_accounts as read_allowed_accounts,
)


mcp = FastMCP(
    "mcp-mash",
    instructions=(
        "MCP-MASH is a personal Mail Automation Script Host. "
        "It starts empty and must be initialized before scripts are created. "
        "Scripts are structured mail automation files, not shell commands. "
        "Mail sending must always go through Mailbridge policy."
    ),
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "127.0.0.1",
            "127.0.0.1:8080",
            "127.0.0.1:18083",
            "localhost",
            "localhost:8080",
            "localhost:18083",
            "192.168.1.172",
            "192.168.1.172:18083",
        ],
        allowed_origins=[
            "http://127.0.0.1:8080",
            "http://127.0.0.1:18083",
            "http://localhost:8080",
            "http://localhost:18083",
            "http://192.168.1.172:18083",
        ],
    ),
)


@mcp.tool()
def get_status() -> dict[str, Any]:
    """Return MASH configuration, scheduler state, script counts, and recent run state."""
    scripts = read_scripts()
    runs = read_runs(limit=5)
    return {
        "version": __version__,
        "configured": get_config("configured", "false") == "true",
        "public_url": settings.public_url,
        "mcp_url": f"{settings.public_url.rstrip('/')}/mcp/",
        "timezone": settings.timezone,
        "allowed_accounts": read_allowed_accounts(),
        "script_count": len(scripts),
        "enabled_script_count": len([script for script in scripts if script["enabled"]]),
        "validated_script_count": len([script for script in scripts if script.get("validated")]),
        "scheduler": scheduler.status(),
        "mailbridge": {
            "configured": bool(runtime_config.snapshot().mailbridge_mcp_url and runtime_config.snapshot().mailbridge_mcp_token),
            "url": runtime_config.snapshot().mailbridge_mcp_url,
            "sync_before_run": runtime_config.snapshot().mailbridge_sync_before_run,
            "account_aliases": runtime_config.snapshot().account_aliases or {},
        },
        "recent_runs": runs,
    }


@mcp.tool()
def initialize_mash(
    allowed_accounts: list[str],
    default_report_recipient: str = "owner",
    default_timezone: str = "",
) -> dict[str, Any]:
    """Initialize an empty MASH instance with allowed Mailbridge account names and report defaults."""
    accounts = write_allowed_accounts(allowed_accounts)
    set_config("configured", "true")
    set_config("default_report_recipient", default_report_recipient.strip() or "owner")
    if default_timezone.strip():
        set_config("default_timezone", default_timezone.strip())
    return get_status() | {"initialized": True, "allowed_accounts": accounts}


@mcp.tool()
def list_allowed_accounts() -> list[str]:
    """List Mailbridge account names that MASH may use."""
    return read_allowed_accounts()


@mcp.tool()
def set_allowed_accounts(accounts: list[str]) -> dict[str, Any]:
    """Replace the Mailbridge account allowlist used by MASH validation."""
    return {"allowed_accounts": write_allowed_accounts(accounts)}


@mcp.tool()
def configure_mailbridge(mailbridge_mcp_url: str, mailbridge_mcp_token: str, sync_before_run: bool = True, account_aliases: dict[str, str] | None = None) -> dict[str, Any]:
    """Configure the Mailbridge MCP adapter used by autonomous MASH runs. Token is stored and not returned."""
    set_config("mailbridge_mcp_url", mailbridge_mcp_url.strip())
    set_config("mailbridge_mcp_token", mailbridge_mcp_token.strip())
    set_config("mailbridge_sync_before_run", "true" if sync_before_run else "false")
    set_config("account_aliases", json.dumps(account_aliases or {}))
    runtime_config.load_once()
    return test_mailbridge_connection()


@mcp.tool()
def get_mailbridge_config() -> dict[str, Any]:
    """Return Mailbridge adapter configuration without revealing the token."""
    config = runtime_config.snapshot()
    return {
        "configured": bool(config.mailbridge_mcp_url and config.mailbridge_mcp_token),
        "mailbridge_mcp_url": config.mailbridge_mcp_url,
        "token_present": bool(config.mailbridge_mcp_token),
        "sync_before_run": config.mailbridge_sync_before_run,
        "account_aliases": config.account_aliases or {},
    }


@mcp.tool()
def test_mailbridge_connection() -> dict[str, Any]:
    """Test the configured Mailbridge MCP adapter and return visible account names."""
    client = MailbridgeClient(runtime_config.snapshot())
    accounts = client.list_accounts()
    return {
        "ok": True,
        "visible_accounts": [account.get("name") for account in accounts],
        "account_count": len(accounts),
    }


def _mailbridge_client_and_account(account: str) -> tuple[MailbridgeClient, int]:
    if account not in read_allowed_accounts():
        raise ValueError(f"account '{account}' is not in the MASH allowlist")
    client = MailbridgeClient(runtime_config.snapshot())
    config_snapshot = runtime_config.snapshot()
    mailbridge_account_name = (config_snapshot.account_aliases or {}).get(account, account)
    return client, client.account_id_by_name(mailbridge_account_name)


@mcp.tool()
def list_contacts(account: str, limit: int = 50) -> dict[str, Any]:
    """List synced contacts for an allowed Mailbridge account."""
    client, account_id = _mailbridge_client_and_account(account)
    return client.search_contacts(account_id, query="", limit=max(1, min(limit, 100)))


@mcp.tool()
def search_contacts(account: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Search synced contacts for an allowed Mailbridge account."""
    client, account_id = _mailbridge_client_and_account(account)
    return client.search_contacts(account_id, query=query, limit=max(1, min(limit, 100)))


@mcp.tool()
def get_message(account: str, message_id: int) -> dict[str, Any]:
    """Read one message through Mailbridge for an allowed account."""
    client, account_id = _mailbridge_client_and_account(account)
    message = client.get_message(message_id)
    if int(message.get("account_id", 0)) != int(account_id):
        raise ValueError("message does not belong to requested account")
    return message


@mcp.tool()
def list_attachments(account: str, message_id: int) -> dict[str, Any]:
    """List attachments for one message through Mailbridge without content."""
    client, account_id = _mailbridge_client_and_account(account)
    result = client.list_attachments(message_id)
    if int(result.get("account_id", 0)) != int(account_id):
        raise ValueError("message does not belong to requested account")
    return result


@mcp.tool()
def get_attachment(account: str, message_id: int, attachment_index: int = 0, filename: str = "", max_bytes: int = 1000000) -> dict[str, Any]:
    """Read one message attachment as base64 content through Mailbridge."""
    client, account_id = _mailbridge_client_and_account(account)
    result = client.get_attachment(message_id, attachment_index=attachment_index, filename=filename, max_bytes=max(1, min(max_bytes, 5000000)))
    if int(result.get("account_id", 0)) != int(account_id):
        raise ValueError("message does not belong to requested account")
    return result


@mcp.tool()
def create_forward_draft(
    account: str,
    message_id: int,
    to_recipients: str,
    note: str = "",
    cc_recipients: str = "",
    bcc_recipients: str = "",
) -> dict[str, Any]:
    """Create a forward draft for one message. Sending remains Mailbridge-policy controlled."""
    client, account_id = _mailbridge_client_and_account(account)
    message = client.get_message(message_id)
    if int(message.get("account_id", 0)) != int(account_id):
        raise ValueError("message does not belong to requested account")
    return client.create_forward_draft(message_id, to_recipients, note=note, cc_recipients=cc_recipients, bcc_recipients=bcc_recipients)


@mcp.tool()
def create_contact(
    account: str,
    display_name: str,
    email: str,
    phone: str = "",
    company: str = "",
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Create a contact for an allowed Mailbridge account through Mailbridge policy."""
    client, account_id = _mailbridge_client_and_account(account)
    return client.create_contact(account_id, display_name, email, phone=phone, company=company, profile_id=profile_id)


@mcp.tool()
def list_calendar_events(account: str, start_at: str, end_at: str, limit: int = 50) -> dict[str, Any]:
    """List synced calendar events for an allowed Mailbridge account."""
    client, account_id = _mailbridge_client_and_account(account)
    return client.list_calendar_events(account_id, start_at=start_at, end_at=end_at, limit=max(1, min(limit, 200)))


@mcp.tool()
def create_calendar_event(
    account: str,
    title: str,
    starts_at: str,
    ends_at: str = "",
    location: str = "",
    description: str = "",
    attendees: str = "",
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Create a calendar event for an allowed Mailbridge account through Mailbridge policy."""
    client, account_id = _mailbridge_client_and_account(account)
    return client.create_calendar_event(
        account_id,
        title,
        starts_at,
        ends_at=ends_at,
        location=location,
        description=description,
        attendees=attendees,
        profile_id=profile_id,
    )


@mcp.tool()
def validate_script(content_yaml: str) -> dict[str, Any]:
    """Validate a MASH YAML script without saving it."""
    data = validate_script_content(content_yaml)
    return {"valid": True, "script": data}


@mcp.tool()
def create_script(content_yaml: str) -> dict[str, Any]:
    """Create or replace a MASH script from YAML content."""
    return save_script(content_yaml)


@mcp.tool()
def list_scripts() -> list[dict[str, Any]]:
    """List stored MASH scripts without returning full YAML content."""
    return read_scripts()


@mcp.tool()
def get_script(script_id: str) -> dict[str, Any]:
    """Return one script including its YAML content."""
    return read_script(script_id)


@mcp.tool()
def update_script(script_id: str, content_yaml: str) -> dict[str, Any]:
    """Replace one script and reset its validation. The YAML id must match the requested script_id."""
    data = validate_script_content(content_yaml)
    if str(data["id"]) != script_id:
        raise ValueError("content id does not match script_id")
    return save_script(content_yaml)


@mcp.tool()
def approve_script_validation(script_id: str, validation_run_id: int, user_ok: bool, validated_by: str = "user", note: str = "") -> dict[str, Any]:
    """Mark a script as validated after a successful dry run and explicit user OK."""
    return approve_validation(script_id, validation_run_id, user_ok=user_ok, validated_by=validated_by, note=note)


@mcp.tool()
def revoke_script_validation(script_id: str, note: str = "") -> dict[str, Any]:
    """Remove script validation so non-dry-run execution is blocked again."""
    return revoke_validation(script_id, note=note)


@mcp.tool()
def enable_script(script_id: str) -> dict[str, Any]:
    """Enable a script for scheduled execution."""
    return set_script_enabled(script_id, True)


@mcp.tool()
def disable_script(script_id: str) -> dict[str, Any]:
    """Disable a script for scheduled execution."""
    return set_script_enabled(script_id, False)


@mcp.tool()
def delete_script(script_id: str) -> dict[str, Any]:
    """Delete a stored MASH script and its metadata."""
    return remove_script(script_id)


@mcp.tool()
def run_script_now(script_id: str, dry_run: bool = True) -> dict[str, Any]:
    """Run a script immediately. dry_run defaults to true."""
    return run_script(script_id, dry_run=dry_run, reason="manual")


@mcp.tool()
def list_runs(limit: int = 20, script_id: str = "") -> list[dict[str, Any]]:
    """List recent script runs."""
    return read_runs(limit=limit, script_id=script_id)


@mcp.tool()
def get_run_status(run_id: int) -> dict[str, Any]:
    """Return one run metadata record."""
    return get_run(run_id)


@mcp.tool()
def get_run_log(run_id: int, max_lines: int = 200) -> dict[str, Any]:
    """Return compact structured JSONL log lines for one run."""
    return read_run_log(run_id, max_lines=max_lines)


@mcp.tool()
def get_scheduler_status() -> dict[str, Any]:
    """Return scheduler health and last tick state."""
    return scheduler.status()


@mcp.tool()
def create_weekly_report(
    script_id: str,
    account: str,
    recipient: str = "owner",
    schedule: str = "0 8 * * MON",
    query: str = "newer_than:7d",
) -> dict[str, Any]:
    """Create a standard weekly mail report script."""
    content = {
        "id": script_id,
        "name": "Weekly Mail Report",
        "enabled": True,
        "schedule": schedule,
        "description": "Sends a weekly summary of relevant mail and automation state.",
        "account": account,
        "query": query,
        "limits": {"max_messages": 200, "timeout_seconds": 120},
        "actions": [
            {
                "type": "summarize",
                "group_by": ["sender", "subject", "unread_state"],
                "include_examples": True,
            },
            {
                "type": "send_report",
                "to": recipient,
                "subject": "Weekly mail report",
                "mode": "via_mailbridge_policy",
            },
        ],
        "logging": {"level": "detailed"},
    }
    return save_script(yaml.safe_dump(content, sort_keys=False))


@mcp.tool()
def send_report_now(script_id: str, dry_run: bool = True) -> dict[str, Any]:
    """Run a report script immediately. Current basic build records planned report actions."""
    return run_script(script_id, dry_run=dry_run, reason="report_now")


@mcp.tool()
def preview_script(script_id: str) -> dict[str, Any]:
    """Preview what a script would do. Current basic build returns a dry-run log."""
    return run_script(script_id, dry_run=True, reason="preview")


@mcp.tool()
def test_mail_query(account: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Placeholder for future Mailbridge query testing."""
    return {
        "status": "coming_soon",
        "account": account,
        "query": query,
        "limit": max(1, min(limit, 200)),
        "message": "Mailbridge query execution is not wired in this basic build.",
    }


@mcp.tool()
def explain_script(script_id: str) -> dict[str, Any]:
    """Return a simple structural explanation of a script."""
    script = read_script(script_id)
    data = yaml.safe_load(script["content"]) or {}
    return {
        "script_id": script_id,
        "name": data.get("name", ""),
        "enabled": bool(data.get("enabled", False)),
        "schedule": data.get("schedule", ""),
        "account": data.get("account", ""),
        "query": data.get("query", ""),
        "actions": [action.get("type", "") for action in data.get("actions", []) if isinstance(action, dict)],
    }
