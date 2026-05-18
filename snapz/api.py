"""Programmatic entry points.

These are the functions external integrations (e.g. ``topics-bot``'s
``SnapshotBot``) should import. They never prompt the user — interactive
behaviour lives in :mod:`snapz.cli`.
"""

from __future__ import annotations

import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BufferedWriter
from io import BytesIO
from pathlib import Path
import shutil
from contextlib import contextmanager
from typing import Callable, Iterable, Optional

from snapz import archive, cas, events, filecache, preferences
from snapz.archive import ArchiveMember, PackResult, ProgressCallback, WalkResult
from snapz.config import META_SUFFIX, RuntimeConfig, default_config
from snapz.ignore import build_matcher
from snapz.store import (
    ARCHIVE_SUFFIXES,
    DirEntry,
    DirMeta,
    SnapshotMeta,
    Store,
    read_source_marker,
    source_identity,
    source_marker_path,
    write_source_marker,
)
from snapz.util import (
    auto_name,
    compute_key,
    is_undo_snapshot,
    now_iso,
    resolve_path,
    validate_snapshot_name,
    validate_tag,
)


@dataclass
class SaveOutcome:
    snapshot: SnapshotMeta
    pack_result: PackResult
    walk_result: WalkResult


@dataclass
class _SaveFileResult:
    sha256: str
    size: int
    was_new: bool
    blob_bytes: int


@dataclass
class RestoreEstimate:
    """Comparison of an archive against the current source tree."""

    snapshot: SnapshotMeta
    archive_path: Path
    new_files: list[str]            # in archive, missing on disk
    overwritten_files: list[str]    # in both
    extra_files: list[str]          # on disk, not in archive (for --clean)
    archive_member_count: int
    archive_total_bytes: int


@dataclass
class RestoreOutcome:
    snapshot: SnapshotMeta
    pre_restore: Optional[SnapshotMeta]
    extracted_count: int
    cleaned_count: int


@dataclass
class ExportOutcome:
    snapshot: SnapshotMeta
    destination: Path
    extracted_count: int


@dataclass
class BundleExportOutcome:
    source: Path
    destination: Path
    key: str
    snapshot_count: int
    blob_count: int
    size_bytes: int


@dataclass
class BundleImportOutcome:
    bundle: Path
    source: Path
    key: str
    snapshot_count: int
    blob_count: int
    imported_snapshots: list[str]
    overwritten_snapshots: list[str]
    archived: bool


@dataclass
class SourceInitOutcome:
    source: Path
    marker_path: Path
    marker_id: str
    created: bool


@dataclass
class RelocationCandidate:
    key: str
    old_path: Path
    new_path: Path
    method: str
    snapshot_count: int


@dataclass
class RelocationSkip:
    key: str
    old_path: Path
    reason: str
    candidates: list[RelocationCandidate] = field(default_factory=list)


@dataclass
class AutoRelocateOutcome:
    roots: list[Path]
    candidates: list[RelocationCandidate]
    relocated: list[RelocationCandidate]
    skipped: list[RelocationSkip]
    dry_run: bool


@dataclass
class FileChange:
    """Single per-path diff entry between two snapshots (or snapshot vs live)."""

    path: str
    status: str           # "A" added, "M" modified, "D" deleted, "T" type-change
    size_a: Optional[int] = None
    size_b: Optional[int] = None
    sha_a: Optional[str] = None
    sha_b: Optional[str] = None
    type_a: Optional[str] = None
    type_b: Optional[str] = None


@dataclass
class DiffResult:
    a_meta: SnapshotMeta
    b_meta: Optional[SnapshotMeta]   # None when comparing snapshot vs live
    changes: list[FileChange]
    abspath: Path

    @property
    def added(self) -> list[FileChange]:
        return [c for c in self.changes if c.status == "A"]

    @property
    def modified(self) -> list[FileChange]:
        return [c for c in self.changes if c.status in ("M", "T")]

    @property
    def deleted(self) -> list[FileChange]:
        return [c for c in self.changes if c.status == "D"]


@dataclass
class GcResult:
    """Result of :func:`gc` — reclaimed blob count + freed bytes."""

    blobs_removed: int
    bytes_freed: int
    dirs_scanned: int
    dry_run: bool


@dataclass
class CheckIssue:
    severity: str
    code: str
    message: str
    path: str = ""
    snapshot: Optional[str] = None
    fixed: bool = False


@dataclass
class CheckResult:
    dirs_scanned: int
    issues: list[CheckIssue]
    fixed_count: int
    deep: bool

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


@dataclass
class MigrateOutcome:
    dirs_scanned: int
    blobs_migrated: int
    bytes_migrated: int
    blobs_skipped: int
    dry_run: bool


_STATS_CACHE_MISS = object()


def _record_event(
    store: "Store",
    abspath: Path,
    kind: str,
    *,
    snapshot: str = "",
    **extra,
) -> None:
    """Append an event for *abspath*'s store folder. Never raises."""

    try:
        folder = store.dir_for(abspath)
        key = folder.name
    except Exception:
        return
    events.record(
        folder,
        kind,
        source=str(abspath),
        snapshot=snapshot,
        key=key,
        **extra,
    )


def estimate(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    include_large: bool = False,
) -> WalkResult:
    """Run a dry-run walk over *path* and return the projected workload."""

    config = config or default_config()
    abspath = _source_path(path, config=config)
    if not abspath.is_dir():
        raise NotADirectoryError(f"not a directory: {abspath}")
    matcher = build_matcher(
        abspath,
        apply_defaults=config.apply_default_ignores,
        apply_gitignore=config.apply_gitignore,
        apply_snapzignore=config.apply_snapzignore,
        local_excludes_path=preferences.local_excludes_path(
            Store(config).dir_for(abspath)
        ),
    )
    return archive.dry_run(abspath, matcher, config, include_large=include_large)


def _write_save_blob(
    dir_root: Path,
    source: Path,
    *,
    use_zstd: bool,
) -> _SaveFileResult:
    sha, blob_size, was_new = cas.write_blob(
        dir_root,
        source,
        use_zstd=use_zstd,
        global_store=True,
    )
    blob_bytes = 0
    if was_new:
        try:
            blob_bytes = cas.find_blob(dir_root, sha).stat().st_size
        except OSError:
            blob_bytes = 0
    return _SaveFileResult(
        sha256=sha,
        size=blob_size,
        was_new=was_new,
        blob_bytes=blob_bytes,
    )


