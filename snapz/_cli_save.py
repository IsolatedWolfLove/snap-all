"""Interactive and scripted save commands."""

from __future__ import annotations

from snapz._cli_common import *
from snapz._cli_list import _print_snapshot_table
from dataclasses import dataclass

@dataclass
class _ExcludeSuggestion:
    pattern: str
    bytes_total: int
    file_count: int

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
    dedup_text = f"({ratio:.1f}\u00d7 dedup)"
    print(f"{st.ok_mark()} {t('msg.saved')} {st.name(snapz.name)}")
    if snapz.note:
        print(_kv(t('kv.note'), st.muted(snapz.note)))
    print(_kv(t('kv.archive'), st.path(str(outcome.pack_result.archive_path))))
    print(_kv(
        t('kv.size'),
        f"{st.numeric(format_size(snapz.size_bytes))}  "
        f"{st.muted(t('kv.full_size') + ':')}  "
        f"{st.numeric(format_size(snapz.total_bytes_in))}"
        f"  {st.muted(dedup_text)}",
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
