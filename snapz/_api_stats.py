"""Storage usage statistics for snapz sources."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from snapz import cas
from snapz.config import RuntimeConfig, default_config
from snapz.store import ARCHIVE_SUFFIXES, DirEntry, SnapshotMeta, Store
from snapz._api_core import _source_path


_STATS_CACHE_MISS = object()


# ---------------------------------------------------------------------------
# Stats — per-source-directory storage breakdown
# ---------------------------------------------------------------------------


@dataclass
class StatsEntry:
    """Storage breakdown for one source directory."""

    abspath: Path
    key: str
    snapshot_count: int
    logical_bytes: int          # sum of snapshot total_bytes_in (uncompressed)
    marginal_bytes: int         # sum of snapshot size_bytes (this snapshot's adds)
    on_disk_bytes: int          # everything inside the per-dir folder
    blob_count: int             # entries under objects/
    blob_bytes: int             # disk size of all blobs
    unique_logical_bytes: int   # uncompressed bytes for unique referenced blobs
    legacy_count: int           # legacy *.tar.* archives kept around
    legacy_bytes: int
    oldest: Optional[str]       # earliest snapshot ISO timestamp, if any
    newest: Optional[str]       # latest snapshot ISO timestamp, if any
    largest: Optional[SnapshotMeta]
    snapshots: list[SnapshotMeta] = field(default_factory=list)

    @property
    def dedup_ratio(self) -> float:
        denominator = self.unique_logical_bytes or self.blob_bytes
        if denominator <= 0:
            return 1.0
        return self.logical_bytes / denominator


def _path_disk_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for fn in files:
            try:
                total += (Path(dirpath) / fn).lstat().st_size
            except OSError:
                continue
    return total


def _scan_dir_folder(folder: Path) -> tuple[int, int, int, int, int]:
    """Return ``(disk_bytes, blob_count, blob_bytes, legacy_count, legacy_bytes)``."""

    if not folder.exists():
        return 0, 0, 0, 0, 0
    disk = _path_disk_bytes(folder)
    blob_count = 0
    blob_bytes = 0
    for sha in cas.referenced_blobs(folder):
        try:
            blob = cas.find_blob(folder, sha)
            blob_bytes += blob.stat().st_size
        except OSError:
            continue
        blob_count += 1
    # v3 blobs live outside the per-dir folder; include the source's
    # referenced share so stats still answer "what keeps this source
    # restorable?" rather than only reporting manifest metadata.
    disk += blob_bytes
    legacy_count = 0
    legacy_bytes = 0
    for child in folder.iterdir():
        if not child.is_file():
            continue
        if any(child.name.endswith(suf) for suf in ARCHIVE_SUFFIXES):
            try:
                legacy_bytes += child.stat().st_size
            except OSError:
                continue
            legacy_count += 1
    return disk, blob_count, blob_bytes, legacy_count, legacy_bytes


def _unique_logical_bytes(folder: Path) -> int:
    seen: set[str] = set()
    total = 0
    for manifest_path in cas.iter_manifest_paths(folder):
        try:
            manifest = cas.read_manifest(manifest_path)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        for entry in manifest.entries:
            if entry.chunks:
                for chunk in entry.chunks:
                    if chunk.sha256 in seen:
                        continue
                    seen.add(chunk.sha256)
                    total += max(0, int(chunk.size))
                continue
            if entry.sha256 and entry.sha256 not in seen:
                seen.add(entry.sha256)
                total += max(0, int(entry.size or 0))
    return total


def _stats_for_dir(store: Store, abspath: Path, key: str) -> StatsEntry:
    snaps = store.list_snapshots(abspath)
    folder = store.dir_for(abspath)
    disk, blob_count, blob_bytes, legacy_count, legacy_bytes = _scan_dir_folder(folder)
    logical = sum(s.total_bytes_in for s in snaps)
    marginal = sum(s.size_bytes for s in snaps)
    unique_logical = _unique_logical_bytes(folder)
    largest = max(snaps, key=lambda s: s.total_bytes_in, default=None)
    iso_values = sorted(s.created for s in snaps)
    return StatsEntry(
        abspath=abspath,
        key=key,
        snapshot_count=len(snaps),
        logical_bytes=logical,
        marginal_bytes=marginal,
        on_disk_bytes=disk,
        blob_count=blob_count,
        blob_bytes=blob_bytes,
        unique_logical_bytes=unique_logical,
        legacy_count=legacy_count,
        legacy_bytes=legacy_bytes,
        oldest=iso_values[0] if iso_values else None,
        newest=iso_values[-1] if iso_values else None,
        largest=largest,
        snapshots=snaps,
    )


def _stats_from_bulk_entry(entry: DirEntry) -> StatsEntry | object:
    """Build a fast global stats row from cached dir metadata."""

    snapshot_count = entry.meta.snapshot_count_cached
    on_disk_bytes = entry.meta.on_disk_bytes_cached
    if snapshot_count <= 0 or on_disk_bytes <= 0:
        return _STATS_CACHE_MISS
    return StatsEntry(
        abspath=Path(entry.meta.abspath),
        key=entry.key,
        snapshot_count=snapshot_count,
        logical_bytes=0,
        marginal_bytes=0,
        on_disk_bytes=on_disk_bytes,
        blob_count=0,
        blob_bytes=0,
        unique_logical_bytes=0,
        legacy_count=0,
        legacy_bytes=0,
        oldest=None,
        newest=entry.meta.last_used or None,
        largest=None,
        snapshots=[],
    )


def stats(
    path: Optional[str | Path] = None,
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[StatsEntry]:
    """Return storage breakdowns: a single source if *path* is given, else all.

    The list is sorted by on-disk size (descending), so the heaviest
    consumers float to the top.
    """

    config = config or default_config()
    store = Store(config)
    out: list[StatsEntry] = []

    if path is not None:
        abspath = _source_path(path, config=config)
        out.append(_stats_for_dir(store, abspath, store.key_for(abspath)))
    else:
        for entry in store.load_all_meta_bulk():
            abspath = Path(entry.meta.abspath)
            cached = _stats_from_bulk_entry(entry)
            if cached is _STATS_CACHE_MISS:
                out.append(_stats_for_dir(store, abspath, entry.key))
            else:
                out.append(cached)

    out.sort(key=lambda s: s.on_disk_bytes, reverse=True)
    return out
