"""``snapz find`` — locate a path / glob across every CAS snapshot of a
source directory."""

from __future__ import annotations

import json

import pytest

from snapz import api, cli


# ----------------- API -------------------------------------------------------


def test_find_exact_path_returns_all_snapshots(project_dir, config):
    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# v2\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)
    (project_dir / "src" / "main.py").write_text("# v3\n", encoding="utf-8")
    api.save(project_dir, "v3", config=config)

    res = api.find(project_dir, "src/main.py", config=config)
    assert "src/main.py" in res.by_path
    hits = res.by_path["src/main.py"]
    # Newest first.
    assert [h.snapshot.name for h in hits] == ["v3", "v2", "v1"]
    # Each consecutive pair has different content -> changed flag.
    assert hits[0].changed_from_prev is False  # newest, no "prev"
    assert hits[1].changed_from_prev is True
    assert hits[2].changed_from_prev is True


def test_find_unchanged_blob_marks_no_change(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)  # same content

    res = api.find(project_dir, "src/main.py", config=config)
    hits = res.by_path["src/main.py"]
    # v2 is newest; v1 is older with same sha -> changed_from_prev=False.
    assert hits[1].changed_from_prev is False


def test_find_directory_prefix_matches_subtree(project_dir, config):
    api.save(project_dir, "v1", config=config)
    res = api.find(project_dir, "src", config=config)
    matched = set(res.by_path.keys())
    assert {"src/main.py", "src/lib.py"}.issubset(matched)
    assert "data/input.txt" not in matched


def test_find_glob_extension(project_dir, config):
    api.save(project_dir, "v1", config=config)
    res = api.find(project_dir, "**/*.py", config=config)
    assert "src/main.py" in res.by_path
    assert "src/lib.py" in res.by_path
    assert "README.md" not in res.by_path


def test_find_excludes_auto_by_default(project_dir, config):
    """auto-* safety snapshots are out of the user-facing scan unless
    --all is passed."""

    api.save(project_dir, "v1", config=config)
    (project_dir / "src" / "main.py").write_text("# patched\n", encoding="utf-8")
    api.restore(project_dir, "v1", config=config, auto_save=True, clean=False)

    plain = api.find(project_dir, "src/main.py", config=config)
    snap_names = {h.snapshot.name for h in plain.by_path.get("src/main.py", [])}
    assert all(not n.startswith("auto-") for n in snap_names)

    with_auto = api.find(
        project_dir, "src/main.py", config=config, include_auto=True,
    )
    auto_names = {h.snapshot.name for h in with_auto.by_path["src/main.py"]}
    assert any(n.startswith("auto-pre-restore-") for n in auto_names)


def test_find_no_matches(project_dir, config):
    api.save(project_dir, "v1", config=config)
    res = api.find(project_dir, "does/not/exist.bin", config=config)
    assert res.by_path == {}
    assert res.total_hits == 0


# ----------------- CLI ------------------------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_find_text(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["find", "src/main.py", "--path", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "src/main.py" in out
    assert "v1" in out


def test_cli_find_json(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main([
        "find", "src/main.py", "--path", str(project_dir), "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["pattern"] == "src/main.py"
    assert "src/main.py" in payload["by_path"]
    assert payload["by_path"]["src/main.py"][0]["snapshot"]["name"] == "v1"


def test_cli_find_no_matches_returns_error(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()
    rc = cli.main(["find", "missing.txt", "--path", str(project_dir)])
    assert rc == cli.EXIT_ERROR
