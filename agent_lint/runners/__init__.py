"""Pluggable linter runners. Each one is optional and detected at run time."""

from __future__ import annotations

from agent_lint.runners.base import Context
from agent_lint.runners.base import Runner
from agent_lint.runners.base import RunnerError
from agent_lint.runners.other import Eslint
from agent_lint.runners.other import Semgrep
from agent_lint.runners.other import Shellcheck
from agent_lint.runners.other import parse_sarif
from agent_lint.runners.python import Bandit
from agent_lint.runners.python import Mypy
from agent_lint.runners.python import Ruff

RUNNERS: dict[str, type[Runner]] = {
    cls.name: cls for cls in (Ruff, Mypy, Bandit, Eslint, Semgrep, Shellcheck)
}

__all__ = ["RUNNERS", "Context", "Runner", "RunnerError", "parse_sarif"]
