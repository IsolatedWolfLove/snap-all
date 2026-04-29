"""``snapz undo`` — pops the most recent auto-pre-* safety snapshot,
chains back to "initial" with repeated calls, and stays invisible to
the user-facing listings."""

from __future__ import annotations

import json

import pytest

from snapz import api, cli
from snapz.util import is_auto_snapshot, is_undo_snapshot


# ----------------- API -------------------------------------------------------


def test_undo_target_none_when_no_auto_pre(project_dir, config):
    api.save(project_dir, "v1", config=config)
    assert api.find_undo_target(project_dir, config=config) is None


def test_undo_chain_walks_back_to_initial(project_dir, config):
    """Two destructive ops + two undos must end up back at the original tree."""

    main_py = project_dir / "src" / "main.py"
    initial = main_py.read_text(encoding="utf-8")

    # Snapshot original state, then a "future" state we'll never want.
    api.save(project_dir, "good", config=config)
    main_py.write_text("# v2\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)
    main_py.write_text("# v3\n", encoding="utf-8")

    # First destructive op: restore "good" (creates auto-pre-restore #1
    # capturing the v3 state).
    api.restore(project_dir, "good", config=config, auto_save=True, clean=True)
    assert main_py.read_text(encoding="utf-8") == initial

    # Now mutate again, then revert (creates auto-pre-revert #2).
    main_py.write_text("# patched\n", encoding="utf-8")
    api.revert(
        project_dir, "v2", ["src/main.py"],
        config=config, auto_save=True,
    )
    assert main_py.read_text(encoding="utf-8") == "# v2\n"

    # First undo: should restore the state captured before the revert
    # (i.e. the patched content), and consume that auto-pre-revert.
    out = api.undo(project_dir, config=config)
    assert out.snapshot.name.startswith("auto-pre-revert-")
    assert main_py.read_text(encoding="utf-8") == "# patched\n"

    # Second undo: walks back further to the auto-pre-restore captured
    # right before the original `restore "good"` call.
    out = api.undo(project_dir, config=config)
    assert out.snapshot.name.startswith("auto-pre-restore-")
    assert main_py.read_text(encoding="utf-8") == "# v3\n"
    assert out.remaining == 0

    # No more undo points: third undo errors instead of bouncing.
    with pytest.raises(FileNotFoundError):
        api.undo(project_dir, config=config)


def test_undo_does_not_create_new_safety_snapshot(project_dir, config):
    """The undo machinery deliberately runs with auto_save=False so the
    chain doesn't fork/loop — repeated undo just walks the existing
    auto-pre-* timeline."""

    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    api.restore(project_dir, "v1", config=config, auto_save=True, clean=False)

    before = {
        s.name for s in api.list_snapshots(project_dir, config=config)
        if is_undo_snapshot(s.name)
    }
    api.undo(project_dir, config=config)
    after = {
        s.name for s in api.list_snapshots(project_dir, config=config)
        if is_undo_snapshot(s.name)
    }
    # The consumed snapshot is gone and no new auto-pre-* takes its place.
    assert len(after) == len(before) - 1


def test_undo_clean_restores_byte_identical_state(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.restore(project_dir, "v1", config=config, auto_save=True, clean=False)
    # Add a file that wasn't in the captured pre-restore tree.
    (project_dir / "added_after.txt").write_text("new\n", encoding="utf-8")

    api.undo(project_dir, config=config, clean=True)
    assert not (project_dir / "added_after.txt").exists()


# ----------------- CLI ------------------------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_undo_no_target(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["undo", "--path", str(project_dir), "-y"])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_ERROR
    assert "undo" in err.lower() or "auto-pre" in err


def test_cli_undo_text_mode(env_root, project_dir, capsys):
    main_py = project_dir / "src" / "main.py"
    initial = main_py.read_text(encoding="utf-8")

    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    main_py.write_text("# patched\n", encoding="utf-8")
    cli.main(["restore", "v1", "--path", str(project_dir), "-y"])
    assert main_py.read_text(encoding="utf-8") == initial

    main_py.write_text("# patched again\n", encoding="utf-8")
    capsys.readouterr()

    rc = cli.main(["undo", "--path", str(project_dir), "-y"])
    out = capsys.readouterr().out
    assert rc == 0
    # Undo restored the captured pre-restore state (= patched).
    assert main_py.read_text(encoding="utf-8") == "# patched\n"
    assert "rolled back" in out or "已回到" in out


def test_cli_undo_json_dry_run(env_root, project_dir, capsys):
    """Without -y the JSON consumer gets a structured 'needs-confirmation'
    response and a non-zero exit code so scripts don't accidentally run."""

    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    cli.main(["restore", "v1", "--path", str(project_dir), "-y"])
    capsys.readouterr()

    rc = cli.main(["undo", "--path", str(project_dir), "--json"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_ERROR
    payload = json.loads(out)
    assert payload["undone"] is False
    assert payload["reason"] == "needs-confirmation"
    assert payload["target"]["name"].startswith("auto-pre-")


def test_cli_undo_json_executes_with_yes(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    cli.main(["restore", "v1", "--path", str(project_dir), "-y"])
    capsys.readouterr()

    rc = cli.main(["undo", "--path", str(project_dir), "--json", "-y"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["undone"] is True
    assert "outcome" in payload
    assert payload["outcome"]["snapshot"]["name"].startswith("auto-pre-")
