from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from agent_lint import config as cfg
from agent_lint import proc
from agent_lint.runners import RUNNERS
from agent_lint.runners import Context
from agent_lint.runners import RunnerError
from agent_lint.runners.base import load_json

# ------------------------------------------------------------------ config


def test_defaults_without_config(tmp_path: Path) -> None:
    c = cfg.load(tmp_path)
    assert c.tools is None
    assert c.base == "HEAD"
    assert c.runner("ruff").args == ()


def test_config_file_wins_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.agent-lint]\ntools = ["mypy"]\n', encoding="utf-8"
    )
    c = cfg.load(tmp_path)
    assert c.tools == ("mypy",)
    assert c.source == "pyproject.toml"
    (tmp_path / ".agent-lint").mkdir()
    (tmp_path / cfg.CONFIG_FILE).write_text(
        'tools = ["ruff"]\nbase = "origin/main"\ntimeout = 30\nexclude = ["gen/**"]\n'
        '[ruff]\nargs = ["--select", "E"]\nexecutable = "ruff"\n',
        encoding="utf-8",
    )
    c = cfg.load(tmp_path)
    assert c.tools == ("ruff",)
    assert c.base == "origin/main"
    assert c.timeout == 30.0
    assert c.exclude == ("gen/**",)
    assert c.runner("ruff").args == ("--select", "E")
    assert c.runner("ruff").executable == "ruff"


@pytest.mark.parametrize(
    "data",
    [
        {"nope": 1},
        {"tools": "ruff"},
        {"tools": ["pylint"]},
        {"hook_tools": ["x"]},
        {"base": ""},
        {"timeout": 0},
        {"timeout": True},
        {"ruff": []},
        {"ruff": {"args": "x"}},
        {"ruff": {"bogus": 1}},
        {"ruff": {"executable": ""}},
    ],
)
def test_invalid_config(data: dict[str, object]) -> None:
    with pytest.raises(cfg.ConfigError):
        cfg.from_mapping(data)


def test_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool", encoding="utf-8")
    with pytest.raises(cfg.ConfigError):
        cfg.load(tmp_path)
    (tmp_path / ".agent-lint").mkdir()
    (tmp_path / cfg.CONFIG_FILE).write_text("= =", encoding="utf-8")
    with pytest.raises(cfg.ConfigError):
        cfg.load(tmp_path)


def test_effective_base(monkeypatch: pytest.MonkeyPatch) -> None:
    c = cfg.Config(base="develop")
    assert cfg.effective_base(c, None) == "develop"
    monkeypatch.setenv("AGENT_LINT_BASE", "origin/main")
    assert cfg.effective_base(c, None) == "origin/main"
    assert cfg.effective_base(c, "v1") == "v1"


# ----------------------------------------------------------------- parsers


def _ctx(root: Path, files: tuple[str, ...] = (), **kw: object) -> Context:
    return Context(root, cfg.from_mapping(dict(kw)), files)


def test_ruff_parse(tmp_path: Path) -> None:
    out = json.dumps(
        [
            {
                "code": "F401",
                "filename": str(tmp_path / "a.py"),
                "location": {"row": 3, "column": 1},
                "message": "unused",
            },
            {"code": None, "filename": "b.py", "location": {"row": 1}, "message": "syntax"},
            {"code": "E1", "filename": "/elsewhere/c.py", "location": {}, "message": "out"},
            "garbage",
        ]
    )
    found = RUNNERS["ruff"]().parse(out, _ctx(tmp_path))
    assert [(f.rule, f.path, f.line) for f in found] == [
        ("F401", "a.py", 3),
        ("syntax-error", "b.py", 1),
    ]
    assert RUNNERS["ruff"]().parse("", _ctx(tmp_path)) == []
    with pytest.raises(RunnerError):
        RUNNERS["ruff"]().parse('{"a": 1}', _ctx(tmp_path))


def test_mypy_parse(tmp_path: Path) -> None:
    lines = [
        json.dumps(
            {
                "file": "a.py",
                "line": 2,
                "column": 4,
                "message": "bad",
                "code": "arg-type",
                "severity": "error",
            }
        ),
        json.dumps(
            {
                "file": "a.py",
                "line": 2,
                "column": 4,
                "message": "see",
                "code": None,
                "severity": "note",
            }
        ),
        json.dumps(
            {
                "file": "b.py",
                "line": 1,
                "column": 0,
                "message": "m",
                "code": None,
                "severity": "error",
            }
        ),
        "Success: no issues found",
    ]
    found = RUNNERS["mypy"]().parse("\n".join(lines), _ctx(tmp_path))
    assert [(f.rule, f.path, f.column) for f in found] == [
        ("arg-type", "a.py", 5),
        ("misc", "b.py", 1),
    ]
    with pytest.raises(RunnerError):
        RUNNERS["mypy"]().parse("{not json", _ctx(tmp_path))


