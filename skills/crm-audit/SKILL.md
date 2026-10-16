---
name: crm-audit
description: Read-only HubSpot CRM audit. Use when someone asks what is wrong with their CRM, wants a data quality or RevOps health score, needs duplicate contacts found, wants to know where pipeline is stalling or revenue is leaking, or wants a prioritized cleanup plan. Requires the gtmos-mcp server.
---

# CRM Audit (gtmos-mcp)

A read-only audit of a HubSpot CRM, run through the `gtmos` MCP server. Every tool reads; nothing writes to the CRM, sends mail, or calls any API other than the user's own HubSpot endpoint. Scores are deterministic: report the numbers the tools return and never estimate or invent them.

## Setup check

Before the first tool call, confirm the `gtmos` server is connected (its six tools are listed). If not, tell the user to install it:

```bash
pip install gtmos-mcp
claude mcp add gtmos --env GTMOS_HUBSPOT_TOKEN=<token> -- gtmos mcp
```

Then pick the data source:

- **Live portal:** pass `"portal": true`. Needs `GTMOS_HUBSPOT_TOKEN`, a HubSpot private-app token with read-only scopes (`crm.objects.contacts.read`, `crm.objects.deals.read`).
- **Export file:** pass `contacts_file` and/or `deals_file` (JSON arrays in HubSpot export shape). No token and no network call. Prefer this when the user has no token or does not want a live connection.

If neither is available, ask the user for one. Do not guess file paths.

## Which tool for which question

| User asks | Tool | Key inputs |
|---|---|---|
| "How healthy is our CRM / RevOps?" (start here when vague) | `cortex_scorecard` | contacts, deals (optional), `sla_hours` |
| "How clean is our contact data?" "Find duplicates" | `audit_crm` | contacts, `acv` for the dollar estimate |
| "Where is pipeline stuck?" "Where are we leaking revenue?" | `funnel_leak` | deals, `stall_days` (default 21), `acv` |
| "What is breaking in our stack right now?" | `ops_signals` | contacts, deals (optional), `sla_hours` |
| "Tell me about account X" "Who owns which accounts?" | `cortex_graph` | contacts, deals, `account` (domain or company), `top` |
| "What should we fix first?" "Draft the cleanup" | `cortex_proposals` | contacts, deals, optional `policy_file` |

Ask for the user's average contract value (`acv`) and routing SLA (`sla_hours`) when the dollar figure or SLA breaches matter. If they don't know, run with defaults and say which defaults were used.

## Default flow for an open-ended audit

1. `cortex_scorecard` for the composite score, grade and weakest dimensions.
2. Go deeper only where the score is weak: `audit_crm` for data quality, `funnel_leak` for pipeline, `ops_signals` for routing and sync.
3. `cortex_proposals` to turn findings into a fix queue.

Stop after step 1 if the user only wanted a score.

## How to report results

- Lead with the verdict: score, grade, and the one or two weakest areas.
- Then the top three fixes in the tool's own order (week 1, weeks 2 to 4, quarter).
- Quote counts and dollar figures exactly as returned. Say "estimated" for any dollar figure that used a default or imputed `acv`.
- Point to the written report file (`./audit-out`, `./funnel-out` or `./cortex-out`; `ops_signals` writes no file) for the full detail rather than pasting it.
- Keep record-level personal data (emails, phones) out of the summary unless the user asks for it.

## Proposals are proposals

`cortex_proposals` never applies a change. Present each proposal with its target, before/after, confidence and blast radius, and show BLOCKED proposals with their reason. Do not apply proposals through any other CRM tool unless the user explicitly approves that specific change. `requires_human` cannot be turned off.

## Errors

- "GTMOS_HUBSPOT_TOKEN is not set": offer the export-file path or explain how to create the token.
- "token invalid or missing scope": the token lacks a read scope listed above.
- Empty or tiny portal: say the sample is too small for a meaningful score rather than over-interpreting it.
