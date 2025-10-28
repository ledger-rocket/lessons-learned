# Repository Guidelines

## Project Structure & Module Organization

`lessons_toolkit/` contains the installable package. The layout is hexagonal: `ports.py`
defines Protocol-based ports, `adapters/` hosts filesystem repositories plus CLI/API transports,
`services/` implements domain logic (classifier, pipeline, targeted extractor), and `container.py`
wires everything together. Prompt templates live in `prompts/*.txt`, keeping long strings out of the
codebase. Runtime data (`transcripts/`, `extracted_knowledge/`, `config/`) stays git-ignored—create
those directories locally as needed.

When introducing new adapters or orchestration logic, update `ToolkitSettings` and the composition root
(`container.py`) so dependency injection remains authoritative and tests can swap implementations cleanly.

## Build, Test, and Development Commands

- `uv sync --extra dev` provisions runtime and tooling. Run everything with `uv run …`.
- `uv run lessons-tk extract-prompts --project-id <id>` pulls Claude Code sessions directly from
  `~/.claude/projects/<id>` and refreshes the prompt feed (`transcripts/user_prompts_only.txt`). When
  `--since` is omitted, it falls back to `config/start_from.txt`. Use `uv run lessons-tk projects`
  to list available project identifiers.
- `uv run lessons-tk extract-prompts transcripts/all_sessions.json` rebuilds the prompt feed from an
  existing JSON export if you prefer the old workflow.
- `uv run lessons-tk classify` and `uv run lessons-tk run` execute the concurrent pipeline using
  whichever transport `ToolkitSettings` resolves (`LESSONS_TRANSPORT=cli|api|bedrock`).
- `uv run lessons-tk extract-lessons --corrections-file extracted_knowledge/correction_classifications.json`
  regenerates lessons directly from stored classifications for prompt experiments.
- All CLI invocations honour `ToolkitSettings`, so override environment variables (e.g.,
  `LESSONS_TRANSPORT`, custom paths) before running commands that depend on alternate transports or
  locations.
- Individual `just` recipes map to the toolchain:
  - `just format`, `just lint`, `just typecheck`, `just test` run Ruff formatting, Ruff lint, Pyright
    (strict), and pytest respectively.
  - `just check` executes the full gauntlet (format check + lint + typecheck + tests). It must pass clean
    before merge.
  - `just fix` applies Ruff format and safe autofixes (`ruff check --fix`). If diagnostics remain, resolve
    them manually.
  - `just fix-unsafe` adds Ruff’s `--unsafe-fixes`; use it only when you will review the diff carefully.
    If either recipe cannot clear the errors, stop and fix the root cause—do not work around lint failures.

## Coding Style & Naming Conventions

Code should stay typed and explicit. Prefer Pydantic models for data interchange, `pathlib.Path` for
filesystem work, and dependency injection through ports/adapters. Keep public functions pure—log state
transitions via the provided repositories. Prompt constants belong in `prompts/*.txt`; reference them
by filename through the template repository instead of embedding multi-line strings inline. Whenever
prompt templates change, ensure corresponding fixtures or regression tests cover the behaviour shift.

**Zero shortcuts mandate.** Every feature must ship production-ready: no placeholders, temporary hacks,
or deferred clean-up. If a requirement cannot be met without compromising quality, STOP, surface the
blocker, and ask for clarification (comment in the PR or raise it in the team channel). Do not paper
over gaps or work around missing context.

## Testing Guidelines

Add pytest suites under `tests/`, favouring fake adapters (in-memory prompt repositories, stub Claude
ports) so runs stay deterministic. When touching classification or extraction heuristics, add targeted
unit tests plus a golden-sample fixture covering JSON artefacts. Always run `just check` before
pushing; for larger refactors, manually inspect the regenerated `extracted_knowledge/*.json*` to verify
categories, confidences, and Markdown formatting. Where feasible, exercise both transports (CLI and
API) using fakes to ensure parity across adapters.

## Lint & Type Expectations

- Ruff runs with `select = ["ALL"]` and only `CPY001` ignored. Resolve every lint error—do not disable
  rules without prior agreement. Use `just fix`/`just fix-unsafe` first, then address anything left
  manually.
- Pyright operates in strict mode (`pyrightconfig.json`); keep public APIs fully typed and eliminate
  `Any` leakage.
- `just check` is the gate: Ruff format/lint, Pyright, and pytest must all pass before merging.

## Commit & Pull Request Guidelines

Use Conventional Commit prefixes (`feat:`, `fix:`, `refactor:`) with verbs in the subject. Summaries
should note the affected service, transport, or prompt. PRs must describe behavioural impact, list
updated artefact paths, call out new environment variables, and reference any linked issues. Screenshot
relevant Markdown excerpts or attach before/after JSON snippets when user-facing text changes.

## Security & Configuration Tips

Transcripts and corrections remain private data—never commit raw exports or API keys. Set
`LESSONS_ANTHROPIC_API_KEY` in your shell/keychain when using API transport. The CLI adapter intentionally
sets `--dangerously-skip-permissions`; keep that binary on a trusted machine. To seed incremental runs,
write an ISO8601 timestamp to `config/start_from.txt`; the state repository will track subsequent cut-offs.
