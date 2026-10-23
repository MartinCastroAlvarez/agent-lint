"""Project configuration: ``.agent-lint/config.toml`` or ``[tool.agent-lint]``.

The config is data (TOML parsed with :mod:`tomllib`); nothing in it is
evaluated. Unknown keys are errors so a typo cannot silently switch a
guard off.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from agent_lint.paths import read_text

CONFIG_DIR = ".agent-lint"
CONFIG_FILE = f"{CONFIG_DIR}/config.toml"
BASELINE_FILE = f"{CONFIG_DIR}/baseline.json"
DEFAULT_BASE = "HEAD"
#: Runners that accept a list of files and are quick enough for a PostToolUse hook.
DEFAULT_HOOK_TOOLS = ("ruff", "eslint", "shellcheck", "bandit")

_TOP_KEYS = {
    "tools",
    "base",
    "exclude",
    "tests",
    "allow",
    "disable",
    "timeout",
    "hook_tools",
}


class ConfigError(ValueError):
    """The configuration is invalid."""


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    args: tuple[str, ...] = ()
    executable: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    #: Runners to use. ``None`` = every runner that is installed and relevant.
    tools: tuple[str, ...] | None = None
    base: str = DEFAULT_BASE
    exclude: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    disable: tuple[str, ...] = ()
    timeout: float = 600.0
    hook_tools: tuple[str, ...] = DEFAULT_HOOK_TOOLS
    runners: dict[str, RunnerConfig] = field(default_factory=dict)
    source: str | None = None

    def runner(self, name: str) -> RunnerConfig:
        return self.runners.get(name, RunnerConfig())


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(value)


def from_mapping(data: dict[str, Any], source: str | None = None) -> Config:
    from agent_lint.runners import RUNNERS  # local import: runners import config

    runners: dict[str, RunnerConfig] = {}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key in RUNNERS:
            if not isinstance(value, dict):
                raise ConfigError(f"[{key}] must be a table")
            extra = set(value) - {"args", "executable"}
            if extra:
                raise ConfigError(f"[{key}] unknown keys: {', '.join(sorted(extra))}")
            exe = value.get("executable")
            if exe is not None and (not isinstance(exe, str) or not exe):
                raise ConfigError(f"[{key}] executable must be a string")
            runners[key] = RunnerConfig(_strings(value.get("args", []), f"{key}.args"), exe)
            continue
        if key not in _TOP_KEYS:
            raise ConfigError(f"unknown config key: {key}")
        if key == "base":
            if not isinstance(value, str) or not value:
                raise ConfigError("base must be a non-empty string")
            kwargs["base"] = value
        elif key == "timeout":
            if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
                raise ConfigError("timeout must be a positive number")
            kwargs["timeout"] = float(value)
        else:
            kwargs[key] = _strings(value, key)
    for key in ("tools", "hook_tools"):
        unknown = set(kwargs.get(key, ())) - set(RUNNERS)
        if unknown:
            raise ConfigError(f"{key}: unknown tools: {', '.join(sorted(unknown))}")
    return Config(runners=runners, source=source, **kwargs)


def load(root: Path) -> Config:
    """Load the project config; defaults when none exists."""
    text = read_text(root, CONFIG_FILE)
    if text is not None:
        try:
            return from_mapping(tomllib.loads(text), CONFIG_FILE)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{CONFIG_FILE}: {exc}") from exc
    text = read_text(root, "pyproject.toml")
    if text is not None:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"pyproject.toml: {exc}") from exc
        section = data.get("tool", {}).get("agent-lint")
        if isinstance(section, dict):
            return from_mapping(section, "pyproject.toml")
    return Config()


def effective_base(config: Config, override: str | None) -> str:
    """``--base`` wins, then ``$AGENT_LINT_BASE``, then the config, then ``HEAD``."""
    return override or os.environ.get("AGENT_LINT_BASE") or config.base
