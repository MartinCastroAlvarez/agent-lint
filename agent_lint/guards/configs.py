"""AL301/AL302: linter, type-checker, test and agent-lint config weakened.

Each known config file is reduced to a *policy*: ``{key: (kind, value)}``.
The kind says which direction is "weaker":

* ``shrink`` - a set; removing items weakens (``ruff select``, ``tsconfig strict`` …)
* ``grow``   - a set; adding items weakens (``ignore``, ``exclude``, ``skips`` …)
* ``raise``  - a number; raising it weakens (``line-length``, ``max-complexity``)
* ``lower``  - a number; lowering it weakens (``fail_under``)
* ``on`` / ``off`` - a flag that weakens when switched on / off
* ``changed`` - any change is suspicious (agent-lint's ``base``)
* ``levels`` - ``{rule: severity}``; lowering a severity weakens (eslint rules)
* ``limits`` - ``{rule: number}``; raising a limit weakens (eslint thresholds)

Files are parsed as data: TOML with :mod:`tomllib`, INI with
:mod:`configparser` (no interpolation), JSON/JSONC with :mod:`json`. JS
config files (``eslint.config.js``) are never evaluated; added lines that
look like relaxations are reported instead.
"""

from __future__ import annotations

import configparser
import json
import re
import shlex
import tomllib
from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

from agent_lint.config import CONFIG_FILE
from agent_lint.fingerprint import normalise
from agent_lint.guards.base import DiffContext
from agent_lint.guards.base import unmatched
from agent_lint.models import Violation

WEAKENED = "AL301"
SELF = "AL302"

Value = Any
Policy = dict[str, tuple[str, Value]]

# --------------------------------------------------------------------- utils


def _set(*values: Any) -> frozenset[str]:
    out: set[str] = set()
    for v in values:
        if isinstance(v, str):
            out.update(p.strip() for p in re.split(r"[,\n]", v) if p.strip())
        elif isinstance(v, list | tuple | set | frozenset):
            out.update(str(x).strip() for x in v if str(x).strip())
    return frozenset(out)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return None


def _toml(text: str) -> dict[str, Any] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _ini(text: str) -> configparser.ConfigParser | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    return parser


