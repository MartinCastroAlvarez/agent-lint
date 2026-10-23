"""End-to-end: real ruff in fixture git repos, through the engine, the CLI and the hook."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agent_lint import baseline as bl
from agent_lint import cli
from agent_lint import engine
from agent_lint import hooks
from agent_lint.config import BASELINE_FILE
from agent_lint.config import CONFIG_FILE
from agent_lint.config import Config
from tests.conftest import Repo

LEGACY = "import os\nimport sys\n\n\ndef f():\n    return 1\n"


@pytest.fixture
def adopted(repo: Repo) -> Repo:
    """A project with two old ruff findings recorded in a committed baseline."""
    repo.write(CONFIG_FILE, 'tools = ["ruff"]\n')
    repo.write("pyproject.toml", '[tool.ruff.lint]\nselect = ["F", "E"]\n')
    repo.write("app.py", LEGACY)
    repo.write("tests/test_app.py", "def test_f():\n    assert 1\n")
    report = engine.init(repo.project())
    assert report.tools == ["ruff"]
    assert report.current_size == 2
    repo.commit("adopt agent-lint")
    return repo


def test_init_check_pass(adopted: Repo) -> None:
    report = engine.check(adopted.project())
    assert report.passed, report
    assert report.baseline_size == 2
    assert report.base is not None


def test_line_shift_does_not_create_new_findings(adopted: Repo) -> None:
    adopted.write("app.py", '"""Docs."""\n\n\n' + LEGACY)
    assert engine.check(adopted.project()).passed


def test_new_finding_fails_and_fixed_ratchets(adopted: Repo) -> None:
    adopted.write("app.py", "import sys\nimport json\n\n\ndef f():\n    return 1\n")
    report = engine.check(adopted.project())
    assert [f.rule for f in report.new] == ["F401"]
    assert "json" in report.new[0].message
    assert [f.message for f in report.fixed] == ["`os` imported but unused"]
    assert not report.passed
    # ratchet drops the fixed one but never adds the new one.
    report, removed = engine.ratchet(adopted.project())
    assert removed == 1
    assert not report.passed
    current = bl.load(adopted.root)
    assert current is not None and len(current) == 1
    report, removed = engine.ratchet(adopted.project())
    assert removed == 0


def test_guard_and_finding_together(adopted: Repo) -> None:
    adopted.write("app.py", LEGACY + "import json  # noqa\n")
    report = engine.check(adopted.project())
    assert report.new == []  # ruff honoured the noqa ...
    assert [v.code for v in report.violations] == ["AL201"]  # ... and we caught it


def test_files_subset_and_exclude(adopted: Repo) -> None:
    adopted.write("other.py", "import json\n")
    adopted.write("gen/out.py", "import json\n")
    project = adopted.project(Config(tools=("ruff",), exclude=("gen/**",)))
    report = engine.check(project, files=["app.py"])
    assert report.passed
    report = engine.check(project)
    assert [f.path for f in report.new] == ["other.py"]


def test_tools_missing_or_failing(adopted: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    report = engine.check(adopted.project())
    assert [f.tool for f in report.failures] == ["ruff"]
    assert not report.passed
    # Auto-detect mode: a missing tool is only a warning and its baseline survives.
    adopted.rm(CONFIG_FILE)
    report = engine.check(adopted.project())
    assert report.failures == []
    assert any("not installed" in w for w in report.warnings)
    assert report.fixed == []
    with pytest.raises(engine.UsageError):
        engine.check(adopted.project(), tools=["pylint"])


def test_broken_tool_config_is_a_failure(adopted: Repo) -> None:
    adopted.write("pyproject.toml", '[tool.ruff.lint]\nselect = ["NOT-A-RULE"]\n')
    report = engine.check(adopted.project(Config(tools=("ruff",))), run_guards=False)
    assert report.failures and report.failures[0].tool == "ruff"


def test_init_refuses_overwrite_and_ratchet_needs_baseline(adopted: Repo, repo: Repo) -> None:
    with pytest.raises(engine.UsageError):
        engine.init(adopted.project())
    assert engine.init(adopted.project(), force=True).current_size == 2
    adopted.rm(BASELINE_FILE)
    with pytest.raises(engine.UsageError):
        engine.ratchet(adopted.project())


def test_bad_base_ref_and_no_commits(repo: Repo) -> None:
    repo.write("a.py", "x = 1\n")
    project = repo.project(Config(tools=("ruff",)))
    report = engine.check(project)
    assert any("no commits" in w for w in report.warnings)
    with pytest.raises(engine.UsageError):
        engine.check(project, base="nope")


def test_not_a_git_repo(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    project = engine.Project.open(tmp_path, Config(tools=("ruff",)))
    report = engine.check(project)
    assert report.passed
    assert any("not a git repository" in w for w in report.warnings)


def test_corrupt_baseline_is_a_usage_error(adopted: Repo) -> None:
    adopted.write(BASELINE_FILE, "{")
    with pytest.raises(engine.UsageError):
        engine.check(adopted.project())


# --------------------------------------------------------------------- CLI


def _cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(args)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_flow(
    adopted: Repo,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = str(adopted.root)
    code, out, _ = _cli(["--root", root, "check"], capsys)
    assert code == 0 and "passed" in out
    adopted.write("app.py", LEGACY + "import json\n")
    sarif, md = tmp_path / "out.sarif", tmp_path / "out.md"
    code, out, _ = _cli(
        ["--root", root, "check", "--format", "json", "--sarif", str(sarif), "--markdown", str(md)],
        capsys,
    )
    assert code == 1
    doc = json.loads(out)
    assert doc["passed"] is False and doc["new"][0]["rule"] in ("E402", "F401")
    assert json.loads(sarif.read_text())["version"] == "2.1.0"
    assert "New findings" in md.read_text()
    fp = doc["new"][0]["fingerprint"]
    report_file = tmp_path / "r" / "report.txt"
    code, _, _ = _cli(["--root", root, "check", "--output", str(report_file)], capsys)
    assert code == 1 and "NEW" in report_file.read_text()
    monkeypatch.chdir(adopted.root)
    code, out, _ = _cli(["check", "--format", "markdown", "app.py"], capsys)
    assert code == 1 and "agent-lint failed" in out
    code, _, err = _cli(["check", "/etc/passwd"], capsys)
    assert code == 2 and "outside the project" in err
    code, out, _ = _cli(["--root", root, "check", "--format", "sarif", "--no-guards"], capsys)
    assert code == 1 and '"results"' in out
    code, out, _ = _cli(["--root", root, "explain", fp], capsys)
    assert code == 2  # new findings are not in the baseline
    baseline = bl.load(adopted.root)
    assert baseline is not None
    known = next(iter(baseline.entries))
    code, out, _ = _cli(["--root", root, "explain", known[:8]], capsys)
    assert code == 0 and "ruff F401" in out
    code, out, _ = _cli(["--root", root, "ratchet"], capsys)
    assert code == 1 and "removed 0" in out


def test_cli_init_explain_tools_errors(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(repo.root)
    repo.write("a.py", "import os\n")
    code, out, _ = _cli(["--root", root, "init", "--tool", "ruff"], capsys)
    assert code == 0 and "1 finding" in out
    code, _, err = _cli(["--root", root, "init"], capsys)
    assert code == 2 and "already exists" in err
    code, out, _ = _cli(["--root", root, "explain"], capsys)
    assert "AL201" in out and "AL401" in out
    code, out, _ = _cli(["--root", root, "explain", "al202"], capsys)
    assert code == 0 and "focused" in out
    code, _, err = _cli(["--root", root, "explain", "zz"], capsys)
    assert code == 2
    code, out, _ = _cli(["--root", root, "tools"], capsys)
    assert "ruff" in out and "active" in out
    code, _, err = _cli(["--root", str(repo.root / "missing"), "check"], capsys)
    assert code == 2
    repo.write(CONFIG_FILE, "bogus = 1\n")
    code, _, err = _cli(["--root", root, "check"], capsys)
    assert code == 2 and "unknown config key" in err
    repo.write(CONFIG_FILE, 'tools = ["ruff", "mypy"]\n')
    code, out, _ = _cli(["--root", root, "tools"], capsys)
    assert "disabled in config" in out


def test_cli_init_reports_tool_failures(
    repo: Repo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    repo.write("a.py", "x = 1\n")
    code, out, _ = _cli(["--root", str(repo.root), "init", "--tool", "ruff"], capsys)
    assert code == 1 and "not installed" in out
    assert bl.load(repo.root) is None


def test_cli_explain_ambiguous(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    from agent_lint.models import Finding

    entries = [Finding("ruff", "E1", "a.py", 0, 0, "m", fingerprint=f"abcdef{i}") for i in range(2)]
    bl.save(repo.root, bl.Baseline.from_findings(entries))
    code, out, _ = _cli(["--root", str(repo.root), "explain", "abcdef"], capsys)
    assert code == 2 and "ambiguous" in out


# -------------------------------------------------------------------- hook


def _hook(payload: object) -> tuple[int, str, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    out, err = io.StringIO(), io.StringIO()
    code = hooks.run(stdin, out, err)
    return code, out.getvalue(), err.getvalue()


def _edit(repo: Repo, path: str, tool: str = "Edit") -> dict[str, object]:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "cwd": str(repo.root),
        "tool_input": {"file_path": str(repo.root / path)},
    }


def test_hook_post_tool_use(adopted: Repo) -> None:
    assert _hook(_edit(adopted, "app.py")) == (0, "", "")
    adopted.write("app.py", LEGACY + "import json  # noqa\n")
    code, out, err = _hook(_edit(adopted, "app.py"))
    assert code == 2 and out == ""
    assert "AL201" in err
    # Other files, other tools and paths outside the project are left alone.
    assert _hook(_edit(adopted, "tests/test_app.py"))[0] == 0
    assert _hook(_edit(adopted, "app.py", tool="Read"))[0] == 0
    outside = _edit(adopted, "app.py")
    outside["tool_input"] = {"file_path": "/etc/passwd"}
    assert _hook(outside)[0] == 0
    no_input = _edit(adopted, "app.py")
    no_input["tool_input"] = "nope"
    assert _hook(no_input)[0] == 0


def test_hook_stop(adopted: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = {"hook_event_name": "Stop", "cwd": str(adopted.root)}
    assert _hook(stop) == (0, "", "")
    adopted.write(
        "tests/test_app.py", "import pytest\n\n\n@pytest.mark.skip\ndef test_f():\n    assert 1\n"
    )
    code, out, _ = _hook(stop)
    assert code == 0
    decision = json.loads(out)
    assert decision["decision"] == "block" and "AL202" in decision["reason"]
    assert _hook({**stop, "stop_hook_active": True}) == (0, "", "")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(adopted.root))
    code, out, _ = _hook({"hook_event_name": "SubagentStop"})
    assert json.loads(out)["decision"] == "block"


def test_hook_bad_payloads_and_config(adopted: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _hook("not json")[0] == 0
    assert _hook("[1]")[0] == 0
    monkeypatch.setattr(hooks, "MAX_PAYLOAD", 10)
    assert "too large" in _hook({"hook_event_name": "Stop", "cwd": "x" * 50})[2]
    monkeypatch.undo()
    assert _hook({"hook_event_name": "SessionStart", "cwd": str(adopted.root)}) == (0, "", "")
    adopted.write(CONFIG_FILE, "nope = 1\n")
    code, _, err = _hook(_edit(adopted, "app.py"))
    assert code == 2 and "unknown config key" in err
    assert _hook({"hook_event_name": "Stop", "cwd": str(adopted.root)})[0] == 0


def test_hook_usage_errors_never_block(adopted: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LINT_BASE", "does-not-exist")
    assert _hook(_edit(adopted, "app.py"))[0] == 0
    assert _hook({"hook_event_name": "Stop", "cwd": str(adopted.root)})[0] == 0


def test_hook_print_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["hook", "--print-config"]) == 0
    settings = json.loads(capsys.readouterr().out)
    post = settings["hooks"]["PostToolUse"][0]
    assert post["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert post["hooks"][0]["args"] == ["hook"]
    assert "Stop" in settings["hooks"]
    committed = Path(__file__).parents[1] / "integrations" / "claude-code" / "settings.json"
    assert json.loads(committed.read_text()) == settings


def test_hook_via_cli_reads_stdin(adopted: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "cwd": str(adopted.root)}))
    )
    assert cli.main(["hook"]) == 0
