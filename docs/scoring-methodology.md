# Scoring methodology

Every number this package reports comes from a deterministic function. No
model is involved in scoring. This page states each rule as the code
implements it, so that you can check a score by hand or reimplement it.

If this page and the code ever disagree, the code is what ran and this page
is the bug. Each section names the module it describes.

## Reproducibility

Freshness, SLA and stall checks compare record dates to a reference moment.
By default that moment is the time of the call, so the same export scores
lower as it ages. Pass `as_of` (an ISO 8601 date or datetime) to fix it.
The same export with the same `as_of` always yields the same result.

The sample export in `examples/` scores 77.5 (B) as of 2026-08-19.

## 1. Record integrity (`gtmos/audit/engine.py`)

Each contact gets five scores from 0 to 100 and a weighted overall score.

| Dimension | Weight |
| --- | --- |
| Completeness | 0.30 |
| Validity | 0.25 |
| Freshness | 0.25 |
| Ownership | 0.10 |
| Consistency | 0.10 |

A value counts as missing if it is absent or one of the placeholder tokens
`test`, `asdf`, `n/a`, `na`, `unknown`, `none`, `-`, `null`, `n.a.`, `xx`,
`xxx` or an empty string, compared case-insensitively.

### Completeness

Points for each field that is present. They sum to 100.

| Field | Points |
| --- | --- |
| Email | 30 |
| Company | 20 |
| Job title | 15 |
| Owner | 15 |
| Last name | 10 |
| Phone | 10 |

### Validity

A weighted average over the parts that are populated. A part that is empty
is left out of the average, because an empty field is a completeness
failure and is not counted twice. A record with none of these parts
populated scores 0.

| Part | Weight | Passes when |
| --- | --- | --- |
| Email | 50 | It matches `local@domain.tld`, the local part is not a placeholder token, the first label of the domain is not a placeholder or one of `example`, `test`, `email`, `domain`, and the domain contains none of the disposable-mail markers (`mailinator`, `tempmail`, `yopmail`, `guerrillamail` and eight others). |
| Phone | 25 | It has 7 to 15 digits after everything else is stripped. |
| Text fields | 25 | Scored as the share of populated text fields (first name, last name, company, job title, lifecycle stage, owner) that are not placeholder tokens. |

### Freshness

Days between the last activity date and the reference moment.

| Days since last activity | Score |
| --- | --- |
| 30 or fewer | 100 |
| 31 to 90 | 80 |
| 91 to 180 | 55 |
| 181 to 365 | 30 |
| More than 365 | 10 |
| No date, or one that cannot be parsed | 0 |

### Ownership

100 if the record has an owner, otherwise 0. The export does not say
whether an owner is still an active user, so a departed owner scores 100.

### Consistency

| Condition | Score |
| --- | --- |
| Lifecycle stage missing | 0 |
| Stage is `customer` or `opportunity`, and there is no parseable activity date or the last activity is more than 365 days old | 20 |
| Anything else | 100 |

### Overall record score

```
overall = 0.30 * completeness + 0.25 * validity + 0.25 * freshness
        + 0.10 * ownership    + 0.10 * consistency
```

**Unreachability cap.** A record with no valid email and no valid phone
cannot be contacted, so its overall score is capped at 45 whatever else it
has.

### Portal score and grade

```
portal = mean(overall) - min(10, duplicated_share * 25)
```

`duplicated_share` is the fraction of records that sit in any duplicate
cluster. The penalty is capped at 10 points.

| Grade | Portal score |
| --- | --- |
| A | 85 or more |
| B | 70 to 84.9 |
| C | 55 to 69.9 |
| D | 40 to 54.9 |
| F | Below 40 |

## 2. Duplicates (`gtmos/audit/engine.py`)

Three kinds of cluster are reported. The first two count towards
`duplicated_share` and can become `merge_duplicates` proposals. The third
does neither.

| Kind | Rule |
| --- | --- |
| `exact_email` | Two or more records share an email address, compared lower-case and trimmed. |
| `fuzzy_name_company` | Same last name and same normalised company, **and** the first names agree. An initial counts as agreement only if it matches exactly one full first name in that group: "P Kapoor" joins "Priya Kapoor", but not when "Pooja Kapoor" is also there. A record with no first name is never matched this way. |
| `possible_name_company` | Same last name and same normalised company where the first names do not confirm a match. Often two different people. Listed for review only. |

Company names are normalised by lower-casing, removing commas and full
stops, collapsing spaces and stripping trailing legal suffixes (`inc`,
`incorporated`, `llc`, `ltd`, `limited`, `corp`, `corporation`, `co`,
`company`, `plc`, `gmbh`, `pvt`) until none is left. A one-word name is
never stripped.

Known limits: there is no edit-distance matching, so "Jon" and "John" or
"Acme" and "Acme Labs" are not matched. Nicknames are not resolved.

## 3. Dollar-leak estimate (`gtmos/audit/report.py`)

```
unreachable = records with validity < 40 or freshness <= 10
leak        = unreachable * ACV * 0.02
```