def strip_jsonc(text: str) -> str:
    """Remove ``//`` / ``/* */`` comments and trailing commas outside strings."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _jsonc(text: str) -> Any:
    try:
        return json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, RecursionError):
        return None


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------- ruff

_RUFF_LIMITS = {
    "mccabe.max-complexity",
    "pylint.max-args",
    "pylint.max-branches",
    "pylint.max-returns",
    "pylint.max-statements",
    "pylint.max-locals",
    "pylint.max-positional-args",
    "pylint.max-nested-blocks",
}


def ruff_policy(table: dict[str, Any]) -> Policy:
    if not table:
        return {}
    lint = _dict(table.get("lint"))

    def both(key: str) -> Any:
        return lint.get(key, table.get(key))

    p: Policy = {
        "ruff select": ("shrink", _set(both("select"), both("extend-select"))),
        "ruff ignore": ("grow", _set(both("ignore"), both("extend-ignore"))),
        "ruff exclude": ("grow", _set(table.get("exclude"), table.get("extend-exclude"))),
    }
    pfi: set[str] = set()
    for key in ("per-file-ignores", "extend-per-file-ignores"):
        for glob, codes in _dict(both(key)).items():
            pfi.update(f"{glob}: {c}" for c in _set(codes))
    p["ruff per-file-ignores"] = ("grow", frozenset(pfi))
    length = _num(table.get("line-length"))
    if length is not None:
        p["ruff line-length"] = ("raise", length)
    for dotted in _RUFF_LIMITS:
        section, key = dotted.split(".")
        value = _num(_dict(both(section)).get(key))
        if value is not None:
            p[f"ruff {dotted}"] = ("raise", value)
    return p


# ------------------------------------------------------------------- mypy

_MYPY_STRICT_FLAGS = (
    "strict",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "disallow_untyped_calls",
    "disallow_untyped_decorators",
    "disallow_any_generics",
    "disallow_subclassing_any",
    "check_untyped_defs",
    "warn_return_any",
    "warn_unused_ignores",
    "warn_redundant_casts",
    "warn_unreachable",
    "no_implicit_optional",
    "no_implicit_reexport",
    "strict_equality",
    "extra_checks",
    "strict_optional",
)
_MYPY_LOOSE_FLAGS = (
    "ignore_missing_imports",
    "ignore_errors",
    "allow_untyped_defs",
    "allow_untyped_calls",
    "allow_incomplete_defs",
    "allow_any_generics",
    "allow_redefinition",
    "implicit_optional",
)


def _mypy_global(get: Callable[[str], Any]) -> Policy:
    p: Policy = {}
    for flag in _MYPY_STRICT_FLAGS:
        if _bool(get(flag)):
            p[f"mypy {flag}"] = ("off", True)
    for flag in _MYPY_LOOSE_FLAGS:
        if _bool(get(flag)):
            p[f"mypy {flag}"] = ("on", True)
    if str(get("follow_imports") or "").strip() in ("skip", "silent"):
        p["mypy follow_imports=skip/silent"] = ("on", True)
    p["mypy disable_error_code"] = ("grow", _set(get("disable_error_code")))
    p["mypy enable_error_code"] = ("shrink", _set(get("enable_error_code")))
    p["mypy exclude"] = ("grow", _set(get("exclude")))
    p["mypy files"] = ("shrink", _set(get("files")))
    return p


def _mypy_overrides(overrides: Sequence[tuple[frozenset[str], Callable[[str], Any]]]) -> Policy:
    ignored: set[str] = set()
    disabled: set[str] = set()
    for modules, get in overrides:
        if _bool(get("ignore_errors")):
            ignored.update(modules)
        for code in _set(get("disable_error_code")):
            disabled.update(f"{m}: {code}" for m in modules)
        for flag in ("disallow_untyped_defs", "check_untyped_defs", "strict"):
            if _bool(get(flag)) is False:
                disabled.update(f"{m}: {flag}=false" for m in modules)
    return {
        "mypy overrides ignore_errors": ("grow", frozenset(ignored)),
        "mypy overrides relaxed": ("grow", frozenset(disabled)),
    }


def mypy_policy_toml(table: dict[str, Any]) -> Policy:
    if not table:
        return {}
    p = _mypy_global(table.get)
    raw = table.get("overrides")
    overrides = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            overrides.append((_set(item.get("module")), item.get))
    p.update(_mypy_overrides(overrides))
    return p


def mypy_policy_ini(parser: configparser.ConfigParser) -> Policy:
    if not parser.has_section("mypy"):
        return {}
    section = parser["mypy"]
    p = _mypy_global(section.get)
    overrides = []
    for name in parser.sections():
        if name.startswith("mypy-"):
            overrides.append((_set(name[len("mypy-") :]), parser[name].get))
    p.update(_mypy_overrides(overrides))
    return p


# --------------------------------------------- bandit, coverage, pytest


def bandit_policy(get: Callable[[str], Any]) -> Policy:
    return {
        "bandit skips": ("grow", _set(get("skips"))),
        "bandit exclude": ("grow", _set(get("exclude_dirs"), get("exclude"))),
        "bandit tests": ("shrink", _set(get("tests"))),
    }


def coverage_policy(run: Callable[[str], Any], report: Callable[[str], Any]) -> Policy:
    p: Policy = {
        "coverage omit": ("grow", _set(run("omit"), report("omit"))),
        "coverage exclude_lines": ("grow", _set(report("exclude_lines"), report("exclude_also"))),
    }
    fail_under = _num(report("fail_under"))
    if fail_under is not None:
        p["coverage fail_under"] = ("lower", fail_under)
    return p


_PYTEST_NARROWING = ("-k", "-m", "--deselect", "--ignore", "--ignore-glob", "-p", "-x", "--lf")


def pytest_policy(get: Callable[[str], Any]) -> Policy:
    raw = get("addopts")
    tokens: list[str] = []
    if isinstance(raw, list):
        tokens = [str(t) for t in raw]
    elif isinstance(raw, str):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
    narrowing: set[str] = set()
    for i, tok in enumerate(tokens):
        opt = tok.split("=", 1)[0]
        if opt in _PYTEST_NARROWING:
            if "=" in tok or opt in ("-x", "--lf"):
                narrowing.add(tok)
            elif i + 1 < len(tokens):
                narrowing.add(f"{tok} {tokens[i + 1]}")
    return {
        "pytest addopts": ("grow", frozenset(narrowing)),
        "pytest testpaths": ("shrink", _set(get("testpaths"))),
        "pytest norecursedirs": ("grow", _set(get("norecursedirs"))),
    }


# ------------------------------------------------------------- agent-lint


def agent_lint_policy(table: dict[str, Any]) -> Policy:
    if not table:
        return {}
    p: Policy = {
        "agent-lint tools": ("shrink", _set(table.get("tools"))),
        "agent-lint hook_tools": ("shrink", _set(table.get("hook_tools"))),
        "agent-lint exclude": ("grow", _set(table.get("exclude"))),
        "agent-lint allow": ("grow", _set(table.get("allow"))),
        "agent-lint disable": ("grow", _set(table.get("disable"))),
    }
    if isinstance(table.get("base"), str):
        p["agent-lint base"] = ("changed", table["base"])
    for name, section in table.items():
        if isinstance(section, dict) and "args" in section:
            p[f"agent-lint {name}.args"] = ("changed", " ".join(_set(section.get("args"))))
    return p


# ------------------------------------------------------- eslint, typescript

_ESLINT_LEVELS = {"off": 0, "warn": 1, "error": 2}
_ESLINT_LIMIT_RULES = {
    "complexity",
    "max-depth",
    "max-lines",
    "max-lines-per-function",
    "max-params",
    "max-statements",
    "max-nested-callbacks",
    "max-len",
}


def _eslint_level(v: Any) -> float:
    first = v[0] if isinstance(v, list) and v else v
    if isinstance(first, str):
        return float(_ESLINT_LEVELS.get(first.lower(), 0))
    n = _num(first)
    return n if n is not None else 0.0


def _eslint_limit(v: Any) -> float | None:
    if not isinstance(v, list) or len(v) < 2:
        return None
    opt = v[1]
    if isinstance(opt, dict):
        opt = opt.get("max", opt.get("code"))
    return _num(opt)


def eslint_policy(doc: Any) -> Policy:
    if not isinstance(doc, dict):
        return {}
    levels: dict[str, float] = {}
    limits: dict[str, float] = {}

    def add_rules(rules: Any) -> None:
        for name, v in _dict(rules).items():
            levels[name] = max(levels.get(name, 0.0), _eslint_level(v))
            if name in _ESLINT_LIMIT_RULES:
                limit = _eslint_limit(v)
                if limit is not None:
                    limits[name] = limit

    add_rules(doc.get("rules"))
    for override in doc.get("overrides") or []:
        if isinstance(override, dict):
            add_rules(override.get("rules"))
    return {
        "eslint rules": ("levels", levels),
        "eslint limits": ("limits", limits),
        "eslint extends": ("shrink", _set(doc.get("extends"))),
        "eslint ignorePatterns": ("grow", _set(doc.get("ignorePatterns"))),
    }


_TS_STRICT_FAMILY = (
    "noImplicitAny",
    "strictNullChecks",
    "strictFunctionTypes",
    "strictBindCallApply",
    "strictPropertyInitialization",
    "noImplicitThis",
    "alwaysStrict",
    "useUnknownInCatchVariables",
)
_TS_FLAGS = (
    "noImplicitReturns",
    "noUncheckedIndexedAccess",
    "noUnusedLocals",
    "noUnusedParameters",
    "noFallthroughCasesInSwitch",
    "exactOptionalPropertyTypes",
    "noImplicitOverride",
    "checkJs",
)


def tsconfig_policy(doc: Any) -> Policy:
    if not isinstance(doc, dict):
        return {}
    opts = _dict(doc.get("compilerOptions"))
    strict = opts.get("strict") is True
    enabled = {"strict"} if strict else set()
    for flag in _TS_STRICT_FAMILY:
        if opts.get(flag, strict) is True:
            enabled.add(flag)
    enabled.update(flag for flag in _TS_FLAGS if opts.get(flag) is True)
    p: Policy = {
        "tsconfig strict flags": ("shrink", frozenset(enabled)),
        "tsconfig exclude": ("grow", _set(doc.get("exclude"))),
    }
    if opts.get("noEmitOnError") is False:
        p["tsconfig noEmitOnError=false"] = ("on", True)
    return p


# -------------------------------------------------------------- dispatch


def pyproject_policy(text: str) -> Policy | None:
    doc = _toml(text)
    if doc is None:
        return None
    tool = _dict(doc.get("tool"))
    cov = _dict(tool.get("coverage"))
    pytest_opts = _dict(_dict(tool.get("pytest")).get("ini_options"))
    p: Policy = {}
    p.update(ruff_policy(_dict(tool.get("ruff"))))
    p.update(mypy_policy_toml(_dict(tool.get("mypy"))))
    if "bandit" in tool:
        p.update(bandit_policy(_dict(tool.get("bandit")).get))
    if cov:
        p.update(coverage_policy(_dict(cov.get("run")).get, _dict(cov.get("report")).get))
    if pytest_opts:
        p.update(pytest_policy(pytest_opts.get))
    p.update(agent_lint_policy(_dict(tool.get("agent-lint"))))
    return p


def _ini_get(parser: configparser.ConfigParser, section: str) -> Callable[[str], Any]:
    if not parser.has_section(section):
        return lambda _key: None
    sec = parser[section]
    return sec.get


def ini_policy(text: str, name: str) -> Policy | None:
    parser = _ini(text)
    if parser is None:
        return None
    p: Policy = {}
    p.update(mypy_policy_ini(parser))
    if name == ".coveragerc":
        p.update(coverage_policy(_ini_get(parser, "run"), _ini_get(parser, "report")))
    elif parser.has_section("coverage:report") or parser.has_section("coverage:run"):
        run, report = _ini_get(parser, "coverage:run"), _ini_get(parser, "coverage:report")
        p.update(coverage_policy(run, report))
    for section in ("pytest", "tool:pytest"):
        if parser.has_section(section):
            p.update(pytest_policy(_ini_get(parser, section)))
    if name == ".bandit" and parser.has_section("bandit"):
        p.update(bandit_policy(_ini_get(parser, "bandit")))
    return p


def ignore_file_policy(text: str, label: str) -> Policy:
    lines = {
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")
    }
    return {f"{label} patterns": ("grow", frozenset(lines))}


_PRECOMMIT_ID = re.compile(r"^\s*-?\s*id:\s*([\w.-]+)", re.M)


def policy_for(path: str, text: str) -> Policy | None:
    """The policy encoded in ``text`` if ``path`` is a config file we understand."""
    name = path.rsplit("/", 1)[-1]
    if path == "pyproject.toml":
        return pyproject_policy(text)
    if path == CONFIG_FILE:
        doc = _toml(text)
        return None if doc is None else agent_lint_policy(doc)
    if name in ("ruff.toml", ".ruff.toml"):
        doc = _toml(text)
        return None if doc is None else ruff_policy(doc)
    if name in (
        "mypy.ini",
        ".mypy.ini",
        "setup.cfg",
        "tox.ini",
        "pytest.ini",
        ".coveragerc",
        ".bandit",
    ):
        return ini_policy(text, name)
    if name in (".eslintrc", ".eslintrc.json"):
        return eslint_policy(_jsonc(text))
    if name == "package.json":
        doc = _jsonc(text)
        return eslint_policy(doc.get("eslintConfig")) if isinstance(doc, dict) else None
    if name.startswith("tsconfig") and name.endswith(".json"):
        return tsconfig_policy(_jsonc(text))
    if name in (".eslintignore", ".semgrepignore"):
        return ignore_file_policy(text, name)
    if path == ".pre-commit-config.yaml":
        return {"pre-commit hooks": ("shrink", frozenset(_PRECOMMIT_ID.findall(text)))}
    return None


def compare(old: Policy, new: Policy) -> list[str]:
    """Human-readable weakenings from ``old`` to ``new``."""
    out: list[str] = []
    for key in sorted(set(old) | set(new)):
        kind, ov = old.get(key, (None, None))
        nkind, nv = new.get(key, (None, None))
        kind = kind or nkind
        if kind == "shrink":
            out += [f"{key}: `{x}` removed" for x in sorted(_set(ov) - _set(nv))]
        elif kind == "grow":
            out += [f"{key}: `{x}` added" for x in sorted(_set(nv) - _set(ov))]
        elif kind == "raise" and ov is not None and nv is not None and nv > ov:
            out.append(f"{key} raised from {ov:g} to {nv:g}")
        elif kind == "lower" and ov is not None and (nv if nv is not None else 0.0) < ov:
            out.append(f"{key} lowered from {ov:g} to {nv if nv is not None else 0:g}")
        elif kind == "on" and nv is True and ov is not True:
            out.append(f"{key} turned on")
        elif kind == "off" and ov is True and nv is not True:
            out.append(f"{key} turned off")
        elif kind == "changed" and ov is not None and ov != nv:
            out.append(f"{key} changed from `{ov}` to `{nv}`")
        elif kind == "levels":
            olds, news = _dict(ov), _dict(nv)
            for rule in sorted(olds):
                if news.get(rule, 0.0) < olds[rule]:
                    out.append(
                        f"{key}: `{rule}` relaxed ({olds[rule]:g} -> {news.get(rule, 0.0):g})"
                    )
        elif kind == "limits":
            olds, news = _dict(ov), _dict(nv)
            for rule in sorted(set(olds) & set(news)):
                if news[rule] > olds[rule]:
                    out.append(f"{key}: `{rule}` raised from {olds[rule]:g} to {news[rule]:g}")
    return out


_JS_CONFIG = re.compile(r"(^|/)eslint\.config\.(?:js|mjs|cjs|ts|mts|cts)$")
_JS_RELAX = re.compile(r"""['"](?:off|warn)['"]|:\s*[01]\s*[,}\]]|\bignores\s*:""")


