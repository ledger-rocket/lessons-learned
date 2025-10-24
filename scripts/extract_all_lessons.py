#!/usr/bin/env python3
"""
Unified lessons learned extraction pipeline.

Flow:
1. For each user prompt (parallel):
   - Quick classify: is this a correction?
   - If yes: get context + confirm + extract lesson
   - Append to output file
2. After all processed:
   - Group by category
   - Deduplicate within each category (parallel)
   - Save final lessons_learned.json
"""

import argparse
import json
import logging
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger(__name__)

# Shared lock to prevent clobbered JSONL writes from worker threads
FILE_WRITE_LOCK = threading.Lock()

# Categories enum
CATEGORIES = [
    "infrastructure",
    "data_model",
    "authentication",
    "api_usage",
    "build_process",
    "code_patterns"
]

# Step 1: Quick classification prompt
QUICK_CLASSIFY_PROMPT = """Is this user message correcting or expressing frustration with the assistant?

User message: {user_message}

Respond with ONLY: YES or NO"""

# Step 3: Full extraction prompt (with context)
FULL_EXTRACTION_PROMPT = """Analyze this conversation where the user may have corrected the assistant.

## Conversation Context
{conversation_window}

## User Message (at timestamp {timestamp})
{user_message}

## Task
1. Confirm this is actually a correction/frustration (user pointing out mistake, wrong assumption, or bad approach)
2. If YES, extract the lesson learned

## Output Format
Respond with JSON only (no markdown):
{{
  "is_correction": true/false,
  "reasoning": "Why this is/isn't a correction and what mistake was made (2-3 sentences)",
  "lesson_title": "Brief title (5-10 words)" or null,
  "instruction": "Clear instruction to prevent this mistake (1-3 sentences)" or null,
  "category": "one of: {categories}" or null,
  "confidence": 0.0-1.0
}}

If is_correction is false, set lesson_title, instruction, and category to null.
"""

# Step 7: Deduplication prompt
DEDUPE_PROMPT = """You are deduplicating lessons learned. Be AGGRESSIVE about merging similar lessons.

## Lessons in category: {category}
{lessons_json}

## Deduplication Rules

**MERGE if lessons teach fundamentally the same behavior:**
- "Never guess X" + "Always verify X first" = MERGE (same lesson)
- "Move backups to archive" + "Don't leave backups in main dir" = MERGE (same lesson)
- "Read code before guessing" + "Verify actual code first" = MERGE (same lesson)

**KEEP SEPARATE only if:**
- Different specific actions (e.g., "use curl" vs "check justfile")
- Different contexts (e.g., "test mocks" vs "authentication")

## When Merging
1. Combine the clearest parts of both instructions
2. Keep the most specific/actionable title
3. Take the highest confidence
4. Make the merged instruction comprehensive

## Output Format
JSON array (no markdown, no explanation):
[
  {{
    "lesson_title": "...",
    "instruction": "...",
    "category": "...",
    "confidence": 0.0-1.0
  }}
]

Be aggressive. If in doubt, MERGE."""


def call_claude(prompt: str, timeout: int = 30, model: str = 'claude-haiku-4-5') -> Optional[str]:
    """Call Claude CLI and return response text, or None on failure."""
    try:
        result = subprocess.run(
            [
                'claude',
                '--print',
                '--allowedTools', '',
                '--dangerously-skip-permissions',
                '--strict-mcp-config',
                '--model', model
            ],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False
        )

        if result.returncode != 0:
            return None

        return result.stdout.strip()

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def parse_json_response(response: str) -> Optional[Any]:
    """Parse JSON from Claude response, tolerating markdown wrappers and leading text."""
    if not response:
        return None

    payload = response.strip()

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        if "```json" in payload:
            try:
                json_str = payload.split("```json", 1)[1].split("```", 1)[0].strip()
                return json.loads(json_str)
            except (IndexError, json.JSONDecodeError):
                pass

        decoder = json.JSONDecoder()
        for idx, char in enumerate(payload):
            if char in ("{", "["):
                try:
                    parsed, _ = decoder.raw_decode(payload[idx:])
                    return parsed
                except json.JSONDecodeError:
                    continue

    return None


