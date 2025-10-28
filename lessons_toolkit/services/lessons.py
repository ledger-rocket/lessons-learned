"""Lesson extraction services."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
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

        started_at = time.perf_counter()

        if not prompts:
            logger.info("No prompts selected; writing empty artifacts.")
            empty: dict[str, list[LessonDetails]] = {
                category.value: list[LessonDetails]() for category in Category
            }
            self._lessons.write_grouped(empty)
            self._lessons.write_markdown(_build_markdown(empty))
            total_time = time.perf_counter() - started_at
            logger.info("Pipeline runtime: %.2fs (0 prompts)", total_time)
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

        total_time = time.perf_counter() - started_at
        if total_time:
            throughput = len(prompts) / total_time
            logger.info(
                "Pipeline runtime: %.2fs (%.2f prompts/second)",
                total_time,
                throughput,
            )

        if records:
            raw_category_counts = Counter(record.category.value for record in records)
            deduped_counts = {category: len(items) for category, items in deduped.items() if items}
            logger.info("Raw lesson categories: %s", dict(sorted(raw_category_counts.items())))
            logger.info("Deduped lesson categories: %s", dict(sorted(deduped_counts.items())))

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
            total = len(future_map)
            for completed, future in enumerate(as_completed(future_map), start=1):
                record = future.result()
                if record:
                    logger.debug(
                        "Lesson extracted for %s (%d/%d) category=%s confidence=%.2f",
                        record.timestamp.isoformat().replace("+00:00", "Z"),
                        completed,
                        total,
                        record.category.value,
                        record.details.confidence,
                    )
                if record:
                    results.append(record)
                    self._lessons.append_raw(record)
                else:
                    logger.debug(
                        "No lesson extracted for prompt %s (%d/%d)",
                        future_map[future].iso_timestamp,
                        completed,
                        total,
                    )
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

        category_values = [cat.value for cat in Category if cat is not Category.OTHER]
        categories_text = ", ".join((*category_values, Category.OTHER.value))
        categories_enum = ", ".join(
            f'"{value}"' for value in (*category_values, Category.OTHER.value)
        )

        full_prompt = template_full.format(
            conversation_window=window,
            timestamp=prompt.iso_timestamp,
            user_message=prompt.content,
            categories=categories_text,
            categories_enumerated=categories_enum,
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
                if payload and payload.is_correction and payload.is_general:
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
        current_details = [record.details for record in records]
        if len(current_details) <= 1:
            return current_details

        logger.info(
            "Lesson dedupe starting for %s with %d candidates",
            category.value,
            len(current_details),
        )

        max_rounds = max(1, self._settings.max_dedupe_rounds)
        chunk_size = max(1, self._settings.dedupe_chunk_size)
        signature = _details_signature(current_details)
        total_removed = 0

        for round_index in range(1, max_rounds + 1):
            new_details = self._invoke_dedupe_model(
                category=category,
                details=current_details,
                template=template,
            )
            if new_details is None:
                if len(current_details) > chunk_size:
                    logger.info(
                        "Lesson dedupe retrying %s in %d-sized batches after failed response",
                        category.value,
                        chunk_size,
                    )
                    batch_results = self._dedupe_batches(
                        category=category,
                        details=current_details,
                        template=template,
                        chunk_size=chunk_size,
                    )
                    removed = max(0, len(current_details) - len(batch_results))
                    if removed > 0:
                        total_removed += removed
                        logger.info(
                            "Lesson dedupe batches removed %d entries for %s (remaining %d)",
                            removed,
                            category.value,
                            len(batch_results),
                        )
                    current_details = batch_results
                    signature = _details_signature(current_details)
                    continue

                logger.info(
                    "Lesson dedupe aborted for %s: invalid response",
                    category.value,
                )
                return current_details

            new_signature = _details_signature(new_details)
            if new_signature == signature:
                if round_index == 1:
                    logger.info(
                        "Lesson dedupe found no duplicates in %s (%d lessons)",
                        category.value,
                        len(current_details),
                    )
                else:
                    logger.info(
                        "Lesson dedupe stabilised after %d rounds for %s (%d lessons)",
                        round_index,
                        category.value,
                        len(current_details),
                    )
                break

            removed = max(0, len(current_details) - len(new_details))
            total_removed += removed
            logger.info(
                "Lesson dedupe round %d for %s removed %d entries (remaining %d)",
                round_index,
                category.value,
                removed,
                len(new_details),
            )

            current_details = new_details
            signature = new_signature
        else:
            logger.info(
                "Reached max lesson dedupe rounds (%d) for %s; remaining %d lessons",
                max_rounds,
                category.value,
                len(current_details),
            )

        if total_removed > 0:
            logger.info(
                "Lesson dedupe removed %d total entries for %s (final %d)",
                total_removed,
                category.value,
                len(current_details),
            )

        return current_details

    def _invoke_dedupe_model(
        self,
        *,
        category: Category,
        details: list[LessonDetails],
        template: str,
    ) -> list[LessonDetails] | None:
        lessons_json = json.dumps(_details_to_payload(details), indent=2)
        payload = template.format(category=category.value, lessons_json=lessons_json)
        response = self._claude.invoke(
            payload,
            model=self._settings.dedupe_model,
            timeout=self._settings.dedupe_timeout,
        )
        if response is None:
            return None
        parsed = parse_response_json(response)
        if not isinstance(parsed, list):
            return None
        return _parse_dedupe_entries(parsed, category)

    def _dedupe_batches(
        self,
        *,
        category: Category,
        details: list[LessonDetails],
        template: str,
        chunk_size: int,
    ) -> list[LessonDetails]:
        batches = list(_chunk_details(details, chunk_size))
        if len(batches) <= 1:
            return details

        combined: list[LessonDetails] = []
        total_batches = len(batches)
        for index, batch in enumerate(batches, start=1):
            logger.info(
                "Lesson dedupe batch %d/%d for %s with %d candidates",
                index,
                total_batches,
                category.value,
                len(batch),
            )
            batch_result = self._invoke_dedupe_model(
                category=category,
                details=batch,
                template=template,
            )
            if batch_result is None:
                logger.info(
                    "Lesson dedupe batch %d for %s kept originals (invalid response)",
                    index,
                    category.value,
                )
                combined.extend(batch)
                continue

            removed = max(0, len(batch) - len(batch_result))
            if removed > 0:
                logger.info(
                    "Lesson dedupe batch %d for %s removed %d entries (remaining %d)",
                    index,
                    category.value,
                    removed,
                    len(batch_result),
                )
            combined.extend(batch_result)

        return combined


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


def _details_to_payload(details: list[LessonDetails]) -> list[dict[str, object]]:
    return [
        {
            "lesson_title": detail.lesson_title,
            "instruction": detail.instruction,
            "category": detail.category.value,
            "confidence": detail.confidence,
        }
        for detail in details
    ]


def _details_signature(details: list[LessonDetails]) -> tuple[tuple[str, str, str, float], ...]:
    return tuple(
        (
            detail.lesson_title.strip().casefold(),
            detail.instruction.strip(),
            detail.category.value,
            round(detail.confidence, 6),
        )
        for detail in details
    )


def _parse_dedupe_entries(
    entries: list[object],
    default_category: Category,
) -> list[LessonDetails] | None:
    deduped: list[LessonDetails] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_dict = cast("dict[str, object]", entry)
        try:
            raw_category = entry_dict.get("category", default_category.value)
            category_value = (
                Category(raw_category) if isinstance(raw_category, str) else default_category
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

    return deduped or None


def _chunk_details(details: list[LessonDetails], size: int) -> list[list[LessonDetails]]:
    if size <= 0:
        return [details]
    return [details[index : index + size] for index in range(0, len(details), size)]


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

            category_values = [cat.value for cat in Category if cat is not Category.OTHER]
            categories_text = ", ".join((*category_values, Category.OTHER.value))
            categories_enum = ", ".join(
                f'"{value}"' for value in (*category_values, Category.OTHER.value)
            )

            prompt_text = template.format(
                conversation_window=window,
                timestamp=iso_timestamp,
                user_correction=correction.prompt,
                classification=json.dumps(correction.classification, indent=2),
                categories=categories_text,
                categories_enumerated=categories_enum,
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

            if not payload.is_correction:
                failures.append({"timestamp": iso_timestamp, "error": "not_a_correction"})
                continue
            if not payload.is_general:
                failures.append({"timestamp": iso_timestamp, "error": "not_general"})
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
