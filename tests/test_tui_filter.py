"""Pure unit tests for the TUI filter helpers — they're easy to drive
without spinning up curses, and the integration into ``run_*_view`` is
covered by the visibility / undo tests."""

from __future__ import annotations

from snapz.store import SnapshotMeta
from snapz.tui import _filter_predicate


def _meta(name: str, note: str = "") -> SnapshotMeta:
    return SnapshotMeta(
        name=name,
        source="/tmp/proj",
        created="2026-04-29T20:00:00",
        size_bytes=0,
        file_count=0,
        total_bytes_in=0,
        compression="zstd-cas",
        archive=f"{name}.manifest.json",
        note=note,
    )


def test_empty_pattern_matches_everything():
    pred = _filter_predicate("")
    assert pred(_meta("v1"))
    assert pred(_meta("auto-20260101-000000"))


def test_substring_matches_name():
    pred = _filter_predicate("rel")
    assert pred(_meta("release-1.0"))
    assert not pred(_meta("v1"))


def test_substring_is_case_insensitive():
    pred = _filter_predicate("RELEASE")
    assert pred(_meta("release-1.0"))


def test_substring_matches_note():
    pred = _filter_predicate("refactor")
    assert pred(_meta("v1", note="before huge refactor"))
    assert not pred(_meta("v1", note="something else"))


def test_pattern_is_stripped():
    pred = _filter_predicate("   v1   ")
    assert pred(_meta("v1"))
    assert pred(_meta("v10"))
    assert not pred(_meta("v2"))
