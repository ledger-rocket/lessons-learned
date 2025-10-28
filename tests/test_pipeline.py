"""Tests for the lesson extraction pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch
else:
    MonkeyPatch = Any  # type: ignore[assignment]
    Path = Any  # type: ignore[assignment]

from lessons_toolkit.adapters.bedrock import BedrockAdapterConfig
from lessons_toolkit.container import ToolkitContainer, parse_corrections
from lessons_toolkit.models import (
    Category,
    CorrectionRecord,
    LessonDetails,
    LessonRecord,
    PromptRecord,
)
from lessons_toolkit.ports import (
    LessonRepository,
    PromptRepository,
    PromptTemplateRepository,
    TranscriptSource,
)
from lessons_toolkit.services.lessons import (
    LessonPipeline,
    PipelineDependencies,
    TargetedLessonExtractor,
)
from lessons_toolkit.settings import ToolkitSettings


class StubClaudePort:
    """Return canned responses used to drive deterministic tests."""

    @staticmethod
    def invoke(prompt: str, *, model: str, timeout: int) -> str | None:
        """Return the configured JSON snippets for known prompts.

        Returns:
            The predefined JSON response matching ``prompt``.

        Raises:
            AssertionError: If the prompt is not recognised.

        """
        del model, timeout
        if "Return YES if the user message likely indicates" in prompt:
            return "YES"
        if '"Lesson extraction payload for the lessons toolkit."' in prompt:
            return json.dumps({
                "is_correction": True,
                "is_general": True,
                "reasoning": "Bug confirmed",
                "lesson_title": "Verify inputs first",
                "instruction": "Validate user input before executing database writes.",
                "category": "code_patterns",
                "confidence": 0.8,
            })
        if '"Lesson extraction payload for targeted regeneration."' in prompt:
            return json.dumps({
                "is_correction": True,
                "is_general": True,
                "reasoning": "Bug confirmed",
                "lesson_title": "Verify inputs first",
                "instruction": "Validate user input before executing database writes.",
                "category": "code_patterns",
                "confidence": 0.8,
            })
        if '"Deduplicated lesson list for a single category."' in prompt:
            return json.dumps([
                {
                    "lesson_title": "Verify inputs first",
                    "instruction": "Validate user input before executing database writes.",
                    "category": "code_patterns",
                    "confidence": 0.85,
                },
            ])
        message = f"Unexpected prompt: {prompt[:60]}"
        raise AssertionError(message)


class MemoryPromptRepository(PromptRepository):
    """In-memory prompt repository."""

    def __init__(self, prompts: list[PromptRecord]) -> None:
        """Capture prompt records supplied by the test."""
        self._prompts = prompts

    def list_prompts(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PromptRecord]:
        """Return prompts respecting optional cutoff and limit.

        Returns:
            The filtered prompt list.

        """
        results = [prompt for prompt in self._prompts if not since or prompt.timestamp > since]
        if limit is not None:
            results = results[:limit]
        return results


class FixedTemplateRepository(PromptTemplateRepository):
    """Load templates from a fixed directory."""

    def __init__(self, prompt_dir: Path) -> None:
        """Store the directory containing prompt templates."""
        self._dir = prompt_dir

    def load(self, name: str) -> str:
        """Load and return the template text for ``name``.

        Returns:
            The template content as a string.

        """
        path = self._dir / name
        if not path.exists():
            path = self._dir / f"{name}.txt"
        return path.read_text(encoding="utf-8")


class MemoryTranscriptSource(TranscriptSource):
    """Supply a predetermined transcript window."""

    def __init__(self, window: str) -> None:
        """Keep the window text."""
        self._window = window

    def fetch_window(self, _timestamp: datetime, *, before: int, after: int) -> str | None:
        """Return the stored transcript window regardless of coordinates.

        Returns:
            The canned transcript window.

        """
        del before, after
        return self._window


class MemoryLessonRepository(LessonRepository):
    """Capture lesson artefacts for assertions."""

    def __init__(self, base_dir: Path) -> None:
        """Initialise storage attributes and derived paths."""
        self.raw: list[LessonRecord] = []
        self.grouped: dict[str, list[LessonDetails]] = {}
        self.markdown: str = ""
        self.extracted: list[LessonRecord] = []
        self._jsonl_path = base_dir / "lessons_raw.jsonl"
        self._json_path = base_dir / "lessons.json"
        self._markdown_path = base_dir / "lessons.md"
        self._extracted_path = base_dir / "lessons_extracted.json"

    def prepare_run(self) -> None:
        """Reset in-memory storage before a run."""
        self.raw.clear()

    def append_raw(self, record: LessonRecord) -> None:
        """Capture an individual raw lesson."""
        self.raw.append(record)

    def write_grouped(self, grouped: dict[str, list[LessonDetails]]) -> None:
        """Store grouped lessons for later assertions."""
        self.grouped = grouped

    def write_markdown(self, markdown: str) -> None:
        """Store the markdown representation."""
        self.markdown = markdown

    def write_extracted(self, records: list[LessonRecord]) -> None:
        """Capture the detailed extraction artefact."""
        self.extracted = records

    @property
    def jsonl_path(self) -> Path:
        """Return the JSONL artefact path."""
        return self._jsonl_path

    @property
    def json_path(self) -> Path:
        """Return the grouped JSON artefact path."""
        return self._json_path

    @property
    def markdown_path(self) -> Path:
        """Return the markdown artefact path."""
        return self._markdown_path

    @property
    def extracted_path(self) -> Path:
        """Return the extracted lessons artefact path."""
        return self._extracted_path


def test_container_supports_bedrock_transport(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ToolkitContainer should construct the Bedrock adapter when requested."""
    captured: dict[str, object] = {}

    class StubBedrockAdapter:
        def __init__(self, *, config: object) -> None:
            captured["config"] = config
            self.seen_prompts: list[str] = []

        def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
            self.seen_prompts.append(prompt)
            del model, timeout
            return None

    monkeypatch.setattr(
        "lessons_toolkit.container.BedrockClaudeAdapter",
        StubBedrockAdapter,
    )

    requested_read_timeout = 120
    requested_connect_timeout = 5

    settings = ToolkitSettings(
        transport="bedrock",
        base_dir=tmp_path,
        bedrock_region="us-east-1",
        bedrock_read_timeout=requested_read_timeout,
        bedrock_connect_timeout=requested_connect_timeout,
    )

    container = ToolkitContainer(settings)

    assert isinstance(container.claude, StubBedrockAdapter)

    config = captured["config"]
    assert isinstance(config, BedrockAdapterConfig)
    assert config.region == "us-east-1"
    assert config.timeouts == (
        requested_read_timeout,
        requested_connect_timeout,
    )


