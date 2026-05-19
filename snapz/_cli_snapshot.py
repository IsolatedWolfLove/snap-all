"""Snapshot mutation, restore, show, and export commands."""

from __future__ import annotations

from snapz._cli_common import *
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
        show_auto=bool(getattr(args, "all", False)),
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
        show_auto=bool(getattr(args, "all", False)),
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
    restore_icon = "\u21a9"
    # Heading verb is bare (not "已 restored"); use the verb-form key so
    # ZH gets the imperative "还原" rather than the past-tense "已还原".
    print(
        f"{st.bold(restore_icon)} {t('verb.restore_imp')} {st.name(estimate.snapshot.name)} "
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

def cmd_export(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")
    dst = resolve_path(args.dst)
    name = _resolve_snapshot_name(
        src, args.name, config, title_key="picker.title_export",
        show_auto=bool(getattr(args, "all", False)),
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

def cmd_show(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_show",
        show_auto=bool(getattr(args, "all", False)),
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
    dedup_text = f"({ratio:.1f}\u00d7 dedup)"
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
        f"{st.muted(dedup_text)}",
    ))
    print(_kv(t('kv.files'), st.numeric(f'{meta.file_count:,}')))
    return EXIT_OK

def cmd_protect(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    name = _resolve_snapshot_name(
        path, args.name, config, title_key="picker.title_show",
        show_auto=bool(getattr(args, "all", False)),
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
