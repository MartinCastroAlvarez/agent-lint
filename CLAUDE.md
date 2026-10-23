# CLAUDE.md

Contract between this repository and any AI agent contributing to it.

## Required reading

1. This file. 2. [`README.md`](README.md). 3. [`ARCHITECTURE.md`](ARCHITECTURE.md).
4. [`SECURITY.md`](SECURITY.md).

## Mission

`agent-lint` is a gate that agents are meant to be unable to game. Every
change must keep it that way.

## The rules

1. **This repo is gated by itself.** Never add `# noqa`, `# type: ignore` or
   `# nosec`, skip a test, loosen `pyproject.toml` lint/type/coverage config,
   or add baseline entries. Fix the code.
2. **No shell, no evaluation.** Processes only via `proc.run`; project files
   are parsed (`ast`, `tokenize`, `tomllib`, `json`), never imported.
3. **No path escapes.** Anything from outside (linter output, hook payload,
   git, config) goes through `paths.relative` / `paths.safe_join`.
4. **Stdlib only** at run time.
5. **Every AL code** has a `catalog.py` entry, a README row and tests with a
   positive and a negative case.
6. **Every folder has a `README.md`** (package and test folders excepted).

## Done means

`poetry run pytest` (coverage ≥ 85%), `ruff check`, `ruff format --check`,
`mypy` (strict), `bandit`, `pip-audit`, `bash examples/demo.sh` and
`agent-lint check --base origin/main` all green; docs and CHANGELOG updated.

## Git

Small commits, imperative subjects, never force-push `main`, never commit
secrets, never bypass hooks.
