"""AL201: suppression comments added since the base ref.

Only *comments* are inspected (Python via :mod:`tokenize`, JS/TS with a
small scanner), so a string that merely mentions ``noqa`` is not a hit.
A suppression is "new" when its exact comment text was not already in the
file at the base ref, so moving or re-indenting an old one is fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_lint.fingerprint import normalise
from agent_lint.guards.base import DiffContext
from agent_lint.guards.base import unmatched
from agent_lint.guards.comments import comments
from agent_lint.models import Violation

CODE = "AL201"

#: (name, pattern) — matched against comment text only.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in (
        ("noqa", r"\bnoqa\b"),
        ("type: ignore", r"\btype:\s*ignore\b"),
        ("pyright: ignore", r"\bpyright:\s*ignore\b"),
        ("mypy: ignore-errors", r"\bmypy:\s*ignore-errors\b"),
        ("nosec", r"\bnosec\b"),
        ("pylint: disable", r"\bpylint:\s*disable\b"),
        ("pragma: no cover", r"\bpragma:\s*no\s*cover\b"),
        ("eslint-disable", r"\beslint-disable\b"),
        ("@ts-ignore", r"@ts-ignore\b"),
        ("@ts-nocheck", r"@ts-nocheck\b"),
        ("@ts-expect-error", r"@ts-expect-error\b"),
        ("biome-ignore", r"\bbiome-ignore\b"),
        ("coverage ignore", r"\b(?:istanbul|c8|v8)\s+ignore\b"),
        ("nosemgrep", r"\bnosemgrep\b"),
        ("shellcheck disable", r"\bshellcheck\s+disable="),
    )
)


@dataclass(frozen=True, slots=True)
class Suppression:
    name: str
    line: int
    comment: str


def find(path: str, text: str, allow: tuple[str, ...] = ()) -> list[Suppression]:
    out = []
    for line, comment in comments(path, text):
        if any(a in comment for a in allow):
            continue
        for name, rx in PATTERNS:
            if rx.search(comment):
                out.append(Suppression(name, line, normalise(comment)))
    return out


def check(ctx: DiffContext) -> list[Violation]:
    violations = []
    for change in ctx.changes:
        if change.status == "D" or ctx.excluded(change.path):
            continue
        new_text = ctx.new(change)
        if new_text is None:
            continue
        new = find(change.path, new_text, ctx.config.allow)
        if not new:
            continue
        old_text = ctx.old(change)
        old = find(change.path, old_text, ctx.config.allow) if old_text else []
        for s in unmatched(old, new, key=lambda s: (s.name, s.comment)):
            violations.append(
                Violation(
                    CODE,
                    change.path,
                    s.line,
                    f"new `{s.name}` suppression; fix the finding instead of silencing it",
                    s.comment[:200],
                )
            )
    return violations
