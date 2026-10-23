"""Claude Code hook entry point (``agent-lint hook``).

* ``PostToolUse`` (Edit/Write/MultiEdit/NotebookEdit): checks just the file
  that was written with the fast runners (``hook_tools``) and the guards.
  On a problem it exits 2 so Claude Code shows stderr to Claude, which then
  fixes it in the same turn. The edit itself already happened.
* ``Stop``: runs the full gate. On a problem it prints
  ``{"decision": "block", "reason": ...}`` so Claude keeps working instead
  of ending the turn. If the hook already blocked once in this stop cycle
  (``stop_hook_active``), it lets Claude stop, so it can never loop.

The payload is untrusted input: it is size-capped, parsed as JSON, and the
file path is accepted only if it resolves inside the project root.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from typing import TextIO

from agent_lint import engine
from agent_lint import report as render
from agent_lint.config import load as load_config
from agent_lint.gitio import Git
from agent_lint.models import EXIT_OK
from agent_lint.paths import relative

MAX_PAYLOAD = 5 * 1024 * 1024
BLOCK = 2
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_FEEDBACK_LIMIT = 20

SETTINGS: dict[str, Any] = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                "hooks": [
                    {"type": "command", "command": "agent-lint", "args": ["hook"], "timeout": 120}
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {"type": "command", "command": "agent-lint", "args": ["hook"], "timeout": 900}
                ]
            }
        ],
    }
}


def settings_json() -> str:
    return json.dumps(SETTINGS, indent=2) + "\n"


def _payload(stdin: TextIO) -> dict[str, Any]:
    raw = stdin.read(MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("hook payload too large")
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("hook payload must be a JSON object")
    return data


def _root(data: dict[str, Any]) -> Path:
    candidates = [os.environ.get("CLAUDE_PROJECT_DIR"), data.get("cwd"), os.getcwd()]
    for c in candidates:
        if isinstance(c, str) and c and Path(c).is_dir():
            top = Git.toplevel(Path(c))
            return (top or Path(c)).resolve()
    return Path.cwd().resolve()


def _edited_file(data: dict[str, Any], root: Path) -> str | None:
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return relative(root, value)
    return None


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    try:
        data = _payload(stdin)
    except (ValueError, json.JSONDecodeError) as exc:
        stderr.write(f"agent-lint hook: ignoring malformed payload ({exc})\n")
        return EXIT_OK
    event = data.get("hook_event_name")
    root = _root(data)
    try:
        project = engine.Project.open(root, load_config(root))
    except ValueError as exc:  # ConfigError
        stderr.write(f"agent-lint hook: {exc}\n")
        return BLOCK if event == "PostToolUse" else EXIT_OK

    if event == "PostToolUse":
        if data.get("tool_name") not in _EDIT_TOOLS:
            return EXIT_OK
        rel = _edited_file(data, root)
        if rel is None:
            return EXIT_OK  # outside the project: not ours to judge
        try:
            result = engine.check(project, tools=None, files=[rel], hook=True)
        except engine.UsageError as exc:
            stderr.write(f"agent-lint hook: {exc}\n")
            return EXIT_OK
        if result.passed:
            return EXIT_OK
        stderr.write(f"agent-lint: {rel} does not pass the gate.\n")
        stderr.write(render.text(result, limit=_FEEDBACK_LIMIT))
        return BLOCK

    if event in ("Stop", "SubagentStop"):
        if data.get("stop_hook_active") is True:
            return EXIT_OK
        try:
            result = engine.check(project)
        except engine.UsageError as exc:
            stderr.write(f"agent-lint hook: {exc}\n")
            return EXIT_OK
        if result.passed:
            return EXIT_OK
        reason = (
            "agent-lint gate failed. Fix these before finishing; do not add suppressions, "
            "skip tests, loosen linter config or edit the baseline.\n"
            + render.text(result, limit=_FEEDBACK_LIMIT)
        )
        stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
        return EXIT_OK

    return EXIT_OK


def main() -> int:
    return run(sys.stdin, sys.stdout, sys.stderr)
