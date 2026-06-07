"""Admin-side helpers for inspecting and editing uploaded ``.snapz`` bundles."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from snapz import cas
from snapz.api import _open_bundle_tar_reader, BUNDLE_FORMAT_VERSION, BUNDLE_META_NAME
from snapz.config import META_SUFFIX
from snapz.util import validate_snapshot_name

MAX_PAGE_SIZE = 200
MEMORY_FRACTION = 0.80
MAX_TAR_MEMBERS = 200_000


class BundleMemoryError(ValueError):
    """Raised when a bundle would exceed the configured in-memory budget."""


def validate_bundle_file(bundle: Path) -> None:
    """Reject malformed or unsafe bundle tar members before storing them."""

    with _open_bundle_tar_reader(bundle) as tar:
        members = tar.getmembers()
        if len(members) > MAX_TAR_MEMBERS:
            raise ValueError("bundle has too many tar members")
        names = {member.name for member in members}
        for member in members:
            _validate_member(member)
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows(meta)
        if not rows:
            raise ValueError("bundle has no snapshots")
        for row in rows:
            meta_path = _row_path(row, "meta")
            artifact_path = _row_path(row, "artifact")
            kind = str(row.get("kind") or "")
            if meta_path not in names:
                raise ValueError(f"bundle missing snapshot metadata: {meta_path}")
            if artifact_path not in names:
                raise ValueError(f"bundle missing snapshot artifact: {artifact_path}")
            if kind not in {"manifest", "legacy"}:
                raise ValueError(f"unknown snapshot artifact kind: {kind!r}")
            if kind == "manifest" and not (
                artifact_path.startswith("source/snapshots/")
                and (
                    artifact_path.endswith(cas.MANIFEST_SUFFIX)
                    or artifact_path.endswith(cas.COMPRESSED_MANIFEST_SUFFIX)
                )
            ):
                raise ValueError(f"invalid manifest path: {artifact_path}")
            if kind == "legacy" and not artifact_path.startswith("source/"):
                raise ValueError(f"invalid legacy artifact path: {artifact_path}")
        for sha in meta.get("blobs") or []:
            sha_text = str(sha or "")
            if not _is_sha256(sha_text):
                raise ValueError(f"invalid blob id in bundle: {sha_text!r}")
            blob_path = f"objects/{sha_text[:2]}/{sha_text}"
            if blob_path not in names:
                raise ValueError(f"bundle missing blob: {blob_path}")
        manifest_refs = _referenced_blobs(tar, rows)
        declared_blobs = {str(sha or "") for sha in meta.get("blobs") or []}
        missing_declared = sorted(manifest_refs - declared_blobs)
        if missing_declared:
            raise ValueError(
                "bundle manifest references undeclared blob: "
                + missing_declared[0]
            )


def bundle_index(bundle: Path) -> dict[str, Any]:
    """Return metadata, manifests, and object ids for an uploaded bundle."""

    with _open_bundle_tar_reader(bundle) as tar:
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows(meta)
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "row": dict(row),
                "meta": _read_json_member(tar, _row_path(row, "meta")),
            }
            if str(row.get("kind") or "") == "manifest":
                item["manifest"] = _read_json_member(tar, _row_path(row, "artifact"))
            snapshots.append(item)
    return {
        "source": dict(meta.get("source") or {}),
        "snapshots": snapshots,
        "blobs": [str(sha) for sha in meta.get("blobs") or []],
        "snapshot_count": len(snapshots),
        "bundle_bytes": bundle.stat().st_size,
        "bundle_sha256": _sha256_file(bundle),
    }


def validate_delta_bundle_file(delta: Path, *, existing_blobs: set[str]) -> None:
    """Validate a delta bundle against blobs already present remotely."""

    with _open_bundle_tar_reader(delta) as tar:
        members = tar.getmembers()
        if len(members) > MAX_TAR_MEMBERS:
            raise ValueError("bundle has too many tar members")
        names = {member.name for member in members}
        for member in members:
            _validate_member(member)
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows(meta)
        if not rows:
            raise ValueError("bundle has no snapshots")
        for row in rows:
            meta_path = _row_path(row, "meta")
            artifact_path = _row_path(row, "artifact")
            kind = str(row.get("kind") or "")
            if meta_path not in names:
                raise ValueError(f"bundle missing snapshot metadata: {meta_path}")
            if artifact_path not in names:
                raise ValueError(f"bundle missing snapshot artifact: {artifact_path}")
            if kind not in {"manifest", "legacy"}:
                raise ValueError(f"unknown snapshot artifact kind: {kind!r}")
        delta_blobs = _declared_blobs(meta)
        for sha in delta_blobs:
            blob_path = f"objects/{sha[:2]}/{sha}"
            if blob_path not in names:
                raise ValueError(f"bundle missing blob: {blob_path}")
        manifest_refs = _referenced_blobs(tar, rows)
        missing = sorted(manifest_refs - existing_blobs - delta_blobs)
        if missing:
            raise ValueError(
                "bundle manifest references unavailable blob: "
                + missing[0]
            )


def merge_delta_bundle(target: Path, delta: Path) -> dict[str, Any]:
    """Merge *delta* into *target*, preserving a full bundle on disk."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        validate_bundle_file(delta)
        os.replace(delta, target)
        return bundle_index(target)

    with _open_bundle_tar_reader(target) as existing, _open_bundle_tar_reader(delta) as incoming:
        existing_meta = _read_bundle_meta(existing)
        incoming_meta = _read_bundle_meta(incoming)
        existing_rows = _snapshot_rows(existing_meta)
        incoming_rows = _snapshot_rows(incoming_meta)
        declared_snapshot_names = {
            str(name)
            for name in incoming_meta.get("snapshot_names") or []
            if str(name)
        }
        incoming_by_name = {
            str(row.get("name") or ""): row
            for row in incoming_rows
        }
        merged_rows: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for row in existing_rows:
            name = str(row.get("name") or "")
            if name in incoming_by_name:
                merged_rows.append(dict(incoming_by_name[name]))
            elif declared_snapshot_names and name not in declared_snapshot_names:
                continue
            else:
                merged_rows.append(dict(row))
            seen_names.add(name)
        for row in incoming_rows:
            name = str(row.get("name") or "")
            if name not in seen_names:
                merged_rows.append(dict(row))
                seen_names.add(name)

        keep_blobs = _referenced_blobs_for_rows(
            existing,
            incoming,
            merged_rows,
            incoming_by_name=set(incoming_by_name),
        )
        merged_meta = dict(existing_meta)
        merged_meta["created"] = incoming_meta.get("created", existing_meta.get("created", ""))
        merged_meta["source"] = dict(incoming_meta.get("source") or existing_meta.get("source") or {})
        _set_bundle_rows(merged_meta, merged_rows)
        merged_meta["blobs"] = sorted(keep_blobs)

        incoming_source_meta = _optional_json_member(incoming, "source/_meta.json")
        existing_source_meta = _optional_json_member(existing, "source/_meta.json")
        source_meta = incoming_source_meta or existing_source_meta
        if source_meta is not None:
            source_meta["snapshot_count"] = len(merged_rows)

        existing_members = existing.getmembers()
        incoming_members = incoming.getmembers()
        replaced_paths = _snapshot_member_paths(list(incoming_rows))
        incoming_blobs = _declared_blobs(incoming_meta)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        os.close(fd)
        tmp = Path(tmp_name)
        added: set[str] = set()
        try:
            mode = target.stat().st_mode & 0o777
            with tarfile.open(tmp, "w:gz") as dst:
                _add_json(dst, BUNDLE_META_NAME, merged_meta)
                added.add(BUNDLE_META_NAME)
                if source_meta is not None:
                    _add_json(dst, "source/_meta.json", source_meta)
                    added.add("source/_meta.json")
                for member in existing_members:
                    if member.name in added or member.name in replaced_paths:
                        continue
                    if _is_bundle_object(member.name):
                        sha = Path(member.name).name
                        if sha not in keep_blobs or sha in incoming_blobs:
                            continue
                    _copy_member(existing, dst, member)
                    added.add(member.name)
                for member in incoming_members:
                    if member.name in added or member.name in {
                        BUNDLE_META_NAME,
                        "source/_meta.json",
                    }:
                        continue
                    if _is_bundle_object(member.name):
                        sha = Path(member.name).name
                        if sha not in keep_blobs:
                            continue
                    _copy_member(incoming, dst, member)
                    added.add(member.name)
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    validate_bundle_file(target)
    return bundle_index(target)


