#!/usr/bin/env python3
"""
Classify user prompts to identify corrections/annoyed responses.

Uses Claude Code CLI with Haiku 4.5 to classify each user prompt.
Only classifies the prompts - conversation windows extracted later for positives.

NOTE: This is a CLI analysis script, not production code.
print() to stderr is appropriate for user-facing progress/status messages.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed


CLASSIFICATION_PROMPT = """You are analyzing a user's message to a coding assistant (Claude Code).

Determine if the user is:
1. Correcting the assistant (pointing out an error, wrong assumption, incorrect information)
2. Expressing frustration/annoyance with the assistant's approach

User message:
{user_message}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "is_correction": true/false,
  "is_frustrated": true/false,
  "confidence": 0.0-1.0,
  "reason": "brief explanation of why this is/isn't a correction"
}}

Examples:
- "no they dont. stop guessing and hacking" → is_correction: true, is_frustrated: true
- "you're right" → is_correction: false
- "actually it's field_name not fieldName" → is_correction: true
- "can you help with X?" → is_correction: false
"""


def classify_prompt(prompt_text: str, timestamp: str) -> Dict[str, Any]:
    """Classify a single user prompt using Claude Code CLI."""
    # Build the prompt
    full_prompt = CLASSIFICATION_PROMPT.format(user_message=prompt_text)

    # Call claude CLI with Haiku 4.5
    try:
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
            timeout=30,
            check=False  # Don't raise on non-zero exit
        )
    except subprocess.TimeoutExpired:
        return {
            "timestamp": timestamp,
            "prompt": prompt_text,
            "error": "Claude CLI timeout after 30s",
            "success": False
        }
    except FileNotFoundError:
        return {
            "timestamp": timestamp,
            "prompt": prompt_text,
            "error": "claude command not found",
            "success": False
        }

    # Check if subprocess succeeded
    if result.returncode != 0:
        return {
            "timestamp": timestamp,
            "prompt": prompt_text,
            "error": f"Claude CLI failed with code {result.returncode}: {result.stderr}",
            "success": False
        }

    response_text = result.stdout.strip()

    # Parse JSON response
    try:
        classification = json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown if present
        if "```json" in response_text:
            try:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
                classification = json.loads(json_str)
            except (IndexError, json.JSONDecodeError) as e:
                return {
                    "timestamp": timestamp,
                    "prompt": prompt_text,
                    "error": f"Failed to parse markdown JSON: {e}",
                    "success": False
                }
        else:
            return {
                "timestamp": timestamp,
                "prompt": prompt_text,
                "error": f"Invalid JSON response: {response_text[:200]}",
                "success": False
            }

    return {
        "timestamp": timestamp,
        "prompt": prompt_text,
        "classification": classification,
        "success": True
    }


def load_user_prompts(prompts_file: Path) -> List[Dict[str, str]]:
    """Load user prompts from text file."""
    prompts = []
    current_timestamp = None
    current_content = []

    with open(prompts_file, 'r') as f:
        for line in f:
            line = line.rstrip('\n')

            # Timestamp line
            if line.startswith('[') and line.endswith(']'):
                # Save previous prompt if exists
                if current_timestamp and current_content:
                    prompts.append({
                        "timestamp": current_timestamp,
                        "content": '\n'.join(current_content).strip()
                    })

                current_timestamp = line[1:-1]  # Remove brackets
                current_content = []

            # Blank line separator
            elif not line:
                continue

            # Content line
            else:
                current_content.append(line)

        # Save final prompt
        if current_timestamp and current_content:
            prompts.append({
                "timestamp": current_timestamp,
                "content": '\n'.join(current_content).strip()
            })

    return prompts


def main():
    parser = argparse.ArgumentParser(
        description="Classify user prompts to find corrections or frustration signals."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N user prompts (default: all prompts).",
    )
    args = parser.parse_args()

    # Load prompts
    prompts_file = Path("transcripts/user_prompts_only.txt")
    if not prompts_file.exists():
        print(f"Error: {prompts_file} not found", file=sys.stderr)
        sys.exit(1)

    prompts = load_user_prompts(prompts_file)
    print(f"Loaded {len(prompts)} user prompts", file=sys.stderr)

    if args.limit is not None:
        prompts = prompts[:args.limit]
        print(f"Processing first {len(prompts)} prompts (limit={args.limit})", file=sys.stderr)
    else:
        print(f"Processing all {len(prompts)} prompts", file=sys.stderr)

    # Classify prompts in parallel
    results = []
    corrections_found = 0

    print("Classifying prompts...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(classify_prompt, p["content"], p["timestamp"]): i
            for i, p in enumerate(prompts)
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            if result["success"]:
                classification = result["classification"]
                if classification.get("is_correction") or classification.get("is_frustrated"):
                    corrections_found += 1
                    print(f"  [{corrections_found}] Found correction at {result['timestamp']}", file=sys.stderr)

            # Progress
            completed = len(results)
            if completed % 50 == 0:
                print(f"Progress: {completed}/{len(prompts)} ({100*completed/len(prompts):.1f}%)", file=sys.stderr)

    # Sort results by timestamp
    results.sort(key=lambda r: r["timestamp"])

    # Filter to only corrections/frustrated responses
    corrections = [
        r for r in results
        if r["success"] and (
            r["classification"].get("is_correction") or
            r["classification"].get("is_frustrated")
        )
    ]

    # Save only corrections
    output_file = Path("extracted_knowledge/correction_classifications.json")

    # Also save all results for debugging
    debug_file = Path("extracted_knowledge/all_classifications_debug.json")

    try:
        output_file.parent.mkdir(exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(corrections, f, indent=2)
        with open(debug_file, 'w') as f:
            json.dump(results[:5], f, indent=2)  # Save first 5 for debugging
    except (IOError, OSError) as e:
        # CLI script - print() to stderr is appropriate for user-facing error messages
        print(f"Error: Failed to write output file: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Classified {len(results)} prompts", file=sys.stderr)
    print(f"✓ Found {corrections_found} corrections/frustrated responses", file=sys.stderr)
    print(f"✓ Saved to {output_file}", file=sys.stderr)

    # Summary statistics
    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful

    print(f"\nSuccessful: {successful}", file=sys.stderr)
    print(f"Failed: {failed}", file=sys.stderr)

    # Show sample corrections
    corrections = [r for r in results if r["success"] and
                   (r["classification"].get("is_correction") or r["classification"].get("is_frustrated"))]

    if corrections:
        print(f"\nSample corrections (first 5):", file=sys.stderr)
        for i, corr in enumerate(corrections[:5], 1):
            prompt_preview = corr["prompt"][:80].replace('\n', ' ')
            confidence = corr["classification"].get("confidence", 0)
            reason = corr["classification"].get("reason", "")
            print(f"\n{i}. [{corr['timestamp']}] (confidence: {confidence})", file=sys.stderr)
            print(f"   Prompt: {prompt_preview}...", file=sys.stderr)
            print(f"   Reason: {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
