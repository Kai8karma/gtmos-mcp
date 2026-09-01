"""gtmos command-line entrypoint. Argparse only, stdlib.

Each subcommand's implementation module is imported lazily inside its
handler function, so `gtmos --help` / `gtmos <cmd> --help` stay fast.
"""

from __future__ import annotations

import argparse

from gtmos import __version__


def _cmd_audit(args: argparse.Namespace) -> int:
    from gtmos.audit.cli_entry import run

    return run(args)


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from gtmos.calibrate.cli_entry import run

    return run(args)


def _cmd_cortex(args: argparse.Namespace) -> int:
    from gtmos.cortex.cli_entry import run

    return run(args)


def _cmd_mcp(args: argparse.Namespace) -> int:
    from gtmos.mcp.cli_entry import run

    return run(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gtmos", description="Read-only CRM audit tools, served over MCP"
    )
    parser.add_argument("--version", action="version", version=f"gtmos {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved plan for the subcommand and execute nothing",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Every subparser also accepts --dry-run so it works in the natural
    # `gtmos <cmd> --dry-run ...` position, not just before the subcommand.
    # default=SUPPRESS means "not given here" leaves the top-level parser's
    # value (set below) untouched instead of clobbering it with False.
    dry_run_kwargs = dict(action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    audit_p = subparsers.add_parser("audit", help="audit HubSpot contacts for data-integrity leaks")
    audit_p.add_argument("--input", metavar="FILE", help="offline contacts JSON (fallback to --portal)")
    audit_p.add_argument("--acv", type=float, help="average contract value, for $-leak estimate")
    audit_p.add_argument("--portal", action="store_true", help="fetch live from HubSpot API")
    audit_p.add_argument("--out", default="./audit-out", help="output directory (default ./audit-out)")
    audit_p.add_argument("--dry-run", **dry_run_kwargs)
    audit_p.set_defaults(func=_cmd_audit)

    cal_p = subparsers.add_parser("calibrate", help="grade the scorer against real closed outcomes")
    cal_p.add_argument("--scores", required=True, metavar="FILE", help="scored accounts JSON")
    cal_p.add_argument("--outcomes", required=True, metavar="FILE", help="real outcomes JSON (won/lost/open)")
    cal_p.add_argument("--out", default="./calibrate-out", help="output directory (default ./calibrate-out)")
    cal_p.add_argument("--dry-run", **dry_run_kwargs)
    cal_p.set_defaults(func=_cmd_calibrate)

    cortex_p = subparsers.add_parser(
        "cortex", help="Marketing Cortex: ops health scorecard (default), --graph, or --proposals"
    )
    cortex_p.add_argument("--input", metavar="FILE", help="offline contacts JSON (fallback to --portal)")
    cortex_p.add_argument("--deals", metavar="FILE", help="offline deals JSON (optional, enables the pipeline check)")
    cortex_p.add_argument("--portal", action="store_true", help="fetch live from HubSpot API")
    cortex_p.add_argument("--sla-hours", dest="sla_hours", type=float, help="routing SLA in hours (default 24)")
    cortex_p.add_argument("--out", default="./cortex-out", help="output directory (default ./cortex-out)")
    cortex_mode = cortex_p.add_mutually_exclusive_group()
    cortex_mode.add_argument("--graph", action="store_true", help="build the context graph and per-account rollup instead of the scorecard")
    cortex_mode.add_argument("--proposals", action="store_true", help="run the governed agents and write the policy-gated proposal queue")
    cortex_p.add_argument("--account", metavar="DOMAIN", help="with --graph: drill into one account (email domain or company name)")
    cortex_p.add_argument("--top", type=int, default=10, help="with --graph: accounts to list (default 10)")
    cortex_p.add_argument("--policy", metavar="FILE", help="with --proposals: policy JSON override")
    cortex_p.add_argument("--stall-days", dest="stall_days", type=int, help="with --proposals: deal stall threshold in days (default 21)")
    cortex_p.add_argument("--dry-run", **dry_run_kwargs)
    cortex_p.set_defaults(func=_cmd_cortex)

    mcp_p = subparsers.add_parser("mcp", help="run the MCP server on stdio (read-only audit tools)")
    mcp_p.add_argument("--config", action="store_true", help="print a ready-to-paste MCP host config block")
    mcp_p.add_argument("--dry-run", **dry_run_kwargs)
    mcp_p.set_defaults(func=_cmd_mcp)

    return parser


def _log_crash(command: str) -> str:
    """Write the full traceback to ~/.gtmos/errors.log; return the path."""
    import time
    import traceback
    from pathlib import Path

    log_path = Path.home() / ".gtmos" / "errors.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(f"\n--- {time.strftime('%Y-%m-%dT%H:%M:%S%z')} gtmos {command} ---\n")
        f.write(traceback.format_exc())
    return str(log_path)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # `mcp --dry-run` prints its own richer plan (the tool list) in cli_entry,
    # so it bypasses the generic interception below.
    if args.dry_run and args.command != "mcp":
        plan = {k: v for k, v in vars(args).items() if k not in ("func", "dry_run")}
        print(f"[dry-run] gtmos {args.command}: {plan}")
        return 0

    # Fail-proof contract: a subcommand bug never dumps a traceback on an
    # operator. One clean line, full trace in the local error log, exit 1.
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print(f"\n{args.command}: interrupted")
        return 130
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the whole point is catching everything
        log_path = _log_crash(args.command)
        print(f"{args.command}: unexpected error: {exc} - full trace: {log_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