def list_bundle_snapshots(
    bundle: Path,
    *,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    memory = _check_bundle_memory(bundle)
    with _open_bundle_tar_reader(bundle) as tar:
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows_sorted(tar, meta)
        page, per_page = _normalize_page(page, per_page)
        total = len(rows)
        start = (page - 1) * per_page
        selected = rows[start:start + per_page]
        snapshots = [_snapshot_summary(tar, row) for row in selected]
    return {
        "snapshots": snapshots,
        "page": page,
        "per_page": per_page,
        "total": total,
        "has_next": start + per_page < total,
        "has_prev": page > 1,
        "memory": memory,
    }


def rename_bundle_snapshot(bundle: Path, old_name: str, new_name: str) -> dict[str, Any]:
    validate_snapshot_name(old_name)
    validate_snapshot_name(new_name)
    if old_name == new_name:
        raise ValueError("new snapshot name must be different")

    memory = _check_bundle_memory(bundle)
    with _open_bundle_tar_reader(bundle) as tar:
        members = tar.getmembers()
        member_names = {member.name for member in members}
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows(meta)
        row = _find_snapshot_row(rows, old_name)
        if row is None:
            raise KeyError(f"snapshot not found: {old_name}")
        if any(str(item.get("name") or "") == new_name for item in rows):
            raise FileExistsError(f"snapshot already exists: {new_name}")

        old_meta_arc = _row_path(row, "meta")
        old_artifact_arc = _row_path(row, "artifact")
        kind = str(row.get("kind") or "")
        new_meta_arc = f"source/{new_name}{META_SUFFIX}"
        new_artifact_arc = _renamed_artifact_path(old_artifact_arc, kind, new_name)
        _ensure_target_member_available(member_names, new_meta_arc, old_meta_arc)
        _ensure_target_member_available(member_names, new_artifact_arc, old_artifact_arc)

        snapshot_meta = _read_json_member(tar, old_meta_arc)
        snapshot_meta["name"] = new_name
        snapshot_meta["archive"] = Path(new_artifact_arc).name
        manifest_meta: dict[str, Any] | None = None
        if kind == "manifest":
            manifest_meta = _read_json_member(tar, old_artifact_arc)
            manifest_meta["snapshot"] = new_name

        updated_rows: list[dict[str, Any]] = []
        for item in rows:
            item_copy = dict(item)
            if str(item_copy.get("name") or "") == old_name:
                item_copy.update({
                    "name": new_name,
                    "meta": new_meta_arc,
                    "artifact": new_artifact_arc,
                })
            updated_rows.append(item_copy)
        _set_bundle_rows(meta, updated_rows)

        def transform(src: tarfile.TarFile, dst: tarfile.TarFile) -> None:
            for member in members:
                if member.name == BUNDLE_META_NAME:
                    _add_json(dst, BUNDLE_META_NAME, meta)
                elif member.name == old_meta_arc:
                    _add_json(dst, new_meta_arc, snapshot_meta)
                elif member.name == old_artifact_arc:
                    if manifest_meta is None:
                        _copy_member(src, dst, member, arcname=new_artifact_arc)
                    else:
                        _add_json(dst, new_artifact_arc, manifest_meta)
                else:
                    _copy_member(src, dst, member)

        _rewrite_bundle(bundle, transform)

    return {
        "snapshot_count": len(rows),
        "bundle_bytes": bundle.stat().st_size,
        "bundle_sha256": _sha256_file(bundle),
        "memory": memory,
    }


def delete_bundle_snapshot(bundle: Path, name: str) -> dict[str, Any]:
    validate_snapshot_name(name)
    memory = _check_bundle_memory(bundle)
    with _open_bundle_tar_reader(bundle) as tar:
        members = tar.getmembers()
        meta = _read_bundle_meta(tar)
        rows = _snapshot_rows(meta)
        row = _find_snapshot_row(rows, name)
        if row is None:
            raise KeyError(f"snapshot not found: {name}")

        remaining = [
            item
            for item in rows
            if str(item.get("name") or "") != name
        ]
        if not remaining:
            bundle.unlink(missing_ok=True)
            return {
                "snapshot_count": 0,
                "bundle_bytes": 0,
                "bundle_sha256": "",
                "memory": memory,
            }

        keep_blobs = _referenced_blobs(tar, remaining)
        _set_bundle_rows(meta, remaining)
        meta["blobs"] = sorted(keep_blobs)
        dir_meta = _optional_json_member(tar, "source/_meta.json")
        if dir_meta is not None:
            dir_meta["snapshot_count"] = len(remaining)

        old_meta_arc = _row_path(row, "meta")
        old_artifact_arc = _row_path(row, "artifact")

        def transform(src: tarfile.TarFile, dst: tarfile.TarFile) -> None:
            for member in members:
                if member.name in {old_meta_arc, old_artifact_arc}:
                    continue
                if member.name == BUNDLE_META_NAME:
                    _add_json(dst, BUNDLE_META_NAME, meta)
                elif member.name == "source/_meta.json" and dir_meta is not None:
                    _add_json(dst, "source/_meta.json", dir_meta)
                elif _is_unreferenced_blob(member.name, keep_blobs):
                    continue
                else:
                    _copy_member(src, dst, member)

        _rewrite_bundle(bundle, transform)

    return {
        "snapshot_count": len(remaining),
        "bundle_bytes": bundle.stat().st_size,
        "bundle_sha256": _sha256_file(bundle),
        "memory": memory,
    }


def _check_bundle_memory(bundle: Path) -> dict[str, int | float]:
    available = _available_memory_bytes()
    if available <= 0:
        raise BundleMemoryError("could not determine available system memory")

    with _open_bundle_tar_reader(bundle) as tar:
        unpacked = sum(member.size for member in tar.getmembers() if member.isfile())
    required = max(bundle.stat().st_size, unpacked)
    limit = int(available * MEMORY_FRACTION)
    if required > limit:
        raise BundleMemoryError(
            "bundle would exceed 80% of available memory "
            f"({required} bytes required, {limit} bytes allowed)"
        )
    return {
        "available_bytes": int(available),
        "limit_bytes": limit,
        "required_bytes": int(required),
        "limit_fraction": MEMORY_FRACTION,
    }


def _available_memory_bytes() -> int:
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                parts = line.split()
                return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0
    return pages * page_size


def _normalize_page(page: int, per_page: int) -> tuple[int, int]:
    page = max(1, int(page or 1))
    per_page = min(MAX_PAGE_SIZE, max(1, int(per_page or 50)))
    return page, per_page


def _read_bundle_meta(tar: tarfile.TarFile) -> dict[str, Any]:
    data = _read_json_member(tar, BUNDLE_META_NAME)
    if int(data.get("format_version", 0)) != BUNDLE_FORMAT_VERSION:
        raise ValueError(f"unsupported bundle format: {data.get('format_version')!r}")
    return data


def _read_json_member(tar: tarfile.TarFile, name: str) -> dict[str, Any]:
    try:
        member = tar.getmember(name)
    except KeyError as exc:
        raise ValueError(f"bundle missing {name}") from exc
    if not member.isfile():
        raise ValueError(f"bundle member is not a file: {name}")
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError(f"bundle member is not readable: {name}")
    raw = extracted.read()
    if name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX) or raw[:4] == cas._ZSTD_MAGIC:
        if cas._zstandard is None:  # noqa: SLF001
            raise ValueError(f"zstandard not installed; cannot read {name}")
        try:
            raw = cas._zstandard.ZstdDecompressor().decompress(raw)  # noqa: SLF001
        except Exception as exc:
            raise ValueError(f"bundle has invalid zstd member: {name}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"bundle has invalid JSON: {name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"bundle JSON member must be an object: {name}")
    return data


def _optional_json_member(tar: tarfile.TarFile, name: str) -> dict[str, Any] | None:
    try:
        return _read_json_member(tar, name)
    except ValueError:
        return None


def _snapshot_summary(tar: tarfile.TarFile, row: dict[str, Any]) -> dict[str, Any]:
    item = {
        "name": str(row.get("name") or ""),
        "kind": str(row.get("kind") or ""),
        "meta": str(row.get("meta") or ""),
        "artifact": str(row.get("artifact") or ""),
    }
    try:
        snap = _read_json_member(tar, item["meta"])
    except ValueError as exc:
        item["error"] = str(exc)
        return item
    item.update({
        "created": str(snap.get("created") or ""),
        "size_bytes": int(snap.get("size_bytes") or 0),
        "file_count": int(snap.get("file_count") or 0),
        "total_bytes_in": int(snap.get("total_bytes_in") or 0),
        "compression": str(snap.get("compression") or ""),
        "note": str(snap.get("note") or ""),
        "protected": bool(snap.get("protected", False)),
    })
    return item


def _snapshot_rows(meta: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row or {}) for row in meta.get("snapshots") or []]


