"""Operation log and self-management commands."""

from __future__ import annotations

import zipfile

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
    from snapz import self_update
    from snapz.i18n import get_lang

    release_url = getattr(args, "target", None) or self_update.GITHUB_RELEASE_API
    language = get_lang()
    if _wants_json(args):
        try:
            plan = self_update.plan_update(
                language=language,
                package=self_update.CLIENT_PACKAGE_NAME,
                release_url=release_url,
            )
            result = self_update.install_plan(plan)
        except Exception as exc:
            _emit_json({"updated": False, "error": str(exc)})
            return EXIT_ERROR
        _emit_json({
            "updated": result.ok,
            "target": result.plan.download_url,
            "asset": result.plan.asset_name,
            "tag": result.plan.tag,
            "language": result.plan.language,
            "command": result.command,
            "returncode": result.returncode,
        })
        return EXIT_OK if result.ok else EXIT_ERROR
    try:
        plan = self_update.plan_update(
            language=language,
            package=self_update.CLIENT_PACKAGE_NAME,
            release_url=release_url,
        )
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    print(f"{st.muted(t('update.source'))} {st.path(plan.download_url)}")
    try:
        result = self_update.install_plan(plan)
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if result.returncode != 0:
        _print_error(t("update.failed", code=result.returncode))
        return EXIT_ERROR
    print(f"{st.ok_mark()} {t('update.done')}")
    return EXIT_OK


def _uninstall_action() -> tuple[str, list[str], subprocess.CompletedProcess[str]]:
    from snapz import self_update

    if self_update.deb_package_installed(SNAPZ_PACKAGE_NAME):
        result = self_update.remove_deb_package(SNAPZ_PACKAGE_NAME)
        command = list(result.args) if isinstance(result.args, list) else []
        return "deb", command, result

    executable = _current_command_path()
    if executable is not None and _is_zipapp_executable(executable):
        command = ["rm", "-f", str(executable)]
        try:
            executable.unlink()
            return "zipapp", command, subprocess.CompletedProcess(command, 0)
        except OSError as exc:
            return (
                "zipapp",
                command,
                subprocess.CompletedProcess(command, 1, stderr=str(exc)),
            )

    pip_args = ["uninstall", "-y", SNAPZ_PACKAGE_NAME]
    result = _cli_facade()._run_pip(pip_args)
    return (
        "pip",
        [sys.executable, "-m", "pip", *pip_args],
        result,
    )


def _unregister_remote_device(config: RuntimeConfig) -> bool:
    unregistered = False
    try:
        unregistered = remote.unregister_device(config=config)
    except Exception:
        # Local uninstall/logout should continue even if the remote is gone.
        unregistered = False
    try:
        remote.config_path(config).unlink(missing_ok=True)
    except OSError:
        pass
    return unregistered


def _current_command_path() -> Path | None:
    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.is_absolute() or argv0.parent != Path("."):
        return argv0
    resolved = shutil.which(sys.argv[0])
    return Path(resolved) if resolved else None


def _is_zipapp_executable(path: Path) -> bool:
    name = path.name
    if not (
        name == "snapz"
        or (name.startswith("snapz") and name.endswith(".pyz"))
    ):
        return False
    try:
        return path.is_file() and zipfile.is_zipfile(path)
    except OSError:
        return False


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
                "install_type": "unknown",
                "data_root": data_root,
                "data_bytes": data_bytes,
                "data_size": _format_data_size(data_bytes),
            })
            return EXIT_ERROR
    else:
        print(st.bold(t("uninstall.heading")))
        print(_kv(t("kv.package"), st.name(SNAPZ_PACKAGE_NAME)))
        print(_kv(t("kv.command"), st.path(sys.argv[0])))
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

    remote_unregistered = _unregister_remote_device(config)
    install_type, command, result = _uninstall_action()
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
            "install_type": install_type,
            "data_root": data_root,
            "data_bytes": data_bytes,
            "deleted_data": deleted_data,
            "data_error": data_error,
            "remote_unregistered": remote_unregistered,
            "command": command,
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
