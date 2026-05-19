"""Diff picker and unified diff TUI views."""

from __future__ import annotations

import curses
from typing import Callable, Optional

from snapz.i18n import t
from snapz.util import format_size
from snapz._tui_common import _init_colors, _truncate


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


def _run_file_preview(
    stdscr,
    *,
    title: str,
    relpath: str,
    data: Optional[bytes],
    attrs: dict[str, int],
) -> None:
    """Read-only preview used by the generic manifest browser."""

    lines, placeholder = _decode_for_diff(data)
    body: list[tuple[str, str]] = []
    if placeholder:
        body.append(("info", placeholder))
    else:
        for line in lines or []:
            body.append(("ctx", line))
        if not body:
            body.append(("info", t("diff.placeholder_text", size="0 B")))

    kind_attrs = {
        "info": attrs.get("dim", curses.A_DIM),
        "ctx": 0,
    }

    scroll = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        header = f" {title}  ·  {relpath} "
        try:
            stdscr.addstr(
                0, 0, header.ljust(width)[: width - 1],
                attrs.get("title", curses.A_BOLD) | curses.A_REVERSE,
            )
        except curses.error:
            pass

        body_top = 1
        body_height = max(1, height - body_top - 1)
        max_scroll = max(0, len(body) - body_height)
        scroll = max(0, min(scroll, max_scroll))

        for i in range(body_height):
            idx = scroll + i
            if idx >= len(body):
                break
            kind, text = body[idx]
            try:
                stdscr.addstr(
                    body_top + i, 0,
                    _truncate(text, width - 1),
                    kind_attrs.get(kind, 0),
                )
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
