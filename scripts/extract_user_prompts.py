#!/usr/bin/env python3
"""
Extract only user prompts from Claude Code session transcripts.

Filters out:
- System messages and hooks
- Interruptions
- IDE event notifications
- Duplicate timestamps

Output: Clean list of user prompts with timestamps.
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any


def is_actual_user_prompt(content: str) -> bool:
    """Determine if content is an actual user prompt vs system noise."""
    content = content.strip()

    # Filter out interruptions
    if content.startswith('[Request interrupted'):
        return False

    # Filter out hook messages
    if content.startswith('<user-prompt-submit-hook>'):
        return False

    # Filter out IDE file opened events
    if content.startswith('<ide_opened_file>'):
        return False

    # Filter out session continuation messages
    if 'This session is being continued from a previous conversation' in content:
        return False

    # Filter out empty or whitespace-only
    if not content or content.isspace():
        return False

    return True


def extract_user_prompts(json_file: Path) -> List[Dict[str, Any]]:
    """Extract clean user prompts from transcript JSON."""
    with open(json_file, 'r') as f:
        messages = json.load(f)

    user_prompts = []
    seen_timestamps = set()  # Deduplicate by timestamp

    for msg in messages:
        if msg['role'] != 'user':
            continue

        content = msg['content']
        timestamp = msg['timestamp']

        # Skip duplicates (same timestamp)
        if timestamp in seen_timestamps:
            continue

        # Check if this is an actual user prompt
        if is_actual_user_prompt(content):
            user_prompts.append({
                'timestamp': timestamp,
                'content': content
            })
            seen_timestamps.add(timestamp)

    return user_prompts


def format_as_text(prompts: List[Dict[str, Any]]) -> str:
    """Format prompts as readable text file."""
    lines = []
    for prompt in prompts:
        lines.append(f"[{prompt['timestamp']}]")
        lines.append(prompt['content'])
        lines.append("")  # Blank line separator
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_user_prompts.py <transcript.json> [output.txt]")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    # Extract prompts
    prompts = extract_user_prompts(input_file)

    print(f"Extracted {len(prompts)} user prompts", file=sys.stderr)

    # Format output
    output = format_as_text(prompts)

    # Write or print
    if len(sys.argv) >= 3:
        output_file = Path(sys.argv[2])
        output_file.write_text(output, encoding='utf-8')
        print(f"Written to {output_file}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
