# Tool transcript

Every tool run against the sample export, scored as of 2026-08-19 so the
numbers do not drift. Each section shows the arguments, the text result
and the structured result an agent receives.

## `audit_crm`

Arguments:

```json
{
  "contacts_file": "examples/contacts.sample.json",
  "acv": 9000,
  "as_of": "2026-08-19",
  "out_dir": "examples/sample-output"
}
```

Text result:

```text
Scored 25 records.
Portal integrity score: 77.5 (B).
Duplicate clusters found: 2.
Dimension averages (worst first):
  - freshness: 55.0
  - ownership: 88.0
  - validity: 91.9
  - consistency: 93.6
  - completeness: 94.6
Scored as of: 2026-08-19T00:00:00+00:00
Full report written to: examples/sample-output/integrity-report.md
```

Structured result:

```json
{
  "total_records": 25,
  "portal_score": 77.5,
  "grade": "B",
  "duplicate_clusters": 2,
  "possible_duplicate_clusters": 0,
  "dimension_averages": {
    "completeness": 94.6,
    "validity": 91.9,
    "freshness": 55.0,
    "ownership": 88.0,
    "consistency": 93.6
  },
  "as_of": "2026-08-19T00:00:00+00:00",
  "report_path": "examples/sample-output/integrity-report.md"
}
```

## `funnel_leak`

Arguments:

```json
{
  "deals_file": "examples/deals.sample.json",
  "as_of": "2026-08-19",
  "out_dir": "examples/sample-output"
}
```

Text result:

```text
Analyzed 15 deals.
Leaking $53,000 across 6 stalled deals (stall threshold: 21 days).
Scored as of: 2026-08-19T00:00:00+00:00
Full report written to: examples/sample-output/funnel-report.md
```

Structured result:

```json
{
  "total_deals": 15,
  "leak_total": 53000.0,
  "stalled_count": 6,
  "stall_days": 21,
  "stage_order": [
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
    "closedwon"
  ],
  "as_of": "2026-08-19T00:00:00+00:00",
  "report_path": "examples/sample-output/funnel-report.md"
}
```

## `cortex_scorecard`

Arguments:

```json
{
  "contacts_file": "examples/contacts.sample.json",
  "deals_file": "examples/deals.sample.json",
  "as_of": "2026-08-19",
  "out_dir": "examples/sample-output"
}
```

Text result:

```text
Assessed 25 contacts and 15 deals.
Composite GTM ops health: 89.2 (A).
GTM ops stack is in solid shape (grade A); data_quality is the softest spot at 77.5.
Dimensions (worst first):
  - data_quality: 77.5 (HEALTHY)
  - routing: 88.0 (HEALTHY)
  - lifecycle: 100.0 (HEALTHY)
  - automation: 100.0 (HEALTHY)
Insufficient data (excluded from composite): reporting.
1 ranked fix(es); worst is WATCH on reporting.
Scored as of: 2026-08-19T00:00:00+00:00
Full report written to: examples/sample-output/ops-scorecard.md
```

Structured result:

```json
{
  "total_contacts": 25,
  "total_deals": 15,
  "composite_score": 89.2,
  "grade": "A",
  "verdict": "GTM ops stack is in solid shape (grade A); data_quality is the softest spot at 77.5.",
  "dimensions": {
    "data_quality": {
      "score": 77.5,
      "status": "HEALTHY"
    },
    "lifecycle": {
      "score": 100.0,
      "status": "HEALTHY"
    },
    "routing": {
      "score": 88.0,
      "status": "HEALTHY"
    },
    "automation": {
      "score": 100.0,
      "status": "HEALTHY"
    },
    "reporting": {
      "score": null,
      "status": "INSUFFICIENT_DATA"
    }
  },
  "excluded_dimensions": [
    "reporting"
  ],
  "fixes": [
    {
      "severity": "WATCH",
      "dimension": "reporting",
      "finding": "no analytics-source / utm_source-like property present anywhere in this export - attribution checks skipped",
      "fix": "Start capturing hs_analytics_source or a utm_source-equivalent at capture time so attribution can be measured.",
      "sequence": "quarter"
    }
  ],
  "as_of": "2026-08-19T00:00:00+00:00",
  "report_path": "examples/sample-output/ops-scorecard.md"
}
```

## `ops_signals`

Arguments:

```json
{
  "contacts_file": "examples/contacts.sample.json",
  "deals_file": "examples/deals.sample.json",
  "as_of": "2026-08-19"
}
```

Text result:

```text
25 contacts, 15 deals scanned. SLA threshold: 24h.
[CRITICAL] routing_sla_breach: 25 - 25/25 contacts breached the 24h SLA
    - yusuf.qureshi@titanindustrialgases.com created 71712.0h ago, still untouched
    - meera.krishnan@apexpetrochem.com created 59832.0h ago, still untouched
    - vivaan.chauhan@pinnacleautoparts.com created 56640.0h ago, still untouched
    - reema.chowdhury@vantagepolymers.com created 49992.0h ago, still untouched
    - leela.subramaniam@crestlinepackaging.com created 48912.0h ago, still untouched
[INFO] property_drift_never_populated: 0 - 0/10 exported properties are present-but-never-populated
[INFO] property_drift_stale_modified: 0 - hs_lastmodifieddate not present anywhere in this export - skipped
[INFO] lifecycle_integrity: 0 - 0/25 missing lifecyclestage, 0/25 hold a non-standard stage value
[WATCH] sync_failure_malformed: 2 - 2 malformed email/date value(s) found across 25 contacts
    - asdf@asdf (malformed email)
    - notanemailnodomain (malformed email)
[INFO] sync_failure_no_owner_recent: 0 - owner and/or hs_lastmodifieddate not present anywhere in this export - skipped
[INFO] sync_failure_reversed_timestamps: 0 - createdate and/or a first-touch property not present anywhere in this export - skipped
Scored as of: 2026-08-19T00:00:00+00:00
```

Structured result:

```json
{
  "total_contacts": 25,
  "total_deals": 15,
  "sla_hours": 24.0,
  "signals": [
    {
      "severity": "CRITICAL",
      "name": "routing_sla_breach",
      "count": 25,
      "detail": "25/25 contacts breached the 24h SLA",
      "top_offenders": [
        "yusuf.qureshi@titanindustrialgases.com created 71712.0h ago, still untouched",
        "meera.krishnan@apexpetrochem.com created 59832.0h ago, still untouched",
        "vivaan.chauhan@pinnacleautoparts.com created 56640.0h ago, still untouched",
        "reema.chowdhury@vantagepolymers.com created 49992.0h ago, still untouched",
        "leela.subramaniam@crestlinepackaging.com created 48912.0h ago, still untouched"
      ]
    },
    {
      "severity": "INFO",
      "name": "property_drift_never_populated",
      "count": 0,
      "detail": "0/10 exported properties are present-but-never-populated",
      "top_offenders": []
    },
    {
      "severity": "INFO",
      "name": "property_drift_stale_modified",
      "count": 0,
      "detail": "hs_lastmodifieddate not present anywhere in this export - skipped",
      "top_offenders": []
    },
    {
      "severity": "INFO",
      "name": "lifecycle_integrity",
      "count": 0,
      "detail": "0/25 missing lifecyclestage, 0/25 hold a non-standard stage value",
      "top_offenders": []
    },
    {
      "severity": "WATCH",
      "name": "sync_failure_malformed",
      "count": 2,
      "detail": "2 malformed email/date value(s) found across 25 contacts",
      "top_offenders": [
        "asdf@asdf (malformed email)",
        "notanemailnodomain (malformed email)"
      ]
    },
    {
      "severity": "INFO",
      "name": "sync_failure_no_owner_recent",
      "count": 0,
      "detail": "owner and/or hs_lastmodifieddate not present anywhere in this export - skipped",
      "top_offenders": []
    },
    {
      "severity": "INFO",
      "name": "sync_failure_reversed_timestamps",
      "count": 0,
      "detail": "createdate and/or a first-touch property not present anywhere in this export - skipped",
      "top_offenders": []
    }
  ],
  "as_of": "2026-08-19T00:00:00+00:00"
}
```

## `cortex_graph`

Arguments:

```json
{
  "contacts_file": "examples/contacts.sample.json",
  "deals_file": "examples/deals.sample.json",
  "as_of": "2026-08-19",
  "out_dir": "examples/sample-output"
}
```

Text result:

