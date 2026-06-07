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
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlsplit

from snapz import api, cas
from snapz.config import META_SUFFIX, RuntimeConfig, default_config
from snapz.store import DirEntry, DirMeta, SnapshotMeta, Store
from snapz.util import now_iso
from snapz.util import format_size

REMOTE_CONFIG_FILENAME = "_remote.json"
REMOTE_SYNC_LOCK_FILENAME = "_remote_sync.lock"
CHUNK_SIZE = 1024 * 1024
SyncProgressCallback = Callable[[dict[str, Any]], None]
UploadProgressCallback = Callable[[int, int, float], None]


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


@contextmanager
def _remote_sync_lock(config: RuntimeConfig) -> Iterator[None]:
    Path(config.root).mkdir(parents=True, exist_ok=True)
    path = Path(config.root) / REMOTE_SYNC_LOCK_FILENAME
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "created": now_iso(),
        },
        separators=(",", ":"),
    ) + "\n"
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _remove_stale_remote_sync_lock(path):
                continue
            raise RemoteError(f"remote sync already running: {path}")
        break
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        yield
    finally:
        try:
            if path.read_text(encoding="utf-8") == payload:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_stale_remote_sync_lock(path: Path) -> bool:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return False
    pid = data.get("pid") if isinstance(data, dict) else None
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if os.name == "nt":
        return False
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        try:
            if path.read_text(encoding="utf-8") != raw:
                return False
        except OSError:
            return False
        path.unlink(missing_ok=True)
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


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


