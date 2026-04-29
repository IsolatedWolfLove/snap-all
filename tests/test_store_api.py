"""High-level api + store integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from snapz import api
from snapz.config import RuntimeConfig
from snapz.store import Store
from snapz.util import compute_key


def test_save_creates_archive_and_meta(project_dir, config):
    outcome = api.save(project_dir, "before-refactor", config=config)
    artifact = Path(outcome.pack_result.archive_path)
    # CAS layout: <root>/<key>/snapshots/<name>.manifest.json
    assert artifact.exists()
    assert artifact.name.endswith(".manifest.json")
    assert artifact.parent.name == "snapshots"
    assert artifact.parent.parent.parent == config.root
    assert outcome.snapshot.file_count >= 4

    # The per-snapshot meta still lives at the dir-folder root.
    dir_root = artifact.parent.parent
    meta_path = dir_root / f"{outcome.snapshot.name}.meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["name"] == "before-refactor"
    assert meta["source"] == str(project_dir.resolve())

    # The blob store should now contain at least file_count blobs.
    objects = dir_root / "objects"
    assert objects.exists()


def test_save_auto_names_when_name_missing(project_dir, config):
    outcome = api.save(project_dir, config=config)
    assert outcome.snapshot.name.startswith("auto-")


def test_save_refuses_duplicate_without_overwrite(project_dir, config):
    api.save(project_dir, "v1", config=config)
    with pytest.raises(FileExistsError):
        api.save(project_dir, "v1", config=config)


def test_save_overwrite_replaces_archive(project_dir, config):
    first = api.save(project_dir, "v1", config=config)
    (project_dir / "new_file.txt").write_text("hi", encoding="utf-8")
    second = api.save(project_dir, "v1", config=config, overwrite=True)
    assert second.snapshot.file_count == first.snapshot.file_count + 1


def test_list_snapshots_returns_newest_first(project_dir, config):
    api.save(project_dir, "older", config=config)
    api.save(project_dir, "newer", config=config)
    snaps = api.list_snapshots(project_dir, config=config)
    assert [s.name for s in snaps[:2]] == ["newer", "older"]


def test_delete_snapshot_removes_files(project_dir, config):
    outcome = api.save(project_dir, "v1", config=config)
    artifact = Path(outcome.pack_result.archive_path)
    dir_root = artifact.parent.parent  # snapshots/X.manifest.json -> dir_root
    meta_path = dir_root / "v1.meta.json"
    assert api.delete(project_dir, "v1", config=config) is True
    assert not artifact.exists()
    assert not meta_path.exists()
    assert api.list_snapshots(project_dir, config=config) == []


def test_delete_unknown_snapshot_returns_false(project_dir, config):
    assert api.delete(project_dir, "ghost", config=config) is False


def test_rename_moves_archive_and_meta(project_dir, config):
    outcome = api.save(project_dir, "v1", config=config)
    api.rename(project_dir, "v1", "production", config=config)
    snaps = api.list_snapshots(project_dir, config=config)
    assert [s.name for s in snaps] == ["production"]
    assert (Path(outcome.pack_result.archive_path)).exists() is False


def test_rename_refuses_existing_target(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    with pytest.raises(FileExistsError):
        api.rename(project_dir, "v1", "v2", config=config)


def test_show_returns_metadata(project_dir, config):
    api.save(project_dir, "v1", config=config)
    meta = api.show(project_dir, "v1", config=config)
    assert meta is not None
    assert meta.name == "v1"
    # New format labels are ``zstd-cas`` / ``gzip-cas``; legacy archives keep
    # the old ``zstd`` / ``gzip`` labels for forward compatibility.
    assert meta.compression in {"zstd", "gzip", "zstd-cas", "gzip-cas"}


def test_show_returns_none_for_missing(project_dir, config):
    assert api.show(project_dir, "ghost", config=config) is None


def test_list_all_picks_up_multiple_dirs(tmp_path, config):
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("a", encoding="utf-8")
    (b / "g.txt").write_text("b", encoding="utf-8")
    api.save(a, "first", config=config)
    api.save(b, "first", config=config)

    entries = api.list_all(config=config)
    by_key = {e.key: e for e in entries}
    assert compute_key(a) in by_key
    assert compute_key(b) in by_key
    assert by_key[compute_key(a)].snapshots[0].name == "first"


def test_registry_records_dir_after_save(project_dir, config):
    api.save(project_dir, "v1", config=config)
    registry_path = config.root / "registry.json"
    assert registry_path.exists()
    data = json.loads(registry_path.read_text())
    assert compute_key(project_dir) in data["dirs"]


def test_estimate_does_not_create_archive(project_dir, config):
    walk = api.estimate(project_dir, config=config)
    assert walk.file_count > 0
    store = Store(config)
    assert not store.dir_for(project_dir).exists()


def test_save_uses_provided_walk_result(project_dir, config, monkeypatch):
    """Re-using a walk result should skip the second tree walk."""

    walk = api.estimate(project_dir, config=config)
    calls = {"count": 0}
    real_dry_run = api.archive.dry_run

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real_dry_run(*args, **kwargs)

    monkeypatch.setattr(api.archive, "dry_run", spy)
    api.save(project_dir, "v1", config=config, walk_result=walk)
    assert calls["count"] == 0
