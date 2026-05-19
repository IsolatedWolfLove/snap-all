"""Manifest browser and revert picker TUI."""

from __future__ import annotations

import curses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from snapz.i18n import t
from snapz.util import format_size
from snapz._tui_common import _init_colors, _read_filter_pattern, _truncate
from snapz._tui_diff import _run_file_preview


# ---------------------------------------------------------------------------
# Generic path-tree browser for manifest entries
#
# Used by ``snapz revert`` (select mode) and ``snapz browse`` / ``snapz
# cat`` (view mode). The pure helpers (``browse_rows``,
# ``browse_toggle_path``, ``browse_selection_marker``) have no curses
# dependency so they can be unit-tested directly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowseRow:
    """One visible row in the manifest browser."""

    path: str
    name: str
    kind: str              # "up" | "dir" | "file" | "symlink"
    size: int | None = None
    target: str | None = None
    differs: bool = False


@dataclass
class BrowseAction:
    """Outcome of ``browse_manifest``. *kind* is one of:

    - ``"apply"``  — select-mode: user confirmed the selection.
    - ``"file"``   — view-mode:   user opened a file (path in *path*).
    - ``"cancel"`` — user quit with q/Esc.
    """

    kind: str
    path: str = ""
    selected: list[str] = field(default_factory=list)


def _browse_parent(path: str) -> str:
    path = path.strip().strip("/")
    if not path or "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def _browse_descendant_paths(entries, path: str) -> set[str]:
    path = path.strip().strip("/")
    if not path:
        return {e.path for e in entries}
    prefix = path + "/"
    return {e.path for e in entries if e.path == path or e.path.startswith(prefix)}


def browse_toggle_path(entries, path: str, selected: set[str]) -> None:
    """Toggle (recursively for dirs) *path*'s membership in *selected*."""

    descendants = _browse_descendant_paths(entries, path)
    if not descendants:
        return
    if descendants <= selected:
        selected.difference_update(descendants)
    else:
        selected.update(descendants)


def browse_rows(entries, cwd: str, differs: set[str]) -> list[BrowseRow]:
    """Return the list of visible rows for directory *cwd*.

    Directories are grouped first, files after, both case-insensitively
    sorted. A synthetic ``../`` row is prepended when ``cwd`` is non-empty.
    """

    cwd = cwd.strip().strip("/")
    prefix = cwd + "/" if cwd else ""
    dirs: dict[str, tuple[str, int, bool]] = {}
    files: list[BrowseRow] = []

    for e in entries:
        if prefix and not e.path.startswith(prefix):
            continue
        rest = e.path[len(prefix):] if prefix else e.path
        if not rest:
            continue
        head, sep, _tail = rest.partition("/")
        child_path = prefix + head if prefix else head
        if sep:
            _name, count, changed = dirs.get(child_path, (head, 0, False))
            dirs[child_path] = (head, count + 1, changed or e.path in differs)
            continue
        files.append(BrowseRow(
            path=e.path,
            name=head,
            kind=e.type,
            size=e.size,
            target=e.target,
            differs=e.path in differs,
        ))

    rows: list[BrowseRow] = []
    if cwd:
        rows.append(BrowseRow(path=_browse_parent(cwd), name="../", kind="up"))
    for path, (name, count, changed) in sorted(
        dirs.items(), key=lambda item: item[1][0].casefold(),
    ):
        rows.append(BrowseRow(
            path=path,
            name=name + "/",
            kind="dir",
            size=count,
            differs=changed,
        ))
    rows.extend(sorted(files, key=lambda row: row.name.casefold()))
    return rows


def browse_selection_marker(row: BrowseRow, entries, selected: set[str]) -> str:
    if row.kind == "up":
        return " "
    descendants = _browse_descendant_paths(entries, row.path)
    if not descendants:
        return " "
    if descendants <= selected:
        return "\u2713"
    if descendants & selected:
        return "~"
    return " "


# ---- Backwards-compatible private aliases (older callers / tests) ----

_RevertBrowserRow = BrowseRow
_revert_parent = _browse_parent
_revert_descendant_paths = _browse_descendant_paths
_revert_toggle_path = browse_toggle_path
_revert_browser_rows = browse_rows
_revert_selection_marker = browse_selection_marker


