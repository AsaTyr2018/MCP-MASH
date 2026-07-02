# MCP-MASH

MCP-MASH means **Mail Automation Script Host**.

It is a personal MCP server for scheduled mail automation. MASH starts empty and is configured through an MCP-capable client such as Codex. The user describes the desired mail behavior, and the client creates structured MASH scripts that MASH can later run autonomously.

MASH is additive to [Mailbridge MCP](https://github.com/AsaTyr2018/Mailbridge-MCP):

- **Mailbridge** owns mail accounts, IMAP/SMTP credentials, mail index, send policy, contacts, calendar data, and audit.
- **MCP-MASH** owns automation scripts, schedules, run state, logs, and report jobs.

MASH does not store mail account passwords and does not provide a web UI.

## Current Status

MCP-MASH is an early local automation host.

Recommended use today:

- local machine
- trusted LAN
- one personal user
- Mailbridge running as the mail gateway

Public internet exposure is not recommended yet.

## Features

- Single-user MCP-only automation host.
- Initial empty state with MCP-driven setup.
- YAML MASH scripts for mail automation instructions.
- Internal scheduler for enabled scripts.
- Manual script execution through MCP.
- SQLite metadata store.
- File-backed script store.
- Structured JSONL run logs.
- Runtime-polling configuration, so Mailbridge adapter changes do not require a restart.
- Mailbridge MCP adapter with personal automation-token support.
- Account alias support, for example `botmail -> main`.
- Real `move` execution through Mailbridge `move_messages`.
- Contact and calendar list/search/create helpers through Mailbridge.
- Weekly report script generator.
- Placeholder tools for future report/send/query-preview work.

## Non-Goals

- No multi-user administration.
- No browser UI.
- No direct IMAP/SMTP credential storage.
- No generic shell command runner.
- No replacement for Mailbridge send policy.
- No hidden send bypass.

## Architecture

```text
MCP client such as Codex
        |
        | MCP: configure scripts, status, logs
        v
MCP-MASH
        |
        | MCP client: autonomous script execution
        v
Mailbridge MCP
        |
        v
User-owned mail accounts and Mailbridge policy
```

MASH uses a **personal Mailbridge automation token**. That token must be scoped in Mailbridge to one Mailbridge user, selected accounts, and explicit permissions.

## Quickstart

Copy the environment example:

```bash
cp .env.example .env
```

Generate a long token and place it in `.env`:

```bash
openssl rand -base64 36
```

Start MASH:

```bash
docker compose up -d --build
```

Health check:

```bash
curl http://127.0.0.1:18083/healthz
```

MCP endpoint:

```text
http://127.0.0.1:18083/mcp/
```

## Codex MCP Example

```toml
[mcp_servers.mash]
url = "http://127.0.0.1:18083/mcp/"
bearer_token_env_var = "MASH_MCP_TOKEN"
enabled = true
```

The token is the value of `MASH_MCP_TOKEN` from your `.env` or shell environment.

## Initial Setup

A fresh MASH instance has:

- no allowed accounts
- no scripts
- no Mailbridge adapter config
- no report settings

Example setup flow:

1. Create a user-scoped automation token in Mailbridge for the accounts MASH may use.
2. Configure MASH with `configure_mailbridge`.
3. Initialize MASH with `initialize_mash`.
4. Create scripts with `create_script` or helper tools such as `create_weekly_report`.
5. Test with `run_script_now` and inspect logs with `get_run_log`.

## MCP Tools

- `get_status`
- `initialize_mash`
- `list_allowed_accounts`
- `set_allowed_accounts`
- `configure_mailbridge`
- `get_mailbridge_config`
- `test_mailbridge_connection`
- `list_contacts`
- `search_contacts`
- `create_contact`
- `list_calendar_events`
- `create_calendar_event`
- `validate_script`
- `create_script`
- `list_scripts`
- `get_script`
- `update_script`
- `enable_script`
- `disable_script`
- `delete_script`
- `run_script_now`
- `list_runs`
- `get_run_status`
- `get_run_log`
- `get_scheduler_status`
- `create_weekly_report`
- `send_report_now`
- `preview_script`
- `test_mail_query`
- `explain_script`

## MASH Scripts

Scripts are YAML instruction files. They are not shell scripts.

Example:

```yaml
id: move-facebook-inbox-to-test
name: Move Facebook Inbox Mail To Test
enabled: true
schedule: "*/5 * * * *"

account: personal
query: "from:facebook in:inbox"

limits:
  max_messages: 100
  timeout_seconds: 120

actions:
  - type: move
    folder: test

logging:
  level: detailed
```

Current real execution support:

- `move` through Mailbridge `move_messages`

Current planned/placeholder actions:

- `summarize`
- `mark_read`
- `mark_unread`
- `trash`
- `draft_reply`
- `send_reply`
- `send_report`
- `add_label`
- `remove_label`

Placeholder actions are logged as coming soon until their Mailbridge adapter mapping exists.

## Mailbridge Adapter

Use `configure_mailbridge` to set:

- `mailbridge_mcp_url`
- `mailbridge_mcp_token`
- `sync_before_run`
- optional `account_aliases`

Example alias:

```json
{
  "botmail": "main"
}
```

This lets scripts use `botmail` while Mailbridge exposes the account as `main`.

MASH polls runtime config internally. Updating Mailbridge adapter settings through MCP does not require restarting the container.

## Contact And Calendar Tools

MASH can delegate contact and calendar operations to Mailbridge for allowed accounts:

- `list_contacts`
- `search_contacts`
- `create_contact`
- `list_calendar_events`
- `create_calendar_event`

The Mailbridge automation token configured in MASH must include the matching permissions:

```text
contacts
contacts_write
calendar
calendar_write
```

Mailbridge still owns provider credentials, writable sync profiles, account scoping, and audit.

## Security Notes

- MASH is single-user by design.
- MASH has no web UI and no login surface.
- MASH stores its own MCP bearer token and the Mailbridge automation token in local runtime data.
- MASH does not store IMAP/SMTP passwords.
- MASH cannot see Mailbridge accounts outside the Mailbridge automation token scope.
- Mailbridge remains responsible for account ownership, send policy, and audit.
- Do not commit `./data`, `.env`, tokens, logs, or generated scripts containing private data.

## License

PolyForm Noncommercial License 1.0.0. Commercial use is not permitted without a separate commercial license.
