"""Context graph - the substrate the Marketing Cortex reads from.

Builds a deterministic entity graph from the same contacts/deals export the
scorecard and signals already consume. Nothing here is inferred by a model:
every node is a record that exists in the export, and every edge is an
explicit property on one of those records (email domain, owner, source,
deal association). A deal with no explicit company association stays
unlinked and is counted as such - it is never attached to an account by
guessing from its name.

Nodes    contact, company (account), owner, source, deal
Edges    contact -belongs_to->  company
         owner   -owns->        contact | deal
         contact -sourced_by->  source
         deal    -for_account-> company   (explicit association only)

The per-account rollup is what the governed agents (gtmos.cortex.govern)
read: who owns the account, which contacts are unrouted, whether sources
conflict, how much open pipeline sits on it, and when it was last touched.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gtmos.audit import engine as audit_engine
from gtmos.audit import fetch as audit_fetch
from gtmos.cortex import engine as cortex_engine
from gtmos.funnel import engine as funnel_engine

COMPANY_ALIASES = audit_fetch.FIELD_ALIASES["company"]
LAST_ACTIVITY_ALIASES = audit_fetch.FIELD_ALIASES["last_activity_date"]
TOUCH_ALIASES = cortex_engine.FIRST_TOUCH_ALIASES + LAST_ACTIVITY_ALIASES

# Explicit deal-to-account association keys, priority order. A value is
# matched against a company node by email domain first, then by normalized
# company name. dealname is deliberately never consulted.
DEAL_COMPANY_ALIASES = [
    "company_domain", "hs_associated_company_domain", "associated_company",
    "Associated Company", "company", "associatedcompanyid",
]
DEAL_OWNER_ALIASES = ["hubspot_owner_id", "owner", "Deal Owner", "owner_id"]
WON_STAGES = {"closedwon", "won"}
LOST_STAGES = {"closedlost", "lost"}

# Contacts on a shared mailbox provider do not form an account by domain;
# they fall back to the company name, or to no account at all.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "yandex.com", "mail.com", "rediffmail.com",
}


@dataclass
class Node:
    id: str
    kind: str  # contact | company | owner | source | deal
    label: str
    attrs: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    dst: str
    rel: str  # belongs_to | owns | sourced_by | for_account


@dataclass
class Account:
    key: str  # email domain, or "name:<normalized company>" when no usable domain
    name: str
    contacts: int
    owners: dict[str, int]
    unowned_contacts: int
    lifecycle_stages: dict[str, int]
    missing_lifecycle: int
    sources: dict[str, int]
    source_conflict: bool
    sla_breaches: int
    last_touch: str | None  # ISO-8601, most recent touch across the account's contacts
    deals: int
    open_deals: int
    open_amount: float
    won_deals: int
    contact_ids: list[str]
    deal_ids: list[str]
    flags: list[str]


@dataclass
class ContextGraph:
    nodes: list[Node]
    edges: list[Edge]
    accounts: list[Account]  # sorted: open pipeline desc, then contacts desc, then key
    total_contacts: int
    total_deals: int
    contacts_without_account: int
    unlinked_deals: int
    sla_hours: float

    def node(self, node_id: str) -> Node | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def account(self, key: str) -> Account | None:
        wanted = key.strip().lower()
        for a in self.accounts:
            if a.key == wanted or a.name.strip().lower() == wanted:
                return a
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _email_domain(email) -> str | None:
    if not email or "@" not in str(email):
        return None
    return str(email).strip().lower().rsplit("@", 1)[-1]


def _account_key(contact: dict) -> tuple[str | None, str | None]:
    """(key, display name) for the account this contact belongs to, or
    (None, None) when neither a corporate email domain nor a company name
    is present. Domain wins over name so two spellings of the same company
    at one domain still land on one account."""
    _, email = cortex_engine._first_present_value(contact, cortex_engine.EMAIL_ALIASES)
    _, company = cortex_engine._first_present_value(contact, COMPANY_ALIASES)
    domain = _email_domain(email)
    if domain and domain not in FREEMAIL_DOMAINS and audit_engine.EMAIL_RE.match(str(email).strip()):
        return domain, (str(company).strip() if company else domain)
    if company:
        normalized = audit_engine._normalize_company(str(company))
        if normalized:
            return f"name:{normalized}", str(company).strip()
    return None, None


def _last_touch(contact: dict) -> datetime | None:
    touches: list[datetime] = []
    for alias in TOUCH_ALIASES:
        raw = contact.get(alias)
        if raw is None or (isinstance(raw, str) and audit_engine.is_junk(raw)):
            continue
        parsed = funnel_engine.parse_date(raw)
        if parsed is not None:
            touches.append(parsed)
    return max(touches) if touches else None


def _deal_status(deal: dict) -> tuple[str | None, str]:
    stage = str(deal.get("dealstage") or "").strip().lower() or None
    if stage in WON_STAGES:
        return stage, "won"
    if stage in LOST_STAGES:
        return stage, "lost"
    return stage, "open"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build_graph(
    contacts_raw: list[dict],
    deals: list[dict] | None = None,
    sla_hours: float = cortex_engine.DEFAULT_SLA_HOURS,
    now: datetime | None = None,
) -> ContextGraph:
    now = now or datetime.now(timezone.utc)
    deals = deals or []

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    account_contacts: dict[str, list[str]] = {}
    account_names: dict[str, Counter] = {}
    account_touch: dict[str, datetime] = {}
    without_account = 0

    for idx, contact in enumerate(contacts_raw):
        cid = f"contact:{idx}"
        label = cortex_engine._record_label(contact)
        _, owner = cortex_engine._first_present_value(contact, cortex_engine.OWNER_ALIASES)
        _, source = cortex_engine._first_present_value(contact, cortex_engine.SOURCE_ALIASES)
        _, stage = cortex_engine._first_present_value(contact, cortex_engine.LIFECYCLE_ALIASES)
        touched = _last_touch(contact)
        # One-record call to the shared verdict function so the graph and the
        # routing dimension can never disagree on what a breach is.
        _facts, _judged, breaches, _reversed = cortex_engine._routing_sla_verdict([contact], sla_hours, now)

        nodes[cid] = Node(
            cid, "contact", label,
            {
                "owner": str(owner) if owner else None,
                "source": str(source).strip().lower() if source else None,
                "lifecyclestage": str(stage).strip().lower() if stage else None,
                "last_touch": touched.isoformat(timespec="seconds") if touched else None,
                "sla_breach": bool(breaches),
            },
        )

        key, display = _account_key(contact)
        if key:
            account_contacts.setdefault(key, []).append(cid)
            account_names.setdefault(key, Counter())[display] += 1
            if touched and (key not in account_touch or touched > account_touch[key]):
                account_touch[key] = touched
            edges.append(Edge(cid, f"company:{key}", "belongs_to"))
        else:
            without_account += 1

        if owner:
            oid = f"owner:{owner}"
            nodes.setdefault(oid, Node(oid, "owner", str(owner)))
            edges.append(Edge(oid, cid, "owns"))
        if source:
            sid = f"source:{str(source).strip().lower()}"
            nodes.setdefault(sid, Node(sid, "source", str(source).strip().lower()))
            edges.append(Edge(cid, sid, "sourced_by"))

    for key, names in account_names.items():
        nodes[f"company:{key}"] = Node(f"company:{key}", "company", names.most_common(1)[0][0], {"key": key})

    # deal -> account resolution table: by domain, then by normalized name
    by_domain = {key: key for key in account_contacts if not key.startswith("name:")}
    by_name: dict[str, str] = {}
    for key, names in account_names.items():
        if key.startswith("name:"):
            by_name[key[len("name:"):]] = key
        for name in names:
            normalized = audit_engine._normalize_company(name)
            if normalized:
                by_name.setdefault(normalized, key)

    account_deals: dict[str, list[str]] = {}
    unlinked = 0
    for j, deal in enumerate(deals):
        raw_id = deal.get("id")
        did = f"deal:{raw_id}" if raw_id not in (None, "") else f"deal:#{j}"  # an id of 0 is a real id
        stage, status = _deal_status(deal)
        amount = funnel_engine.parse_amount(deal.get("amount")) or 0.0
        nodes[did] = Node(
            did, "deal", str(deal.get("dealname") or did),
            {"stage": stage, "status": status, "amount": amount},
        )
        _, deal_owner = cortex_engine._first_present_value(deal, DEAL_OWNER_ALIASES)
        if deal_owner:
            oid = f"owner:{deal_owner}"
            nodes.setdefault(oid, Node(oid, "owner", str(deal_owner)))
            edges.append(Edge(oid, did, "owns"))

        _, assoc = cortex_engine._first_present_value(deal, DEAL_COMPANY_ALIASES)
        key = None
        if assoc:
            wanted = str(assoc).strip().lower()
            key = (
                by_domain.get(wanted)
                or by_domain.get(_email_domain(wanted) or "")
                or by_name.get(audit_engine._normalize_company(wanted))
            )
        if key:
            account_deals.setdefault(key, []).append(did)
            edges.append(Edge(did, f"company:{key}", "for_account"))
        else:
            unlinked += 1

    accounts: list[Account] = []
    for key, cids in account_contacts.items():
        cnodes = [nodes[c] for c in cids]
        owners = Counter(n.attrs["owner"] for n in cnodes if n.attrs["owner"])
        stages = Counter(n.attrs["lifecyclestage"] for n in cnodes if n.attrs["lifecyclestage"])
        sources = Counter(n.attrs["source"] for n in cnodes if n.attrs["source"])
        unowned = len(cnodes) - sum(owners.values())
        missing_stage = len(cnodes) - sum(stages.values())
        breaches = sum(1 for n in cnodes if n.attrs["sla_breach"])
        dnodes = [nodes[d] for d in account_deals.get(key, [])]
        open_deals = [n for n in dnodes if n.attrs["status"] == "open"]
        won_deals = [n for n in dnodes if n.attrs["status"] == "won"]
        open_amount = sum(n.attrs["amount"] for n in open_deals)

        flags: list[str] = []
        if unowned:
            flags.append(f"unowned:{unowned}")
        if missing_stage:
            flags.append(f"no-lifecycle:{missing_stage}")
        if len(sources) > 1:
            flags.append("source-conflict")
        if breaches:
            flags.append(f"sla-breach:{breaches}")
        if open_deals and unowned:
            flags.append("open-pipeline-unrouted")

        touched = account_touch.get(key)
        accounts.append(
            Account(
                key=key,
                name=nodes[f"company:{key}"].label,
                contacts=len(cnodes),
                owners=dict(owners.most_common()),
                unowned_contacts=unowned,
                lifecycle_stages=dict(stages.most_common()),
                missing_lifecycle=missing_stage,
                sources=dict(sources.most_common()),
                source_conflict=len(sources) > 1,
                sla_breaches=breaches,
                last_touch=touched.isoformat(timespec="seconds") if touched else None,
                deals=len(dnodes),
                open_deals=len(open_deals),
                open_amount=open_amount,
                won_deals=len(won_deals),
                contact_ids=list(cids),
                deal_ids=[n.id for n in dnodes],
                flags=flags,
            )
        )
    accounts.sort(key=lambda a: (-a.open_amount, -a.contacts, a.key))

    return ContextGraph(
        nodes=list(nodes.values()),
        edges=edges,
        accounts=accounts,
        total_contacts=len(contacts_raw),
        total_deals=len(deals),
        contacts_without_account=without_account,
        unlinked_deals=unlinked,
        sla_hours=sla_hours,
    )


# ---------------------------------------------------------------------------
# describe + render
# ---------------------------------------------------------------------------


def _account_line(a: Account) -> str:
    owners = ", ".join(f"{o} ({n})" for o, n in a.owners.items()) or "no owner"
    pipeline = f"{a.open_deals} open deal(s) ${a.open_amount:,.0f}" if a.deals else "no linked deals"
    flags = ", ".join(a.flags) if a.flags else "clean"
    return f"{a.name} [{a.key}]: {a.contacts} contact(s); owners: {owners}; {pipeline}; flags: {flags}"


def account_detail(a: Account) -> list[str]:
    lines = [f"Account {a.name} [{a.key}]"]
    lines.append(f"  contacts: {a.contacts} ({a.unowned_contacts} unowned, {a.missing_lifecycle} missing lifecyclestage)")
    lines.append(f"  owners: {', '.join(f'{o} ({n})' for o, n in a.owners.items()) or 'none'}")
    lines.append(f"  lifecycle: {', '.join(f'{s} ({n})' for s, n in a.lifecycle_stages.items()) or 'none recorded'}")
    src = ", ".join(f"{s} ({n})" for s, n in a.sources.items()) or "none recorded"
    lines.append(f"  sources: {src}{' - CONFLICT' if a.source_conflict else ''}")
    lines.append(f"  routing: {a.sla_breaches} SLA breach(es); last touch {a.last_touch or 'never recorded'}")
    lines.append(f"  pipeline: {a.deals} linked deal(s), {a.open_deals} open (${a.open_amount:,.0f}), {a.won_deals} won")
    lines.append(f"  flags: {', '.join(a.flags) if a.flags else 'clean'}")
    return lines


def summarize(graph: ContextGraph, account: str | None = None, top: int = 10) -> str:
    kinds = Counter(n.kind for n in graph.nodes)
    lines = [
        f"Context graph: {kinds.get('contact', 0)} contacts, {kinds.get('company', 0)} accounts, "
        f"{kinds.get('owner', 0)} owners, {kinds.get('source', 0)} sources, {kinds.get('deal', 0)} deals; "
        f"{len(graph.edges)} edges.",
        f"{graph.contacts_without_account} contact(s) carry no corporate domain or company name (no account); "
        f"{graph.unlinked_deals} deal(s) have no explicit account association (left unlinked, not guessed).",
    ]
    if account:
        found = graph.account(account)
        if found is None:
            lines.append(f"Account '{account}' not found in this export.")
        else:
            lines.extend(account_detail(found))
        return "\n".join(lines)
    lines.append(f"Top {min(top, len(graph.accounts))} accounts (open pipeline first):")
    for a in graph.accounts[:top]:
        lines.append(f"  - {_account_line(a)}")
    flagged = sum(1 for a in graph.accounts if a.flags)
    lines.append(f"{flagged}/{len(graph.accounts)} accounts carry at least one flag.")
    return "\n".join(lines)


def render(graph: ContextGraph, out_dir: str, top: int = 10) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_contacts": graph.total_contacts,
        "total_deals": graph.total_deals,
        "contacts_without_account": graph.contacts_without_account,
        "unlinked_deals": graph.unlinked_deals,
        "sla_hours": graph.sla_hours,
        "accounts": [asdict(a) for a in graph.accounts],
        "nodes": [asdict(n) for n in graph.nodes],
        "edges": [asdict(e) for e in graph.edges],
    }
    json_path = out_path / "context-graph.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = ["# Context Graph", "", f"_Generated {payload['generated']}_", ""]
    lines.append(summarize(graph, top=top))
    lines.append("")
    lines.append("## Accounts")
    lines.append("")
    lines.append("| Account | Contacts | Unowned | Open deals | Open $ | SLA breaches | Flags |")
    lines.append("|---|---|---|---|---|---|---|")
    for a in graph.accounts:
        lines.append(
            f"| {a.name} | {a.contacts} | {a.unowned_contacts} | {a.open_deals} | {a.open_amount:,.0f} | "
            f"{a.sla_breaches} | {', '.join(a.flags) or 'clean'} |"
        )
    lines.append("")
    md_path = out_path / "context-graph.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "graph_path": str(json_path),
        "report_path": str(md_path),
        "accounts": len(graph.accounts),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
    }
