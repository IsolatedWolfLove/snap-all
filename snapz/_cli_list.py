"""Snapshot list and all-list commands."""

from __future__ import annotations

from snapz._cli_common import *
def _print_snapshot_table(
    snaps: Iterable[SnapshotMeta], *, show_auto: bool = False,
) -> None:
    """Render the per-dir snapshot table.

    Auto-* snapshots (``auto-…``, ``auto-pre-restore-…``,
    ``auto-pre-revert-…``) are hidden unless *show_auto* is True so
    the user-facing surface stays clean. A ``(N hidden)`` footer is
    emitted whenever some were filtered.
    """

    all_rows = list(snaps)
    rows = all_rows if show_auto else [
        s for s in all_rows if not is_auto_snapshot(s.name)
    ]
    hidden = len(all_rows) - len(rows)
    if not rows:
        if hidden:
            print(st.muted(t('status.hidden_auto', n=hidden).strip()))
        else:
            print(st.muted(t('status.no_snapshots_yet')))
        return
    name_w = max(4, *(len(s.name) for s in rows))
    has_notes = any(s.note for s in rows)
    note_header = f"  {t('header.NOTE')}" if has_notes else ''
    header = (
        f"  {t('header.NAME').ljust(name_w)}  {t('header.CREATED').ljust(16)}  "
        f"{t('header.SIZE').rjust(10)}  {t('header.FILES').rjust(6)}{note_header}"
    )
    print(st.dim(header))
    for snapz in rows:
        is_auto = is_auto_snapshot(snapz.name)
        name_cell = snapz.name.ljust(name_w)
        created_cell = format_iso(snapz.created).ljust(16)
        size_cell = format_size(snapz.size_bytes).rjust(10)
        files_cell = f'{snapz.file_count:,}'.rjust(6)
        note_cell = f"  {snapz.note}" if (has_notes and snapz.note) else ''
        if is_auto:
            line = (
                f"  {st.muted(name_cell)}  {st.muted(created_cell)}  "
                f"{st.muted(size_cell)}  {st.muted(files_cell)}"
                f"{st.muted(note_cell) if note_cell else ''}"
            )
        else:
            line = (
                f"  {st.name(name_cell)}  {st.muted(created_cell)}  "
                f"{st.numeric(size_cell)}  {st.numeric(files_cell)}"
                f"{st.muted(note_cell) if note_cell else ''}"
            )
        print(line)
    if hidden:
        print(st.muted(t('status.hidden_auto', n=hidden)))

def _timeline_bucket(created: str) -> str:
    try:
        dt = datetime.fromisoformat(created)
    except ValueError:
        return "Unknown"
    today = datetime.now(dt.tzinfo).date()
    day = dt.date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    if day.year == today.year:
        return day.strftime("%b %d")
    return day.strftime("%Y-%m-%d")

def _print_snapshot_timeline(
    snaps: Iterable[SnapshotMeta], *, show_auto: bool = False,
) -> None:
    all_rows = list(snaps)
    rows = all_rows if show_auto else [
        s for s in all_rows if not is_auto_snapshot(s.name)
    ]
    hidden = len(all_rows) - len(rows)
    if not rows:
        if hidden:
            print(st.muted(t('status.hidden_auto', n=hidden).strip()))
        else:
            print(st.muted(t('status.no_snapshots_yet')))
        return

    current_bucket = ""
    for snapz in rows:
        bucket = _timeline_bucket(snapz.created)
        if bucket != current_bucket:
            if current_bucket:
                print()
            print(st.bold(bucket))
            current_bucket = bucket
        try:
            when = datetime.fromisoformat(snapz.created).strftime("%H:%M")
        except ValueError:
            when = snapz.created[:16]
        parts = [
            st.muted(when.rjust(5)),
            st.name(snapz.name),
            st.numeric(format_size(snapz.size_bytes)),
            st.numeric(f"{snapz.file_count:,}"),
            st.muted(snapz.compression),
        ]
        if snapz.tags:
            parts.append(st.muted("#" + " #".join(snapz.tags)))
        if snapz.note:
            parts.append(st.muted(snapz.note))
        print("  " + "  ".join(parts))
    if hidden:
        print()
        print(st.muted(t('status.hidden_auto', n=hidden)))

