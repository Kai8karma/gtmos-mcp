"""Contact/deal sourcing for the cortex scorecard.

No new HubSpot client and no new offline JSON parser: portal mode delegates
straight to gtmos.audit.fetch.fetch_hubspot / gtmos.funnel.fetch.
fetch_hubspot_deals, so live-portal cortex runs are limited to the same
canonical property set those already request. The one thing cortex adds is
a raw-properties offline loader, because gtmos.audit.fetch's canonical
mapping does not carry the automation/reporting/routing properties
(hs_analytics_source, hs_lastmodifieddate, notes_last_contacted, ...) that a
HubSpot contacts export otherwise includes. Point cortex_scorecard at a
file export, not --portal, for full dimension coverage.
"""

from __future__ import annotations

import json

from gtmos.audit import fetch as audit_fetch
from gtmos.funnel import fetch as funnel_fetch


def load_contacts_raw(path: str) -> list[dict]:
    """Full-property contacts: unwraps a {"results": [...]} export and a
    HubSpot {"properties": {...}} object shape, but keeps every property
    key the export carries, not just gtmos.audit.fetch's canonical subset.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of contacts (or {{'results': [...]}})")
    return [item.get("properties") if isinstance(item.get("properties"), dict) else item for item in data]


def load_deals(path: str) -> list[dict]:
    """Deals for the lifecycle pipeline-divergence check. Delegates entirely
    to gtmos.funnel.fetch."""
    return funnel_fetch.load_json(path)


def fetch_portal_contacts(token: str | None) -> list[dict]:
    """Live HubSpot contacts. Delegates to gtmos.audit.fetch.fetch_hubspot,
    so portal mode only ever sees that function's canonical property set -
    dimensions needing properties outside it read as insufficient data."""
    return audit_fetch.fetch_hubspot(token)


def fetch_portal_deals(token: str | None) -> list[dict]:
    """Live HubSpot deals. Delegates to gtmos.funnel.fetch.fetch_hubspot_deals."""
    return funnel_fetch.fetch_hubspot_deals(token)
