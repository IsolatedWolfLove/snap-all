"""Cold compaction tests for snapz-server."""

from __future__ import annotations

import threading
import io
from pathlib import Path

import pytest

from snapz import api, cas, remote
from snapz.config import RuntimeConfig
from snapz_server import db
from snapz_server.app import make_server
from snapz_server.compact import CompactError, compact_source
from snapz_server.server_config import CompactConfig


class _FakeZstdCompressor:
    def __init__(self, level: int = 3, **_kwargs):
        self.level = level

    def compress(self, raw: bytes) -> bytes:
        return cas._ZSTD_MAGIC + raw  # noqa: SLF001

    def stream_writer(self, fileobj, **_kwargs):
        return _FakeZstdWriter(fileobj)


class _FakeZstdWriter:
    def __init__(self, fileobj):
        self.fileobj = fileobj
        self.buffer = io.BytesIO()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def write(self, data: bytes) -> int:
        return self.buffer.write(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if self.buffer is None:
            return
        raw = self.buffer.getvalue()
        self.fileobj.write(cas._ZSTD_MAGIC + raw)  # noqa: SLF001
        self.buffer = None


class _FakeZstdDecompressor:
    def decompress(self, raw: bytes) -> bytes:
        if not raw.startswith(cas._ZSTD_MAGIC):  # noqa: SLF001
            raise ValueError("not fake zstd")
        return raw[len(cas._ZSTD_MAGIC):]  # noqa: SLF001

    def stream_reader(self, fileobj):
        return io.BytesIO(self.decompress(fileobj.read()))


class _FakeZstd:
    ZstdCompressor = _FakeZstdCompressor
    ZstdDecompressor = _FakeZstdDecompressor


def _start_server(data_dir: Path):
    server = make_server(data_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _push_single_snapshot(tmp_path: Path):
    server_root = tmp_path / "server"
    db.create_user(server_root, "tenant-a", "alice", "secret")
    server, url = _start_server(server_root)
    local = RuntimeConfig(
        root=tmp_path / "client-a",
        use_zstd=False,
        chunk_file_bytes=128 * 1024,
        chunk_min_bytes=32 * 1024,
        chunk_avg_bytes=64 * 1024,
        chunk_max_bytes=128 * 1024,
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("cold compact\n", encoding="utf-8")
    (project / "big.bin").write_bytes((b"alpha" * 70000) + (b"beta" * 70000))
    api.save(project, "v1", config=local)
    auth = remote.login(
        url,
        tenant="tenant-a",
        username="alice",
        password="secret",
        device_name="laptop-a",
        config=local,
    )
    pushed = remote.push_all(config=local)
    assert pushed.ok
    source_id = pushed.items[0].source_id
    ctx = db.authenticate_token(server_root, auth.token)
    assert ctx is not None
    source = db.get_source(server_root, ctx, source_id)
    assert source is not None
    return server, url, server_root, auth, ctx, source_id, source


def test_compact_requires_zstandard_when_unavailable(tmp_path):
    if cas._zstandard is not None:  # noqa: SLF001
        pytest.skip("zstandard is installed")
    server, _url, server_root, _auth, ctx, source_id, source = _push_single_snapshot(tmp_path)
    try:
        with pytest.raises(CompactError, match="zstandard"):
            compact_source(
                server_root,
                tenant_id=ctx.tenant_id,
                source_id=source_id,
                revision=source["bundle_sha256"],
                config=CompactConfig(
                    chunk_file_bytes=64 * 1024,
                    chunk_min_bytes=16 * 1024,
                    chunk_avg_bytes=32 * 1024,
                    chunk_max_bytes=64 * 1024,
                    pack_target_bytes=128 * 1024,
                ),
            )
        job = db.get_compact_job(
            server_root,
            ctx.tenant_id,
            source_id,
            source["bundle_sha256"],
        )
        assert job is not None
        assert job["status"] == "failed"
    finally:
        server.shutdown()
        server.server_close()


def test_compact_complete_cold_pull_restores_byte_for_byte(tmp_path):
    if cas._zstandard is None:  # noqa: SLF001
        pytest.skip("zstandard not installed")
    server, url, server_root, auth, ctx, source_id, source = _push_single_snapshot(tmp_path)
    try:
        result = compact_source(
            server_root,
            tenant_id=ctx.tenant_id,
            source_id=source_id,
            revision=source["bundle_sha256"],
            config=CompactConfig(
                chunk_file_bytes=64 * 1024,
                chunk_min_bytes=16 * 1024,
                chunk_avg_bytes=32 * 1024,
                chunk_max_bytes=64 * 1024,
                pack_target_bytes=128 * 1024,
            ),
        )
        assert result.object_count >= 2
        assert result.chunk_count >= result.object_count
        compacted = db.get_source(server_root, ctx, source_id)
        assert compacted is not None
        assert compacted["compact_status"] == "complete"
        assert db.get_complete_cold_source(server_root, ctx.tenant_id, source_id) is not None

        pulled_store = RuntimeConfig(root=tmp_path / "client-b", pull_transfer_mode="cold")
        remote.save_auth(
            remote.RemoteAuth(
                server_url=url,
                tenant="tenant-a",
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
        restored = tmp_path / "restored-cold"
        api.restore_archive(archive_entry.key, "v1", restored, config=pulled_store)
        assert (restored / "README.md").read_text(encoding="utf-8") == "cold compact\n"
        assert (restored / "big.bin").read_bytes() == (
            (b"alpha" * 70000) + (b"beta" * 70000)
        )
    finally:
        server.shutdown()
        server.server_close()


def test_compact_cold_and_client_bundle_pull_with_zstd_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(cas, "_zstandard", _FakeZstd)
    server, url, server_root, auth, ctx, source_id, source = _push_single_snapshot(tmp_path)
    try:
        compact_source(
            server_root,
            tenant_id=ctx.tenant_id,
            source_id=source_id,
            revision=source["bundle_sha256"],
            config=CompactConfig(
                chunk_file_bytes=64 * 1024,
                chunk_min_bytes=16 * 1024,
                chunk_avg_bytes=32 * 1024,
                chunk_max_bytes=64 * 1024,
                pack_target_bytes=128 * 1024,
            ),
        )

        cold_store = RuntimeConfig(root=tmp_path / "client-cold", pull_transfer_mode="cold")
        remote.save_auth(
            remote.RemoteAuth(
                server_url=url,
                tenant="tenant-a",
                username="alice",
                token=auth.token,
                device_id=auth.device_id,
                device_name=auth.device_name,
            ),
            cold_store,
        )
        assert remote.pull_all(config=cold_store).ok
        cold_archive = api.list_archives(config=cold_store)[0]
        restored_cold = tmp_path / "restored-cold-fake"
        api.restore_archive(cold_archive.key, "v1", restored_cold, config=cold_store)
        assert (restored_cold / "README.md").read_text(encoding="utf-8") == "cold compact\n"

        db.incoming_bundle_path(server_root, ctx.tenant_id, source_id).unlink()
        bundle_store = RuntimeConfig(
            root=tmp_path / "client-bundle",
            pull_transfer_mode="client-bundle",
        )
        remote.save_auth(
            remote.RemoteAuth(
                server_url=url,
                tenant="tenant-a",
                username="alice",
                token=auth.token,
                device_id=auth.device_id,
                device_name=auth.device_name,
            ),
            bundle_store,
        )
        assert remote.pull_all(config=bundle_store).ok
        bundle_archive = api.list_archives(config=bundle_store)[0]
        restored_bundle = tmp_path / "restored-bundle-fake"
        api.restore_archive(bundle_archive.key, "v1", restored_bundle, config=bundle_store)
        expected = (b"alpha" * 70000) + (b"beta" * 70000)
        assert (restored_cold / "big.bin").read_bytes() == expected
        assert (restored_bundle / "big.bin").read_bytes() == expected
    finally:
        server.shutdown()
        server.server_close()


def test_client_bundle_pull_uses_cold_generated_bundle(tmp_path):
    if cas._zstandard is None:  # noqa: SLF001
        pytest.skip("zstandard not installed")
    server, url, server_root, auth, ctx, source_id, source = _push_single_snapshot(tmp_path)
    try:
        compact_source(
            server_root,
            tenant_id=ctx.tenant_id,
            source_id=source_id,
            revision=source["bundle_sha256"],
            config=CompactConfig(
                chunk_file_bytes=64 * 1024,
                chunk_min_bytes=16 * 1024,
                chunk_avg_bytes=32 * 1024,
                chunk_max_bytes=64 * 1024,
                pack_target_bytes=128 * 1024,
            ),
        )
        incoming = db.incoming_bundle_path(server_root, ctx.tenant_id, source_id)
        incoming.unlink()
        pulled_store = RuntimeConfig(
            root=tmp_path / "client-c",
            pull_transfer_mode="client-bundle",
        )
        remote.save_auth(
            remote.RemoteAuth(
                server_url=url,
                tenant="tenant-a",
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
        restored = tmp_path / "restored-client-bundle"
        api.restore_archive(archive_entry.key, "v1", restored, config=pulled_store)
        assert (restored / "big.bin").read_bytes() == (
            (b"alpha" * 70000) + (b"beta" * 70000)
        )
    finally:
        server.shutdown()
        server.server_close()
