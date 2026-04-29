"""Prune planning + execution tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from snapz import api, cli


def _force_created(snap_root: Path, _abspath: Path, name: str, dt: datetime) -> None:
    """Rewrite a snapshot's ``created`` timestamp on disk for retention tests."""

    iso = dt.replace(microsecond=0).isoformat()
    for child in snap_root.rglob(f"{name}.meta.json"):
        data = json.loads(child.read_text(encoding="utf-8"))
        if data.get("name") != name:
            continue
        data["created"] = iso
        child.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )
        return
    raise AssertionError(f"snapshot {name!r} not found under {snap_root}")


# ----------------- plan_prune --------------------------------------------


def test_plan_prune_keep_last_only(project_dir, config, snap_root):
    base = datetime.now() - timedelta(days=10)
    for i, days_back in enumerate([8, 6, 4, 2, 0]):
        api.save(project_dir, f"s{i}", config=config)
        _force_created(snap_root, project_dir, f"s{i}", base + timedelta(days=days_back))

    plan = api.plan_prune(project_dir, keep_last=2, config=config)
    keep_names = {s.name for s in plan.keep}
    drop_names = {s.name for s in plan.drop}
    # Newest two (s0, s1 — see days-back schedule above) kept.
    assert keep_names == {"s0", "s1"}
    assert drop_names == {"s2", "s3", "s4"}


def test_plan_prune_keep_within_days(project_dir, config, snap_root):
    base = datetime.now()
    api.save(project_dir, "old", config=config)
    api.save(project_dir, "fresh", config=config)
    _force_created(snap_root, project_dir, "old", base - timedelta(days=40))
    _force_created(snap_root, project_dir, "fresh", base - timedelta(days=2))

    plan = api.plan_prune(project_dir, keep_within_days=7, config=config)
    assert {s.name for s in plan.keep} == {"fresh"}
    assert {s.name for s in plan.drop} == {"old"}


def test_plan_prune_keep_daily_picks_one_per_day(project_dir, config, snap_root):
    base = datetime.now() - timedelta(days=5)
    # Two snapshots on day 0, one on day 1, one on day 2.
    api.save(project_dir, "d0a", config=config)
    api.save(project_dir, "d0b", config=config)
    api.save(project_dir, "d1", config=config)
    api.save(project_dir, "d2", config=config)
    _force_created(snap_root, project_dir, "d0a", base + timedelta(hours=1))
    _force_created(snap_root, project_dir, "d0b", base + timedelta(hours=20))
    _force_created(snap_root, project_dir, "d1", base + timedelta(days=1, hours=12))
    _force_created(snap_root, project_dir, "d2", base + timedelta(days=2, hours=12))

    # keep-daily=2 → keep latest of day 2 + day 1 only; both day-0 entries dropped.
    plan = api.plan_prune(project_dir, keep_daily=2, config=config)
    keep = {s.name for s in plan.keep}
    drop = {s.name for s in plan.drop}
    assert keep == {"d2", "d1"}
    assert drop == {"d0a", "d0b"}


def test_plan_prune_protect_keeps_named_snapshot(project_dir, config, snap_root):
    api.save(project_dir, "release", config=config)
    api.save(project_dir, "scratch", config=config)
    _force_created(
        snap_root, project_dir, "release",
        datetime.now() - timedelta(days=120),
    )
    _force_created(
        snap_root, project_dir, "scratch",
        datetime.now() - timedelta(days=120),
    )

    plan = api.plan_prune(
        project_dir,
        keep_last=1,
        protect=["release"],
        config=config,
    )
    keep = {s.name for s in plan.keep}
    # keep-last=1 picks the newest (whichever) and protect always keeps release.
    assert "release" in keep


def test_plan_prune_no_rules_raises(project_dir, config):
    api.save(project_dir, "v1", config=config)
    with pytest.raises(ValueError):
        api.plan_prune(project_dir, config=config)


# ----------------- execute_prune -----------------------------------------


def test_execute_prune_dry_run_does_not_delete(project_dir, config, snap_root):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    plan = api.plan_prune(project_dir, keep_last=1, config=config)

    outcome = api.execute_prune(plan, dry_run=True, config=config)
    assert outcome.dry_run is True
    assert "v1" in outcome.deleted
    # Snapshots still present on disk.
    snaps = {s.name for s in api.list_snapshots(project_dir, config=config)}
    assert {"v1", "v2"} <= snaps


def test_execute_prune_deletes_and_runs_gc(project_dir, config, snap_root):
    api.save(project_dir, "v1", config=config)
    # Mutate so v2 references new blobs that will be orphaned by v1 deletion.
    (project_dir / "src" / "main.py").write_text("x = 2\n", encoding="utf-8")
    api.save(project_dir, "v2", config=config)

    plan = api.plan_prune(project_dir, keep_last=1, config=config)
    outcome = api.execute_prune(plan, config=config)
    assert outcome.dry_run is False
    assert "v1" in outcome.deleted
    snaps = {s.name for s in api.list_snapshots(project_dir, config=config)}
    assert snaps == {"v2"}


def test_execute_prune_drop_names_override(project_dir, config):
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    plan = api.plan_prune(project_dir, keep_last=1, config=config)

    # Even though plan says drop=v1, the user can override via the TUI return.
    outcome = api.execute_prune(plan, drop_names=[], config=config)
    assert outcome.deleted == []
    assert {s.name for s in api.list_snapshots(project_dir, config=config)} == {
        "v1", "v2",
    }


# ----------------- CLI text mode -----------------------------------------


@pytest.fixture
def env_root(monkeypatch, snap_root):
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def test_cli_prune_dry_run_text(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    cli.main(["save", str(project_dir), "-n", "v2", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "prune", "--path", str(project_dir),
        "--keep-last", "1", "--dry-run", "--text",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "keep" in out and "drop" in out
    # Nothing actually deleted on dry-run.
    assert {s.name for s in api.list_snapshots(project_dir)} == {"v1", "v2"}


def test_cli_prune_yes_deletes(env_root, project_dir, capsys):
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    cli.main(["save", str(project_dir), "-n", "v2", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "prune", "--path", str(project_dir),
        "--keep-last", "1", "-y",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "deleted" in out
    assert {s.name for s in api.list_snapshots(project_dir)} == {"v2"}
