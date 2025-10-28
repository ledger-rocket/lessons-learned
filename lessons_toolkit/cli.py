"""Command-line interface for lessons_toolkit."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from . import __version__
from .container import ToolkitContainer, parse_corrections
from .prompts import (
    extract_user_prompts,
    extract_user_prompts_from_messages,
    write_prompts_text,
)
from .settings import ToolkitSettings
from .state import resolve_cutoff, update_state_with_summary
from .transcripts import (
    collate_messages,
    find_session_files,
    format_as_json,
    format_as_text,
    list_projects,
)

logger = logging.getLogger(__name__)
DEFAULTS = ToolkitSettings()


class SubparserRegistry(Protocol):
    """Subset of the argparse subparser API used by this module."""

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser:  # noqa: ANN401
        """Create a sub-parser definition and return it."""
        ...


def _configure_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def _build_settings(**overrides: object) -> ToolkitSettings:
    settings = ToolkitSettings()
    sanitized: dict[str, object] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        if isinstance(value, Path):
            sanitized[key] = value.resolve()
        else:
            sanitized[key] = value
    if sanitized:
        settings = settings.model_copy(update=sanitized)
    return settings


def cmd_extract_prompts(args: argparse.Namespace) -> None:
    """Extract user prompts from JSON exports or Claude project logs.

    Raises:
        SystemExit: If Claude session data cannot be located or inputs are invalid.

    """
    if args.input is not None:
        prompts = extract_user_prompts(args.input)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        write_prompts_text(prompts, output)
        logger.info("Extracted %s prompts → %s", len(prompts), output)
        return

    project_id = args.project_id or DEFAULTS.claude_project_id
    if not project_id:
        projects = list_projects(args.sessions_dir or DEFAULTS.claude_projects_dir)
        available = ", ".join(projects) if projects else "<none found>"
        message = (
            "Provide --project-id or set LESSONS_CLAUDE_PROJECT_ID to target Claude logs. "
            f"Available projects: {available}"
        )
        raise SystemExit(message)

    base_dir = args.sessions_dir or DEFAULTS.claude_projects_dir
    session_files = find_session_files(base_dir, project_id=project_id)
    if not session_files:
        message = (
            f"No Claude session files found for project '{project_id}' in {base_dir}. "
            "Launch Claude Code at least once to populate logs."
        )
        raise SystemExit(message)

    since_text = args.since
    if not since_text:
        start_from_file = DEFAULTS.start_from_file
        if start_from_file.exists():
            loaded = start_from_file.read_text(encoding="utf-8").strip()
            if loaded:
                since_text = loaded
                logger.info(
                    "Using config/start_from.txt cutoff for prompt extraction: %s",
                    since_text,
                )

    since_dt: datetime | None = None
    if since_text:
        try:
            since_dt = datetime.fromisoformat(since_text.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - user input validation
            message = f"Invalid --since timestamp: {since_text}"
            raise SystemExit(message) from exc

    messages = collate_messages(session_files, session_id=args.session_id, since=since_dt)
    if not messages:
        logger.info("No Claude messages matched the provided filters; writing empty outputs.")

    prompts = extract_user_prompts_from_messages(messages)

    transcript_output = (args.transcript_output or DEFAULTS.transcript_file).resolve()
    transcript_output.parent.mkdir(parents=True, exist_ok=True)
    transcript_output.write_text(format_as_text(messages), encoding="utf-8")
    logger.info("Transcript written to %s", transcript_output)

    json_output = (args.json_output or DEFAULTS.transcript_json_file).resolve()
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(format_as_json(messages), encoding="utf-8")
    logger.info("Transcript JSON written to %s", json_output)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_prompts_text(prompts, output)
    logger.info("Extracted %s prompts → %s", len(prompts), output)


def cmd_classify(args: argparse.Namespace) -> None:
    """Classify prompts to locate corrections or frustration."""
    settings = _build_settings(
        prompts_file=args.prompts_file,
        corrections_file=args.output,
        classification_debug_file=args.debug_output,
        quick_model=args.model,
        max_workers=args.max_workers,
    )
    container = ToolkitContainer(settings)

    state_snapshot = container.state.load()
    start_from = container.start_from.read()
    since_dt = resolve_cutoff(args.since, state_snapshot, start_from)
    if since_dt:
        logger.info("Using cutoff timestamp: %s", since_dt.isoformat().replace("+00:00", "Z"))

    summary = container.create_classifier().classify(since=since_dt, limit=args.limit)
    logger.info(
        "Classified %s prompts (%s success / %s failed)",
        summary.total,
        summary.successful,
        summary.failed,
    )
    logger.info("Corrections/frustration detected: %s", summary.corrections_found)
    logger.info("Corrections written to %s", summary.corrections_path)
    if summary.debug_path:
        logger.info("Debug sample written to %s", summary.debug_path)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the full lesson pipeline and persist artefacts/state."""
    settings = _build_settings(
        prompts_file=args.prompts_file,
        transcript_file=args.transcript_file,
        lessons_raw_file=args.jsonl,
        lessons_json_file=args.json,
        lessons_markdown_file=args.markdown,
        quick_model=args.quick_model,
        full_model=args.full_model,
        dedupe_model=args.dedupe_model,
        max_workers=args.max_workers,
        context_before=args.before,
        context_after=args.after,
    )
    container = ToolkitContainer(settings)

    state_snapshot = container.state.load()
    start_from = container.start_from.read()
    since_dt = resolve_cutoff(args.since, state_snapshot, start_from)
    if since_dt:
        logger.info("Using cutoff timestamp: %s", since_dt.isoformat().replace("+00:00", "Z"))

    summary = container.create_pipeline().run(since=since_dt, limit=args.limit)
    logger.info(
        "Processed %s prompts → %s lessons (deduped %s)",
        summary.prompts_processed,
        summary.lessons_found,
        summary.total_lessons,
    )
    logger.info(
        "Artifacts:\n  JSONL: %s\n  JSON: %s\n  Markdown: %s",
        summary.jsonl_path,
        summary.json_path,
        summary.markdown_path,
    )

    updated_state = update_state_with_summary(state_snapshot, summary)
    container.state.save(updated_state)
    logger.info("State updated: %s", settings.state_file)


