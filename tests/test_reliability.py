"""Reliability features added for the v0.3 storage hardening pass."""

from __future__ import annotations

import pytest

from snapz import api, cas
from snapz.store import Store


def test_check_reports_missing_blob_and_restore_fails(project_dir, config):
    api.save(project_dir, "v1", config=config)
    store = Store(config)
    dir_root = store.dir_for(project_dir.resolve())
    manifest = cas.read_manifest(cas.manifest_path(dir_root, "v1"))
    sha = next(e.sha256 for e in manifest.entries if e.type == "file" and e.sha256)
    cas.find_blob(dir_root, sha).unlink()

    result = api.check(project_dir, config=config)

    assert not result.ok
    assert any(i.code == "bad-blob" for i in result.issues)
    with pytest.raises(FileNotFoundError):
        api.restore(project_dir, "v1", config=config, auto_save=False)


def test_protected_snapshot_survives_prune_and_blocks_delete(project_dir, config):
    api.save(project_dir, "old", config=config)
    api.protect(project_dir, "old", config=config)
    (project_dir / "README.md").write_text("# changed\n", encoding="utf-8")
    api.save(project_dir, "new", config=config)

    plan = api.plan_prune(project_dir, keep_last=1, config=config)

    assert "old" in {s.name for s in plan.keep}
    assert "old" not in {s.name for s in plan.drop}
    outcome = api.execute_prune(plan, config=config)
    assert "old" not in outcome.deleted
    with pytest.raises(PermissionError):
        api.delete(project_dir, "old", config=config)
    api.unprotect(project_dir, "old", config=config)
    assert api.delete(project_dir, "old", config=config) is True


def test_migrate_moves_legacy_blob_to_global_pool(project_dir, config):
    store = Store(config)
    dir_root = store.ensure_dir(project_dir.resolve())
    src = project_dir / "README.md"
    sha, size, was_new = cas.write_blob(dir_root, src, use_zstd=True)
    legacy_path = cas.legacy_blob_path(dir_root, sha)
    assert was_new
    assert size > 0
    assert legacy_path.exists()
    assert not cas.global_blob_path(config.root, sha).exists()

    outcome = api.migrate(project_dir, config=config)

    assert outcome.blobs_migrated == 1
    assert outcome.bytes_migrated > 0
    assert cas.global_blob_path(config.root, sha).exists()
    assert not legacy_path.exists()


def test_check_fix_rewrites_manifest_snapshot_name(project_dir, config):
    api.save(project_dir, "v1", config=config)
    dir_root = Store(config).dir_for(project_dir.resolve())
    manifest_path = cas.manifest_path(dir_root, "v1")
    manifest = cas.read_manifest(manifest_path)
    manifest.snapshot = "wrong"
    cas.write_manifest(manifest_path, manifest)

    result = api.check(project_dir, fix=True, config=config)

    assert any(i.code == "manifest-name-mismatch" and i.fixed for i in result.issues)
    assert cas.read_manifest(manifest_path).snapshot == "v1"
