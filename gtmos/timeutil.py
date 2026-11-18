"""Shared date handling for the CLI and the MCP tools."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_as_of(raw: object) -> datetime | None:
    """Parse an as-of value into an aware datetime. None or "" means "now",
    which callers express by passing None on to the engines. A date with no
    time is midnight; a datetime with no zone is read as UTC."""
    if raw is None or raw == "":
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"as_of must be an ISO 8601 date or datetime, got {raw!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
