"""Command-line entry point for the standalone snapz server."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

from snapz import __version__
from snapz.util import format_size
from snapz_server import db
from snapz_server.app import make_server

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CONFIG_PATH = Path("/etc/default/snapz-server")
DEFAULT_SERVICE_FILE = Path("/etc/systemd/system/snapz-server.service")
DEFAULT_SERVICE_DATA_DIR = Path("/srv/snapz")
SNAPZ_PACKAGE_NAME = "snapz-cli"
SNAPZ_GITHUB_REPO = "https://github.com/IsolatedWolfLove/snap-all.git"
SNAPZ_GITHUB_INSTALL_TARGET = (
    f"{SNAPZ_PACKAGE_NAME}[zstd] @ git+{SNAPZ_GITHUB_REPO}"
)


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
            if key.startswith("SNAPZ_SERVER_"):
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


def _run_pip(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pip", *args], check=False, text=True)


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", *args], check=False, text=True)


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
            or "0.0.0.0"
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
    return EXIT_OK


def cmd_update(args: argparse.Namespace) -> int:
    target = getattr(args, "target", None) or SNAPZ_GITHUB_INSTALL_TARGET
    config_path = _config_path(args)
    pip_args = ["install", "--upgrade", target]
    print(f"updating snapz-server from {target}")
    if config_path.exists():
        print(f"preserving config: {config_path}")
    result = _run_pip(pip_args)
    if result.returncode != 0:
        _print_error(f"update failed (pip exit code {result.returncode})")
        return EXIT_ERROR
    print("updated snapz-server")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapz-server",
        description="Multi-tenant HTTP snapshot server for snapz clients.",
    )
    parser.add_argument("--version", action="version", version=f"snapz-server {__version__}")
    parser.add_argument(
        "--data",
        help="server data directory (default: ~/.snapz-server or SNAPZ_SERVER_DATA)",
    )
    parser.add_argument(
        "--config",
        dest="global_config",
        help=(
            "server config file "
            "(default: /etc/default/snapz-server or SNAPZ_SERVER_CONFIG)"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="initialize config, data, and systemd service")
    p_init.add_argument("--config", help="server config file")
    p_init.add_argument("--data", help="server data directory (default: /srv/snapz)")
    p_init.add_argument("--host", default=None, help="listen host (default: 0.0.0.0)")
    p_init.add_argument("--port", default=None, type=int, help="listen port (default: 8765)")
    p_init.add_argument("--admin-token", help="admin API bearer token (default: generated)")
    p_init.add_argument("--max-bundle-mb", default=None, type=int)
    p_init.add_argument("--cors-origin", action="append", default=[])
    p_init.add_argument("--tls-cert", help="PEM certificate for HTTPS")
    p_init.add_argument("--tls-key", help="PEM private key for HTTPS")
    p_init.add_argument("--tls-client-ca", help="PEM CA bundle for client certificates")
    p_init.add_argument("--service-file", help="systemd service path")
    p_init.add_argument("--server-bin", help="snapz-server executable path for systemd")
    p_init.add_argument("--force", action="store_true", help="overwrite existing config/service")
    p_init.add_argument("--no-enable", action="store_true", help="do not enable/start systemd service")
    p_init.set_defaults(func=cmd_init)

    p_setup = sub.add_parser("setup", help="initialize the server data directory")
    p_setup.add_argument("--config", help="server config file")
    p_setup.set_defaults(func=cmd_setup)

    p_run = sub.add_parser("run", help="run the HTTP server")
    p_run.add_argument("--config", help="server config file")
    p_run.add_argument(
        "--host",
        default=None,
        help="listen host (default: 127.0.0.1 or SNAPZ_SERVER_HOST)",
    )
    p_run.add_argument(
        "--port",
        default=None,
        type=int,
        help="listen port (default: 8765 or SNAPZ_SERVER_PORT)",
    )
    p_run.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help=(
            "allow this browser Origin for cross-origin admin API calls; "
            "repeat for multiple origins (default: same-origin only)"
        ),
    )
    p_run.add_argument(
        "--max-bundle-mb",
        default=None,
        type=int,
        help=(
            "maximum upload bundle size in MiB "
            "(default: 10240 or SNAPZ_SERVER_MAX_BUNDLE_MB)"
        ),
    )
    p_run.add_argument(
        "--tls-cert",
        help="PEM certificate for HTTPS",
    )
    p_run.add_argument(
        "--tls-key",
        help="PEM private key for HTTPS",
    )
    p_run.add_argument(
        "--tls-client-ca",
        help="PEM CA bundle that enables mTLS and requires client certificates",
    )
    p_run.add_argument(
        "--admin-token",
        help=(
            "enable /admin and /api/admin with this bearer token "
            "(or set SNAPZ_SERVER_ADMIN_TOKEN)"
        ),
    )
    p_run.set_defaults(func=cmd_run)

    p_tenant = sub.add_parser("tenant", help="manage tenants")
    p_tenant.add_argument("--config", help="server config file")
    tenant_sub = p_tenant.add_subparsers(dest="tenant_op")
    p_tenant_add = tenant_sub.add_parser("add", help="create a tenant")
    p_tenant_add.add_argument("name")
    p_tenant_add.set_defaults(func=cmd_tenant_add)

    p_user = sub.add_parser("user", help="manage users")
    p_user.add_argument("--config", help="server config file")
    user_sub = p_user.add_subparsers(dest="user_op")
    p_user_add = user_sub.add_parser("add", help="create a user")
    p_user_add.add_argument("tenant")
    p_user_add.add_argument("username")
    p_user_add.add_argument("--password")
    p_user_add.set_defaults(func=cmd_user_add)

    p_user_reset = user_sub.add_parser("reset-password", help="set a new password")
    p_user_reset.add_argument("tenant")
    p_user_reset.add_argument("username")
    p_user_reset.add_argument("--password")
    p_user_reset.set_defaults(func=cmd_user_reset_password)

    p_device = sub.add_parser("device", help="manage devices")
    p_device.add_argument("--config", help="server config file")
    device_sub = p_device.add_subparsers(dest="device_op")
    p_device_revoke = device_sub.add_parser("revoke", help="revoke a device token")
    p_device_revoke.add_argument("device_id")
    p_device_revoke.set_defaults(func=cmd_device_revoke)

    p_doctor = sub.add_parser("doctor", help="show server health and storage stats")
    p_doctor.add_argument("--config", help="server config file")
    p_doctor.set_defaults(func=cmd_doctor)

    p_update = sub.add_parser("update", help="upgrade snapz-server without touching config")
    p_update.add_argument("--config", help="server config file")
    p_update.add_argument(
        "--target",
        default=SNAPZ_GITHUB_INSTALL_TARGET,
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
