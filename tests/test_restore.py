"""Tests for :mod:`snapz.api.restore` and CLI ``restore`` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import api, archive, cli


def _all_snapshot_names(project_dir, config):
    return [s.name for s in api.list_snapshots(project_dir, config=config)]


def test_restore_estimate_classifies_paths(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# changed\n", encoding="utf-8")
    (project_dir / "added.txt").write_text("new\n", encoding="utf-8")
    (project_dir / "data" / "input.txt").unlink()

    est = api.restore_estimate(project_dir, "v1", config=config)
    assert "src/main.py" in est.overwritten_files
    assert "data/input.txt" in est.new_files
    assert "added.txt" in est.extra_files
    assert est.archive_member_count >= 4


def test_restore_extracts_files_and_creates_pre_backup(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# wrecked\n", encoding="utf-8")

    outcome = api.restore(project_dir, "v1", config=config)

    # Auto pre-restore snapshot should have been created
    names = _all_snapshot_names(project_dir, config)
    assert any(n.startswith("auto-pre-restore-") for n in names)
    assert outcome.pre_restore is not None
    assert outcome.pre_restore.name.startswith("auto-pre-restore-")
    # File should be back to its snapshotted content
    assert (project_dir / "src" / "main.py").read_text() == "print('hi')\n"
    assert outcome.extracted_count >= 1


def test_restore_no_auto_save_skips_pre_backup(project_dir, config):
    api.save(project_dir, "v1", config=config)
    outcome = api.restore(project_dir, "v1", config=config, auto_save=False)
    assert outcome.pre_restore is None
    names = _all_snapshot_names(project_dir, config)
    assert not any(n.startswith("auto-pre-restore-") for n in names)


def test_restore_clean_removes_extra_files(project_dir, config):
    api.save(project_dir, "v1", config=config)
    extra = project_dir / "leftover.txt"
    extra.write_text("delete me\n", encoding="utf-8")

    outcome = api.restore(
        project_dir, "v1", config=config, auto_save=False, clean=True
    )
    assert outcome.cleaned_count >= 1
    assert not extra.exists()


def test_restore_without_clean_keeps_extras(project_dir, config):
    api.save(project_dir, "v1", config=config)
    extra = project_dir / "leftover.txt"
    extra.write_text("keep me\n", encoding="utf-8")

    api.restore(project_dir, "v1", config=config, auto_save=False, clean=False)
    assert extra.exists()


def test_restore_unknown_snapshot_raises(project_dir, config):
    with pytest.raises(FileNotFoundError):
        api.restore(project_dir, "ghost", config=config)


def test_manifest_records_relative_paths_and_hashes(project_dir, config):
    """CAS manifest is the new equivalent of ``list_archive_members``."""

    from snapz import cas

    outcome = api.save(project_dir, "v1", config=config)
    manifest_path = Path(outcome.pack_result.archive_path)
    assert manifest_path.name.endswith(".manifest.json")

    manifest = cas.read_manifest(manifest_path)
    rels = {e.path for e in manifest.entries if e.type == "file"}
    assert "src/main.py" in rels
    assert "README.md" in rels
    # Every file entry has a sha256 + size.
    for e in manifest.entries:
        if e.type == "file":
            assert e.sha256 and len(e.sha256) == 64
            assert e.size is not None and e.size >= 0


# ---------- CLI ----------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_restore_with_yes(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# bad\n", encoding="utf-8")

    rc = cli.main(["restore", "v1", "--path", str(project_dir), "-y"])
    assert rc == 0
    assert (project_dir / "src" / "main.py").read_text() == "print('hi')\n"


def test_cli_restore_unknown_returns_error(env_root, project_dir, capsys):
    rc = cli.main(["restore", "ghost", "--path", str(project_dir), "-y"])
    assert rc == cli.EXIT_ERROR
    assert "ghost" in capsys.readouterr().err


def test_cli_restore_no_auto_save_flag(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    cli.main(
        ["restore", "v1", "--path", str(project_dir), "-y", "--no-auto-save"]
    )
    names = _all_snapshot_names(project_dir, config=api.default_config())
    assert not any(n.startswith("auto-pre-restore-") for n in names)


def test_cli_restore_clean_flag(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    extra = project_dir / "leftover.txt"
    extra.write_text("x", encoding="utf-8")
    cli.main(
        [
            "restore",
            "v1",
            "--path",
            str(project_dir),
            "-y",
            "--no-auto-save",
            "--clean",
        ]
    )
    assert not extra.exists()
