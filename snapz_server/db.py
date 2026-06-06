"""SQLite metadata and auth helpers for ``snapz-server``."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from snapz.util import now_iso

DEFAULT_DATA_DIR = Path("~/.snapz-server").expanduser()
PBKDF2_ROUNDS = 210_000


@dataclass
class AuthContext:
    tenant_id: str
    tenant_name: str
    user_id: str
    username: str
    device_id: str = ""
    device_name: str = ""


def resolve_data_dir(path: str | Path | None = None) -> Path:
    raw = path or os.environ.get("SNAPZ_SERVER_DATA") or DEFAULT_DATA_DIR
    return Path(raw).expanduser().resolve()


def db_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "server.sqlite3"


def connect(data_dir: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(data_dir))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _create_sources_table_sql(table_name: str = "sources") -> str:
    return f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                source_marker TEXT NOT NULL DEFAULT '',
                origin_store_key TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                path_hint TEXT NOT NULL DEFAULT '',
                snapshot_count INTEGER NOT NULL DEFAULT 0,
                bundle_bytes INTEGER NOT NULL DEFAULT 0,
                pushed_by_device TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT '',
                sync_phase TEXT NOT NULL DEFAULT '',
                sync_progress_percent REAL NOT NULL DEFAULT 0,
                sync_bytes_sent INTEGER NOT NULL DEFAULT 0,
                sync_bytes_total INTEGER NOT NULL DEFAULT 0,
                sync_speed_bps REAL NOT NULL DEFAULT 0,
                sync_eta_seconds REAL,
                sync_started_at TEXT NOT NULL DEFAULT '',
                sync_updated_at TEXT NOT NULL DEFAULT '',
                sync_finished_at TEXT NOT NULL DEFAULT '',
                last_sync_at TEXT NOT NULL DEFAULT '',
                sync_error TEXT NOT NULL DEFAULT '',
                sync_remote_only INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, id)
            );
            """


def _migrate_sources_table(con: sqlite3.Connection) -> None:
    rows = list(con.execute("PRAGMA table_info(sources)"))
    pk_columns = [
        row["name"]
        for row in sorted((r for r in rows if r["pk"]), key=lambda r: r["pk"])
    ]
    if pk_columns == ["tenant_id", "id"]:
        _migrate_sources_columns(con)
        return
    con.execute("ALTER TABLE sources RENAME TO sources_old_pk")
    con.executescript(_create_sources_table_sql("sources"))
    con.execute(
        """
        INSERT OR REPLACE INTO sources(
          id, tenant_id, source_marker, origin_store_key, display_name,
          path_hint, snapshot_count, bundle_bytes, pushed_by_device, updated_at
        )
        SELECT
          id, tenant_id, source_marker, origin_store_key, display_name,
          path_hint, snapshot_count, bundle_bytes, pushed_by_device, updated_at
        FROM sources_old_pk
        """
    )
    con.execute("DROP TABLE sources_old_pk")
    _migrate_sources_columns(con)


