"""Portable bundle import/export support for snapz stores."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BufferedWriter, BytesIO
from pathlib import Path
from typing import Optional

from snapz import archive, cas
from snapz.config import META_SUFFIX, RuntimeConfig, default_config
from snapz.store import (
    ARCHIVE_SUFFIXES,
    DirMeta,
    SnapshotMeta,
    Store,
    read_source_marker,
    source_identity,
)
from snapz.util import compute_key, now_iso, resolve_path, validate_snapshot_name


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
