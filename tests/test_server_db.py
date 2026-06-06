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
