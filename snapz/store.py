"""On-disk layout for ``~/.snapz-all/``.

```
<root>/
├── registry.json                 path<->key reverse lookup
└── <key>/                        one folder per snapshotted dir
    ├── _meta.json                { abspath, first_seen, last_used, snapshot_count, ... }
    ├── <name>.tar.zst            archive
    └── <name>.meta.json          { name, source, created, size_bytes, file_count, ... }
```
"""

from __future__ import annotations

import json
import os
import shutil
import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from snapz import cas
from snapz.config import (
    ARCHIVE_SUFFIX_GZIP,
    ARCHIVE_SUFFIX_ZSTD,
    DIR_META_FILENAME,
    META_SUFFIX,
    REGISTRY_FILENAME,
    RuntimeConfig,
    SOURCE_MARKER_FILENAME,
)
from snapz.util import compute_key, now_iso

ARCHIVE_SUFFIXES = (ARCHIVE_SUFFIX_ZSTD, ARCHIVE_SUFFIX_GZIP)
SOURCE_MARKER_FORMAT_VERSION = 1


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _path_disk_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for filename in filenames:
            try:
                total += (Path(dirpath) / filename).lstat().st_size
            except OSError:
                continue
    return total


def _source_on_disk_bytes(folder: Path) -> int:
    total = _path_disk_bytes(folder)
    for sha in cas.referenced_blobs(folder):
        try:
            total += cas.find_blob(folder, sha).stat().st_size
        except OSError:
            continue
    return total


def source_identity(path: Path) -> str:
    """Return a stable-enough identity for a live source directory."""

    try:
        st = Path(path).stat()
    except OSError:
        return ""
    return f"{st.st_dev}:{st.st_ino}"


def source_marker_path(path: Path) -> Path:
    return Path(path) / SOURCE_MARKER_FILENAME


def read_source_marker(path: Path) -> str:
    """Return the opt-in persistent source marker for *path*, if present."""

    marker = source_marker_path(path)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    value = str(data.get("id", "") or "").strip()
    return value if value else ""


