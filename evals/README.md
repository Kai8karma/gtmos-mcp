# Evals

The test suite proves the scorer is deterministic. It does not prove that a
high score means anything. That is an empirical question, and this folder
is where the answer goes.

## Status

**No calibration against real outcomes has been published yet.** Until one
is, treat the weights in
[`docs/scoring-methodology.md`](../docs/scoring-methodology.md) as a
reasoned opinion with good test coverage, not as a validated model.

## Run one

You need two files: the score each account had at some past date, and what
happened to it afterwards.

```bash
gtmos calibrate --scores scores.json --outcomes outcomes.json --out evals/2026-Q4
```

```jsonc
// scores.json
[{"account": "acme", "score": 82, "tier": "A"}]

// outcomes.json: won, lost, or open / no_response / pending
[{"account": "acme", "outcome": "won"}]
```

Only won and lost can grade a score. Open deals are counted as matched and
left out of the verdict.

To avoid grading the scorer on data it has already seen, score the accounts
with `as_of` set to a date before the outcomes were known.

## How the verdict is reached

The thresholds are fixed in `gtmos/calibrate/__init__.py` and were set
before any result was looked at.

| Threshold | Value | Meaning |
| --- | --- | --- |
| Minimum sample | 30 decided accounts | Below this the verdict is INSUFFICIENT, whatever the numbers show |
| Top-tier lift | 1.3x | The best tier must win at least 30% more often than the baseline |
| Separation | 5 points | Mean score of won accounts minus mean score of lost ones |

A NOT PREDICTIVE verdict is a successful run. It means the weights need
work, and it is worth publishing as it stands.

## Publishing a result

Commit the generated report under `evals/<period>/` with the date range,
the number of accounts, and the `as_of` used for scoring. Do not commit the
input files if they name real accounts. Link the report from the README
only once it exists.