def test_bandit_parse(tmp_path: Path) -> None:
    out = "Working... \n" + json.dumps(
        {
            "results": [
                {
                    "filename": "./a.py",
                    "line_number": 7,
                    "col_offset": 0,
                    "test_id": "B602",
                    "issue_text": "shell",
                    "issue_severity": "HIGH",
                },
                {
                    "filename": "./a.py",
                    "line_number": 1,
                    "test_id": "B404",
                    "issue_text": "import",
                    "issue_severity": "LOW",
                },
            ]
        }
    )
    found = RUNNERS["bandit"]().parse(out, _ctx(tmp_path))
    assert [(f.rule, f.severity) for f in found] == [("B602", "error"), ("B404", "warning")]
    with pytest.raises(RunnerError):
        RUNNERS["bandit"]().parse("[]", _ctx(tmp_path))


def test_eslint_parse(tmp_path: Path) -> None:
    out = json.dumps(
        [
            {
                "filePath": str(tmp_path / "src" / "a.ts"),
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,
                        "message": "x",
                        "line": 1,
                        "column": 7,
                    },
                    {"ruleId": None, "fatal": True, "severity": 2, "message": "parse", "line": 3},
                    {"ruleId": "eqeqeq", "severity": 1, "message": "==", "line": 4},
                ],
            }
        ]
    )
    found = RUNNERS["eslint"]().parse(out, _ctx(tmp_path))
    assert [(f.rule, f.path, f.severity) for f in found] == [
        ("no-unused-vars", "src/a.ts", "error"),
        ("fatal", "src/a.ts", "error"),
        ("eqeqeq", "src/a.ts", "warning"),
    ]
    with pytest.raises(RunnerError):
        RUNNERS["eslint"]().parse("{}", _ctx(tmp_path))


def test_semgrep_sarif_parse(tmp_path: Path) -> None:
    sarif = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "rules.no-eval",
                        "level": "error",
                        "message": {"text": "eval"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "a.py"},
                                    "region": {"startLine": 3, "startColumn": 2},
                                }
                            }
                        ],
                    },
                    {
                        "ruleId": "r2",
                        "message": "plain",
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "../x.py"}}}
                        ],
                    },
                ]
            }
        ]
    }
    found = RUNNERS["semgrep"]().parse(json.dumps(sarif), _ctx(tmp_path))
    assert [(f.rule, f.line, f.severity) for f in found] == [("rules.no-eval", 3, "error")]
    with pytest.raises(RunnerError):
        RUNNERS["semgrep"]().parse('{"x": 1}', _ctx(tmp_path))


def test_shellcheck_parse(tmp_path: Path) -> None:
    out = json.dumps(
        {
            "comments": [
                {
                    "file": "s.sh",
                    "line": 2,
                    "column": 5,
                    "level": "warning",
                    "code": 2086,
                    "message": "quote",
                }
            ]
        }
    )
    found = RUNNERS["shellcheck"]().parse(out, _ctx(tmp_path))
    assert [(f.rule, f.path) for f in found] == [("SC2086", "s.sh")]
    with pytest.raises(RunnerError):
        RUNNERS["shellcheck"]().parse("[]", _ctx(tmp_path))


def test_load_json_errors() -> None:
    assert load_json("  ", "x") is None
    with pytest.raises(RunnerError):
        load_json("not json at all", "x")


# -------------------------------------------------------------------- argv


def test_argv_shapes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, ("a.py", "s.sh", "x.js"))
    assert RUNNERS["ruff"]().argv("ruff", ctx, None)[-1] == "."
    assert RUNNERS["ruff"]().argv("ruff", ctx, ["a.py"])[-1] == "a.py"
    assert RUNNERS["mypy"]().argv("mypy", ctx, None)[-1] == "."
    assert "-r" in RUNNERS["bandit"]().argv("bandit", ctx, None)
    assert RUNNERS["eslint"]().argv("eslint", ctx, None)[-1] == "."
    assert RUNNERS["shellcheck"]().targets(ctx, None) == ["s.sh"]
    assert RUNNERS["shellcheck"]().argv("shellcheck", ctx, ["s.sh"])[-1] == "s.sh"
    assert RUNNERS["ruff"]().targets(ctx, ["a.py", "x.js"]) == ["a.py"]


