"""Offline tests for gtmos.cortex.govern - deterministic agents behind a
policy gate. No network, and no write path: the module proposes, a human
applies.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest

from gtmos.cortex import govern

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _contact(**kwargs) -> dict:
    base = {
        "email": "a@acme.com",
        "firstname": "A",
        "lastname": "B",
        "company": "Acme",
        "jobtitle": "VP",
        "phone": "+1 415 555 0100",
        "owner": "rep1",
        "lifecyclestage": "lead",
        "createdate": "2026-08-01T00:00:00Z",
        "notes_last_contacted": "2026-08-01T06:00:00Z",
        "hs_analytics_source": "ORGANIC_SEARCH",
    }
    base.update(kwargs)
    return base


OPEN_DEAL = {"id": "d1", "dealname": "Acme expansion", "amount": "5000", "dealstage": "qualifiedtobuy",
             "company_domain": "acme.com", "hubspot_owner_id": "rep1",
             "createdate": "2026-08-01T00:00:00Z", "hs_lastmodifieddate": "2026-08-18T00:00:00Z"}
WON_DEAL = {"id": "d2", "dealname": "Beta renewal", "amount": "9000", "dealstage": "closedwon",
            "company_domain": "beta.io", "hubspot_owner_id": "rep2"}


def _by_action(result: govern.GovernResult, action: str) -> list[govern.Proposal]:
    return [p for p in result.proposals if p.action == action]


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


def test_router_assigns_dominant_owner_with_evidence():
    contacts = [_contact(email="a@acme.com"), _contact(email="b@acme.com", owner=None)]
    result = govern.run_engine(contacts, [OPEN_DEAL], now=NOW)
    (p,) = _by_action(result, "assign_owner")
    assert p.status == "PROPOSED"
    assert p.targets == ["b@acme.com"]
    assert p.after == "owner = rep1" and p.confidence == 1.0
    assert p.severity == "CRITICAL"  # open pipeline sitting unrouted
    assert any("open deal" in line for line in p.evidence)


def test_router_without_owner_precedent_is_blocked_by_confidence_floor():
    contacts = [_contact(email="x@solo.com", company="Solo", owner=None)]
    result = govern.run_engine(contacts, [], now=NOW)
    (p,) = _by_action(result, "assign_owner")
    assert p.status == "BLOCKED"
    assert "human pick" in p.after
    assert "floor" in p.blocked_reason


def test_deduper_merges_into_most_complete_record():
    contacts = [
        _contact(email="dupe@acme.com", jobtitle=None, phone=None, lastname=None),  # sparse -> loses
        _contact(email="dupe@acme.com"),  # complete -> survivor
    ]
    result = govern.run_engine(contacts, [], now=NOW)
    (p,) = _by_action(result, "merge_duplicates")
    assert p.status == "PROPOSED" and p.severity == "SERIOUS" and p.confidence == 0.9
    assert p.blast_radius == 2
    assert "merge into #1 dupe@acme.com" in p.after


def test_lifecycle_steward_backs_stage_with_deal_evidence_and_blocks_without_it():
    contacts = [
        _contact(email="a@acme.com", lifecyclestage=None),  # open deal -> opportunity
        _contact(email="b@beta.io", company="Beta", owner="rep2", lifecyclestage=None),  # won deal -> customer
        _contact(email="c@gamma.io", company="Gamma", owner="rep3", lifecyclestage=None, notes_last_contacted=None),  # nothing
    ]
    result = govern.run_engine(contacts, [OPEN_DEAL, WON_DEAL], now=NOW)
    proposals = {p.targets[0]: p for p in _by_action(result, "set_lifecycle_stage")}
    assert proposals["a@acme.com"].after == "lifecyclestage = opportunity"
    assert proposals["a@acme.com"].status == "PROPOSED"
    assert proposals["b@beta.io"].after == "lifecyclestage = customer"
    assert proposals["c@gamma.io"].status == "BLOCKED"
    assert "human pick" in proposals["c@gamma.io"].after


def test_lifecycle_steward_skips_when_property_absent_from_export():
    contacts = [{"email": "a@acme.com", "owner": "rep1"}]
    result = govern.run_engine(contacts, [], now=NOW)
    assert _by_action(result, "set_lifecycle_stage") == []


def test_attribution_steward_harmonizes_to_majority_and_blocks_ties():
    contacts = [
        _contact(email="a@acme.com", hs_analytics_source="ORGANIC_SEARCH"),
        _contact(email="b@acme.com", hs_analytics_source="ORGANIC_SEARCH"),
        _contact(email="c@acme.com", hs_analytics_source="PAID_SEARCH"),
        _contact(email="d@tie.io", company="Tie", hs_analytics_source="ORGANIC_SEARCH"),
        _contact(email="e@tie.io", company="Tie", hs_analytics_source="PAID_SEARCH"),
        _contact(email="f@clean.io", company="Clean", hs_analytics_source="REFERRALS"),
        _contact(email="g@clean.io", company="Clean", hs_analytics_source="REFERRALS"),
        _contact(email="h@clean.io", company="Clean", hs_analytics_source=None),  # backfill candidate
    ]
    result = govern.run_engine(contacts, [], now=NOW)
    by_target = {p.targets[0]: p for p in _by_action(result, "set_attribution_source")}
    assert by_target["c@acme.com"].after == "source = organic_search"
    assert by_target["c@acme.com"].status == "PROPOSED"
    tie = by_target["d@tie.io"] if "d@tie.io" in by_target else by_target["e@tie.io"]
    assert tie.status == "BLOCKED" and "tie" in tie.after
    assert by_target["h@clean.io"].after == "source = referrals" and by_target["h@clean.io"].confidence == 0.7


def test_pipeline_steward_flags_stalled_open_deals():
    stalled = {"id": "s1", "dealname": "Old one", "amount": "7000", "dealstage": "qualifiedtobuy",
               "hubspot_owner_id": "rep9", "createdate": "2026-01-01T00:00:00Z",
               "hs_lastmodifieddate": "2026-01-05T00:00:00Z"}
    result = govern.run_engine([_contact()], [stalled, WON_DEAL], stall_days=21, now=NOW)
    (p,) = _by_action(result, "review_stalled_deal")
    assert p.targets == ["Old one"] and p.universe == "deals"
    assert p.severity == "CRITICAL"  # idle far beyond 2x the threshold
    assert "rep9" in p.after


def test_schema_steward_retires_never_populated_properties():
    contacts = [_contact(email="a@acme.com", dead_field=""), _contact(email="b@acme.com", dead_field="n/a")]
    result = govern.run_engine(contacts, [], now=NOW)
    (p,) = _by_action(result, "retire_property")
    assert p.targets == ["dead_field"] and p.universe == "properties"
    assert p.status == "PROPOSED"


# ---------------------------------------------------------------------------
# policy gate
# ---------------------------------------------------------------------------


def test_policy_allow_list_blocks_disallowed_actions():
    contacts = [_contact(email="a@acme.com"), _contact(email="b@acme.com", owner=None, dead_field="")]
    policy = govern.Policy(name="routing-only", allowed_actions=["assign_owner"])
    result = govern.run_engine(contacts, [], policy=policy, now=NOW)
    assert all(p.status == "PROPOSED" for p in _by_action(result, "assign_owner"))
    (retire,) = _by_action(result, "retire_property")
    assert retire.status == "BLOCKED" and "allow-list" in retire.blocked_reason
    assert retire.policy == "routing-only"


def test_policy_blast_record_cap_blocks_large_proposals():
    contacts = [_contact(email="owner@big.com", company="Big")] + [
        _contact(email=f"u{i}@big.com", company="Big", owner=None) for i in range(26)
    ]
    result = govern.run_engine(contacts, [], now=NOW)
    (p,) = _by_action(result, "assign_owner")
    assert p.blast_radius == 26
    assert p.status == "BLOCKED" and "25-record cap" in p.blocked_reason


def test_policy_blast_share_cap_respects_small_sample_floor():
    small = [_contact(email="o@s.com", company="S")] + [_contact(email=f"u{i}@s.com", company="S", owner=None) for i in range(3)]
    (p_small,) = _by_action(govern.run_engine(small, [], now=NOW), "assign_owner")
    assert p_small.blast_share == 0.75 and p_small.status == "PROPOSED"  # 3 records: share cap does not apply

    bigger = [_contact(email="o@s.com", company="S")] + [_contact(email=f"u{i}@s.com", company="S", owner=None) for i in range(5)]
    (p_big,) = _by_action(govern.run_engine(bigger, [], now=NOW), "assign_owner")
    assert p_big.status == "BLOCKED" and "of contacts" in p_big.blocked_reason


def test_load_policy_validates_keys_actions_and_human_override(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"name": "strict", "max_blast_records": 5, "min_confidence": 0.9,
                                "allowed_actions": ["assign_owner", "merge_duplicates"]}), encoding="utf-8")
    policy = govern.load_policy(str(good))
    assert policy.name == "strict" and policy.max_blast_records == 5 and policy.min_confidence == 0.9
    assert policy.requires_human is True

    bad_key = tmp_path / "bad_key.json"
    bad_key.write_text(json.dumps({"auto_apply": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown policy key"):
        govern.load_policy(str(bad_key))

    no_human = tmp_path / "no_human.json"
    no_human.write_text(json.dumps({"requires_human": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be disabled"):
        govern.load_policy(str(no_human))

    bad_action = tmp_path / "bad_action.json"
    bad_action.write_text(json.dumps({"allowed_actions": ["delete_everything"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown action"):
        govern.load_policy(str(bad_action))


# ---------------------------------------------------------------------------
# determinism, ordering, read-only by construction
# ---------------------------------------------------------------------------


def test_ids_are_severity_ordered_and_runs_are_deterministic():
    contacts = [
        _contact(email="a@acme.com", dead_field=""),
        _contact(email="b@acme.com", owner=None, dead_field=""),
        _contact(email="b@acme.com", owner=None, dead_field=""),
    ]
    first = govern.run_engine(contacts, [OPEN_DEAL], now=NOW)
    second = govern.run_engine(contacts, [OPEN_DEAL], now=NOW)
    assert [p.id for p in first.proposals] == [f"P-{n:03d}" for n in range(1, len(first.proposals) + 1)]
    ranks = [govern._SEVERITY_ORDER[p.severity] for p in first.proposals]
    assert ranks == sorted(ranks)
    assert [(p.id, p.status, p.action, p.targets) for p in first.proposals] == [
        (p.id, p.status, p.action, p.targets) for p in second.proposals
    ]


def test_no_network_or_write_path_in_module_source():
    src = inspect.getsource(govern)
    for banned in ("urllib", "http.client", "socket", "requests", "subprocess"):
        assert banned not in src, f"govern must not reach for {banned}"


def test_render_writes_only_the_two_artifacts_and_summarize_states_nothing_is_applied(tmp_path):
    contacts = [_contact(email="a@acme.com"), _contact(email="b@acme.com", owner=None)]
    result = govern.run_engine(contacts, [OPEN_DEAL], now=NOW)
    out = govern.render(result, str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["proposals.json", "proposals.md"]
    payload = json.loads((tmp_path / "proposals.json").read_text(encoding="utf-8"))
    assert payload["proposed"] == out["proposed"] and payload["blocked"] == out["blocked"]
    assert payload["policy"]["requires_human"] is True
    md = (tmp_path / "proposals.md").read_text(encoding="utf-8")
    assert "## Blocked by policy" in md and "## Evidence" in md
    assert "Nothing is applied by this tool." in govern.summarize(result)