def _print_alist_table(
    entries: Iterable[DirEntry], *, show_auto: bool = False,
) -> None:
    rows = list(entries)
    if not rows:
        print(st.muted(t('status.no_snapshots_anywhere')))
        return
    flat_all: list[tuple[str, SnapshotMeta]] = []
    for entry in rows:
        for snapz in entry.snapshots:
            flat_all.append((Path(entry.meta.abspath).name or entry.key, snapz))
    flat = flat_all if show_auto else [
        (d, s) for d, s in flat_all if not is_auto_snapshot(s.name)
    ]
    hidden = len(flat_all) - len(flat)
    if not flat:
        if hidden:
            print(st.muted(t('status.hidden_auto', n=hidden).strip()))
        else:
            print(st.muted(t('status.no_snapshots_only_empty')))
        return
    dir_w = max(3, *(len(d) for d, _ in flat))
    name_w = max(4, *(len(s.name) for _, s in flat))
    header = (
        f"{t('header.DIR').ljust(dir_w)}  {t('header.NAME').ljust(name_w)}  "
        f"{t('header.CREATED').ljust(16)}  {t('header.SIZE').rjust(10)}  {t('header.FILES')}"
    )
    print(st.dim(header))
    for dir_name, snapz in flat:
        is_auto = is_auto_snapshot(snapz.name)
        dir_cell = dir_name.ljust(dir_w)
        name_cell = snapz.name.ljust(name_w)
        created_cell = format_iso(snapz.created).ljust(16)
        size_cell = format_size(snapz.size_bytes).rjust(10)
        files_cell = f'{snapz.file_count:,}'
        if is_auto:
            print(
                f"{st.muted(dir_cell)}  {st.muted(name_cell)}  "
                f"{st.muted(created_cell)}  {st.muted(size_cell)}  "
                f"{st.muted(files_cell)}"
            )
        else:
            print(
                f"{st.path(dir_cell)}  {st.name(name_cell)}  "
                f"{st.muted(created_cell)}  {st.numeric(size_cell)}  "
                f"{st.numeric(files_cell)}"
            )
    if hidden:
        print(st.muted(t('status.hidden_auto', n=hidden)))

def cmd_list(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    show_auto = bool(getattr(args, "all", False))
    snaps = api.list_snapshots(path, config=config)

    if _wants_json(args):
        visible = _filter_user_visible(snaps, show_auto=show_auto)
        _emit_json({
            "path": str(path),
            "show_auto": show_auto,
            "hidden_auto": len(snaps) - len(visible),
            "snapshots": visible,
        })
        return EXIT_OK

    if args.text or not _stdout_is_tty():
        print(f"\U0001f4c2 {st.path(str(path))}")
        if getattr(args, "timeline", False):
            _print_snapshot_timeline(snaps, show_auto=show_auto)
        else:
            _print_snapshot_table(snaps, show_auto=show_auto)
        return EXIT_OK

    from snapz import tui  # local import to keep curses opt-in

    deferred = tui.run_list_view(config, path, show_auto=show_auto)
    if deferred is None:
        return EXIT_OK
    return _restore_with_confirmation(
        deferred.abspath,
        deferred.snapshot_name,
        config,
        auto_save=True,
        clean=False,
        assume_yes=False,
    )

def cmd_alist(args: argparse.Namespace, config: RuntimeConfig) -> int:
    show_auto = bool(getattr(args, "all", False))
    entries = api.list_all(config=config)

    if _wants_json(args):
        # Filter at the per-dir level so the JSON consumer sees the same
        # rows that the text/TUI views do.
        rows = []
        hidden_total = 0
        for entry in entries:
            visible = _filter_user_visible(entry.snapshots, show_auto=show_auto)
            hidden_total += len(entry.snapshots) - len(visible)
            rows.append({
                "key": entry.key,
                "abspath": entry.meta.abspath,
                "first_seen": entry.meta.first_seen,
                "last_used": entry.meta.last_used,
                "snapshots": visible,
            })
        _emit_json({
            "show_auto": show_auto,
            "hidden_auto": hidden_total,
            "dirs": rows,
        })
        return EXIT_OK

    if args.text or not _stdout_is_tty():
        _print_alist_table(entries, show_auto=show_auto)
        return EXIT_OK

    from snapz import tui

    deferred = tui.run_alist_view(config, show_auto=show_auto)
    if deferred is None:
        return EXIT_OK
    return _restore_with_confirmation(
        deferred.abspath,
        deferred.snapshot_name,
        config,
        auto_save=True,
        clean=False,
        assume_yes=False,
    )

