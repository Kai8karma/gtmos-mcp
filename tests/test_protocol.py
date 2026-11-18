"""Protocol surface: both MCP eras, and input that must never kill the server.

Modern era (2026-07-28) requests carry their protocol version in
params._meta; legacy era (2025-06-18) requests do not and follow the
initialize handshake. One process serves both.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from gtmos.mcp import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    LEGACY_VERSION,
    METHOD_NOT_FOUND,
    MODERN_VERSIONS,
    SUPPORTED_VERSIONS,
    TOOLS,
    UNSUPPORTED_PROTOCOL_VERSION,
    handle_message,
    serve,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONTACTS = str(FIXTURES / "contacts_sample.json")
MODERN = MODERN_VERSIONS[0]
SERVER_INFO = "io.modelcontextprotocol/serverInfo"


def _meta(version: str = MODERN) -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
    }


def _request(method: str, params: dict | None = None, *, msg_id: int = 1, modern: bool = True) -> dict:
    body = dict(params or {})
    if modern:
        body["_meta"] = _meta()
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": body}


# ---------------------------------------------------------------------------
# modern era: 2026-07-28
# ---------------------------------------------------------------------------


def test_discover_advertises_both_eras():
    result = handle_message(_request("server/discover"))["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == SUPPORTED_VERSIONS
    assert MODERN in result["supportedVersions"] and LEGACY_VERSION in result["supportedVersions"]
    assert result["capabilities"] == {"tools": {}}
    assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
    assert result["cacheScope"] in ("public", "private")
    assert result["_meta"][SERVER_INFO]["name"] == "gtmos"


def test_discover_answers_a_probe_that_sends_no_meta():
    # A client may probe before it has picked a version.
    result = handle_message({"jsonrpc": "2.0", "id": 9, "method": "server/discover"})["result"]
    assert result["supportedVersions"] == SUPPORTED_VERSIONS


def test_modern_tools_list_needs_no_handshake_and_is_cacheable():
    result = handle_message(_request("tools/list"))["result"]
    assert result["resultType"] == "complete"
    assert [t["name"] for t in result["tools"]] == [t["name"] for t in TOOLS]
    assert result["ttlMs"] > 0 and result["cacheScope"] == "public"
    assert result["_meta"][SERVER_INFO]["version"]


def test_tools_list_order_is_stable():
    first = handle_message(_request("tools/list"))["result"]["tools"]
    second = handle_message(_request("tools/list", msg_id=2))["result"]["tools"]
    assert [t["name"] for t in first] == [t["name"] for t in second]


def test_modern_tools_call_returns_complete_result_with_structured_content(tmp_path):
    resp = handle_message(
        _request("tools/call", {"name": "audit_crm", "arguments": {"contacts_file": CONTACTS, "out_dir": str(tmp_path)}})
    )
    result = resp["result"]
    assert result["resultType"] == "complete"
    assert not result.get("isError")
    assert result["content"][0]["type"] == "text"
    structured = result["structuredContent"]
    assert structured["total_records"] == 25
    assert 0 <= structured["portal_score"] <= 100
    assert structured["grade"] in "ABCDF"
    assert set(structured["dimension_averages"]) == {"completeness", "validity", "freshness", "ownership", "consistency"}


def test_modern_tool_failure_is_still_a_complete_result():
    result = handle_message(_request("tools/call", {"name": "audit_crm", "arguments": {}}))["result"]
    assert result["resultType"] == "complete"
    assert result["isError"] is True


def test_unsupported_version_is_rejected_with_the_supported_list():
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": _meta("1900-01-01")}}
    error = handle_message(msg)["error"]
    assert error["code"] == UNSUPPORTED_PROTOCOL_VERSION == -32022
    assert error["data"] == {"supported": SUPPORTED_VERSIONS, "requested": "1900-01-01"}


def test_discover_with_unsupported_version_is_a_modern_error_not_a_legacy_fallback():
    msg = {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {"_meta": _meta("2099-01-01")}}
    assert handle_message(msg)["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION


def test_ping_is_gone_in_the_modern_era():
    assert handle_message(_request("ping"))["error"]["code"] == METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# legacy era: 2025-06-18
# ---------------------------------------------------------------------------


def test_legacy_handshake_is_unchanged():
    resp = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert resp["result"]["protocolVersion"] == LEGACY_VERSION
    assert resp["result"]["capabilities"] == {"tools": {}}
    assert resp["result"]["serverInfo"]["name"] == "gtmos"


def test_legacy_results_gain_no_modern_fields():
    listing = handle_message(_request("tools/list", modern=False))["result"]
    assert set(listing) == {"tools"}
    call = handle_message(_request("tools/call", {"name": "audit_crm", "arguments": {}}, modern=False))["result"]
    assert "resultType" not in call and "_meta" not in call


def test_legacy_ping_still_answers():
    assert handle_message(_request("ping", modern=False))["result"] == {}


# ---------------------------------------------------------------------------
# tool metadata
# ---------------------------------------------------------------------------


def test_every_tool_declares_title_schema_and_honest_annotations():
    for tool in TOOLS:
        assert tool["title"]
        assert tool["inputSchema"]["type"] == "object"
        assert "as_of" in tool["inputSchema"]["properties"]
        writes_reports = "out_dir" in tool["inputSchema"]["properties"]
        # A tool that writes report files must not claim to be read-only.
        assert tool["annotations"]["readOnlyHint"] is (not writes_reports), tool["name"]


def test_funnel_leak_schema_matches_the_engine_default():
    from gtmos.funnel.engine import DEFAULT_STALL_DAYS

    props = next(t for t in TOOLS if t["name"] == "funnel_leak")["inputSchema"]["properties"]
    assert f"default {DEFAULT_STALL_DAYS}" in props["stall_days"]["description"]
    assert "out_dir" in props


# ---------------------------------------------------------------------------
# malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [[1, 2, 3], "tools/list", 42, None, True])
def test_non_object_message_is_invalid_request(bad):
    resp = handle_message(bad)
    assert resp["id"] is None
    assert resp["error"]["code"] == INVALID_REQUEST


@pytest.mark.parametrize("bad_params", ["not-an-object", [1, 2], 7])
def test_non_object_params_is_invalid_params(bad_params):
    resp = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": bad_params})
    assert resp["error"]["code"] == INVALID_PARAMS


def test_non_object_params_on_a_notification_is_dropped_silently():
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": "x"}) is None


def test_non_string_method_is_invalid_request():
    assert handle_message({"jsonrpc": "2.0", "id": 6, "method": 12})["error"]["code"] == INVALID_REQUEST


def test_unknown_tool_is_a_protocol_error():
    resp = handle_message(_request("tools/call", {"name": "nope", "arguments": {}}, modern=False))
    assert resp["error"]["code"] == INVALID_PARAMS
    assert "unknown tool" in resp["error"]["message"]


@pytest.mark.parametrize("bad_name", [None, 3, ["audit_crm"]])
def test_non_string_tool_name_is_a_protocol_error(bad_name):
    resp = handle_message(_request("tools/call", {"name": bad_name}, modern=False))
    assert resp["error"]["code"] == INVALID_PARAMS


def test_non_object_arguments_is_a_protocol_error():
    resp = handle_message(_request("tools/call", {"name": "audit_crm", "arguments": "x"}, modern=False))
    assert resp["error"]["code"] == INVALID_PARAMS


def test_serve_outlives_every_malformed_line():
    lines = [
        "not json at all",
        "[1,2,3]",
        '"just a string"',
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "not-an-object"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": 5}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
    ]
    out = io.StringIO()
    assert serve(io.StringIO("\n".join(lines) + "\n"), out) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(responses) == len(lines)
    assert all("error" in r for r in responses[:-1])
    # the last request is still answered: the session survived everything before it
    assert responses[-1] == {"jsonrpc": "2.0", "id": 3, "result": {}}
