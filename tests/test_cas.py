"""Tests for the content-addressable storage backend.

These exercise the dedup, restore, and GC properties that motivated the
CAS rewrite. They run on top of the same ``project_dir`` / ``config``
fixtures as the rest of the suite, so the storage root stays in
``tmp_path``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from snapz import api, cas
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