def save(
    path: str | Path,
    name: Optional[str] = None,
    *,
    config: Optional[RuntimeConfig] = None,
    include_large: bool = False,
    on_progress: Optional[ProgressCallback] = None,
    walk_result: Optional[WalkResult] = None,
    overwrite: bool = False,
    note: str = "",
    use_file_cache: Optional[bool] = None,
) -> SaveOutcome:
    """Create a new snapshot of *path*.

    Pass *walk_result* to re-use a previous :func:`estimate` result and
    avoid scanning the tree twice.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    if not abspath.is_dir():
        raise NotADirectoryError(f"not a directory: {abspath}")

    snapshot_name = name or auto_name()
    validate_snapshot_name(snapshot_name)

    store = Store(config)
    if not overwrite and store.name_exists(abspath, snapshot_name):
        raise FileExistsError(f"snapshot '{snapshot_name}' already exists")

    if walk_result is None:
        matcher = build_matcher(
            abspath,
            apply_defaults=config.apply_default_ignores,
            apply_gitignore=config.apply_gitignore,
            apply_snapzignore=config.apply_snapzignore,
            local_excludes_path=preferences.local_excludes_path(
                store.dir_for(abspath)
            ),
        )
        walk_result = archive.dry_run(
            abspath, matcher, config, include_large=include_large
        )

    store.ensure_dir(abspath)
    if overwrite:
        _delete_snapshot_with_refs(store, abspath, snapshot_name)

    dir_root = store.dir_for(abspath)
    use_zstd = config.use_zstd and archive.zstd_available()
    cache_enabled = config.use_file_cache if use_file_cache is None else use_file_cache
    current_cache = filecache.load(dir_root) if cache_enabled else {}
    next_cache: dict[str, filecache.CacheEntry] = {}

    planned: list[tuple[FileEntry, os.stat_result | None, str | None]] = []
    to_process: list[tuple[int, FileEntry]] = []
    for fe in walk_result.files:
        try:
            stat_info = fe.abspath.lstat()
        except OSError:
            planned.append((fe, None, None))
            continue
        if fe.is_symlink:
            planned.append((fe, stat_info, None))
            continue
        cached_sha = filecache.lookup(current_cache, fe, stat_info)
        if cached_sha is not None:
            try:
                cas.find_blob(dir_root, cached_sha)
            except FileNotFoundError:
                cached_sha = None
        if cached_sha is not None:
            planned.append((fe, stat_info, cached_sha))
        else:
            planned.append((fe, stat_info, None))
            to_process.append((len(planned) - 1, fe))

    file_results: dict[int, _SaveFileResult] = {}
    worker_count = max(1, int(config.save_workers))
    if to_process:
        if worker_count == 1 or len(to_process) == 1:
            for plan_index, fe in to_process:
                try:
                    file_results[plan_index] = _write_save_blob(
                        dir_root,
                        fe.abspath,
                        use_zstd=use_zstd,
                    )
                except OSError:
                    continue
        else:
            max_workers = min(worker_count, len(to_process))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _write_save_blob,
                        dir_root,
                        fe.abspath,
                        use_zstd=use_zstd,
                    ): plan_index
                    for plan_index, fe in to_process
                }
                for future in as_completed(futures):
                    plan_index = futures[future]
                    try:
                        file_results[plan_index] = future.result()
                    except OSError:
                        continue

    # Build the manifest in walk order after parallel blob writes finish.
    entries: list[cas.ManifestEntry] = []
    new_blob_count = 0
    new_blob_bytes = 0
    total_bytes_in = 0

    for index, (fe, stat_info, cached) in enumerate(planned, start=1):
        if stat_info is None:
            if on_progress is not None:
                on_progress(index, walk_result.file_count, fe)
            continue
        mode = stat_info.st_mode & 0o7777
        mtime = stat_info.st_mtime

        if fe.is_symlink:
            try:
                target_str = os.readlink(fe.abspath)
            except OSError:
                if on_progress is not None:
                    on_progress(index, walk_result.file_count, fe)
                continue
            entries.append(cas.ManifestEntry(
                path=fe.relpath,
                type="symlink",
                mode=mode,
                mtime=mtime,
                target=target_str,
            ))
        else:
            if isinstance(cached, str):
                result = _SaveFileResult(
                    sha256=cached,
                    size=stat_info.st_size,
                    was_new=False,
                    blob_bytes=0,
                )
            else:
                result = file_results.get(index - 1)
                if result is None:
                    if on_progress is not None:
                        on_progress(index, walk_result.file_count, fe)
                    continue
            entries.append(cas.ManifestEntry(
                path=fe.relpath,
                type="file",
                mode=mode,
                mtime=mtime,
                sha256=result.sha256,
                size=result.size,
            ))
            total_bytes_in += result.size
            if result.was_new:
                new_blob_count += 1
                new_blob_bytes += result.blob_bytes
            if cache_enabled:
                next_cache[fe.relpath] = filecache.CacheEntry(
                    size=result.size,
                    mtime=mtime,
                    inode=int(getattr(stat_info, "st_ino", 0)),
                    sha256=result.sha256,
                )

        if on_progress is not None:
            on_progress(index, walk_result.file_count, fe)

    created = now_iso()
    manifest = cas.Manifest(
        snapshot=snapshot_name,
        created=created,
        entries=entries,
    )
    m_path = cas.manifest_path(dir_root, snapshot_name)
    cas.write_manifest(m_path, manifest)

    compression_label = "zstd-cas" if use_zstd else "gzip-cas"
    meta = SnapshotMeta(
        name=snapshot_name,
        source=str(abspath),
        created=created,
        size_bytes=new_blob_bytes,            # marginal cost (this snapshot's adds)
        file_count=len(entries),
        total_bytes_in=total_bytes_in,        # logical content sum
        compression=compression_label,
        archive=m_path.name,
        note=note.strip(),
    )
    store.record_snapshot(meta)
    cas.increment_refs(
        store.root,
        [entry.sha256 for entry in entries if entry.sha256],
    )
    if cache_enabled:
        filecache.save(dir_root, next_cache)
    _record_event(
        store, abspath, events.KIND_SAVE,
        snapshot=snapshot_name,
        file_count=len(entries),
        size_bytes=new_blob_bytes,
        note=(note.strip() or None),
    )

    _auto_prune_after_save(abspath, config)
    store.refresh_cached_summary_in_dir(dir_root)

    pack_result = PackResult(
        archive_path=m_path,
        bytes_written=new_blob_bytes,
        file_count=len(entries),
        total_bytes_in=total_bytes_in,
        compression=compression_label,
    )
    return SaveOutcome(snapshot=meta, pack_result=pack_result, walk_result=walk_result)


def list_snapshots(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[SnapshotMeta]:
    config = config or default_config()
    abspath = _source_path(path, config=config)
    return Store(config).list_snapshots(abspath)


def list_all(
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[DirEntry]:
    config = config or default_config()
    return Store(config).list_all()


def list_archives(
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[DirEntry]:
    config = config or default_config()
    return Store(config).list_archived()


def relocate_source(
    old: str | Path,
    new: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> DirEntry:
    config = config or default_config()
    store = Store(config)
    old_path = resolve_path(old)
    new_path = resolve_path(new)
    entry = store.relocate_source(old_path, new_path)
    _record_event(
        store, new_path, events.KIND_RELOCATE,
        previous=str(old_path),
        snapshot_count=len(entry.snapshots),
    )
    return entry


def adopt_archive(
    archive_key: str,
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> DirEntry:
    """Bind an archived source record to an explicit local directory."""

    config = config or default_config()
    store = Store(config)
    new_path = resolve_path(path)
    entry = store.relocate_key(archive_key, new_path)
    _record_event(
        store, new_path, events.KIND_ADOPT,
        previous_key=archive_key,
        snapshot_count=len(entry.snapshots),
    )
    return entry


def init_source(
    path: str | Path = ".",
    *,
    config: Optional[RuntimeConfig] = None,
    force: bool = False,
) -> SourceInitOutcome:
    """Create the opt-in ``.snapz-id`` marker used for move detection."""

    config = config or default_config()
    abspath = resolve_path(path)
    if not abspath.is_dir():
        raise NotADirectoryError(f"not a directory: {abspath}")
    marker_id, created = write_source_marker(abspath, force=force)

    store = Store(config)
    registry = store._load_registry()  # noqa: SLF001
    changed = False
    for entry in store.find_dirs_for_source(abspath):
        folder = store.dir_by_key(entry.key)
        meta = store._read_dir_meta_from_folder(folder, abspath)  # noqa: SLF001
        meta.source_marker = marker_id
        store._write_dir_meta_to_folder(folder, meta)  # noqa: SLF001
        reg_entry = registry.setdefault("dirs", {}).get(entry.key)
        if reg_entry is not None:
            reg_entry["source_marker"] = marker_id
            changed = True
    if changed:
        store._save_registry(registry)  # noqa: SLF001

    return SourceInitOutcome(
        source=abspath,
        marker_path=source_marker_path(abspath),
        marker_id=marker_id,
        created=created,
    )


_RELOCATION_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".snapz-all",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


def _iter_relocation_scan_dirs(root: Path, storage_root: Path) -> Iterable[Path]:
    storage_root = storage_root.resolve()
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        try:
            current_resolved = current.resolve()
        except OSError:
            current_resolved = current
        if current_resolved == storage_root:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if d not in _RELOCATION_SKIP_DIRS
            and not d.endswith(".egg-info")
        ]
        yield current


def find_relocation_candidates(
    roots: Iterable[str | Path],
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[RelocationCandidate]:
    """Find archived sources that appear to have moved under *roots*.

    Matches are exact only: either the source directory inode is the same
    as the archived record, or an opt-in ``.snapz-id`` marker matches.
    Ambiguity is handled by :func:`auto_relocate_sources`.
    """

    config = config or default_config()
    store = Store(config)
    root_paths = [resolve_path(r) for r in roots]
    for root in root_paths:
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root}")

    archives = store.list_archived()
    by_source_id: dict[str, list[DirEntry]] = {}
    by_marker: dict[str, list[DirEntry]] = {}
    for entry in archives:
        if entry.meta.source_id:
            by_source_id.setdefault(entry.meta.source_id, []).append(entry)
        if entry.meta.source_marker:
            by_marker.setdefault(entry.meta.source_marker, []).append(entry)

    found: dict[tuple[str, str], RelocationCandidate] = {}
    for root in root_paths:
        for candidate_dir in _iter_relocation_scan_dirs(root, store.root):
            marker = read_source_marker(candidate_dir)
            source_id = source_identity(candidate_dir)
            matches: dict[str, set[str]] = {}
            if marker:
                for entry in by_marker.get(marker, []):
                    matches.setdefault(entry.key, set()).add("marker")
            if source_id:
                for entry in by_source_id.get(source_id, []):
                    matches.setdefault(entry.key, set()).add("inode")
            if not matches:
                continue
            entries_by_key = {e.key: e for e in archives}
            for key, methods in matches.items():
                entry = entries_by_key.get(key)
                if entry is None:
                    continue
                pair = (key, str(candidate_dir.resolve()))
                existing = found.get(pair)
                method = "+".join(sorted(methods))
                if existing is not None:
                    combined = set(existing.method.split("+")) | methods
                    existing.method = "+".join(sorted(combined))
                    continue
                found[pair] = RelocationCandidate(
                    key=entry.key,
                    old_path=Path(entry.meta.abspath),
                    new_path=candidate_dir.resolve(),
                    method=method,
                    snapshot_count=len(entry.snapshots),
                )

    return sorted(
        found.values(),
        key=lambda c: (str(c.old_path), str(c.new_path), c.method),
    )


def auto_relocate_sources(
    roots: Iterable[str | Path],
    *,
    config: Optional[RuntimeConfig] = None,
    dry_run: bool = False,
) -> AutoRelocateOutcome:
    """Automatically relocate archived sources with one exact match."""

    config = config or default_config()
    store = Store(config)
    root_paths = [resolve_path(r) for r in roots]
    candidates = find_relocation_candidates(root_paths, config=config)
    by_key: dict[str, list[RelocationCandidate]] = {}
    by_new_path: dict[str, list[RelocationCandidate]] = {}
    for candidate in candidates:
        by_key.setdefault(candidate.key, []).append(candidate)
        by_new_path.setdefault(str(candidate.new_path), []).append(candidate)

    relocated: list[RelocationCandidate] = []
    skipped: list[RelocationSkip] = []
    for key, matches in sorted(by_key.items()):
        entry = store.entry_by_key(key)
        old_path = Path(entry.meta.abspath) if entry is not None else matches[0].old_path
        unique_paths = {str(c.new_path) for c in matches}
        if len(unique_paths) != 1:
            skipped.append(RelocationSkip(
                key=key,
                old_path=old_path,
                reason="ambiguous-candidates",
                candidates=matches,
            ))
            continue
        candidate = matches[0]
        path_claims = by_new_path.get(str(candidate.new_path), [])
        claimed_keys = {c.key for c in path_claims}
        if len(claimed_keys) != 1:
            skipped.append(RelocationSkip(
                key=key,
                old_path=old_path,
                reason="target-matches-multiple-sources",
                candidates=path_claims,
            ))
            continue
        if dry_run:
            relocated.append(candidate)
            continue
        try:
            refreshed = store.relocate_key(key, candidate.new_path)
        except (FileNotFoundError, FileExistsError, NotADirectoryError) as exc:
            skipped.append(RelocationSkip(
                key=key,
                old_path=old_path,
                reason=str(exc),
                candidates=[candidate],
            ))
            continue
        relocated.append(RelocationCandidate(
            key=refreshed.key,
            old_path=candidate.old_path,
            new_path=Path(refreshed.meta.abspath),
            method=candidate.method,
            snapshot_count=len(refreshed.snapshots),
        ))

    return AutoRelocateOutcome(
        roots=root_paths,
        candidates=candidates,
        relocated=relocated,
        skipped=skipped,
        dry_run=dry_run,
    )


def auto_relocate_path(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    dry_run: bool = False,
) -> Optional[RelocationCandidate]:
    """Relocate one archived source when *path* is an exact identity match.

    This is the quiet convenience path used by normal source-oriented
    operations. It checks only *path* itself, never a recursive search,
    and acts only when exactly one archived source matches by inode or
    opt-in ``.snapz-id`` marker.
    """

    config = config or default_config()
    abspath = resolve_path(path)
    if not abspath.is_dir():
        return None

    store = Store(config)
    active = store.entry_by_key(store.key_for(abspath))
    if active is not None and not active.archived:
        return None

    marker = read_source_marker(abspath)
    current_id = source_identity(abspath)
    matches: list[RelocationCandidate] = []
    for entry in store.list_archived():
        methods: set[str] = set()
        if marker and entry.meta.source_marker == marker:
            methods.add("marker")
        if current_id and entry.meta.source_id == current_id:
            methods.add("inode")
        if not methods:
            continue
        matches.append(RelocationCandidate(
            key=entry.key,
            old_path=Path(entry.meta.abspath),
            new_path=abspath,
            method="+".join(sorted(methods)),
            snapshot_count=len(entry.snapshots),
        ))

    keys = {m.key for m in matches}
    if len(keys) != 1 or not matches:
        return None

    candidate = matches[0]
    if dry_run:
        return candidate
    try:
        refreshed = store.relocate_key(candidate.key, abspath)
    except (FileNotFoundError, FileExistsError, NotADirectoryError):
        return None
    return RelocationCandidate(
        key=refreshed.key,
        old_path=candidate.old_path,
        new_path=Path(refreshed.meta.abspath),
        method=candidate.method,
        snapshot_count=len(refreshed.snapshots),
    )


def _source_path(path: str | Path, *, config: RuntimeConfig) -> Path:
    abspath = resolve_path(path)
    if abspath.is_dir():
        auto_relocate_path(abspath, config=config)
    return abspath


def _manifest_shas_for_snapshot(store: Store, abspath: Path, name: str) -> list[str]:
    artifact = store.find_archive(abspath, name)
    if artifact is None or not cas.is_manifest_artifact(artifact):
        return []
    try:
        manifest = cas.read_manifest(artifact)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return []
    return [entry.sha256 for entry in manifest.entries if entry.sha256]


def _delete_snapshot_with_refs(store: Store, abspath: Path, name: str) -> bool:
    shas = _manifest_shas_for_snapshot(store, abspath, name)
    removed = store.delete_snapshot(abspath, name)
    if removed and shas:
        cas.decrement_refs(store.root, shas)
    return removed


def delete(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> bool:
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    removed = _delete_snapshot_with_refs(store, abspath, name)
    if removed:
        _record_event(store, abspath, events.KIND_DELETE, snapshot=name)
    return removed


def rename(
    path: str | Path,
    old: str,
    new: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> bool:
    validate_snapshot_name(new)
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    renamed = store.rename_snapshot(abspath, old, new)
    if renamed:
        _record_event(
            store, abspath, events.KIND_RENAME,
            snapshot=new, previous=old,
        )
    return renamed


def protect(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> SnapshotMeta:
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    meta = store.protect_snapshot(abspath, name, True)
    _record_event(store, abspath, events.KIND_PROTECT, snapshot=name)
    return meta


def unprotect(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> SnapshotMeta:
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    meta = store.protect_snapshot(abspath, name, False)
    _record_event(store, abspath, events.KIND_UNPROTECT, snapshot=name)
    return meta


def tag_add(
    path: str | Path,
    name: str,
    tags: Iterable[str],
    *,
    config: Optional[RuntimeConfig] = None,
    allow_reserved: bool = False,
) -> SnapshotMeta:
    """Add *tags* to snapshot *name*. Existing tags are preserved."""

    cleaned = [validate_tag(t, allow_reserved=allow_reserved) for t in tags]
    if not cleaned:
        raise ValueError("tag_add requires at least one tag")
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    meta = store.read_snapshot_meta(abspath, name)
    if meta is None:
        raise FileNotFoundError(f"no snapshot named '{name}' under {abspath}")
    existing = list(meta.tags)
    seen = set(existing)
    added: list[str] = []
    for tag in cleaned:
        if tag in seen:
            continue
        existing.append(tag)
        seen.add(tag)
        added.append(tag)
    meta.tags = existing
    store.write_snapshot_meta(meta)
    if added:
        _record_event(
            store, abspath, events.KIND_TAG_ADD,
            snapshot=name, tags=added,
        )
    return meta


def tag_remove(
    path: str | Path,
    name: str,
    tags: Iterable[str],
    *,
    config: Optional[RuntimeConfig] = None,
) -> SnapshotMeta:
    """Drop *tags* from snapshot *name*. Unknown tags are silently ignored."""

    to_drop = {str(t).strip() for t in tags if str(t).strip()}
    if not to_drop:
        raise ValueError("tag_remove requires at least one tag")
    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    meta = store.read_snapshot_meta(abspath, name)
    if meta is None:
        raise FileNotFoundError(f"no snapshot named '{name}' under {abspath}")
    removed = [t for t in meta.tags if t in to_drop]
    meta.tags = [t for t in meta.tags if t not in to_drop]
    store.write_snapshot_meta(meta)
    if removed:
        _record_event(
            store, abspath, events.KIND_TAG_RM,
            snapshot=name, tags=removed,
        )
    return meta


def list_tags(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
) -> dict[str, list[SnapshotMeta]]:
    """Return ``{tag: [meta, ...]}`` grouping for *path*.

    Hidden auto-* snapshots are included so their system tags (if any)
    are visible; callers that want a user-facing view should filter
    with ``is_auto_snapshot`` themselves.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    out: dict[str, list[SnapshotMeta]] = {}
    for snap in Store(config).list_snapshots(abspath):
        for tag in snap.tags:
            out.setdefault(tag, []).append(snap)
    return out