def _snapshot_rows_sorted(
    tar: tarfile.TarFile,
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    rows_with_order: list[tuple[dict[str, Any], str, float, str]] = []
    for index, row in enumerate(_snapshot_rows(meta)):
        created = ""
        member_mtime = 0.0
        meta_path = str(row.get("meta") or "")
        try:
            member = tar.getmember(meta_path)
            member_mtime = float(member.mtime)
            snap = _read_json_member(tar, meta_path)
            created = str(snap.get("created") or "")
        except (KeyError, ValueError):
            pass
        rows_with_order.append((
            row,
            created,
            member_mtime,
            str(row.get("name") or index),
        ))
    rows_with_order.sort(
        key=lambda item: (item[1], item[2], item[3]),
        reverse=True,
    )
    return [row for row, _created, _member_mtime, _name in rows_with_order]


def _find_snapshot_row(
    rows: list[dict[str, Any]],
    name: str,
) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("name") or "") == name:
            return row
    return None


def _row_path(row: dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ".." in Path(value).parts
    ):
        raise ValueError(f"invalid bundle snapshot {key}: {value!r}")
    return value


def _validate_member(member: tarfile.TarInfo) -> None:
    name = member.name
    if not name or name.startswith("/") or "\\" in name or ".." in Path(name).parts:
        raise ValueError(f"unsafe bundle member path: {name!r}")
    if member.islnk() or member.issym():
        raise ValueError(f"links are not allowed in bundle tar: {name}")
    if not (member.isfile() or member.isdir()):
        raise ValueError(f"unsupported bundle tar member: {name}")
    if member.isdir():
        return
    if name == BUNDLE_META_NAME:
        return
    if name == "source/_meta.json":
        return
    if name.startswith("source/snapshots/") and (
        name.endswith(cas.MANIFEST_SUFFIX)
        or name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX)
    ):
        return
    if name.startswith("source/") and (
        name.endswith(META_SUFFIX)
        or name.endswith(".tar.gz")
        or name.endswith(".tar.zst")
    ):
        return
    if name.startswith("objects/"):
        parts = name.split("/")
        if len(parts) != 3 or parts[1] != parts[2][:2] or not _is_sha256(parts[2]):
            raise ValueError(f"invalid object member path: {name}")
        return
    raise ValueError(f"unexpected bundle member: {name}")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _renamed_artifact_path(old_artifact: str, kind: str, new_name: str) -> str:
    if kind == "manifest":
        suffix = (
            cas.COMPRESSED_MANIFEST_SUFFIX
            if old_artifact.endswith(cas.COMPRESSED_MANIFEST_SUFFIX)
            else cas.MANIFEST_SUFFIX
        )
        return f"source/snapshots/{new_name}{suffix}"
    if kind == "legacy":
        suffixes = Path(old_artifact).suffixes
        suffix = "".join(suffixes[-2:]) if len(suffixes) >= 2 else Path(old_artifact).suffix
        return f"source/{new_name}{suffix}"
    raise ValueError(f"unknown snapshot artifact kind: {kind!r}")


