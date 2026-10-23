from __future__ import annotations

import json

import pytest

from agent_lint.config import CONFIG_FILE
from agent_lint.config import Config
from agent_lint.guards import configs
from tests.conftest import Repo
from tests.conftest import codes


def _weakenings(path: str, old: str | None, new: str | None) -> list[str]:
    o = configs.policy_for(path, old) if old is not None else {}
    n = configs.policy_for(path, new) if new is not None else {}
    assert o is not None and n is not None
    return configs.compare(o, n)


RUFF_BASE = """
[tool.ruff]
line-length = 100
exclude = ["build"]

[tool.ruff.lint]
select = ["E", "F", "S"]
ignore = ["E203"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]

[tool.ruff.lint.mccabe]
max-complexity = 10
"""


def test_ruff_weakenings() -> None:
    new = """
[tool.ruff]
line-length = 140
exclude = ["build", "src/legacy"]

[tool.ruff.lint]
select = ["E", "F"]
ignore = ["E203", "E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]
"src/*" = ["F401"]

[tool.ruff.lint.mccabe]
max-complexity = 30
"""
    got = _weakenings("pyproject.toml", RUFF_BASE, new)
    assert got == [
        "ruff exclude: `src/legacy` added",
        "ruff ignore: `E501` added",
        "ruff line-length raised from 100 to 140",
        "ruff mccabe.max-complexity raised from 10 to 30",
        "ruff per-file-ignores: `src/*: F401` added",
        "ruff select: `S` removed",
    ]


def test_ruff_tightening_is_fine_and_ruff_toml() -> None:
    tighter = RUFF_BASE.replace('["E", "F", "S"]', '["E", "F", "S", "B"]').replace("100", "88")
    assert _weakenings("pyproject.toml", RUFF_BASE, tighter) == []
    assert _weakenings("ruff.toml", 'select = ["E"]\n', "select = []\n") == [
        "ruff select: `E` removed"
    ]
    assert _weakenings(
        ".ruff.toml", '[lint]\nextend-select = ["B"]\n', '[lint]\nextend-ignore = ["B9"]\n'
    ) == ["ruff ignore: `B9` added", "ruff select: `B` removed"]


def test_deleting_a_config_file_weakens_everything() -> None:
    assert "ruff select: `E` removed" in _weakenings("ruff.toml", 'select = ["E"]\n', None)


def test_mypy_toml() -> None:
    old = """
[tool.mypy]
strict = true
warn_unused_ignores = true
files = ["src"]
"""
    new = """
[tool.mypy]
strict = false
ignore_missing_imports = true
follow_imports = "skip"
disable_error_code = ["arg-type"]
exclude = ["src/old"]

[[tool.mypy.overrides]]
module = ["pkg.legacy.*"]
ignore_errors = true

[[tool.mypy.overrides]]
module = "pkg.api"
disable_error_code = ["union-attr"]
disallow_untyped_defs = false
"""
    got = _weakenings("pyproject.toml", old, new)
    assert got == [
        "mypy disable_error_code: `arg-type` added",
        "mypy exclude: `src/old` added",
        "mypy files: `src` removed",
        "mypy follow_imports=skip/silent turned on",
        "mypy ignore_missing_imports turned on",
        "mypy overrides ignore_errors: `pkg.legacy.*` added",
        "mypy overrides relaxed: `pkg.api: disallow_untyped_defs=false` added",
        "mypy overrides relaxed: `pkg.api: union-attr` added",
        "mypy strict turned off",
        "mypy warn_unused_ignores turned off",
    ]


def test_mypy_ini_and_setup_cfg() -> None:
    old = "[mypy]\nstrict = True\n"
    new = "[mypy]\nstrict = False\n\n[mypy-vendor.*]\nignore_errors = True\n"
    assert _weakenings("mypy.ini", old, new) == [
        "mypy overrides ignore_errors: `vendor.*` added",
        "mypy strict turned off",
    ]
    assert _weakenings("setup.cfg", old, new)[0].startswith("mypy overrides")
    assert configs.policy_for("mypy.ini", "not = an [ini") is None


def test_coverage_pytest_bandit() -> None:
    old = """
[tool.coverage.report]
fail_under = 90

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests", "integration"]

[tool.bandit]
skips = []
"""
    new = """
[tool.coverage.run]
omit = ["src/hard.py"]

[tool.coverage.report]
fail_under = 60

[tool.pytest.ini_options]
addopts = "-ra --strict-markers -k 'not slow' --deselect tests/test_a.py::test_x"
testpaths = ["tests"]

[tool.bandit]
skips = ["B602"]
"""
    got = _weakenings("pyproject.toml", old, new)
    assert got == [
        "bandit skips: `B602` added",
        "coverage fail_under lowered from 90 to 60",
        "coverage omit: `src/hard.py` added",
        "pytest addopts: `--deselect tests/test_a.py::test_x` added",
        "pytest addopts: `-k not slow` added",
        "pytest testpaths: `integration` removed",
    ]


def test_ini_files_for_coverage_pytest_bandit() -> None:
    assert _weakenings(".coveragerc", "[report]\nfail_under = 80\n", "[report]\n") == [
        "coverage fail_under lowered from 80 to 0"
    ]
    assert _weakenings("pytest.ini", "[pytest]\naddopts = -q\n", "[pytest]\naddopts = -q -x\n") == [
        "pytest addopts: `-x` added"
    ]
    assert _weakenings("tox.ini", "[pytest]\n", "[pytest]\naddopts = --ignore=tests/e2e\n") == [
        "pytest addopts: `--ignore=tests/e2e` added"
    ]
    assert _weakenings(
        "setup.cfg", "[coverage:report]\nfail_under = 80\n", "[coverage:report]\nfail_under = 79\n"
    ) == ["coverage fail_under lowered from 80 to 79"]
    assert _weakenings(".bandit", "[bandit]\nskips = B101\n", "[bandit]\nskips = B101,B602\n") == [
        "bandit skips: `B602` added"
    ]


