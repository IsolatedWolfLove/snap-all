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
import getpass
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from snapz import __version__, api, archive, events, preferences, remote
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

SNAPZ_PACKAGE_NAME = "snapz-cli"
SNAPZ_GITHUB_REPO = "https://github.com/IsolatedWolfLove/snap-all.git"
SNAPZ_GITHUB_INSTALL_TARGET = (
    f"{SNAPZ_PACKAGE_NAME}[zstd] @ git+{SNAPZ_GITHUB_REPO}"
)


SUGGEST_EXCLUDE_DIRS = {
    ".cache",
    ".parcel-cache",
    ".turbo",
    "coverage",
    "tmp",
}
SUGGEST_EXCLUDE_SUFFIXES = {
    ".7z",
    ".avi",
    ".bak",
    ".bz2",
    ".dmg",
    ".gz",
    ".iso",
    ".log",
    ".mov",
    ".mp4",
    ".tar",
    ".tgz",
    ".webm",
    ".xz",
    ".zip",
    ".zst",
}


@dataclass
class _ExcludeSuggestion:
    pattern: str
    bytes_total: int
    file_count: int


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


def _looks_binary(data: bytes) -> bool:
    """Cheap binary heuristic for terminal-safe previews/output."""

    return b"\x00" in data[:8192]


def _path_total_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for filename in filenames:
            try:
                total += (Path(dirpath) / filename).lstat().st_size
            except OSError:
                continue
    return total


def _format_data_size(num_bytes: int) -> str:
    return f"{format_size(num_bytes)} ({num_bytes / (1024 ** 3):.2f} GB)"


def _run_pip(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", *args],
        check=False,
        text=True,
    )


def _delete_data_root(root: Path) -> bool:
    root = Path(root).expanduser()
    if not root.exists():
        return False
    resolved = root.resolve()
    unsafe_roots = {Path("/").resolve(), Path.home().resolve()}
    if resolved in unsafe_roots:
        raise ValueError(t("uninstall.refuse_delete_root", path=resolved))
    shutil.rmtree(resolved)
    return True


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


def _maybe_run_first_source_config(
    abspath: Path,
    config: RuntimeConfig,
    *,
    assume_yes: bool = False,
) -> None:
    if assume_yes or not _stdout_is_tty():
        return
    store = Store(config)
    dir_root = store.ensure_dir(abspath)
    if preferences.source_configured(dir_root):
        return

    print()
    print(st.bold(t("source_config.heading")))
    print(st.muted(t("source_config.body")))
    for key, patterns in preferences.SOURCE_EXCLUDE_PRESETS.items():
        label = preferences.SOURCE_PRESET_LABELS.get(key, key)
        print(f"  {st.name(key.ljust(7))} {label}  {st.muted(', '.join(patterns))}")

    raw = _prompt(t("source_config.include_prompt"), default="")
    requested = {
        part.strip().lower()
        for part in raw.replace(",", " ").split()
        if part.strip()
    }
    include_presets = [
        key for key in preferences.SOURCE_EXCLUDE_PRESETS
        if key in requested
    ]
    include_patterns: list[str] = []
    for key in include_presets:
        include_patterns.extend(
            f"!{pattern}" for pattern in preferences.SOURCE_EXCLUDE_PRESETS[key]
        )
    if include_patterns:
        added = preferences.append_local_excludes(dir_root, include_patterns)
        print(st.muted(t("source_config.added", n=added)))
    preferences.mark_source_configured(
        dir_root,
        include_presets=include_presets,
        skipped=not include_presets,
    )


def _suggest_excludes(walk: WalkResult) -> list[_ExcludeSuggestion]:
    buckets: dict[str, tuple[int, int]] = {}
    for entry in walk.files:
        rel = entry.relpath.replace("\\", "/")
        parts = rel.split("/")
        pattern = ""
        if len(parts) > 1:
            for index, part in enumerate(parts[:-1]):
                candidate = "/".join(parts[: index + 1])
                if index == 0 and (
                    part in SUGGEST_EXCLUDE_DIRS
                    or candidate in SUGGEST_EXCLUDE_DIRS
                ):
                    pattern = candidate + "/"
                    break
        if not pattern:
            suffixes = "".join(Path(parts[-1]).suffixes[-2:]).lower()
            suffix = Path(parts[-1]).suffix.lower()
            if suffixes in SUGGEST_EXCLUDE_SUFFIXES:
                pattern = f"*{suffixes}"
            elif suffix in SUGGEST_EXCLUDE_SUFFIXES:
                pattern = f"*{suffix}"
        if not pattern:
            continue
        bytes_total, file_count = buckets.get(pattern, (0, 0))
        buckets[pattern] = (bytes_total + entry.size, file_count + 1)

    suggestions = [
        _ExcludeSuggestion(pattern, bytes_total, file_count)
        for pattern, (bytes_total, file_count) in buckets.items()
    ]
    min_bytes = max(2 * 1024 * 1024, int(walk.total_bytes * 0.05))
    suggestions = [
        s for s in suggestions
        if s.bytes_total >= min_bytes or s.file_count >= 100
    ]
    suggestions.sort(key=lambda s: (s.bytes_total, s.file_count), reverse=True)
    return suggestions[:5]


