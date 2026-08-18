"""gtmos.mcp - Model Context Protocol server over stdio.

Lets a client's own Claude (Desktop, Code, or any MCP host) call the audit
engine directly instead of pasting prompts. The wedge is preserved by
construction: the server runs on the client's machine, reads their CRM with
their own token, and every tool here is READ-ONLY. Nothing in this module
writes to a CRM, sends mail, or calls a network API other than the client's
own HubSpot endpoint.

Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, per the MCP
stdio transport. Implemented on the stdlib because this package ships with
zero dependencies (pyproject: dependencies = []), and a protocol this small
does not justify breaking that.

handle_message() is deliberately pure - message in, response dict (or None
for notifications) out - so the whole protocol surface is testable without
spawning a process or binding a socket.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, TextIO

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "gtmos"

# JSON-RPC 2.0 reserved codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def _tool_audit_crm(args: dict) -> str:
    """Score CRM contacts for integrity leaks. Read-only."""
    from gtmos.audit import engine, fetch, report

    contacts_file = args.get("contacts_file")
    if contacts_file:
        contacts = fetch.load_json(contacts_file)
    elif args.get("portal"):
        contacts = fetch.fetch_hubspot(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")

    if not contacts:
        raise ValueError("no contacts found in source")

    result = engine.run_engine(contacts)
    acv = float(args.get("acv") or 0.0)
    summary = report.render(result, acv, args.get("out_dir") or "./audit-out")

    lines = [
        f"Scored {result.total_records} records.",
        f"Portal integrity score: {summary['portal_score']:.1f} ({summary['grade']}).",
        f"Duplicate clusters found: {len(result.dupe_clusters)}.",
        "Dimension averages (worst first):",
    ]
    for name, value in sorted(result.dimension_averages.items(), key=lambda kv: kv[1]):
        lines.append(f"  - {name}: {value:.1f}")
    lines.append(f"Full report written to: {summary['report_path']}")
    return "\n".join(lines)


def _tool_funnel_leak(args: dict) -> str:
    """Analyse deal-stage revenue leaks. Read-only."""
    from gtmos.funnel import engine as funnel_engine
    from gtmos.funnel import fetch as funnel_fetch
    from gtmos.funnel import report as funnel_report

    deals_file = args.get("deals_file")
    if deals_file:
        deals = funnel_fetch.load_json(deals_file)
    elif args.get("portal"):
        deals = funnel_fetch.fetch_hubspot_deals(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass deals_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")

    if not deals:
        raise ValueError("no deals found in source")

    # Mirror the CLI: with no amounts anywhere, median imputation has nothing
    # to work from, so fall back to the caller's ACV assumption.
    acv = float(args.get("acv") or 0.0)
    if acv and funnel_engine.all_amounts_missing(deals):
        for deal in deals:
            deal["amount"] = acv

    stall_days = int(args.get("stall_days") or funnel_engine.DEFAULT_STALL_DAYS)
    result = funnel_engine.run_engine(deals, funnel_engine.DEFAULT_STAGE_ORDER, stall_days=stall_days)
    summary = funnel_report.render(result, args.get("out_dir") or "./funnel-out")

    return "\n".join(
        [
            f"Analyzed {result.total_deals} deals.",
            f"Leaking ${summary['leak_total']:,.0f} across {summary['stalled_count']} stalled deals "
            f"(stall threshold: {stall_days} days).",
            f"Full report written to: {summary['report_path']}",
        ]
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "audit_crm",
        "description": (
            "Score a CRM export or live HubSpot portal for data-integrity leaks. "
            "Returns the portal integrity score, grade, duplicate clusters, and the "
            "weakest dimensions. Read-only: never writes to the CRM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contacts_file": {"type": "string", "description": "Path to a contacts JSON export."},
                "portal": {"type": "boolean", "description": "Fetch live from HubSpot using GTMOS_HUBSPOT_TOKEN."},
                "acv": {"type": "number", "description": "Average contract value, for the dollar-leak estimate."},
                "out_dir": {"type": "string", "description": "Where to write the report (default ./audit-out)."},
            },
        },
    },
    {
        "name": "funnel_leak",
        "description": (
            "Analyse deals by stage to find stalled pipeline and where revenue leaks "
            "out of the funnel. Read-only: never writes to the CRM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deals_file": {"type": "string", "description": "Path to a deals JSON export."},
                "portal": {"type": "boolean", "description": "Fetch live deals using GTMOS_HUBSPOT_TOKEN."},
                "acv": {"type": "number", "description": "Average contract value, for imputing missing amounts."},
                "stall_days": {"type": "integer", "description": "Days untouched before a deal counts as stalled (default 30)."},
            },
        },
    },
]

HANDLERS: dict[str, Callable[[dict], str]] = {
    "audit_crm": _tool_audit_crm,
    "funnel_leak": _tool_funnel_leak,
}


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def _result(msg_id: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": payload}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def call_tool(name: str, arguments: dict) -> dict:
    """Run a tool, mapping failures to an isError result rather than raising.

    A crashed tool must not take the server down: MCP hosts expect tool
    failures as content, so the model can read the message and adjust.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        return {"content": [{"type": "text", "text": handler(arguments or {})}]}
    except Exception as exc:  # noqa: BLE001 - surface any tool failure to the host
        return {"content": [{"type": "text", "text": f"{name} failed: {exc}"}], "isError": True}


def handle_message(msg: dict) -> dict | None:
    """Map one JSON-RPC message to its response. None for notifications."""
    msg_id = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg

    if method is None:
        return None if is_notification else _error(msg_id, INVALID_REQUEST, "missing method")

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _version()},
            },
        )

    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        params = msg.get("params") or {}
        return _result(msg_id, call_tool(params.get("name", ""), params.get("arguments") or {}))

    if method == "ping":
        return _result(msg_id, {})

    if is_notification:
        return None
    return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _version() -> str:
    from gtmos import __version__

    return __version__


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, PARSE_ERROR, "invalid JSON")) + "\n")
            stdout.flush()
            continue

        response = handle_message(msg)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