def load_user_prompts(prompts_file: Path) -> List[Dict[str, str]]:
    """Load user prompts from text file."""
    prompts = []
    current_timestamp = None
    current_content = []

    with open(prompts_file, 'r') as f:
        for line in f:
            line = line.rstrip('\n')

            if line.startswith('[') and line.endswith(']'):
                # Save previous prompt
                if current_timestamp and current_content:
                    prompts.append({
                        "timestamp": current_timestamp,
                        "content": '\n'.join(current_content).strip()
                    })
                current_timestamp = line[1:-1]
                current_content = []
            elif not line:
                continue
            else:
                current_content.append(line)

        # Save final prompt
        if current_timestamp and current_content:
            prompts.append({
                "timestamp": current_timestamp,
                "content": '\n'.join(current_content).strip()
            })

    return prompts


def load_full_transcript(transcript_file: Path) -> List[str]:
    """Load full transcript as lines."""
    with open(transcript_file, 'r') as f:
        return f.readlines()


def find_line_number(transcript_lines: List[str], timestamp: str) -> int:
    """Find line number where timestamp appears."""
    for i, line in enumerate(transcript_lines):
        if timestamp in line:
            return i
    return -1


def extract_window(transcript_lines: List[str], line_num: int, window_size: int = 100) -> str:
    """Extract conversation window around a line."""
    start = max(0, line_num - window_size)
    end = min(len(transcript_lines), line_num + window_size)
    return ''.join(transcript_lines[start:end])


def process_single_prompt(
    prompt_data: Dict[str, str],
    transcript_lines: List[str],
    output_file: Path
) -> Optional[Dict[str, Any]]:
    """
    Process a single user prompt through the full pipeline.
    Returns lesson if found, None otherwise.
    """
    timestamp = prompt_data['timestamp']
    user_message = prompt_data['content']

    # Step 1: Quick classification
    quick_prompt = QUICK_CLASSIFY_PROMPT.format(user_message=user_message)
    quick_response = call_claude(quick_prompt, timeout=20)

    if not quick_response or 'YES' not in quick_response.upper():
        return None

    # Step 2: Get context
    line_num = find_line_number(transcript_lines, timestamp)
    if line_num == -1:
        return None

    conversation_window = extract_window(transcript_lines, line_num, window_size=100)

    # Step 3: Full extraction with context
    full_prompt = FULL_EXTRACTION_PROMPT.format(
        conversation_window=conversation_window,
        timestamp=timestamp,
        user_message=user_message,
        categories=", ".join(CATEGORIES)
    )

    full_response = call_claude(full_prompt, timeout=30)
    result = parse_json_response(full_response)

    if not result or not result.get('is_correction'):
        return None

    # Build lesson (without reasoning)
    lesson = {
        "timestamp": timestamp,
        "user_prompt": user_message[:200],  # Truncate for readability
        "lesson_title": result.get('lesson_title'),
        "instruction": result.get('instruction'),
        "category": result.get('category'),
        "confidence": result.get('confidence', 0.0)
    }

    # Step 4: Append immediately
    try:
        with FILE_WRITE_LOCK:
            with open(output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(lesson) + '\n')
    except IOError:
        pass  # Continue even if append fails

    return lesson


