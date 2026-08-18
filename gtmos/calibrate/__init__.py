"""gtmos.calibrate - does the score actually predict revenue?

A passing test suite proves the scorer is DETERMINISTIC. It says nothing
about whether a high score means a likelier win. That is an empirical
question, and until it is answered with real closed outcomes the weights
are just an opinion with good test coverage.

This module answers it, and is built to return a NEGATIVE verdict. The
thresholds are pre-registered below rather than chosen after seeing a
result, and small samples are reported as INSUFFICIENT rather than dressed
up as signal - claiming predictive power from nine deals is the same
failure as a green CI that gates nothing.

Pure stdlib, pure functions, no I/O here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WIN = "won"
LOSS = "lost"
OPEN_STATES = frozenset({"open", "no_response", "pending"})

# Pre-registered decision thresholds. Fixed before looking at any result.
MIN_SAMPLE = 30          # below this, no verdict is honest
LIFT_THRESHOLD = 1.3     # top tier must win >=30% more often than baseline
MIN_SEPARATION = 5.0     # mean(score|won) - mean(score|lost), in score points


@dataclass
class TierStats:
    tier: str
    n: int
    wins: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0


@dataclass
class Calibration:
    matched: int
    decided: int                      # won/lost only; open deals cannot grade a score
    baseline_win_rate: float
    tiers: list[TierStats]
    mean_score_won: float
    mean_score_lost: float
    separation: float
    rank_correlation: float
    verdict: str
    reasons: list[str] = field(default_factory=list)

    @property
    def top_tier(self) -> TierStats | None:
        graded = [t for t in self.tiers if t.n]
        return sorted(graded, key=lambda t: t.tier)[0] if graded else None

    @property
    def top_tier_lift(self) -> float:
        top = self.top_tier
        if not top or not self.baseline_win_rate:
            return 0.0
        return top.win_rate / self.baseline_win_rate


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties do not fabricate ordering."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation. 0.0 when undefined (n<2 or no variance)."""
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = _mean(rx), _mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy) ** 0.5


def join(scores: list[dict], outcomes: list[dict], key: str = "account") -> list[dict]:
    """Inner-join scored accounts to their real outcomes."""
    by_key = {str(o.get(key, "")).strip().lower(): o for o in outcomes if o.get(key)}
    joined = []
    for s in scores:
        k = str(s.get(key, "")).strip().lower()
        o = by_key.get(k)
        if not o:
            continue
        joined.append(
            {
                key: k,
                "score": float(s.get("score", 0)),
                "tier": str(s.get("tier", "?")),
                "outcome": str(o.get("outcome", "")).strip().lower(),
            }
        )
    return joined


def calibrate(scores: list[dict], outcomes: list[dict]) -> Calibration:
    rows = join(scores, outcomes)
    decided = [r for r in rows if r["outcome"] in (WIN, LOSS)]

    tier_map: dict[str, TierStats] = {}
    for r in decided:
        st = tier_map.setdefault(r["tier"], TierStats(tier=r["tier"], n=0, wins=0))
        st.n += 1
        if r["outcome"] == WIN:
            st.wins += 1

    won_scores = [r["score"] for r in decided if r["outcome"] == WIN]
    lost_scores = [r["score"] for r in decided if r["outcome"] == LOSS]
    mean_won, mean_lost = _mean(won_scores), _mean(lost_scores)
    separation = mean_won - mean_lost
    baseline = len(won_scores) / len(decided) if decided else 0.0
    corr = spearman(
        [r["score"] for r in decided],
        [1.0 if r["outcome"] == WIN else 0.0 for r in decided],
    )

    cal = Calibration(
        matched=len(rows),
        decided=len(decided),
        baseline_win_rate=baseline,
        tiers=sorted(tier_map.values(), key=lambda t: t.tier),
        mean_score_won=mean_won,
        mean_score_lost=mean_lost,
        separation=separation,
        rank_correlation=corr,
        verdict="INSUFFICIENT",
    )

    reasons: list[str] = []
    if cal.decided < MIN_SAMPLE:
        reasons.append(
            f"only {cal.decided} decided outcomes (need {MIN_SAMPLE}); "
            "any verdict here would be noise"
        )
        cal.verdict = "INSUFFICIENT"
    else:
        lift = cal.top_tier_lift
        lift_ok = lift >= LIFT_THRESHOLD
        sep_ok = separation >= MIN_SEPARATION
        if lift_ok and sep_ok:
            cal.verdict = "PREDICTIVE"
            reasons.append(f"top tier wins {lift:.2f}x the baseline rate (>= {LIFT_THRESHOLD})")
            reasons.append(f"won accounts score {separation:.1f} points above lost (>= {MIN_SEPARATION})")
        else:
            cal.verdict = "NOT PREDICTIVE"
            if not lift_ok:
                reasons.append(f"top-tier lift {lift:.2f}x is below the {LIFT_THRESHOLD} threshold")
            if not sep_ok:
                reasons.append(
                    f"won/lost score separation {separation:.1f} is below {MIN_SEPARATION} points"
                )
            reasons.append("the weights are not earning their keep; retune against these outcomes")

    cal.reasons = reasons
    return cal


def render(cal: Calibration) -> str:
    lines = [
        "# Score Calibration",
        "",
        f"**Verdict: {cal.verdict}**",
        "",
    ]
    for r in cal.reasons:
        lines.append(f"- {r}")
    lines += [
        "",
        "## Sample",
        f"- accounts matched to an outcome: {cal.matched}",
        f"- decided (won/lost, gradeable): {cal.decided}",
        f"- baseline win rate: {cal.baseline_win_rate:.1%}",
        "",
        "## Win rate by tier",
        "",
        "| tier | n | wins | win rate | lift vs baseline |",
        "|---|---|---|---|---|",
    ]
    for t in cal.tiers:
        lift = (t.win_rate / cal.baseline_win_rate) if cal.baseline_win_rate else 0.0
        lines.append(f"| {t.tier} | {t.n} | {t.wins} | {t.win_rate:.1%} | {lift:.2f}x |")
    lines += [
        "",
        "## Separation",
        f"- mean score, won: {cal.mean_score_won:.1f}",
        f"- mean score, lost: {cal.mean_score_lost:.1f}",
        f"- separation: {cal.separation:.1f} points",
        f"- rank correlation (score vs win): {cal.rank_correlation:.3f}",
        "",
        f"_Thresholds pre-registered before measurement: sample >= {MIN_SAMPLE}, "
        f"top-tier lift >= {LIFT_THRESHOLD}x, separation >= {MIN_SEPARATION} points._",
        "",
    ]
    return "\n".join(lines)
