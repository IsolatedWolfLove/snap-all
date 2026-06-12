"""JSON serializers for snapz-server database rows."""

from __future__ import annotations

from typing import Any

from snapz_server import db
from snapz_server.server_config import PULL_MODE_CLIENT_BUNDLE, PULL_MODE_COLD


def _sync_status_dict(row: Any) -> dict[str, Any]:
    eta = row["sync_eta_seconds"]
    last_sync_at = row["last_sync_at"]
    if not last_sync_at and int(row["snapshot_count"] or 0) > 0:
        last_sync_at = row["updated_at"]
    return {
        "status": row["sync_status"] or "idle",
        "phase": row["sync_phase"],
        "progress_percent": float(row["sync_progress_percent"] or 0),
        "bytes_sent": int(row["sync_bytes_sent"] or 0),
        "bytes_total": int(row["sync_bytes_total"] or 0),
        "speed_bps": float(row["sync_speed_bps"] or 0),
        "eta_seconds": None if eta is None else float(eta),
        "started_at": row["sync_started_at"],
        "updated_at": row["sync_updated_at"],
        "finished_at": row["sync_finished_at"],
        "last_sync_at": last_sync_at,
        "error": row["sync_error"],
        "remote_only": bool(row["sync_remote_only"]),
    }


def ctx_dict(ctx: db.AuthContext) -> dict[str, str]:
    return {
        "tenant_id": ctx.tenant_id,
        "tenant": ctx.tenant_name,
        "user_id": ctx.user_id,
        "username": ctx.username,
        "device_id": ctx.device_id,
        "device_name": ctx.device_name,
    }


def admin_user_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "tenant": row["tenant"],
        "username": row["username"],
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "device_count": int(row["device_count"]),
        "active_device_count": int(row["active_device_count"]),
        "last_seen_at": row["last_seen_at"],
    }


def admin_device_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "tenant": row["tenant"],
        "user_id": row["user_id"],
        "username": row["username"],
        "name": row["name"],
        "machine_id": row["machine_id"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "offline_at": row["offline_at"],
        "offline": bool(row["offline_at"]) and not bool(row["revoked_at"]),
        "revoked_at": row["revoked_at"],
        "revoked": bool(row["revoked_at"]),
    }


def _compact_dict(
    row: Any,
    supported_pull_modes: tuple[str, ...],
) -> dict[str, Any]:
    status = row["compact_status"] or "legacy"
    return {
        "status": status,
        "revision": row["compact_revision"],
        "updated_at": row["compact_updated_at"],
        "raw_logical_bytes": int(row["raw_logical_bytes"] or 0),
        "cold_physical_bytes": int(row["cold_physical_bytes"] or 0),
        "supported_pull_modes": list(supported_pull_modes),
    }


def admin_source_dict(
    row: Any,
    *,
    supported_pull_modes: tuple[str, ...] = (PULL_MODE_COLD, PULL_MODE_CLIENT_BUNDLE),
) -> dict[str, Any]:
    sync_status = _sync_status_dict(row)
    compact_status = _compact_dict(row, supported_pull_modes)
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "tenant": row["tenant"],
        "source_marker": row["source_marker"],
        "origin_store_key": row["origin_store_key"],
        "display_name": row["display_name"],
        "path_hint": row["path_hint"],
        "snapshot_count": int(row["snapshot_count"]),
        "bundle_bytes": int(row["bundle_bytes"]),
        "bundle_sha256": row["bundle_sha256"],
        "pushed_by_device": row["pushed_by_device"],
        "pushed_by_device_name": row["pushed_by_device_name"],
        "pushed_by_user_id": row["pushed_by_user_id"],
        "pushed_by_username": row["pushed_by_username"],
        "updated_at": row["updated_at"],
        "last_sync_at": sync_status["last_sync_at"],
        "sync_status": sync_status,
        "compact_status": compact_status["status"],
        "compact": compact_status,
        "supported_pull_modes": compact_status["supported_pull_modes"],
    }


def row_dict(
    row: Any,
    *,
    supported_pull_modes: tuple[str, ...] = (PULL_MODE_COLD, PULL_MODE_CLIENT_BUNDLE),
) -> dict[str, Any]:
    sync_status = _sync_status_dict(row)
    compact_status = _compact_dict(row, supported_pull_modes)
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "source_marker": row["source_marker"],
        "origin_store_key": row["origin_store_key"],
        "display_name": row["display_name"],
        "path_hint": row["path_hint"],
        "snapshot_count": int(row["snapshot_count"]),
        "bundle_bytes": int(row["bundle_bytes"]),
        "bundle_sha256": row["bundle_sha256"],
        "pushed_by_device": row["pushed_by_device"],
        "updated_at": row["updated_at"],
        "last_sync_at": sync_status["last_sync_at"],
        "sync_status": sync_status,
        "compact_status": compact_status["status"],
        "compact": compact_status,
        "supported_pull_modes": compact_status["supported_pull_modes"],
    }
