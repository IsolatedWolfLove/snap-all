"""Client-side remote sync helpers for ``snapz push`` and ``snapz pull``."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import platform
import ssl
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from snapz import api
from snapz.config import RuntimeConfig, default_config
from snapz.store import DirEntry, Store
from snapz.util import format_size

REMOTE_CONFIG_FILENAME = "_remote.json"
CHUNK_SIZE = 1024 * 1024


@dataclass
class RemoteAuth:
    server_url: str
    tenant: str
    username: str
    token: str
    device_id: str
    device_name: str
    tls_ca: str = ""
    tls_client_cert: str = ""
    tls_client_key: str = ""

    def tls_kwargs(self) -> dict[str, str]:
        return {
            "tls_ca": self.tls_ca,
            "tls_client_cert": self.tls_client_cert,
            "tls_client_key": self.tls_client_key,
        }


@dataclass
class SyncItem:
    source_id: str
    key: str
    display_name: str
    snapshot_count: int
    bundle_bytes: int
    archived: bool = False


@dataclass
class SyncFailure:
    key: str
    source_id: str
    message: str


@dataclass
class SyncOutcome:
    server_url: str
    items: list[SyncItem] = field(default_factory=list)
    failures: list[SyncFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


class RemoteError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def config_path(config: Optional[RuntimeConfig] = None) -> Path:
    cfg = config or default_config()
    return Path(cfg.root) / REMOTE_CONFIG_FILENAME


def load_auth(config: Optional[RuntimeConfig] = None) -> RemoteAuth:
    path = config_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "not logged in; run snapz login SERVER --tenant TENANT --username USER"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid remote config: {path}") from exc
    return RemoteAuth(
        server_url=str(data["server_url"]),
        tenant=str(data["tenant"]),
        username=str(data["username"]),
        token=str(data["token"]),
        device_id=str(data.get("device_id", "")),
        device_name=str(data.get("device_name", "")),
        tls_ca=str(data.get("tls_ca", "")),
        tls_client_cert=str(data.get("tls_client_cert", "")),
        tls_client_key=str(data.get("tls_client_key", "")),
    )


def save_auth(auth: RemoteAuth, config: Optional[RuntimeConfig] = None) -> Path:
    path = config_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "server_url": auth.server_url,
        "tenant": auth.tenant,
        "username": auth.username,
        "token": auth.token,
        "device_id": auth.device_id,
        "device_name": auth.device_name,
    }
    if auth.tls_ca:
        payload["tls_ca"] = auth.tls_ca
    if auth.tls_client_cert:
        payload["tls_client_cert"] = auth.tls_client_cert
    if auth.tls_client_key:
        payload["tls_client_key"] = auth.tls_client_key
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def logout(config: Optional[RuntimeConfig] = None) -> bool:
    path = config_path(config)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def login(
    server_url: str,
    *,
    tenant: str,
    username: str,
    password: str,
    device_name: str = "",
    tls_ca: str = "",
    tls_client_cert: str = "",
    tls_client_key: str = "",
    config: Optional[RuntimeConfig] = None,
) -> RemoteAuth:
    normalized_url = _normalize_server_url(server_url)
    tls_ca, tls_client_cert, tls_client_key = _prepare_tls_options(
        normalized_url,
        tls_ca=tls_ca,
        tls_client_cert=tls_client_cert,
        tls_client_key=tls_client_key,
    )
    payload = {
        "tenant": tenant,
        "username": username,
        "password": password,
        "device_name": device_name or platform.node() or "device",
    }
    response = _request_json(
        "POST",
        normalized_url,
        "/api/auth/login",
        payload=payload,
        tls_ca=tls_ca,
        tls_client_cert=tls_client_cert,
        tls_client_key=tls_client_key,
    )
    auth = RemoteAuth(
        server_url=normalized_url,
        tenant=tenant,
        username=username,
        token=str(response["token"]),
        device_id=str((response.get("device") or {}).get("id", "")),
        device_name=str((response.get("device") or {}).get("name", payload["device_name"])),
        tls_ca=tls_ca,
        tls_client_cert=tls_client_cert,
        tls_client_key=tls_client_key,
    )
    save_auth(auth, config)
    return auth


def push_all(*, config: Optional[RuntimeConfig] = None) -> SyncOutcome:
    cfg = config or default_config()
    auth = load_auth(cfg)
    store = Store(cfg)
    outcome = SyncOutcome(server_url=auth.server_url)
    entries = store.list_all(include_archived=True)
    for entry in entries:
        if not entry.snapshots:
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="snapz-push-") as tmpdir:
                bundle = Path(tmpdir) / f"{entry.key}.snapz"
                exported = _export_entry_bundle(entry, bundle, cfg)
                meta = read_bundle_meta(bundle)
                source = dict(meta.get("source") or {})
                source_id = source_id_for(source)
                bundle_bytes = bundle.stat().st_size
                payload = {
                    "source": source,
                    "snapshot_count": len(list(meta.get("snapshots") or [])),
                    "bundle_bytes": bundle_bytes,
                    "bundle_sha256": _sha256_file(bundle),
                }
                _upload_bundle(auth, source_id, payload, bundle)
                outcome.items.append(
                    SyncItem(
                        source_id=source_id,
                        key=entry.key,
                        display_name=Path(source.get("abspath") or exported.source).name
                        or entry.key,
                        snapshot_count=exported.snapshot_count,
                        bundle_bytes=bundle_bytes,
                        archived=entry.archived,
                    )
                )
        except Exception as exc:  # keep syncing independent sources
            outcome.failures.append(
                SyncFailure(
                    key=entry.key,
                    source_id="",
                    message=str(exc),
                )
            )
    return outcome


def pull_all(*, config: Optional[RuntimeConfig] = None) -> SyncOutcome:
    cfg = config or default_config()
    auth = load_auth(cfg)
    response = _request_json(
        "GET",
        auth.server_url,
        "/api/sources",
        token=auth.token,
        **auth.tls_kwargs(),
    )
    outcome = SyncOutcome(server_url=auth.server_url)
    for source in list(response.get("sources") or []):
        source_id = str(source.get("id") or "")
        target_key = remote_archive_key(source_id)
        try:
            with tempfile.TemporaryDirectory(prefix="snapz-pull-") as tmpdir:
                bundle = Path(tmpdir) / f"{source_id}.snapz"
                _download_bundle(auth, source_id, bundle)
                imported = api.import_bundle(
                    bundle,
                    config=cfg,
                    target_key=target_key,
                    overwrite=True,
                )
                outcome.items.append(
                    SyncItem(
                        source_id=source_id,
                        key=imported.key,
                        display_name=str(source.get("display_name") or source_id),
                        snapshot_count=imported.snapshot_count,
                        bundle_bytes=int(source.get("bundle_bytes") or bundle.stat().st_size),
                        archived=imported.archived,
                    )
                )
        except Exception as exc:
            outcome.failures.append(
                SyncFailure(
                    key=target_key,
                    source_id=source_id,
                    message=str(exc),
                )
            )
    return outcome


def remote_archive_key(source_id: str) -> str:
    safe = "".join(c for c in source_id if c.isalnum() or c in "_-")
    if not safe:
        safe = "unknown"
    return f"remote-{safe}"


def _export_entry_bundle(
    entry: DirEntry,
    bundle: Path,
    config: RuntimeConfig,
) -> api.BundleExportOutcome:
    if entry.archived:
        return api.export_bundle(
            entry.key,
            bundle,
            config=config,
            overwrite=True,
            archived=True,
        )
    return api.export_bundle(
        entry.meta.abspath,
        bundle,
        config=config,
        overwrite=True,
    )


def source_id_for(source: dict[str, Any]) -> str:
    marker = str(source.get("source_marker", "") or "")
    key = str(source.get("key", "") or source.get("origin_store_key", "") or "")
    raw = f"marker:{marker}" if marker else f"key:{key}"
    return "src_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def read_bundle_meta(bundle: Path) -> dict[str, Any]:
    with api._open_bundle_tar_reader(bundle) as tar:
        try:
            member = tar.getmember(api.BUNDLE_META_NAME)
        except KeyError as exc:
            raise ValueError(f"bundle missing {api.BUNDLE_META_NAME}") from exc
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"bundle member is not readable: {api.BUNDLE_META_NAME}")
        try:
            data = json.loads(extracted.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"bundle has invalid JSON: {api.BUNDLE_META_NAME}") from exc
    if not isinstance(data, dict):
        raise ValueError("bundle metadata must be an object")
    return data


def _normalize_server_url(server_url: str) -> str:
    url = server_url.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("server URL must look like http://host:port or https://host:port")
    return url


def _prepare_tls_options(
    server_url: str,
    *,
    tls_ca: str = "",
    tls_client_cert: str = "",
    tls_client_key: str = "",
) -> tuple[str, str, str]:
    tls_ca = _normalize_tls_path(tls_ca)
    tls_client_cert = _normalize_tls_path(tls_client_cert)
    tls_client_key = _normalize_tls_path(tls_client_key)
    if not (tls_ca or tls_client_cert or tls_client_key):
        return "", "", ""
    parsed = urlsplit(_normalize_server_url(server_url))
    if parsed.scheme != "https":
        raise ValueError("TLS options require an https:// server URL")
    if bool(tls_client_cert) != bool(tls_client_key):
        raise ValueError("both TLS client certificate and key are required")
    for label, path in (
        ("TLS CA", tls_ca),
        ("TLS client certificate", tls_client_cert),
        ("TLS client key", tls_client_key),
    ):
        if path and not Path(path).is_file():
            raise ValueError(f"{label} file not found: {path}")
    return tls_ca, tls_client_cert, tls_client_key


def _normalize_tls_path(value: str | os.PathLike[str] | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve(strict=False))


def _request_json(
    method: str,
    server_url: str,
    path: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    token: str = "",
    tls_ca: str = "",
    tls_client_cert: str = "",
    tls_client_key: str = "",
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, _headers, raw = _http_request(
        method,
        server_url,
        path,
        headers=headers,
        body=body,
        tls_ca=tls_ca,
        tls_client_cert=tls_client_cert,
        tls_client_key=tls_client_key,
    )
    if status >= 400:
        raise _remote_error(status, raw)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteError("remote returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RemoteError("remote returned non-object JSON")
    return data


def _upload_bundle(
    auth: RemoteAuth,
    source_id: str,
    metadata: dict[str, Any],
    bundle: Path,
) -> None:
    meta_raw = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {auth.token}",
        "Content-Type": "application/octet-stream",
        "X-Snapz-Source-Meta": base64.b64encode(meta_raw).decode("ascii"),
    }
    status, _headers, raw = _http_request(
        "PUT",
        auth.server_url,
        f"/api/sources/{source_id}/bundle",
        headers=headers,
        upload=bundle,
        **auth.tls_kwargs(),
    )
    if status >= 400:
        raise _remote_error(status, raw)


def _download_bundle(auth: RemoteAuth, source_id: str, destination: Path) -> None:
    headers = {"Authorization": f"Bearer {auth.token}"}
    status, _headers, raw = _http_request(
        "GET",
        auth.server_url,
        f"/api/sources/{source_id}/bundle",
        headers=headers,
        download=destination,
        **auth.tls_kwargs(),
    )
    if status >= 400:
        raise _remote_error(status, raw)


def _http_request(
    method: str,
    server_url: str,
    path: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    upload: Optional[Path] = None,
    download: Optional[Path] = None,
    tls_ca: str = "",
    tls_client_cert: str = "",
    tls_client_key: str = "",
) -> tuple[int, list[tuple[str, str]], bytes]:
    parsed = urlsplit(_normalize_server_url(server_url))
    request_path = _join_url_path(parsed.path, path)
    if parsed.scheme == "https":
        context = _make_ssl_context(
            tls_ca=tls_ca,
            tls_client_cert=tls_client_cert,
            tls_client_key=tls_client_key,
        )
        conn = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=60,
            context=context,
        )
    else:
        if tls_ca or tls_client_cert or tls_client_key:
            raise ValueError("TLS options require an https:// server URL")
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=60)
    try:
        if upload is not None:
            request_headers = dict(headers or {})
            request_headers["Content-Length"] = str(upload.stat().st_size)
            conn.putrequest(method, request_path)
            for name, value in request_headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            with open(upload, "rb") as src:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.send(chunk)
            response = conn.getresponse()
        else:
            conn.request(method, request_path, body=body, headers=headers or {})
            response = conn.getresponse()

        response_headers = response.getheaders()
        if download is not None and response.status < 400:
            download.parent.mkdir(parents=True, exist_ok=True)
            with open(download, "wb") as out:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
            raw = b""
        else:
            raw = response.read()
        return response.status, response_headers, raw
    except OSError as exc:
        raise RemoteError(str(exc)) from exc
    finally:
        conn.close()


def _make_ssl_context(
    *,
    tls_ca: str = "",
    tls_client_cert: str = "",
    tls_client_key: str = "",
) -> ssl.SSLContext:
    tls_ca = str(tls_ca or "").strip()
    tls_client_cert = str(tls_client_cert or "").strip()
    tls_client_key = str(tls_client_key or "").strip()
    if bool(tls_client_cert) != bool(tls_client_key):
        raise ValueError("both TLS client certificate and key are required")
    context = ssl.create_default_context(cafile=tls_ca or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if tls_client_cert:
        context.load_cert_chain(certfile=tls_client_cert, keyfile=tls_client_key)
    return context


def _join_url_path(base_path: str, api_path: str) -> str:
    base = base_path.rstrip("/")
    suffix = "/" + api_path.lstrip("/")
    return (base + suffix) or "/"


def _remote_error(status: int, raw: bytes) -> RemoteError:
    message = raw.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(message)
        if isinstance(data, dict) and data.get("error"):
            message = str(data["error"])
    except json.JSONDecodeError:
        pass
    return RemoteError(f"remote HTTP {status}: {message}", status=status)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def format_sync_item(item: SyncItem) -> str:
    state = "archive" if item.archived else "active"
    return (
        f"{item.display_name} ({item.snapshot_count} snapshot(s), "
        f"{format_size(item.bundle_bytes)}, {state})"
    )