def cmd_extract_lessons(args: argparse.Namespace) -> None:
    """Regenerate lessons directly from stored corrections.

    Raises:
        SystemExit: If the corrections file cannot be located.

    """
    settings = _build_settings(
        transcript_file=args.transcript_file,
        lessons_extracted_file=args.output_json,
        lessons_markdown_file=args.output_markdown,
        full_model=args.model,
        context_before=args.before,
        context_after=args.after,
    )
    container = ToolkitContainer(settings)

    corrections_path = args.corrections_file.resolve()
    if not corrections_path.exists():
        message = f"Corrections file not found: {corrections_path}"
        raise SystemExit(message)

    corrections_data = json.loads(corrections_path.read_text(encoding="utf-8"))
    records = parse_corrections(corrections_data)
    summary = container.create_targeted_extractor().extract(records)
    logger.info(
        "Extracted lessons from %s corrections (%s success / %s failed)",
        summary.total_corrections,
        summary.successful,
        summary.failed,
    )
    if summary.failures:
        logger.info("Failures: %s", summary.failures)
    logger.info(
        "Artifacts:\n  JSON: %s\n  Markdown: %s",
        settings.lessons_extracted_file,
        settings.lessons_markdown_file,
    )


def cmd_sessions(args: argparse.Namespace) -> None:
    """Render Claude session JSONL logs to text or JSON.

    Raises:
        SystemExit: If timestamps are invalid or no matching files/messages exist.

    """
    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        except ValueError as exc:
            message = f"Invalid --since timestamp: {args.since}"
            raise SystemExit(message) from exc

    sessions_dir = args.sessions_dir or DEFAULTS.transcript_file.parent
    files = args.files or find_session_files(sessions_dir, project_id=args.project_id)
    if not files:
        message = "No session files found"
        raise SystemExit(message)

    messages = collate_messages(files, session_id=args.session_id, since=since_dt)
    if not messages:
        message = "No messages found matching criteria"
        raise SystemExit(message)

    output_text = format_as_json(messages) if args.format == "json" else format_as_text(messages)

    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
        logger.info("Transcript written to %s", args.output)
    else:
        sys.stdout.write(f"{output_text}\n")


