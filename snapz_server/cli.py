"""Command-line entry point for the standalone snapz server."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shlex
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Optional

from snapz import __version__
from snapz.i18n import install_argparse_i18n, t
from snapz.util import format_size
from snapz_server import db
from snapz_server.app import make_server
from snapz_server.compact import compact_pending
from snapz_server.server_config import resolve_compact_config

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_HOST = "127.0.0.1"
ALL_INTERFACES_HOST = ".".join(("0", "0", "0", "0"))
DEFAULT_PORT = 8765
DEFAULT_CONFIG_PATH = Path("/etc/default/snapz-server")
DEFAULT_SERVICE_FILE = Path("/etc/systemd/system/snapz-server.service")
DEFAULT_SERVICE_DATA_DIR = Path("/srv/snapz")
SNAPZ_PACKAGE_NAME = "snapz-server"


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _data_dir(args: argparse.Namespace) -> Path:
    return db.resolve_data_dir(getattr(args, "data", None))


def _config_path(args: argparse.Namespace) -> Path:
    raw = (
        getattr(args, "config", None)
        or getattr(args, "global_config", None)
        or os.environ.get("SNAPZ_SERVER_CONFIG")
        or DEFAULT_CONFIG_PATH
    )
    return Path(raw).expanduser()


def _service_file_path(args: argparse.Namespace) -> Path:
    raw = getattr(args, "service_file", None) or DEFAULT_SERVICE_FILE
    return Path(raw).expanduser()


def _parse_env_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"could not read config {path}: {exc}") from exc
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid config {path}:{lineno}: {exc}") from exc
        for token in tokens:
            if "=" not in token:
                raise ValueError(f"invalid config {path}:{lineno}: expected KEY=value")
            key, value = token.split("=", 1)
            if (
                key.startswith("SNAPZ_SERVER_")
                or key.startswith("SNAPZ_COMPACT_")
                or key in {"SNAPZ_PULL_TRANSFER_MODE", "SNAPZ_ENABLE_RAW_STREAM_PULL"}
            ):
                values[key] = value
    return values


def _load_config_env(path: Path) -> None:
    for key, value in _parse_env_config(path).items():
        os.environ.setdefault(key, value)


def _quote_env_value(value: str) -> str:
    return shlex.quote(value) if value else ""


def _server_bin(args: argparse.Namespace) -> str:
    value = getattr(args, "server_bin", None)
    if value:
        return str(value)
    found = shutil.which("snapz-server")
    if found:
        return found
    if sys.argv and sys.argv[0]:
        return sys.argv[0]
    return "/usr/bin/snapz-server"


def _write_text_file(path: Path, text: str, *, force: bool, mode: int = 0o644) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    return subprocess.run([systemctl, *args], check=False, text=True)  # nosec B603


def _run_host(args: argparse.Namespace) -> str:
    return str(
        getattr(args, "host", None)
        or os.environ.get("SNAPZ_SERVER_HOST")
        or DEFAULT_HOST
    )


def _run_port(args: argparse.Namespace) -> int:
    value = getattr(args, "port", None)
    if value is not None:
        return int(value)
    raw = os.environ.get("SNAPZ_SERVER_PORT") or str(DEFAULT_PORT)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"invalid SNAPZ_SERVER_PORT: {raw!r}") from exc


def _max_bundle_bytes(args: argparse.Namespace) -> int | None:
    value = getattr(args, "max_bundle_mb", None)
    if value is None:
        return None
    return int(value) * 1024 * 1024


def _init_data_dir(args: argparse.Namespace) -> Path:
    raw = (
        getattr(args, "data", None)
        or os.environ.get("SNAPZ_SERVER_DATA")
        or str(DEFAULT_SERVICE_DATA_DIR)
    )
    return Path(raw).expanduser().resolve()


def _init_config_values(args: argparse.Namespace) -> dict[str, str]:
    cors_origins = getattr(args, "cors_origin", None)
    cors_value = ",".join(cors_origins or [])
    admin_token = (
        getattr(args, "admin_token", None)
        or os.environ.get("SNAPZ_SERVER_ADMIN_TOKEN")
        or secrets.token_hex(32)
    )
    max_bundle_mb = getattr(args, "max_bundle_mb", None)
    return {
        "SNAPZ_SERVER_DATA": str(_init_data_dir(args)),
        "SNAPZ_SERVER_HOST": str(
            getattr(args, "host", None)
            or os.environ.get("SNAPZ_SERVER_HOST")
            or ALL_INTERFACES_HOST
        ),
        "SNAPZ_SERVER_PORT": str(
            getattr(args, "port", None)
            or os.environ.get("SNAPZ_SERVER_PORT")
            or DEFAULT_PORT
        ),
        "SNAPZ_SERVER_ADMIN_TOKEN": admin_token,
        "SNAPZ_SERVER_MAX_BUNDLE_MB": str(
            max_bundle_mb
            or os.environ.get("SNAPZ_SERVER_MAX_BUNDLE_MB")
            or 10 * 1024
        ),
        "SNAPZ_SERVER_CORS_ORIGIN": cors_value
        or os.environ.get("SNAPZ_SERVER_CORS_ORIGIN", ""),
        "SNAPZ_SERVER_TLS_CERT": str(
            getattr(args, "tls_cert", None)
            or os.environ.get("SNAPZ_SERVER_TLS_CERT", "")
        ),
        "SNAPZ_SERVER_TLS_KEY": str(
            getattr(args, "tls_key", None)
            or os.environ.get("SNAPZ_SERVER_TLS_KEY", "")
        ),
        "SNAPZ_SERVER_TLS_CLIENT_CA": str(
            getattr(args, "tls_client_ca", None)
            or os.environ.get("SNAPZ_SERVER_TLS_CLIENT_CA", "")
        ),
        "SNAPZ_SERVER_STORAGE": os.environ.get("SNAPZ_SERVER_STORAGE", "hot-cold"),
        "SNAPZ_COMPACT_ZSTD_LEVEL": os.environ.get("SNAPZ_COMPACT_ZSTD_LEVEL", "22"),
        "SNAPZ_COMPACT_MANIFEST_ZSTD_LEVEL": os.environ.get(
            "SNAPZ_COMPACT_MANIFEST_ZSTD_LEVEL",
            "22",
        ),
        "SNAPZ_COMPACT_CHUNK_FILE_BYTES": os.environ.get(
            "SNAPZ_COMPACT_CHUNK_FILE_BYTES",
            "1048576",
        ),
        "SNAPZ_COMPACT_CHUNK_MIN_BYTES": os.environ.get(
            "SNAPZ_COMPACT_CHUNK_MIN_BYTES",
            "262144",
        ),
        "SNAPZ_COMPACT_CHUNK_AVG_BYTES": os.environ.get(
            "SNAPZ_COMPACT_CHUNK_AVG_BYTES",
            "1048576",
        ),
        "SNAPZ_COMPACT_CHUNK_MAX_BYTES": os.environ.get(
            "SNAPZ_COMPACT_CHUNK_MAX_BYTES",
            "4194304",
        ),
        "SNAPZ_COMPACT_PACK_TARGET_BYTES": os.environ.get(
            "SNAPZ_COMPACT_PACK_TARGET_BYTES",
            "268435456",
        ),
        "SNAPZ_COMPACT_KEEP_INCOMING_DAYS": os.environ.get(
            "SNAPZ_COMPACT_KEEP_INCOMING_DAYS",
            "1",
        ),
        "SNAPZ_COMPACT_SCOPE": os.environ.get("SNAPZ_COMPACT_SCOPE", "tenant"),
        "SNAPZ_PULL_TRANSFER_MODE": os.environ.get("SNAPZ_PULL_TRANSFER_MODE", "cold"),
        "SNAPZ_ENABLE_RAW_STREAM_PULL": os.environ.get(
            "SNAPZ_ENABLE_RAW_STREAM_PULL",
            "false",
        ),
    }


def _effective_init_config_values(
    args: argparse.Namespace,
    config_path: Path,
    *,
    force: bool,
) -> dict[str, str]:
    values = _init_config_values(args)
    if config_path.exists() and not force:
        values.update(_parse_env_config(config_path))
    return values


def _config_text(values: dict[str, str]) -> str:
    lines = [
        "# snapz-server runtime configuration.",
        "#",
        "# This file is preserved by snapz-server update and Debian package upgrades.",
    ]
    for key in (
        "SNAPZ_SERVER_DATA",
        "SNAPZ_SERVER_HOST",
        "SNAPZ_SERVER_PORT",
        "SNAPZ_SERVER_ADMIN_TOKEN",
        "SNAPZ_SERVER_MAX_BUNDLE_MB",
        "SNAPZ_SERVER_CORS_ORIGIN",
        "SNAPZ_SERVER_TLS_CERT",
        "SNAPZ_SERVER_TLS_KEY",
        "SNAPZ_SERVER_TLS_CLIENT_CA",
        "SNAPZ_SERVER_STORAGE",
        "SNAPZ_COMPACT_ZSTD_LEVEL",
        "SNAPZ_COMPACT_MANIFEST_ZSTD_LEVEL",
        "SNAPZ_COMPACT_CHUNK_FILE_BYTES",
        "SNAPZ_COMPACT_CHUNK_MIN_BYTES",
        "SNAPZ_COMPACT_CHUNK_AVG_BYTES",
        "SNAPZ_COMPACT_CHUNK_MAX_BYTES",
        "SNAPZ_COMPACT_PACK_TARGET_BYTES",
        "SNAPZ_COMPACT_KEEP_INCOMING_DAYS",
        "SNAPZ_COMPACT_SCOPE",
        "SNAPZ_PULL_TRANSFER_MODE",
        "SNAPZ_ENABLE_RAW_STREAM_PULL",
    ):
        lines.append(f"{key}={_quote_env_value(values.get(key, ''))}")
    return "\n".join(lines) + "\n"


def _service_text(*, config_path: Path, server_bin: str) -> str:
    return f"""[Unit]