def push_all(
    *,
    config: Optional[RuntimeConfig] = None,
    progress: SyncProgressCallback | None = None,
    report_to_server: bool = True,
) -> SyncOutcome:
    cfg = config or default_config()
    auth = load_auth(cfg)
    store = Store(cfg)
    outcome = SyncOutcome(server_url=auth.server_url)
    try:
        with _remote_sync_lock(cfg):
            entries = store.list_all(include_archived=True)
            uploaded_keys: set[str] = set()
            uploaded_blobs: set[str] = set()
            for entry in entries:
                if not entry.snapshots:
                    continue
                if _is_pulled_remote_archive(entry):
                    continue
                source = _entry_source_payload(entry)
                source_id = source_id_for(source)
                display_name = Path(source.get("abspath") or entry.meta.abspath).name or entry.key
                try:
                    with tempfile.TemporaryDirectory(prefix="snapz-push-") as tmpdir:
                        bundle = Path(tmpdir) / f"{entry.key}.snapz"
                        _emit_progress(
                            progress,
                            phase="checking",
                            status="running",
                            source_id=source_id,
                            key=entry.key,
                            display_name=display_name,
                            remote_only=cfg.remote_only,
                            message="Checking remote state",
                        )
                        if report_to_server:
                            _report_sync_status(
                                auth,
                                source_id,
                                {
                                    "status": "running",
                                    "phase": "checking",
                                    "display_name": display_name,
                                    "key": entry.key,
                                    "remote_only": cfg.remote_only,
                                    "message": "Checking remote state",
                                },
                            )
                        remote_index = _get_remote_index(auth, source_id)
                        if remote_index is None:
                            _emit_progress(
                                progress,
                                phase="exporting",
                                status="running",
                                source_id=source_id,
                                key=entry.key,
                                display_name=display_name,
                                remote_only=cfg.remote_only,
                                message="Exporting source bundle",
                            )
                            exported = _export_entry_bundle(entry, bundle, cfg)
                            meta = read_bundle_meta(bundle)
                            delta = bundle
                            exported_delta = exported
                            delta_meta = meta
                        else:
                            local_index = _entry_local_index(store, entry, cfg)
                            missing_blobs = set(local_index["blobs"]) - set(
                                remote_index.get("blobs") or []
                            )
                            remote_snapshot_fingerprints = _remote_snapshot_fingerprints(
                                remote_index
                            )
                            local_snapshot_names = set(local_index["snapshot_names"])
                            local_snapshot_fingerprints = dict(local_index["fingerprints"])
                            missing_snapshots = {
                                name
                                for name in local_snapshot_names
                                if local_snapshot_fingerprints.get(name)
                                != remote_snapshot_fingerprints.get(name)
                            }
                            if not missing_snapshots and not missing_blobs:
                                completed = {
                                    "status": "completed",
                                    "phase": "skipped",
                                    "source_id": source_id,
                                    "key": entry.key,
                                    "display_name": display_name,
                                    "bytes_sent": 0,
                                    "bytes_total": 0,
                                    "progress_percent": 100.0,
                                    "speed_bps": 0.0,
                                    "eta_seconds": 0.0,
                                    "remote_only": cfg.remote_only,
                                    "message": "Already up to date",
                                }
                                _emit_progress(progress, **completed)
                                if report_to_server:
                                    _report_sync_status(auth, source_id, completed)
                                outcome.items.append(
                                    SyncItem(
                                        source_id=source_id,
                                        key=entry.key,
                                        display_name=display_name,
                                        snapshot_count=len(entry.snapshots),
                                        bundle_bytes=0,
                                        archived=entry.archived,
                                    )
                                )
                                uploaded_keys.add(entry.key)
                                uploaded_blobs.update(str(sha) for sha in local_index["blobs"])
                                continue
                            delta = Path(tmpdir) / f"{entry.key}.delta.snapz"
                            _emit_progress(
                                progress,
                                phase="exporting",
                                status="running",
                                source_id=source_id,
                                key=entry.key,
                                display_name=display_name,
                                remote_only=cfg.remote_only,
                                message="Exporting source delta",
                            )
                            exported_delta = _export_entry_bundle(
                                entry,
                                delta,
                                cfg,
                                include_blobs=missing_blobs,
                                include_snapshot_names=missing_snapshots,
                            )
                            delta_meta = read_bundle_meta(delta)
                            meta = delta_meta
                        upload_bytes = delta.stat().st_size
                        payload = {
                            "source": source,
                            "snapshot_count": len(list(delta_meta.get("snapshots") or [])),
                            "bundle_bytes": upload_bytes,
                            "bundle_sha256": _sha256_file(delta),
                        }
                        item_bundle_bytes = upload_bytes
                        try:
                            _upload_delta(
                                auth,
                                source_id,
                                payload,
                                delta,
                                on_progress=_source_upload_progress(
                                    progress,
                                    auth if report_to_server else None,
                                    source_id,
                                    entry.key,
                                    display_name,
                                    cfg.remote_only,
                                    "delta",
                                ),
                            )
                        except RemoteError as exc:
                            if exc.status != 404:
                                raise
                            _emit_progress(
                                progress,
                                phase="exporting",
                                status="running",
                                source_id=source_id,
                                key=entry.key,
                                display_name=display_name,
                                remote_only=cfg.remote_only,
                                message="Exporting source bundle",
                            )
                            exported = _export_entry_bundle(entry, bundle, cfg)
                            meta = read_bundle_meta(bundle)
                            bundle_payload = {
                                "source": source,
                                "snapshot_count": len(list(meta.get("snapshots") or [])),
                                "bundle_bytes": bundle.stat().st_size,
                                "bundle_sha256": _sha256_file(bundle),
                            }
                            item_bundle_bytes = bundle.stat().st_size
                            _upload_bundle(
                                auth,
                                source_id,
                                bundle_payload,
                                bundle,
                                on_progress=_source_upload_progress(
                                    progress,
                                    auth if report_to_server else None,
                                    source_id,
                                    entry.key,
                                    display_name,
                                    cfg.remote_only,
                                    "bundle",
                                ),
                            )
                        outcome.items.append(
                            SyncItem(
                                source_id=source_id,
                                key=entry.key,
                                display_name=display_name,
                                snapshot_count=exported_delta.snapshot_count,
                                bundle_bytes=item_bundle_bytes,
                                archived=entry.archived,
                            )
                        )
                        completed = {
                            "status": "completed",
                            "phase": "uploaded",
                            "source_id": source_id,
                            "key": entry.key,
                            "display_name": display_name,
                            "bytes_sent": item_bundle_bytes,
                            "bytes_total": item_bundle_bytes,
                            "progress_percent": 100.0,
                            "speed_bps": 0.0,
                            "eta_seconds": 0.0,
                            "remote_only": cfg.remote_only,
                            "message": "Upload complete",
                        }
                        _emit_progress(progress, **completed)
                        if report_to_server:
                            _report_sync_status(auth, source_id, completed)
                        uploaded_keys.add(entry.key)
                        uploaded_blobs.update(str(sha) for sha in _entry_referenced_blobs(store, entry))
                except Exception as exc:  # keep syncing independent sources
                    failed = {
                        "status": "failed",
                        "phase": "failed",
                        "source_id": source_id,
                        "key": entry.key,
                        "display_name": Path(entry.meta.abspath).name or entry.key,
                        "remote_only": cfg.remote_only,
                        "message": str(exc),
                    }
                    _emit_progress(progress, **failed)
                    if report_to_server and source_id:
                        _report_sync_status(auth, source_id, failed)
                    outcome.failures.append(
                        SyncFailure(
                            key=entry.key,
                            source_id=source_id,
                            message=str(exc),
                        )
                    )
            if cfg.remote_only and uploaded_blobs and outcome.ok:
                _evict_uploaded_blobs(store, uploaded_keys, uploaded_blobs)
    except RemoteError as exc:
        outcome.failures.append(SyncFailure(key="", source_id="", message=str(exc)))
    return outcome


