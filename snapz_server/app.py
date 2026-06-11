"""HTTP API for the standalone ``snapz-server`` process."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import sqlite3
import tarfile
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from snapz.util import format_size
from snapz import __version__
from snapz_server import db
from snapz_server.admin_ui import ADMIN_UI_HTML
from snapz_server.bundles import (
    BundleMemoryError,
    bundle_index,
    delete_bundle_snapshot,
    list_bundle_snapshots,
    merge_delta_bundle,
    rename_bundle_snapshot,
    validate_delta_bundle_file,
    validate_bundle_file,
)
from snapz.api import _open_bundle_tar_reader
from snapz_server.payloads import (
    decode_meta_header as _decode_meta_header,
    read_bundle_meta as _read_bundle_meta,
)
from snapz_server.routes import (
    admin_device_id_from_revoke_path as _admin_device_id_from_revoke_path,
    admin_source_ref_from_path as _admin_source_ref_from_path,
    admin_source_snapshot_ref_from_path as _admin_source_snapshot_ref_from_path,
    admin_source_snapshots_ref_from_path as _admin_source_snapshots_ref_from_path,
    admin_user_id_from_devices_path as _admin_user_id_from_devices_path,
    admin_user_id_from_password_path as _admin_user_id_from_password_path,
    admin_user_id_from_path as _admin_user_id_from_path,
    admin_user_id_from_revoke_devices_path as _admin_user_id_from_revoke_devices_path,
    is_sha256 as _is_sha256,
    safe_id as _safe_id,
    safe_snapshot_name as _safe_snapshot_name,
    source_id_from_bundle_path as _source_id_from_bundle_path,
    source_id_from_delta_path as _source_id_from_delta_path,
    source_id_from_index_path as _source_id_from_index_path,
    source_id_from_sync_status_path as _source_id_from_sync_status_path,
    source_object_ref_from_path as _source_object_ref_from_path,
)
from snapz_server.serializers import (
    admin_device_dict as _admin_device_dict,
    admin_source_dict as _admin_source_dict,
    admin_user_dict as _admin_user_dict,
    ctx_dict as _ctx_dict,
    row_dict as _row_dict,
)
from snapz_server.server_config import (
    DEFAULT_MAX_BUNDLE_BYTES,
    enable_tls as _enable_tls,
    resolve_cors_origins as _resolve_cors_origins,
    resolve_max_bundle_bytes as _resolve_max_bundle_bytes,
    resolve_tls_path as _resolve_tls_path,
)

MAX_JSON_BYTES = 1024 * 1024
CHUNK_SIZE = 1024 * 1024
DEVICE_SWEEP_INTERVAL_SECONDS = db.DEVICE_OFFLINE_AFTER_HOURS * 60 * 60


class SnapzHTTPServer(ThreadingHTTPServer):
    """Threaded stdlib HTTP server with an attached data directory."""

    data_dir: Path
    admin_token: str
    cors_origins: tuple[str, ...]
    max_bundle_bytes: int
    tls_certfile: str
    tls_keyfile: str
    tls_client_ca: str
    _device_sweeper_stop: threading.Event
    _device_sweeper_thread: threading.Thread | None

    def server_close(self) -> None:
        stop = getattr(self, "_device_sweeper_stop", None)
        if stop is not None:
            stop.set()
        thread = getattr(self, "_device_sweeper_thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        super().server_close()


def make_server(
    data_dir: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    admin_token: str | None = None,
    cors_origins: Iterable[str] | None = None,
    max_bundle_bytes: int | None = None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
    tls_client_ca: str | None = None,
    device_sweep_interval_seconds: float | None = None,
) -> SnapzHTTPServer:
    root = db.resolve_data_dir(data_dir)
    db.init_db(root)
    server = SnapzHTTPServer((host, port), SnapzHandler)
    server.data_dir = root
    server.admin_token = (
        admin_token
        if admin_token is not None
        else os.environ.get("SNAPZ_SERVER_ADMIN_TOKEN", "")
    )
    server.cors_origins = _resolve_cors_origins(cors_origins)
    server.max_bundle_bytes = _resolve_max_bundle_bytes(max_bundle_bytes)
    server.tls_certfile = _resolve_tls_path(
        tls_certfile,
        env_name="SNAPZ_SERVER_TLS_CERT",
    )
    server.tls_keyfile = _resolve_tls_path(
        tls_keyfile,
        env_name="SNAPZ_SERVER_TLS_KEY",
    )
    server.tls_client_ca = _resolve_tls_path(
        tls_client_ca,
        env_name="SNAPZ_SERVER_TLS_CLIENT_CA",
    )
    _enable_tls(server)
    _start_device_sweeper(
        server,
        (
            DEVICE_SWEEP_INTERVAL_SECONDS
            if device_sweep_interval_seconds is None
            else device_sweep_interval_seconds
        ),
    )
    return server


def _start_device_sweeper(
    server: SnapzHTTPServer,
    interval_seconds: float,
) -> None:
    stop = threading.Event()
    server._device_sweeper_stop = stop
    if interval_seconds <= 0:
        server._device_sweeper_thread = None
        return
    thread = threading.Thread(
        target=_device_sweeper_loop,
        args=(server, stop, interval_seconds),
        name="snapz-device-sweeper",
        daemon=True,
    )
    server._device_sweeper_thread = thread
    thread.start()


def _device_sweeper_loop(
    server: SnapzHTTPServer,
    stop: threading.Event,
    interval_seconds: float,
) -> None:
    while not stop.wait(interval_seconds):
        try:
            db.mark_stale_devices_offline(server.data_dir)
        except Exception:
            pass


class SnapzHandler(BaseHTTPRequestHandler):
    server_version = "snapz-server/0.4"

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        db.mark_stale_devices_offline(self.data_dir)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self._send_dashboard()
            return
        if path == "/admin":
            self._send_admin_app()
            return
        if path == "/api/admin/overview":
            if not self._require_admin():
                return
            stats = db.server_stats(self.data_dir)
            self._send_json(
                HTTPStatus.OK,
                {
                    "stats": stats,
                    "version": __version__,
                    "server_version": self.server_version,
                },
            )
            return
        if path == "/api/admin/users":
            if not self._require_admin():
                return
            self._send_json(
                HTTPStatus.OK,
                {"users": [_admin_user_dict(row) for row in db.list_admin_users(self.data_dir)]},
            )
            return
        if path == "/api/admin/sources":
            if not self._require_admin():
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "sources": [
                        _admin_source_dict(row)
                        for row in db.list_admin_sources(self.data_dir)
                    ],
                },
            )
            return
        user_id = _admin_user_id_from_devices_path(path)
        if user_id:
            if not self._require_admin():
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "devices": [
                        _admin_device_dict(row)
                        for row in db.list_admin_devices(self.data_dir, user_id=user_id)
                    ],
                },
            )
            return
        if path == "/api/admin/devices":
            if not self._require_admin():
                return
            self._send_json(
                HTTPStatus.OK,
                {"devices": [_admin_device_dict(row) for row in db.list_admin_devices(self.data_dir)]},
            )
            return
        source_ref = _admin_source_snapshots_ref_from_path(path)
        if source_ref:
            tenant_id, source_id = source_ref
            self._handle_admin_list_source_snapshots(
                tenant_id,
                source_id,
                parsed.query,
            )
            return
        if path == "/api/me":
            ctx = self._require_auth()
            if ctx is None:
                return
            self._send_json(HTTPStatus.OK, {"user": _ctx_dict(ctx)})
            return
        if path == "/api/sources":
            ctx = self._require_auth()
            if ctx is None:
                return
            rows = [_row_dict(row) for row in db.list_sources(self.data_dir, ctx)]
            self._send_json(HTTPStatus.OK, {"sources": rows})
            return
        if path.startswith("/api/sources/") and path.endswith("/index"):
            self._handle_get_index(path)
            return
        if path.startswith("/api/sources/") and "/objects/" in path:
            self._handle_get_object(path)
            return
        if path.startswith("/api/sources/") and path.endswith("/bundle"):
            self._handle_get_bundle(path)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        db.mark_stale_devices_offline(self.data_dir)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/auth/login":
            self._handle_login()
            return
        if path == "/api/auth/logout":
            self._handle_logout()
            return
        source_id = _source_id_from_sync_status_path(path)
        if source_id:
            self._handle_source_sync_status(source_id)
            return
        if path == "/api/admin/users":
            self._handle_admin_create_user()
            return
        user_id = _admin_user_id_from_password_path(path)
        if user_id:
            self._handle_admin_reset_password(user_id)
            return
        user_id = _admin_user_id_from_revoke_devices_path(path)
        if user_id:
            self._handle_admin_revoke_user_devices(user_id)
            return
        device_id = _admin_device_id_from_revoke_path(path)
        if device_id:
            self._handle_admin_revoke_device(device_id)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        db.mark_stale_devices_offline(self.data_dir)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/api/sources/") and path.endswith("/bundle"):
            self._handle_put_bundle(path)
            return
        if path.startswith("/api/sources/") and path.endswith("/delta"):
            self._handle_put_delta(path)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        db.mark_stale_devices_offline(self.data_dir)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        snapshot_ref = _admin_source_snapshot_ref_from_path(path)
        if snapshot_ref:
            tenant_id, source_id, snapshot_name = snapshot_ref
            self._handle_admin_rename_source_snapshot(
                tenant_id,
                source_id,
                snapshot_name,
            )
            return
        source_ref = _admin_source_ref_from_path(path)
        if source_ref:
            tenant_id, source_id = source_ref
            self._handle_admin_update_source(tenant_id, source_id)
            return
        user_id = _admin_user_id_from_path(path)
        if user_id:
            self._handle_admin_update_user(user_id)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        db.mark_stale_devices_offline(self.data_dir)
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        snapshot_ref = _admin_source_snapshot_ref_from_path(path)
        if snapshot_ref:
            tenant_id, source_id, snapshot_name = snapshot_ref
            self._handle_admin_delete_source_snapshot(
                tenant_id,
                source_id,
                snapshot_name,
            )
            return
        source_ref = _admin_source_ref_from_path(path)
        if source_ref:
            tenant_id, source_id = source_ref
            self._handle_admin_delete_source(tenant_id, source_id)
            return
        user_id = _admin_user_id_from_path(path)
        if user_id:
            self._handle_admin_delete_user(user_id)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_login(self) -> None:
        try:
            payload = self._read_json()
            tenant = str(payload.get("tenant") or "").strip()
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
            device_name = str(payload.get("device_name") or "device").strip()
            machine_id = str(payload.get("machine_id") or "").strip()
            machine_id_aliases = [
                str(item).strip()
                for item in (payload.get("machine_id_aliases") or [])
                if str(item).strip()
            ]
            if not tenant or not username or not password:
                raise ValueError("tenant, username and password are required")
            ctx, token = db.login_device(
                self.data_dir,
                tenant,
                username,
                password,
                device_name,
                machine_id,
                machine_id_aliases,
            )
        except PermissionError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "token": token,
                "device": {
                    "id": ctx.device_id,
                    "name": ctx.device_name,
                },
                "user": _ctx_dict(ctx),
            },
        )

    def _handle_logout(self) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        db.unregister_device(self.data_dir, ctx)
        self._send_json(HTTPStatus.OK, {"ok": True, "device_id": ctx.device_id})

    def _handle_source_sync_status(self, source_id: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        try:
            payload = self._read_json()
            db.update_source_sync_status(
                self.data_dir,
                ctx,
                source_id,
                status=str(payload.get("status") or "running"),
                phase=str(payload.get("phase") or ""),
                display_name=str(payload.get("display_name") or ""),
                origin_store_key=str(payload.get("key") or ""),
                bytes_sent=int(payload.get("bytes_sent") or 0),
                bytes_total=int(payload.get("bytes_total") or 0),
                progress_percent=float(payload.get("progress_percent") or 0.0),
                speed_bps=float(payload.get("speed_bps") or 0.0),
                eta_seconds=(
                    None
                    if payload.get("eta_seconds") in {None, ""}
                    else float(payload.get("eta_seconds") or 0.0)
                ),
                remote_only=bool(payload.get("remote_only", False)),
                message=str(payload.get("message") or ""),
            )
        except (TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_create_user(self) -> None:
        if not self._require_admin():
            return
        try:
            payload = self._read_json()
            tenant = str(payload.get("tenant") or "").strip()
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
            disabled = bool(payload.get("disabled", False))
            if not tenant or not username or not password:
                raise ValueError("tenant, username and password are required")
            row = db.create_user(self.data_dir, tenant, username, password)
            if disabled:
                db.update_user(self.data_dir, row["id"], disabled=True)
            created = db.get_admin_user(self.data_dir, row["id"])
            if created is None:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "created user could not be loaded"},
                )
                return
        except sqlite3.IntegrityError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.CREATED,
            {"user": _admin_user_dict(created), "devices": []},
        )

    def _handle_admin_update_user(self, user_id: str) -> None:
        if not self._require_admin():
            return
        try:
            payload = self._read_json()
            username = (
                str(payload.get("username") or "").strip()
                if "username" in payload
                else None
            )
            disabled = bool(payload["disabled"]) if "disabled" in payload else None
            row = db.update_user(
                self.data_dir,
                user_id,
                username=username,
                disabled=disabled,
            )
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
            return
        except sqlite3.IntegrityError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"user": _admin_user_dict(row)})

    def _handle_admin_reset_password(self, user_id: str) -> None:
        if not self._require_admin():
            return
        try:
            payload = self._read_json()
            password = str(payload.get("password") or "")
            if not db.reset_password_by_user_id(self.data_dir, user_id, password):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "user not found"})
                return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_delete_user(self, user_id: str) -> None:
        if not self._require_admin():
            return
        if not db.delete_user(self.data_dir, user_id):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "user not found"})
            return
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_revoke_user_devices(self, user_id: str) -> None:
        if not self._require_admin():
            return
        if db.get_admin_user(self.data_dir, user_id) is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "user not found"})
            return
        count = db.revoke_user_devices(self.data_dir, user_id)
        self._send_json(HTTPStatus.OK, {"revoked": count})

    def _handle_admin_revoke_device(self, device_id: str) -> None:
        if not self._require_admin():
            return
        if not db.revoke_device(self.data_dir, device_id):
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "device not found or already revoked"},
            )
            return
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_update_source(self, tenant_id: str, source_id: str) -> None:
        if not self._require_admin():
            return
        try:
            payload = self._read_json()
            display_name = (
                str(payload.get("display_name") or "").strip()
                if "display_name" in payload
                else None
            )
            row = db.update_admin_source(
                self.data_dir,
                tenant_id,
                source_id,
                display_name=display_name,
            )
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"source": _admin_source_dict(row)})

    def _handle_admin_delete_source(self, tenant_id: str, source_id: str) -> None:
        if not self._require_admin():
            return
        if not db.delete_admin_source(self.data_dir, tenant_id, source_id):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "source not found"})
            return
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _handle_admin_list_source_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        query: str,
    ) -> None:
        if not self._require_admin():
            return
        source_bundle = self._admin_source_bundle_or_error(tenant_id, source_id)
        if source_bundle is None:
            return
        row, bundle = source_bundle
        params = parse_qs(query)
        try:
            page = int((params.get("page") or ["1"])[0])
            per_page = int((params.get("per_page") or ["50"])[0])
            result = list_bundle_snapshots(bundle, page=page, per_page=per_page)
        except BundleMemoryError as exc:
            self._send_json(HTTPStatus.INSUFFICIENT_STORAGE, {"error": str(exc)})
            return
        except (ValueError, OSError, tarfile.TarError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "source": _admin_source_dict(row),
                **result,
            },
        )

    def _handle_admin_rename_source_snapshot(
        self,
        tenant_id: str,
        source_id: str,
        snapshot_name: str,
    ) -> None:
        if not self._require_admin():
            return
        source_bundle = self._admin_source_bundle_or_error(tenant_id, source_id)
        if source_bundle is None:
            return
        _row, bundle = source_bundle
        try:
            payload = self._read_json()
            new_name = str(payload.get("name") or "").strip()
            result = rename_bundle_snapshot(bundle, snapshot_name, new_name)
            updated = db.update_admin_source_bundle_stats(
                self.data_dir,
                tenant_id,
                source_id,
                snapshot_count=int(result["snapshot_count"]),
                bundle_bytes=int(result["bundle_bytes"]),
                bundle_sha256=str(result.get("bundle_sha256") or ""),
            )
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
            return
        except FileExistsError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        except BundleMemoryError as exc:
            self._send_json(HTTPStatus.INSUFFICIENT_STORAGE, {"error": str(exc)})
            return
        except (ValueError, OSError, tarfile.TarError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "source": _admin_source_dict(updated),
                **result,
            },
        )

    def _handle_admin_delete_source_snapshot(
        self,
        tenant_id: str,
        source_id: str,
        snapshot_name: str,
    ) -> None:
        if not self._require_admin():
            return
        source_bundle = self._admin_source_bundle_or_error(tenant_id, source_id)
        if source_bundle is None:
            return
        _row, bundle = source_bundle
        try:
            result = delete_bundle_snapshot(bundle, snapshot_name)
            if int(result["snapshot_count"]) == 0:
                db.delete_admin_source(self.data_dir, tenant_id, source_id)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "deleted_source": True,
                        **result,
                    },
                )
                return
            updated = db.update_admin_source_bundle_stats(
                self.data_dir,
                tenant_id,
                source_id,
                snapshot_count=int(result["snapshot_count"]),
                bundle_bytes=int(result["bundle_bytes"]),
                bundle_sha256=str(result.get("bundle_sha256") or ""),
            )
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
            return
        except BundleMemoryError as exc:
            self._send_json(HTTPStatus.INSUFFICIENT_STORAGE, {"error": str(exc)})
            return
        except (ValueError, OSError, tarfile.TarError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "deleted_source": False,
                "source": _admin_source_dict(updated),
                **result,
            },
        )

    def _admin_source_bundle_or_error(
        self,
        tenant_id: str,
        source_id: str,
    ) -> tuple[Any, Path] | None:
        row = db.get_admin_source(self.data_dir, tenant_id, source_id)
        if row is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "source not found"})
            return None
        bundle = db.bundle_path(self.data_dir, tenant_id, source_id)
        if not bundle.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "bundle not found"})
            return None
        return row, bundle

    def _handle_get_bundle(self, path: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        source_id = _source_id_from_bundle_path(path)
        if not source_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid source id"})
            return
        row = db.get_source(self.data_dir, ctx, source_id)
        if row is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "source not found"})
            return
        bundle = db.bundle_path(self.data_dir, ctx.tenant_id, source_id)
        if not bundle.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "bundle not found"})
            return
        self._send_file(bundle, content_type="application/octet-stream")

    def _handle_get_index(self, path: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        source_id = _source_id_from_index_path(path)
        if not source_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid source id"})
            return
        row = db.get_source(self.data_dir, ctx, source_id)
        if row is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "source not found"})
            return
        bundle = db.bundle_path(self.data_dir, ctx.tenant_id, source_id)
        if not bundle.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "bundle not found"})
            return
        try:
            self._send_json(HTTPStatus.OK, bundle_index(bundle))
        except (ValueError, tarfile.TarError, OSError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_get_object(self, path: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        ref = _source_object_ref_from_path(path)
        if ref is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid object path"})
            return
        source_id, sha = ref
        row = db.get_source(self.data_dir, ctx, source_id)
        if row is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "source not found"})
            return
        bundle = db.bundle_path(self.data_dir, ctx.tenant_id, source_id)
        if not bundle.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "bundle not found"})
            return
        try:
            with _open_bundle_tar_reader(bundle) as tar:
                member_name = f"objects/{sha[:2]}/{sha}"
                try:
                    member = tar.getmember(member_name)
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "object not found"})
                    return
                if not member.isfile():
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "object is not a file"})
                    return
                src = tar.extractfile(member)
                if src is None:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "object is not readable"})
                    return
                raw = src.read()
        except (ValueError, tarfile.TarError, OSError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_bytes(HTTPStatus.OK, raw, content_type="application/octet-stream")

    def _handle_put_bundle(self, path: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        source_id = _source_id_from_bundle_path(path)
        if not source_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid source id"})
            return
        length_text = self.headers.get("Content-Length")
        if not length_text:
            self._send_json(
                HTTPStatus.LENGTH_REQUIRED,
                {"error": "Content-Length is required"},
            )
            return
        try:
            remaining = int(length_text)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
            return
        if remaining < 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
            return
        max_bundle_bytes = self.server.max_bundle_bytes  # type: ignore[attr-defined]
        if remaining > max_bundle_bytes:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "error": (
                        f"bundle is too large: {remaining} bytes "
                        f"(limit {max_bundle_bytes} bytes)"
                    ),
                },
            )
            return
        try:
            header_meta = _decode_meta_header(
                self.headers.get("X-Snapz-Source-Meta", "")
            )
            expected_sha256 = str(header_meta.get("bundle_sha256") or "").strip()
            if not _is_sha256(expected_sha256):
                raise ValueError("missing or invalid bundle_sha256 in metadata")
            expected_id = db.source_id_for(dict(header_meta.get("source") or {}))
            if expected_id != source_id:
                raise ValueError("source id does not match metadata")
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        target = db.bundle_path(self.data_dir, ctx.tenant_id, source_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{source_id}.",
            suffix=".tmp",
            dir=target.parent,
        )
        received = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("incomplete request body")
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ValueError("bundle sha256 does not match metadata")
            validate_bundle_file(Path(tmp_name))
            bundle_meta = _read_bundle_meta(Path(tmp_name))
            bundle_source = dict(bundle_meta.get("source") or {})
            expected_id = db.source_id_for(bundle_source)
            if expected_id != source_id:
                raise ValueError("bundle source id does not match URL")
            snapshot_count = len(list(bundle_meta.get("snapshots") or []))
            os.replace(tmp_name, target)
            db.upsert_source(
                self.data_dir,
                ctx,
                source_id,
                bundle_source,
                snapshot_count=snapshot_count,
                bundle_bytes=received,
                bundle_sha256=expected_sha256,
            )
            db.log_event(
                self.data_dir,
                ctx,
                "push",
                f"{source_id} {snapshot_count} snapshot(s)",
            )
        except (ValueError, tarfile.TarError) as exc:
            Path(tmp_name).unlink(missing_ok=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except OSError as exc:
            Path(tmp_name).unlink(missing_ok=True)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "source_id": source_id,
                "snapshot_count": snapshot_count,
                "bundle_bytes": received,
            },
        )

    def _handle_put_delta(self, path: str) -> None:
        ctx = self._require_auth()
        if ctx is None:
            return
        source_id = _source_id_from_delta_path(path)
        if not source_id:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid source id"})
            return
        length_text = self.headers.get("Content-Length")
        if not length_text:
            self._send_json(
                HTTPStatus.LENGTH_REQUIRED,
                {"error": "Content-Length is required"},
            )
            return
        try:
            remaining = int(length_text)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
            return
        if remaining < 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
            return
        max_bundle_bytes = self.server.max_bundle_bytes  # type: ignore[attr-defined]
        if remaining > max_bundle_bytes:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "error": (
                        f"bundle is too large: {remaining} bytes "
                        f"(limit {max_bundle_bytes} bytes)"
                    ),
                },
            )
            return
        try:
            header_meta = _decode_meta_header(
                self.headers.get("X-Snapz-Source-Meta", "")
            )
            expected_sha256 = str(header_meta.get("bundle_sha256") or "").strip()
            if not _is_sha256(expected_sha256):
                raise ValueError("missing or invalid bundle_sha256 in metadata")
            expected_id = db.source_id_for(dict(header_meta.get("source") or {}))
            if expected_id != source_id:
                raise ValueError("source id does not match metadata")
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        target = db.bundle_path(self.data_dir, ctx.tenant_id, source_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{source_id}.delta.",
            suffix=".tmp",
            dir=target.parent,
        )
        received = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("incomplete request body")
                    out.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ValueError("bundle sha256 does not match metadata")
            existing_blobs: set[str] = set()
            if target.is_file():
                existing_blobs = set(bundle_index(target).get("blobs") or [])
            validate_delta_bundle_file(Path(tmp_name), existing_blobs=existing_blobs)
            delta_meta = _read_bundle_meta(Path(tmp_name))
            bundle_source = dict(delta_meta.get("source") or {})
            expected_id = db.source_id_for(bundle_source)
            if expected_id != source_id:
                raise ValueError("bundle source id does not match URL")
            index = merge_delta_bundle(target, Path(tmp_name))
            snapshot_count = int(index.get("snapshot_count") or 0)
            bundle_bytes = int(index.get("bundle_bytes") or target.stat().st_size)
            db.upsert_source(
                self.data_dir,
                ctx,
                source_id,
                bundle_source,
                snapshot_count=snapshot_count,
                bundle_bytes=bundle_bytes,
                bundle_sha256=str(index.get("bundle_sha256") or ""),
            )
            db.log_event(
                self.data_dir,
                ctx,
                "push-delta",
                f"{source_id} {snapshot_count} snapshot(s)",
            )
        except (ValueError, tarfile.TarError) as exc:
            Path(tmp_name).unlink(missing_ok=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except OSError as exc:
            Path(tmp_name).unlink(missing_ok=True)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "source_id": source_id,
                "snapshot_count": snapshot_count,
                "bundle_bytes": bundle_bytes,
                "delta_bytes": received,
            },
        )

    def _require_auth(self) -> Optional[db.AuthContext]:
        auth = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "missing bearer token"})
            return None
        ctx = db.authenticate_token(self.data_dir, auth[len(prefix):].strip())
        if ctx is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
            return None
        return ctx

    def _require_admin(self) -> bool:
        expected = self.server.admin_token  # type: ignore[attr-defined]
        if not expected:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error": (
                        "admin API disabled; start snapz-server with "
                        "--admin-token or SNAPZ_SERVER_ADMIN_TOKEN"
                    ),
                },
            )
            return False
        auth = self.headers.get("Authorization", "")
        prefix = "Bearer "
        token = auth[len(prefix):].strip() if auth.startswith(prefix) else ""
        if not token or not hmac.compare_digest(token, expected):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid admin token"})
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_text)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0:
            return {}
        if length > MAX_JSON_BYTES:
            raise ValueError("JSON body is too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self._send_bytes(
            status,
            raw,
            content_type="application/json; charset=utf-8",
            cache_control="no-store",
        )

    def _send_bytes(
        self,
        status: int,
        raw: bytes,
        *,
        content_type: str,
        cache_control: str | None = None,
    ) -> None:
        self.send_response(int(status))
        self._send_cors_headers()
        self._send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, *, content_type: str) -> None:
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self._send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as src:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        allowed = self.server.cors_origins  # type: ignore[attr-defined]
        allow_origin = ""
        if "*" in allowed:
            allow_origin = "*"
        elif origin and origin in allowed:
            allow_origin = origin
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Snapz-Source-Meta",
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        )

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )

    def _send_admin_app(self) -> None:
        raw = ADMIN_UI_HTML.encode("utf-8")
        self._send_bytes(
            HTTPStatus.OK,
            raw,
            content_type="text/html; charset=utf-8",
            cache_control="no-store",
        )

    def _send_dashboard(self) -> None:
        stats = db.server_stats(self.data_dir)
        rows = "\n".join(
            "<tr><th>{}</th><td>{}</td></tr>".format(
                html.escape(label),
                html.escape(value),
            )
            for label, value in [
                ("version", __version__),
                ("data dir", str(self.data_dir)),
                ("tenants", str(stats["tenants"])),
                ("users", str(stats["users"])),
                ("devices", str(stats["devices"])),
                ("sources", str(stats["sources"])),
                ("bundle storage", format_size(stats["bundle_bytes"])),
            ]
        )
        raw = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>snapz-server</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #111; }}
    table {{ border-collapse: collapse; min-width: 24rem; }}
    th, td {{ padding: .55rem .75rem; border-bottom: 1px solid #ddd; text-align: left; }}
    th {{ color: #555; font-weight: 600; }}
    code {{ background: #f3f3f3; padding: .15rem .3rem; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>snapz-server</h1>
  <table>{rows}</table>
  <p><a href="/admin">Open the admin console</a> to manage users and devices.</p>
  <p>Use <code>snapz login</code>, <code>snapz push all</code>, and <code>snapz pull all</code> from clients.</p>
</body>
</html>
        """.encode("utf-8")
        self._send_bytes(
            HTTPStatus.OK,
            raw,
            content_type="text/html; charset=utf-8",
        )
