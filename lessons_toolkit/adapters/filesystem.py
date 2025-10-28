"""Filesystem adapters for prompts, transcripts, and state."""

from __future__ import annotations

import json
import threading
import typing
from datetime import datetime
from pathlib import Path

from lessons_toolkit.models import (
    ClassificationResult,
    LessonDetails,
    LessonRecord,
    PromptRecord,
    StateSnapshot,
)
from lessons_toolkit.ports import (
    ClassificationRepository,
    LessonRepository,
    PromptRepository,
    PromptTemplateRepository,
    StartFromRepository,
    StateRepository,
    TranscriptSource,
)
from lessons_toolkit.timestamps import parse_timestamp

if typing.TYPE_CHECKING:
    from datetime import datetime

_LOCK = threading.Lock()


class FilesystemPromptTemplateRepository(PromptTemplateRepository):
    """Load prompt templates from disk with simple caching."""

    def __init__(self, directory: Path) -> None:
        """Initialise the repository for the given directory."""
        self._directory = Path(directory)
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        """Read template from disk; treat name without suffix as .txt file.

        Returns:
            The prompt template text.

        """
        cached = self._cache.get(name)
        if cached:
            return cached

        path = self._resolve_path(name)
        text = path.read_text(encoding="utf-8")
        self._cache[name] = text
        return text

    def _resolve_path(self, name: str) -> Path:
        path = self._directory / name
        if path.suffix:
            return path
        return self._directory / f"{name}.txt"


