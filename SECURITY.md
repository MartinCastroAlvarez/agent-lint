# Security

agent-lint is a gate. It runs inside agent sessions, pre-commit hooks and CI,
on repositories whose contents (code, config, git metadata) may have been
written by an AI agent. The design goal: nothing in the repository can make
agent-lint run a program it did not intend to run, read outside the project,
or report a pass it should not.

## Reporting a vulnerability

Open a private security advisory at
<https://github.com/MartinCastroAlvarez/agent-lint/security/advisories/new>.
Do **not** open a public issue for a suspected vulnerability. We acknowledge
within 5 business days and aim to ship a fix within 30 days; you are credited
in the advisory unless you ask otherwise.

Supported versions: the latest minor release.

## 1. Threat model

**Assets.** The integrity of the gate's verdict; the machine it runs on (a
developer laptop, a Claude Code session, a CI runner with a token); the PR
conversation it comments on.

**Actors.**

| Actor | Controls | Must not be able to |
| --- | --- | --- |
| A coding agent editing the repo | source, tests, linter config, `.agent-lint/`, `.git/config` in its sandbox, hook payload timing | pass the gate by silencing, skipping, loosening or re-baselining; make agent-lint execute something |
| A contributor opening a PR | the PR's files and git objects | inject shell into the GitHub Action, HTML/mentions into the PR comment |
| Linter output | messages, file paths | point agent-lint at files outside the root, crash it into a pass |
| Claude Code hook payload | `tool_input.file_path`, `cwd` | make agent-lint read or report on files outside the project |

**Threats and controls.**

| Threat | Control |
| --- | --- |
| Shell injection via file names, refs, config values, action inputs | `proc.run` is the only process call: argv list, `shell=False`, stdin closed, timeout. File arguments starting with `-` are passed as `./-name`. Git refs are validated by regex, ranges refused, passed after `--end-of-options`. Action inputs reach scripts only via `env:`. A test asserts no `shell=True`, `eval`, `exec`, `pickle`, `yaml`, `os.system` in the package. |
| Repository-configured programs (`diff.external`, `textconv`, `core.fsmonitor`) | every git call sets `-c core.fsmonitor=false -c diff.external=` and `--no-ext-diff --no-textconv`; blobs are read with `git cat-file blob`. A test with a hostile `.git/config` proves nothing runs. |
| Executing project code | agent-lint parses Python with `ast`/`tokenize` and configs with `tomllib`/`configparser`/`json`; it never imports or evaluates project files (`eslint.config.js` is diffed as text). A test scans for `importlib`/`runpy`. Note: the **linters you enable** may load project code (eslint configs are JS, mypy plugins are Python) — the same trust as running them yourself. |
| Path traversal (linter output, hook payload, config) | `paths.relative` / `safe_join` resolve symlinks and refuse anything outside the root; findings outside it are dropped; reads are size-capped (2 MB per file, 20 MB baseline, 5 MB hook payload); the baseline is written atomically and never through a symlink. |
| Gaming the gate | suppressions (AL201), skipped/deleted/gutted tests (AL202–AL205), weakened config (AL301), weakened agent-lint config or CI/pre-commit integration (AL302), a growing baseline (AL401). AL302/AL401 cannot be disabled. A linter that crashes, times out or prints non-JSON is a failure. A required tool that is missing is a failure; a missing auto-detected tool never "fixes" its baseline entries. |
| Hostile text in the PR comment | Markdown cells are whitespace-collapsed; `|`, backticks, `<`/`>` and `@` are neutralised; output is capped at 50 rows. |
| Network / cost | agent-lint makes no network calls. semgrep runs only with local rule files, `--metrics=off`, no version check. |

**Out of scope.** Linters' own vulnerabilities; an operator who disables the
gate in CI (that is a human decision — though AL302 reports it in the diff);
heuristics being incomplete (a determined agent can find edits these guards do
not model — review still matters).

## 2. Non-negotiable rules

Enforced by `tests/test_report_security.py` and `tests/test_git_and_guards.py`.

1. Processes start only in `proc.py`, with an argv list and `shell=False`.
2. No `eval`, `exec`, `pickle`, `yaml`, `marshal`, `importlib`, `runpy`.
3. Every external path goes through `paths.relative` or `paths.safe_join`.
4. git is called only through `gitio.Git` with the hardening flags.
5. The baseline can only shrink outside `init --force`.

## 3. Prompt-injection notes

Hook feedback and the `Stop` reason are shown to the agent. They contain
linter messages and source snippets, which an attacker who controls the code
could word as instructions. agent-lint quotes them as data inside a fixed
report format, caps their length, and the reason text tells the agent which
shortcuts are not allowed; treat the agent's response to it as untrusted like
any other agent output.
