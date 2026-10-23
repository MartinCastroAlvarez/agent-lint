"""Path handling that never leaves the project root.

Every path that comes from outside this package (a linter's output, git, a
Claude Code hook payload, the config file) goes through :func:`relative` or
:func:`safe_join` before it is read.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from pathlib import PurePosixPath

#: Files larger than this are never read for context or guards.
MAX_READ_BYTES = 2 * 1024 * 1024

_ALWAYS_SKIPPED = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache"}
)


def relative(root: Path, raw: str) -> str | None:
    """Return ``raw`` as a POSIX path relative to ``root``, or ``None``.

    ``raw`` may be absolute or relative to ``root``. Anything that resolves
    outside ``root`` (``..``, symlinks pointing out, other drives) is refused.
    """
    if not raw or "\x00" in raw:
        return None
    text = raw.replace("\\", "/")
    if text.startswith("file://"):
        text = text[len("file://") :]
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.normpath(candidate))
    try:
        real_root = root.resolve()
    except OSError:
        return None
    # Tools print either the path as given or the fully resolved one.
    for base in (root, real_root):
        try:
            rel = lexical.relative_to(base)
        except ValueError:
            continue
        posix = rel.as_posix()
        if posix in ("", ".") or posix == ".." or posix.startswith("../"):
            return None
        try:
            if not (real_root / rel).resolve().is_relative_to(real_root):
                return None  # a symlink pointing out of the project
        except OSError:
            return None
        return posix
    return None


def safe_join(root: Path, rel: str) -> Path | None:
    """Join a relative POSIX path onto ``root``; ``None`` if it would escape."""
    if not rel or "\x00" in rel:
        return None
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        return None
    full = root.joinpath(*pure.parts)
    try:
        resolved = full.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    return full


def read_text(root: Path, rel: str, limit: int = MAX_READ_BYTES) -> str | None:
    """Read a project file as text, or ``None`` if missing, too big or unsafe."""
    full = safe_join(root, rel)
    if full is None:
        return None
    try:
        if not full.is_file() or full.stat().st_size > limit:
            return None
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def walk(root: Path) -> list[str]:
    """List project files without git (skips VCS, virtualenvs, node_modules)."""
    found: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _ALWAYS_SKIPPED)
        for name in sorted(files):
            rel = relative(root, os.path.join(current, name))
            if rel is not None:
                found.append(rel)
    return found


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    pat = pattern.strip().replace("\\", "/")
    anchored = "/" in pat.rstrip("/")
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    # A directory pattern also matches everything below it.
    return re.compile(f"^{prefix}{body}(?:/.*)?$")


def matches(path: str, patterns: Iterable[str]) -> bool:
    """gitignore-style matching: a pattern without ``/`` matches at any depth."""
    return any(_glob_to_regex(p).match(path) for p in patterns if p.strip())