def _entry_source_payload(entry: DirEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "abspath": entry.meta.abspath,
        "first_seen": entry.meta.first_seen,
        "last_used": entry.meta.last_used,
        "snapshot_count": len(entry.snapshots),
        "source_id": entry.meta.source_id,
        "source_marker": entry.meta.source_marker,
        "archived_at": entry.meta.archived_at,
    }


def _entry_referenced_blobs(store: Store, entry: DirEntry) -> set[str]:
    return cas.referenced_blobs(store.dir_by_key(entry.key))


def _entry_local_index(
    store: Store,
    entry: DirEntry,
    config: RuntimeConfig,
) -> dict[str, Any]:
    dir_root = store.dir_by_key(entry.key)
    blobs: set[str] = set()
    fingerprints: dict[str, str] = {}
    snapshot_names: list[str] = []
    for snap in entry.snapshots:
        meta_path = dir_root / f"{snap.name}{META_SUFFIX}"
        artifact = store.find_archive_in_dir(dir_root, snap.name)
        if artifact is None or not meta_path.exists():
            raise FileNotFoundError(
                f"snapshot {snap.name!r} is missing metadata or artifact"
            )
        name = str(snap.name)
        snapshot_names.append(name)
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta_data, dict):
            raise ValueError(f"snapshot metadata must be an object: {meta_path}")
        manifest_data: dict[str, Any] = {}
        if cas.is_manifest_artifact(artifact):
            manifest = cas.read_manifest(artifact)
            manifest_data = {
                "format_version": manifest.format_version,
                "snapshot": manifest.snapshot,
                "created": manifest.created,
                "entries": [item.to_dict() for item in manifest.entries],
            }
            blobs.update(cas.manifest_blob_refs(manifest))
        fingerprints[name] = _snapshot_fingerprint(meta_data, manifest_data)
    return {
        "blobs": blobs,
        "fingerprints": fingerprints,
        "snapshot_names": snapshot_names,
    }


def _source_bundle_sha256(source: dict[str, Any]) -> str:
    value = str(source.get("bundle_sha256") or "").strip().lower()
    if len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return ""


def _current_remote_archive(
    store: Store,
    target_key: str,
    source_id: str,
    bundle_sha256: str,
) -> DirEntry | None:
    if not bundle_sha256:
        return None
    entry = store.entry_by_key(target_key)
    if entry is None:
        return None
    if not _is_pulled_remote_archive(entry):
        return None
    if entry.meta.remote_source_id and entry.meta.remote_source_id != source_id:
        return None
    if entry.meta.remote_bundle_sha256 != bundle_sha256:
        return None
    return entry


