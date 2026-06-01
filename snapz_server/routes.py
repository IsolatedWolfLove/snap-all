"""Path parsing helpers for the snapz-server HTTP API."""

from __future__ import annotations


def source_id_from_bundle_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "sources"] or parts[3] != "bundle":
        return ""
    value = parts[2]
    if not value or any(not (c.isalnum() or c in "_-") for c in value):
        return ""
    return value


def admin_user_id_from_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:3] != ["api", "admin", "users"]:
        return ""
    return safe_id(parts[3])


def admin_user_id_from_password_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "admin", "users"]:
        return ""
    if parts[4] != "password":
        return ""
    return safe_id(parts[3])


def admin_user_id_from_devices_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "admin", "users"]:
        return ""
    if parts[4] != "devices":
        return ""
    return safe_id(parts[3])


def admin_user_id_from_revoke_devices_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 6 or parts[:3] != ["api", "admin", "users"]:
        return ""
    if parts[4:] != ["devices", "revoke"]:
        return ""
    return safe_id(parts[3])


def admin_device_id_from_revoke_path(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "admin", "devices"]:
        return ""
    if parts[4] != "revoke":
        return ""
    return safe_id(parts[3])


def admin_source_ref_from_path(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "admin", "sources"]:
        return None
    tenant_id = safe_id(parts[3])
    source_id = safe_id(parts[4])
    if not tenant_id or not source_id:
        return None
    return tenant_id, source_id


def admin_source_snapshots_ref_from_path(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 6 or parts[:3] != ["api", "admin", "sources"]:
        return None
    if parts[5] != "snapshots":
        return None
    tenant_id = safe_id(parts[3])
    source_id = safe_id(parts[4])
    if not tenant_id or not source_id:
        return None
    return tenant_id, source_id


def admin_source_snapshot_ref_from_path(path: str) -> tuple[str, str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 7 or parts[:3] != ["api", "admin", "sources"]:
        return None
    if parts[5] != "snapshots":
        return None
    tenant_id = safe_id(parts[3])
    source_id = safe_id(parts[4])
    snapshot_name = safe_snapshot_name(parts[6])
    if not tenant_id or not source_id or not snapshot_name:
        return None
    return tenant_id, source_id, snapshot_name


def safe_snapshot_name(value: str) -> str:
    if not value or not value[0].isalnum():
        return ""
    if any(not (c.isalnum() or c in "_.-") for c in value):
        return ""
    return value


def is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def safe_id(value: str) -> str:
    if not value or any(not (c.isalnum() or c in "_-") for c in value):
        return ""
    return value
