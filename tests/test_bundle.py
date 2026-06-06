"""Portable bundle export/import tests."""

from __future__ import annotations

import importlib.util
import json
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from snapz import api, cas, cli
from snapz.config import RuntimeConfig
from snapz.store import Store


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_bundle_import_without_path_stays_archived(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# v2\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)

    bundle = tmp_path / "project.snapz"
    exported = api.export_bundle(project_dir, bundle, config=config)
    assert exported.snapshot_count == 2
    assert exported.blob_count > 0

    fresh = RuntimeConfig(root=tmp_path / "fresh-store")
    imported = api.import_bundle(bundle, config=fresh)
    assert imported.archived is True
    assert imported.snapshot_count == 2

    archives = api.list_archives(config=fresh)
    assert [a.key for a in archives] == [imported.key]
    restored = tmp_path / "restored"
    outcome = api.restore_archive(imported.key, "v1", restored, config=fresh)
    assert outcome.extracted_count > 0
    assert (restored / "src" / "main.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_bundle_import_with_path_binds_to_live_directory(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)

    target = tmp_path / "target"
    target.mkdir()
    fresh = RuntimeConfig(root=tmp_path / "fresh-store")
    imported = api.import_bundle(bundle, config=fresh, path=target)

    assert imported.archived is False
    assert api.list_archives(config=fresh) == []
    assert [s.name for s in api.list_snapshots(target, config=fresh)] == ["v1"]

    api.restore(target, "v1", config=fresh, auto_save=False)
    assert (target / "README.md").read_text(encoding="utf-8") == "# demo\n"


def test_bundle_import_updates_refs_index_for_gc(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)
    fresh = RuntimeConfig(root=tmp_path / "fresh-store")
    cas.save_refs_index(fresh.root, {})

    imported = api.import_bundle(bundle, config=fresh)
    result = api.gc(all_dirs=True, config=fresh)

    assert result.blobs_removed == 0
    assert cas.load_refs_index(fresh.root)
    restored = tmp_path / "restored"
    api.restore_archive(imported.key, "v1", restored, config=fresh)
    assert (restored / "README.md").read_text(encoding="utf-8") == "# demo\n"


def test_bundle_import_requires_overwrite_for_name_conflicts(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)

    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("local\n", encoding="utf-8")
    fresh = RuntimeConfig(root=tmp_path / "fresh-store")
    api.save(target, "v1", config=fresh)

    with pytest.raises(FileExistsError):
        api.import_bundle(bundle, config=fresh, path=target)

    imported = api.import_bundle(bundle, config=fresh, path=target, overwrite=True)
    assert imported.overwritten_snapshots == ["v1"]
    api.restore(target, "v1", config=fresh, auto_save=False)
    assert (target / "README.md").read_text(encoding="utf-8") == "# demo\n"


def test_bundle_includes_shared_blobs_once(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    bundle = tmp_path / "project.snapz"

    outcome = api.export_bundle(project_dir, bundle, config=config)

    with api._open_bundle_tar_reader(bundle) as tar:
        object_members = [
            m.name for m in tar.getmembers()
            if m.name.startswith("objects/") and m.isfile()
        ]
    refs = cas.referenced_blobs(Store(config).dir_for(project_dir.resolve()))
    assert len(object_members) == len(refs)
    assert outcome.blob_count == len(refs)


def test_bundle_includes_chunked_file_blobs(project_dir, config, tmp_path):
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
    bundle = tmp_path / "project.snapz"
    expected = big.read_bytes()

    outcome = api.export_bundle(project_dir, bundle, config=cfg)
    fresh = RuntimeConfig(root=tmp_path / "fresh")
    imported = api.import_bundle(bundle, config=fresh, path=project_dir)
    project_dir.joinpath("big.bin").write_text("corrupt\n", encoding="utf-8")
    api.restore(project_dir, "v1", config=fresh, auto_save=False)

    assert outcome.blob_count > 1
    assert imported.blob_count == outcome.blob_count
    assert big.read_bytes() == expected


def test_bundle_import_rejects_invalid_blob_id(tmp_path):
    bundle = tmp_path / "bad.snapz"
    meta = {
        "format_version": api.BUNDLE_FORMAT_VERSION,
        "source": {"key": "bad", "abspath": str(tmp_path / "source")},
        "snapshots": [
            {
                "name": "v1",
                "meta": "source/v1.meta.json",
                "artifact": "source/snapshots/v1.manifest.json",
                "kind": "manifest",
            }
        ],
        "blobs": [".."],
    }
    with tarfile.open(bundle, "w:gz") as tar:
        raw = (json.dumps(meta) + "\n").encode("utf-8")
        info = tarfile.TarInfo(api.BUNDLE_META_NAME)
        info.size = len(raw)
        tar.addfile(info, BytesIO(raw))

    with pytest.raises(ValueError, match="invalid blob id"):
        api.import_bundle(bundle, config=RuntimeConfig(root=tmp_path / "store"))


def test_bundle_import_rejects_corrupt_declared_blob(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)
    corrupted = tmp_path / "corrupted.snapz"

    with api._open_bundle_tar_reader(bundle) as src, tarfile.open(corrupted, "w:gz") as dst:
        for member in src.getmembers():
            info = tarfile.TarInfo(member.name)
            info.mode = member.mode
            info.mtime = member.mtime
            if member.isfile():
                data = (
                    b"not a compressed blob"
                    if member.name.startswith("objects/")
                    else src.extractfile(member).read()
                )
                info.size = len(data)
                dst.addfile(info, BytesIO(data))
            else:
                dst.addfile(info)

    fresh = RuntimeConfig(root=tmp_path / "fresh")
    with pytest.raises(ValueError, match="bundle blob checksum mismatch"):
        api.import_bundle(corrupted, config=fresh)
    assert not list(Path(fresh.root).rglob("*.manifest.json"))


def test_bundle_import_rejects_manifest_blob_not_declared(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)
    corrupted = tmp_path / "missing-ref.snapz"

    with api._open_bundle_tar_reader(bundle) as src, tarfile.open(corrupted, "w:gz") as dst:
        meta = json.loads(
            src.extractfile(src.getmember(api.BUNDLE_META_NAME)).read().decode("utf-8")
        )
        meta["blobs"] = []
        for member in src.getmembers():
            info = tarfile.TarInfo(member.name)
            info.mode = member.mode
            info.mtime = member.mtime
            if member.name == api.BUNDLE_META_NAME:
                data = (json.dumps(meta) + "\n").encode("utf-8")
            elif member.isfile():
                data = src.extractfile(member).read()
            else:
                dst.addfile(info)
                continue
            info.size = len(data)
            dst.addfile(info, BytesIO(data))

    with pytest.raises(ValueError, match="references missing blob"):
        api.import_bundle(corrupted, config=RuntimeConfig(root=tmp_path / "fresh"))


def test_bundle_prefers_zstd_when_available(project_dir, config, tmp_path):
    if importlib.util.find_spec("zstandard") is None:
        pytest.skip("zstandard not installed")

    api.save(project_dir, "v1", config=config)
    bundle = tmp_path / "project.snapz"
    api.export_bundle(project_dir, bundle, config=config)

    with open(bundle, "rb") as fh:
        magic = fh.read(4)
    assert magic == b"\x28\xb5\x2f\xfd"


def test_bundle_gzip_fallback_uses_high_compression(project_dir, config, tmp_path):
    (project_dir / "bulk.txt").write_text(
        ("bundle compression\n" * 8000),
        encoding="utf-8",
    )
    low_cfg = RuntimeConfig(root=tmp_path / "low-store", use_zstd=False, gzip_level=1)
    high_cfg = RuntimeConfig(root=tmp_path / "high-store", use_zstd=False, gzip_level=9)
    api.save(project_dir, "v1", config=low_cfg)
    api.save(project_dir, "v1", config=high_cfg)
    low_bundle = tmp_path / "low.snapz"
    high_bundle = tmp_path / "high.snapz"

    api.export_bundle(project_dir, low_bundle, config=low_cfg)
    api.export_bundle(project_dir, high_bundle, config=high_cfg)

    assert high_bundle.stat().st_size < low_bundle.stat().st_size


def test_cli_bundle_and_import_with_path(env_root, project_dir, tmp_path, monkeypatch, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    bundle = tmp_path / "project.snapz"
    capsys.readouterr()

    rc = cli.main(["bundle", str(project_dir), str(bundle), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "snapshot_count" in out
    assert bundle.exists()

    fresh_root = tmp_path / "fresh-store"
    fresh_root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(fresh_root))

    rc = cli.main(["import", str(bundle), "--path", str(target), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "imported_snapshots" in out
    assert [s.name for s in api.list_snapshots(target, config=api.default_config())] == ["v1"]
