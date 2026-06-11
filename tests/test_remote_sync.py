"""Remote server/client sync tests."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import threading
import http.client
from pathlib import Path

import pytest

from snapz import api, remote
from snapz import cas
from snapz.config import RuntimeConfig
from snapz_server import db
from snapz_server.app import make_server


def _start_server(data_dir: Path):
    server = make_server(data_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _start_tls_server(data_dir: Path, cert: Path, key: Path, client_ca: Path | None = None):
    server = make_server(
        data_dir,
        host="127.0.0.1",
        port=0,
        tls_certfile=str(cert),
        tls_keyfile=str(key),
        tls_client_ca=str(client_ca) if client_ca else None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"https://{host}:{port}"


def _run_openssl(*args: str) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl not installed")
    subprocess.run(
        ["openssl", *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_login_sends_stable_machine_id(config, monkeypatch):
    seen: dict[str, object] = {}

    def fake_request_json(method, server_url, path, **kwargs):
        seen["method"] = method
        seen["server_url"] = server_url
        seen["path"] = path
        seen["payload"] = kwargs["payload"]
        return {
            "token": "tok",
            "device": {"id": "dev_1", "name": "laptop"},
        }

    monkeypatch.setattr(remote, "machine_id", lambda: "machine-hash")
    monkeypatch.setattr(remote, "machine_id_aliases", lambda current=None: [])
    monkeypatch.setattr(remote, "_request_json", fake_request_json)

    auth = remote.login(
        "http://server.example",
        tenant="acme",
        username="alice",
        password="secret",
        device_name="laptop",
        config=config,
    )

    assert auth.device_id == "dev_1"
    assert seen["path"] == "/api/auth/login"
    assert seen["payload"]["machine_id"] == "machine-hash"
    assert seen["payload"]["machine_id_aliases"] == []


def test_machine_id_uses_stable_parts_without_hostname(monkeypatch):
    values = {
        "/etc/machine-id": "machine",
        "/var/lib/dbus/machine-id": "",
        "/sys/class/dmi/id/product_uuid": "product",
        "/sys/class/dmi/id/product_serial": "serial",
        "/sys/class/dmi/id/board_serial": "To be filled by O.E.M.",
        "/sys/class/dmi/id/chassis_serial": "",
    }

    def fake_read(path):
        return values.get(str(path), "")

    monkeypatch.setattr(remote, "_read_machine_id_file", fake_read)
    monkeypatch.setattr(remote.platform, "node", lambda: "old-host")
    first = remote.machine_id()
    monkeypatch.setattr(remote.platform, "node", lambda: "new-host")

    assert remote.machine_id() == first
    assert remote.machine_id_aliases(first) == [remote._machine_id_hash("machine")]


def test_logout_unregisters_remote_device_once(config, monkeypatch):
    auth = remote.RemoteAuth(
        server_url="http://server.example",
        tenant="acme",
        username="alice",
        token="tok",
        device_id="dev_1",
        device_name="laptop",
    )
    remote.save_auth(auth, config)
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def fake_request_json(method, server_url, path, **kwargs):
        calls.append((method, server_url, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(remote, "_request_json", fake_request_json)

    assert remote.logout(config) is True

    assert not remote.config_path(config).exists()
    assert len(calls) == 1
    method, server_url, path, kwargs = calls[0]
    assert (method, server_url, path) == (
        "POST",
        "http://server.example",
        "/api/auth/logout",
    )
    assert kwargs["token"] == "tok"


def _generate_self_signed_server_cert(tmp_path: Path) -> tuple[Path, Path]:
    cert = tmp_path / "server-cert.pem"
    key = tmp_path / "server-key.pem"
    config = tmp_path / "server-cert.cnf"
    config.write_text(
        "\n".join(
            [
                "[req]",
                "distinguished_name = dn",
                "x509_extensions = v3_req",
                "prompt = no",
                "[dn]",
                "CN = 127.0.0.1",
                "[v3_req]",
                "subjectAltName = IP:127.0.0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _run_openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-config",
        str(config),
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-days",
        "1",
        "-sha256",
    )
    return cert, key


def _generate_mutual_tls_certs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    ca_cert = tmp_path / "ca-cert.pem"
    ca_key = tmp_path / "ca-key.pem"
    server_cert = tmp_path / "mtls-server-cert.pem"
    server_key = tmp_path / "mtls-server-key.pem"
    server_csr = tmp_path / "mtls-server.csr"
    server_ext = tmp_path / "mtls-server.ext"
    client_cert = tmp_path / "client-cert.pem"
    client_key = tmp_path / "client-key.pem"
    client_csr = tmp_path / "client.csr"
    client_ext = tmp_path / "client.ext"

    _run_openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=snapz-test-ca",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_cert),
        "-days",
        "1",
        "-sha256",
    )
    _run_openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=127.0.0.1",
        "-keyout",
        str(server_key),
        "-out",
        str(server_csr),
    )
    server_ext.write_text(
        "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    _run_openssl(
        "x509",
        "-req",
        "-in",
        str(server_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(server_cert),
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(server_ext),
    )
    _run_openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=snapz-client",
        "-keyout",
        str(client_key),
        "-out",
        str(client_csr),
    )
    client_ext.write_text("extendedKeyUsage=clientAuth\n", encoding="utf-8")
    _run_openssl(
        "x509",
        "-req",
        "-in",
        str(client_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(client_cert),
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(client_ext),
    )
    return ca_cert, server_cert, server_key, client_cert, client_key


def test_push_pull_all_defaults_to_archive(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        active = tmp_path / "active-project"
        active.mkdir()
        (active / "README.md").write_text("active\n", encoding="utf-8")
        api.save(active, "v1", config=local_a)

        archived = tmp_path / "archived-project"
        archived.mkdir()
        (archived / "old.txt").write_text("archived\n", encoding="utf-8")
        api.save(archived, "v1", config=local_a)
        shutil.rmtree(archived)

        auth_a = remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert auth_a.device_id

        pushed = remote.push_all(config=local_a)
        assert pushed.ok
        assert len(pushed.items) == 2

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        assert len(pulled.items) == 2
        assert all(item.archived for item in pulled.items)

        archives = api.list_archives(config=local_b)
        assert len(archives) == 2
        assert all(entry.key.startswith("remote-src_") for entry in archives)

        active_archive = next(
            entry for entry in archives
            if Path(entry.meta.abspath).name == "active-project"
        )
        restored = tmp_path / "restored"
        api.restore_archive(active_archive.key, "v1", restored, config=local_b)
        assert (restored / "README.md").read_text(encoding="utf-8") == "active\n"

        adopted_path = tmp_path / "adopted-active"
        adopted_path.mkdir()
        adopted = api.adopt_archive(active_archive.key, adopted_path, config=local_b)
        assert adopted.archived is False
        assert [s.name for s in api.list_snapshots(adopted_path, config=local_b)] == ["v1"]
    finally:
        server.shutdown()
        server.server_close()


def test_remote_index_pull_and_on_demand_object_hydration(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a", remote_only=True)
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        pushed = remote.push_all(config=local_a)
        assert pushed.ok
        assert len(pushed.items) == 1

        dir_root_a = cas.global_objects_root(local_a.root)
        assert not list(dir_root_a.glob("*/*"))

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        assert len(pulled.items) == 1

        archives = api.list_archives(config=local_b)
        assert len(archives) == 1
        assert [snap.name for snap in archives[0].snapshots] == ["v1"]
        assert not list(cas.global_objects_root(local_b.root).glob("*/*"))

        restored = tmp_path / "restored"
        api.restore_archive(archives[0].key, "v1", restored, config=local_b)
        assert (restored / "README.md").read_text(encoding="utf-8") == "v1\n"
        assert list(cas.global_objects_root(local_b.root).glob("*/*"))
    finally:
        server.shutdown()
        server.server_close()


def test_pull_all_skips_remote_index_when_bundle_hash_unchanged(tmp_path, monkeypatch):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        first = remote.pull_all(config=local_b)
        assert first.ok

        original_http_request = remote._http_request
        index_requests: list[str] = []

        def recording_request(method, server_url, path, **kwargs):
            if method == "GET" and path.endswith("/index"):
                index_requests.append(path)
            return original_http_request(method, server_url, path, **kwargs)

        monkeypatch.setattr(remote, "_http_request", recording_request)

        second = remote.pull_all(config=local_b)

        assert second.ok
        assert len(second.items) == 1
        assert index_requests == []
    finally:
        server.shutdown()
        server.server_close()


def test_push_all_skips_pulled_remote_index_archives(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a", remote_only=True)
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        assert remote.pull_all(config=local_b).ok
        archives = api.list_archives(config=local_b)
        assert len(archives) == 1
        assert archives[0].key.startswith("remote-src_")
        assert not list(cas.global_objects_root(local_b.root).glob("*/*"))
        meta_path = local_b.root / archives[0].key / "_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["archived_at"] = ""
        meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

        pushed = remote.push_all(config=local_b)

        assert pushed.ok
        assert pushed.items == []
        assert pushed.failures == []
    finally:
        server.shutdown()
        server.server_close()


def test_remote_only_push_after_eviction_uploads_delta(tmp_path, monkeypatch):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    uploaded_sizes: list[tuple[str, int]] = []
    original_http_request = remote._http_request

    def recording_request(method, server_url, path, **kwargs):
        upload = kwargs.get("upload")
        if method == "PUT" and upload is not None:
            uploaded_sizes.append((path, Path(upload).stat().st_size))
        return original_http_request(method, server_url, path, **kwargs)

    monkeypatch.setattr(remote, "_http_request", recording_request)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a", remote_only=True)
        project = tmp_path / "project"
        project.mkdir()
        shared = "shared payload\n" * 200
        (project / "keep.txt").write_text(shared, encoding="utf-8")
        (project / "change.txt").write_text("one\n" * 200, encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok
        assert not list(cas.global_objects_root(local_a.root).glob("*/*"))
        first_upload = uploaded_sizes[-1]

        (project / "change.txt").write_text("two\n" * 200, encoding="utf-8")
        api.save(project, "v2", config=local_a)
        pushed = remote.push_all(config=local_a)
        assert pushed.ok
        second_upload = uploaded_sizes[-1]

        assert first_upload[0].endswith("/delta")
        assert second_upload[0].endswith("/delta")
        assert second_upload[1] < first_upload[1]
        assert not list(cas.global_objects_root(local_a.root).glob("*/*"))

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        assert remote.pull_all(config=local_b).ok
        archives = api.list_archives(config=local_b)
        restored = tmp_path / "restored-v2"
        api.restore_archive(archives[0].key, "v2", restored, config=local_b)
        assert (restored / "keep.txt").read_text(encoding="utf-8") == shared
        assert (restored / "change.txt").read_text(encoding="utf-8") == "two\n" * 200
    finally:
        server.shutdown()
        server.server_close()


def test_remote_only_push_failure_preserves_uploaded_source_blobs(tmp_path, monkeypatch):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local = RuntimeConfig(root=tmp_path / "client", remote_only=True)
        first = tmp_path / "first"
        first.mkdir()
        (first / "README.md").write_text("first\n", encoding="utf-8")
        api.save(first, "v1", config=local)

        second = tmp_path / "second"
        second.mkdir()
        (second / "README.md").write_text("second\n", encoding="utf-8")
        api.save(second, "v1", config=local)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop",
            config=local,
        )

        original_upload_delta = remote._upload_delta
        calls = 0

        def fail_second_delta(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise remote.RemoteError("interrupted upload")
            return original_upload_delta(*args, **kwargs)

        monkeypatch.setattr(remote, "_upload_delta", fail_second_delta)

        pushed = remote.push_all(config=local)

        assert not pushed.ok
        assert len(pushed.items) == 1
        assert len(pushed.failures) == 1
        assert list(cas.global_objects_root(local.root).glob("*/*"))

        monkeypatch.setattr(remote, "_upload_delta", original_upload_delta)
        retried = remote.push_all(config=local)

        assert retried.ok
        assert not list(cas.global_objects_root(local.root).glob("*/*"))
    finally:
        server.shutdown()
        server.server_close()


def test_push_all_reports_upload_progress(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    events: list[dict] = []
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        pushed = remote.push_all(config=local_a, progress=events.append)
        assert pushed.ok

        upload_events = [
            event for event in events if str(event.get("phase", "")).startswith("uploading_")
        ]
        assert upload_events
        assert upload_events[-1]["progress_percent"] == 100.0
        assert upload_events[-1]["bytes_sent"] == upload_events[-1]["bytes_total"]
        assert upload_events[-1]["speed_bps"] >= 0

        admin_source = db.list_admin_sources(server_root)[0]
        assert admin_source["sync_status"] == "completed"
        assert admin_source["last_sync_at"]
    finally:
        server.shutdown()
        server.server_close()


def test_second_push_uploads_only_missing_blobs(tmp_path, monkeypatch):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    uploaded_sizes: list[tuple[str, int]] = []
    original_http_request = remote._http_request

    def recording_request(method, server_url, path, **kwargs):
        upload = kwargs.get("upload")
        if method == "PUT" and upload is not None:
            uploaded_sizes.append((path, Path(upload).stat().st_size))
        return original_http_request(method, server_url, path, **kwargs)

    monkeypatch.setattr(remote, "_http_request", recording_request)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        project = tmp_path / "project"
        project.mkdir()
        shared = "shared payload\n" * 200
        (project / "keep.txt").write_text(shared, encoding="utf-8")
        (project / "change.txt").write_text("one\n" * 200, encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok
        first_upload = uploaded_sizes[-1]

        (project / "change.txt").write_text("two\n" * 200, encoding="utf-8")
        api.save(project, "v2", config=local_a)
        assert remote.push_all(config=local_a).ok
        second_upload = uploaded_sizes[-1]

        assert first_upload[0].endswith("/delta")
        assert second_upload[0].endswith("/delta")
        assert second_upload[1] < first_upload[1]

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        archives = api.list_archives(config=local_b)
        assert len(archives) == 1
        restored = tmp_path / "restored-v2"
        api.restore_archive(archives[0].key, "v2", restored, config=local_b)
        assert (restored / "keep.txt").read_text(encoding="utf-8") == shared
        assert (restored / "change.txt").read_text(encoding="utf-8") == "two\n" * 200
    finally:
        server.shutdown()
        server.server_close()


def test_push_delta_updates_overwritten_snapshot(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("first\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)

        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok

        (project / "README.md").write_text("second\n", encoding="utf-8")
        api.save(project, "v1", config=local_a, overwrite=True)
        assert remote.push_all(config=local_a).ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        assert remote.pull_all(config=local_b).ok
        archive = api.list_archives(config=local_b)[0]
        restored = tmp_path / "restored-overwrite"
        api.restore_archive(archive.key, "v1", restored, config=local_b)
        assert (restored / "README.md").read_text(encoding="utf-8") == "second\n"
    finally:
        server.shutdown()
        server.server_close()


def test_index_pull_replaces_previous_remote_index(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        project = tmp_path / "project"
        project.mkdir()
        (project / "README.md").write_text("v1\n", encoding="utf-8")
        api.save(project, "v1", config=local_a)
        (project / "README.md").write_text("v2\n", encoding="utf-8")
        api.save(project, "v2", config=local_a)

        auth_a = remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            config=local_a,
        )
        assert remote.push_all(config=local_a).ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            config=local_b,
        )
        assert remote.pull_all(config=local_b).ok
        assert remote.pull_all(config=local_b).ok
        archive = api.list_archives(config=local_b)[0]
        assert {snap.name for snap in archive.snapshots} == {"v1", "v2"}

        source_id = remote.source_id_for({
            "key": api.Store(local_a).key_for(project.resolve()),
            "source_marker": "",
        })
        ctx = db.authenticate_token(server_root, auth_a.token)
        assert ctx is not None
        row = db.get_source(server_root, ctx, source_id)
        assert row is not None
        bundle = db.bundle_path(server_root, row["tenant_id"], source_id)
        from snapz_server.bundles import delete_bundle_snapshot

        delete_bundle_snapshot(bundle, "v1")
        bundle_sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()
        db.upsert_source(
            server_root,
            ctx,
            source_id,
            remote.read_bundle_meta(bundle)["source"],
            snapshot_count=1,
            bundle_bytes=bundle.stat().st_size,
            bundle_sha256=bundle_sha256,
        )

        assert remote.pull_all(config=local_b).ok
        archive = api.list_archives(config=local_b)[0]
        assert [snap.name for snap in archive.snapshots] == ["v2"]
    finally:
        server.shutdown()
        server.server_close()


def test_push_pull_over_https_with_self_signed_cert(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    cert, key = _generate_self_signed_server_cert(tmp_path)

    server, url = _start_tls_server(server_root, cert, key)
    try:
        local_a = RuntimeConfig(root=tmp_path / "client-a")
        active = tmp_path / "active-project"
        active.mkdir()
        (active / "README.md").write_text("active\n", encoding="utf-8")
        api.save(active, "v1", config=local_a)

        auth_a = remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            tls_ca=str(cert),
            config=local_a,
        )
        assert auth_a.server_url.startswith("https://")
        assert auth_a.tls_ca == str(cert.resolve())

        pushed = remote.push_all(config=local_a)
        assert pushed.ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            tls_ca=str(cert),
            config=local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        assert len(pulled.items) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_push_pull_over_https_with_client_certificate(tmp_path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    ca_cert, server_cert, server_key, client_cert, client_key = (
        _generate_mutual_tls_certs(tmp_path)
    )

    server, url = _start_tls_server(server_root, server_cert, server_key, ca_cert)
    try:
        with pytest.raises(remote.RemoteError):
            remote.login(
                url,
                tenant="tenant-a",
                username="alice",
                password="secret",
                tls_ca=str(ca_cert),
                config=RuntimeConfig(root=tmp_path / "client-no-cert"),
            )

        local_a = RuntimeConfig(root=tmp_path / "client-a")
        active = tmp_path / "active-project"
        active.mkdir()
        (active / "README.md").write_text("active\n", encoding="utf-8")
        api.save(active, "v1", config=local_a)

        auth_a = remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-a",
            tls_ca=str(ca_cert),
            tls_client_cert=str(client_cert),
            tls_client_key=str(client_key),
            config=local_a,
        )
        assert auth_a.tls_client_cert == str(client_cert.resolve())
        assert auth_a.tls_client_key == str(client_key.resolve())

        pushed = remote.push_all(config=local_a)
        assert pushed.ok

        local_b = RuntimeConfig(root=tmp_path / "client-b")
        remote.login(
            url,
            tenant="tenant-a",
            username="alice",
            password="secret",
            device_name="laptop-b",
            tls_ca=str(ca_cert),
            tls_client_cert=str(client_cert),
            tls_client_key=str(client_key),
            config=local_b,
        )
        pulled = remote.pull_all(config=local_b)
        assert pulled.ok
        assert len(pulled.items) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_download_bundle_cleans_partial_file_on_stream_error(tmp_path, monkeypatch):
    destination = tmp_path / "download.snapz"

    class BrokenResponse:
        status = 200

        def getheaders(self):
            return []

        def read(self, _size):
            raise OSError("connection dropped")

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return BrokenResponse()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", FakeConnection)
    auth = remote.RemoteAuth(
        server_url="http://example.test",
        tenant="acme",
        username="alice",
        token="token",
        device_id="dev",
        device_name="device",
    )

    with pytest.raises(remote.RemoteError, match="connection dropped"):
        remote._download_bundle(auth, "src_abc", destination)

    assert not destination.exists()
    assert not list(tmp_path.glob("download.snapz.*.tmp"))
