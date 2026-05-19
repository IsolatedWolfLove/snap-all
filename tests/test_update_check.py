"""Non-blocking CLI update checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz import __version__, cli, preferences, update_check


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_version_compare_handles_v_tags():
    assert update_check.version_is_newer("v2.1.2", "2.1.1") is True
    assert update_check.version_is_newer("2.1.1", "2.1.1") is False
    assert update_check.version_is_newer("v2.0.9", "2.1.1") is False


def test_run_check_records_available_update(snap_root):
    def fake_fetch():
        return update_check.LatestVersion(
            tag="v9.0.0",
            version="9.0.0",
            source="release",
        )

    rc = update_check.run_check(
        snap_root,
        current_version="1.0.0",
        fetcher=fake_fetch,
    )

    state = update_check.load_state(snap_root)
    assert rc == 0
    assert state["latest_tag"] == "v9.0.0"
    assert state["latest_version"] == "9.0.0"
    assert state["update_available"] is True


def test_maybe_start_is_daily_and_non_blocking(snap_root, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))

        class Proc:
            pass

        return Proc()

    first = update_check.maybe_start(
        snap_root,
        current_version="1.0.0",
        argv0="snapz",
        command="list",
        popen=fake_popen,
        force=True,
    )
    second = update_check.maybe_start(
        snap_root,
        current_version="1.0.0",
        argv0="snapz",
        command="list",
        popen=fake_popen,
        force=True,
    )

    assert first == "started"
    assert second == "not-due"
    assert len(calls) == 1
    assert update_check.WORKER_COMMAND in calls[0][0]
    assert calls[0][1]["stdout"] is update_check.subprocess.DEVNULL


def test_maybe_start_respects_config_toggle(snap_root, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    preferences.set_config_value(snap_root, "update_check.enabled", "false")

    result = update_check.maybe_start(
        snap_root,
        current_version="1.0.0",
        argv0="snapz",
        command="list",
        popen=lambda *_a, **_kw: None,
        force=True,
    )

    assert result == "disabled"
    assert not update_check.state_path(snap_root).exists()


def test_cached_update_notice_prints_on_next_start(snap_root, capsys):
    update_check.save_state(
        snap_root,
        {
            "last_check_date": update_check._today_key(),
            "latest_version": "9.0.0",
        },
    )

    result = update_check.maybe_start(
        snap_root,
        current_version="1.0.0",
        argv0="snapz",
        command="list",
        popen=lambda *_a, **_kw: None,
        force=True,
    )
    err = capsys.readouterr().err

    assert result == "notified"
    assert "snapz 9.0.0 is available" in err
    assert "snapz update" in err


def test_cli_prints_cached_notice_on_next_invocation(env_root, monkeypatch, capsys):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    update_check.save_state(
        env_root,
        {
            "last_check_date": update_check._today_key(),
            "latest_version": "9.0.0",
        },
    )

    rc = cli.main(["alist", "--text"])
    err = capsys.readouterr().err

    assert rc == 0
    assert "snapz 9.0.0 is available" in err


def test_json_invocation_skips_update_check(snap_root):
    result = update_check.maybe_start(
        snap_root,
        current_version="1.0.0",
        argv0="snapz",
        command="list",
        json_requested=True,
        popen=lambda *_a, **_kw: None,
        force=True,
    )

    assert result == "skipped"
    assert not update_check.state_path(snap_root).exists()


def test_cli_config_lists_update_check_key(env_root, capsys):
    rc = cli.main(["config", "list"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "update_check.enabled" in out


def test_cli_update_check_worker_writes_state(env_root, monkeypatch):
    root = Path(env_root)

    def fake_run_check(root_arg, *, current_version, **_kwargs):
        update_check.save_state(
            Path(root_arg),
            {
                "latest_version": "9.0.0",
                "checked_current_version": current_version,
            },
        )
        return 0

    monkeypatch.setattr(update_check, "run_check", fake_run_check)

    rc = cli.main([
        update_check.WORKER_COMMAND,
        "--root",
        str(root),
        "--current-version",
        __version__,
    ])

    state = update_check.load_state(root)
    assert rc == 0
    assert state["latest_version"] == "9.0.0"
    assert state["checked_current_version"] == __version__
