"""Stats API and CLI text-mode tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import api, cli


# ----------------- stats API ----------------------------------------------


def test_stats_single_dir_reports_counts(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)

    [entry] = api.stats(project_dir, config=config)
    assert entry.snapshot_count == 2
    assert entry.abspath == project_dir.resolve()
    assert entry.logical_bytes > 0
    assert entry.on_disk_bytes > 0
    # Two identical content snapshots → CAS dedup, blobs reused.
    assert entry.blob_count > 0
    assert entry.dedup_ratio >= 1.0
    assert entry.newest is not None and entry.oldest is not None
    assert entry.largest is not None


def test_stats_returns_empty_for_unknown_dir(tmp_path, config):
    fresh = tmp_path / "untouched"
    fresh.mkdir()
    [entry] = api.stats(fresh, config=config)
    assert entry.snapshot_count == 0
    assert entry.logical_bytes == 0
    assert entry.on_disk_bytes == 0
    assert entry.dedup_ratio == 1.0
    assert entry.newest is None and entry.oldest is None
    assert entry.largest is None


def test_stats_dedup_ratio_with_duplicate_content(project_dir, config):
    """Adding a duplicate-content file should not noticeably grow blob_bytes."""

    api.save(project_dir, "v1", config=config)
    [before] = api.stats(project_dir, config=config)

    # Copy an existing file under a new name → same sha → dedup.
    (project_dir / "dup.py").write_text(
        (project_dir / "src" / "main.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    api.save(project_dir, "v2", config=config)
    [after] = api.stats(project_dir, config=config)

    # Logical content grew; blob storage essentially didn't.
    assert after.logical_bytes > before.logical_bytes
    assert after.blob_bytes <= before.blob_bytes + 256  # tiny manifest overhead
    assert after.dedup_ratio > before.dedup_ratio


def test_stats_global_lists_every_known_source(project_dir, tmp_path, config):
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_text("hi", encoding="utf-8")

    api.save(project_dir, "p1", config=config)
    api.save(other, "o1", config=config)

    entries = api.stats(config=config)
    paths = {e.abspath for e in entries}
    assert project_dir.resolve() in paths
    assert other.resolve() in paths
    # Sorted by on-disk size descending.
    sizes = [e.on_disk_bytes for e in entries]
    assert sizes == sorted(sizes, reverse=True)


def test_stats_global_uses_bulk_cached_meta(project_dir, tmp_path, config, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_text("hi", encoding="utf-8")

    api.save(project_dir, "p1", config=config)
    api.save(other, "o1", config=config)

    def fail_list_snapshots(*_args, **_kwargs):
        raise AssertionError("global stats should use cached dir metadata")

    monkeypatch.setattr(api.Store, "list_snapshots", fail_list_snapshots)
    entries = api.stats(config=config)

    assert {entry.abspath for entry in entries} == {
        project_dir.resolve(),
        other.resolve(),
    }
    assert all(entry.snapshot_count == 1 for entry in entries)
    assert all(entry.on_disk_bytes > 0 for entry in entries)


# ----------------- CLI text mode ------------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_stats_text_output(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["stats", str(project_dir), "--text"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DIR" in out and "SNAPS" in out and "DEDUP" in out
    assert str(project_dir.resolve()) in out


def test_cli_stats_all_flag_lists_every_dir(
    env_root, project_dir, tmp_path, capsys,
):
    cli.main(["save", str(project_dir), "-n", "p1", "-y"])
    other = tmp_path / "other"
    other.mkdir()
    (other / "x").write_text("y", encoding="utf-8")
    cli.main(["save", str(other), "-n", "o1", "-y"])
    capsys.readouterr()

    rc = cli.main(["stats", "--all", "--text"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(project_dir.resolve()) in out
    assert str(other.resolve()) in out
    assert "total:" in out
