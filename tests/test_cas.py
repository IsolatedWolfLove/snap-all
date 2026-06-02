"""Tests for the content-addressable storage backend.

These exercise the dedup, restore, and GC properties that motivated the
CAS rewrite. They run on top of the same ``project_dir`` / ``config``
fixtures as the rest of the suite, so the storage root stays in
``tmp_path``.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from snapz import api, cas
from snapz.config import RuntimeConfig
from snapz.store import Store


def _dir_root(config, abspath: Path) -> Path:
    return Store(config).dir_for(abspath.resolve())


def _count_blobs(dir_root: Path) -> int:
    return sum(1 for _ in cas.iter_blob_files(dir_root))


# ----------------- dedup ----------------------------------------------------


def test_save_creates_manifest_and_blobs(project_dir, config):
    outcome = api.save(project_dir, "v1", config=config)
    manifest_path = Path(outcome.pack_result.archive_path)
    assert manifest_path.exists()
    assert manifest_path.name == "v1.manifest.json"
    assert manifest_path.parent.name == "snapshots"

    dir_root = manifest_path.parent.parent
    assert (dir_root / "objects").exists()
    assert _count_blobs(dir_root) >= outcome.snapshot.file_count


def test_unchanged_resnap_dedupes_to_zero_new_blobs(project_dir, config):
    o1 = api.save(project_dir, "v1", config=config)
    o2 = api.save(project_dir, "v2", config=config)
    # No content changed → second snapshot adds zero new blob bytes.
    assert o2.snapshot.size_bytes == 0
    # Yet the file count is still complete on the second snapshot.
    assert o2.snapshot.file_count == o1.snapshot.file_count

    dir_root = _dir_root(config, project_dir)
    # Only one blob per unique file content.
    assert _count_blobs(dir_root) >= o1.snapshot.file_count
    # Two manifests share the same blob set.
    refs1 = cas.referenced_blobs(dir_root)
    assert len(refs1) >= o1.snapshot.file_count


def test_partial_change_creates_only_one_new_blob(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    o2 = api.save(project_dir, "v2", config=config)
    # Exactly one file changed → exactly one new blob.
    dir_root = _dir_root(config, project_dir)
    # Marginal cost matches one zstd-compressed line of text (small).
    assert 0 < o2.snapshot.size_bytes < 200
    # Both snapshots together reference the union; before-change blob still
    # exists so v1 is restorable.
    refs = cas.referenced_blobs(dir_root)
    assert len(refs) == o2.snapshot.file_count + 1   # +1 = old main.py blob


def test_blobs_share_across_snapshots_in_same_dir(project_dir, config):
    o1 = api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    api.save(project_dir, "v3", config=config)
    dir_root = _dir_root(config, project_dir)
    # 3 manifests but blob count should equal unique content count.
    assert _count_blobs(dir_root) == o1.snapshot.file_count


def test_manifest_written_in_compact_json(tmp_path):
    path = tmp_path / "v1.manifest.json"
    manifest = cas.Manifest(
        snapshot="v1",
        created="2026-01-01T00:00:00",
        entries=[
            cas.ManifestEntry(
                path="a.txt",
                type="file",
                sha256="a" * 64,
                size=1,
            )
        ],
    )

    cas.write_manifest(path, manifest)

    text = path.read_text(encoding="utf-8")
    assert "\n  " not in text
    assert cas.read_manifest(path).entries[0].path == "a.txt"


def test_large_manifest_is_compressed_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cas, "MANIFEST_COMPRESS_THRESHOLD", 128)
    path = tmp_path / "big.manifest.json"
    manifest = cas.Manifest(
        snapshot="big",
        created="2026-01-01T00:00:00",
        entries=[
            cas.ManifestEntry(
                path=f"file-{i}.txt",
                type="file",
                sha256=f"{i:064x}"[-64:],
                size=i,
            )
            for i in range(20)
        ],
    )

    cas.write_manifest(path, manifest)
    compressed = Path(str(path) + ".zst")

    if cas._zstandard is None:  # noqa: SLF001
        assert path.exists()
        return
    assert compressed.exists()
    assert not path.exists()
    read_back = cas.read_manifest(compressed)
    assert read_back.snapshot == "big"
    assert len(read_back.entries) == 20


def test_find_manifest_path_prefers_legacy_plain(tmp_path):
    dir_root = tmp_path
    plain = cas.manifest_path(dir_root, "v1")
    plain.parent.mkdir(parents=True)
    plain.write_text(
        '{"format_version":3,"snapshot":"v1","created":"x","entries":[]}\n',
        encoding="utf-8",
    )

    found = cas.find_manifest_path(dir_root, "v1")

    assert found == plain
    assert cas.read_manifest(found).snapshot == "v1"


def test_find_manifest_path_falls_back_to_compressed(tmp_path, monkeypatch):
    if cas._zstandard is None:  # noqa: SLF001
        pytest.skip("zstandard not installed")
    monkeypatch.setattr(cas, "MANIFEST_COMPRESS_THRESHOLD", 1)
    dir_root = tmp_path
    plain = cas.manifest_path(dir_root, "v1")
    cas.write_manifest(
        plain,
        cas.Manifest(snapshot="v1", created="x", entries=[]),
    )

    found = cas.find_manifest_path(dir_root, "v1")

    assert found.name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX)
    assert cas.read_manifest(found).snapshot == "v1"


def test_zstd_writers_use_configured_level(tmp_path, monkeypatch):
    seen: list[int] = []

    class FakeWriter:
        def __init__(self, raw):
            self.raw = raw

        def write(self, data):
            return self.raw.write(data)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeCompressor:
        def __init__(self, *, level):
            seen.append(level)

        def stream_writer(self, raw, closefd=False):
            return FakeWriter(raw)

        def compress(self, raw):
            return cas._ZSTD_MAGIC + raw[4:]

    class FakeZstd:
        ZstdCompressor = FakeCompressor

    monkeypatch.setattr(cas, "_zstandard", FakeZstd)
    dir_root = tmp_path / "store" / "source"
    dir_root.mkdir(parents=True)
    src = tmp_path / "payload.txt"
    src.write_text("payload\n", encoding="utf-8")

    cas.write_blob(dir_root, src, use_zstd=True, zstd_level=17)
    cas.write_manifest(
        cas.manifest_path(dir_root, "v1"),
        cas.Manifest(
            snapshot="v1",
            created="x",
            entries=[
                cas.ManifestEntry(
                    path="payload.txt",
                    type="file",
                    sha256="a" * 64,
                    size=8,
                )
            ],
        ),
        zstd_level=17,
    )

    assert set(seen) == {17}


def test_write_blob_uses_configured_gzip_level(tmp_path):
    dir_root = tmp_path / "store" / "source"
    dir_root.mkdir(parents=True)
    src = tmp_path / "payload.txt"
    src.write_text(("snapz gzip compression\n" * 5000), encoding="utf-8")

    sha_low, _size_low, _new_low = cas.write_blob(
        dir_root,
        src,
        use_zstd=False,
        gzip_level=1,
    )
    low_size = cas.legacy_blob_path(dir_root, sha_low).stat().st_size
    cas.legacy_blob_path(dir_root, sha_low).unlink()

    sha_high, _size_high, _new_high = cas.write_blob(
        dir_root,
        src,
        use_zstd=False,
        gzip_level=9,
    )
    high_size = cas.legacy_blob_path(dir_root, sha_high).stat().st_size

    assert sha_high == sha_low
    assert high_size < low_size


def test_read_blob_bytes_verifies_checksum(tmp_path):
    dir_root = tmp_path / "store" / "source"
    dir_root.mkdir(parents=True)
    sha = "0" * 64
    blob = cas.global_blob_path(tmp_path / "store", sha)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(gzip.compress(b"payload\n"))

    with pytest.raises(ValueError, match="checksum mismatch"):
        cas.read_blob_bytes(dir_root, sha)


# ----------------- restore --------------------------------------------------


def test_cas_restore_roundtrip(project_dir, config):
    api.save(project_dir, "v1", config=config)
    target = project_dir / "src" / "main.py"
    original = target.read_text(encoding="utf-8")
    target.write_text("CORRUPTED\n", encoding="utf-8")

    api.restore(project_dir, "v1", config=config, auto_save=False)

    assert target.read_text(encoding="utf-8") == original


def test_cas_restore_preserves_mode(project_dir, config):
    script = project_dir / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    os.chmod(script, 0o755)
    api.save(project_dir, "v1", config=config)
    os.chmod(script, 0o600)

    api.restore(project_dir, "v1", config=config, auto_save=False)
    assert (script.stat().st_mode & 0o777) == 0o755


def test_cas_restore_handles_symlinks(project_dir, config):
    real = project_dir / "real.txt"
    real.write_text("payload\n", encoding="utf-8")
    link = project_dir / "alias.txt"
    link.symlink_to("real.txt")
    api.save(project_dir, "v1", config=config)

    link.unlink()
    api.restore(project_dir, "v1", config=config, auto_save=False)
    assert link.is_symlink()
    assert os.readlink(link) == "real.txt"


def test_cas_restore_allows_relative_symlink_within_target(project_dir, config):
    real = project_dir / "README.md"
    link = project_dir / "src" / "readme-link"
    link.symlink_to("../README.md")
    api.save(project_dir, "v1", config=config)

    link.unlink()
    api.restore(project_dir, "v1", config=config, auto_save=False)

    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == real.read_text(encoding="utf-8")


def test_large_file_uses_content_defined_chunks(project_dir, config):
    big = project_dir / "big.bin"
    big.write_bytes((b"alpha" * 70000) + (b"beta" * 70000) + (b"gamma" * 70000))
    cfg = RuntimeConfig(
        root=config.root,
        use_zstd=False,
        use_file_cache=False,
        chunk_file_bytes=128 * 1024,
        chunk_min_bytes=32 * 1024,
        chunk_avg_bytes=64 * 1024,
        chunk_max_bytes=128 * 1024,
    )

    api.save(project_dir, "v1", config=cfg)
    dir_root = _dir_root(cfg, project_dir)
    manifest = cas.read_manifest(cas.find_manifest_path(dir_root, "v1"))
    entry = next(e for e in manifest.entries if e.path == "big.bin")

    assert entry.sha256
    assert entry.chunks
    assert len(entry.chunks) > 1
    assert sum(chunk.size for chunk in entry.chunks) == big.stat().st_size
    assert api.read_snapshot_bytes(project_dir, "v1", "big.bin", config=cfg) == big.read_bytes()


def test_large_file_small_edit_reuses_most_chunks(project_dir, config):
    big = project_dir / "big.bin"
    payload = bytearray((b"alpha" * 70000) + (b"beta" * 70000) + (b"gamma" * 70000))
    big.write_bytes(payload)
    cfg = RuntimeConfig(
        root=config.root,
        use_zstd=False,
        use_file_cache=False,
        chunk_file_bytes=128 * 1024,
        chunk_min_bytes=32 * 1024,
        chunk_avg_bytes=64 * 1024,
        chunk_max_bytes=128 * 1024,
    )

    api.save(project_dir, "v1", config=cfg)
    payload[len(payload) // 2: len(payload) // 2 + 4] = b"EDIT"
    big.write_bytes(payload)
    outcome = api.save(project_dir, "v2", config=cfg)
    dir_root = _dir_root(cfg, project_dir)
    first = cas.read_manifest(cas.find_manifest_path(dir_root, "v1"))
    second = cas.read_manifest(cas.find_manifest_path(dir_root, "v2"))
    first_entry = next(e for e in first.entries if e.path == "big.bin")
    second_entry = next(e for e in second.entries if e.path == "big.bin")
    first_chunks = {chunk.sha256 for chunk in first_entry.chunks}
    second_chunks = {chunk.sha256 for chunk in second_entry.chunks}

    assert first_entry.sha256 != second_entry.sha256
    assert first_chunks & second_chunks
    assert outcome.snapshot.size_bytes < big.stat().st_size

    big.write_text("corrupt\n", encoding="utf-8")
    api.restore(project_dir, "v2", config=cfg, auto_save=False)
    assert big.read_bytes() == bytes(payload)


def test_restore_detects_corrupt_blob_during_stream(project_dir, config, monkeypatch):
    api.save(project_dir, "v1", config=config)
    target = project_dir / "src" / "main.py"
    target.write_text("keep me\n", encoding="utf-8")
    calls = 0
    original = cas.verify_blob

    def counting_verify_blob(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(cas, "verify_blob", counting_verify_blob)
    dir_root = _dir_root(config, project_dir)
    manifest = cas.read_manifest(cas.manifest_path(dir_root, "v1"))
    entry = next(e for e in manifest.entries if e.path == "src/main.py")
    blob = cas.find_blob(dir_root, entry.sha256)
    blob.write_bytes(b"not a compressed blob")

    with pytest.raises(ValueError):
        api.restore(project_dir, "v1", config=config, auto_save=False)

    assert calls == 0
    assert target.read_text(encoding="utf-8") == "keep me\n"


# ----------------- GC -------------------------------------------------------


def test_gc_removes_unreferenced_blobs(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)
    # Delete v1; blob for old main.py is now orphaned but still on disk.
    api.delete(project_dir, "v1", config=config)

    dir_root = _dir_root(config, project_dir)
    before = _count_blobs(dir_root)

    result = api.gc(project_dir, config=config)
    assert result.blobs_removed >= 1
    assert result.bytes_freed > 0
    after = _count_blobs(dir_root)
    assert after == before - result.blobs_removed


def test_gc_dry_run_reports_without_deleting(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("changed\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)
    api.delete(project_dir, "v1", config=config)

    dir_root = _dir_root(config, project_dir)
    before = _count_blobs(dir_root)

    result = api.gc(project_dir, config=config, dry_run=True)
    assert result.dry_run is True
    assert result.blobs_removed >= 1
    # Nothing actually removed
    assert _count_blobs(dir_root) == before


def test_gc_keeps_blobs_referenced_by_remaining_snapshots(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    api.delete(project_dir, "v1", config=config)
    # All v1 blobs are still referenced by v2 → gc removes nothing.
    result = api.gc(project_dir, config=config)
    assert result.blobs_removed == 0


def test_gc_all_dirs_visits_every_recorded_directory(tmp_path, config):
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("a", encoding="utf-8")
    (b / "g.txt").write_text("b", encoding="utf-8")
    api.save(a, "v1", config=config)
    api.save(b, "v1", config=config)

    result = api.gc(all_dirs=True, config=config)
    assert result.dirs_scanned >= 2


def test_gc_requires_path_or_all_dirs(config):
    with pytest.raises(ValueError):
        api.gc(config=config)


def test_refs_index_increments_on_save(project_dir, config):
    outcome = api.save(project_dir, "v1", config=config)
    refs = cas.load_refs_index(config.root)
    dir_root = _dir_root(config, project_dir)
    manifest = cas.read_manifest(cas.find_manifest_path(dir_root, "v1"))
    shas = [entry.sha256 for entry in manifest.entries if entry.sha256]

    assert refs
    assert all(refs[sha] >= 1 for sha in shas)
    assert sum(refs.values()) == outcome.snapshot.file_count


def test_refs_index_decrements_on_delete(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    before = dict(cas.load_refs_index(config.root))

    api.delete(project_dir, "v1", config=config)

    after = cas.load_refs_index(config.root)
    assert sum(after.values()) == sum(before.values()) - api.show(
        project_dir, "v2", config=config
    ).file_count


def test_gc_global_uses_refs_index_when_present(project_dir, config):
    api.save(project_dir, "v1", config=config)
    dir_root = _dir_root(config, project_dir)
    manifest = cas.read_manifest(cas.find_manifest_path(dir_root, "v1"))
    sha = next(entry.sha256 for entry in manifest.entries if entry.sha256)
    cas.save_refs_index(config.root, {sha: 1})

    result = api.gc(project_dir, config=config)

    assert result.blobs_removed >= 1
    remaining = {blob.name for blob in cas.iter_global_blob_files(config.root)}
    assert remaining == {sha}


def test_rebuild_refs_index(project_dir, config):
    api.save(project_dir, "v1", config=config)
    cas.save_refs_index(config.root, {})

    refs = cas.rebuild_refs_index(config.root)

    assert refs == cas.load_refs_index(config.root)
    assert sum(refs.values()) == api.show(project_dir, "v1", config=config).file_count


def test_gc_rebuild_index_flag(project_dir, config):
    api.save(project_dir, "v1", config=config)
    cas.save_refs_index(config.root, {})

    result = api.gc(project_dir, config=config, rebuild_index=True)

    assert result.blobs_removed == 0
    assert cas.load_refs_index(config.root)


# ----------------- export ---------------------------------------------------


def test_export_extracts_into_arbitrary_dir(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    target = tmp_path / "exported"
    outcome = api.export(project_dir, "v1", target, config=config)
    assert (target / "src" / "main.py").exists()
    assert (target / "README.md").exists()
    assert outcome.extracted_count > 0
    assert outcome.destination == target.resolve()


def test_export_creates_missing_parent(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    target = tmp_path / "deep" / "nested" / "out"
    api.export(project_dir, "v1", target, config=config)
    assert (target / "src" / "main.py").exists()


def test_export_refuses_nonempty_dst(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    target = tmp_path / "exported"
    target.mkdir()
    (target / "garbage").write_text("hi", encoding="utf-8")
    with pytest.raises(FileExistsError):
        api.export(project_dir, "v1", target, config=config)
    assert (target / "garbage").exists()
    assert not (target / "src").exists()


def test_export_overwrites_when_flag_set(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    target = tmp_path / "exported"
    target.mkdir()
    (target / "garbage").write_text("hi", encoding="utf-8")
    api.export(project_dir, "v1", target, config=config, overwrite=True)
    assert (target / "src" / "main.py").exists()


def test_export_does_not_modify_source(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    snaps_before = [s.name for s in api.list_snapshots(project_dir, config=config)]
    target = tmp_path / "exported"
    api.export(project_dir, "v1", target, config=config)
    snaps_after = [s.name for s in api.list_snapshots(project_dir, config=config)]
    # No auto-pre-restore created.
    assert snaps_before == snaps_after


def test_export_unknown_snapshot_raises(project_dir, config, tmp_path):
    with pytest.raises(FileNotFoundError):
        api.export(project_dir, "ghost", tmp_path / "out", config=config)


# ----------------- legacy compat -------------------------------------------


def test_legacy_tar_archive_still_listable(project_dir, config, tmp_path):
    """Snapshots created in the pre-CAS format should still appear in
    ``snapz list`` and be restorable."""

    import tarfile

    # Hand-craft a legacy snapshot: <key>/old.tar.zst + old.meta.json
    store = Store(config)
    dir_root = store.dir_for(project_dir.resolve())
    dir_root.mkdir(parents=True, exist_ok=True)
    archive_path = dir_root / "legacy.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(str(project_dir / "README.md"), arcname="README.md")

    meta = {
        "name": "legacy",
        "source": str(project_dir.resolve()),
        "created": "2026-01-01T00:00:00",
        "size_bytes": archive_path.stat().st_size,
        "file_count": 1,
        "total_bytes_in": 10,
        "compression": "gzip",
        "archive": "legacy.tar.gz",
    }
    import json as _json
    (dir_root / "legacy.meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    # Touch dir_meta so list_all sees it
    store.ensure_dir(project_dir.resolve())

    snaps = api.list_snapshots(project_dir, config=config)
    names = [s.name for s in snaps]
    assert "legacy" in names

    # Restore should work via the legacy tar path.
    (project_dir / "README.md").write_text("BAD", encoding="utf-8")
    api.restore(project_dir, "legacy", config=config, auto_save=False)
    assert (project_dir / "README.md").read_text(encoding="utf-8").startswith("# demo")
