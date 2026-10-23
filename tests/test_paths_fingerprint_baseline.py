from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_lint import baseline as bl
from agent_lint.config import BASELINE_FILE
from agent_lint.fingerprint import assign
from agent_lint.fingerprint import normalise
from agent_lint.models import Finding
from agent_lint.paths import matches
from agent_lint.paths import read_text
from agent_lint.paths import relative
from agent_lint.paths import safe_join
from agent_lint.paths import walk

# ------------------------------------------------------------------ paths


def test_relative_accepts_inside_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    assert relative(tmp_path, "pkg/a.py") == "pkg/a.py"
    assert relative(tmp_path, str(tmp_path / "pkg" / "a.py")) == "pkg/a.py"
    assert relative(tmp_path, "./pkg/../pkg/a.py") == "pkg/a.py"
    assert relative(tmp_path, "pkg\\a.py") == "pkg/a.py"
    assert relative(tmp_path, f"file://{tmp_path}/pkg/a.py") == "pkg/a.py"


@pytest.mark.parametrize("raw", ["", "../x.py", "/etc/passwd", "a/../../x", "a\x00b", "."])
def test_relative_refuses_escapes(tmp_path: Path, raw: str) -> None:
    assert relative(tmp_path, raw) is None


def test_relative_refuses_symlink_out(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside)
    assert relative(tmp_path, "link/secret.txt") is None


def test_safe_join_and_read_text(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    assert safe_join(tmp_path, "a.txt") == tmp_path / "a.txt"
    assert safe_join(tmp_path, "../a.txt") is None
    assert safe_join(tmp_path, "/abs") is None
    assert safe_join(tmp_path, "") is None
    assert read_text(tmp_path, "a.txt") == "hello"
    assert read_text(tmp_path, "missing.txt") is None
    assert read_text(tmp_path, "a.txt", limit=2) is None
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "evil").symlink_to(outside)
    assert read_text(tmp_path, "evil") is None


def test_walk_skips_vcs_and_venvs(tmp_path: Path) -> None:
    for p in ("a.py", ".git/config", ".venv/bin/x", "node_modules/m/i.js", "src/b.py"):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text("", encoding="utf-8")
    assert walk(tmp_path) == ["a.py", "src/b.py"]


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("tests/test_a.py", ["tests/**"], True),
        ("pkg/tests/test_a.py", ["tests/**"], False),
        ("pkg/tests/test_a.py", ["**/tests/**"], True),
        ("pkg/test_a.py", ["test_*.py"], True),
        ("pkg/migrations/0001.py", ["migrations/"], True),
        ("migrations/0001.py", ["migrations/"], True),
        ("a/b/c.js", ["*.js"], True),
        ("a/b/c.js", ["a/*.js"], False),
        ("a/b/c.js", ["a/**/*.js"], True),
        ("x.py", ["", "  "], False),
        ("a.spec.ts", ["*.spec.*"], True),
        ("vendor/x/y.py", ["vendor"], True),
        ("doc?.md", ["doc?.md"], True),
    ],
)
def test_matches(path: str, patterns: list[str], expected: bool) -> None:
    assert matches(path, patterns) is expected


# ------------------------------------------------------------ fingerprints


def _f(line: int, rule: str = "E1", path: str = "a.py", tool: str = "ruff") -> Finding:
    return Finding(tool, rule, path, line, 1, "msg")


def test_fingerprint_survives_line_shifts(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny =  2\n", encoding="utf-8")
    before = assign([_f(2)], tmp_path)[0].fingerprint
    (tmp_path / "a.py").write_text("import os\n\n\nx = 1\n    y = 2\n", encoding="utf-8")
    after = assign([_f(5)], tmp_path)[0].fingerprint
    assert before == after


def test_fingerprint_distinguishes_rule_path_tool_and_duplicates(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    fps = [
        f.fingerprint
        for f in assign(
            [_f(1), _f(2), _f(1, rule="E2"), _f(1, path="b.py"), _f(1, tool="mypy")], tmp_path
        )
    ]
    assert len(set(fps)) == 5


def test_fingerprint_without_readable_context(tmp_path: Path) -> None:
    fp = assign([_f(10, path="missing.py")], tmp_path)[0].fingerprint
    assert len(fp) == 20
    assert normalise("  a \t  b  ") == "a b"


# --------------------------------------------------------------- baseline


def test_baseline_round_trip_is_deterministic(tmp_path: Path) -> None:
    findings = assign([_f(1, "B"), _f(1, "A")], tmp_path)
    b = bl.Baseline.from_findings(findings)
    path = bl.save(tmp_path, b)
    assert path == tmp_path / BASELINE_FILE
    text = path.read_text(encoding="utf-8")
    loaded = bl.load(tmp_path)
    assert loaded is not None
    assert loaded.dumps() == text
    assert len(loaded) == 2
    assert [e["rule"] for e in json.loads(text)["findings"]] == ["A", "B"]
    smaller = loaded.without([findings[0].fingerprint])
    assert len(smaller) == 1


def test_baseline_missing_and_invalid(tmp_path: Path) -> None:
    assert bl.load(tmp_path) is None
    for bad in (
        "nope",
        "[]",
        '{"schema": 2, "findings": []}',
        '{"schema": 1, "findings": {}}',
        '{"schema": 1, "findings": [1]}',
        '{"schema": 1, "findings": [{"tool": "ruff"}]}',
    ):
        with pytest.raises(bl.BaselineError):
            bl.loads(bad)


def test_baseline_refuses_symlink_write(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / ".agent-lint").mkdir()
    (tmp_path / BASELINE_FILE).symlink_to(target)
    with pytest.raises(bl.BaselineError):
        bl.save(tmp_path, bl.Baseline.empty())
