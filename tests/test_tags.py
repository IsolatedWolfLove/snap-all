"""Tests for snapshot tags (P3).

Covers:
- ``SnapshotMeta`` round-trips a ``tags`` list.
- ``api.tag_add`` / ``tag_remove`` / ``list_tags`` semantics.
- Validation rules in ``snapz.util.validate_tag``.
- ``plan_prune --keep-tag`` integration.
- CLI ``snapz tag add / rm / list`` (text + json).
- Tag preservation across ``rename`` / ``protect`` cycles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from snapz import api, cli, events
from snapz.config import RuntimeConfig
from snapz.store import SnapshotMeta, Store
from snapz.util import validate_tag


# ---------------------------------------------------------------------------
# meta + validation
# ---------------------------------------------------------------------------


def test_snapshot_meta_round_trips_tags() -> None:
    raw = {
        "name": "v1",
        "source": "/p",
        "created": "2026-05-01T00:00:00",
        "size_bytes": 1,
        "file_count": 1,
        "total_bytes_in": 1,
        "compression": "zstd",
        "archive": "v1.tar.zst",
        "tags": ["a", "b", "a", "  c "],   # dupes + whitespace
    }
    meta = SnapshotMeta.from_dict(raw)
    assert meta.tags == ["a", "b", "c"]
    again = SnapshotMeta.from_dict(meta.to_dict())
    assert again.tags == ["a", "b", "c"]


def test_snapshot_meta_handles_legacy_string_tag() -> None:
    raw = {
        "name": "v1", "source": "/p", "created": "x",
        "size_bytes": 0, "file_count": 0, "total_bytes_in": 0,
        "compression": "zstd", "archive": "v1.tar.zst",
        "tags": "single",
    }
    assert SnapshotMeta.from_dict(raw).tags == ["single"]


def test_snapshot_meta_no_tags_default_empty_list() -> None:
    raw = {
        "name": "v1", "source": "/p", "created": "x",
        "size_bytes": 0, "file_count": 0, "total_bytes_in": 0,
        "compression": "zstd", "archive": "v1.tar.zst",
    }
    assert SnapshotMeta.from_dict(raw).tags == []


def test_validate_tag_rules() -> None:
    assert validate_tag("release-1.2") == "release-1.2"
    assert validate_tag("  prod ") == "prod"
    with pytest.raises(ValueError):
        validate_tag("")
    with pytest.raises(ValueError):
        validate_tag("has space")
    with pytest.raises(ValueError):
        validate_tag("-leading-dash")
    with pytest.raises(ValueError):
        validate_tag("auto-foo")        # reserved prefix
    with pytest.raises(ValueError):
        validate_tag("stash")           # reserved name
    # Internal callers may bypass the reserved guard.
    assert validate_tag("stash", allow_reserved=True) == "stash"
    assert validate_tag("auto-x", allow_reserved=True) == "auto-x"


# ---------------------------------------------------------------------------
# api: add / remove / list
# ---------------------------------------------------------------------------


def test_tag_add_persists_and_dedups(project_dir: Path, config: RuntimeConfig) -> None:
    api.save(project_dir, "v1", config=config)
    meta = api.tag_add(project_dir, "v1", ["release", "stable"], config=config)
    assert meta.tags == ["release", "stable"]

    # Adding existing tag is a no-op (no duplicates, but still succeeds).
    again = api.tag_add(project_dir, "v1", ["release"], config=config)
    assert again.tags == ["release", "stable"]

    # Reload from disk to confirm persistence.
    snap = api.show(project_dir, "v1", config=config)
    assert snap is not None
    assert snap.tags == ["release", "stable"]


def test_tag_add_rejects_reserved(project_dir: Path, config: RuntimeConfig) -> None:
    api.save(project_dir, "v1", config=config)
    with pytest.raises(ValueError):
        api.tag_add(project_dir, "v1", ["stash"], config=config)
    with pytest.raises(ValueError):
        api.tag_add(project_dir, "v1", ["auto-foo"], config=config)
    # Allow-reserved escape hatch is for internal use (P4 stash).
    meta = api.tag_add(
        project_dir, "v1", ["stash"],
        config=config, allow_reserved=True,
    )
    assert meta.tags == ["stash"]


def test_tag_remove_drops_known_silently_ignores_unknown(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.tag_add(project_dir, "v1", ["a", "b", "c"], config=config)

    meta = api.tag_remove(project_dir, "v1", ["b", "z-not-there"], config=config)
    assert meta.tags == ["a", "c"]

    snap = api.show(project_dir, "v1", config=config)
    assert snap is not None and snap.tags == ["a", "c"]


def test_list_tags_groups_by_label(project_dir: Path, config: RuntimeConfig) -> None:
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    api.tag_add(project_dir, "v1", ["release", "ci"], config=config)
    api.tag_add(project_dir, "v2", ["ci"], config=config)

    groups = api.list_tags(project_dir, config=config)
    assert sorted(groups) == ["ci", "release"]
    assert {s.name for s in groups["ci"]} == {"v1", "v2"}
    assert {s.name for s in groups["release"]} == {"v1"}


def test_tag_emits_events(project_dir: Path, config: RuntimeConfig) -> None:
    api.save(project_dir, "v1", config=config)
    api.tag_add(project_dir, "v1", ["a", "b"], config=config)
    api.tag_remove(project_dir, "v1", ["a"], config=config)

    folder = Store(config).dir_for(project_dir)
    rows = events.load_for(folder)
    kinds = [r.kind for r in rows]
    assert kinds[0] == events.KIND_TAG_RM
    assert kinds[1] == events.KIND_TAG_ADD
    # Deleting a non-existent tag emits no event.
    api.tag_remove(project_dir, "v1", ["does-not-exist"], config=config)
    rows_after = events.load_for(folder)
    assert len(rows_after) == len(rows)


def test_tag_add_survives_rename_and_protect(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.tag_add(project_dir, "v1", ["keep"], config=config)
    api.rename(project_dir, "v1", "v2", config=config)
    api.protect(project_dir, "v2", config=config)

    meta = api.show(project_dir, "v2", config=config)
    assert meta is not None
    assert meta.tags == ["keep"]
    assert meta.protected is True


# ---------------------------------------------------------------------------
# prune --keep-tag
# ---------------------------------------------------------------------------


def test_plan_prune_keep_tag_protects_matching_snapshots(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    (project_dir / "extra.txt").write_text("two", encoding="utf-8")
    api.save(project_dir, "v2", config=config)
    api.tag_add(project_dir, "v1", ["release"], config=config)

    # No keep_last — the only retention rule is keep_tag. v1 is rescued
    # by the tag; v2 has no tag and no other rule, so it must drop.
    plan = api.plan_prune(project_dir, keep_tag=["release"], config=config)
    keep_names = {s.name for s in plan.keep}
    drop_names = {s.name for s in plan.drop}
    assert keep_names == {"v1"}
    assert drop_names == {"v2"}
    assert plan.rules["keep_tag"] == ["release"]


def test_plan_prune_keep_tag_alone_is_sufficient(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.tag_add(project_dir, "v1", ["release"], config=config)

    # No other rule — keep_tag alone is enough to satisfy the safety check.
    plan = api.plan_prune(project_dir, keep_tag=["release"], config=config)
    assert {s.name for s in plan.keep} == {"v1"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_tag_add_list_rm_text(
    project_dir: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    api.save(project_dir, "v1", config=config)
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))

    rc = cli.main(["tag", "add", "v1", "release", "ci", "--path", str(project_dir)])
    assert rc == 0
    capsys.readouterr()  # discard

    rc = cli.main(["tag", "list", "--path", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "release" in out and "ci" in out and "v1" in out

    rc = cli.main(["tag", "rm", "v1", "ci", "--path", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ci" in out
    assert "release" not in out

    rc = cli.main([
        "tag", "list", "--path", str(project_dir), "--json",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["tags"] == {"release": ["v1"]}


def test_cli_tag_invalid_returns_error(
    project_dir: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    api.save(project_dir, "v1", config=config)
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))

    rc = cli.main(["tag", "add", "v1", "stash", "--path", str(project_dir)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "reserved" in err.lower() or "stash" in err.lower()


def test_cli_prune_keep_tag(
    project_dir: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.save(project_dir, "v2", config=config)
    api.tag_add(project_dir, "v1", ["release"], config=config)
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))

    rc = cli.main([
        "prune",
        "--path", str(project_dir),
        "--keep-tag", "release",
        "--dry-run", "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    keep = {s["name"] for s in payload["plan"]["keep"]}
    drop = {s["name"] for s in payload["plan"]["drop"]}
    assert "v1" in keep
    assert "v2" in drop
    assert payload["plan"]["rules"]["keep_tag"] == ["release"]
