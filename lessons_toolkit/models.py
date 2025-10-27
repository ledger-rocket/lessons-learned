"""Typed domain models for the lessons toolkit."""

from __future__ import annotations

import datetime as dt
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, RootModel, model_validator


class Category(str, Enum):
    """Canonical set of lesson categories."""

    INFRASTRUCTURE = "infrastructure"
    DATA_MODEL = "data_model"
    AUTHENTICATION = "authentication"
    API_USAGE = "api_usage"
    BUILD_PROCESS = "build_process"
    CODE_PATTERNS = "code_patterns"
    OTHER = "other"


class PromptRecord(BaseModel):
    """A user prompt extracted from a transcript."""

    timestamp: dt.datetime
    content: str

    @property
    def iso_timestamp(self) -> str:
        """Return the timestamp in ISO format with Z suffix if UTC."""
        value = self.timestamp.isoformat()
        return value.replace("+00:00", "Z")

    @classmethod
    def from_iso_timestamp(cls, iso_timestamp: str, content: str) -> PromptRecord:
        """Construct a record from an ISO-8601 string and content.

        Returns:
            A newly constructed `PromptRecord` instance.

        """
        parsed = dt.datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return cls(timestamp=parsed, content=content)


class ClassificationPayload(BaseModel):
    """Structured response from Claude for a classification prompt."""

    reason: str | None = None
    is_correction: bool = False
    is_frustrated: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ClassificationResult(BaseModel):
    """Outcome of classifying a single prompt."""

    prompt: PromptRecord
    success: bool
    classification: ClassificationPayload | None = None
    error: str | None = None

    @property
    def is_correction(self) -> bool:
        """True if the prompt is a confirmed correction."""
        return bool(self.classification and self.classification.is_correction)

    @property
    def is_frustrated(self) -> bool:
        """True if the prompt expresses frustration."""
        return bool(self.classification and self.classification.is_frustrated)


class PromptWindow(BaseModel):
    """Conversation slice around a user prompt."""

    timestamp: dt.datetime
    window: str
    user_message: str


class CorrectionRecord(BaseModel):
    """Stored correction entry from classification output."""

    timestamp: dt.datetime
    prompt: str
    classification: dict[str, Any]


class LessonDetails(BaseModel):
    """Lesson extracted from Claude."""

    lesson_title: str
    instruction: str
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)


class LessonExtractionPayload(BaseModel):
    """Raw response from lesson extraction prompt."""

    is_correction: bool = False
    reasoning: str | None = None
    lesson_title: str | None = None
    instruction: str | None = None
    category: str | None = None
    confidence: float | None = None

    def to_details(self) -> LessonDetails | None:
        """Convert payload into LessonDetails if populated.

        Returns:
            The converted `LessonDetails`, or ``None`` if required fields are missing.

        """
        if not (self.lesson_title and self.instruction and self.category):
            return None
        try:
            category = Category(self.category)
        except ValueError:
            category = Category.OTHER
        confidence = self.confidence if self.confidence is not None else 0.0
        return LessonDetails(
            lesson_title=self.lesson_title,
            instruction=self.instruction,
            category=category,
            confidence=confidence,
        )


class LessonRecord(BaseModel):
    """Lesson plus metadata used for artifact generation."""

    timestamp: dt.datetime
    user_prompt: str
    details: LessonDetails

    @property
    def category(self) -> Category:
        """Expose convenience accessor."""
        return self.details.category

    @property
    def as_jsonable(self) -> dict[str, Any]:
        """Render record suitable for JSON storage."""
        return {
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "user_prompt": self.user_prompt,
            "lesson_title": self.details.lesson_title,
            "instruction": self.details.instruction,
            "category": self.details.category.value,
            "confidence": self.details.confidence,
        }


class LessonBucket(RootModel[list[LessonDetails]]):
    """Group of lessons in the same category."""

    def as_list(self) -> list[LessonDetails]:
        """Expose contained lessons as a list.

        Returns:
            A shallow copy of the underlying lessons.

        """
        return list(self.root)


class LessonRunSummary(BaseModel):
    """Summary of an end-to-end lesson pipeline execution."""

    prompts_processed: int
    lessons_found: int
    total_lessons: int
    latest_timestamp: dt.datetime | None
    jsonl_path: Path
    json_path: Path
    markdown_path: Path

    @model_validator(mode="before")
    @classmethod
    def _normalise_paths(
        cls,
        values: dict[str, object] | LessonRunSummary,
    ) -> dict[str, object] | LessonRunSummary:
        """Coerce path fields to ``Path`` instances.

        Returns:
            The normalised mapping or existing model instance.

        """
        if isinstance(values, dict):
            for field in ("jsonl_path", "json_path", "markdown_path"):
                maybe_value = values.get(field)
                if isinstance(maybe_value, str):
                    values[field] = Path(maybe_value)
        return values

    @property
    def markdown_path_str(self) -> str:
        """Return the Markdown artefact path as a string."""
        return self.markdown_path.as_posix()


class LessonExtractionSummary(BaseModel):
    """Summary of targeted lesson extraction run."""

    total_corrections: int
    successful: int
    failed: int
    failures: list[dict[str, str]]


class ClassificationSummary(BaseModel):
    """Summary of classification run."""

    total: int
    successful: int
    failed: int
    corrections_found: int
    corrections_path: Path
    debug_path: Path | None

    @model_validator(mode="before")
    @classmethod
    def _normalise_paths(
        cls,
        values: dict[str, object] | ClassificationSummary,
    ) -> dict[str, object] | ClassificationSummary:
        """Ensure stored paths are concrete ``Path`` objects.

        Returns:
            The updated mapping or original model value.

        """
        if isinstance(values, dict):
            for field in ("corrections_path", "debug_path"):
                maybe_value = values.get(field)
                if isinstance(maybe_value, str):
                    values[field] = Path(maybe_value)
        return values


class RunLogEntry(BaseModel):
    """Metrics captured per completed run."""

    ran_at: dt.datetime
    latest_timestamp: dt.datetime
    prompts: int
    lessons_found: int
    total_lessons: int


def _empty_run_log_entries() -> list[RunLogEntry]:
    """Return an empty collection for run log entries.

    Returns:
        A new, empty run log list.

    """
    return []


class StateSnapshot(BaseModel):
    """Incremental state tracking."""

    last_timestamp: dt.datetime | None = None
    runs: list[RunLogEntry] = Field(default_factory=_empty_run_log_entries)

    @model_validator(mode="after")
    def _sort_runs(self) -> StateSnapshot:
        """Ensure runs are sorted chronologically.

        Returns:
            The snapshot with runs sorted by execution time.

        """
        self.runs.sort(key=lambda run: run.ran_at)
        return self
