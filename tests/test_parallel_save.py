"""Parallel save behaviour."""

from __future__ import annotations

from pathlib import Path

from snapz import api, cas
from snapz.config import RuntimeConfig
from snapz.store import Store


def _make_tree(root: Path) -> None:
    root.mkdir()
    for name in ["b.txt", "a.txt", "c.txt"]:
        (root / name).write_text(name + "\n", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "same-1.txt").write_text("same\n", encoding="utf-8")
    (nested / "same-2.txt").write_text("same\n", encoding="utf-8")


def _manifest(config: RuntimeConfig, source: Path, name: str) -> cas.Manifest:
    dir_root = Store(config).dir_for(source.resolve())
    return cas.read_manifest(cas.manifest_path(dir_root, name))


def test_parallel_save_preserves_manifest_order(tmp_path, snap_root):
    source = tmp_path / "src"
    _make_tree(source)
    config = RuntimeConfig(root=snap_root, save_workers=4, use_file_cache=False)

    api.save(source, "v1", config=config)
    manifest = _manifest(config, source, "v1")

    assert [entry.path for entry in manifest.entries] == [
        "a.txt",
        "b.txt",
        "c.txt",
        "nested/same-1.txt",
        "nested/same-2.txt",
    ]


def test_parallel_save_does_not_duplicate_same_content_blobs(tmp_path, snap_root):
    source = tmp_path / "src"
    _make_tree(source)
    config = RuntimeConfig(root=snap_root, save_workers=4, use_file_cache=False)

    outcome = api.save(source, "v1", config=config)
    dir_root = Store(config).dir_for(source.resolve())

    shas = [
        entry.sha256
        for entry in _manifest(config, source, "v1").entries
        if entry.type == "file"
    ]
    assert len(set(shas)) == outcome.snapshot.file_count - 1
    assert sum(1 for _ in cas.iter_blob_files(dir_root)) == len(set(shas))


def test_parallel_save_progress_matches_walk_count(tmp_path, snap_root):
    source = tmp_path / "src"
    _make_tree(source)
    config = RuntimeConfig(root=snap_root, save_workers=3, use_file_cache=False)
    calls: list[tuple[int, int, str]] = []

    def on_progress(index, total, entry):
        calls.append((index, total, entry.relpath))

    outcome = api.save(source, "v1", config=config, on_progress=on_progress)

    assert len(calls) == outcome.walk_result.file_count
    assert calls[-1][0] == outcome.walk_result.file_count
    assert {call[2] for call in calls} == {f.relpath for f in outcome.walk_result.files}


def test_workers_one_matches_parallel_manifest(tmp_path):
    source = tmp_path / "src"
    _make_tree(source)
    one = RuntimeConfig(root=tmp_path / "one-store", save_workers=1, use_file_cache=False)
    many = RuntimeConfig(root=tmp_path / "many-store", save_workers=4, use_file_cache=False)

    api.save(source, "v1", config=one)
    api.save(source, "v1", config=many)

    serial_entries = [
        (entry.path, entry.type, entry.sha256, entry.size)
        for entry in _manifest(one, source, "v1").entries
    ]
    parallel_entries = [
        (entry.path, entry.type, entry.sha256, entry.size)
        for entry in _manifest(many, source, "v1").entries
    ]
    assert serial_entries == parallel_entries
