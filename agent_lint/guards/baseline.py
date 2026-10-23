"""AL401: the baseline file gained entries (or was deleted) since the base ref.

Adopting agent-lint (no baseline at the base ref) is allowed; after that the
baseline may only shrink.
"""

from __future__ import annotations

from agent_lint.baseline import BaselineError
from agent_lint.baseline import loads
from agent_lint.config import BASELINE_FILE
from agent_lint.guards.base import DiffContext
from agent_lint.models import Violation

CODE = "AL401"


def check(ctx: DiffContext) -> list[Violation]:
    for change in ctx.changes:
        if BASELINE_FILE not in (change.path, change.old_path):
            continue
        old_text = ctx.old(change)
        if old_text is None:
            return []  # first adoption
        if change.status == "D" or change.path != BASELINE_FILE:
            return [Violation(CODE, BASELINE_FILE, 0, "baseline deleted or moved")]
        new_text = ctx.new(change)
        try:
            old = loads(old_text)
            new = loads(new_text or "")
        except BaselineError as exc:
            return [Violation(CODE, BASELINE_FILE, 0, f"baseline unreadable: {exc}")]
        added = sorted(set(new.entries) - set(old.entries))
        return [
            Violation(
                CODE,
                BASELINE_FILE,
                0,
                f"baseline entry added: {new.entries[fp].tool} {new.entries[fp].rule} "
                f"in {new.entries[fp].path}",
                fp,
            )
            for fp in added
        ]
    return []
