"""Source-directory identity, archive, and relocation behavior."""

from __future__ import annotations

import shutil

import pytest

from snapz import api, cas, cli
from snapz.store import Store


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_deleted_source_moves_to_archive_and_restores_elsewhere(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    old = project_dir.resolve()
    shutil.rmtree(project_dir)

    assert api.list_snapshots(old, config=config) == []
    archives = api.list_archives(config=config)
    assert len(archives) == 1
    assert archives[0].meta.abspath == str(old)
    assert archives[0].archive_reason == "missing-source"

    restored = tmp_path / "restored"
    outcome = api.restore_archive(archives[0].key, "v1", restored, config=config)
    assert outcome.extracted_count > 0
    assert (restored / "src" / "main.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_recreated_same_path_gets_new_snapshot_generation(project_dir, config):
    old = project_dir.resolve()
    parent = project_dir.parent
    api.save(project_dir, "old", config=config)
    shutil.rmtree(project_dir)
    for i in range(32):
        (parent / f"burn-{i}").mkdir()
    project_dir.mkdir()
    (project_dir / "fresh.txt").write_text("fresh\n", encoding="utf-8")

    assert api.list_snapshots(project_dir, config=config) == []
    api.save(project_dir, "new", config=config)

    assert [s.name for s in api.list_snapshots(project_dir, config=config)] == ["new"]
    archives = api.list_archives(config=config)
    assert len(archives) == 1
    assert archives[0].meta.abspath == str(old)
    assert [s.name for s in archives[0].snapshots] == ["old"]
    assert archives[0].archive_reason == "source-recreated"


def test_relocate_source_after_directory_rename(project_dir, config, tmp_path):
    old = project_dir.resolve()
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "renamed"
    project_dir.rename(new)

    entry = api.relocate_source(old, new, config=config)

    assert entry.meta.abspath == str(new.resolve())
    assert api.list_archives(config=config) == []
    snaps = api.list_snapshots(new, config=config)
    assert [s.name for s in snaps] == ["v1"]
    assert snaps[0].source == str(new.resolve())


def test_init_source_writes_marker_and_snapshot_ignores_it(project_dir, config):
    outcome = api.init_source(project_dir, config=config)

    assert outcome.created is True
    assert outcome.marker_path.name == ".snapz-id"
    assert outcome.marker_path.exists()

    save = api.save(project_dir, "v1", config=config)
    manifest = cas.read_manifest(save.pack_result.archive_path)
    assert ".snapz-id" not in {e.path for e in manifest.entries}
    entry = Store(config).entry_by_key(Store(config).key_for(project_dir.resolve()))
    assert entry is not None
    assert entry.meta.source_marker == outcome.marker_id


def test_auto_relocate_after_same_filesystem_rename(project_dir, config, tmp_path):
    old = project_dir.resolve()
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "moved"
    project_dir.rename(new)

    plan = api.auto_relocate_sources([tmp_path], config=config, dry_run=True)
    assert len(plan.relocated) == 1
    assert plan.relocated[0].method == "inode"

    outcome = api.auto_relocate_sources([tmp_path], config=config)

    assert len(outcome.relocated) == 1
    assert outcome.relocated[0].old_path == old
    assert outcome.relocated[0].new_path == new.resolve()
    assert api.list_archives(config=config) == []
    assert [s.name for s in api.list_snapshots(new, config=config)] == ["v1"]


def test_list_snapshots_auto_relocates_after_rename(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "renamed"
    project_dir.rename(new)

    snaps = api.list_snapshots(new, config=config)

    assert [s.name for s in snaps] == ["v1"]
    assert api.list_archives(config=config) == []


def test_save_auto_relocates_after_rename(project_dir, config, tmp_path):
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "renamed"
    project_dir.rename(new)
    (new / "README.md").write_text("# moved\n", encoding="utf-8")

    api.save(new, "v2", config=config)

    assert [s.name for s in api.list_snapshots(new, config=config)] == ["v2", "v1"]
    assert api.list_archives(config=config) == []


def test_auto_relocate_after_copy_delete_uses_marker(project_dir, config, tmp_path):
    api.init_source(project_dir, config=config)
    old = project_dir.resolve()
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "copied"
    shutil.copytree(project_dir, new)
    shutil.rmtree(project_dir)

    outcome = api.auto_relocate_sources([tmp_path], config=config)

    assert len(outcome.relocated) == 1
    assert outcome.relocated[0].old_path == old
    assert outcome.relocated[0].new_path == new.resolve()
    assert "marker" in outcome.relocated[0].method
    assert [s.name for s in api.list_snapshots(new, config=config)] == ["v1"]


def test_restore_auto_relocates_after_copy_delete_with_marker(project_dir, config, tmp_path):
    api.init_source(project_dir, config=config)
    api.save(project_dir, "v1", config=config)
    new = tmp_path / "copied"
    shutil.copytree(project_dir, new)
    shutil.rmtree(project_dir)
    (new / "README.md").write_text("changed\n", encoding="utf-8")

    api.restore(new, "v1", config=config, auto_save=False)

    assert (new / "README.md").read_text(encoding="utf-8") == "# demo\n"
    assert api.list_archives(config=config) == []


def test_auto_relocate_skips_ambiguous_marker(project_dir, config, tmp_path):
    api.init_source(project_dir, config=config)
    api.save(project_dir, "v1", config=config)
    new_a = tmp_path / "copy-a"
    new_b = tmp_path / "copy-b"
    shutil.copytree(project_dir, new_a)
    shutil.copytree(project_dir, new_b)
    shutil.rmtree(project_dir)

    outcome = api.auto_relocate_sources([tmp_path], config=config)

    assert outcome.relocated == []
    assert len(outcome.skipped) == 1
    assert outcome.skipped[0].reason == "ambiguous-candidates"
    assert len(outcome.skipped[0].candidates) == 2
    assert len(api.list_archives(config=config)) == 1


def test_cli_init_and_auto_relocate(env_root, project_dir, tmp_path, capsys):
    rc = cli.main(["initd", str(project_dir)])
    assert rc == 0
    assert (project_dir / ".snapz-id").exists()
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    new = tmp_path / "renamed"
    project_dir.rename(new)
    capsys.readouterr()

    rc = cli.main(["relocate", "--auto", str(tmp_path), "-y", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "relocated" in out
    assert [s.name for s in api.list_snapshots(new, config=api.default_config())] == ["v1"]


def test_archive_restore_resolves_archive_entry_once(env_root, project_dir, tmp_path, capsys, monkeypatch):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    shutil.rmtree(project_dir)
    entry = api.list_archives(config=api.default_config())[0]
    calls = 0
    original = api.list_archives

    def counting_list_archives(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "list_archives", counting_list_archives)

    rc = cli.main(["archive", "restore", entry.key, "v1", str(tmp_path / "out")])

    assert rc == 0
    assert calls == 1


# ----------------- legacy compat -------------------------------------------