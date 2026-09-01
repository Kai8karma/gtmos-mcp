"""`gtmos cortex` handler. Resolves contacts (and optional deals), then
runs one of three modes: the five-dimension scorecard (default), the
context graph (--graph), or the governed-agent proposal queue
(--proposals)."""

from __future__ import annotations

import argparse
import os

from gtmos.cortex import engine, fetch, report


def run(args: argparse.Namespace) -> int:
    if args.input:
        try:
            contacts_raw = fetch.load_contacts_raw(args.input)
        except (OSError, ValueError) as exc:
            print(f"cortex: {exc}")
            return 1
    elif args.portal:
        try:
            contacts_raw = fetch.fetch_portal_contacts(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError as exc:
            print(f"cortex: {exc}")
            return 1
    else:
        print("cortex: pass --input <file.json> or --portal (requires GTMOS_HUBSPOT_TOKEN)")
        return 2

    if not contacts_raw:
        print("cortex: no contacts found in source")
        return 1

    deals: list[dict] = []
    if args.deals:
        try:
            deals = fetch.load_deals(args.deals)
        except (OSError, ValueError) as exc:
            print(f"cortex: {exc}")
            return 1
    elif args.portal:
        try:
            deals = fetch.fetch_portal_deals(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError:
            deals = []  # deals are optional; a missing scope just drops the lifecycle pipeline check

    sla_hours = args.sla_hours if args.sla_hours is not None else engine.DEFAULT_SLA_HOURS

    if getattr(args, "graph", False):
        from gtmos.cortex import graph as cortex_graph

        built = cortex_graph.build_graph(contacts_raw, deals, sla_hours=sla_hours)
        summary = cortex_graph.render(built, args.out, top=args.top)
        print(cortex_graph.summarize(built, account=args.account, top=args.top))
        print(f"cortex: graph written to {summary['graph_path']}")
        return 0

    if getattr(args, "proposals", False):
        from gtmos.cortex import govern
        from gtmos.funnel import engine as funnel_engine

        try:
            policy = govern.load_policy(args.policy) if args.policy else govern.Policy()
        except (OSError, ValueError) as exc:
            print(f"cortex: {exc}")
            return 1
        stall_days = args.stall_days if args.stall_days is not None else funnel_engine.DEFAULT_STALL_DAYS
        governed = govern.run_engine(contacts_raw, deals, policy=policy, sla_hours=sla_hours, stall_days=stall_days)
        summary = govern.render(governed, args.out)
        print(govern.summarize(governed))
        print(f"cortex: proposals written to {summary['report_path']}")
        return 0

    result = engine.run_engine(contacts_raw, deals, sla_hours=sla_hours)
    summary = report.render(result, args.out)

    print(
        f"cortex: {result.total_contacts} contacts / {result.total_deals} deals scored - "
        f"composite {summary['composite_score']:.1f} ({summary['grade']}) - "
        f"report: {summary['report_path']}"
    )
    return 0
