"""Selective path restore from a snapshot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from snapz import cas, events
from snapz.config import RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store
from snapz.util import auto_name
from snapz._api_core import (
    _ensure_blob_available,
    _ensure_entry_blobs_available,
    _load_manifest_or_raise,
    _record_event,
    _safe_snapshot_target_path,
    _safe_snapshot_symlink_target,
    save,
)


# ---------------------------------------------------------------------------
# Revert — restore selected paths from a snapshot back into the live tree
# ---------------------------------------------------------------------------


@dataclass
class RevertOutcome:
    snapshot: SnapshotMeta
    pre_revert: Optional[SnapshotMeta]
    reverted_count: int             # files+symlinks written back
    deleted_count: int              # extras under revert prefix removed
    skipped: list[tuple[str, str]]  # (path, reason)


def revert(
    path: str | Path,
    name: str,
    paths: Iterable[str],
    *,
    config: Optional[RuntimeConfig] = None,
    auto_save: bool = True,
    delete_extras: bool = False,
) -> RevertOutcome:
    """Restore *paths* from snapshot *name* over the live source tree.

    Each entry in *paths* is interpreted source-relative: an exact file
    match restores that file; a directory (or anything that prefixes
    multiple manifest entries) restores everything underneath. Other
    files in the source are left untouched. With *delete_extras* True,
    files that exist under one of the requested prefixes but aren't in
    the snapshot are removed.

    Requires a CAS-format snapshot — legacy tar archives raise
    ``ValueError`` (use :func:`restore` or :func:`export` instead).
    """

    config = config or default_config()
    abspath, meta, manifest = _load_manifest_or_raise(path, name, config=config)
    dir_root = Store(config).dir_for(abspath)

    requested = [p.strip().rstrip("/") for p in paths if p and p.strip()]
    if not requested:
        raise ValueError("revert requires at least one path to restore")

    by_path = {e.path: e for e in manifest.entries}
    matched: dict[str, cas.ManifestEntry] = {}
    skipped: list[tuple[str, str]] = []
    for req in requested:
        exact = by_path.get(req)
        if exact is not None:
            matched[req] = exact
            continue
        prefix = req + "/"
        children = {p: e for p, e in by_path.items() if p.startswith(prefix)}
        if not children:
            skipped.append((req, "not in snapshot"))
            continue
        matched.update(children)

    pre_meta: Optional[SnapshotMeta] = None
    if auto_save and abspath.exists() and matched:
        pre_name = f"auto-pre-revert-{auto_name()[5:]}"
        try:
            pre_outcome = save(
                abspath, pre_name,
                config=config, include_large=True,
            )
            pre_meta = pre_outcome.snapshot
        except FileExistsError:
            pre_meta = None

    deleted_count = 0
    if delete_extras and matched:
        # Walk the live tree under each requested prefix, removing
        # anything that isn't being restored from the manifest.
        keep_relpaths = set(matched.keys())
        for req in requested:
            try:
                target_root = _safe_snapshot_target_path(abspath, req)
            except ValueError:
                skipped.append((req, "unsafe path"))
                continue
            if not target_root.exists():
                continue
            if target_root.is_file() or target_root.is_symlink():
                # Single-file revert: nothing to delete (we're about to
                # overwrite the target).
                continue
            for dirpath, _dirs, files in os.walk(target_root):
                for fn in files:
                    full = Path(dirpath) / fn
                    rel = str(full.relative_to(abspath))
                    if rel not in keep_relpaths:
                        try:
                            full.unlink()
                            deleted_count += 1
                        except OSError:
                            continue

    reverted = 0
    # Files first so dir creation is implicit; symlinks last.
    for entry in sorted(matched.values(), key=lambda e: (e.type != "file", e.path)):
        try:
            full = _safe_snapshot_target_path(abspath, entry.path)
        except ValueError:
            skipped.append((entry.path, "unsafe path"))
            continue
        full.parent.mkdir(parents=True, exist_ok=True)
        if entry.type == "file":
            if entry.chunks:
                _ensure_entry_blobs_available(dir_root, entry, config=config)
                size = cas.read_blobs_to(
                    dir_root,
                    entry.chunks,
                    full,
                    expected_sha256=entry.sha256,
                )
            else:
                if not entry.sha256:
                    skipped.append((entry.path, "missing sha"))
                    continue
                _ensure_blob_available(dir_root, entry.sha256, config=config)
                size = cas.read_blob_to(dir_root, entry.sha256, full)
            if entry.size is not None and size != entry.size:
                raise ValueError(f"blob size mismatch for {entry.path}")
            if full.is_symlink():
                try:
                    full.unlink()
                except OSError:
                    pass
            try:
                os.chmod(full, entry.mode)
            except OSError:
                pass
            try:
                os.utime(full, (entry.mtime, entry.mtime))
            except OSError:
                pass
            reverted += 1
        elif entry.type == "symlink" and entry.target is not None:
            try:
                _safe_snapshot_symlink_target(abspath, full, entry.target)
                if full.is_symlink() or full.exists():
                    full.unlink()
                os.symlink(entry.target, full)
                reverted += 1
            except ValueError as exc:
                skipped.append((entry.path, str(exc)))
            except OSError as exc:
                skipped.append((entry.path, str(exc)))

    _record_event(
        Store(config), abspath, events.KIND_REVERT,
        snapshot=name,
        pre_revert=(pre_meta.name if pre_meta else None),
        reverted=reverted,
        deleted=deleted_count,
        paths=sorted(set(requested))[:20],
    )
    return RevertOutcome(
        snapshot=meta,
        pre_revert=pre_meta,
        reverted_count=reverted,
        deleted_count=deleted_count,
        skipped=skipped,
    )
