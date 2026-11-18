#!/usr/bin/env python3
"""Fail if the version is not the same everywhere it is written down.

    python scripts/check_versions.py              # the files agree with each other
    python scripts/check_versions.py --tag v0.6.0 # ...and with the release tag

The registry entry points at a specific PyPI version, so a server.json that
names a version PyPI does not have is a broken listing. This is the check
that stops that from being published.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    found["pyproject.toml"] = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

    init = (ROOT / "gtmos" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init, re.MULTILINE)
    found["gtmos/__init__.py"] = match.group(1) if match else "(missing)"

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    found["server.json version"] = server["version"]
    for i, package in enumerate(server.get("packages", [])):
        found[f"server.json packages[{i}].version"] = package["version"]

    plugin = ROOT / ".claude-plugin" / "plugin.json"
    if plugin.exists():
        found[".claude-plugin/plugin.json"] = json.loads(plugin.read_text(encoding="utf-8"))["version"]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag to compare against, e.g. v0.6.0")
    args = parser.parse_args()

    found = collect()
    if args.tag:
        found["git tag"] = args.tag.removeprefix("v")

    width = max(map(len, found))
    for where, version in found.items():
        print(f"{where:<{width}}  {version}")

    if len(set(found.values())) != 1:
        print("\nversions disagree - fix them before releasing", file=sys.stderr)
        return 1
    print("\nversions agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