def _js_config_lines(text: str | None) -> list[tuple[int, str]]:
    if not text:
        return []
    return [(i, normalise(ln)) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]


def _is_self(path: str) -> bool:
    return path == CONFIG_FILE or path.startswith(".github/workflows/")


def check(ctx: DiffContext) -> list[Violation]:
    out: list[Violation] = []
    for change in ctx.changes:
        path = change.path
        old_text = ctx.old(change)
        new_text = ctx.new(change)
        if _JS_CONFIG.search(path):
            old_lines = _js_config_lines(old_text)
            added = unmatched(old_lines, _js_config_lines(new_text), key=lambda x: x[1])
            for line, text in added:
                if _JS_RELAX.search(text):
                    msg = "possible rule relaxation in eslint flat config"
                    out.append(Violation(WEAKENED, path, line, msg, text[:200]))
            continue
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            before = (old_text or "").count("agent-lint")
            after = (new_text or "").count("agent-lint")
            if after < before:
                msg = "agent-lint removed from a CI workflow"
                out.append(Violation(SELF, path, 0, msg))
            continue
        old = policy_for(path, old_text) if old_text is not None else {}
        new = policy_for(path, new_text) if new_text is not None else {}
        if old is None or new is None:
            continue  # unparsable: the tool itself will fail on it
        for message in compare(old, new):
            code = SELF if message.startswith("agent-lint") or _is_self(path) else WEAKENED
            if message.startswith("pre-commit hooks: `agent-lint"):
                code = SELF
            out.append(Violation(code, path, 0, message))
    return out
