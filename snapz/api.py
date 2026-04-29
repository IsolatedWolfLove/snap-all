"""Programmatic entry points.

These are the functions external integrations (e.g. ``topics-bot``'s
``SnapshotBot``) should import. They never prompt the user — interactive
behaviour lives in :mod:`snapz.cli`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from snapz import archive, cas, preferences
from snapz.archive import ArchiveMember, PackResult, ProgressCallback, WalkResult
from snapz.config import RuntimeConfig, default_config
from snapz.ignore import build_matcher
from snapz.store import ARCHIVE_SUFFIXES, DirEntry, SnapshotMeta, Store
from snapz.util import (
    auto_name,
    compute_key,
    is_undo_snapshot,
    now_iso,
    resolve_path,
    validate_snapshot_name,
)


@dataclass
class SaveOutcome:
    snapshot: SnapshotMeta
    pack_result: PackResult
    walk_result: WalkResult


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


def estimate(
    path: str | Path,
    *,
    config: Optional[RuntimeConfig] = None,
    include_large: bool = False,
) -> WalkResult:
    """Run a dry-run walk over *path* and return the projected workload."""

    config = config or default_config()
    abspath = resolve_path(path)
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
) -> SaveOutcome:
    """Create a new snapshot of *path*.

    Pass *walk_result* to re-use a previous :func:`estimate` result and
    avoid scanning the tree twice.
    """

    config = config or default_config()
    abspath = resolve_path(path)
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
        store.delete_snapshot(abspath, snapshot_name)

    dir_root = store.dir_for(abspath)
    use_zstd = config.use_zstd and archive.zstd_available()

    # Walk the planned files, hash + dedup each one into the blob store,
    # build the manifest in memory.
    entries: list[cas.ManifestEntry] = []
    new_blob_count = 0
    new_blob_bytes = 0
    total_bytes_in = 0

    for index, fe in enumerate(walk_result.files, start=1):
        try:
            stat_info = fe.abspath.lstat()
        except OSError:
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
            try:
                sha, blob_size, was_new = cas.write_blob(
                    dir_root, fe.abspath, use_zstd=use_zstd
                )
            except OSError:
                if on_progress is not None:
                    on_progress(index, walk_result.file_count, fe)
                continue
            entries.append(cas.ManifestEntry(
                path=fe.relpath,
                type="file",
                mode=mode,
                mtime=mtime,
                sha256=sha,
                size=blob_size,
            ))
            total_bytes_in += blob_size
            if was_new:
                new_blob_count += 1
                try:
                    new_blob_bytes += cas.blob_path(dir_root, sha).stat().st_size
                except OSError:
                    pass

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
    abspath = resolve_path(path)
    return Store(config).list_snapshots(abspath)


def list_all(
    *,
    config: Optional[RuntimeConfig] = None,
) -> list[DirEntry]:
    config = config or default_config()
    return Store(config).list_all()


def delete(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> bool:
    config = config or default_config()
    abspath = resolve_path(path)
    return Store(config).delete_snapshot(abspath, name)


def rename(
    path: str | Path,
    old: str,
    new: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> bool:
    validate_snapshot_name(new)
    config = config or default_config()
    abspath = resolve_path(path)
    return Store(config).rename_snapshot(abspath, old, new)


def show(
    path: str | Path,
    name: str,
    *,
    config: Optional[RuntimeConfig] = None,
) -> Optional[SnapshotMeta]:
    config = config or default_config()
    abspath = resolve_path(path)
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
    abspath = resolve_path(path)
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

    return RestoreOutcome(
        snapshot=meta,
        pre_restore=pre_meta,
        extracted_count=extracted,
        cleaned_count=cleaned,
    )


def _extract_cas(manifest_path: Path, target: Path, *, dir_root: Path) -> int:
    """Extract a CAS manifest's content over *target*. Returns entry count.

    Files come first (so missing parent dirs get created), then
    symlinks. Mode and mtime are reapplied per the manifest. Mismatched
    blob sizes are tolerated silently — the user can rerun ``snapz gc``
    + ``snapz restore`` if the store has been corrupted.
    """

    manifest = cas.read_manifest(manifest_path)
    extracted = 0

    for entry in manifest.entries:
        if entry.type != "file":
            continue
        full = target / entry.path
        full.parent.mkdir(parents=True, exist_ok=True)
        if full.is_symlink() or full.exists():
            try:
                full.unlink()
            except OSError:
                pass
        if not entry.sha256:
            continue
        try:
            cas.read_blob_to(dir_root, entry.sha256, full)
        except FileNotFoundError:
            continue
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
    abspath = resolve_path(path)
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


# ---------------------------------------------------------------------------
# Garbage collection (M5+)
# ---------------------------------------------------------------------------


def gc(
    path: Optional[str | Path] = None,
    *,
    all_dirs: bool = False,
    dry_run: bool = False,
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
        abspath = resolve_path(path)
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

    return GcResult(
        blobs_removed=total_blobs,
        bytes_freed=total_bytes,
        dirs_scanned=scanned,
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
    legacy_count: int           # legacy *.tar.* archives kept around
    legacy_bytes: int
    oldest: Optional[str]       # earliest snapshot ISO timestamp, if any
    newest: Optional[str]       # latest snapshot ISO timestamp, if any
    largest: Optional[SnapshotMeta]
    snapshots: list[SnapshotMeta] = field(default_factory=list)

    @property
    def dedup_ratio(self) -> float:
        if self.blob_bytes <= 0:
            return 1.0
        return self.logical_bytes / self.blob_bytes


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
    for blob in cas.iter_blob_files(folder):
        try:
            blob_bytes += blob.stat().st_size
        except OSError:
            continue
        blob_count += 1
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


def _stats_for_dir(store: Store, abspath: Path, key: str) -> StatsEntry:
    snaps = store.list_snapshots(abspath)
    folder = store.dir_for(abspath)
    disk, blob_count, blob_bytes, legacy_count, legacy_bytes = _scan_dir_folder(folder)
    logical = sum(s.total_bytes_in for s in snaps)
    marginal = sum(s.size_bytes for s in snaps)
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
        legacy_count=legacy_count,
        legacy_bytes=legacy_bytes,
        oldest=iso_values[0] if iso_values else None,
        newest=iso_values[-1] if iso_values else None,
        largest=largest,
        snapshots=snaps,
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
        abspath = resolve_path(path)
        out.append(_stats_for_dir(store, abspath, compute_key(abspath)))
    else:
        for entry in store.list_all():
            abspath = Path(entry.meta.abspath)
            out.append(_stats_for_dir(store, abspath, entry.key))

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
    protect: Iterable[str] = (),
    config: Optional[RuntimeConfig] = None,
) -> PrunePlan:
    """Compute which snapshots to keep / drop under the given rules.

    Rules are *unioned*: a snapshot is kept if **any** rule matches.
    Snapshots whose name appears in *protect* are always kept. Pass at
    least one rule (and/or *protect*) — calling with no rules raises
    ``ValueError`` so users can't accidentally wipe everything.
    """

    if (
        keep_last is None
        and keep_within_days is None
        and keep_daily is None
        and keep_weekly is None
        and not list(protect)
    ):
        raise ValueError(
            "plan_prune requires at least one retention rule "
            "(keep_last / keep_within_days / keep_daily / keep_weekly / protect)"
        )

    config = config or default_config()
    abspath = resolve_path(path)
    snaps = Store(config).list_snapshots(abspath)
    snaps_sorted = sorted(snaps, key=lambda s: s.created, reverse=True)

    keep_names: set[str] = set(name for name in protect if name)

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
        "protect": sorted(name for name in protect if name),
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

    deleted: list[str] = []
    if not dry_run:
        for name in names:
            if store.delete_snapshot(plan.abspath, name):
                deleted.append(name)
    else:
        deleted = list(names)

    blobs_removed = 0
    bytes_freed = 0
    if run_gc and deleted:
        gc_res = gc(plan.abspath, dry_run=dry_run, config=config)
        blobs_removed = gc_res.blobs_removed
        bytes_freed = gc_res.bytes_freed
    return PruneOutcome(
        deleted=deleted,
        blobs_removed=blobs_removed,
        bytes_freed=bytes_freed,
        dry_run=dry_run,
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
        try:
            if full.is_symlink() or full.exists():
                full.unlink()
        except OSError:
            pass
        if entry.type == "file":
            if not entry.sha256:
                skipped.append((entry.path, "missing sha"))
                continue
            try:
                cas.read_blob_to(dir_root, entry.sha256, full)
            except FileNotFoundError as exc:
                skipped.append((entry.path, str(exc)))
                continue
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
                os.symlink(entry.target, full)
                reverted += 1
            except OSError as exc:
                skipped.append((entry.path, str(exc)))

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
    abspath = resolve_path(path)
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
    abspath = resolve_path(path)
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
    abspath = resolve_path(path)
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
    Store(config).delete_snapshot(abspath, target.name)

    remaining = sum(
        1 for s in Store(config).list_snapshots(abspath)
        if is_undo_snapshot(s.name)
    )
    return UndoOutcome(
        snapshot=target,
        extracted_count=outcome.extracted_count,
        cleaned_count=outcome.cleaned_count,
        remaining=remaining,
    )
