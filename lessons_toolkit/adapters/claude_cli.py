"""Claude CLI adapter implementing the ClaudePort."""

from __future__ import annotations

import logging
import subprocess  # noqa: S404

from lessons_toolkit.ports import ClaudePort

logger = logging.getLogger(__name__)


class ClaudeCliAdapter(ClaudePort):
    """Invoke the claude CLI with a controlled flag set."""

    def __init__(
        self,
        *,
        binary: str,
        flags: tuple[str, ...],
    ) -> None:
        """Store the CLI binary path and reusable flag set."""
        self._binary = binary
        self._flags = list(flags)

    def invoke(self, prompt: str, *, model: str, timeout: int) -> str | None:
        """Execute the CLI and return stdout or ``None`` on failure.

        Returns:
            Trimmed stdout from the claude CLI, or ``None`` if execution fails.

        Raises:
            RuntimeError: If the claude binary cannot be located.

        """
        command = [self._binary, *self._flags, "--model", model]
        try:
            result = subprocess.run(  # noqa: S603 - controlled CLI invocation by design
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            message = (
                "claude CLI binary not found; ensure CLAUDE_BIN/LESSONS_CLAUDE_BIN is configured."
            )
            raise RuntimeError(message) from exc
        except subprocess.TimeoutExpired:
            logger.warning("claude CLI timed out after %s seconds", timeout)
            return None

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning("claude CLI exited with code %s: %s", result.returncode, stderr)
            return None

        return (result.stdout or "").strip()
