"""Store maintenance commands: garbage collection, check, and migration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from snapz import cas, filecache
from snapz.config import META_SUFFIX, RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store
from snapz._api_core import _source_path


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
