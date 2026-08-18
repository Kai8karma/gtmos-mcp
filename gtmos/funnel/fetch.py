"""Deal sourcing: offline JSON fallback, or live HubSpot REST paging.

No third-party HTTP deps — urllib.request only. The offline path
(load_json) is what makes the engine/report testable without a token.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

HUBSPOT_BASE_URL = "https://api.hubapi.com/crm/v3/objects/deals"

# fixed backoff schedule in seconds, indexed by (0-based) attempt number
RETRY_SCHEDULE = (1, 2, 4)
MAX_RETRY_AFTER_S = 30

HUBSPOT_PROPERTIES = [
    "dealname", "amount", "dealstage", "createdate",
    "hs_lastmodifieddate", "closedate", "hubspot_owner_id",
]


def _flatten(raw: dict) -> dict:
    """Map a HubSpot API deal object ({id, properties: {...}}) to a flat dict."""
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else raw
    flat = {"id": raw.get("id") or props.get("id")}
    for key in ["dealname", "amount", "dealstage", "createdate", "hs_lastmodifieddate", "closedate", "hubspot_owner_id"]:
        flat[key] = props.get(key)
    return flat


def load_json(path: str) -> list[dict]:
    """Load a JSON file of deals (list of dicts, or {"results": [...]})."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of deals (or {{'results': [...]}})")
    return [_flatten(item) if "properties" in item else item for item in data]


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before the next attempt: honors a Retry-After header
    when present (capped at MAX_RETRY_AFTER_S), else falls back to the
    fixed RETRY_SCHEDULE."""
    retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
    if retry_after:
        try:
            return min(float(retry_after), MAX_RETRY_AFTER_S)
        except ValueError:
            pass
    return RETRY_SCHEDULE[min(attempt, len(RETRY_SCHEDULE) - 1)]


def _request_with_retry(url: str, headers: dict, attempts: int = 3, sleeper=time.sleep) -> dict:
    """GET url with headers, retrying transient failures.

    Retries up to `attempts` times on HTTP 429/5xx or network errors
    (urllib.error.URLError), sleeping between attempts per RETRY_SCHEDULE
    (1s/2s/4s) unless the response carries a Retry-After header (capped at
    MAX_RETRY_AFTER_S). 401/403 raise immediately with a message that
    never echoes the token — those are not transient. Any other HTTP
    error, or exhausting all attempts, raises RuntimeError. `sleeper` is
    injectable so tests never actually sleep.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"HubSpot API error {exc.code}: token invalid or missing scope"
                ) from exc
            if exc.code == 429 or 500 <= exc.code < 600:
                last_error = exc
                if attempt < attempts - 1:
                    sleeper(_retry_delay(exc, attempt))
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"HubSpot API error {exc.code} after {attempts} attempts: {body}"
                ) from exc
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HubSpot API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleeper(RETRY_SCHEDULE[min(attempt, len(RETRY_SCHEDULE) - 1)])
                continue
            raise RuntimeError(
                f"HubSpot API unreachable after {attempts} attempts: {exc.reason}"
            ) from exc
    raise RuntimeError(f"HubSpot API request failed after {attempts} attempts") from last_error


def fetch_hubspot_deals(token: str | None) -> list[dict]:
    """Paginate GET /crm/v3/objects/deals and return flattened deal records."""
    if not token:
        raise RuntimeError(
            "GTMOS_HUBSPOT_TOKEN is not set. Export a HubSpot private-app token "
            "or pass --input <file.json> to analyze an offline export instead."
        )

    deals: list[dict] = []
    after: str | None = None

    while True:
        params = {"limit": "100", "properties": ",".join(HUBSPOT_PROPERTIES)}
        if after:
            params["after"] = after
        url = f"{HUBSPOT_BASE_URL}?{urllib.parse.urlencode(params)}"
        payload = _request_with_retry(url, headers={"Authorization": f"Bearer {token}"})

        deals.extend(_flatten(item) for item in payload.get("results", []))

        after = payload.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return deals
