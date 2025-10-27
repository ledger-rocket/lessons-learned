"""Composition root wiring ports and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .adapters import (
    AnthropicClaudeAdapter,
    ClaudeCliAdapter,
    FilesystemClassificationRepository,
    FilesystemLessonRepository,
    FilesystemPromptRepository,
    FilesystemPromptTemplateRepository,
    FilesystemStartFromRepository,
    FilesystemStateRepository,
    FilesystemTranscriptSource,
)
from .models import CorrectionRecord
from .services import (
    LessonPipeline,
    PipelineDependencies,
    PromptClassifier,
    TargetedLessonExtractor,
)

if TYPE_CHECKING:
    from .ports import (
        ClassificationRepository,
        ClaudePort,
        LessonRepository,
        PromptRepository,
        PromptTemplateRepository,
        StartFromRepository,
        StateRepository,
        TranscriptSource,
    )
    from .settings import ToolkitSettings


@dataclass
class ToolkitContainer:
    """Bundle settings, adapters, and services."""

    settings: ToolkitSettings

    def __post_init__(self) -> None:
        """Initialise adapters and repositories from settings."""
        self.settings.ensure_directories()
        self._prompt_templates = FilesystemPromptTemplateRepository(self.settings.prompts_dir)
        self._prompt_repository = FilesystemPromptRepository(self.settings.prompts_file)
        self._transcript_source = FilesystemTranscriptSource(self.settings.transcript_file)
        self._lesson_repository = FilesystemLessonRepository(
            jsonl_path=self.settings.lessons_raw_file,
            json_path=self.settings.lessons_json_file,
            markdown_path=self.settings.lessons_markdown_file,
            extracted_path=self.settings.lessons_extracted_file,
        )
        self._classification_repository = FilesystemClassificationRepository(
            corrections_path=self.settings.corrections_file,
            debug_path=self.settings.classification_debug_file,
        )
        self._state_repository = FilesystemStateRepository(self.settings.state_file)
        self._start_from_repository = FilesystemStartFromRepository(self.settings.start_from_file)
        self._claude_port = self._build_claude_port()

    def _build_claude_port(self) -> ClaudePort:
        """Construct the Claude port specified by configuration.

        Returns:
            The instantiated Claude port implementation.

        """
        if self.settings.transport == "cli":
            return ClaudeCliAdapter(
                binary=self.settings.claude_cli_bin,
                flags=self.settings.claude_cli_flags,
            )
        return AnthropicClaudeAdapter(
            api_key=self.settings.anthropic_api_key or "",
            base_url=str(self.settings.anthropic_base_url)
            if self.settings.anthropic_base_url
            else None,
        )

    @property
    def prompts(self) -> PromptRepository:
        """Return the configured prompt repository."""
        return self._prompt_repository

    @property
    def templates(self) -> PromptTemplateRepository:
        """Return the prompt template repository."""
        return self._prompt_templates

    @property
    def transcripts(self) -> TranscriptSource:
        """Return the transcript source."""
        return self._transcript_source

    @property
    def lessons(self) -> LessonRepository:
        """Return the lesson repository adapter."""
        return self._lesson_repository

    @property
    def classifications(self) -> ClassificationRepository:
        """Return the classification repository adapter."""
        return self._classification_repository

    @property
    def state(self) -> StateRepository:
        """Return the state repository adapter."""
        return self._state_repository

    @property
    def start_from(self) -> StartFromRepository:
        """Return the start-from repository."""
        return self._start_from_repository

    @property
    def claude(self) -> ClaudePort:
        """Return the active Claude port."""
        return self._claude_port

    def create_classifier(self) -> PromptClassifier:
        """Create a classifier service wired with current dependencies.

        Returns:
            A `PromptClassifier` ready for use.

        """
        return PromptClassifier(
            claude=self.claude,
            prompt_repository=self.prompts,
            template_repository=self.templates,
            output_repository=self.classifications,
            settings=self.settings,
        )

    def create_pipeline(self) -> LessonPipeline:
        """Create the end-to-end lesson pipeline service.

        Returns:
            A `LessonPipeline` bound to the configured adapters.

        """
        dependencies = PipelineDependencies(
            prompt_repository=self.prompts,
            transcript_source=self.transcripts,
            template_repository=self.templates,
            lesson_repository=self.lessons,
        )
        return LessonPipeline(
            claude=self.claude,
            dependencies=dependencies,
            settings=self.settings,
        )

    def create_targeted_extractor(self) -> TargetedLessonExtractor:
        """Create the targeted lesson extractor service.

        Returns:
            A `TargetedLessonExtractor` bound to the configured adapters.

        """
        return TargetedLessonExtractor(
            claude=self.claude,
            transcript_source=self.transcripts,
            template_repository=self.templates,
            lesson_repository=self.lessons,
            settings=self.settings,
        )


def parse_corrections(data: list[dict[str, object]]) -> list[CorrectionRecord]:
    """Convert raw corrections JSON into typed records.

    Returns:
        The sorted list of valid `CorrectionRecord` instances.

    """
    records: list[CorrectionRecord] = []
    for item in data:
        try:
            record = CorrectionRecord.model_validate({
                "timestamp": item.get("timestamp"),
                "prompt": item.get("prompt"),
                "classification": item.get("classification", {}),
            })
        except ValueError:
            continue
        records.append(record)
    records.sort(key=lambda record: record.timestamp)
    return records