def write_source_marker(path: Path, *, force: bool = False) -> tuple[str, bool]:
    """Create or read ``.snapz-id`` under *path*.

    Returns ``(marker_id, created)``. Existing valid markers are reused
    unless *force* is True.
    """

    marker = source_marker_path(path)
    if marker.exists() and not force:
        existing = read_source_marker(path)
        if existing:
            return existing, False
        raise ValueError(f"invalid existing marker: {marker}")
    marker_id = uuid.uuid4().hex
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "format_version": SOURCE_MARKER_FORMAT_VERSION,
                "id": marker_id,
                "created": now_iso(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(marker, 0o600)
    except OSError:
        pass
    return marker_id, True


def identity_key(abspath: Path, source_id: str) -> str:
    """Disambiguate generations when the same absolute path is recreated."""

    suffix = hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:8]
    return f"{compute_key(abspath)}--{suffix}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SnapshotMeta:
    name: str
    source: str  # absolute path of the snapshotted directory
    created: str  # ISO timestamp
    size_bytes: int
    file_count: int
    total_bytes_in: int
    compression: str  # "zstd" | "gzip"
    archive: str  # filename inside the dir folder
    note: str = ""             # optional human-readable description
    protected: bool = False    # retention/delete guard
    tags: list[str] = field(default_factory=list)  # user labels (P3)

    @classmethod
    def from_dict(cls, data: dict) -> "SnapshotMeta":
        raw_tags = data.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        # Deduplicate while preserving insertion order.
        seen: set[str] = set()
        tags: list[str] = []
        for tag in raw_tags:
            tag = str(tag).strip()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
        return cls(
            name=data["name"],
            source=data["source"],
            created=data["created"],
            size_bytes=int(data.get("size_bytes", 0)),
            file_count=int(data.get("file_count", 0)),
            total_bytes_in=int(data.get("total_bytes_in", 0)),
            compression=data.get("compression", "gzip"),
            archive=data["archive"],
            note=str(data.get("note", "") or ""),
            protected=bool(data.get("protected", False)),
            tags=tags,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DirMeta:
    abspath: str
    first_seen: str
    last_used: str
    snapshot_count: int = 0
    snapshot_count_cached: int = 0
    on_disk_bytes_cached: int = 0
    source_id: str = ""
    source_marker: str = ""
    archived_at: str = ""

    @classmethod
    def fresh(cls, abspath: Path) -> "DirMeta":
        ts = now_iso()
        return cls(
            abspath=str(abspath),
            first_seen=ts,
            last_used=ts,
            snapshot_count=0,
            snapshot_count_cached=0,
            on_disk_bytes_cached=0,
            source_id=source_identity(abspath),
            source_marker=read_source_marker(abspath),
            archived_at="",
        )


@dataclass
class DirEntry:
    """A single row of ``snapz alist``."""

    key: str
    meta: DirMeta
    snapshots: list[SnapshotMeta] = field(default_factory=list)
    archived: bool = False
    archive_reason: str = ""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    """Filesystem-backed store rooted at :class:`RuntimeConfig.root`."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.root = Path(config.root)

    # ----- paths --------------------------------------------------------

    def key_for(self, abspath: Path) -> str:
        """Return the active store key for *abspath*.

        The first generation keeps the historical path-derived key. If
        that key already belongs to a different live directory identity
        (delete + recreate at the same path), new snapshots use an
        identity-suffixed key so old snapshots remain archived.
        """

        abspath = Path(abspath)
        base_key = compute_key(abspath)
        base_folder = self.root / base_key
        current_id = source_identity(abspath)
        if not base_folder.exists() or not current_id:
            return base_key
        meta = self._read_dir_meta_from_folder(base_folder, abspath)
        current_marker = read_source_marker(abspath)
        if meta.source_marker and current_marker == meta.source_marker:
            return base_key
        if not meta.source_id or meta.source_id == current_id:
            return base_key
        return identity_key(abspath, current_id)

    def dir_for(self, abspath: Path) -> Path:
        return self.root / self.key_for(abspath)

    def dir_by_key(self, key: str) -> Path:
        return self.root / key

    def registry_path(self) -> Path:
        return self.root / REGISTRY_FILENAME

    def archive_path(self, abspath: Path, name: str, suffix: str) -> Path:
        return self.dir_for(abspath) / f"{name}{suffix}"

    def meta_path(self, abspath: Path, name: str) -> Path:
        return self.dir_for(abspath) / f"{name}{META_SUFFIX}"

    # ----- registry -----------------------------------------------------

    def _load_registry(self) -> dict:
        path = self.registry_path()
        if not path.exists():
            return {"version": 1, "dirs": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "dirs": {}}

    def _save_registry(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.registry_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _touch_registry(
        self,
        abspath: Path,
        snapshot_count: int,
        *,
        snapshot_count_cached: Optional[int] = None,
        on_disk_bytes_cached: Optional[int] = None,
    ) -> None:
        data = self._load_registry()
        key = self.key_for(abspath)
        entry = data["dirs"].get(key, {})
        ts = now_iso()
        if not entry:
            entry["first_seen"] = ts
        entry["abspath"] = str(abspath)
        entry["last_used"] = ts
        entry["snapshot_count"] = snapshot_count
        entry["snapshot_count_cached"] = (
            snapshot_count
            if snapshot_count_cached is None
            else snapshot_count_cached
        )
        entry["on_disk_bytes_cached"] = (
            entry.get("on_disk_bytes_cached", 0)
            if on_disk_bytes_cached is None
            else on_disk_bytes_cached
        )
        entry["source_id"] = source_identity(abspath) or entry.get("source_id", "")
        entry["source_marker"] = read_source_marker(abspath) or entry.get(
            "source_marker", ""
        )
        entry["archived_at"] = entry.get("archived_at", "")
        data["dirs"][key] = entry
        self._save_registry(data)

    @staticmethod
    def _dir_meta_from_data(data: dict, abspath: Path) -> DirMeta:
        snapshot_count = _safe_int(data.get("snapshot_count", 0))
        snapshot_count_cached = _safe_int(
            data.get("snapshot_count_cached", snapshot_count)
        )
        return DirMeta(
            abspath=str(data.get("abspath", str(abspath))),
            first_seen=str(data.get("first_seen", now_iso())),
            last_used=str(data.get("last_used", now_iso())),
            snapshot_count=snapshot_count,
            snapshot_count_cached=snapshot_count_cached,
            on_disk_bytes_cached=_safe_int(data.get("on_disk_bytes_cached", 0)),
            source_id=str(data.get("source_id", "") or ""),
            source_marker=str(data.get("source_marker", "") or ""),
            archived_at=str(data.get("archived_at", "") or ""),
        )

    # ----- per-dir folder ----------------------------------------------

    def ensure_dir(self, abspath: Path) -> Path:
        target = self.dir_for(abspath)
        target.mkdir(parents=True, exist_ok=True)
        # v3 writes blobs to the root-level object pool; keep the
        # per-dir objects/ folder around for v2 compatibility and tools
        # that inspect the historical layout.
        cas.objects_root(target).mkdir(parents=True, exist_ok=True)
        cas.global_objects_root(self.root).mkdir(parents=True, exist_ok=True)
        meta_path = target / DIR_META_FILENAME
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps(asdict(DirMeta.fresh(abspath)), indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            meta = self._read_dir_meta_from_folder(target, abspath)
            current_id = source_identity(abspath)
            current_marker = read_source_marker(abspath)
            same_marker = (
                bool(meta.source_marker)
                and current_marker == meta.source_marker
            )
            if current_marker and not meta.source_marker:
                meta.source_marker = current_marker
            if current_id and (
                same_marker or not meta.source_id or meta.source_id == current_id
            ):
                meta.abspath = str(abspath)
                meta.source_id = current_id
                meta.source_marker = current_marker or meta.source_marker
                meta.archived_at = ""
                self._write_dir_meta_to_folder(target, meta)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(target, 0o700)
        except OSError:
            pass
        return target

    def _read_dir_meta_from_folder(self, folder: Path, abspath: Path) -> DirMeta:
        path = folder / DIR_META_FILENAME
        if not path.exists():
            return DirMeta.fresh(abspath)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DirMeta.fresh(abspath)
        return self._dir_meta_from_data(data, abspath)

    def _write_dir_meta_to_folder(self, folder: Path, meta: DirMeta) -> None:
        path = folder / DIR_META_FILENAME
        path.write_text(
            json.dumps(asdict(meta), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _read_dir_meta(self, abspath: Path) -> DirMeta:
        return self._read_dir_meta_from_folder(self.dir_for(abspath), abspath)

    def _write_dir_meta(self, abspath: Path, meta: DirMeta) -> None:
        self._write_dir_meta_to_folder(self.dir_for(abspath), meta)

    def _registry_entry_for_meta(self, meta: DirMeta) -> dict[str, object]:
        return {
            "abspath": meta.abspath,
            "first_seen": meta.first_seen,
            "last_used": meta.last_used,
            "snapshot_count": meta.snapshot_count,
            "snapshot_count_cached": meta.snapshot_count_cached,
            "on_disk_bytes_cached": meta.on_disk_bytes_cached,
            "source_id": meta.source_id,
            "source_marker": meta.source_marker,
            "archived_at": meta.archived_at,
        }

    def _refresh_cached_summary(self, folder: Path, meta: DirMeta) -> DirMeta:
        snapshot_count = len(self.list_snapshots_in_dir(folder))
        meta.snapshot_count = snapshot_count
        meta.snapshot_count_cached = snapshot_count
        meta.on_disk_bytes_cached = _source_on_disk_bytes(folder)
        return meta

    def _write_dir_meta_with_cached_summary(
        self, folder: Path, meta: DirMeta,
    ) -> DirMeta:
        self._refresh_cached_summary(folder, meta)
        for _ in range(3):
            self._write_dir_meta_to_folder(folder, meta)
            actual = _source_on_disk_bytes(folder)
            if actual == meta.on_disk_bytes_cached:
                break
            meta.on_disk_bytes_cached = actual
        return meta

    def refresh_cached_summary_in_dir(self, folder: Path) -> Optional[DirMeta]:
        meta_path = folder / DIR_META_FILENAME
        if not meta_path.exists():
            return None
        try:
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        meta = self._dir_meta_from_data(
            meta_data, Path(str(meta_data.get("abspath", ""))),
        )
        return self._write_dir_meta_with_cached_summary(folder, meta)

    def archive_status(self, folder: Path, meta: DirMeta) -> tuple[bool, str]:
        if meta.archived_at:
            return True, "archived"
        src = Path(meta.abspath)
        if not src.exists():
            return True, "missing-source"
        current_marker = read_source_marker(src)
        if meta.source_marker:
            if current_marker == meta.source_marker:
                return False, ""
            if current_marker:
                return True, "source-recreated"
        current_id = source_identity(src)
        if meta.source_id and current_id and meta.source_id != current_id:
            return True, "source-recreated"
        return False, ""

    def _is_active_folder(self, folder: Path, abspath: Path) -> bool:
        if not folder.exists():
            return False
        meta = self._read_dir_meta_from_folder(folder, abspath)
        archived, _ = self.archive_status(folder, meta)
        return not archived

    # ----- snapshot meta -----------------------------------------------

    def write_snapshot_meta_in_dir(self, folder: Path, meta: SnapshotMeta) -> None:
        path = folder / f"{meta.name}{META_SUFFIX}"
        path.write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def write_snapshot_meta(self, meta: SnapshotMeta) -> None:
        self.write_snapshot_meta_in_dir(Path(self.dir_for(Path(meta.source))), meta)

    def read_snapshot_meta(self, abspath: Path, name: str) -> Optional[SnapshotMeta]:
        folder = self.dir_for(abspath)
        if not self._is_active_folder(folder, abspath):
            return None
        path = folder / f"{name}{META_SUFFIX}"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return SnapshotMeta.from_dict(data)

    # ----- queries ------------------------------------------------------

    def list_snapshots(self, abspath: Path) -> list[SnapshotMeta]:
        folder = self.dir_for(abspath)
        if not folder.is_dir():
            return []
        if not self._is_active_folder(folder, abspath):
            return []
        return self.list_snapshots_in_dir(folder)

    def list_snapshots_in_dir(self, folder: Path) -> list[SnapshotMeta]:
        if not folder.is_dir():
            return []
        out: list[tuple[SnapshotMeta, int]] = []
        for child in folder.iterdir():
            if not child.name.endswith(META_SUFFIX):
                continue
            if child.name == DIR_META_FILENAME:
                continue
            try:
                data = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                out.append((SnapshotMeta.from_dict(data), child.stat().st_mtime_ns))
            except KeyError:
                continue
            except OSError:
                continue
        out.sort(
            key=lambda item: (item[0].created, item[1], item[0].name),
            reverse=True,
        )
        return [meta for meta, _mtime_ns in out]

    def _read_dir_entry(self, child: Path) -> Optional[DirEntry]:
        meta_path = child / DIR_META_FILENAME
        if not meta_path.exists():
            return None
        try:
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        abspath = Path(meta_data.get("abspath", ""))
        dir_meta = self._read_dir_meta_from_folder(child, abspath)
        snaps = self.list_snapshots_in_dir(child)
        archived, reason = self.archive_status(child, dir_meta)
        return DirEntry(
            key=child.name,
            meta=dir_meta,
            snapshots=snaps,
            archived=archived,
            archive_reason=reason,
        )

    def load_all_meta_bulk(
        self,
        *,
        include_archived: bool = False,
        include_snapshots: bool = False,
        reconcile_registry: bool = True,
    ) -> list[DirEntry]:
        """Load per-source metadata with one root-directory scan.

        By default this reads only each source folder's ``_meta.json`` and
        leaves ``DirEntry.snapshots`` empty. Set ``include_snapshots`` for
        callers that need the historical full ``list_all`` shape.
        """

        if not self.root.exists():
            return []
        entries: list[DirEntry] = []
        seen_keys: set[str] = set()
        registry_entries: dict[str, dict[str, object]] = {}
        for child in self.root.iterdir():
            if not child.is_dir() or child.name == cas.OBJECTS_DIR:
                continue
            meta_path = child / DIR_META_FILENAME
            if not meta_path.exists():
                continue
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            seen_keys.add(child.name)
            abspath = Path(str(meta_data.get("abspath", "")))
            dir_meta = self._dir_meta_from_data(meta_data, abspath)
            registry_entries[child.name] = self._registry_entry_for_meta(dir_meta)
            archived, reason = self.archive_status(child, dir_meta)
            if archived and not include_archived:
                continue
            entries.append(DirEntry(
                key=child.name,
                meta=dir_meta,
                snapshots=(
                    self.list_snapshots_in_dir(child)
                    if include_snapshots
                    else []
                ),
                archived=archived,
                archive_reason=reason,
            ))

        if reconcile_registry:
            registry = self._load_registry()
            registry_dirs = registry.get("dirs", {})
            if set(registry_dirs.keys()) != seen_keys:
                registry["dirs"] = registry_entries
                self._save_registry(registry)

        entries.sort(key=lambda e: e.meta.last_used, reverse=True)
        return entries

    def list_all(self, *, include_archived: bool = False) -> list[DirEntry]:
        return self.load_all_meta_bulk(
            include_archived=include_archived,
            include_snapshots=True,
        )

    def list_archived(self) -> list[DirEntry]:
        return [e for e in self.list_all(include_archived=True) if e.archived]

    def find_archive(self, abspath: Path, name: str) -> Optional[Path]:
        """Locate the artifact backing snapshot *name*.

        For format v2 (CAS) snapshots this is the manifest JSON under
        ``snapshots/``. For legacy snapshots it's the ``.tar.zst`` /
        ``.tar.gz`` archive at the per-dir folder root.
        """

        folder = self.dir_for(abspath)
        if not self._is_active_folder(folder, abspath):
            return None
        return self.find_archive_in_dir(folder, name)

    def find_archive_in_dir(self, folder: Path, name: str) -> Optional[Path]:
        manifest = cas.find_manifest_path(folder, name)
        if manifest.exists():
            return manifest
        for suffix in ARCHIVE_SUFFIXES:
            cand = folder / f"{name}{suffix}"
            if cand.exists():
                return cand
        return None

    def name_exists(self, abspath: Path, name: str) -> bool:
        return self.read_snapshot_meta(abspath, name) is not None or (
            self.find_archive(abspath, name) is not None
        )

    # ----- mutators -----------------------------------------------------

    def record_snapshot(self, meta: SnapshotMeta) -> None:
        abspath = Path(meta.source)
        folder = self.ensure_dir(abspath)
        self.write_snapshot_meta(meta)
        dir_meta = self._read_dir_meta(abspath)
        dir_meta.last_used = now_iso()
        dir_meta = self._write_dir_meta_with_cached_summary(folder, dir_meta)
        self._touch_registry(
            abspath,
            dir_meta.snapshot_count,
            snapshot_count_cached=dir_meta.snapshot_count_cached,
            on_disk_bytes_cached=dir_meta.on_disk_bytes_cached,
        )

    def delete_snapshot(self, abspath: Path, name: str, *, force: bool = False) -> bool:
        meta = self.read_snapshot_meta(abspath, name)
        if meta is not None and meta.protected and not force:
            raise PermissionError(f"snapshot '{name}' is protected")
        meta_path = self.meta_path(abspath, name)
        archive = self.find_archive(abspath, name)
        existed = False
        if archive is not None and archive.exists():
            archive.unlink()
            existed = True
        if meta_path.exists():
            meta_path.unlink()
            existed = True
        if existed:
            dir_meta = self._read_dir_meta(abspath)
            dir_meta.last_used = now_iso()
            dir_meta = self._write_dir_meta_with_cached_summary(
                self.dir_for(abspath), dir_meta,
            )
            self._touch_registry(
                abspath,
                dir_meta.snapshot_count,
                snapshot_count_cached=dir_meta.snapshot_count_cached,
                on_disk_bytes_cached=dir_meta.on_disk_bytes_cached,
            )
        return existed

    def rename_snapshot(self, abspath: Path, old: str, new: str) -> bool:
        old_meta = self.read_snapshot_meta(abspath, old)
        if old_meta is None:
            return False
        if self.name_exists(abspath, new):
            raise FileExistsError(f"snapshot '{new}' already exists")
        artifact = self.find_archive(abspath, old)
        if artifact is None:
            return False
        if cas.is_manifest_artifact(artifact):
            old_suffix = (
                cas.COMPRESSED_MANIFEST_SUFFIX
                if artifact.name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX)
                else cas.MANIFEST_SUFFIX
            )
            new_artifact = cas.snapshots_root(self.dir_for(abspath)) / f"{new}{old_suffix}"
            shutil.move(str(artifact), str(new_artifact))
            # Rewrite manifest snapshot field for forensic consistency.
            try:
                manifest = cas.read_manifest(new_artifact)
                manifest.snapshot = new
                cas.write_manifest(new_artifact, manifest)
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        else:
            suffix = "".join(artifact.suffixes[-2:])
            new_artifact = self.archive_path(abspath, new, suffix)
            shutil.move(str(artifact), str(new_artifact))
        new_meta = SnapshotMeta(
            name=new,
            source=old_meta.source,
            created=old_meta.created,
            size_bytes=old_meta.size_bytes,
            file_count=old_meta.file_count,
            total_bytes_in=old_meta.total_bytes_in,
            compression=old_meta.compression,
            archive=new_artifact.name,
            note=old_meta.note,
            protected=old_meta.protected,
            tags=list(old_meta.tags),
        )
        self.write_snapshot_meta(new_meta)
        self.meta_path(abspath, old).unlink(missing_ok=True)
        return True

    def protect_snapshot(self, abspath: Path, name: str, value: bool = True) -> SnapshotMeta:
        meta = self.read_snapshot_meta(abspath, name)
        if meta is None:
            raise FileNotFoundError(f"no snapshot named '{name}' under {abspath}")
        meta.protected = bool(value)
        self.write_snapshot_meta(meta)
        return meta

    def read_snapshot_meta_in_dir(self, folder: Path, name: str) -> Optional[SnapshotMeta]:
        path = folder / f"{name}{META_SUFFIX}"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return SnapshotMeta.from_dict(data)

    def find_dirs_for_source(self, abspath: Path) -> list[DirEntry]:
        target = str(Path(abspath))
        matches = [
            e for e in self.list_all(include_archived=True)
            if e.meta.abspath == target
        ]
        matches.sort(key=lambda e: e.meta.last_used, reverse=True)
        return matches

    def entry_by_key(self, key: str) -> Optional[DirEntry]:
        folder = self.dir_by_key(key)
        if not folder.is_dir():
            return None
        return self._read_dir_entry(folder)

    def _relocate_entry(self, entry: DirEntry, new: Path) -> DirEntry:
        new = Path(new).resolve()
        if not new.is_dir():
            raise NotADirectoryError(f"not a directory: {new}")
        old_folder = self.dir_by_key(entry.key)
        current_id = source_identity(new)
        current_marker = read_source_marker(new)
        new_key = self.key_for(new)
        new_folder = self.root / new_key
        if new_folder.exists() and new_folder != old_folder:
            existing = self._read_dir_entry(new_folder)
            if existing is not None and existing.snapshots:
                raise FileExistsError(
                    f"target already has snapshots: {new} ({new_key})"
                )
            raise FileExistsError(f"target store already exists: {new_folder}")

        if new_folder != old_folder:
            new_folder.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_folder), str(new_folder))

        meta = self._read_dir_meta_from_folder(new_folder, new)
        meta.abspath = str(new)
        meta.source_id = current_id
        meta.source_marker = current_marker or meta.source_marker
        meta.archived_at = ""
        meta.last_used = now_iso()
        meta = self._write_dir_meta_with_cached_summary(new_folder, meta)

        for snap in self.list_snapshots_in_dir(new_folder):
            snap.source = str(new)
            self.write_snapshot_meta_in_dir(new_folder, snap)

        registry = self._load_registry()
        registry.setdefault("dirs", {}).pop(entry.key, None)
        registry["dirs"][new_key] = self._registry_entry_for_meta(meta)
        self._save_registry(registry)

        refreshed = self._read_dir_entry(new_folder)
        if refreshed is None:
            raise FileNotFoundError(f"relocated store missing: {new_folder}")
        return refreshed

    def relocate_key(self, key: str, new: Path) -> DirEntry:
        """Move a specific store folder binding to live dir *new*."""

        entry = self.entry_by_key(key)
        if entry is None:
            raise FileNotFoundError(f"no snapz store with key {key!r}")
        return self._relocate_entry(entry, new)

    def relocate_source(self, old: Path, new: Path) -> DirEntry:
        """Move one source's store binding from *old* to live dir *new*."""

        old = Path(old).resolve()
        candidates = self.find_dirs_for_source(old)
        if not candidates:
            raise FileNotFoundError(f"no snapz store for {old}")
        return self._relocate_entry(candidates[0], new)

    # ----- convenience iterators ---------------------------------------

    def iter_snapshots(self, abspath: Path) -> Iterable[SnapshotMeta]:
        yield from self.list_snapshots(abspath)