def deduplicate_category(category: str, lessons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate lessons within a category using Claude Sonnet 4.5."""
    if len(lessons) <= 1:
        return lessons

    # Prepare lessons for deduplication (remove metadata)
    clean_lessons = [
        {
            "lesson_title": l["lesson_title"],
            "instruction": l["instruction"],
            "category": l["category"],
            "confidence": l["confidence"]
        }
        for l in lessons
    ]

    dedupe_prompt = DEDUPE_PROMPT.format(
        category=category,
        lessons_json=json.dumps(clean_lessons, indent=2)
    )

    # Use Sonnet 4.5 for deduplication (smarter model for complex merging)
    response = call_claude(dedupe_prompt, timeout=60, model='claude-sonnet-4-5')
    result = parse_json_response(response)

    if isinstance(result, list):
        return result

    # Fallback: return original if deduplication fails
    return clean_lessons


def main():
    parser = argparse.ArgumentParser(
        description="Run the unified lessons learned extraction pipeline."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N user prompts (default: all prompts).",
    )
    args = parser.parse_args()

    logger.info("=== Unified Lessons Learned Extraction ===\n")

    # Load prompts
    prompts_file = Path("transcripts/user_prompts_only.txt")
    if not prompts_file.exists():
        logger.error(f"Error: {prompts_file} not found")
        sys.exit(1)

    prompts = load_user_prompts(prompts_file)
    logger.info(f"Loaded {len(prompts)} user prompts")

    if args.limit is not None:
        prompts = prompts[:args.limit]
        logger.info(f"Processing first {len(prompts)} prompts (limit={args.limit})")
    else:
        logger.info(f"Processing all {len(prompts)} prompts")

    # Load transcript
    transcript_file = Path("transcripts/all_sessions.txt")
    if not transcript_file.exists():
        logger.error(f"Error: {transcript_file} not found")
        sys.exit(1)

    transcript_lines = load_full_transcript(transcript_file)
    logger.info(f"Loaded transcript with {len(transcript_lines)} lines\n")

    # Prepare output file
    output_file = Path("extracted_knowledge/lessons_raw.jsonl")
    output_file.parent.mkdir(exist_ok=True)

    # Clear output file
    if output_file.exists():
        output_file.unlink()

    # Process all prompts in parallel
    logger.info("Processing prompts...")
    lessons_found = 0
    prompts_processed = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(process_single_prompt, p, transcript_lines, output_file): i
            for i, p in enumerate(prompts)
        }

        for future in as_completed(futures):
            prompts_processed += 1
            result = future.result()

            if result:
                lessons_found += 1
                logger.info(f"  [{prompts_processed}/{len(prompts)}] ✓ Lesson: {result['lesson_title']}")
            else:
                logger.info(f"  [{prompts_processed}/{len(prompts)}] - No lesson")

    logger.info(f"\n✓ Processed {len(prompts)} prompts")
    logger.info(f"✓ Found {lessons_found} lessons\n")

    # Load all lessons
    lessons = []
    with open(output_file, 'r') as f:
        for line in f:
            lessons.append(json.loads(line))

    # Group by category
    logger.info("Grouping by category...")
    by_category = {cat: [] for cat in CATEGORIES}
    by_category["other"] = []

    for lesson in lessons:
        category = lesson.get("category", "other")
        if category not in by_category:
            category = "other"
        by_category[category].append(lesson)

    for cat, cat_lessons in by_category.items():
        if cat_lessons:
            logger.info(f"  {cat}: {len(cat_lessons)} lessons")

    # Deduplicate each category in parallel
    logger.info("\nDeduplicating by category...")
    deduplicated = {}

    with ThreadPoolExecutor(max_workers=len(CATEGORIES)) as executor:
        futures = {
            executor.submit(deduplicate_category, cat, by_category[cat]): cat
            for cat in CATEGORIES if by_category[cat]
        }

        for future in as_completed(futures):
            category = futures[future]
            result = future.result()
            deduplicated[category] = result
            logger.info(f"  {category}: {len(by_category[category])} → {len(result)} lessons")

    # Save as JSON
    json_file = Path("extracted_knowledge/lessons_learned.json")
    with open(json_file, 'w') as f:
        json.dump(deduplicated, f, indent=2)

    # Convert to Markdown
    md_file = Path("extracted_knowledge/lessons_learned.md")
    md_lines = ["# Lessons Learned", ""]

    category_titles = {
        "infrastructure": "Infrastructure & Services",
        "data_model": "Data Models & Schemas",
        "authentication": "Authentication & Credentials",
        "api_usage": "API Usage & Integration",
        "build_process": "Build & Deployment",
        "code_patterns": "Code Patterns & Practices"
    }

    for category in CATEGORIES:
        if category in deduplicated and deduplicated[category]:
            md_lines.append(f"## {category_titles.get(category, category.title())}")
            md_lines.append("")
            for lesson in deduplicated[category]:
                md_lines.append(f"### {lesson['lesson_title']}")
                md_lines.append("")
                md_lines.append(lesson['instruction'])
                md_lines.append("")

    with open(md_file, 'w') as f:
        f.write('\n'.join(md_lines))

    total_lessons = sum(len(lessons) for lessons in deduplicated.values())
    logger.info(f"\n✓ Saved {total_lessons} deduplicated lessons")
    logger.info(f"  JSON: {json_file}")
    logger.info(f"  Markdown: {md_file}")


if __name__ == "__main__":
    main()
