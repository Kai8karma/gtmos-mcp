"""Offline tests for gtmos.cortex.graph - deterministic context graph, no
network. Every edge must trace to an explicit property; nothing is guessed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from gtmos.cortex import graph as g

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _contact(**kwargs) -> dict:
    base = {
        "email": "a@acme.com",
        "firstname": "A",
        "lastname": "B",
        "company": "Acme",
        "owner": "rep1",
        "lifecyclestage": "lead",
        "createdate": "2026-08-01T00:00:00Z",
        "notes_last_contacted": "2026-08-01T06:00:00Z",
        "hs_analytics_source": "ORGANIC_SEARCH",
    }
    base.update(kwargs)
    return base


CONTACTS = [
    _contact(email="a@acme.com"),
    _contact(email="b@acme.com", owner=None, hs_analytics_source="PAID_SEARCH"),
    _contact(email="c@beta.io", company="Beta", owner="rep2", notes_last_contacted=None),  # untouched -> breach
    _contact(email="gmailuser@gmail.com", company="Gamma Inc", owner="rep3"),
    _contact(email=None, company=None, owner=None),  # no account possible
]

DEALS = [
    {"id": "d1", "dealname": "Acme expansion", "amount": "5000", "dealstage": "qualifiedtobuy",
     "company_domain": "acme.com", "hubspot_owner_id": "rep1"},
    {"id": "d2", "dealname": "Beta renewal", "amount": "9000", "dealstage": "closedwon",
     "company": "Beta Inc", "hubspot_owner_id": "rep2"},
    {"id": "d3", "dealname": "Acme mystery", "amount": "100", "dealstage": "appointmentscheduled"},  # no association
]


def test_nodes_and_edges_are_explicit_only():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    assert {n.kind for n in graph.nodes} == {"contact", "company", "owner", "source", "deal"}
    assert {e.rel for e in graph.edges} == {"belongs_to", "owns", "sourced_by", "for_account"}
    # d3 says "Acme" in its name and nothing else - the name is never consulted
    assert graph.unlinked_deals == 1
    assert graph.contacts_without_account == 1


def test_accounts_group_by_domain_and_freemail_falls_back_to_company_name():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    keys = {a.key for a in graph.accounts}
    assert "acme.com" in keys and "beta.io" in keys
    assert "gmail.com" not in keys
    assert "name:gamma" in keys


def test_deal_links_by_domain_and_by_normalized_name():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    acme = graph.account("acme.com")
    beta = graph.account("beta.io")
    assert acme.deal_ids == ["deal:d1"] and acme.open_deals == 1 and acme.open_amount == 5000.0
    assert beta.deal_ids == ["deal:d2"] and beta.won_deals == 1 and beta.open_deals == 0


def test_account_flags_and_rollup():
    graph = g.build_graph(CONTACTS, DEALS, sla_hours=24, now=NOW)
    acme = graph.account("acme.com")
    assert acme.contacts == 2 and acme.unowned_contacts == 1
    assert acme.owners == {"rep1": 1}
    assert acme.source_conflict is True
    assert acme.sla_breaches == 0  # both touched within 6h
    for flag in ("unowned:1", "source-conflict", "open-pipeline-unrouted"):
        assert flag in acme.flags
    beta = graph.account("beta.io")
    assert beta.sla_breaches == 1 and "sla-breach:1" in beta.flags
    assert beta.last_touch is None


def test_accounts_sorted_open_pipeline_first():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    assert graph.accounts[0].key == "acme.com"


def test_account_lookup_by_name_is_case_insensitive():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    assert graph.account("BETA").key == "beta.io"
    assert graph.account("nope.com") is None


def test_summarize_overview_and_drilldown():
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    text = g.summarize(graph, top=2)
    assert "Context graph:" in text and "Top 2 accounts" in text
    assert "left unlinked, not guessed" in text
    drill = g.summarize(graph, account="acme.com")
    assert "Account Acme [acme.com]" in drill and "CONFLICT" in drill
    assert "not found" in g.summarize(graph, account="nope.com")


def test_render_writes_json_and_md_and_is_deterministic(tmp_path):
    graph = g.build_graph(CONTACTS, DEALS, now=NOW)
    out = g.render(graph, str(tmp_path / "a"))
    payload = json.loads((tmp_path / "a" / "context-graph.json").read_text(encoding="utf-8"))
    assert payload["accounts"][0]["key"] == "acme.com"
    assert len(payload["nodes"]) == out["nodes"] and len(payload["edges"]) == out["edges"]
    assert "| Account |" in (tmp_path / "a" / "context-graph.md").read_text(encoding="utf-8")

    g.render(g.build_graph(CONTACTS, DEALS, now=NOW), str(tmp_path / "b"))
    second = json.loads((tmp_path / "b" / "context-graph.json").read_text(encoding="utf-8"))
    strip = lambda p: {k: v for k, v in p.items() if k != "generated"}  # noqa: E731
    assert strip(payload) == strip(second)


def test_empty_records_build_an_empty_graph():
    graph = g.build_graph([{}, {}], [], now=NOW)
    assert graph.accounts == [] and graph.contacts_without_account == 2
    assert "0 accounts" in g.summarize(graph)
