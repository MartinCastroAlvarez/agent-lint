"""Anti-cheating guards, all relative to a git base ref."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence

from agent_lint.guards import baseline
from agent_lint.guards import configs
from agent_lint.guards import suppressions
from agent_lint.guards import tests
from agent_lint.guards.base import DiffContext
from agent_lint.models import Violation

ALWAYS_ON = frozenset({configs.SELF, baseline.CODE})

GUARDS: tuple[tuple[str, Callable[[DiffContext], list[Violation]]], ...] = (
    ("suppressions", suppressions.check),
    ("tests", tests.check),
    ("configs", configs.check),
    ("baseline", baseline.check),
)


def run(ctx: DiffContext, files: Sequence[str] | None = None) -> list[Violation]:
    """Run every guard; with ``files``, only changes to those paths are considered."""
    if files is not None:
        wanted = set(files)
        ctx.changes = [c for c in ctx.changes if c.path in wanted or c.old_path in wanted]
    # The guards that protect the gate itself can never be switched off.
    disabled = set(ctx.config.disable) - ALWAYS_ON
    out: list[Violation] = []
    for _name, guard in GUARDS:
        out.extend(v for v in guard(ctx) if v.code not in disabled)
    return sorted(out, key=lambda v: (v.path, v.line, v.code, v.message))


__all__ = ["GUARDS", "DiffContext", "run"]
