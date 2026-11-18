# Funnel Leak Report

_Generated 2026-11-18T16:35:52+00:00_

## You are losing $53,000

$27,500 is closed-lost (3 deals). $51,000 is stalled and open across 6 deals (counted at 50% at-risk weight: $25,500). The single biggest contributor is **closedlost** at $27,500.

_3 deal(s) had a missing or zero amount and were imputed at $7,500 each (the median of this dataset's non-zero deal amounts) so they don't silently vanish from the totals above._

## Stage table

| Stage | Count | Amount |
|---|---|---|
| appointmentscheduled | 1 | $8,000 |
| qualifiedtobuy | 2 | $16,000 |
| presentationscheduled | 2 | $16,000 |
| decisionmakerboughtin | 1 | $7,500 |
| contractsent | 1 | $7,500 |
| closedwon | 4 | $39,000 |
| closedlost | 3 | $27,500 |
| somethingcustom | 1 | $2,000 |

## Conversion (survivorship snapshot — see engine docstring for caveat)

| From | To | From count | To count | Rate |
|---|---|---|---|---|
| appointmentscheduled | qualifiedtobuy | 11 | 10 | 91% |
| qualifiedtobuy | presentationscheduled | 10 | 8 | 80% |
| presentationscheduled | decisionmakerboughtin | 8 | 6 | 75% |
| decisionmakerboughtin | contractsent | 6 | 5 | 83% |
| contractsent | closedwon | 5 | 4 | 80% |

## Stalled deals

| Deal | Stage | Amount | Days stale | Owner |
|---|---|---|---|---|
| Deal F Stalled Qualified | qualifiedtobuy | $12,000 | 79 | owner2 |
| Deal G Fresh Presentation | presentationscheduled | $9,000 | 40 | owner1 |
| Deal E Stalled Appointment | appointmentscheduled | $8,000 | 110 | owner1 |
| Deal H Unvalued Fresh | decisionmakerboughtin | $7,500 | 45 | owner2 |
| Deal I Unvalued Stalled | contractsent | $7,500 | 140 | owner1 |
| Deal M Epoch Ms Stalled | presentationscheduled | $7,000 | 110 | owner3 |

## Leak by owner

| Owner | Leak |
|---|---|
| owner2 | $24,750 |
| owner1 | $17,250 |
| owner3 | $11,000 |

## Velocity: 31 median days to close (closed-won)
