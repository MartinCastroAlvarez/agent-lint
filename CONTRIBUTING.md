# Contributing to agent-lint

## TL;DR

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install poetry
poetry install
poetry run pytest
poetry run ruff check agent_lint tests && poetry run ruff format --check agent_lint tests
poetry run mypy agent_lint
poetry run bandit -c pyproject.toml -q -r agent_lint
poetry run pip-audit
poetry run bash examples/demo.sh
poetry run agent-lint check --base origin/main   # the gate, on itself
```

Open a PR against `main` from a `feat/…`, `fix/…`, `docs/…` or `chore/…` branch.

## What this package will not accept

- Running anything through a shell, or importing/evaluating project files.
- A runtime dependency where the stdlib will do.
- A way to grow the baseline other than `init --force`, or a way to disable
  AL302/AL401.
- Network calls (semgrep stays on local rules).

## What it does accept

- New runners (one class: `binary`, `argv`, `parse`; a canned-output test and a
  fake-executable test).
- New guards or new config formats for `guards/configs.py` (a policy
  extractor + a test with the weakening and the tightening case).
- New suppression patterns, test frameworks, integrations, docs.

## House rules

1. Tests alongside features; security-relevant changes get a hostile case.
2. Every new AL code gets a `catalog.py` entry and a README row.
3. No secrets in commits.

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in
   `agent_lint/__init__.py`; move `Unreleased` in `CHANGELOG.md` under the new version.
2. Merge, tag the merge commit `vX.Y.Z`, push the tag. `publish.yml` checks
   tag == version, runs the tests, scans the artefacts and publishes through
   PyPI Trusted Publishing (environment `pypi`), then creates the GitHub Release.
   The PyPI pending publisher must be registered first
   (owner `MartinCastroAlvarez`, repository `agent-lint`, workflow `publish.yml`, environment `pypi`).