Description=snapz remote sync server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-{config_path}
ExecStart={server_bin} --config {config_path} run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _password_from_args(args: argparse.Namespace) -> str:
    password = getattr(args, "password", None)
    if password:
        return str(password)
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise ValueError("passwords do not match")
    if not first:
        raise ValueError("password cannot be empty")
    return first


def cmd_setup(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    db.init_db(root)
    print(f"initialized {root}")
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    config_path = _config_path(args)
    service_file = _service_file_path(args)
    force = bool(getattr(args, "force", False))
    try:
        values = _effective_init_config_values(args, config_path, force=force)
        wrote_config = _write_text_file(
            config_path,
            _config_text(values),
            force=force,
            mode=0o600,
        )
        db.init_db(values["SNAPZ_SERVER_DATA"])
        wrote_service = _write_text_file(
            service_file,
            _service_text(config_path=config_path, server_bin=_server_bin(args)),
            force=force,
            mode=0o644,
        )
    except (OSError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    print(f"{'created' if wrote_config else 'kept'} config: {config_path}")
    print(f"initialized data: {values['SNAPZ_SERVER_DATA']}")
    print(f"{'created' if wrote_service else 'kept'} service: {service_file}")

    if getattr(args, "no_enable", False):
        print("systemd: not enabled (--no-enable)")
        return EXIT_OK

    for systemctl_args in (["daemon-reload"], ["enable", "--now", "snapz-server"]):
        result = _run_systemctl(systemctl_args)
        if result.returncode != 0:
            _print_error(f"systemctl {' '.join(systemctl_args)} failed")
            return EXIT_ERROR
    print("systemd: enabled and started snapz-server")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    try:
        host = _run_host(args)
        port = _run_port(args)
        server = make_server(
            root,
            host=host,
            port=port,
            admin_token=args.admin_token,
            cors_origins=args.cors_origin,
            max_bundle_bytes=_max_bundle_bytes(args),
            tls_certfile=args.tls_cert,
            tls_keyfile=args.tls_key,
            tls_client_ca=args.tls_client_ca,
        )
    except (OSError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    host, port = server.server_address[:2]
    scheme = "https" if server.tls_certfile else "http"
    print(f"snapz-server {__version__} listening on {scheme}://{host}:{port}")
    print(f"data: {root}")
    if server.tls_client_ca:
        print(f"client certs: required, CA {server.tls_client_ca}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return EXIT_OK


def cmd_tenant_add(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    try:
        tenant = db.create_tenant(root, args.name)
    except sqlite3.IntegrityError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    print(f"tenant {tenant['name']} {tenant['id']}")
    return EXIT_OK


def cmd_user_add(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    try:
        password = _password_from_args(args)
        user = db.create_user(root, args.tenant, args.username, password)
    except (ValueError, sqlite3.IntegrityError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    print(f"user {args.tenant}/{user['username']} {user['id']}")
    return EXIT_OK


def cmd_user_reset_password(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    try:
        password = _password_from_args(args)
        db.reset_password(root, args.tenant, args.username, password)
    except (ValueError, KeyError) as exc:
        _print_error(str(exc).strip("'\""))
        return EXIT_ERROR
    print(f"password reset for {args.tenant}/{args.username}")
    return EXIT_OK


def cmd_device_revoke(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    db.init_db(root)
    if not db.revoke_device(root, args.device_id):
        _print_error(f"device not found or already revoked: {args.device_id}")
        return EXIT_ERROR
    print(f"revoked {args.device_id}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    stats = db.server_stats(root)
    print(f"data: {root}")
    print(f"tenants: {stats['tenants']}")
    print(f"users: {stats['users']}")
    print(f"devices: {stats['devices']}")
    print(f"sources: {stats['sources']}")
    print(f"bundles: {format_size(stats['bundle_bytes'])}")
    print(f"incoming: {format_size(stats['incoming_bytes'])}")
    print(f"cold: {format_size(stats['cold_bytes'])}")
    compact_jobs = stats.get("compact_jobs") or {}
    if compact_jobs:
        parts = [f"{name}={count}" for name, count in sorted(compact_jobs.items())]
        print(f"compact jobs: {', '.join(parts)}")
    else:
        print("compact jobs: 0")
    return EXIT_OK


def cmd_compact(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    db.init_db(root)
    limit = getattr(args, "limit", None)
    try:
        results = compact_pending(
            root,
            config=resolve_compact_config(),
            limit=limit,
        )
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if not results:
        print("compact jobs: 0")
        return EXIT_OK
    for result in results:
        print(
            "compacted "
            f"{result.tenant_id}/{result.source_id} "
            f"{result.snapshot_count} snapshot(s), "
            f"{result.object_count} object(s), "
            f"{result.chunk_count} chunk(s), "
            f"{format_size(result.cold_physical_bytes)} cold"
        )
    return EXIT_OK


def cmd_update(args: argparse.Namespace) -> int:
    from snapz import self_update
    from snapz.i18n import get_lang

    release_url = getattr(args, "target", None) or self_update.GITHUB_RELEASE_API
    config_path = _config_path(args)
    if config_path.exists():
        print(f"preserving config: {config_path}")
    try:
        plan = self_update.plan_update(
            language=get_lang(),
            package=self_update.SERVER_PACKAGE_NAME,
            release_url=release_url,
        )
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    print(f"updating snapz-server from {plan.download_url}")
    try:
        result = self_update.install_plan(plan)
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if result.returncode != 0:
        _print_error(f"update failed (installer exit code {result.returncode})")
        return EXIT_ERROR
    print("updated snapz-server")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    install_argparse_i18n()
    parser = argparse.ArgumentParser(
        prog="snapz-server",
        description=t("server.root.description"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"snapz-server {__version__}",
        help=t("flag.version"),
    )
    parser.add_argument(
        "--data",
        help=t("server.flag.data"),
    )
    parser.add_argument(
        "--config",
        dest="global_config",
        help=t("server.flag.config"),
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help=t("server.init.help"))
    p_init.add_argument("--config", help=t("server.arg.config"))
    p_init.add_argument("--data", help=t("server.init.data"))
    p_init.add_argument("--host", default=None, help=t("server.init.host"))
    p_init.add_argument("--port", default=None, type=int, help=t("server.init.port"))
    p_init.add_argument("--admin-token", help=t("server.init.admin_credential"))
    p_init.add_argument(
        "--max-bundle-mb",
        default=None,
        type=int,
        help=t("server.arg.max_bundle_mb"),
    )
    p_init.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help=t("server.arg.cors_origin"),
    )
    p_init.add_argument("--tls-cert", help=t("server.arg.tls_cert"))
    p_init.add_argument("--tls-key", help=t("server.arg.tls_key"))
    p_init.add_argument("--tls-client-ca", help=t("server.arg.tls_client_ca"))
    p_init.add_argument("--service-file", help=t("server.init.service_file"))
    p_init.add_argument("--server-bin", help=t("server.init.server_bin"))
    p_init.add_argument("--force", action="store_true", help=t("server.init.force"))
    p_init.add_argument("--no-enable", action="store_true", help=t("server.init.no_enable"))
    p_init.set_defaults(func=cmd_init)

    p_setup = sub.add_parser("setup", help=t("server.setup.help"))
    p_setup.add_argument("--config", help=t("server.arg.config"))
    p_setup.set_defaults(func=cmd_setup)

    p_run = sub.add_parser("run", help=t("server.run.help"))
    p_run.add_argument("--config", help=t("server.arg.config"))
    p_run.add_argument(
        "--host",
        default=None,
        help=t("server.run.host"),
    )
    p_run.add_argument(
        "--port",
        default=None,
        type=int,
        help=t("server.run.port"),
    )
    p_run.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help=t("server.run.cors_origin"),
    )
    p_run.add_argument(
        "--max-bundle-mb",
        default=None,
        type=int,
        help=t("server.run.max_bundle_mb"),
    )
    p_run.add_argument(
        "--tls-cert",
        help=t("server.arg.tls_cert"),
    )
    p_run.add_argument(
        "--tls-key",
        help=t("server.arg.tls_key"),
    )
    p_run.add_argument(
        "--tls-client-ca",
        help=t("server.run.tls_client_ca"),
    )
    p_run.add_argument(
        "--admin-token",
        help=t("server.run.admin_credential"),
    )
    p_run.set_defaults(func=cmd_run)

    p_tenant = sub.add_parser("tenant", help=t("server.tenant.help"))
    p_tenant.add_argument("--config", help=t("server.arg.config"))
    tenant_sub = p_tenant.add_subparsers(dest="tenant_op")
    p_tenant_add = tenant_sub.add_parser("add", help=t("server.tenant.add_help"))
    p_tenant_add.add_argument("name", help=t("server.tenant.name"))
    p_tenant_add.set_defaults(func=cmd_tenant_add)

    p_user = sub.add_parser("user", help=t("server.user.help"))
    p_user.add_argument("--config", help=t("server.arg.config"))
    user_sub = p_user.add_subparsers(dest="user_op")
    p_user_add = user_sub.add_parser("add", help=t("server.user.add_help"))
    p_user_add.add_argument("tenant", help=t("server.user.tenant"))
    p_user_add.add_argument("username", help=t("server.user.username"))
    p_user_add.add_argument("--password", help=t("server.user.password"))
    p_user_add.set_defaults(func=cmd_user_add)

    p_user_reset = user_sub.add_parser(
        "reset-password",
        help=t("server.user.reset_help"),
    )
    p_user_reset.add_argument("tenant", help=t("server.user.tenant"))
    p_user_reset.add_argument("username", help=t("server.user.username"))
    p_user_reset.add_argument("--password", help=t("server.user.password"))
    p_user_reset.set_defaults(func=cmd_user_reset_password)

    p_device = sub.add_parser("device", help=t("server.device.help"))
    p_device.add_argument("--config", help=t("server.arg.config"))
    device_sub = p_device.add_subparsers(dest="device_op")
    p_device_revoke = device_sub.add_parser(
        "revoke",
        help=t("server.device.revoke_help"),
    )
    p_device_revoke.add_argument("device_id", help=t("server.device.id"))
    p_device_revoke.set_defaults(func=cmd_device_revoke)

    p_doctor = sub.add_parser("doctor", help=t("server.doctor.help"))
    p_doctor.add_argument("--config", help=t("server.arg.config"))
    p_doctor.set_defaults(func=cmd_doctor)

    p_compact = sub.add_parser("compact", help="compact pending cold-storage jobs")
    p_compact.add_argument("--config", help=t("server.arg.config"))
    p_compact.add_argument(
        "--limit",
        type=int,
        default=None,
        help="maximum jobs to compact in this run",
    )
    p_compact.set_defaults(func=cmd_compact)

    p_update = sub.add_parser("update", help=t("server.update.help"))
    p_update.add_argument("--config", help=t("server.arg.config"))
    p_update.add_argument(
        "--target",
        default=None,
        help=argparse.SUPPRESS,
    )
    p_update.set_defaults(func=cmd_update)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if getattr(args, "command", None) not in {"init", "update"}:
        try:
            _load_config_env(_config_path(args))
        except ValueError as exc:
            _print_error(str(exc))
            return EXIT_ERROR
    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
