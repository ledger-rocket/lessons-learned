"""Common helpers for parsing Claude responses."""

from __future__ import annotations

import json
from typing import cast

JSONData = dict[str, object] | list[object]


def parse_response_json(payload: str) -> JSONData | None:
    """Parse JSON object/array from Claude responses with markdown fallbacks.

    Returns:
        The parsed JSON object/array, or ``None`` if parsing fails.

    """
    text = payload.strip()
    if not text:
        return None

    parsed = _try_load_json(text)
    if parsed is not None:
        return parsed

    markdown_payload = _extract_markdown_json(text)
    if markdown_payload:
        markdown_parsed = _try_load_json(markdown_payload)
        if markdown_parsed is not None:
            return markdown_parsed

    return _scan_for_embedded_json(text)


def _try_load_json(text: str) -> JSONData | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    if isinstance(value, list):
        return cast("list[object]", value)
    return None


def _extract_markdown_json(text: str) -> str | None:
    if "```json" not in text:
        return None
    try:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    except IndexError:
        return None


def _scan_for_embedded_json(text: str) -> JSONData | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                parsed, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return cast("dict[str, object]", parsed)
            if isinstance(parsed, list):
                return cast("list[object]", parsed)
    return None
