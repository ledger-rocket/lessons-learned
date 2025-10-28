"""Tests for prompt extraction utilities."""

from __future__ import annotations

from lessons_toolkit.prompts import extract_user_prompts_from_messages


def test_extract_user_prompts_from_messages_filters_noise() -> None:
    """Ensure only distinct, meaningful user prompts are returned."""
    messages = [
        {
            "role": "user",
            "timestamp_str": "2025-01-01T10:00:00Z",
            "content": "Investigate the ledger import failures",
        },
        {
            "role": "assistant",
            "timestamp_str": "2025-01-01T10:00:05Z",
            "content": "Acknowledged",
        },
        {
            "role": "user",
            "timestamp_str": "2025-01-01T10:01:00Z",
            "content": "<ide_opened_file> logs/server.log",
        },
        {
            "role": "user",
            "timestamp_str": "2025-01-01T10:00:00Z",
            "content": "Duplicate timestamp should be deduped",
        },
    ]

    prompts = extract_user_prompts_from_messages(messages)

    assert len(prompts) == 1
    assert prompts[0]["timestamp"] == "2025-01-01T10:00:00Z"
    assert prompts[0]["content"] == "Investigate the ledger import failures"
