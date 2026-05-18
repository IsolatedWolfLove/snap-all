"""Stash workflow on top of regular snapshots.

A *stash* is just a snapshot carrying the system tag ``stash``. The
on-disk name follows the pattern ``stash-<N>`` where N is a per-source
monotonically increasing counter. The CLI calls down here for the
``snapz stash / pop / apply / drop / list`` verbs.

This is a thin orchestration layer — the heavy lifting (packing,
restoring, deleting) all goes through :mod:`snapz.api`. We deliberately
*reuse* the snapshot infrastructure so stashes participate in
``snapz find``, ``snapz log``, ``snapz gc``, the TUI, etc., without
extra plumbing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from snapz import api
from snapz.config import RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store

# Stash names are restricted to ``stash-<N>``. The tag is the literal
# string ``stash`` and is reserved (callers must opt in via the
# ``allow_reserved`` knob on ``api.tag_add``).
STASH_TAG = "stash"
STASH_NAME_RE = re.compile(r"^stash-(\d+)$")


@dataclass
class StashOutcome:
    """Return value of :func:`stash_pop` and :func:`stash_apply`.

    *removed* is True only for ``pop`` after the underlying snapshot was
    deleted post-restore. *restore* carries the underlying restore
    metadata for callers that want to print extracted/cleaned counts.
    """

    snapshot: SnapshotMeta
    restore: Optional[api.RestoreOutcome]
    removed: bool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stash_index(meta: SnapshotMeta) -> int:
    """Return the numeric component of ``stash-<N>`` (or -1 if not stashy)."""

    m = STASH_NAME_RE.match(meta.name)
    return int(m.group(1)) if m else -1


def list_stashes(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[SnapshotMeta]:
    """Return stash snapshots for *path*, newest-first.

    Newest-first is determined by ``created`` timestamp, with the stash
    counter as the tie-breaker (the counter strictly increases per
    source so it doubles as a monotonic clock when two stashes land in
    the same calendar second).
    """

    config = config or default_config()
    snaps = api.list_snapshots(path, config=config)
    stashes = [s for s in snaps if STASH_TAG in s.tags]
    stashes.sort(key=lambda s: (s.created, _stash_index(s)), reverse=True)
    return stashes


def _next_stash_name(stashes: list[SnapshotMeta]) -> str:
    """Pick ``stash-<N>`` with N = max(existing) + 1, starting at 1."""

    used = {idx for idx in (_stash_index(s) for s in stashes) if idx >= 0}
    n = max(used, default=0) + 1
    return f"stash-{n}"


def _resolve_stash(
    stashes: list[SnapshotMeta], ref: Optional[str],
) -> SnapshotMeta:
    """Map a user-supplied *ref* to a concrete stash, or raise.

    Accepted forms:

    - ``None`` or ``""`` — latest stash (newest-first head).
    - ``@N`` (or just ``N``) — 0-based index from newest, like git.
    - ``stash-<N>`` — exact name match.
    """

    if not stashes:
        raise FileNotFoundError("no stashes")
    if ref is None or ref == "":
        return stashes[0]
    candidate = ref.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if candidate.isdigit():
        idx = int(candidate)
        if not (0 <= idx < len(stashes)):
            raise IndexError(
                f"stash index {idx} out of range (have {len(stashes)} stash(es))"
            )
        return stashes[idx]
    for s in stashes:
        if s.name == ref:
            return s
    raise FileNotFoundError(f"no stash named {ref!r}")


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------


def stash_save(
    path: str | Path,
    *,
    message: str = "",
    config: Optional[RuntimeConfig] = None,
) -> SnapshotMeta:
    """Capture the working tree as a new stash snapshot."""

    config = config or default_config()
    existing = list_stashes(path, config=config)
    name = _next_stash_name(existing)
    outcome = api.save(path, name, note=message or "", config=config)
    # Tag with the reserved ``stash`` label so list/find/log can group.
    meta = api.tag_add(
        path, name, [STASH_TAG],
        config=config, allow_reserved=True,
    )
    # `outcome.snapshot` predates tag_add, so prefer the post-tag meta.
    _ = outcome  # silence unused-warning when downstream tooling forks.
    return meta


def stash_apply(
    path: str | Path,
    ref: Optional[str] = None,
    *,
    config: Optional[RuntimeConfig] = None,
    auto_save: bool = True,
    clean: bool = False,
) -> StashOutcome:
    """Restore a stash without deleting it."""

    config = config or default_config()
    target = _resolve_stash(list_stashes(path, config=config), ref)
    restore = api.restore(
        path, target.name,
        config=config, auto_save=auto_save, clean=clean,
    )
    store = Store(config)
    abspath = store.abspath_for_source(path)
    api._record_event(store, abspath, api.events.KIND_STASH_APPLY, snapshot=target.name)
    return StashOutcome(snapshot=target, restore=restore, removed=False)


def stash_drop(
    path: str | Path,
    ref: Optional[str] = None,
    *,
    config: Optional[RuntimeConfig] = None,
) -> SnapshotMeta:
    """Discard a stash without restoring it."""

    config = config or default_config()
    target = _resolve_stash(list_stashes(path, config=config), ref)
    api.delete(path, target.name, config=config)
    store = Store(config)
    abspath = store.abspath_for_source(path)
    api._record_event(store, abspath, api.events.KIND_STASH_DROP, snapshot=target.name)
    return target


def stash_pop(
    path: str | Path,
    ref: Optional[str] = None,
    *,
    config: Optional[RuntimeConfig] = None,
    auto_save: bool = True,
    clean: bool = False,
) -> StashOutcome:
    """Restore a stash, then delete it on success."""

    config = config or default_config()
    target = _resolve_stash(list_stashes(path, config=config), ref)
    restore = api.restore(
        path, target.name,
        config=config, auto_save=auto_save, clean=clean,
    )
    # Restore succeeded — drop the stash. If the delete itself fails we
    # surface the error so the user can re-run ``stash drop`` manually.
    api.delete(path, target.name, config=config)
    store = Store(config)
    abspath = store.abspath_for_source(path)
    api._record_event(store, abspath, api.events.KIND_STASH_POP, snapshot=target.name)
    return StashOutcome(snapshot=target, restore=restore, removed=True)
