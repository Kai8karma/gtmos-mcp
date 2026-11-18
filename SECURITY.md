# Security

gtmos-mcp runs on your machine, with your HubSpot token, as a subprocess of
your MCP host. This page says exactly what it can touch, so you can decide
whether that is acceptable before you run it.

## What the server can do

| Capability | Detail |
| --- | --- |
| Call HubSpot | `GET` requests to `api.hubapi.com` only, for contacts and deals, and only when a tool is called with `portal: true`. |
| Write to HubSpot | Never. There is no write call in the package, and `tests/test_read_only.py` fails the build if one is added. |
| Call anything else | Never. No other host, no telemetry, no update check. The same test enforces it. |
| Read local files | The export and policy files you name in a tool call. |
| Write local files | Markdown and JSON reports under `out_dir` (default `./audit-out`, `./funnel-out`, `./cortex-out`), and `~/.gtmos/errors.log` if the CLI crashes. |
| Run code or shell commands | Never. |

"Read-only" in the README means read-only towards the CRM. Most tools do
write report files to disk; their MCP annotations say so.

## Confine file access

By default a tool can read or write any path the server process can. The
paths come from the model, so on a shared or sensitive machine set:

```bash
GTMOS_ALLOWED_DIR=/path/to/a/working/folder
```

With it set, relative paths resolve inside that folder and anything that
lands outside it is refused, including `..` and symlinks that point out.
Put your exports there and reports will be written there.

## The token

- Read from `GTMOS_HUBSPOT_TOKEN` at call time. It is never written to
  disk, never placed in a report and never returned in a tool result.
- Sent only as an `Authorization` header to `api.hubapi.com`.
- A 401 or 403 is reported as "token invalid or missing scope" without
  echoing the token.
- Create the private app with read scopes only
  (`crm.objects.contacts.read`, `crm.objects.deals.read`). The server never
  needs more, and a read-only token bounds the damage if it leaks.

## CRM text is untrusted input

Names, company names, deal names and other field values are copied into
tool results that a model then reads. Someone who can create a record in
your CRM, for example through a public form, can put instructions in a
field. This server takes no action on that text, but your host's model
might. Treat tool output as data, keep write-capable tools behind human
approval in the same session, and review any proposal before acting on it.

## Reports contain personal data

Reports list email addresses and record details from your CRM. They stay on
your disk; delete them or store them under the same controls as an export.

## Supported versions

Fixes land on the latest release. PyPI versions before 0.6.0 stop on a
malformed `tools/call`; upgrade.

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** on
this repository. If you cannot, email kai8karma@gmail.com with "gtmos-mcp
security" in the subject. Please include the version, the tool call or
message that triggers the problem, and what you observed.

You will get an acknowledgement within 7 days. Please do not open a public
issue for a vulnerability before a fix is released.
