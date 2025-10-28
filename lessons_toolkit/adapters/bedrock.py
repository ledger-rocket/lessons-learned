"""AWS Bedrock adapter implementing the ClaudePort."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import quote

import boto3  # pyright: ignore[reportMissingTypeStubs]
import requests
from botocore.config import Config  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
    BotoCoreError,
    ClientError,
)
from requests.adapters import HTTPAdapter

from lessons_toolkit.ports import ClaudePort

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "bedrock-2023-05-31"


@runtime_checkable
class SupportsRead(Protocol):
    """Subset of StreamingBody interface required for decoding responses."""

    def read(self) -> bytes:
        """Return the remaining response bytes."""
        ...


class BedrockRuntimeClient(Protocol):
    """Protocol describing the subset of Bedrock runtime client used by the adapter."""

    def invoke_model(
        self,
        *,
        modelId: str,  # noqa: N803 - AWS casing
        body: str,
        contentType: str,  # noqa: N803 - AWS casing
        accept: str,
    ) -> dict[str, object]:
        """Invoke a Claude model and return the response payload."""
        ...


@dataclass(frozen=True)
class BedrockCredentials:
    """Credential bundle used for constructing Bedrock clients."""

    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    profile: str | None = None


@dataclass(frozen=True)
class BedrockAdapterConfig:
    """Configuration options for `BedrockClaudeAdapter`."""

    region: str | None = None
    credentials: BedrockCredentials | None = None
    client: BedrockRuntimeClient | None = None
    timeouts: tuple[int, int] | None = None
    api_token: str | None = None
    http_pool_size: int | None = None
    model_aliases: dict[str, str] | None = None


class BedrockClaudeAdapter(ClaudePort):
    """Invoke Claude models hosted on AWS Bedrock."""

    def __init__(self, *, config: BedrockAdapterConfig | None = None) -> None:
        """Initialise a Bedrock client session.

        Args:
            config: Optional configuration bundle. When omitted, sensible defaults are used.

        Raises:
            ValueError: If an API token is supplied without an accompanying region.

        """
        cfg = config or BedrockAdapterConfig()
        region = cfg.region
        credentials = cfg.credentials
        client = cfg.client
        timeouts = cfg.timeouts
        api_token = cfg.api_token
        http_pool_size = cfg.http_pool_size
        model_aliases = cfg.model_aliases

        if api_token and not region:
            message = "bedrock_api_token requires bedrock_region to be configured"
            raise ValueError(message)
        read_timeout, connect_timeout = timeouts or (90, 10)
        self._api_token = api_token
        self._region = region
        self._http_timeout = read_timeout
        self._http_pool_size = http_pool_size or 50
        self._model_aliases = model_aliases or {}
        self._session: requests.Session | None = None
        if api_token:
            self._session = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=self._http_pool_size,
                pool_maxsize=self._http_pool_size,
            )
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
            self._client = None
            return

        credential_bundle = credentials or BedrockCredentials()
        self._client: BedrockRuntimeClient | None = client or self._build_client(
            region=region,
            credentials=credential_bundle,
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
        )

    @staticmethod
    def _build_client(
        *,
        region: str | None,
        credentials: BedrockCredentials,
        read_timeout: int,
        connect_timeout: int,
    ) -> BedrockRuntimeClient:
        config = Config(
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            retries={"max_attempts": 3},
        )

        if credentials.profile:
            session = boto3.Session(profile_name=credentials.profile, region_name=region)
        else:
            session = boto3.Session(
                aws_access_key_id=credentials.access_key_id,
                aws_secret_access_key=credentials.secret_access_key,
                aws_session_token=credentials.session_token,
                region_name=region,
            )

        session_any: Any = session
        client_any = session_any.client("bedrock-runtime", config=config)
        return cast("BedrockRuntimeClient", client_any)

    def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
        """Send prompt text to a Bedrock model and return the response.

        Returns:
            Aggregated assistant text from the response, or ``None`` on failure.

        """
        target_model = self._model_aliases.get(model, model)
        logger.debug(
            "Invoking Bedrock model=%s (resolved=%s) timeout=%s",
            model,
            target_model,
            timeout,
        )
        payload: dict[str, object] = {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }

        if self._api_token:
            return self._invoke_with_token(payload, target_model, timeout)

        if self._client is None:
            logger.warning("Bedrock boto client not initialised; skipping request")
            return None

        try:
            response = self._client.invoke_model(
                modelId=target_model,
                body=json.dumps(payload),
                contentType="application/json",
                accept="application/json",
            )
        except (BotoCoreError, ClientError) as exc:  # pragma: no cover - network failures
            logger.warning("Bedrock invocation failed: %s", exc)
            return None

        body_obj = response.get("body")
        if not isinstance(body_obj, SupportsRead):
            logger.warning("Bedrock response missing body stream")
            return None

        raw = body_obj.read()

        try:
            decoded = raw.decode("utf-8")
            data = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Failed to decode Bedrock payload: %s", exc)
            return None

        return _extract_text(data)

    def _invoke_with_token(
        self,
        payload: dict[str, object],
        model: str,
        timeout: int,
    ) -> str | None:
        if self._session is None or self._region is None:
            logger.warning("Bedrock HTTP session not initialised; skipping request")
            return None

        quoted = quote(model, safe="")
        url = f"https://bedrock-runtime.{self._region}.amazonaws.com/model/{quoted}/invoke"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_token}",
        }
        request_timeout = timeout or self._http_timeout

        try:
            response = self._session.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=request_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                body = exc.response.text
                logger.warning(
                    "Bedrock HTTP invocation failed (%s): %s",
                    exc.response.status_code,
                    body.strip(),
                )
            else:
                logger.warning("Bedrock HTTP invocation failed: %s", exc)
            return None

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Failed to decode Bedrock HTTP payload: %s", exc)
            return None

        return _extract_text(data)


def _extract_text(data: object) -> str | None:
    """Extract assistant text blocks from a Bedrock response payload.

    Returns:
        The concatenated assistant text, or ``None`` when the payload lacks text fragments.

    """
    if not isinstance(data, dict):
        return None

    data_dict = cast("dict[str, object]", data)
    content = cast("list[object] | None", data_dict.get("content"))
    if not content:
        return None

    fragments: list[str] = []
    for block in content:
        text: str | None = None
        if isinstance(block, dict):
            block_dict = cast("dict[str, object]", block)
            maybe_content = block_dict.get("text")
            if isinstance(maybe_content, str):
                text = maybe_content
        if text:
            text = text.strip()
            if text:
                fragments.append(text)

    if not fragments:
        return None

    return "\n".join(fragments)
