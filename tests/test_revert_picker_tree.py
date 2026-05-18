"""Pure helper tests for the revert browser picker."""

from __future__ import annotations

from snapz import cas, tui


def _entries():
    return [
        cas.ManifestEntry(path="README.md", type="file", size=6, sha256="a"),
        cas.ManifestEntry(path="data/input.txt", type="file", size=12, sha256="b"),
        cas.ManifestEntry(path="src/lib.py", type="file", size=18, sha256="c"),
        cas.ManifestEntry(path="src/main.py", type="file", size=12, sha256="d"),
        cas.ManifestEntry(path="src/link", type="symlink", target="../README.md"),
    ]


def test_revert_browser_rows_group_directories_at_root():
    rows = tui._revert_browser_rows(_entries(), "", {"src/main.py"})

    assert [(r.name, r.kind, r.path) for r in rows] == [
        ("data/", "dir", "data"),
        ("src/", "dir", "src"),
        ("README.md", "file", "README.md"),
    ]
    src = rows[1]
    assert src.differs is True
    assert src.size == 3


def test_revert_browser_rows_can_drill_into_directory():
    rows = tui._revert_browser_rows(_entries(), "src", set())

    assert rows[0].kind == "up"
    assert rows[0].path == ""
    assert [(r.name, r.kind) for r in rows[1:]] == [
        ("lib.py", "file"),
        ("link", "symlink"),
        ("main.py", "file"),
    ]


def test_revert_toggle_directory_selects_descendants():
    selected: set[str] = set()
    entries = _entries()

    tui._revert_toggle_path(entries, "src", selected)
    assert selected == {"src/lib.py", "src/main.py", "src/link"}
    row = tui._revert_browser_rows(entries, "", set())[1]
    assert tui._revert_selection_marker(row, entries, selected) == "\u2713"

    selected.remove("src/link")
    assert tui._revert_selection_marker(row, entries, selected) == "~"

    tui._revert_toggle_path(entries, "src", selected)
    assert selected == {"src/lib.py", "src/main.py", "src/link"}
    tui._revert_toggle_path(entries, "src", selected)
    assert selected == set()
