from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from agent_lint import report as render
from agent_lint.models import Finding
from agent_lint.models import Report
from agent_lint.models import RunnerFailure
from agent_lint.models import Violation

PACKAGE = Path(__file__).parents[1] / "agent_lint"


def _report(n: int = 1) -> Report:
    return Report(
        new=[
            Finding("ruff", "F401", f"a{i}.py", i + 1, 2, "unused", fingerprint=f"fp{i}")
            for i in range(n)
        ],
        fixed=[Finding("mypy", "x", "b.py", 0, 0, "old", fingerprint="old")],
        violations=[
            Violation("AL201", "a.py", 3, "new `noqa` suppression", "# noqa"),
            Violation("AL999", "c.py", 0, "unknown code"),
        ],
        failures=[RunnerFailure("eslint", "exited with 2")],
        warnings=["something"],
        tools=["ruff"],
    )


def test_text_report_and_limit() -> None:
    out = render.text(_report(3), limit=1)
    assert "TOOL   eslint" in out and "NEW    a0.py:1" in out
    assert "a1.py" not in out and "more" in out
    assert "note: something" in out and "ratchet" in out
    assert "agent-lint explain AL100" in out
    assert (
        render.text(Report()).strip()
        == "agent-lint passed: 0 new, 0 guard violation(s), 0 fixed (tools: none)"
    )


def test_json_report() -> None:
    doc = json.loads(render.as_json(_report()))
    assert doc["passed"] is False
    assert doc["new"][0]["fingerprint"] == "fp0"
    assert doc["violations"][0]["code"] == "AL201"
    assert doc["failures"] == [{"tool": "eslint", "message": "exited with 2"}]


def test_sarif_report() -> None:
    doc = json.loads(render.sarif(_report()))
    run = doc["runs"][0]
    assert doc["version"] == "2.1.0"
    ids = [r["id"] for r in run["tool"]["driver"]["rules"]]
    assert ids == ["ruff/F401", "AL201", "AL999"]
    first = run["results"][0]
    assert first["partialFingerprints"] == {"agentLint/v1": "fp0"}
    assert first["locations"][0]["physicalLocation"]["region"] == {"startLine": 1, "startColumn": 2}
    # File-level violation still gets a valid (1-based) line.
    assert run["results"][2]["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_markdown_neutralises_hostile_messages() -> None:
    hostile = Report(
        new=[
            Finding(
                "eslint",
                "x|y",
                "a.js",
                1,
                1,
                "<img src=x onerror=alert(1)> | `code` @everyone\n## heading",
                fingerprint="f",
            )
        ]
        * 60,
    )
    md = render.markdown(hostile)
    assert md.startswith(render.COMMENT_MARKER)
    assert "<img" not in md and "&lt;img" in md
    assert "@everyone" not in md
    assert "\n## heading" not in md
    assert "x\\|y" in md
    assert "10 more not shown" in md
    ok = render.markdown(_report())
    assert "Tool failures" in ok and "Guard violations" in ok and "ratchet" in ok


# ------------------------------------------------------- security invariants


def _sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(PACKAGE.rglob("*.py"))]


def test_no_shell_eval_pickle_or_yaml() -> None:
    for path, text in _sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert isinstance(node.value, ast.Constant) and node.value.value is False, path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "compile", "__import__"), path
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(
                    n.split(".")[0] in ("pickle", "yaml", "marshal", "shelve") for n in names
                ), path
        assert "os.system" not in text and "os.popen" not in text, path


def test_only_proc_starts_processes() -> None:
    for path, text in _sources():
        if path.name == "proc.py":
            continue
        assert not re.search(r"^\s*import subprocess|from subprocess import", text, re.M), path


def test_project_code_is_parsed_never_imported() -> None:
    for path, text in _sources():
        assert "importlib" not in text, path
        assert "runpy" not in text, path