def test_mypy_respects_configured_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.mypy]\nfiles = ["src"]\n', encoding="utf-8")
    argv = RUNNERS["mypy"]().argv("mypy", _ctx(tmp_path), None)
    assert argv[-1] != "."
    argv = RUNNERS["mypy"]().argv("mypy", _ctx(tmp_path, mypy={"args": ["pkg"]}), None)
    assert argv[-1] == "pkg"


def test_bandit_uses_pyproject_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.bandit]\nskips = []\n", encoding="utf-8")
    argv = RUNNERS["bandit"]().argv("bandit", _ctx(tmp_path), ["a.py"])
    assert argv[argv.index("-c") + 1] == "pyproject.toml"


def test_semgrep_requires_local_rules(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, ("a.py",))
    runner = RUNNERS["semgrep"]()
    assert not runner.applies(ctx)
    with pytest.raises(RunnerError):
        runner.argv("semgrep", ctx, None)
    (tmp_path / ".semgrep.yml").write_text("rules: []\n", encoding="utf-8")
    assert runner.applies(ctx)
    argv = runner.argv("semgrep", ctx, None)
    assert "--metrics=off" in argv
    assert argv[argv.index("--config") + 1] == ".semgrep.yml"


# --------------------------------------------------------- running a fake


def _fake_tool(root: Path, name: str, body: str, folder: str = "bin") -> str:
    script = root / folder / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return f"{folder}/{name}"


def test_runner_runs_fake_executable_with_safe_arguments(tmp_path: Path) -> None:
    exe = _fake_tool(
        tmp_path,
        "fake-ruff",
        "import json, sys\n"
        "args = [a for a in sys.argv[1:] if a.endswith('.py')]\n"
        "print(json.dumps([{'code': 'X1', 'filename': a, 'location': {'row': 1}, 'message': 'm'} for a in args]))",
    )
    ctx = _ctx(tmp_path, ("-rf.py", "a.py"), ruff={"executable": exe})
    runner = RUNNERS["ruff"]()
    found = runner.run(ctx, ["-rf.py", "a.py", "b.txt"])
    # "-rf.py" reached the tool as "./-rf.py", never as an option.
    assert sorted(f.path for f in found) == ["-rf.py", "a.py"]
    assert runner.run(ctx, ["b.txt"]) == []


def test_runner_failures(tmp_path: Path) -> None:
    exe = _fake_tool(tmp_path, "crash", "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)")
    runner = RUNNERS["ruff"]()
    with pytest.raises(RunnerError, match="exited with 3"):
        runner.run(_ctx(tmp_path, ruff={"executable": exe}))
    with pytest.raises(RunnerError, match="not found"):
        runner.run(_ctx(tmp_path, ruff={"executable": "bin/missing"}))
    with pytest.raises(RunnerError, match="not found"):
        runner.run(_ctx(tmp_path, ruff={"executable": "definitely-not-a-binary-xyz"}))
    slow = _fake_tool(tmp_path, "slow", "import time\ntime.sleep(5)")
    with pytest.raises(RunnerError, match="timed out"):
        runner.run(_ctx(tmp_path, ruff={"executable": slow}, timeout=0.2))


def test_runner_local_bin_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    ctx = _ctx(tmp_path)
    assert RUNNERS["eslint"]().binary(ctx) is None
    _fake_tool(tmp_path, "eslint", "pass", folder="node_modules/.bin")
    assert RUNNERS["eslint"]().binary(ctx) == str(tmp_path / "node_modules" / ".bin" / "eslint")


def test_proc_refuses_bad_argv(tmp_path: Path) -> None:
    with pytest.raises(proc.ProcessError):
        proc.run([], tmp_path, timeout=1)
    with pytest.raises(proc.ProcessError):
        proc.run(["echo", "a\x00b"], tmp_path, timeout=1)
    with pytest.raises(proc.ProcessError):
        proc.run(["echo"], tmp_path / "missing-dir", timeout=1)


def test_example_config_is_valid() -> None:
    import tomllib

    path = Path(__file__).parents[1] / "examples" / "config.toml"
    c = cfg.from_mapping(tomllib.loads(path.read_text(encoding="utf-8")))
    assert c.tools == ("ruff", "mypy", "eslint")
    assert c.runner("eslint").executable == "node_modules/.bin/eslint"
