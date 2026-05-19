"""Snapshot and archive picker views."""

from __future__ import annotations

import curses
from dataclasses import dataclass
from typing import Optional

from snapz.i18n import t
from snapz.store import DirEntry, SnapshotMeta
from snapz.util import format_iso, format_size
from snapz._tui_common import (
    LIVE,
    _init_colors,
    _prompt_input,
    _read_filter_pattern,
    _truncate,
)


# ---------------------------------------------------------------------------
# Snapshot name picker (used by rm / mv / show / restore / export / diff /
# revert when the positional snapshot name is missing)
# ---------------------------------------------------------------------------


def run_snapshot_picker(
    snaps: list[SnapshotMeta],
    *,
    title: str,
    allow_live: bool = False,
) -> Optional[str]:
    """Single-select picker over a flat list of snapshots.

    Returns the chosen snapshot name, the :data:`LIVE` sentinel when the
    user picks the synthetic ``[live]`` row (only available with
    ``allow_live=True``), or ``None`` on cancel / empty list.

    Press ``/`` to substring-filter rows by name + note;
    ``Esc`` clears the active filter (and only quits when the filter is
    already empty).
    """

    @dataclass
    class _PickerRow:
        key: str         # picker return value (snapshot name or LIVE)
        name: str        # display string (column 1)
        created: str
        size: str
        haystack: str    # casefolded "name\nnote" for filtering

    all_rows: list[_PickerRow] = []
    if allow_live:
        all_rows.append(_PickerRow(
            key=LIVE,
            name=t("picker.live_row"),
            created=t("picker.live_hint"),
            size="",
            haystack=t("picker.live_row").casefold(),
        ))
    for snapz in snaps:
        all_rows.append(_PickerRow(
            key=snapz.name,
            name=snapz.name,
            created=format_iso(snapz.created),
            size=format_size(snapz.size_bytes),
            haystack=f"{snapz.name}\n{snapz.note or ''}".casefold(),
        ))
    if not all_rows:
        return None

    def _filter_rows(pattern: str) -> list[_PickerRow]:
        if not pattern:
            return all_rows
        needle = pattern.casefold().strip()
        # The synthetic ``[live]`` row is always shown so users can pick
        # it without clearing the filter first.
        return [
            r for r in all_rows
            if r.key == LIVE or needle in r.haystack
        ]

    def _curses_main(stdscr) -> Optional[str]:
        curses.curs_set(0)
        stdscr.keypad(True)
        attrs = _init_colors()
        cursor = 0
        scroll = 0
        filter_pattern = ""
        rows = _filter_rows(filter_pattern)
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            display_title = title
            if filter_pattern:
                display_title = (
                    f"{title}  ·  "
                    + t(
                        "tui.filter_status",
                        pattern=filter_pattern,
                        n=len(rows), total=len(all_rows),
                    )
                )
            try:
                stdscr.addstr(
                    0, 0,
                    _truncate(" " + display_title + " ", width - 1).ljust(width - 1),
                    attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
                )
            except curses.error:
                pass

            body_top = 2
            body_height = max(1, height - body_top - 1)
            if rows:
                if cursor >= len(rows):
                    cursor = len(rows) - 1
                if cursor < 0:
                    cursor = 0
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + body_height:
                    scroll = cursor - body_height + 1
            else:
                scroll = 0

            if not rows:
                try:
                    stdscr.addstr(
                        body_top, 2,
                        "(no matches — Esc to clear filter, q to quit)",
                        attrs.get("dim", curses.A_DIM),
                    )
                except curses.error:
                    pass
            else:
                name_w = max(8, min(width - 32, max(len(r.name) for r in rows)))
                for i in range(body_height):
                    idx = scroll + i
                    if idx >= len(rows):
                        break
                    row = rows[idx]
                    base = (
                        attrs.get("cursor", curses.A_REVERSE) if idx == cursor else 0
                    )
                    caret = "\u25b8 " if idx == cursor else "  "
                    is_live = row.key == LIVE
                    name_attr = (
                        (attrs.get("warn", curses.A_BOLD) if is_live
                         else attrs.get("name", curses.A_BOLD))
                        | base
                    )
                    try:
                        stdscr.addstr(
                            body_top + i, 0, caret,
                            attrs.get("title", 0) | base,
                        )
                        stdscr.addstr(
                            body_top + i, 2,
                            _truncate(row.name, name_w).ljust(name_w),
                            name_attr,
                        )
                        stdscr.addstr(
                            body_top + i, 2 + name_w + 2,
                            _truncate(row.created, 18).ljust(18),
                            attrs.get("dim", curses.A_DIM) | base,
                        )
                        stdscr.addstr(
                            body_top + i, 2 + name_w + 2 + 18 + 2,
                            _truncate(row.size, 12).rjust(12),
                            attrs.get("num", 0) | base,
                        )
                    except curses.error:
                        pass

            footer = (
                " " + t("picker.snapshot_footer")
                + "  ·  " + t("tui.filter_hint") + " "
            )
            try:
                stdscr.addstr(
                    height - 1, 0, footer.ljust(width)[: width - 1],
                    attrs.get("dim", curses.A_DIM),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == ord("q"):
                return None
            if key == 27:
                if filter_pattern:
                    filter_pattern = ""
                    rows = _filter_rows(filter_pattern)
                    cursor = 0
                    continue
                return None
            if key == ord("/"):
                entered = _read_filter_pattern(
                    stdscr, attrs=attrs, initial=filter_pattern,
                )
                if entered is not None:
                    filter_pattern = entered
                    rows = _filter_rows(filter_pattern)
                    cursor = 0
                continue
            if key == curses.KEY_RESIZE:
                continue
            if not rows:
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                cursor = min(cursor + 1, len(rows) - 1)
            elif key in (curses.KEY_UP, ord("k")):
                cursor = max(cursor - 1, 0)
            elif key == curses.KEY_NPAGE:
                cursor = min(cursor + body_height, len(rows) - 1)
            elif key == curses.KEY_PPAGE:
                cursor = max(cursor - body_height, 0)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(rows) - 1
            elif key in (10, 13, curses.KEY_ENTER, ord(" ")):
                return rows[cursor].key

    return curses.wrapper(_curses_main)


def run_archive_picker(entries: list[DirEntry], *, title: str) -> Optional[str]:
    """Single-select picker over archived source directories."""

    if not entries:
        return None

    @dataclass
    class _ArchiveRow:
        key: str
        path: str
        reason: str
        snaps: str
        haystack: str

    all_rows = [
        _ArchiveRow(
            key=e.key,
            path=e.meta.abspath,
            reason=e.archive_reason or "archived",
            snaps=str(len(e.snapshots)),
            haystack=f"{e.key}\n{e.meta.abspath}\n{e.archive_reason}".casefold(),
        )
        for e in entries
    ]

    def _filter_rows(pattern: str) -> list[_ArchiveRow]:
        if not pattern:
            return all_rows
        needle = pattern.casefold().strip()
        return [r for r in all_rows if needle in r.haystack]

    def _curses_main(stdscr) -> Optional[str]:
        curses.curs_set(0)
        stdscr.keypad(True)
        attrs = _init_colors()
        cursor = 0
        scroll = 0
        filter_pattern = ""
        rows = _filter_rows(filter_pattern)
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            display_title = title
            if filter_pattern:
                display_title = (
                    f"{title}  ·  "
                    + t(
                        "tui.filter_status",
                        pattern=filter_pattern,
                        n=len(rows), total=len(all_rows),
                    )
                )
            try:
                stdscr.addstr(
                    0, 0,
                    _truncate(" " + display_title + " ", width - 1).ljust(width - 1),
                    attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
                )
            except curses.error:
                pass

            body_top = 2
            body_height = max(1, height - body_top - 1)
            if rows:
                cursor = max(0, min(cursor, len(rows) - 1))
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + body_height:
                    scroll = cursor - body_height + 1
            else:
                scroll = 0

            if not rows:
                try:
                    stdscr.addstr(
                        body_top, 2,
                        "(no matches — Esc to clear filter, q to quit)",
                        attrs.get("dim", curses.A_DIM),
                    )
                except curses.error:
                    pass
            else:
                path_w = max(10, width - 34)
                for i in range(body_height):
                    idx = scroll + i
                    if idx >= len(rows):
                        break
                    row = rows[idx]
                    base = attrs.get("cursor", curses.A_REVERSE) if idx == cursor else 0
                    caret = "\u25b8 " if idx == cursor else "  "
                    try:
                        stdscr.addstr(body_top + i, 0, caret, attrs.get("title", 0) | base)
                        stdscr.addstr(
                            body_top + i, 2,
                            _truncate(row.path, path_w).ljust(path_w),
                            attrs.get("name", curses.A_BOLD) | base,
                        )
                        stdscr.addstr(
                            body_top + i, 2 + path_w + 2,
                            row.snaps.rjust(5),
                            attrs.get("num", 0) | base,
                        )
                        stdscr.addstr(
                            body_top + i, 2 + path_w + 9,
                            _truncate(row.reason, 20),
                            attrs.get("dim", curses.A_DIM) | base,
                        )
                    except curses.error:
                        pass

            footer = " Enter select  ·  / filter  ·  q quit "
            try:
                stdscr.addstr(
                    height - 1, 0, footer.ljust(width)[: width - 1],
                    attrs.get("dim", curses.A_DIM),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == ord("q"):
                return None
            if key == 27:
                if filter_pattern:
                    filter_pattern = ""
                    rows = _filter_rows(filter_pattern)
                    cursor = 0
                    continue
                return None
            if key == ord("/"):
                entered = _read_filter_pattern(
                    stdscr, attrs=attrs, initial=filter_pattern,
                )
                if entered is not None:
                    filter_pattern = entered
                    rows = _filter_rows(filter_pattern)
                    cursor = 0
                continue
            if key == curses.KEY_RESIZE:
                continue
            if not rows:
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                cursor = min(cursor + 1, len(rows) - 1)
            elif key in (curses.KEY_UP, ord("k")):
                cursor = max(cursor - 1, 0)
            elif key == curses.KEY_NPAGE:
                cursor = min(cursor + body_height, len(rows) - 1)
            elif key == curses.KEY_PPAGE:
                cursor = max(cursor - body_height, 0)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(rows) - 1
            elif key in (10, 13, curses.KEY_ENTER, ord(" ")):
                return rows[cursor].key

    return curses.wrapper(_curses_main)


def prompt_text(label: str, *, initial: str = "") -> Optional[str]:
    """Block on a single-line text prompt drawn via :mod:`curses`.

    Used by ``snapz mv`` to ask for the new snapshot name when the user
    picked the old one through the snapshot picker. Returns the trimmed
    string, or ``None`` if the user pressed Esc.
    """

    def _curses_main(stdscr):
        curses.curs_set(0)
        attrs = _init_colors()
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        try:
            stdscr.addstr(
                0, 0,
                _truncate(" " + label + " ", width - 1).ljust(width - 1),
                attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
            )
        except curses.error:
            pass
        stdscr.refresh()
        return _prompt_input(stdscr, label, initial=initial)

    return curses.wrapper(_curses_main)
