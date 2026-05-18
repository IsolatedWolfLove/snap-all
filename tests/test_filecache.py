"""Tests for the per-source save file cache."""

from __future__ import annotations

import json

from snapz import api, cas, filecache
from snapz.config import RuntimeConfig
from snapz.store import Store


def _dir_root(config: RuntimeConfig, project_dir):
    return Store(config).dir_for(project_dir.resolve())


def test_save_writes_file_cache(project_dir, config):
    outcome = api.save(project_dir, "v1", config=config)
    dir_root = _dir_root(config, project_dir)

    cache = filecache.load(dir_root)

    file_entries = [e for e in outcome.walk_result.files if not e.is_symlink]
    assert len(cache) == len(file_entries)
    assert "README.md" in cache
    assert cache["README.md"].sha256


def test_warm_cache_skips_blob_hashing(project_dir, config, monkeypatch):
    api.save(project_dir, "v1", config=config)

    def fail_write_blob(*_args, **_kwargs):
        raise AssertionError("write_blob should not run on a warm cache hit")

    monkeypatch.setattr(api.cas, "write_blob", fail_write_blob)
    outcome = api.save(project_dir, "v2", config=config)

    assert outcome.snapshot.file_count > 0
    assert outcome.snapshot.size_bytes == 0


def test_stale_cache_entry_is_rehashed(project_dir, config, monkeypatch):
    api.save(project_dir, "v1", config=config)
    (project_dir / "README.md").write_text("# changed\n", encoding="utf-8")
    calls = {"count": 0}
    real_write_blob = api.cas.write_blob

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real_write_blob(*args, **kwargs)

    monkeypatch.setattr(api.cas, "write_blob", spy)
    api.save(project_dir, "v2", config=config)

    assert calls["count"] == 1


def test_corrupt_cache_is_ignored(project_dir, config):
    api.save(project_dir, "v1", config=config)
    dir_root = _dir_root(config, project_dir)
    filecache.cache_path(dir_root).write_text("{", encoding="utf-8")

    outcome = api.save(project_dir, "v2", config=config)

    assert outcome.snapshot.file_count > 0
    assert filecache.load(dir_root)


def test_no_cache_bypasses_and_does_not_write_cache(project_dir, snap_root):
    cfg = RuntimeConfig(root=snap_root, use_file_cache=False)

    api.save(project_dir, "v1", config=cfg)

    assert not filecache.cache_path(_dir_root(cfg, project_dir)).exists()


def test_missing_blob_rehashes_even_when_cache_matches(project_dir, config):
    api.save(project_dir, "v1", config=config)
    dir_root = _dir_root(config, project_dir)
    cache = filecache.load(dir_root)
    sha = cache["README.md"].sha256
    cas.find_blob(dir_root, sha).unlink()

    outcome = api.save(project_dir, "v2", config=config)

    assert outcome.snapshot.file_count > 0
    assert cas.find_blob(dir_root, sha).exists()


def test_check_fix_invalidates_file_cache(project_dir, config):
    api.save(project_dir, "v1", config=config)
    dir_root = _dir_root(config, project_dir)
    cache_path = filecache.cache_path(dir_root)
    assert cache_path.exists()

    result = api.check(project_dir, fix=True, config=config)

    assert result.ok
    assert any(i.code == "removed-file-cache" and i.fixed for i in result.issues)
    assert not cache_path.exists()


def test_cache_loader_skips_bad_entries(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    filecache.cache_path(root).write_text(
        json.dumps(
            {
                "version": 1,
                "files": {
                    "good.txt": {
                        "size": 1,
                        "mtime": 2.0,
                        "inode": 3,
                        "sha256": "a" * 64,
                    },
                    "bad.txt": {"sha256": "short"},
                },
            }
        ),
        encoding="utf-8",
    )

    cache = filecache.load(root)

    assert list(cache) == ["good.txt"]
