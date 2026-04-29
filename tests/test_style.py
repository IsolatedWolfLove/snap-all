"""Tests for :mod:`snapz.style`.

Pytest captures stdout, so colour auto-detect resolves to OFF by default
during tests — handy because every assertion here can compare against
plain strings.
"""

from __future__ import annotations

import pytest

from snapz import style as st


@pytest.fixture(autouse=True)
def _force_off():
    """Make every test deterministic regardless of host TTY."""
    prev = st.is_enabled()
    st.set_enabled(False)
    yield
    st.set_enabled(prev)


def test_disabled_passthrough():
    assert st.bold("hi") == "hi"
    assert st.red("err") == "err"
    assert st.muted("x") == "x"


def test_enabled_wraps_with_ansi():
    st.set_enabled(True)
    assert st.bold("hi") == "\x1b[1mhi\x1b[0m"
    assert st.red("x") == "\x1b[31mx\x1b[0m"
    assert st.cyan("y") == "\x1b[36my\x1b[0m"


def test_enabled_empty_string_short_circuit():
    st.set_enabled(True)
    # Empty string stays empty (no codes wrapping nothing).
    assert st.bold("") == ""


def test_semantic_helpers_route_to_primitives():
    st.set_enabled(True)
    assert st.heading("h") == st.bold("h")
    assert st.label("l") == st.dim("l")
    assert st.path("p") == st.cyan("p")
    assert st.numeric("9") == st.cyan("9")
    assert st.success("ok") == st.green("ok")
    assert st.error("e") == st.red("e")
    assert st.warn("w") == st.yellow("w")
    assert st.muted("m") == st.grey("m")


def test_composed_phrases():
    st.set_enabled(False)
    assert "error" in st.err_prefix()
    assert st.ok_mark() == "✓"
    assert st.fail_mark() == "✗"
    assert st.arrow() == "›"
    assert st.bullet() == "·"


def test_set_enabled_is_idempotent():
    st.set_enabled(False)
    st.set_enabled(False)
    assert st.is_enabled() is False
    st.set_enabled(True)
    assert st.is_enabled() is True


def test_no_color_env_disables(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # The module's _detect re-reads env each call.
    assert st._detect() is False


def test_force_color_env_enables(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert st._detect() is True


def test_dumb_term_disables(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert st._detect() is False
