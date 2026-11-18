#!/usr/bin/env python3
"""Drive this server with the official MCP Python SDK client over stdio.

The unit tests check the protocol against our own reading of the spec. This
checks it against someone else's: the reference client connects in each
mode, lists the tools and calls one. Needs the SDK (pip install -e ".[interop]"),
which the package itself never imports.

    python scripts/interop_check.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
CONTACTS = ROOT / "tests" / "fixtures" / "contacts_sample.json"

# mode -> the protocol version the client must end up speaking
EXPECTED = {
    "auto": "2026-07-28",  # probes server/discover, then picks the newest shared version
    "2026-07-28": "2026-07-28",  # no probe, no handshake: straight to a self-describing request
    "legacy": "2025-06-18",  # the initialize handshake older hosts still use
}


async def check(mode: str, expected: str, out_dir: str) -> list[str]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "gtmos.cli", "mcp"], cwd=str(ROOT))
    problems: list[str] = []
    async with Client(params, mode=mode, read_timeout_seconds=30) as client:
        if client.protocol_version != expected:
            problems.append(f"negotiated {client.protocol_version}, expected {expected}")

        tools = (await client.list_tools()).tools
        if len(tools) != 6:
            problems.append(f"listed {len(tools)} tools, expected 6")

        result = await client.call_tool(
            "audit_crm", {"contacts_file": str(CONTACTS), "out_dir": out_dir, "as_of": "2026-08-19"}
        )
        text = result.content[0].text
        if "Portal integrity score: 77.5 (B)" not in text:
            problems.append(f"unexpected audit output: {text.splitlines()[:2]}")
        structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        if not structured or structured.get("total_records") != 25:
            problems.append(f"missing or wrong structured content: {structured}")

        try:
            await client.call_tool("no_such_tool", {})
            problems.append("an unknown tool did not raise a protocol error")
        except Exception:  # noqa: BLE001 - any client-side error type is the pass condition
            pass
    return problems


async def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory() as out_dir:
        for mode, expected in EXPECTED.items():
            try:
                problems = await check(mode, expected, out_dir)
            except BaseException as exc:  # noqa: BLE001 - report it and keep checking the other modes
                problems = [f"{type(exc).__name__}: {exc}"]
            status = "ok" if not problems else "FAIL"
            print(f"[{status}] client mode {mode!r} -> protocol {expected}")
            for problem in problems:
                print(f"       {problem}")
            failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
