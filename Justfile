set shell := ["bash", "-uo", "pipefail", "-c"]

default:
    @just help

install:
    uv sync --extra dev

format:
    uv run ruff format .

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check --fix .

lint-fix-unsafe:
    uv run ruff check --fix --unsafe-fixes .

typecheck:
    uv run pyright

test:
    uv run pytest

check: format-check lint typecheck test
    @echo "All checks passed."

fix: format lint-fix
    @echo "Auto-fixes applied."

fix-unsafe: format lint-fix-unsafe
    @echo "Unsafe auto-fixes applied."

classify *args:
    start_from="$(cat config/start_from.txt 2>/dev/null || true)"; if [ -n "$start_from" ]; then echo "Using start-from: $start_from"; else echo "No start-from override in config/start_from.txt"; fi
    uv run lessons-tk classify {{args}}

run-pipeline *args:
    start_from="$(cat config/start_from.txt 2>/dev/null || true)"; if [ -n "$start_from" ]; then echo "Using start-from: $start_from"; else echo "No start-from override in config/start_from.txt"; fi
    uv run lessons-tk run {{args}}

extract-lessons *args:
    uv run lessons-tk extract-lessons {{args}}

extract-prompts *args:
    uv run lessons-tk extract-prompts {{args}}

list-projects *args:
    uv run lessons-tk projects {{args}}

sessions *args:
    uv run lessons-tk sessions {{args}}

help:
    @printf "Lessons Toolkit commands\\n"
    @printf "  just install                 # Sync dependencies with dev extras\\n"
    @printf "  just check                   # Ruff format/lint, Pyright, pytest\\n"
    @printf "  just format                  # Apply Ruff formatter\\n"
    @printf "  just lint                    # Ruff lint only\\n"
    @printf "  just lint-fix[-unsafe]       # Ruff lint with autofix\\n"
    @printf "  just typecheck               # Pyright strict type check\\n"
    @printf "  just test                    # Pytest suite\\n"
    @printf "  just classify --args         # Run classifier (echoes config/start_from.txt)\\n"
    @printf "  just run-pipeline --args     # Full pipeline run (echoes config/start_from.txt)\\n"
    @printf "  just extract-lessons --args  # Rehydrate lessons from corrections JSON\\n"
    @printf "  just extract-prompts ARGS    # Build prompts feed (Claude logs or JSON export)\\n"
    @printf "  just list-projects           # Show Claude project IDs discovered locally\\n"
    @printf "  just sessions --args         # Inspect transcript sessions\\n"
    @printf "  just fix                     # Formatter + lint fix\\n"
    @printf "  just fix-unsafe              # Formatter + lint fix with unsafe rule\\n"
