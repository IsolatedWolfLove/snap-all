"""Content-addressable storage for snapz snapshots.

Each per-directory folder under ``~/.snapz-all/`` looks like:

```
<key>-<basename>/
├── _meta.json
├── objects/                       # shared blob pool, keyed by sha256
│   └── ab/
│       └── abcdef1234...          # zstd-compressed file payload
├── snapshots/
│   ├── v1.manifest.json           # full file list + sha256 refs
│   └── auto-20260428.manifest.json
├── v1.meta.json                   # legacy meta files stay alongside
└── auto-20260428.meta.json
```

A snapshot's manifest enumerates every file/symlink in the source tree
and references its content by sha256. Blobs live in ``objects/`` and are
shared across snapshots **and** across distinct source directories that
happen to contain identical files. Deleting a snapshot just removes its
manifest + meta — orphan blobs are reclaimed by ``snapz gc``.

Older ``.tar.zst`` archives created before format v2 remain readable;
the routing happens in :mod:`snapz.api`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

try:  # pragma: no cover - import-only branch
    import zstandard as _zstandard  # type: ignore
except ImportError:  # pragma: no cover
    _zstandard = None


OBJECTS_DIR = "objects"
SNAPSHOTS_DIR = "snapshots"
MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_FORMAT_VERSION = 2

# zstd magic per RFC 8478 §3.1.1
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    path: str               # source-relative path
    type: str               # "file" | "symlink"
    mode: int = 0o644
    mtime: float = 0.0
    sha256: Optional[str] = None    # files only
    size: Optional[int] = None      # files only (uncompressed bytes)
    target: Optional[str] = None    # symlinks only

    def to_dict(self) -> dict:
        out: dict = {
            "path": self.path,
            "type": self.type,
            "mode": self.mode,
            "mtime": self.mtime,
        }
        if self.sha256 is not None:
            out["sha256"] = self.sha256
        if self.size is not None:
            out["size"] = self.size
        if self.target is not None:
            out["target"] = self.target
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestEntry":
        return cls(
            path=d["path"],
            type=d["type"],
            mode=int(d.get("mode", 0o644)),
            mtime=float(d.get("mtime", 0.0)),
            sha256=d.get("sha256"),
            size=d.get("size"),
            target=d.get("target"),
        )


@dataclass
class Manifest:
    snapshot: str
    created: str
    entries: list[ManifestEntry] = field(default_factory=list)
    format_version: int = MANIFEST_FORMAT_VERSION


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def objects_root(dir_root: Path) -> Path:
    return dir_root / OBJECTS_DIR


def snapshots_root(dir_root: Path) -> Path:
    return dir_root / SNAPSHOTS_DIR


def blob_path(dir_root: Path, sha256: str) -> Path:
    return objects_root(dir_root) / sha256[:2] / sha256


def manifest_path(dir_root: Path, name: str) -> Path:
    return snapshots_root(dir_root) / f"{name}{MANIFEST_SUFFIX}"


# ---------------------------------------------------------------------------
# Hashing + blob IO
# ---------------------------------------------------------------------------


def hash_file(path: Path, *, chunk_size: int = 64 * 1024) -> tuple[str, int]:
    """Stream-read *path*, return ``(sha256_hex, size_bytes)``."""

    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def write_blob(
    dir_root: Path,
    src: Path,
    *,
    use_zstd: bool = True,
) -> tuple[str, int, bool]:
    """Hash *src*, store as a blob under ``objects/`` if not already present.

    Returns ``(sha256, uncompressed_size, was_new)``.
    """

    sha, size = hash_file(src)
    target = blob_path(dir_root, sha)
    if target.exists():
        return sha, size, False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    if use_zstd and _zstandard is not None:
        cctx = _zstandard.ZstdCompressor(level=3)
        with open(src, "rb") as in_f, open(tmp, "wb") as out_f:
            cctx.copy_stream(in_f, out_f)
    else:
        with open(src, "rb") as in_f, gzip.open(tmp, "wb", compresslevel=6) as out_f:
            shutil.copyfileobj(in_f, out_f)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    return sha, size, True


def read_blob_bytes(dir_root: Path, sha256: str) -> bytes:
    """Decompress *sha256* into memory and return the raw payload.

    Convenience companion to :func:`read_blob_to` that avoids touching
    the filesystem — handy for previews (e.g. unified diffs in the TUI)
    where we don't want a temp file.
    """

    src = blob_path(dir_root, sha256)
    if not src.exists():
        raise FileNotFoundError(f"blob {sha256[:12]}… missing in {dir_root}")
    raw = src.read_bytes()
    if raw[:4] == _ZSTD_MAGIC:
        if _zstandard is None:
            raise RuntimeError(
                "zstandard not installed; cannot read zstd-compressed blob"
            )
        # ``ZstdDecompressor.decompress`` needs the original frame to
        # carry an explicit content-size header, which our writer does
        # not emit. Stream-decompress instead — same pattern as
        # :func:`read_blob_to` — so headerless frames round-trip too.
        import io as _io
        dctx = _zstandard.ZstdDecompressor()
        return dctx.stream_reader(_io.BytesIO(raw)).read()
    if raw[:2] == _GZIP_MAGIC:
        return gzip.decompress(raw)
    raise ValueError(f"unknown blob format for {sha256}")


def read_blob_to(dir_root: Path, sha256: str, dst: Path) -> int:
    """Decompress the blob into *dst*. Returns bytes written.

    Raises :class:`FileNotFoundError` if the blob is missing (suggests
    the store has been GC'd while a stale manifest still references
    it).
    """

    src = blob_path(dir_root, sha256)
    if not src.exists():
        raise FileNotFoundError(f"blob {sha256[:12]}… missing in {dir_root}")
    with open(src, "rb") as f:
        head = f.read(4)
        f.seek(0)
        if head[:4] == _ZSTD_MAGIC:
            if _zstandard is None:
                raise RuntimeError(
                    "zstandard not installed; cannot read zstd-compressed blob"
                )
            dctx = _zstandard.ZstdDecompressor()
            with open(dst, "wb") as out:
                dctx.copy_stream(f, out)
        elif head[:2] == _GZIP_MAGIC:
            with gzip.open(f, "rb") as gz, open(dst, "wb") as out:
                shutil.copyfileobj(gz, out)
        else:
            raise ValueError(f"unknown blob format for {sha256}")
    return dst.stat().st_size


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


def write_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format_version": manifest.format_version,
        "snapshot": manifest.snapshot,
        "created": manifest.created,
        "entries": [e.to_dict() for e in manifest.entries],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def read_manifest(path: Path) -> Manifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Manifest(
        format_version=int(data.get("format_version", MANIFEST_FORMAT_VERSION)),
        snapshot=data["snapshot"],
        created=data["created"],
        entries=[ManifestEntry.from_dict(e) for e in data.get("entries", [])],
    )


# ---------------------------------------------------------------------------
# GC helpers
# ---------------------------------------------------------------------------


def referenced_blobs(dir_root: Path) -> set[str]:
    """Sha256 set referenced by every manifest under *dir_root*."""

    refs: set[str] = set()
    snap_dir = snapshots_root(dir_root)
    if not snap_dir.exists():
        return refs
    for m_file in snap_dir.glob(f"*{MANIFEST_SUFFIX}"):
        try:
            m = read_manifest(m_file)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        for e in m.entries:
            if e.sha256:
                refs.add(e.sha256)
    return refs


def iter_blob_files(dir_root: Path) -> Iterator[Path]:
    obj = objects_root(dir_root)
    if not obj.exists():
        return
    for bucket in obj.iterdir():
        if not bucket.is_dir() or len(bucket.name) != 2:
            continue
        for blob in bucket.iterdir():
            if blob.is_file() and not blob.name.endswith(".tmp"):
                yield blob


def gc_dir(dir_root: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Remove blobs in *dir_root* not referenced by any manifest.

    Returns ``(blobs_removed, bytes_freed)``.
    """

    refs = referenced_blobs(dir_root)
    removed = 0
    freed = 0
    for blob in iter_blob_files(dir_root):
        if blob.name in refs:
            continue
        try:
            size = blob.stat().st_size
        except OSError:
            continue
        if not dry_run:
            try:
                blob.unlink()
            except OSError:
                continue
        removed += 1
        freed += size
    if not dry_run:
        obj_root = objects_root(dir_root)
        if obj_root.exists():
            for sub in list(obj_root.iterdir()):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
    return removed, freed


def is_manifest_artifact(path: Path) -> bool:
    return path.name.endswith(MANIFEST_SUFFIX)
