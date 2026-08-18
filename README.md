# gtmos-mcp

Read-only CRM audit for Claude: score data integrity, find duplicate clusters and stalled revenue, in your environment through your own token.

An MCP (Model Context Protocol) server that lets Claude Desktop, Claude Code, or any MCP host audit your HubSpot CRM. It runs in your environment, reads with your own HubSpot token, and no new vendor touches your data. Every tool is read-only by construction: nothing in this package writes to a CRM, sends mail, or calls any network API other than your own HubSpot endpoint.

- **Zero dependencies.** Pure Python stdlib (`dependencies = []`). Nothing to vet transitively.
- **Read-only.** Two tools, both audits. No write scopes needed on your token.
- **Deterministic scoring.** No LLM in the score path; the model reads results, it does not invent them.
- **Works offline.** Point the tools at a JSON export instead of a live portal and no network call happens at all.

## Tools

| Tool | What it does |
|------|--------------|
| `audit_crm` | Scores contacts for data-integrity leaks: completeness, validity, freshness, ownership, consistency. Returns a portal integrity score, grade, duplicate clusters, and the weakest dimensions. Writes a full Markdown report. |
| `funnel_leak` | Analyses deals by stage to find stalled pipeline and where revenue leaks out of the funnel. Returns the dollar total leaking and the stalled deals behind it. |

## Install

Requires Python 3.11+.

```bash
pip install git+https://github.com/Kai8karma/gtmos-mcp.git
```

Verify:

```bash
gtmos mcp --dry-run
```

Expected output:

```
[dry-run] gtmos mcp would serve 2 tools on stdio (protocol 2025-06-18)
  - audit_crm: Score a CRM export or live HubSpot portal for data-integrity leaks.
  - funnel_leak: Analyse deals by stage to find stalled pipeline and where revenue leaks out of the funnel.
```

## Connect to Claude

Generate the config block (always matches the installed code):

```bash
gtmos mcp --config
```

Then paste it into your MCP host config. For Claude Desktop that is `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "gtmos": {
      "command": "gtmos",
      "args": [
        "mcp"
      ],
      "env": {
        "GTMOS_HUBSPOT_TOKEN": "<your-hubspot-private-app-token>"
      }
    }
  }
}
```

For Claude Code:

```bash
claude mcp add gtmos --env GTMOS_HUBSPOT_TOKEN=<your-hubspot-private-app-token> -- gtmos mcp
```

Restart the host, then ask: *"Audit my CRM and show me the weakest dimension."*

### Getting a HubSpot token

Create a [private app](https://developers.hubspot.com/docs/guides/apps/private-apps/overview) in your HubSpot portal with read-only scopes: `crm.objects.contacts.read` for `audit_crm`, `crm.objects.deals.read` for `funnel_leak`. The token stays in your host config on your machine; this package never stores or forwards it.

### No token? Use an export

Both tools accept a file instead of a live portal:

```
audit_crm  { "contacts_file": "./contacts.json", "acv": 9000 }
funnel_leak { "deals_file": "./deals.json" }
```

`contacts.json` is a JSON array of contact objects (HubSpot export shape: `properties.email`, `properties.firstname`, ...). See [tests/fixtures/contacts_sample.json](tests/fixtures/contacts_sample.json) and [tests/fixtures/deals_sample.json](tests/fixtures/deals_sample.json) for the exact shapes.

## What a run looks like

```
Scored 25 records.
Portal integrity score: 77.5 (B).
Duplicate clusters found: 2.
Dimension averages (worst first):
  - freshness: 55.0
  - ownership: 88.0
  - validity: 91.9
  - consistency: 93.6
  - completeness: 94.6
Full report written to: ./audit-out/integrity-report.md
```

## CLI without MCP

The same engines run directly from the shell:

```bash
gtmos audit --input contacts.json --acv 9000     # contact integrity audit
gtmos calibrate --scores s.json --outcomes o.json # grade the scorer against real outcomes
```

## Architecture

Newline-delimited JSON-RPC 2.0 over stdio, per the MCP stdio transport. The protocol handler (`handle_message`) is a pure function, message in, response out, so the whole surface is testable without spawning a process. The scoring engines are deterministic and network-free; the only network code is the optional HubSpot fetch, using your token, from your machine.

```
gtmos/
  mcp/        stdio server + tool definitions
  audit/      contact integrity engine, fetch, report
  funnel/     deal-stage leak engine, fetch, report
  calibrate/  scorer-vs-reality grading
```

## Tests

```bash
python -m pytest tests/
```

59 tests, all offline, no token required.

## License

[MIT](LICENSE)
