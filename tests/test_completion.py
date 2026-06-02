"""Shell-completion command and completer tests."""

from __future__ import annotations

import argparse

from snapz import api, cli


def test_completion_bash_outputs_argcomplete_registration(monkeypatch, snap_root, capsys):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))

    rc = cli.main(["completion", "bash"])
    out = capsys.readouterr().out

    assert rc == cli.EXIT_OK
    assert "_ARGCOMPLETE" in out
    assert "snapz" in out


def test_completion_zsh_outputs_argcomplete_registration(monkeypatch, snap_root, capsys):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))

    rc = cli.main(["completion", "zsh"])
    out = capsys.readouterr().out

    assert rc == cli.EXIT_OK
    assert "_ARGCOMPLETE_SHELL" in out
    assert "zsh" in out


def test_completion_install_writes_rcfile(monkeypatch, snap_root, tmp_path, capsys):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    rcfile = tmp_path / ".bashrc"

    rc = cli.main([
        "completion",
        "install",
        "--shell",
        "bash",
        "--rcfile",
        str(rcfile),
    ])
    out = capsys.readouterr().out

    assert rc == cli.EXIT_OK
    assert "installed snapz bash completion" in out
    text = rcfile.read_text(encoding="utf-8")
    assert "# snapz completion" in text
    assert "_ARGCOMPLETE" in text


def test_completion_install_is_idempotent(monkeypatch, snap_root, tmp_path, capsys):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    rcfile = tmp_path / ".bashrc"

    assert cli.main([
        "completion",
        "install",
        "--shell",
        "bash",
        "--rcfile",
        str(rcfile),
    ]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main([
        "completion",
        "install",
        "--shell",
        "bash",
        "--rcfile",
        str(rcfile),
    ]) == cli.EXIT_OK
    out = capsys.readouterr().out

    assert "already appears" in out
    assert rcfile.read_text(encoding="utf-8").count("# snapz completion") == 1


def test_snapshot_completer_uses_path_and_descriptions(
    monkeypatch,
    snap_root,
    project_dir,
):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    api.save(project_dir, "v1", note="first save")
    (project_dir / "README.md").write_text("# changed\n", encoding="utf-8")
    api.save(project_dir, "other")
    parsed = argparse.Namespace(path=str(project_dir), all=False)

    completions = cli._snapshot_name_completer("v", parsed)

    assert list(completions) == ["v1"]
    assert "first save" in completions["v1"]


def test_snapshot_completer_hides_auto_unless_all(monkeypatch, snap_root, project_dir):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    api.save(project_dir, "auto-pre-restore-1")
    parsed = argparse.Namespace(path=str(project_dir), all=False)

    assert cli._snapshot_name_completer("auto", parsed) == {}
    parsed.all = True
    assert "auto-pre-restore-1" in cli._snapshot_name_completer("auto", parsed)


def test_tag_completer_can_scope_to_snapshot(monkeypatch, snap_root, project_dir):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    api.save(project_dir, "v1")
    (project_dir / "README.md").write_text("# changed\n", encoding="utf-8")
    api.save(project_dir, "v2")
    api.tag_add(project_dir, "v1", ["release"])
    api.tag_add(project_dir, "v2", ["draft"])

    scoped = cli._tag_completer(
        "",
        argparse.Namespace(path=str(project_dir), name="v1"),
    )
    all_tags = cli._tag_completer(
        "",
        argparse.Namespace(path=str(project_dir), name=None),
    )

    assert scoped == {"release": "1 snapshot"}
    assert all_tags == {"draft": "1 snapshot", "release": "1 snapshot"}
