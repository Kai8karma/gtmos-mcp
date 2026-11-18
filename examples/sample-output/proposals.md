# Cortex Proposals

_Generated 2026-11-18T16:35:52+00:00_

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

## Proposed

| ID | Sev | Agent | Action | Targets | Before | After | Conf | Blast |
|---|---|---|---|---|---|---|---|---|
| P-001 | CRITICAL | pipeline_steward | review_stalled_deal | Deal E Stalled Appointment | stage appointmentscheduled, 110d idle, $8,000, owner owner1 | owner owner1 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |
| P-002 | CRITICAL | pipeline_steward | review_stalled_deal | Deal F Stalled Qualified | stage qualifiedtobuy, 79d idle, $12,000, owner owner2 | owner owner2 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |
| P-003 | CRITICAL | pipeline_steward | review_stalled_deal | Deal H Unvalued Fresh | stage decisionmakerboughtin, 45d idle, $7,500, owner owner2 | owner owner2 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |
| P-004 | CRITICAL | pipeline_steward | review_stalled_deal | Deal I Unvalued Stalled | stage contractsent, 140d idle, $7,500, owner owner1 | owner owner1 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |
| P-005 | CRITICAL | pipeline_steward | review_stalled_deal | Deal M Epoch Ms Stalled | stage presentationscheduled, 110d idle, $7,000, owner owner3 | owner owner3 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |
| P-006 | SERIOUS | deduper | merge_duplicates | sales@brightpathlogistics.com, sales@brightpathlogistics.com, sales@brightpathlogistics.com | 3 separate records (exact_email: sales@brightpathlogistics.com) | 1 record - merge into #0 sales@brightpathlogistics.com (record score 95), keep the most complete value per field | 0.90 | 3 contacts (12%) |
| P-007 | SERIOUS | pipeline_steward | review_stalled_deal | Deal G Fresh Presentation | stage presentationscheduled, 40d idle, $9,000, owner owner1 | owner owner1 logs a next step or moves the deal to closedlost | 0.80 | 1 deals (7%) |

## Blocked by policy

| ID | Agent | Action | Targets | Reason |
|---|---|---|---|---|
| P-008 | router | assign_owner | zoya.ansari@bluecrestenergy.com, tanvi.saxena@orionvalveworks.com, harish.menon@sterlingcastparts.com | confidence 0.30 is below the 0.80 floor - needs a human decision |
| P-009 | deduper | merge_duplicates | priya.kapoor@meridiansteelworks.com, p.kapoor@meridiansteelworks.com | confidence 0.60 is below the 0.80 floor - needs a human decision |

## Evidence

### P-001 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 110 days against a 21d stall threshold

### P-002 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 79 days against a 21d stall threshold

### P-003 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 45 days against a 21d stall threshold

### P-004 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 140 days against a 21d stall threshold

### P-005 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 110 days against a 21d stall threshold

### P-006 [PROPOSED] deduper/merge_duplicates

- exact_email match on 'sales@brightpathlogistics.com'
- survivor #0 scores 95, highest in the cluster

### P-007 [PROPOSED] pipeline_steward/review_stalled_deal

- no activity for 40 days against a 21d stall threshold

### P-008 [BLOCKED] router/assign_owner

- 3 unowned contact(s) sit at accounts where no other contact has an owner either

### P-009 [BLOCKED] deduper/merge_duplicates

- fuzzy_name_company match on 'priya kapoor / meridian steelworks'
- survivor #3 scores 95, highest in the cluster
