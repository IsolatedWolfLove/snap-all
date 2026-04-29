"""Selective restore (revert) API and CLI tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import api, cli


# ----------------- revert API --------------------------------------------


def test_revert_single_file_restores_only_that_file(project_dir, config):
    api.save(project_dir, "v1", config=config)
    main_py = project_dir / "src" / "main.py"
    lib_py = project_dir / "src" / "lib.py"
    main_py.write_text("# patched main\n", encoding="utf-8")
    lib_py.write_text("# patched lib\n", encoding="utf-8")

    outcome = api.revert(
        project_dir, "v1", ["src/main.py"],
        config=config, auto_save=False,
    )
    assert outcome.reverted_count == 1
    assert outcome.skipped == []
    # main.py rolled back; lib.py left alone.
    assert main_py.read_text(encoding="utf-8") == "print('hi')\n"
    assert lib_py.read_text(encoding="utf-8") == "# patched lib\n"


def test_revert_directory_restores_subtree(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    (project_dir / "src" / "lib.py").write_text("# patched lib\n", encoding="utf-8")
    # Non-target directory should remain untouched.
    (project_dir / "data" / "input.txt").write_text("KEEP\n", encoding="utf-8")

    outcome = api.revert(
        project_dir, "v1", ["src"],
        config=config, auto_save=False,
    )
    assert outcome.reverted_count == 2
    assert (project_dir / "src" / "main.py").read_text(
        encoding="utf-8",
    ) == "print('hi')\n"
    assert (project_dir / "src" / "lib.py").read_text(
        encoding="utf-8",
    ) == "def f():\n    return 1\n"
    # Untouched outside selected prefix.
    assert (project_dir / "data" / "input.txt").read_text(
        encoding="utf-8",
    ) == "KEEP\n"


def test_revert_creates_pre_revert_snapshot(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# different\n", encoding="utf-8")

    outcome = api.revert(
        project_dir, "v1", ["src/main.py"],
        config=config, auto_save=True,
    )
    assert outcome.pre_revert is not None
    assert outcome.pre_revert.name.startswith("auto-pre-revert-")
    snaps = {s.name for s in api.list_snapshots(project_dir, config=config)}
    assert outcome.pre_revert.name in snaps


def test_revert_skip_unknown_path(project_dir, config):
    api.save(project_dir, "v1", config=config)
    outcome = api.revert(
        project_dir, "v1", ["does/not/exist.txt"],
        config=config, auto_save=False,
    )
    assert outcome.reverted_count == 0
    assert outcome.skipped == [("does/not/exist.txt", "not in snapshot")]


def test_revert_delete_extras_removes_added_files(project_dir, config):
    api.save(project_dir, "v1", config=config)
    # Add a file under src/ that isn't in v1.
    extra = project_dir / "src" / "extra.py"
    extra.write_text("x = 1\n", encoding="utf-8")

    outcome = api.revert(
        project_dir, "v1", ["src"],
        config=config, auto_save=False, delete_extras=True,
    )
    assert outcome.deleted_count == 1
    assert not extra.exists()


def test_revert_requires_path(project_dir, config):
    api.save(project_dir, "v1", config=config)
    with pytest.raises(ValueError):
        api.revert(project_dir, "v1", [], config=config, auto_save=False)


def test_revert_unknown_snapshot_raises(project_dir, config):
    with pytest.raises(FileNotFoundError):
        api.revert(
            project_dir, "ghost", ["src/main.py"],
            config=config, auto_save=False,
        )


# ----------------- CLI text mode -----------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_revert_explicit_path_text(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    capsys.readouterr()

    rc = cli.main([
        "revert", "v1", "src/main.py",
        "--path", str(project_dir),
        "--no-auto-save", "-y", "--text",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "reverted" in out
    assert (project_dir / "src" / "main.py").read_text(
        encoding="utf-8",
    ) == "print('hi')\n"


def test_cli_revert_text_mode_requires_paths(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "revert", "v1",
        "--path", str(project_dir),
        "--text",
    ])
    err = capsys.readouterr().err
    assert rc != 0
    assert "no paths" in err
