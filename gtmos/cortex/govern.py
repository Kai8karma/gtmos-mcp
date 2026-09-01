"""Governed agents - the Cortex proposes, a human applies.

Six deterministic agents read the context graph, the audit/funnel engines,
and the same property facts the signals use. Each emits proposals: one
concrete change, the records it would touch, the evidence for it, and a
confidence. A policy then gates every proposal before it is shown - action
allow-list, blast radius, confidence floor - and anything that fails stays
in the output as BLOCKED with the reason, so governance is visible rather
than silent.

    router               unrouted contacts -> the account's dominant owner
    deduper              duplicate clusters -> merge into the most complete record
    lifecycle_steward    missing lifecyclestage -> stage backed by deal/touch evidence
    attribution_steward  conflicting / missing source at a domain -> the domain majority
    pipeline_steward     stalled deals -> owner review, with days idle and amount
    schema_steward       never-populated properties -> retire

Nothing in this module executes a proposal. There is no write path to any
CRM here and none is planned: the output is a queue for a human (or a
separately authorized system) to apply. requires_human is not a setting
that can be turned off - load_policy() rejects any attempt.
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
from gtmos.cortex import graph as cortex_graph
from gtmos.funnel import engine as funnel_engine

ACTIONS = (
    "assign_owner",
    "merge_duplicates",
    "set_lifecycle_stage",
    "set_attribution_source",
    "review_stalled_deal",
    "retire_property",
)

# A proposal touching fewer records than this is never blocked on share
# alone: in a 4-record export every real change is "most of the data".
SMALL_SAMPLE_FLOOR = 5

_SEVERITY_ORDER = {"CRITICAL": 0, "SERIOUS": 1, "WATCH": 2}
_SEQUENCE_FOR = {"CRITICAL": "week_1", "SERIOUS": "weeks_2_4", "WATCH": "quarter"}
_POLICY_KEYS = {"name", "allowed_actions", "max_blast_records", "max_blast_share", "min_confidence", "requires_human"}


@dataclass
class Policy:
    name: str = "default"
    allowed_actions: list[str] = field(default_factory=lambda: list(ACTIONS))
    max_blast_records: int = 25  # a single proposal may touch at most this many records
    max_blast_share: float = 0.25  # ... and at most this share of its universe (contacts / deals / properties)
    min_confidence: float = 0.5  # below this a proposal needs a human decision, not an agent one
    requires_human: bool = True  # always True; kept explicit so it prints in every report

    def describe(self) -> str:
        return (
            f"policy {self.name}: max {self.max_blast_records} records or {self.max_blast_share:.0%} of universe "
            f"per proposal, confidence floor {self.min_confidence:.2f}, human-applied"
        )


def load_policy(path: str) -> Policy:
    """Read a policy override from JSON. Unknown keys are an error, not a
    silent no-op, and requires_human cannot be disabled."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: policy must be a JSON object")
    unknown = sorted(set(data) - _POLICY_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown policy key(s): {', '.join(unknown)}")
    if data.get("requires_human", True) is not True:
        raise ValueError(f"{path}: requires_human cannot be disabled - proposals are never auto-applied")
    actions = data.get("allowed_actions")
    if actions is not None:
        bad = sorted(set(actions) - set(ACTIONS))
        if bad:
            raise ValueError(f"{path}: unknown action(s) in allowed_actions: {', '.join(bad)}")
    policy = Policy(
        name=str(data.get("name", "custom")),
        allowed_actions=list(actions) if actions is not None else list(ACTIONS),
        max_blast_records=int(data.get("max_blast_records", Policy.max_blast_records)),
        max_blast_share=float(data.get("max_blast_share", Policy.max_blast_share)),
        min_confidence=float(data.get("min_confidence", Policy.min_confidence)),
    )
    return policy


@dataclass
class Proposal:
    id: str  # assigned at govern time, P-001 ...
    agent: str
    action: str
    severity: str  # CRITICAL | SERIOUS | WATCH
    targets: list[str]
    before: str
    after: str
    evidence: list[str]
    confidence: float
    blast_radius: int
    blast_share: float
    universe: str  # contacts | deals | properties
    sequence: str = ""
    policy: str = ""
    status: str = "DRAFT"  # DRAFT -> PROPOSED | BLOCKED
    blocked_reason: str | None = None


@dataclass
class GovernResult:
    proposals: list[Proposal]  # every proposal, PROPOSED and BLOCKED, severity-ordered
    policy: Policy
    total_contacts: int
    total_deals: int
    agents_run: list[str]

    @property
    def proposed(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "PROPOSED"]

    @property
    def blocked(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "BLOCKED"]


def _draft(agent: str, action: str, severity: str, targets: list[str], before: str, after: str,
           evidence: list[str], confidence: float, universe: str, universe_size: int) -> Proposal:
    return Proposal(
        id="",
        agent=agent,
        action=action,
        severity=severity,
        targets=targets,
        before=before,
        after=after,
        evidence=evidence,
        confidence=round(confidence, 2),
        blast_radius=len(targets),
        blast_share=(len(targets) / universe_size) if universe_size else 0.0,
        universe=universe,
    )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


def _agent_router(graph: cortex_graph.ContextGraph) -> list[Proposal]:
    drafts: list[Proposal] = []
    labels = {n.id: n.label for n in graph.nodes if n.kind == "contact"}
    owner_of = {n.id: n.attrs.get("owner") for n in graph.nodes if n.kind == "contact"}
    orphans: list[str] = []
    for account in graph.accounts:
        if not account.unowned_contacts:
            continue
        targets = [labels[c] for c in account.contact_ids if not owner_of.get(c)]
        if account.owners:
            owner, held = next(iter(account.owners.items()))
            owned_total = sum(account.owners.values())
            confidence = held / owned_total
            severity = "CRITICAL" if account.open_deals else ("SERIOUS" if len(targets) >= 3 else "WATCH")
            evidence = [
                f"{held}/{owned_total} owned contact(s) at {account.name} belong to {owner}",
                f"{account.unowned_contacts}/{account.contacts} contact(s) at this account have no owner",
            ]
            if account.open_deals:
                evidence.append(f"{account.open_deals} open deal(s) worth ${account.open_amount:,.0f} sit on this account unrouted")
            drafts.append(_draft(
                "router", "assign_owner", severity, targets,
                before="owner = (none)", after=f"owner = {owner}",
                evidence=evidence, confidence=confidence, universe="contacts", universe_size=graph.total_contacts,
            ))
        else:
            orphans.extend(targets)
    if orphans:
        drafts.append(_draft(
            "router", "assign_owner", "SERIOUS" if len(orphans) >= 3 else "WATCH", orphans,
            before="owner = (none)", after="owner = (human pick: no owner precedent at these accounts)",
            evidence=[f"{len(orphans)} unowned contact(s) sit at accounts where no other contact has an owner either"],
            confidence=0.3, universe="contacts", universe_size=graph.total_contacts,
        ))
    return drafts


def _agent_deduper(contacts_raw: list[dict], now: datetime) -> list[Proposal]:
    canonical = [audit_fetch.normalize_contact(c) for c in contacts_raw]
    drafts: list[Proposal] = []
    for cluster in audit_engine.find_dupes(canonical):
        scored = [(audit_engine.score_record(canonical[i], i, now).overall, i) for i in cluster.indices]
        best_score, survivor = max(scored, key=lambda s: (s[0], -s[1]))
        labels = [cortex_engine._record_label(contacts_raw[i]) for i in cluster.indices]
        survivor_label = cortex_engine._record_label(contacts_raw[survivor])
        exact = cluster.kind == "exact_email"
        drafts.append(_draft(
            "deduper", "merge_duplicates", "SERIOUS" if exact else "WATCH", labels,
            before=f"{len(labels)} separate records ({cluster.kind}: {cluster.key})",
            after=f"1 record - merge into #{survivor} {survivor_label} (record score {best_score:.0f}), keep the most complete value per field",
            evidence=[f"{cluster.kind} match on '{cluster.key}'", f"survivor #{survivor} scores {best_score:.0f}, highest in the cluster"],
            confidence=0.9 if exact else 0.6, universe="contacts", universe_size=len(contacts_raw),
        ))
    return drafts


def _agent_lifecycle_steward(graph: cortex_graph.ContextGraph, contacts_raw: list[dict]) -> list[Proposal]:
    if not cortex_engine._key_ever_present(contacts_raw, cortex_engine.LIFECYCLE_ALIASES):
        return []
    drafts: list[Proposal] = []
    contact_nodes = {n.id: n for n in graph.nodes if n.kind == "contact"}
    unevidenced: list[str] = []
    for account in graph.accounts:
        missing = [contact_nodes[c] for c in account.contact_ids if not contact_nodes[c].attrs.get("lifecyclestage")]
        if not missing:
            continue
        targets = [n.label for n in missing]
        if account.won_deals:
            stage, confidence = "customer", 0.8
            evidence = [f"{account.won_deals} won deal(s) linked to {account.name}"]
        elif account.open_deals:
            stage, confidence = "opportunity", 0.7
            evidence = [f"{account.open_deals} open deal(s) worth ${account.open_amount:,.0f} linked to {account.name}"]
        else:
            touched = [n for n in missing if n.attrs.get("last_touch")]
            if touched:
                stage, confidence = "lead", 0.5
                evidence = [f"{len(touched)}/{len(missing)} of these contacts carry a recorded touch"]
            else:
                unevidenced.extend(targets)
                continue
        drafts.append(_draft(
            "lifecycle_steward", "set_lifecycle_stage", "SERIOUS" if account.open_deals or account.won_deals else "WATCH",
            targets, before="lifecyclestage = (none)", after=f"lifecyclestage = {stage}",
            evidence=evidence + [f"{len(targets)}/{account.contacts} contact(s) at {account.name} are missing lifecyclestage"],
            confidence=confidence, universe="contacts", universe_size=graph.total_contacts,
        ))
    # contacts with no account at all, or at accounts with no deal/touch evidence
    no_account_missing = [
        n.label for n in contact_nodes.values()
        if not n.attrs.get("lifecyclestage") and not any(n.id in a.contact_ids for a in graph.accounts)
    ]
    unevidenced.extend(no_account_missing)
    if unevidenced:
        drafts.append(_draft(
            "lifecycle_steward", "set_lifecycle_stage", "WATCH", unevidenced,
            before="lifecyclestage = (none)", after="lifecyclestage = (human pick: no deal or touch evidence)",
            evidence=[f"{len(unevidenced)} contact(s) missing lifecyclestage with no linked deal and no recorded touch"],
            confidence=0.3, universe="contacts", universe_size=graph.total_contacts,
        ))
    return drafts


def _agent_attribution_steward(graph: cortex_graph.ContextGraph, contacts_raw: list[dict]) -> list[Proposal]:
    if not cortex_engine._key_ever_present(contacts_raw, cortex_engine.SOURCE_ALIASES):
        return []
    drafts: list[Proposal] = []
    contact_nodes = {n.id: n for n in graph.nodes if n.kind == "contact"}
    for account in graph.accounts:
        nodes = [contact_nodes[c] for c in account.contact_ids]
        sourced = [n for n in nodes if n.attrs.get("source")]
        if len(sourced) < 2:
            continue
        counts = Counter(n.attrs["source"] for n in sourced)
        (majority, held), *rest = counts.most_common()
        tie = bool(rest) and rest[0][1] == held
        if account.source_conflict:
            minority = [n.label for n in sourced if n.attrs["source"] != majority]
            drafts.append(_draft(
                "attribution_steward", "set_attribution_source", "SERIOUS", minority,
                before=f"sources at {account.name}: " + ", ".join(f"{s} ({c})" for s, c in counts.most_common()),
                after=f"source = {majority}" if not tie else "source = (human pick: sources tie at this account)",
                evidence=[f"{held}/{len(sourced)} sourced contact(s) at {account.name} carry {majority}"],
                confidence=0.4 if tie else held / len(sourced), universe="contacts", universe_size=graph.total_contacts,
            ))
        unsourced = [n.label for n in nodes if not n.attrs.get("source")]
        if unsourced and not account.source_conflict:
            drafts.append(_draft(
                "attribution_steward", "set_attribution_source", "WATCH", unsourced,
                before="source = (none)", after=f"source = {majority}",
                evidence=[f"all {len(sourced)} sourced contact(s) at {account.name} carry {majority}"],
                confidence=0.7, universe="contacts", universe_size=graph.total_contacts,
            ))
    return drafts


def _agent_pipeline_steward(deals: list[dict], stall_days: int, now: datetime) -> list[Proposal]:
    if not deals:
        return []
    result = funnel_engine.run_engine(deals, stall_days=stall_days, now=now.timestamp())
    drafts: list[Proposal] = []
    for deal in result.stalled_deals:
        label = f"{deal.dealname or deal.id or '(unnamed deal)'}"
        severity = "CRITICAL" if deal.days_stale >= 2 * stall_days else "SERIOUS"
        drafts.append(_draft(
            "pipeline_steward", "review_stalled_deal", severity, [label],
            before=f"stage {deal.dealstage}, {deal.days_stale:.0f}d idle, ${deal.amount:,.0f}, owner {deal.owner}",
            after=f"owner {deal.owner} logs a next step or moves the deal to closedlost",
            evidence=[f"no activity for {deal.days_stale:.0f} days against a {stall_days}d stall threshold"],
            confidence=0.8, universe="deals", universe_size=len(deals),
        ))
    return drafts


def _agent_schema_steward(contacts_raw: list[dict]) -> list[Proposal]:
    universe, never_populated = cortex_engine._never_populated_properties(contacts_raw)
    if not never_populated:
        return []
    return [_draft(
        "schema_steward", "retire_property", "WATCH", never_populated,
        before=f"{len(never_populated)} exported properties present but never populated",
        after="retire the property, or attach the workflow that was supposed to fill it",
        evidence=[f"never holds a non-junk value on any of {len(contacts_raw)} records: {', '.join(never_populated[:5])}"],
        confidence=0.6, universe="properties", universe_size=len(universe),
    )]


# ---------------------------------------------------------------------------
# governance
# ---------------------------------------------------------------------------


def _govern(drafts: list[Proposal], policy: Policy) -> list[Proposal]:
    ordered = sorted(drafts, key=lambda p: (_SEVERITY_ORDER[p.severity], p.agent, p.targets))
    for n, p in enumerate(ordered, start=1):
        p.id = f"P-{n:03d}"
        p.policy = policy.name
        p.sequence = _SEQUENCE_FOR[p.severity]
        if p.action not in policy.allowed_actions:
            p.status, p.blocked_reason = "BLOCKED", f"action {p.action} is not in policy {policy.name}'s allow-list"
        elif p.blast_radius > policy.max_blast_records:
            p.status, p.blocked_reason = "BLOCKED", (
                f"touches {p.blast_radius} {p.universe}, above the {policy.max_blast_records}-record cap - split the change"
            )
        elif p.blast_radius >= SMALL_SAMPLE_FLOOR and p.blast_share > policy.max_blast_share:
            p.status, p.blocked_reason = "BLOCKED", (
                f"touches {p.blast_share:.0%} of {p.universe}, above the {policy.max_blast_share:.0%} cap - split the change"
            )
        elif p.confidence < policy.min_confidence:
            p.status, p.blocked_reason = "BLOCKED", (
                f"confidence {p.confidence:.2f} is below the {policy.min_confidence:.2f} floor - needs a human decision"
            )
        else:
            p.status, p.blocked_reason = "PROPOSED", None
    return ordered


def run_engine(
    contacts_raw: list[dict],
    deals: list[dict] | None = None,
    policy: Policy | None = None,
    sla_hours: float = cortex_engine.DEFAULT_SLA_HOURS,
    stall_days: int = funnel_engine.DEFAULT_STALL_DAYS,
    now: datetime | None = None,
    graph: cortex_graph.ContextGraph | None = None,
) -> GovernResult:
    now = now or datetime.now(timezone.utc)
    deals = deals or []
    policy = policy or Policy()
    graph = graph or cortex_graph.build_graph(contacts_raw, deals, sla_hours=sla_hours, now=now)

    drafts: list[Proposal] = []
    drafts += _agent_router(graph)
    drafts += _agent_deduper(contacts_raw, now)
    drafts += _agent_lifecycle_steward(graph, contacts_raw)
    drafts += _agent_attribution_steward(graph, contacts_raw)
    drafts += _agent_pipeline_steward(deals, stall_days, now)
    drafts += _agent_schema_steward(contacts_raw)

    return GovernResult(
        proposals=_govern(drafts, policy),
        policy=policy,
        total_contacts=len(contacts_raw),
        total_deals=len(deals),
        agents_run=["router", "deduper", "lifecycle_steward", "attribution_steward", "pipeline_steward", "schema_steward"],
    )


# ---------------------------------------------------------------------------
# describe + render
# ---------------------------------------------------------------------------


def _targets_str(p: Proposal, limit: int = 3) -> str:
    shown = ", ".join(p.targets[:limit])
    more = len(p.targets) - limit
    return shown + (f" (+{more} more)" if more > 0 else "")


def summarize(result: GovernResult, top: int = 5) -> str:
    proposed, blocked = result.proposed, result.blocked
    lines = [
        f"{len(result.proposals)} proposal(s) from {len(result.agents_run)} agents on {result.total_contacts} contacts "
        f"/ {result.total_deals} deals: {len(proposed)} PROPOSED, {len(blocked)} BLOCKED.",
        result.policy.describe() + ". Nothing is applied by this tool.",
    ]
    if proposed:
        lines.append(f"Top {min(top, len(proposed))} proposed:")
        for p in proposed[:top]:
            lines.append(f"  - [{p.severity}] {p.id} {p.agent}/{p.action}: {p.after} - {_targets_str(p)} (conf {p.confidence:.2f})")
    if blocked:
        lines.append("Blocked (kept visible):")
        for p in blocked[:top]:
            lines.append(f"  - {p.id} {p.agent}/{p.action} on {p.blast_radius} {p.universe}: {p.blocked_reason}")
    return "\n".join(lines)


def render(result: GovernResult, out_dir: str) -> dict:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines = ["# Cortex Proposals", "", f"_Generated {generated}_", ""]
    lines.append(summarize(result))
    lines.append("")
    lines.append("## Proposed")
    lines.append("")
    lines.append("| ID | Sev | Agent | Action | Targets | Before | After | Conf | Blast |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for p in result.proposed:
        lines.append(
            f"| {p.id} | {p.severity} | {p.agent} | {p.action} | {_targets_str(p)} | {p.before} | {p.after} | "
            f"{p.confidence:.2f} | {p.blast_radius} {p.universe} ({p.blast_share:.0%}) |"
        )
    lines.append("")
    lines.append("## Blocked by policy")
    lines.append("")
    if not result.blocked:
        lines.append("None.")
    else:
        lines.append("| ID | Agent | Action | Targets | Reason |")
        lines.append("|---|---|---|---|---|")
        for p in result.blocked:
            lines.append(f"| {p.id} | {p.agent} | {p.action} | {_targets_str(p)} | {p.blocked_reason} |")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    for p in result.proposals:
        lines.append(f"### {p.id} [{p.status}] {p.agent}/{p.action}")
        lines.append("")
        for line in p.evidence:
            lines.append(f"- {line}")
        lines.append("")
    md_path = out_path / "proposals.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    payload = {
        "generated": generated,
        "policy": asdict(result.policy),
        "total_contacts": result.total_contacts,
        "total_deals": result.total_deals,
        "agents_run": result.agents_run,
        "proposed": len(result.proposed),
        "blocked": len(result.blocked),
        "proposals": [asdict(p) for p in result.proposals],
    }
    json_path = out_path / "proposals.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "report_path": str(md_path),
        "proposals_path": str(json_path),
        "proposed": len(result.proposed),
        "blocked": len(result.blocked),
    }