def _migrate_sources_columns(con: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in con.execute("PRAGMA table_info(sources)")
    }
    migrations = {
        "sync_status": "TEXT NOT NULL DEFAULT ''",
        "sync_phase": "TEXT NOT NULL DEFAULT ''",
        "sync_progress_percent": "REAL NOT NULL DEFAULT 0",
        "sync_bytes_sent": "INTEGER NOT NULL DEFAULT 0",
        "sync_bytes_total": "INTEGER NOT NULL DEFAULT 0",
        "sync_speed_bps": "REAL NOT NULL DEFAULT 0",
        "sync_eta_seconds": "REAL",
        "sync_started_at": "TEXT NOT NULL DEFAULT ''",
        "sync_updated_at": "TEXT NOT NULL DEFAULT ''",
        "sync_finished_at": "TEXT NOT NULL DEFAULT ''",
        "last_sync_at": "TEXT NOT NULL DEFAULT ''",
        "sync_error": "TEXT NOT NULL DEFAULT ''",
        "sync_remote_only": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in migrations.items():
        if name not in existing:
            con.execute(f"ALTER TABLE sources ADD COLUMN {name} {definition}")


def init_db(data_dir: str | Path) -> None:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundles").mkdir(parents=True, exist_ok=True)
    with connect(root) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, username)
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT ''
            );
            """
            + _create_sources_table_sql()
            + """
            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                action TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _migrate_sources_table(con)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ROUNDS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_hex, digest_hex = stored.split("$", 3)
        rounds = int(rounds_s)
    except (ValueError, TypeError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_tenant(data_dir: str | Path, name: str) -> sqlite3.Row:
    init_db(data_dir)
    tenant_id = _new_id("ten")
    with connect(data_dir) as con:
        con.execute(
            "INSERT INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, name, now_iso()),
        )
        return con.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()


def get_tenant(data_dir: str | Path, name: str) -> Optional[sqlite3.Row]:
    with connect(data_dir) as con:
        return con.execute(
            "SELECT * FROM tenants WHERE name = ?", (name,)
        ).fetchone()


def create_user(
    data_dir: str | Path,
    tenant_name: str,
    username: str,
    password: str,
) -> sqlite3.Row:
    init_db(data_dir)
    tenant = get_tenant(data_dir, tenant_name)
    if tenant is None:
        tenant = create_tenant(data_dir, tenant_name)
    user_id = _new_id("usr")
    with connect(data_dir) as con:
        con.execute(
            """
            INSERT INTO users(id, tenant_id, username, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, tenant["id"], username, hash_password(password), now_iso()),
        )
        return con.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def reset_password(
    data_dir: str | Path,
    tenant_name: str,
    username: str,
    password: str,
) -> None:
    tenant = get_tenant(data_dir, tenant_name)
    if tenant is None:
        raise KeyError(f"tenant not found: {tenant_name}")
    with connect(data_dir) as con:
        cur = con.execute(
            """
            UPDATE users SET password_hash = ?
            WHERE tenant_id = ? AND username = ?
            """,
            (hash_password(password), tenant["id"], username),
        )
        if cur.rowcount == 0:
            raise KeyError(f"user not found: {tenant_name}/{username}")


def login_device(
    data_dir: str | Path,
    tenant_name: str,
    username: str,
    password: str,
    device_name: str,
) -> tuple[AuthContext, str]:
    tenant = get_tenant(data_dir, tenant_name)
    if tenant is None:
        raise PermissionError("invalid username or password")
    with connect(data_dir) as con:
        user = con.execute(
            """
            SELECT * FROM users
            WHERE tenant_id = ? AND username = ? AND disabled = 0
            """,
            (tenant["id"], username),
        ).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            raise PermissionError("invalid username or password")
        token = secrets.token_urlsafe(32)
        device_id = _new_id("dev")
        ts = now_iso()
        con.execute(
            """
            INSERT INTO devices(
              id, tenant_id, user_id, name, token_hash, created_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                tenant["id"],
                user["id"],
                device_name or "device",
                hash_token(token),
                ts,
                ts,
            ),
        )
    return (
        AuthContext(
            tenant_id=tenant["id"],
            tenant_name=tenant["name"],
            user_id=user["id"],
            username=user["username"],
            device_id=device_id,
            device_name=device_name or "device",
        ),
        token,
    )


def authenticate_token(data_dir: str | Path, token: str) -> Optional[AuthContext]:
    if not token:
        return None
    token_hash = hash_token(token)
    with connect(data_dir) as con:
        row = con.execute(
            """
            SELECT
              t.id AS tenant_id, t.name AS tenant_name,
              u.id AS user_id, u.username AS username,
              d.id AS device_id, d.name AS device_name
            FROM devices d
            JOIN users u ON u.id = d.user_id
            JOIN tenants t ON t.id = d.tenant_id
            WHERE d.token_hash = ? AND d.revoked_at = '' AND u.disabled = 0
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        con.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            (now_iso(), row["device_id"]),
        )
        return AuthContext(
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            user_id=row["user_id"],
            username=row["username"],
            device_id=row["device_id"],
            device_name=row["device_name"],
        )


def revoke_device(data_dir: str | Path, device_id: str) -> bool:
    with connect(data_dir) as con:
        cur = con.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at = ''",
            (now_iso(), device_id),
        )
        return cur.rowcount > 0


def list_admin_users(data_dir: str | Path) -> list[sqlite3.Row]:
    init_db(data_dir)
    with connect(data_dir) as con:
        return list(
            con.execute(
                """
                SELECT
                  t.id AS tenant_id,
                  t.name AS tenant,
                  u.id AS id,
                  u.username AS username,
                  u.disabled AS disabled,
                  u.created_at AS created_at,
                  COUNT(d.id) AS device_count,
                  COALESCE(SUM(CASE WHEN d.revoked_at = '' THEN 1 ELSE 0 END), 0)
                    AS active_device_count,
                  COALESCE(MAX(NULLIF(d.last_seen_at, '')), '') AS last_seen_at
                FROM users u
                JOIN tenants t ON t.id = u.tenant_id
                LEFT JOIN devices d ON d.user_id = u.id
                GROUP BY t.id, t.name, u.id, u.username, u.disabled, u.created_at
                ORDER BY t.name ASC, u.username ASC
                """
            )
        )


def get_admin_user(data_dir: str | Path, user_id: str) -> Optional[sqlite3.Row]:
    init_db(data_dir)
    with connect(data_dir) as con:
        return con.execute(
            """
            SELECT
              t.id AS tenant_id,
              t.name AS tenant,
              u.id AS id,
              u.username AS username,
              u.disabled AS disabled,
              u.created_at AS created_at,
              COUNT(d.id) AS device_count,
              COALESCE(SUM(CASE WHEN d.revoked_at = '' THEN 1 ELSE 0 END), 0)
                AS active_device_count,
              COALESCE(MAX(NULLIF(d.last_seen_at, '')), '') AS last_seen_at
            FROM users u
            JOIN tenants t ON t.id = u.tenant_id
            LEFT JOIN devices d ON d.user_id = u.id
            WHERE u.id = ?
            GROUP BY t.id, t.name, u.id, u.username, u.disabled, u.created_at
            """,
            (user_id,),
        ).fetchone()


def update_user(
    data_dir: str | Path,
    user_id: str,
    *,
    username: str | None = None,
    disabled: bool | None = None,
) -> sqlite3.Row:
    init_db(data_dir)
    current = get_admin_user(data_dir, user_id)
    if current is None:
        raise KeyError(f"user not found: {user_id}")

    next_username = current["username"] if username is None else username.strip()
    if not next_username:
        raise ValueError("username is required")
    next_disabled = int(bool(current["disabled"] if disabled is None else disabled))

    with connect(data_dir) as con:
        con.execute(
            """
            UPDATE users
            SET username = ?, disabled = ?
            WHERE id = ?
            """,
            (next_username, next_disabled, user_id),
        )

    updated = get_admin_user(data_dir, user_id)
    if updated is None:
        raise KeyError(f"user not found: {user_id}")
    return updated


def reset_password_by_user_id(
    data_dir: str | Path,
    user_id: str,
    password: str,
) -> bool:
    if not password:
        raise ValueError("password cannot be empty")
    init_db(data_dir)
    with connect(data_dir) as con:
        cur = con.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        return cur.rowcount > 0


def delete_user(data_dir: str | Path, user_id: str) -> bool:
    init_db(data_dir)
    with connect(data_dir) as con:
        cur = con.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def list_admin_sources(data_dir: str | Path) -> list[sqlite3.Row]:
    init_db(data_dir)
    with connect(data_dir) as con:
        return list(
            con.execute(
                """
                SELECT
                  s.id AS id,
                  s.tenant_id AS tenant_id,
                  t.name AS tenant,
                  s.source_marker AS source_marker,
                  s.origin_store_key AS origin_store_key,
                  s.display_name AS display_name,
                  s.path_hint AS path_hint,
                  s.snapshot_count AS snapshot_count,
                  s.bundle_bytes AS bundle_bytes,
                  s.pushed_by_device AS pushed_by_device,
                  s.sync_status AS sync_status,
                  s.sync_phase AS sync_phase,
                  s.sync_progress_percent AS sync_progress_percent,
                  s.sync_bytes_sent AS sync_bytes_sent,
                  s.sync_bytes_total AS sync_bytes_total,
                  s.sync_speed_bps AS sync_speed_bps,
                  s.sync_eta_seconds AS sync_eta_seconds,
                  s.sync_started_at AS sync_started_at,
                  s.sync_updated_at AS sync_updated_at,
                  s.sync_finished_at AS sync_finished_at,
                  s.last_sync_at AS last_sync_at,
                  s.sync_error AS sync_error,
                  s.sync_remote_only AS sync_remote_only,
                  COALESCE(d.name, '') AS pushed_by_device_name,
                  COALESCE(u.id, '') AS pushed_by_user_id,
                  COALESCE(u.username, '') AS pushed_by_username,
                  s.updated_at AS updated_at
                FROM sources s
                JOIN tenants t ON t.id = s.tenant_id
                LEFT JOIN devices d ON d.id = s.pushed_by_device
                LEFT JOIN users u ON u.id = d.user_id
                ORDER BY s.updated_at DESC, t.name ASC, s.display_name ASC
                """
            )
        )


def get_admin_source(
    data_dir: str | Path,
    tenant_id: str,
    source_id: str,
) -> Optional[sqlite3.Row]:
    init_db(data_dir)
    with connect(data_dir) as con:
        return con.execute(
            """
            SELECT
              s.id AS id,
              s.tenant_id AS tenant_id,
              t.name AS tenant,
              s.source_marker AS source_marker,
              s.origin_store_key AS origin_store_key,
              s.display_name AS display_name,
              s.path_hint AS path_hint,
              s.snapshot_count AS snapshot_count,
              s.bundle_bytes AS bundle_bytes,
              s.pushed_by_device AS pushed_by_device,
              s.sync_status AS sync_status,
              s.sync_phase AS sync_phase,
              s.sync_progress_percent AS sync_progress_percent,
              s.sync_bytes_sent AS sync_bytes_sent,
              s.sync_bytes_total AS sync_bytes_total,
              s.sync_speed_bps AS sync_speed_bps,
              s.sync_eta_seconds AS sync_eta_seconds,
              s.sync_started_at AS sync_started_at,
              s.sync_updated_at AS sync_updated_at,
              s.sync_finished_at AS sync_finished_at,
              s.last_sync_at AS last_sync_at,
              s.sync_error AS sync_error,
              s.sync_remote_only AS sync_remote_only,
              COALESCE(d.name, '') AS pushed_by_device_name,
              COALESCE(u.id, '') AS pushed_by_user_id,
              COALESCE(u.username, '') AS pushed_by_username,
              s.updated_at AS updated_at
            FROM sources s
            JOIN tenants t ON t.id = s.tenant_id
            LEFT JOIN devices d ON d.id = s.pushed_by_device
            LEFT JOIN users u ON u.id = d.user_id
            WHERE s.tenant_id = ? AND s.id = ?
            """,
            (tenant_id, source_id),
        ).fetchone()


def update_admin_source(
    data_dir: str | Path,
    tenant_id: str,
    source_id: str,
    *,
    display_name: str | None = None,
) -> sqlite3.Row:
    init_db(data_dir)
    current = get_admin_source(data_dir, tenant_id, source_id)
    if current is None:
        raise KeyError(f"source not found: {tenant_id}/{source_id}")

    next_display_name = (
        current["display_name"] if display_name is None else display_name.strip()
    )
    if not next_display_name:
        raise ValueError("display_name is required")

    with connect(data_dir) as con:
        con.execute(
            """
            UPDATE sources
            SET display_name = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (next_display_name, now_iso(), tenant_id, source_id),
        )

    updated = get_admin_source(data_dir, tenant_id, source_id)
    if updated is None:
        raise KeyError(f"source not found: {tenant_id}/{source_id}")
    return updated


def update_admin_source_bundle_stats(
    data_dir: str | Path,
    tenant_id: str,
    source_id: str,
    *,
    snapshot_count: int,
    bundle_bytes: int,
) -> sqlite3.Row:
    init_db(data_dir)
    with connect(data_dir) as con:
        cur = con.execute(
            """
            UPDATE sources
            SET snapshot_count = ?, bundle_bytes = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (snapshot_count, bundle_bytes, now_iso(), tenant_id, source_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"source not found: {tenant_id}/{source_id}")

    updated = get_admin_source(data_dir, tenant_id, source_id)
    if updated is None:
        raise KeyError(f"source not found: {tenant_id}/{source_id}")
    return updated


def delete_admin_source(
    data_dir: str | Path,
    tenant_id: str,
    source_id: str,
) -> bool:
    init_db(data_dir)
    current = get_admin_source(data_dir, tenant_id, source_id)
    if current is None:
        return False
    with connect(data_dir) as con:
        cur = con.execute(
            "DELETE FROM sources WHERE tenant_id = ? AND id = ?",
            (tenant_id, source_id),
        )
    if cur.rowcount <= 0:
        return False
    bundle_path(data_dir, tenant_id, source_id).unlink(missing_ok=True)
    return True


def list_admin_devices(
    data_dir: str | Path,
    *,
    user_id: str | None = None,
) -> list[sqlite3.Row]:
    init_db(data_dir)
    where = ""
    params: tuple[str, ...] = ()
    if user_id:
        where = "WHERE d.user_id = ?"
        params = (user_id,)
    with connect(data_dir) as con:
        return list(
            con.execute(
                f"""
                SELECT
                  d.id AS id,
                  d.tenant_id AS tenant_id,
                  t.name AS tenant,
                  d.user_id AS user_id,
                  u.username AS username,
                  d.name AS name,
                  d.created_at AS created_at,
                  d.last_seen_at AS last_seen_at,
                  d.revoked_at AS revoked_at
                FROM devices d
                JOIN tenants t ON t.id = d.tenant_id
                JOIN users u ON u.id = d.user_id
                {where}
                ORDER BY d.last_seen_at DESC, d.created_at DESC
                """,
                params,
            )
        )


def revoke_user_devices(data_dir: str | Path, user_id: str) -> int:
    init_db(data_dir)
    with connect(data_dir) as con:
        cur = con.execute(
            """
            UPDATE devices
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at = ''
            """,
            (now_iso(), user_id),
        )
        return cur.rowcount


def log_event(
    data_dir: str | Path,
    ctx: AuthContext,
    action: str,
    message: str,
) -> None:
    with connect(data_dir) as con:
        con.execute(
            """
            INSERT INTO sync_logs(tenant_id, device_id, action, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ctx.tenant_id, ctx.device_id, action, message, now_iso()),
        )


def source_id_for(source: dict) -> str:
    marker = str(source.get("source_marker", "") or "")
    key = str(source.get("key", "") or source.get("origin_store_key", "") or "")
    raw = f"marker:{marker}" if marker else f"key:{key}"
    return "src_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _source_display_name(source_id: str, path_hint: str, origin_store_key: str) -> str:
    return Path(path_hint).name or origin_store_key or source_id


def bundle_path(data_dir: str | Path, tenant_id: str, source_id: str) -> Path:
    return Path(data_dir) / "bundles" / tenant_id / f"{source_id}.snapz"


def upsert_source(
    data_dir: str | Path,
    ctx: AuthContext,
    source_id: str,
    source: dict,
    *,
    snapshot_count: int,
    bundle_bytes: int,
) -> None:
    timestamp = now_iso()
    path_hint = str(source.get("abspath", "") or "")
    origin_store_key = str(source.get("key", "") or "")
    default_display_name = _source_display_name(
        source_id,
        path_hint,
        origin_store_key,
    )
    display_name = default_display_name
    with connect(data_dir) as con:
        current = con.execute(
            """
            SELECT display_name, path_hint, origin_store_key
            FROM sources
            WHERE tenant_id = ? AND id = ?
            """,
            (ctx.tenant_id, source_id),
        ).fetchone()
        if current is not None:
            previous_default = _source_display_name(
                source_id,
                current["path_hint"],
                current["origin_store_key"],
            )
            if current["display_name"] and current["display_name"] != previous_default:
                display_name = current["display_name"]
        con.execute(
            """
            INSERT INTO sources(
              id, tenant_id, source_marker, origin_store_key, display_name,
              path_hint, snapshot_count, bundle_bytes, pushed_by_device,
              sync_status, sync_phase, sync_progress_percent, sync_bytes_sent,
              sync_bytes_total, sync_speed_bps, sync_eta_seconds,
              sync_updated_at, sync_finished_at, last_sync_at, sync_error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, id) DO UPDATE SET
              source_marker = excluded.source_marker,
              origin_store_key = excluded.origin_store_key,
              display_name = excluded.display_name,
              path_hint = excluded.path_hint,
              snapshot_count = excluded.snapshot_count,
              bundle_bytes = excluded.bundle_bytes,
              pushed_by_device = excluded.pushed_by_device,
              sync_status = excluded.sync_status,
              sync_phase = excluded.sync_phase,
              sync_progress_percent = excluded.sync_progress_percent,
              sync_bytes_sent = excluded.sync_bytes_sent,
              sync_bytes_total = excluded.sync_bytes_total,
              sync_speed_bps = excluded.sync_speed_bps,
              sync_eta_seconds = excluded.sync_eta_seconds,
              sync_updated_at = excluded.sync_updated_at,
              sync_finished_at = excluded.sync_finished_at,
              last_sync_at = excluded.last_sync_at,
              sync_error = excluded.sync_error,
              updated_at = excluded.updated_at
            """,
            (
                source_id,
                ctx.tenant_id,
                str(source.get("source_marker", "") or ""),
                origin_store_key,
                display_name,
                path_hint,
                snapshot_count,
                bundle_bytes,
                ctx.device_id,
                "completed",
                "finished",
                100.0,
                bundle_bytes,
                bundle_bytes,
                0.0,
                0.0,
                timestamp,
                timestamp,
                timestamp,
                "",
                timestamp,
            ),
        )


def update_source_sync_status(
    data_dir: str | Path,
    ctx: AuthContext,
    source_id: str,
    *,
    status: str,
    phase: str = "",
    display_name: str = "",
    origin_store_key: str = "",
    bytes_sent: int = 0,
    bytes_total: int = 0,
    progress_percent: float = 0.0,
    speed_bps: float = 0.0,
    eta_seconds: float | None = None,
    remote_only: bool = False,
    message: str = "",
) -> None:
    timestamp = now_iso()
    clean_status = status.strip()[:32] or "running"
    clean_phase = phase.strip()[:64]
    clean_display_name = display_name.strip()[:240]
    clean_origin_store_key = origin_store_key.strip()[:240]
    clean_message = message.strip()[:2000]
    progress = max(0.0, min(100.0, float(progress_percent or 0.0)))
    sent = max(0, int(bytes_sent or 0))
    total = max(0, int(bytes_total or 0))
    speed = max(0.0, float(speed_bps or 0.0))
    eta = None if eta_seconds is None else max(0.0, float(eta_seconds))
    finished_at = timestamp if clean_status in {"completed", "failed"} else ""
    last_sync_at = timestamp if clean_status == "completed" else ""
    if clean_status == "completed":
        progress = 100.0
        eta = 0.0
    with connect(data_dir) as con:
        current = con.execute(
            """
            SELECT display_name, path_hint, origin_store_key, last_sync_at
            FROM sources
            WHERE tenant_id = ? AND id = ?
            """,
            (ctx.tenant_id, source_id),
        ).fetchone()
        if current is None:
            con.execute(
                """
                INSERT INTO sources(
                  id, tenant_id, display_name, origin_store_key, path_hint,
                  pushed_by_device, sync_status, sync_phase,
                  sync_progress_percent, sync_bytes_sent, sync_bytes_total,
                  sync_speed_bps, sync_eta_seconds, sync_started_at,
                  sync_updated_at, sync_finished_at, last_sync_at,
                  sync_error, sync_remote_only, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    ctx.tenant_id,
                    clean_display_name or source_id,
                    clean_origin_store_key,
                    "",
                    ctx.device_id,
                    clean_status,
                    clean_phase,
                    progress,
                    sent,
                    total,
                    speed,
                    eta,
                    timestamp,
                    timestamp,
                    finished_at,
                    last_sync_at,
                    clean_message if clean_status == "failed" else "",
                    1 if remote_only else 0,
                    timestamp,
                ),
            )
            return
        next_display_name = current["display_name"]
        if clean_display_name and (
            not next_display_name or next_display_name == source_id
        ):
            next_display_name = clean_display_name
        next_origin_store_key = current["origin_store_key"] or clean_origin_store_key
        previous_last_sync_at = current["last_sync_at"] or ""
        con.execute(
            """
            UPDATE sources
            SET
              display_name = ?,
              origin_store_key = ?,
              pushed_by_device = ?,
              sync_status = ?,
              sync_phase = ?,
              sync_progress_percent = ?,
              sync_bytes_sent = ?,
              sync_bytes_total = ?,
              sync_speed_bps = ?,
              sync_eta_seconds = ?,
              sync_started_at = CASE
                WHEN sync_status != 'running' OR sync_started_at = '' THEN ?
                ELSE sync_started_at
              END,
              sync_updated_at = ?,
              sync_finished_at = CASE WHEN ? != '' THEN ? ELSE sync_finished_at END,
              last_sync_at = CASE WHEN ? != '' THEN ? ELSE ? END,
              sync_error = ?,
              sync_remote_only = ?,
              updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                next_display_name,
                next_origin_store_key,
                ctx.device_id,
                clean_status,
                clean_phase,
                progress,
                sent,
                total,
                speed,
                eta,
                timestamp,
                timestamp,
                finished_at,
                finished_at,
                last_sync_at,
                last_sync_at,
                previous_last_sync_at,
                clean_message if clean_status == "failed" else "",
                1 if remote_only else 0,
                timestamp,
                ctx.tenant_id,
                source_id,
            ),
        )


