"""AL202-AL205: tests skipped, focused, deleted or emptied since the base ref.

Python test files are read with :func:`ast.parse` (parsed, never executed);
JavaScript/TypeScript test files with line patterns for the usual
Jest/Vitest/Mocha/Playwright APIs.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from agent_lint.fingerprint import normalise
from agent_lint.guards.base import DiffContext
from agent_lint.guards.base import unmatched
from agent_lint.guards.comments import JS_EXT
from agent_lint.guards.comments import PY_EXT
from agent_lint.models import Violation
from agent_lint.paths import matches

SKIPPED = "AL202"
DELETED = "AL203"
REMOVED = "AL204"
GUTTED = "AL205"

DEFAULT_TEST_GLOBS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "**/tests/**",
    "**/test/**",
    "**/__tests__/**",
    "*.test.*",
    "*.spec.*",
    "*_spec.*",
    "**/e2e/**",
)

_PY_SKIP_DECORATORS = {
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "mark.skip",
    "mark.skipif",
    "mark.xfail",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
    "unittest.expectedFailure",
    "skip",
    "skipIf",
    "skipUnless",
    "expectedFailure",
}
_PY_SKIP_CALLS = {"pytest.skip", "pytest.xfail", "pytest.importorskip", "self.skipTest", "skipTest"}

_JS_SKIP = re.compile(
    r"\b(?:(?:describe|it|test|context|suite|bench)\.(?:skip|only|todo|fixme)"
    r"|(?:xit|xdescribe|xtest|xcontext|fit|fdescribe|ftest))\s*\("
)
_JS_TEST = re.compile(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*([\"'`])(.*?)\1")
_JS_ASSERT = re.compile(r"\b(?:expect|assert)\s*[.(]")


def is_test_path(path: str, extra: tuple[str, ...] = ()) -> bool:
    return matches(path, (*DEFAULT_TEST_GLOBS, *extra))


@dataclass(frozen=True, slots=True)
class Marker:
    kind: str
    owner: str
    line: int
    text: str


@dataclass(slots=True)
class TestFile:
    tests: dict[str, int]  # qualified test name -> line
    asserts: dict[str, int]  # qualified test name -> number of assertions
    markers: list[Marker]


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _count_asserts(fn: ast.AST) -> int:
    count = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            name = _dotted(node.func).rsplit(".", 1)[-1]
            if name.startswith(("assert", "fail")) or name in ("raises", "warns", "approx"):
                count += 1
    return count


def parse_python(text: str) -> TestFile | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    result = TestFile({}, {}, [])

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                owner = f"{prefix}{node.name}"
                for dec in node.decorator_list:
                    _decorator(dec, owner)
                visit(node.body, f"{owner}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                owner = f"{prefix}{node.name}"
                for dec in node.decorator_list:
                    _decorator(dec, owner)
                if node.name.startswith("test"):
                    result.tests[owner] = node.lineno
                    result.asserts[owner] = _count_asserts(node)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and _dotted(sub.func) in _PY_SKIP_CALLS:
                        result.markers.append(
                            Marker("skip call", owner, sub.lineno, _dotted(sub.func))
                        )
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "pytestmark" in names and _mentions_skip(node.value):
                    result.markers.append(
                        Marker("module skip", f"{prefix}pytestmark", node.lineno, "pytestmark")
                    )
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = _dotted(node.value.func)
                if name in _PY_SKIP_CALLS:
                    result.markers.append(
                        Marker("module skip", f"{prefix}<module>", node.lineno, name)
                    )

    def _decorator(dec: ast.expr, owner: str) -> None:
        name = _dotted(dec)
        if name in _PY_SKIP_DECORATORS:
            result.markers.append(Marker("skip decorator", owner, dec.lineno, name))

    visit(tree.body, "")
    return result


def _mentions_skip(node: ast.AST) -> bool:
    return any(
        _dotted(sub) in _PY_SKIP_DECORATORS
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute | ast.Call)
    )


def parse_js(text: str) -> TestFile:
    result = TestFile({}, {}, [])
    current: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _JS_SKIP.finditer(line):
            result.markers.append(Marker("skip/only", normalise(line)[:120], lineno, m.group(0)))
        t = _JS_TEST.search(line)
        if t:
            current = t.group(2)
            name = current
            n = 2
            while name in result.tests:
                name = f"{current} #{n}"
                n += 1
            current = name
            result.tests[current] = lineno
            result.asserts[current] = 0
        if current is not None:
            result.asserts[current] += len(_JS_ASSERT.findall(line))
    return result


def parse(path: str, text: str) -> TestFile | None:
    if path.endswith(PY_EXT):
        return parse_python(text)
    if path.endswith(JS_EXT):
        return parse_js(text)
    return None


def check(ctx: DiffContext) -> list[Violation]:
    extra = ctx.config.tests
    out: list[Violation] = []
    for change in ctx.changes:
        was_test = is_test_path(change.old_path or change.path, extra)
        now_test = is_test_path(change.path, extra)
        if change.status == "D" and was_test:
            out.append(Violation(DELETED, change.path, 0, "test file deleted"))
            continue
        if change.status == "R" and was_test and not now_test:
            msg = f"test file moved out of the test suite (was {change.old_path})"
            out.append(Violation(DELETED, change.path, 0, msg))
            continue
        if not now_test or change.status == "D":
            continue
        new_text = ctx.new(change)
        new = parse(change.path, new_text) if new_text is not None else None
        if new is None:
            continue
        old_text = ctx.old(change)
        old = parse(change.path, old_text) if old_text else None
        old_markers = old.markers if old else []
        for m in unmatched(old_markers, new.markers, key=lambda m: (m.kind, m.owner, m.text)):
            out.append(
                Violation(SKIPPED, change.path, m.line, f"{m.text} added ({m.kind})", m.owner)
            )
        if old is None:
            continue
        missing = [name for name in old.tests if name not in new.tests]
        if missing and len(new.tests) < len(old.tests):
            for name in missing[: len(old.tests) - len(new.tests)]:
                out.append(Violation(REMOVED, change.path, 0, f"test `{name}` was removed", name))
        for name, count in new.asserts.items():
            if old.asserts.get(name, 0) > 0 and count == 0:
                out.append(
                    Violation(
                        GUTTED,
                        change.path,
                        new.tests.get(name, 0),
                        f"test `{name}` no longer asserts anything",
                        name,
                    )
                )
    return out
