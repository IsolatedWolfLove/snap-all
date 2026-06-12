"""Cold compaction support for snapz-server."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from snapz import cas
from snapz.util import now_iso
from snapz._api_core import _BUZHASH_TABLE, _rotl  # noqa: PLC2701
from snapz._api_bundle import _open_bundle_tar_writer
from snapz.config import RuntimeConfig
from snapz_server import db
from snapz_server.bundles import (
    BUNDLE_META_NAME,
    _open_bundle_tar_reader,
    _read_bundle_meta,
    _read_json_member,
    _row_path,
)
from snapz_server.server_config import CompactConfig


class CompactError(RuntimeError):
    """Raised when cold compaction or cold reads fail."""


@dataclass(frozen=True)
class CompactResult:
    tenant_id: str
    source_id: str
    revision: str
    snapshot_count: int
    object_count: int
    chunk_count: int
    raw_logical_bytes: int
    cold_physical_bytes: int


@dataclass(frozen=True)
class _StoredChunk:
    sha256: str
    raw_size: int
    pack_id: str
    offset: int
    compressed_size: int


@dataclass
class _PackWriter:
    data_dir: Path
    tenant_id: str
    config: CompactConfig
    pack_id: str = ""
    tmp: Path | None = None
    file: Any = None
    offset: int = 0
    raw_size: int = 0
    chunk_count: int = 0
    sealed: list[tuple[str, Path, int, int, int]] | None = None

    def __post_init__(self) -> None:
        self.sealed = []

    def add_chunk(self, raw: bytes) -> dict[str, Any]:
        if cas._zstandard is None:  # noqa: SLF001
            raise CompactError("zstandard not installed; cannot compact cold chunks")
        compressed = cas._zstandard.ZstdCompressor(  # noqa: SLF001
            level=self.config.zstd_level,
        ).compress(raw)
        if self.file is None:
            self._open()
        elif (
            self.offset > 0
            and self.offset + len(compressed) > self.config.pack_target_bytes
        ):
            self.seal()
            self._open()
        assert self.file is not None
        pack_id = self.pack_id
        offset = self.offset
        self.file.write(compressed)
        self.offset += len(compressed)
        self.raw_size += len(raw)
        self.chunk_count += 1
        return {
            "pack_id": pack_id,
            "offset": offset,
            "compressed_size": len(compressed),
        }

    def _open(self) -> None:
        pack_root = self.data_dir / "cold" / self.tenant_id / "packs"
        pack_root.mkdir(parents=True, exist_ok=True)
        stamp = now_iso().replace(":", "").replace("-", "")
        self.pack_id = (
            f"pack_{stamp}_{os.getpid()}_"
            f"{len(self.sealed or []):04d}_{secrets.token_hex(4)}"
        )
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{self.pack_id}.",
            suffix=".tmp",
            dir=pack_root,
        )
        self.tmp = Path(tmp_name)
        self.file = os.fdopen(fd, "wb")
        self.offset = 0
        self.raw_size = 0
        self.chunk_count = 0

    def seal(self) -> None:
        if self.file is None or self.tmp is None:
            return
        self.file.close()
        self.file = None
        target = db.cold_pack_path(self.data_dir, self.tenant_id, self.pack_id)
        os.replace(self.tmp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self.sealed.append(
            (self.pack_id, target, self.offset, self.raw_size, self.chunk_count)
        )
        self.tmp = None

    def close(self) -> None:
        self.seal()

    def cleanup(self) -> None:
        try:
            if self.file is not None:
                self.file.close()
        finally:
            self.file = None
        if self.tmp is not None:
            self.tmp.unlink(missing_ok=True)
            self.tmp = None


def compact_pending(
    data_dir: str | Path,
    *,
    config: CompactConfig,
    limit: int | None = None,
) -> list[CompactResult]:
    """Compact pending or failed jobs once."""

    results: list[CompactResult] = []
    jobs = [
        *db.list_compact_jobs(data_dir, status="pending"),
        *db.list_compact_jobs(data_dir, status="failed"),
    ]
    if limit is not None:
        jobs = jobs[: max(0, int(limit))]
    for job in jobs:
        results.append(
            compact_source(
                data_dir,
                tenant_id=str(job["tenant_id"]),
                source_id=str(job["source_id"]),
                revision=str(job["revision"]),
                config=config,
            )
        )
    return results


def compact_source(
    data_dir: str | Path,
    *,
    tenant_id: str,
    source_id: str,
    revision: str,
    config: CompactConfig,
) -> CompactResult:
    """Compact one source revision from incoming/legacy bundle storage."""

    data_root = Path(data_dir)
    bundle = db.readable_bundle_path(data_root, tenant_id, source_id)
    if not bundle.is_file():
        raise FileNotFoundError(f"bundle not found: {tenant_id}/{source_id}")
    actual_revision = _sha256_file(bundle)
    if actual_revision != revision:
        raise CompactError("compact revision does not match bundle sha256")
    db.update_compact_job_status(
        data_root,
        tenant_id,
        source_id,
        revision,
        status="running",
    )
    try:
        result = _compact_source_inner(
            data_root,
            tenant_id=tenant_id,
            source_id=source_id,
            revision=revision,
            bundle=bundle,
            config=config,
        )
    except Exception as exc:
        db.update_compact_job_status(
            data_root,
            tenant_id,
            source_id,
            revision,
            status="failed",
            error=str(exc),
        )
        raise
    db.update_compact_job_status(
        data_root,
        tenant_id,
        source_id,
        revision,
        status="complete",
        raw_logical_bytes=result.raw_logical_bytes,
        cold_physical_bytes=result.cold_physical_bytes,
    )
    return result


def cold_index(
    data_dir: str | Path,
    *,
    tenant_id: str,
    source_id: str,
) -> dict[str, Any] | None:
    row = db.get_complete_cold_source(data_dir, tenant_id, source_id)
    if row is None:
        return None
    return _read_metadata_json(
        db.cold_metadata_path(data_dir, tenant_id, str(row["cold_manifest_sha256"]))
    )


def cold_object_blob(
    data_dir: str | Path,
    *,
    tenant_id: str,
    source_id: str,
    sha256: str,
    zstd_level: int,
) -> bytes | None:
    source = db.get_complete_cold_source(data_dir, tenant_id, source_id)
    if source is None:
        return None
    if not db.cold_source_object_exists(
        data_dir,
        tenant_id=tenant_id,
        source_id=source_id,
        revision=str(source["revision"]),
        raw_sha256=sha256,
    ):
        return None
    object_row = db.get_cold_object(data_dir, tenant_id, sha256)
    if object_row is None:
        return None
    raw = read_cold_object_raw(data_dir, tenant_id=tenant_id, sha256=sha256)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise CompactError(f"cold object checksum mismatch: {sha256}")
    if cas._zstandard is not None:  # noqa: SLF001
        return cas._zstandard.ZstdCompressor(level=zstd_level).compress(raw)  # noqa: SLF001
    return gzip.compress(raw, compresslevel=9)


def write_client_bundle(
    data_dir: str | Path,
    *,
    tenant_id: str,
    source_id: str,
    destination: Path,
    zstd_level: int,
) -> bool:
    index = cold_index(data_dir, tenant_id=tenant_id, source_id=source_id)
    if index is None:
        return False
    cfg = RuntimeConfig(
        root=Path(data_dir) / "hot",
        use_zstd=cas._zstandard is not None,  # noqa: SLF001
        zstd_level=zstd_level,
        gzip_level=9,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        with _open_bundle_tar_writer(tmp, cfg) as tar:
            _add_json(tar, BUNDLE_META_NAME, _bundle_meta_from_index(index))
            source_meta = index.get("source_meta")
            if isinstance(source_meta, dict):
                _add_json(tar, "source/_meta.json", source_meta)
            for item in list(index.get("snapshots") or []):
                if not isinstance(item, dict):
                    continue
                row = dict(item.get("row") or {})
                meta = dict(item.get("meta") or {})
                manifest = dict(item.get("manifest") or {})
                _add_json(tar, str(row.get("meta") or ""), meta)
                _add_json(tar, str(row.get("artifact") or ""), manifest)
            for sha in list(index.get("blobs") or []):
                sha_text = str(sha or "")
                blob = cold_object_blob(
                    data_dir,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    sha256=sha_text,
                    zstd_level=zstd_level,
                )
                if blob is None:
                    raise CompactError(f"cold object not found: {sha_text}")
                _add_bytes(tar, f"objects/{sha_text[:2]}/{sha_text}", blob)
        os.replace(tmp, destination)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def read_cold_object_raw(
    data_dir: str | Path,
    *,
    tenant_id: str,
    sha256: str,
) -> bytes:
    row = db.get_cold_object(data_dir, tenant_id, sha256)
    if row is None:
        raise FileNotFoundError(f"cold object not found: {sha256}")
    try:
        chunks = json.loads(row["chunks_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise CompactError(f"invalid cold object chunk index: {sha256}") from exc
    out = bytearray()
    for chunk in chunks if isinstance(chunks, list) else []:
        if not isinstance(chunk, dict):
            raise CompactError(f"invalid cold object chunk entry: {sha256}")
        out.extend(
            _read_cold_chunk(
                data_dir,
                tenant_id=tenant_id,
                chunk_sha256=str(chunk.get("sha256") or ""),
                expected_size=int(chunk.get("size") or 0),
            )
        )
    raw = bytes(out)
    if len(raw) != int(row["raw_size"] or 0):
        raise CompactError(f"cold object size mismatch: {sha256}")
    return raw


def _compact_source_inner(
    data_dir: Path,
    *,
    tenant_id: str,
    source_id: str,
    revision: str,
    bundle: Path,
    config: CompactConfig,
) -> CompactResult:
    pack = _PackWriter(data_dir=data_dir, tenant_id=tenant_id, config=config)
    try:
        with _open_bundle_tar_reader(bundle) as tar:
            meta = _read_bundle_meta(tar)
            rows = [dict(row or {}) for row in meta.get("snapshots") or []]
            if not rows:
                raise CompactError("bundle has no snapshots")
            index: dict[str, Any] = {
                "source": dict(meta.get("source") or {}),
                "source_meta": _optional_json_member(tar, "source/_meta.json"),
                "snapshots": [],
                "blobs": [],
                "snapshot_count": len(rows),
                "bundle_bytes": bundle.stat().st_size,
                "bundle_sha256": revision,
                "cold": {
                    "format_version": 1,
                    "created_at": now_iso(),
                    "source_id": source_id,
                    "revision": revision,
                },
            }
            object_refs: dict[str, int] = {}
            object_chunks: dict[str, dict[str, Any]] = {}
            snapshot_rows: list[dict[str, str]] = []
            stored_chunks: list[_StoredChunk] = []
            raw_logical_bytes = 0
            for row in rows:
                kind = str(row.get("kind") or "")
                item: dict[str, Any] = {
                    "row": dict(row),
                    "meta": _read_json_member(tar, _row_path(row, "meta")),
                }
                if kind == "manifest":
                    manifest = _read_json_member(tar, _row_path(row, "artifact"))
                    item["manifest"] = manifest
                    for sha in _manifest_refs(manifest):
                        object_refs[sha] = object_refs.get(sha, 0) + 1
                    snapshot_rows.append({
                        "snapshot_name": str(row.get("name") or ""),
                        "meta_zstd_sha256": _write_metadata_json(
                            data_dir,
                            tenant_id,
                            item["meta"],
                            level=config.manifest_zstd_level,
                        ),
                        "manifest_zstd_sha256": _write_metadata_json(
                            data_dir,
                            tenant_id,
                            manifest,
                            level=config.manifest_zstd_level,
                        ),
                    })
                elif kind != "legacy":
                    raise CompactError(f"unknown snapshot artifact kind: {kind!r}")
                index["snapshots"].append(item)

            for sha in sorted(object_refs):
                raw = _read_bundle_object_raw(tar, sha)
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise CompactError(f"bundle object checksum mismatch: {sha}")
                raw_logical_bytes += len(raw) * object_refs[sha]
                object_chunks[sha] = _store_object_chunks(
                    data_dir,
                    tenant_id=tenant_id,
                    raw_sha256=sha,
                    raw=raw,
                    ref_count=object_refs[sha],
                    pack=pack,
                    config=config,
                    stored_chunks=stored_chunks,
                )
            index["blobs"] = sorted(object_refs)
            cold_manifest_sha256 = _write_metadata_json(
                data_dir,
                tenant_id,
                index,
                level=config.manifest_zstd_level,
            )
        pack.close()
        with db.connect(data_dir) as con:
            for pack_id, target, compressed_size, raw_size, chunk_count in pack.sealed or []:
                db.insert_cold_pack(
                    con,
                    tenant_id=tenant_id,
                    pack_id=pack_id,
                    relative_path=str(target.relative_to(data_dir)),
                    compressed_size=compressed_size,
                    raw_size=raw_size,
                    chunk_count=chunk_count,
                )
            for chunk in stored_chunks:
                db.insert_cold_chunk(
                    con,
                    tenant_id=tenant_id,
                    chunk_sha256=chunk.sha256,
                    raw_size=chunk.raw_size,
                    pack_id=chunk.pack_id,
                    offset=chunk.offset,
                    compressed_size=chunk.compressed_size,
                    zstd_level=config.zstd_level,
                )
        cold_physical_bytes = _cold_physical_bytes(data_dir, tenant_id)
        db.replace_cold_source(
            data_dir,
            tenant_id=tenant_id,
            source_id=source_id,
            revision=revision,
            incoming_bundle_sha256=revision,
            cold_manifest_sha256=cold_manifest_sha256,
            snapshots=snapshot_rows,
            objects=object_chunks,
            raw_logical_bytes=raw_logical_bytes,
            cold_physical_bytes=cold_physical_bytes,
        )
        _verify_cold_index(
            data_dir,
            tenant_id=tenant_id,
            cold_manifest_sha256=cold_manifest_sha256,
        )
        return CompactResult(
            tenant_id=tenant_id,
            source_id=source_id,
            revision=revision,
            snapshot_count=len(rows),
            object_count=len(object_chunks),
            chunk_count=sum(len(item["chunks"]) for item in object_chunks.values()),
            raw_logical_bytes=raw_logical_bytes,
            cold_physical_bytes=cold_physical_bytes,
        )
    except Exception:
        pack.cleanup()
        raise


def _store_object_chunks(
    data_dir: Path,
    *,
    tenant_id: str,
    raw_sha256: str,
    raw: bytes,
    ref_count: int,
    pack: _PackWriter,
    config: CompactConfig,
    stored_chunks: list[_StoredChunk],
) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    for data in _iter_server_chunks(raw, config=config):
        chunk_sha = hashlib.sha256(data).hexdigest()
        chunks.append({"sha256": chunk_sha, "size": len(data)})
        if db.cold_chunk_exists(data_dir, tenant_id, chunk_sha):
            continue
        packed = pack.add_chunk(data)
        stored_chunks.append(
            _StoredChunk(
                sha256=chunk_sha,
                raw_size=len(data),
                pack_id=str(packed["pack_id"]),
                offset=int(packed["offset"]),
                compressed_size=int(packed["compressed_size"]),
            )
        )
    return {
        "raw_size": len(raw),
        "chunks": chunks,
        "ref_count": max(1, int(ref_count)),
    }


def _iter_server_chunks(raw: bytes, *, config: CompactConfig) -> Iterable[bytes]:
    if len(raw) < config.chunk_file_bytes:
        yield raw
        return
    yield from _iter_content_defined_bytes(
        raw,
        min_bytes=config.chunk_min_bytes,
        avg_bytes=config.chunk_avg_bytes,
        max_bytes=config.chunk_max_bytes,
    )


def _iter_content_defined_bytes(
    data: bytes,
    *,
    min_bytes: int,
    avg_bytes: int,
    max_bytes: int,
) -> Iterable[bytes]:
    min_bytes = max(1, int(min_bytes))
    avg_bytes = max(min_bytes + 1, int(avg_bytes))
    max_bytes = max(avg_bytes, int(max_bytes))
    mask = max(1, avg_bytes - 1)
    window_size = 64
    window = bytearray()
    window_pos = 0
    rolling = 0
    chunk = bytearray()
    for value in data:
        chunk.append(value)
        if len(window) < window_size:
            window.append(value)
            rolling = _rotl(rolling, 1) ^ _BUZHASH_TABLE[value]
        else:
            old = window[window_pos]
            window[window_pos] = value
            window_pos = (window_pos + 1) % window_size
            rolling = (
                _rotl(rolling, 1)
                ^ _rotl(_BUZHASH_TABLE[old], window_size)
                ^ _BUZHASH_TABLE[value]
            )
        length = len(chunk)
        if length < min_bytes:
            continue
        if length >= max_bytes or (rolling & mask) == 0:
            yield bytes(chunk)
            chunk.clear()
    if chunk:
        yield bytes(chunk)


def _read_bundle_object_raw(tar: Any, sha: str) -> bytes:
    member_name = f"objects/{sha[:2]}/{sha}"
    try:
        member = tar.getmember(member_name)
    except KeyError as exc:
        raise CompactError(f"bundle missing object: {sha}") from exc
    if not member.isfile():
        raise CompactError(f"bundle object is not a file: {sha}")
    extracted = tar.extractfile(member)
    if extracted is None:
        raise CompactError(f"bundle object is not readable: {sha}")
    return cas._decode_blob_bytes(extracted.read(), sha)  # noqa: SLF001


def _read_cold_chunk(
    data_dir: str | Path,
    *,
    tenant_id: str,
    chunk_sha256: str,
    expected_size: int,
) -> bytes:
    row = db.get_cold_chunk(data_dir, tenant_id, chunk_sha256)
    if row is None:
        raise FileNotFoundError(f"cold chunk not found: {chunk_sha256}")
    pack_path = db.cold_pack_path(data_dir, tenant_id, str(row["pack_id"]))
    with pack_path.open("rb") as fh:
        fh.seek(int(row["offset"]))
        compressed = fh.read(int(row["compressed_size"]))
    if len(compressed) != int(row["compressed_size"]):
        raise CompactError(f"cold chunk truncated: {chunk_sha256}")
    if cas._zstandard is None:  # noqa: SLF001
        raise CompactError("zstandard not installed; cannot read cold chunk")
    raw = cas._zstandard.ZstdDecompressor().decompress(compressed)  # noqa: SLF001
    if expected_size and len(raw) != expected_size:
        raise CompactError(f"cold chunk size mismatch: {chunk_sha256}")
    if hashlib.sha256(raw).hexdigest() != chunk_sha256:
        raise CompactError(f"cold chunk checksum mismatch: {chunk_sha256}")
    return raw


def _write_metadata_json(
    data_dir: Path,
    tenant_id: str,
    payload: dict[str, Any],
    *,
    level: int,
) -> str:
    if cas._zstandard is None:  # noqa: SLF001
        raise CompactError("zstandard not installed; cannot compact metadata")
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    compressed = cas._zstandard.ZstdCompressor(level=level).compress(raw)  # noqa: SLF001
    sha = hashlib.sha256(compressed).hexdigest()
    target = db.cold_metadata_path(data_dir, tenant_id, sha)
    if target.exists():
        return sha
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{sha}.",
        suffix=".tmp",
        dir=target.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(compressed)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        try:
            os.link(tmp, target)
        except FileExistsError:
            pass
        return sha
    finally:
        tmp.unlink(missing_ok=True)


def _read_metadata_json(path: Path) -> dict[str, Any]:
    if cas._zstandard is None:  # noqa: SLF001
        raise CompactError("zstandard not installed; cannot read cold metadata")
    raw = cas._zstandard.ZstdDecompressor().decompress(path.read_bytes())  # noqa: SLF001
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise CompactError(f"cold metadata must be an object: {path}")
    return data


def _optional_json_member(tar: Any, name: str) -> dict[str, Any] | None:
    try:
        return _read_json_member(tar, name)
    except ValueError:
        return None


def _manifest_refs(manifest: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for entry in list(manifest.get("entries") or []):
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        chunks = entry.get("chunks") or []
        if isinstance(chunks, list) and chunks:
            for chunk in chunks:
                if isinstance(chunk, dict) and chunk.get("sha256"):
                    refs.append(str(chunk["sha256"]))
            continue
        sha = str(entry.get("sha256") or "")
        if sha:
            refs.append(sha)
    return refs


def _verify_cold_index(
    data_dir: Path,
    *,
    tenant_id: str,
    cold_manifest_sha256: str,
) -> None:
    index = _read_metadata_json(
        db.cold_metadata_path(data_dir, tenant_id, cold_manifest_sha256)
    )
    for sha in list(index.get("blobs") or [])[:3]:
        raw = read_cold_object_raw(data_dir, tenant_id=tenant_id, sha256=str(sha))
        if hashlib.sha256(raw).hexdigest() != str(sha):
            raise CompactError(f"cold verification failed for object {sha}")


def _bundle_meta_from_index(index: dict[str, Any]) -> dict[str, Any]:
    source = dict(index.get("source") or {})
    snapshots = [dict(item.get("row") or {}) for item in list(index.get("snapshots") or [])]
    return {
        "format_version": 1,
        "created": str(index.get("created") or now_iso()),
        "source": source,
        "snapshots": snapshots,
        "blobs": [str(sha) for sha in list(index.get("blobs") or [])],
    }


def _add_json(tar: Any, name: str, data: dict[str, Any]) -> None:
    if not name:
        raise CompactError("cannot write empty bundle member")
    raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX):
        if cas._zstandard is None:  # noqa: SLF001
            raise CompactError(f"zstandard not installed; cannot write {name}")
        raw = cas._zstandard.ZstdCompressor().compress(raw)  # noqa: SLF001
    _add_bytes(tar, name, raw)


def _add_bytes(tar: Any, name: str, raw: bytes) -> None:
    if not name:
        raise CompactError("cannot write empty bundle member")
    import tarfile

    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(raw))


def _cold_physical_bytes(data_dir: Path, tenant_id: str) -> int:
    root = data_dir / "cold" / tenant_id
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
