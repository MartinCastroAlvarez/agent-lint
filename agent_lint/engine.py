"""Orchestration: run linters, compare with the baseline, run the guards."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_lint import baseline as baseline_mod
from agent_lint import guards
from agent_lint.baseline import Baseline
from agent_lint.config import DEFAULT_BASE
from agent_lint.config import Config
from agent_lint.config import effective_base
from agent_lint.fingerprint import assign
from agent_lint.gitio import Git
from agent_lint.gitio import GitError
from agent_lint.models import Finding
from agent_lint.models import Report
from agent_lint.models import RunnerFailure
from agent_lint.paths import matches
from agent_lint.paths import walk
from agent_lint.runners import RUNNERS
from agent_lint.runners import Context
from agent_lint.runners import Runner
from agent_lint.runners import RunnerError


class UsageError(ValueError):
    """Bad arguments (unknown tool, unresolvable base ref, missing baseline)."""


@dataclass(slots=True)
class Project:
    root: Path
    config: Config
    git: Git | None
    files: tuple[str, ...]

    @classmethod
    def open(cls, root: Path, config: Config) -> Project:
        root = root.resolve()
        git = Git(root) if Git.toplevel(root) is not None else None
        files: list[str]
        if git is not None:
            try:
                files = git.tracked()
            except GitError:
                files = walk(root)
        else:
            files = walk(root)
        return cls(root, config, git, tuple(files))

    @property
    def context(self) -> Context:
        return Context(self.root, self.config, self.files)


def select(
    project: Project,
    requested: Sequence[str] | None,
    report: Report,
    baseline: Baseline,
    *,
    hook: bool = False,
) -> list[Runner]:
    """Runners to use; missing *required* ones become failures, others warnings."""
    unknown = set(requested or ()) - set(RUNNERS)
    if unknown:
        raise UsageError(f"unknown tools: {', '.join(sorted(unknown))}")
    names = list(requested or project.config.tools or RUNNERS)
    required = bool(requested) or project.config.tools is not None
    if hook and not requested:
        # The per-edit hook runs only the fast runners; the Stop hook runs them all.
        names = [n for n in names if n in project.config.hook_tools]
        required = False
    ctx = project.context
    chosen: list[Runner] = []
    baselined = {e.tool for e in baseline.entries.values()}
    for name in names:
        runner = RUNNERS[name]()
        if runner.binary(ctx) is None:
            if required:
                report.failures.append(RunnerFailure(name, "required but not installed"))
            elif name in baselined:
                report.warnings.append(
                    f"{name} is not installed; its baseline entries are kept unchanged"
                )
            continue
        if not runner.applies(ctx):
            continue
        chosen.append(runner)
    return chosen


def collect(
    project: Project, runners: Sequence[Runner], files: Sequence[str] | None, report: Report
) -> list[Finding]:
    ctx = project.context
    findings: list[Finding] = []
    for runner in runners:
        try:
            raw = runner.run(ctx, files)
        except RunnerError as exc:
            report.failures.append(RunnerFailure(runner.name, str(exc)))
            continue
        report.tools.append(runner.name)
        findings.extend(f for f in raw if not matches(f.path, project.config.exclude))
    return assign(findings, project.root)


def _guards(
    project: Project, base: str | None, files: Sequence[str] | None, report: Report
) -> None:
    ref = effective_base(project.config, base)
    if project.git is None:
        report.warnings.append("not a git repository: guards skipped")
        return
    try:
        sha = project.git.resolve(ref)
    except GitError as exc:
        if ref == DEFAULT_BASE and base is None:
            report.warnings.append(f"no commits yet ({exc}): guards skipped")
            return
        raise UsageError(f"cannot resolve base ref {ref!r}: {exc}") from exc
    report.base = sha
    ctx = guards.DiffContext(
        project.root, project.git, sha, project.git.changes(sha), project.config
    )
    report.violations.extend(guards.run(ctx, files))


def _load_baseline(project: Project) -> Baseline | None:
    try:
        return baseline_mod.load(project.root)
    except baseline_mod.BaselineError as exc:
        raise UsageError(str(exc)) from exc


def check(
    project: Project,
    *,
    base: str | None = None,
    tools: Sequence[str] | None = None,
    files: Sequence[str] | None = None,
    run_guards: bool = True,
    hook: bool = False,
) -> Report:
    """Run linters + guards and compare against the baseline. Writes nothing."""
    report = Report()
    loaded = _load_baseline(project)
    if loaded is None:
        report.warnings.append("no baseline yet: run `agent-lint init` to adopt existing findings")
    current_baseline = loaded or Baseline.empty()
    runners = select(project, tools, report, current_baseline, hook=hook)
    current = collect(project, runners, files, report)
    wanted = set(files) if files is not None else None

    def in_scope(f: Finding) -> bool:
        return f.tool in report.tools and (wanted is None or f.path in wanted)

    present = {f.fingerprint for f in current}
    report.new = [f for f in current if f.fingerprint not in current_baseline.entries]
    report.fixed = [
        e for fp, e in current_baseline.entries.items() if in_scope(e) and fp not in present
    ]
    report.baseline_size = len(current_baseline)
    report.current_size = len(current)
    if run_guards:
        _guards(project, base, files, report)
    return report


def ratchet(project: Project, *, tools: Sequence[str] | None = None) -> tuple[Report, int]:
    """Drop fixed findings from the baseline. Never adds an entry.

    Returns the report and how many entries were removed.
    """
    current = _load_baseline(project)
    if current is None:
        raise UsageError("no baseline: run `agent-lint init` first")
    report = check(project, tools=tools, run_guards=False)
    if report.fixed:
        baseline_mod.save(project.root, current.without(f.fingerprint for f in report.fixed))
    return report, len(report.fixed)


def init(project: Project, *, force: bool = False, tools: Sequence[str] | None = None) -> Report:
    """Record every current finding as the starting baseline."""
    if not force and _load_baseline(project) is not None:
        raise UsageError("baseline already exists (use `agent-lint ratchet` to tighten it)")
    report = Report()
    runners = select(project, tools, report, Baseline.empty())
    current = collect(project, runners, None, report)
    if report.failures:
        return report
    baseline_mod.save(project.root, Baseline.from_findings(current))
    report.current_size = len(current)
    report.baseline_size = len(current)
    return report
