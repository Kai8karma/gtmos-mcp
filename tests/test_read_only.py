"""The read-only claim, enforced: the package may only ever issue GET
requests, and only to HubSpot's API host. If someone adds a write call or a
second host, this fails before it can ship."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "gtmos"
ALLOWED_HOSTS = {"api.hubapi.com"}
# urllib.request.Request(url, data=..., method=...) is the only way this
# package builds a request; either keyword turns a GET into a write.
WRITE_KEYWORDS = {"data", "method"}


def _sources():
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(PACKAGE.rglob("*.py"))]


def test_every_request_is_a_plain_get():
    offenders = []
    for path, source in _sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name.endswith("Request") or name.endswith("urlopen"):
                bad = {kw.arg for kw in node.keywords} & WRITE_KEYWORDS
                if bad or (name.endswith("Request") and len(node.args) > 1):
                    offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno} {name}({', '.join(sorted(bad)) or 'positional body'})")
    assert not offenders, "write-capable HTTP call found:\n" + "\n".join(offenders)


def test_only_the_hubspot_api_host_is_contacted():
    hosts = set()
    for _path, source in _sources():
        hosts.update(re.findall(r"https?://([A-Za-z0-9.-]+)", source))
    assert hosts <= ALLOWED_HOSTS, f"unexpected network host(s): {sorted(hosts - ALLOWED_HOSTS)}"


def test_no_third_party_http_client_is_imported():
    banned = {"requests", "httpx", "aiohttp", "urllib3", "http.client", "socket", "smtplib", "ftplib"}
    offenders = []
    for path, source in _sources():
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n in banned or n.split(".")[0] in banned]
    assert not offenders, f"unexpected network module import(s): {offenders}"
