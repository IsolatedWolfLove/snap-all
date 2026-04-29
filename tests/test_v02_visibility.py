"""auto-* safety snapshots are hidden from default user-facing listings,
``--all`` reveals them, and ``--json`` emits structured data."""

from __future__ import annotations

import json

import pytest

from snapz import api, cli


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def _make_state_with_auto(env_root, project_dir):
    """Save + restore so an ``auto-pre-restore-*`` snapshot exists."""
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    cli.main(["restore", "v1", "--path", str(project_dir), "-y"])


# ----------------- list -----------------------------------------------------


def test_list_hides_auto_by_default(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    assert "auto-pre-restore-" not in out
    # The footer reminds the user how to see them.
    assert "--all" in out


def test_list_all_reveals_auto(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir), "--all"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    assert "auto-pre-restore-" in out


def test_list_json_payload_excludes_auto(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    names = [s["name"] for s in payload["snapshots"]]
    assert "v1" in names
    assert all(not n.startswith("auto-") for n in names)
    assert payload["hidden_auto"] >= 1
    assert payload["show_auto"] is False


def test_list_json_with_all_shows_everything(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["list", str(project_dir), "--all", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    names = [s["name"] for s in payload["snapshots"]]
    assert any(n.startswith("auto-pre-restore-") for n in names)
    assert payload["hidden_auto"] == 0


# ----------------- alist ----------------------------------------------------


def test_alist_text_hides_auto(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["alist"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "v1" in out
    assert "auto-pre-restore-" not in out


def test_alist_json_groups_dirs(env_root, project_dir, capsys):
    _make_state_with_auto(env_root, project_dir)
    capsys.readouterr()
    rc = cli.main(["alist", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["show_auto"] is False
    assert payload["dirs"]
    names = [s["name"] for d in payload["dirs"] for s in d["snapshots"]]
    assert "v1" in names
    assert all(not n.startswith("auto-") for n in names)


# ----------------- show + save JSON ------------------------------------------


def test_show_json(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["show", "v1", "--path", str(project_dir), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["name"] == "v1"
    assert payload["file_count"] > 0


def test_save_json_emits_structured_outcome(env_root, project_dir, capsys):
    rc = cli.main([
        "save", str(project_dir), "-n", "v1", "-y", "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["snapshot"]["name"] == "v1"
    assert payload["pack"]["file_count"] >= 1
    assert "elapsed_seconds" in payload


# ----------------- --json position-independent ------------------------------


def test_json_flag_works_after_subcommand(env_root, project_dir, capsys):
    """``snapz list --json`` must work, not just ``snapz --json list``."""

    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["list", str(project_dir), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    json.loads(out)  # raises if it isn't valid JSON
