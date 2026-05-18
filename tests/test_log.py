"""Tests for the P1 event log + ``snapz log`` command."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from snapz import api, cli, events
from snapz.config import RuntimeConfig
from snapz.store import Store


# ---------------------------------------------------------------------------
# events module — pure unit tests
# ---------------------------------------------------------------------------


def test_record_and_load_for(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    folder.mkdir()

    events.record(folder, events.KIND_SAVE, source="/p", snapshot="v1", file_count=3)
    events.record(folder, events.KIND_DELETE, source="/p", snapshot="v1")

    rows = events.load_for(folder)
    assert [r.kind for r in rows] == [events.KIND_DELETE, events.KIND_SAVE]
    assert rows[1].snapshot == "v1"
    assert rows[1].extra.get("file_count") == 3


def test_record_extras_and_ts_override(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    folder.mkdir()

    events.record(
        folder, "save",
        source="/p", snapshot="v1",
        ts="2026-05-01T00:00:00",  # caller replays a historical timestamp
        file_count=42, note="seeded",
    )
    rows = events.load_for(folder)
    assert len(rows) == 1
    assert rows[0].kind == "save"
    assert rows[0].ts == "2026-05-01T00:00:00"
    assert rows[0].extra.get("file_count") == 42
    assert rows[0].extra.get("note") == "seeded"


def test_from_dict_drops_reserved_collisions() -> None:
    # The on-disk JSON line could technically carry a field that shares
    # a name with a canonical field; Event.from_dict should split them.
    ev = events.Event.from_dict({
        "ts": "2026-05-01T00:00:00",
        "kind": "save",
        "source": "/p",
        "snapshot": "v1",
        "key": "abc",
        "file_count": 3,
    })
    assert ev.kind == "save"
    assert ev.extra == {"file_count": 3}


def test_load_for_filters_kinds_and_limits(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    folder.mkdir()
    for i in range(5):
        events.record(folder, "save", source="/p", snapshot=f"s{i}", ts=f"2026-05-0{i}T00:00:00")
        events.record(folder, "delete", source="/p", snapshot=f"s{i}", ts=f"2026-05-0{i}T01:00:00")

    saves = events.load_for(folder, kinds=["save"])
    assert all(e.kind == "save" for e in saves)
    assert len(saves) == 5

    latest = events.load_for(folder, limit=2)
    assert len(latest) == 2
    # newest-first ordering
    assert latest[0].ts >= latest[1].ts


def test_rotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "store"
    folder.mkdir()
    # Keep a comfortable room for ~4 entries before rotating.
    monkeypatch.setattr(events, "MAX_LOG_BYTES", 400)

    for i in range(20):
        events.record(
            folder, "save", source="/p", snapshot=f"s{i:02d}",
            ts=f"2026-05-01T00:00:{i:02d}",
        )

    events_path = folder / events.EVENTS_FILENAME
    rotated = folder / (events.EVENTS_FILENAME + ".1")
    assert events_path.exists()
    # Rotation should have fired at least once for 20 writes at 400 byte cap.
    assert rotated.exists()

    rows = events.load_for(folder)
    # We capped the log so we may have dropped older rows; the newest
    # ones must always survive.
    names = [r.snapshot for r in rows]
    assert names[0] == "s19"
    assert len(rows) >= 4 and len(rows) <= 20
    # rows stay sorted newest-first
    assert rows == sorted(rows, key=lambda r: r.ts, reverse=True)


def test_load_all_merges_folders(tmp_path: Path) -> None:
    root = tmp_path / "snapz-all"
    (root / "aaa-proj").mkdir(parents=True)
    (root / "bbb-other").mkdir(parents=True)

    events.record(
        root / "aaa-proj", "save",
        source="/a/proj", snapshot="v1", ts="2026-05-01T00:00:00",
    )
    events.record(
        root / "bbb-other", "restore",
        source="/b/other", snapshot="v2", ts="2026-05-02T00:00:00",
    )

    cfg = RuntimeConfig(root=root)
    rows = events.load_all(cfg)
    assert [r.kind for r in rows] == ["restore", "save"]
    # key is auto-filled from folder name when missing
    assert rows[0].key == "bbb-other"
    assert rows[1].key == "aaa-proj"


def test_load_all_filters_before_collecting(tmp_path: Path) -> None:
    root = tmp_path / "snapz-all"
    (root / "aaa-proj").mkdir(parents=True)
    for i in range(5):
        events.record(
            root / "aaa-proj", "save",
            source="/a/proj", snapshot=f"s{i}", ts=f"2026-05-01T00:00:0{i}",
        )
    events.record(
        root / "aaa-proj", "delete",
        source="/a/proj", snapshot="s0", ts="2026-05-01T00:01:00",
    )

    rows = events.load_all(RuntimeConfig(root=root), kinds=["delete"], limit=1)

    assert [r.kind for r in rows] == ["delete"]
    assert rows[0].key == "aaa-proj"


# ---------------------------------------------------------------------------


def test_save_emits_event(project_dir: Path, config: RuntimeConfig) -> None:
    api.save(project_dir, "v1", config=config)

    folder = Store(config).dir_for(project_dir)
    rows = events.load_for(folder)
    assert len(rows) == 1
    assert rows[0].kind == events.KIND_SAVE
    assert rows[0].snapshot == "v1"
    assert rows[0].source == str(project_dir)
    assert rows[0].extra["file_count"] >= 1


def test_delete_rename_protect_emit_events(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.rename(project_dir, "v1", "v2", config=config)
    api.protect(project_dir, "v2", config=config)
    api.unprotect(project_dir, "v2", config=config)
    api.delete(project_dir, "v2", config=config)

    folder = Store(config).dir_for(project_dir)
    kinds = [e.kind for e in events.load_for(folder)]
    # newest-first
    assert kinds[:5] == [
        events.KIND_DELETE,
        events.KIND_UNPROTECT,
        events.KIND_PROTECT,
        events.KIND_RENAME,
        events.KIND_SAVE,
    ]


def test_restore_and_undo_chain(
    project_dir: Path, config: RuntimeConfig,
) -> None:
    api.save(project_dir, "v1", config=config)
    # mutate
    (project_dir / "README.md").write_text("changed\n", encoding="utf-8")
    api.restore(project_dir, "v1", config=config)
    api.undo(project_dir, config=config)

    folder = Store(config).dir_for(project_dir)
    kinds = [e.kind for e in events.load_for(folder)]
    # newest-first contains an undo after the restore+auto-pre-restore save
    assert events.KIND_UNDO in kinds
    assert events.KIND_RESTORE in kinds
    assert events.KIND_SAVE in kinds


def test_cli_log_text_and_json(
    project_dir: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    api.save(project_dir, "v1", config=config)

    # Make sure `default_config()` picks up our throw-away root and
    # resolve_path doesn't need to chdir.
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))

    # text output
    rc = cli.main(["log", "--path", str(project_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "save" in out
    assert "v1" in out

    # --json output
    rc = cli.main(["log", "--path", str(project_dir), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "events" in payload
    assert any(e["kind"] == "save" and e["snapshot"] == "v1" for e in payload["events"])


def test_cli_log_kind_filter(
    project_dir: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    api.save(project_dir, "v1", config=config)
    api.delete(project_dir, "v1", config=config)
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))

    rc = cli.main([
        "log", "--path", str(project_dir), "--kind", "delete", "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    kinds = [e["kind"] for e in payload["events"]]
    assert kinds == ["delete"]


def test_cli_log_all_merges_sources(
    tmp_path: Path, config: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    a = tmp_path / "a"
    a.mkdir()
    (a / "f.txt").write_text("a", encoding="utf-8")
    b = tmp_path / "b"
    b.mkdir()
    (b / "f.txt").write_text("b", encoding="utf-8")

    api.save(a, "v1", config=config)
    api.save(b, "v1", config=config)

    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(config.root))
    rc = cli.main(["log", "--all", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    sources = {e["source"] for e in payload["events"]}
    assert str(a) in sources
    assert str(b) in sources


def test_log_corrupt_line_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "store"
    folder.mkdir()
    events.record(folder, "save", source="/p", snapshot="v1")
    # corrupt line hand-appended between valid events
    with (folder / events.EVENTS_FILENAME).open("a", encoding="utf-8") as fh:
        fh.write("{ not json\n")
    events.record(folder, "delete", source="/p", snapshot="v1")

    rows = events.load_for(folder)
    assert [r.kind for r in rows] == ["delete", "save"]
