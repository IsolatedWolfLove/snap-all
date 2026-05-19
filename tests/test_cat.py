"""P5 ``snapz cat`` command."""

from __future__ import annotations

import json

import pytest

from snapz import api, cli
from snapz._tui_browser import BrowseAction


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cat_prints_text_file(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "cat", "v1", "README.md", "--path", str(project_dir),
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert out == "# demo\n"


def test_cat_missing_path_errors(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "cat", "v1", "missing.txt", "--path", str(project_dir),
    ])
    err = capsys.readouterr().err

    assert rc == cli.EXIT_ERROR
    assert "missing.txt" in err
    assert "v1" in err


def test_cat_binary_default_is_terminal_safe(env_root, project_dir, capsys):
    (project_dir / "blob.bin").write_bytes(b"a\x00b\xff")
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "cat", "v1", "blob.bin", "--path", str(project_dir),
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "binary" in out.lower()
    assert "4 B" in out


def test_cat_raw_writes_exact_bytes(env_root, project_dir, capfdbinary):
    payload = b"\x00snapz\xff\n"
    (project_dir / "blob.bin").write_bytes(payload)
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capfdbinary.readouterr()

    rc = cli.main([
        "cat", "v1", "blob.bin", "--path", str(project_dir), "--raw",
    ])
    out = capfdbinary.readouterr().out

    assert rc == 0
    assert out == payload


def test_cat_json_reports_metadata_without_content(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "cat", "v1", "README.md", "--path", str(project_dir), "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload == {
        "snapshot": "v1",
        "path": "README.md",
        "bytes": len("# demo\n".encode()),
        "binary": False,
    }


def test_cat_interactive_missing_relpath_opens_file_picker(
    env_root, project_dir, monkeypatch, capsys
):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    calls = []

    def fake_browse(entries, **kwargs):
        calls.append((list(entries), kwargs))
        return BrowseAction(kind="file", path="README.md")

    monkeypatch.setattr("snapz.tui.browse_manifest", fake_browse)

    rc = cli.main(["cat", "v1", "--path", str(project_dir)])
    out = capsys.readouterr().out

    assert rc == 0
    assert out == "# demo\n"
    assert calls
    assert calls[0][1]["mode"] == "view"
    assert calls[0][1]["preview"] is None


def test_cat_all_accepts_hidden_auto_snapshot(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "README.md").write_text("patched\n", encoding="utf-8")
    cli.main(["restore", "v1", "--path", str(project_dir), "-y"])
    capsys.readouterr()
    auto_name = next(
        snap.name for snap in api.list_snapshots(project_dir)
        if snap.name.startswith("auto-pre-restore-")
    )

    rc = cli.main([
        "cat", auto_name, "README.md", "--path", str(project_dir), "--all",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert out == "patched\n"
