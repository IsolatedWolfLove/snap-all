"""ANSI styling for snapz CLI/TUI output.

Respects ``NO_COLOR``, ``TERM=dumb``, and falls back to plain text when
``stdout`` isn't a TTY (so ``snapz list | grep`` doesn't get polluted
with escape codes). Set ``FORCE_COLOR=1`` to override the auto-detect.

Tests can call :func:`set_enabled` to make output deterministic.

This module is dependency-free on purpose; we want ``style.green('ok')``
to be a one-liner that does the right thing.
"""

from __future__ import annotations

import os
import sys

_ENABLED: bool = False


def _detect() -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _refresh() -> None:
    global _ENABLED
    _ENABLED = _detect()


_refresh()


def is_enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> None:
    """Force colour on/off; primarily for tests and piping."""
    global _ENABLED
    _ENABLED = bool(value)


def _wrap(code: str, text: str) -> str:
    if not _ENABLED or not text:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def bold(t: str) -> str:   return _wrap("1", t)
def dim(t: str) -> str:    return _wrap("2", t)
def italic(t: str) -> str: return _wrap("3", t)
def under(t: str) -> str:  return _wrap("4", t)
def red(t: str) -> str:    return _wrap("31", t)
def green(t: str) -> str:  return _wrap("32", t)
def yellow(t: str) -> str: return _wrap("33", t)
def blue(t: str) -> str:   return _wrap("34", t)
def magenta(t: str) -> str: return _wrap("35", t)
def cyan(t: str) -> str:   return _wrap("36", t)
def grey(t: str) -> str:   return _wrap("90", t)


# ---------------------------------------------------------------------------
# Semantic shortcuts (the rest of the codebase should prefer these so we
# can re-tune the palette in one place)
# ---------------------------------------------------------------------------


def heading(t: str) -> str:  return bold(t)
def label(t: str) -> str:    return dim(t)
def path(t: str) -> str:     return cyan(t)
def name(t: str) -> str:     return bold(t)
def numeric(t: str) -> str:  return cyan(t)
def success(t: str) -> str:  return green(t)
def error(t: str) -> str:    return red(t)
def warn(t: str) -> str:     return yellow(t)
def muted(t: str) -> str:    return grey(t)


# Common composed phrases
def err_prefix() -> str:
    return red(bold("error:"))


def ok_mark() -> str:
    return green("✓")


def fail_mark() -> str:
    return red("✗")


def arrow() -> str:
    return dim("›")


def bullet() -> str:
    return dim("·")
