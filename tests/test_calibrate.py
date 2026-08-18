"""Tests for gtmos.calibrate.

The point of this module is that it must be WILLING TO RETURN A NEGATIVE.
So the suite pins both directions: a genuinely predictive scorer earns
PREDICTIVE, a random one earns NOT PREDICTIVE, and a tiny sample earns
INSUFFICIENT no matter how flattering the numbers look.
"""

from __future__ import annotations

import json

from gtmos.calibrate import (
    LIFT_THRESHOLD,
    MIN_SAMPLE,
    calibrate,
    join,
    render,
    spearman,
)
from gtmos.cli import build_parser


def _dataset(n: int, predictive: bool) -> tuple[list[dict], list[dict]]:
    """n accounts. If predictive, high scores win and low scores lose."""
    scores, outcomes = [], []
    for i in range(n):
        high = i % 2 == 0
        scores.append(
            {"account": f"acct{i}", "score": 90 if high else 40, "tier": "A" if high else "C"}
        )
        if predictive:
            outcome = "won" if high else "lost"
        else:
            outcome = "won" if i % 4 in (0, 1) else "lost"  # uncorrelated with score
        outcomes.append({"account": f"acct{i}", "outcome": outcome})
    return scores, outcomes


# ---------------------------------------------------------------------------
# math
# ---------------------------------------------------------------------------


def test_spearman_perfect_and_degenerate():
    assert spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert spearman([5, 5, 5], [1, 2, 3]) == 0.0   # no variance
    assert spearman([1], [1]) == 0.0                # undefined


def test_join_is_case_and_space_insensitive_and_inner():
    rows = join(
        [{"account": " Acme ", "score": 10, "tier": "A"}, {"account": "ghost", "score": 5, "tier": "C"}],
        [{"account": "ACME", "outcome": "Won"}],
    )
    assert len(rows) == 1
    assert rows[0]["account"] == "acme" and rows[0]["outcome"] == "won"


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------


def test_small_sample_is_insufficient_even_when_perfect():
    scores, outcomes = _dataset(10, predictive=True)
    cal = calibrate(scores, outcomes)
    assert cal.verdict == "INSUFFICIENT"
    assert "need" in " ".join(cal.reasons)


def test_predictive_scorer_earns_predictive():
    scores, outcomes = _dataset(60, predictive=True)
    cal = calibrate(scores, outcomes)
    assert cal.verdict == "PREDICTIVE"
    assert cal.top_tier_lift >= LIFT_THRESHOLD
    assert cal.separation > 0
    assert cal.rank_correlation > 0.9


def test_random_scorer_earns_not_predictive():
    scores, outcomes = _dataset(60, predictive=False)
    cal = calibrate(scores, outcomes)
    assert cal.verdict == "NOT PREDICTIVE"
    assert "not earning their keep" in " ".join(cal.reasons)


def test_open_deals_counted_as_matched_but_never_graded():
    scores = [{"account": f"a{i}", "score": 80, "tier": "A"} for i in range(5)]
    outcomes = [{"account": f"a{i}", "outcome": "open"} for i in range(5)]
    cal = calibrate(scores, outcomes)
    assert cal.matched == 5
    assert cal.decided == 0
    assert cal.verdict == "INSUFFICIENT"


def test_thresholds_are_constants_not_derived():
    # guards against the failure this module exists to prevent: thresholds
    # quietly moving to fit whatever result the data happened to produce
    assert MIN_SAMPLE == 30
    assert LIFT_THRESHOLD == 1.3


def test_render_states_verdict_and_thresholds():
    scores, outcomes = _dataset(60, predictive=True)
    body = render(calibrate(scores, outcomes))
    assert "**Verdict: PREDICTIVE**" in body
    assert "pre-registered before measurement" in body
    assert "| tier | n | wins |" in body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_end_to_end_writes_report(tmp_path, capsys):
    scores, outcomes = _dataset(60, predictive=True)
    sp, op = tmp_path / "s.json", tmp_path / "o.json"
    sp.write_text(json.dumps(scores), encoding="utf-8")
    op.write_text(json.dumps(outcomes), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        ["calibrate", "--scores", str(sp), "--outcomes", str(op), "--out", str(tmp_path / "c")]
    )
    assert args.func(args) == 0
    assert "PREDICTIVE" in capsys.readouterr().out
    assert (tmp_path / "c" / "calibration.md").is_file()


def test_cli_no_overlap_exits_2(tmp_path, capsys):
    sp, op = tmp_path / "s.json", tmp_path / "o.json"
    sp.write_text(json.dumps([{"account": "x", "score": 1, "tier": "A"}]), encoding="utf-8")
    op.write_text(json.dumps([{"account": "y", "outcome": "won"}]), encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(["calibrate", "--scores", str(sp), "--outcomes", str(op)])
    assert args.func(args) == 2
    assert "no scored accounts matched" in capsys.readouterr().out


def test_cli_bad_input_exits_1(tmp_path, capsys):
    sp, op = tmp_path / "s.json", tmp_path / "o.json"
    sp.write_text('{"not": "a list"}', encoding="utf-8")
    op.write_text("[]", encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(["calibrate", "--scores", str(sp), "--outcomes", str(op)])
    assert args.func(args) == 1
    assert "expected a JSON array" in capsys.readouterr().out
