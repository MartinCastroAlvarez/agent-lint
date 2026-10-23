"""What every agent-lint code means (``agent-lint explain CODE``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Entry:
    code: str
    title: str
    why: str
    fix: str


CATALOG: dict[str, Entry] = {
    e.code: e
    for e in (
        Entry(
            "AL100",
            "New lint finding",
            "A linter reported a finding whose fingerprint (tool + rule + file + "
            "normalised line) is not in the baseline: the change introduced it.",
            "Fix the code. If the finding is wrong, fix the linter config in a "
            "separate, human-reviewed change.",
        ),
        Entry(
            "AL201",
            "Suppression comment added",
            "A comment such as `# noqa`, `# type: ignore`, `# nosec`, `eslint-disable` "
            "or `@ts-ignore` appeared that was not in the file at the base ref. "
            "Silencing a linter is the cheapest way for an agent to turn a gate green.",
            "Remove the suppression and fix the finding. Legitimate, reviewed "
            "suppressions can be allow-listed with `allow = [...]` in the config.",
        ),
        Entry(
            "AL202",
            "Test skipped or focused",
            "A skip/xfail marker (`pytest.mark.skip`, `unittest.skip`, `pytest.skip()`, "
            "`it.skip`, `xit`) or a focus marker (`.only`, `fit`) was added to a test file. "
            "A focused test silently disables every other test in the file.",
            "Make the test pass instead of skipping it, and never commit `.only`.",
        ),
        Entry(
            "AL203",
            "Test file deleted",
            "A test file that existed at the base ref is gone (or was moved out of the "
            "test suite's paths).",
            "Restore it, or delete it in a human-reviewed change with a reason.",
        ),
        Entry(
            "AL204",
            "Test function removed",
            "A test file now has fewer test functions than at the base ref.",
            "Restore the test. Renames are fine: only a net loss of tests is reported.",
        ),
        Entry(
            "AL205",
            "Test emptied",
            "A test that used to assert something now contains no assertions, so it "
            "can never fail.",
            "Put the assertions back; fix the code under test.",
        ),
        Entry(
            "AL301",
            "Linter or test config weakened",
            "A rule was removed from ruff/eslint/mypy config, an ignore/exclude was "
            "added, a threshold was raised (line-length, complexity), a strictness "
            "flag was turned off, coverage `fail_under` lowered, or pytest options "
            "were added that deselect tests.",
            "Revert the config change; config loosening is a human decision.",
        ),
        Entry(
            "AL302",
            "agent-lint itself weakened",
            "agent-lint's own config dropped a tool or added an exclude/allow/disable, "
            "its base ref changed, or it was removed from a CI workflow or the "
            "pre-commit config. Cannot be disabled.",
            "Revert the change. Humans change the gate; agents do not.",
        ),
        Entry(
            "AL401",
            "Baseline loosened",
            "`.agent-lint/baseline.json` gained entries (or was deleted) relative to "
            "the base ref. The baseline may only shrink. Cannot be disabled.",
            "Revert the baseline file and fix the new findings; run "
            "`agent-lint ratchet` to shrink it after fixing old ones.",
        ),
    )
}
