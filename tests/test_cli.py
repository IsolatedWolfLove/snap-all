"""CLI smoke tests via ``main([...])``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from snapz import api, cli, filecache


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_save_subcommand_creates_archive(env_root, project_dir, capsys):
    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    assert rc == 0
    snaps = api.list_snapshots(project_dir)
    assert [s.name for s in snaps] == ["v1"]


def test_save_subcommand_rejects_duplicate(env_root, project_dir):
    assert cli.main(["save", str(project_dir), "-n", "v1", "-y"]) == 0
    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    assert rc == cli.EXIT_ERROR


def test_save_subcommand_overwrite(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y", "--overwrite"])
    assert rc == 0


def test_save_subcommand_no_cache(env_root, project_dir, monkeypatch):
    def fail_cache_load(*_args, **_kwargs):
        raise AssertionError("cache load should be bypassed by --no-cache")

    monkeypatch.setattr(filecache, "load", fail_cache_load)

    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y", "--no-cache"])

    assert rc == 0


def test_save_subcommand_workers_flag(env_root, project_dir, monkeypatch):
    seen = {}
    real_save = api.save

    def spy(*args, **kwargs):
        seen["workers"] = kwargs["config"].save_workers
        return real_save(*args, **kwargs)

    monkeypatch.setattr(api, "save", spy)

    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y", "--workers", "1"])

    assert rc == 0
    assert seen["workers"] == 1


def test_save_subcommand_workers_rejects_zero(env_root, project_dir, capsys):
    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y", "--workers", "0"])

    assert rc == cli.EXIT_ERROR
    assert "workers" in capsys.readouterr().err


def test_gc_rebuild_index_flag(env_root, project_dir, monkeypatch):
    api.save(project_dir, "v1")
    seen = {}
    real_gc = api.gc

    def spy(*args, **kwargs):
        seen["rebuild_index"] = kwargs.get("rebuild_index")
        return real_gc(*args, **kwargs)

    monkeypatch.setattr(api, "gc", spy)

    rc = cli.main(["gc", "--path", str(project_dir), "--rebuild-index"])

    assert rc == 0
    assert seen["rebuild_index"] is True


def test_list_subcommand_prints_table(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    assert "FILES" in out


def test_alist_subcommand(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["alist"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out


def test_show_subcommand(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["show", "v1", "--path", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    # New layout shows compression name inline with the archive line
    # (e.g. ``archive  v1.tar.zst  (zstd)`` or ``(gzip)``).
    assert "zstd" in out or "gzip" in out
    assert "archive" in out
    assert "files" in out


def test_show_subcommand_missing(env_root, project_dir, capsys):
    rc = cli.main(["show", "ghost", "--path", str(project_dir)])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_ERROR
    assert "ghost" in err


def test_rm_with_yes(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    rc = cli.main(["rm", "v1", "--path", str(project_dir), "-y"])
    assert rc == 0
    assert api.list_snapshots(project_dir) == []


def test_mv(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    rc = cli.main(["mv", "v1", "production", "--path", str(project_dir)])
    assert rc == 0
    snaps = api.list_snapshots(project_dir)
    assert [s.name for s in snaps] == ["production"]


def test_bare_invocation_runs_interactive_with_inputs(
    env_root, project_dir, monkeypatch, capsys
):
    # Inputs: name, confirm, note
    answers = iter(["v1", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    rc = cli.main([str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "saved v1" in out
    assert api.list_snapshots(project_dir)[0].name == "v1"


def test_bare_invocation_aborts_on_no(
    env_root, project_dir, monkeypatch, capsys
):
    answers = iter(["v1", "n"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    rc = cli.main([str(project_dir)])
    assert rc == cli.EXIT_USER_ABORT
    assert api.list_snapshots(project_dir) == []


def test_bare_invocation_default_name(
    env_root, project_dir, monkeypatch, capsys
):
    # Inputs: name (default), confirm, note (blank)
    answers = iter(["", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    rc = cli.main([str(project_dir)])
    assert rc == 0
    assert api.list_snapshots(project_dir)[0].name.startswith("auto-")


def test_version_flag_does_not_enter_bare_mode(env_root, capsys):
    """``snapz --version`` must defer to argparse, not be treated as a path."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "snapz" in out


def test_help_flag_does_not_enter_bare_mode(env_root, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_keyboard_interrupt_in_prompt_aborts_cleanly(
    env_root, project_dir, monkeypatch, capsys
):
    """Ctrl-C during the interactive prompt must not raise out of main()."""

    def boom(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", boom)
    rc = cli.main([str(project_dir)])
    assert rc == cli.EXIT_USER_ABORT
    assert "aborted" in capsys.readouterr().err


def test_double_keyboard_interrupt_during_abort_message(
    env_root, project_dir, monkeypatch, capsys
):
    """A second SIGINT while emitting 'aborted.' must still return cleanly."""

    state = {"count": 0}

    def boom(*_args, **_kwargs):
        raise KeyboardInterrupt

    real_write = sys.stderr.write

    def hostile_write(text):
        # First call (the abort notice) raises another KI; subsequent
        # calls (e.g. flush) succeed.
        state["count"] += 1
        if state["count"] == 1:
            raise KeyboardInterrupt
        return real_write(text)

    monkeypatch.setattr("builtins.input", boom)
    monkeypatch.setattr(sys.stderr, "write", hostile_write)
    rc = cli.main([str(project_dir)])
    assert rc == cli.EXIT_USER_ABORT
