# pre-commit

This repository ships a [`.pre-commit-hooks.yaml`](../../.pre-commit-hooks.yaml).
agent-lint runs whatever linters are on `PATH`, so the simplest setup is a
`local` hook in the project's own environment:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: agent-lint
        name: agent-lint
        entry: agent-lint check
        language: system
        pass_filenames: false
        always_run: true
```

Or install it from this repository into pre-commit's isolated environment,
together with the linters it should run:

```yaml
repos:
  - repo: https://github.com/MartinCastroAlvarez/agent-lint
    rev: v0.1.0
    hooks:
      - id: agent-lint
        additional_dependencies: [ruff, mypy]
```

The guards compare against `HEAD`, which in a pre-commit hook means "what this
commit changes". Removing the `agent-lint` hook from `.pre-commit-config.yaml`
is itself reported (AL302).
