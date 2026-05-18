"""Pure-function tests for the generalized manifest browser (P2).

The curses loop itself is covered indirectly by ``run_revert_picker``
integration paths — these tests focus on the structural helpers that
future consumers (cat/browse/grep) rely on.
"""

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


def test_browse_rows_groups_dirs_at_root() -> None:
    rows = tui.browse_rows(_entries(), "", set())
    kinds = [(r.name, r.kind) for r in rows]
    assert kinds == [
        ("data/", "dir"),
        ("src/", "dir"),
        ("README.md", "file"),
    ]


def test_browse_rows_differs_propagates_to_parent_dir() -> None:
    rows = tui.browse_rows(_entries(), "", {"src/main.py"})
    src_row = next(r for r in rows if r.name == "src/")
    assert src_row.differs is True
    readme_row = next(r for r in rows if r.name == "README.md")
    assert readme_row.differs is False


def test_browse_rows_drills_into_subdir_with_up_entry() -> None:
    rows = tui.browse_rows(_entries(), "src", set())
    assert rows[0].kind == "up"
    assert [(r.name, r.kind) for r in rows[1:]] == [
        ("lib.py", "file"),
        ("link", "symlink"),
        ("main.py", "file"),
    ]


def test_browse_toggle_selects_all_descendants() -> None:
    selected: set[str] = set()
    entries = _entries()
    tui.browse_toggle_path(entries, "src", selected)
    assert selected == {"src/lib.py", "src/main.py", "src/link"}

    # second call unselects
    tui.browse_toggle_path(entries, "src", selected)
    assert selected == set()


def test_browse_selection_marker_transitions() -> None:
    entries = _entries()
    rows = tui.browse_rows(entries, "", set())
    src_row = next(r for r in rows if r.name == "src/")

    selected: set[str] = set()
    assert tui.browse_selection_marker(src_row, entries, selected) == " "

    selected.add("src/main.py")
    assert tui.browse_selection_marker(src_row, entries, selected) == "~"

    selected.update({"src/lib.py", "src/link"})
    assert tui.browse_selection_marker(src_row, entries, selected) == "\u2713"


def test_browse_manifest_empty_returns_cancel() -> None:
    """Empty manifest short-circuits before curses init."""

    action = tui.browse_manifest([], title="t", mode="view")
    assert action.kind == "cancel"
    assert action.selected == []


def test_browse_manifest_rejects_unknown_mode() -> None:
    try:
        tui.browse_manifest(_entries(), title="t", mode="nope")
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_browse_action_defaults_and_dataclass() -> None:
    # ``selected`` default is the empty list (no mutable-default sharing).
    a = tui.BrowseAction(kind="cancel")
    b = tui.BrowseAction(kind="file", path="x")
    assert a.selected == [] and b.selected == []
    a.selected.append("lifted")
    assert b.selected == []


def test_backwards_compatible_private_aliases_still_work() -> None:
    entries = _entries()
    # The old private names that external tests may import still resolve.
    assert tui._revert_browser_rows is tui.browse_rows
    assert tui._revert_toggle_path is tui.browse_toggle_path
    assert tui._revert_selection_marker is tui.browse_selection_marker
    # And they still behave identically.
    sel: set[str] = set()
    tui._revert_toggle_path(entries, "data", sel)
    assert sel == {"data/input.txt"}
