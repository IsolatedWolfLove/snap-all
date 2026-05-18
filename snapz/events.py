"""Per-source event log (`<store>/<key>/_events.log`).

Each entry is a single-line JSON object (JSONL) so the file stays
``jq``-friendly and append-only:

```
{"ts": "2026-05-02T00:12:33", "kind": "save", "source": "/abs/path",
 "snapshot": "baseline", ...}
```

The log rotates to ``_events.log.1`` when it grows past
:data:`MAX_LOG_BYTES`; the previous rotation (if any) is dropped so the
total footprint never exceeds ~2× that limit per source.

Writes are best-effort — failures here must never break user-facing
operations, so every filesystem call is wrapped in ``try/except OSError``
and silently ignored when the store is read-only or otherwise wedged.
"""

from __future__ import annotations

import heapq
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from snapz.config import RuntimeConfig, default_config

EVENTS_FILENAME = "_events.log"
EVENTS_ROTATED_SUFFIX = ".1"
MAX_LOG_BYTES = 1 * 1024 * 1024  # 1 MiB

# Canonical event ``kind`` labels. Not enforced, but new emitters should
# pick from this set when possible so `snapz log --kind` is predictable.
KIND_SAVE = "save"
KIND_DELETE = "delete"
KIND_RENAME = "rename"
KIND_PROTECT = "protect"
KIND_UNPROTECT = "unprotect"
KIND_RESTORE = "restore"
KIND_REVERT = "revert"
KIND_UNDO = "undo"
KIND_PRUNE = "prune"
KIND_RELOCATE = "relocate"
KIND_ADOPT = "adopt"
KIND_TAG_ADD = "tag_add"
KIND_TAG_RM = "tag_rm"
KIND_STASH = "stash"
KIND_STASH_POP = "stash_pop"
KIND_STASH_APPLY = "stash_apply"
KIND_STASH_DROP = "stash_drop"


@dataclass
class Event:
    ts: str
    kind: str
    source: str
    snapshot: str = ""
    key: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        known = {"ts", "kind", "source", "snapshot", "key"}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            ts=str(data.get("ts", "")),
            kind=str(data.get("kind", "")),
            source=str(data.get("source", "")),
            snapshot=str(data.get("snapshot", "") or ""),
            key=str(data.get("key", "") or ""),
            extra=extra,
        )

    def to_dict(self) -> dict:
        out = {
            "ts": self.ts,
            "kind": self.kind,
            "source": self.source,
        }
        if self.snapshot:
            out["snapshot"] = self.snapshot
        if self.key:
            out["key"] = self.key
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def _events_path(store_folder: Path) -> Path:
    return store_folder / EVENTS_FILENAME


def _rotate_if_needed(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < MAX_LOG_BYTES:
        return
    rotated = path.with_name(path.name + EVENTS_ROTATED_SUFFIX)
    try:
        rotated.unlink(missing_ok=True)
        os.replace(path, rotated)
    except OSError:
        # If rotation fails just keep appending — we'd rather overflow
        # than lose the operation log.
        return


def _write_line(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    _rotate_if_needed(path)
    line = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    first_write = False
    try:
        with path.open("x", encoding="utf-8") as fh:
            first_write = True
            fh.write(line + "\n")
    except FileExistsError:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            return
    except OSError:
        return
    if not first_write:
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def record(
    store_folder: Path,
    kind: str,
    *,
    source: str | Path = "",
    snapshot: str = "",
    key: str = "",
    ts: Optional[str] = None,
    **extra,
) -> None:
    """Append one structured event to *store_folder*'s log.

    Callers pass the per-source store folder (typically
    ``Store(config).dir_for(abspath)``). ``extra`` fields are merged
    into the JSON object alongside the canonical ``ts/kind/source``.
    """

    payload = {
        # Microsecond precision so stable newest-first ordering survives
        # multiple ops landing inside the same calendar second.
        "ts": ts or datetime.now().isoformat(timespec="microseconds"),
        "kind": str(kind),
        "source": str(source),
    }
    if snapshot:
        payload["snapshot"] = str(snapshot)
    if key:
        payload["key"] = str(key)
    # Drop None values and keys that collide with the canonical ones.
    for k, v in extra.items():
        if v is None or k in ("ts", "kind", "source", "snapshot", "key"):
            continue
        payload[k] = v
    _write_line(_events_path(store_folder), payload)


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def _iter_file(path: Path) -> Iterable[Event]:
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                yield Event.from_dict(data)
            except (KeyError, TypeError, ValueError):
                continue


def _iter_folder(store_folder: Path) -> Iterable[Event]:
    """Yield events from one source folder. Rotated file first, then
    current, so a merged sort stays stable for same-timestamp rows."""

    rotated = _events_path(store_folder).with_name(
        EVENTS_FILENAME + EVENTS_ROTATED_SUFFIX
    )
    yield from _iter_file(rotated)
    yield from _iter_file(_events_path(store_folder))


def _apply_filters(
    events: Iterable[Event],
    *,
    kinds: Optional[Iterable[str]] = None,
) -> list[Event]:
    allowed = None if kinds is None else {k.strip() for k in kinds if k and k.strip()}
    out: list[Event] = []
    for ev in events:
        if allowed is not None and ev.kind not in allowed:
            continue
        out.append(ev)
    return out


def _newest_first(events: Iterable[Event], limit: Optional[int] = None) -> list[Event]:
    if limit is not None and limit > 0:
        return heapq.nlargest(limit, events, key=lambda e: e.ts)
    out = list(events)
    out.sort(key=lambda e: e.ts, reverse=True)
    return out


def load_for(
    store_folder: Path,
    *,
    kinds: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> list[Event]:
    """Return events for a single store folder, newest first."""

    return _newest_first(_apply_filters(_iter_folder(store_folder), kinds=kinds), limit)


def load_all(
    config: Optional[RuntimeConfig] = None,
    *,
    kinds: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> list[Event]:
    """Return events across every store folder under *config.root*."""

    cfg = config or default_config()
    root = Path(cfg.root)
    if not root.is_dir():
        return []
    collected: list[Event] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        key = child.name
        for ev in _apply_filters(_iter_folder(child), kinds=kinds):
            if not ev.key:
                ev.key = key
            collected.append(ev)
    return _newest_first(collected, limit)
