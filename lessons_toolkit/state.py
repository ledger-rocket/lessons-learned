"""Typed helpers for state management."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import LessonRunSummary, RunLogEntry, StateSnapshot
from .timestamps import parse_timestamp


def resolve_cutoff(
    explicit_since: str | None,
    state: StateSnapshot,
    start_from: datetime | None,
) -> datetime | None:
    """Determine the cutoff timestamp in priority order.

    Returns:
        The resolved cutoff timestamp, or ``None`` if no cutoff applies.

    Raises:
        SystemExit: If ``explicit_since`` is provided but invalid.

    """
    if explicit_since:
        try:
            return parse_timestamp(explicit_since)
        except ValueError as exc:  # pragma: no cover - validated by argparse before
            msg = f"Invalid --since value: {explicit_since}"
            raise SystemExit(msg) from exc
    if state.last_timestamp:
        return state.last_timestamp
    return start_from


def update_state_with_summary(state: StateSnapshot, summary: LessonRunSummary) -> StateSnapshot:
    """Append a run entry to the state snapshot.

    Returns:
        The updated snapshot reference (mutated in place).

    """
    if not summary.latest_timestamp:
        return state

    if not state.last_timestamp or summary.latest_timestamp > state.last_timestamp:
        state.last_timestamp = summary.latest_timestamp

    state.runs.append(
        RunLogEntry(
            ran_at=datetime.now(timezone.utc),
            latest_timestamp=summary.latest_timestamp,
            prompts=summary.prompts_processed,
            lessons_found=summary.lessons_found,
            total_lessons=summary.total_lessons,
        ),
    )

    return state
