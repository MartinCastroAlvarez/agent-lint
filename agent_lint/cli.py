"""``agent-lint`` command line: init | check | ratchet | explain | tools | hook."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_lint import __version__
from agent_lint import baseline as baseline_mod
from agent_lint import engine
from agent_lint import hooks
from agent_lint import report as render
from agent_lint.baseline import BaselineError
from agent_lint.catalog import CATALOG
from agent_lint.config import BASELINE_FILE
from agent_lint.config import ConfigError
from agent_lint.config import load as load_config
from agent_lint.gitio import Git
from agent_lint.gitio import GitError
from agent_lint.models import EXIT_ERROR
from agent_lint.models import EXIT_FAILED
from agent_lint.models import EXIT_OK
from agent_lint.models import Report
from agent_lint.paths import relative
from agent_lint.runners import RUNNERS


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-lint",
        description="Ratcheting lint gate for code written by AI coding agents.",
    )
    p.add_argument("--version", action="version", version=f"agent-lint {__version__}")
    p.add_argument("--root", type=Path, help="project root (default: the git top level)")
    sub = p.add_subparsers(dest="command", required=True)

    def tools_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--tool",
            action="append",
            choices=sorted(RUNNERS),
            help="run only this linter (repeatable; default: config or auto-detect)",
        )

    init = sub.add_parser("init", help="record current findings as the baseline")
    init.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    tools_arg(init)

    check = sub.add_parser("check", help="fail on new findings and on guard violations")
    check.add_argument("--base", help="git ref to compare against (default: config, else HEAD)")
    check.add_argument("--format", choices=sorted(render.FORMATS), default="text")
    check.add_argument("--output", type=Path, help="write the report here instead of stdout")
    check.add_argument("--sarif", type=Path, help="also write SARIF 2.1.0 to this file")
    check.add_argument("--markdown", type=Path, help="also write a Markdown summary here")
    check.add_argument("--no-guards", action="store_true", help="skip the anti-cheating guards")
    check.add_argument("files", nargs="*", help="limit the check to these files")
    tools_arg(check)

    ratchet = sub.add_parser("ratchet", help="drop fixed findings from the baseline")
    tools_arg(ratchet)

    explain = sub.add_parser("explain", help="explain a code (AL201) or a fingerprint")
    explain.add_argument("what", nargs="?", help="code or fingerprint prefix; omit to list codes")

    sub.add_parser("tools", help="show which linters are installed and used")

    hook = sub.add_parser("hook", help="Claude Code hook (reads the event JSON on stdin)")
    hook.add_argument("--print-config", action="store_true", help="print settings.json hooks")
    return p


def _root(arg: Path | None) -> Path:
    if arg is not None:
        if not arg.is_dir():
            raise engine.UsageError(f"--root {arg} is not a directory")
        return arg.resolve()
    return (Git.toplevel(Path.cwd()) or Path.cwd()).resolve()


def _write(path: Path | None, content: str) -> None:
    if path is None:
        sys.stdout.write(content)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render(fmt: str, report: Report) -> str:
    if fmt == "json":
        return render.as_json(report)
    if fmt == "markdown":
        return render.markdown(report)
    if fmt == "sarif":
        return render.sarif(report)
    return render.text(report)


def _explain(root: Path, what: str | None) -> int:
    if not what:
        for entry in CATALOG.values():
            print(f"{entry.code}  {entry.title}")
        return EXIT_OK
    found = CATALOG.get(what.upper())
    if found:
        entry = found
        print(f"{entry.code}: {entry.title}\n\nWhy: {entry.why}\n\nFix: {entry.fix}")
        return EXIT_OK
    try:
        current = baseline_mod.load(root)
    except BaselineError as exc:
        raise engine.UsageError(str(exc)) from exc
    if current is not None and len(what) >= 6:
        hits = [f for fp, f in current.entries.items() if fp.startswith(what.lower())]
        if len(hits) == 1:
            f = hits[0]
            print(f"{f.fingerprint}: {f.tool} {f.rule} in {f.path}\n  {f.message}")
            print(
                f"\nIn {BASELINE_FILE}: an existing finding the gate tolerates until it is fixed."
            )
            return EXIT_OK
        if len(hits) > 1:
            print(f"ambiguous fingerprint prefix {what!r} ({len(hits)} matches)")
            return EXIT_ERROR
    print(f"unknown code or fingerprint: {what}", file=sys.stderr)
    return EXIT_ERROR


def _tools(project: engine.Project) -> int:
    ctx = project.context
    configured = project.config.tools
    for name, cls in RUNNERS.items():
        runner = cls()
        binary = runner.binary(ctx)
        if configured is not None and name not in configured:
            state = "disabled in config"
        elif binary is None:
            state = "not installed"
        elif not runner.applies(ctx):
            state = "installed, nothing to check"
        else:
            state = f"active ({binary})"
        print(f"{name:<11} {state}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "hook":
            if args.print_config:
                sys.stdout.write(hooks.settings_json())
                return EXIT_OK
            return hooks.main()
        root = _root(args.root)
        if args.command == "explain":
            return _explain(root, args.what)
        project = engine.Project.open(root, load_config(root))
        if args.command == "tools":
            return _tools(project)
        if args.command == "init":
            report = engine.init(project, force=args.force, tools=args.tool)
            if report.failures:
                sys.stdout.write(render.text(report))
                return EXIT_FAILED
            print(
                f"agent-lint: baseline written to {BASELINE_FILE} with "
                f"{report.current_size} finding(s) from {', '.join(report.tools) or 'no tools'}"
            )
            return EXIT_OK
        if args.command == "ratchet":
            report, removed = engine.ratchet(project, tools=args.tool)
            sys.stdout.write(render.text(report))
            print(f"agent-lint: removed {removed} fixed finding(s) from {BASELINE_FILE}")
            return EXIT_OK if report.passed else EXIT_FAILED
        # check
        files = None
        if args.files:
            files = [r for r in (relative(root, str(Path(f).absolute())) for f in args.files) if r]
            if not files:
                raise engine.UsageError("the given files are all outside the project")
        report = engine.check(
            project, base=args.base, tools=args.tool, files=files, run_guards=not args.no_guards
        )
        _write(args.output, _render(args.format, report))
        if args.sarif:
            _write(args.sarif, render.sarif(report))
        if args.markdown:
            _write(args.markdown, render.markdown(report))
        return EXIT_OK if report.passed else EXIT_FAILED
    except (engine.UsageError, ConfigError, BaselineError, GitError) as exc:
        print(f"agent-lint: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