def cmd_list_projects(args: argparse.Namespace) -> None:
    """List Claude Code projects discovered on disk."""
    base_dir = args.sessions_dir or DEFAULTS.claude_projects_dir
    projects = list_projects(base_dir)
    if not projects:
        logger.info("No Claude projects found under %s", base_dir)
        return

    for project in projects:
        sys.stdout.write(f"{project}\n")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse CLI parser.

    Returns:
        The fully configured top-level argument parser.

    """
    parser = argparse.ArgumentParser(prog="lessons-tk", description="Lessons Learned Toolkit CLI")
    parser.add_argument("--version", action="version", version=f"lessons-tk {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = cast("SubparserRegistry", parser.add_subparsers(dest="command", required=True))
    _configure_extract_prompts_subparser(subparsers)
    _configure_classify_subparser(subparsers)
    _configure_run_subparser(subparsers)
    _configure_extract_lessons_subparser(subparsers)
    _configure_transcripts_subparser(subparsers)
    _configure_projects_subparser(subparsers)

    return parser


def _configure_extract_prompts_subparser(
    subparsers: SubparserRegistry,
) -> None:
    """Register the extract-prompts subcommand."""
    prompts_parser = subparsers.add_parser(
        "extract-prompts",
        help=(
            "Extract user prompts from a Claude transcript JSON export or \n"
            "directly from the local Claude project logs"
        ),
    )
    prompts_parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Transcript JSON file (all_sessions.json)",
    )
    prompts_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULTS.prompts_file,
        help="Output text file",
    )
    prompts_parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=DEFAULTS.claude_projects_dir,
        help="Directory containing Claude project session logs",
    )
    prompts_parser.add_argument(
        "--project-id",
        default=DEFAULTS.claude_project_id,
        help="Claude project identifier (defaults to LESSONS_CLAUDE_PROJECT_ID)",
    )
    prompts_parser.add_argument("--session-id", help="Specific session identifier to filter")
    prompts_parser.add_argument(
        "--since",
        help="Only include messages at or after this ISO-8601 timestamp",
    )
    prompts_parser.add_argument(
        "--transcript-output",
        type=Path,
        help=(
            "Where to write the flattened text transcript "
            "(defaults to ToolkitSettings.transcript_file)"
        ),
    )
    prompts_parser.add_argument(
        "--json-output",
        type=Path,
        help=(
            "Where to write the flattened JSON transcript "
            "(defaults to ToolkitSettings.transcript_json_file)"
        ),
    )
    prompts_parser.set_defaults(func=cmd_extract_prompts)


def _configure_classify_subparser(
    subparsers: SubparserRegistry,
) -> None:
    """Register the classify subcommand."""
    classify_parser = subparsers.add_parser(
        "classify",
        help="Classify prompts for corrections/frustration",
    )
    classify_parser.add_argument("--prompts-file", type=Path, default=DEFAULTS.prompts_file)
    classify_parser.add_argument("--limit", type=int)
    classify_parser.add_argument("--max-workers", type=int, default=DEFAULTS.max_workers)
    classify_parser.add_argument("--model", default=DEFAULTS.quick_model)
    classify_parser.add_argument("--output", type=Path, default=DEFAULTS.corrections_file)
    classify_parser.add_argument(
        "--debug-output",
        type=Path,
        default=DEFAULTS.classification_debug_file,
    )
    classify_parser.add_argument("--since")
    classify_parser.set_defaults(func=cmd_classify)


def _configure_run_subparser(
    subparsers: SubparserRegistry,
) -> None:
    """Register the run subcommand."""
    run_parser = subparsers.add_parser("run", help="Run the unified lessons pipeline")
    run_parser.add_argument("--prompts-file", type=Path, default=DEFAULTS.prompts_file)
    run_parser.add_argument("--transcript-file", type=Path, default=DEFAULTS.transcript_file)
    run_parser.add_argument("--jsonl", type=Path, default=DEFAULTS.lessons_raw_file)
    run_parser.add_argument("--json", type=Path, default=DEFAULTS.lessons_json_file)
    run_parser.add_argument("--markdown", type=Path, default=DEFAULTS.lessons_markdown_file)
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--max-workers", type=int, default=DEFAULTS.max_workers)
    run_parser.add_argument("--quick-model", default=DEFAULTS.quick_model)
    run_parser.add_argument("--full-model", default=DEFAULTS.full_model)
    run_parser.add_argument("--dedupe-model", default=DEFAULTS.dedupe_model)
    run_parser.add_argument("--before", type=int, default=DEFAULTS.context_before)
    run_parser.add_argument("--after", type=int, default=DEFAULTS.context_after)
    run_parser.add_argument("--since")
    run_parser.set_defaults(func=cmd_run)


def _configure_extract_lessons_subparser(
    subparsers: SubparserRegistry,
) -> None:
    """Register the extract-lessons subcommand."""
    lessons_parser = subparsers.add_parser(
        "extract-lessons",
        help="Extract lessons from stored corrections",
    )
    lessons_parser.add_argument("--corrections-file", type=Path, default=DEFAULTS.corrections_file)
    lessons_parser.add_argument("--transcript-file", type=Path, default=DEFAULTS.transcript_file)
    lessons_parser.add_argument("--output-json", type=Path, default=DEFAULTS.lessons_extracted_file)
    lessons_parser.add_argument(
        "--output-markdown",
        type=Path,
        default=DEFAULTS.lessons_markdown_file,
    )
    lessons_parser.add_argument("--model", default=DEFAULTS.full_model)
    lessons_parser.add_argument("--before", type=int, default=DEFAULTS.context_before)
    lessons_parser.add_argument("--after", type=int, default=DEFAULTS.context_after)
    lessons_parser.set_defaults(func=cmd_extract_lessons)


def _configure_transcripts_subparser(
    subparsers: SubparserRegistry,
) -> None:
    """Register the transcripts subcommand."""
    sessions_parser = subparsers.add_parser("transcripts", help="Export Claude session transcripts")
    sessions_parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=DEFAULTS.claude_projects_dir,
    )
    sessions_parser.add_argument("--project-id", default=DEFAULTS.claude_project_id)
    sessions_parser.add_argument("--session-id")
    sessions_parser.add_argument("--since")
    sessions_parser.add_argument("--format", choices=["text", "json"], default="text")
    sessions_parser.add_argument("--output", type=Path)
    sessions_parser.add_argument("files", nargs="*", type=Path)
    sessions_parser.set_defaults(func=cmd_sessions)


def _configure_projects_subparser(subparsers: SubparserRegistry) -> None:
    """Register the projects listing subcommand."""
    projects_parser = subparsers.add_parser(
        "projects",
        help="List Claude Code projects discovered in ~/.claude/projects",
    )
    projects_parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=DEFAULTS.claude_projects_dir,
        help="Claude projects directory (defaults to ~/.claude/projects)",
    )
    projects_parser.set_defaults(func=cmd_list_projects)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
