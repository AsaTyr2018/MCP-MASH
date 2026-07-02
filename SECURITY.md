# Security Policy

MCP-MASH stores automation scripts, run logs, its own MCP bearer token, and a Mailbridge automation token.

MCP-MASH is licensed under the PolyForm Noncommercial License 1.0.0. Commercial use requires a separate commercial license.

## Deployment

- Prefer localhost or trusted LAN deployment.
- Do not expose MASH directly to the public internet.
- Use a strong `MASH_MCP_TOKEN`.
- Use a user-scoped Mailbridge automation token with only the required accounts and permissions.
- Keep `./data` private and backed up securely.
- Do not commit runtime data, `.env`, databases, tokens, generated scripts with private data, or run logs.
- Rotate MASH and Mailbridge automation tokens after suspected exposure.

## Reporting

Please open a private security advisory or contact the maintainer directly before publishing vulnerabilities.
