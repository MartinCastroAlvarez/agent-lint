"""The runner protocol: find a linter, build its argv, parse its output."""

from __future__ import annotations

import json
import shutil
from abc import ABC
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import ClassVar

from agent_lint import proc
from agent_lint.config import Config
from agent_lint.models import Finding
from agent_lint.paths import relative
from agent_lint.paths import safe_join

#: Maximum number of file arguments per invocation (keeps argv under ARG_MAX).
CHUNK = 400


class RunnerError(RuntimeError):
    """The linter crashed, or its output could not be parsed."""


@dataclass(frozen=True, slots=True)
class Context:
    root: Path
    config: Config
    #: Every project file (git-tracked + untracked-not-ignored), POSIX relative.
    files: tuple[str, ...]


class Runner(ABC):
    name: ClassVar[str]
    executable: ClassVar[str]
    #: Extensions this linter cares about; used for detection and file filtering.
    extensions: ClassVar[tuple[str, ...]]
    #: Exit codes that mean "ran fine" (with or without findings).
    ok_codes: ClassVar[frozenset[int]] = frozenset({0, 1})
    #: Project-local places a binary may live (checked after ``$PATH``).
    local_bins: ClassVar[tuple[str, ...]] = (".venv/bin", "venv/bin")
    #: Whether the runner can be pointed at an explicit file list.
    accepts_files: ClassVar[bool] = True

    # ----------------------------------------------------------------- detect
    def binary(self, ctx: Context) -> str | None:
        configured = ctx.config.runner(self.name).executable
        if configured:
            if "/" in configured:
                full = safe_join(ctx.root, configured)
                return str(full) if full is not None and full.is_file() else None
            return shutil.which(configured)
        found = shutil.which(self.executable)
        if found:
            return found
        for folder in self.local_bins:
            full = safe_join(ctx.root, f"{folder}/{self.executable}")
            if full is not None and full.is_file():
                return str(full)
        return None

    def relevant(self, path: str) -> bool:
        return path.endswith(self.extensions)

    def applies(self, ctx: Context) -> bool:
        """Whether the project has anything for this runner to look at."""
        return any(self.relevant(p) for p in ctx.files)

    # -------------------------------------------------------------------- run
    def targets(self, ctx: Context, files: Sequence[str] | None) -> list[str] | None:
        """Explicit file arguments, or ``None`` for "the whole project"."""
        if files is None:
            return None
        return [f for f in files if self.relevant(f)]

    @staticmethod
    def _as_argument(path: str) -> str:
        # A file called "-rf.py" must never be read as an option.
        return f"./{path}" if path.startswith("-") else path

    @abstractmethod
    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        """The full command line for one invocation."""

    @abstractmethod
    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        """Normalise the tool's machine-readable output."""

    def run(self, ctx: Context, files: Sequence[str] | None = None) -> list[Finding]:
        binary = self.binary(ctx)
        if binary is None:
            raise RunnerError(f"{self.name}: executable not found")
        targets = self.targets(ctx, files)
        if targets is not None and not targets:
            return []
        batches: list[list[str] | None]
        if targets is None:
            batches = [None]
        else:
            safe = [self._as_argument(t) for t in targets]
            batches = [safe[i : i + CHUNK] for i in range(0, len(safe), CHUNK)]
        findings: list[Finding] = []
        for batch in batches:
            argv = self.argv(binary, ctx, batch)
            try:
                out = proc.run(argv, ctx.root, timeout=ctx.config.timeout)
            except proc.ProcessError as exc:
                raise RunnerError(f"{self.name}: {exc}") from exc
            if out.returncode not in self.ok_codes:
                detail = (out.stderr or out.stdout).strip().splitlines()[-5:]
                raise RunnerError(
                    f"{self.name} exited with {out.returncode}: " + " | ".join(detail)
                )
            findings.extend(self.parse(out.stdout, ctx))
        return findings

    # ---------------------------------------------------------------- helpers
    def finding(
        self,
        ctx: Context,
        *,
        rule: str,
        path: str,
        line: Any,
        column: Any,
        message: str,
        severity: str = "error",
    ) -> Finding | None:
        rel = relative(ctx.root, path)
        if rel is None:
            return None
        return Finding(
            tool=self.name,
            rule=rule or "unknown",
            path=rel,
            line=line if isinstance(line, int) and line > 0 else 0,
            column=column if isinstance(column, int) and column > 0 else 0,
            message=" ".join(str(message).split())[:1000],
            severity=severity,
        )


def load_json(stdout: str, tool: str) -> Any:
    """Parse a tool's JSON, tolerating banner lines before it."""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener in ("{", "["):
            idx = text.find(opener)
            if idx > 0:
                try:
                    return json.loads(text[idx:])
                except json.JSONDecodeError:
                    continue
    raise RunnerError(f"{tool}: output is not JSON")
