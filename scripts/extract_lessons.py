#!/usr/bin/env python3
"""
Extract conversation windows around corrections and generate lessons learned.

For each correction identified, extracts context (100 lines before/after),
sends to Claude CLI to analyze, and generates lessons_learned.md.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any


LESSON_EXTRACTION_PROMPT = """You are analyzing a conversation between a user and a coding assistant (Claude Code) where the user corrected or expressed frustration with the assistant.

## Context
The assistant made a mistake, wrong assumption, or used a problematic approach. The user corrected this behavior.

## Your Task
Extract a clear, actionable instruction that should be added to the assistant's context/guidelines to prevent this mistake in the future.

## Conversation Window
{conversation_window}

## The Correction
Timestamp: {timestamp}
User's correction: {user_correction}
Classification: {classification}

## Output Format
Respond with a JSON object (no markdown):
{{
  "reasoning": "Think through what mistake was made and why this lesson matters (2-3 sentences)",
  "lesson_title": "Brief title (5-10 words)",
  "instruction": "Clear instruction to add to guidelines (1-3 sentences)",
  "category": "one of: infrastructure, data_model, authentication, api_usage, build_process, code_patterns",
  "confidence": 0.0-1.0
}}

The reasoning helps you think clearly but won't be included in final output.

## Examples

Example 1:
User: "no they dont. stop guessing and hacking"
Lesson: {{"lesson_title": "Never guess field names or data structures", "instruction": "Always verify actual field names and data structures by reading code or schemas. Never guess or try multiple variations without understanding the actual implementation.", "category": "code_patterns", "confidence": 0.95}}