def _maybe_offer_exclude_suggestions(
    abspath: Path,
    walk: WalkResult,
    config: RuntimeConfig,
    args: argparse.Namespace,
) -> WalkResult:
    if getattr(args, "yes", False) or getattr(args, "include_large", False):
        return walk
    if not _stdout_is_tty():
        return walk
    suggestions = _suggest_excludes(walk)
    if not suggestions:
        return walk

    print()
    print(st.bold(t("suggest.heading")))
    for suggestion in suggestions:
        print(
            "  "
            f"{st.warn(suggestion.pattern.ljust(16))}  "
            f"{st.numeric(format_size(suggestion.bytes_total)).rjust(8)}  "
            f"{st.muted(t('label.files_count', n=f'{suggestion.file_count:,}', word=_pluralize(suggestion.file_count, 'file')))}"
        )
    total = sum(s.bytes_total for s in suggestions)
    if walk.total_bytes > 0:
        pct = total / walk.total_bytes * 100
        print(st.muted(t("suggest.summary", pct=f"{pct:.0f}")))
    if not _confirm(t("suggest.confirm"), default_yes=False):
        return walk

    store = Store(config)
    dir_root = store.dir_for(abspath)
    added = preferences.append_local_excludes(
        dir_root, [s.pattern for s in suggestions],
    )
    if added <= 0:
        print(st.muted(t("suggest.none_added")))
        return walk
    print(st.muted(t("suggest.added", n=added)))
    next_walk = api.estimate(
        abspath, config=config, include_large=args.include_large,
    )
    _print_walk_summary(next_walk)
    return next_walk


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
    _maybe_run_first_source_config(abspath, config, assume_yes=args.yes)
    print(st.dim(t('status.planning')))
    walk = api.estimate(abspath, config=config, include_large=args.include_large)
    _print_walk_summary(walk)
    print()

    if walk.file_count == 0:
        print(st.warn(t('status.empty_walk')))
        return EXIT_USER_ABORT

    walk = _maybe_offer_exclude_suggestions(abspath, walk, config, args)
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
            use_file_cache=not getattr(args, "no_cache", False),
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
        f"{st.muted(t('kv.full_size') + ':')}  "
        f"{st.numeric(format_size(snapz.total_bytes_in))}"
        f"  {st.muted(f'({ratio:.1f}\u00d7 dedup)')}",
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
        if walk.file_count == 0:
            print(st.warn(t('status.empty_walk')))
            return EXIT_USER_ABORT
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
            use_file_cache=not getattr(args, "no_cache", False),
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
        f"{st.numeric(format_size(snapz.size_bytes))} {st.muted('new')}{sep}"
        f"{st.numeric(format_size(snapz.total_bytes_in))} {st.muted('full')}{sep}"
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


def _print_path_preview(label: str, paths: list[str], *, warn: bool = False) -> None:
    if not paths:
        return
    renderer = st.warn if warn else st.path
    shown = paths[:5]
    print(f"  {st.muted(label)}")
    for rel in shown:
        print(f"    {renderer(rel)}")
    remaining = len(paths) - len(shown)
    if remaining > 0:
        print(f"    {st.muted(t('restore.more_paths', n=remaining))}")


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
    if _wants_json(args) and not args.yes:
        _emit_json({"deleted": False, "reason": "needs-confirmation", "snapshot": meta})
        return EXIT_ERROR
    if not args.yes and not _confirm(
        t(
            'prompt.delete_one',
            name=st.name(meta.name),
            size=st.muted(format_size(meta.size_bytes)),
        )
    ):
        return EXIT_USER_ABORT
    try:
        deleted = api.delete(path, name, config=config)
    except PermissionError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json({"deleted": deleted, "snapshot": meta})
        return EXIT_OK
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
    if _wants_json(args):
        _emit_json({"renamed": True, "old": old, "new": new, "path": path})
        return EXIT_OK
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
        wants_json=_wants_json(args),
    )


