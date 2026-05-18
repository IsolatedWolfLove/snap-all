"""P5 ``snapz browse`` CLI wiring without entering curses."""

from __future__ import annotations

import json

import pytest

from snapz import cli


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_browse_json_lists_manifest_entries(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["browse", "v1", "--path", str(project_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["snapshot"] == "v1"
    paths = {entry["path"] for entry in payload["entries"]}
    assert {"README.md", "src/main.py", "src/lib.py"}.issubset(paths)


def test_browse_non_tty_prints_paths(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main(["browse", "v1", "--path", str(project_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "README.md" in out
    assert "src/main.py" in out


def test_browse_missing_snapshot_errors(env_root, project_dir, capsys):
    rc = cli.main(["browse", "ghost", "--path", str(project_dir), "--json"])
    err = capsys.readouterr().err

    assert rc == cli.EXIT_ERROR
    assert "ghost" in err
