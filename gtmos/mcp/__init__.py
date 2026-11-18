"""gtmos.mcp - Model Context Protocol server over stdio.

Lets a client's own Claude (Desktop, Code, or any MCP host) call the audit
engine directly instead of pasting prompts. The wedge is preserved by
construction: the server runs on the client's machine, reads their CRM with
their own token, and no tool here ever writes to a CRM, sends mail, or calls
a network API other than the client's own HubSpot endpoint. Tools do write
report files to local disk; set GTMOS_ALLOWED_DIR to confine every path a
tool may read or write to one directory.

Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, per the MCP
stdio transport. Implemented on the stdlib because this package ships with
zero dependencies (pyproject: dependencies = []), and a protocol this small
does not justify breaking that.

Two protocol eras are served from the same process:

* Modern (2026-07-28): stateless. There is no handshake; every request names
  its protocol version in ``params._meta``. ``server/discover`` advertises
  what the server supports, every result carries ``resultType``, and
  ``tools/list`` is cacheable (``ttlMs`` / ``cacheScope``).
* Legacy (2025-06-18): the ``initialize`` handshake. A request with no
  per-request protocol version is answered with legacy semantics, so hosts
  that have not moved yet keep working unchanged.

handle_message() is deliberately pure - message in, response dict (or None
for notifications) out - so the whole protocol surface is testable without
spawning a process or binding a socket.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TextIO

from gtmos.timeutil import parse_as_of

LEGACY_VERSION = "2025-06-18"
MODERN_VERSIONS = ("2026-07-28",)
SUPPORTED_VERSIONS = [*MODERN_VERSIONS, LEGACY_VERSION]
# Kept for callers that import it: the version the initialize handshake speaks.
PROTOCOL_VERSION = LEGACY_VERSION
SERVER_NAME = "gtmos"

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# The tool list is static and carries nothing user-specific, so any client or
# intermediary may cache it; an hour keeps a long-lived host from re-listing.
LIST_TTL_MS = 3_600_000
LIST_CACHE_SCOPE = "public"

ALLOWED_DIR_ENV = "GTMOS_ALLOWED_DIR"

# JSON-RPC 2.0 reserved codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# MCP 2026-07-28
UNSUPPORTED_PROTOCOL_VERSION = -32022


# ---------------------------------------------------------------------------
# argument helpers
# ---------------------------------------------------------------------------


def _safe_path(path: str) -> str:
    """Resolve a tool-supplied path, confined to GTMOS_ALLOWED_DIR when set.

    Unset (the default), the path is used as given - the server can read and
    write wherever its own process can. Set, relative paths resolve against
    that directory and anything that lands outside it is refused, symlinks
    and ``..`` included.
    """
    base = os.environ.get(ALLOWED_DIR_ENV)
    if not base:
        return path
    root = Path(base).expanduser().resolve()
    candidate = Path(path).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path is outside {ALLOWED_DIR_ENV} ({root}): {path}")
    return str(resolved)


def _file_arg(args: dict, key: str) -> str | None:
    value = args.get(key)
    return _safe_path(str(value)) if value else None


def _out_dir(args: dict, default: str) -> str:
    return _safe_path(str(args.get("out_dir") or default))


def _as_of(args: dict) -> datetime | None:
    """The moment the data is judged against. Freshness, SLA and stall checks
    all compare record timestamps to "now"; passing as_of pins that so the
    same export always produces the same score."""
    return parse_as_of(args.get("as_of"))


def _as_of_line(moment: datetime | None) -> list[str]:
    return [f"Scored as of: {moment.isoformat(timespec='seconds')}"] if moment else []


def _as_of_field(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

ToolOutput = tuple[str, dict]


def _tool_audit_crm(args: dict) -> ToolOutput:
    """Score CRM contacts for integrity leaks. Never writes to the CRM."""
    from gtmos.audit import engine, fetch, report

    contacts_file = _file_arg(args, "contacts_file")
    if contacts_file:
        contacts = fetch.load_json(contacts_file)
    elif args.get("portal"):
        contacts = fetch.fetch_hubspot(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")

    if not contacts:
        raise ValueError("no contacts found in source")

    as_of = _as_of(args)
    result = engine.run_engine(contacts, as_of)
    acv = float(args.get("acv") or 0.0)
    summary = report.render(result, acv, _out_dir(args, "./audit-out"))

    lines = [
        f"Scored {result.total_records} records.",
        f"Portal integrity score: {summary['portal_score']:.1f} ({summary['grade']}).",
        f"Duplicate clusters found: {len(result.dupe_clusters)}.",
        "Dimension averages (worst first):",
    ]
    for name, value in sorted(result.dimension_averages.items(), key=lambda kv: kv[1]):
        lines.append(f"  - {name}: {value:.1f}")
    if result.possible_dupe_clusters:
        lines.append(
            f"Possible duplicates to review: {len(result.possible_dupe_clusters)} "
            "(same surname and company, first names differ; not scored)."
        )
    lines += _as_of_line(as_of)
    lines.append(f"Full report written to: {summary['report_path']}")
    structured = {
        "total_records": result.total_records,
        "portal_score": round(summary["portal_score"], 1),
        "grade": summary["grade"],
        "duplicate_clusters": len(result.dupe_clusters),
        "possible_duplicate_clusters": len(result.possible_dupe_clusters),
        "dimension_averages": {k: round(v, 1) for k, v in result.dimension_averages.items()},
        "as_of": _as_of_field(as_of),
        "report_path": summary["report_path"],
    }
    return "\n".join(lines), structured


def _tool_funnel_leak(args: dict) -> ToolOutput:
    """Analyse deal-stage revenue leaks. Never writes to the CRM."""
    from gtmos.funnel import engine as funnel_engine
    from gtmos.funnel import fetch as funnel_fetch
    from gtmos.funnel import report as funnel_report

    deals_file = _file_arg(args, "deals_file")
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

    as_of = _as_of(args)
    stall_days = int(args.get("stall_days") or funnel_engine.DEFAULT_STALL_DAYS)
    stage_order = args.get("stage_order") or funnel_engine.DEFAULT_STAGE_ORDER
    if not isinstance(stage_order, list) or len(stage_order) < 2 or not all(isinstance(s, str) for s in stage_order):
        raise ValueError("stage_order must be a list of at least two stage ids, first stage to won stage")
    result = funnel_engine.run_engine(
        deals,
        stage_order,
        stall_days=stall_days,
        now=as_of.timestamp() if as_of else None,
    )
    summary = funnel_report.render(result, _out_dir(args, "./funnel-out"))

    lines = [
        f"Analyzed {result.total_deals} deals.",
        f"Leaking ${summary['leak_total']:,.0f} across {summary['stalled_count']} stalled deals "
        f"(stall threshold: {stall_days} days).",
        *_as_of_line(as_of),
        f"Full report written to: {summary['report_path']}",
    ]
    structured = {
        "total_deals": result.total_deals,
        "leak_total": round(summary["leak_total"], 2),
        "stalled_count": summary["stalled_count"],
        "stall_days": stall_days,
        "stage_order": list(stage_order),
        "as_of": _as_of_field(as_of),
        "report_path": summary["report_path"],
    }
    return "\n".join(lines), structured


def _cortex_sources(args: dict) -> tuple[list, list, float]:
    """(contacts_raw, deals, sla_hours) for every Cortex tool: file wins over
    portal, deals are optional (a missing scope just drops the pipeline
    checks), and an explicit sla_hours of 0 is honored."""
    from gtmos.cortex import engine, fetch

    contacts_file = _file_arg(args, "contacts_file")
    if contacts_file:
        contacts_raw = fetch.load_contacts_raw(contacts_file)
    elif args.get("portal"):
        contacts_raw = fetch.fetch_portal_contacts(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
    else:
        raise ValueError("pass contacts_file, or portal=true with GTMOS_HUBSPOT_TOKEN set")
    if not contacts_raw:
        raise ValueError("no contacts found in source")

    deals_file = _file_arg(args, "deals_file")
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


def _tool_cortex_scorecard(args: dict) -> ToolOutput:
    """Score GTM ops health across 5 dimensions from a CRM export or live HubSpot portal. Never writes to the CRM."""
    from gtmos.cortex import engine, report

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    as_of = _as_of(args)
    result = engine.run_engine(contacts_raw, deals, sla_hours=sla_hours, now=as_of)
    summary = report.render(result, _out_dir(args, "./cortex-out"))

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
    lines += _as_of_line(as_of)
    lines.append(f"Full report written to: {summary['report_path']}")
    structured = {
        "total_contacts": result.total_contacts,
        "total_deals": result.total_deals,
        "composite_score": round(summary["composite_score"], 1),
        "grade": summary["grade"],
        "verdict": summary["verdict"],
        "dimensions": {
            d.name: {"score": None if d.score is None else round(d.score, 1), "status": d.status}
            for d in result.dimensions.values()
        },
        "excluded_dimensions": list(result.excluded_dimensions),
        "fixes": [
            {"severity": f.severity, "dimension": f.dimension, "finding": f.finding, "fix": f.fix, "sequence": f.sequence}
            for f in result.fixes
        ],
        "as_of": _as_of_field(as_of),
        "report_path": summary["report_path"],
    }
    return "\n".join(lines), structured


def _tool_ops_signals(args: dict) -> ToolOutput:
    """Terse severity-tagged signals on the GTM stack itself: SLA breaches, property drift, lifecycle breaks, sync-failure proxies. Never writes to the CRM or to disk."""
    from gtmos.cortex import engine

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    as_of = _as_of(args)
    signals = engine.compute_signals(contacts_raw, deals, sla_hours=sla_hours, now=as_of)

    lines = [f"{signals.total_contacts} contacts, {signals.total_deals} deals scanned. SLA threshold: {sla_hours:.0f}h."]
    for sig in signals.signals:
        lines.append(f"[{sig.severity}] {sig.name}: {sig.count} - {sig.detail}")
        for offender in sig.top_offenders:
            lines.append(f"    - {offender}")
    lines += _as_of_line(as_of)
    structured = {
        "total_contacts": signals.total_contacts,
        "total_deals": signals.total_deals,
        "sla_hours": sla_hours,
        "signals": [
            {
                "severity": s.severity,
                "name": s.name,
                "count": s.count,
                "detail": s.detail,
                "top_offenders": list(s.top_offenders),
            }
            for s in signals.signals
        ],
        "as_of": _as_of_field(as_of),
    }
    return "\n".join(lines), structured


def _tool_cortex_graph(args: dict) -> ToolOutput:
    """Build the context graph (contacts, accounts, owners, sources, deals) and roll it up per account. Never writes to the CRM."""
    from gtmos.cortex import graph as cortex_graph

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    as_of = _as_of(args)
    graph = cortex_graph.build_graph(contacts_raw, deals, sla_hours=sla_hours, now=as_of)
    top = int(args.get("top") or 10)
    summary = cortex_graph.render(graph, _out_dir(args, "./cortex-out"), top=top)
    text = cortex_graph.summarize(graph, account=args.get("account"), top=top)
    lines = [text, *_as_of_line(as_of), f"Graph written to: {summary['graph_path']} (report: {summary['report_path']})"]
    structured = {
        "accounts": summary["accounts"],
        "nodes": summary["nodes"],
        "edges": summary["edges"],
        "as_of": _as_of_field(as_of),
        "graph_path": summary["graph_path"],
        "report_path": summary["report_path"],
    }
    return "\n".join(lines), structured


def _tool_cortex_proposals(args: dict) -> ToolOutput:
    """Run the governed agents over the context graph and return a policy-gated proposal queue. Proposes, never applies."""
    from gtmos.cortex import govern
    from gtmos.funnel import engine as funnel_engine

    contacts_raw, deals, sla_hours = _cortex_sources(args)
    policy_file = _file_arg(args, "policy_file")
    policy = govern.load_policy(policy_file) if policy_file else govern.Policy()
    raw_stall = args.get("stall_days")
    stall_days = int(raw_stall) if raw_stall is not None else funnel_engine.DEFAULT_STALL_DAYS
    as_of = _as_of(args)
    result = govern.run_engine(
        contacts_raw, deals, policy=policy, sla_hours=sla_hours, stall_days=stall_days, now=as_of
    )
    summary = govern.render(result, _out_dir(args, "./cortex-out"))
    lines = [govern.summarize(result), *_as_of_line(as_of), f"Full queue written to: {summary['report_path']}"]
    structured = {
        "policy": result.policy.name,
        "proposed": summary["proposed"],
        "blocked": summary["blocked"],
        "proposals": [
            {
                "id": p.id,
                "status": p.status,
                "agent": p.agent,
                "action": p.action,
                "severity": p.severity,
                "targets": list(p.targets),
                "before": p.before,
                "after": p.after,
                "confidence": p.confidence,
                "blast_radius": p.blast_radius,
                "blocked_reason": p.blocked_reason,
            }
            for p in result.proposals
        ],
        "as_of": _as_of_field(as_of),
        "report_path": summary["report_path"],
        "proposals_path": summary["proposals_path"],
    }
    return "\n".join(lines), structured


_AS_OF_PROPERTY = {
    "type": "string",
    "description": (
        "ISO 8601 date or datetime to judge the data against (default: now). "
        "Pin it to get the same score from the same export on any day."
    ),
}

# None of these tools touches the CRM, but most write report files to local
# disk, so they are not read-only in the protocol's sense. Re-running one
# rewrites the same files (idempotent, nothing else is removed), and
# portal=true reaches out to HubSpot (open world).
_WRITES_REPORTS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
_NO_WRITES = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "audit_crm",
        "title": "CRM data-integrity audit",
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
                "as_of": _AS_OF_PROPERTY,
                "out_dir": {"type": "string", "description": "Where to write the report (default ./audit-out)."},
            },
        },
        "annotations": _WRITES_REPORTS,
    },
    {
        "name": "funnel_leak",
        "title": "Funnel leak analysis",
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
                "stall_days": {"type": "integer", "description": "Days untouched before a deal counts as stalled (default 21)."},
                "stage_order": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Internal dealstage ids of your pipeline, first stage to the won stage. "
                        "Default is HubSpot's stock sales pipeline; a custom pipeline needs its own ids "
                        "or its deals are counted but never judged as stalled."
                    ),
                },
                "as_of": _AS_OF_PROPERTY,
                "out_dir": {"type": "string", "description": "Where to write the report (default ./funnel-out)."},
            },
        },
        "annotations": _WRITES_REPORTS,
    },
    {
        "name": "cortex_scorecard",
        "title": "GTM ops health scorecard",
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
                "as_of": _AS_OF_PROPERTY,
                "out_dir": {"type": "string", "description": "Where to write the report (default ./cortex-out)."},
            },
        },
        "annotations": _WRITES_REPORTS,
    },
    {
        "name": "ops_signals",
        "title": "GTM stack signals",
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
                "as_of": _AS_OF_PROPERTY,
            },
        },
        "annotations": _NO_WRITES,
    },
    {
        "name": "cortex_graph",
        "title": "Cortex context graph",
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
                "as_of": _AS_OF_PROPERTY,
                "out_dir": {"type": "string", "description": "Where to write context-graph.json/.md (default ./cortex-out)."},
            },
        },
        "annotations": _WRITES_REPORTS,
    },
    {
        "name": "cortex_proposals",
        "title": "Governed proposal queue",
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
                "as_of": _AS_OF_PROPERTY,
                "out_dir": {"type": "string", "description": "Where to write proposals.md/.json (default ./cortex-out)."},
            },
        },
        "annotations": _WRITES_REPORTS,
    },
]

HANDLERS: dict[str, Callable[[dict], ToolOutput]] = {
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


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _server_info() -> dict:
    return {"name": SERVER_NAME, "version": _version()}


def _finish(payload: dict, modern: bool) -> dict:
    """Stamp what a 2026-07-28 result must carry. Legacy results are left
    exactly as they were, so an older host sees no new fields."""
    if not modern:
        return payload
    meta = {**payload.get("_meta", {}), META_SERVER_INFO: _server_info()}
    return {**payload, "resultType": "complete", "_meta": meta}


def call_tool(name: str, arguments: dict) -> dict:
    """Run a tool, mapping failures to an isError result rather than raising.

    A crashed tool must not take the server down: MCP hosts expect tool
    failures as content, so the model can read the message and adjust.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        text, structured = handler(arguments or {})
    except Exception as exc:  # noqa: BLE001 - surface any tool failure to the host
        return {"content": [{"type": "text", "text": f"{name} failed: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured}


def handle_message(msg: Any) -> dict | None:
    """Map one JSON-RPC message to its response. None for notifications.

    Never raises on malformed input: a message that is not an object, or
    params that are not, come back as JSON-RPC errors.
    """
    if not isinstance(msg, dict):
        return _error(None, INVALID_REQUEST, "request must be a JSON object")

    msg_id = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg

    if not isinstance(method, str):
        return None if is_notification else _error(msg_id, INVALID_REQUEST, "missing method")

    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None if is_notification else _error(msg_id, INVALID_PARAMS, "params must be an object")

    meta = params.get("_meta")
    requested = meta.get(META_PROTOCOL_VERSION) if isinstance(meta, dict) else None
    modern = requested is not None

    if is_notification:
        return None

    if modern and requested not in MODERN_VERSIONS:
        return _error(
            msg_id,
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": SUPPORTED_VERSIONS, "requested": requested},
        )

    if method == "server/discover":
        return _result(
            msg_id,
            _finish(
                {
                    "supportedVersions": SUPPORTED_VERSIONS,
                    "capabilities": {"tools": {}},
                    "ttlMs": LIST_TTL_MS,
                    "cacheScope": LIST_CACHE_SCOPE,
                },
                modern=True,
            ),
        )

    if method == "initialize":
        # The handshake era is served at one version; a client that asks for
        # another gets this one back and decides whether it can proceed.
        return _result(
            msg_id,
            {
                "protocolVersion": LEGACY_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(),
            },
        )

    if method == "tools/list":
        payload: dict[str, Any] = {"tools": TOOLS}
        if modern:
            payload.update({"ttlMs": LIST_TTL_MS, "cacheScope": LIST_CACHE_SCOPE})
        return _result(msg_id, _finish(payload, modern))

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or name not in HANDLERS:
            return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
        if arguments is not None and not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        return _result(msg_id, _finish(call_tool(name, arguments or {}), modern))

    # ping was removed in 2026-07-28; it stays for hosts on the handshake era.
    if method == "ping" and not modern:
        return _result(msg_id, {})

    return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _version() -> str:
    from gtmos import __version__

    return __version__


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout.

    Returns when stdin closes, which is how a host shuts the server down.
    """
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

        try:
            response = handle_message(msg)
        except Exception as exc:  # noqa: BLE001 - no single message may end the session
            msg_id = msg.get("id") if isinstance(msg, dict) else None
            response = _error(msg_id, INTERNAL_ERROR, f"internal error: {exc}")
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
