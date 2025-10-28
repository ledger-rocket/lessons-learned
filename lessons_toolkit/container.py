"""Composition root wiring ports and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .adapters import (
    AnthropicClaudeAdapter,
    BedrockClaudeAdapter,
    BedrockCredentials,
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

        Raises:
            ValueError: If the configured transport is not recognised.

        """
        if self.settings.transport == "cli":
            return ClaudeCliAdapter(
                binary=self.settings.claude_cli_bin,
                flags=self.settings.claude_cli_flags,
            )
        if self.settings.transport == "api":
            return AnthropicClaudeAdapter(
                api_key=self.settings.anthropic_api_key or "",
                base_url=str(self.settings.anthropic_base_url)
                if self.settings.anthropic_base_url
                else None,
            )
        if self.settings.transport == "bedrock":
            profile_aliases: dict[str, str] = {}

            if self.settings.bedrock_quick_profile:
                profile_aliases[self.settings.quick_model] = self.settings.bedrock_quick_profile
            if self.settings.bedrock_full_profile:
                profile_aliases[self.settings.full_model] = self.settings.bedrock_full_profile
            if self.settings.bedrock_dedupe_profile:
                profile_aliases[self.settings.dedupe_model] = self.settings.bedrock_dedupe_profile
            return BedrockClaudeAdapter(
                region=self.settings.bedrock_region,
                credentials=BedrockCredentials(
                    profile=self.settings.bedrock_profile,
                    access_key_id=self.settings.bedrock_access_key_id,
                    secret_access_key=self.settings.bedrock_secret_access_key,
                    session_token=self.settings.bedrock_session_token,
                ),
                timeouts=(
                    self.settings.bedrock_read_timeout,
                    self.settings.bedrock_connect_timeout,
                ),
                api_token=self.settings.bedrock_api_token,
                http_pool_size=self.settings.bedrock_http_pool_size,
                model_aliases=profile_aliases,
            )
        message = f"Unsupported transport: {self.settings.transport}"
        raise ValueError(message)

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
