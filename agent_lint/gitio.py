"""Read-only git access: what changed since a base ref, and old file contents.

Hardening: every call disables external diff drivers, textconv filters and
fsmonitor hooks (all of which a repository's own ``.git/config`` could point
at an arbitrary program), refs are validated before use and passed after
``--end-of-options``, and blobs are read with ``cat-file`` (no filters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_lint import proc
from agent_lint.paths import MAX_READ_BYTES

_SAFE_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotepath=off",
    "-c",
    "diff.external=",
    "-c",
    "core.untrackedCache=false",
)
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~+-]{0,254}$")
_GIT_TIMEOUT = 60.0


class GitError(RuntimeError):
    """A git command failed or a ref is invalid."""


@dataclass(frozen=True, slots=True)
class Change:
    """A file that differs between the base ref and the working tree.

    ``status`` is ``A`` (added or untracked), ``M``, ``D`` or ``R`` (renamed,
    with ``old_path``). Type changes and copies are reported as ``M``/``A``.
    """

    status: str
    path: str
    old_path: str | None = None


class Git:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def toplevel(start: Path) -> Path | None:
        try:
            out = proc.run(
                ["git", *_SAFE_CONFIG, "rev-parse", "--show-toplevel"],
                start,
                timeout=_GIT_TIMEOUT,
            )
        except proc.ProcessError:
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return Path(out.stdout.strip())

    def _git(self, *args: str) -> str:
        try:
            out = proc.run(["git", *_SAFE_CONFIG, *args], self.root, timeout=_GIT_TIMEOUT)
        except proc.ProcessError as exc:
            raise GitError(str(exc)) from exc
        if out.returncode != 0:
            raise GitError(out.stderr.strip() or f"git {args[0]} failed")
        return out.stdout

    def resolve(self, ref: str) -> str:
        """Return the commit sha for ``ref`` or raise :class:`GitError`."""
        if not _REF_RE.match(ref) or ".." in ref:
            raise GitError(f"invalid git ref: {ref!r}")
        sha = self._git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
        sha = sha.strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise GitError(f"cannot resolve {ref!r}")
        return sha

    def tracked(self) -> list[str]:
        out = self._git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted({p for p in out.split("\0") if p})

    def changes(self, base_sha: str) -> list[Change]:
        """Files changed between ``base_sha`` and the working tree (plus untracked)."""
        out = self._git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--name-status",
            "-z",
            "-M",
            base_sha,
            "--",
        )
        parts = out.split("\0")
        changes: list[Change] = []
        i = 0
        while i < len(parts) and parts[i]:
            status = parts[i]
            kind = status[:1]
            if kind in ("R", "C") and i + 2 < len(parts):
                old, new = parts[i + 1], parts[i + 2]
                changes.append(Change("R" if kind == "R" else "A", new, old))
                i += 3
                continue
            if i + 1 >= len(parts):
                break
            path = parts[i + 1]
            changes.append(Change(kind if kind in ("A", "M", "D") else "M", path))
            i += 2
        untracked = self._git("ls-files", "-z", "--others", "--exclude-standard")
        seen = {c.path for c in changes}
        changes.extend(Change("A", p) for p in untracked.split("\0") if p and p not in seen)
        return sorted(changes, key=lambda c: c.path)

    def show(self, base_sha: str, path: str) -> str | None:
        """Contents of ``path`` at ``base_sha`` (``None`` if absent or too big)."""
        if not path or "\0" in path or path.startswith("-"):
            return None
        spec = f"{base_sha}:{path}"
        try:
            size = int(self._git("cat-file", "-s", spec).strip())
        except (GitError, ValueError):
            return None
        if size > MAX_READ_BYTES:
            return None
        try:
            return self._git("cat-file", "blob", spec)
        except GitError:
            return None
