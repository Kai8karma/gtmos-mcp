"""Regression tests for three fixes: the duplicate rule, pinned scoring
dates (as_of), and optional path confinement (GTMOS_ALLOWED_DIR)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gtmos.audit.engine import _normalize_company, find_dupes
from gtmos.cortex import govern
from gtmos.mcp import ALLOWED_DIR_ENV, call_tool

FIXTURES = Path(__file__).parent / "fixtures"
CONTACTS = FIXTURES / "contacts_sample.json"
DEALS = FIXTURES / "deals_sample.json"


def _person(first, last="Patel", company="Acme Inc", email=None):
    email = email or f"{(first or 'x').lower()}.{last.lower()}@acme.com"
    return {"email": email, "firstname": first, "lastname": last, "company": company}


def _fuzzy(contacts):
    return [c for c in find_dupes(contacts) if c.kind == "fuzzy_name_company"]


# ---------------------------------------------------------------------------
# duplicate rule
# ---------------------------------------------------------------------------


def test_two_people_sharing_a_surname_and_employer_are_not_duplicates():
    assert _fuzzy([_person("Asha"), _person("Ravi")]) == []


def test_same_first_name_surname_and_employer_is_a_duplicate():
    (cluster,) = _fuzzy([_person("Asha", email="asha@acme.com"), _person("asha ", email="a.patel@acme.com")])
    assert cluster.indices == [0, 1]
    assert cluster.key == "asha patel / acme"


def test_an_initial_joins_the_one_full_name_it_matches():
    (cluster,) = _fuzzy([_person("Priya", "Kapoor"), _person("P", "Kapoor"), _person("Rohit", "Kapoor")])
    assert cluster.indices == [0, 1]


def test_an_initial_matching_two_people_is_left_alone():
    assert _fuzzy([_person("Priya"), _person("Pooja"), _person("P")]) == []


def test_two_bare_initials_cluster_together():
    (cluster,) = _fuzzy([_person("P", email="p1@acme.com"), _person("p.", email="p2@acme.com")])
    assert cluster.indices == [0, 1]


def test_a_record_with_no_first_name_is_never_fuzzy_matched():
    assert _fuzzy([_person("Asha"), _person(None, email="patel@acme.com")]) == []


def test_company_punctuation_and_suffixes_do_not_hide_a_duplicate():
    (cluster,) = _fuzzy([_person("Asha", company="Acme, Inc."), _person("Asha", company="ACME Inc", email="ap@acme.com")])
    assert cluster.indices == [0, 1]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Inc", "acme"),
        ("Acme, Inc.", "acme"),
        ("Acme Co., Ltd.", "acme"),
        ("Meridian Steelworks Pvt Ltd", "meridian steelworks"),
        ("Co", "co"),  # a one-word name is never stripped to nothing
        ("  Brightpath   Logistics ", "brightpath logistics"),
    ],
)
def test_normalize_company(raw, expected):
    assert _normalize_company(raw) == expected


def test_deduper_no_longer_proposes_merging_two_different_people():
    contacts = [
        {"id": "1", "properties": {"email": "asha.patel@acme.com", "firstname": "Asha", "lastname": "Patel", "company": "Acme Inc"}},
        {"id": "2", "properties": {"email": "ravi.patel@acme.com", "firstname": "Ravi", "lastname": "Patel", "company": "Acme Inc"}},
    ]
    result = govern.run_engine(contacts, [])
    assert [p for p in result.proposals if p.action == "merge_duplicates"] == []


def test_sample_fixture_still_yields_its_two_clusters():
    from gtmos.audit import fetch

    kinds = sorted(c.kind for c in find_dupes(fetch.load_json(str(CONTACTS))))
    assert kinds == ["exact_email", "fuzzy_name_company"]


# ---------------------------------------------------------------------------
# as_of
# ---------------------------------------------------------------------------


def _audit(tmp_path, **extra):
    out = call_tool("audit_crm", {"contacts_file": str(CONTACTS), "out_dir": str(tmp_path), **extra})
    assert not out.get("isError"), out["content"][0]["text"]
    return out


def test_as_of_pins_the_score(tmp_path):
    first = _audit(tmp_path, as_of="2026-08-19")["structuredContent"]
    second = _audit(tmp_path, as_of="2026-08-19T00:00:00Z")["structuredContent"]
    assert first["portal_score"] == second["portal_score"]
    assert first["as_of"] == "2026-08-19T00:00:00+00:00"


def test_as_of_changes_what_counts_as_fresh(tmp_path):
    early = _audit(tmp_path, as_of="2026-08-19")["structuredContent"]["dimension_averages"]["freshness"]
    late = _audit(tmp_path, as_of="2028-08-19")["structuredContent"]["dimension_averages"]["freshness"]
    assert late < early


def test_as_of_is_echoed_in_the_text_only_when_given(tmp_path):
    assert "Scored as of: 2026-08-19" in _audit(tmp_path, as_of="2026-08-19")["content"][0]["text"]
    assert "Scored as of" not in _audit(tmp_path)["content"][0]["text"]


def test_bad_as_of_is_reported_as_content_not_crash(tmp_path):
    out = call_tool("audit_crm", {"contacts_file": str(CONTACTS), "out_dir": str(tmp_path), "as_of": "last tuesday"})
    assert out["isError"] is True
    assert "as_of" in out["content"][0]["text"]


@pytest.mark.parametrize("tool", ["funnel_leak", "cortex_scorecard", "ops_signals", "cortex_graph", "cortex_proposals"])
def test_every_other_tool_accepts_as_of(tool, tmp_path):
    args = {"contacts_file": str(CONTACTS), "deals_file": str(DEALS), "out_dir": str(tmp_path), "as_of": "2026-08-19"}
    out = call_tool(tool, args)
    assert not out.get("isError"), out["content"][0]["text"]
    assert out["structuredContent"]["as_of"] == "2026-08-19T00:00:00+00:00"


# ---------------------------------------------------------------------------
# GTMOS_ALLOWED_DIR
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    shutil.copy(CONTACTS, root / "contacts.json")
    monkeypatch.setenv(ALLOWED_DIR_ENV, str(root))
    return root


def test_confined_relative_paths_resolve_inside_the_allowed_dir(sandbox):
    out = call_tool("audit_crm", {"contacts_file": "contacts.json", "out_dir": "reports"})
    assert not out.get("isError"), out["content"][0]["text"]
    assert (sandbox / "reports" / "integrity-report.md").exists()


def test_confined_read_outside_the_allowed_dir_is_refused(sandbox):
    out = call_tool("audit_crm", {"contacts_file": str(CONTACTS), "out_dir": "reports"})
    assert out["isError"] is True
    assert ALLOWED_DIR_ENV in out["content"][0]["text"]


def test_confined_write_outside_the_allowed_dir_is_refused(sandbox, tmp_path):
    escape = tmp_path / "elsewhere"
    out = call_tool("audit_crm", {"contacts_file": "contacts.json", "out_dir": str(escape)})
    assert out["isError"] is True
    assert not escape.exists()


def test_confined_dotdot_traversal_is_refused(sandbox):
    out = call_tool("audit_crm", {"contacts_file": "contacts.json", "out_dir": "../elsewhere"})
    assert out["isError"] is True
    assert not (sandbox.parent / "elsewhere").exists()


def test_confined_policy_file_outside_the_allowed_dir_is_refused(sandbox, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    out = call_tool("cortex_proposals", {"contacts_file": "contacts.json", "policy_file": str(policy), "out_dir": "q"})
    assert out["isError"] is True


def test_unset_allowed_dir_keeps_today_s_behaviour(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOWED_DIR_ENV, raising=False)
    out = call_tool("audit_crm", {"contacts_file": str(CONTACTS), "out_dir": str(tmp_path / "anywhere")})
    assert not out.get("isError"), out["content"][0]["text"]


# ---------------------------------------------------------------------------
# possible duplicates: surfaced for review, never scored or merged
# ---------------------------------------------------------------------------


def test_surname_only_matches_surface_as_possible_duplicates():
    from gtmos.audit.engine import find_possible_dupes

    contacts = [_person("Asha"), _person("Ravi"), _person("Jane", last="Smith", company="Globex", email="j@globex.com")]
    (cluster,) = find_possible_dupes(contacts)
    assert cluster.kind == "possible_name_company"
    assert cluster.key == "patel / acme"
    assert cluster.indices == [0, 1]


def test_a_confirmed_cluster_is_not_also_listed_as_possible():
    from gtmos.audit.engine import find_possible_dupes

    assert find_possible_dupes([_person("Priya", "Kapoor"), _person("P", "Kapoor")]) == []


def test_a_bucket_with_a_confirmed_pair_and_a_stranger_is_still_listed_for_review():
    from gtmos.audit.engine import find_possible_dupes

    contacts = [_person("Priya", "Kapoor"), _person("P", "Kapoor"), _person("Rohit", "Kapoor")]
    (cluster,) = find_possible_dupes(contacts)
    assert cluster.indices == [0, 1, 2]


def test_possible_duplicates_do_not_move_the_score(tmp_path):
    from gtmos.audit.engine import run_engine

    result = run_engine([_person("Asha"), _person("Ravi")])
    assert result.dupe_clusters == []
    assert result.dupe_penalty == 0.0
    assert len(result.possible_dupe_clusters) == 1


def test_possible_duplicates_are_reported_but_labelled(tmp_path):
    contacts = tmp_path / "c.json"
    contacts.write_text(json.dumps([_person("Asha"), _person("Ravi")]), encoding="utf-8")
    out = call_tool("audit_crm", {"contacts_file": str(contacts), "out_dir": str(tmp_path / "o")})
    assert not out.get("isError"), out["content"][0]["text"]
    assert out["structuredContent"]["duplicate_clusters"] == 0
    assert out["structuredContent"]["possible_duplicate_clusters"] == 1
    assert "Possible duplicates to review: 1" in out["content"][0]["text"]
    report = (tmp_path / "o" / "integrity-report.md").read_text(encoding="utf-8")
    assert "## Possible duplicates to review" in report
    assert "asha.patel@acme.com, ravi.patel@acme.com" in report


# ---------------------------------------------------------------------------
# funnel_leak on a custom pipeline
# ---------------------------------------------------------------------------


def _custom_pipeline_deals(tmp_path):
    deals = [
        {"id": "1", "properties": {"dealname": "Stuck", "amount": "1000", "dealstage": "discovery", "hs_lastmodifieddate": "2026-01-01T00:00:00Z"}},
        {"id": "2", "properties": {"dealname": "Moving", "amount": "2000", "dealstage": "proposal", "hs_lastmodifieddate": "2026-08-18T00:00:00Z"}},
        {"id": "3", "properties": {"dealname": "Won", "amount": "3000", "dealstage": "won", "hs_lastmodifieddate": "2026-01-01T00:00:00Z"}},
    ]
    path = tmp_path / "deals.json"
    path.write_text(json.dumps(deals), encoding="utf-8")
    return str(path)


def test_default_stage_order_cannot_judge_a_custom_pipeline(tmp_path):
    out = call_tool("funnel_leak", {"deals_file": _custom_pipeline_deals(tmp_path), "out_dir": str(tmp_path), "as_of": "2026-08-19"})
    assert out["structuredContent"]["stalled_count"] == 0


def test_stage_order_makes_a_custom_pipeline_judgeable(tmp_path):
    out = call_tool(
        "funnel_leak",
        {
            "deals_file": _custom_pipeline_deals(tmp_path),
            "out_dir": str(tmp_path),
            "as_of": "2026-08-19",
            "stage_order": ["discovery", "proposal", "won"],
        },
    )
    assert not out.get("isError"), out["content"][0]["text"]
    structured = out["structuredContent"]
    assert structured["stalled_count"] == 1  # only the open deal untouched since January
    assert structured["leak_total"] == 500.0  # half of the one stalled deal's amount
    assert structured["stage_order"] == ["discovery", "proposal", "won"]


@pytest.mark.parametrize("bad", ["discovery,won", ["only-one"], [1, 2]])
def test_bad_stage_order_is_reported_as_content_not_crash(bad, tmp_path):
    out = call_tool("funnel_leak", {"deals_file": _custom_pipeline_deals(tmp_path), "out_dir": str(tmp_path), "stage_order": bad})
    assert out["isError"] is True
    assert "stage_order" in out["content"][0]["text"]


def test_confined_symlink_pointing_outside_is_refused(sandbox, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (sandbox / "link").symlink_to(outside, target_is_directory=True)
    out = call_tool("audit_crm", {"contacts_file": "contacts.json", "out_dir": "link/reports"})
    assert out["isError"] is True
    assert not (outside / "reports").exists()


# ---------------------------------------------------------------------------
# --as-of on the CLI
# ---------------------------------------------------------------------------


def test_cli_audit_as_of_reproduces_the_documented_score(tmp_path, capsys):
    from gtmos.cli import build_parser

    args = build_parser().parse_args(["audit", "--input", str(CONTACTS), "--out", str(tmp_path), "--as-of", "2026-08-19"])
    assert args.func(args) == 0
    assert "portal score 77.5 (B)" in capsys.readouterr().out


def test_cli_cortex_accepts_as_of_in_every_mode(tmp_path, capsys):
    from gtmos.cli import build_parser

    base = ["cortex", "--input", str(CONTACTS), "--deals", str(DEALS), "--out", str(tmp_path), "--as-of", "2026-08-19"]
    for extra in ([], ["--graph"], ["--proposals"]):
        args = build_parser().parse_args(base + extra)
        assert args.func(args) == 0
    capsys.readouterr()


def test_cli_bad_as_of_is_a_clean_error(tmp_path, capsys):
    from gtmos.cli import build_parser

    args = build_parser().parse_args(["audit", "--input", str(CONTACTS), "--out", str(tmp_path), "--as-of", "yesterday"])
    assert args.func(args) == 1
    assert "as_of" in capsys.readouterr().out
