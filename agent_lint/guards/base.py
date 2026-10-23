"""What every guard sees: the changed files and their before/after text."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from collections.abc import Hashable
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TypeVar

from agent_lint.config import Config
from agent_lint.gitio import Change
from agent_lint.gitio import Git
from agent_lint.paths import matches
from agent_lint.paths import read_text

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


@dataclass(slots=True)
class DiffContext:
    root: Path
    git: Git
    base: str
    changes: list[Change]
    config: Config
    _old: dict[str, str | None] = field(default_factory=dict)

    def old(self, change: Change) -> str | None:
        """The file at the base ref (following renames)."""
        if change.status == "A":
            return None
        path = change.old_path or change.path
        if path not in self._old:
            self._old[path] = self.git.show(self.base, path)
        return self._old[path]

    def new(self, change: Change) -> str | None:
        """The file in the working tree, ``None`` if deleted."""
        if change.status == "D":
            return None
        return read_text(self.root, change.path)

    def excluded(self, path: str) -> bool:
        return matches(path, self.config.exclude)


def unmatched(old: Iterable[T], new: Iterable[T], key: Callable[[T], K]) -> list[T]:
    """Items of ``new`` with no counterpart in ``old`` (multiset difference by key)."""
    pool: Counter[K] = Counter(key(o) for o in old)
    out = []
    for item in new:
        k = key(item)
        if pool[k] > 0:
            pool[k] -= 1
        else:
            out.append(item)
    return out
