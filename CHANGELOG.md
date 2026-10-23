# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### To do before the first PyPI release
- Register the PyPI pending publisher for `publish.yml` (environment `pypi`),
  then tag `v0.1.0`.

## [0.1.0] — unreleased

### Added
- `agent-lint init | check | ratchet | explain | tools | hook`.
- Runners for ruff, mypy, bandit, eslint, semgrep (local rules, SARIF) and
  shellcheck; each optional and auto-detected, or required via `tools`.
- Line-shift-proof fingerprints (tool + rule + file + normalised line).
- Baseline in `.agent-lint/baseline.json` that only shrinks (`ratchet`).
- Guards relative to a git base ref: AL201 suppressions, AL202 skipped/focused
  tests, AL203 deleted test files, AL204 removed tests, AL205 emptied tests,
  AL301 weakened ruff/mypy/bandit/coverage/pytest/eslint/tsconfig config,
  AL302 weakened agent-lint config or integration, AL401 baseline growth.
- Reports: text, JSON, Markdown (PR comment), SARIF 2.1.0.
- Claude Code hook (`PostToolUse` per edit, `Stop` full gate), pre-commit hook,
  composite GitHub Action with a sticky PR comment and optional SARIF upload.
