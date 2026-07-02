# Changelog

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
