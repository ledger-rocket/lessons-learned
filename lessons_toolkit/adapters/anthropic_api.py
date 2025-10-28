"""Anthropic API adapter implementing the ClaudePort."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

from anthropic import Anthropic, APIError, BadRequestError
from anthropic import types as anthropic_types

from lessons_toolkit.ports import ClaudePort

AnthropicMessage = anthropic_types.Message
MessageParam = anthropic_types.MessageParam

logger = logging.getLogger(__name__)


class AnthropicClaudeAdapter(ClaudePort):
    """Invoke the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        """Build an Anthropic client with the provided credentials.

        Raises:
            ValueError: If an API key is not supplied.

        """
        if not api_key:
            message = "Anthropic API key is required when transport='api'"
            raise ValueError(message)
        self._client = (
            Anthropic(api_key=api_key, base_url=base_url)
            if base_url
            else Anthropic(api_key=api_key)
        )

    def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
        """Send prompt to the Anthropic Messages API.

        Returns:
            The concatenated response text, or ``None`` if the call fails.

        """
        message_payload = [MessageParam(role="user", content=prompt)]
        try:
            response: AnthropicMessage = self._client.messages.create(
                model=model,
                max_tokens=1024,
                messages=message_payload,
                timeout=timeout,
                stream=False,
            )
        except BadRequestError as exc:
            logger.warning("Anthropic API rejected request: %s", exc)
            return None
        except APIError as exc:
            logger.warning("Anthropic API error: %s", exc)
            return None

        return _extract_text(response)


def _extract_text(response: AnthropicMessage) -> str | None:
    """Flatten the Anthropic response content into a plain string.

    Returns:
        The concatenated text payload, or ``None`` when no text blocks exist.

    """
    fragments: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            fragments.append(text.strip())
            continue
        if isinstance(block, Mapping):
            mapping_block = cast("Mapping[str, object]", block)
            maybe_text = mapping_block.get("text")
            if isinstance(maybe_text, str) and maybe_text.strip():
                fragments.append(maybe_text.strip())
    output = "\n".join(fragments)
    return output or None
