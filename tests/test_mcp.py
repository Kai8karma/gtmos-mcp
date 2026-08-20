"""Tests for gtmos.mcp - the stdio MCP server.

handle_message is pure, so the whole protocol surface is exercised without
spawning a process. No network, no CRM: the live-portal paths are never
touched here, only the file-input and failure paths.
"""

from __future__ import annotations

import io
import json

from gtmos.cli import build_parser
from gtmos.mcp import (
    HANDLERS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    TOOLS,
    call_tool,
    handle_message,
    serve,
)

CONTACTS = [
    {"id": "1", "properties": {"email": "a@acme.io", "firstname": "A", "company": "Acme"}},
    {"id": "2", "properties": {"email": "a@acme.io", "firstname": "A", "company": "Acme"}},
    {"id": "3", "properties": {"email": "", "firstname": "", "company": ""}},
]

CORTEX_CONTACTS = [
    {
        "email": "a@acme.io", "owner": "rep1", "lifecyclestage": "opportunity",
        "createdate": "2026-08-01T00:00:00Z", "notes_last_contacted": "2026-08-02T00:00:00Z",
        "hs_lastmodifieddate": "2026-08-10T00:00:00Z", "hs_analytics_source": "ORGANIC_SEARCH",
    },
    {
        "email": "b@acme.io", "owner": None, "lifecyclestage": None,
        "createdate": "2026-08-01T00:00:00Z", "hs_analytics_source": None,
    },
]


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_initialize_advertises_tools_and_version():
    resp = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "gtmos"


def test_tools_list_matches_registry_and_has_schemas():
    resp = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert {t["name"] for t in tools} == {t["name"] for t in TOOLS}
    assert {t["name"] for t in tools} == set(HANDLERS)
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_notification_gets_no_response():
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_errors():
    resp = handle_message({"jsonrpc": "2.0", "id": 3, "method": "nope/nope"})
    assert resp["error"]["code"] == METHOD_NOT_FOUND


def test_ping():
    assert handle_message({"jsonrpc": "2.0", "id": 4, "method": "ping"})["result"] == {}


# ---------------------------------------------------------------------------
# tool dispatch
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_iserror_not_raise():
    out = call_tool("does_not_exist", {})
    assert out["isError"] is True
    assert "unknown tool" in out["content"][0]["text"]


def test_tool_failure_is_reported_as_content_not_crash():
    # no source given -> handler raises -> must surface as isError content
    out = call_tool("audit_crm", {})
    assert out["isError"] is True
    assert "contacts_file" in out["content"][0]["text"]


def test_audit_crm_scores_a_file(tmp_path):
    src = tmp_path / "contacts.json"
    src.write_text(json.dumps(CONTACTS), encoding="utf-8")
    out = call_tool(
        "audit_crm",
        {"contacts_file": str(src), "acv": 1000, "out_dir": str(tmp_path / "out")},
    )
    assert not out.get("isError"), out["content"][0]["text"]
    text = out["content"][0]["text"]
    assert "Scored 3 records" in text
    assert "Portal integrity score" in text
    assert (tmp_path / "out").is_dir()


def test_cortex_scorecard_scores_a_file(tmp_path):
    src = tmp_path / "contacts.json"
    src.write_text(json.dumps(CORTEX_CONTACTS), encoding="utf-8")
    out = call_tool(
        "cortex_scorecard",
        {"contacts_file": str(src), "out_dir": str(tmp_path / "out")},
    )
    assert not out.get("isError"), out["content"][0]["text"]
    text = out["content"][0]["text"]
    assert "Composite GTM ops health" in text
    assert "Dimensions (worst first)" in text
    assert (tmp_path / "out" / "ops-scorecard.md").exists()
    assert (tmp_path / "out" / "cortex-scores.json").exists()


def test_cortex_scorecard_missing_source_is_reported_as_content_not_crash():
    out = call_tool("cortex_scorecard", {})
    assert out["isError"] is True
    assert "contacts_file" in out["content"][0]["text"]


def test_ops_signals_scans_a_file(tmp_path):
    src = tmp_path / "contacts.json"
    src.write_text(json.dumps(CORTEX_CONTACTS), encoding="utf-8")
    out = call_tool("ops_signals", {"contacts_file": str(src), "sla_hours": 1})
    assert not out.get("isError"), out["content"][0]["text"]
    text = out["content"][0]["text"]
    assert "contacts" in text and "deals scanned" in text
    assert "routing_sla_breach" in text


def test_ops_signals_missing_source_is_reported_as_content_not_crash():
    out = call_tool("ops_signals", {})
    assert out["isError"] is True
    assert "contacts_file" in out["content"][0]["text"]


def test_tools_call_routes_through_handle_message(tmp_path):
    src = tmp_path / "contacts.json"
    src.write_text(json.dumps(CONTACTS), encoding="utf-8")
    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "audit_crm",
                "arguments": {"contacts_file": str(src), "out_dir": str(tmp_path / "o")},
            },
        }
    )
    assert resp["result"]["content"][0]["type"] == "text"


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def test_serve_reads_lines_and_writes_responses():
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    stdout = io.StringIO()
    assert serve(stdin, stdout) == 0
    lines = [json.loads(x) for x in stdout.getvalue().strip().splitlines()]
    assert [m["id"] for m in lines] == [1, 2]  # notification produced no line


def test_serve_survives_bad_json():
    stdout = io.StringIO()
    serve(io.StringIO("{not json\n"), stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == PARSE_ERROR


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_mcp_dry_run_lists_tools(capsys):
    parser = build_parser()
    args = parser.parse_args(["mcp", "--dry-run"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "audit_crm" in out and "funnel_leak" in out
    assert "cortex_scorecard" in out and "ops_signals" in out


def test_cli_mcp_config_emits_valid_json(capsys):
    parser = build_parser()
    args = parser.parse_args(["mcp", "--config"])
    assert args.func(args) == 0
    body = capsys.readouterr().out
    block = json.loads(body[body.index("{") :])
    assert block["mcpServers"]["gtmos"]["args"] == ["mcp"]
