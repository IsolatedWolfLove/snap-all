"""snapz-server admin HTTP API tests."""

from __future__ import annotations

import json
import threading
import tarfile
import time
import base64
import hashlib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from snapz import api, cas, remote
from snapz import __version__
from snapz.config import RuntimeConfig
from snapz_server import db
from snapz_server.app import make_server
from snapz_server.routes import admin_source_snapshot_ref_from_path, safe_snapshot_name


def _start_server(
    data_dir: Path,
    *,
    admin_token: str = "admin-secret",
    **server_kwargs: Any,
):
    server = make_server(
        data_dir,
        host="127.0.0.1",
        port=0,
        admin_token=admin_token,
        **server_kwargs,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    token: str | None = "admin-secret",
    expect: int = 200,
) -> dict:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = Request(f"{base_url}{path}", data=body, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert resp.status == expect
            return data
    except HTTPError as exc:
        data = json.loads(exc.read().decode("utf-8"))
        assert exc.code == expect
        return data


def _text(base_url: str, path: str, *, expect: int = 200) -> str:
    req = Request(f"{base_url}{path}", method="GET")
    with urlopen(req, timeout=5) as resp:
        assert resp.status == expect
        return resp.read().decode("utf-8")


def _response_headers(
    base_url: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    token: str | None = "admin-secret",
) -> dict[str, str]:
    req = Request(f"{base_url}{path}", method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    with urlopen(req, timeout=5) as resp:
        return dict(resp.headers.items())


def _upload_bundle(
    base_url: str,
    source_id: str,
    body: bytes,
    metadata: dict,
    *,
    token: str,
    expect: int,
) -> dict:
    req = Request(
        f"{base_url}/api/sources/{source_id}/bundle",
        data=body,
        method="PUT",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header(
        "X-Snapz-Source-Meta",
        base64.b64encode(json.dumps(metadata).encode("utf-8")).decode("ascii"),
    )
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert resp.status == expect
            return data
    except HTTPError as exc:
        data = json.loads(exc.read().decode("utf-8"))
        assert exc.code == expect
        return data


@pytest.mark.parametrize("bad", [".", "..", ".hidden", "-snap", "_snap"])
def test_server_rejects_parent_directory_snapshot_names(bad):
    assert safe_snapshot_name(bad) == ""
    assert admin_source_snapshot_ref_from_path(
        f"/api/admin/sources/tenant/source/snapshots/{bad}"
    ) is None


def test_admin_api_requires_enabled_admin_token(tmp_path):
    server, url = _start_server(tmp_path / "server", admin_token="")
    try:
        assert "snapz-server Admin" in _text(url, "/admin")
        response = _json(url, "/api/admin/users", expect=403)
    finally:
        server.shutdown()
        server.server_close()

    assert "admin API disabled" in response["error"]


def test_admin_overview_and_ui_show_version(tmp_path):
    server, url = _start_server(tmp_path / "server")
    try:
        overview = _json(url, "/api/admin/overview")
        admin_html = _text(url, "/admin")
        dashboard = _text(url, "/")
    finally:
        server.shutdown()
        server.server_close()

    assert overview["version"] == __version__
    assert overview["server_version"].startswith("snapz-server/")
    assert "Version unknown" in admin_html
    assert f"<td>{__version__}</td>" in dashboard


def test_security_headers_and_cors_defaults(tmp_path):
    server, url = _start_server(tmp_path / "server")
    try:
        headers = _response_headers(
            url,
            "/api/admin/overview",
            headers={"Origin": "https://evil.example"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "Access-Control-Allow-Origin" not in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_cors_allows_explicit_origin_only(tmp_path):
    server, url = _start_server(
        tmp_path / "server",
        cors_origins=("https://admin.example",),
    )
    try:
        allowed = _response_headers(
            url,
            "/api/admin/overview",
            headers={"Origin": "https://admin.example"},
        )
        denied = _response_headers(
            url,
            "/api/admin/overview",
            headers={"Origin": "https://evil.example"},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert allowed["Access-Control-Allow-Origin"] == "https://admin.example"
    assert "Access-Control-Allow-Origin" not in denied


def test_login_rate_limits_repeated_failures(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "acme", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        payload = {
            "tenant": "acme",
            "username": "alice",
            "password": "wrong",
            "device_name": "laptop",
        }
        for _ in range(5):
            _json(url, "/api/auth/login", method="POST", payload=payload, token=None, expect=401)
        limited = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload=payload,
            token=None,
            expect=429,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "too many failed login attempts" in limited["error"]


def test_admin_api_manages_users_and_devices(tmp_path):
    server, url = _start_server(tmp_path / "server")
    try:
        response = _json(url, "/api/admin/users", token=None, expect=401)
        assert response["error"] == "invalid admin token"

        created = _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )
        user_id = created["user"]["id"]
        assert created["user"]["tenant"] == "acme"
        assert created["user"]["username"] == "alice"

        login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop",
            },
            token=None,
        )
        client_token = login["token"]

        devices = _json(url, f"/api/admin/users/{user_id}/devices")
        assert [device["name"] for device in devices["devices"]] == ["laptop"]
        device_id = devices["devices"][0]["id"]

        users = _json(url, "/api/admin/users")
        assert users["users"][0]["device_count"] == 1
        assert users["users"][0]["active_device_count"] == 1

        _json(url, f"/api/admin/devices/{device_id}/revoke", method="POST")
        _json(url, "/api/me", token=client_token, expect=401)

        _json(
            url,
            f"/api/admin/users/{user_id}/password",
            method="POST",
            payload={"password": "new-secret"},
        )
        _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "old-password",
            },
            token=None,
            expect=401,
        )
        new_login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "new-secret",
                "device_name": "phone",
            },
            token=None,
        )
        assert new_login["device"]["name"] == "phone"

        updated = _json(
            url,
            f"/api/admin/users/{user_id}",
            method="PATCH",
            payload={"username": "alice-renamed", "disabled": True},
        )
        assert updated["user"]["username"] == "alice-renamed"
        assert updated["user"]["disabled"] is True
        _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice-renamed",
                "password": "new-secret",
                "device_name": "disabled",
            },
            token=None,
            expect=401,
        )

        _json(url, f"/api/admin/users/{user_id}", method="DELETE")
        assert _json(url, "/api/admin/users")["users"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_admin_api_manages_pushed_sources(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )

        client = RuntimeConfig(root=tmp_path / "client")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("hello\n", encoding="utf-8")
        api.save(project, "v1", config=client)
        auth = remote.login(
            url,
            tenant="acme",
            username="alice",
            password="secret",
            device_name="laptop",
            config=client,
        )
        pushed = remote.push_all(config=client)
        assert pushed.ok
        assert len(pushed.items) == 1

        listed = _json(url, "/api/admin/sources")
        source = listed["sources"][0]
        assert source["tenant"] == "acme"
        assert source["display_name"] == "project"
        assert source["snapshot_count"] == 1
        assert len(source["bundle_sha256"]) == 64
        assert source["pushed_by_username"] == "alice"
        assert source["pushed_by_device_name"] == "laptop"

        bundle = db.bundle_path(server_root, source["tenant_id"], source["id"])
        assert bundle.is_file()
        assert source["bundle_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()

        renamed = _json(
            url,
            f"/api/admin/sources/{source['tenant_id']}/{source['id']}",
            method="PATCH",
            payload={"display_name": "production-image"},
        )
        assert renamed["source"]["display_name"] == "production-image"
        assert _json(url, "/api/sources", token=auth.token)["sources"][0][
            "display_name"
        ] == "production-image"
        assert _json(url, "/api/sources", token=auth.token)["sources"][0][
            "bundle_sha256"
        ] == source["bundle_sha256"]

        _json(
            url,
            f"/api/admin/sources/{source['tenant_id']}/{source['id']}",
            method="PATCH",
            payload={"display_name": ""},
            expect=400,
        )

        _json(
            url,
            f"/api/admin/sources/{source['tenant_id']}/{source['id']}",
            method="DELETE",
        )
        assert bundle.exists() is False
        assert _json(url, "/api/admin/sources")["sources"] == []
        assert _json(url, "/api/sources", token=auth.token)["sources"] == []
        _json(
            url,
            f"/api/sources/{source['id']}/bundle",
            token=auth.token,
            expect=404,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_logout_revokes_token_but_keeps_sources_and_reuses_machine_device(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )
        login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop",
                "machine_id": "machine-1",
            },
            token=None,
        )
        token = login["token"]
        device_id = login["device"]["id"]
        source = {"key": "local-key", "abspath": "/tmp/project"}
        source_id = db.source_id_for(source)
        ctx = db.authenticate_token(server_root, token)
        assert ctx is not None
        db.upsert_source(
            server_root,
            ctx,
            source_id,
            source,
            snapshot_count=1,
            bundle_bytes=123,
            bundle_sha256="a" * 64,
        )

        _json(url, "/api/auth/logout", method="POST", token=token)

        _json(url, "/api/me", token=token, expect=401)
        sources = _json(url, "/api/admin/sources")["sources"]
        assert [item["id"] for item in sources] == [source_id]
        devices = _json(url, "/api/admin/devices")["devices"]
        assert devices[0]["id"] == device_id
        assert devices[0]["revoked"] is True

        relogin = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop-again",
                "machine_id": "machine-1",
            },
            token=None,
        )

        assert relogin["device"]["id"] == device_id
        assert relogin["device"]["name"] == "laptop-again"
        _json(url, "/api/me", token=token, expect=401)
        assert _json(url, "/api/me", token=relogin["token"])["user"]["device_id"] == device_id
        devices = _json(url, "/api/admin/devices")["devices"]
        assert len(devices) == 1
        assert devices[0]["revoked"] is False
        assert devices[0]["offline"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_stale_device_goes_offline_but_token_stays_valid(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "acme", "alice", "secret")
    ctx, token = db.login_device(
        server_root,
        "acme",
        "alice",
        "secret",
        "laptop",
        machine_id="machine-1",
    )
    with db.connect(server_root) as con:
        con.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00", ctx.device_id),
        )

    assert db.mark_stale_devices_offline(server_root, now="2020-01-02T01:00:00") == 1
    devices = db.list_admin_devices(server_root)
    assert devices[0]["offline_at"] == "2020-01-02T01:00:00"
    assert devices[0]["revoked_at"] == ""

    assert db.authenticate_token(server_root, token) is not None
    devices = db.list_admin_devices(server_root)
    assert devices[0]["offline_at"] == ""


