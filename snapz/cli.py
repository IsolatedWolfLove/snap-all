# PYTHON_ARGCOMPLETE_OK
"""Command-line interface.

M1 surface (no curses):

- ``snapz [path]``           interactive create
- ``snapz save <path> ...``  scriptable create
- ``snapz list``             text table for current dir
- ``snapz alist``            global text table
- ``snapz rm <name>``
- ``snapz mv <old> <new>``
- ``snapz show <name>``

The interactive create flow is intentionally kept here, not in
:mod:`snapz.api`, so the API stays free of stdin/stdout coupling.

Shell completion is wired through the optional ``argcomplete``
package: if it's importable, ``parser.parse_args`` will service the
``_ARGCOMPLETE`` environment hook automatically. Install with
``pip install argcomplete`` and activate via
``eval "$(register-python-argcomplete snapz)"`` in your shell rc.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from snapz import __version__, api, archive
from snapz import style as st
from snapz.archive import FileEntry, WalkResult
from snapz.config import RuntimeConfig, default_config
from snapz.i18n import t
from snapz.store import DirEntry, SnapshotMeta, Store
from snapz.util import (
    auto_name,
    format_duration,
    format_iso,
    format_size,
    is_auto_snapshot,
    resolve_path,
    validate_snapshot_name,
)

EXIT_OK = 0
EXIT_USER_ABORT = 130
EXIT_ERROR = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confirm(prompt: str, *, default_yes: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    # ``y``/``n`` are kept regardless of locale so input parsing is
    # uniform; only the visual hint can be translated.
    marker = t("confirm.default_yes" if default_yes else "confirm.default_no")
    if marker == "[Y/n]":
        suffix = f" {st.dim('[')}{st.bold('Y')}{st.dim('/n]')} "
    elif marker == "[y/N]":
        suffix = f" {st.dim('[y/')}{st.bold('N')}{st.dim(']')} "
    else:
        suffix = f" {st.dim(marker)} "
    try:
        answer = input(prompt + suffix).strip().lower()
    except EOFError:
        return False
    if not answer:
        return default_yes
    return answer in {"y", "yes"}


def _prompt(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" {st.dim('[' + default + ']')} " if default else " "
    try:
        value = input(prompt + suffix).strip()
    except EOFError:
        return default or ""
    return value or (default or "")


def _print_error(msg: str) -> None:
    print(f"{st.err_prefix()} {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# JSON output (--json) — keep one helper so every command emits the same
# shape (envelope + payload) and dataclasses serialise consistently.
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def _emit_json(payload: Any) -> None:
    """Serialise *payload* to stdout. Dataclasses and ``Path`` are handled
    transparently so call sites can pass api result objects directly."""

    text = json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)
    sys.stdout.write(text + "\n")


def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _filter_user_visible(snaps: Iterable[SnapshotMeta], *, show_auto: bool) -> list[SnapshotMeta]:
    """Drop ``auto-*`` snapshots unless the caller opted into them."""

    rows = list(snaps)
    if show_auto:
        return rows
    return [s for s in rows if not is_auto_snapshot(s.name)]


def _pluralize(n: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if n == 1 else (plural or singular + 's')


def _kv(label: str, value: str, *, indent: int = 2, label_w: int = 11) -> str:
    pad = ' ' * indent
    return f"{pad}{st.label(label.ljust(label_w))} {value}"


def _print_walk_summary(walk: WalkResult) -> None:
    lines = [
        _kv(t('kv.files'), st.numeric(f'{walk.file_count:,}')),
        _kv(t('kv.total_size'), st.numeric(format_size(walk.total_bytes))),
        _kv(t('kv.ignored'), st.muted(f'{walk.ignored_count:,}')),
    ]
    if walk.large_files:
        lines.append(
            _kv(
                t('kv.large_skip'),
                st.warn(t('save.large_over_cap', n=len(walk.large_files)))
                + st.muted(t('save.large_hint')),
            )
        )
    print('\n'.join(lines), flush=True)


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


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class _Progress:
    """Lightweight stderr progress bar.

    Falls back to silent when stderr isn't a TTY (e.g. piped to a file)
    so log output doesn't get polluted.
    """

    def __init__(self, total: int, *, width: int = 24) -> None:
        self.total = max(total, 1)
        self.width = width
        self.tty = sys.stderr.isatty()
        self.last_pct = -1

    def __call__(self, index: int, total: int, entry: FileEntry) -> None:
        if not self.tty:
            return
        pct = int(index * 100 / max(total, 1))
        if pct == self.last_pct:
            return
        self.last_pct = pct
        filled = int(pct * self.width / 100)
        bar = st.green('█' * filled) + st.dim('░' * (self.width - filled))
        counter = st.muted(f'({index}/{total})')
        msg = f"\r{bar} {pct:3d}%  {counter}"
        sys.stderr.write(msg)
        sys.stderr.flush()

    def finish(self) -> None:
        if self.tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _maybe_run_save_picker(
    abspath: Path,
    walk,
    config: RuntimeConfig,
    args: argparse.Namespace,
):
    """If `save_picker=true`, open the curses picker and re-walk on changes.

    Returns the (possibly updated) walk result. Falls through unchanged
    when the picker is disabled, when stdout isn't a TTY, or when the
    user skipped via 'q'/Esc.
    """

    from snapz import preferences

    if (
        not _stdout_is_tty()
        or getattr(args, "no_picker", False)
        or not preferences.get_config_value(Path(config.root), "save_picker")
    ):
        return walk

    from snapz import tui

    chosen = tui.run_save_picker(walk.files, abspath)
    if not chosen or not api.add_local_excludes(abspath, chosen, config=config):
        return walk

    print(
        f"{st.ok_mark()} "
        f"{t('msg.added_patterns_walk', n=st.numeric(str(len(chosen))))}"
    )
    walk = api.estimate(abspath, config=config, include_large=args.include_large)
    _print_walk_summary(walk)
    print()
    return walk


def cmd_save_interactive(args: argparse.Namespace, config: RuntimeConfig) -> int:
    """Default action when the user runs ``snapz`` or ``snapz <path>``."""

    raw_path = args.path or "."
    abspath = resolve_path(raw_path)
    if not abspath.is_dir():
        _print_error(t('msg.no_directory', path=abspath))
        return EXIT_ERROR

    print(f"\U0001f4c2 {st.path(str(abspath))}")

    store = Store(config)
    existing = store.list_snapshots(abspath)
    visible = [s for s in existing if not is_auto_snapshot(s.name)]
    if visible:
        n = len(visible)
        print(st.muted(
            t('msg.exists_n_snapshots', n=n, word=_pluralize(n, 'snapshot'))
        ))
        _print_snapshot_table(existing, show_auto=False)
    elif existing:
        # Only auto-* in the store: don't shout "no snapshots" — surface
        # that they exist but are hidden, so the user knows ``--all`` is
        # available and ``snapz undo`` has something to work with.
        print(st.muted(t('status.hidden_auto', n=len(existing)).strip()))
    else:
        print(st.muted(t('status.no_existing_snapshots')))
    print()

    default_name = auto_name()
    while True:
        name = _prompt(t('prompt.snapshot_name'), default=default_name)
        try:
            validate_snapshot_name(name)
        except ValueError as exc:
            print(f"  {st.warn(t('warn.bang'))} {exc}")
            continue
        if store.name_exists(abspath, name):
            print(
                f"  {st.warn(t('warn.bang'))} "
                f"{t('save.exists_warn', name=st.name(name))}"
            )
            choice = _prompt(t('prompt.overwrite_choice'), default='r').lower()
            if choice.startswith('o'):
                overwrite = True
                break
            if choice.startswith('a'):
                print(st.muted(t('status.aborted')))
                return EXIT_USER_ABORT
            continue
        else:
            overwrite = False
            break

    print()
    print(st.dim(t('status.planning')))
    walk = api.estimate(abspath, config=config, include_large=args.include_large)
    _print_walk_summary(walk)
    print()

    if walk.file_count == 0:
        print(st.warn(t('status.empty_walk')))
        return EXIT_USER_ABORT

    # Optional: if the user has enabled `save_picker`, let them prune
    # unwanted files into the per-source local-excludes file before we
    # commit. The picker is only useful in interactive TTY mode.
    walk = _maybe_run_save_picker(abspath, walk, config, args)
    if walk.file_count == 0:
        print(st.warn(t('status.empty_picker')))
        return EXIT_USER_ABORT

    if not _confirm(
        t('prompt.create_snapshot'), default_yes=False, assume_yes=args.yes,
    ):
        print(st.muted(t('status.aborted')))
        return EXIT_USER_ABORT

    note = (getattr(args, "message", "") or "").strip()
    if not note:
        # Interactive prompt: blank Enter keeps it empty.
        try:
            entered = _prompt(t('prompt.note_optional'), default='')
        except (EOFError, KeyboardInterrupt):
            entered = ''
        note = entered.strip()

    progress = _Progress(walk.file_count)
    started = time.monotonic()
    try:
        outcome = api.save(
            abspath,
            name=name,
            config=config,
            include_large=args.include_large,
            walk_result=walk,
            on_progress=progress,
            overwrite=overwrite,
            note=note,
        )
    finally:
        progress.finish()
    elapsed = time.monotonic() - started

    ratio = (
        outcome.pack_result.total_bytes_in / outcome.pack_result.bytes_written
        if outcome.pack_result.bytes_written > 0
        else 1.0
    )
    snapz = outcome.snapshot
    print(f"{st.ok_mark()} {t('msg.saved')} {st.name(snapz.name)}")
    if snapz.note:
        print(_kv(t('kv.note'), st.muted(snapz.note)))
    print(_kv(t('kv.archive'), st.path(str(outcome.pack_result.archive_path))))
    print(_kv(
        t('kv.size'),
        f"{st.numeric(format_size(snapz.size_bytes))}  "
        f"{st.muted('\u2190')}  {st.numeric(format_size(snapz.total_bytes_in))}"
        f"  {st.muted(f'({ratio:.1f}\u00d7 ratio)')}",
    ))
    print(_kv(
        t('kv.files'),
        f"{st.numeric(f'{snapz.file_count:,}')}  {st.bullet()}  "
        f"{st.numeric(format_duration(elapsed))}  {st.bullet()}  "
        f"{st.muted(snapz.compression)}",
    ))
    return EXIT_OK


def cmd_save_scripted(args: argparse.Namespace, config: RuntimeConfig) -> int:
    abspath = resolve_path(args.path)
    if not abspath.is_dir():
        _print_error(t('msg.no_directory', path=abspath))
        return EXIT_ERROR

    name = args.name or auto_name()
    try:
        validate_snapshot_name(name)
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    store = Store(config)
    if store.name_exists(abspath, name) and not args.overwrite:
        _print_error(
            f"{t('save.exists_warn', name=st.name(name))}"
            f"{st.muted(t('save.exists_hint'))}"
        )
        return EXIT_ERROR

    if not args.yes:
        walk = api.estimate(abspath, config=config, include_large=args.include_large)
        print(f"\U0001f4c2 {st.path(str(abspath))}")
        _print_walk_summary(walk)
        if not _confirm(t('prompt.create_snapshot'), default_yes=False):
            return EXIT_USER_ABORT
        walk_result = walk
    else:
        walk_result = None

    progress = _Progress(0)  # total filled in below if interactive
    started = time.monotonic()
    try:
        outcome = api.save(
            abspath,
            name=name,
            config=config,
            include_large=args.include_large,
            walk_result=walk_result,
            on_progress=progress if sys.stderr.isatty() else None,
            overwrite=args.overwrite,
            note=(getattr(args, "message", "") or "").strip(),
        )
    finally:
        progress.finish()
    elapsed = time.monotonic() - started

    if _wants_json(args):
        _emit_json({
            "snapshot": outcome.snapshot,
            "elapsed_seconds": round(elapsed, 3),
            "pack": {
                "archive_path": str(outcome.pack_result.archive_path),
                "bytes_written": outcome.pack_result.bytes_written,
                "file_count": outcome.pack_result.file_count,
                "total_bytes_in": outcome.pack_result.total_bytes_in,
                "compression": outcome.pack_result.compression,
            },
        })
        return EXIT_OK

    snapz = outcome.snapshot
    files_phrase = t(
        'label.files_count',
        n=f'{snapz.file_count:,}',
        word=_pluralize(snapz.file_count, 'file'),
    )
    sep = f"  {st.bullet()}  "
    line = (
        f"{st.ok_mark()} {t('msg.saved')} {st.name(snapz.name)}{sep}"
        f"{st.numeric(format_size(snapz.size_bytes))}{sep}"
        f"{st.numeric(files_phrase)}{sep}"
        f"{st.numeric(format_duration(elapsed))}"
    )
    if snapz.note:
        line += f"{sep}{st.muted(repr(snapz.note))}"
    print(line)
    return EXIT_OK


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


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def _resolve_snapshot_name(
    path: Path,
    given: Optional[str],
    config: RuntimeConfig,
    *,
    title_key: str,
    show_auto: bool = False,
) -> Optional[str]:
    """Pick a snapshot name interactively when *given* is missing.

    Returns the resolved name, or ``None`` to indicate the caller should
    treat this as an aborted/erroring command — *_print_error* is
    already called for the non-TTY case, so the caller just has to
    return the right exit code.

    The picker hides ``auto-*`` safety snapshots by default; pass
    ``show_auto=True`` (e.g. for ``rm --all``) to expose them so the
    user can still clean them up by hand if needed.
    """

    if given:
        return given
    if not _stdout_is_tty():
        _print_error(t("picker.no_name_given"))
        return None
    snaps = api.list_snapshots(path, config=config)
    visible = _filter_user_visible(snaps, show_auto=show_auto)
    if not visible:
        if snaps and not show_auto:
            _print_error(t("status.hidden_auto", n=len(snaps)).strip())
        else:
            _print_error(t("picker.no_snapshots_in", path=path))
        return None
    from snapz import tui
    chosen = tui.run_snapshot_picker(
        visible,
        title=t(title_key, path=str(path)),
    )
    if not chosen:
        print(st.muted(t("picker.cancelled")))
        return None
    return chosen


def cmd_rm(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_rm",
        show_auto=bool(getattr(args, "all", False)),
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    store = Store(config)
    meta = store.read_snapshot_meta(path, name)
    if meta is None:
        _print_error(t('msg.no_snapshot_named', name=st.name(name), path=path))
        return EXIT_ERROR
    if not args.yes and not _confirm(
        t(
            'prompt.delete_one',
            name=st.name(meta.name),
            size=st.muted(format_size(meta.size_bytes)),
        )
    ):
        return EXIT_USER_ABORT
    api.delete(path, name, config=config)
    print(f"{st.ok_mark()} {t('msg.deleted_one', name=st.name(meta.name))}")
    return EXIT_OK


def cmd_mv(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    old = _resolve_snapshot_name(
        path, args.old, config, title_key="picker.title_mv_old",
    )
    if old is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    new = args.new
    if not new:
        if not _stdout_is_tty():
            _print_error(t("picker.no_name_given"))
            return EXIT_ERROR
        from snapz import tui
        new = tui.prompt_text(
            t("picker.prompt_mv_new", old=old),
            initial=old,
        )
        if not new or new == old:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT
    try:
        ok = api.rename(path, old, new, config=config)
    except FileExistsError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if not ok:
        _print_error(t('msg.no_snapshot_named', name=st.name(old), path=path))
        return EXIT_ERROR
    print(
        f"{st.ok_mark()} {t('msg.renamed', old=st.name(old))} "
        f"{st.arrow()} {st.name(new)}"
    )
    return EXIT_OK


def cmd_restore(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_restore",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    return _restore_with_confirmation(
        path,
        name,
        config,
        auto_save=not args.no_auto_save,
        clean=args.clean,
        assume_yes=args.yes,
    )


def _restore_with_confirmation(
    path: Path,
    name: str,
    config: RuntimeConfig,
    *,
    auto_save: bool,
    clean: bool,
    assume_yes: bool,
) -> int:
    try:
        estimate = api.restore_estimate(path, name, config=config)
    except FileNotFoundError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    label_w = 14
    # Heading verb is bare (not "已 restored"); use the verb-form key so
    # ZH gets the imperative "还原" rather than the past-tense "已还原".
    print(
        f"{st.bold('\u21a9')} {t('verb.restore_imp')} {st.name(estimate.snapshot.name)} "
        f"{st.arrow()} {st.path(str(path))}"
    )
    print(_kv(t('kv.archive'), st.path(str(estimate.archive_path)), label_w=label_w))
    print(_kv(
        t('kv.archive_size'),
        f"{st.numeric(format_size(estimate.snapshot.size_bytes))}  "
        f"{st.muted(t('save.uncompressed_meta', size=format_size(estimate.archive_total_bytes), n=f'{estimate.archive_member_count:,}'))}",
        label_w=label_w,
    ))
    print(_kv(t('kv.to_overwrite'), st.numeric(f'{len(estimate.overwritten_files):,}'), label_w=label_w))
    print(_kv(t('kv.to_add'), st.numeric(f'{len(estimate.new_files):,}'), label_w=label_w))
    extras_count = len(estimate.extra_files)
    if clean:
        extras_value = (
            f"{st.warn(f'{extras_count:,}')}  "
            f"{st.muted(t('restore.will_clean'))}"
        )
    else:
        extras_value = (
            f"{st.numeric(f'{extras_count:,}')}  "
            f"{st.muted(t('restore.kept'))}"
        )
    print(_kv(t('kv.extras'), extras_value, label_w=label_w))
    if auto_save and path.exists():
        print(_kv(
            t('kv.pre_backup'),
            st.success(t('label.yes')) + st.muted(t('restore.pre_yes')),
            label_w=label_w,
        ))
    else:
        print(_kv(
            t('kv.pre_backup'),
            st.warn(t('label.no')) + st.muted(t('restore.pre_no')),
            label_w=label_w,
        ))

    if not _confirm(
        t('prompt.proceed_restore'), default_yes=False, assume_yes=assume_yes,
    ):
        print(st.muted(t('status.aborted')))
        return EXIT_USER_ABORT

    started = time.monotonic()
    outcome = api.restore(
        path,
        name,
        config=config,
        auto_save=auto_save,
        clean=clean,
    )
    elapsed = time.monotonic() - started

    print(f"{st.ok_mark()} {t('msg.restored')} {st.name(outcome.snapshot.name)}")
    print(_kv(
        t('kv.extracted'),
        st.numeric(f'{outcome.extracted_count:,}') + ' ' + t('label.entries_n'),
    ))
    if outcome.cleaned_count:
        print(_kv(
            t('kv.cleaned'),
            st.numeric(f'{outcome.cleaned_count:,}') + t('label.extras_suffix'),
        ))
    if outcome.pre_restore is not None:
        print(_kv(
            t('kv.roll_back'),
            f"{st.name(outcome.pre_restore.name)}  "
            f"{st.muted('(' + format_size(outcome.pre_restore.size_bytes) + ')')}",
        ))
    print(_kv(t('kv.time'), st.numeric(format_duration(elapsed))))
    return EXIT_OK


def _format_config_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def cmd_config(args: argparse.Namespace, config: RuntimeConfig) -> int:
    from snapz import preferences

    op = args.op
    root = Path(config.root)

    if op == "list":
        on_disk = preferences.load_config(root)
        key_w = max(len(k) for k in preferences.KNOWN_CONFIG_KEYS)
        for key, spec in preferences.KNOWN_CONFIG_KEYS.items():
            effective = on_disk.get(key, spec["default"])
            origin = st.muted(
                t("config.set_marker") if key in on_disk
                else t("config.default_marker")
            )
            value_text = _format_config_value(effective)
            print(
                f"{st.name(key.ljust(key_w))}  "
                f"{st.numeric(value_text.rjust(10))}  {origin}"
            )
            print(f"  {st.muted(spec['help'])}")
        return EXIT_OK

    if op in ("get", "set", "unset") and not args.key:
        _print_error(t('config.requires_key', op=op))
        return EXIT_ERROR

    try:
        if op == "get":
            value = preferences.get_config_value(root, args.key)
            print(_format_config_value(value))
            return EXIT_OK
        if op == "set":
            if args.value is None:
                _print_error(t('config.set_requires_value'))
                return EXIT_ERROR
            parsed = preferences.set_config_value(root, args.key, args.value)
            print(
                f"{st.ok_mark()} {st.name(args.key)} = "
                f"{st.numeric(_format_config_value(parsed))}"
            )
            return EXIT_OK
        if op == "unset":
            removed = preferences.unset_config_value(root, args.key)
            if removed:
                spec = preferences.KNOWN_CONFIG_KEYS.get(args.key)
                default = spec["default"] if spec else None
                print(
                    f"{st.ok_mark()} unset {st.name(args.key)} "
                    f"{st.muted(t('config.unset_default'))} "
                    f"{st.numeric(_format_config_value(default))}"
                )
            else:
                print(st.muted(t('config.was_not_set', key=args.key)))
            return EXIT_OK
    except KeyError as exc:
        _print_error(str(exc).strip("'\""))
        known = ", ".join(preferences.KNOWN_CONFIG_KEYS.keys())
        _print_error(t('config.known_keys', keys=known))
        return EXIT_ERROR
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    _print_error(t('config.unknown_op', op=op))
    return EXIT_ERROR


_STATUS_FNS = {
    "A": (st.green, "+"),
    "M": (st.yellow, "~"),
    "D": (st.red, "-"),
    "T": (st.yellow, "T"),
}


def _fmt_change_line(change, *, name_w: int) -> str:
    color_fn, sigil = _STATUS_FNS.get(change.status, (st.muted, change.status))
    if change.status == "A":
        suffix = f"+{format_size(change.size_b or 0)}"
    elif change.status == "D":
        suffix = f"-{format_size(change.size_a or 0)}"
    elif change.status == "T":
        suffix = f"{change.type_a or '?'} -> {change.type_b or '?'}"
    else:  # M
        suffix = f"{format_size(change.size_a or 0)} -> {format_size(change.size_b or 0)}"
    return (
        f"  {color_fn(f'{sigil} {change.status}')}  "
        f"{change.path.ljust(name_w)}  {st.muted(suffix)}"
    )


def cmd_diff(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")

    a_name = _resolve_snapshot_name(
        src, args.a, config, title_key="picker.title_diff_a",
    )
    if a_name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    # When the user gave only `snapz diff` (no args at all) we also offer
    # a B picker — including a synthetic [live] entry. When they passed
    # ``a`` explicitly but omitted ``b`` we keep the long-standing
    # contract: ``b is None`` means "compare A to live tree".
    b_name = args.b
    if not b_name and not args.a and _stdout_is_tty():
        snaps = api.list_snapshots(src, config=config)
        from snapz import tui
        chosen = tui.run_snapshot_picker(
            [s for s in snaps if s.name != a_name],
            title=t("picker.title_diff_b", path=str(src)),
            allow_live=True,
        )
        if chosen is None:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT
        if chosen != tui.LIVE:
            b_name = chosen
        # else: keep b_name as None ⇒ live diff

    try:
        result = api.diff(src, a_name, b_name, config=config)
    except (FileNotFoundError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    # The diff TUI is the default when interactive; --text forces plain
    # output, and --tui still works as an explicit opt-in.
    use_tui = (not args.text) and _stdout_is_tty()
    if use_tui:
        from snapz import preferences, tui

        def _read_a(relpath: str) -> Optional[bytes]:
            return api.read_snapshot_bytes(src, a_name, relpath, config=config)

        def _read_b(relpath: str) -> Optional[bytes]:
            if b_name is None:
                return api.read_live_bytes(src, relpath)
            return api.read_snapshot_bytes(src, b_name, relpath, config=config)

        chosen = tui.run_diff_view(result, read_a=_read_a, read_b=_read_b)
        if chosen:
            added = api.add_local_excludes(src, chosen, config=config)
            if added:
                ex_path = preferences.local_excludes_path(
                    Store(config).dir_for(src)
                )
                print(
                    f"{st.ok_mark()} "
                    f"{t('msg.added_patterns', n=st.numeric(str(added)), path=st.muted(str(ex_path)))}"
                )
        return EXIT_OK

    # Plain-text mode (default for non-TTY or no --tui flag)
    label = (
        f"{st.name(result.a_meta.name)} {st.muted(st.arrow())} "
        f"{st.name(result.b_meta.name) if result.b_meta else st.muted(t('label.diff_live'))}"
    )
    print(f"{t('msg.diff_label')} {label}  {st.muted('(' + str(result.abspath) + ')')}")
    if not result.changes:
        print(st.muted(t('status.no_changes')))
        return EXIT_OK

    name_w = max(8, *(len(c.path) for c in result.changes))
    for change in result.changes:
        print(_fmt_change_line(change, name_w=name_w))
    counts = t(
        'label.diff_counts',
        a=len(result.added), m=len(result.modified), d=len(result.deleted),
    )
    print(st.muted(f"\n{counts}"))
    return EXIT_OK


def cmd_export(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    dst = resolve_path(args.dst)
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_export",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    try:
        outcome = api.export(
            src, name, dst,
            config=config, overwrite=args.overwrite,
        )
    except FileNotFoundError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    except (NotADirectoryError, FileExistsError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    snapz = outcome.snapshot
    print(
        f"{st.ok_mark()} {t('msg.exported')} {st.name(snapz.name)}  "
        f"{st.muted(st.arrow())}  {st.path(str(outcome.destination))}"
    )
    if snapz.note:
        print(_kv(t('kv.note'), st.muted(snapz.note)))
    print(_kv(
        t('kv.extracted'),
        f"{st.numeric(f'{outcome.extracted_count:,}')} {t('label.entries_n')}  "
        f"{st.muted(t('save.uncompressed_meta', size=format_size(snapz.total_bytes_in), n=f'{outcome.extracted_count:,}'))}",
    ))
    return EXIT_OK


def cmd_gc(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        if args.all:
            result = api.gc(
                all_dirs=True, dry_run=args.dry_run, config=config
            )
        else:
            path = resolve_path(args.path or ".")
            result = api.gc(
                path, dry_run=args.dry_run, config=config
            )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK

    verb = t('verb.would_free') if args.dry_run else t('verb.freed')
    blobs = f"{result.blobs_removed:,}"
    suffix = st.muted(
        t('label.gc_summary', blobs=blobs, dirs=result.dirs_scanned)
    )
    if result.blobs_removed == 0:
        print(
            f"{st.ok_mark()} {t('status.nothing_to_reclaim')} "
            f"{st.muted(t('label.scan_summary', n=result.dirs_scanned))}"
        )
        return EXIT_OK
    print(
        f"{st.ok_mark()} {verb} "
        f"{st.numeric(format_size(result.bytes_freed))}  {suffix}"
    )
    return EXIT_OK


def cmd_show(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_show",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    meta = api.show(path, name, config=config)
    if meta is None:
        _print_error(t('msg.no_snapshot_named', name=st.name(name), path=path))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(meta)
        return EXIT_OK
    ratio = (meta.total_bytes_in / meta.size_bytes) if meta.size_bytes else 1.0
    print(st.name(meta.name))
    if meta.note:
        print(_kv(t('kv.note'), meta.note))
    print(_kv(t('kv.source'), st.path(str(meta.source))))
    print(_kv(t('kv.created'), st.muted(format_iso(meta.created))))
    print(_kv(
        t('kv.archive'),
        f"{st.path(str(meta.archive))}  {st.muted('(' + meta.compression + ')')}",
    ))
    print(_kv(
        t('kv.size'),
        f"{st.numeric(format_size(meta.size_bytes))}  {st.muted('\u2190')}  "
        f"{st.numeric(format_size(meta.total_bytes_in))}  "
        f"{st.muted(f'({ratio:.1f}\u00d7 ratio)')}",
    ))
    print(_kv(t('kv.files'), st.numeric(f'{meta.file_count:,}')))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def _print_stats_table(entries) -> None:
    if not entries:
        print(st.muted(t('status.no_sources_recorded')))
        return
    name_w = max(3, *(len(str(e.abspath)) for e in entries))
    header = (
        f"  {t('header.DIR').ljust(name_w)}  {t('header.SNAPS').rjust(5)}  "
        f"{t('header.ON_DISK').rjust(10)}  {t('header.LOGICAL').rjust(10)}  "
        f"{t('header.DEDUP').rjust(6)}  {t('header.NEWEST').ljust(16)}"
    )
    print(st.dim(header))
    total_disk = 0
    total_logical = 0
    for entry in entries:
        ratio = entry.dedup_ratio
        ratio_text = f"{ratio:.1f}\u00d7" if ratio else '\u2014'
        newest = format_iso(entry.newest) if entry.newest else '\u2014'
        print(
            f"  {st.path(str(entry.abspath).ljust(name_w))}  "
            f"{st.numeric(f'{entry.snapshot_count:,}'.rjust(5))}  "
            f"{st.numeric(format_size(entry.on_disk_bytes).rjust(10))}  "
            f"{st.numeric(format_size(entry.logical_bytes).rjust(10))}  "
            f"{st.numeric(ratio_text.rjust(6))}  "
            f"{st.muted(newest.ljust(16))}"
        )
        total_disk += entry.on_disk_bytes
        total_logical += entry.logical_bytes
    if len(entries) > 1:
        print(st.muted(t(
            'label.totals',
            disk=format_size(total_disk),
            logical=format_size(total_logical),
        )))


def cmd_stats(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.path is None and not args.all:
        # Default to the current directory; users explicitly opt into a
        # global view with --all so a `cd` mistake doesn't surface
        # everyone's snapshots.
        path = "."
    else:
        path = args.path
    entries = (
        api.stats(config=config) if args.all
        else api.stats(path, config=config)
    )

    if _wants_json(args):
        _emit_json({
            "scope": "all" if args.all else "single",
            "entries": entries,
        })
        return EXIT_OK

    # ``stats`` has historically promised a curses view, but the dedicated
    # widget was never finished. Until it is, always render the text
    # table — it's already informative and avoids an AttributeError.
    _print_stats_table(entries)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


def _print_prune_plan(plan) -> None:
    rules_bits = []
    for k in ("keep_last", "keep_within_days", "keep_daily", "keep_weekly"):
        v = plan.rules.get(k)
        if v:
            rules_bits.append(f"{k.replace('_', '-')}={v}")
    protect = plan.rules.get("protect") or []
    if isinstance(protect, list) and protect:
        rules_bits.append(f"protect={','.join(protect)}")
    print(
        f"\U0001f5d1  {t('verb.prune_imp')} {st.path(str(plan.abspath))}  "
        f"{st.muted('(' + (', '.join(rules_bits) or t('label.no_rules')) + ')')}"
    )
    print(_kv(t('kv.keep'), st.numeric(f'{len(plan.keep):,}')))
    print(_kv(t('kv.drop'), st.numeric(f'{len(plan.drop):,}')))
    if plan.drop:
        name_w = max(4, *(len(s.name) for s in plan.drop))
        print(st.dim(
            f"  {t('header.NAME').ljust(name_w)}  "
            f"{t('header.CREATED').ljust(16)}  {t('header.SIZE')}"
        ))
        for snapz in plan.drop:
            print(
                f"  {st.warn(snapz.name.ljust(name_w))}  "
                f"{st.muted(format_iso(snapz.created))}  "
                f"{st.numeric(format_size(snapz.size_bytes))}"
            )


def cmd_prune(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    try:
        plan = api.plan_prune(
            path,
            keep_last=args.keep_last,
            keep_within_days=args.keep_within_days,
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            protect=args.protect or (),
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    drop_names = [s.name for s in plan.drop]
    if not plan.keep and not plan.drop:
        print(st.muted(t('status.no_snapshots_dir')))
        return EXIT_OK

    use_tui = (
        not args.text
        and not args.dry_run
        and not args.yes
        and _stdout_is_tty()
    )
    if use_tui:
        from snapz import tui
        chosen = tui.run_prune_view(plan)
        if chosen is None:
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT
        drop_names = chosen
    else:
        _print_prune_plan(plan)
        if args.dry_run:
            print(st.muted(t('status.dry_run_nothing')))
            return EXIT_OK
        if not drop_names:
            print(st.muted(t('status.nothing_to_prune')))
            return EXIT_OK
        if not args.yes and not _confirm(
            t('prompt.delete_n', n=len(drop_names)),
            default_yes=False,
        ):
            return EXIT_USER_ABORT

    if not drop_names:
        print(st.muted(t('status.nothing_to_prune')))
        return EXIT_OK

    outcome = api.execute_prune(
        plan,
        drop_names=drop_names,
        run_gc=not args.no_gc,
        dry_run=args.dry_run,
        config=config,
    )
    verb = t('verb.would_delete') if outcome.dry_run else t('verb.deleted')
    print(
        f"{st.ok_mark()} {verb} {st.numeric(f'{len(outcome.deleted):,}')} "
        f"{t('label.snapshots_n')}"
    )
    if outcome.blobs_removed:
        gc_verb = t('verb.would_free') if outcome.dry_run else t('verb.freed')
        print(_kv(
            t('kv.gc'),
            f"{gc_verb} {st.numeric(format_size(outcome.bytes_freed))}  "
            f"{st.muted(t('label.blobs_paren', n=outcome.blobs_removed))}",
        ))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def _print_revert_outcome(outcome) -> None:
    print(
        f"{st.ok_mark()} {t('msg.reverted_from', name=st.name(outcome.snapshot.name))}  "
        f"{st.muted('(' + format_iso(outcome.snapshot.created) + ')')}"
    )
    print(_kv(
        t('kv.files'),
        st.numeric(f'{outcome.reverted_count:,}') + t('label.files_written_suffix'),
    ))
    if outcome.deleted_count:
        print(_kv(
            t('kv.cleaned'),
            st.numeric(f'{outcome.deleted_count:,}') + t('label.extras_suffix'),
        ))
    if outcome.pre_revert is not None:
        print(_kv(
            t('kv.roll_back'),
            f"{st.name(outcome.pre_revert.name)}  "
            f"{st.muted('(' + format_size(outcome.pre_revert.size_bytes) + ')')}",
        ))
    if outcome.skipped:
        print(_kv(
            t('kv.skipped'),
            st.warn(f'{len(outcome.skipped):,}') + ' ' + t('label.entries_paren'),
        ))
        for path, reason in outcome.skipped[:5]:
            print(f"    {st.muted('-')} {path}  {st.muted('(' + reason + ')')}")
        if len(outcome.skipped) > 5:
            print(
                f"    {st.muted(t('label.dots_more', n=len(outcome.skipped) - 5))}"
            )


def cmd_revert(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_revert",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    try:
        # Pre-flight: load manifest so we can drive the picker / validate.
        from snapz import api as _api  # for clarity below
        _abspath, snap_meta, manifest = _api._load_manifest_or_raise(  # noqa: SLF001
            src, name, config=config,
        )
    except FileNotFoundError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    paths = list(args.paths or [])
    if not paths:
        if args.text or not _stdout_is_tty():
            _print_error(t('picker.no_paths_given'))
            return EXIT_ERROR
        from snapz import tui
        paths = tui.run_revert_picker(manifest.entries, src)
        if not paths:
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT

    print(
        f"{st.bold('\u21a9')} {t('verb.revert_imp')} {st.name(snap_meta.name)} "
        f"{st.arrow()} {st.path(str(src))}"
    )
    print(_kv(t('kv.paths'), st.numeric(f'{len(paths):,}')))
    for p in paths[:5]:
        print(f"    {st.muted('-')} {p}")
    if len(paths) > 5:
        print(f"    {st.muted(t('label.dots_more', n=len(paths) - 5))}")
    if args.delete_extras:
        print(_kv(
            t('kv.clean'),
            st.warn(t('label.yes')) + st.muted(t('revert.clean_yes')),
        ))
    pre_text = (
        st.success(t('label.yes')) + st.muted(t('revert.pre_yes'))
        if not args.no_auto_save
        else st.warn(t('label.no')) + st.muted(t('revert.pre_no'))
    )
    print(_kv(t('kv.pre_backup'), pre_text))

    if not args.yes and not _confirm(
        t('prompt.proceed_revert'), default_yes=False,
    ):
        print(st.muted(t('status.aborted')))
        return EXIT_USER_ABORT

    outcome = api.revert(
        src,
        name,
        paths,
        config=config,
        auto_save=not args.no_auto_save,
        delete_extras=args.delete_extras,
    )
    _print_revert_outcome(outcome)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Undo (v0.2)
# ---------------------------------------------------------------------------


def cmd_undo(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")

    target = api.find_undo_target(src, config=config)
    if target is None:
        if _wants_json(args):
            _emit_json({"undone": False, "reason": "no-undo-target"})
            return EXIT_OK
        _print_error(t("undo.no_target", path=src))
        return EXIT_ERROR

    if _wants_json(args):
        if not args.yes:
            # JSON consumers don't want an interactive prompt; require -y.
            _emit_json({
                "undone": False,
                "reason": "needs-confirmation",
                "target": target,
            })
            return EXIT_ERROR
        outcome = api.undo(
            src, config=config, clean=not args.no_clean,
        )
        _emit_json({"undone": True, "outcome": outcome})
        return EXIT_OK

    # Pretty preview
    print(
        f"{st.bold('\u21a9')} {t('undo.heading', name=st.name(target.name))}"
    )
    print(_kv(t('undo.captured'), st.muted(format_iso(target.created))))
    print(_kv(t('kv.source'), st.path(str(src))))
    print(_kv(t('kv.files'), st.numeric(f'{target.file_count:,}')))
    print(_kv(
        t('kv.size'),
        f"{st.numeric(format_size(target.size_bytes))}",
    ))
    if target.note:
        print(_kv(t('kv.note'), st.muted(target.note)))

    if not args.yes and not _confirm(
        t('undo.confirm'), default_yes=False,
    ):
        print(st.muted(t('status.aborted')))
        return EXIT_USER_ABORT

    started = time.monotonic()
    outcome = api.undo(src, config=config, clean=not args.no_clean)
    elapsed = time.monotonic() - started

    print(
        f"{st.ok_mark()} "
        f"{t('undo.success', when=format_iso(target.created))}"
    )
    print(_kv(
        t('kv.extracted'),
        st.numeric(f'{outcome.extracted_count:,}') + ' ' + t('label.entries_n'),
    ))
    if outcome.cleaned_count:
        print(_kv(
            t('kv.cleaned'),
            st.numeric(f'{outcome.cleaned_count:,}') + t('label.extras_suffix'),
        ))
    print(_kv(t('undo.remaining'), st.numeric(str(outcome.remaining))))
    print(_kv(t('kv.time'), st.numeric(format_duration(elapsed))))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Find (v0.2)
# ---------------------------------------------------------------------------


def _print_find_text(result, path: Path) -> None:
    if not result.by_path:
        print(st.muted(t('find.no_matches', pattern=result.pattern, path=path)))
        return

    # Newest-first ordering inside each path.
    name_w = max(8, *(len(s.name) for hits in result.by_path.values() for s in (h.snapshot for h in hits)))
    for matched_path, hits in result.by_path.items():
        print(f"{st.path(matched_path)}")
        for hit in hits:
            sha_short = (hit.sha256 or "")[:12] if hit.type == "file" else (hit.target or "")
            sha_cell = sha_short[:12].ljust(12)
            size_cell = (
                format_size(hit.size or 0).rjust(10) if hit.type == "file"
                else "→".rjust(10)
            )
            marker = (
                f"  {st.warn(t('find.changed_marker'))}"
                if hit.changed_from_prev else ""
            )
            print(
                f"  {st.name(hit.snapshot.name.ljust(name_w))}  "
                f"{st.muted(format_iso(hit.snapshot.created).ljust(16))}  "
                f"{st.numeric(size_cell)}  "
                f"{st.muted(sha_cell)}{marker}"
            )
        print()

    scanned = sum(len(v) for v in result.by_path.values())
    print(st.muted(t(
        'find.summary',
        paths=len(result.by_path),
        hits=result.total_hits,
        scanned=scanned,
    )))


def cmd_find(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    pattern = (args.pattern or "").strip()
    if not pattern:
        _print_error(t('find.no_matches', pattern=pattern, path=src))
        return EXIT_ERROR

    result = api.find(
        src, pattern,
        config=config,
        include_auto=bool(getattr(args, "all", False)),
    )

    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK

    _print_find_text(result, src)
    return EXIT_OK if result.by_path else EXIT_ERROR


# ---------------------------------------------------------------------------
# Argparse plumbing
# ---------------------------------------------------------------------------


def _snapshot_name_completer(prefix, parsed_args, **kwargs):
    """argcomplete dynamic completer: list snapshot names for the target dir."""

    try:
        raw = getattr(parsed_args, "path", None) or "."
        snaps = api.list_snapshots(Path(raw))
        return [s.name for s in snaps if s.name.startswith(prefix)]
    except Exception:
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapz",
        description=t("root.description"),
    )
    parser.add_argument("--version", action="version", version=f"snapz {__version__}")
    parser.add_argument(
        "--no-zstd",
        action="store_true",
        help=t("flag.no_zstd"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=t("flag.json"),
    )

    sub = parser.add_subparsers(dest="command")

    # save (scripted)
    p_save = sub.add_parser("save", help=t("save.help"))
    p_save.add_argument("path")
    p_save.add_argument("-n", "--name")
    p_save.add_argument("-y", "--yes", action="store_true", help=t("save.yes"))
    p_save.add_argument("--overwrite", action="store_true")
    p_save.add_argument("--include-large", action="store_true")
    p_save.add_argument(
        "-m", "--message", default="", metavar="NOTE",
        help=t("save.message"),
    )
    p_save.set_defaults(func=cmd_save_scripted)

    # list (current dir)
    p_list = sub.add_parser("list", help=t("list.help"))
    p_list.add_argument("path", nargs="?")
    p_list.add_argument(
        "--text",
        action="store_true",
        help=t("list.text"),
    )
    p_list.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_list.set_defaults(func=cmd_list)

    # alist (global)
    p_alist = sub.add_parser("alist", help=t("alist.help"))
    p_alist.add_argument(
        "--text",
        action="store_true",
        help=t("list.text"),
    )
    p_alist.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_alist.set_defaults(func=cmd_alist)

    # rm
    p_rm = sub.add_parser("rm", help=t("rm.help"))
    p_rm.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_rm.add_argument("--path", help=t("rm.path"))
    p_rm.add_argument("-y", "--yes", action="store_true")
    p_rm.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_rm.set_defaults(func=cmd_rm)

    # mv
    p_mv = sub.add_parser("mv", help=t("mv.help"))
    p_mv.add_argument("old", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_mv.add_argument("new", nargs="?")
    p_mv.add_argument("--path", help=t("mv.path"))
    p_mv.set_defaults(func=cmd_mv)

    # show
    p_show = sub.add_parser("show", help=t("show.help"))
    p_show.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_show.add_argument("--path", help=t("show.path"))
    p_show.set_defaults(func=cmd_show)

    # restore
    p_restore = sub.add_parser(
        "restore",
        help=t("restore.help"),
    )
    p_restore.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_restore.add_argument("--path", help=t("restore.path"))
    p_restore.add_argument(
        "-y", "--yes", action="store_true", help=t("restore.yes")
    )
    p_restore.add_argument(
        "--no-auto-save",
        action="store_true",
        help=t("restore.no_auto_save"),
    )
    p_restore.add_argument(
        "--clean",
        action="store_true",
        help=t("restore.clean"),
    )
    p_restore.set_defaults(func=cmd_restore)

    # export
    p_export = sub.add_parser(
        "export",
        help=t("export.help"),
    )
    p_export.add_argument("name", nargs="?", help=t("export.name")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_export.add_argument("dst", help=t("export.dst"))
    p_export.add_argument(
        "--path",
        help=t("export.path"),
    )
    p_export.add_argument(
        "--overwrite",
        action="store_true",
        help=t("export.overwrite"),
    )
    p_export.set_defaults(func=cmd_export)

    # diff
    p_diff = sub.add_parser(
        "diff",
        help=t("diff.help"),
    )
    p_diff.add_argument("a", nargs="?", help=t("diff.a")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_diff.add_argument(
        "b", nargs="?",
        help=t("diff.b"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_diff.add_argument(
        "--path", help=t("diff.path"),
    )
    p_diff.add_argument(
        "--text", action="store_true",
        help=t("diff.text"),
    )
    p_diff.add_argument(
        "--tui", action="store_true",
        help=t("diff.tui"),
    )
    p_diff.set_defaults(func=cmd_diff)

    # config
    p_config = sub.add_parser(
        "config",
        help=t("config.help"),
    )
    p_config.add_argument(
        "op",
        choices=["get", "set", "unset", "list"],
        help=t("config.op"),
    )
    p_config.add_argument("key", nargs="?", help=t("config.key"))
    p_config.add_argument("value", nargs="?", help=t("config.value"))
    p_config.set_defaults(func=cmd_config)

    # gc
    p_gc = sub.add_parser(
        "gc",
        help=t("gc.help"),
    )
    p_gc.add_argument("--path", help=t("gc.path"))
    p_gc.add_argument(
        "--all", action="store_true",
        help=t("gc.all"),
    )
    p_gc.add_argument(
        "--dry-run", action="store_true",
        help=t("gc.dry_run"),
    )
    p_gc.set_defaults(func=cmd_gc)

    # stats
    p_stats = sub.add_parser(
        "stats",
        help=t("stats.help"),
    )
    p_stats.add_argument(
        "path", nargs="?",
        help=t("stats.path"),
    )
    p_stats.add_argument(
        "--all", action="store_true",
        help=t("stats.all"),
    )
    p_stats.add_argument(
        "--text", action="store_true",
        help=t("stats.text"),
    )
    p_stats.set_defaults(func=cmd_stats)

    # prune
    p_prune = sub.add_parser(
        "prune",
        help=t("prune.help"),
    )
    p_prune.add_argument(
        "--path", help=t("prune.path"),
    )
    p_prune.add_argument(
        "--keep-last", type=int, metavar="N",
        help=t("prune.keep_last"),
    )
    p_prune.add_argument(
        "--keep-within-days", type=int, metavar="DAYS",
        help=t("prune.keep_within"),
    )
    p_prune.add_argument(
        "--keep-daily", type=int, metavar="N",
        help=t("prune.keep_daily"),
    )
    p_prune.add_argument(
        "--keep-weekly", type=int, metavar="N",
        help=t("prune.keep_weekly"),
    )
    p_prune.add_argument(
        "--protect", action="append", metavar="NAME",
        help=t("prune.protect"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_prune.add_argument(
        "-y", "--yes", action="store_true",
        help=t("prune.yes"),
    )
    p_prune.add_argument(
        "--dry-run", action="store_true",
        help=t("prune.dry_run"),
    )
    p_prune.add_argument(
        "--no-gc", action="store_true",
        help=t("prune.no_gc"),
    )
    p_prune.add_argument(
        "--text", action="store_true",
        help=t("prune.text"),
    )
    p_prune.set_defaults(func=cmd_prune)

    # revert
    p_revert = sub.add_parser(
        "revert",
        help=t("revert.help"),
    )
    p_revert.add_argument(
        "name", nargs="?", help=t("revert.name"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_revert.add_argument(
        "paths", nargs="*",
        help=t("revert.paths"),
    )
    p_revert.add_argument(
        "--path", help=t("revert.path"),
    )
    p_revert.add_argument(
        "-y", "--yes", action="store_true",
        help=t("revert.yes"),
    )
    p_revert.add_argument(
        "--no-auto-save", action="store_true",
        help=t("revert.no_auto_save"),
    )
    p_revert.add_argument(
        "--delete-extras", action="store_true",
        help=t("revert.delete_extras"),
    )
    p_revert.add_argument(
        "--text", action="store_true",
        help=t("revert.text"),
    )
    p_revert.set_defaults(func=cmd_revert)

    # undo (v0.2)
    p_undo = sub.add_parser("undo", help=t("undo.help"))
    p_undo.add_argument("--path", help=t("undo.path"))
    p_undo.add_argument(
        "-y", "--yes", action="store_true",
        help=t("undo.yes"),
    )
    p_undo.add_argument(
        "--no-clean", action="store_true",
        help=t("undo.no_clean"),
    )
    p_undo.set_defaults(func=cmd_undo)

    # find (v0.2)
    p_find = sub.add_parser("find", help=t("find.help"))
    p_find.add_argument("pattern", help=t("find.pattern"))
    p_find.add_argument("--path", help=t("find.path"))
    p_find.add_argument(
        "--all", action="store_true",
        help=t("find.all"),
    )
    p_find.add_argument(
        "--text", action="store_true",
        help=t("find.text"),
    )
    p_find.set_defaults(func=cmd_find)

    return parser


def _emit_abort() -> None:
    """Best-effort ``aborted.`` notice that survives a second SIGINT.

    A user mashing Ctrl-C can deliver another SIGINT *while* we're
    printing the notice; without this guard the second one bubbles all
    the way out and the bootloader (or shell) reports an "unhandled
    exception" instead of a clean exit.
    """

    try:
        sys.stderr.write("\n" + t("status.aborted") + "\n")
        sys.stderr.flush()
    except (KeyboardInterrupt, BrokenPipeError, OSError):
        pass


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _main_impl(argv)
    except KeyboardInterrupt:
        _emit_abort()
        return EXIT_USER_ABORT


def _main_impl(argv: Optional[list[str]]) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()

    # Optional shell-completion hook. Only fires when invoked through
    # the argcomplete environment (``_ARGCOMPLETE``); otherwise it's a
    # no-op and won't slow down regular CLI invocations.
    try:
        import argcomplete  # type: ignore
    except ImportError:
        pass
    else:
        argcomplete.autocomplete(parser)

    # Handle the bare ``snapz`` and ``snapz <path>`` forms before argparse
    # gets a chance to complain about an unknown positional. Anything that
    # starts with ``-`` (e.g. ``--version``, ``--help``) goes through
    # argparse so the user gets the expected behaviour.
    known_subs = {
        "save", "list", "alist", "rm", "mv", "show", "restore",
        "gc", "export", "config", "diff",
        "stats", "prune", "revert",
        "undo", "find",
    }
    enter_bare_mode = (
        not argv
        or (not argv[0].startswith("-") and argv[0] not in known_subs)
    )
    if enter_bare_mode:
        path = argv[0] if argv else "."
        if len(argv) > 1:
            print(t('bare.too_many_args', argv=argv), file=sys.stderr)
            return EXIT_ERROR
        config = default_config()
        ns = argparse.Namespace(
            path=path,
            yes=False,
            include_large=False,
            message="",
            no_picker=False,
        )
        return cmd_save_interactive(ns, config)

    # Root-level flags (``--no-zstd``, ``--json``) are valid both before
    # and after the subcommand for ergonomics, but argparse only honours
    # them in the leading position. Strip+remember them here so any
    # ordering works (``snapz list --json`` and ``snapz --json list``
    # are equivalent).
    json_requested = "--json" in argv
    no_zstd_requested = "--no-zstd" in argv
    argv = [a for a in argv if a not in ("--json",)]

    args = parser.parse_args(argv)
    config = default_config()
    if getattr(args, "no_zstd", False) or no_zstd_requested:
        config = RuntimeConfig(
            root=config.root,
            large_file_bytes=config.large_file_bytes,
            follow_symlinks=config.follow_symlinks,
            use_zstd=False,
        )

    if json_requested:
        args.json = True

    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK

    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
