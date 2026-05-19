"""Compatibility checks for supported Python versions."""

from __future__ import annotations

from pathlib import Path

from tools.check_py310_fstrings import find_python310_compat_issues


def test_fstring_expressions_are_python310_compatible():
    roots = [Path("snapz"), Path("snapz_server"), Path("tests")]
    offenders = [
        (path, position)
        for root in roots
        for path in root.rglob("*.py")
        for position in find_python310_compat_issues(path)
    ]

    assert offenders == []


def test_python310_check_rejects_pep701_fstring_expression(tmp_path):
    path = tmp_path / "bad.py"
    path.write_text(
        'x = f"{st.bold(\'\\\\u21a9\')}"\n',
        encoding="utf-8",
    )

    issues = find_python310_compat_issues(path)

    assert issues
