"""Tar+compression backend with dry-run support.

Two output formats are supported:

- ``.tar.zst`` (default): requires the optional :mod:`zstandard` package.
- ``.tar.gz`` (fallback): pure stdlib.

The compression choice is decided at runtime by detecting whether
:mod:`zstandard` is importable. ``RuntimeConfig.use_zstd`` lets callers
force the gzip path (mostly useful for tests that want a deterministic
output).
"""

from __future__ import annotations

import os
import sys
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from snapz.config import ARCHIVE_SUFFIX_GZIP, ARCHIVE_SUFFIX_ZSTD, RuntimeConfig
from snapz.ignore import IgnoreMatcher

try:  # pragma: no cover - import-only branch
    import zstandard as _zstandard  # type: ignore
except ImportError:  # pragma: no cover
    _zstandard = None


# ---------------------------------------------------------------------------
# Compression detection
# ---------------------------------------------------------------------------


def zstd_available() -> bool:
    return _zstandard is not None


def pick_compression(config: RuntimeConfig) -> tuple[str, str]:
    """Return ``(compression, suffix)``.

    ``compression`` is one of ``"zstd"`` or ``"gzip"``.
    """

    if config.use_zstd and zstd_available():
        return "zstd", ARCHIVE_SUFFIX_ZSTD
    return "gzip", ARCHIVE_SUFFIX_GZIP


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    abspath: Path
    relpath: str
    size: int
    is_symlink: bool


@dataclass
class WalkResult:
    files: list[FileEntry] = field(default_factory=list)
    total_bytes: int = 0
    ignored_count: int = 0
    large_files: list[FileEntry] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)


def walk(
    source: Path,
    matcher: IgnoreMatcher,
    *,
    large_file_bytes: int,
    follow_symlinks: bool,
    include_large: bool = False,
) -> WalkResult:
    """Enumerate everything under *source* respecting *matcher*.

    Symlinks are stored as symlinks (not followed) by default. Files
    larger than ``large_file_bytes`` are recorded but excluded from
    ``files`` unless ``include_large`` is set.
    """

    source = Path(source)
    result = WalkResult()

    # Use os.walk for predictable ordering (sort dirs/files for stable
    # output, useful when tests assert on archive contents).
    for dirpath, dirnames, filenames in os.walk(source, followlinks=follow_symlinks):
        rel_dir = os.path.relpath(dirpath, source)
        if rel_dir == ".":
            rel_dir = ""

        # Filter ignored sub-dirs in place; this prunes os.walk recursion.
        keep: list[str] = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if matcher.match(rel, is_dir=True):
                result.ignored_count += 1
                continue
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            abspath = Path(dirpath) / name
            try:
                stat = abspath.lstat()
            except OSError:
                result.ignored_count += 1
                continue

            is_symlink = bool(stat.st_mode & 0o170000 == 0o120000)
            if matcher.match(rel, is_dir=False):
                result.ignored_count += 1
                continue

            size = stat.st_size if not is_symlink else 0
            entry = FileEntry(
                abspath=abspath,
                relpath=rel,
                size=size,
                is_symlink=is_symlink,
            )

            if size > large_file_bytes and not include_large:
                result.large_files.append(entry)
                continue

            result.files.append(entry)
            result.total_bytes += size

    return result


def dry_run(
    source: Path,
    matcher: IgnoreMatcher,
    config: RuntimeConfig,
    *,
    include_large: bool = False,
) -> WalkResult:
    return walk(
        source,
        matcher,
        large_file_bytes=config.large_file_bytes,
        follow_symlinks=config.follow_symlinks,
        include_large=include_large,
    )


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


@dataclass
class PackResult:
    archive_path: Path
    bytes_written: int
    file_count: int
    total_bytes_in: int
    compression: str


ProgressCallback = Callable[[int, int, FileEntry], None]


@contextmanager
def _open_tar_writer(target: Path, compression: str) -> Iterator[tarfile.TarFile]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if compression == "zstd":
        if _zstandard is None:  # pragma: no cover - guarded by pick_compression
            raise RuntimeError("zstandard not available")
        cctx = _zstandard.ZstdCompressor(level=3, threads=-1)
        with open(target, "wb") as raw:
            with cctx.stream_writer(raw, closefd=False) as compressor:
                with tarfile.open(fileobj=compressor, mode="w|") as tar:
                    yield tar
    elif compression == "gzip":
        with tarfile.open(target, "w:gz", compresslevel=6) as tar:
            yield tar
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown compression: {compression}")


