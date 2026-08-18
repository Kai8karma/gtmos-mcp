"""CLI entry for `gtmos mcp` - run the MCP server on stdio.

Hosts (Claude Desktop, Claude Code, any MCP client) launch this as a
subprocess and speak JSON-RPC over the pipe. There is nothing to bind and
no port to expose, which is the point: the server lives and dies with the
client's own session, on the client's own machine.

    gtmos mcp                 # serve on stdio (what a host runs)
    gtmos mcp --config        # print a ready-to-paste host config block
    gtmos mcp --dry-run       # list the tools without serving
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from gtmos.mcp import PROTOCOL_VERSION, SERVER_NAME, TOOLS, serve


def _config_block() -> str:
    binary = shutil.which("gtmos") or "gtmos"
    return json.dumps(
        {
            "mcpServers": {
                SERVER_NAME: {
                    "command": binary,
                    "args": ["mcp"],
                    "env": {"GTMOS_HUBSPOT_TOKEN": "<your-hubspot-private-app-token>"},
                }
            }
        },
        indent=2,
    )


def run(args: argparse.Namespace) -> int:
    if getattr(args, "config", False):
        print("# Add to your MCP host config, then restart the host.")
        print("# Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json")
        print(_config_block())
        return 0

    if getattr(args, "dry_run", False):
        print(f"[dry-run] gtmos mcp would serve {len(TOOLS)} tools on stdio (protocol {PROTOCOL_VERSION})")
        for tool in TOOLS:
            print(f"  - {tool['name']}: {tool['description'].split('.')[0]}.")
        return 0

    return serve(sys.stdin, sys.stdout)
