"""Offline unit tests for gtmos.cortex.engine - pure-Python GTM ops scoring,
zero network. Small synthetic fixtures per dimension, including the
insufficient-data degradation path.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gtmos.cortex import engine

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _contact(**kwargs) -> dict:
    base = {
        "email": "person@acme.com",
        "firstname": "A",
        "lastname": "B",
        "company": "Acme",
        "jobtitle": "VP",
        "phone": "+1 415 555 0100",
        "lifecyclestage": "opportunity",
        "owner": "rep@acme.com",
        "createdate": "2026-08-01T00:00:00Z",
        "notes_last_contacted": "2026-08-01T12:00:00Z",
        "hs_lastmodifieddate": "2026-08-15T00:00:00Z",
        "hs_analytics_source": "ORGANIC_SEARCH",
    }
    base.update(kwargs)
    return base


RICH_CONTACTS = [
    _contact(email="a@acme.com", owner="rep@acme.com"),
    _contact(email="b@beta.com", company="Beta", owner="rep@beta.com", hs_analytics_source="PAID_SEARCH"),
    _contact(
        email="c@beta.com", company="Beta", owner=None, notes_last_contacted=None,
        hs_sales_email_last_replied="2026-08-05T00:00:00Z", hs_analytics_source="PAID_SEARCH",
    ),
    _contact(
        email="d@gamma.com", company="Gamma", lifecyclestage=None, owner=None,
        notes_last_contacted=None, hs_analytics_source=None,
    ),
]

RICH_DEALS = [
    {"id": "1", "dealstage": "closedwon", "pipeline": "default"},
    {"id": "2", "dealstage": "closedlost", "pipeline": "default"},
    {"id": "3", "dealstage": "won", "pipeline": "renewals"},
]

EMPTY_CONTACTS = [{}, {}, {}]


# ---------------------------------------------------------------------------
# dimensions - happy paths
# ---------------------------------------------------------------------------


def test_data_quality_delegates_to_audit_engine():
    result = engine.run_engine(RICH_CONTACTS, [], now=NOW)
    dim = result.dimensions["data_quality"]
    assert dim.score is not None
    assert 0.0 <= dim.score <= 100.0


def test_lifecycle_flags_missing_stage_and_pipeline_divergence():
    result = engine.run_engine(RICH_CONTACTS, RICH_DEALS, now=NOW)
    dim = result.dimensions["lifecycle"]
    assert dim.score is not None
    assert dim.status in ("HEALTHY", "WATCH", "SERIOUS")
    assert any("lifecyclestage" in line for line in dim.evidence)
    assert any("pipeline" in line for line in dim.evidence)


def test_routing_computes_sla_breach_and_unowned():
    result = engine.run_engine(RICH_CONTACTS, [], sla_hours=6, now=NOW)
    dim = result.dimensions["routing"]
    assert dim.score is not None
    assert any("SLA" in line for line in dim.evidence)
    assert any("owner" in line for line in dim.evidence)


def test_automation_flags_never_populated_and_stale():
    contacts = [
        _contact(email="a@acme.com", hs_lastmodifieddate="2024-01-01T00:00:00Z", never_used_field=""),
        _contact(email="b@acme.com", hs_lastmodifieddate="2026-08-01T00:00:00Z", never_used_field=""),
    ]
    result = engine.run_engine(contacts, [], now=NOW)
    dim = result.dimensions["automation"]
    assert dim.score is not None
    assert any("never_used_field" in line for line in dim.evidence)
    assert any("stale" in line or "modified" in line for line in dim.evidence)


def test_reporting_flags_missing_source_and_conflicts():
    contacts = [
        _contact(email="a@acme.com", hs_analytics_source="ORGANIC_SEARCH"),
        _contact(email="b@acme.com", hs_analytics_source="PAID_SEARCH"),
        _contact(email="c@acme.com", hs_analytics_source=None),
    ]
    result = engine.run_engine(contacts, [], now=NOW)
    dim = result.dimensions["reporting"]
    assert dim.score is not None
    assert any("missing an attribution source" in line for line in dim.evidence)
    assert any("conflicting" in line for line in dim.evidence)


# ---------------------------------------------------------------------------
# insufficient-data degradation path
# ---------------------------------------------------------------------------


def test_insufficient_data_degrades_gracefully_and_is_excluded_from_composite():
    result = engine.run_engine(EMPTY_CONTACTS, [], now=NOW)

    for name in ("lifecycle", "routing", "automation", "reporting"):
        dim = result.dimensions[name]
        assert dim.score is None, f"{name} should be insufficient, not fabricated"
        assert dim.status == "INSUFFICIENT_DATA"
        assert name in result.excluded_dimensions
        assert dim.evidence  # always explains why, never silent

    # data_quality is always computable, even from empty records - a low
    # score is a real finding, not a fabricated one, so it stays scored.
    dq = result.dimensions["data_quality"]
    assert dq.score is not None
    assert result.composite_score == dq.score
    assert set(result.composite_weights) == {"data_quality"}
    assert abs(sum(result.composite_weights.values()) - 1.0) < 1e-9


def test_composite_weights_renormalize_over_available_dimensions():
    result = engine.run_engine(RICH_CONTACTS, RICH_DEALS, sla_hours=6, now=NOW)
    scored_names = {name for name, dim in result.dimensions.items() if dim.score is not None}
    assert set(result.composite_weights) == scored_names
    assert abs(sum(result.composite_weights.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# fixes
# ---------------------------------------------------------------------------


def test_fixes_are_severity_ranked_and_sequenced():
    result = engine.run_engine(EMPTY_CONTACTS, [], now=NOW)
    assert result.fixes
    severities = [f.severity for f in result.fixes]
    order = {"CRITICAL": 0, "SERIOUS": 1, "WATCH": 2}
    assert severities == sorted(severities, key=lambda s: order[s])
    for fix in result.fixes:
        assert fix.sequence in ("week_1", "weeks_2_4", "quarter")
        assert fix.finding
        assert fix.fix


def test_healthy_dimension_produces_no_fix():
    result = engine.run_engine(RICH_CONTACTS, RICH_DEALS, now=NOW)
    healthy_names = {name for name, dim in result.dimensions.items() if dim.status == "HEALTHY"}
    fix_names = {f.dimension for f in result.fixes}
    assert not (healthy_names & fix_names)


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------


def test_compute_signals_never_crashes_on_empty_and_rich_data():
    empty_signals = engine.compute_signals(EMPTY_CONTACTS, [], now=NOW)
    assert empty_signals.total_contacts == 3
    assert all(s.severity in ("CRITICAL", "SERIOUS", "WATCH", "INFO") for s in empty_signals.signals)
    assert all(s.severity == "INFO" for s in empty_signals.signals)  # nothing to compute at all

    rich_signals = engine.compute_signals(RICH_CONTACTS, RICH_DEALS, sla_hours=6, now=NOW)
    names = {s.name for s in rich_signals.signals}
    assert "routing_sla_breach" in names
    assert "lifecycle_integrity" in names
    assert rich_signals.total_contacts == 4
    assert rich_signals.total_deals == 3


def test_routing_sla_breach_signal_flags_untouched_old_contact():
    rich_signals = engine.compute_signals(RICH_CONTACTS, [], sla_hours=6, now=NOW)
    sla_signal = next(s for s in rich_signals.signals if s.name == "routing_sla_breach")
    assert sla_signal.count > 0
    assert sla_signal.top_offenders
