"""Tests for the snapshot/file readers and the picker fallback wiring.

These cover the data plumbing that drives the new diff-TUI drill-down
and the snapshot name picker — none of the actual curses logic, only
the pure-Python helpers and the non-TTY error paths in the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from snapz import api, cas, cli
from snapz.store import Store


# ---------------------------------------------------------------------------
# api.read_snapshot_bytes / read_live_bytes / cas.read_blob_bytes
# ---------------------------------------------------------------------------


def test_read_blob_bytes_round_trip(config, project_dir):
    api.save(project_dir, "v1", config=config)
    dir_root = Store(config).dir_for(project_dir)
    manifest_path = cas.manifest_path(dir_root, "v1")
    manifest = cas.read_manifest(manifest_path)
    file_entries = [e for e in manifest.entries if e.type == "file"]
    assert file_entries, "expected at least one file in manifest"
    sample = file_entries[0]
    raw = cas.read_blob_bytes(dir_root, sample.sha256)
    expected = (project_dir / sample.path).read_bytes()
    assert raw == expected


def test_read_snapshot_bytes_returns_file_content(config, project_dir):
    api.save(project_dir, "v1", config=config)
    data = api.read_snapshot_bytes(project_dir, "v1", "src/main.py", config=config)
    assert data == b"print('hi')\n"


def test_read_snapshot_bytes_missing_path_returns_none(config, project_dir):
    api.save(project_dir, "v1", config=config)
    assert (
        api.read_snapshot_bytes(project_dir, "v1", "nope/missing.txt", config=config)
        is None
    )


def test_read_snapshot_bytes_unknown_snapshot_raises(config, project_dir):
    with pytest.raises(FileNotFoundError):
        api.read_snapshot_bytes(project_dir, "ghost", "README.md", config=config)


def test_read_snapshot_bytes_handles_symlink(config, tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "real.txt").write_text("hi\n")
    (src / "link").symlink_to("real.txt")
    api.save(src, "v1", config=config)
    data = api.read_snapshot_bytes(src, "v1", "link", config=config)
    assert data == b"real.txt"


def test_read_live_bytes_file_and_missing(project_dir):
    assert api.read_live_bytes(project_dir, "src/main.py") == b"print('hi')\n"
    assert api.read_live_bytes(project_dir, "src/does-not-exist.py") is None


def test_read_live_bytes_directory_returns_none(project_dir):
    assert api.read_live_bytes(project_dir, "src") is None


# ---------------------------------------------------------------------------
# Non-TTY: missing snapshot name should produce a friendly error, not crash.
# We force isatty() to False so the picker path is bypassed regardless of
# how pytest is invoked.
# ---------------------------------------------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


@pytest.fixture(autouse=True)
def _force_non_tty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)


@pytest.mark.parametrize("subcmd", ["rm", "show", "restore", "revert", "mv"])
def test_missing_name_non_tty_errors_gracefully(
    env_root, project_dir, capsys, subcmd
):
    api.save(project_dir, "v1")
    capsys.readouterr()
    argv = [subcmd, "--path", str(project_dir)]
    if subcmd == "rm":
        argv.append("-y")
    rc = cli.main(argv)
    captured = capsys.readouterr()
    assert rc == cli.EXIT_ERROR, (rc, captured.err)
    assert "no snapshot name" in captured.err.lower() or "未指定快照" in captured.err


def test_missing_name_export_non_tty_errors(env_root, project_dir, tmp_path, capsys):
    api.save(project_dir, "v1")
    capsys.readouterr()
    dst = tmp_path / "out"
    rc = cli.main(["export", "--path", str(project_dir), str(dst)])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_ERROR
    assert "no snapshot name" in err.lower() or "未指定快照" in err


def test_missing_name_diff_non_tty_errors(env_root, project_dir, capsys):
    api.save(project_dir, "v1")
    capsys.readouterr()
    rc = cli.main(["diff", "--path", str(project_dir)])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_ERROR
    assert "no snapshot name" in err.lower() or "未指定快照" in err


def test_diff_with_explicit_a_still_works_non_tty(
    env_root, project_dir, capsys
):
    api.save(project_dir, "v1")
    capsys.readouterr()
    # Explicit a, b omitted ⇒ compare to live tree. Should succeed even
    # without a TTY (the b-picker only kicks in when both args missing).
    rc = cli.main(["diff", "v1", "--path", str(project_dir), "--text"])
    out = capsys.readouterr().out
    assert rc == 0, out


# ---------------------------------------------------------------------------
# Pure-Python helpers from tui that don't touch curses.
# ---------------------------------------------------------------------------


def test_decode_for_diff_text():
    from snapz import tui

    lines, placeholder = tui._decode_for_diff(b"a\nb\nc\n")
    assert placeholder is None
    assert lines == ["a", "b", "c"]


def test_decode_for_diff_binary():
    from snapz import tui

    lines, placeholder = tui._decode_for_diff(b"\x00\x01\x02junk")
    assert lines is None
    assert placeholder is not None
    assert "binary" in placeholder.lower() or "二进制" in placeholder


def test_decode_for_diff_none_means_absent():
    from snapz import tui

    lines, placeholder = tui._decode_for_diff(None)
    assert placeholder is None
    assert lines == []


# ---------------------------------------------------------------------------
# Regression: zstd blobs without a content-size header round-trip via
# read_blob_bytes (the streaming decompressor doesn't need the header).
# ---------------------------------------------------------------------------


def test_read_blob_bytes_works_for_zstd_blobs(config, project_dir):
    api.save(project_dir, "v1", config=config)
    dir_root = Store(config).dir_for(project_dir)
    manifest = cas.read_manifest(cas.manifest_path(dir_root, "v1"))
    files = [e for e in manifest.entries if e.type == "file"]
    assert files
    # Force exercise of the zstd path when zstandard is available.
    if cas._zstandard is None:  # pragma: no cover
        pytest.skip("zstandard not installed")
    sample = files[0]
    raw_blob = cas.blob_path(dir_root, sample.sha256).read_bytes()
    assert raw_blob[:4] == cas._ZSTD_MAGIC, (
        "fixture must produce zstd blobs to exercise the regression"
    )
    payload = cas.read_blob_bytes(dir_root, sample.sha256)
    assert payload == (project_dir / sample.path).read_bytes()


# ---------------------------------------------------------------------------
# Regression: cmd_stats and cmd_revert both used to AttributeError on TTY
# because they referenced TUI views that don't exist. Verify they now work
# end-to-end via cli.main() — non-TTY path is what the tests exercise, but
# the call site is the same.
# ---------------------------------------------------------------------------


def test_cmd_stats_no_attribute_error(env_root, project_dir, capsys):
    api.save(project_dir, "v1")
    capsys.readouterr()
    rc = cli.main(["stats", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "v1" in out or "1" in out  # at least the snapshot count column


def test_cmd_revert_picker_callable_exists():
    """The CLI references ``tui.run_revert_picker`` — make sure it's there.

    We don't drive the curses loop in tests; just import the symbol to
    catch the AttributeError class of bug at the unit level.
    """

    from snapz import tui as _tui
    assert callable(getattr(_tui, "run_revert_picker", None))
