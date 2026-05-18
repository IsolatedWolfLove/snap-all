"""Command-line entry point for the standalone snapz server."""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from snapz import __version__
from snapz.util import format_size
from snapz_server import db
from snapz_server.app import make_server

EXIT_OK = 0
EXIT_ERROR = 1


def _print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _data_dir(args: argparse.Namespace) -> Path:
    return db.resolve_data_dir(getattr(args, "data", None))


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


def cmd_run(args: argparse.Namespace) -> int:
    root = _data_dir(args)
    try:
        server = make_server(
            root,
            host=args.host,
            port=args.port,
            admin_token=args.admin_token,
            cors_origins=args.cors_origin,
            max_bundle_bytes=args.max_bundle_mb * 1024 * 1024,
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
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="initialize the server data directory")
    p_setup.set_defaults(func=cmd_setup)

    p_run = sub.add_parser("run", help="run the HTTP server")
    p_run.add_argument("--host", default="127.0.0.1")
    p_run.add_argument("--port", default=8765, type=int)
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
        default=10 * 1024,
        type=int,
        help="maximum upload bundle size in MiB (default: 10240)",
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
    tenant_sub = p_tenant.add_subparsers(dest="tenant_op")
    p_tenant_add = tenant_sub.add_parser("add", help="create a tenant")
    p_tenant_add.add_argument("name")
    p_tenant_add.set_defaults(func=cmd_tenant_add)

    p_user = sub.add_parser("user", help="manage users")
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
    device_sub = p_device.add_subparsers(dest="device_op")
    p_device_revoke = device_sub.add_parser("revoke", help="revoke a device token")
    p_device_revoke.add_argument("device_id")
    p_device_revoke.set_defaults(func=cmd_device_revoke)

    p_doctor = sub.add_parser("doctor", help="show server health and storage stats")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
