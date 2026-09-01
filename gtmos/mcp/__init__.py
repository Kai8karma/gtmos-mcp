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


def _tool_cortex_scorecard(args: dict) -> str:
    """Score GTM ops health across 5 dimensions from a CRM export or live HubSpot portal. Read-only."""
    from gtmos.cortex import engine, fetch, report

    contacts_file = args.get("contacts_file")
    if contacts_file:
        contacts_raw = fetch.load_contacts_raw(contacts_file)
    elif args.get("portal"):
        contacts_raw = fetch.fetch_portal_contacts(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")

    if not contacts_raw:
        raise ValueError("no contacts found in source")

    deals_file = args.get("deals_file")
    deals: list = []
    if deals_file:
        deals = fetch.load_deals(deals_file)
    elif args.get("portal"):
        try:
            deals = fetch.fetch_portal_deals(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError:
            deals = []  # deals are optional for the scorecard; a missing scope just drops the lifecycle pipeline check

    raw_sla_hours = args.get("sla_hours")
    sla_hours = float(raw_sla_hours) if raw_sla_hours is not None else engine.DEFAULT_SLA_HOURS
    result = engine.run_engine(contacts_raw, deals, sla_hours=sla_hours)
    summary = report.render(result, args.get("out_dir") or "./cortex-out")

    lines = [
        f"Assessed {result.total_contacts} contacts and {result.total_deals} deals.",
        f"Composite GTM ops health: {summary['composite_score']:.1f} ({summary['grade']}).",
        summary["verdict"],
        "Dimensions (worst first):",
    ]
    scored = sorted((d for d in result.dimensions.values() if d.score is not None), key=lambda d: d.score)
    for dim in scored:
        lines.append(f"  - {dim.name}: {dim.score:.1f} ({dim.status})")
    if result.excluded_dimensions:
        lines.append(f"Insufficient data (excluded from composite): {', '.join(result.excluded_dimensions)}.")
    if result.fixes:
        top = result.fixes[0]
        lines.append(f"{len(result.fixes)} ranked fix(es); worst is {top.severity} on {top.dimension}.")
    lines.append(f"Full report written to: {summary['report_path']}")
    return "\n".join(lines)


def _tool_ops_signals(args: dict) -> str:
    """Terse severity-tagged signals on the GTM stack itself: SLA breaches, property drift, lifecycle breaks, sync-failure proxies. Read-only."""
    from gtmos.cortex import engine, fetch

    contacts_file = args.get("contacts_file")
    if contacts_file:
        contacts_raw = fetch.load_contacts_raw(contacts_file)
    elif args.get("portal"):
        contacts_raw = fetch.fetch_portal_contacts(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")

    if not contacts_raw:
        raise ValueError("no contacts found in source")

    deals_file = args.get("deals_file")
    deals: list = []
    if deals_file:
        deals = fetch.load_deals(deals_file)
    elif args.get("portal"):
        try:
            deals = fetch.fetch_portal_deals(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError:
            deals = []

    raw_sla_hours = args.get("sla_hours")
    sla_hours = float(raw_sla_hours) if raw_sla_hours is not None else engine.DEFAULT_SLA_HOURS
    signals = engine.compute_signals(contacts_raw, deals, sla_hours=sla_hours)

    lines = [f"{signals.total_contacts} contacts, {signals.total_deals} deals scanned. SLA threshold: {sla_hours:.0f}h."]
    for sig in signals.signals:
        lines.append(f"[{sig.severity}] {sig.name}: {sig.count} - {sig.detail}")
        for offender in sig.top_offenders:
            lines.append(f"    - {offender}")
    return "\n".join(lines)


def _cortex_sources(args: dict) -> tuple[list, list, float]:
    """(contacts_raw, deals, sla_hours) for the graph and proposals tools.
    Same resolution rules as cortex_scorecard: file wins over portal, deals
    are optional, an explicit sla_hours of 0 is honored."""
    from gtmos.cortex import engine, fetch

    contacts_file = args.get("contacts_file")
    if contacts_file:
        contacts_raw = fetch.load_contacts_raw(contacts_file)
    elif args.get("portal"):
        contacts_raw = fetch.fetch_portal_contacts(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")
    if not contacts_raw:
        raise ValueError("no contacts found in source")

    deals_file = args.get("deals_file")
    deals: list = []
    if deals_file:
        deals = fetch.load_deals(deals_file)
    elif args.get("portal"):
        try:
            deals = fetch.fetch_portal_deals(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError:
            deals = []

    raw_sla_hours = args.get("sla_hours")
    sla_hours = float(raw_sla_hours) if raw_sla_hours is not None else engine.DEFAULT_SLA_HOURS
    return contacts_raw, deals, sla_hours


def _tool_cortex_graph(args: dict) -> str:
    """Build the context graph (contacts, accounts, owners, sources, deals) and roll it up per account. Read-only."""
    from gtmos.cortex import graph as cortex_graph

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    graph = cortex_graph.build_graph(contacts_raw, deals, sla_hours=sla_hours)
    top = int(args.get("top") or 10)
    summary = cortex_graph.render(graph, args.get("out_dir") or "./cortex-out", top=top)
    text = cortex_graph.summarize(graph, account=args.get("account"), top=top)
    return f"{text}\nGraph written to: {summary['graph_path']} (report: {summary['report_path']})"


def _tool_cortex_proposals(args: dict) -> str:
    """Run the governed agents over the context graph and return a policy-gated proposal queue. Read-only: proposes, never applies."""
    from gtmos.cortex import govern
    from gtmos.funnel import engine as funnel_engine

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    policy = govern.load_policy(args["policy_file"]) if args.get("policy_file") else govern.Policy()
    raw_stall = args.get("stall_days")
    stall_days = int(raw_stall) if raw_stall is not None else funnel_engine.DEFAULT_STALL_DAYS
    result = govern.run_engine(contacts_raw, deals, policy=policy, sla_hours=sla_hours, stall_days=stall_days)
    summary = govern.render(result, args.get("out_dir") or "./cortex-out")
    return f"{govern.summarize(result)}\nFull queue written to: {summary['report_path']}"


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
    {
        "name": "cortex_scorecard",
        "description": (
            "Score GTM ops health across five dimensions (data quality, lifecycle, routing, "
            "automation, reporting) from a CRM export or live HubSpot portal. Returns a weighted "
            "composite score, grade, verdict, and severity-ranked fixes sequenced into week 1 / "
            "weeks 2-4 / quarter. Read-only: never writes to the CRM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contacts_file": {"type": "string", "description": "Path to a contacts JSON export."},
                "deals_file": {
                    "type": "string",
                    "description": "Path to a deals JSON export (optional; enables the pipeline stage-divergence check).",
                },
                "portal": {"type": "boolean", "description": "Fetch live from HubSpot using GTMOS_HUBSPOT_TOKEN."},
                "sla_hours": {"type": "number", "description": "Routing SLA in hours, createdate to first touch (default 24)."},
                "out_dir": {"type": "string", "description": "Where to write the report (default ./cortex-out)."},
            },
        },
    },
    {
        "name": "ops_signals",
        "description": (
            "Terse severity-tagged signal list on the GTM stack itself, not the buyer: routing SLA "
            "breaches, property drift, lifecycle integrity breaks, and sync-failure proxies, with "
            "counts and top offenders. Read-only: never writes to the CRM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contacts_file": {"type": "string", "description": "Path to a contacts JSON export."},
                "deals_file": {"type": "string", "description": "Path to a deals JSON export (optional)."},
                "portal": {"type": "boolean", "description": "Fetch live from HubSpot using GTMOS_HUBSPOT_TOKEN."},
                "sla_hours": {"type": "number", "description": "Routing SLA in hours, createdate to first touch (default 24)."},
            },
        },
    },
    {
        "name": "cortex_graph",
        "description": (
            "Build the Cortex context graph from a CRM export or live HubSpot portal: contacts, "
            "accounts (by email domain), owners, sources, and deals, with explicit-only edges and a "
            "per-account rollup (owners, unrouted contacts, source conflicts, SLA breaches, open "
            "pipeline, last touch, flags). Pass account to drill into one account. Read-only: never "
            "writes to the CRM."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contacts_file": {"type": "string", "description": "Path to a contacts JSON export."},
                "deals_file": {"type": "string", "description": "Path to a deals JSON export (optional; deals link to accounts only via an explicit association property)."},
                "portal": {"type": "boolean", "description": "Fetch live from HubSpot using GTMOS_HUBSPOT_TOKEN."},
                "sla_hours": {"type": "number", "description": "Routing SLA in hours, createdate to first touch (default 24)."},
                "account": {"type": "string", "description": "Email domain or company name to drill into (optional)."},
                "top": {"type": "integer", "description": "How many accounts to list, open pipeline first (default 10)."},
                "out_dir": {"type": "string", "description": "Where to write context-graph.json/.md (default ./cortex-out)."},
            },
        },
    },
    {
        "name": "cortex_proposals",
        "description": (
            "Run the Cortex governed agents (router, deduper, lifecycle steward, attribution steward, "
            "pipeline steward, schema steward) over the context graph and return a policy-gated "
            "proposal queue: each proposal carries targets, a before/after diff, evidence, confidence, "
            "and blast radius; proposals that fail the policy (action allow-list, blast-radius cap, "
            "confidence floor) are returned as BLOCKED with the reason. Read-only: proposes, never applies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contacts_file": {"type": "string", "description": "Path to a contacts JSON export."},
                "deals_file": {"type": "string", "description": "Path to a deals JSON export (optional; enables the pipeline steward and deal-backed lifecycle evidence)."},
                "portal": {"type": "boolean", "description": "Fetch live from HubSpot using GTMOS_HUBSPOT_TOKEN."},
                "sla_hours": {"type": "number", "description": "Routing SLA in hours, createdate to first touch (default 24)."},
                "stall_days": {"type": "integer", "description": "Days untouched before a deal counts as stalled (default 21)."},
                "policy_file": {"type": "string", "description": "Path to a policy JSON override (name, allowed_actions, max_blast_records, max_blast_share, min_confidence). requires_human cannot be disabled."},
                "out_dir": {"type": "string", "description": "Where to write proposals.md/.json (default ./cortex-out)."},
            },
        },
    },
]

HANDLERS: dict[str, Callable[[dict], str]] = {
    "audit_crm": _tool_audit_crm,
    "funnel_leak": _tool_funnel_leak,
    "cortex_scorecard": _tool_cortex_scorecard,
    "ops_signals": _tool_ops_signals,
    "cortex_graph": _tool_cortex_graph,
    "cortex_proposals": _tool_cortex_proposals,
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