```text
Context graph: 25 contacts, 22 accounts, 6 owners, 0 sources, 15 deals; 62 edges.
0 contact(s) carry no corporate domain or company name (no account); 15 deal(s) have no explicit account association (left unlinked, not guessed).
Top 10 accounts (open pipeline first):
  - Brightpath Logistics [brightpathlogistics.com]: 3 contact(s); owners: Ananya Iyer (3); no linked deals; flags: sla-breach:3
  - Meridian Steelworks [meridiansteelworks.com]: 2 contact(s); owners: Vikram Sethi (2); no linked deals; flags: sla-breach:2
  - Apex Petrochem [apexpetrochem.com]: 1 contact(s); owners: Vikram Sethi (1); no linked deals; flags: sla-breach:1
  - Bluecrest Energy [bluecrestenergy.com]: 1 contact(s); owners: no owner; no linked deals; flags: unowned:1, sla-breach:1
  - Crestline Packaging [crestlinepackaging.com]: 1 contact(s); owners: Ravi Suresh (1); no linked deals; flags: sla-breach:1
  - Duneshield Cement [duneshieldcement.com]: 1 contact(s); owners: Vikram Sethi (1); no linked deals; flags: sla-breach:1
  - Ferrotech Alloys [ferrotechalloys.com]: 1 contact(s); owners: Ravi Suresh (1); no linked deals; flags: sla-breach:1
  - Granitewave Exports [granitewaveexports.com]: 1 contact(s); owners: Vikram Sethi (1); no linked deals; flags: sla-breach:1
  - Haldia Port Services [haldiaportservices.com]: 1 contact(s); owners: Ananya Iyer (1); no linked deals; flags: sla-breach:1
  - Coastal Freight Co [name:coastal freight]: 1 contact(s); owners: Ravi Suresh (1); no linked deals; flags: sla-breach:1
22/22 accounts carry at least one flag.
Scored as of: 2026-08-19T00:00:00+00:00
Graph written to: examples/sample-output/context-graph.json (report: examples/sample-output/context-graph.md)
```

Structured result:

```json
{
  "accounts": 22,
  "nodes": 68,
  "edges": 62,
  "as_of": "2026-08-19T00:00:00+00:00",
  "graph_path": "examples/sample-output/context-graph.json",
  "report_path": "examples/sample-output/context-graph.md"
}
```

## `cortex_proposals`

Arguments:

```json
{
  "contacts_file": "examples/contacts.sample.json",
  "deals_file": "examples/deals.sample.json",
  "as_of": "2026-08-19",
  "out_dir": "examples/sample-output",
  "policy_file": "examples/policy.strict.json"
}
```

Text result:

```text
9 proposal(s) from 6 agents on 25 contacts / 15 deals: 7 PROPOSED, 2 BLOCKED.
policy strict: max 10 records or 10% of universe per proposal, confidence floor 0.80, human-applied. Nothing is applied by this tool.
Top 5 proposed:
  - [CRITICAL] P-001 pipeline_steward/review_stalled_deal: owner owner1 logs a next step or moves the deal to closedlost - Deal E Stalled Appointment (conf 0.80)
  - [CRITICAL] P-002 pipeline_steward/review_stalled_deal: owner owner2 logs a next step or moves the deal to closedlost - Deal F Stalled Qualified (conf 0.80)
  - [CRITICAL] P-003 pipeline_steward/review_stalled_deal: owner owner2 logs a next step or moves the deal to closedlost - Deal H Unvalued Fresh (conf 0.80)
  - [CRITICAL] P-004 pipeline_steward/review_stalled_deal: owner owner1 logs a next step or moves the deal to closedlost - Deal I Unvalued Stalled (conf 0.80)
  - [CRITICAL] P-005 pipeline_steward/review_stalled_deal: owner owner3 logs a next step or moves the deal to closedlost - Deal M Epoch Ms Stalled (conf 0.80)
Blocked (kept visible):
  - P-008 router/assign_owner on 3 contacts: confidence 0.30 is below the 0.80 floor - needs a human decision
  - P-009 deduper/merge_duplicates on 2 contacts: confidence 0.60 is below the 0.80 floor - needs a human decision
Scored as of: 2026-08-19T00:00:00+00:00
Full queue written to: examples/sample-output/proposals.md
```

Structured result:

```json
{
  "policy": "strict",
  "proposed": 7,
  "blocked": 2,
  "proposals": [
    {
      "id": "P-001",
      "status": "PROPOSED",
      "agent": "pipeline_steward",
      "action": "review_stalled_deal",
      "severity": "CRITICAL",
      "targets": [
        "Deal E Stalled Appointment"
      ],
      "before": "stage appointmentscheduled, 110d idle, $8,000, owner owner1",
      "after": "owner owner1 logs a next step or moves the deal to closedlost",
      "confidence": 0.8,
      "blast_radius": 1,
      "blocked_reason": null
    },
    "... 8 more"
  ],
  "as_of": "2026-08-19T00:00:00+00:00",
  "report_path": "examples/sample-output/proposals.md",
  "proposals_path": "examples/sample-output/proposals.json"
}
```
