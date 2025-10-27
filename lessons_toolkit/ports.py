"""Hexagonal architecture ports (interfaces)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from pathlib import Path

    from .models import (
        ClassificationResult,
        LessonDetails,
        LessonRecord,
        PromptRecord,
        StateSnapshot,
    )


@runtime_checkable
class ClaudePort(Protocol):
    """Generalised Claude interaction."""

    def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
        """Send a prompt to Claude and return the raw text response."""
        ...


@runtime_checkable
class PromptTemplateRepository(Protocol):
    """Load long-form prompt templates."""

    def load(self, name: str) -> str:
        """Retrieve the prompt template with caching."""
        ...


@runtime_checkable
class PromptRepository(Protocol):
    """Access user prompts ready for classification."""

    def list_prompts(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PromptRecord]:
        """Return prompts ordered by timestamp."""
        ...


@runtime_checkable
class TranscriptSource(Protocol):
    """Provide transcript windows around prompts."""

    def fetch_window(
        self,
        timestamp: datetime,
        *,
        before: int,
        after: int,
    ) -> str | None:
        """Return context window around timestamp; None if timestamp is missing."""
        ...


@runtime_checkable
class StateRepository(Protocol):
    """Persist incremental run state."""

    def load(self) -> StateSnapshot:
        """Load stored state."""
        ...

    def save(self, snapshot: StateSnapshot) -> None:
        """Persist latest state."""
        ...


@runtime_checkable
class StartFromRepository(Protocol):
    """Provide the initial cutoff timestamp."""

    def read(self) -> datetime | None:
        """Return configured starting timestamp."""
        ...


@runtime_checkable
class LessonRepository(Protocol):
    """Handle persistence of lesson artifacts."""

    def prepare_run(self) -> None:
        """Ensure destination directories/files are ready."""
        ...

    def append_raw(self, record: LessonRecord) -> None:
        """Append the raw lesson record to JSONL."""
        ...

    def write_grouped(self, grouped: dict[str, list[LessonDetails]]) -> None:
        """Persist grouped lessons to JSON."""
        ...

    def write_markdown(self, markdown: str) -> None:
        """Persist Markdown summary."""
        ...

    def write_extracted(self, records: list[LessonRecord]) -> None:
        """Persist detailed lesson extraction output."""
        ...

    @property
    def jsonl_path(self) -> Path:
        """Location of the JSONL artifact."""
        ...

    @property
    def json_path(self) -> Path:
        """Location of the grouped JSON artifact."""
        ...

    @property
    def markdown_path(self) -> Path:
        """Location of the Markdown artifact."""
        ...

    @property
    def extracted_path(self) -> Path:
        """Location of the detailed extracted lessons artifact."""
        ...


@runtime_checkable
class ClassificationRepository(Protocol):
    """Persist classification output artifacts."""

    def write_corrections(self, corrections: Iterable[ClassificationResult]) -> None:
        """Persist successful corrections only."""
        ...

    def write_debug_sample(self, results: list[ClassificationResult], sample_size: int = 5) -> None:
        """Persist a sample for debugging."""
        ...

    @property
    def corrections_path(self) -> Path:
        """Location of the corrections artifact."""
        ...

    @property
    def debug_path(self) -> Path | None:
        """Location of the debug artifact."""
