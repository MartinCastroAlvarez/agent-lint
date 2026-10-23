from __future__ import annotations

from pathlib import Path

import pytest

from agent_lint.baseline import Baseline
from agent_lint.config import BASELINE_FILE
from agent_lint.config import CONFIG_FILE
from agent_lint.gitio import Git
from agent_lint.gitio import GitError
from agent_lint.guards import comments
from agent_lint.guards import suppressions
from agent_lint.guards import tests as test_guard
from agent_lint.models import Finding
from tests.conftest import Repo
from tests.conftest import codes

# --------------------------------------------------------------------- git


@pytest.mark.parametrize("ref", ["-x", "--output=/tmp/x", "a..b", "a b", "", "$(id)", "a;b"])
def test_resolve_refuses_hostile_refs(repo: Repo, ref: str) -> None:
    repo.commit()
    with pytest.raises(GitError):
        Git(repo.root).resolve(ref)


def test_resolve_and_changes(repo: Repo) -> None:
    repo.write("keep.py", "x = 1\n")
    repo.write("gone.py", "y = 1\n")
    repo.write("old_name.py", "z = 1\n" * 20)
    base = repo.commit()
    git = Git(repo.root)
    assert git.resolve("HEAD") == base
    assert git.resolve("main") == base
    repo.write("keep.py", "x = 2\n")
    repo.rm("gone.py")
    (repo.root / "old_name.py").rename(repo.root / "new_name.py")
    repo.write("untracked.py", "u = 1\n")
    repo.git("add", "new_name.py", "old_name.py")
    changes = {c.path: (c.status, c.old_path) for c in git.changes(base)}
    assert changes == {
        "gone.py": ("D", None),
        "keep.py": ("M", None),
        "new_name.py": ("R", "old_name.py"),
        "untracked.py": ("A", None),
    }
    assert git.show(base, "keep.py") == "x = 1\n"
    assert git.show(base, "untracked.py") is None
    assert git.show(base, "-bad") is None
    assert "untracked.py" in git.tracked()
    assert Git.toplevel(repo.root) is not None


def test_resolve_unknown_ref(repo: Repo) -> None:
    repo.commit()
    with pytest.raises(GitError):
        Git(repo.root).resolve("does-not-exist")


def test_git_does_not_run_repo_configured_programs(repo: Repo, tmp_path: Path) -> None:
    """A hostile .git/config cannot make agent-lint execute anything."""
    marker = tmp_path / "pwned"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    repo.write("a.py", "x = 1\n")
    repo.write(".gitattributes", "*.py diff=evil\n")
    base = repo.commit()
    repo.git("config", "diff.external", str(script))
    repo.git("config", "diff.evil.textconv", str(script))
    repo.git("config", "core.fsmonitor", str(script))
    repo.write("a.py", "x = 2\n")
    git = Git(repo.root)
    git.changes(base)
    git.show(base, "a.py")
    git.tracked()
    assert not marker.exists()


def test_non_git_directory(tmp_path: Path) -> None:
    assert Git.toplevel(tmp_path) is None


# ---------------------------------------------------------------- comments


def test_python_comments_ignore_strings() -> None:
    text = 'x = "# noqa"  # real noqa\ny = 1\n'
    assert comments.python_comments(text) == [(1, "# real noqa")]
    # Untokenizable input falls back to the line scanner.
    assert comments.python_comments("x = (\n# c\n") == [(2, "# c")]


def test_js_comments() -> None:
    text = 'const a = \'// no\';\n// one\nconst b = `/* no */`;\n/* two\n three */ x\nconst c = "a\\"//";\n'
    found = comments.js_comments(text)
    assert (2, "// one") in found
    assert (4, "/* two") in found
    assert (5, " three */") in found
    assert all("no" not in c for _, c in found)


def test_hash_comments_and_language() -> None:
    assert comments.hash_comments("echo '#x' # real\n#top\n") == [(1, "# real"), (2, "#top")]
    assert comments.language("a.py") == "python"
    assert comments.language("a.tsx") == "js"
    assert comments.language("run.sh") == "hash"
    assert comments.language("Dockerfile") == "hash"
    assert comments.language("a.md") is None
    assert comments.comments("a.md", "# x") == []


# ------------------------------------------------------------ suppressions


@pytest.mark.parametrize(
    ("path", "line", "name"),
    [
        ("a.py", "x = 1  # noqa: E501", "noqa"),
        ("a.py", "x: int = 's'  # type: ignore[assignment]", "type: ignore"),
        ("a.py", "x = 1  # pyright: ignore", "pyright: ignore"),
        ("a.py", "# mypy: ignore-errors", "mypy: ignore-errors"),
        ("a.py", "eval(x)  # nosec", "nosec"),
        ("a.py", "x = 1  # pylint: disable=foo", "pylint: disable"),
        ("a.py", "if x:  # pragma: no cover", "pragma: no cover"),
        ("a.ts", "// eslint-disable-next-line no-console", "eslint-disable"),
        ("a.ts", "/* eslint-disable */", "eslint-disable"),
        ("a.ts", "// @ts-ignore", "@ts-ignore"),
        ("a.ts", "// @ts-nocheck", "@ts-nocheck"),
        ("a.ts", "// @ts-expect-error", "@ts-expect-error"),
        ("a.ts", "// biome-ignore lint: x", "biome-ignore"),
        ("a.ts", "/* istanbul ignore next */", "coverage ignore"),
        ("a.py", "x = 1  # nosemgrep", "nosemgrep"),
        ("s.sh", "# shellcheck disable=SC2086", "shellcheck disable"),
    ],
)
def test_suppression_patterns(path: str, line: str, name: str) -> None:
    assert [s.name for s in suppressions.find(path, line + "\n")] == [name]


