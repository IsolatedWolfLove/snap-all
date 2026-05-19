"""Undo support built on auto-pre-* safety snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from snapz import events
from snapz.config import RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store
from snapz.util import is_undo_snapshot
from snapz._api_core import (
    _delete_snapshot_with_refs,
    _record_event,
    _source_path,
    restore,
)


# ---------------------------------------------------------------------------
# Undo — pop the most recent auto-pre-* snapshot and restore it (v0.2)
# ---------------------------------------------------------------------------


@dataclass
class UndoOutcome:
    """Result of :func:`undo`. *consumed* is the safety snapshot that
    was restored and then deleted; *remaining* is how many further
    undo points still exist on disk after this call."""

    snapshot: SnapshotMeta
    extracted_count: int
    cleaned_count: int
    remaining: int


def find_undo_target(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> Optional[SnapshotMeta]:
    """Return the most recent ``auto-pre-*`` safety snapshot for *path*,
    or ``None`` when there's nothing to undo.

    Ordering: ``created`` (ISO timestamp at second precision) is the
    primary key, but two safety snapshots can land in the same calendar
    second when the user runs back-to-back ops. Tie-break with the
    meta file's mtime (it's set by ``write_snapshot_meta`` when the
    record lands on disk and is monotonic for a single source).
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    snaps = store.list_snapshots(abspath)
    candidates = [s for s in snaps if is_undo_snapshot(s.name)]
    if not candidates:
        return None

    def _sort_key(meta: SnapshotMeta) -> tuple[str, float]:
        try:
            mtime = store.meta_path(abspath, meta.name).stat().st_mtime
        except OSError:
            mtime = 0.0
        return (meta.created, mtime)

    candidates.sort(key=_sort_key, reverse=True)
    return candidates[0]


def undo(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    clean: bool = True,
) -> UndoOutcome:
    """Roll back the most recent destructive op.

    Restores the most recent ``auto-pre-restore-*`` /
    ``auto-pre-revert-*`` snapshot for *path* (CAS-format only),
    consumes it (deletes the manifest+meta so the next undo walks
    further back in time), and returns an :class:`UndoOutcome`. The
    restore itself runs with ``auto_save=False`` so the undo chain
    doesn't fork.

    *clean* defaults to True so the live tree ends up byte-identical to
    the captured state — pass False if you want to preserve any extra
    files that were added since the safety snapshot was taken.

    Raises :class:`FileNotFoundError` when no safety snapshot exists.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    target = find_undo_target(abspath, config=config)
    if target is None:
        raise FileNotFoundError(
            f"no undo points under {abspath} (no auto-pre-* snapshots)"
        )

    outcome = restore(
        abspath, target.name,
        config=config, auto_save=False, clean=clean,
    )
    # Consume so chained undo walks back rather than bouncing on the
    # same snapshot. ``restore`` already ran by this point, so even if
    # delete fails we leave the user in a consistent (just-restored)
    # state.
    _delete_snapshot_with_refs(Store(config), abspath, target.name)

    remaining = sum(
        1 for s in Store(config).list_snapshots(abspath)
        if is_undo_snapshot(s.name)
    )
    _record_event(
        Store(config), abspath, events.KIND_UNDO,
        snapshot=target.name,
        extracted=outcome.extracted_count,
        cleaned=outcome.cleaned_count,
        remaining=remaining,
    )
    return UndoOutcome(
        snapshot=target,
        extracted_count=outcome.extracted_count,
        cleaned_count=outcome.cleaned_count,
        remaining=remaining,
    )
