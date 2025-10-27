"""Timestamp helpers for ISO-8601 parsing and formatting."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string (tolerating trailing 'Z').

    Returns:
        A timezone-aware ``datetime`` instance.

    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def now_utc() -> str:
    """Return current UTC time in ISO-8601 format without microseconds.

    Returns:
        An ISO-8601 timestamp string.

    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
