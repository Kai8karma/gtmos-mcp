"""Deterministic GTM Ops Health scoring. Pure Python - no LLM, no network.

Five dimensions, each scored 0-100 with a HEALTHY (>=75) / WATCH (60-74) /
SERIOUS (<60) status:

    data_quality  delegates to gtmos.audit.engine on the same contacts
                  (completeness/validity/freshness/ownership/consistency
                  + duplicate clusters). Never reimplemented here.
    lifecycle     percent of contacts missing lifecyclestage, plus - if
                  deals are supplied and carry a "pipeline" property -
                  stage-name divergence across pipelines.
    routing       time from createdate to first touch (p50/p95 vs an SLA),
                  plus percent of contacts with no owner (never routed).
    automation    property-health proxy: fraction of exported properties
                  that are present but never populated, and records whose
                  hs_lastmodifieddate is stale beyond 365 days.
    reporting     attribution consistency: percent of contacts missing an
                  analytics-source value, plus conflicting source values
                  within the same email domain.

A dimension whose required property never appears as a key anywhere in the
record sample (not merely blank on some records - genuinely absent from
the export) is scored None and marked "insufficient data": it is excluded
from the composite rather than guessed at. See _key_ever_present().

compute_signals() is the sibling read: the same underlying facts, reframed
as a terse severity-tagged signal list about the stack itself (SLA
breaches, property drift, lifecycle breaks, sync-failure proxies) instead
of a scored dimension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from gtmos.audit import engine as audit_engine
from gtmos.audit import fetch as audit_fetch
from gtmos.funnel import engine as funnel_engine

DEFAULT_SLA_HOURS = 24.0
HEALTHY_THRESHOLD = 75.0
WATCH_THRESHOLD = 60.0
STALE_MODIFIED_DAYS = 365
RECENT_SYNC_DAYS = 30

# composite weights, judgment call: data_quality carries the most weight
# because it is the foundational, most-validated dimension (it delegates
# to the existing audit engine); the other four split the remainder by
# how directly each maps to lost revenue (lifecycle/routing) vs stack
# hygiene (automation/reporting). Renormalized over available dimensions
# at composite time - see _composite().
DIMENSION_WEIGHTS = {
    "data_quality": 0.30,
    "lifecycle": 0.20,
    "routing": 0.20,
    "automation": 0.15,
    "reporting": 0.15,
}

LIFECYCLE_ALIASES = audit_fetch.FIELD_ALIASES["lifecyclestage"]
OWNER_ALIASES = audit_fetch.FIELD_ALIASES["owner"]
CREATEDATE_ALIASES = audit_fetch.FIELD_ALIASES["createdate"]
EMAIL_ALIASES = audit_fetch.FIELD_ALIASES["email"]

# First-touch signal, checked in this priority order per contact - judgment
# call: these are the closest real HubSpot properties to "when did a human
# first reach this lead": an outbound touch (notes_last_contacted), an
# inbound reply (hs_sales_email_last_replied), then HubSpot's own
# analytics first-touch timestamp as the final fallback.
FIRST_TOUCH_ALIASES = ["notes_last_contacted", "hs_sales_email_last_replied", "hs_analytics_first_timestamp"]

MODIFIED_DATE_ALIASES = ["hs_lastmodifieddate", "Last Modified Date", "modified_date", "hs_last_modified_date"]

# Attribution / lead-source signal, checked in this priority order.
SOURCE_ALIASES = ["hs_analytics_source", "hs_latest_source", "utm_source", "original_source", "Original Source"]

# Standard HubSpot lifecycle stage internal values. A populated stage
# outside this set is flagged as "non-standard" rather than "wrong" -
# custom lifecycle stages are legitimate HubSpot configuration, so this is
# a signal to review, not a hard error.
VALID_LIFECYCLE_STAGES = {
    "subscriber", "lead", "marketingqualifiedlead", "salesqualifiedlead",
    "opportunity", "customer", "evangelist", "other",
}


def status_for(score: float) -> str:
    if score >= HEALTHY_THRESHOLD:
        return "HEALTHY"
    if score >= WATCH_THRESHOLD:
        return "WATCH"
    return "SERIOUS"


@dataclass
class Dimension:
    name: str
    score: float | None  # None means insufficient data
    status: str  # HEALTHY | WATCH | SERIOUS | INSUFFICIENT_DATA
    evidence: list[str] = field(default_factory=list)


@dataclass
class Fix:
    severity: str  # CRITICAL | SERIOUS | WATCH
    dimension: str
    finding: str
    fix: str
    sequence: str  # week_1 | weeks_2_4 | quarter


@dataclass
class CortexResult:
    dimensions: dict[str, Dimension]
    composite_score: float
    composite_weights: dict[str, float]  # renormalized, available dimensions only
    excluded_dimensions: list[str]
    fixes: list[Fix]
    total_contacts: int
    total_deals: int
    sla_hours: float


@dataclass
class Signal:
    severity: str  # CRITICAL | SERIOUS | WATCH | INFO
    name: str
    count: int
    detail: str
    top_offenders: list[str] = field(default_factory=list)


@dataclass
class SignalsResult:
    signals: list[Signal]
    total_contacts: int
    total_deals: int
    sla_hours: float


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _key_ever_present(records: list[dict], aliases: list[str]) -> bool:
    """True if any alias key appears (as a dict key, whatever the value) on
    at least one record. Distinguishes "property not exported" from
    "property exported but blank" - only the former is insufficient data."""
    return any(alias in record for record in records for alias in aliases)


def _first_present_value(record: dict, aliases: list[str]):
    """(alias_used, value) for the first alias in priority order whose key
    is present on this record with a non-junk value; (None, None) if none."""
    for alias in aliases:
        if alias not in record:
            continue
        value = record[alias]
        if value is None:
            continue
        if isinstance(value, str) and audit_engine.is_junk(value):
            continue
        return alias, value
    return None, None


def _record_label(record: dict) -> str:
    """Best-effort human label for a raw contact record, for offender lists."""
    _, email = _first_present_value(record, EMAIL_ALIASES)
    return str(email) if email else "(no email)"


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile - deterministic, no numpy. pct in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[k]


def _first_touch_facts(contacts_raw: list[dict], now: datetime) -> list[dict]:
    """Per-contact first-touch facts, only for contacts whose createdate
    parses. Each entry: label, created, touched (datetime | None),
    touch_alias (str | None, only set when hours resolved), hours
    (float | None, create-to-touch), age_hours (float, create-to-now)."""
    facts = []
    for c in contacts_raw:
        _, created_raw = _first_present_value(c, CREATEDATE_ALIASES)
        created = funnel_engine.parse_date(created_raw) if created_raw else None
        if created is None:
            continue
        alias, touch_raw = _first_present_value(c, FIRST_TOUCH_ALIASES)
        touched = funnel_engine.parse_date(touch_raw) if touch_raw else None
        hours = None
        if touched is not None:
            delta = (touched - created).total_seconds() / 3600.0
            if delta >= 0:
                hours = delta
        facts.append(
            {
                "label": _record_label(c),
                "created": created,
                "touched": touched if hours is not None else None,
                "touch_alias": alias if hours is not None else None,
                "hours": hours,
                "age_hours": (now - created).total_seconds() / 3600.0,
            }
        )
    return facts


def _never_populated_properties(contacts_raw: list[dict]):
    """(universe of exported property keys, sorted keys that are present on
    at least one record but never hold a non-junk value on any record)."""
    universe = {k for c in contacts_raw for k in c.keys()}
    never_populated = sorted(
        key for key in universe if all(audit_engine.is_junk(str(c[key])) for c in contacts_raw if key in c)
    )
    return universe, never_populated


def _stale_modified_records(contacts_raw: list[dict], now: datetime):
    """(records with a parseable modified date, [(days_stale, label), ...]
    for those beyond STALE_MODIFIED_DAYS, most-stale first)."""
    checked = 0
    stale: list[tuple[int, str]] = []
    for c in contacts_raw:
        _, raw_val = _first_present_value(c, MODIFIED_DATE_ALIASES)
        if not raw_val:
            continue
        modified = funnel_engine.parse_date(raw_val)
        if modified is None:
            continue
        checked += 1
        days = (now - modified).days
        if days > STALE_MODIFIED_DAYS:
            stale.append((days, _record_label(c)))
    stale.sort(key=lambda s: -s[0])
    return checked, stale


def _finalize_dimension(name: str, components: list[tuple[float, float]], evidence: list[str]) -> Dimension:
    if not components:
        return Dimension(name=name, score=None, status="INSUFFICIENT_DATA", evidence=evidence)
    weight_total = sum(w for _, w in components)
    score = sum(s * w for s, w in components) / weight_total
    return Dimension(name=name, score=score, status=status_for(score), evidence=evidence)


# ---------------------------------------------------------------------------
# dimensions
# ---------------------------------------------------------------------------


def _dim_lifecycle(contacts_raw: list[dict], deals: list[dict], now: datetime) -> Dimension:
    total = len(contacts_raw)
    evidence: list[str] = []
    components: list[tuple[float, float]] = []

    if _key_ever_present(contacts_raw, LIFECYCLE_ALIASES):
        missing = sum(1 for c in contacts_raw if not _first_present_value(c, LIFECYCLE_ALIASES)[1])
        pct_missing = missing / total if total else 0.0
        components.append((100.0 * (1 - pct_missing), 0.6))
        evidence.append(f"{missing}/{total} contacts ({pct_missing:.0%}) are missing lifecyclestage")
    else:
        evidence.append("lifecyclestage not present anywhere in this export - stage-hygiene check skipped")

    if deals and _key_ever_present(deals, ["pipeline"]):
        by_pipeline: dict[str, set[str]] = {}
        for d in deals:
            pipeline, stage = d.get("pipeline"), d.get("dealstage")
            if not pipeline or not stage:
                continue
            by_pipeline.setdefault(str(pipeline), set()).add(str(stage))
        if len(by_pipeline) >= 2:
            stage_sets = list(by_pipeline.values())
            union = set.union(*stage_sets)
            intersection = set.intersection(*stage_sets)
            overlap = (len(intersection) / len(union)) if union else 1.0
            components.append((100.0 * overlap, 0.4))
            evidence.append(
                f"{len(by_pipeline)} pipelines observed; stage-name overlap "
                f"{len(intersection)}/{len(union)} ({overlap:.0%})"
            )
        else:
            evidence.append("only one pipeline observed in deals - stage-divergence check not applicable")
    else:
        evidence.append("deals not supplied, or deals carry no pipeline property - stage-divergence check skipped")

    return _finalize_dimension("lifecycle", components, evidence)


def _dim_routing(contacts_raw: list[dict], sla_hours: float, now: datetime) -> Dimension:
    total = len(contacts_raw)
    evidence: list[str] = []
    components: list[tuple[float, float]] = []

    has_createdate = _key_ever_present(contacts_raw, CREATEDATE_ALIASES)
    has_touch = _key_ever_present(contacts_raw, FIRST_TOUCH_ALIASES)
    if has_createdate and has_touch:
        resolved = [f for f in _first_touch_facts(contacts_raw, now) if f["hours"] is not None]
        if resolved:
            hours = [f["hours"] for f in resolved]
            p50, p95 = _percentile(hours, 50), _percentile(hours, 95)
            breach_pct = sum(1 for h in hours if h > sla_hours) / len(hours)
            components.append((max(0.0, 100.0 - breach_pct * 100.0), 0.65))
            counts: dict[str, int] = {}
            for f in resolved:
                counts[f["touch_alias"]] = counts.get(f["touch_alias"], 0) + 1
            top_alias = max(counts, key=counts.get)
            evidence.append(
                f"first-touch signal used: {top_alias} ({counts[top_alias]}/{len(resolved)} of resolvable "
                f"records); p50 {p50:.1f}h / p95 {p95:.1f}h to first touch vs {sla_hours:.0f}h SLA "
                f"({breach_pct:.0%} breach)"
            )
        else:
            evidence.append(
                "createdate and a first-touch property both exist but no record has a valid pair of them - "
                "SLA check skipped"
            )
    else:
        missing = []
        if not has_createdate:
            missing.append("createdate")
        if not has_touch:
            missing.append(
                "a first-touch signal (notes_last_contacted / hs_sales_email_last_replied / "
                "hs_analytics_first_timestamp)"
            )
        evidence.append(f"{' and '.join(missing)} not present anywhere in this export - SLA check skipped")

    if _key_ever_present(contacts_raw, OWNER_ALIASES):
        unowned = sum(1 for c in contacts_raw if not _first_present_value(c, OWNER_ALIASES)[1])
        pct_unowned = unowned / total if total else 0.0
        components.append((100.0 * (1 - pct_unowned), 0.35))
        evidence.append(f"{unowned}/{total} contacts ({pct_unowned:.0%}) have no owner - never routed")
    else:
        evidence.append("owner property not present anywhere in this export - routing-coverage check skipped")

    return _finalize_dimension("routing", components, evidence)


def _dim_automation(contacts_raw: list[dict], now: datetime) -> Dimension:
    evidence: list[str] = []
    components: list[tuple[float, float]] = []

    universe, never_populated = _never_populated_properties(contacts_raw)
    if universe:
        pct_never = len(never_populated) / len(universe)
        components.append((100.0 * (1 - pct_never), 0.5))
        sample = ", ".join(never_populated[:5]) or "none"
        evidence.append(
            f"{len(never_populated)}/{len(universe)} exported properties are present-but-never-populated "
            f"across the sample (e.g. {sample})"
        )
    else:
        evidence.append("no properties observed in this export - property-health check skipped")

    if _key_ever_present(contacts_raw, MODIFIED_DATE_ALIASES):
        checked, stale = _stale_modified_records(contacts_raw, now)
        if checked:
            pct_stale = len(stale) / checked
            components.append((100.0 * (1 - pct_stale), 0.5))
            evidence.append(f"{len(stale)}/{checked} records last modified over {STALE_MODIFIED_DAYS} days ago")
        else:
            evidence.append("hs_lastmodifieddate is present but not parseable on any record - staleness check skipped")
    else:
        evidence.append("hs_lastmodifieddate not present anywhere in this export - staleness check skipped")

    return _finalize_dimension("automation", components, evidence)


def _dim_reporting(contacts_raw: list[dict], now: datetime) -> Dimension:
    total = len(contacts_raw)
    evidence: list[str] = []
    components: list[tuple[float, float]] = []

    if _key_ever_present(contacts_raw, SOURCE_ALIASES):
        missing = sum(1 for c in contacts_raw if not _first_present_value(c, SOURCE_ALIASES)[1])
        pct_missing = missing / total if total else 0.0
        components.append((100.0 * (1 - pct_missing), 0.6))
        evidence.append(f"{missing}/{total} contacts ({pct_missing:.0%}) are missing an attribution source")

        by_domain: dict[str, list[str]] = {}
        for c in contacts_raw:
            _, email = _first_present_value(c, EMAIL_ALIASES)
            _, source = _first_present_value(c, SOURCE_ALIASES)
            if not email or not source or "@" not in str(email):
                continue
            domain = str(email).strip().lower().rsplit("@", 1)[-1]
            by_domain.setdefault(domain, []).append(str(source).strip().lower())

        eligible = {d: vals for d, vals in by_domain.items() if len(vals) >= 2}
        conflicting = {d: vals for d, vals in eligible.items() if len(set(vals)) > 1}
        if eligible:
            conflict_ratio = len(conflicting) / len(eligible)
            components.append((100.0 * (1 - conflict_ratio), 0.4))
            evidence.append(f"{len(conflicting)}/{len(eligible)} domains show conflicting source values across contacts")
        else:
            evidence.append("no domain has an attribution source recorded on 2+ contacts - conflict check not applicable")
    else:
        evidence.append("no analytics-source / utm_source-like property present anywhere in this export - attribution checks skipped")

    return _finalize_dimension("reporting", components, evidence)


def _composite(dimensions: dict[str, Dimension]):
    available = {name: dim for name, dim in dimensions.items() if dim.score is not None}
    excluded = [name for name in dimensions if name not in available]
    weight_total = sum(DIMENSION_WEIGHTS[name] for name in available)
    if weight_total == 0:
        return 0.0, {}, excluded
    used_weights = {name: DIMENSION_WEIGHTS[name] / weight_total for name in available}
    composite = sum(dim.score * used_weights[name] for name, dim in available.items())
    return composite, used_weights, excluded


# ---------------------------------------------------------------------------
# fixes
# ---------------------------------------------------------------------------

FIX_COPY = {
    "data_quality": "Run audit_crm, work the top-20 worst records and duplicate clusters first, then re-score to confirm it moved.",
    "lifecycle": "Enforce lifecyclestage on create (required field or workflow default) and reconcile stage names across pipelines so reporting rolls up cleanly.",
    "routing": "Add a routing workflow (owner assignment + first-touch task) that fires on contact create, and alert on breaches of the sla_hours threshold.",
    "automation": "Retire or start populating the never-used properties in a cleanup pass, and add a workflow that reassigns or flags records stale beyond a year.",
    "reporting": "Require an original-source value at capture (form hidden fields / UTM enforcement) and fix the domains with conflicting sources first - highest-leverage cleanup.",
}

# judgment call: a dimension with no usable signal at all still gets one
# WATCH-severity fix, recommending the instrumentation needed to score it
# next run, sequenced into "quarter" since turning on new capture is a
# longer-horizon change than fixing data that already exists.
INSUFFICIENT_FIX_COPY = {
    "lifecycle": "Start populating lifecyclestage (and export deal pipeline names) so this dimension can be scored next run.",
    "routing": "Start capturing createdate plus a first-touch signal (notes_last_contacted, hs_sales_email_last_replied, or hs_analytics_first_timestamp) so SLA breaches can be measured.",
    "automation": "Export hs_lastmodifieddate and the full property set, not just canonical fields, so property health can be measured.",
    "reporting": "Start capturing hs_analytics_source or a utm_source-equivalent at capture time so attribution can be measured.",
}

_SEVERITY_ORDER = {"CRITICAL": 0, "SERIOUS": 1, "WATCH": 2}


def _build_fixes(dimensions: dict[str, Dimension]) -> list[Fix]:
    fixes: list[Fix] = []
    for name, dim in dimensions.items():
        if dim.score is None:
            if name in INSUFFICIENT_FIX_COPY:
                fixes.append(
                    Fix(
                        severity="WATCH",
                        dimension=name,
                        finding=dim.evidence[-1] if dim.evidence else f"{name}: insufficient data",
                        fix=INSUFFICIENT_FIX_COPY[name],
                        sequence="quarter",
                    )
                )
            continue
        if dim.status == "HEALTHY":
            continue
        severity = "CRITICAL" if dim.score < 30.0 else ("SERIOUS" if dim.status == "SERIOUS" else "WATCH")
        sequence = {"CRITICAL": "week_1", "SERIOUS": "weeks_2_4", "WATCH": "quarter"}[severity]
        fixes.append(
            Fix(
                severity=severity,
                dimension=name,
                finding=dim.evidence[0] if dim.evidence else f"{name} scored {dim.score:.1f}",
                fix=FIX_COPY.get(name, "Investigate and remediate."),
                sequence=sequence,
            )
        )
    fixes.sort(key=lambda f: _SEVERITY_ORDER[f.severity])
    return fixes


# ---------------------------------------------------------------------------
# entry point: scorecard
# ---------------------------------------------------------------------------


def run_engine(
    contacts_raw: list[dict],
    deals: list[dict] | None = None,
    sla_hours: float = DEFAULT_SLA_HOURS,
    now: datetime | None = None,
) -> CortexResult:
    now = now or datetime.now(timezone.utc)
    deals = deals or []

    contacts_canonical = [audit_fetch.normalize_contact(c) for c in contacts_raw]
    audit_result = audit_engine.run_engine(contacts_canonical, now)
    dq_evidence = [
        f"portal integrity score {audit_result.portal_score:.1f} (delegated to gtmos.audit.engine)",
        f"{len(audit_result.dupe_clusters)} duplicate cluster(s)",
        f"{audit_result.dead_share:.0%} unreachable, {audit_result.at_risk_share:.0%} at risk (record score < 70)",
    ]
    if audit_result.dimension_averages:
        worst_dim = min(audit_result.dimension_averages, key=audit_result.dimension_averages.get)
        dq_evidence.append(f"weakest sub-dimension: {worst_dim} ({audit_result.dimension_averages[worst_dim]:.1f})")

    dimensions = {
        "data_quality": Dimension(
            name="data_quality",
            score=audit_result.portal_score,
            status=status_for(audit_result.portal_score),
            evidence=dq_evidence,
        ),
        "lifecycle": _dim_lifecycle(contacts_raw, deals, now),
        "routing": _dim_routing(contacts_raw, sla_hours, now),
        "automation": _dim_automation(contacts_raw, now),
        "reporting": _dim_reporting(contacts_raw, now),
    }

    composite, used_weights, excluded = _composite(dimensions)
    fixes = _build_fixes(dimensions)

    return CortexResult(
        dimensions=dimensions,
        composite_score=composite,
        composite_weights=used_weights,
        excluded_dimensions=excluded,
        fixes=fixes,
        total_contacts=len(contacts_raw),
        total_deals=len(deals),
        sla_hours=sla_hours,
    )


# ---------------------------------------------------------------------------
# entry point: signals
# ---------------------------------------------------------------------------


def _severity_for_rate(rate: float) -> str:
    if rate >= 0.40:
        return "CRITICAL"
    if rate >= 0.15:
        return "SERIOUS"
    if rate > 0:
        return "WATCH"
    return "INFO"


def _signal_routing_sla(contacts_raw: list[dict], sla_hours: float, now: datetime) -> Signal:
    if not _key_ever_present(contacts_raw, CREATEDATE_ALIASES):
        return Signal("INFO", "routing_sla_breach", 0, "createdate not present anywhere in this export - skipped")
    facts = _first_touch_facts(contacts_raw, now)
    if not facts:
        return Signal("INFO", "routing_sla_breach", 0, "createdate present but not parseable on any record - skipped")
    breaches: list[tuple[float, str]] = []
    for f in facts:
        if f["hours"] is not None and f["hours"] > sla_hours:
            breaches.append((f["hours"], f"{f['label']} touched after {f['hours']:.1f}h (SLA {sla_hours:.0f}h)"))
        elif f["hours"] is None and f["touched"] is None and f["age_hours"] > sla_hours:
            breaches.append((f["age_hours"], f"{f['label']} created {f['age_hours']:.1f}h ago, still untouched"))
    breaches.sort(key=lambda b: -b[0])
    rate = len(breaches) / len(facts)
    return Signal(
        _severity_for_rate(rate),
        "routing_sla_breach",
        len(breaches),
        f"{len(breaches)}/{len(facts)} contacts breached the {sla_hours:.0f}h SLA",
        [msg for _, msg in breaches[:5]],
    )


def _signal_property_drift_never_populated(contacts_raw: list[dict]) -> Signal:
    universe, never_populated = _never_populated_properties(contacts_raw)
    if not universe:
        return Signal("INFO", "property_drift_never_populated", 0, "no properties observed in this export - skipped")
    rate = len(never_populated) / len(universe)
    return Signal(
        _severity_for_rate(rate),
        "property_drift_never_populated",
        len(never_populated),
        f"{len(never_populated)}/{len(universe)} exported properties are present-but-never-populated",
        never_populated[:5],
    )


def _signal_property_drift_stale_modified(contacts_raw: list[dict], now: datetime) -> Signal:
    if not _key_ever_present(contacts_raw, MODIFIED_DATE_ALIASES):
        return Signal("INFO", "property_drift_stale_modified", 0, "hs_lastmodifieddate not present anywhere in this export - skipped")
    checked, stale = _stale_modified_records(contacts_raw, now)
    if checked == 0:
        return Signal("INFO", "property_drift_stale_modified", 0, "hs_lastmodifieddate present but not parseable on any record - skipped")
    rate = len(stale) / checked
    return Signal(
        _severity_for_rate(rate),
        "property_drift_stale_modified",
        len(stale),
        f"{len(stale)}/{checked} records last modified over {STALE_MODIFIED_DAYS}d ago",
        [f"{label} ({days}d)" for days, label in stale[:5]],
    )


def _signal_lifecycle_integrity(contacts_raw: list[dict]) -> Signal:
    if not _key_ever_present(contacts_raw, LIFECYCLE_ALIASES):
        return Signal("INFO", "lifecycle_integrity", 0, "lifecyclestage not present anywhere in this export - skipped")
    total = len(contacts_raw)
    missing: list[str] = []
    invalid: list[str] = []
    for c in contacts_raw:
        _, stage = _first_present_value(c, LIFECYCLE_ALIASES)
        if not stage:
            missing.append(_record_label(c))
        elif str(stage).strip().lower() not in VALID_LIFECYCLE_STAGES:
            invalid.append(f"{_record_label(c)}: '{stage}'")
    broken = missing + invalid
    rate = len(broken) / total if total else 0.0
    detail = f"{len(missing)}/{total} missing lifecyclestage, {len(invalid)}/{total} hold a non-standard stage value"
    return Signal(_severity_for_rate(rate), "lifecycle_integrity", len(broken), detail, broken[:5])


def _signal_sync_failure_malformed(contacts_raw: list[dict]) -> Signal:
    total = len(contacts_raw)
    bad: list[str] = []
    for c in contacts_raw:
        _, email = _first_present_value(c, EMAIL_ALIASES)
        if email and not audit_engine.EMAIL_RE.match(str(email).strip()):
            bad.append(f"{email} (malformed email)")
        for alias in CREATEDATE_ALIASES + MODIFIED_DATE_ALIASES:
            raw_val = c.get(alias)
            if raw_val and not (isinstance(raw_val, str) and audit_engine.is_junk(raw_val)) and funnel_engine.parse_date(raw_val) is None:
                bad.append(f"{_record_label(c)}: unparseable {alias}={raw_val!r}")
    rate = len(bad) / total if total else 0.0
    return Signal(
        _severity_for_rate(rate),
        "sync_failure_malformed",
        len(bad),
        f"{len(bad)} malformed email/date value(s) found across {total} contacts",
        bad[:5],
    )


def _signal_sync_failure_no_owner_recent(contacts_raw: list[dict], now: datetime) -> Signal:
    if not (_key_ever_present(contacts_raw, OWNER_ALIASES) and _key_ever_present(contacts_raw, MODIFIED_DATE_ALIASES)):
        return Signal(
            "INFO", "sync_failure_no_owner_recent", 0,
            "owner and/or hs_lastmodifieddate not present anywhere in this export - skipped",
        )
    total = len(contacts_raw)
    hits: list[tuple[int, str]] = []
    for c in contacts_raw:
        _, owner = _first_present_value(c, OWNER_ALIASES)
        if owner:
            continue
        _, raw_val = _first_present_value(c, MODIFIED_DATE_ALIASES)
        modified = funnel_engine.parse_date(raw_val) if raw_val else None
        if modified is None:
            continue
        days = (now - modified).days
        if days <= RECENT_SYNC_DAYS:
            hits.append((days, f"{_record_label(c)} modified {days}d ago, no owner"))
    hits.sort(key=lambda h: h[0])
    rate = len(hits) / total if total else 0.0
    return Signal(
        _severity_for_rate(rate),
        "sync_failure_no_owner_recent",
        len(hits),
        f"{len(hits)}/{total} contacts were modified in the last {RECENT_SYNC_DAYS}d but carry no owner",
        [msg for _, msg in hits[:5]],
    )


def compute_signals(
    contacts_raw: list[dict],
    deals: list[dict] | None = None,
    sla_hours: float = DEFAULT_SLA_HOURS,
    now: datetime | None = None,
) -> SignalsResult:
    now = now or datetime.now(timezone.utc)
    deals = deals or []
    signals = [
        _signal_routing_sla(contacts_raw, sla_hours, now),
        _signal_property_drift_never_populated(contacts_raw),
        _signal_property_drift_stale_modified(contacts_raw, now),
        _signal_lifecycle_integrity(contacts_raw),
        _signal_sync_failure_malformed(contacts_raw),
        _signal_sync_failure_no_owner_recent(contacts_raw, now),
    ]
    return SignalsResult(signals=signals, total_contacts=len(contacts_raw), total_deals=len(deals), sla_hours=sla_hours)
