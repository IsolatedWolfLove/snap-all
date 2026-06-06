"""Scheduler integration tests."""

from __future__ import annotations

import subprocess
import sys

from snapz import scheduler


def test_install_remote_sync_cron_replaces_own_block(tmp_path, monkeypatch):
    root = tmp_path / "store"
    marker = scheduler._cron_marker(root)
    calls: list[tuple[list[str], str | None]] = []
    existing = "\n".join([
        "MAILTO=ops@example.test",
        marker,
        "0 */3 * * * old",
        "5 * * * * unrelated",
        "",
    ])

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs.get("input")))
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=existing, stderr="")
        if args == ["crontab", "-"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = scheduler.install_remote_sync_cron(root, python=sys.executable)

    assert result.updated is True
    payload = calls[-1][1]
    assert payload is not None
    assert "MAILTO=ops@example.test" in payload
    assert "5 * * * * unrelated" in payload
    assert "old" not in payload
    assert marker in payload
    assert "snapz push all" in payload
    assert "snapz pull all" in payload


def test_install_remote_sync_cron_treats_missing_crontab_as_empty(tmp_path, monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="no crontab for user\n")
        if args == ["crontab", "-"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = scheduler.install_remote_sync_cron(tmp_path / "store")

    assert result.installed is True
    assert result.updated is False
