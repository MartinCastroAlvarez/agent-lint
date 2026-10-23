"""Plain data types shared by runners, guards, the baseline and reports."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace

#: Exit codes of every command.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2


@dataclass(frozen=True, slots=True)
class Finding:
    """One diagnostic reported by a linter, normalised across tools.

    ``path`` is always a POSIX path relative to the project root. ``fingerprint``
    is empty until :func:`agent_lint.fingerprint.assign` fills it in.
    """

    tool: str
    rule: str
    path: str
    line: int
    column: int
    message: str
    severity: str = "error"
    fingerprint: str = ""

    def with_fingerprint(self, fingerprint: str) -> Finding:
        return replace(self, fingerprint=fingerprint)


@dataclass(frozen=True, slots=True)
class Violation:
    """A guard result: something an agent did to make the gate easier to pass."""

    code: str
    path: str
    line: int
    message: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class RunnerFailure:
    """A linter that was expected to run but crashed or produced garbage."""

    tool: str
    message: str


@dataclass(slots=True)
class Report:
    """The outcome of ``check`` / ``ratchet``."""

    new: list[Finding] = field(default_factory=list)
    fixed: list[Finding] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    failures: list[RunnerFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    base: str | None = None
    baseline_size: int = 0
    current_size: int = 0

    @property
    def passed(self) -> bool:
        return not (self.new or self.violations or self.failures)