def test_lesson_pipeline_builds_artifacts(tmp_path: Path) -> None:
    ts = datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc)
    prompts = [PromptRecord(timestamp=ts, content="Why did the API crash?")]
    repository = MemoryLessonRepository(tmp_path)
    dependencies = PipelineDependencies(
        prompt_repository=MemoryPromptRepository(prompts),
        transcript_source=MemoryTranscriptSource("User: Fix the API crash"),
        template_repository=FixedTemplateRepository(ToolkitSettings().prompts_dir),
        lesson_repository=repository,
    )
    pipeline = LessonPipeline(
        claude=StubClaudePort(),
        dependencies=dependencies,
        settings=ToolkitSettings(max_workers=2),
    )

    summary = pipeline.run()

    assert summary.prompts_processed == 1
    assert summary.lessons_found == 1
    assert summary.total_lessons == 1
    assert repository.raw and repository.grouped[Category.CODE_PATTERNS.value]
    assert "Validate user input" in repository.markdown


def test_targeted_extractor_uses_corrections(tmp_path: Path) -> None:
    correction = CorrectionRecord(
        timestamp=datetime(2025, 1, 5, tzinfo=timezone.utc),
        prompt="The response ignored validation",
        classification={"is_correction": True},
    )
    repository = MemoryLessonRepository(tmp_path)
    extractor = TargetedLessonExtractor(
        claude=StubClaudePort(),
        transcript_source=MemoryTranscriptSource("User: Please validate input"),
        template_repository=FixedTemplateRepository(ToolkitSettings().prompts_dir),
        lesson_repository=repository,
        settings=ToolkitSettings(),
    )

    summary = extractor.extract([correction])

    assert summary.total_corrections == 1
    assert summary.successful == 1
    assert repository.extracted
    assert repository.grouped[Category.CODE_PATTERNS.value]
    assert repository.markdown.startswith("# Lessons Learned")


def test_parse_corrections_filters_invalid_entries() -> None:
    data = [
        {"timestamp": "2025-01-01T00:00:00Z", "prompt": "Valid", "classification": {}},
        {"timestamp": "not a timestamp", "prompt": "Invalid", "classification": {}},
    ]

    records = parse_corrections(data)
    assert len(records) == 1
    assert records[0].prompt == "Valid"
