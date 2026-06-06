"""Remote server/client sync tests."""

from __future__ import annotations

import shutil
import subprocess
import threading
import http.client
from pathlib import Path

import pytest

from snapz import api, remote
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
