"""Tests for the AWS Bedrock Claude adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

from botocore.exceptions import ClientError
from requests.adapters import HTTPAdapter

from lessons_toolkit.adapters.bedrock import BedrockClaudeAdapter

if TYPE_CHECKING:
    from collections.abc import Mapping

    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch


@dataclass(slots=True)
class DummyBody:
    """Minimal StreamingBody substitute for unit tests."""

    payload: dict[str, object]

    def read(self) -> bytes:
        """Return the payload encoded as UTF-8 JSON bytes.

        Returns:
            UTF-8 encoded JSON representation of ``payload``.

        """
        return json.dumps(self.payload).encode("utf-8")


def test_bedrock_adapter_returns_expected_text() -> None:
    """Adapter flattens text blocks from the Bedrock response."""
    response = {"content": [{"type": "text", "text": "Hello world"}]}
    client = Mock()
    client.invoke_model.return_value = {"body": DummyBody(response)}

    adapter = BedrockClaudeAdapter(client=cast("object", client))

    result = adapter.invoke("Ping", model="anthropic.claude-test", timeout=20)

    assert result == "Hello world"
    kwargs = client.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "anthropic.claude-test"
    assert json.loads(cast("str", kwargs["body"]))["messages"][0]["content"][0]["text"] == "Ping"


def test_bedrock_adapter_handles_client_error() -> None:
    """Client errors return ``None`` instead of raising up-stack."""
    client = Mock()
    client.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Exceeded"}},
        "InvokeModel",
    )

    adapter = BedrockClaudeAdapter(client=cast("object", client))

    assert adapter.invoke("Ping", model="anthropic.claude-test", timeout=20) is None


def test_bedrock_adapter_rejects_missing_body(caplog: LogCaptureFixture) -> None:
    """Empty responses result in ``None`` and emit a log message."""
    client = Mock()
    client.invoke_model.return_value = {}

    adapter = BedrockClaudeAdapter(client=cast("object", client))

    with caplog.at_level("WARNING"):
        result = adapter.invoke("Ping", model="anthropic.claude-test", timeout=20)

    assert result is None
    assert "missing body stream" in " ".join(caplog.messages)


def test_bedrock_adapter_token_flow(monkeypatch: MonkeyPatch) -> None:
    """API token path should issue HTTP requests and return parsed text."""

    class DummyResponse:
        def __init__(self, payload: Mapping[str, object]) -> None:
            self._payload = payload

        @staticmethod
        def raise_for_status() -> None:
            return None

        def json(self) -> Mapping[str, object]:
            return self._payload

    captured: dict[str, object] = {}

    class DummySession:
        def __init__(self) -> None:
            self.mounts: dict[str, object] = {}

        def mount(self, prefix: str, adapter: object) -> None:
            self.mounts[prefix] = adapter

        def post(
            self,
            url: str,
            *,
            headers: Mapping[str, str],
            data: str,
            timeout: int,
        ) -> DummyResponse:
            captured["session"] = self
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json.loads(data)
            captured["timeout"] = timeout
            return DummyResponse({"content": [{"type": "text", "text": "Token flow"}]})

    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    monkeypatch.setattr("requests.Session", make_session)

    token = "test-token"  # noqa: S105 - test fixture token
    adapter = BedrockClaudeAdapter(region="us-east-1", api_token=token)

    result = adapter.invoke("Ping", model="anthropic.claude-test", timeout=15)

    assert result == "Token flow"
    assert (
        captured["url"]
        == "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-test/invoke"
    )
    assert captured["headers"]["Authorization"] == f"Bearer {token}"
    timeout_seconds = 15
    assert captured["timeout"] == timeout_seconds
    assert sessions, "Expected HTTP session to be created"
    session = sessions[0]
    assert "https://" in session.mounts
    https_adapter = session.mounts["https://"]
    assert isinstance(https_adapter, HTTPAdapter)
    expected_pool = getattr(adapter, "_http_pool_size", None)
    assert getattr(https_adapter, "_pool_maxsize", None) == expected_pool
