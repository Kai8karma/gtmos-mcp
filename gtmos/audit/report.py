"""Render an AuditResult to integrity-report.md + scores.json."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from gtmos.audit.engine import AuditResult

GRADE_BANDS = [(85, "A"), (70, "B"), (55, "C"), (40, "D"), (0, "F")]

LEAK_CONVERSION_RATE = 0.02  # baseline assumption: 2% of unreachable records would have converted


def grade_for(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _histogram(records, bucket_size: int = 10) -> list[tuple[str, int]]:
    buckets = [0] * (100 // bucket_size + 1)
    for r in records:
        idx = min(int(r.overall // bucket_size), len(buckets) - 1)
        buckets[idx] += 1
    rows = []
    for i, count in enumerate(buckets):
        lo = i * bucket_size
        hi = min(lo + bucket_size - 1, 100)
        rows.append((f"{lo:>3}-{hi:<3}", count))
    return rows


def _leak_estimate(result: AuditResult, acv: float) -> dict:
    total = result.total_records
    if total == 0:
        return {"unreachable_pct": 0.0, "estimated_leak_usd": 0.0, "acv": acv}
    unreachable = sum(1 for r in result.records if r.validity < 40 or r.freshness <= 10)
    unreachable_pct = unreachable / total
    leak = unreachable_pct * total * (acv * LEAK_CONVERSION_RATE)
    return {"unreachable_pct": unreachable_pct, "unreachable_count": unreachable, "estimated_leak_usd": leak, "acv": acv}


def render(result: AuditResult, acv: float, out_dir: str) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    grade = grade_for(result.portal_score)
    ranked_dims = sorted(result.dimension_averages.items(), key=lambda kv: kv[1])
    histogram = _histogram(result.records)
    worst_records = sorted(result.records, key=lambda r: r.overall)[:20]
    leak = _leak_estimate(result, acv)

    lines: list[str] = []
    lines.append("# CRM Integrity Report")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"## Overall: {result.portal_score:.1f} / 100 — Grade {grade}")
    lines.append("")
    lines.append(f"{result.total_records} records audited.")
    lines.append("")
    lines.append(
        f"**Record mean {result.record_mean:.1f}** − dupe penalty {result.dupe_penalty:.1f} "
        f"({result.duped_record_share:.0%} of records sit in duplicate clusters) · "
        f"**{result.dead_share:.0%} dead** (unreachable) · "
        f"**{result.at_risk_share:.0%} at risk** (record score < 70)"
    )
    lines.append("")

    lines.append("## Dimension averages (worst first)")
    lines.append("")
    lines.append("| Dimension | Avg score |")
    lines.append("|---|---|")
    for name, avg in ranked_dims:
        lines.append(f"| {name} | {avg:.1f} |")
    lines.append("")

    lines.append("## Score distribution")
    lines.append("")
    lines.append("```")
    max_count = max((c for _, c in histogram), default=0) or 1
    for label, count in histogram:
        bar = "#" * int(round((count / max_count) * 40))
        lines.append(f"{label} | {bar} {count}")
    lines.append("```")
    lines.append("")

    lines.append("## Top 20 worst records")
    lines.append("")
    lines.append("| Email | Overall | Worst dimension |")
    lines.append("|---|---|---|")
    for r in worst_records:
        lines.append(f"| {r.email or '(missing)'} | {r.overall:.1f} | {r.worst_dimension} |")
    lines.append("")

    lines.append("## Duplicate clusters")
    lines.append("")
    if not result.dupe_clusters:
        lines.append("None found.")
    else:
        lines.append("| Type | Key | Records |")
        lines.append("|---|---|---|")
        for cluster in result.dupe_clusters:
            emails = ", ".join(e or "(missing)" for e in cluster.emails)
            lines.append(f"| {cluster.kind} | {cluster.key} | {emails} |")
    lines.append("")

    lines.append("## $-leak estimate")
    lines.append("")
    lines.append(
        f"{leak.get('unreachable_count', 0)} of {result.total_records} records "
        f"({leak['unreachable_pct'] * 100:.1f}%) are unreachable (validity < 40 or freshness <= 10)."
    )
    lines.append("")
    lines.append(
        f"Estimated leak: **${leak['estimated_leak_usd']:,.0f}** "
        f"(unreachable_pct × total_records × ACV × {LEAK_CONVERSION_RATE:.0%})"
    )
    lines.append("")
    lines.append(
        f"_Assumption: ACV = ${acv:,.0f}, baseline conversion rate = {LEAK_CONVERSION_RATE:.0%}. "
        "Both are editable inputs — pass --acv to change the contract value assumption._"
    )
    lines.append("")

    report_path = out_path / "integrity-report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    scores_payload = {
        "portal_score": result.portal_score,
        "grade": grade,
        "total_records": result.total_records,
        "dimension_averages": result.dimension_averages,
        "leak_estimate": leak,
        "dupe_clusters": [asdict(c) for c in result.dupe_clusters],
        "records": [asdict(r) for r in result.records],
    }
    scores_path = out_path / "scores.json"
    scores_path.write_text(json.dumps(scores_payload, indent=2), encoding="utf-8")

    return {"report_path": str(report_path), "scores_path": str(scores_path), "portal_score": result.portal_score, "grade": grade}
