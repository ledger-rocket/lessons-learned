"""Utilities for extracting user prompts from transcript JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PromptRecord = dict[str, Any]


def is_actual_user_prompt(content: str) -> bool:
    """Determine if content is an actual user prompt vs system noise.

    Returns:
        ``True`` when the line represents a real user prompt.

    """
    content = content.strip()

    if content.startswith("[Request interrupted"):
        return False
    if content.startswith("<user-prompt-submit-hook>"):
        return False
    if content.startswith("<ide_opened_file>"):
        return False
    if "This session is being continued from a previous conversation" in content:
        return False
    return not (not content or content.isspace())


def extract_user_prompts(json_file: Path) -> list[PromptRecord]:
    """Extract clean user prompts from `json_file`.

    Returns:
        A list of `PromptRecord` instances sorted by timestamp.

    """
    with Path(json_file).open("r", encoding="utf-8") as fh:
        messages = cast("list[dict[str, Any]]", json.load(fh))

    prompts: list[PromptRecord] = []
    seen_timestamps: set[str] = set()

    for msg in messages:
        if msg.get("role") != "user":
            continue

        timestamp = msg.get("timestamp")
        if not timestamp or timestamp in seen_timestamps:
            continue

        content = msg.get("content", "")
        if isinstance(content, str) and is_actual_user_prompt(content):
            prompts.append({"timestamp": timestamp, "content": content})
            seen_timestamps.add(timestamp)

    return prompts


def format_prompts_as_text(prompts: list[PromptRecord]) -> str:
    """Format prompt records into a text file layout.

    Returns:
        The text representation ready to be written to disk.

    """
    lines: list[str] = []
    for prompt in prompts:
        lines.extend((f"[{prompt['timestamp']}]", prompt["content"], ""))
    return "\n".join(lines)


def write_prompts_text(prompts: list[PromptRecord], destination: Path) -> None:
    """Write prompts to `destination` in the text format."""
    destination.write_text(format_prompts_as_text(prompts), encoding="utf-8")
