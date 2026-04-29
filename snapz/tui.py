"""ncdu-style curses TUI for ``snapz list`` / ``snapz alist`` / ``snapz diff``.

Design goals:

- Single key bindings: ``j/k`` (or arrows), ``d`` delete, ``n`` rename,
  ``r`` restore, ``Enter`` details, ``q``/``Esc`` quit.
- Destructive actions stay inside curses (small confirmation popups);
  restore exits the loop and returns a sentinel so the CLI can suspend
  curses cleanly and run the existing two-step confirmation flow.
- No third-party dependencies — only stdlib :mod:`curses`.

Curses internals are intentionally walled off from the data layer:
:func:`run_list_view`, :func:`run_alist_view` and :func:`run_diff_view`
are the public entry points and only depend on :mod:`snapz.api`.
"""

from __future__ import annotations

import curses
import curses.textpad as textpad
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from snapz import api
from snapz.config import RuntimeConfig
from snapz.i18n import t
from snapz.store import DirEntry, SnapshotMeta
from snapz.util import (
    format_iso,
    format_size,
    is_auto_snapshot,
    validate_snapshot_name,
)

# Public sentinel for run_snapshot_picker(allow_live=True): when the user
# selects the synthetic "[live]" row we return this string instead of an
# actual snapshot name. CLI code compares against ``LIVE`` (don't use a
# magic string at the call site).
LIVE = "@live"

# ---------------------------------------------------------------------------
# Public sentinels returned by run_*_view
# ---------------------------------------------------------------------------


@dataclass
class DeferredRestore:
    """The user pressed ``r`` on a row; CLI should suspend curses and run
    the regular ``snapz restore`` confirmation flow."""

    abspath: Path
    snapshot_name: str


# ---------------------------------------------------------------------------
# Internal action enum
# ---------------------------------------------------------------------------


class _Action:
    NOOP = 0
    REFRESH = 1
    QUIT = 2
    DEFER = 3


@dataclass
class _Row:
    """Generic table row backing both list and alist views."""

    columns: list[str]
    snapshot: SnapshotMeta
    abspath: Path  # source dir the snapshot belongs to


@dataclass
class _Column:
    header: str
    width: int
    align: str = "left"  # "left" | "right"


# ---------------------------------------------------------------------------
# Rendering helpers (kept pure for unit testing)
# ---------------------------------------------------------------------------


def _truncate(text: str, width: int) -> str:
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def _format_row(values: list[str], columns: list[_Column], gap: int = 2) -> str:
    parts: list[str] = []
    for value, col in zip(values, columns):
        chunk = _truncate(value, col.width)
        if col.align == "right":
            parts.append(chunk.rjust(col.width))
        else:
            parts.append(chunk.ljust(col.width))
    return (" " * gap).join(parts)


def _compute_columns(headers: list[tuple[str, str, int]], term_width: int) -> list[_Column]:
    """Lay out columns to fit *term_width*.

    Each input is ``(header, align, weight_or_min_width)``. Columns with
    a positive weight share the leftover space; columns with a fixed
    width keep it.
    """

    fixed: list[tuple[int, _Column]] = []
    flex: list[tuple[int, str, str, int]] = []
    total_fixed = 0
    gap = 2
    gaps = max(len(headers) - 1, 0) * gap

    for index, (header, align, hint) in enumerate(headers):
        if hint < 0:
            # Negative number = ``-weight`` for flex column
            flex.append((index, header, align, -hint))
        else:
            col = _Column(header=header, width=hint, align=align)
            fixed.append((index, col))
            total_fixed += hint

    leftover = max(term_width - total_fixed - gaps, 0)
    flex_total_weight = sum(weight for _, _, _, weight in flex) or 1
    cols: list[Optional[_Column]] = [None] * len(headers)

    for index, col in fixed:
        cols[index] = col

    for index, header, align, weight in flex:
        share = max(8, int(leftover * weight / flex_total_weight))
        cols[index] = _Column(header=header, width=share, align=align)

    # Final pass to satisfy mypy / runtime guard
    return [c if c is not None else _Column(header="", width=8) for c in cols]


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _build_list_rows(snaps: list[SnapshotMeta], abspath: Path) -> list[_Row]:
    return [
        _Row(
            columns=[
                snapz.name,
                format_iso(snapz.created),
                format_size(snapz.size_bytes),
                f"{snapz.file_count:,}",
            ],
            snapshot=snapz,
            abspath=abspath,
        )
        for snapz in snaps
    ]


