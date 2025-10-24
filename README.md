# Lessons Learned - Automated Knowledge Extraction

Extracts actionable lessons from Claude Code conversation history by analyzing times when the user corrected the agent.

## Purpose

Mine your Claude Code session transcripts to identify hard-won knowledge:

- Times you corrected the agent
- Patterns of mistakes that wasted time
- Infrastructure details learned through trial-and-error
- Best practices discovered through frustration

**Goal:** Create a `lessons_learned.md` file with instructions to prevent the agent from repeating the same mistakes.

## How It Works

**Unified Pipeline** (`extract_all_lessons.py`):

1. **Quick Classification** (Haiku 4.5)
   - For each user prompt: "Is this a correction?"
   - Fast filter to identify potential lessons

2. **Context Extraction**
   - If classified as correction: Get 100 lines before/after from transcript
   - Provides full conversation context

3. **Lesson Extraction** (Haiku 4.5)
   - Send prompt + context to Claude
   - Confirm it's a correction with full context
   - Extract: lesson title + instruction + category
   - LLM outputs reasoning (helps it think) but reasoning is NOT saved

4. **Grouping**
   - Sort lessons by category (infrastructure, code_patterns, etc.)

5. **Deduplication** (Sonnet 4.5, parallel by category)
   - Merge similar lessons within each category
   - Use smarter model for complex merging decisions

6. **Output**
   - `lessons_learned.md` - Clean markdown (titles + instructions only)
   - `lessons_learned.json` - Structured data for processing

## Usage

### Run the Main Script

```bash
cd /Users/laurencehook/Claude-scratchpad/lessons-learned
time python3 scripts/extract_all_lessons.py
```

**Configuration:**

- Edit line 325-326 to limit prompts: `prompts = prompts[:200]`
- Parallel workers: 20 (line 354)
- Models: Haiku 4.5 (classify/extract), Sonnet 4.5 (dedupe)

**Expected Time:**

- 200 prompts: ~5 minutes
- 1,133 prompts (full): ~28 minutes

**Output files:**

- `extracted_knowledge/lessons_learned.md` - Final lessons (clean, no metadata)
- `extracted_knowledge/lessons_learned.json` - Structured JSON by category
- `extracted_knowledge/lessons_raw.jsonl` - Raw extractions before dedup

### Viewing Results

```bash
# View markdown output
cat extracted_knowledge/lessons_learned.md

# Count lessons by category
cat extracted_knowledge/lessons_learned.json | jq 'to_entries | .[] | "\(.key): \(.value | length)"'
```

## Output Format

### lessons_learned.md

Clean markdown organized by category:

```markdown
# Lessons Learned

## Code Patterns & Practices

### Stop guessing about errors - read the actual code first

When debugging an error, read the actual implementation code that creates
the transfers and accounts before speculating about causes...

### Never leave temporary files or backups in main directories

When creating temporary files for exploration or testing, immediately move
them to an archive directory...
```

**No reasoning, no confidence scores, no timestamps** - just actionable instructions.

## Directory Structure

```
lessons-learned/
├── README.md              - This file
├── scripts/               - Extraction scripts
│   ├── extract_all_lessons.py          - Main unified pipeline ⭐
│   ├── extract_claude_sessions.py      - Parse .jsonl session logs
│   ├── extract_user_prompts.py         - Extract user prompts only
│   ├── classify_corrections.py         - (deprecated, now part of unified)
│   └── extract_lessons.py              - (deprecated, now part of unified)
├── transcripts/           - Raw conversation data
│   ├── all_sessions.txt               - Full transcript (4.7MB)
│   ├── all_sessions.json              - JSON format (5.9MB)
│   ├── event_service.txt              - Event service only (4.3MB)
│   └── user_prompts_only.txt          - Just user prompts (524KB)
└── extracted_knowledge/   - Output files
    ├── lessons_learned.md             - Final lessons (markdown)
    ├── lessons_learned.json           - Final lessons (JSON)
    └── lessons_raw.jsonl              - Raw before dedup
```

## Transcripts

**Coverage:** Sept 24 - Oct 24, 2025 (29 days)

**Source:** All `.jsonl` files from `~/.claude/projects/*/`

**Statistics:**

- Total messages: 13,493
- User messages: 1,892 (after filtering)
- Projects covered: 9 (94% from ledger-rocket-go-event-service)

## Categories

Lessons are grouped into:

- `infrastructure` - Services, endpoints, connections
- `data_model` - Schemas, field names, structures
- `authentication` - AWS SSO, credentials, profiles
- `api_usage` - API calls, parameters, integration
- `build_process` - Build tools, deployment, testing
- `code_patterns` - General coding practices

## Scripts Reference

### extract_all_lessons.py ⭐

Main unified pipeline. Processes user prompts → extracts lessons → deduplicates.

**Key features:**

- Parallel processing (20 workers)
- Two-model approach (Haiku for speed, Sonnet for quality)
- Logs progress for every prompt
- Outputs clean markdown + JSON

### extract_claude_sessions.py

Converts `.jsonl` session logs to readable transcripts.

```bash
# Extract all sessions
python scripts/extract_claude_sessions.py

# Specific project
python scripts/extract_claude_sessions.py ~/.claude/projects/PROJECT/*.jsonl \
  --output-file output.txt
```

### extract_user_prompts.py

Extracts only user prompts from transcripts (filters out hooks, IDE events, interruptions).

```bash
python scripts/extract_user_prompts.py transcripts/all_sessions.json \
  transcripts/user_prompts_only.txt
```

## Extending

To add new categories, edit `CATEGORIES` list in `extract_all_lessons.py`:

```python
CATEGORIES = [
    "infrastructure",
    "data_model",
    "authentication",
    "api_usage",
    "build_process",
    "code_patterns",
    "your_new_category"  # Add here
]
```

## Troubleshooting

**Script seems stuck:**

- Check logs - it prints progress for every prompt: `[45/200] - No lesson`
- With 20 workers, each prompt takes ~1-2 seconds

**Deduplication not working:**

- Check Sonnet 4.5 responses in output
- Try adjusting DEDUPE_PROMPT aggressiveness

**Too many/few lessons:**

- Adjust classification threshold in QUICK_CLASSIFY_PROMPT
- Adjust confirmation logic in FULL_EXTRACTION_PROMPT
