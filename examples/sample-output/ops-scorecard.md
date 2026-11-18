# GTM Ops Health Scorecard

_Generated 2026-11-18T16:35:52+00:00_

## Composite: 89.2 / 100 - Grade A

GTM ops stack is in solid shape (grade A); data_quality is the softest spot at 77.5.

25 contacts and 15 deals assessed. SLA threshold: 24h.

Composite weights (renormalized over scored dimensions): data_quality 35%, lifecycle 24%, routing 24%, automation 18%.
Excluded from composite (insufficient data): reporting.

## Dimensions

| Dimension | Score | Status |
|---|---|---|
| data_quality | 77.5 | HEALTHY |
| lifecycle | 100.0 | HEALTHY |
| routing | 88.0 | HEALTHY |
| automation | 100.0 | HEALTHY |
| reporting | n/a | INSUFFICIENT_DATA |

### data_quality

- portal integrity score 77.5 (delegated to gtmos.audit.engine)
- 2 duplicate cluster(s)
- 8% unreachable, 16% at risk (record score < 70)
- weakest sub-dimension: freshness (55.0)

### lifecycle

- 0/25 contacts (0%) are missing lifecyclestage
- deals not supplied, or deals carry no pipeline property - stage-divergence check skipped

### routing

- a first-touch signal (notes_last_contacted / hs_sales_email_last_replied / hs_analytics_first_timestamp) not present anywhere in this export - SLA check skipped
- 3/25 contacts (12%) have no owner - never routed

### automation

- 0/10 exported properties are present-but-never-populated across the sample (e.g. none)
- hs_lastmodifieddate not present anywhere in this export - staleness check skipped

### reporting

- no analytics-source / utm_source-like property present anywhere in this export - attribution checks skipped

## Ranked fixes

| Severity | Dimension | Finding | Fix |
|---|---|---|---|
| WATCH | reporting | no analytics-source / utm_source-like property present anywhere in this export - attribution checks skipped | Start capturing hs_analytics_source or a utm_source-equivalent at capture time so attribution can be measured. |

## Sequence

### Week 1

Nothing queued.

### Weeks 2-4

Nothing queued.

### This quarter

- [WATCH] reporting: Start capturing hs_analytics_source or a utm_source-equivalent at capture time so attribution can be measured.