def pack(
    source: Path,
    target_path: Path,
    walk_result: WalkResult,
    *,
    config: RuntimeConfig,
    on_progress: Optional[ProgressCallback] = None,
) -> PackResult:
    """Materialise *walk_result* into a tar archive at *target_path*.

    The caller is expected to have already run :func:`dry_run` and
    confirmed with the user; we re-use its file list rather than
    walking twice.
    """

    compression, _ = pick_compression(config)
    total = walk_result.file_count

    with _open_tar_writer(target_path, compression) as tar:
        for index, entry in enumerate(walk_result.files, start=1):
            try:
                tar.add(str(entry.abspath), arcname=entry.relpath, recursive=False)
            except (FileNotFoundError, PermissionError):
                # File disappeared between dry-run and pack, or is
                # unreadable. Skip silently — the dry-run report is
                # the source of truth for what was *attempted*.
                continue
            if on_progress is not None:
                on_progress(index, total, entry)

    bytes_written = target_path.stat().st_size
    return PackResult(
        archive_path=target_path,
        bytes_written=bytes_written,
        file_count=walk_result.file_count,
        total_bytes_in=walk_result.total_bytes,
        compression=compression,
    )


# ---------------------------------------------------------------------------
# Unpacking (used by M3 restore; included now so api can call it)
# ---------------------------------------------------------------------------


@dataclass
class ArchiveMember:
    relpath: str
    size: int
    is_symlink: bool
    is_dir: bool


def list_archive_members(archive_path: Path) -> list[ArchiveMember]:
    """Read tar headers without extracting payload.

    Used by ``api.restore_estimate`` to diff archive contents against
    the current source tree.
    """

    suffix = "".join(archive_path.suffixes[-2:])
    members: list[ArchiveMember] = []

    def consume(tar: tarfile.TarFile) -> None:
        for member in tar:
            members.append(
                ArchiveMember(
                    relpath=member.name,
                    size=member.size if member.isfile() else 0,
                    is_symlink=member.issym() or member.islnk(),
                    is_dir=member.isdir(),
                )
            )

    if suffix == ARCHIVE_SUFFIX_ZSTD:
        if _zstandard is None:
            raise RuntimeError(
                "zstandard not installed; cannot read .tar.zst archive"
            )
        dctx = _zstandard.ZstdDecompressor()
        with open(archive_path, "rb") as raw:
            with dctx.stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    consume(tar)
    elif suffix == ARCHIVE_SUFFIX_GZIP:
        with tarfile.open(archive_path, "r:gz") as tar:
            consume(tar)
    else:
        raise ValueError(f"unsupported archive format: {archive_path.name}")

    return members


def unpack(archive_path: Path, target_dir: Path) -> int:
    """Extract *archive_path* into *target_dir*. Returns file count."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    suffix = "".join(archive_path.suffixes[-2:])
    # ``filter="tar"`` keeps full POSIX semantics (modes, symlinks) while
    # silencing the Python 3.14 deprecation around the default extraction
    # behaviour. We trust archives we created ourselves.
    extract_kwargs = {"set_attrs": True}
    if sys.version_info >= (3, 12):
        extract_kwargs["filter"] = "tar"

    if suffix == ARCHIVE_SUFFIX_ZSTD:
        if _zstandard is None:
            raise RuntimeError(
                "zstandard not installed; cannot read .tar.zst archive"
            )
        dctx = _zstandard.ZstdDecompressor()
        count = 0
        with open(archive_path, "rb") as raw:
            with dctx.stream_reader(raw) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    for member in tar:
                        tar.extract(member, path=target_dir, **extract_kwargs)
                        count += 1
        return count
    elif suffix == ARCHIVE_SUFFIX_GZIP:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                tar.extract(member, path=target_dir, **extract_kwargs)
            return len(members)
    else:
        raise ValueError(f"unsupported archive format: {archive_path.name}")
