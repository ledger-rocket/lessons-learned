"""Tests for the prompt classification service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from lessons_toolkit.models import ClassificationResult, PromptRecord
from lessons_toolkit.ports import (
    ClassificationRepository,
    ClaudePort,
    PromptRepository,
    PromptTemplateRepository,
)
from lessons_toolkit.services.classification import PromptClassifier
from lessons_toolkit.settings import ToolkitSettings

TOTAL_PROMPTS = 2
EXPECTED_SUCCESSFUL = 2

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class StubClaudePort(ClaudePort):
    """Fake Claude port that returns canned JSON payloads."""

    def __init__(self, mapping: dict[str, dict[str, object]]) -> None:
        """Store the mapping of prompt substrings to responses."""
        self._mapping = mapping

    def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
        """Return the configured JSON string for the matching prompt.

        Returns:
            The canned JSON payload or ``None`` when no match exists.

        """
        del model, timeout
        for key, payload in self._mapping.items():
            if key in prompt:
                return json.dumps(payload)
        return None


class MemoryPromptRepository(PromptRepository):
    """In-memory prompt repository used by tests."""

    def __init__(self, prompts: list[PromptRecord]) -> None:
        """Keep a copy of prompt records."""
        self._prompts = prompts

    def list_prompts(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PromptRecord]:
        """Return prompts respecting optional cutoff and limit.

        Returns:
            The filtered list of prompt records.

        """
        items = [prompt for prompt in self._prompts if not since or prompt.timestamp > since]
        if limit is not None:
            items = items[:limit]
        return items


class MemoryTemplateRepository(PromptTemplateRepository):
    """Load prompt templates from the working directory."""

    def __init__(self, prompt_dir: Path) -> None:
        """Initialise with the directory containing templates."""
        self._prompt_dir = prompt_dir

    def load(self, name: str) -> str:
        """Load and return the text for the named template.

        Returns:
            The template string for ``name``.

        """
        path = self._prompt_dir / name
        if not path.exists():
            path = self._prompt_dir / f"{name}.txt"
        return path.read_text(encoding="utf-8")


class MemoryClassificationRepository(ClassificationRepository):
    """Capture classification artefacts in memory for assertions."""

    def __init__(self, base_dir: Path) -> None:
        """Store file paths used in assertions."""
        self.corrections: list[ClassificationResult] = []
        self.debug: list[ClassificationResult] = []
        self._corrections_path = base_dir / "corrections.json"
        self._debug_path = base_dir / "debug.json"

    def write_corrections(self, corrections: Iterable[ClassificationResult]) -> None:
        """Persist copies of correction results for verification."""
        self.corrections = list(corrections)

    def write_debug_sample(
        self,
        results: list[ClassificationResult],
        sample_size: int = 5,
    ) -> None:
        """Capture a subset of results for debugging assertions."""
        self.debug = results[:sample_size]

    @property
    def corrections_path(self) -> Path:
        """Expose the corrections artefact path used in assertions."""
        return self._corrections_path

    @property
    def debug_path(self) -> Path | None:
        """Expose the debug artefact path used in assertions."""
        return self._debug_path


def test_prompt_classifier_filters_and_counts(tmp_path: Path) -> None:
    settings = ToolkitSettings(max_workers=2)

    prompts = [
        PromptRecord(
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            content="Fix the broken endpoint",
        ),
        PromptRecord(
            timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
            content="Thanks, it works now",
        ),
    ]

    claude = StubClaudePort({
        "Fix the broken endpoint": {
            "reason": "User reports a defect",
            "is_correction": True,
            "is_frustrated": False,
            "confidence": 0.9,
        },
        "Thanks, it works now": {
            "reason": "Compliment",
            "is_correction": False,
            "is_frustrated": False,
            "confidence": 0.2,
        },
    })

    repository = MemoryClassificationRepository(tmp_path)
    classifier = PromptClassifier(
        claude=claude,
        prompt_repository=MemoryPromptRepository(prompts),
        template_repository=MemoryTemplateRepository(settings.prompts_dir),
        output_repository=repository,
        settings=settings,
    )

    summary = classifier.classify()

    assert summary.total == TOTAL_PROMPTS
    assert summary.successful == EXPECTED_SUCCESSFUL
    assert summary.failed == 0
    assert summary.corrections_found == 1
    assert len(repository.corrections) == 1
    assert repository.corrections[0].classification is not None
    assert repository.corrections_path.name == "corrections.json"
    assert repository.debug_path is not None
    # Debug sample keeps chronological order
    assert [result.prompt.content for result in repository.debug] == [
        prompt.content for prompt in prompts
    ]
