"""Render a :class:`~agent_lint.models.Report` as text, JSON, Markdown or SARIF."""

from __future__ import annotations

import json
from typing import Any

from agent_lint import __version__
from agent_lint.catalog import CATALOG
from agent_lint.models import Finding
from agent_lint.models import Report
from agent_lint.models import Violation

NEW = "AL100"
COMMENT_MARKER = "<!-- agent-lint-report -->"
_MD_LIMIT = 50


def _loc(path: str, line: int) -> str:
    return f"{path}:{line}" if line else path


def summary(report: Report) -> str:
    verdict = "passed" if report.passed else "FAILED"
    parts = [
        f"{len(report.new)} new",
        f"{len(report.violations)} guard violation(s)",
        f"{len(report.fixed)} fixed",
    ]
    if report.failures:
        parts.append(f"{len(report.failures)} tool failure(s)")
    tools = ", ".join(report.tools) or "none"
    return f"agent-lint {verdict}: {', '.join(parts)} (tools: {tools})"


def text(report: Report, *, limit: int | None = None) -> str:
    lines: list[str] = []
    for failure in report.failures:
        lines.append(f"TOOL   {failure.tool}: {failure.message}")
    for f in report.new[:limit]:
        lines.append(
            f"NEW    {_loc(f.path, f.line)}  {f.tool} {f.rule}  {f.message}  [{f.fingerprint}]"
        )
    for v in report.violations[:limit]:
        extra = f"  ({v.snippet})" if v.snippet and v.snippet not in v.message else ""
        lines.append(f"GUARD  {_loc(v.path, v.line)}  {v.code} {v.message}{extra}")
    hidden = 0
    if limit is not None:
        hidden = max(0, len(report.new) - limit) + max(0, len(report.violations) - limit)
    if hidden:
        lines.append(f"... and {hidden} more (run `agent-lint check`)")
    for w in report.warnings:
        lines.append(f"note: {w}")
    if report.fixed:
        lines.append(
            f"note: {len(report.fixed)} baseline finding(s) are fixed; "
            "run `agent-lint ratchet` to tighten the baseline"
        )
    lines.append(summary(report))
    if not report.passed:
        codes = sorted({v.code for v in report.violations} | ({NEW} if report.new else set()))
        lines.append("explain: " + " ".join(f"`agent-lint explain {c}`" for c in codes))
    return "\n".join(lines) + "\n"


def _finding_dict(f: Finding) -> dict[str, Any]:
    return {
        "tool": f.tool,
        "rule": f.rule,
        "path": f.path,
        "line": f.line,
        "column": f.column,
        "message": f.message,
        "severity": f.severity,
        "fingerprint": f.fingerprint,
    }


def _violation_dict(v: Violation) -> dict[str, Any]:
    return {
        "code": v.code,
        "path": v.path,
        "line": v.line,
        "message": v.message,
        "snippet": v.snippet,
    }


def as_json(report: Report) -> str:
    doc = {
        "passed": report.passed,
        "base": report.base,
        "tools": report.tools,
        "baseline_size": report.baseline_size,
        "current_size": report.current_size,
        "new": [_finding_dict(f) for f in report.new],
        "fixed": [_finding_dict(f) for f in report.fixed],
        "violations": [_violation_dict(v) for v in report.violations],
        "failures": [{"tool": x.tool, "message": x.message} for x in report.failures],
        "warnings": report.warnings,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _md_cell(value: str) -> str:
    """Untrusted text (linter messages, file content) as an inert table cell."""
    cleaned = " ".join(value.split()).replace("|", "\\|").replace("`", "'")
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;").replace("@", "@​")
    return cleaned[:300]


def markdown(report: Report) -> str:
    icon = "passed" if report.passed else "failed"
    lines = [
        COMMENT_MARKER,
        f"### agent-lint {icon}",
        "",
        summary(report).replace("agent-lint ", "", 1),
        "",
    ]
    if report.failures:
        lines += ["**Tool failures**", ""]
        lines += [f"- `{x.tool}`: {_md_cell(x.message)}" for x in report.failures]
        lines.append("")
    if report.new:
        lines += [
            "**New findings**",
            "",
            "| Location | Tool | Rule | Message |",
            "| --- | --- | --- | --- |",
        ]
        for f in report.new[:_MD_LIMIT]:
            loc, rule = _md_cell(_loc(f.path, f.line)), _md_cell(f.rule)
            lines.append(f"| `{loc}` | {f.tool} | `{rule}` | {_md_cell(f.message)} |")
        lines.append("")
    if report.violations:
        lines += ["**Guard violations**", "", "| Location | Code | What |", "| --- | --- | --- |"]
        for v in report.violations[:_MD_LIMIT]:
            title = CATALOG[v.code].title if v.code in CATALOG else v.code
            loc = _md_cell(_loc(v.path, v.line))
            lines.append(f"| `{loc}` | {v.code} ({title}) | {_md_cell(v.message)} |")
        lines.append("")
    hidden = max(0, len(report.new) - _MD_LIMIT) + max(0, len(report.violations) - _MD_LIMIT)
    if hidden:
        lines += [f"_{hidden} more not shown; see the SARIF upload or the job log._", ""]
    if report.fixed:
        lines += [f"{len(report.fixed)} baseline finding(s) fixed - run `agent-lint ratchet`.", ""]
    return "\n".join(lines)


def sarif(report: Report) -> str:
    """SARIF 2.1.0 for GitHub code scanning (new findings + guard violations)."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    def rule(rule_id: str, name: str, help_text: str) -> int:
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": name,
                "shortDescription": {"text": name},
                "help": {"text": help_text},
            }
        return list(rules).index(rule_id)

    def location(path: str, line: int, column: int = 0) -> dict[str, Any]:
        region: dict[str, Any] = {"startLine": max(line, 1)}
        if column:
            region["startColumn"] = column
        return {"physicalLocation": {"artifactLocation": {"uri": path}, "region": region}}

    for f in report.new:
        rid = f"{f.tool}/{f.rule}"
        idx = rule(rid, rid, f"New {f.tool} finding not present in the agent-lint baseline.")
        results.append(
            {
                "ruleId": rid,
                "ruleIndex": idx,
                "level": "error" if f.severity == "error" else "warning",
                "message": {"text": f.message or rid},
                "locations": [location(f.path, f.line, f.column)],
                "partialFingerprints": {"agentLint/v1": f.fingerprint},
            }
        )
    for v in report.violations:
        entry = CATALOG.get(v.code)
        idx = rule(v.code, entry.title if entry else v.code, entry.fix if entry else v.code)
        results.append(
            {
                "ruleId": v.code,
                "ruleIndex": idx,
                "level": "error",
                "message": {"text": v.message},
                "locations": [location(v.path, v.line)],
            }
        )
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "agent-lint",
                        "version": __version__,
                        "informationUri": "https://github.com/MartinCastroAlvarez/agent-lint",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


FORMATS = {"text": text, "json": as_json, "markdown": markdown, "sarif": sarif}
