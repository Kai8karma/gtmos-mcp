"""Offline smoke tests for gtmos.funnel — pure-Python leak engine, zero network."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from gtmos.funnel import engine, fetch, report

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "deals_sample.json"
NOW_TS = datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp()


def _load():
    return fetch.load_json(str(FIXTURE_PATH))


def _run():
    deals = _load()
    return engine.run_engine(deals, engine.DEFAULT_STAGE_ORDER, stall_days=21, now=NOW_TS)


def test_fixture_has_15_deals():
    assert len(_load()) == 15


def test_stage_counts_and_amounts():
    result = _run()
    assert result.total_deals == 15
    assert result.stage_counts["appointmentscheduled"] == 1
    assert result.stage_counts["qualifiedtobuy"] == 2
    assert result.stage_counts["presentationscheduled"] == 2
    assert result.stage_counts["decisionmakerboughtin"] == 1
    assert result.stage_counts["contractsent"] == 1
    assert result.stage_counts["closedwon"] == 4
    assert result.stage_counts["closedlost"] == 3
    assert result.stage_counts["somethingcustom"] == 1

    assert result.stage_amounts["closedwon"] == 39000.0
    assert result.stage_amounts["appointmentscheduled"] == 8000.0


def test_stalled_detection_with_injected_now():
    result = _run()
    stalled_ids = {sd.id for sd in result.stalled_deals}
    assert stalled_ids == {"5", "6", "9", "13"}
    assert result.stalled_count == 4
    # id 7 (3 days stale) and id 8 (8 days stale) are under the 21-day threshold
    assert "7" not in stalled_ids
    assert "8" not in stalled_ids
    # id 11 has an unparseable hs_lastmodifieddate and must be skipped, not crash
    assert "11" not in stalled_ids
    # id 14 sits in an unknown stage, not in stage_order, so it's excluded too
    assert "14" not in stalled_ids
    assert result.stalled_value == 34500.0


def test_leak_total_arithmetic_exact():
    result = _run()
    # closedlost_value = 5000 + 15000 + 7500(imputed) = 27500
    # stalled_value = 8000 + 12000 + 7500(imputed) + 7000 = 34500
    # leak_total = 27500 + 0.5 * 34500 = 44750.0
    assert result.closedlost_value == 27500.0
    assert result.closedlost_count == 3
    assert result.leak_total == 44750.0


def test_imputation_path():
    result = _run()
    assert result.imputed_value == 7500.0
    assert result.unvalued_count == 3
    assert set(result.unvalued_deal_ids) == {"8", "9", "15"}


def test_velocity_median_days_skips_malformed_createdate():
    result = _run()
    # won deals: id1 (31d), id2 (41d), id10 (9d); id12 has a bad createdate and is skipped
    assert result.velocity_median_days == 31.0


def test_malformed_dates_do_not_crash():
    # engine.run_engine already ran inside _run() above without raising;
    # directly exercise the defensive parsers too.
    assert engine.parse_date("not-a-date") is None
    assert engine.parse_date("bad-date") is None
    assert engine.parse_date(None) is None
    assert engine.parse_date("1777593600000") is not None
    assert engine.parse_amount("not-a-number") is None
    assert engine.parse_amount(None) is None


def test_conversion_survivorship_counts():
    result = _run()
    first_step = result.conversion[0]
    assert first_step.from_stage == "appointmentscheduled"
    assert first_step.to_stage == "qualifiedtobuy"
    # all 11 deals sitting in a known stage_order stage are "at or past" stage 0
    # (closedlost and the unknown "somethingcustom" stage are excluded)
    assert first_step.from_count == 11


def test_report_writes_files(tmp_path):
    result = _run()
    out_dir = tmp_path / "funnel-out"
    summary = report.render(result, str(out_dir))

    report_path = Path(summary["report_path"])
    json_path = out_dir / "funnel.json"

    assert report_path.exists()
    assert json_path.exists()
    assert summary["leak_total"] == 44750.0
    assert summary["stalled_count"] == 4

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["leak_total"] == 44750.0

    report_text = report_path.read_text(encoding="utf-8")
    assert "44,750" in report_text or "44750" in report_text


def test_empty_deals_do_not_crash():
    result = engine.run_engine([], engine.DEFAULT_STAGE_ORDER, now=NOW_TS)
    assert result.total_deals == 0
    assert result.leak_total == 0.0
