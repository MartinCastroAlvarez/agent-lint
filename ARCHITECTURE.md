# Architecture

## One-line summary

`engine.check` runs the configured linters through runners, fingerprints and
compares their findings with the committed baseline, and runs guards over the
git diff from a base ref; reports render the result for a terminal, a hook,
a PR comment or code scanning.

```
runners/* ──JSON/SARIF──▶ Finding ──fingerprint.assign──▶ engine.compare ◀── baseline.json
                                                              │
git base ref ──gitio.changes/show──▶ guards/* ──▶ Violation ──┤
                                                              ▼
                                       Report ──▶ report.{text,json,markdown,sarif}
                                         ▲                    │
                cli (init|check|ratchet|explain|tools)   hooks (Claude Code)
```

## Package layout

```
agent_lint/
├── cli.py          — argparse front end; exit codes 0/1/2
├── engine.py       — Project (root, config, git, file list), select runners,
│                     collect + compare, init / check / ratchet
├── models.py       — Finding, Violation, RunnerFailure, Report
├── config.py       — .agent-lint/config.toml or [tool.agent-lint]; strict keys
├── fingerprint.py  — tool + rule + path + normalised line + occurrence → sha256[:20]
├── baseline.py     — deterministic JSON, load/save (atomic, no symlinks)
├── paths.py        — relative/safe_join/read_text: nothing outside the root is read
├── proc.py         — the only subprocess call: argv list, shell=False, stdin closed, timeout
├── gitio.py        — hardened read-only git (no ext diff, no textconv, no fsmonitor)
├── report.py       — text / JSON / Markdown (escaped) / SARIF 2.1.0
├── catalog.py      — AL codes: title, why, fix (`agent-lint explain`)
├── hooks.py        — Claude Code PostToolUse / Stop protocol
├── runners/
│   ├── base.py     — Runner: binary lookup, argv, chunking, parse, failures
│   ├── python.py   — ruff, mypy, bandit
│   └── other.py    — eslint, semgrep (local rules, SARIF), shellcheck; parse_sarif
└── guards/
    ├── base.py         — DiffContext (old/new text per change), multiset `unmatched`
    ├── comments.py     — comments via tokenize (Python) / a scanner (JS) / `#` lines
    ├── suppressions.py — AL201
    ├── tests.py        — AL202–AL205 (ast for Python, patterns for JS/TS)
    ├── configs.py      — AL301/AL302: config files → policies → weakenings
    └── baseline.py     — AL401
```

## Decisions

- **Line numbers are not identity.** A finding is identified by what the line
  says, not where it is. Identical lines with the same rule in one file are
  numbered in order, so the baseline is a multiset.
- **Only tools that ran can fix entries.** If mypy is missing or crashes, its
  baseline entries stay; otherwise uninstalling a linter would "fix" everything.
- **Configured tools are required.** Listing `tools` turns a missing linter
  into a failure; auto-detect only warns.
- **Guards compare by content, not by diff hunks.** Each guard extracts a
  multiset from the old and new file (suppression comments, skip markers,
  test names, config policy) and reports what is new. Moving code around is
  never a violation; adding a suppression to a moved line is.
- **Configs are data.** TOML/INI/JSON(C) are parsed; `eslint.config.js` is never
  evaluated — added lines that look like relaxations are reported instead.
- **The gate protects itself.** AL302 (agent-lint config, CI workflow,
  pre-commit hook) and AL401 (baseline growth) cannot be disabled from config.
- **No runtime dependencies.** stdlib only (`tomllib`, `ast`, `tokenize`,
  `configparser`, `json`); the linters are whatever the project installs.