def test_eslint_json_and_package_json() -> None:
    old = json.dumps(
        {
            "extends": ["eslint:recommended", "plugin:security/recommended"],
            "rules": {"eqeqeq": "error", "no-console": ["error"], "complexity": ["error", 10]},
            "overrides": [{"files": ["*.ts"], "rules": {"no-explicit-any": 2}}],
        }
    )
    new = (
        "// comment\n"
        + json.dumps(
            {
                "extends": ["eslint:recommended"],
                "ignorePatterns": ["src/legacy/**"],
                "rules": {"eqeqeq": "warn", "complexity": ["error", {"max": 25}]},
                "overrides": [{"files": ["*.ts"], "rules": {"no-explicit-any": 0}}],
            }
        )[:-1]
        + ",}"
    )
    got = _weakenings(".eslintrc.json", old, new)
    assert got == [
        "eslint extends: `plugin:security/recommended` removed",
        "eslint ignorePatterns: `src/legacy/**` added",
        "eslint limits: `complexity` raised from 10 to 25",
        "eslint rules: `eqeqeq` relaxed (2 -> 1)",
        "eslint rules: `no-console` relaxed (2 -> 0)",
        "eslint rules: `no-explicit-any` relaxed (2 -> 0)",
    ]
    pkg_old = json.dumps({"name": "x", "eslintConfig": {"rules": {"semi": "error"}}})
    pkg_new = json.dumps({"name": "x", "eslintConfig": {"rules": {"semi": "off"}}})
    assert _weakenings("package.json", pkg_old, pkg_new) == [
        "eslint rules: `semi` relaxed (2 -> 0)"
    ]


def test_tsconfig() -> None:
    old = '{\n  // strict!\n  "compilerOptions": {"strict": true, "noUnusedLocals": true},\n}'
    new = '{"compilerOptions": {"strict": true, "noImplicitAny": false, "noEmitOnError": false}, "exclude": ["src/old"]}'
    assert _weakenings("tsconfig.json", old, new) == [
        "tsconfig exclude: `src/old` added",
        "tsconfig noEmitOnError=false turned on",
        "tsconfig strict flags: `noImplicitAny` removed",
        "tsconfig strict flags: `noUnusedLocals` removed",
    ]
    assert (
        configs.strip_jsonc('{"a": "// not a comment", /* x */ "b": [1,],}')
        == '{"a": "// not a comment",  "b": [1]}'
    )


def test_ignore_files_and_precommit() -> None:
    assert _weakenings(".eslintignore", "dist\n", "dist\n# c\nsrc/\n") == [
        ".eslintignore patterns: `src/` added"
    ]
    old = "repos:\n- repo: local\n  hooks:\n  - id: ruff\n  - id: agent-lint\n"
    assert _weakenings(".pre-commit-config.yaml", old, "repos: []\n") == [
        "pre-commit hooks: `agent-lint` removed",
        "pre-commit hooks: `ruff` removed",
    ]
    assert configs.policy_for("README.md", "x") is None


def test_agent_lint_config_is_self_protected(repo: Repo) -> None:
    repo.write(CONFIG_FILE, 'tools = ["ruff", "mypy"]\nbase = "origin/main"\n')
    repo.write(".pre-commit-config.yaml", "repos:\n- hooks:\n  - id: agent-lint\n")
    repo.write(".github/workflows/ci.yml", "steps:\n  - run: agent-lint check\n")
    repo.commit()
    repo.write(
        CONFIG_FILE,
        'tools = ["ruff"]\nbase = "HEAD"\nexclude = ["src/**"]\ndisable = ["AL201", "AL302"]\n',
    )
    repo.write(".pre-commit-config.yaml", "repos: []\n")
    repo.write(".github/workflows/ci.yml", "steps:\n  - run: echo ok\n")
    found = repo.guards()
    assert set(codes(found)) == {"AL302"}
    messages = {v.message for v in found}
    assert "agent-lint tools: `mypy` removed" in messages
    assert "agent-lint base changed from `origin/main` to `HEAD`" in messages
    assert "agent-lint disable: `AL201` added" in messages
    assert "agent-lint removed from a CI workflow" in messages
    assert "pre-commit hooks: `agent-lint` removed" in messages


def test_flat_eslint_config_heuristic(repo: Repo) -> None:
    repo.write("eslint.config.js", "export default [\n  { rules: { eqeqeq: 'error' } },\n];\n")
    repo.commit()
    repo.write(
        "eslint.config.js",
        "export default [\n  { rules: { eqeqeq: 'off', semi: 0 } },\n  { ignores: ['src/**'] },\n];\n",
    )
    found = repo.guards()
    assert codes(found) == ["AL301", "AL301"]


def test_config_guard_in_repo_with_unparsable_file(repo: Repo) -> None:
    repo.write("pyproject.toml", RUFF_BASE)
    repo.commit()
    repo.write("pyproject.toml", "[tool.ruff\n")  # ruff itself will fail on this
    assert repo.guards(config=Config()) == []


@pytest.mark.parametrize("path", ["ruff.toml", "mypy.ini", ".eslintrc.json", "tsconfig.json"])
def test_new_config_files_are_compared_against_nothing(path: str) -> None:
    text = {
        "ruff.toml": 'ignore = ["E501"]\n',
        "mypy.ini": "[mypy]\nignore_errors = True\n",
        ".eslintrc.json": '{"ignorePatterns": ["x"]}',
        "tsconfig.json": '{"exclude": ["x"]}',
    }[path]
    assert _weakenings(path, None, text)
