#!/usr/bin/env python3
"""
Extract readable chat transcripts from Claude Code session logs (.jsonl files).

Usage:
    python extract_claude_sessions.py [OPTIONS]

Options:
    --sessions-dir PATH    Path to sessions directory (default: ~/.claude/projects/**/sessions/)
    --output-format TEXT   Output format: 'text' or 'json' (default: text)
    --output-file PATH     Write output to file instead of stdout
    --session-id UUID      Filter by specific session ID
    --project-id ID        Filter by specific project ID
    --since TIMESTAMP      Only include messages after this timestamp (ISO-8601)
    --combine              Combine all sessions into single transcript (default: separate files)
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import argparse


def parse_jsonl_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single JSONL line, returning None if invalid."""
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def extract_message_text(content: List[Dict[str, Any]]) -> str:
    """Extract and concatenate all text content from a message."""
    text_parts = []
    for item in content:
        if item.get("type") == "text" and "text" in item:
            text_parts.append(item["text"])
    return "\n".join(text_parts)


def extract_messages_from_file(
    filepath: Path,
    session_id_filter: Optional[str] = None,
    since: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Extract all user/assistant messages from a single .jsonl file."""
    messages = []

    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            entry = parse_jsonl_line(line)
            if not entry:
                continue

            # Skip if filtering by session ID
            if session_id_filter and entry.get("sessionId") != session_id_filter:
                continue

            # Skip if no message object
            message = entry.get("message")
            if not message:
                continue

            # Skip if not user or assistant role
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue

            # Parse timestamp
            timestamp_str = entry.get("timestamp")
            if not timestamp_str:
                continue

            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                continue

            # Skip if before 'since' filter
            if since and timestamp < since:
                continue

            # Extract text content
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            text = extract_message_text(content)
            if not text.strip():
                continue

            messages.append({
                "timestamp": timestamp,
                "timestamp_str": timestamp_str,
                "role": role,
                "model": message.get("model"),
                "content": text,
                "session_id": entry.get("sessionId"),
                "cwd": entry.get("cwd"),
            })

    return messages


def format_as_text(messages: List[Dict[str, Any]]) -> str:
    """Format messages as readable text transcript."""
    output = []

    for msg in messages:
        timestamp = msg["timestamp_str"]
        role = msg["role"].upper()

        if role == "ASSISTANT" and msg.get("model"):
            model_short = msg["model"].split("-")[-1] if msg["model"] else ""
            header = f"[{timestamp}] ASSISTANT ({model_short}):"
        else:
            header = f"[{timestamp}] {role}:"

        output.append(header)
        output.append(msg["content"])
        output.append("")  # Blank line between messages

    return "\n".join(output)


def format_as_json(messages: List[Dict[str, Any]]) -> str:
    """Format messages as JSON array."""
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
    base_path: Optional[Path] = None,
    project_id: Optional[str] = None
) -> List[Path]:
    """Find all .jsonl session files in Claude Code directory structure."""
    if base_path is None:
        base_path = Path.home() / ".claude" / "projects"

    if not base_path.exists():
        return []

    if project_id:
        # Search specific project
        project_dir = base_path / project_id
        if project_dir.exists():
            return list(project_dir.glob("*.jsonl"))
        return []

    # Search all projects - .jsonl files are directly in project directories
    return list(base_path.glob("*/*.jsonl"))


def main():
    parser = argparse.ArgumentParser(
        description="Extract readable chat transcripts from Claude Code session logs"
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to sessions directory (default: ~/.claude/projects/)"
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Write output to file instead of stdout"
    )
    parser.add_argument(
        "--session-id",
        help="Filter by specific session ID (UUID)"
    )
    parser.add_argument(
        "--project-id",
        help="Filter by specific project ID"
    )
    parser.add_argument(
        "--since",
        help="Only include messages after this timestamp (ISO-8601)"
    )
    parser.add_argument(
        "--combine",
        action="store_true",
        help="Combine all sessions into single transcript"
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Specific .jsonl files to process (overrides --sessions-dir)"
    )

    args = parser.parse_args()

    # Parse 'since' filter
    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
        except ValueError:
            print(f"Error: Invalid timestamp format: {args.since}", file=sys.stderr)
            sys.exit(1)

    # Find files to process
    if args.files:
        files = args.files
    else:
        files = find_session_files(args.sessions_dir, args.project_id)

    if not files:
        print("No session files found", file=sys.stderr)
        sys.exit(1)

    # Extract messages from all files
    all_messages = []
    for filepath in files:
        if not filepath.exists():
            print(f"Warning: File not found: {filepath}", file=sys.stderr)
            continue

        messages = extract_messages_from_file(filepath, args.session_id, since)
        all_messages.extend(messages)

    if not all_messages:
        print("No messages found matching criteria", file=sys.stderr)
        sys.exit(1)

    # Sort by timestamp
    all_messages.sort(key=lambda m: m["timestamp"])

    # Format output
    if args.output_format == "json":
        output = format_as_json(all_messages)
    else:
        output = format_as_text(all_messages)

    # Write output
    if args.output_file:
        args.output_file.write_text(output, encoding="utf-8")
        print(f"Transcript written to {args.output_file}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