def _ensure_target_member_available(
    member_names: set[str],
    new_name: str,
    old_name: str,
) -> None:
    if new_name in member_names and new_name != old_name:
        raise FileExistsError(f"bundle member already exists: {new_name}")


def _set_bundle_rows(meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    meta["snapshots"] = rows
    source = dict(meta.get("source") or {})
    source["snapshot_count"] = len(rows)
    meta["source"] = source


def _referenced_blobs(tar: tarfile.TarFile, rows: list[dict[str, Any]]) -> set[str]:
    blobs: set[str] = set()
    for row in rows:
        if str(row.get("kind") or "") != "manifest":
            continue
        manifest = _read_json_member(tar, _row_path(row, "artifact"))
        for entry in manifest.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            chunks = entry.get("chunks") or []
            if isinstance(chunks, list) and chunks:
                for chunk in chunks:
                    if not isinstance(chunk, dict):
                        continue
                    sha = str(chunk.get("sha256") or "")
                    if sha:
                        blobs.add(sha)
                continue
            sha = str(entry.get("sha256") or "")
            if sha:
                blobs.add(sha)
    return blobs


def _referenced_blobs_for_rows(
    existing: tarfile.TarFile,
    incoming: tarfile.TarFile,
    rows: list[dict[str, Any]],
    *,
    incoming_by_name: set[str],
) -> set[str]:
    refs: set[str] = set()
    for row in rows:
        source = incoming if str(row.get("name") or "") in incoming_by_name else existing
        refs.update(_referenced_blobs(source, [row]))
    return refs


def _declared_blobs(meta: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for sha in meta.get("blobs") or []:
        sha_text = str(sha or "")
        if not _is_sha256(sha_text):
            raise ValueError(f"invalid blob id in bundle: {sha_text!r}")
        out.add(sha_text)
    return out


def _is_unreferenced_blob(member_name: str, keep_blobs: set[str]) -> bool:
    if not member_name.startswith("objects/"):
        return False
    sha = Path(member_name).name
    return sha not in keep_blobs


def _is_bundle_object(member_name: str) -> bool:
    if not member_name.startswith("objects/"):
        return False
    parts = member_name.split("/")
    return len(parts) == 3 and _is_sha256(parts[2])


def _snapshot_member_paths(rows: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for row in rows:
        paths.add(_row_path(row, "meta"))
        paths.add(_row_path(row, "artifact"))
    return paths


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _rewrite_bundle(
    bundle: Path,
    transform: Callable[[tarfile.TarFile, tarfile.TarFile], None],
) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{bundle.name}.",
        suffix=".tmp",
        dir=bundle.parent,
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        mode = bundle.stat().st_mode & 0o777
        with _open_bundle_tar_reader(bundle) as src, tarfile.open(tmp, "w:gz") as dst:
            transform(src, dst)
        os.chmod(tmp, mode)
        os.replace(tmp, bundle)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _copy_member(
    src: tarfile.TarFile,
    dst: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    arcname: str | None = None,
) -> None:
    info = copy.copy(member)
    if arcname is not None:
        info.name = arcname
    if member.isfile():
        extracted = src.extractfile(member)
        if extracted is None:
            raise ValueError(f"bundle member is not readable: {member.name}")
        dst.addfile(info, extracted)
    else:
        dst.addfile(info)


def _add_json(tar: tarfile.TarFile, name: str, data: dict[str, Any]) -> None:
    raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if name.endswith(cas.COMPRESSED_MANIFEST_SUFFIX):
        if cas._zstandard is None:  # noqa: SLF001
            raise ValueError(f"zstandard not installed; cannot write {name}")
        raw = cas._zstandard.ZstdCompressor().compress(raw)  # noqa: SLF001
    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = 0o600
    tar.addfile(info, BytesIO(raw))
