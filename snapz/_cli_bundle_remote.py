"""Portable bundle and remote sync commands."""

from __future__ import annotations

from snapz._cli_common import *
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
            password = None
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
