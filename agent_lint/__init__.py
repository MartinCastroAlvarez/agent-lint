"""agent-lint: a ratcheting lint gate for code written by AI coding agents.

Run the project's own linters, compare their findings with a committed
baseline (``.agent-lint/baseline.json``) and fail on anything new; refuse the
usual shortcuts an agent takes to get a green check (suppression comments,
skipped or deleted tests, weakened linter config, a growing baseline).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