def list_sources(data_dir: str | Path, ctx: AuthContext) -> list[sqlite3.Row]:
    with connect(data_dir) as con:
        return list(con.execute(
            """
            SELECT * FROM sources
            WHERE tenant_id = ?
            ORDER BY updated_at DESC, display_name ASC
            """,
            (ctx.tenant_id,),
        ))


def get_source(
    data_dir: str | Path,
    ctx: AuthContext,
    source_id: str,
) -> Optional[sqlite3.Row]:
    with connect(data_dir) as con:
        return con.execute(
            """
            SELECT * FROM sources
            WHERE tenant_id = ? AND id = ?
            """,
            (ctx.tenant_id, source_id),
        ).fetchone()


def server_stats(data_dir: str | Path) -> dict:
    init_db(data_dir)
    with connect(data_dir) as con:
        tenants = con.execute("SELECT COUNT(*) AS n FROM tenants").fetchone()["n"]
        users = con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        devices = con.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
        sources = con.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        bundles = con.execute(
            "SELECT COALESCE(SUM(bundle_bytes), 0) AS n FROM sources"
        ).fetchone()["n"]
    return {
        "tenants": tenants,
        "users": users,
        "devices": devices,
        "sources": sources,
        "bundle_bytes": int(bundles or 0),
    }
