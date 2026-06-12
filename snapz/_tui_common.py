"""Shared curses rendering helpers and row models for snapz TUI."""

from __future__ import annotations

import curses
import curses.textpad as textpad
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from snapz.i18n import t
from snapz.store import DirEntry, SnapshotMeta
from snapz.util import format_iso, format_size, validate_snapshot_name


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
    archive_key: str = ""


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
    archive_key: str = ""


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
        archive_key = entry.key if entry.archived else ""
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
                    archive_key=archive_key,
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
            if row.archive_key:
                status = "remote archive rows are read-only"
                continue
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
            if row.archive_key:
                status = "remote archive rows are read-only"
                continue
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
                archive_key=row.archive_key,
            )
        # else: ignore unknown key
