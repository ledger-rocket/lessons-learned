"""Lesson extraction services."""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from lessons_toolkit.models import (
    Category,
    CorrectionRecord,
    LessonDetails,
    LessonExtractionPayload,
    LessonExtractionSummary,
    LessonRecord,
    LessonRunSummary,
    PromptRecord,
)
from lessons_toolkit.services.parsing import parse_response_json

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from lessons_toolkit.ports import (
        ClaudePort,
        LessonRepository,
        PromptRepository,
        PromptTemplateRepository,
        TranscriptSource,
    )
    from lessons_toolkit.settings import ToolkitSettings

logger = logging.getLogger(__name__)

QUICK_TEMPLATE = "quick_filter_prompt.txt"
FULL_TEMPLATE = "lesson_pipeline_prompt.txt"
DEDUPE_TEMPLATE = "dedupe_prompt.txt"
TARGETED_TEMPLATE = "targeted_lesson_prompt.txt"


@dataclass(frozen=True)
class PipelineDependencies:
    """Dependencies required to run the lesson pipeline."""

    prompt_repository: PromptRepository
    transcript_source: TranscriptSource
    template_repository: PromptTemplateRepository
    lesson_repository: LessonRepository


CATEGORY_TITLES = {
    Category.INFRASTRUCTURE: "Infrastructure & Services",
    Category.DATA_MODEL: "Data Models & Schemas",
    Category.AUTHENTICATION: "Authentication & Credentials",
    Category.API_USAGE: "API Usage & Integration",
    Category.BUILD_PROCESS: "Build & Deployment",
    Category.CODE_PATTERNS: "Code Patterns & Practices",
    Category.OTHER: "Other",
}