def test_server_background_sweeper_marks_stale_device_offline(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "acme", "alice", "secret")
    ctx, token = db.login_device(
        server_root,
        "acme",
        "alice",
        "secret",
        "laptop",
        machine_id="machine-1",
    )
    with db.connect(server_root) as con:
        con.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00", ctx.device_id),
        )

    server = make_server(
        server_root,
        host="127.0.0.1",
        port=0,
        device_sweep_interval_seconds=0.01,
    )
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            devices = db.list_admin_devices(server_root)
            if devices[0]["offline_at"]:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("stale device was not marked offline")

        assert db.authenticate_token(server_root, token) is not None
    finally:
        server.server_close()


def test_bundle_upload_requires_hash_and_size_limit(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root, max_bundle_bytes=8)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )
        login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop",
            },
            token=None,
        )

        source = {"key": "local-key", "abspath": "/tmp/project"}
        source_id = db.source_id_for(source)
        body = b"not a real bundle"
        metadata = {"source": source}
        response = _upload_bundle(
            url,
            source_id,
            body,
            metadata,
            token=login["token"],
            expect=413,
        )
        assert "too large" in response["error"]
    finally:
        server.shutdown()
        server.server_close()

    server, url = _start_server(tmp_path / "server-hash")
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )
        login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop",
            },
            token=None,
        )
        source = {"key": "local-key", "abspath": "/tmp/project"}
        source_id = db.source_id_for(source)
        body = b"not a real bundle"
        response = _upload_bundle(
            url,
            source_id,
            body,
            {"source": source},
            token=login["token"],
            expect=400,
        )
        assert "bundle_sha256" in response["error"]

        response = _upload_bundle(
            url,
            source_id,
            body,
            {
                "source": source,
                "bundle_sha256": "0" * 64,
            },
            token=login["token"],
            expect=400,
        )
        assert "sha256" in response["error"]

        response = _upload_bundle(
            url,
            source_id,
            body,
            {
                "source": source,
                "bundle_sha256": hashlib.sha256(body).hexdigest(),
            },
            token=login["token"],
            expect=400,
        )
        assert response["error"]
        assert not list((tmp_path / "server-hash" / "bundles").rglob("*.snapz"))
    finally:
        server.shutdown()
        server.server_close()


