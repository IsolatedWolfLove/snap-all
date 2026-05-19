"""JSON serializers for snapz-server database rows."""

from __future__ import annotations

from typing import Any

from snapz_server import db


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
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "revoked_at": row["revoked_at"],
        "revoked": bool(row["revoked_at"]),
    }


def admin_source_dict(row: Any) -> dict[str, Any]:
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
        "pushed_by_device": row["pushed_by_device"],
        "pushed_by_device_name": row["pushed_by_device_name"],
        "pushed_by_user_id": row["pushed_by_user_id"],
        "pushed_by_username": row["pushed_by_username"],
        "updated_at": row["updated_at"],
    }


def row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "source_marker": row["source_marker"],
        "origin_store_key": row["origin_store_key"],
        "display_name": row["display_name"],
        "path_hint": row["path_hint"],
        "snapshot_count": int(row["snapshot_count"]),
        "bundle_bytes": int(row["bundle_bytes"]),
        "pushed_by_device": row["pushed_by_device"],
        "updated_at": row["updated_at"],
    }
