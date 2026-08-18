"""`gtmos audit` handler. Resolves a contact source, scores it, writes the report."""

from __future__ import annotations

import argparse
import os

from gtmos.audit import engine, fetch, report


def run(args: argparse.Namespace) -> int:
    if args.input:
        try:
            contacts = fetch.load_json(args.input)
        except (OSError, ValueError) as exc:
            print(f"audit: {exc}")
            return 1
    elif args.portal:
        try:
            contacts = fetch.fetch_hubspot(os.environ.get("GTMOS_HUBSPOT_TOKEN"))
        except RuntimeError as exc:
            print(f"audit: {exc}")
            return 1
    else:
        print("audit: pass --input <file.json> or --portal (requires GTMOS_HUBSPOT_TOKEN)")
        return 2

    if not contacts:
        print("audit: no contacts found in source")
        return 1

    result = engine.run_engine(contacts)
    acv = args.acv if args.acv is not None else 0.0
    summary = report.render(result, acv, args.out)

    print(
        f"audit: {result.total_records} records scored — "
        f"portal score {summary['portal_score']:.1f} ({summary['grade']}) — "
        f"report: {summary['report_path']}"
    )
    return 0
