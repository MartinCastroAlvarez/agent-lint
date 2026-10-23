"""eslint, semgrep (SARIF) and shellcheck, plus the shared SARIF reader."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from typing import ClassVar

from agent_lint.models import Finding
from agent_lint.paths import safe_join
from agent_lint.runners.base import Context
from agent_lint.runners.base import Runner
from agent_lint.runners.base import RunnerError
from agent_lint.runners.base import load_json

_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte")
_SH = (".sh", ".bash", ".ksh", ".bats")


class Eslint(Runner):
    name = "eslint"
    executable = "eslint"
    extensions = _JS
    local_bins = ("node_modules/.bin",)

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = ctx.config.runner(self.name).args
        base = [binary, "--format", "json", "--no-error-on-unmatched-pattern"]
        return [*base, *args, *(targets if targets is not None else ["."])]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        data = load_json(stdout, self.name)
        if data is None:
            return []
        if not isinstance(data, list):
            raise RunnerError("eslint: expected a JSON list")
        out = []
        for result in data:
            if not isinstance(result, dict):
                continue
            for msg in result.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                f = self.finding(
                    ctx,
                    rule=msg.get("ruleId") or ("fatal" if msg.get("fatal") else "eslint"),
                    path=str(result.get("filePath", "")),
                    line=msg.get("line"),
                    column=msg.get("column"),
                    message=str(msg.get("message", "")),
                    severity="error" if msg.get("severity") == 2 else "warning",
                )
                if f:
                    out.append(f)
        return out


def parse_sarif(data: Any, runner: Runner, ctx: Context) -> list[Finding]:
    """Read results from a SARIF 2.1.0 log (as written by semgrep and others)."""
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise RunnerError(f"{runner.name}: not a SARIF log")
    out = []
    for run in data["runs"]:
        if not isinstance(run, dict):
            continue
        for res in run.get("results") or []:
            if not isinstance(res, dict):
                continue
            locs = res.get("locations") or [{}]
            phys = (locs[0] or {}).get("physicalLocation") or {}
            uri = (phys.get("artifactLocation") or {}).get("uri", "")
            region = phys.get("region") or {}
            message = res.get("message") or {}
            level = res.get("level", "warning")
            f = runner.finding(
                ctx,
                rule=str(res.get("ruleId") or runner.name),
                path=str(uri),
                line=region.get("startLine"),
                column=region.get("startColumn"),
                message=str(message.get("text", "") if isinstance(message, dict) else message),
                severity="error" if level == "error" else "warning",
            )
            if f:
                out.append(f)
    return out


class Semgrep(Runner):
    """semgrep with a *local* rules file only: no registry downloads, no metrics."""

    name = "semgrep"
    executable = "semgrep"
    extensions = (*_JS, ".py", ".go", ".java", ".rb", ".rs", ".php", ".c", ".cpp", *_SH)
    configs: ClassVar[tuple[str, ...]] = (".semgrep.yml", ".semgrep.yaml", ".semgrep")

    def _config(self, ctx: Context) -> str | None:
        for name in self.configs:
            full = safe_join(ctx.root, name)
            if full is not None and full.exists():
                return name
        return None

    def applies(self, ctx: Context) -> bool:
        args = ctx.config.runner(self.name).args
        return ("--config" in args or self._config(ctx) is not None) and super().applies(ctx)

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = list(ctx.config.runner(self.name).args)
        if "--config" not in args:
            cfg = self._config(ctx)
            if cfg is None:
                raise RunnerError("semgrep: no local rules (.semgrep.yml) configured")
            args = ["--config", cfg, *args]
        base = [binary, "scan", "--sarif", "--metrics=off", "--disable-version-check", "--quiet"]
        return [*base, *args, *(targets if targets is not None else ["."])]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        data = load_json(stdout, self.name)
        return [] if data is None else parse_sarif(data, self, ctx)


class Shellcheck(Runner):
    name = "shellcheck"
    executable = "shellcheck"
    extensions = _SH

    def targets(self, ctx: Context, files: Sequence[str] | None) -> list[str] | None:
        # shellcheck has no recursive mode: always pass the files explicitly.
        pool = files if files is not None else ctx.files
        return [f for f in pool if self.relevant(f)]

    def argv(self, binary: str, ctx: Context, targets: list[str] | None) -> list[str]:
        args = ctx.config.runner(self.name).args
        return [binary, "-f", "json1", *args, *(targets or [])]

    def parse(self, stdout: str, ctx: Context) -> list[Finding]:
        data = load_json(stdout, self.name)
        if data is None:
            return []
        if not isinstance(data, dict):
            raise RunnerError("shellcheck: expected a JSON object")
        out = []
        for item in data.get("comments") or []:
            if not isinstance(item, dict):
                continue
            f = self.finding(
                ctx,
                rule=f"SC{item.get('code', '')}",
                path=str(item.get("file", "")),
                line=item.get("line"),
                column=item.get("column"),
                message=str(item.get("message", "")),
                severity="error" if item.get("level") == "error" else "warning",
            )
            if f:
                out.append(f)
        return out
