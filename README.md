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
- Script validation gate: real script execution requires a successful dry run and explicit user OK.
- Account alias support, for example `botmail -> main`.
- Real `move` execution through Mailbridge `move_messages`.
- Read/unread, trash, add-label, remove-label, draft-reply, and send-reply actions through Mailbridge.
- `sync_before_run` waits for Mailbridge background sync jobs before searching.
- Contact and calendar list/search/create helpers through Mailbridge.
- Calendar script actions for fixed events and mail-derived delivery windows.
- Message read, attachment list/read, forward-draft helpers, and attachment-forwarding script actions through Mailbridge.
- Real mail query previews through `test_mail_query`.
- Dry-run logs for attachment forwarding include selected message IDs, subjects, senders, and attachment filenames.
- Weekly MASH job overview report generator.
- Report dry-runs with job status, processed counts, and run-log highlights.
- Report draft/send support through Mailbridge policy, including `send_account` and `automation_consent_id`.
- Reply draft/send actions through Mailbridge policy.

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
5. Test with `run_script_now(..., dry_run=true)` and inspect logs with `get_run_log`.
6. Show the dry-run result to the user and ask for explicit OK.
7. Call `approve_script_validation` or the shorter alias `approve_validation` with `user_ok=true` and the successful dry-run ID.
8. Enable or run the script for real.

## Script Validation Gate

Every real script execution is blocked until the script is validated.

Required flow:

1. Create or update the script.
2. Run a dry run.
3. Review the run result with the user.
4. After explicit user OK, call `approve_script_validation` or `approve_validation`.

Any later script content update resets validation. `enable_script` and `disable_script` keep the validation state because they do not change script behavior.

This keeps interactive AI work approval-based while allowing validated MASH scripts to run autonomously inside their Mailbridge token and account scope.

## MCP Tools

- `get_status`
- `initialize_mash`
- `list_allowed_accounts`
- `set_allowed_accounts`
- `configure_mailbridge`
- `get_mailbridge_config`
- `test_mailbridge_connection`
- `get_message`
- `list_attachments`
- `get_attachment`
- `create_forward_draft`
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
- `approve_script_validation`
- `approve_validation`
- `revoke_script_validation`
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
- `create_calendar_event` and aliases `calendar_event`, `calendar_create`, `create_event`
- `calendar_from_delivery` and `create_calendar_events_from_mail` for mail-derived delivery events
- `list_attachments`
- `read_attachment`
- `create_forward_draft`, `forward_draft`, `forward`, `forward_mail`, `forward_email`, `forward_to`, `forward_message`, and `document_forward`
- `forward_attachments`, `forward_pdf`, `send_attachments`, `extract_attachments`, `forward_message_with_attachments`, and `forward_matching_attachments`
- `summarize` for deterministic MASH job overview previews
- `send_report` for MASH job overview drafts and Mailbridge-policy sends

Attachment-forwarding actions support:

- `to` or `to_recipients`
- `cc` / `bcc`
- `subject`, with templates such as `{subject}`
- `attachment_extensions`, for example `.pdf`
- `dedupe: true` to keep only one selected attachment per filename

In dry runs, MASH does not create drafts. It inspects matching messages and writes a `would_forward_attachments` log entry with the selected attachment filenames. After the dry-run result is shown to the user and approved, `approve_script_validation` allows the script to create real Mailbridge forward drafts with copied attachments.

Report actions generate a MASH job overview from recent run history. They do not summarize mailbox content. Use `send_account` or `from_account` to send from a different allowed Mailbridge account, and `automation_consent_id` to use a Mailbridge-approved automation send consent.

Example:

```yaml
actions:
  - type: send_report
    to: hauke@example.com
    send_account: botmail
    subject: "Weekly MASH job overview"
    mode: via_mailbridge_policy
    window_hours: 168
    automation_consent_id: 1
```

Current planned/placeholder actions:

- `mark_read`
- `mark_unread`
- `trash`
- `draft_reply`
- `send_reply`
- `add_label`
- `remove_label`

`mark_read`, `mark_unread`, `trash`, `add_label`, `remove_label`, `draft_reply`, and `send_reply` are wired through the Mailbridge adapter.

## Mailbridge Adapter

Use `configure_mailbridge` to set:

- `mailbridge_mcp_url`
- `mailbridge_mcp_token`
- `sync_before_run`
- optional `account_aliases`

When `sync_before_run` is enabled, MASH waits for Mailbridge background sync jobs to finish before searching the local index. Mailbridge stays responsive while the job runs.

Example alias:

```json
{
  "botmail": "main"
}
```

This lets scripts use `botmail` while Mailbridge exposes the account as `main`.

## Example: Forward Invoice PDFs

```yaml
id: forward-invoice-pdfs
name: Forward invoice PDFs
enabled: false
schedule: "*/10 * * * *"
account: Hauke Lenz
query: "in:inbox has:attachment (rechnung OR invoice OR receipt OR beleg)"
on_no_matches: sleep
limits:
  max_messages: 10
actions:
  - type: forward_attachments
    to: documents@example.com
    attachment_extensions:
      - .pdf
    subject: "Fwd: {subject}"
    dedupe: true
logging:
  level: detailed
```

Validation flow:

1. `run_script_now` with `dry_run=true`
2. `get_run_log` and show the selected attachments to the user
3. `approve_script_validation` or `approve_validation` with `user_ok=true`
4. enable the script or run it for real

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
attachments
forward
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
