set shell := ["bash", "-uo", "pipefail", "-c"]

default := "help"

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

help:
    @just --list
