"""Deterministic deal-stage revenue-leak engine. Pure Python — no LLM, no network.

Canonical deal fields (HubSpot-ish, all optional / None-able unless noted):
    id, dealname, amount (str|float|None), dealstage, createdate,
    hs_lastmodifieddate, closedate, hubspot_owner_id

Conversion-rate caveat: HubSpot's object snapshot (offline export or a
single /crm/v3/objects/deals page) gives us each deal's *current* stage,
not the sequence of stages it passed through. There is no stage-history
event stream here. So "conversion" below is a survivorship approximation
computed from the current-state snapshot: a deal currently sitting at
stage N is assumed to have passed through stages 0..N-1. This is honest
for a healthy pipeline snapshot but will misstate true historical
conversion if deals regress backward through stages (HubSpot allows
this) — treat it as a stage-inventory funnel, not an audited cohort
funnel. Closed-lost deals are excluded from conversion counts entirely,
since a snapshot cannot tell us which stage they died at.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_STAGE_ORDER = [
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
    "closedwon",
]

TERMINAL_LOST_STAGE = "closedlost"
DEFAULT_STALL_DAYS = 21


def parse_amount(value) -> float | None:
    """Defensively parse a deal amount from str | int | float | None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_date(value) -> datetime | None:
    """Defensively parse ISO8601 or epoch-ms strings/numbers into a UTC datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        ts = ts / 1000.0 if ts > 10**11 else ts
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            ts = float(raw)
            ts = ts / 1000.0 if ts > 10**11 else ts
            try:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def all_amounts_missing(deals: list[dict]) -> bool:
    """True if every deal has a missing or zero amount — the imputation fallback trigger."""
    return all(not parse_amount(d.get("amount")) for d in deals)


@dataclass
class ConversionStep:
    from_stage: str
    to_stage: str
    from_count: int
    to_count: int
    rate: float | None


@dataclass
class StalledDeal:
    id: str | None
    dealname: str | None
    dealstage: str
    amount: float
    days_stale: float
    owner: str


@dataclass
class FunnelResult:
    total_deals: int
    stage_counts: dict[str, int]
    stage_amounts: dict[str, float]
    conversion: list[ConversionStep]
    stalled_deals: list[StalledDeal]
    stalled_count: int
    stalled_value: float
    closedlost_count: int
    closedlost_value: float
    leak_total: float
    leak_by_stage: dict[str, float]
    leak_by_owner: dict[str, float]
    unvalued_count: int
    unvalued_deal_ids: list = field(default_factory=list)
    imputed_value: float | None = None
    velocity_median_days: float | None = None
    now_ts: float = 0.0


def _owner_of(deal: dict) -> str:
    owner = deal.get("hubspot_owner_id")
    return str(owner) if owner not in (None, "") else "unassigned"


def run_engine(
    deals: list[dict],
    stage_order: list[str] | None = None,
    stall_days: dict[str, int] | int = DEFAULT_STALL_DAYS,
    now: float | None = None,
) -> FunnelResult:
    stage_order = list(stage_order) if stage_order else list(DEFAULT_STAGE_ORDER)
    now_ts = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    total = len(deals)
    if total == 0:
        return FunnelResult(
            total_deals=0,
            stage_counts={},
            stage_amounts={},
            conversion=[],
            stalled_deals=[],
            stalled_count=0,
            stalled_value=0.0,
            closedlost_count=0,
            closedlost_value=0.0,
            leak_total=0.0,
            leak_by_stage={},
            leak_by_owner={},
            unvalued_count=0,
            now_ts=now_ts,
        )

    # Imputation: unvalued deals (missing/zero amount) get the median of the
    # dataset's non-zero amounts as a stand-in value, so a leaky stage full
    # of blank amounts doesn't invisibly disappear from the leak total.
    # Every dollar aggregate below (stage table, stalled value, closed-lost
    # value, leak) uses this single "effective amount" consistently.
    raw_amounts = [parse_amount(d.get("amount")) for d in deals]
    nonzero_amounts = [a for a in raw_amounts if a]
    imputed_value = statistics.median(nonzero_amounts) if nonzero_amounts else None

    unvalued_ids: list = []
    effective_amounts: list[float] = []
    for d, raw in zip(deals, raw_amounts):
        if raw:
            effective_amounts.append(raw)
        else:
            unvalued_ids.append(d.get("id"))
            effective_amounts.append(imputed_value or 0.0)

    stage_index = {stage: i for i, stage in enumerate(stage_order)}
    won_stage = stage_order[-1] if stage_order else None

    # 1. Stage table — every distinct dealstage observed, known stages first.
    stage_counts: dict[str, int] = {s: 0 for s in stage_order}
    stage_amounts: dict[str, float] = {s: 0.0 for s in stage_order}
    stage_counts.setdefault(TERMINAL_LOST_STAGE, 0)
    stage_amounts.setdefault(TERMINAL_LOST_STAGE, 0.0)
    for d, amt in zip(deals, effective_amounts):
        stage = d.get("dealstage") or "(unknown)"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        stage_amounts[stage] = stage_amounts.get(stage, 0.0) + amt

    # 2. Conversion — survivorship snapshot, see module docstring.
    idx_by_deal = [stage_index.get(d.get("dealstage")) for d in deals]
    conversion: list[ConversionStep] = []
    for i in range(len(stage_order) - 1):
        from_stage, to_stage = stage_order[i], stage_order[i + 1]
        from_count = sum(1 for idx in idx_by_deal if idx is not None and idx >= i)
        to_count = sum(1 for idx in idx_by_deal if idx is not None and idx >= i + 1)
        rate = (to_count / from_count) if from_count else None
        conversion.append(ConversionStep(from_stage, to_stage, from_count, to_count, rate))

    # 3. Stalled deals — open deal (known stage, not won/lost) whose
    # last-modified date is older than the stall threshold for its stage.
    stalled_deals: list[StalledDeal] = []
    for d, amt in zip(deals, effective_amounts):
        stage = d.get("dealstage")
        if stage not in stage_index or stage == won_stage:
            continue
        last_modified = parse_date(d.get("hs_lastmodifieddate"))
        if last_modified is None:
            continue
        threshold = stall_days.get(stage, DEFAULT_STALL_DAYS) if isinstance(stall_days, dict) else stall_days
        days_stale = (now_dt - last_modified).total_seconds() / 86400.0
        if days_stale > threshold:
            stalled_deals.append(
                StalledDeal(
                    id=d.get("id"),
                    dealname=d.get("dealname"),
                    dealstage=stage,
                    amount=amt,
                    days_stale=days_stale,
                    owner=_owner_of(d),
                )
            )
    stalled_value = sum(sd.amount for sd in stalled_deals)

    # 4. Leak model.
    closedlost_amounts = [
        amt for d, amt in zip(deals, effective_amounts) if d.get("dealstage") == TERMINAL_LOST_STAGE
    ]
    closedlost_count = len(closedlost_amounts)
    closedlost_value = sum(closedlost_amounts)

    leak_total = closedlost_value + 0.5 * stalled_value

    leak_by_stage: dict[str, float] = {TERMINAL_LOST_STAGE: closedlost_value}
    for sd in stalled_deals:
        leak_by_stage[sd.dealstage] = leak_by_stage.get(sd.dealstage, 0.0) + 0.5 * sd.amount

    leak_by_owner: dict[str, float] = {}
    for d, amt in zip(deals, effective_amounts):
        if d.get("dealstage") == TERMINAL_LOST_STAGE:
            owner = _owner_of(d)
            leak_by_owner[owner] = leak_by_owner.get(owner, 0.0) + amt
    for sd in stalled_deals:
        leak_by_owner[sd.owner] = leak_by_owner.get(sd.owner, 0.0) + 0.5 * sd.amount

    # 5. Velocity — median days-in-pipeline for closed-won deals.
    won_days: list[float] = []
    for d in deals:
        if d.get("dealstage") != won_stage:
            continue
        created = parse_date(d.get("createdate"))
        closed = parse_date(d.get("closedate"))
        if created is None or closed is None:
            continue
        won_days.append((closed - created).total_seconds() / 86400.0)
    velocity_median_days = statistics.median(won_days) if won_days else None

    return FunnelResult(
        total_deals=total,
        stage_counts=stage_counts,
        stage_amounts=stage_amounts,
        conversion=conversion,
        stalled_deals=stalled_deals,
        stalled_count=len(stalled_deals),
        stalled_value=stalled_value,
        closedlost_count=closedlost_count,
        closedlost_value=closedlost_value,
        leak_total=leak_total,
        leak_by_stage=leak_by_stage,
        leak_by_owner=leak_by_owner,
        unvalued_count=len(unvalued_ids),
        unvalued_deal_ids=unvalued_ids,
        imputed_value=imputed_value,
        velocity_median_days=velocity_median_days,
        now_ts=now_ts,
    )