def _compute_manifest_differs(entries, src) -> set[str]:
    """Return paths whose live content differs from the manifest.

    Best-effort — any OSError just leaves the path out of the set.
    """

    if src is None:
        return set()
    import os as _os
    differs: set[str] = set()
    for e in entries:
        try:
            live = Path(src) / e.path
            if e.type == "symlink":
                if not live.is_symlink():
                    differs.add(e.path)
                    continue
                if _os.readlink(live) != (e.target or ""):
                    differs.add(e.path)
            else:  # "file"
                if not live.is_file() or live.is_symlink():
                    differs.add(e.path)
                    continue
                if e.size is not None and live.stat().st_size != e.size:
                    differs.add(e.path)
        except OSError:
            continue
    return differs


def browse_manifest(
    entries,
    *,
    title: str,
    src: Optional[Path] = None,
    mode: str = "view",              # "view" | "select"
    preview: Optional[Callable[[str], Optional[bytes]]] = None,
    initial_filter: str = "",
    preselect: Optional[set[str]] = None,
    differs: Optional[set[str]] = None,
    footer_hint: Optional[str] = None,
) -> BrowseAction:
    """Interactive curses path-tree browser over *entries*.

    Modes:

    - ``"view"`` — read-only. Enter on a file returns
      ``BrowseAction(kind="file", path=...)`` so the caller can preview
      externally. q/Esc returns ``kind="cancel"``.
    - ``"select"`` — Space toggles rows (recursively for dirs), ``e`` or
      Enter on a non-drillable row returns
      ``BrowseAction(kind="apply", selected=[...])``. q/Esc returns
      ``kind="cancel"``.

    In view mode, *preview* is called when Enter opens a file/symlink;
    when omitted the function returns ``BrowseAction(kind="file")`` for
    the caller to handle externally. *initial_filter* narrows visible
    rows by substring-matching path/name and can be changed with ``/``.
    *preselect* seeds the selection (select mode). *differs* highlights
    rows that diverge from the live tree; callers that want the classic
    ``snapz revert`` pre-selection pass ``differs`` as ``preselect``.
    """

    if mode not in {"view", "select"}:
        raise ValueError("mode must be 'view' or 'select'")

    manifest_entries = list(entries)
    if not manifest_entries:
        return BrowseAction(kind="cancel")

    differs_set: set[str] = set(differs or ())
    selected: set[str] = set(preselect or ())

    def _filter_entries(pattern: str):
        needle = pattern.casefold().strip()
        if not needle:
            return manifest_entries
        return [
            e for e in manifest_entries
            if needle in e.path.casefold()
            or needle in Path(e.path).name.casefold()
        ]

    def _curses_main(stdscr) -> BrowseAction:
        nonlocal selected
        curses.curs_set(0)
        stdscr.keypad(True)
        attrs = _init_colors()
        cursor = 0
        scroll = 0
        cwd = ""
        filter_pattern = initial_filter

        while True:
            active_entries = _filter_entries(filter_pattern)
            active_differs = differs_set & {e.path for e in active_entries}
            view_rows = browse_rows(active_entries, cwd, active_differs)
            if not view_rows:
                view_rows = (
                    [BrowseRow(path=_browse_parent(cwd), name="../", kind="up")]
                    if cwd else []
                )
            if cursor >= len(view_rows):
                cursor = max(0, len(view_rows) - 1)

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            location = "/" + cwd if cwd else "/"
            banner = f" {title}  {src or ''}  {location} "
            if filter_pattern:
                banner += t(
                    "tui.filter_status",
                    pattern=filter_pattern,
                    n=len(active_entries),
                    total=len(manifest_entries),
                ) + " "
            try:
                stdscr.addstr(
                    0, 0, banner.ljust(width)[: width - 1],
                    attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
                )
            except curses.error:
                pass

            body_top = 1
            body_height = max(1, height - body_top - 1)
            if cursor < scroll:
                scroll = cursor
            elif cursor >= scroll + body_height:
                scroll = cursor - body_height + 1

            max_name = max((len(r.name) for r in view_rows), default=8)
            name_w = max(8, min(width - 26, max_name))
            for i in range(body_height):
                idx = scroll + i
                if idx >= len(view_rows):
                    break
                row = view_rows[idx]
                base = (
                    attrs.get("cursor", curses.A_REVERSE) if idx == cursor else 0
                )
                if mode == "select":
                    marker = browse_selection_marker(
                        row, active_entries, selected,
                    )
                    marker_cell = f"  [{marker}] "
                    name_col = 6
                else:
                    marker_cell = "  "
                    name_col = 2
                row_attr = (
                    attrs.get("warn", curses.A_BOLD) if row.differs
                    else attrs.get("name", 0)
                ) | base
                if row.kind == "up":
                    row_attr = attrs.get("dim", curses.A_DIM) | base
                    info = "parent"
                elif row.kind == "dir":
                    info = f"{row.size or 0} entries"
                elif row.kind == "file":
                    info = format_size(row.size or 0)
                else:
                    info = f"\u2192 {row.target or ''}"
                try:
                    stdscr.addstr(body_top + i, 0, marker_cell, base)
                    stdscr.addstr(
                        body_top + i, name_col,
                        _truncate(row.name, name_w).ljust(name_w),
                        row_attr,
                    )
                    stdscr.addstr(
                        body_top + i, name_col + name_w + 2,
                        _truncate(info, max(0, width - name_col - name_w - 3)),
                        attrs.get("dim", curses.A_DIM) | base,
                    )
                except curses.error:
                    pass

            if footer_hint is not None:
                footer_text = footer_hint
            elif mode == "select":
                footer_text = t("revert.picker_footer", n=len(selected))
            else:
                footer_text = t("browse.footer")
            footer = " " + footer_text + " "
            try:
                stdscr.addstr(
                    height - 1, 0, footer.ljust(width)[: width - 1],
                    attrs.get("dim", curses.A_DIM),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), 27):
                if key == 27 and filter_pattern:
                    filter_pattern = ""
                    cwd = ""
                    cursor = scroll = 0
                    continue
                return BrowseAction(kind="cancel")
            if key == ord("/"):
                entered = _read_filter_pattern(
                    stdscr, attrs=attrs, initial=filter_pattern,
                )
                if entered is not None:
                    filter_pattern = entered
                    cwd = ""
                    cursor = scroll = 0
                continue
            if key == curses.KEY_RESIZE:
                continue
            if key in (curses.KEY_DOWN, ord("j")) and view_rows:
                cursor = min(cursor + 1, len(view_rows) - 1)
            elif key in (curses.KEY_UP, ord("k")) and view_rows:
                cursor = max(cursor - 1, 0)
            elif key == curses.KEY_NPAGE and view_rows:
                cursor = min(cursor + body_height, len(view_rows) - 1)
            elif key == curses.KEY_PPAGE and view_rows:
                cursor = max(cursor - body_height, 0)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END and view_rows:
                cursor = len(view_rows) - 1
            elif key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 8, 127, ord("h")):
                if cwd:
                    cwd = _browse_parent(cwd)
                    cursor = scroll = 0
            elif key in (curses.KEY_RIGHT, ord("l")) and view_rows:
                row = view_rows[cursor]
                if row.kind == "dir":
                    cwd = row.path
                    cursor = scroll = 0
            elif key == ord(" ") and view_rows and mode == "select":
                row = view_rows[cursor]
                if row.kind != "up":
                    browse_toggle_path(active_entries, row.path, selected)
            elif key == ord("a") and mode == "select":
                selected = {r.path for r in active_entries}
            elif key == ord("c") and mode == "select":
                selected = set(active_differs)
            elif key == ord("n") and mode == "select":
                selected.clear()
            elif key in (10, 13, curses.KEY_ENTER) and view_rows:
                row = view_rows[cursor]
                if row.kind == "dir":
                    cwd = row.path
                    cursor = scroll = 0
                elif row.kind == "up" and cwd:
                    cwd = _browse_parent(cwd)
                    cursor = scroll = 0
                elif mode == "select":
                    return BrowseAction(
                        kind="apply", selected=sorted(selected),
                    )
                elif mode == "view" and row.kind in ("file", "symlink"):
                    if preview is not None:
                        _run_file_preview(
                            stdscr,
                            title=title,
                            relpath=row.path,
                            data=preview(row.path),
                            attrs=attrs,
                        )
                        continue
                    return BrowseAction(kind="file", path=row.path)
            elif key == ord("r") and mode == "view" and view_rows:
                row = view_rows[cursor]
                if row.kind in ("file", "symlink"):
                    return BrowseAction(kind="file", path=row.path)
            elif key == ord("e") and mode == "select":
                return BrowseAction(kind="apply", selected=sorted(selected))

    return curses.wrapper(_curses_main)


def run_revert_picker(entries, src) -> list[str]:
    """Browse a snapshot manifest and select paths to revert.

    Thin wrapper over :func:`browse_manifest` in select mode, pre-seeded
    with the rows that differ from the live tree.
    """

    manifest_entries = list(entries)
    if not manifest_entries:
        return []

    differs = _compute_manifest_differs(manifest_entries, src)
    action = browse_manifest(
        manifest_entries,
        title=t("revert.picker_title", n=len(manifest_entries), src=""),
        src=src,
        mode="select",
        preselect=set(differs),
        differs=differs,
    )
    return action.selected if action.kind == "apply" else []
