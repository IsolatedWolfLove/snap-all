"""Config + local-excludes preference store."""

from __future__ import annotations

import pytest

from snapz import api, preferences
from snapz.store import Store


# ---------------- config ----------------------------------------------------


def test_config_default_returned_when_unset(snap_root):
    assert preferences.get_config_value(snap_root, "save_picker") is False


def test_config_set_persists_across_loads(snap_root):
    preferences.set_config_value(snap_root, "save_picker", "true")
    assert preferences.get_config_value(snap_root, "save_picker") is True
    # Fresh read from disk
    assert preferences.load_config(snap_root) == {"save_picker": True}


def test_config_set_validates_value(snap_root):
    with pytest.raises(ValueError):
        preferences.set_config_value(snap_root, "save_picker", "maybe")


def test_config_unknown_key_rejected(snap_root):
    with pytest.raises(KeyError):
        preferences.set_config_value(snap_root, "nope", "1")
    with pytest.raises(KeyError):
        preferences.get_config_value(snap_root, "nope")


def test_config_unset_returns_false_when_absent(snap_root):
    assert preferences.unset_config_value(snap_root, "save_picker") is False


def test_config_unset_removes_override(snap_root):
    preferences.set_config_value(snap_root, "save_picker", "yes")
    assert preferences.unset_config_value(snap_root, "save_picker") is True
    assert preferences.get_config_value(snap_root, "save_picker") is False


def test_config_effective_overlays_defaults(snap_root):
    preferences.set_config_value(snap_root, "color", "never")
    eff = preferences.effective_config(snap_root)
    assert eff["color"] == "never"
    assert eff["save_picker"] is False  # untouched -> default


def test_default_config_reads_remote_only_env(monkeypatch):
    from snapz.config import default_config

    monkeypatch.setenv("SNAPZ_REMOTE_ONLY", "1")

    assert default_config().remote_only is True


def test_api_save_starts_remote_only_push_in_background(project_dir, config, monkeypatch):
    preferences.set_config_value(config.root, "remote_only", "true")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return object()

    monkeypatch.setattr("snapz._api_core.subprocess.Popen", fake_popen)

    api.save(project_dir, "remote-only", config=config)

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-3:] == ["snapz", "push", "all"]
    assert kwargs["stdin"] is not None
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is not None
    assert kwargs["close_fds"] is True
    assert kwargs["env"]["SNAPZ_ALL_ROOT"] == str(config.root)


def test_api_save_background_push_preserves_runtime_remote_only(
    project_dir, snap_root, monkeypatch,
):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return object()

    monkeypatch.setattr("snapz._api_core.subprocess.Popen", fake_popen)

    from snapz.config import RuntimeConfig

    api.save(project_dir, "runtime-remote-only", config=RuntimeConfig(
        root=snap_root,
        remote_only=True,
    ))

    assert len(calls) == 1
    assert calls[0][1]["env"]["SNAPZ_REMOTE_ONLY"] == "1"


# ---------------- ui_mode ---------------------------------------------------


def test_get_ui_mode_default_is_tui(snap_root):
    assert preferences.get_ui_mode(snap_root) == preferences.UI_MODE_TUI


def test_get_ui_mode_returns_minimal_when_set(snap_root):
    preferences.set_config_value(snap_root, "ui_mode", "minimal")
    assert preferences.get_ui_mode(snap_root) == preferences.UI_MODE_MINIMAL


def test_get_ui_mode_falls_back_to_tui_on_invalid_value(snap_root, tmp_path):
    # Write a config with an unrecognised ui_mode value directly to disk.
    import json
    cfg_path = snap_root / preferences.CONFIG_FILENAME
    snap_root.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"ui_mode": "fancy"}), encoding="utf-8")
    assert preferences.get_ui_mode(snap_root) == preferences.UI_MODE_TUI


def test_set_ui_mode_persists(snap_root):
    preferences.set_ui_mode(snap_root, "minimal")
    assert preferences.get_ui_mode(snap_root) == preferences.UI_MODE_MINIMAL
    # Verify it's actually on disk.
    assert preferences.load_config(snap_root)["ui_mode"] == "minimal"


def test_set_ui_mode_rejects_invalid_value(snap_root):
    with pytest.raises(ValueError):
        preferences.set_ui_mode(snap_root, "fancy")


# ---------------- local excludes -------------------------------------------


def test_local_excludes_round_trip(project_dir, config):
    dir_root = Store(config).dir_for(project_dir.resolve())
    dir_root.mkdir(parents=True, exist_ok=True)

    assert preferences.read_local_excludes(dir_root) == []

    added = preferences.append_local_excludes(dir_root, ["build/", "*.log"])
    assert added == 2
    assert preferences.read_local_excludes(dir_root) == ["build/", "*.log"]

    # Idempotent: re-adding existing patterns is a no-op.
    assert preferences.append_local_excludes(dir_root, ["build/"]) == 0
    assert preferences.read_local_excludes(dir_root) == ["build/", "*.log"]


def test_local_excludes_apply_to_snapshot_walk(project_dir, config):
    dir_root = Store(config).dir_for(project_dir.resolve())
    dir_root.mkdir(parents=True, exist_ok=True)
    # Create a file we plan to exclude.
    (project_dir / "secret.txt").write_text("shh", encoding="utf-8")

    preferences.append_local_excludes(dir_root, ["secret.txt"])

    outcome = api.save(project_dir, "v1", config=config)
    rels = []
    from snapz import cas
    manifest = cas.read_manifest(
        dir_root / "snapshots" / "v1.manifest.json"
    )
    rels = [e.path for e in manifest.entries if e.type == "file"]
    assert "secret.txt" not in rels
    assert "src/main.py" in rels


# ---------------- CLI ------------------------------------------------------


def test_cli_config_set_get_unset(env_root, capsys):
    from snapz import cli

    rc = cli.main(["config", "set", "save_picker", "true"])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["config", "get", "save_picker"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert "true" in out

    rc = cli.main(["config", "unset", "save_picker"])
    assert rc == 0


def test_cli_config_list_shows_known_keys(env_root, capsys):
    from snapz import cli

    rc = cli.main(["config", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    for key in preferences.KNOWN_CONFIG_KEYS:
        assert key in out


def test_cli_config_unknown_key_errors(env_root, capsys):
    from snapz import cli

    rc = cli.main(["config", "set", "made_up_key", "1"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "made_up_key" in err or "unknown" in err.lower()


def test_cli_config_remote_only_prompts_for_cron(env_root, monkeypatch, capsys):
    from snapz import cli
    from snapz import _cli_common

    installed: list[object] = []

    monkeypatch.setattr(_cli_common.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_cli_common.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(
        "snapz.scheduler.install_remote_sync_cron",
        lambda root: installed.append(root) or type(
            "Result",
            (),
            {
                "updated": False,
                "command": "0 */3 * * * snapz push all; snapz pull all",
            },
        )(),
    )

    rc = cli.main(["config", "set", "remote_only", "true"])

    assert rc == 0
    assert installed == [env_root]
    out = capsys.readouterr().out
    assert "cron installed" in out


def test_cli_config_remote_only_json_does_not_prompt_for_cron(env_root, monkeypatch):
    from snapz import cli
    from snapz import _cli_common

    monkeypatch.setattr(_cli_common.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_cli_common.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        "snapz.scheduler.install_remote_sync_cron",
        lambda _root: (_ for _ in ()).throw(AssertionError("should not install")),
    )

    rc = cli.main(["config", "set", "remote_only", "true", "--json"])

    assert rc == 0


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root
