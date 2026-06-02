"""Tests for :mod:`snapz.api.restore` and CLI ``restore`` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import api, archive, cas, cli


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


def test_restore_rejects_manifest_paths_outside_target(project_dir, config, tmp_path):
    outcome = api.save(project_dir, "v1", config=config)
    dir_root = api.Store(config).dir_for(project_dir.resolve())
    manifest_path = outcome.pack_result.archive_path
    manifest = cas.read_manifest(manifest_path)
    sha = next(e.sha256 for e in manifest.entries if e.type == "file" and e.sha256)
    manifest.entries = [
        cas.ManifestEntry(path="../escaped.txt", type="file", sha256=sha, size=0),
    ]
    cas.write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match="unsafe snapshot path"):
        api._extract_cas(manifest_path, tmp_path / "restore", dir_root=dir_root)

    assert not (tmp_path / "escaped.txt").exists()


def test_restore_rejects_symlink_target_outside_target(project_dir, config, tmp_path):
    outcome = api.save(project_dir, "v1", config=config)
    dir_root = api.Store(config).dir_for(project_dir.resolve())
    manifest_path = outcome.pack_result.archive_path
    manifest = cas.read_manifest(manifest_path)
    manifest.entries = [
        cas.ManifestEntry(
            path="link",
            type="symlink",
            target="../escaped.txt",
        ),
    ]
    cas.write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match="unsafe snapshot symlink target"):
        api._extract_cas(manifest_path, tmp_path / "restore", dir_root=dir_root)

    assert not (tmp_path / "restore" / "link").exists()


def test_restore_preserves_manifest_path_whitespace(project_dir, config):
    spaced = project_dir / " leading.txt"
    spaced.write_text("space\n", encoding="utf-8")
    api.save(project_dir, "v1", config=config)

    spaced.unlink()
    api.restore(project_dir, "v1", config=config, auto_save=False)

    assert spaced.read_text(encoding="utf-8") == "space\n"
    assert not (project_dir / "leading.txt").exists()


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


def test_cli_restore_preview_shows_changed_paths(
    env_root, project_dir, monkeypatch, capsys
):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("changed\n", encoding="utf-8")
    (project_dir / "extra.txt").write_text("extra\n", encoding="utf-8")
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))

    rc = cli.main(["restore", "v1", "--path", str(project_dir)])
    out = capsys.readouterr().out

    assert rc == cli.EXIT_USER_ABORT
    assert "will overwrite" in out
    assert "src/main.py" in out
    assert "extra files kept" in out
    assert "extra.txt" in out


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