class FilesystemPromptRepository(PromptRepository):
    """Read user prompts from the generated text file."""

    def __init__(self, prompts_file: Path) -> None:
        """Store the prompts file path."""
        self._prompts_file = Path(prompts_file)

    def list_prompts(
        self,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[PromptRecord]:
        """Return parsed prompts sorted chronologically.

        Returns:
            The ordered list of prompt records.

        """
        prompts = list(self._iter_records())
        if since:
            prompts = [prompt for prompt in prompts if prompt.timestamp > since]

        if limit is not None:
            prompts = prompts[:limit]

        return prompts

    def _iter_records(self) -> typing.Iterable[PromptRecord]:
        """Yield prompt records extracted from the prompts file.

        Returns:
            An iterable of parsed prompt records.

        """
        if not self._prompts_file.exists():
            return []

        prompts: list[PromptRecord] = []
        current_timestamp: str | None = None
        current_lines: list[str] = []

        with self._prompts_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if line.startswith("[") and line.endswith("]"):
                    self._maybe_append(prompts, current_timestamp, current_lines)
                    current_timestamp = line[1:-1]
                    current_lines = []
                elif line:
                    current_lines.append(line)

        self._maybe_append(prompts, current_timestamp, current_lines)
        prompts.sort(key=lambda prompt: prompt.timestamp)
        return prompts

    def _maybe_append(
        self,
        prompts: list[PromptRecord],
        timestamp: str | None,
        lines: list[str],
    ) -> None:
        """Append a record to ``prompts`` if both timestamp and lines are present."""
        if timestamp and lines:
            record = self._build_record(timestamp, lines)
            if record:
                prompts.append(record)

    @staticmethod
    def _build_record(timestamp_text: str, lines: list[str]) -> PromptRecord | None:
        """Create a prompt record from accumulated lines.

        Returns:
            The constructed `PromptRecord`, or ``None`` when parsing fails.

        """
        try:
            timestamp = parse_timestamp(timestamp_text)
        except ValueError:
            return None
        content = "\n".join(lines).strip()
        if not content:
            return None
        return PromptRecord(timestamp=timestamp, content=content)


class FilesystemTranscriptSource(TranscriptSource):
    """Serve transcript windows from a plaintext transcript."""

    def __init__(self, transcript_file: Path) -> None:
        """Cache the transcript path."""
        self._transcript_file = Path(transcript_file)
        self._lines: list[str] | None = None

    def _ensure_loaded(self) -> None:
        """Load transcript lines once."""
        if self._lines is None:
            if not self._transcript_file.exists():
                self._lines = []
                return
            with self._transcript_file.open("r", encoding="utf-8") as handle:
                self._lines = handle.readlines()

    def fetch_window(
        self,
        timestamp: datetime,
        *,
        before: int,
        after: int,
    ) -> str | None:
        """Return the conversation window surrounding ``timestamp``.

        Returns:
            The extracted transcript window, or ``None`` when the timestamp is missing.

        """
        self._ensure_loaded()
        if not self._lines:
            return None

        line_num = self._find_line(timestamp)
        if line_num == -1:
            return None

        start = max(0, line_num - before)
        end = min(len(self._lines), line_num + after + 1)
        return "".join(self._lines[start:end])

    def _find_line(self, target: datetime) -> int:
        """Locate the line index matching ``target`` timestamp.

        Returns:
            The line index if found, otherwise ``-1``.

        Raises:
            RuntimeError: If transcript lines have not been loaded.

        """
        if self._lines is None:
            message = "Transcript lines not loaded; call fetch_window first"
            raise RuntimeError(message)
        for index, line in enumerate(self._lines):
            if not line.startswith("["):
                continue
            closing_bracket = line.find("]")
            if closing_bracket == -1:
                continue
            candidate = line[1:closing_bracket]
            try:
                candidate_ts = parse_timestamp(candidate)
            except ValueError:
                continue
            if candidate_ts == target:
                return index
        return -1


class FilesystemStateRepository(StateRepository):
    """JSON-backed state storage."""

    def __init__(self, path: Path) -> None:
        """Persist state to ``path``."""
        self._path = Path(path)

    def load(self) -> StateSnapshot:
        """Return the stored state snapshot or an empty default.

        Returns:
            The persisted `StateSnapshot`, or a new default when the file is absent or invalid.

        """
        if not self._path.exists():
            return StateSnapshot()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return StateSnapshot.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            return StateSnapshot()

    def save(self, snapshot: StateSnapshot) -> None:
        """Write the provided snapshot to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump(mode="json")
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class FilesystemStartFromRepository(StartFromRepository):
    """Read the initial cutoff timestamp from a text file."""

    def __init__(self, path: Path) -> None:
        """Initialise the repository with ``path``."""
        self._path = Path(path)

    def read(self) -> datetime | None:
        """Return the stored timestamp if present and valid.

        Returns:
            The parsed datetime, or ``None`` if not configured.

        """
        if not self._path.exists():
            return None
        text = self._path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        try:
            return parse_timestamp(text)
        except ValueError:
            return None


class FilesystemLessonRepository(LessonRepository):
    """Persist lessons to JSONL/JSON/Markdown artifacts."""

    def __init__(
        self,
        *,
        jsonl_path: Path,
        json_path: Path,
        markdown_path: Path,
        extracted_path: Path,
    ) -> None:
        """Prepare repository targets for lesson artefacts."""
        self._jsonl_path = Path(jsonl_path)
        self._json_path = Path(json_path)
        self._markdown_path = Path(markdown_path)
        self._extracted_path = Path(extracted_path)

    def prepare_run(self) -> None:
        """Ensure folders exist and reset JSONL."""
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._markdown_path.parent.mkdir(parents=True, exist_ok=True)
        self._extracted_path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl_path.write_text("", encoding="utf-8")

    def append_raw(self, record: LessonRecord) -> None:
        """Append a raw lesson record to the JSONL artefact."""
        payload = json.dumps(record.as_jsonable, ensure_ascii=False)
        with _LOCK, self._jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")

    def write_grouped(self, grouped: dict[str, list[LessonDetails]]) -> None:
        """Persist grouped lessons to the structured JSON artefact."""
        serializable: dict[str, list[dict[str, object]]] = {}
        for category, records in grouped.items():
            serializable[category] = [
                {
                    "lesson_title": item.lesson_title,
                    "instruction": item.instruction,
                    "category": item.category.value,
                    "confidence": item.confidence,
                }
                for item in records
            ]
        self._json_path.write_text(
            json.dumps(serializable, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_markdown(self, markdown: str) -> None:
        """Persist the Markdown summary."""
        self._markdown_path.write_text(markdown, encoding="utf-8")

    def write_extracted(self, records: list[LessonRecord]) -> None:
        """Persist the detailed lesson extraction output."""
        self._extracted_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [record.as_jsonable for record in records]
        self._extracted_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def jsonl_path(self) -> Path:
        """Return the JSONL artefact path."""
        return self._jsonl_path

    @property
    def json_path(self) -> Path:
        """Return the grouped JSON artefact path."""
        return self._json_path

    @property
    def markdown_path(self) -> Path:
        """Return the Markdown summary path."""
        return self._markdown_path

    @property
    def extracted_path(self) -> Path:
        """Return the detailed extraction artefact path."""
        return self._extracted_path


class FilesystemClassificationRepository(ClassificationRepository):
    """Persist classification corrections/debug artifacts."""

    def __init__(self, *, corrections_path: Path, debug_path: Path | None) -> None:
        """Configure output locations for classification artefacts."""
        self._corrections_path = Path(corrections_path)
        self._debug_path = Path(debug_path) if debug_path else None

    def write_corrections(self, corrections: typing.Iterable[ClassificationResult]) -> None:
        """Persist the subset of results representing corrections/frustrations."""
        payload: list[dict[str, object]] = []
        for result in corrections:
            if not (result.success and result.classification):
                continue
            if not (result.is_correction or result.is_frustrated):
                continue
            payload.append({
                "timestamp": result.prompt.iso_timestamp,
                "prompt": result.prompt.content,
                "classification": result.classification.model_dump(mode="json"),
                "success": True,
            })
        self._corrections_path.parent.mkdir(parents=True, exist_ok=True)
        self._corrections_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_debug_sample(self, results: list[ClassificationResult], sample_size: int = 5) -> None:
        """Persist a sample of classification results for debugging."""
        if not self._debug_path:
            return
        sample = results[:sample_size]
        payload = [
            {
                "timestamp": result.prompt.iso_timestamp,
                "prompt": result.prompt.content,
                "success": result.success,
                "classification": result.classification.model_dump(mode="json")
                if result.classification
                else None,
                "error": result.error,
            }
            for result in sample
        ]
        self._debug_path.parent.mkdir(parents=True, exist_ok=True)
        self._debug_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def corrections_path(self) -> Path:
        """Location of the persisted corrections artefact."""
        return self._corrections_path

    @property
    def debug_path(self) -> Path | None:
        """Location of the debug artefact if configured."""
        return self._debug_path
