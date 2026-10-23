# GitHub Action

[`action.yml`](../../action.yml) at the root of this repository is a composite
action. It installs agent-lint, runs `agent-lint check` against the pull
request base (or the previous commit on a push), writes the Markdown summary to
the job summary, updates one sticky comment on the pull request, and can upload
SARIF to GitHub code scanning.

```yaml
name: agent-lint
on:
  pull_request:

permissions:
  contents: read
  pull-requests: write      # the summary comment
  security-events: write    # only with upload-sarif: "true"

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0     # the base commit must exist locally
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - run: pip install ruff mypy   # whatever your project lints with
      - uses: MartinCastroAlvarez/agent-lint@v0.1.0
        with:
          upload-sarif: "true"
```

| Input | Default | Meaning |
| --- | --- | --- |
| `base` | PR base sha / `github.event.before` | git ref to compare against |
| `args` | `""` | extra `agent-lint check` arguments |
| `install` | `"true"` | `pip install` agent-lint from the action's source |
| `comment` | `"true"` | sticky PR comment (skipped quietly on fork PRs) |
| `sarif` | `agent-lint.sarif` | where the SARIF file is written |
| `upload-sarif` | `"false"` | upload to code scanning (free for public repositories) |

Outputs: `exit-code` (0 passed, 1 failed, 2 error) and `sarif`.

Every input reaches the scripts through environment variables, never through
`${{ }}` inside a `run:` body, so an input cannot inject shell code.
Linter messages in the comment are escaped (no HTML, no mentions, no
table-breaking pipes).