def test_client_can_report_source_sync_status(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )
        login = _json(
            url,
            "/api/auth/login",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
                "device_name": "laptop",
            },
            token=None,
        )

        source_id = "src_status"
        reported = _json(
            url,
            f"/api/sources/{source_id}/sync-status",
            method="POST",
            payload={
                "status": "running",
                "phase": "uploading_delta",
                "display_name": "project",
                "key": "local-key",
                "bytes_sent": 64,
                "bytes_total": 128,
                "progress_percent": 50,
                "speed_bps": 32,
                "eta_seconds": 2,
                "remote_only": True,
            },
            token=login["token"],
        )
        assert reported["ok"] is True

        source = _json(url, "/api/admin/sources")["sources"][0]
        assert source["id"] == source_id
        assert source["display_name"] == "project"
        assert source["sync_status"]["status"] == "running"
        assert source["sync_status"]["phase"] == "uploading_delta"
        assert source["sync_status"]["progress_percent"] == 50
        assert source["sync_status"]["speed_bps"] == 32
        assert source["sync_status"]["eta_seconds"] == 2
        assert source["sync_status"]["remote_only"] is True
        assert source["last_sync_at"] == ""

        user_source = _json(url, "/api/sources", token=login["token"])["sources"][0]
        assert user_source["sync_status"]["bytes_sent"] == 64
    finally:
        server.shutdown()
        server.server_close()


