"""Path-oriented commands: revert, undo, find, cat, browse, and tags."""

from __future__ import annotations

from snapz._cli_common import *
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
        show_auto=bool(getattr(args, "all", False)),
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

    revert_icon = "\u21a9"
    print(
        f"{st.bold(revert_icon)} {t('verb.revert_imp')} {st.name(snap_meta.name)} "
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
    undo_icon = "\u21a9"
    print(
        f"{st.bold(undo_icon)} {t('undo.heading', name=st.name(target.name))}"
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

def cmd_cat(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_cat",
        show_auto=bool(getattr(args, "all", False)),
    )
    if name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    relpath = (args.relpath or "").strip().strip("/")
    if not relpath:
        if _wants_json(args) or getattr(args, "raw", False) or not _stdout_is_tty():
            _print_error(t("cat.no_path_given"))
            return EXIT_ERROR
        try:
            _abspath, meta, manifest = api._load_manifest_or_raise(  # noqa: SLF001
                src, name, config=config,
            )
        except (FileNotFoundError, ValueError) as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        from snapz import tui
        action = tui.browse_manifest(
            manifest.entries,
            title=t("browse.title", name=meta.name),
            src=src,
            mode="view",
            preview=None,
            footer_hint=t("cat.browse_footer"),
        )
        if action.kind != "file":
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT
        relpath = action.path

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
        show_auto=bool(getattr(args, "all", False)),
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
                all=bool(getattr(args, "all", False)),
            ),
            config,
        )
    return EXIT_OK

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
