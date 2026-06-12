"""snapz-server metadata and auth helpers."""

from __future__ import annotations

from snapz_server import db


def test_auth_tokens_and_revoke_device(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")

    ctx, token = db.login_device(root, "acme", "alice", "secret", "laptop")
    assert ctx.tenant_name == "acme"
    assert ctx.username == "alice"
    assert db.authenticate_token(root, token).device_id == ctx.device_id

    assert db.revoke_device(root, ctx.device_id) is True
    assert db.authenticate_token(root, token) is None


def test_expired_auth_token_is_revoked(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx, token = db.login_device(root, "acme", "alice", "secret", "laptop")

    with db.connect(root) as con:
        con.execute(
            "UPDATE device_tokens SET created_at = ? WHERE device_id = ?",
            ("2000-01-01T00:00:00", ctx.device_id),
        )

    assert db.authenticate_token(root, token) is None
    with db.connect(root) as con:
        row = con.execute(
            "SELECT revoked_at FROM device_tokens WHERE device_id = ?",
            (ctx.device_id,),
        ).fetchone()
    assert row["revoked_at"]


def test_sources_are_tenant_scoped(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "tenant-a", "alice", "secret")
    db.create_user(root, "tenant-b", "bob", "secret")
    ctx_a, _ = db.login_device(root, "tenant-a", "alice", "secret", "a")
    ctx_b, _ = db.login_device(root, "tenant-b", "bob", "secret", "b")

    source = {"key": "same-local-key", "abspath": "/project"}
    source_id = db.source_id_for(source)
    db.upsert_source(
        root,
        ctx_a,
        source_id,
        source,
        snapshot_count=1,
        bundle_bytes=10,
    )
    db.upsert_source(
        root,
        ctx_b,
        source_id,
        source,
        snapshot_count=2,
        bundle_bytes=20,
    )

    sources_a = db.list_sources(root, ctx_a)
    sources_b = db.list_sources(root, ctx_b)
    assert [row["id"] for row in sources_a] == [source_id]
    assert [row["id"] for row in sources_b] == [source_id]
    assert sources_a[0]["snapshot_count"] == 1
    assert sources_b[0]["snapshot_count"] == 2


def test_init_db_migrates_compact_schema(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")

    with db.connect(root) as con:
        con.execute("DROP TABLE compact_jobs")
        con.execute("DROP TABLE cold_sources")
        for table in ("sources",):
            columns = {
                row["name"]
                for row in con.execute(f"PRAGMA table_info({table})")
            }
            assert "compact_status" in columns
        con.execute("ALTER TABLE sources RENAME TO sources_before_compact")
        con.executescript(
            """
            CREATE TABLE sources (
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
            INSERT INTO sources SELECT
                id, tenant_id, source_marker, origin_store_key, display_name,
                path_hint, snapshot_count, bundle_bytes, bundle_sha256,
                pushed_by_device, sync_status, sync_phase,
                sync_progress_percent, sync_bytes_sent, sync_bytes_total,
                sync_speed_bps, sync_eta_seconds, sync_started_at,
                sync_updated_at, sync_finished_at, last_sync_at, sync_error,
                sync_remote_only, updated_at
            FROM sources_before_compact;
            DROP TABLE sources_before_compact;
            """
        )

    db.init_db(root)

    with db.connect(root) as con:
        source_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(sources)")
        }
        assert "compact_status" in source_columns
        assert "compact_revision" in source_columns
        tables = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "compact_jobs" in tables
        assert "cold_sources" in tables
        assert "cold_chunks" in tables
        assert "cold_source_objects" in tables


def test_admin_source_helpers_update_name_and_delete_bundle(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx, _ = db.login_device(root, "acme", "alice", "secret", "laptop")

    source = {"key": "local-key", "abspath": "/work/project"}
    source_id = db.source_id_for(source)
    db.upsert_source(
        root,
        ctx,
        source_id,
        source,
        snapshot_count=3,
        bundle_bytes=128,
    )
    bundle = db.bundle_path(root, ctx.tenant_id, source_id)
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(b"bundle")

    sources = db.list_admin_sources(root)
    assert len(sources) == 1
    assert sources[0]["tenant"] == "acme"
    assert sources[0]["display_name"] == "project"
    assert sources[0]["pushed_by_username"] == "alice"
    assert sources[0]["pushed_by_device_name"] == "laptop"

    renamed = db.update_admin_source(
        root,
        ctx.tenant_id,
        source_id,
        display_name="production-image",
    )
    assert renamed["display_name"] == "production-image"

    assert db.delete_admin_source(root, ctx.tenant_id, source_id) is True
    assert bundle.exists() is False
    assert db.list_sources(root, ctx) == []
    assert db.delete_admin_source(root, ctx.tenant_id, source_id) is False


def test_upsert_source_creates_pending_compact_job(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx, _ = db.login_device(root, "acme", "alice", "secret", "laptop")

    source = {"key": "local-key", "abspath": "/work/project"}
    source_id = db.source_id_for(source)
    revision = "a" * 64
    db.upsert_source(
        root,
        ctx,
        source_id,
        source,
        snapshot_count=1,
        bundle_bytes=128,
        bundle_sha256=revision,
    )

    source_row = db.get_source(root, ctx, source_id)
    assert source_row["compact_status"] == "pending"
    assert source_row["compact_revision"] == revision
    job = db.get_compact_job(root, ctx.tenant_id, source_id, revision)
    assert job is not None
    assert job["status"] == "pending"


def test_source_sync_status_tracks_progress_and_last_sync(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx, _ = db.login_device(root, "acme", "alice", "secret", "laptop")
    source_id = "src_abc"

    db.update_source_sync_status(
        root,
        ctx,
        source_id,
        status="running",
        phase="uploading_delta",
        display_name="project",
        origin_store_key="local-key",
        bytes_sent=50,
        bytes_total=100,
        progress_percent=50,
        speed_bps=25,
        eta_seconds=2,
        remote_only=True,
    )

    row = db.list_admin_sources(root)[0]
    assert row["display_name"] == "project"
    assert row["sync_status"] == "running"
    assert row["sync_phase"] == "uploading_delta"
    assert row["sync_progress_percent"] == 50
    assert row["sync_remote_only"] == 1
    assert row["last_sync_at"] == ""

    db.update_source_sync_status(
        root,
        ctx,
        source_id,
        status="completed",
        phase="finished",
        bytes_sent=100,
        bytes_total=100,
        progress_percent=100,
    )
    row = db.list_admin_sources(root)[0]
    assert row["sync_status"] == "completed"
    assert row["sync_progress_percent"] == 100
    assert row["last_sync_at"]


def test_admin_source_rename_survives_next_push(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx, _ = db.login_device(root, "acme", "alice", "secret", "laptop")

    source = {"key": "local-key", "abspath": "/work/project"}
    source_id = db.source_id_for(source)
    db.upsert_source(
        root,
        ctx,
        source_id,
        source,
        snapshot_count=1,
        bundle_bytes=10,
    )
    db.update_admin_source(
        root,
        ctx.tenant_id,
        source_id,
        display_name="production-image",
    )
    db.upsert_source(
        root,
        ctx,
        source_id,
        source,
        snapshot_count=2,
        bundle_bytes=20,
    )

    source_row = db.get_source(root, ctx, source_id)
    assert source_row["display_name"] == "production-image"
    assert source_row["snapshot_count"] == 2


def test_admin_user_and_device_helpers(tmp_path):
    root = tmp_path / "server"
    user = db.create_user(root, "acme", "alice", "secret")
    ctx_a, token_a = db.login_device(root, "acme", "alice", "secret", "laptop")
    ctx_b, _ = db.login_device(root, "acme", "alice", "secret", "desktop")

    users = db.list_admin_users(root)
    assert len(users) == 1
    assert users[0]["username"] == "alice"
    assert users[0]["device_count"] == 2
    assert users[0]["active_device_count"] == 2

    devices = db.list_admin_devices(root, user_id=user["id"])
    assert {row["id"] for row in devices} == {ctx_a.device_id, ctx_b.device_id}

    db.update_user(root, user["id"], username="alice-renamed", disabled=True)
    updated = db.get_admin_user(root, user["id"])
    assert updated["username"] == "alice-renamed"
    assert updated["disabled"] == 1
    assert db.authenticate_token(root, token_a) is None

    assert db.reset_password_by_user_id(root, user["id"], "new-secret") is True
    db.update_user(root, user["id"], disabled=False)
    ctx_c, _ = db.login_device(root, "acme", "alice-renamed", "new-secret", "phone")
    assert ctx_c.username == "alice-renamed"

    assert db.revoke_user_devices(root, user["id"]) == 3
    assert db.revoke_user_devices(root, user["id"]) == 0

    assert db.delete_user(root, user["id"]) is True
    assert db.list_admin_devices(root, user_id=user["id"]) == []


def test_login_merges_duplicate_machine_devices(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx_old, token_old = db.login_device(root, "acme", "alice", "secret", "laptop")
    ctx_duplicate, token_duplicate = db.login_device(
        root,
        "acme",
        "alice",
        "secret",
        "laptop-again",
        machine_id="machine-1",
    )
    source = {"key": "local-key", "abspath": "/work/project"}
    source_id = db.source_id_for(source)
    db.upsert_source(
        root,
        ctx_duplicate,
        source_id,
        source,
        snapshot_count=1,
        bundle_bytes=10,
    )

    ctx, token = db.login_device(
        root,
        "acme",
        "alice",
        "secret",
        "laptop",
        machine_id="machine-1",
    )

    assert ctx.device_id == ctx_duplicate.device_id
    devices = db.list_admin_devices(root)
    assert [row["id"] for row in devices] == [ctx.device_id]
    assert devices[0]["machine_id"] == "machine-1"
    source_row = db.list_admin_sources(root)[0]
    assert source_row["pushed_by_device"] == ctx.device_id
    assert db.authenticate_token(root, token).device_id == ctx.device_id
    assert db.authenticate_token(root, token_duplicate).device_id == ctx.device_id
    assert db.authenticate_token(root, token_old).device_id == ctx.device_id


def test_login_matches_previous_machine_id_alias(tmp_path):
    root = tmp_path / "server"
    db.create_user(root, "acme", "alice", "secret")
    ctx_old, token_old = db.login_device(
        root,
        "acme",
        "alice",
        "secret",
        "laptop",
        machine_id="old-machine",
    )

    ctx_new, token_new = db.login_device(
        root,
        "acme",
        "alice",
        "secret",
        "laptop-renamed",
        machine_id="new-machine",
        machine_id_aliases=["old-machine"],
    )

    assert ctx_new.device_id == ctx_old.device_id
    devices = db.list_admin_devices(root)
    assert len(devices) == 1
    assert devices[0]["machine_id"] == "new-machine"
    assert devices[0]["name"] == "laptop-renamed"
    assert db.authenticate_token(root, token_old).device_id == ctx_new.device_id
    assert db.authenticate_token(root, token_new).device_id == ctx_new.device_id
