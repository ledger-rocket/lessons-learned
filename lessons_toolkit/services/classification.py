"""Prompt classification service."""

from __future__ import annotations

import logging
import time
from collections import Counter
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
        started_at = time.perf_counter()

        results = self._run_concurrent(prompts, template)
        corrections = [result for result in results if result.is_correction or result.is_frustrated]

        self._output.write_corrections(corrections)
        self._output.write_debug_sample(results)

        total_time = time.perf_counter() - started_at
        successful = sum(1 for result in results if result.success)
        failed = len(results) - successful
        logger.info("Classification complete: %s success / %s failed", successful, failed)
        logger.info("Corrections/frustrations detected: %s", len(corrections))
        if total_time:
            throughput = len(results) / total_time
            logger.info(
                "Classification runtime: %.2fs (%.2f prompts/second)",
                total_time,
                throughput,
            )

        category_counts: Counter[str] = Counter()
        for result in results:
            if result.success and result.classification:
                category = (
                    "correction"
                    if result.is_correction
                    else ("frustrated" if result.is_frustrated else "neutral")
                )
                category_counts[category] += 1
        if category_counts:
            breakdown: dict[str, int] = dict(sorted(category_counts.items()))
            logger.info("Classification breakdown: %s", breakdown)

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
            total = len(future_map)
            for completed, future in enumerate(as_completed(future_map), start=1):
                result = future.result()
                results.append(result)
                if result.success and result.classification:
                    logger.debug(
                        "Classified prompt %s (%d/%d): correction=%s frustrated=%s confidence=%.2f",
                        result.prompt.iso_timestamp,
                        completed,
                        total,
                        result.is_correction,
                        result.is_frustrated,
                        result.classification.confidence,
                    )
                else:
                    logger.debug(
                        "Classification failed for prompt %s (%d/%d): %s",
                        result.prompt.iso_timestamp,
                        completed,
                        total,
                        result.error or "unknown error",
                    )
        results.sort(key=lambda result: result.prompt.timestamp)
        return results

    def _classify_single(self, prompt: PromptRecord, template: str) -> ClassificationResult:
        max_attempts = 2
        last_error = "Claude returned no output"
        for attempt in range(1, max_attempts + 1):
            payload = template.format(user_message=prompt.content)
            response = self._claude.invoke(
                payload,
                model=self._settings.quick_model,
                timeout=self._settings.quick_timeout,
            )

            if response is None:
                logger.warning(
                    "Classification attempt %s/%s returned no output for %s",
                    attempt,
                    max_attempts,
                    prompt.iso_timestamp,
                )
                last_error = "Claude returned no output"
                continue

            parsed_raw = parse_response_json(response)
            if not isinstance(parsed_raw, dict):
                last_error = "Claude response did not contain a JSON object"
                logger.warning(
                    "Classification attempt %s/%s produced non-dict response for %s: %s",
                    attempt,
                    max_attempts,
                    prompt.iso_timestamp,
                    response,
                )
                continue

            try:
                classification = ClassificationPayload.model_validate(parsed_raw)
            except ValueError as exc:
                last_error = str(exc)
                logger.warning(
                    "Classification validation failed for %s (attempt %s/%s): %s -- raw=%s",
                    prompt.iso_timestamp,
                    attempt,
                    max_attempts,
                    exc,
                    parsed_raw,
                )
                continue

            logger.debug(
                "Prompt %s classified as correction=%s frustrated=%s confidence=%.2f"
                " (attempt %s/%s)",
                prompt.iso_timestamp,
                classification.is_correction,
                classification.is_frustrated,
                classification.confidence,
                attempt,
                max_attempts,
            )

            return ClassificationResult(
                prompt=prompt,
                success=True,
                classification=classification,
            )

        return ClassificationResult(
            prompt=prompt,
            success=False,
            error=last_error,
        )
