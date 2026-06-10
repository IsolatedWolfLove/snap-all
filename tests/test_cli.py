"""CLI smoke tests via ``main([...])``."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from snapz import api, cas, cli, filecache, preferences, self_update
from snapz.store import Store


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


def test_update_installs_release_deb(env_root, monkeypatch, capsys):
    from snapz import self_update

    calls = []
    plan = self_update.DebUpdatePlan(
        tag="v9.0.0",
        package=self_update.CLIENT_PACKAGE_NAME,
        asset_name="snapz-cli_9.0.0_all.deb",
        download_url="https://example.test/snapz-cli_9.0.0_all.deb",
        language="en",
    )
    result = self_update.DebUpdateResult(
        ok=True,
        plan=plan,
        deb_path=Path("/tmp/snapz-cli_9.0.0_all.deb"),
        command=["apt", "install", "-y", "/tmp/snapz-cli_9.0.0_all.deb"],
        returncode=0,
    )

    def fake_plan_update(**kwargs):
        calls.append(("plan", kwargs))
        return plan

    def fake_install_plan(plan_arg):
        calls.append(("install", plan_arg))
        return result

    monkeypatch.setattr(self_update, "plan_update", fake_plan_update)
    monkeypatch.setattr(self_update, "install_plan", fake_install_plan)

    rc = cli.main(["update"])
    out = capsys.readouterr().out

    assert rc == 0
    assert calls[0][0] == "plan"
    assert calls[0][1]["language"] == "en"
    assert calls[0][1]["package"] == self_update.CLIENT_PACKAGE_NAME
    assert calls[1] == ("install", plan)
    assert "snapz-cli_9.0.0_all.deb" in out


def test_uninstall_keeps_data_when_user_says_no(
    env_root, monkeypatch, capsys
):
    calls = []

    class Result:
        returncode = 0

    def fake_run_pip(args):
        calls.append(args)
        return Result()

    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(self_update, "deb_package_installed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_run_pip", fake_run_pip)
    (env_root / "registry.json").write_text("{}", encoding="utf-8")

    rc = cli.main(["uninstall"])
    out = capsys.readouterr().out

    assert rc == 0
    assert env_root.exists()
    assert calls == [["uninstall", "-y", cli.SNAPZ_PACKAGE_NAME]]
    assert "data size" in out
    assert "GB" in out


def test_uninstall_deletes_data_when_user_says_yes(
    env_root, monkeypatch, capsys
):
    calls = []

    class Result:
        returncode = 0

    def fake_run_pip(args):
        calls.append(args)
        return Result()

    answers = iter(["y", "y"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(self_update, "deb_package_installed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_run_pip", fake_run_pip)
    (env_root / "registry.json").write_text("{}", encoding="utf-8")

    rc = cli.main(["uninstall"])
    out = capsys.readouterr().out

    assert rc == 0
    assert not env_root.exists()
    assert calls == [["uninstall", "-y", cli.SNAPZ_PACKAGE_NAME]]
    assert "deleted data" in out


def test_uninstall_yes_purge_data_is_non_interactive(env_root, monkeypatch):
    calls = []

    class Result:
        returncode = 0

    def fake_run_pip(args):
        calls.append(args)
        return Result()

    def fail_input(*_args, **_kwargs):
        raise AssertionError("uninstall -y --purge-data should not prompt")

    monkeypatch.setattr("builtins.input", fail_input)
    monkeypatch.setattr(self_update, "deb_package_installed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_run_pip", fake_run_pip)
    (env_root / "registry.json").write_text("{}", encoding="utf-8")

    rc = cli.main(["uninstall", "-y", "--purge-data"])

    assert rc == 0
    assert not env_root.exists()
    assert calls == [["uninstall", "-y", cli.SNAPZ_PACKAGE_NAME]]


def test_uninstall_deb_uses_package_manager(env_root, monkeypatch):
    calls = []
    unregister_calls = []
    remote_config = env_root / "_remote.json"
    remote_config.write_text("{}", encoding="utf-8")

    def fake_remove(package):
        calls.append(package)
        return subprocess.CompletedProcess(["apt", "remove", "-y", package], 0)

    monkeypatch.setattr(
        "snapz._cli_log_self.remote.unregister_device",
        lambda config=None: unregister_calls.append(config) or True,
    )
    monkeypatch.setattr(self_update, "deb_package_installed", lambda package: True)
    monkeypatch.setattr(self_update, "remove_deb_package", fake_remove)
    monkeypatch.setattr(cli, "_run_pip", lambda *_args: (_ for _ in ()).throw(
        AssertionError("deb uninstall should not call pip")
    ))

    rc = cli.main(["uninstall", "-y"])

    assert rc == 0
    assert calls == [cli.SNAPZ_PACKAGE_NAME]
    assert len(unregister_calls) == 1
    assert not remote_config.exists()


def test_uninstall_zipapp_deletes_current_executable(env_root, monkeypatch, tmp_path):
    executable = tmp_path / "snapz"
    with zipfile.ZipFile(executable, "w") as zf:
        zf.writestr("__main__.py", "print('snapz')\n")

    monkeypatch.setattr(sys, "argv", [str(executable), "uninstall", "-y"])
    monkeypatch.setattr(self_update, "deb_package_installed", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_run_pip", lambda *_args: (_ for _ in ()).throw(
        AssertionError("zipapp uninstall should not call pip")
    ))

    rc = cli.main(["uninstall", "-y"])

    assert rc == 0
    assert not executable.exists()


def test_list_subcommand_prints_table(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    assert "FILES" in out


def test_list_timeline_groups_snapshots(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["list", str(project_dir), "--text", "--timeline"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Today" in out
    assert "v1" in out


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


def test_export_accepts_yes_for_script_compat(env_root, project_dir, tmp_path):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    dst = tmp_path / "exported"

    rc = cli.main([
        "export", "v1", str(dst), "--path", str(project_dir), "-y",
    ])

    assert rc == 0
    assert (dst / "README.md").exists()


def test_check_accepts_path_option(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])

    rc = cli.main(["check", "--path", str(project_dir)])

    assert rc == 0


def test_migrate_accepts_path_option(env_root, project_dir):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])

    rc = cli.main(["migrate", "--path", str(project_dir), "--dry-run"])

    assert rc == 0


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


def test_bare_invocation_remote_only_starts_background_push(
    env_root, project_dir, monkeypatch, capsys,
):
    preferences.set_config_value(env_root, "remote_only", "true")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return object()

    monkeypatch.setattr("snapz._api_core.subprocess.Popen", fake_popen)
    answers = iter(["v1", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))

    rc = cli.main([str(project_dir)])

    assert rc == 0
    capsys.readouterr()
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-3:] == ["snapz", "push", "all"]
    assert kwargs["env"]["SNAPZ_ALL_ROOT"] == str(env_root)


def test_bare_invocation_lists_existing_snapshots_before_prompt(
    env_root, project_dir, monkeypatch, capsys
):
    assert cli.main(["save", str(project_dir), "-n", "v1", "-y"]) == 0
    capsys.readouterr()

    answers = iter(["v2", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))

    rc = cli.main([str(project_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "existing 1 snapshot" in out
    assert "v1" in out
    assert "saved v2" in out


def test_bare_invocation_aborts_on_no(
    env_root, project_dir, monkeypatch, capsys
):
    answers = iter(["v1", "n"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    rc = cli.main([str(project_dir)])
    assert rc == cli.EXIT_USER_ABORT
    assert api.list_snapshots(project_dir) == []


def test_interactive_save_can_apply_exclude_suggestions(
    env_root, project_dir, monkeypatch, capsys
):
    cache_dir = project_dir / "coverage"
    cache_dir.mkdir()
    (cache_dir / "blob.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    answers = iter(["v1", "", "y", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    rc = cli.main([str(project_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Possible local excludes" in out
    store = Store(api.default_config())
    local = preferences.read_local_excludes(store.dir_for(project_dir.resolve()))
    assert "coverage/" in local
    manifest = cas.read_manifest(cas.find_manifest_path(store.dir_for(project_dir.resolve()), "v1"))
    assert all(not entry.path.startswith("coverage/") for entry in manifest.entries)


def test_first_interactive_save_records_source_config_defaults(
    env_root, project_dir, monkeypatch, capsys
):
    answers = iter(["v1", "", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    rc = cli.main([str(project_dir)])

    assert rc == 0
    store = Store(api.default_config())
    source_config = preferences.read_source_config(store.dir_for(project_dir.resolve()))
    assert source_config["configured"] is True
    assert source_config["profile"] == "default"


def test_first_interactive_save_can_include_build_preset(
    env_root, project_dir, monkeypatch, capsys
):
    (project_dir / "build").mkdir()
    (project_dir / "build" / "artifact.txt").write_text("keep\n", encoding="utf-8")
    answers = iter(["v1", "build", "y", ""])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    rc = cli.main([str(project_dir)])

    assert rc == 0
    store = Store(api.default_config())
    manifest = cas.read_manifest(
        cas.find_manifest_path(store.dir_for(project_dir.resolve()), "v1")
    )
    assert any(entry.path == "build/artifact.txt" for entry in manifest.entries)
    source_config = preferences.read_source_config(store.dir_for(project_dir.resolve()))
    assert source_config["include_presets"] == ["build"]


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
