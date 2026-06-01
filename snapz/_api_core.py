"""Core snapshot API: save, restore, diff, tags, and source relocation."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable, Optional

from snapz import archive, cas, events, filecache, preferences
from snapz.archive import FileEntry, PackResult, ProgressCallback, WalkResult
from snapz.config import RuntimeConfig, default_config
from snapz.ignore import build_matcher
from snapz.store import (
    DirEntry,
    SnapshotMeta,
    Store,
    read_source_marker,
    source_identity,
    source_marker_path,
    write_source_marker,
)
from snapz.util import (
    auto_name,
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
    new_files: list[str]
    overwritten_files: list[str]
    extra_files: list[str]
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
    status: str
    size_a: Optional[int] = None
    size_b: Optional[int] = None
    sha_a: Optional[str] = None
    sha_b: Optional[str] = None
    type_a: Optional[str] = None
    type_b: Optional[str] = None


@dataclass
class DiffResult:
    a_meta: SnapshotMeta
    b_meta: Optional[SnapshotMeta]
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

    from snapz._api_prune import _auto_prune_after_save

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


def _safe_snapshot_target_path(target: Path, relpath: str) -> Path:
    rel = PurePosixPath(str(relpath).rstrip("/"))
    if (
        not rel.parts
        or rel.is_absolute()
        or any(part in {"", ".", ".."} for part in rel.parts)
    ):
        raise ValueError(f"unsafe snapshot path: {relpath!r}")
    full = Path(target, *rel.parts)
    base = Path(target).resolve()
    parent = full.parent.resolve(strict=False)
    try:
        parent.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"unsafe snapshot path: {relpath!r}") from exc
    return full


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
    try:
        target = _safe_snapshot_target_path(abspath, relpath)
    except ValueError:
        return None
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
