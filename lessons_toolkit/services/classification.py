"""Prompt classification service."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from lessons_toolkit.models import (
    ClassificationPayload,
    ClassificationResult,
    ClassificationSummary,
    PromptRecord,
)
from lessons_toolkit.services.parsing import parse_response_json

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from lessons_toolkit.ports import (
        ClassificationRepository,
        ClaudePort,
        PromptRepository,
        PromptTemplateRepository,
    )
    from lessons_toolkit.settings import ToolkitSettings

logger = logging.getLogger(__name__)

CLASSIFICATION_TEMPLATE = "classification_prompt.txt"


class PromptClassifier:
    """Coordinate concurrent classification of user prompts."""

    def __init__(
        self,
        *,
        claude: ClaudePort,
        prompt_repository: PromptRepository,
        template_repository: PromptTemplateRepository,
        output_repository: ClassificationRepository,
        settings: ToolkitSettings,
    ) -> None:
        """Store dependencies used throughout the classification workflow."""
        self._claude = claude
        self._prompts = prompt_repository
        self._templates = template_repository
        self._output = output_repository
        self._settings = settings

    def classify(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> ClassificationSummary:
        """Classify prompts and persist results.

        Returns:
            A summary containing counts and artefact locations.

        """
        prompts = self._prompts.list_prompts(since=since, limit=limit)
        if not prompts:
            logger.info("No prompts to classify; generating empty corrections file.")
            self._output.write_corrections([])
            self._output.write_debug_sample([])
            return ClassificationSummary(
                total=0,
                successful=0,
                failed=0,
                corrections_found=0,
                corrections_path=self._output.corrections_path,
                debug_path=self._output.debug_path,
            )

        logger.info("Classifying %s prompts (since=%s, limit=%s)", len(prompts), since, limit)
        template = self._templates.load(CLASSIFICATION_TEMPLATE)

        results = self._run_concurrent(prompts, template)
        corrections = [result for result in results if result.is_correction or result.is_frustrated]

        self._output.write_corrections(corrections)
        self._output.write_debug_sample(results)

        successful = sum(1 for result in results if result.success)
        failed = len(results) - successful
        logger.info("Classification complete: %s success / %s failed", successful, failed)
        logger.info("Corrections/frustrations detected: %s", len(corrections))

        return ClassificationSummary(
            total=len(results),
            successful=successful,
            failed=failed,
            corrections_found=len(corrections),
            corrections_path=self._output.corrections_path,
            debug_path=self._output.debug_path,
        )

    def _run_concurrent(
        self,
        prompts: Iterable[PromptRecord],
        template: str,
    ) -> list[ClassificationResult]:
        results: list[ClassificationResult] = []
        with ThreadPoolExecutor(max_workers=self._settings.max_workers) as executor:
            future_map = {
                executor.submit(self._classify_single, prompt, template): prompt
                for prompt in prompts
            }
            results.extend(future.result() for future in as_completed(future_map))
        results.sort(key=lambda result: result.prompt.timestamp)
        return results

    def _classify_single(self, prompt: PromptRecord, template: str) -> ClassificationResult:
        payload = template.format(user_message=prompt.content)
        response = self._claude.invoke(
            payload,
            model=self._settings.quick_model,
            timeout=self._settings.quick_timeout,
        )

        if response is None:
            return ClassificationResult(
                prompt=prompt,
                success=False,
                error="Claude returned no output",
            )

        parsed_raw = parse_response_json(response)
        if not isinstance(parsed_raw, dict):
            return ClassificationResult(
                prompt=prompt,
                success=False,
                error="Claude response did not contain a JSON object",
            )
        parsed = parsed_raw

        try:
            classification = ClassificationPayload.model_validate(parsed)
        except ValueError as exc:
            return ClassificationResult(prompt=prompt, success=False, error=str(exc))

        return ClassificationResult(
            prompt=prompt,
            success=True,
            classification=classification,
        )
