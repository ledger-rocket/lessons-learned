"""Adapters binding external systems to the toolkit ports."""

from __future__ import annotations

from .anthropic_api import AnthropicClaudeAdapter
from .bedrock import BedrockAdapterConfig, BedrockClaudeAdapter, BedrockCredentials
from .claude_cli import ClaudeCliAdapter
from .filesystem import (
    FilesystemClassificationRepository,
    FilesystemLessonRepository,
    FilesystemPromptRepository,
    FilesystemPromptTemplateRepository,
    FilesystemStartFromRepository,
    FilesystemStateRepository,
    FilesystemTranscriptSource,
)

__all__ = [
    "AnthropicClaudeAdapter",
    "BedrockAdapterConfig",
    "BedrockClaudeAdapter",
    "BedrockCredentials",
    "ClaudeCliAdapter",
    "FilesystemClassificationRepository",
    "FilesystemLessonRepository",
    "FilesystemPromptRepository",
    "FilesystemPromptTemplateRepository",
    "FilesystemStartFromRepository",
    "FilesystemStateRepository",
    "FilesystemTranscriptSource",
]
