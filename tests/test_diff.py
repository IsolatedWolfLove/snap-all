"""Diff API and CLI text-mode tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from snapz import api, cli


# ----------------- diff API -------------------------------------------------


def test_diff_two_snapshots_no_change(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    result = api.diff(project_dir, "v1", "v2", config=config)
    assert result.changes == []


def test_diff_picks_up_modified_file(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)

    result = api.diff(project_dir, "v1", "v2", config=config)
    paths = {c.path: c.status for c in result.changes}
    assert paths == {"src/main.py": "M"}
    [c] = result.modified
    assert c.sha_a != c.sha_b
    assert c.size_a is not None and c.size_b is not None


def test_diff_picks_up_added_and_deleted(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").unlink()
    (project_dir / "fresh.txt").write_text("hi\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)

    result = api.diff(project_dir, "v1", "v2", config=config)
    statuses = {c.path: c.status for c in result.changes}
    assert statuses == {"src/main.py": "D", "fresh.txt": "A"}


def test_diff_against_live_tree(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# live edit\n", encoding="utf-8")
    (project_dir / "new.py").write_text("x=1\n", encoding="utf-8")

    result = api.diff(project_dir, "v1", config=config)
    statuses = {c.path: c.status for c in result.changes}
    assert statuses["src/main.py"] == "M"
    assert statuses["new.py"] == "A"
    assert result.b_meta is None


def test_diff_legacy_tar_rejected(project_dir, config, snap_root):
    """Diff should refuse legacy tar snapshots with a clear ValueError."""

    import json
    import tarfile

    from snapz.store import Store

    store = Store(config)
    dir_root = store.dir_for(project_dir.resolve())
    dir_root.mkdir(parents=True, exist_ok=True)
    archive_path = dir_root / "old.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(str(project_dir / "README.md"), arcname="README.md")
    (dir_root / "old.meta.json").write_text(
        json.dumps({
            "name": "old",
            "source": str(project_dir.resolve()),
            "created": "2026-01-01T00:00:00",
            "size_bytes": archive_path.stat().st_size,
            "file_count": 1,
            "total_bytes_in": 10,
            "compression": "gzip",
            "archive": "old.tar.gz",
        }),
        encoding="utf-8",
    )
    store.ensure_dir(project_dir.resolve())
    api.save(project_dir, "new", config=config)

    with pytest.raises(ValueError, match="legacy"):
        api.diff(project_dir, "old", "new", config=config)


def test_diff_unknown_snapshot_raises(project_dir, config):
    api.save(project_dir, "v1", config=config)
    with pytest.raises(FileNotFoundError):
        api.diff(project_dir, "ghost", config=config)


def test_diff_detects_type_change(project_dir, config):
    """Replace a file with a symlink → status 'T'."""

    target = project_dir / "thing"
    target.write_text("content\n", encoding="utf-8")
    api.save(project_dir, "v1", config=config)

    target.unlink()
    target.symlink_to("README.md")
    api.save(project_dir, "v2", config=config)

    result = api.diff(project_dir, "v1", "v2", config=config)
    [c] = [c for c in result.changes if c.path == "thing"]
    assert c.status == "T"


# ----------------- add_local_excludes integration --------------------------


def test_add_local_excludes_persists_and_filters(project_dir, config):
    """Patterns added via the diff workflow should be honoured by the next save."""

    (project_dir / "junk.bin").write_bytes(b"x" * 100)
    api.save(project_dir, "v1", config=config)

    api.add_local_excludes(project_dir, ["junk.bin"], config=config)
    api.save(project_dir, "v2", config=config)

    diff = api.diff(project_dir, "v1", "v2", config=config)
    statuses = {c.path: c.status for c in diff.changes}
    assert statuses.get("junk.bin") == "D"  # excluded → not in v2


# ----------------- CLI text mode -------------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_diff_text_output(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    cli.main(["save", str(project_dir), "-n", "v2", "-y"])
    capsys.readouterr()

    rc = cli.main(["diff", "v1", "v2", "--path", str(project_dir), "--text"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "src/main.py" in out
    assert "modified" in out


def test_cli_diff_against_live(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    (project_dir / "extra.txt").write_text("x", encoding="utf-8")
    capsys.readouterr()
    rc = cli.main(["diff", "v1", "--path", str(project_dir), "--text"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "extra.txt" in out
    assert "added" in out
