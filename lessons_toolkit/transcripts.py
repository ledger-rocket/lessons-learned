"""Utilities for working with Claude Code session transcripts."""

from __future__ import annotations

import json
from datetime import datetime
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL line, returning ``None`` if invalid.

    Returns:
        The parsed dictionary, or ``None`` on malformed JSON.

    """
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def extract_message_text(content: list[dict[str, Any]]) -> str:
    """Extract and concatenate all text content from a message.

    Returns:
        The joined text payload for the message.

    """
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text")


def extract_messages_from_file(
    filepath: Path,
    *,
    session_id_filter: str | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract all user/assistant messages from a single .jsonl file.

    Returns:
        A list of message dictionaries sorted by appearance order.

    """
    messages: list[dict[str, Any]] = []

    with Path(filepath).open("r", encoding="utf-8") as fh:
        for line in fh:
            entry = parse_jsonl_line(line)
            if not entry:
                continue

            if session_id_filter and entry.get("sessionId") != session_id_filter:
                continue

            message = entry.get("message")
            if not message or message.get("role") not in {"user", "assistant"}:
                continue

            timestamp_str = entry.get("timestamp")
            if not timestamp_str:
                continue

            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            if since and timestamp < since:
                continue

            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            content_list = cast("list[dict[str, Any]]", content)

            text = extract_message_text(content_list)
            if not text.strip():
                continue

            messages.append({
                "timestamp": timestamp,
                "timestamp_str": timestamp_str,
                "role": message.get("role"),
                "model": message.get("model"),
                "content": text,
                "session_id": entry.get("sessionId"),
                "cwd": entry.get("cwd"),
            })

    return messages


def format_as_text(messages: Iterable[dict[str, Any]]) -> str:
    """Format messages as a readable text transcript.

    Returns:
        A newline-separated text representation of the transcript.

    """
    output: list[str] = []

    for msg in messages:
        timestamp = msg["timestamp_str"]
        role = msg["role"].upper()

        if role == "ASSISTANT" and msg.get("model"):
            model_short = msg["model"].split("-")[-1]
            header = f"[{timestamp}] ASSISTANT ({model_short}):"
        else:
            header = f"[{timestamp}] {role}:"

        output.extend((header, msg["content"], ""))

    return "\n".join(output)


def format_as_json(messages: Iterable[dict[str, Any]]) -> str:
    """Format messages as a JSON array.

    Returns:
        A JSON-formatted string containing the simplified messages.

    """
    simplified = [
        {
            "timestamp": msg["timestamp_str"],
            "role": msg["role"],
            "model": msg.get("model"),
            "content": msg["content"],
        }
        for msg in messages
    ]
    return json.dumps(simplified, indent=2, ensure_ascii=False)


def find_session_files(
    base_path: Path | None = None,
    *,
    project_id: str | None = None,
) -> list[Path]:
    """Find all .jsonl session files in Claude Code directory structure.

    Returns:
        A list of discovered session file paths.

    """
    if base_path is None:
        base_path = Path.home() / ".claude" / "projects"

    if not base_path.exists():
        return []

    if project_id:
        project_dir = base_path / project_id
        if project_dir.exists():
            return list(project_dir.glob("*.jsonl"))
        return []

    return list(base_path.glob("*/*.jsonl"))


def collate_messages(
    files: Iterable[Path],
    *,
    session_id: str | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract, combine, and sort messages from multiple session files.

    Returns:
        A list of normalized message dictionaries ordered by timestamp.

    """
    all_messages: list[dict[str, Any]] = []

    for filepath in files:
        if not filepath.exists():
            continue
        all_messages.extend(
            extract_messages_from_file(filepath, session_id_filter=session_id, since=since),
        )

    all_messages.sort(key=itemgetter("timestamp"))
    return all_messages
