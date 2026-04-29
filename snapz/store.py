"""On-disk layout for ``~/.snapz-all/``.

```
<root>/
├── registry.json                 path<->key reverse lookup
└── <key>/                        one folder per snapshotted dir
    ├── _meta.json                { abspath, first_seen, last_used, snapshot_count }
    ├── <name>.tar.zst            archive
    └── <name>.meta.json          { name, source, created, size_bytes, file_count, ... }
```
"""

from __future__ import annotations

import json
import os
import shutil
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
)
from snapz.util import compute_key, now_iso

ARCHIVE_SUFFIXES = (ARCHIVE_SUFFIX_ZSTD, ARCHIVE_SUFFIX_GZIP)


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

    @classmethod
    def from_dict(cls, data: dict) -> "SnapshotMeta":
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
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DirMeta:
    abspath: str
    first_seen: str
    last_used: str
    snapshot_count: int = 0

    @classmethod
    def fresh(cls, abspath: Path) -> "DirMeta":
        ts = now_iso()
        return cls(abspath=str(abspath), first_seen=ts, last_used=ts, snapshot_count=0)


@dataclass
class DirEntry:
    """A single row of ``snapz alist``."""

    key: str
    meta: DirMeta
    snapshots: list[SnapshotMeta] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    """Filesystem-backed store rooted at :class:`RuntimeConfig.root`."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.root = Path(config.root)

    # ----- paths --------------------------------------------------------

    def dir_for(self, abspath: Path) -> Path:
        return self.root / compute_key(abspath)

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

    def _touch_registry(self, abspath: Path, snapshot_count: int) -> None:
        data = self._load_registry()
        key = compute_key(abspath)
        entry = data["dirs"].get(key, {})
        ts = now_iso()
        if not entry:
            entry["first_seen"] = ts
        entry["abspath"] = str(abspath)
        entry["last_used"] = ts
        entry["snapshot_count"] = snapshot_count
        data["dirs"][key] = entry
        self._save_registry(data)

    # ----- per-dir folder ----------------------------------------------

    def ensure_dir(self, abspath: Path) -> Path:
        target = self.dir_for(abspath)
        target.mkdir(parents=True, exist_ok=True)
        meta_path = target / DIR_META_FILENAME
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps(asdict(DirMeta.fresh(abspath)), indent=2) + "\n",
                encoding="utf-8",
            )
        try:
            os.chmod(self.root, 0o700)
            os.chmod(target, 0o700)
        except OSError:
            pass
        return target

    def _read_dir_meta(self, abspath: Path) -> DirMeta:
        path = self.dir_for(abspath) / DIR_META_FILENAME
        if not path.exists():
            return DirMeta.fresh(abspath)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DirMeta.fresh(abspath)
        return DirMeta(
            abspath=data.get("abspath", str(abspath)),
            first_seen=data.get("first_seen", now_iso()),
            last_used=data.get("last_used", now_iso()),
            snapshot_count=int(data.get("snapshot_count", 0)),
        )

    def _write_dir_meta(self, abspath: Path, meta: DirMeta) -> None:
        path = self.dir_for(abspath) / DIR_META_FILENAME
        path.write_text(
            json.dumps(asdict(meta), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ----- snapshot meta -----------------------------------------------

    def write_snapshot_meta(self, meta: SnapshotMeta) -> None:
        path = Path(self.dir_for(Path(meta.source))) / f"{meta.name}{META_SUFFIX}"
        path.write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def read_snapshot_meta(self, abspath: Path, name: str) -> Optional[SnapshotMeta]:
        path = self.meta_path(abspath, name)
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
        out: list[SnapshotMeta] = []
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
                out.append(SnapshotMeta.from_dict(data))
            except KeyError:
                continue
        out.sort(key=lambda m: m.created, reverse=True)
        return out

    def list_all(self) -> list[DirEntry]:
        if not self.root.exists():
            return []
        registry = self._load_registry()
        entries: list[DirEntry] = []
        # Walk directories on disk so we discover folders that might be
        # missing from the registry (e.g. after a manual move).
        seen_keys: set[str] = set()
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            key = child.name
            seen_keys.add(key)
            meta_path = child / DIR_META_FILENAME
            if not meta_path.exists():
                continue
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            abspath = Path(meta_data.get("abspath", ""))
            dir_meta = DirMeta(
                abspath=meta_data.get("abspath", str(child)),
                first_seen=meta_data.get("first_seen", now_iso()),
                last_used=meta_data.get("last_used", now_iso()),
                snapshot_count=int(meta_data.get("snapshot_count", 0)),
            )
            snaps = self.list_snapshots(abspath) if abspath else []
            entries.append(DirEntry(key=key, meta=dir_meta, snapshots=snaps))
        # Reconcile registry with what we found
        registry_dirs = registry.get("dirs", {})
        if set(registry_dirs.keys()) != seen_keys:
            registry["dirs"] = {
                key: {
                    "abspath": entry.meta.abspath,
                    "first_seen": entry.meta.first_seen,
                    "last_used": entry.meta.last_used,
                    "snapshot_count": entry.meta.snapshot_count,
                }
                for key, entry in (
                    (e.key, e) for e in entries
                )
            }
            self._save_registry(registry)
        entries.sort(key=lambda e: e.meta.last_used, reverse=True)
        return entries

    def find_archive(self, abspath: Path, name: str) -> Optional[Path]:
        """Locate the artifact backing snapshot *name*.

        For format v2 (CAS) snapshots this is the manifest JSON under
        ``snapshots/``. For legacy snapshots it's the ``.tar.zst`` /
        ``.tar.gz`` archive at the per-dir folder root.
        """

        folder = self.dir_for(abspath)
        manifest = cas.manifest_path(folder, name)
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
        self.ensure_dir(abspath)
        self.write_snapshot_meta(meta)
        dir_meta = self._read_dir_meta(abspath)
        dir_meta.last_used = now_iso()
        # Re-count from disk to stay self-healing.
        dir_meta.snapshot_count = len(self.list_snapshots(abspath))
        self._write_dir_meta(abspath, dir_meta)
        self._touch_registry(abspath, dir_meta.snapshot_count)

    def delete_snapshot(self, abspath: Path, name: str) -> bool:
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
            dir_meta.snapshot_count = len(self.list_snapshots(abspath))
            dir_meta.last_used = now_iso()
            self._write_dir_meta(abspath, dir_meta)
            self._touch_registry(abspath, dir_meta.snapshot_count)
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
            new_artifact = cas.manifest_path(self.dir_for(abspath), new)
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
        )
        self.write_snapshot_meta(new_meta)
        self.meta_path(abspath, old).unlink(missing_ok=True)
        return True

    # ----- convenience iterators ---------------------------------------

    def iter_snapshots(self, abspath: Path) -> Iterable[SnapshotMeta]:
        yield from self.list_snapshots(abspath)
