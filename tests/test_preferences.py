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


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root
