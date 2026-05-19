"""Operation log and self-management commands."""

from __future__ import annotations

from snapz._cli_common import *


def _cli_facade():
    import snapz.cli as cli

    return cli

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
    result = _cli_facade()._run_pip(pip_args)
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
    result = _cli_facade()._run_pip(pip_args)
    deleted_data = False
    data_error = ""
    if result.returncode == 0 and delete_data:
        try:
            deleted_data = _cli_facade()._delete_data_root(data_root)
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

