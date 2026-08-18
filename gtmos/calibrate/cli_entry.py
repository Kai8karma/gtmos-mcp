"""CLI entry for `gtmos calibrate` - grade the scorer against real outcomes.

    gtmos calibrate --scores scores.json --outcomes outcomes.json [--out DIR]

scores.json:    [{"account": "acme", "score": 82, "tier": "A"}, ...]
outcomes.json:  [{"account": "acme", "outcome": "won"}, ...]
                outcome is one of: won, lost, open/no_response/pending
                (only won/lost can grade a score; open deals are counted
                 as matched but excluded from the verdict)

Exit codes: 0 verdict reached (including NOT PREDICTIVE - a true negative
is a successful run), 1 bad input, 2 nothing to grade.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtmos.calibrate import calibrate, render


def _load(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of objects")
    return data


def run(args: argparse.Namespace) -> int:
    if getattr(args, "dry_run", False):
        print(f"[dry-run] would calibrate {args.scores} against {args.outcomes}")
        return 0

    try:
        scores = _load(args.scores)
        outcomes = _load(args.outcomes)
    except (OSError, ValueError) as exc:
        print(f"calibrate: {exc}")
        return 1

    cal = calibrate(scores, outcomes)
    if cal.matched == 0:
        print("calibrate: no scored accounts matched an outcome (check the 'account' keys)")
        return 2

    out_dir = Path(getattr(args, "out", None) or "./calibrate-out")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "calibration.md"
    report_path.write_text(render(cal), encoding="utf-8")

    print(
        f"calibrate: {cal.verdict} - {cal.decided} decided outcomes, "
        f"top-tier lift {cal.top_tier_lift:.2f}x, separation {cal.separation:.1f} pts "
        f"- report: {report_path}"
    )
    for reason in cal.reasons:
        print(f"  - {reason}")
    return 0