def show(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> Optional[SnapshotMeta]:
    config = config or default_config()
    abspath = _source_path(path, config=config)
    return Store(config).read_snapshot_meta(abspath, name)


# ---------------------------------------------------------------------------
# Restore (M3)
# ---------------------------------------------------------------------------


def _resolve_archive(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> tuple[Path, SnapshotMeta, Path]:
    """Return ``(abspath, snapshot_meta, archive_path)`` or raise."""

    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    meta = store.read_snapshot_meta(abspath, name)
    if meta is None:
        raise FileNotFoundError(f"no snapshot named '{name}' under {abspath}")
    archive_path = store.find_archive(abspath, name)
    if archive_path is None:
        raise FileNotFoundError(
            f"snapshot meta exists but archive missing for '{name}'"
        )
    return abspath, meta, archive_path


def restore_estimate(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> RestoreEstimate:
    """Diff archive contents against the current source tree.

    The diff respects the *current* ignore configuration when computing
    extras, so files matching ``.gitignore`` / defaults won't show up as
    "candidates for --clean".
    """

    config = config or default_config()
    abspath, meta, archive_path = _resolve_archive(path, name, config=config)

    if cas.is_manifest_artifact(archive_path):
        manifest = cas.read_manifest(archive_path)
        archive_sizes: dict[str, int] = {
            e.path: (e.size or 0)
            for e in manifest.entries
            if e.type in ("file", "symlink")
        }
    else:
        members = archive.list_archive_members(archive_path)
        archive_sizes = {
            m.relpath.rstrip("/"): m.size for m in members if not m.is_dir
        }

    matcher = build_matcher(
        abspath,
        apply_defaults=config.apply_default_ignores,
        apply_gitignore=config.apply_gitignore,
        apply_snapzignore=config.apply_snapzignore,
        local_excludes_path=preferences.local_excludes_path(
            Store(config).dir_for(abspath)
        ),
    )
    walk = archive.walk(
        abspath,
        matcher,
        large_file_bytes=2**63 - 1,
        follow_symlinks=config.follow_symlinks,
        include_large=True,
    ) if abspath.exists() else None

    on_disk: set[str] = set()
    if walk is not None:
        on_disk = {f.relpath for f in walk.files}

    archive_set = set(archive_sizes.keys())
    new_files = sorted(archive_set - on_disk)
    overwritten = sorted(archive_set & on_disk)
    extras = sorted(on_disk - archive_set)
    total_bytes = sum(archive_sizes.values())

    return RestoreEstimate(
        snapshot=meta,
        archive_path=archive_path,
        new_files=new_files,
        overwritten_files=overwritten,
        extra_files=extras,
        archive_member_count=len(archive_sizes),
        archive_total_bytes=total_bytes,
    )


def restore(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
    auto_save: bool = True,
    clean: bool = False,
) -> RestoreOutcome:
    """Extract the named snapshot back over *path*.

    When *auto_save* is True (the default) the current state is first
    captured as ``auto-pre-restore-<ts>`` so a botched restore can be
    rolled back. When *clean* is True, files present in the working
    tree but absent from the archive are deleted (respecting the active
    ignore rules — ignored files are kept either way).
    """

    config = config or default_config()
    abspath, meta, archive_path = _resolve_archive(path, name, config=config)

    pre_meta: Optional[SnapshotMeta] = None
    if auto_save and abspath.exists():
        pre_name = f"auto-pre-restore-{auto_name()[5:]}"
        try:
            pre_outcome = save(
                abspath,
                pre_name,
                config=config,
                include_large=True,
            )
            pre_meta = pre_outcome.snapshot
        except FileExistsError:
            pre_meta = None  # extremely unlikely; skip rather than crash

    cleaned = 0
    if clean and abspath.exists():
        est = restore_estimate(abspath, name, config=config)
        for rel in est.extra_files:
            target = abspath / rel
            try:
                target.unlink()
                cleaned += 1
            except (OSError, IsADirectoryError):
                # best-effort; leftover dirs are pruned below
                continue

    abspath.mkdir(parents=True, exist_ok=True)
    if cas.is_manifest_artifact(archive_path):
        extracted = _extract_cas(
            archive_path,
            abspath,
            dir_root=Store(config).dir_for(abspath),
        )
    else:
        extracted = archive.unpack(archive_path, abspath)

    if clean and abspath.exists():
        # Remove now-empty directories that were not part of the archive.
        for dirpath, dirnames, filenames in os.walk(abspath, topdown=False):
            if not dirnames and not filenames and Path(dirpath) != abspath:
                try:
                    Path(dirpath).rmdir()
                except OSError:
                    continue

    _record_event(
        Store(config), abspath, events.KIND_RESTORE,
        snapshot=name,
        pre_restore=(pre_meta.name if pre_meta else None),
        extracted=extracted,
        cleaned=cleaned,
    )
    return RestoreOutcome(
        snapshot=meta,
        pre_restore=pre_meta,
        extracted_count=extracted,
        cleaned_count=cleaned,
    )


def _extract_cas(manifest_path: Path, target: Path, *, dir_root: Path) -> int:
    """Extract a CAS manifest's content over *target*. Returns entry count.

    Files come first (so missing parent dirs get created), then
    symlinks. Mode and mtime are reapplied per the manifest. Missing or
    corrupt blobs raise instead of silently producing a partial restore.
    """

    manifest = cas.read_manifest(manifest_path)
    extracted = 0

    for entry in manifest.entries:
        if entry.type != "file":
            continue
        full = target / entry.path
        full.parent.mkdir(parents=True, exist_ok=True)
        if not entry.sha256:
            raise ValueError(f"manifest entry lacks sha256: {entry.path}")
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
        extracted += 1

    for entry in manifest.entries:
        if entry.type != "symlink" or entry.target is None:
            continue
        full = target / entry.path
        full.parent.mkdir(parents=True, exist_ok=True)
        if full.is_symlink() or full.exists():
            try:
                full.unlink()
            except OSError:
                pass
        try:
            os.symlink(entry.target, full)
            extracted += 1
        except OSError:
            continue

    return extracted


# ---------------------------------------------------------------------------
# Diff — compare two snapshots (or snapshot vs live tree)
# ---------------------------------------------------------------------------


# Compact projection used internally by `diff()`: ``(type, key, size)``
# where ``key`` is either the file's sha256 or the symlink target. This
# normalises manifest entries and live tree entries into a single shape.
_DiffSide = tuple[str, str, int]


def _project_manifest(manifest: cas.Manifest) -> dict[str, _DiffSide]:
    return {
        e.path: (e.type, e.sha256 or (e.target or ""), e.size or 0)
        for e in manifest.entries if e.type in ("file", "symlink")
    }


def _project_live(abspath: Path, config: RuntimeConfig) -> dict[str, _DiffSide]:
    matcher = build_matcher(
        abspath,
        apply_defaults=config.apply_default_ignores,
        apply_gitignore=config.apply_gitignore,
        apply_snapzignore=config.apply_snapzignore,
        local_excludes_path=preferences.local_excludes_path(
            Store(config).dir_for(abspath)
        ),
    )
    walk = archive.walk(
        abspath,
        matcher,
        large_file_bytes=2**63 - 1,
        follow_symlinks=config.follow_symlinks,
        include_large=True,
    )
    out: dict[str, _DiffSide] = {}
    for fe in walk.files:
        try:
            if fe.is_symlink:
                out[fe.relpath] = ("symlink", os.readlink(fe.abspath), 0)
            else:
                sha, size = cas.hash_file(fe.abspath)
                out[fe.relpath] = ("file", sha, size)
        except OSError:
            continue
    return out


def _load_manifest_or_raise(
    path: str | Path, name: str, *, config: RuntimeConfig
) -> tuple[Path, SnapshotMeta, cas.Manifest]:
    abspath, meta, artifact = _resolve_archive(path, name, config=config)
    if not cas.is_manifest_artifact(artifact):
        raise ValueError(
            f"snapshot {name!r} is in legacy tar format; "
            f"diff requires CAS snapshots"
        )
    return abspath, meta, cas.read_manifest(artifact)


def diff(
    path: str | Path,
    a: str,
    b: Optional[str] = None,
    *,
    config: Optional[RuntimeConfig] = None,
) -> DiffResult:
    """Diff snapshot *a* against snapshot *b* (or the live tree if *b* is None).

    Changes are described as transitions ``a -> b``: paths only in *a*
    are *deleted*, paths only in *b* are *added*, content mismatch is
    *modified*, file/symlink type swap is *T*. Both snapshots must be
    in CAS (manifest) format.
    """

    config = config or default_config()
    abspath_a, meta_a, manifest_a = _load_manifest_or_raise(path, a, config=config)
    side_a = _project_manifest(manifest_a)

    if b is not None:
        _, meta_b, manifest_b = _load_manifest_or_raise(path, b, config=config)
        side_b = _project_manifest(manifest_b)
    else:
        meta_b = None
        side_b = _project_live(abspath_a, config)

    changes: list[FileChange] = []
    for p in sorted(set(side_a) | set(side_b)):
        a_side = side_a.get(p)
        b_side = side_b.get(p)

        if a_side is None:
            t_b, k_b, s_b = b_side
            changes.append(FileChange(
                path=p, status="A", size_b=s_b,
                sha_b=k_b if t_b == "file" else None, type_b=t_b,
            ))
        elif b_side is None:
            t_a, k_a, s_a = a_side
            changes.append(FileChange(
                path=p, status="D", size_a=s_a,
                sha_a=k_a if t_a == "file" else None, type_a=t_a,
            ))
        elif a_side != b_side:
            t_a, k_a, s_a = a_side
            t_b, k_b, s_b = b_side
            status = "T" if t_a != t_b else "M"
            changes.append(FileChange(
                path=p, status=status,
                size_a=s_a, size_b=s_b,
                sha_a=k_a if t_a == "file" else None,
                sha_b=k_b if t_b == "file" else None,
                type_a=t_a, type_b=t_b,
            ))

    return DiffResult(
        a_meta=meta_a, b_meta=meta_b, changes=changes, abspath=abspath_a,
    )


def add_local_excludes(
    path: str | Path,
    patterns: list[str],
    *,
    config: Optional[RuntimeConfig] = None,
) -> int:
    """Append *patterns* to the per-source local excludes file.

    Returns the number of new patterns persisted. Caller should pass
    relative paths (or gitignore-style globs); duplicates are skipped.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    dir_root = Store(config).dir_for(abspath)
    return preferences.append_local_excludes(dir_root, patterns)


# ---------------------------------------------------------------------------
# Single-file snapshot / live readers (used by the diff TUI drill-down)
# ---------------------------------------------------------------------------


def read_snapshot_bytes(
    path: str | Path,
    name: str,
    relpath: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> Optional[bytes]:
    """Return the bytes of *relpath* inside snapshot *name*.

    Returns ``None`` when the path doesn't exist in the snapshot.
    Symlink entries return their target string encoded as UTF-8 (so the
    caller can render them as a one-liner in a diff view). Raises
    ``ValueError`` on legacy tar snapshots — drill-down is CAS-only.
    """

    config = config or default_config()
    abspath, _meta, manifest = _load_manifest_or_raise(path, name, config=config)
    dir_root = Store(config).dir_for(abspath)
    relpath = relpath.strip().rstrip("/")

    for entry in manifest.entries:
        if entry.path != relpath:
            continue
        if entry.type == "file":
            if not entry.sha256:
                return None
            return cas.read_blob_bytes(dir_root, entry.sha256)
        if entry.type == "symlink":
            return (entry.target or "").encode("utf-8", errors="replace")
        return None
    return None


def read_live_bytes(path: str | Path, relpath: str) -> Optional[bytes]:
    """Return the live bytes of *relpath* under *path*, or ``None``.

    Symlinks are returned as their target encoded UTF-8 (mirrors the
    snapshot reader so a diff between manifest-symlink and live-symlink
    just compares targets).
    """

    abspath = resolve_path(path)
    relpath = relpath.strip().rstrip("/")
    target = abspath / relpath
    try:
        if target.is_symlink():
            return os.readlink(target).encode("utf-8", errors="replace")
        if target.is_file():
            return target.read_bytes()
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Export — restore into an arbitrary directory
# ---------------------------------------------------------------------------


def export(
    path: str | Path,
    name: str,
    dst: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    overwrite: bool = False,
) -> ExportOutcome:
    """Extract snapshot *name* (recorded for *path*) into *dst*.

    Unlike :func:`restore`, this writes to a destination of the user's
    choosing, never touches the original source, and never takes an
    auto-pre-restore snapshot. Refuses non-empty destinations unless
    *overwrite* is True.
    """

    config = config or default_config()
    abspath, meta, archive_path = _resolve_archive(path, name, config=config)

    dst_path = resolve_path(dst)
    if dst_path.exists():
        if not dst_path.is_dir():
            raise NotADirectoryError(f"not a directory: {dst_path}")
        if any(dst_path.iterdir()) and not overwrite:
            raise FileExistsError(
                f"destination is not empty: {dst_path} (pass --overwrite to allow)"
            )
    else:
        dst_path.mkdir(parents=True, exist_ok=True)

    if cas.is_manifest_artifact(archive_path):
        extracted = _extract_cas(
            archive_path,
            dst_path,
            dir_root=Store(config).dir_for(abspath),
        )
    else:
        extracted = archive.unpack(archive_path, dst_path)

    return ExportOutcome(
        snapshot=meta, destination=dst_path, extracted_count=extracted,
    )


def restore_archive(
    archive_key: str,
    name: str,
    dst: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    overwrite: bool = False,
) -> ExportOutcome:
    """Extract an archived source snapshot into an arbitrary destination."""

    config = config or default_config()
    store = Store(config)
    entry = store.entry_by_key(archive_key)
    if entry is None:
        raise FileNotFoundError(f"no archived source with key {archive_key!r}")
    if not entry.archived:
        raise ValueError(f"source {archive_key!r} is not archived")
    dir_root = store.dir_by_key(archive_key)
    meta = store.read_snapshot_meta_in_dir(dir_root, name)
    if meta is None:
        raise FileNotFoundError(
            f"no snapshot named '{name}' in archived source {archive_key}"
        )
    artifact = store.find_archive_in_dir(dir_root, name)
    if artifact is None:
        raise FileNotFoundError(
            f"snapshot meta exists but artifact missing for '{name}'"
        )

    dst_path = resolve_path(dst)
    if dst_path.exists():
        if not dst_path.is_dir():
            raise NotADirectoryError(f"not a directory: {dst_path}")
        if any(dst_path.iterdir()) and not overwrite:
            raise FileExistsError(
                f"destination is not empty: {dst_path} (pass --overwrite to allow)"
            )
    else:
        dst_path.mkdir(parents=True, exist_ok=True)

    if cas.is_manifest_artifact(artifact):
        extracted = _extract_cas(artifact, dst_path, dir_root=dir_root)
    else:
        extracted = archive.unpack(artifact, dst_path)
    return ExportOutcome(
        snapshot=meta,
        destination=dst_path,
        extracted_count=extracted,
    )


# ---------------------------------------------------------------------------
# Portable bundles — export/import snapshots between snapz stores
# ---------------------------------------------------------------------------


BUNDLE_FORMAT_VERSION = 1
BUNDLE_META_NAME = "bundle.json"


def _tar_add_json(tar: tarfile.TarFile, name: str, data: dict) -> None:
    raw = (
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = 0o600
    tar.addfile(info, BytesIO(raw))


def _tar_read_json(tar: tarfile.TarFile, name: str) -> dict:
    try:
        member = tar.getmember(name)
    except KeyError as exc:
        raise ValueError(f"bundle missing {name}") from exc
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError(f"bundle member is not a file: {name}")
    try:
        return json.loads(extracted.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"bundle has invalid JSON: {name}") from exc


def _copy_tar_member(tar: tarfile.TarFile, arcname: str, dst: Path) -> int:
    try:
        member = tar.getmember(arcname)
    except KeyError as exc:
        raise ValueError(f"bundle missing {arcname}") from exc
    if not member.isfile():
        raise ValueError(f"bundle member is not a file: {arcname}")
    src = tar.extractfile(member)
    if src is None:
        raise ValueError(f"bundle member is not readable: {arcname}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with open(tmp, "wb") as out:
        shutil.copyfileobj(src, out)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dst)
    return member.size


@contextmanager
def _open_bundle_tar_writer(path: Path):
    if archive.zstd_available():
        import zstandard as zstd

        with open(path, "wb") as raw:
            compressor = zstd.ZstdCompressor(level=6)
            with compressor.stream_writer(raw) as stream:
                writer = BufferedWriter(stream)
                try:
                    with tarfile.open(fileobj=writer, mode="w|") as tar:
                        yield tar
                finally:
                    writer.flush()
    else:
        with tarfile.open(path, "w:gz") as tar:
            yield tar


@contextmanager
def _open_bundle_tar_reader(path: Path):
    head = path.open("rb").read(4)
    if head[:4] == cas._ZSTD_MAGIC:
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        with path.open("rb") as raw, dctx.stream_reader(raw) as reader:
            data = reader.read()
        with tarfile.open(fileobj=BytesIO(data), mode="r:") as tar:
            yield tar
    else:
        with tarfile.open(path, "r:*") as tar:
            yield tar


def _safe_store_key(raw: object, fallback: str) -> str:
    key = str(raw or "")
    if not key or "/" in key or "\\" in key or key in {".", ".."}:
        return fallback
    if any(part == ".." for part in key.split("-")):
        return fallback
    return key


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _unique_import_key(root: Path, base_key: str) -> str:
    if not (root / base_key).exists():
        return base_key
    stem = f"{base_key}--import"
    if not (root / stem).exists():
        return stem
    index = 2
    while (root / f"{stem}{index}").exists():
        index += 1
    return f"{stem}{index}"


def export_bundle(
    source: str | Path,
    dst: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    overwrite: bool = False,
    archived: bool = False,
) -> BundleExportOutcome:
    """Pack all snapshots for one source into a portable ``.snapz`` bundle.

    Active sources are addressed by path. Archived sources can be exported
    by passing ``archived=True`` and using their archive key as *source*.
    """

    config = config or default_config()
    store = Store(config)

    if archived:
        key = str(source)
        entry = store.entry_by_key(key)
        if entry is None:
            raise FileNotFoundError(f"no archived source with key {key!r}")
        if not entry.archived:
            raise ValueError(f"source {key!r} is not archived")
        dir_root = store.dir_by_key(entry.key)
        snapshots = store.list_snapshots_in_dir(dir_root)
        source_path = Path(entry.meta.abspath)
    else:
        source_path = resolve_path(source)
        key = store.key_for(source_path)
        entry = store.entry_by_key(key)
        if entry is None or entry.archived:
            raise FileNotFoundError(f"no active snapshots under {source_path}")
        dir_root = store.dir_by_key(key)
        snapshots = store.list_snapshots(source_path)

    if not snapshots:
        raise FileNotFoundError(f"no snapshots to bundle for {source}")

    dst_path = resolve_path(dst)
    if dst_path.exists() and not overwrite:
        raise FileExistsError(
            f"destination exists: {dst_path} (pass --overwrite to replace)"
        )
    if dst_path.exists() and dst_path.is_dir():
        raise IsADirectoryError(f"destination is a directory: {dst_path}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot_rows: list[dict] = []
    blob_shas: set[str] = set()
    for snap in snapshots:
        meta_path = dir_root / f"{snap.name}{META_SUFFIX}"
        artifact = store.find_archive_in_dir(dir_root, snap.name)
        if artifact is None or not meta_path.exists():
            raise FileNotFoundError(
                f"snapshot {snap.name!r} is missing metadata or artifact"
            )
        is_manifest = cas.is_manifest_artifact(artifact)
        artifact_arc = (
            f"source/snapshots/{artifact.name}"
            if is_manifest
            else f"source/{artifact.name}"
        )
        snapshot_rows.append({
            "name": snap.name,
            "meta": f"source/{meta_path.name}",
            "artifact": artifact_arc,
            "kind": "manifest" if is_manifest else "legacy",
        })
        if is_manifest:
            manifest = cas.read_manifest(artifact)
            for entry_obj in manifest.entries:
                if entry_obj.sha256:
                    blob_shas.add(entry_obj.sha256)

    bundle_meta = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "created": now_iso(),
        "source": {
            "key": key,
            "abspath": entry.meta.abspath,
            "first_seen": entry.meta.first_seen,
            "last_used": entry.meta.last_used,
            "snapshot_count": len(snapshots),
            "source_id": entry.meta.source_id,
            "source_marker": entry.meta.source_marker,
            "archived_at": entry.meta.archived_at,
        },
        "snapshots": snapshot_rows,
        "blobs": sorted(blob_shas),
    }

    tmp = dst_path.with_suffix(dst_path.suffix + ".tmp")
    try:
        with _open_bundle_tar_writer(tmp) as tar:
            _tar_add_json(tar, BUNDLE_META_NAME, bundle_meta)
            tar.add(dir_root / "_meta.json", arcname="source/_meta.json", recursive=False)
            for row in snapshot_rows:
                meta_name = Path(row["meta"]).name
                tar.add(dir_root / meta_name, arcname=row["meta"], recursive=False)
                artifact_src = (
                    dir_root / "snapshots" / Path(row["artifact"]).name
                    if row["kind"] == "manifest"
                    else dir_root / Path(row["artifact"]).name
                )
                tar.add(artifact_src, arcname=row["artifact"], recursive=False)
            for sha in sorted(blob_shas):
                blob = cas.find_blob(dir_root, sha)
                tar.add(blob, arcname=f"objects/{sha[:2]}/{sha}", recursive=False)
        os.replace(tmp, dst_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    return BundleExportOutcome(
        source=source_path,
        destination=dst_path,
        key=key,
        snapshot_count=len(snapshots),
        blob_count=len(blob_shas),
        size_bytes=dst_path.stat().st_size,
    )


def import_bundle(
    bundle: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    path: Optional[str | Path] = None,
    overwrite: bool = False,
    target_key: Optional[str] = None,
) -> BundleImportOutcome:
    """Import a portable bundle into the local snapz store.

    Without *path*, imported snapshots stay archived. Passing *path* binds
    them to an existing live directory and lets normal ``list``/``restore``
    operations see them there.
    """

    config = config or default_config()
    store = Store(config)
    bundle_path = resolve_path(bundle)
    if not bundle_path.is_file():
        raise FileNotFoundError(f"not a bundle file: {bundle_path}")

    with _open_bundle_tar_reader(bundle_path) as tar:
        meta = _tar_read_json(tar, BUNDLE_META_NAME)
        if int(meta.get("format_version", 0)) != BUNDLE_FORMAT_VERSION:
            raise ValueError(
                f"unsupported bundle format: {meta.get('format_version')!r}"
            )
        source_data = dict(meta.get("source") or {})
        snapshots = list(meta.get("snapshots") or [])
        blobs = [str(s) for s in meta.get("blobs") or []]
        if not snapshots:
            raise ValueError("bundle has no snapshots")

        original_path = Path(str(source_data.get("abspath") or ".")).expanduser()
        if path is not None:
            source_path = resolve_path(path)
            if not source_path.is_dir():
                raise NotADirectoryError(f"not a directory: {source_path}")
            target_key = store.key_for(source_path)
            source_id = source_identity(source_path)
            source_marker = read_source_marker(source_path) or str(
                source_data.get("source_marker", "") or ""
            )
            archived_at = ""
        else:
            source_path = original_path
            fallback_key = compute_key(source_path)
            base_key = _safe_store_key(source_data.get("key"), fallback_key)
            target_key = (
                _safe_store_key(target_key, base_key)
                if target_key is not None
                else _unique_import_key(store.root, base_key)
            )
            source_id = str(source_data.get("source_id", "") or "")
            source_marker = str(source_data.get("source_marker", "") or "")
            archived_at = now_iso()

        target_dir = store.dir_by_key(target_key)
        existing_names = {
            snap.name for snap in store.list_snapshots_in_dir(target_dir)
        } if target_dir.exists() else set()
        incoming_names = [str(row.get("name") or "") for row in snapshots]
        for name in incoming_names:
            validate_snapshot_name(name)
        conflicts = sorted(existing_names & set(incoming_names))
        if conflicts and not overwrite:
            raise FileExistsError(
                "snapshot(s) already exist: "
                + ", ".join(conflicts)
                + " (pass --overwrite to replace)"
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        cas.objects_root(target_dir).mkdir(parents=True, exist_ok=True)
        cas.snapshots_root(target_dir).mkdir(parents=True, exist_ok=True)
        cas.global_objects_root(store.root).mkdir(parents=True, exist_ok=True)

        for sha in blobs:
            if not _is_sha256(sha):
                raise ValueError(f"invalid blob id in bundle: {sha!r}")
            dst_blob = cas.global_blob_path(store.root, sha)
            if dst_blob.exists():
                continue
            _copy_tar_member(tar, f"objects/{sha[:2]}/{sha}", dst_blob)

        imported_names: list[str] = []
        overwritten_names: list[str] = []
        for row in snapshots:
            name = str(row.get("name") or "")
            meta_arc = str(row.get("meta") or "")
            artifact_arc = str(row.get("artifact") or "")
            kind = str(row.get("kind") or "")
            snap_data = _tar_read_json(tar, meta_arc)
            snap = SnapshotMeta.from_dict(snap_data)
            snap.name = name
            snap.source = str(source_path)
            if kind == "manifest":
                artifact_dst = cas.manifest_path(target_dir, name)
                snap.archive = artifact_dst.name
            elif kind == "legacy":
                artifact_name = Path(artifact_arc).name
                artifact_dst = target_dir / artifact_name
                snap.archive = artifact_name
            else:
                raise ValueError(f"unknown snapshot artifact kind: {kind!r}")
            if name in existing_names:
                overwritten_names.append(name)
                (target_dir / f"{name}{META_SUFFIX}").unlink(missing_ok=True)
                cas.manifest_path(target_dir, name).unlink(missing_ok=True)
                cas.compressed_manifest_path(target_dir, name).unlink(missing_ok=True)
                for suffix in ARCHIVE_SUFFIXES:
                    (target_dir / f"{name}{suffix}").unlink(missing_ok=True)
            _copy_tar_member(tar, artifact_arc, artifact_dst)
            meta_dst = target_dir / f"{name}{META_SUFFIX}"
            meta_dst.write_text(
                json.dumps(snap.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(meta_dst, 0o600)
            except OSError:
                pass
            imported_names.append(name)

        dir_meta_obj = DirMeta(
            abspath=str(source_path),
            first_seen=str(source_data.get("first_seen") or now_iso()),
            last_used=now_iso(),
            source_id=source_id,
            source_marker=source_marker,
            archived_at=archived_at,
        )
        store._write_dir_meta_with_cached_summary(  # noqa: SLF001
            target_dir, dir_meta_obj,
        )
        dir_meta = store._registry_entry_for_meta(dir_meta_obj)  # noqa: SLF001
        try:
            os.chmod(store.root, 0o700)
            os.chmod(target_dir, 0o700)
            os.chmod(target_dir / "_meta.json", 0o600)
        except OSError:
            pass

        registry = store._load_registry()  # noqa: SLF001
        registry.setdefault("version", 1)
        registry.setdefault("dirs", {})[target_key] = dir_meta
        store._save_registry(registry)  # noqa: SLF001

        entry = store.entry_by_key(target_key)
        archived_state = True if entry is None else entry.archived

    return BundleImportOutcome(
        bundle=bundle_path,
        source=source_path,
        key=target_key,
        snapshot_count=len(imported_names),
        blob_count=len(blobs),
        imported_snapshots=imported_names,
        overwritten_snapshots=overwritten_names,
        archived=archived_state,
    )
# ---------------------------------------------------------------------------
# Garbage collection (M5+)
# ---------------------------------------------------------------------------


def gc(
    path: Optional[str | Path] = None,
    *,
    all_dirs: bool = False,
    dry_run: bool = False,
    rebuild_index: bool = False,
    config: Optional[RuntimeConfig] = None,
) -> GcResult:
    """Reclaim blob storage no longer referenced by any manifest.

    With *all_dirs* True, every per-dir folder under the storage root
    is scanned. Otherwise the scope is the directory at *path*.
    """

    config = config or default_config()
    store = Store(config)

    targets: list[Path] = []
    if all_dirs:
        for entry in store.list_all():
            targets.append(store.root / entry.key)
    elif path is not None:
        abspath = _source_path(path, config=config)
        targets.append(store.dir_for(abspath))
    else:
        raise ValueError("gc() requires either path= or all_dirs=True")

    total_blobs = 0
    total_bytes = 0
    scanned = 0
    for dir_root in targets:
        if not dir_root.exists():
            continue
        scanned += 1
        blobs, bytes_freed = cas.gc_dir(dir_root, dry_run=dry_run)
        total_blobs += blobs
        total_bytes += bytes_freed

    # v3 uses a root-level blob pool shared across sources. It is safe
    # to sweep on either path-scoped or all-dir GC because the reference
    # set is computed across the entire storage root.
    blobs, bytes_freed = cas.gc_global(
        store.root,
        dry_run=dry_run,
        rebuild_index=rebuild_index,
    )
    total_blobs += blobs
    total_bytes += bytes_freed

    return GcResult(
        blobs_removed=total_blobs,
        bytes_freed=total_bytes,
        dirs_scanned=scanned,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Check / migrate — store reliability tooling
# ---------------------------------------------------------------------------


def _dir_roots_for_scope(
    store: Store,
    path: Optional[str | Path],
    *,
    all_dirs: bool,
) -> list[tuple[Path, Optional[Path]]]:
    if all_dirs:
        out: list[tuple[Path, Optional[Path]]] = []
        if not store.root.exists():
            return out
        for child in store.root.iterdir():
            if child.name == cas.OBJECTS_DIR or not child.is_dir():
                continue
            dir_meta_path = child / "_meta.json"
            abspath: Optional[Path] = None
            try:
                data = json.loads(dir_meta_path.read_text(encoding="utf-8"))
                raw = data.get("abspath")
                if raw:
                    abspath = Path(raw)
            except (OSError, ValueError, TypeError):
                pass
            out.append((child, abspath))
        return out
    if path is None:
        raise ValueError("operation requires either path= or all_dirs=True")
    abspath = _source_path(path, config=store.config)
    return [(store.dir_for(abspath), abspath)]


def _issue(
    issues: list[CheckIssue],
    severity: str,
    code: str,
    message: str,
    *,
    path: Path | str = "",
    snapshot: Optional[str] = None,
    fixed: bool = False,
) -> None:
    issues.append(CheckIssue(
        severity=severity,
        code=code,
        message=message,
        path=str(path) if path else "",
        snapshot=snapshot,
        fixed=fixed,
    ))


def check(
    path: Optional[str | Path] = None,
    *,
    all_dirs: bool = False,
    deep: bool = False,
    fix: bool = False,
    config: Optional[RuntimeConfig] = None,
) -> CheckResult:
    """Validate store metadata and blob reachability.

    ``fix`` only performs safe repairs: chmod known store files,
    remove stale ``*.tmp`` files, rewrite a mismatched manifest
    snapshot name, and rebuild the registry from readable dir metadata.
    It does not delete snapshots or orphan blobs.
    """

    config = config or default_config()
    store = Store(config)
    issues: list[CheckIssue] = []
    fixed = 0

    targets = _dir_roots_for_scope(store, path, all_dirs=all_dirs)
    registry_dirs: dict[str, dict[str, object]] = {}
    for dir_root, abspath in targets:
        if not dir_root.exists():
            continue

        if fix:
            for candidate, mode in ((store.root, 0o700), (dir_root, 0o700)):
                try:
                    os.chmod(candidate, mode)
                except OSError:
                    pass

        dir_meta_path = dir_root / "_meta.json"
        try:
            dir_meta = json.loads(dir_meta_path.read_text(encoding="utf-8"))
            raw_abs = dir_meta.get("abspath")
            if raw_abs:
                abspath = Path(raw_abs)
        except (OSError, ValueError, TypeError) as exc:
            _issue(
                issues, "error", "bad-dir-meta",
                f"cannot read dir metadata: {exc}", path=dir_meta_path,
            )
            continue

        if abspath is not None:
            registry_dirs[dir_root.name] = {
                "abspath": str(abspath),
                "first_seen": dir_meta.get("first_seen", ""),
                "last_used": dir_meta.get("last_used", ""),
                "snapshot_count": dir_meta.get("snapshot_count", 0),
                "snapshot_count_cached": dir_meta.get(
                    "snapshot_count_cached",
                    dir_meta.get("snapshot_count", 0),
                ),
                "on_disk_bytes_cached": dir_meta.get(
                    "on_disk_bytes_cached", 0,
                ),
                "source_id": dir_meta.get("source_id", ""),
                "source_marker": dir_meta.get("source_marker", ""),
                "archived_at": dir_meta.get("archived_at", ""),
            }

        meta_by_name: dict[str, SnapshotMeta] = {}
        for meta_path in dir_root.glob(f"*{META_SUFFIX}"):
            if meta_path.name == "_meta.json":
                continue
            name = meta_path.name[: -len(META_SUFFIX)]
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                meta = SnapshotMeta.from_dict(meta_data)
            except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
                meta = None
            if meta is None:
                _issue(
                    issues, "error", "bad-snapshot-meta",
                    "cannot parse snapshot metadata",
                    path=meta_path, snapshot=name,
                )
                continue
            meta_by_name[name] = meta
            artifact = store.find_archive_in_dir(dir_root, name)
            if artifact is None:
                _issue(
                    issues, "error", "missing-artifact",
                    "snapshot metadata has no manifest/archive",
                    path=meta_path, snapshot=name,
                )
                continue
            if fix:
                try:
                    os.chmod(meta_path, 0o600)
                    os.chmod(artifact, 0o600)
                except OSError:
                    pass
            if not cas.is_manifest_artifact(artifact):
                continue
            try:
                manifest = cas.read_manifest(artifact)
            except (OSError, ValueError, KeyError) as exc:
                _issue(
                    issues, "error", "bad-manifest",
                    f"cannot read manifest: {exc}",
                    path=artifact, snapshot=name,
                )
                continue
            if manifest.snapshot != name:
                did_fix = False
                if fix:
                    manifest.snapshot = name
                    try:
                        cas.write_manifest(artifact, manifest)
                        did_fix = True
                        fixed += 1
                    except OSError:
                        did_fix = False
                _issue(
                    issues, "warn", "manifest-name-mismatch",
                    "manifest snapshot name does not match metadata",
                    path=artifact, snapshot=name, fixed=did_fix,
                )
            for entry in manifest.entries:
                if entry.type != "file":
                    continue
                if not entry.sha256:
                    _issue(
                        issues, "error", "missing-entry-sha",
                        f"file entry lacks sha256: {entry.path}",
                        path=artifact, snapshot=name,
                    )
                    continue
                try:
                    if deep:
                        size = cas.verify_blob(dir_root, entry.sha256)
                        if entry.size is not None and size != entry.size:
                            _issue(
                                issues, "error", "blob-size-mismatch",
                                f"{entry.path} expected {entry.size} bytes, got {size}",
                                path=cas.find_blob(dir_root, entry.sha256),
                                snapshot=name,
                            )
                    else:
                        cas.find_blob(dir_root, entry.sha256)
                except Exception as exc:
                    _issue(
                        issues, "error", "bad-blob",
                        f"{entry.path}: {exc}",
                        path=dir_root, snapshot=name,
                    )

        for manifest_path in cas.iter_manifest_paths(dir_root):
            name = cas.manifest_name(manifest_path)
            if name not in meta_by_name:
                _issue(
                    issues, "warn", "orphan-manifest",
                    "manifest has no snapshot metadata",
                    path=manifest_path, snapshot=name,
                )

        if fix:
            if filecache.remove(dir_root):
                fixed += 1
                _issue(
                    issues, "warn", "removed-file-cache",
                    "removed stale file hash cache",
                    path=filecache.cache_path(dir_root), fixed=True,
                )

            for tmp in list(dir_root.rglob("*.tmp")):
                try:
                    tmp.unlink()
                    fixed += 1
                    _issue(
                        issues, "warn", "removed-temp",
                        "removed stale temporary file",
                        path=tmp, fixed=True,
                    )
                except OSError:
                    pass

    refs = cas.referenced_blobs_in_root(store.root)
    for blob in cas.iter_global_blob_files(store.root):
        if blob.name not in refs:
            _issue(
                issues, "warn", "orphan-blob",
                "global blob is not referenced by any manifest; run snapz gc to reclaim it",
                path=blob,
            )

    if fix:
        cas.rebuild_refs_index(store.root)
        fixed += 1
        _issue(
            issues, "warn", "rebuilt-refs-index",
            "rebuilt global blob reference index",
            path=cas.refs_index_path(store.root), fixed=True,
        )
        if all_dirs:
            data = {"version": 1, "dirs": registry_dirs}
        else:
            data = store._load_registry()  # noqa: SLF001
            data.setdefault("version", 1)
            data.setdefault("dirs", {}).update(registry_dirs)
        store._save_registry(data)  # noqa: SLF001
        fixed += 1

    return CheckResult(
        dirs_scanned=sum(1 for d, _ in targets if d.exists()),
        issues=issues,
        fixed_count=fixed,
        deep=deep,
    )


def migrate(
    path: Optional[str | Path] = None,
    *,
    all_dirs: bool = False,
    to: str = "v3",
    dry_run: bool = False,
    config: Optional[RuntimeConfig] = None,
) -> MigrateOutcome:
    """Move legacy v2 per-dir blobs into the v3 global object pool."""

    if to != "v3":
        raise ValueError("only migration target supported is v3")
    config = config or default_config()
    store = Store(config)
    targets = _dir_roots_for_scope(store, path, all_dirs=all_dirs)

    migrated = 0
    skipped = 0
    bytes_migrated = 0
    for dir_root, _abspath in targets:
        if not dir_root.exists():
            continue
        for blob in list(cas.iter_blob_files(dir_root, include_global=False)):
            target = cas.global_blob_path(store.root, blob.name)
            try:
                size = blob.stat().st_size
            except OSError:
                continue
            if target.exists():
                skipped += 1
                if not dry_run:
                    try:
                        blob.unlink()
                    except OSError:
                        pass
                continue
            migrated += 1
            bytes_migrated += size
            if dry_run:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            try:
                import shutil as _shutil
                _shutil.copy2(blob, tmp)
                os.chmod(tmp, 0o600)
                os.replace(tmp, target)
                blob.unlink()
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                migrated -= 1
                bytes_migrated -= size
        if not dry_run:
            obj_root = cas.objects_root(dir_root)
            if obj_root.exists():
                for sub in list(obj_root.iterdir()):
                    if sub.is_dir():
                        try:
                            sub.rmdir()
                        except OSError:
                            pass

    return MigrateOutcome(
        dirs_scanned=sum(1 for d, _ in targets if d.exists()),
        blobs_migrated=migrated,
        bytes_migrated=bytes_migrated,
        blobs_skipped=skipped,
        dry_run=dry_run,
    )


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
            if not entry.sha256 or entry.sha256 in seen:
                continue
            seen.add(entry.sha256)
            total += max(0, int(entry.size))
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


# ---------------------------------------------------------------------------
# Prune — retention policy over snapshots
# ---------------------------------------------------------------------------


@dataclass
class PrunePlan:
    """A keep/drop split produced by :func:`plan_prune`."""

    abspath: Path
    keep: list[SnapshotMeta]
    drop: list[SnapshotMeta]
    rules: dict[str, object]


@dataclass
class PruneOutcome:
    """Result of :func:`execute_prune`."""

    deleted: list[str]
    blobs_removed: int
    bytes_freed: int
    dry_run: bool


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _bucket_keys(dt: datetime) -> tuple[str, str]:
    """Return ``(day_key, week_key)`` for grouping in retention rules."""

    iso_year, iso_week, _ = dt.isocalendar()
    return dt.strftime("%Y-%m-%d"), f"{iso_year}-W{iso_week:02d}"


def plan_prune(
    path: str | Path,
    *,
    keep_last: Optional[int] = None,
    keep_within_days: Optional[int] = None,
    keep_daily: Optional[int] = None,
    keep_weekly: Optional[int] = None,
    keep_tag: Iterable[str] = (),
    protect: Iterable[str] = (),
    config: Optional[RuntimeConfig] = None,
) -> PrunePlan:
    """Compute which snapshots to keep / drop under the given rules.

    Rules are *unioned*: a snapshot is kept if **any** rule matches.
    Snapshots whose name appears in *protect* are always kept, as are
    snapshots carrying any tag listed in *keep_tag*. Pass at least one
    rule (and/or *protect* / *keep_tag*) — calling with no rules raises
    ``ValueError`` so users can't accidentally wipe everything.
    """

    config = config or default_config()
    root = Path(config.root)
    if keep_last is None:
        keep_last = int(preferences.get_config_value(root, "retention.keep_last") or 0) or None
    if keep_within_days is None:
        keep_within_days = (
            int(preferences.get_config_value(root, "retention.keep_within_days") or 0)
            or None
        )
    if keep_daily is None:
        keep_daily = int(preferences.get_config_value(root, "retention.keep_daily") or 0) or None
    if keep_weekly is None:
        keep_weekly = int(preferences.get_config_value(root, "retention.keep_weekly") or 0) or None

    explicit_protect = list(protect)
    keep_tag_set = {str(t).strip() for t in keep_tag if str(t).strip()}
    if (
        keep_last is None
        and keep_within_days is None
        and keep_daily is None
        and keep_weekly is None
        and not explicit_protect
        and not keep_tag_set
    ):
        raise ValueError(
            "plan_prune requires at least one retention rule "
            "(keep_last / keep_within_days / keep_daily / keep_weekly / "
            "keep_tag / protect)"
        )

    abspath = _source_path(path, config=config)
    store = Store(config)
    snaps = store.list_snapshots(abspath)

    def _snapshot_sort_key(meta: SnapshotMeta) -> tuple[str, int]:
        try:
            mtime = store.meta_path(abspath, meta.name).stat().st_mtime_ns
        except OSError:
            mtime = 0
        return (meta.created, mtime)

    snaps_sorted = sorted(snaps, key=_snapshot_sort_key, reverse=True)

    keep_names: set[str] = {s.name for s in snaps if s.protected}
    keep_names.update(name for name in explicit_protect if name)
    if keep_tag_set:
        for snap in snaps:
            if keep_tag_set & set(snap.tags):
                keep_names.add(snap.name)

    if keep_last is not None and keep_last > 0:
        for snapz in snaps_sorted[:keep_last]:
            keep_names.add(snapz.name)

    if keep_within_days is not None and keep_within_days > 0:
        cutoff = datetime.now() - timedelta(days=keep_within_days)
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is not None and dt >= cutoff:
                keep_names.add(snapz.name)

    if keep_daily is not None and keep_daily > 0:
        seen_days: dict[str, str] = {}  # day_key -> snapshot name (latest)
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is None:
                continue
            day, _ = _bucket_keys(dt)
            if day not in seen_days:
                seen_days[day] = snapz.name
        for name in list(seen_days.values())[:keep_daily]:
            keep_names.add(name)

    if keep_weekly is not None and keep_weekly > 0:
        seen_weeks: dict[str, str] = {}
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is None:
                continue
            _, week = _bucket_keys(dt)
            if week not in seen_weeks:
                seen_weeks[week] = snapz.name
        for name in list(seen_weeks.values())[:keep_weekly]:
            keep_names.add(name)

    keep = [s for s in snaps_sorted if s.name in keep_names]
    drop = [s for s in snaps_sorted if s.name not in keep_names]
    rules: dict[str, object] = {
        "keep_last": keep_last,
        "keep_within_days": keep_within_days,
        "keep_daily": keep_daily,
        "keep_weekly": keep_weekly,
        "keep_tag": sorted(keep_tag_set),
        "protect": sorted(keep_names),
    }
    return PrunePlan(abspath=abspath, keep=keep, drop=drop, rules=rules)


def execute_prune(
    plan: PrunePlan,
    *,
    drop_names: Optional[Iterable[str]] = None,
    run_gc: bool = True,
    dry_run: bool = False,
    config: Optional[RuntimeConfig] = None,
) -> PruneOutcome:
    """Apply *plan*. Pass *drop_names* to override the plan's drop list
    (useful for the TUI which lets users toggle individual rows)."""

    config = config or default_config()
    store = Store(config)
    names = (
        list(drop_names)
        if drop_names is not None
        else [s.name for s in plan.drop]
    )
    protected_names = {
        s.name for s in store.list_snapshots(plan.abspath) if s.protected
    }
    names = [name for name in names if name not in protected_names]

    deleted: list[str] = []
    if not dry_run:
        for name in names:
            if _delete_snapshot_with_refs(store, plan.abspath, name):
                deleted.append(name)
    else:
        deleted = list(names)

    blobs_removed = 0
    bytes_freed = 0
    if run_gc and deleted:
        gc_res = gc(plan.abspath, dry_run=dry_run, config=config)
        blobs_removed = gc_res.blobs_removed
        bytes_freed = gc_res.bytes_freed
    if deleted and not dry_run:
        _record_event(
            store, plan.abspath, events.KIND_PRUNE,
            deleted=list(deleted),
            blobs_removed=blobs_removed,
            bytes_freed=bytes_freed,
        )
    return PruneOutcome(
        deleted=deleted,
        blobs_removed=blobs_removed,
        bytes_freed=bytes_freed,
        dry_run=dry_run,
    )


def _auto_prune_after_save(abspath: Path, config: RuntimeConfig) -> None:
    try:
        enabled = preferences.get_config_value(
            Path(config.root), "retention.auto_prune_after_save"
        )
    except KeyError:
        return
    if not enabled:
        return
    try:
        plan = plan_prune(abspath, config=config)
    except ValueError:
        return
    execute_prune(plan, config=config)


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
            target_root = abspath / req
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
        full = abspath / entry.path
        full.parent.mkdir(parents=True, exist_ok=True)
        if entry.type == "file":
            if not entry.sha256:
                skipped.append((entry.path, "missing sha"))
                continue
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
                if full.is_symlink() or full.exists():
                    full.unlink()
                os.symlink(entry.target, full)
                reverted += 1
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


# ---------------------------------------------------------------------------
# Find — locate paths across snapshots (v0.2)
# ---------------------------------------------------------------------------


@dataclass
class FindHit:
    """One ``(snapshot, path)`` pairing emitted by :func:`find`."""

    snapshot: SnapshotMeta
    path: str
    type: str                 # "file" | "symlink"
    size: Optional[int]
    sha256: Optional[str]
    target: Optional[str]     # symlinks only
    changed_from_prev: bool   # sha differs from the chronologically next-newer
                              # hit for this same path (False for the newest /
                              # for symlinks)


@dataclass
class FindResult:
    """Output of :func:`find` — grouped by source-relative path."""

    abspath: Path
    pattern: str
    by_path: dict[str, list[FindHit]]    # path -> hits, newest first

    @property
    def total_hits(self) -> int:
        return sum(len(v) for v in self.by_path.values())


def _match_path(pattern: str, path: str) -> bool:
    """Match *pattern* against a source-relative *path*.

    Supports:
    - exact match (``src/main.py``)
    - leading dir match (``src`` matches ``src/main.py``)
    - fnmatch wildcards (``*.py``, ``src/*.py``)
    - recursive wildcards (``**/*.py`` via :class:`pathlib.PurePosixPath`)
    """

    import fnmatch
    from pathlib import PurePosixPath

    if pattern == path:
        return True
    if "*" not in pattern and "?" not in pattern and "[" not in pattern:
        # Treat as a path/dir prefix.
        return path == pattern or path.startswith(pattern + "/")
    if "**" in pattern:
        try:
            return PurePosixPath(path).match(pattern)
        except (ValueError, TypeError):
            return False
    return fnmatch.fnmatchcase(path, pattern)


def find(
    path: str | Path,
    pattern: str,
    *,
    config: Optional[RuntimeConfig] = None,
    include_auto: bool = False,
) -> FindResult:
    """Locate every snapshot under *path* that contains *pattern*.

    *pattern* may be a literal path, a directory prefix, or an
    fnmatch / ``**`` glob. ``include_auto=True`` widens the scan to
    cover the auto-* safety snapshots (hidden from user-facing tools by
    default).

    Legacy ``.tar.zst`` snapshots are silently skipped — their content
    isn't indexed by manifest, so listing them would require unpacking
    every archive on every ``find`` call. CAS snapshots cover all
    new captures.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    snaps = store.list_snapshots(abspath)
    snaps_sorted = sorted(snaps, key=lambda s: s.created, reverse=True)

    by_path: dict[str, list[FindHit]] = {}
    for snap in snaps_sorted:
        if not include_auto and snap.name.startswith("auto-"):
            continue
        artifact = store.find_archive(abspath, snap.name)
        if artifact is None or not cas.is_manifest_artifact(artifact):
            continue
        try:
            manifest = cas.read_manifest(artifact)
        except (OSError, KeyError):
            continue
        for entry in manifest.entries:
            if entry.type not in ("file", "symlink"):
                continue
            if not _match_path(pattern, entry.path):
                continue
            by_path.setdefault(entry.path, []).append(FindHit(
                snapshot=snap,
                path=entry.path,
                type=entry.type,
                size=entry.size,
                sha256=entry.sha256,
                target=entry.target,
                changed_from_prev=False,    # filled in after grouping
            ))

    # Annotate "differs from chronologically next-newer hit". Since we
    # iterated snapshots newest-first, position 0 is newest; 1 differs
    # from 0 if its sha doesn't match.
    for hits in by_path.values():
        for i in range(1, len(hits)):
            prev = hits[i - 1]
            curr = hits[i]
            if curr.type == "file" and prev.type == "file":
                hits[i].changed_from_prev = (curr.sha256 != prev.sha256)
            elif curr.type == "symlink" and prev.type == "symlink":
                hits[i].changed_from_prev = (curr.target != prev.target)
            else:
                hits[i].changed_from_prev = True

    return FindResult(abspath=abspath, pattern=pattern, by_path=by_path)


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
