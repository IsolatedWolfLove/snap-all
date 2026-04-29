"""Pure-function tests for :mod:`snapz.tui`.

The curses main loop is intentionally *not* exercised here — that's
manual-validation territory. We only assert on the helpers that decide
what the screen would show, so regressions in column layout or row
construction are caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import api, tui


# ---------- pure helpers ----------


def test_truncate_short_text_unchanged():
    assert tui._truncate("hi", 10) == "hi"


def test_truncate_long_text_uses_ellipsis():
    out = tui._truncate("abcdefghij", 5)
    assert out.endswith("…")
    assert len(out) == 5


def test_format_row_pads_columns():
    cols = [
        tui._Column(header="A", width=4),
        tui._Column(header="B", width=6, align="right"),
    ]
    out = tui._format_row(["xx", "1"], cols)
    # left-pad column to 4, gap of 2 spaces, right-pad column to 6
    assert out == "xx".ljust(4) + "  " + "1".rjust(6)
    assert len(out) == 4 + 2 + 6


def test_compute_columns_assigns_flex_widths():
    headers = [
        ("NAME", "left", -3),
        ("SIZE", "right", 10),
    ]
    cols = tui._compute_columns(headers, term_width=40)
    assert cols[1].width == 10
    assert cols[1].align == "right"
    # Flex column should consume the rest minus gaps and fixed cols
    assert cols[0].width >= 8
    assert cols[0].width <= 40


def test_compute_columns_handles_narrow_terminal():
    cols = tui._compute_columns(
        [("A", "left", -1), ("B", "right", 10)],
        term_width=12,
    )
    assert cols[1].width == 10
    assert cols[0].width >= 8  # honors min width even when squeezed


# ---------- row builders ----------


def test_build_list_rows_includes_metadata(project_dir, config):
    api.save(project_dir, "v1", config=config)
    rows = tui._build_list_rows(api.list_snapshots(project_dir, config=config), project_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row.snapshot.name == "v1"
    assert row.abspath == project_dir
    # Columns: name, created, size, files
    assert row.columns[0] == "v1"
    assert row.columns[3].endswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))


def test_build_alist_rows_flattens_per_dir(tmp_path, config):
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("a", encoding="utf-8")
    (b / "g.txt").write_text("b", encoding="utf-8")
    api.save(a, "first", config=config)
    api.save(b, "first", config=config)

    entries = api.list_all(config=config)
    rows = tui._build_alist_rows(entries)
    dir_labels = {row.columns[0] for row in rows}
    assert dir_labels == {"alpha", "beta"}
    # Each row carries the proper abspath for restore deferral
    for row in rows:
        assert row.abspath in (a.resolve(), b.resolve())


def test_deferred_restore_sentinel_holds_path_and_name():
    sentinel = tui.DeferredRestore(abspath=Path("/x"), snapshot_name="v1")
    assert sentinel.abspath == Path("/x")
    assert sentinel.snapshot_name == "v1"