class LessonPipeline:
    """Execute the end-to-end lesson pipeline."""

    def __init__(
        self,
        *,
        claude: ClaudePort,
        dependencies: PipelineDependencies,
        settings: ToolkitSettings,
    ) -> None:
        """Store the dependencies used during pipeline execution."""
        self._claude = claude
        self._prompts = dependencies.prompt_repository
        self._transcripts = dependencies.transcript_source
        self._templates = dependencies.template_repository
        self._lessons = dependencies.lesson_repository
        self._settings = settings

    def run(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> LessonRunSummary:
        """Execute the pipeline from prompts through deduplication.

        Returns:
            A summary describing pipeline results and artefact locations.

        """
        prompts = self._prompts.list_prompts(since=since, limit=limit)
        self._lessons.prepare_run()

        if not prompts:
            logger.info("No prompts selected; writing empty artifacts.")
            empty: dict[str, list[LessonDetails]] = {
                category.value: list[LessonDetails]() for category in Category
            }
            self._lessons.write_grouped(empty)
            self._lessons.write_markdown(_build_markdown(empty))
            return LessonRunSummary(
                prompts_processed=0,
                lessons_found=0,
                total_lessons=0,
                latest_timestamp=None,
                jsonl_path=self._lessons.jsonl_path,
                json_path=self._lessons.json_path,
                markdown_path=self._lessons.markdown_path,
            )

        template_quick = self._templates.load(QUICK_TEMPLATE)
        template_full = self._templates.load(FULL_TEMPLATE)

        records = self._process_prompts(prompts, template_quick, template_full)

        grouped_records = _group_by_category(records)
        deduped = self._dedupe(grouped_records)
        markdown = _build_markdown(deduped)

        self._lessons.write_grouped(deduped)
        self._lessons.write_markdown(markdown)

        latest = prompts[-1].timestamp if prompts else None
        return LessonRunSummary(
            prompts_processed=len(prompts),
            lessons_found=len(records),
            total_lessons=sum(len(bucket) for bucket in deduped.values()),
            latest_timestamp=latest,
            jsonl_path=self._lessons.jsonl_path,
            json_path=self._lessons.json_path,
            markdown_path=self._lessons.markdown_path,
        )

    def _process_prompts(
        self,
        prompts: Iterable[PromptRecord],
        template_quick: str,
        template_full: str,
    ) -> list[LessonRecord]:
        results: list[LessonRecord] = []
        with ThreadPoolExecutor(max_workers=self._settings.max_workers) as executor:
            future_map = {
                executor.submit(self._process_single, prompt, template_quick, template_full): prompt
                for prompt in prompts
            }
            for future in as_completed(future_map):
                record = future.result()
                if record:
                    results.append(record)
                    self._lessons.append_raw(record)
        results.sort(key=lambda item: item.timestamp)
        return results

    def _process_single(
        self,
        prompt: PromptRecord,
        template_quick: str,
        template_full: str,
    ) -> LessonRecord | None:
        if not self._passes_quick_filter(prompt, template_quick):
            return None

        window = self._transcripts.fetch_window(
            prompt.timestamp,
            before=self._settings.context_before,
            after=self._settings.context_after,
        )
        if not window:
            return None

        full_prompt = template_full.format(
            conversation_window=window,
            timestamp=prompt.iso_timestamp,
            user_message=prompt.content,
            categories=", ".join(cat.value for cat in Category if cat is not Category.OTHER),
        )
        response = self._claude.invoke(
            full_prompt,
            model=self._settings.full_model,
            timeout=self._settings.full_timeout,
        )
        details: LessonDetails | None = None
        if response is not None:
            parsed = parse_response_json(response)
            if isinstance(parsed, dict):
                try:
                    payload = LessonExtractionPayload.model_validate(parsed)
                except ValueError:
                    payload = None
                if payload and payload.is_correction:
                    details = payload.to_details()

        if details:
            return LessonRecord(
                timestamp=prompt.timestamp,
                user_prompt=prompt.content[:200],
                details=details,
            )
        return None

    def _passes_quick_filter(self, prompt: PromptRecord, template_quick: str) -> bool:
        prompt_text = template_quick.format(user_message=prompt.content)
        response = self._claude.invoke(
            prompt_text,
            model=self._settings.quick_model,
            timeout=self._settings.quick_timeout,
        )
        if response is None:
            return False
        return "YES" in response.upper()

    def _dedupe(
        self,
        grouped: dict[Category, list[LessonRecord]],
    ) -> dict[str, list[LessonDetails]]:
        deduped: dict[str, list[LessonDetails]] = {
            category.value: list[LessonDetails]() for category in Category
        }
        template_dedupe = self._templates.load(DEDUPE_TEMPLATE)
        with ThreadPoolExecutor(max_workers=max(1, len(grouped))) as executor:
            future_map: dict[Future[list[LessonDetails]], Category] = {}
            for category, records in grouped.items():
                if not records:
                    continue
                future_map[
                    executor.submit(self._dedupe_category, category, records, template_dedupe)
                ] = category

            for future in as_completed(future_map):
                category = future_map[future]
                deduped[category.value] = future.result()

        for category, records in grouped.items():
            if not deduped[category.value] and records:
                deduped[category.value] = [record.details for record in records]

        return deduped

    def _dedupe_category(
        self,
        category: Category,
        records: list[LessonRecord],
        template: str,
    ) -> list[LessonDetails]:
        items = [
            {
                "lesson_title": record.details.lesson_title,
                "instruction": record.details.instruction,
                "category": record.details.category.value,
                "confidence": record.details.confidence,
            }
            for record in records
        ]
        payload = template.format(category=category.value, lessons_json=json.dumps(items, indent=2))
        response = self._claude.invoke(
            payload,
            model=self._settings.dedupe_model,
            timeout=self._settings.dedupe_timeout,
        )
        parsed = parse_response_json(response or "") if response else None
        if not isinstance(parsed, list):
            return [record.details for record in records]

        deduped: list[LessonDetails] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            entry_dict = cast("dict[str, object]", entry)
            try:
                raw_category = entry_dict.get("category", category.value)
                category_value = (
                    Category(raw_category) if isinstance(raw_category, str) else category
                )
            except ValueError:
                category_value = Category.OTHER
            confidence_raw = entry_dict.get("confidence", 0.0)
            if isinstance(confidence_raw, (int, float, str)):
                try:
                    confidence_value = float(confidence_raw)
                except (TypeError, ValueError):
                    confidence_value = 0.0
            else:
                confidence_value = 0.0
            try:
                detail = LessonDetails(
                    lesson_title=str(entry_dict.get("lesson_title", "Untitled")),
                    instruction=str(entry_dict.get("instruction", "")),
                    category=category_value,
                    confidence=confidence_value,
                )
            except (TypeError, ValueError):
                continue
            deduped.append(detail)
        return deduped or [record.details for record in records]


def _group_by_category(records: Iterable[LessonRecord]) -> dict[Category, list[LessonRecord]]:
    buckets: dict[Category, list[LessonRecord]] = {
        category: list[LessonRecord]() for category in Category
    }
    for record in records:
        buckets.setdefault(record.category, []).append(record)
    return buckets


def _build_markdown(grouped: dict[str, list[LessonDetails]]) -> str:
    lines: list[str] = ["# Lessons Learned", ""]
    any_content = False
    for category in (
        Category.INFRASTRUCTURE,
        Category.DATA_MODEL,
        Category.AUTHENTICATION,
        Category.API_USAGE,
        Category.BUILD_PROCESS,
        Category.CODE_PATTERNS,
        Category.OTHER,
    ):
        bucket = grouped.get(category.value, [])
        if not bucket:
            continue
        any_content = True
        lines.extend((f"## {CATEGORY_TITLES[category]}", ""))
        for lesson in bucket:
            lines.extend((f"### {lesson.lesson_title}", "", lesson.instruction, ""))

    if not any_content:
        lines.append("_No lessons extracted._")

    return "\n".join(lines)


class TargetedLessonExtractor:
    """Regenerate lessons from stored classification corrections."""

    def __init__(
        self,
        *,
        claude: ClaudePort,
        transcript_source: TranscriptSource,
        template_repository: PromptTemplateRepository,
        lesson_repository: LessonRepository,
        settings: ToolkitSettings,
    ) -> None:
        """Store dependencies required for targeted extraction."""
        self._claude = claude
        self._transcripts = transcript_source
        self._templates = template_repository
        self._lessons = lesson_repository
        self._settings = settings

    def extract(self, corrections: list[CorrectionRecord]) -> LessonExtractionSummary:
        """Rebuild lessons from stored corrections.

        Returns:
            A summary detailing successes, failures, and artefact locations.

        """
        if not corrections:
            logger.info("No corrections available; writing empty lessons artifact.")
            self._lessons.write_extracted([])
            empty: dict[str, list[LessonDetails]] = {
                category.value: list[LessonDetails]() for category in Category
            }
            self._lessons.write_grouped(empty)
            self._lessons.write_markdown(_build_markdown(empty))
            return LessonExtractionSummary(total_corrections=0, successful=0, failed=0, failures=[])

        template = self._templates.load(TARGETED_TEMPLATE)
        results: list[LessonRecord] = []
        failures: list[dict[str, str]] = []

        for correction in corrections:
            iso_timestamp = correction.timestamp.isoformat().replace("+00:00", "Z")
            window = self._transcripts.fetch_window(
                correction.timestamp,
                before=self._settings.context_before,
                after=self._settings.context_after,
            )
            if not window:
                failures.append({"timestamp": iso_timestamp, "error": "timestamp_not_found"})
                continue

            prompt_text = template.format(
                conversation_window=window,
                timestamp=iso_timestamp,
                user_correction=correction.prompt,
                classification=json.dumps(correction.classification, indent=2),
            )
            response = self._claude.invoke(
                prompt_text,
                model=self._settings.full_model,
                timeout=self._settings.full_timeout,
            )
            if response is None:
                failures.append({"timestamp": iso_timestamp, "error": "no_response"})
                continue

            parsed = parse_response_json(response)
            if not isinstance(parsed, dict):
                failures.append({"timestamp": iso_timestamp, "error": "invalid_json"})
                continue

            try:
                payload = LessonExtractionPayload.model_validate(parsed)
            except ValueError as exc:
                failures.append({"timestamp": iso_timestamp, "error": str(exc)})
                continue

            details = payload.to_details()
            if not details:
                failures.append({"timestamp": iso_timestamp, "error": "incomplete_details"})
                continue

            results.append(
                LessonRecord(
                    timestamp=correction.timestamp,
                    user_prompt=correction.prompt[:200],
                    details=details,
                ),
            )

        self._lessons.write_extracted(results)

        grouped_records = _group_by_category(results)
        grouped = {
            category.value: [record.details for record in grouped_records.get(category, [])]
            for category in Category
        }
        self._lessons.write_grouped(grouped)
        self._lessons.write_markdown(_build_markdown(grouped))

        return LessonExtractionSummary(
            total_corrections=len(corrections),
            successful=len(results),
            failed=len(failures),
            failures=failures,
        )
