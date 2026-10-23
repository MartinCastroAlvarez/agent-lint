"""Stable finding fingerprints: rule + file + normalised code context.

The line *number* is deliberately not part of the fingerprint, so adding a
function above an old finding does not turn it into a "new" one. Instead the
fingerprint hashes the normalised text of the offending line; identical
lines with the same rule in the same file are told apart by their order.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from agent_lint.models import Finding
from agent_lint.paths import read_text

VERSION = "v1"
_WS = re.compile(r"\s+")


def normalise(line: str) -> str:
    """Collapse whitespace so re-indenting or reformatting keeps the fingerprint."""
    return _WS.sub(" ", line).strip()


def _context(lines: list[str] | None, finding: Finding) -> str:
    if lines and 1 <= finding.line <= len(lines):
        text = normalise(lines[finding.line - 1])
        if text:
            return text
    # File-level findings (or files we cannot read): fall back to the rule only.
    return ""


def compute(tool: str, rule: str, path: str, context: str, occurrence: int) -> str:
    raw = "\0".join((VERSION, tool, rule, path, context, str(occurrence)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def assign(findings: Iterable[Finding], root: Path) -> list[Finding]:
    """Return the findings, sorted, each with its fingerprint filled in."""
    ordered = sorted(findings, key=lambda f: (f.path, f.line, f.column, f.tool, f.rule, f.message))
    cache: dict[str, list[str] | None] = {}
    seen: Counter[tuple[str, str, str, str]] = Counter()
    out: list[Finding] = []
    for finding in ordered:
        if finding.path not in cache:
            text = read_text(root, finding.path)
            cache[finding.path] = text.splitlines() if text is not None else None
        context = _context(cache[finding.path], finding)
        key = (finding.tool, finding.rule, finding.path, context)
        seen[key] += 1
        fp = compute(finding.tool, finding.rule, finding.path, context, seen[key])
        out.append(finding.with_fingerprint(fp))
    return out
