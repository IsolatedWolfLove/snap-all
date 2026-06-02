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
import tempfile
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
COMPRESSED_MANIFEST_SUFFIX = MANIFEST_SUFFIX + ".zst"
MANIFEST_FORMAT_VERSION = 3
MANIFEST_COMPRESS_THRESHOLD = 512 * 1024
REFS_INDEX_FILENAME = "_refs.index"

# zstd magic per RFC 8478 §3.1.1
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ChunkRef:
    sha256: str
    size: int

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkRef":
        return cls(
            sha256=str(d["sha256"]),
            size=int(d.get("size", 0)),
        )


@dataclass
class ManifestEntry:
    path: str               # source-relative path
    type: str               # "file" | "symlink"
    mode: int = 0o644
    mtime: float = 0.0
    sha256: Optional[str] = None    # files only
    size: Optional[int] = None      # files only (uncompressed bytes)
    target: Optional[str] = None    # symlinks only
    chunks: list[ChunkRef] = field(default_factory=list)

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
        if self.chunks:
            out["chunks"] = [chunk.to_dict() for chunk in self.chunks]
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestEntry":
        raw_chunks = d.get("chunks") or []
        return cls(
            path=d["path"],
            type=d["type"],
            mode=int(d.get("mode", 0o644)),
            mtime=float(d.get("mtime", 0.0)),
            sha256=d.get("sha256"),
            size=d.get("size"),
            target=d.get("target"),
            chunks=[
                ChunkRef.from_dict(chunk)
                for chunk in raw_chunks
                if isinstance(chunk, dict)
            ],
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


def global_objects_root(storage_root: Path) -> Path:
    """Return the v3 root-level blob pool."""

    return storage_root / OBJECTS_DIR


def snapshots_root(dir_root: Path) -> Path:
    return dir_root / SNAPSHOTS_DIR


def legacy_blob_path(dir_root: Path, sha256: str) -> Path:
    return objects_root(dir_root) / sha256[:2] / sha256


def blob_path(dir_root: Path, sha256: str) -> Path:
    """Return the visible blob path for *sha256*.

    For v3 stores this is the root-level global pool. For legacy v2
    stores it falls back to the per-dir object path.
    """

    global_path = global_blob_path(dir_root.parent, sha256)
    if global_path.exists():
        return global_path
    return legacy_blob_path(dir_root, sha256)


def global_blob_path(storage_root: Path, sha256: str) -> Path:
    return global_objects_root(storage_root) / sha256[:2] / sha256


def candidate_blob_paths(dir_root: Path, sha256: str) -> tuple[Path, ...]:
    """Return v3 then v2 blob locations for *sha256*.

    New snapshots write to the root-level v3 pool. Older v2 snapshots
    keep per-source blobs under ``<dir_root>/objects``; readers check
    both so old stores remain usable.
    """

    storage_root = dir_root.parent
    return (global_blob_path(storage_root, sha256), legacy_blob_path(dir_root, sha256))


def manifest_path(dir_root: Path, name: str) -> Path:
    return snapshots_root(dir_root) / f"{name}{MANIFEST_SUFFIX}"


def compressed_manifest_path(dir_root: Path, name: str) -> Path:
    return snapshots_root(dir_root) / f"{name}{COMPRESSED_MANIFEST_SUFFIX}"


def find_manifest_path(dir_root: Path, name: str) -> Path:
    plain = manifest_path(dir_root, name)
    if plain.exists():
        return plain
    compressed = compressed_manifest_path(dir_root, name)
    if compressed.exists():
        return compressed
    return plain


def manifest_name(path: Path) -> str:
    name = path.name
    if name.endswith(COMPRESSED_MANIFEST_SUFFIX):
        return name[: -len(COMPRESSED_MANIFEST_SUFFIX)]
    if name.endswith(MANIFEST_SUFFIX):
        return name[: -len(MANIFEST_SUFFIX)]
    return path.stem


def iter_manifest_paths(dir_root: Path) -> Iterator[Path]:
    snap_dir = snapshots_root(dir_root)
    if not snap_dir.exists():
        return
    seen: set[str] = set()
    for pattern in (f"*{MANIFEST_SUFFIX}", f"*{COMPRESSED_MANIFEST_SUFFIX}"):
        for path in snap_dir.glob(pattern):
            if path.name in seen:
                continue
            seen.add(path.name)
            yield path


def refs_index_path(storage_root: Path) -> Path:
    return Path(storage_root) / REFS_INDEX_FILENAME


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


def _compress_bytes(
    raw: bytes,
    *,
    use_zstd: bool,
    zstd_level: int,
    gzip_level: int,
) -> bytes:
    if use_zstd and _zstandard is not None:
        return _zstandard.ZstdCompressor(level=zstd_level).compress(raw)
    return gzip.compress(raw, compresslevel=gzip_level)


def write_blob_bytes(
    dir_root: Path,
    data: bytes,
    *,
    use_zstd: bool = True,
    global_store: bool = False,
    zstd_level: int = 10,
    gzip_level: int = 9,
) -> tuple[str, int, bool]:
    """Store raw *data* as one compressed blob.

    Returns ``(sha256, uncompressed_size, was_new)``.
    """

    sha = hashlib.sha256(data).hexdigest()
    size = len(data)
    target = (
        global_blob_path(dir_root.parent, sha)
        if global_store
        else legacy_blob_path(dir_root, sha)
    )
    if target.exists():
        return sha, size, False

    target_root = global_objects_root(dir_root.parent) if global_store else objects_root(dir_root)
    target_root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".blob-",
        suffix=".tmp",
        dir=str(target_root),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw_out:
            raw_out.write(
                _compress_bytes(
                    data,
                    use_zstd=use_zstd,
                    zstd_level=zstd_level,
                    gzip_level=gzip_level,
                )
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        try:
            os.link(tmp, target)
        except FileExistsError:
            return sha, size, False
        return sha, size, True
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_blob(
    dir_root: Path,
    src: Path,
    *,
    use_zstd: bool = True,
    global_store: bool = False,
    precomputed_sha: Optional[str] = None,
    zstd_level: int = 10,
    gzip_level: int = 9,
) -> tuple[str, int, bool]:
    """Hash *src*, store as a blob under ``objects/`` if not already present.

    Returns ``(sha256, uncompressed_size, was_new)``.
    """

    if precomputed_sha is not None:
        target = (
            global_blob_path(dir_root.parent, precomputed_sha)
            if global_store
            else legacy_blob_path(dir_root, precomputed_sha)
        )
        if target.exists():
            try:
                return precomputed_sha, src.stat().st_size, False
            except OSError:
                return precomputed_sha, 0, False

    h = hashlib.sha256()
    size = 0
    target_root = global_objects_root(dir_root.parent) if global_store else objects_root(dir_root)
    target_root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".blob-",
        suffix=".tmp",
        dir=str(target_root),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw_out:
            if use_zstd and _zstandard is not None:
                cctx = _zstandard.ZstdCompressor(level=zstd_level)
                writer = cctx.stream_writer(raw_out, closefd=False)
            else:
                writer = gzip.GzipFile(
                    fileobj=raw_out,
                    mode="wb",
                    compresslevel=gzip_level,
                )
            with writer:
                with open(src, "rb") as in_f:
                    while True:
                        chunk = in_f.read(64 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
                        size += len(chunk)
                        writer.write(chunk)
        actual_sha = h.hexdigest()
        if precomputed_sha is not None and precomputed_sha != actual_sha:
            raise ValueError(f"precomputed sha mismatch for {src}")
        sha = actual_sha
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    sha = h.hexdigest()
    target = (
        global_blob_path(dir_root.parent, sha)
        if global_store
        else legacy_blob_path(dir_root, sha)
    )
    if target.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
        return sha, size, False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    try:
        os.link(tmp, target)
    except FileExistsError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return sha, size, False
    else:
        try:
            tmp.unlink()
        except OSError:
            pass
    return sha, size, True


def find_blob(dir_root: Path, sha256: str) -> Path:
    """Locate *sha256* in the v3 global pool or the legacy v2 pool."""

    for src in candidate_blob_paths(dir_root, sha256):
        if src.exists():
            return src
    raise FileNotFoundError(f"blob {sha256[:12]}... missing in {dir_root}")


def _decode_blob_bytes(raw: bytes, sha256: str) -> bytes:
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


def read_blob_bytes(dir_root: Path, sha256: str) -> bytes:
    """Decompress *sha256* into memory and return the raw payload.

    Convenience companion to :func:`read_blob_to` that avoids touching
    the filesystem — handy for previews (e.g. unified diffs in the TUI)
    where we don't want a temp file.
    """

    raw = find_blob(dir_root, sha256).read_bytes()
    data = _decode_blob_bytes(raw, sha256)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise ValueError(f"blob {sha256[:12]} checksum mismatch")
    return data


def read_blob_to(dir_root: Path, sha256: str, dst: Path) -> int:
    """Decompress the blob into *dst*. Returns bytes written.

    Raises :class:`FileNotFoundError` if the blob is missing (suggests
    the store has been GC'd while a stale manifest still references
    it).
    """

    src = find_blob(dir_root, sha256)
    h = hashlib.sha256()
    size = 0
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        with open(src, "rb") as f:
            head = f.read(4)
            f.seek(0)
            with open(tmp, "wb") as out:
                if head[:4] == _ZSTD_MAGIC:
                    if _zstandard is None:
                        raise RuntimeError(
                            "zstandard not installed; cannot read zstd-compressed blob"
                        )
                    dctx = _zstandard.ZstdDecompressor()
                    reader = dctx.stream_reader(f)
                elif head[:2] == _GZIP_MAGIC:
                    reader = gzip.GzipFile(fileobj=f, mode="rb")
                else:
                    raise ValueError(f"unknown blob format for {sha256}")
                with reader:
                    while True:
                        chunk = reader.read(64 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
        actual = h.hexdigest()
        if actual != sha256:
            raise ValueError(f"blob {sha256[:12]} checksum mismatch")
        os.replace(tmp, dst)
        return size
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_blobs_to(
    dir_root: Path,
    chunks: Iterable[ChunkRef],
    dst: Path,
    *,
    expected_sha256: str | None = None,
) -> int:
    """Decompress *chunks* into *dst* in order. Returns bytes written."""

    tmp = dst.with_name(dst.name + ".tmp")
    size = 0
    h = hashlib.sha256() if expected_sha256 else None
    try:
        with open(tmp, "wb") as out:
            for chunk in chunks:
                data = read_blob_bytes(dir_root, chunk.sha256)
                if chunk.size is not None and len(data) != chunk.size:
                    raise ValueError(f"blob {chunk.sha256[:12]} size mismatch")
                size += len(data)
                if h is not None:
                    h.update(data)
                out.write(data)
        if h is not None and h.hexdigest() != expected_sha256:
            raise ValueError("chunked file checksum mismatch")
        os.replace(tmp, dst)
        return size
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


def write_manifest(
    path: Path,
    manifest: Manifest,
    *,
    zstd_level: int = 10,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format_version": manifest.format_version,
        "snapshot": manifest.snapshot,
        "created": manifest.created,
        "entries": [e.to_dict() for e in manifest.entries],
    }
    raw = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    target = path
    if len(raw) > MANIFEST_COMPRESS_THRESHOLD and _zstandard is not None:
        if target.name.endswith(MANIFEST_SUFFIX):
            target = Path(str(target) + ".zst")
        raw = _zstandard.ZstdCompressor(level=zstd_level).compress(raw)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(raw)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    if target != path:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def read_manifest(path: Path) -> Manifest:
    raw = path.read_bytes()
    if path.name.endswith(".zst") or raw[:4] == _ZSTD_MAGIC:
        if _zstandard is None:
            raise RuntimeError(
                "zstandard not installed; cannot read compressed manifest"
            )
        raw = _zstandard.ZstdDecompressor().decompress(raw)
    data = json.loads(raw.decode("utf-8"))
    return Manifest(
        format_version=int(data.get("format_version", MANIFEST_FORMAT_VERSION)),
        snapshot=data["snapshot"],
        created=data["created"],
        entries=[ManifestEntry.from_dict(e) for e in data.get("entries", [])],
    )


def entry_blob_refs(entry: ManifestEntry) -> list[str]:
    if entry.chunks:
        return [chunk.sha256 for chunk in entry.chunks if chunk.sha256]
    return [entry.sha256] if entry.sha256 else []


def manifest_blob_refs(manifest: Manifest) -> list[str]:
    refs: list[str] = []
    for entry in manifest.entries:
        refs.extend(entry_blob_refs(entry))
    return refs


# ---------------------------------------------------------------------------
# GC helpers
# ---------------------------------------------------------------------------


def referenced_blobs(dir_root: Path) -> set[str]:
    """Sha256 set referenced by every manifest under *dir_root*."""

    refs: set[str] = set()
    for m_file in iter_manifest_paths(dir_root):
        try:
            m = read_manifest(m_file)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        refs.update(manifest_blob_refs(m))
    return refs


def _iter_blob_files_under(obj: Path) -> Iterator[Path]:
    if not obj.exists():
        return
    for bucket in obj.iterdir():
        if not bucket.is_dir() or len(bucket.name) != 2:
            continue
        for blob in bucket.iterdir():
            if blob.is_file() and not blob.name.endswith(".tmp"):
                yield blob


def iter_blob_files(dir_root: Path, *, include_global: bool = True) -> Iterator[Path]:
    """Yield blobs visible to *dir_root*.

    The default includes the v3 global pool for compatibility with
    older callers that treated ``iter_blob_files(dir_root)`` as "all
    blobs this source can restore from". Pass ``include_global=False``
    to inspect only legacy v2 per-directory objects.
    """

    seen: set[Path] = set()
    roots = [objects_root(dir_root)]
    if include_global:
        roots.append(global_objects_root(dir_root.parent))
    for root in roots:
        for blob in _iter_blob_files_under(root):
            if blob in seen:
                continue
            seen.add(blob)
            yield blob


def iter_global_blob_files(storage_root: Path) -> Iterator[Path]:
    yield from _iter_blob_files_under(global_objects_root(storage_root))


def referenced_blobs_in_root(storage_root: Path) -> set[str]:
    """Sha256 set referenced by every manifest below *storage_root*."""

    refs: set[str] = set()
    if not storage_root.exists():
        return refs
    for child in storage_root.iterdir():
        if child.name == OBJECTS_DIR or not child.is_dir():
            continue
        refs.update(referenced_blobs(child))
    return refs


def load_refs_index(storage_root: Path) -> dict[str, int]:
    path = refs_index_path(storage_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    refs = data.get("refs") if isinstance(data, dict) else None
    if not isinstance(refs, dict):
        return {}
    out: dict[str, int] = {}
    for sha, count in refs.items():
        if not isinstance(sha, str) or len(sha) != 64:
            continue
        try:
            parsed = int(count)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out[sha] = parsed
    return out


def save_refs_index(storage_root: Path, refs: dict[str, int]) -> None:
    storage_root = Path(storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)
    path = refs_index_path(storage_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    clean = {
        sha: int(count)
        for sha, count in sorted(refs.items())
        if len(sha) == 64 and int(count) > 0
    }
    tmp.write_text(
        json.dumps({"version": 1, "refs": clean}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def increment_refs(storage_root: Path, shas: Iterable[str]) -> None:
    refs = load_refs_index(storage_root)
    for sha in shas:
        refs[sha] = refs.get(sha, 0) + 1
    save_refs_index(storage_root, refs)


def decrement_refs(storage_root: Path, shas: Iterable[str]) -> None:
    refs = load_refs_index(storage_root)
    for sha in shas:
        next_count = refs.get(sha, 0) - 1
        if next_count > 0:
            refs[sha] = next_count
        else:
            refs.pop(sha, None)
    save_refs_index(storage_root, refs)


def rebuild_refs_index(storage_root: Path) -> dict[str, int]:
    refs: dict[str, int] = {}
    storage_root = Path(storage_root)
    if storage_root.exists():
        for child in storage_root.iterdir():
            if child.name == OBJECTS_DIR or not child.is_dir():
                continue
            for manifest_path in iter_manifest_paths(child):
                try:
                    manifest = read_manifest(manifest_path)
                except (OSError, json.JSONDecodeError, KeyError, ValueError):
                    continue
                for sha in manifest_blob_refs(manifest):
                    refs[sha] = refs.get(sha, 0) + 1
    save_refs_index(storage_root, refs)
    return refs


def gc_dir(dir_root: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Remove blobs in *dir_root* not referenced by any manifest.

    Returns ``(blobs_removed, bytes_freed)``.
    """

    refs = referenced_blobs(dir_root)
    removed = 0
    freed = 0
    for blob in iter_blob_files(dir_root, include_global=False):
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


def gc_global(
    storage_root: Path,
    *,
    dry_run: bool = False,
    rebuild_index: bool = False,
) -> tuple[int, int]:
    """Remove v3 global blobs not referenced by any manifest in the store."""

    if rebuild_index:
        indexed_refs = rebuild_refs_index(storage_root)
    else:
        indexed_refs = load_refs_index(storage_root)
    refs = set(indexed_refs) if indexed_refs else referenced_blobs_in_root(storage_root)
    removed = 0
    freed = 0
    for blob in _iter_blob_files_under(global_objects_root(storage_root)):
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
        obj_root = global_objects_root(storage_root)
        if obj_root.exists():
            for sub in list(obj_root.iterdir()):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
    return removed, freed


def verify_blob(dir_root: Path, sha256: str) -> int:
    """Decompress and verify one blob. Returns raw byte size."""

    data = read_blob_bytes(dir_root, sha256)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise ValueError(f"blob {sha256[:12]} checksum mismatch")
    return len(data)


def is_manifest_artifact(path: Path) -> bool:
    return path.name.endswith(MANIFEST_SUFFIX) or path.name.endswith(
        COMPRESSED_MANIFEST_SUFFIX
    )
