"""SQLite metadata and auth helpers for ``snapz-server``."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from snapz.util import now_iso

DEFAULT_DATA_DIR = Path("~/.snapz-server").expanduser()
PBKDF2_ROUNDS = 210_000
DEVICE_OFFLINE_AFTER_HOURS = 24
TOKEN_TTL_DAYS = 90


@dataclass
class AuthContext:
    tenant_id: str
    tenant_name: str
    user_id: str
    username: str
    device_id: str = ""
    device_name: str = ""
    machine_id: str = ""


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
                bundle_sha256 TEXT NOT NULL DEFAULT '',
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
          path_hint, snapshot_count, bundle_bytes, bundle_sha256,
          pushed_by_device, updated_at
        )
        SELECT
          id, tenant_id, source_marker, origin_store_key, display_name,
          path_hint, snapshot_count, bundle_bytes, '',
          pushed_by_device, updated_at
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
        "bundle_sha256": "TEXT NOT NULL DEFAULT ''",
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
                machine_id TEXT NOT NULL DEFAULT '',
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                offline_at TEXT NOT NULL DEFAULT '',
                revoked_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS device_tokens (
                token_hash TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
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
        _migrate_devices_columns(con)
        _migrate_device_tokens(con)


def _migrate_devices_columns(con: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in con.execute("PRAGMA table_info(devices)")
    }
    if "offline_at" not in existing:
        con.execute("ALTER TABLE devices ADD COLUMN offline_at TEXT NOT NULL DEFAULT ''")
    if "machine_id" not in existing:
        con.execute("ALTER TABLE devices ADD COLUMN machine_id TEXT NOT NULL DEFAULT ''")


def _migrate_device_tokens(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS device_tokens (
            token_hash TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO device_tokens(
          token_hash, device_id, created_at, last_seen_at, revoked_at
        )
        SELECT token_hash, id, created_at, last_seen_at, revoked_at
        FROM devices
        WHERE token_hash != ''
        """
    )


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
    machine_id: str = "",
    machine_id_aliases: list[str] | tuple[str, ...] | None = None,
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
        ts = now_iso()
        machine_id = str(machine_id or "").strip()
        match_machine_ids = _machine_id_match_values(machine_id, machine_id_aliases)
        existing = None
        if machine_id:
            existing = _canonical_machine_device(
                con,
                tenant_id=tenant["id"],
                user_id=user["id"],
                machine_id=machine_id,
                match_machine_ids=match_machine_ids,
                device_name=device_name or "device",
            )
        else:
            existing = _canonical_named_device(
                con,
                tenant_id=tenant["id"],
                user_id=user["id"],
                device_name=device_name or "device",
            )
        if existing is not None:
            device_id = existing["id"]
            saved_name = device_name or existing["name"] or "device"
            con.execute(
                """
                UPDATE devices
                SET name = ?, last_seen_at = ?, offline_at = '', revoked_at = ''
                WHERE id = ?
                """,
                (
                    saved_name,
                    ts,
                    device_id,
                ),
            )
        else:
            device_id = _new_id("dev")
            saved_name = device_name or "device"
            con.execute(
                """
                INSERT INTO devices(
                  id, tenant_id, user_id, name, machine_id, token_hash,
                  created_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    tenant["id"],
                    user["id"],
                    saved_name,
                    machine_id,
                    hash_token(token),
                    ts,
                    ts,
                ),
            )
        con.execute(
            """
            INSERT INTO device_tokens(
              token_hash, device_id, created_at, last_seen_at, revoked_at
            )
            VALUES (?, ?, ?, ?, '')
            """,
            (hash_token(token), device_id, ts, ts),
        )
    return (
        AuthContext(
            tenant_id=tenant["id"],
            tenant_name=tenant["name"],
            user_id=user["id"],
            username=user["username"],
            device_id=device_id,
            device_name=saved_name,
            machine_id=machine_id,
        ),
        token,
    )


def _canonical_machine_device(
    con: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    machine_id: str,
    match_machine_ids: list[str],
    device_name: str,
) -> Optional[sqlite3.Row]:
    if not match_machine_ids:
        match_machine_ids = [machine_id]
    match_machine_id_set = set(match_machine_ids)
    candidates = list(
        con.execute(
            """
            SELECT * FROM devices
            WHERE tenant_id = ? AND user_id = ?
            """,
            (tenant_id, user_id),
        )
    )
    rows = [
        row
        for row in candidates
        if row["machine_id"] in match_machine_id_set
        or (row["machine_id"] == "" and row["name"] == device_name)
    ]
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    rows.sort(key=lambda row: row["last_seen_at"], reverse=True)
    rows.sort(key=lambda row: 0 if row["revoked_at"] == "" else 1)
    rows.sort(key=lambda row: 0 if row["machine_id"] == machine_id else 1)
    if not rows:
        return None
    canonical = rows[0]
    con.execute(
        "UPDATE devices SET machine_id = ? WHERE id = ?",
        (machine_id, canonical["id"]),
    )
    for row in rows[1:]:
        _merge_device(con, source_id=row["id"], target_id=canonical["id"])
    return con.execute(
        "SELECT * FROM devices WHERE id = ?",
        (canonical["id"],),
    ).fetchone()


def _machine_id_match_values(
    machine_id: str,
    aliases: list[str] | tuple[str, ...] | None,
) -> list[str]:
    values: list[str] = []
    for value in (machine_id, *(aliases or ())):
        clean = str(value or "").strip()
        if clean and clean not in values:
            values.append(clean)
    return values


def _canonical_named_device(
    con: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    device_name: str,
) -> Optional[sqlite3.Row]:
    rows = list(
        con.execute(
            """
            SELECT * FROM devices
            WHERE tenant_id = ? AND user_id = ?
              AND machine_id = '' AND name = ?
            ORDER BY
              CASE WHEN revoked_at = '' THEN 0 ELSE 1 END,
              datetime(last_seen_at) DESC,
              datetime(created_at) DESC
            """,
            (tenant_id, user_id, device_name),
        )
    )
    if not rows:
        return None
    canonical = rows[0]
    for row in rows[1:]:
        _merge_device(con, source_id=row["id"], target_id=canonical["id"])
    return con.execute(
        "SELECT * FROM devices WHERE id = ?",
        (canonical["id"],),
    ).fetchone()


def _merge_device(
    con: sqlite3.Connection,
    *,
    source_id: str,
    target_id: str,
) -> None:
    if source_id == target_id:
        return
    con.execute(
        "UPDATE device_tokens SET device_id = ? WHERE device_id = ?",
        (target_id, source_id),
    )
    con.execute(
        "UPDATE sources SET pushed_by_device = ? WHERE pushed_by_device = ?",
        (target_id, source_id),
    )
    con.execute(
        "UPDATE sync_logs SET device_id = ? WHERE device_id = ?",
        (target_id, source_id),
    )
    con.execute("DELETE FROM devices WHERE id = ?", (source_id,))


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
              d.id AS device_id, d.name AS device_name, d.machine_id AS machine_id,
              tok.created_at AS token_created_at
            FROM device_tokens tok
            JOIN devices d ON d.id = tok.device_id
            JOIN users u ON u.id = d.user_id
            JOIN tenants t ON t.id = d.tenant_id
            WHERE tok.token_hash = ?
              AND tok.revoked_at = ''
              AND d.revoked_at = ''
              AND u.disabled = 0
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        ts = now_iso()
        if _is_token_expired(row["token_created_at"], now=ts):
            con.execute(
                """
                UPDATE device_tokens
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at = ''
                """,
                (ts, token_hash),
            )
            return None
        con.execute(
            "UPDATE devices SET last_seen_at = ?, offline_at = '' WHERE id = ?",
            (ts, row["device_id"]),
        )
        con.execute(
            "UPDATE device_tokens SET last_seen_at = ? WHERE token_hash = ?",
            (ts, token_hash),
        )
        return AuthContext(
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            user_id=row["user_id"],
            username=row["username"],
            device_id=row["device_id"],
            device_name=row["device_name"],
            machine_id=row["machine_id"],
        )


def _is_token_expired(
    created_at: str,
    *,
    now: str | None = None,
    ttl_days: int = TOKEN_TTL_DAYS,
) -> bool:
    if ttl_days <= 0:
        return False
    try:
        created = datetime.fromisoformat(created_at)
        current = datetime.fromisoformat(now or now_iso())
    except (TypeError, ValueError):
        return True
    return created <= current - timedelta(days=ttl_days)


def revoke_device(data_dir: str | Path, device_id: str) -> bool:
    with connect(data_dir) as con:
        ts = now_iso()
        cur = con.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at = ''",
            (ts, device_id),
        )
        if cur.rowcount > 0:
            _revoke_device_tokens(con, device_id, ts)
            return True
        return False


def _revoke_device_tokens(con: sqlite3.Connection, device_id: str, ts: str) -> None:
    con.execute(
        """
        UPDATE device_tokens
        SET revoked_at = ?
        WHERE device_id = ? AND revoked_at = ''
        """,
        (ts, device_id),
    )


def _revoke_user_device_tokens(con: sqlite3.Connection, user_id: str, ts: str) -> None:
    con.execute(
        """
        UPDATE device_tokens
        SET revoked_at = ?
        WHERE revoked_at = ''
          AND device_id IN (
            SELECT id FROM devices WHERE user_id = ?
          )
        """,
        (ts, user_id),
    )


def mark_device_offline(data_dir: str | Path, ctx: AuthContext) -> bool:
    with connect(data_dir) as con:
        ts = now_iso()
        cur = con.execute(
            """
            UPDATE devices
            SET offline_at = ?
            WHERE id = ?
              AND tenant_id = ?
              AND user_id = ?
              AND revoked_at = ''
            """,
            (ts, ctx.device_id, ctx.tenant_id, ctx.user_id),
        )
        return cur.rowcount > 0


def unregister_device(data_dir: str | Path, ctx: AuthContext) -> bool:
    with connect(data_dir) as con:
        ts = now_iso()
        cur = con.execute(
            """
            UPDATE devices
            SET offline_at = ?, revoked_at = ?
            WHERE id = ?
              AND tenant_id = ?
              AND user_id = ?
            """,
            (ts, ts, ctx.device_id, ctx.tenant_id, ctx.user_id),
        )
        if cur.rowcount > 0:
            _revoke_device_tokens(con, ctx.device_id, ts)
            return True
        return False


def mark_stale_devices_offline(
    data_dir: str | Path,
    *,
    now: str | None = None,
    offline_after_hours: int = DEVICE_OFFLINE_AFTER_HOURS,
) -> int:
    init_db(data_dir)
    cutoff = _iso_minus_hours(now or now_iso(), offline_after_hours)
    with connect(data_dir) as con:
        cur = con.execute(
            """
            UPDATE devices
            SET offline_at = ?
            WHERE revoked_at = ''
              AND offline_at = ''
              AND last_seen_at <= ?
            """,
            (now or now_iso(), cutoff),
        )
        return cur.rowcount


def _iso_minus_hours(value: str, hours: int) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        dt = datetime.fromisoformat(now_iso())
    return (dt - timedelta(hours=hours)).replace(microsecond=0).isoformat()


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
                  COALESCE(SUM(
                    CASE WHEN d.revoked_at = '' AND d.offline_at = ''
                    THEN 1 ELSE 0 END
                  ), 0) AS active_device_count,
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
              COALESCE(SUM(
                CASE WHEN d.revoked_at = '' AND d.offline_at = ''
                THEN 1 ELSE 0 END
              ), 0) AS active_device_count,
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
                  s.bundle_sha256 AS bundle_sha256,
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
              s.bundle_sha256 AS bundle_sha256,
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
    bundle_sha256: str | None = None,
) -> sqlite3.Row:
    init_db(data_dir)
    with connect(data_dir) as con:
        if bundle_sha256 is None:
            cur = con.execute(
                """
                UPDATE sources
                SET snapshot_count = ?, bundle_bytes = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (snapshot_count, bundle_bytes, now_iso(), tenant_id, source_id),
            )
        else:
            cur = con.execute(
                """
                UPDATE sources
                SET snapshot_count = ?, bundle_bytes = ?, bundle_sha256 = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (
                    snapshot_count,
                    bundle_bytes,
                    bundle_sha256,
                    now_iso(),
                    tenant_id,
                    source_id,
                ),
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
    with connect(data_dir) as con:
        if user_id:
            return list(
                con.execute(
                    """
                    SELECT
                      d.id AS id,
                      d.tenant_id AS tenant_id,
                      t.name AS tenant,
                      d.user_id AS user_id,
                      u.username AS username,
                      d.name AS name,
                      d.machine_id AS machine_id,
                      d.created_at AS created_at,
                      d.last_seen_at AS last_seen_at,
                      d.offline_at AS offline_at,
                      d.revoked_at AS revoked_at
                    FROM devices d
                    JOIN tenants t ON t.id = d.tenant_id
                    JOIN users u ON u.id = d.user_id
                    WHERE d.user_id = ?
                    ORDER BY d.last_seen_at DESC, d.created_at DESC
                    """,
                    (user_id,),
                )
            )
        return list(
            con.execute(
                """
                SELECT
                  d.id AS id,
                  d.tenant_id AS tenant_id,
                  t.name AS tenant,
                  d.user_id AS user_id,
                  u.username AS username,
                  d.name AS name,
                  d.machine_id AS machine_id,
                  d.created_at AS created_at,
                  d.last_seen_at AS last_seen_at,
                  d.offline_at AS offline_at,
                  d.revoked_at AS revoked_at
                FROM devices d
                JOIN tenants t ON t.id = d.tenant_id
                JOIN users u ON u.id = d.user_id
                ORDER BY d.last_seen_at DESC, d.created_at DESC
                """
            )
        )


def revoke_user_devices(data_dir: str | Path, user_id: str) -> int:
    init_db(data_dir)
    with connect(data_dir) as con:
        ts = now_iso()
        cur = con.execute(
            """
            UPDATE devices
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at = ''
            """,
            (ts, user_id),
        )
        if cur.rowcount > 0:
            _revoke_user_device_tokens(con, user_id, ts)
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
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()
    return "src_" + digest[:24]


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
    bundle_sha256: str = "",
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
              path_hint, snapshot_count, bundle_bytes, bundle_sha256, pushed_by_device,
              sync_status, sync_phase, sync_progress_percent, sync_bytes_sent,
              sync_bytes_total, sync_speed_bps, sync_eta_seconds,
              sync_updated_at, sync_finished_at, last_sync_at, sync_error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, id) DO UPDATE SET
              source_marker = excluded.source_marker,
              origin_store_key = excluded.origin_store_key,
              display_name = excluded.display_name,
              path_hint = excluded.path_hint,
              snapshot_count = excluded.snapshot_count,
              bundle_bytes = excluded.bundle_bytes,
              bundle_sha256 = excluded.bundle_sha256,
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
                bundle_sha256,
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
