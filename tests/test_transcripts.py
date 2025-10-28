"""Tests for transcript utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lessons_toolkit.transcripts import list_projects

if TYPE_CHECKING:
    from pathlib import Path


def test_list_projects_returns_sorted_unique(tmp_path: Path) -> None:
    """Projects list should deduplicate and sort when multiple files exist."""
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-a" / "session-1.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "proj-b" / "session-2.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "proj-b" / "session-3.jsonl").write_text("{}\n", encoding="utf-8")

    projects = list_projects(tmp_path)

    assert projects == ["proj-a", "proj-b"]
