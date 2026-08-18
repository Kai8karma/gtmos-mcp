"""Render a FunnelResult to funnel-report.md + funnel.json."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from gtmos.funnel.engine import FunnelResult


def render(result: FunnelResult, out_dir: str) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Funnel Leak Report")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines.append("")

    top_stage, top_stage_value = (
        max(result.leak_by_stage.items(), key=lambda kv: kv[1]) if result.leak_by_stage else ("(none)", 0.0)
    )
    lines.append(f"## You are losing ${result.leak_total:,.0f}")
    lines.append("")
    lines.append(
        f"${result.closedlost_value:,.0f} is closed-lost ({result.closedlost_count} deals). "
        f"${result.stalled_value:,.0f} is stalled and open across {result.stalled_count} deals "
        f"(counted at 50% at-risk weight: ${0.5 * result.stalled_value:,.0f}). "
        f"The single biggest contributor is **{top_stage}** at ${top_stage_value:,.0f}."
    )
    lines.append("")
    if result.unvalued_count:
        imputed = result.imputed_value or 0.0
        lines.append(
            f"_{result.unvalued_count} deal(s) had a missing or zero amount and were imputed at "
            f"${imputed:,.0f} each (the median of this dataset's non-zero deal amounts) so they don't "
            f"silently vanish from the totals above._"
        )
        lines.append("")

    lines.append("## Stage table")
    lines.append("")
    lines.append("| Stage | Count | Amount |")
    lines.append("|---|---|---|")
    for stage, count in result.stage_counts.items():
        lines.append(f"| {stage} | {count} | ${result.stage_amounts.get(stage, 0.0):,.0f} |")
    lines.append("")

    lines.append("## Conversion (survivorship snapshot — see engine docstring for caveat)")
    lines.append("")
    lines.append("| From | To | From count | To count | Rate |")
    lines.append("|---|---|---|---|---|")
    for step in result.conversion:
        rate_str = f"{step.rate:.0%}" if step.rate is not None else "n/a"
        lines.append(f"| {step.from_stage} | {step.to_stage} | {step.from_count} | {step.to_count} | {rate_str} |")
    lines.append("")

    lines.append("## Stalled deals")
    lines.append("")
    if not result.stalled_deals:
        lines.append("None found.")
    else:
        lines.append("| Deal | Stage | Amount | Days stale | Owner |")
        lines.append("|---|---|---|---|---|")
        for sd in sorted(result.stalled_deals, key=lambda s: s.amount, reverse=True):
            lines.append(
                f"| {sd.dealname or sd.id or '(unnamed)'} | {sd.dealstage} | ${sd.amount:,.0f} | "
                f"{sd.days_stale:.0f} | {sd.owner} |"
            )
    lines.append("")

    lines.append("## Leak by owner")
    lines.append("")
    lines.append("| Owner | Leak |")
    lines.append("|---|---|")
    for owner, value in sorted(result.leak_by_owner.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"| {owner} | ${value:,.0f} |")
    lines.append("")

    if result.velocity_median_days is not None:
        lines.append(f"## Velocity: {result.velocity_median_days:.0f} median days to close (closed-won)")
    else:
        lines.append("## Velocity: not enough closed-won date data to compute")
    lines.append("")

    report_path = out_path / "funnel-report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    payload = asdict(result)
    json_path = out_path / "funnel.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "leak_total": result.leak_total,
        "stalled_count": result.stalled_count,
        "report_path": str(report_path),
    }