The 2% is a fixed baseline conversion assumption and ACV is your input.
This is an order-of-magnitude figure for a revenue conversation. It is not
a forecast, and it does not adjust for fit or stage.

## 4. Funnel leak (`gtmos/funnel/engine.py`)

Works from a snapshot of deals: each deal's current stage, amount and last
modified date. There is no stage history in a snapshot.

- **Stage order** defaults to HubSpot's stock sales pipeline
  (`appointmentscheduled` ... `closedwon`). A custom pipeline needs its own
  ids passed as `stage_order`, first stage to won stage. Deals in a stage
  that is not in the order are counted but never judged as stalled.
- **Missing amounts** are replaced by the median of the non-zero amounts in
  the same export, so a stage full of blank amounts still carries value.
  The report says how many deals were valued this way.
- **Conversion** is a survivorship estimate: a deal now at stage N is
  assumed to have passed through every earlier stage. Closed-lost deals are
  excluded because a snapshot cannot say where they died. Read it as stage
  inventory, not as an audited cohort funnel.
- **Stalled** means an open deal in a known stage, other than the won
  stage, whose last modified date is more than `stall_days` old (default
  21).
- **Leak:**

```
leak_total = closed_lost_value + 0.5 * stalled_value
```

Stalled value is weighted at one half because a stalled deal is at risk,
not lost.

## 5. GTM ops health scorecard (`gtmos/cortex/engine.py`)

Five dimensions, each 0 to 100.

| Dimension | Weight | Built from |
| --- | --- | --- |
| Data quality | 0.30 | The portal integrity score from section 1, unchanged. |
| Lifecycle | 0.20 | Share of contacts with a lifecycle stage (0.6). When deals come from two or more pipelines, the share of stage ids the pipelines have in common (0.4). |
| Routing | 0.20 | Share of judged contacts first touched within the SLA, `sla_hours` from create date, default 24 (0.65). Share of contacts with an owner (0.35). |
| Automation | 0.15 | Share of exported properties that are populated on at least one record (0.5). Share of records with a readable modified date that were modified within the last 365 days (0.5). |
| Reporting | 0.15 | Share of contacts with a source value (0.6). Among email domains with a source on two or more contacts, the share whose contacts agree on it (0.4). |

| Status | Score |
| --- | --- |
| HEALTHY | 75 or more |
| WATCH | 60 to 74.9 |
| SERIOUS | Below 60 |

**Insufficient data.** If the property a dimension needs never appears as a
key anywhere in the export, the dimension is not scored. It is left out of
the composite, and the remaining weights are rescaled to sum to 1. A
missing column is never treated as a zero.

**Composite and grade.** The composite is the weighted mean of the scored
dimensions and takes the same A to F bands as section 1.

**Fixes.** Every dimension that is not HEALTHY yields one fix: CRITICAL
below 30 (do in week 1), SERIOUS below 60 (weeks 2 to 4), WATCH otherwise
(this quarter).

Known limits:

- HubSpot assigns stage ids per pipeline, so two pipelines usually share
  none. The pipeline-overlap part of Lifecycle therefore reads near zero on
  most portals with more than one pipeline. Treat a low Lifecycle score on
  such a portal with care.
- Routing judges first touch from `notes_last_contacted`,
  `hs_sales_email_last_replied`, then `hs_analytics_first_timestamp`, in
  that order. A record whose first touch is earlier than its create date is
  excluded as corrupt and reported.

## 6. Proposals (`gtmos/cortex/govern.py`)

Six agents read the context graph and draft proposals. They are rule-based
functions, not models. Each proposal carries a fixed confidence.

| Agent | Action | Confidence |
| --- | --- | --- |
| Router | Assign unowned contacts to the account's dominant owner | Share of the account's owned contacts held by that owner; 0.30 when no contact at the account has an owner |
| Deduper | Merge a duplicate cluster into its highest-scoring record | 0.90 for `exact_email`, 0.60 for `fuzzy_name_company` |
| Lifecycle steward | Set a missing lifecycle stage | 0.80 for `customer` backed by a won deal, 0.70 for `opportunity` backed by an open deal, 0.50 for `lead` backed by a recorded touch, 0.30 with no evidence (left for a person to pick) |
| Attribution steward | Align a conflicting or missing source to the domain majority | The majority's share; 0.40 on a tie; 0.70 for a missing source |
| Pipeline steward | Send a stalled deal to its owner for review | 0.80 |
| Schema steward | Retire properties that are never populated | 0.60 |

Every proposal is then checked against a policy, in this order:

1. The action is on the allow-list.
2. It touches no more than `max_blast_records` records (default 25).
3. It touches no more than `max_blast_share` of its universe (default 25%).
   Proposals touching fewer than 5 records skip this check, so that a
   two-record merge in a small export is not blocked on share alone.
4. Its confidence is at least `min_confidence` (default 0.50).

A proposal that fails stays in the output as BLOCKED with the reason. No
proposal is ever applied by this package, and `requires_human` cannot be
turned off.

## Does the score predict anything?

A deterministic score is repeatable. That does not make it predictive.
`gtmos calibrate` grades scores against closed outcomes with thresholds
fixed in advance. See [`evals/`](../evals/README.md).