def test_new_suppressions_are_reported_moved_ones_are_not(repo: Repo) -> None:
    repo.write("a.py", "import os  # noqa: F401\nx = 1\n")
    repo.write("b.py", "s = '# noqa in a string'\n")
    repo.commit()
    # Moved + re-indented old suppression, a string mention, and one new suppression.
    repo.write("a.py", "\n\nimport os    # noqa: F401\nx = 1  # type: ignore\n")
    repo.write("b.py", "s = '# noqa in a string, still'\n")
    repo.write("c.py", "import sys  # noqa\n")
    found = repo.guards()
    assert [(v.code, v.path, v.line) for v in found] == [("AL201", "a.py", 4), ("AL201", "c.py", 1)]


def test_allow_list_and_exclude(repo: Repo) -> None:
    repo.commit()
    repo.write(
        CONFIG_FILE,
        'allow = ["type: ignore[import-untyped]"]\nexclude = ["vendor/**"]\n',
    )
    repo.commit()
    repo.write("a.py", "import x  # type: ignore[import-untyped]\n")
    repo.write("vendor/v.py", "import y  # noqa\n")
    assert repo.guards() == []


# ------------------------------------------------------------------- tests

BASE_TESTS = """
import pytest


def test_one():
    assert 1 == 1


def test_two():
    assert 2 == 2


class TestThing:
    def test_three(self):
        self.assertEqual(3, 3)
"""


def test_python_test_cheats(repo: Repo) -> None:
    repo.write("tests/test_a.py", BASE_TESTS)
    repo.write("tests/test_gone.py", "def test_x():\n    assert True\n")
    repo.write("src/test_helpers_not_tests.txt", "")
    repo.commit()
    repo.write(
        "tests/test_a.py",
        """
import pytest

pytestmark = pytest.mark.skipif(True, reason="later")


@pytest.mark.skip
def test_one():
    pass


class TestThing:
    @pytest.mark.xfail
    def test_three(self):
        pytest.skip("flaky")
        self.assertEqual(3, 3)
""",
    )
    repo.rm("tests/test_gone.py")
    found = repo.guards()
    got = sorted((v.code, v.snippet or v.message) for v in found)
    assert ("AL203", "test file deleted") in got
    assert ("AL204", "test_two") in got
    assert ("AL205", "test_one") in got
    assert sum(1 for c, _ in got if c == "AL202") == 4  # pytestmark, skip, xfail, pytest.skip()


def test_renamed_test_and_moved_file_are_fine(repo: Repo) -> None:
    repo.write("tests/test_a.py", "def test_old():\n    assert 1\n")
    repo.commit()
    repo.write("tests/test_a.py", "def test_new_name():\n    assert 1\n")
    assert repo.guards() == []


def test_test_moved_out_of_suite(repo: Repo) -> None:
    repo.write("tests/test_a.py", "def test_a():\n    assert 1\n" * 3)
    repo.commit()
    (repo.root / "tests/test_a.py").rename(repo.root / "attic.py")
    repo.git("add", "-A")
    assert codes(repo.guards()) == ["AL203"]


def test_js_test_cheats(repo: Repo) -> None:
    repo.write(
        "src/app.test.ts",
        "describe('app', () => {\n  it('works', () => {\n    expect(1).toBe(1);\n  });\n"
        "  it('also works', () => {\n    expect(2).toBe(2);\n  });\n});\n",
    )
    repo.commit()
    repo.write(
        "src/app.test.ts",
        "describe.only('app', () => {\n  it.skip('works', () => {\n  });\n  xit('new', () => {});\n});\n",
    )
    found = repo.guards()
    assert codes(found).count("AL202") == 3
    assert "AL204" in codes(found)


def test_custom_test_globs() -> None:
    assert test_guard.is_test_path("tests/x.py")
    assert test_guard.is_test_path("pkg/tests/x.py")
    assert test_guard.is_test_path("a/b_test.py")
    assert not test_guard.is_test_path("src/app.py")
    assert test_guard.is_test_path("checks/app.py", ("checks/**",))
    assert test_guard.parse("x.txt", "") is None
    assert test_guard.parse_python("def (") is None


def test_disable_guard_codes(repo: Repo) -> None:
    repo.write("tests/test_a.py", "def test_a():\n    assert 1\n")
    repo.write(CONFIG_FILE, 'disable = ["AL205", "AL401", "AL302"]\n')
    repo.commit()
    repo.write("tests/test_a.py", "def test_a():\n    pass\n")
    assert repo.guards() == []


# ---------------------------------------------------------------- baseline


def _baseline(*fps: str) -> str:
    return Baseline.from_findings(
        Finding("ruff", "E1", "a.py", 1, 1, "m", fingerprint=fp) for fp in fps
    ).dumps()


def test_baseline_may_shrink_but_not_grow(repo: Repo) -> None:
    repo.write(BASELINE_FILE, _baseline("aaa", "bbb"))
    repo.commit()
    repo.write(BASELINE_FILE, _baseline("aaa"))
    assert repo.guards() == []
    repo.write(BASELINE_FILE, _baseline("aaa", "ccc"))
    found = repo.guards()
    assert [(v.code, v.snippet) for v in found] == [("AL401", "ccc")]


def test_baseline_adoption_deletion_and_garbage(repo: Repo) -> None:
    repo.commit()
    repo.write(BASELINE_FILE, _baseline("aaa"))
    assert repo.guards() == []  # first adoption is allowed
    repo.commit()
    repo.write(BASELINE_FILE, "{oops")
    assert "unreadable" in repo.guards()[0].message
    repo.rm(BASELINE_FILE)
    assert "deleted" in repo.guards()[0].message
