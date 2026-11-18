---
name: crm-audit
description: Audit a HubSpot CRM or a contacts/deals export with the gtmos tools and report what is leaking. Use when asked to audit, grade or health-check a CRM, find duplicates or stalled deals, check lead routing or attribution, or produce a fix list for RevOps.
---

# CRM audit with gtmos

The `gtmos` MCP server scores CRM data with deterministic engines. You read
its results and explain them; you never estimate a score yourself.

## Before you start

- Ask for the source if it was not given: a contacts export (and a deals
  export if there is one), or permission to read the live portal
  (`portal: true`, which needs `GTMOS_HUBSPOT_TOKEN`).
- If the sales pipeline is custom, ask for its stage ids in order and pass
  them as `stage_order` to `funnel_leak`. With the default order, deals in
  a custom pipeline are counted but never judged as stalled.
- Pass the same `as_of` date to every call in one audit, so the numbers
  agree with each other and can be reproduced later. Use today's date
  unless the user names another.

## Order of work

1. `cortex_scorecard` for the one-number view: composite grade, the five
   dimensions worst first, and the ranked fixes.
2. `ops_signals` for what is breaking in the stack itself (SLA breaches,
   property drift, sync-failure proxies).
3. `audit_crm` when data quality is the weak dimension: record-level
   integrity, duplicate clusters, the dollar-leak estimate (needs `acv`).
4. `funnel_leak` when there are deals: stalled pipeline and where it sits.
5. `cortex_graph` with `account` to drill into one account that the steps
   above flagged.
6. `cortex_proposals` last, for the policy-gated queue of suggested fixes.

Stop as soon as the question is answered. A request for "the duplicates"
needs step 3 only.

## Reading the results

- Each tool returns `structuredContent` with the numbers. Quote those, not
  figures re-derived from the prose.
- A dimension marked insufficient data was excluded from the composite
  because the export never carried the property it needs. Say which export
  column would unlock it; do not treat it as a zero.
- "Possible duplicates to review" are contacts who share a surname and a
  company but not a first name. They are often different people. Present
  them as a review list, never as duplicates.
- The dollar-leak figure is an order-of-magnitude estimate built on a 2%
  conversion assumption. State that assumption whenever you quote it.
- Proposals marked BLOCKED failed the policy. Report them with the reason;
  do not present them as recommendations.

## Hard limits

- These tools never change the CRM. Do not say a fix was applied. A
  proposal is a suggestion for a person, or for a separately authorised
  system, to act on.
- Report files are written under `out_dir`. Tell the user where they are.
- If a tool returns an error, show the message and what input would fix it.
  Do not retry with invented paths.
