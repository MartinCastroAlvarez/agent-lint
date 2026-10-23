"""Fixture repositories: real ``git init`` in a temp dir, nothing global touched."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_lint import guards
from agent_lint.config import Config
from agent_lint.config import load as load_config
from agent_lint.engine import Project
from agent_lint.gitio import Git
from agent_lint.models import Violation

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        )
        return out.stdout

    def write(self, path: str, text: str) -> Path:
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
        return full

    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def rm(self, path: str) -> None:
        (self.root / path).unlink()

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def project(self, config: Config | None = None) -> Project:
        return Project.open(self.root, config or load_config(self.root))

    def guards(self, base: str = "HEAD", config: Config | None = None) -> list[Violation]:
        git = Git(self.root)
        sha = git.resolve(base)
        cfg = config or load_config(self.root)
        ctx = guards.DiffContext(self.root, git, sha, git.changes(sha), cfg)
        return guards.run(ctx)


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("AGENT_LINT_BASE", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    # The dev linters (ruff, mypy, bandit) live next to the test interpreter.
    bindir = os.path.dirname(os.path.abspath(sys.executable))
    monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ.get("PATH", ""))
    yield


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "project"
    root.mkdir()
    r = Repo(root)
    r.git("init", "-q", "-b", "main")
    return r


def codes(violations: list[Violation]) -> list[str]:
    return [v.code for v in violations]
