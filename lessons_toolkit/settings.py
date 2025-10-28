"""Application configuration powered by Pydantic settings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable


class ToolkitSettings(BaseSettings):
    """Central configuration object for the toolkit."""

    transport: Literal["cli", "api", "bedrock"] = "cli"

    quick_model: str = "claude-haiku-4-5"
    full_model: str = "claude-haiku-4-5"
    dedupe_model: str = "claude-sonnet-4-5"
    bedrock_quick_model: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_full_model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_dedupe_model: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_quick_profile: str | None = None
    bedrock_full_profile: str | None = None
    bedrock_dedupe_profile: str | None = None

    invoke_timeout: int = 60
    quick_timeout: int = 20
    full_timeout: int = 30
    dedupe_timeout: int = 60

    max_dedupe_rounds: int = 10
    dedupe_chunk_size: int = 25

    max_workers: int = 50
    context_before: int = 20
    context_after: int = 10

    base_dir: Path = Field(default_factory=Path.cwd)
    prompts_dir: Path = Field(default_factory=lambda: Path("prompts"))
    prompts_file: Path = Field(
        default_factory=lambda: Path("transcripts") / "user_prompts_only.txt",
    )
    transcript_file: Path = Field(default_factory=lambda: Path("transcripts") / "all_sessions.txt")
    transcript_json_file: Path = Field(
        default_factory=lambda: Path("transcripts") / "all_sessions.json",
    )
    corrections_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "correction_classifications.json",
    )
    classification_debug_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "all_classifications_debug.json",
    )
    lessons_raw_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "lessons_raw.jsonl",
    )
    lessons_json_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "lessons_learned.json",
    )
    lessons_markdown_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "lessons_learned.md",
    )
    lessons_extracted_file: Path = Field(
        default_factory=lambda: Path("extracted_knowledge") / "lessons_extracted.json",
    )
    state_file: Path = Field(default_factory=lambda: Path("extracted_knowledge") / "state.json")
    start_from_file: Path = Field(default_factory=lambda: Path("config") / "start_from.txt")

    claude_cli_bin: str = Field(default="claude", validation_alias="claude_bin")
    claude_cli_flags: tuple[str, ...] = (
        "--print",
        "--allowedTools",
        "",
        "--dangerously-skip-permissions",
        "--strict-mcp-config",
    )

    anthropic_api_key: str | None = Field(default=None, validation_alias="anthropic_api_key")
    anthropic_base_url: str | None = Field(default=None, validation_alias="anthropic_base_url")

    bedrock_region: str | None = Field(default=None)
    bedrock_profile: str | None = Field(default=None)
    bedrock_access_key_id: str | None = Field(default=None)
    bedrock_secret_access_key: str | None = Field(default=None)
    bedrock_session_token: str | None = Field(default=None)
    bedrock_read_timeout: int = Field(default=90)
    bedrock_connect_timeout: int = Field(default=10)
    bedrock_http_pool_size: int = Field(default=50)
    bedrock_api_token: str | None = Field(default=None)
    claude_projects_dir: Path = Field(
        default_factory=lambda: Path.home() / ".claude" / "projects",
        validation_alias="claude_projects_dir",
    )
    claude_project_id: str | None = Field(default=None, validation_alias="claude_project_id")

    model_config = SettingsConfigDict(
        env_prefix="LESSONS_",
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    def model_post_init(self, __context: object, /) -> None:
        """Normalise path fields relative to ``base_dir``."""
        for field in self._path_fields:
            path = getattr(self, field)
            if isinstance(path, Path) and not path.is_absolute():
                setattr(self, field, (self.base_dir / path).resolve())

    @property
    def _path_fields(self) -> Iterable[str]:
        return (
            "prompts_dir",
            "prompts_file",
            "transcript_file",
            "transcript_json_file",
            "corrections_file",
            "classification_debug_file",
            "lessons_raw_file",
            "lessons_json_file",
            "lessons_markdown_file",
            "lessons_extracted_file",
            "state_file",
            "start_from_file",
            "claude_projects_dir",
        )

    def ensure_directories(self) -> None:
        """Create directories required for runtime artefacts."""
        for path in (
            self.prompts_dir,
            self.prompts_file.parent,
            self.transcript_file.parent,
            self.transcript_json_file.parent,
            self.corrections_file.parent,
            self.classification_debug_file.parent,
            self.lessons_raw_file.parent,
            self.lessons_json_file.parent,
            self.lessons_markdown_file.parent,
            self.lessons_extracted_file.parent,
            self.state_file.parent,
            self.start_from_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
