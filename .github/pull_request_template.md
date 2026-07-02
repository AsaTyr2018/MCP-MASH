## Summary

Describe the change and why it is needed.

## Scope

- [ ] MCP tools/protocol behavior
- [ ] Script format/actions
- [ ] Scheduler
- [ ] Mailbridge adapter
- [ ] Run logging/status
- [ ] Reports
- [ ] Security/token handling
- [ ] Docker/deployment
- [ ] Documentation

## Privacy and security impact

Explain what user data this change reads, moves, deletes, sends, stores, logs, or exposes.

- Data touched:
- Retention/logging impact:
- Token/credential impact:
- Mailbridge account/permission impact:

## Verification

List the checks you ran.

- [ ] `python -m py_compile $(find mash -name '*.py' -print)`
- [ ] Docker build
- [ ] MCP tool check
- [ ] Scheduler/run-log check
- [ ] Mailbridge adapter check
- [ ] Not applicable

## Deployment notes

Mention any migration, environment variable, token, Mailbridge permission, or user-action requirements.

## Safety checklist

- [ ] No runtime `./data` files, databases, keys, tokens, passwords, generated private scripts, or private run logs are committed.
- [ ] MASH remains single-user.
- [ ] Mailbridge account boundaries are preserved.
- [ ] Tokens are not logged or returned after configuration.
- [ ] Send behavior still delegates to Mailbridge policy.