Example 2:
User: "i told u i already did that check ur code"
Lesson: {{"lesson_title": "Read existing code before implementing", "instruction": "Before implementing new functionality, always read the existing codebase to check if it already exists. Don't assume or re-implement without verification.", "category": "code_patterns", "confidence": 0.9}}
"""


def load_full_transcript(transcript_file: Path) -> List[str]:
    """Load full transcript as lines."""
    with open(transcript_file, 'r') as f:
        return f.readlines()


def find_line_number(transcript_lines: List[str], timestamp: str) -> int:
    """Find the line number where a timestamp appears."""
    for i, line in enumerate(transcript_lines):
        if timestamp in line:
            return i
    return -1


def extract_window(transcript_lines: List[str], line_num: int, window_size: int = 100) -> str:
    """Extract conversation window around a line number."""
    start = max(0, line_num - window_size)
    end = min(len(transcript_lines), line_num + window_size)
    return ''.join(transcript_lines[start:end])


def parse_json_payload(payload: str) -> Any:
    """Best-effort JSON parsing that tolerates markdown fences and leading noise."""
    if not payload:
        return None

    text = payload.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if "```json" in text:
            try:
                fenced = text.split("```json", 1)[1].split("```", 1)[0].strip()
                return json.loads(fenced)
            except (IndexError, json.JSONDecodeError):
                pass

        decoder = json.JSONDecoder()
        for idx, char in enumerate(text):
            if char in ("{", "["):
                try:
                    parsed, _ = decoder.raw_decode(text[idx:])
                    return parsed
                except json.JSONDecodeError:
                    continue

    return None


def extract_lesson(correction: Dict[str, Any], conversation_window: str) -> Dict[str, Any]:
    """Use Claude CLI to extract lesson from correction + context."""
    try:
        # Build prompt
        full_prompt = LESSON_EXTRACTION_PROMPT.format(
            conversation_window=conversation_window,
            timestamp=correction['timestamp'],
            user_correction=correction['prompt'],
            classification=json.dumps(correction['classification'], indent=2)
        )

        # Call Claude CLI
        result = subprocess.run(
            [
                'claude',
                '--print',
                '--allowedTools', '',
                '--dangerously-skip-permissions',
                '--strict-mcp-config',
                '--model', 'claude-haiku-4-5'
            ],
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=30
        )

        lesson = parse_json_payload(result.stdout)
        if not isinstance(lesson, dict):
            raise ValueError("Claude response did not contain a JSON object")

        return {
            "timestamp": correction['timestamp'],
            "user_prompt": correction['prompt'],
            "lesson": lesson,
            "success": True
        }

    except Exception as e:
        return {
            "timestamp": correction['timestamp'],
            "user_prompt": correction['prompt'],
            "error": str(e),
            "success": False
        }


def main():
    # Load corrections
    corrections_file = Path("extracted_knowledge/correction_classifications.json")
    if not corrections_file.exists():
        print("Error: correction_classifications.json not found", file=sys.stderr)
        print("Run classify_corrections.py first", file=sys.stderr)
        sys.exit(1)

    with open(corrections_file, 'r') as f:
        corrections = json.load(f)

    print(f"Loaded {len(corrections)} corrections", file=sys.stderr)

    # Load full transcript
    transcript_file = Path("transcripts/all_sessions.txt")
    if not transcript_file.exists():
        print("Error: all_sessions.txt not found", file=sys.stderr)
        sys.exit(1)

    transcript_lines = load_full_transcript(transcript_file)
    print(f"Loaded transcript with {len(transcript_lines)} lines", file=sys.stderr)

    # Extract lessons for each correction
    lessons = []
    print("\nExtracting lessons from corrections...", file=sys.stderr)

    for i, correction in enumerate(corrections, 1):
        timestamp = correction['timestamp']

        # Find the line in transcript
        line_num = find_line_number(transcript_lines, timestamp)
        if line_num == -1:
            print(f"  [{i}/{len(corrections)}] Warning: timestamp {timestamp} not found in transcript", file=sys.stderr)
            continue

        # Extract conversation window
        window = extract_window(transcript_lines, line_num, window_size=100)

        # Get lesson from Claude
        lesson_result = extract_lesson(correction, window)
        lessons.append(lesson_result)

        if lesson_result["success"]:
            lesson_title = lesson_result["lesson"]["lesson_title"]
            print(f"  [{i}/{len(corrections)}] ✓ {lesson_title}", file=sys.stderr)
        else:
            print(f"  [{i}/{len(corrections)}] ✗ Failed: {lesson_result['error']}", file=sys.stderr)

    # Save lessons JSON
    lessons_json_file = Path("extracted_knowledge/lessons_extracted.json")
    with open(lessons_json_file, 'w') as f:
        json.dump(lessons, f, indent=2)

    successful_lessons = [l for l in lessons if l["success"]]
    failed_lessons = len(lessons) - len(successful_lessons)

    print(f"\n✓ Extracted {len(successful_lessons)} lessons", file=sys.stderr)
    print(f"✗ Failed: {failed_lessons}", file=sys.stderr)
    print(f"✓ Saved to {lessons_json_file}", file=sys.stderr)

    # Generate lessons_learned.md
    md_file = Path("extracted_knowledge/lessons_learned.md")

    # Group lessons by category
    by_category = {}
    for lesson_result in successful_lessons:
        if lesson_result["success"]:
            lesson = lesson_result["lesson"]
            category = lesson.get("category", "other")
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(lesson_result)

    # Generate markdown
    md_lines = [
        "# Lessons Learned from Corrections",
        "",
        f"Extracted from {len(corrections)} user corrections in conversation history.",
        f"Successfully extracted {len(successful_lessons)} actionable lessons.",
        "",
        "---",
        ""
    ]

    category_names = {
        "infrastructure": "Infrastructure & Services",
        "data_model": "Data Models & Schemas",
        "authentication": "Authentication & Credentials",
        "api_usage": "API Usage & Integration",
        "build_process": "Build & Deployment",
        "code_patterns": "Code Patterns & Practices",
        "other": "Other"
    }

    for category, category_lessons in sorted(by_category.items()):
        category_name = category_names.get(category, category.title())
        md_lines.append(f"## {category_name}")
        md_lines.append("")

        for lesson_result in category_lessons:
            lesson = lesson_result["lesson"]
            md_lines.append(f"### {lesson['lesson_title']}")
            md_lines.append("")
            md_lines.append(f"**Instruction:** {lesson['instruction']}")
            md_lines.append("")
            md_lines.append(f"**Reasoning:** {lesson.get('reasoning', 'N/A')}")
            md_lines.append("")
            md_lines.append(f"*Confidence: {lesson.get('confidence', 0.0):.0%} | From: {lesson_result['timestamp']}*")
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

    with open(md_file, 'w') as f:
        f.write('\n'.join(md_lines))

    print(f"✓ Generated {md_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