def test_admin_api_manages_pushed_source_snapshots(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )

        client = RuntimeConfig(root=tmp_path / "client")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=client)
        (project / "README.md").write_text("v2\n", encoding="utf-8")
        api.save(project, "v2", config=client)
        auth = remote.login(
            url,
            tenant="acme",
            username="alice",
            password="secret",
            device_name="laptop",
            config=client,
        )
        pushed = remote.push_all(config=client)
        assert pushed.ok

        source = _json(url, "/api/admin/sources")["sources"][0]
        tenant_id = source["tenant_id"]
        source_id = source["id"]

        listed = _json(
            url,
            f"/api/admin/sources/{tenant_id}/{source_id}/snapshots?per_page=1",
        )
        assert listed["total"] == 2
        assert listed["per_page"] == 1
        assert listed["has_next"] is True
        assert listed["snapshots"][0]["name"] == "v2"
        assert listed["memory"]["required_bytes"] > 0

        renamed = _json(
            url,
            f"/api/admin/sources/{tenant_id}/{source_id}/snapshots/v2",
            method="PATCH",
            payload={"name": "release-2"},
        )
        assert renamed["snapshot_count"] == 2
        assert renamed["source"]["snapshot_count"] == 2

        names = [
            item["name"]
            for item in _json(
                url,
                f"/api/admin/sources/{tenant_id}/{source_id}/snapshots",
            )["snapshots"]
        ]
        assert names == ["release-2", "v1"]

        bundle = db.bundle_path(server_root, tenant_id, source_id)
        with api._open_bundle_tar_reader(bundle) as tar:
            assert "source/v2.meta.json" not in tar.getnames()
            assert "source/release-2.meta.json" in tar.getnames()
            manifest = tar.extractfile(
                tar.getmember(f"source/snapshots/release-2{cas.MANIFEST_SUFFIX}")
            )
            assert manifest is not None
            assert json.loads(manifest.read().decode("utf-8"))["snapshot"] == "release-2"

        deleted = _json(
            url,
            f"/api/admin/sources/{tenant_id}/{source_id}/snapshots/v1",
            method="DELETE",
        )
        assert deleted["deleted_source"] is False
        assert deleted["snapshot_count"] == 1
        assert _json(url, "/api/admin/sources")["sources"][0]["snapshot_count"] == 1

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.save_auth(
                remote.RemoteAuth(
                    server_url=url,
                    tenant="acme",
                    username="alice",
                    token=auth.token,
                    device_id=auth.device_id,
                    device_name=auth.device_name,
                ),
            local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        archives = api.list_archives(config=local_b)
        assert len(archives) == 1
        assert [snap.name for snap in archives[0].snapshots] == ["release-2"]

        deleted_last = _json(
            url,
            f"/api/admin/sources/{tenant_id}/{source_id}/snapshots/release-2",
            method="DELETE",
        )
        assert deleted_last["deleted_source"] is True
        assert _json(url, "/api/admin/sources")["sources"] == []
        assert _json(url, "/api/sources", token=auth.token)["sources"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_admin_delete_snapshot_preserves_chunked_blobs_for_remaining_snapshot(tmp_path):
    server_root = tmp_path / "server"
    server, url = _start_server(server_root)
    try:
        _json(
            url,
            "/api/admin/users",
            method="POST",
            payload={
                "tenant": "acme",
                "username": "alice",
                "password": "secret",
            },
            expect=201,
        )

        client = RuntimeConfig(
            root=tmp_path / "client",
            use_zstd=False,
            use_file_cache=False,
            chunk_file_bytes=128 * 1024,
            chunk_min_bytes=32 * 1024,
            chunk_avg_bytes=64 * 1024,
            chunk_max_bytes=128 * 1024,
        )
        project = tmp_path / "project"
        project.mkdir()
        payload = bytearray(
            (b"alpha" * 70000) + (b"beta" * 70000) + (b"gamma" * 70000)
        )
        (project / "big.bin").write_bytes(payload)
        api.save(project, "v1", config=client)
        payload[len(payload) // 2: len(payload) // 2 + 4] = b"EDIT"
        (project / "big.bin").write_bytes(payload)
        api.save(project, "v2", config=client)
        expected = bytes(payload)

        auth = remote.login(
            url,
            tenant="acme",
            username="alice",
            password="secret",
            device_name="laptop",
            config=client,
        )
        assert remote.push_all(config=client).ok
        source = _json(url, "/api/admin/sources")["sources"][0]
        tenant_id = source["tenant_id"]
        source_id = source["id"]

        deleted = _json(
            url,
            f"/api/admin/sources/{tenant_id}/{source_id}/snapshots/v1",
            method="DELETE",
        )
        assert deleted["snapshot_count"] == 1

        pulled_store = RuntimeConfig(root=tmp_path / "pulled")
        remote.save_auth(
            remote.RemoteAuth(
                server_url=url,
                tenant="acme",
                username="alice",
                token=auth.token,
                device_id=auth.device_id,
                device_name=auth.device_name,
            ),
            pulled_store,
        )
        pulled = remote.pull_all(config=pulled_store)
        assert pulled.ok
        archive_entry = api.list_archives(config=pulled_store)[0]
        restored = tmp_path / "restored"
        api.restore_archive(archive_entry.key, "v2", restored, config=pulled_store)

        assert (restored / "big.bin").read_bytes() == expected
    finally:
        server.shutdown()
        server.server_close()