def _build_alist_rows(entries: list[DirEntry]) -> list[_Row]:
    rows: list[_Row] = []
    for entry in entries:
        abspath = Path(entry.meta.abspath) if entry.meta.abspath else Path()
        dir_label = abspath.name or entry.key
        for snapz in entry.snapshots:
            rows.append(
                _Row(
                    columns=[
                        dir_label,
                        snapz.name,
                        format_iso(snapz.created),
                        format_size(snapz.size_bytes),
                        f"{snapz.file_count:,}",
                    ],
                    snapshot=snapz,
                    abspath=abspath,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Curses rendering
# ---------------------------------------------------------------------------


# Key hint segments rendered at the bottom of the screen. Each entry is
# ``(key_label, action_text, key_color_attr_name)`` so we can highlight
# destructive actions in colour.
_HINT_SEGMENTS: list[tuple[str, str, str]] = [
    ("↑↓/jk", "move", "dim"),
    ("⏎", "details", "dim"),
    ("r", "restore", "ok"),
    ("d", "delete", "warn"),
    ("n", "rename", "dim"),
    ("q", "quit", "dim"),
]


def _init_colors() -> dict[str, int]:
    """Initialise colour pairs once and return a name->attr mapping.

    Falls back to monochrome attributes when the terminal can't do
    colour (e.g. ``TERM=dumb``).
    """

    attrs: dict[str, int] = {
        "title": curses.A_BOLD,
        "header": curses.A_DIM | curses.A_UNDERLINE,
        "name": curses.A_BOLD,
        "date": curses.A_DIM,
        "num": curses.A_NORMAL,
        "auto": curses.A_DIM,
        "ok": curses.A_BOLD,
        "warn": curses.A_BOLD,
        "dim": curses.A_DIM,
        "cursor": curses.A_REVERSE,
        "cursor_name": curses.A_REVERSE | curses.A_BOLD,
    }
    if not curses.has_colors():
        return attrs
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    pairs = {
        1: curses.COLOR_CYAN,    # title / num / path
        2: curses.COLOR_GREEN,   # ok
        3: curses.COLOR_YELLOW,  # warn
        4: curses.COLOR_RED,     # error
    }
    for idx, fg in pairs.items():
        try:
            curses.init_pair(idx, fg, bg)
        except curses.error:
            pass
    attrs["title"] = curses.color_pair(1) | curses.A_BOLD
    attrs["num"] = curses.color_pair(1)
    attrs["ok"] = curses.color_pair(2) | curses.A_BOLD
    attrs["warn"] = curses.color_pair(3) | curses.A_BOLD
    return attrs


def _addstr(stdscr, y: int, x: int, text: str, attr: int = 0) -> int:
    """Draw *text* at ``(y, x)`` and return the next x position.

    Swallows :class:`curses.error` raised by writing to the bottom-right
    cell, which is normal for ncurses.
    """

    if not text:
        return x
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass
    return x + len(text)


def _render_row(stdscr, y: int, row: _Row, columns: list[_Column],
                attrs: dict[str, int], *, selected: bool, max_width: int) -> None:
    """Render a data row column-by-column with semantic colours.

    Layout assumption: list view = ``[name, created, size, files]``;
    alist view = ``[dir, name, created, size, files]``. The column count
    determines which lookup to apply.
    """

    is_auto = row.snapshot.name.startswith("auto-")
    if is_auto:
        # Whole row dimmed; cursor still reverse-highlights it.
        per_col = ["auto"] * len(columns)
    elif len(columns) == 4:
        per_col = ["name", "date", "num", "num"]
    else:
        per_col = ["title", "name", "date", "num", "num"]

    base = attrs["cursor"] if selected else 0
    name_attr = (
        attrs["cursor_name"] if selected and not is_auto else attrs.get(per_col[0], 0) | base
    )
    if is_auto and selected:
        name_attr = attrs["cursor"]

    # Cursor caret
    caret = "▸ " if selected else "  "
    x = _addstr(stdscr, y, 0, caret, attrs["title"] if selected else attrs["dim"])

    gap = "  "
    for col_index, (col, value) in enumerate(zip(columns, row.columns)):
        chunk = _truncate(value, col.width)
        chunk = chunk.rjust(col.width) if col.align == "right" else chunk.ljust(col.width)
        if x + len(chunk) > max_width:
            return
        attr_name = per_col[col_index] if col_index < len(per_col) else "num"
        attr = attrs.get(attr_name, 0) | base
        if selected and col_index == 0 and not is_auto:
            attr = name_attr
        x = _addstr(stdscr, y, x, chunk, attr)
        if col_index < len(columns) - 1:
            x = _addstr(stdscr, y, x, gap, base)


def _draw_key_hints(stdscr, y: int, attrs: dict[str, int], width: int) -> None:
    # Append the "/ filter  ·  Esc clear" hint so it's discoverable
    # everywhere the main loop runs.
    segments = list(_HINT_SEGMENTS) + [("/", "filter", "dim")]
    x = 0
    sep = "  ·  "
    for i, (key, action, color_name) in enumerate(segments):
        if i > 0:
            if x + len(sep) > width:
                return
            x = _addstr(stdscr, y, x, sep, attrs["dim"])
        key_attr = attrs.get(color_name, attrs["dim"]) | curses.A_BOLD
        if x + len(key) + 1 + len(action) > width:
            return
        x = _addstr(stdscr, y, x, key, key_attr)
        x = _addstr(stdscr, y, x, " " + action, attrs["dim"])


# ---------------------------------------------------------------------------
# Filter helpers (`/` to enter, Esc to clear) — used by the list/alist views
# and the snapshot picker so common scrolling stays uniform.
# ---------------------------------------------------------------------------


def _filter_predicate(pattern: str) -> Callable[[SnapshotMeta], bool]:
    """Build a case-insensitive substring matcher over name + note."""

    needle = pattern.casefold().strip()

    def matches(meta: SnapshotMeta) -> bool:
        if not needle:
            return True
        haystack = f"{meta.name}\n{meta.note or ''}".casefold()
        return needle in haystack

    return matches


def _read_filter_pattern(
    stdscr, *, attrs: dict[str, int], initial: str = "",
) -> Optional[str]:
    """Block on a tiny ``/pattern`` prompt at the bottom-most line.

    Returns:
    - the typed pattern when the user presses Enter (may be the empty
      string, which the caller treats as "clear filter");
    - ``None`` when the user pressed Esc (caller leaves the existing
      filter untouched).
    """

    height, width = stdscr.getmaxyx()
    y = height - 1
    pattern = initial
    while True:
        try:
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            stdscr.addstr(y, 0, t("tui.filter_prompt"),
                          attrs.get("warn", curses.A_BOLD))
            stdscr.addstr(y, 1, " " + pattern, attrs.get("name", curses.A_BOLD))
        except curses.error:
            return None
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (10, 13, curses.KEY_ENTER):
            return pattern
        if ch == 27:  # Esc
            return None
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            pattern = pattern[:-1]
        elif ch == 21:  # Ctrl-U
            pattern = ""
        elif 32 <= ch < 127:  # printable ASCII
            pattern += chr(ch)
        # ignore everything else (arrow keys, function keys, etc.)


def _draw(stdscr, rows: list[_Row], columns: list[_Column], cursor: int,
          title: str, summary: str, status: str,
          attrs: dict[str, int]) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 30:
        stdscr.addstr(0, 0, "terminal too small")
        stdscr.refresh()
        return

    # Title (line 0) and summary (line 1)
    _addstr(stdscr, 0, 0, _truncate(title, width - 1), attrs["title"])
    if summary:
        _addstr(stdscr, 1, 0, _truncate(summary, width - 1), attrs["dim"])

    # Column header (line 2)
    header_text = _format_row([c.header for c in columns], columns)
    _addstr(stdscr, 2, 2, _truncate(header_text, width - 3), attrs["header"])

    # Body
    body_top = 3
    body_height = max(1, height - body_top - 2)
    if not rows:
        _addstr(stdscr, body_top, 2,
                "(no snapshots — press q to quit)", attrs["dim"])
    else:
        if cursor < 0:
            cursor = 0
        if cursor >= len(rows):
            cursor = len(rows) - 1
        first = max(0, cursor - body_height + 1)
        first = min(first, max(0, len(rows) - body_height))
        for row_offset in range(min(body_height, len(rows) - first)):
            idx = first + row_offset
            _render_row(
                stdscr,
                body_top + row_offset,
                rows[idx],
                columns,
                attrs,
                selected=(idx == cursor),
                max_width=width - 1,
            )

    # Status (line height-2) and key hints (line height-1)
    if status:
        _addstr(stdscr, height - 2, 0, _truncate(status, width - 1), attrs["ok"])
    _draw_key_hints(stdscr, height - 1, attrs, width - 1)

    stdscr.refresh()


def _prompt_input(stdscr, label: str, initial: str = "") -> Optional[str]:
    """Display a one-line input box at the bottom; return text or None on Esc."""

    height, width = stdscr.getmaxyx()
    box_y = height - 2
    box_x = len(label) + 2
    box_w = max(8, width - box_x - 2)

    # Clear the line
    try:
        stdscr.move(box_y, 0)
        stdscr.clrtoeol()
        stdscr.addstr(box_y, 0, label + " ", curses.A_BOLD)
    except curses.error:
        return None

    win = curses.newwin(1, box_w, box_y, box_x)
    box = textpad.Textbox(win)
    if initial:
        win.addstr(0, 0, initial[: box_w - 1])
    win.refresh()

    aborted = {"value": False}

    def validate(ch: int) -> int:
        # Esc -> abort; Enter -> finish; Ctrl-U clears
        if ch in (27,):
            aborted["value"] = True
            return 7  # ^G ends edit
        if ch in (10, 13, curses.KEY_ENTER):
            return 7
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            return 8
        if ch == 21:  # ^U
            win.clear()
            win.refresh()
            return 0
        return ch

    curses.curs_set(1)
    try:
        result = box.edit(validate)
    finally:
        curses.curs_set(0)
    if aborted["value"]:
        return None
    return result.strip()


def _confirm_popup(stdscr, message: str, *,
                   attrs: Optional[dict[str, int]] = None) -> bool:
    attrs = attrs or {"warn": curses.A_BOLD, "dim": curses.A_DIM, "ok": curses.A_BOLD}
    height, width = stdscr.getmaxyx()
    h = 5
    w = min(max(len(message) + 6, 36), width - 4)
    y = max(0, (height - h) // 2)
    x = max(0, (width - w) // 2)
    win = curses.newwin(h, w, y, x)
    win.box()
    try:
        win.addstr(1, 2, _truncate(message, w - 4), attrs.get("warn", curses.A_BOLD))
        # Footer: ``y confirm  ·  n/Esc cancel``
        footer_y = h - 2
        col = 2
        win.addstr(footer_y, col, "y", attrs.get("ok", curses.A_BOLD))
        col += 1
        win.addstr(footer_y, col, " confirm", attrs.get("dim", curses.A_DIM))
        col += len(" confirm")
        win.addstr(footer_y, col, "  ·  ", attrs.get("dim", curses.A_DIM))
        col += len("  ·  ")
        win.addstr(footer_y, col, "n/Esc", attrs.get("warn", curses.A_BOLD))
        col += len("n/Esc")
        win.addstr(footer_y, col, " cancel", attrs.get("dim", curses.A_DIM))
    except curses.error:
        pass
    win.refresh()
    while True:
        ch = win.getch()
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27, ord("q")):
            return False


def _details_popup(stdscr, snapz: SnapshotMeta, *,
                   attrs: Optional[dict[str, int]] = None) -> None:
    attrs = attrs or {
        "title": curses.A_BOLD,
        "dim": curses.A_DIM,
        "name": curses.A_BOLD,
        "num": curses.A_NORMAL,
    }
    ratio = (snapz.total_bytes_in / snapz.size_bytes) if snapz.size_bytes else 1.0
    rows: list[tuple[str, str, str]] = [
        ("source", str(snapz.source), "title"),
        ("created", format_iso(snapz.created), "dim"),
        ("archive", f"{snapz.archive}  ({snapz.compression})", "title"),
        (
            "size",
            f"{format_size(snapz.size_bytes)}  ←  "
            f"{format_size(snapz.total_bytes_in)}  ({ratio:.1f}× ratio)",
            "num",
        ),
        ("files", f"{snapz.file_count:,}", "num"),
    ]
    if snapz.note:
        rows.insert(0, ("note", snapz.note, "title"))
    label_w = max(len(r[0]) for r in rows)
    body_lines = [
        f"  {label.ljust(label_w)}   {value}"
        for label, value, _ in rows
    ]
    height, width = stdscr.getmaxyx()
    h = len(rows) + 5
    w = min(max(max(len(line) for line in body_lines) + 4, 44), width - 4)
    y = max(0, (height - h) // 2)
    x = max(0, (width - w) // 2)
    win = curses.newwin(h, w, y, x)
    win.box()
    try:
        # Title bar with the snapshot name highlighted
        title_text = " " + snapz.name + " "
        win.addstr(0, 2, title_text, attrs.get("title", curses.A_BOLD))
        for i, ((label, value, value_attr), _line) in enumerate(zip(rows, body_lines), start=2):
            if i >= h - 2:
                break
            win.addstr(i, 2, "  " + label.ljust(label_w),
                       attrs.get("dim", curses.A_DIM))
            win.addstr(i, 2 + 2 + label_w + 3,
                       _truncate(value, w - (4 + label_w + 4)),
                       attrs.get(value_attr, 0))
        win.addstr(h - 1, 2, " any key to close ",
                   attrs.get("dim", curses.A_DIM))
    except curses.error:
        pass
    win.refresh()
    win.getch()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _run_loop(
    stdscr,
    *,
    title_fn: Callable[[list[_Row]], tuple[str, str]],
    refresh: Callable[[], list[_Row]],
    column_layout: Callable[[int], list[_Column]],
    delete_fn: Callable[[_Row], None],
    rename_fn: Callable[[_Row, str], None],
) -> Optional[DeferredRestore]:
    curses.curs_set(0)
    stdscr.keypad(True)
    attrs = _init_colors()
    cursor = 0
    status = ""
    all_rows = refresh()
    filter_pattern = ""

    def _apply_filter(rows: list[_Row]) -> list[_Row]:
        if not filter_pattern:
            return rows
        pred = _filter_predicate(filter_pattern)
        return [r for r in rows if pred(r.snapshot)]

    while True:
        height, width = stdscr.getmaxyx()
        columns = column_layout(width)
        rows = _apply_filter(all_rows)
        title, summary = title_fn(rows)
        if filter_pattern:
            summary = (
                f"{summary}  ·  "
                + t("tui.filter_status",
                    pattern=filter_pattern,
                    n=len(rows), total=len(all_rows))
            )
        _draw(stdscr, rows, columns, cursor, title, summary, status, attrs)

        ch = stdscr.getch()
        status = ""

        if ch in (ord("q"), 27):
            if ch == 27 and filter_pattern:
                # Esc clears the filter without leaving the view.
                filter_pattern = ""
                cursor = 0
                continue
            return None

        if ch == ord("/"):
            entered = _read_filter_pattern(
                stdscr, attrs=attrs, initial=filter_pattern,
            )
            if entered is not None:
                filter_pattern = entered
                cursor = 0
            continue

        if ch == curses.KEY_RESIZE:
            continue

        if not rows:
            # Only quit + resize + filter relevant when empty
            continue

        if ch in (curses.KEY_DOWN, ord("j")):
            cursor = min(cursor + 1, len(rows) - 1)
        elif ch in (curses.KEY_UP, ord("k")):
            cursor = max(cursor - 1, 0)
        elif ch == curses.KEY_NPAGE:
            cursor = min(cursor + 10, len(rows) - 1)
        elif ch == curses.KEY_PPAGE:
            cursor = max(cursor - 10, 0)
        elif ch == curses.KEY_HOME:
            cursor = 0
        elif ch == curses.KEY_END:
            cursor = len(rows) - 1
        elif ch in (10, 13, curses.KEY_ENTER):
            _details_popup(stdscr, rows[cursor].snapshot, attrs=attrs)
        elif ch in (ord("d"),):
            row = rows[cursor]
            if _confirm_popup(
                stdscr,
                f"Delete '{row.snapshot.name}' "
                f"({format_size(row.snapshot.size_bytes)})?",
                attrs=attrs,
            ):
                try:
                    delete_fn(row)
                    status = f"deleted {row.snapshot.name}"
                    all_rows = refresh()
                    rows = _apply_filter(all_rows)
                    cursor = min(cursor, max(0, len(rows) - 1))
                except Exception as exc:
                    status = f"delete failed: {exc}"
        elif ch in (ord("n"),):
            row = rows[cursor]
            new_name = _prompt_input(
                stdscr,
                f"rename '{row.snapshot.name}' to:",
                initial=row.snapshot.name,
            )
            if new_name is None or not new_name or new_name == row.snapshot.name:
                status = "rename cancelled"
            else:
                try:
                    validate_snapshot_name(new_name)
                    rename_fn(row, new_name)
                    status = f"renamed -> {new_name}"
                    all_rows = refresh()
                except (ValueError, FileExistsError) as exc:
                    status = f"rename failed: {exc}"
        elif ch in (ord("r"),):
            row = rows[cursor]
            return DeferredRestore(
                abspath=row.abspath,
                snapshot_name=row.snapshot.name,
            )
        # else: ignore unknown key


# ---------------------------------------------------------------------------
# Public entry points (one per CLI subcommand)
# ---------------------------------------------------------------------------


def run_list_view(
    config: RuntimeConfig, abspath: Path, *, show_auto: bool = False,
) -> Optional[DeferredRestore]:
    def column_layout(width: int) -> list[_Column]:
        return _compute_columns(
            [
                ("NAME", "left", -3),
                ("CREATED", "left", 16),
                ("SIZE", "right", 10),
                ("FILES", "right", 10),
            ],
            width - 2,
        )

    def refresh() -> list[_Row]:
        snaps = api.list_snapshots(abspath, config=config)
        if not show_auto:
            snaps = [s for s in snaps if not is_auto_snapshot(s.name)]
        return _build_list_rows(snaps, abspath)

    def title_fn(rows: list[_Row]) -> tuple[str, str]:
        total = sum(r.snapshot.size_bytes for r in rows)
        title = f"📂 {abspath}"
        summary = (
            f"{len(rows)} snapshot(s), {format_size(total)} on disk"
            if rows
            else "no snapshots in this directory"
        )
        return title, summary

    def delete_fn(row: _Row) -> None:
        api.delete(abspath, row.snapshot.name, config=config)

    def rename_fn(row: _Row, new: str) -> None:
        api.rename(abspath, row.snapshot.name, new, config=config)

    return curses.wrapper(
        lambda scr: _run_loop(
            scr,
            title_fn=title_fn,
            refresh=refresh,
            column_layout=column_layout,
            delete_fn=delete_fn,
            rename_fn=rename_fn,
        )
    )



def run_alist_view(
    config: RuntimeConfig, *, show_auto: bool = False,
) -> Optional[DeferredRestore]:
    def column_layout(width: int) -> list[_Column]:
        return _compute_columns(
            [
                ("DIR", "left", -2),
                ("NAME", "left", -3),
                ("CREATED", "left", 16),
                ("SIZE", "right", 10),
                ("FILES", "right", 10),
            ],
            width - 2,
        )

    def refresh() -> list[_Row]:
        entries = api.list_all(config=config)
        if not show_auto:
            entries = [
                DirEntry(
                    key=e.key, meta=e.meta,
                    snapshots=[
                        s for s in e.snapshots if not is_auto_snapshot(s.name)
                    ],
                )
                for e in entries
            ]
        return _build_alist_rows(entries)

    def title_fn(rows: list[_Row]) -> tuple[str, str]:
        unique_dirs = {str(r.abspath) for r in rows}
        total = sum(r.snapshot.size_bytes for r in rows)
        title = "🌐 ALL SNAPSHOTS"
        summary = (
            f"{len(rows)} snapshot(s) across {len(unique_dirs)} dir(s), "
            f"{format_size(total)}"
            if rows
            else "(nothing recorded yet)"
        )
        return title, summary

    def delete_fn(row: _Row) -> None:
        api.delete(row.abspath, row.snapshot.name, config=config)

    def rename_fn(row: _Row, new: str) -> None:
        api.rename(row.abspath, row.snapshot.name, new, config=config)

    return curses.wrapper(
        lambda scr: _run_loop(
            scr,
            title_fn=title_fn,
            refresh=refresh,
            column_layout=column_layout,
            delete_fn=delete_fn,
            rename_fn=rename_fn,
        )
    )


# ---------------------------------------------------------------------------
# Diff picker
# ---------------------------------------------------------------------------


def _parent_dir_pattern(path: str) -> str:
    """Return the parent directory pattern (with trailing ``/``), or ''."""

    if "/" not in path:
        return ""
    parent = path.rsplit("/", 1)[0]
    return parent + "/"


def _format_diff_change(change, *, name_w: int) -> tuple[str, str, str]:
    """Render one diff row: ``(status_letter, path_cell, info_cell)``."""

    path_cell = change.path[:name_w].ljust(name_w)
    if change.status == "A":
        info = f"+{format_size(change.size_b or 0)}"
    elif change.status == "D":
        info = f"-{format_size(change.size_a or 0)}"
    elif change.status == "T":
        info = f"{change.type_a or '?'} -> {change.type_b or '?'}"
    else:
        info = (
            f"{format_size(change.size_a or 0)} -> "
            f"{format_size(change.size_b or 0)}"
        )
    return change.status, path_cell, info


def run_diff_view(
    diff_result,
    *,
    read_a: Optional[Callable[[str], Optional[bytes]]] = None,
    read_b: Optional[Callable[[str], Optional[bytes]]] = None,
) -> list[str]:
    """Curses picker over a :class:`snapz.api.DiffResult`.

    Lets the user multi-select files and/or parent directories to add
    to the per-source local-excludes list (``e`` to apply). Pressing
    Enter on a row drills into a unified-diff sub-view for that file,
    using *read_a* / *read_b* callbacks to fetch each side's bytes.

    Returns the list of patterns chosen (relative paths for files,
    paths with trailing ``/`` for dirs). Empty list = user quit without
    applying.
    """

    rows = list(diff_result.changes)

    def _curses_main(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        attrs = _init_colors()

        # Distinct color for the diff status letter.
        status_attrs = {
            "A": attrs.get("ok", curses.A_BOLD),
            "M": attrs.get("warn", curses.A_BOLD),
            "T": attrs.get("warn", curses.A_BOLD),
            "D": attrs.get("warn", curses.A_BOLD) | curses.A_DIM,
        }

        if not rows:
            stdscr.erase()
            stdscr.addstr(
                0, 0, " diff: no changes — press any key to exit",
                attrs.get("dim", curses.A_DIM),
            )
            stdscr.refresh()
            stdscr.getch()
            return []

        selected_files: set[str] = set()
        selected_dirs: set[str] = set()
        cursor = 0
        scroll = 0

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            label_b = (
                diff_result.b_meta.name if diff_result.b_meta else "live"
            )
            title = (
                f" diff: {diff_result.a_meta.name} \u2192 {label_b}  "
                f"({len(rows)} change(s)) "
            )
            try:
                stdscr.addstr(
                    0, 0, title.ljust(width)[:width - 1],
                    attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
                )
            except curses.error:
                pass

            visible = max(1, height - 3)
            if cursor < scroll:
                scroll = cursor
            elif cursor >= scroll + visible:
                scroll = cursor - visible + 1

            name_w = max(8, min(width - 18, max(len(r.path) for r in rows)))
            for i in range(visible):
                idx = scroll + i
                if idx >= len(rows):
                    break
                row = rows[idx]
                marker = " "
                if row.path in selected_files:
                    marker = "\u2713"      # ✓
                else:
                    parent = _parent_dir_pattern(row.path)
                    if parent and parent in selected_dirs:
                        marker = "\u25bc"  # ▼
                status_letter, path_cell, info_cell = _format_diff_change(
                    row, name_w=name_w
                )

                base = (
                    attrs.get("cursor", curses.A_REVERSE)
                    if idx == cursor else 0
                )
                try:
                    line_prefix = f"  [{marker}] "
                    stdscr.addstr(
                        1 + i, 0, line_prefix.ljust(7), base
                    )
                    stdscr.addstr(
                        1 + i, 7, status_letter,
                        status_attrs.get(status_letter, 0) | base,
                    )
                    stdscr.addstr(1 + i, 8, "  ", base)
                    stdscr.addstr(
                        1 + i, 10, path_cell,
                        attrs.get("name", curses.A_BOLD) | base,
                    )
                    info_pad = max(0, width - 10 - name_w - 2)
                    stdscr.addstr(
                        1 + i, 10 + name_w + 2,
                        info_cell[:info_pad].ljust(info_pad),
                        attrs.get("dim", curses.A_DIM) | base,
                    )
                except curses.error:
                    pass

            n_sel = len(selected_files) + len(selected_dirs)
            footer = " " + t("diff.list_footer", n=n_sel) + " "
            try:
                stdscr.addstr(
                    height - 1, 0, footer.ljust(width)[:width - 1],
                    attrs.get("dim", curses.A_DIM),
                )
            except curses.error:
                pass

            stdscr.refresh()
            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                return []

            if key in (ord("q"), 27):
                return []
            elif key in (curses.KEY_UP, ord("k")):
                cursor = max(0, cursor - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = min(len(rows) - 1, cursor + 1)
            elif key == curses.KEY_PPAGE:
                cursor = max(0, cursor - visible)
            elif key == curses.KEY_NPAGE:
                cursor = min(len(rows) - 1, cursor + visible)
            elif key == curses.KEY_HOME:
                cursor = 0
            elif key == curses.KEY_END:
                cursor = len(rows) - 1
            elif key == ord(" "):
                p = rows[cursor].path
                if p in selected_files:
                    selected_files.discard(p)
                else:
                    selected_files.add(p)
            elif key == ord("d"):
                p = _parent_dir_pattern(rows[cursor].path)
                if p:
                    if p in selected_dirs:
                        selected_dirs.discard(p)
                    else:
                        selected_dirs.add(p)
            elif key == ord("a"):
                selected_files = {r.path for r in rows}
                selected_dirs.clear()
            elif key == ord("n"):
                selected_files.clear()
                selected_dirs.clear()
            elif key in (10, 13, curses.KEY_ENTER):
                # Drill into the cursor row's unified diff.
                if read_a is None and read_b is None:
                    continue  # legacy callers without readers: no-op
                _run_unified_diff_for_change(
                    stdscr,
                    rows[cursor],
                    diff_result,
                    read_a=read_a,
                    read_b=read_b,
                    attrs=attrs,
                )
            elif key == ord("e"):
                return sorted(selected_files | selected_dirs)

    return curses.wrapper(_curses_main)


# ---------------------------------------------------------------------------
# Unified-diff sub-view (drilled into from run_diff_view)
# ---------------------------------------------------------------------------


# Cap how many bytes we'll try to render. Above the cap, we show a
# placeholder — the goal is a fast glance, not a full editor.
_MAX_DIFF_BYTES = 2 * 1024 * 1024


def _decode_for_diff(
    data: Optional[bytes],
) -> tuple[Optional[list[str]], Optional[str]]:
    """Return ``(lines, placeholder)``.

    ``lines is None`` means the content is binary or too large; the
    caller should render the placeholder instead. ``lines == []`` and
    ``placeholder is None`` means the side is absent (e.g. file added
    on the other side).
    """

    if data is None:
        return [], None
    if len(data) > _MAX_DIFF_BYTES:
        return None, t(
            "diff.placeholder_too_large",
            size=format_size(len(data)),
        )
    if b"\x00" in data[:8192]:
        return None, t("diff.placeholder_binary", size=format_size(len(data)))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError:
            return None, t("diff.placeholder_binary", size=format_size(len(data)))
    return text.splitlines(), None


def _run_unified_diff_for_change(
    stdscr,
    row,
    diff_result,
    *,
    read_a: Optional[Callable[[str], Optional[bytes]]],
    read_b: Optional[Callable[[str], Optional[bytes]]],
    attrs: dict[str, int],
) -> None:
    import difflib

    relpath = row.path
    label_a = diff_result.a_meta.name
    label_b = (
        diff_result.b_meta.name if diff_result.b_meta else t("label.diff_live")
    )

    a_bytes = read_a(relpath) if read_a is not None else None
    b_bytes = read_b(relpath) if read_b is not None else None
    a_lines, a_placeholder = _decode_for_diff(a_bytes)
    b_lines, b_placeholder = _decode_for_diff(b_bytes)

    body: list[tuple[str, str]] = []
    if a_placeholder or b_placeholder:
        body.append((
            "info", a_placeholder or t("diff.placeholder_text", size="-"),
        ))
        body.append((
            "info", b_placeholder or t("diff.placeholder_text", size="-"),
        ))
    else:
        a_lines = a_lines or []
        b_lines = b_lines or []
        ud = difflib.unified_diff(
            a_lines, b_lines,
            fromfile=f"{label_a}/{relpath}",
            tofile=f"{label_b}/{relpath}",
            lineterm="",
            n=3,
        )
        for line in ud:
            if line.startswith("+++") or line.startswith("---"):
                body.append(("head", line))
            elif line.startswith("@@"):
                body.append(("hunk", line))
            elif line.startswith("+"):
                body.append(("add", line))
            elif line.startswith("-"):
                body.append(("del", line))
            else:
                body.append(("ctx", line))
        if not body:
            body.append(("info", t("diff.identical")))

    kind_attrs = {
        "add": attrs.get("ok", curses.A_BOLD),
        "del": attrs.get("warn", curses.A_BOLD),
        "hunk": attrs.get("title", curses.A_BOLD),
        "head": attrs.get("dim", curses.A_DIM) | curses.A_BOLD,
        "info": attrs.get("dim", curses.A_DIM),
        "ctx": 0,
    }

    scroll = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        title = (
            f" {t('diff.unified_title', a=label_a, b=label_b, path=relpath)} "
        )
        try:
            stdscr.addstr(
                0, 0, title.ljust(width)[: width - 1],
                attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
            )
        except curses.error:
            pass

        body_top = 1
        body_height = max(1, height - body_top - 1)
        max_scroll = max(0, len(body) - body_height)
        if scroll > max_scroll:
            scroll = max_scroll
        if scroll < 0:
            scroll = 0

        for i in range(body_height):
            idx = scroll + i
            if idx >= len(body):
                break
            kind, text = body[idx]
            attr = kind_attrs.get(kind, 0)
            try:
                stdscr.addstr(body_top + i, 0, _truncate(text, width - 1), attr)
            except curses.error:
                pass

        footer = " " + t("diff.unified_footer") + " "
        try:
            stdscr.addstr(
                height - 1, 0, footer.ljust(width)[: width - 1],
                attrs.get("dim", curses.A_DIM),
            )
        except curses.error:
            pass
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), 27, 10, 13, curses.KEY_ENTER):
            return
        if key == curses.KEY_RESIZE:
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            scroll += 1
        elif key in (curses.KEY_UP, ord("k")):
            scroll -= 1
        elif key == curses.KEY_NPAGE or key == ord(" "):
            scroll += body_height
        elif key == curses.KEY_PPAGE:
            scroll -= body_height
        elif key == curses.KEY_HOME or key == ord("g"):
            scroll = 0
        elif key == curses.KEY_END or key == ord("G"):
            scroll = max_scroll


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


# ---------------------------------------------------------------------------
# Revert path picker — multi-select over a manifest's entries
# ---------------------------------------------------------------------------


def run_revert_picker(entries, src) -> list[str]:
    """Curses multi-select over a snapshot's manifest *entries*.

    Returns the relative paths the user picked. ``src`` is the live
    source path; we use it only to highlight rows whose live state
    differs from the snapshot (so the user sees what's actually worth
    rolling back). Passing :data:`None` skips that decoration.
    """

    rows = list(entries)
    if not rows:
        return []

    # Pre-compute "differs from live" so we don't stat() inside the
    # render loop. Best-effort — any OSError just leaves the row
    # un-highlighted.
    differs: set[str] = set()
    if src is not None:
        import os as _os
        for e in rows:
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
                pass

    def _curses_main(stdscr) -> list[str]:
        curses.curs_set(0)
        stdscr.keypad(True)
        attrs = _init_colors()
        cursor = 0
        scroll = 0
        # Default: pre-select rows that differ from the live tree.
        # User can press ``n`` to clear if they want a clean slate.
        selected: set[str] = set(differs)

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            title = " " + t(
                "revert.picker_title",
                n=len(rows), src=str(src) if src else "",
            ) + " "
            try:
                stdscr.addstr(
                    0, 0, title.ljust(width)[: width - 1],
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

            name_w = max(8, min(width - 24, max(len(r.path) for r in rows)))
            for i in range(body_height):
                idx = scroll + i
                if idx >= len(rows):
                    break
                row = rows[idx]
                base = (
                    attrs.get("cursor", curses.A_REVERSE) if idx == cursor else 0
                )
                marker = "\u2713" if row.path in selected else " "
                changed = row.path in differs
                row_attr = (
                    attrs.get("warn", curses.A_BOLD) if changed
                    else attrs.get("name", 0)
                ) | base
                try:
                    stdscr.addstr(body_top + i, 0, f"  [{marker}] ", base)
                    stdscr.addstr(
                        body_top + i, 6,
                        _truncate(row.path, name_w).ljust(name_w),
                        row_attr,
                    )
                    info = (
                        format_size(row.size or 0)
                        if row.type == "file" else f"\u2192 {row.target or ''}"
                    )
                    stdscr.addstr(
                        body_top + i, 6 + name_w + 2,
                        _truncate(info, max(0, width - 6 - name_w - 3)),
                        attrs.get("dim", curses.A_DIM) | base,
                    )
                except curses.error:
                    pass

            footer = " " + t("revert.picker_footer", n=len(selected)) + " "
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
                return []
            if key == curses.KEY_RESIZE:
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
            elif key == ord(" "):
                p = rows[cursor].path
                if p in selected:
                    selected.discard(p)
                else:
                    selected.add(p)
            elif key == ord("a"):
                selected = {r.path for r in rows}
            elif key == ord("c"):
                # Re-select only the rows that differ from live tree.
                selected = set(differs)
            elif key == ord("n"):
                selected.clear()
            elif key in (ord("e"), 10, 13, curses.KEY_ENTER):
                return sorted(selected)

    return curses.wrapper(_curses_main)
