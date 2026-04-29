from datetime import datetime
from pathlib import Path

import pytest

from snapz.util import (
    auto_name,
    compute_key,
    format_duration,
    format_iso,
    format_size,
    resolve_path,
    validate_snapshot_name,
)


def test_compute_key_is_deterministic_and_includes_basename(tmp_path):
    target = tmp_path / "topics-bot"
    target.mkdir()
    key = compute_key(target)
    assert key.endswith("-topics-bot")
    assert key == compute_key(target)
    assert len(key.split("-")[0]) == 12


def test_compute_key_handles_special_chars(tmp_path):
    target = tmp_path / "weird name $ here"
    target.mkdir()
    key = compute_key(target)
    assert "$" not in key
    assert " " not in key


def test_validate_snapshot_name_accepts_valid():
    for name in ["before-refactor", "auto-20260428-1700", "v1.2.3", "x"]:
        assert validate_snapshot_name(name) == name


@pytest.mark.parametrize(
    "bad", ["", "  ", ".hidden", "has space", "with/slash", "back\\slash", "-leading"]
)
def test_validate_snapshot_name_rejects_bad(bad):
    with pytest.raises(ValueError):
        validate_snapshot_name(bad)


def test_auto_name_format():
    name = auto_name(datetime(2026, 4, 28, 17, 5, 30))
    assert name == "auto-20260428-170530"


def test_format_size_units():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024) == "1.0 MB"
    assert format_size(int(1.5 * 1024 * 1024)) == "1.5 MB"


def test_format_duration_units():
    assert format_duration(0.5) == "500ms"
    assert format_duration(9.3) == "9.3s"
    assert format_duration(75) == "1m15s"


def test_format_iso_passthrough_on_invalid():
    assert format_iso("not-a-date") == "not-a-date"
    assert format_iso("2026-04-28T17:05:30") == "2026-04-28 17:05"


def test_resolve_path_handles_dot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_path(".") == tmp_path.resolve()
    assert resolve_path("..").is_dir()


def test_resolve_path_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path(tmp_path) / "foo"
    target.mkdir()
    assert resolve_path("~/foo") == target.resolve()
