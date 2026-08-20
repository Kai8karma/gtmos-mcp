"""Render a CortexResult to ops-scorecard.md + cortex-scores.json."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from gtmos.audit.report import grade_for
from gtmos.cortex.engine import CortexResult

SEQUENCE_ORDER = ["week_1", "weeks_2_4", "quarter"]
SEQUENCE_LABELS = {"week_1": "Week 1", "weeks_2_4": "Weeks 2-4", "quarter": "This quarter"}


def _verdict(result: CortexResult, grade: str) -> str:
    scored = {name: dim for name, dim in result.dimensions.items() if dim.score is not None}
    if not scored:
        return "Not enough data to verdict - every dimension came back insufficient."
    worst_name, worst_dim = min(scored.items(), key=lambda kv: kv[1].score)
    critical = sum(1 for f in result.fixes if f.severity == "CRITICAL")
    if critical:
        return (
            f"GTM ops stack needs urgent attention (grade {grade}); {critical} critical fix(es) open, "
            f"worst dimension is {worst_name} at {worst_dim.score:.1f}."
        )
    if grade in ("A", "B"):
        return f"GTM ops stack is in solid shape (grade {grade}); {worst_name} is the softest spot at {worst_dim.score:.1f}."
    return f"GTM ops stack is workable but leaking (grade {grade}); {worst_name} is the weakest dimension at {worst_dim.score:.1f}."


def render(result: CortexResult, out_dir: str) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    grade = grade_for(result.composite_score)
    verdict = _verdict(result, grade)

    lines: list[str] = []
    lines.append("# GTM Ops Health Scorecard")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"## Composite: {result.composite_score:.1f} / 100 - Grade {grade}")
    lines.append("")
    lines.append(verdict)
    lines.append("")
    lines.append(
        f"{result.total_contacts} contacts and {result.total_deals} deals assessed. "
        f"SLA threshold: {result.sla_hours:.0f}h."
    )
    lines.append("")
    weights_str = ", ".join(
        f"{name} {w:.0%}" for name, w in sorted(result.composite_weights.items(), key=lambda kv: -kv[1])
    )
    lines.append(f"Composite weights (renormalized over scored dimensions): {weights_str}.")
    if result.excluded_dimensions:
        lines.append(f"Excluded from composite (insufficient data): {', '.join(result.excluded_dimensions)}.")
    lines.append("")

    lines.append("## Dimensions")
    lines.append("")
    lines.append("| Dimension | Score | Status |")
    lines.append("|---|---|---|")
    for name, dim in result.dimensions.items():
        score_str = f"{dim.score:.1f}" if dim.score is not None else "n/a"
        lines.append(f"| {name} | {score_str} | {dim.status} |")
    lines.append("")

    for name, dim in result.dimensions.items():
        lines.append(f"### {name}")
        lines.append("")
        for evidence_line in dim.evidence:
            lines.append(f"- {evidence_line}")
        lines.append("")

    lines.append("## Ranked fixes")
    lines.append("")
    if not result.fixes:
        lines.append("None - every scored dimension is healthy.")
    else:
        lines.append("| Severity | Dimension | Finding | Fix |")
        lines.append("|---|---|---|---|")
        for fix in result.fixes:
            lines.append(f"| {fix.severity} | {fix.dimension} | {fix.finding} | {fix.fix} |")
    lines.append("")

    lines.append("## Sequence")
    lines.append("")
    for bucket in SEQUENCE_ORDER:
        bucket_fixes = [f for f in result.fixes if f.sequence == bucket]
        lines.append(f"### {SEQUENCE_LABELS[bucket]}")
        lines.append("")
        if not bucket_fixes:
            lines.append("Nothing queued.")
        else:
            for fix in bucket_fixes:
                lines.append(f"- [{fix.severity}] {fix.dimension}: {fix.fix}")
        lines.append("")

    report_path = out_path / "ops-scorecard.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    payload = {
        "composite_score": result.composite_score,
        "grade": grade,
        "verdict": verdict,
        "composite_weights": result.composite_weights,
        "excluded_dimensions": result.excluded_dimensions,
        "total_contacts": result.total_contacts,
        "total_deals": result.total_deals,
        "sla_hours": result.sla_hours,
        "dimensions": {name: asdict(dim) for name, dim in result.dimensions.items()},
        "fixes": [asdict(f) for f in result.fixes],
    }
    scores_path = out_path / "cortex-scores.json"
    scores_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "report_path": str(report_path),
        "scores_path": str(scores_path),
        "composite_score": result.composite_score,
        "grade": grade,
        "verdict": verdict,
    }
