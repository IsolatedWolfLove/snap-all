"""List and all-list curses views."""

from __future__ import annotations

import curses
from pathlib import Path
from typing import Optional

from snapz import api
from snapz.config import RuntimeConfig
from snapz._list_common import alist_entries
from snapz.store import DirEntry
from snapz.util import format_size, is_auto_snapshot
from snapz._tui_common import (
    DeferredRestore,
    _Column,
    _Row,
    _build_alist_rows,
    _build_list_rows,
    _compute_columns,
    _run_loop,
)


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
        entries = alist_entries(config)
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
        if row.archive_key:
            raise ValueError("remote archive rows are read-only")
        api.delete(row.abspath, row.snapshot.name, config=config)

    def rename_fn(row: _Row, new: str) -> None:
        if row.archive_key:
            raise ValueError("remote archive rows are read-only")
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
