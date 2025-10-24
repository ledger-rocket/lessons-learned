# Repository Guidelines

## Project Structure & Module Organization
`README.md` captures the mission and current data inventory. `scripts/` holds the Python 3 automation that extracts prompts, classifies corrections, and aggregates lessons—add new workflows here with cohesive modules and lightweight CLI entry points. `transcripts/` contains the raw `.txt` and `.json` conversation archives; treat them as read-only inputs. `extracted_knowledge/` stores generated artifacts (JSON, YAML, Markdown); stage new outputs and scratch files inside this directory to keep the repository root tidy.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` prepares an isolated environment; install optional helpers as needed (`pip install black ruff pytest`).
- `python scripts/extract_user_prompts.py transcripts/all_sessions.json transcripts/user_prompts_only.txt` refreshes the prompt feed that downstream scripts expect.
- `python scripts/classify_corrections.py` followed by `python scripts/extract_all_lessons.py` runs the classification and deduplication pipeline; both rely on the `claude` CLI being available on `PATH` and currently cap themselves at the first 100/20 prompts—raise or remove those slices before large backfills.
- `python scripts/extract_lessons.py` rebuilds Markdown summaries from saved classifications; use this for targeted prompt or formatting tweaks.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation, descriptive `snake_case`, and type hints where they clarify interfaces. Keep modules script-friendly: pure helpers at the top, coordination inside `main()`, wrapped with `if __name__ == "__main__":`. Prefer `pathlib.Path` for filesystem work, emit progress to `stderr` via `print` or `logging`, and guard concurrent sections (`ThreadPoolExecutor`) with clear worker limits. Prompt templates should stay in multiline constants with explanatory docstrings.

## Testing Guidelines
There is no baked-in test harness yet. When changing heuristics or prompts, add focused fixtures under a new `tests/` directory (pytest recommended) backed by trimmed transcript snippets. At minimum, dry-run the pipeline on a short slice: regenerate prompts, classify, extract lessons, then inspect the diff in `extracted_knowledge/lessons_raw.jsonl` and the rendered Markdown to confirm categories, confidences, and wording still read sensibly.

## Commit & Pull Request Guidelines
Ship focused commits with imperative subjects (`feat: widen correction sample`) and body bullets covering affected inputs and output artifacts. PRs should summarize the behavior change, link any issues, call out sample outputs (paths inside `extracted_knowledge/`), and highlight prompt adjustments or new dependencies. Include redacted before/after excerpts when touching generated Markdown or JSON for easier review.

## Security & Configuration Tips
Transcripts may reference private infrastructure; never commit fresh raw logs or credentials. Keep the `claude` CLI authenticated locally and avoid hard-coding keys or model names. Large processing runs can hit rate limits—batch work, document temporary caps in PRs, and clean up any oversized intermediate files before pushing.