def _restore_with_confirmation(
    path: Path,
    name: str,
    config: RuntimeConfig,
    *,
    auto_save: bool,
    clean: bool,
    assume_yes: bool,
    wants_json: bool = False,
) -> int:
    try:
        estimate = api.restore_estimate(path, name, config=config)
    except FileNotFoundError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if wants_json:
        if not assume_yes:
            _emit_json({
                "restored": False,
                "reason": "needs-confirmation",
                "estimate": estimate,
            })
            return EXIT_ERROR
        try:
            outcome = api.restore(
                path,
                name,
                config=config,
                auto_save=auto_save,
                clean=clean,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            _emit_json({"restored": False, "reason": str(exc)})
            return EXIT_ERROR
        _emit_json({"restored": True, "outcome": outcome})
        return EXIT_OK

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
    preview_groups = [
        (t("restore.preview_overwrite"), estimate.overwritten_files, False),
        (t("restore.preview_add"), estimate.new_files, False),
    ]
    if clean:
        preview_groups.append(
            (t("restore.preview_clean"), estimate.extra_files, True),
        )
    elif estimate.extra_files:
        preview_groups.append(
            (t("restore.preview_keep"), estimate.extra_files, False),
        )
    for label, paths, warn in preview_groups:
        _print_path_preview(label, paths, warn=warn)
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
    try:
        outcome = api.restore(
            path,
            name,
            config=config,
            auto_save=auto_save,
            clean=clean,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
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
        if _wants_json(args):
            _emit_json({
                "config": preferences.effective_config(root),
                "overrides": on_disk,
            })
            return EXIT_OK
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
            if _wants_json(args):
                _emit_json({"key": args.key, "value": value})
                return EXIT_OK
            print(_format_config_value(value))
            return EXIT_OK
        if op == "set":
            if args.value is None:
                _print_error(t('config.set_requires_value'))
                return EXIT_ERROR
            parsed = preferences.set_config_value(root, args.key, args.value)
            if _wants_json(args):
                _emit_json({"key": args.key, "value": parsed, "set": True})
                return EXIT_OK
            print(
                f"{st.ok_mark()} {st.name(args.key)} = "
                f"{st.numeric(_format_config_value(parsed))}"
            )
            return EXIT_OK
        if op == "unset":
            removed = preferences.unset_config_value(root, args.key)
            if _wants_json(args):
                default = preferences.KNOWN_CONFIG_KEYS.get(args.key, {}).get("default")
                _emit_json({
                    "key": args.key,
                    "removed": removed,
                    "value": default,
                })
                return EXIT_OK
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

    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK

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
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
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


def cmd_bundle(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = api.export_bundle(
            args.source,
            args.dst,
            config=config,
            overwrite=args.overwrite,
            archived=args.archive,
        )
    except (FileNotFoundError, FileExistsError, IsADirectoryError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    print(
        f"{st.ok_mark()} {t('msg.bundled')} "
        f"{st.numeric(f'{outcome.snapshot_count:,}')} {t('label.snapshots_n')}  "
        f"{st.muted(st.arrow())}  {st.path(str(outcome.destination))}"
    )
    print(_kv(t('kv.source'), st.path(str(outcome.source))))
    print(_kv(t('kv.blobs'), st.numeric(f'{outcome.blob_count:,}')))
    print(_kv(t('kv.size'), st.numeric(format_size(outcome.size_bytes))))
    return EXIT_OK


def cmd_import(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = api.import_bundle(
            args.bundle,
            config=config,
            path=args.path,
            overwrite=args.overwrite,
        )
    except (
        FileNotFoundError,
        FileExistsError,
        NotADirectoryError,
        ValueError,
        tarfile.TarError,
    ) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    print(
        f"{st.ok_mark()} {t('msg.imported')} "
        f"{st.numeric(f'{outcome.snapshot_count:,}')} {t('label.snapshots_n')}  "
        f"{st.muted(st.arrow())}  {st.path(str(outcome.source))}"
    )
    print(_kv(t('kv.key'), st.muted(outcome.key)))
    print(_kv(t('kv.blobs'), st.numeric(f'{outcome.blob_count:,}')))
    state = "archive" if outcome.archived else "active"
    print(_kv(t('kv.state'), st.warn(state) if outcome.archived else st.success(state)))
    if outcome.overwritten_snapshots:
        print(_kv(
            t('kv.overwritten'),
            ", ".join(st.name(n) for n in outcome.overwritten_snapshots),
        ))
    return EXIT_OK


def cmd_login(args: argparse.Namespace, config: RuntimeConfig) -> int:
    tenant = args.tenant or _prompt("Tenant", "default")
    username = args.username or _prompt("Username")
    if not tenant or not username:
        _print_error("tenant and username are required")
        return EXIT_ERROR
    password = args.password
    if password is None:
        try:
            password = getpass.getpass("Password: ")
        except EOFError:
            password = ""
    if not password:
        _print_error("password is required")
        return EXIT_ERROR
    try:
        auth = remote.login(
            args.server,
            tenant=tenant,
            username=username,
            password=password,
            device_name=args.device or "",
            tls_ca=args.tls_ca or "",
            tls_client_cert=args.tls_client_cert or "",
            tls_client_key=args.tls_client_key or "",
            config=config,
        )
    except (ValueError, remote.RemoteError, KeyError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(auth)
        return EXIT_OK
    print(f"{st.ok_mark()} logged in to {st.path(auth.server_url)}")
    print(_kv("tenant", st.name(auth.tenant)))
    print(_kv("user", st.name(auth.username)))
    print(_kv("device", st.muted(auth.device_id)))
    return EXIT_OK


def cmd_logout(args: argparse.Namespace, config: RuntimeConfig) -> int:
    existed = remote.logout(config)
    if _wants_json(args):
        _emit_json({"logged_out": existed})
        return EXIT_OK
    if existed:
        print(f"{st.ok_mark()} logged out")
    else:
        print(st.muted("not logged in"))
    return EXIT_OK


def _print_sync_outcome(verb: str, outcome: remote.SyncOutcome) -> None:
    print(
        f"{st.ok_mark() if outcome.ok else st.warn('!')} "
        f"{verb} {st.numeric(str(len(outcome.items)))} source(s)  "
        f"{st.muted(outcome.server_url)}"
    )
    for item in outcome.items:
        print(f"  {st.muted('-')} {remote.format_sync_item(item)}")
        print(f"    {st.muted(item.source_id)}  {st.muted(item.key)}")
    if outcome.failures:
        print(_kv("failed", st.warn(str(len(outcome.failures)))))
        for failure in outcome.failures:
            where = failure.source_id or failure.key
            print(f"  {st.warn(where)}  {failure.message}")


def cmd_push(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = remote.push_all(config=config)
    except (FileNotFoundError, ValueError, remote.RemoteError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK if outcome.ok else EXIT_ERROR
    _print_sync_outcome("pushed", outcome)
    return EXIT_OK if outcome.ok else EXIT_ERROR


def cmd_pull(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = remote.pull_all(config=config)
    except (FileNotFoundError, ValueError, remote.RemoteError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK if outcome.ok else EXIT_ERROR
    _print_sync_outcome("pulled into archive", outcome)
    return EXIT_OK if outcome.ok else EXIT_ERROR


def cmd_adopt(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        entry = api.adopt_archive(args.archive_key, args.path, config=config)
    except (FileNotFoundError, FileExistsError, NotADirectoryError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(entry)
        return EXIT_OK
    print(
        f"{st.ok_mark()} adopted {st.muted(args.archive_key)} "
        f"{st.arrow()} {st.path(entry.meta.abspath)}"
    )
    print(_kv("snapshots", st.numeric(str(len(entry.snapshots)))))
    return EXIT_OK


def cmd_gc(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        if args.all:
            result = api.gc(
                all_dirs=True,
                dry_run=args.dry_run,
                rebuild_index=args.rebuild_index,
                config=config,
            )
        else:
            path = resolve_path(args.path or ".")
            result = api.gc(
                path,
                dry_run=args.dry_run,
                rebuild_index=args.rebuild_index,
                config=config,
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


def _print_check_result(result) -> None:
    status = "ok" if result.ok else "issues found"
    print(
        f"{st.ok_mark() if result.ok else st.warn('!')} "
        f"check {status}  {st.muted(f'({result.dirs_scanned} dir(s) scanned)')}"
    )
    if result.fixed_count:
        print(_kv("fixed", st.numeric(str(result.fixed_count))))
    if not result.issues:
        return
    for issue in result.issues:
        color = st.error if issue.severity == "error" else st.warn
        fixed = f" {st.success('(fixed)')}" if issue.fixed else ""
        where = f"  {st.muted(issue.path)}" if issue.path else ""
        snap = f"  {st.name(issue.snapshot)}" if issue.snapshot else ""
        print(
            f"  {color(issue.severity.upper())} "
            f"{issue.code}{fixed}{snap}{where}"
        )
        print(f"    {issue.message}")


def cmd_check(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        result = api.check(
            None if args.all else resolve_path(args.path or "."),
            all_dirs=args.all,
            deep=args.deep,
            fix=args.fix,
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK if result.ok else EXIT_ERROR
    _print_check_result(result)
    return EXIT_OK if result.ok else EXIT_ERROR


def cmd_migrate(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = api.migrate(
            None if args.all else resolve_path(args.path or "."),
            all_dirs=args.all,
            to=args.to,
            dry_run=args.dry_run,
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    verb = "would migrate" if outcome.dry_run else "migrated"
    print(
        f"{st.ok_mark()} {verb} "
        f"{st.numeric(f'{outcome.blobs_migrated:,}')} blob(s)  "
        f"{st.muted(format_size(outcome.bytes_migrated))}"
    )
    if outcome.blobs_skipped:
        print(_kv("skipped", st.numeric(f"{outcome.blobs_skipped:,}")))
    print(_kv("dirs", st.numeric(str(outcome.dirs_scanned))))
    return EXIT_OK


def cmd_init(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = api.init_source(args.path or ".", config=config, force=args.force)
    except (NotADirectoryError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    action = "created" if outcome.created else "exists"
    print(
        f"{st.ok_mark()} {action} {st.path(str(outcome.marker_path))}"
    )
    print(_kv("source", st.path(str(outcome.source))))
    print(_kv("id", st.muted(outcome.marker_id)))
    return EXIT_OK


def _print_auto_relocate_outcome(outcome) -> None:
    verb = "would relocate" if outcome.dry_run else "relocated"
    if not outcome.relocated and not outcome.skipped:
        print(st.muted("(no relocation candidates found)"))
        return
    if outcome.relocated:
        print(
            f"{st.ok_mark()} {verb} "
            f"{st.numeric(f'{len(outcome.relocated):,}')} source(s)"
        )
        for item in outcome.relocated:
            print(
                f"  {st.path(str(item.old_path))} "
                f"{st.arrow()} {st.path(str(item.new_path))}  "
                f"{st.muted('(' + item.method + ')')}"
            )
    if outcome.skipped:
        print(_kv("skipped", st.warn(f"{len(outcome.skipped):,}")))
        for item in outcome.skipped:
            print(
                f"  {st.warn(item.reason)}  "
                f"{st.path(str(item.old_path))}"
            )
            for cand in item.candidates[:3]:
                print(
                    f"    {st.muted('-')} {st.path(str(cand.new_path))}  "
                    f"{st.muted('(' + cand.method + ')')}"
                )


def cmd_relocate(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.auto:
        roots = args.paths or ["."]
        if _wants_json(args):
            if not args.dry_run and not args.yes:
                try:
                    outcome = api.auto_relocate_sources(
                        roots, config=config, dry_run=True,
                    )
                except NotADirectoryError as exc:
                    _emit_json({"relocated": False, "reason": str(exc)})
                    return EXIT_ERROR
                _emit_json({
                    "relocated": False,
                    "reason": "needs-confirmation",
                    "outcome": outcome,
                })
                return EXIT_ERROR
            try:
                outcome = api.auto_relocate_sources(
                    roots, config=config, dry_run=args.dry_run,
                )
            except NotADirectoryError as exc:
                _emit_json({"relocated": False, "reason": str(exc)})
                return EXIT_ERROR
            _emit_json(outcome)
            return EXIT_OK

        try:
            plan = api.auto_relocate_sources(roots, config=config, dry_run=True)
        except NotADirectoryError as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        _print_auto_relocate_outcome(plan)
        if args.dry_run or not plan.relocated:
            return EXIT_OK
        if not args.yes and not _confirm(
            f"relocate {len(plan.relocated)} source(s)?",
            default_yes=False,
        ):
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT
        outcome = api.auto_relocate_sources(roots, config=config, dry_run=False)
        _print_auto_relocate_outcome(outcome)
        return EXIT_OK

    if len(args.paths) != 2:
        _print_error("relocate requires OLD NEW, or use --auto ROOT...")
        return EXIT_ERROR
    old, new = args.paths
    try:
        entry = api.relocate_source(old, new, config=config)
    except (FileNotFoundError, NotADirectoryError, FileExistsError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(entry)
        return EXIT_OK
    print(
        f"{st.ok_mark()} relocated {st.path(old)} "
        f"{st.arrow()} {st.path(entry.meta.abspath)}"
    )
    print(_kv("key", st.muted(entry.key)))
    print(_kv("snapshots", st.numeric(str(len(entry.snapshots)))))
    return EXIT_OK


def cmd_protect(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_show",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR
    try:
        meta = (
            api.unprotect(path, name, config=config)
            if args.command == "unprotect"
            else api.protect(path, name, config=config)
        )
    except FileNotFoundError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(meta)
        return EXIT_OK
    state = "protected" if meta.protected else "unprotected"
    print(f"{st.ok_mark()} {state} {st.name(meta.name)}")
    return EXIT_OK


def _print_archive_table(entries) -> None:
    if not entries:
        print(st.muted("(no archived sources)"))
        return
    key_w = max(3, *(len(e.key) for e in entries))
    path_w = max(4, *(len(e.meta.abspath) for e in entries))
    print(st.dim(
        f"  {'KEY'.ljust(key_w)}  {'SNAPS'.rjust(5)}  "
        f"{'REASON'.ljust(16)}  {'SOURCE'.ljust(path_w)}"
    ))
    for entry in entries:
        print(
            f"  {st.muted(entry.key.ljust(key_w))}  "
            f"{st.numeric(str(len(entry.snapshots)).rjust(5))}  "
            f"{st.warn((entry.archive_reason or 'archived').ljust(16))}  "
            f"{st.path(entry.meta.abspath)}"
        )


def _resolve_archive_entry(
    raw: Optional[str], config: RuntimeConfig,
) -> Optional[api.DirEntry]:
    entries = api.list_archives(config=config)
    if raw:
        for entry in entries:
            if raw == entry.key or raw == entry.meta.abspath:
                return entry
        maybe_path = str(resolve_path(raw))
        for entry in entries:
            if maybe_path == entry.meta.abspath:
                return entry
        _print_error(f"no archived source matches: {raw}")
        return None
    if not _stdout_is_tty():
        _print_error("archive key/source is required in non-interactive mode")
        return None
    from snapz import tui
    key = tui.run_archive_picker(entries, title="Archived sources")
    if key is None:
        return None
    return next((entry for entry in entries if entry.key == key), None)


def cmd_archive(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.archive_op == "list":
        entries = api.list_archives(config=config)
        if _wants_json(args):
            _emit_json({"archives": entries})
            return EXIT_OK
        _print_archive_table(entries)
        return EXIT_OK

    if args.archive_op != "restore":
        _print_error(f"unknown archive operation: {args.archive_op}")
        return EXIT_ERROR

    entry = _resolve_archive_entry(args.archive, config)
    if entry is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    name = args.name
    if not name:
        if not _stdout_is_tty():
            _print_error("snapshot name is required in non-interactive mode")
            return EXIT_ERROR
        from snapz import tui
        name = tui.run_snapshot_picker(
            entry.snapshots,
            title=f"Archived snapshots · {entry.meta.abspath}",
        )
        if not name:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT

    dst = args.dst
    if not dst:
        if not _stdout_is_tty():
            _print_error("destination path is required in non-interactive mode")
            return EXIT_ERROR
        from snapz import tui
        dst = tui.prompt_text("restore archived snapshot to:", initial=entry.meta.abspath)
        if not dst:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT

    try:
        outcome = api.restore_archive(
            entry.key,
            name,
            dst,
            config=config,
            overwrite=args.overwrite,
        )
    except (FileNotFoundError, NotADirectoryError, FileExistsError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json({"archive": entry, "outcome": outcome})
        return EXIT_OK
    print(
        f"{st.ok_mark()} restored archived {st.name(outcome.snapshot.name)} "
        f"{st.arrow()} {st.path(str(outcome.destination))}"
    )
    print(_kv("source", st.muted(entry.meta.abspath)))
    print(_kv("extracted", st.numeric(str(outcome.extracted_count))))
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
        f"{st.numeric(format_size(meta.size_bytes))}  "
        f"{st.muted(t('kv.full_size') + ':')}  "
        f"{st.numeric(format_size(meta.total_bytes_in))}  "
        f"{st.muted(f'({ratio:.1f}\u00d7 dedup)')}",
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
            keep_tag=args.keep_tag or (),
            protect=args.protect or (),
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    drop_names = [s.name for s in plan.drop]
    if not plan.keep and not plan.drop:
        if _wants_json(args):
            _emit_json({"plan": plan, "outcome": None})
            return EXIT_OK
        print(st.muted(t('status.no_snapshots_dir')))
        return EXIT_OK

    if _wants_json(args):
        if args.dry_run:
            _emit_json({"plan": plan, "outcome": None})
            return EXIT_OK
        if not args.yes:
            _emit_json({
                "pruned": False,
                "reason": "needs-confirmation",
                "plan": plan,
            })
            return EXIT_ERROR
        outcome = api.execute_prune(
            plan,
            drop_names=drop_names,
            run_gc=not args.no_gc,
            dry_run=False,
            config=config,
        )
        _emit_json({"pruned": True, "plan": plan, "outcome": outcome})
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
        if args.text or _wants_json(args) or not _stdout_is_tty():
            _print_error(t('picker.no_paths_given'))
            return EXIT_ERROR
        from snapz import tui
        paths = tui.run_revert_picker(manifest.entries, src)
        if not paths:
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT

    if _wants_json(args):
        if not args.yes:
            _emit_json({
                "reverted": False,
                "reason": "needs-confirmation",
                "snapshot": snap_meta,
                "paths": paths,
            })
            return EXIT_ERROR
        try:
            outcome = api.revert(
                src,
                name,
                paths,
                config=config,
                auto_save=not args.no_auto_save,
                delete_extras=args.delete_extras,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            _emit_json({"reverted": False, "reason": str(exc)})
            return EXIT_ERROR
        _emit_json({"reverted": True, "outcome": outcome})
        return EXIT_OK

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

    try:
        outcome = api.revert(
            src,
            name,
            paths,
            config=config,
            auto_save=not args.no_auto_save,
            delete_extras=args.delete_extras,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
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
# Cat / browse (P5)
# ---------------------------------------------------------------------------


def cmd_cat(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_cat",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    relpath = (args.relpath or "").strip().strip("/")
    if not relpath:
        _print_error(t("cat.no_path_given"))
        return EXIT_ERROR

    try:
        data = api.read_snapshot_bytes(src, name, relpath, config=config)
    except (FileNotFoundError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if data is None:
        _print_error(t("cat.no_such_path", path=relpath, snap=name))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json({
            "snapshot": name,
            "path": relpath,
            "bytes": len(data),
            "binary": _looks_binary(data),
        })
        return EXIT_OK

    raw = bool(getattr(args, "raw", False))
    binary_ok = bool(getattr(args, "binary_ok", False))
    stdout_tty = sys.stdout.isatty()
    if raw:
        sys.stdout.buffer.write(data)
        return EXIT_OK

    if _looks_binary(data):
        if not stdout_tty and binary_ok:
            sys.stdout.buffer.write(data)
            return EXIT_OK
        print(t("cat.binary_placeholder", size=format_size(len(data))))
        return EXIT_OK

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    sys.stdout.write(text)
    return EXIT_OK


def cmd_browse(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_browse",
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    try:
        _abspath, meta, manifest = api._load_manifest_or_raise(  # noqa: SLF001
            src, name, config=config,
        )
    except (FileNotFoundError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json({
            "snapshot": meta.name,
            "path": str(src),
            "entries": [e.to_dict() for e in manifest.entries],
        })
        return EXIT_OK

    if not _stdout_is_tty():
        for entry in sorted(manifest.entries, key=lambda e: e.path.casefold()):
            suffix = "/" if entry.type == "dir" else ""
            print(entry.path + suffix)
        return EXIT_OK

    from snapz import tui

    def _preview(relpath: str) -> Optional[bytes]:
        return api.read_snapshot_bytes(src, name, relpath, config=config)

    action = tui.browse_manifest(
        manifest.entries,
        title=t("browse.title", name=meta.name),
        src=src,
        mode="view",
        preview=_preview,
        initial_filter=getattr(args, "filter", "") or "",
    )
    if action.kind == "file":
        return cmd_cat(
            argparse.Namespace(
                name=name,
                relpath=action.path,
                path=str(src),
                raw=False,
                binary_ok=False,
                json=False,
            ),
            config,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# tag — user-defined labels (P3)
# ---------------------------------------------------------------------------


def cmd_tag(args: argparse.Namespace, config: RuntimeConfig) -> int:
    action = getattr(args, "tag_action", None)
    path = resolve_path(getattr(args, "path", None) or ".")

    if action in ("add", "rm"):
        name = getattr(args, "name", None)
        if not name:
            _print_error(t('tag.missing_name'))
            return EXIT_ERROR
        tags = [t_.strip() for t_ in (getattr(args, "tags", None) or []) if t_.strip()]
        if not tags:
            _print_error(t('tag.missing_tags'))
            return EXIT_ERROR
        try:
            if action == "add":
                meta = api.tag_add(path, name, tags, config=config)
            else:
                meta = api.tag_remove(path, name, tags, config=config)
        except (FileNotFoundError, ValueError) as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        if _wants_json(args):
            _emit_json({"snapshot": meta.name, "tags": meta.tags})
            return EXIT_OK
        label = t('tag.added') if action == "add" else t('tag.removed')
        tag_str = ",".join(meta.tags) if meta.tags else "-"
        print(f"{st.ok_mark()} {label}: {st.name(meta.name)}  [{st.muted(tag_str)}]")
        return EXIT_OK

    # list
    groups = api.list_tags(path, config=config)
    if _wants_json(args):
        _emit_json({
            "path": str(path),
            "tags": {
                tag: [snap.name for snap in snaps]
                for tag, snaps in sorted(groups.items())
            },
        })
        return EXIT_OK

    if not groups:
        print(st.muted(t('tag.empty')))
        return EXIT_OK
    for tag in sorted(groups):
        names = [s.name for s in groups[tag]]
        print(f"{st.name(tag)}  {st.muted('(' + str(len(names)) + ')')}")
        for n in names:
            print(f"  {st.muted('·')} {n}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# log — audit trail of destructive operations (P1)
# ---------------------------------------------------------------------------


_LOG_EXTRA_ORDER = (
    "previous", "pre_restore", "pre_revert",
    "extracted", "cleaned", "reverted", "deleted",
    "file_count", "size_bytes", "bytes_freed", "blobs_removed",
    "remaining", "snapshot_count", "previous_key", "paths", "note",
)


def _format_log_extras(event: "events.Event") -> str:
    parts: list[str] = []
    for key in _LOG_EXTRA_ORDER:
        if key in event.extra and event.extra[key] is not None:
            value = event.extra[key]
            if isinstance(value, list):
                if not value:
                    continue
                rendered = ",".join(str(v) for v in value[:4])
                if len(value) > 4:
                    rendered += f",+{len(value) - 4}"
                parts.append(f"{key}={rendered}")
            else:
                parts.append(f"{key}={value}")
    # Include any remaining unknown extras for forward-compat.
    for key, value in event.extra.items():
        if key in _LOG_EXTRA_ORDER or value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _print_log_text(rows: list["events.Event"], *, show_source: bool) -> None:
    if not rows:
        print(st.muted(t('log.empty')))
        return
    for ev in rows:
        ts = format_iso(ev.ts)
        kind = st.bold(ev.kind.ljust(9))
        snap = ev.snapshot or ""
        snap_cell = st.name(snap) if snap else st.muted("-")
        extras = _format_log_extras(ev)
        extras_cell = (" " + st.muted(extras)) if extras else ""
        line = f"{st.muted(ts)}  {kind}  {snap_cell}{extras_cell}"
        if show_source and ev.source:
            line += "  " + st.muted(f"({ev.source})")
        print(line)


def cmd_log(args: argparse.Namespace, config: RuntimeConfig) -> int:
    kinds: Optional[list[str]] = None
    raw_kinds = getattr(args, "kind", None)
    if raw_kinds:
        kinds = [chunk.strip() for chunk in raw_kinds.split(",") if chunk.strip()]
    limit = getattr(args, "limit", None)

    if getattr(args, "all", False):
        rows = events.load_all(config, kinds=kinds, limit=limit)
        show_source = True
    else:
        src = resolve_path(args.path or ".")
        folder = Store(config).dir_for(src)
        rows = events.load_for(folder, kinds=kinds, limit=limit)
        show_source = False

    if _wants_json(args):
        _emit_json({"events": [e.to_dict() for e in rows]})
        return EXIT_OK

    _print_log_text(rows, show_source=show_source)
    return EXIT_OK


def cmd_update(args: argparse.Namespace, config: RuntimeConfig) -> int:
    target = getattr(args, "target", None) or SNAPZ_GITHUB_INSTALL_TARGET
    pip_args = ["install", "--upgrade", target]
    print(f"{st.muted(t('update.source'))} {st.path(target)}")
    result = _run_pip(pip_args)
    if _wants_json(args):
        _emit_json({
            "updated": result.returncode == 0,
            "target": target,
            "command": [sys.executable, "-m", "pip", *pip_args],
            "returncode": result.returncode,
        })
        return EXIT_OK if result.returncode == 0 else EXIT_ERROR
    if result.returncode != 0:
        _print_error(t("update.failed", code=result.returncode))
        return EXIT_ERROR
    print(f"{st.ok_mark()} {t('update.done')}")
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace, config: RuntimeConfig) -> int:
    data_root = Path(config.root).expanduser()
    data_bytes = _path_total_bytes(data_root)
    delete_data = bool(getattr(args, "purge_data", False))

    if _wants_json(args):
        if not args.yes:
            _emit_json({
                "uninstalled": False,
                "reason": "needs-confirmation",
                "package": SNAPZ_PACKAGE_NAME,
                "data_root": data_root,
                "data_bytes": data_bytes,
                "data_size": _format_data_size(data_bytes),
            })
            return EXIT_ERROR
    else:
        print(st.bold(t("uninstall.heading")))
        print(_kv(t("kv.package"), st.name(SNAPZ_PACKAGE_NAME)))
        print(_kv(t("kv.data_root"), st.path(str(data_root))))
        print(_kv(t("kv.data_size"), st.numeric(_format_data_size(data_bytes))))
        if data_root.exists():
            if args.yes:
                delete_data = bool(getattr(args, "purge_data", False))
            else:
                delete_data = _confirm(
                    t("uninstall.delete_data"),
                    default_yes=False,
                )
        else:
            print(st.muted(t("uninstall.data_missing")))
            delete_data = False
        if not args.yes and not _confirm(
            t("uninstall.confirm_package"),
            default_yes=False,
        ):
            print(st.muted(t("status.aborted")))
            return EXIT_USER_ABORT

    pip_args = ["uninstall", "-y", SNAPZ_PACKAGE_NAME]
    result = _run_pip(pip_args)
    deleted_data = False
    data_error = ""
    if result.returncode == 0 and delete_data:
        try:
            deleted_data = _delete_data_root(data_root)
        except (OSError, ValueError) as exc:
            data_error = str(exc)
    if _wants_json(args):
        _emit_json({
            "uninstalled": result.returncode == 0,
            "package": SNAPZ_PACKAGE_NAME,
            "data_root": data_root,
            "data_bytes": data_bytes,
            "deleted_data": deleted_data,
            "data_error": data_error,
            "command": [sys.executable, "-m", "pip", *pip_args],
            "returncode": result.returncode,
        })
        return EXIT_OK if result.returncode == 0 and not data_error else EXIT_ERROR
    if result.returncode != 0:
        _print_error(t("uninstall.failed", code=result.returncode))
        return EXIT_ERROR
    if data_error:
        _print_error(data_error)
        return EXIT_ERROR
    if deleted_data:
        print(f"{st.ok_mark()} {t('uninstall.data_deleted', path=st.path(str(data_root)))}")
    print(f"{st.ok_mark()} {t('uninstall.done')}")
    return EXIT_OK


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
    parser.add_argument(
        "--minimal",
        action="store_true",
        help=t("flag.minimal"),
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
        "--no-cache",
        action="store_true",
        help=t("save.no_cache"),
    )
    p_save.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help=t("save.workers"),
    )
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
    p_list.add_argument(
        "--timeline",
        action="store_true",
        help=t("list.timeline"),
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

    # portable source bundle
    p_bundle = sub.add_parser(
        "bundle",
        help=t("bundle.help"),
    )
    p_bundle.add_argument("source", help=t("bundle.source"))
    p_bundle.add_argument("dst", help=t("bundle.dst"))
    p_bundle.add_argument(
        "--archive",
        action="store_true",
        help=t("bundle.archive"),
    )
    p_bundle.add_argument(
        "--overwrite",
        action="store_true",
        help=t("bundle.overwrite"),
    )
    p_bundle.set_defaults(func=cmd_bundle)

    # portable source import
    p_import = sub.add_parser(
        "import",
        help=t("import.help"),
    )
    p_import.add_argument("bundle", help=t("import.bundle"))
    p_import.add_argument(
        "--path",
        help=t("import.path"),
    )
    p_import.add_argument(
        "--overwrite",
        action="store_true",
        help=t("import.overwrite"),
    )
    p_import.set_defaults(func=cmd_import)

    # remote login/logout
    p_login = sub.add_parser("login", help="log in to a snapz-server remote")
    p_login.add_argument("server", help="server URL, e.g. http://127.0.0.1:8765")
    p_login.add_argument("--tenant", help="tenant name")
    p_login.add_argument("--username", help="username")
    p_login.add_argument("--password", help="password (prompts when omitted)")
    p_login.add_argument("--device", help="device name recorded on the server")
    p_login.add_argument(
        "--tls-ca",
        help="CA bundle for verifying the HTTPS server certificate",
    )
    p_login.add_argument(
        "--tls-client-cert",
        help="PEM client certificate for mTLS",
    )
    p_login.add_argument(
        "--tls-client-key",
        help="PEM private key for the mTLS client certificate",
    )
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="remove the saved remote token")
    p_logout.set_defaults(func=cmd_logout)

    # remote sync
    p_push = sub.add_parser("push", help="push snapshots to the configured remote")
    p_push.add_argument("scope", choices=["all"], help="push all local sources")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help="pull snapshots from the configured remote")
    p_pull.add_argument("scope", choices=["all"], help="pull all remote sources")
    p_pull.set_defaults(func=cmd_pull)

    p_adopt = sub.add_parser(
        "adopt",
        help="bind an archived source, such as a pulled remote archive, to a path",
    )
    p_adopt.add_argument("archive_key")
    p_adopt.add_argument("path")
    p_adopt.set_defaults(func=cmd_adopt)

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
    p_gc.add_argument(
        "--rebuild-index", action="store_true",
        help=t("gc.rebuild_index"),
    )
    p_gc.set_defaults(func=cmd_gc)

    # check
    p_check = sub.add_parser(
        "check",
        help="validate store metadata and blob reachability",
    )
    p_check.add_argument("path", nargs="?")
    p_check.add_argument("--all", action="store_true")
    p_check.add_argument("--deep", action="store_true")
    p_check.add_argument("--fix", action="store_true")
    p_check.set_defaults(func=cmd_check)

    # migrate
    p_migrate = sub.add_parser(
        "migrate",
        help="migrate legacy per-directory blobs to the v3 global CAS pool",
    )
    p_migrate.add_argument("path", nargs="?")
    p_migrate.add_argument("--all", action="store_true")
    p_migrate.add_argument("--to", default="v3", choices=["v3"])
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.set_defaults(func=cmd_migrate)

    # init source marker
    p_init = sub.add_parser(
        "init",
        help="write a .snapz-id marker for reliable move detection",
    )
    p_init.add_argument("path", nargs="?", help="source directory (default: cwd)")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="replace an existing .snapz-id marker",
    )
    p_init.set_defaults(func=cmd_init)

    # Compatibility alias for the user's requested spelling.
    p_initd = sub.add_parser("initd", help=argparse.SUPPRESS)
    p_initd.add_argument("path", nargs="?")
    p_initd.add_argument("--force", action="store_true")
    p_initd.set_defaults(func=cmd_init)

    # relocate source binding after a directory rename
    p_relocate = sub.add_parser(
        "relocate",
        help="move snapshots from an old source path to a renamed live directory",
    )
    p_relocate.add_argument(
        "paths",
        nargs="*",
        help="OLD NEW for manual relocation, or ROOT... with --auto",
    )
    p_relocate.add_argument(
        "--auto",
        action="store_true",
        help="scan roots for moved archived sources and relocate exact matches",
    )
    p_relocate.add_argument(
        "--dry-run",
        action="store_true",
        help="show automatic matches without moving store bindings",
    )
    p_relocate.add_argument(
        "-y", "--yes",
        action="store_true",
        help="apply automatic relocation without prompting",
    )
    p_relocate.set_defaults(func=cmd_relocate)

    # archive sources whose original directory is missing or recreated
    p_archive = sub.add_parser(
        "archive",
        help="list archived sources and restore archived snapshots",
    )
    archive_sub = p_archive.add_subparsers(dest="archive_op")
    p_archive_list = archive_sub.add_parser("list", help="list archived sources")
    p_archive_list.set_defaults(func=cmd_archive)
    p_archive_restore = archive_sub.add_parser(
        "restore",
        help="restore an archived snapshot to a destination path",
    )
    p_archive_restore.add_argument(
        "archive",
        nargs="?",
        help="archive key or original source path",
    )
    p_archive_restore.add_argument("name", nargs="?", help="snapshot name")
    p_archive_restore.add_argument("dst", nargs="?", help="destination path")
    p_archive_restore.add_argument(
        "--overwrite",
        action="store_true",
        help="extract even if the destination is non-empty",
    )
    p_archive_restore.set_defaults(func=cmd_archive)

    # protect / unprotect
    p_protect = sub.add_parser("protect", help="mark a snapshot as protected")
    p_protect.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_protect.add_argument("--path", help="target directory (default: cwd)")
    p_protect.set_defaults(func=cmd_protect)

    p_unprotect = sub.add_parser("unprotect", help="remove snapshot protection")
    p_unprotect.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_unprotect.add_argument("--path", help="target directory (default: cwd)")
    p_unprotect.set_defaults(func=cmd_protect)

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
        "--keep-tag", action="append", metavar="TAG", default=None,
        help=t("prune.keep_tag"),
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

    # cat — print one file from a snapshot
    p_cat = sub.add_parser("cat", help=t("cat.help"))
    p_cat.add_argument("name", nargs="?", help=t("cat.snapshot")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_cat.add_argument("relpath", nargs="?", help=t("cat.relpath"))
    p_cat.add_argument("--path", help=t("cat.path"))
    p_cat.add_argument(
        "--raw",
        action="store_true",
        help=t("cat.raw"),
    )
    p_cat.add_argument(
        "--binary-ok",
        action="store_true",
        help=t("cat.binary_ok"),
    )
    p_cat.set_defaults(func=cmd_cat)

    # browse — interactive manifest browser
    p_browse = sub.add_parser("browse", help=t("browse.help"))
    p_browse.add_argument("name", nargs="?", help=t("browse.snapshot")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_browse.add_argument("--path", help=t("browse.path"))
    p_browse.add_argument(
        "--filter",
        default="",
        help=t("browse.filter"),
    )
    p_browse.set_defaults(func=cmd_browse)

    # tag — user-defined labels
    p_tag = sub.add_parser("tag", help=t("tag.help"))
    tag_sub = p_tag.add_subparsers(dest="tag_action")
    p_tag_add = tag_sub.add_parser("add", help=t("tag.add_help"))
    p_tag_add.add_argument("name", help=t("tag.snapshot"))
    p_tag_add.add_argument("tags", nargs="+", help=t("tag.values"))
    p_tag_add.add_argument("--path", help=t("tag.path"))
    p_tag_add.set_defaults(func=cmd_tag, tag_action="add")
    p_tag_rm = tag_sub.add_parser("rm", help=t("tag.rm_help"))
    p_tag_rm.add_argument("name", help=t("tag.snapshot"))
    p_tag_rm.add_argument("tags", nargs="+", help=t("tag.values"))
    p_tag_rm.add_argument("--path", help=t("tag.path"))
    p_tag_rm.set_defaults(func=cmd_tag, tag_action="rm")
    p_tag_list = tag_sub.add_parser("list", help=t("tag.list_help"))
    p_tag_list.add_argument("--path", help=t("tag.path"))
    p_tag_list.set_defaults(func=cmd_tag, tag_action="list")
    # Default when `snapz tag` is invoked without a subcommand → list.
    p_tag.set_defaults(func=cmd_tag, tag_action="list")

    # log — operation history
    p_log = sub.add_parser("log", help=t("log.help"))
    p_log.add_argument("--path", help=t("log.path"))
    p_log.add_argument(
        "--all", action="store_true",
        help=t("log.all"),
    )
    p_log.add_argument(
        "-n", "--limit", type=int, default=None,
        help=t("log.limit"),
    )
    p_log.add_argument(
        "--kind", default=None,
        help=t("log.kind"),
    )
    p_log.set_defaults(func=cmd_log)

    # self-management
    p_update = sub.add_parser("update", help=t("update.help"))
    p_update.add_argument(
        "--target",
        default=SNAPZ_GITHUB_INSTALL_TARGET,
        help=argparse.SUPPRESS,
    )
    p_update.set_defaults(func=cmd_update)

    p_uninstall = sub.add_parser("uninstall", help=t("uninstall.help"))
    p_uninstall.add_argument(
        "-y", "--yes",
        action="store_true",
        help=t("uninstall.yes"),
    )
    p_uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help=t("uninstall.purge_data"),
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

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
        "gc", "check", "migrate", "init", "initd", "protect", "unprotect",
        "relocate", "archive",
        "export", "bundle", "import", "config", "diff",
        "login", "logout", "push", "pull", "adopt",
        "stats", "prune", "revert",
        "undo", "find", "cat", "browse", "log", "tag",
        "update", "uninstall",
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
            no_cache=False,
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
    try:
        st.configure(str(preferences.get_config_value(Path(config.root), "color")))
    except (KeyError, ValueError):
        st.configure("auto")
    if getattr(args, "no_zstd", False) or no_zstd_requested:
        config = RuntimeConfig(
            root=config.root,
            large_file_bytes=config.large_file_bytes,
            follow_symlinks=config.follow_symlinks,
            use_zstd=False,
            apply_default_ignores=config.apply_default_ignores,
            apply_gitignore=config.apply_gitignore,
            apply_snapzignore=config.apply_snapzignore,
            use_file_cache=config.use_file_cache,
            save_workers=config.save_workers,
        )

    workers = getattr(args, "workers", None)
    if workers is not None:
        if workers < 1:
            _print_error(t("save.workers_positive"))
            return EXIT_ERROR
        config = RuntimeConfig(
            root=config.root,
            large_file_bytes=config.large_file_bytes,
            follow_symlinks=config.follow_symlinks,
            use_zstd=config.use_zstd,
            apply_default_ignores=config.apply_default_ignores,
            apply_gitignore=config.apply_gitignore,
            apply_snapzignore=config.apply_snapzignore,
            use_file_cache=config.use_file_cache,
            save_workers=workers,
        )

    if json_requested:
        args.json = True

    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK

    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
