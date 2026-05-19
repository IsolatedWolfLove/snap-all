#!/usr/bin/env python3
"""Find syntax that is invalid on Python 3.10.

Python 3.12 relaxed f-string parsing through PEP 701. The package still
supports Python 3.10, so release checks need to reject replacement
expressions that contain backslashes even when the build host is newer.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path


DEFAULT_PATHS = ("snapz", "snapz_server", "tests")
FSTRING_START = getattr(tokenize, "FSTRING_START", None)
FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _python_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.py"))
    return sorted(files)


def _literal_quote(token_text: str) -> str:
    idx = 0
    while idx < len(token_text) and token_text[idx].lower() in "rubf":
        idx += 1
    quote = token_text[idx:]
    if quote.startswith('"""') or quote.startswith("'''"):
        return quote[:3]
    return quote[:1]


def find_pep701_fstring_expressions(path: Path) -> list[tuple[int, int, str]]:
    """Return f-string expressions that Python 3.10/3.11 would reject.

    Older Python versions reject backslashes and comments inside replacement
    fields. They also cannot reuse the outer quote character inside a
    single-quoted f-string expression. Python < 3.12 catches this during
    parsing, so the token-level fallback is only needed on newer hosts.
    """

    if FSTRING_START is None or FSTRING_END is None:
        return []

    source = path.read_text(encoding="utf-8")
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    fstring_quotes: list[str] = []
    expression_depth = 0
    violations: list[tuple[int, int, str]] = []

    for tok in tokens:
        if tok.type == FSTRING_START:
            quote = _literal_quote(tok.string)
            if (
                expression_depth
                and fstring_quotes
                and len(fstring_quotes[-1]) == 1
                and quote == fstring_quotes[-1]
            ):
                violations.append((
                    tok.start[0],
                    tok.start[1],
                    "f-string expression reuses the outer quote",
                ))
            fstring_quotes.append(quote)
            continue
        if tok.type == FSTRING_END:
            fstring_quotes.pop()
            continue
        if not fstring_quotes:
            continue
        if tok.type == tokenize.OP and tok.string == "{":
            expression_depth += 1
            continue
        if tok.type == tokenize.OP and tok.string == "}":
            if expression_depth:
                expression_depth -= 1
            continue
        if expression_depth and tok.type == tokenize.COMMENT:
            violations.append((
                tok.start[0],
                tok.start[1],
                "f-string expression contains a comment",
            ))
            continue
        if (
            expression_depth
            and tok.type == tokenize.STRING
            and fstring_quotes
            and len(fstring_quotes[-1]) == 1
            and _literal_quote(tok.string) == fstring_quotes[-1]
        ):
            violations.append((
                tok.start[0],
                tok.start[1],
                "f-string expression reuses the outer quote",
            ))
            continue
        if expression_depth and "\\" in tok.string:
            violations.append((
                tok.start[0],
                tok.start[1],
                "f-string expression contains a backslash",
            ))

    return violations


def find_python310_compat_issues(path: Path) -> list[tuple[int, int, str]]:
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=(3, 10))
    except SyntaxError as exc:
        return [(
            exc.lineno or 1,
            max((exc.offset or 1) - 1, 0),
            exc.msg,
        )]
    return find_pep701_fstring_expressions(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="check f-string expressions for Python 3.10 compatibility",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help="files or directories to scan",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    for path in _python_files([Path(p) for p in args.paths]):
        for line, col, message in find_python310_compat_issues(path):
            failures.append(f"{path}:{line}:{col + 1}: {message}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
