"""P5 ``snapz cat`` command."""

from __future__ import annotations

import json

import pytest

from snapz import cli


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
