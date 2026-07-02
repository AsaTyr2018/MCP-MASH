# Changelog

## 0.1.7

- Change report rendering from mail-content summaries to MASH job overview reports.
- Add report run-history summaries with status counts, processed counts, and job highlights.
- Add `send_account` / `from_account` support so reports can read one account and send through another allowed account.
- Add `automation_consent_id` support for report sends through Mailbridge approved automation policy.

## 0.1.6

- Add real `test_mail_query` execution through Mailbridge.
- Add `forward_attachments`, `forward_pdf`, and related forward aliases to script validation and execution.
- Forward matching attachments into Mailbridge drafts using `attachment_extensions`, `subject`, and `dedupe`.
- Add dry-run attachment previews with selected message and filename details in run logs.
- Add `approve_validation` alias and expose approval tools early in the MCP tool list for clients with truncated discovery.

## 0.1.5

- Add script validation gate for all non-dry-run execution.
- Add `approve_script_validation` and `revoke_script_validation` MCP tools.
- Reset validation on script content updates while preserving it on enable/disable changes.

## 0.1.4

- Add MASH tools for message reads, attachment listing, attachment reads, and forward draft creation.
- Add script actions for attachment inspection, capped attachment reads, and forward draft creation.
- Document required Mailbridge automation-token permissions for attachments and forwarding.

## 0.1.3

- Add calendar script action validation and runner execution.
- Add `calendar_from_delivery` support for Amazon-style delivery windows in mail snippets.
- Add calendar action documentation and aliases for assistant-generated scripts.

## 0.1.2

- Add MASH MCP tools for listing, searching, and creating contacts through Mailbridge.
- Add MASH MCP tools for listing and creating calendar events through Mailbridge.
- Document required Mailbridge automation-token permissions for contacts and calendar.

## 0.1.1

- Treat empty Mailbridge search results as a successful no-op instead of a runner error.
- Add `on_no_matches` validation with `sleep`, `skip`, and `ok` behaviors.
- Harden Mailbridge MCP response parsing for empty or `{ "result": [] }` responses.

## 0.1.0

- Initial Dockerized MCP-MASH release.
- Add Streamable HTTP MCP server with bearer-token authentication.
- Add single-user initialization flow.
- Add account allowlist and YAML script validation.
- Add script CRUD, enable/disable, manual run, run logs, and scheduler.
- Add Mailbridge MCP adapter configuration with runtime polling.
- Add basic `move` action execution through Mailbridge `move_messages`.
- Add weekly report script generator and placeholder tools for future report/send actions.
