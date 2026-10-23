# Claude Code hooks

`agent-lint hook` is a [Claude Code hook](https://code.claude.com/docs/en/hooks)
command. It reads the hook event JSON on stdin and answers in the way each
event understands:

| Event | What agent-lint runs | On a problem |
| --- | --- | --- |
| `PostToolUse` (`Edit\|Write\|MultiEdit\|NotebookEdit`) | the fast runners (`hook_tools`, default ruff, eslint, shellcheck, bandit) and every guard, on **the file just written** | exit `2`: Claude Code shows stderr to Claude, which fixes it in the same turn (the edit has already happened) |
| `Stop` / `SubagentStop` | the full gate: every runner, every guard | prints `{"decision": "block", "reason": "..."}` so Claude keeps working instead of finishing |

If a `Stop` hook already blocked once in this cycle (`stop_hook_active`), it
lets Claude stop, so it can never loop. Usage errors (bad base ref, broken
config) never block a stop; a broken config does block a `PostToolUse`, because
the agent probably just broke it.

## Install

Merge [`settings.json`](settings.json) into your project's
`.claude/settings.json` (or print it with `agent-lint hook --print-config`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "agent-lint", "args": ["hook"], "timeout": 120 }]
      }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "agent-lint", "args": ["hook"], "timeout": 900 }] }
    ]
  }
}
```

The config uses the exec form (`command` + `args`): Claude Code spawns
`agent-lint` directly, with no shell in between. `agent-lint` must be on the
`PATH` Claude Code runs with (for example `pipx install agent-lint`, or
`uv tool install agent-lint`); otherwise use an absolute path in `command`.

## What the base ref is during a session

Guards compare against `base` from the config (`HEAD` by default), so during a
session they see the uncommitted work. If the agent commits as it goes, set
`base = "origin/main"` (or `AGENT_LINT_BASE`) so the guards see the whole
branch.

## Trust

The hook payload is treated as untrusted input: it is size-capped (5 MB), parsed
as JSON only, and the edited path is used only if it resolves inside the
project (`/etc/passwd`, `../x` and symlinks out are ignored).
