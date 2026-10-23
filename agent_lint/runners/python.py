"""ruff, mypy and bandit."""

from __future__ import annotations

import json
from typing import Any
from typing import ClassVar

from agent_lint.models import Finding
from agent_lint.paths import read_text
from agent_lint.runners.base import Context
from agent_lint.runners.base import Runner
from agent_lint.runners.base import RunnerError
from agent_lint.runners.base import load_json

_PY = (".py", ".pyi")


class Ruff(Runner):
    name = "ruff"
    executable = "ruff"
    extensions = _PY
    ok_codes: ClassVar[frozenset[int]] = frozenset({0})

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = ctx.config.runner(self.name).args
        # --force-exclude: explicit file arguments still honour the project's excludes.
        base = [binary, "check", "--output-format", "json", "--exit-zero", "--force-exclude"]
        return [*base, *args, *(targets if targets is not None else ["."])]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        data = load_json(stdout, self.name)
        if data is None:
            return []
        if not isinstance(data, list):
            raise RunnerError("ruff: expected a JSON list")
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") or {}
            f = self.finding(
                ctx,
                rule=item.get("code") or "syntax-error",
                path=str(item.get("filename", "")),
                line=loc.get("row"),
                column=loc.get("column"),
                message=str(item.get("message", "")),
            )
            if f:
                out.append(f)
        return out


class Mypy(Runner):
    name = "mypy"
    executable = "mypy"
    extensions = _PY

    def _configured_files(self, ctx: Context) -> bool:
        for cfg in ("mypy.ini", ".mypy.ini", "setup.cfg"):
            text = read_text(ctx.root, cfg)
            if text and "[mypy]" in text and "\nfiles" in text:
                return True
        text = read_text(ctx.root, "pyproject.toml") or ""
        return "[tool.mypy]" in text and "\nfiles" in text

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = ctx.config.runner(self.name).args
        base = [binary, "--output", "json", "--no-pretty", "--show-absolute-path"]
        if targets is not None:
            return [*base, *args, *targets]
        if args or self._configured_files(ctx):
            return [*base, *args]
        return [*base, "."]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        out = []
        for raw in stdout.splitlines():
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                item: Any = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RunnerError("mypy: unparsable JSON line") from exc
            if not isinstance(item, dict) or item.get("severity") == "note":
                continue
            f = self.finding(
                ctx,
                rule=item.get("code") or "misc",
                path=str(item.get("file", "")),
                line=item.get("line"),
                column=(item.get("column") or 0) + 1,
                message=str(item.get("message", "")),
            )
            if f:
                out.append(f)
        return out


class Bandit(Runner):
    name = "bandit"
    executable = "bandit"
    extensions = (".py",)

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = list(ctx.config.runner(self.name).args)
        pyproject = read_text(ctx.root, "pyproject.toml") or ""
        if "-c" not in args and "[tool.bandit]" in pyproject:
            args = ["-c", "pyproject.toml", *args]
        base = [binary, "-f", "json", "-q"]
        if targets is None:
            # Configured args (e.g. ["-r", "src"]) replace the default "-r .".
            return (
                [*base, *args] if ctx.config.runner(self.name).args else [*base, *args, "-r", "."]
            )
        return [*base, *args, *targets]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        data = load_json(stdout, self.name)
        if data is None:
            return []
        if not isinstance(data, dict):
            raise RunnerError("bandit: expected a JSON object")
        out = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("issue_severity", "")).lower()
            f = self.finding(
                ctx,
                rule=str(item.get("test_id") or "bandit"),
                path=str(item.get("filename", "")),
                line=item.get("line_number"),
                column=(item.get("col_offset") or 0) + 1,
                message=str(item.get("issue_text", "")),
                severity="error" if sev in ("high", "medium") else "warning",
            )
            if f:
                out.append(f)
        return out
