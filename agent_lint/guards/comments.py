"""Extract comments from source text without executing or importing it."""

from __future__ import annotations

import io
import tokenize

PY_EXT = (".py", ".pyi")
JS_EXT = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte")
SH_EXT = (".sh", ".bash", ".zsh", ".ksh", ".bats")
HASH_EXT = (*SH_EXT, ".yml", ".yaml", ".toml", ".cfg", ".ini", ".rb", ".r")


def language(path: str) -> str | None:
    if path.endswith(PY_EXT):
        return "python"
    if path.endswith(JS_EXT):
        return "js"
    if path.endswith(HASH_EXT) or path.rsplit("/", 1)[-1] in ("Dockerfile", "Makefile"):
        return "hash"
    return None


def python_comments(text: str) -> list[tuple[int, str]]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return hash_comments(text)
    return [(t.start[0], t.string) for t in tokens if t.type == tokenize.COMMENT]


def hash_comments(text: str) -> list[tuple[int, str]]:
    """``#`` comments, skipping ``#`` inside simple single/double-quoted strings."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        quote: str | None = None
        prev = ""
        for i, ch in enumerate(line):
            if quote:
                if ch == quote and prev != "\\":
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t;("):
                out.append((lineno, line[i:]))
                break
            prev = ch
    return out


def js_comments(text: str) -> list[tuple[int, str]]:
    """``//`` and ``/* */`` comments; strings and template literals are skipped.

    Regex literals are not recognised, which can only make us *see* a
    comment-looking string in a regex — erring on the side of reporting.
    """
    out: list[tuple[int, str]] = []
    i, n, line = 0, len(text), 1
    quote: str | None = None
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        if quote:
            if ch == "\\":
                if i + 1 < n and text[i + 1] == "\n":
                    line += 1
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append((line, text[i:end]))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            chunk = text[i:end]
            # Report each line of a block comment on its own line number.
            for offset, part in enumerate(chunk.split("\n")):
                out.append((line + offset, part))
            line += chunk.count("\n")
            i = end
            continue
        i += 1
    return out


def comments(path: str, text: str) -> list[tuple[int, str]]:
    lang = language(path)
    if lang == "python":
        return python_comments(text)
    if lang == "js":
        return js_comments(text)
    if lang == "hash":
        return hash_comments(text)
    return []