def _mark_remote_archive_synced(
    config: RuntimeConfig,
    key: str,
    source_id: str,
    bundle_sha256: str,
) -> None:
    if not bundle_sha256:
        return
    store = Store(config)
    entry = store.entry_by_key(key)
    if entry is None:
        return
    folder = store.dir_by_key(key)
    meta = entry.meta
    meta.remote_source_id = source_id
    meta.remote_bundle_sha256 = bundle_sha256
    dir_meta = store._write_dir_meta_with_cached_summary(folder, meta)  # noqa: SLF001
    registry = store._load_registry()  # noqa: SLF001
    registry.setdefault("version", 1)
    registry.setdefault("dirs", {})[key] = store._registry_entry_for_meta(  # noqa: SLF001
        dir_meta
    )
    store._save_registry(registry)  # noqa: SLF001


def pull_all(*, config: Optional[RuntimeConfig] = None) -> SyncOutcome:
    cfg = config or default_config()
    auth = load_auth(cfg)
    store = Store(cfg)
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
            remote_bundle_sha256 = _source_bundle_sha256(source)
            current = _current_remote_archive(
                store,
                target_key,
                source_id,
                remote_bundle_sha256,
            )
            if current is not None:
                outcome.items.append(
                    SyncItem(
                        source_id=source_id,
                        key=current.key,
                        display_name=str(source.get("display_name") or source_id),
                        snapshot_count=len(current.snapshots),
                        bundle_bytes=int(source.get("bundle_bytes") or 0),
                        archived=current.archived,
                    )
                )
                continue
            index = _get_remote_index(auth, source_id)
            if index is not None:
                imported = _import_remote_index(
                    index,
                    config=cfg,
                    target_key=target_key,
                    source_summary=source,
                )
                outcome.items.append(
                    SyncItem(
                        source_id=source_id,
                        key=imported["key"],
                        display_name=str(source.get("display_name") or source_id),
                        snapshot_count=int(imported["snapshot_count"]),
                        bundle_bytes=int(source.get("bundle_bytes") or index.get("bundle_bytes") or 0),
                        archived=True,
                    )
                )
                continue
            with tempfile.TemporaryDirectory(prefix="snapz-pull-") as tmpdir:
                bundle = Path(tmpdir) / f"{source_id}.snapz"
                _download_bundle(auth, source_id, bundle)
                imported_bundle = api.import_bundle(
                    bundle,
                    config=cfg,
                    target_key=target_key,
                    overwrite=True,
                )
                _mark_remote_archive_synced(
                    cfg,
                    imported_bundle.key,
                    source_id,
                    remote_bundle_sha256,
                )
                outcome.items.append(
                    SyncItem(
                        source_id=source_id,
                        key=imported_bundle.key,
                        display_name=str(source.get("display_name") or source_id),
                        snapshot_count=imported_bundle.snapshot_count,
                        bundle_bytes=int(source.get("bundle_bytes") or bundle.stat().st_size),
                        archived=imported_bundle.archived,
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


def _is_pulled_remote_archive(entry: DirEntry) -> bool:
    return entry.key.startswith("remote-src_")


def _export_entry_bundle(
    entry: DirEntry,
    bundle: Path,
    config: RuntimeConfig,
    *,
    include_blobs: Optional[set[str]] = None,
    include_snapshot_names: Optional[set[str]] = None,
) -> api.BundleExportOutcome:
    if entry.archived:
        return api.export_bundle(
            entry.key,
            bundle,
            config=config,
            overwrite=True,
            archived=True,
            include_blobs=include_blobs,
            include_snapshot_names=include_snapshot_names,
        )
    return api.export_bundle(
        entry.meta.abspath,
        bundle,
        config=config,
        overwrite=True,
        include_blobs=include_blobs,
        include_snapshot_names=include_snapshot_names,
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


def _get_remote_index(auth: RemoteAuth, source_id: str) -> Optional[dict[str, Any]]:
    try:
        return _request_json(
            "GET",
            auth.server_url,
            f"/api/sources/{source_id}/index",
            token=auth.token,
            **auth.tls_kwargs(),
        )
    except RemoteError as exc:
        if exc.status == 404:
            return None
        raise


def _import_remote_index(
    index: dict[str, Any],
    *,
    config: RuntimeConfig,
    target_key: str,
    source_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = Store(config)
    source_data = dict(index.get("source") or {})
    source_summary = dict(source_summary or {})
    source_path = Path(str(source_data.get("abspath") or ".")).expanduser()
    target_key = _safe_store_key(target_key, remote_archive_key(source_id_for(source_data)))
    target_dir = store.dir_by_key(target_key)
    target_dir.mkdir(parents=True, exist_ok=True)
    cas.objects_root(target_dir).mkdir(parents=True, exist_ok=True)
    cas.snapshots_root(target_dir).mkdir(parents=True, exist_ok=True)
    cas.global_objects_root(store.root).mkdir(parents=True, exist_ok=True)

    existing_refs: list[str] = []
    for manifest_path in cas.iter_manifest_paths(target_dir):
        try:
            existing_refs.extend(cas.manifest_blob_refs(cas.read_manifest(manifest_path)))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue

    imported_refs: list[str] = []
    imported_names: list[str] = []
    for item in list(index.get("snapshots") or []):
        if not isinstance(item, dict):
            continue
        row = dict(item.get("row") or {})
        meta_data = dict(item.get("meta") or {})
        manifest_data = dict(item.get("manifest") or {})
        name = str(row.get("name") or meta_data.get("name") or "")
        if not name:
            continue
        snap = SnapshotMeta.from_dict(meta_data)
        snap.name = name
        snap.source = str(source_path)
        kind = str(row.get("kind") or "")
        if kind == "manifest":
            artifact_name = Path(str(row.get("artifact") or "")).name
            if not artifact_name:
                artifact_name = f"{name}{cas.MANIFEST_SUFFIX}"
            if artifact_name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX):
                artifact_path = cas.compressed_manifest_path(target_dir, name)
            else:
                artifact_path = cas.manifest_path(target_dir, name)
            manifest = cas.Manifest(
                format_version=int(manifest_data.get("format_version", cas.MANIFEST_FORMAT_VERSION)),
                snapshot=str(manifest_data.get("snapshot") or name),
                created=str(manifest_data.get("created") or snap.created),
                entries=[
                    cas.ManifestEntry.from_dict(entry)
                    for entry in manifest_data.get("entries", [])
                    if isinstance(entry, dict)
                ],
            )
            cas.write_manifest(artifact_path, manifest, zstd_level=config.zstd_level)
            snap.archive = artifact_path.name
            imported_refs.extend(cas.manifest_blob_refs(manifest))
        elif kind == "legacy":
            # Index-only remote mode is CAS-first. Legacy snapshots still
            # require the old full-bundle pull path.
            continue
        else:
            continue
        store.write_snapshot_meta_in_dir(target_dir, snap)
        imported_names.append(name)

    incoming_names = set(imported_names)
    for snap in store.list_snapshots_in_dir(target_dir):
        if snap.name in incoming_names:
            continue
        (target_dir / f"{snap.name}{META_SUFFIX}").unlink(missing_ok=True)
        cas.manifest_path(target_dir, snap.name).unlink(missing_ok=True)
        cas.compressed_manifest_path(target_dir, snap.name).unlink(missing_ok=True)

    dir_meta_obj = DirMeta(
        abspath=str(source_path),
        first_seen=str(source_data.get("first_seen") or now_iso()),
        last_used=now_iso(),
        source_id=str(source_data.get("source_id", "") or ""),
        source_marker=str(source_data.get("source_marker", "") or ""),
        archived_at=now_iso(),
        remote_source_id=str(source_summary.get("id") or ""),
        remote_bundle_sha256=_source_bundle_sha256(source_summary)
        or _source_bundle_sha256(index),
    )
    dir_meta = store._write_dir_meta_with_cached_summary(  # noqa: SLF001
        target_dir,
        dir_meta_obj,
    )
    registry = store._load_registry()  # noqa: SLF001
    registry.setdefault("version", 1)
    registry.setdefault("dirs", {})[target_key] = store._registry_entry_for_meta(  # noqa: SLF001
        dir_meta
    )
    store._save_registry(registry)  # noqa: SLF001
    if existing_refs:
        cas.decrement_refs(store.root, existing_refs)
    if imported_refs:
        cas.increment_refs(store.root, imported_refs)
    return {
        "key": target_key,
        "snapshot_count": len(imported_names),
    }


def _safe_store_key(raw: object, fallback: str) -> str:
    key = str(raw or "")
    if not key or "/" in key or "\\" in key or key in {".", ".."}:
        return fallback
    if any(part == ".." for part in key.split("-")):
        return fallback
    return key


def _snapshot_fingerprint(meta: dict[str, Any], manifest: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "meta": meta,
            "manifest": manifest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _remote_snapshot_fingerprints(index: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in list(index.get("snapshots") or []):
        if not isinstance(item, dict):
            continue
        row = dict(item.get("row") or {})
        name = str(row.get("name") or "")
        if not name:
            continue
        out[name] = _snapshot_fingerprint(
            dict(item.get("meta") or {}),
            dict(item.get("manifest") or {}),
        )
    return out


def _local_snapshot_fingerprints(bundle: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with api._open_bundle_tar_reader(bundle) as tar:
        meta = _tar_read_json(tar, api.BUNDLE_META_NAME)
        for row in list(meta.get("snapshots") or []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            meta_arc = str(row.get("meta") or "")
            artifact_arc = str(row.get("artifact") or "")
            if not name or not meta_arc or not artifact_arc:
                continue
            snap_meta = _tar_read_json(tar, meta_arc)
            manifest = (
                _tar_read_json(tar, artifact_arc)
                if str(row.get("kind") or "") == "manifest"
                else {}
            )
            out[name] = _snapshot_fingerprint(snap_meta, manifest)
    return out


def _tar_read_json(tar: tarfile.TarFile, name: str) -> dict[str, Any]:
    member = tar.getmember(name)
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError(f"bundle member is not readable: {name}")
    raw = extracted.read()
    if raw[:4] == cas._ZSTD_MAGIC:
        if cas._zstandard is None:  # noqa: SLF001
            raise ValueError(f"zstandard not installed; cannot read {name}")
        raw = cas._zstandard.ZstdDecompressor().decompress(raw)  # noqa: SLF001
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"bundle JSON member must be an object: {name}")
    return data


def _evict_uploaded_blobs(store: Store, uploaded_keys: set[str], shas: set[str]) -> None:
    if not shas:
        return
    pending_refs = _pending_local_refs(store, uploaded_keys)
    for sha in sorted(shas):
        if sha in pending_refs:
            continue
        for entry in store.list_all(include_archived=True):
            dir_root = store.dir_by_key(entry.key)
            for blob in cas.candidate_blob_paths(dir_root, sha):
                try:
                    blob.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    continue


def _pending_local_refs(store: Store, uploaded_keys: set[str]) -> set[str]:
    refs: set[str] = set()
    for entry in store.list_all(include_archived=True):
        if entry.key in uploaded_keys:
            continue
        refs.update(cas.referenced_blobs(store.dir_by_key(entry.key)))
    return refs


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


def _emit_progress(callback: SyncProgressCallback | None, **status: Any) -> None:
    if callback is None:
        return
    payload = dict(status)
    payload.setdefault("updated_at", now_iso())
    callback(payload)


def _report_sync_status(
    auth: RemoteAuth,
    source_id: str,
    status: dict[str, Any],
) -> None:
    if not source_id:
        return
    payload = {
        key: value
        for key, value in status.items()
        if key
        in {
            "status",
            "phase",
            "display_name",
            "key",
            "bytes_sent",
            "bytes_total",
            "progress_percent",
            "speed_bps",
            "eta_seconds",
            "remote_only",
            "message",
            "updated_at",
        }
    }
    try:
        _request_json(
            "POST",
            auth.server_url,
            f"/api/sources/{source_id}/sync-status",
            payload=payload,
            token=auth.token,
            **auth.tls_kwargs(),
        )
    except RemoteError as exc:
        if exc.status in {404, 405}:
            return
        return


def _source_upload_progress(
    callback: SyncProgressCallback | None,
    auth: RemoteAuth | None,
    source_id: str,
    key: str,
    display_name: str,
    remote_only: bool,
    kind: str,
) -> UploadProgressCallback:
    started = time.monotonic()
    last_report = 0.0
    last_percent = -1.0

    def emit(sent: int, total: int, _elapsed: float) -> None:
        nonlocal last_report, last_percent
        now = time.monotonic()
        elapsed = max(0.001, now - started)
        speed = sent / elapsed if sent > 0 else 0.0
        remaining = max(0, total - sent)
        eta = remaining / speed if speed > 0 else None
        percent = round((sent / total) * 100, 1) if total > 0 else 100.0
        should_report = (
            sent >= total
            or last_report == 0.0
            or now - last_report >= 0.5
            or percent - last_percent >= 2.0
        )
        if not should_report:
            return
        last_report = now
        last_percent = percent
        status = {
            "status": "running",
            "phase": f"uploading_{kind}",
            "source_id": source_id,
            "key": key,
            "display_name": display_name,
            "bytes_sent": sent,
            "bytes_total": total,
            "progress_percent": percent,
            "speed_bps": speed,
            "eta_seconds": eta,
            "remote_only": remote_only,
            "message": f"Uploading {kind}",
        }
        _emit_progress(callback, **status)
        if auth is not None:
            _report_sync_status(auth, source_id, status)

    return emit


def _upload_bundle(
    auth: RemoteAuth,
    source_id: str,
    metadata: dict[str, Any],
    bundle: Path,
    *,
    on_progress: UploadProgressCallback | None = None,
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
        on_upload_progress=on_progress,
        **auth.tls_kwargs(),
    )
    if status >= 400:
        raise _remote_error(status, raw)


def _upload_delta(
    auth: RemoteAuth,
    source_id: str,
    metadata: dict[str, Any],
    bundle: Path,
    *,
    on_progress: UploadProgressCallback | None = None,
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
        f"/api/sources/{source_id}/delta",
        headers=headers,
        upload=bundle,
        on_upload_progress=on_progress,
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


def download_object(
    source_id: str,
    sha: str,
    destination: Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> None:
    cfg = config or default_config()
    auth = load_auth(cfg)
    _download_object(auth, source_id, sha, destination)


def _download_object(
    auth: RemoteAuth,
    source_id: str,
    sha: str,
    destination: Path,
) -> None:
    headers = {"Authorization": f"Bearer {auth.token}"}
    status, _headers, raw = _http_request(
        "GET",
        auth.server_url,
        f"/api/sources/{source_id}/objects/{sha}",
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
    on_upload_progress: UploadProgressCallback | None = None,
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
            upload_size = upload.stat().st_size
            request_headers["Content-Length"] = str(upload_size)
            conn.putrequest(method, request_path)
            for name, value in request_headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            sent = 0
            started = time.monotonic()
            if on_upload_progress is not None:
                on_upload_progress(0, upload_size, 0.0)
            with open(upload, "rb") as src:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.send(chunk)
                    sent += len(chunk)
                    if on_upload_progress is not None:
                        on_upload_progress(sent, upload_size, time.monotonic() - started)
            response = conn.getresponse()
        else:
            conn.request(method, request_path, body=body, headers=headers or {})
            response = conn.getresponse()

        response_headers = response.getheaders()
        if download is not None and response.status < 400:
            download.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f"{download.name}.",
                suffix=".tmp",
                dir=download.parent,
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as out:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        out.write(chunk)
                os.replace(tmp, download)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
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
