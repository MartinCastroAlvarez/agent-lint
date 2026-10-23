"""The one place that starts processes: argv lists only, never a shell."""

from __future__ import annotations

# argv lists only; a shell is never used.
import subprocess  # nosec B404
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: Output beyond this is truncated before parsing (a runaway linter cannot OOM us).
MAX_OUTPUT_CHARS = 64 * 1024 * 1024


class ProcessError(RuntimeError):
    """The process could not be started or timed out."""


@dataclass(frozen=True, slots=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def run(
    argv: Sequence[str],
    cwd: Path,
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> Completed:
    """Run ``argv`` in ``cwd`` and capture its output.

    ``shell`` is always ``False`` and stdin is closed, so nothing in a path,
    a ref or a config value can be interpreted by a shell or wait for input.
    """
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ProcessError("argv must be a non-empty list of strings")
    if any("\x00" in a for a in argv):
        raise ProcessError("argv may not contain NUL bytes")
    try:
        proc = subprocess.run(  # nosec B603
            list(argv),
            cwd=cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as exc:
        raise ProcessError(f"{argv[0]}: not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProcessError(f"{argv[0]}: timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise ProcessError(f"{argv[0]}: {exc.strerror or exc}") from exc
    return Completed(
        proc.returncode,
        (proc.stdout or "")[:MAX_OUTPUT_CHARS],
        (proc.stderr or "")[:MAX_OUTPUT_CHARS],
    )
