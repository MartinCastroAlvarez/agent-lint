#!/usr/bin/env bash
# The cheating demo: adopt agent-lint on a tiny project, then make the edits a
# coding agent makes to "get the build green" and watch every one get caught.
# Needs `agent-lint` and `ruff` on PATH. Runs in a temp dir; touches nothing else.
set -euo pipefail

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
git init -q -b main

mkdir -p .agent-lint tests
printf 'tools = ["ruff"]\n' > .agent-lint/config.toml
printf '[tool.ruff.lint]\nselect = ["E", "F", "B"]\n' > pyproject.toml
printf 'import os\n\n\ndef add(a, b):\n    return a + b\n' > app.py
cat > tests/test_app.py <<'PY'
from app import add


def test_add():
    assert add(1, 2) == 3


def test_add_negative():
    assert add(-1, -2) == -3
PY

echo "== 1. adopt: the existing finding (unused os) goes into the baseline"
agent-lint init
git add -A && git commit -qm "adopt agent-lint"
agent-lint check

echo
echo "== 2. the agent's shortcuts"
# A new bug, and another one silenced instead of fixed.
printf '\n\ndef total(items):\n    return sum(itmes)\n' >> app.py
printf '\n\ndef unused():\n    import sys  # noqa: F401\n' >> app.py
# A failing test, skipped instead of fixed; another one deleted.
cat > tests/test_app.py <<'PY'
import pytest

from app import add


@pytest.mark.skip(reason="flaky")
def test_add():
    assert add(1, 2) == 3
PY
# A rule family switched off.
printf '[tool.ruff.lint]\nselect = ["E", "F"]\nignore = ["E501"]\n' > pyproject.toml

status=0
agent-lint check || status=$?
if [ "$status" -ne 1 ]; then
  echo "expected the gate to fail (exit 1), got $status" >&2
  exit 1
fi
report="$(agent-lint check --format json || true)"
for code in F821 AL201 AL202 AL204 AL301; do
  grep -q "\"$code\"" <<< "$report" || { echo "missing $code" >&2; exit 1; }
done

echo
echo "== 3. fix the old finding and ratchet: the baseline only shrinks"
git checkout -q -- .
printf 'def add(a, b):\n    return a + b\n' > app.py
agent-lint ratchet
git add -A && git commit -qm "fix unused import, ratchet"
agent-lint check
echo
echo "demo: all shortcuts caught"
