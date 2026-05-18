"""Fast per-source file hash cache for repeated saves.

The cache maps source-relative paths to file identity data plus the
previously computed sha256. It is an optimization only: malformed files,
stale entries, and missing data are ignored so snapshot correctness still
comes from the manifest/blob verification paths.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from snapz.archive import FileEntry

CACHE_FILENAME = "_filecache.json"
CACHE_VERSION = 1


@dataclass(frozen=True)
class CacheEntry:
    size: int
    mtime: float
    inode: int
    sha256: str


def cache_path(dir_root: Path) -> Path:
    return Path(dir_root) / CACHE_FILENAME


def load(dir_root: Path) -> dict[str, CacheEntry]:
    """Read the cache for *dir_root*.

    Returns an empty dict on missing/corrupt input. That keeps the cache
    strictly best-effort and avoids blocking a save on an optimization.
    """

    path = cache_path(dir_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}

    items = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}

    out: dict[str, CacheEntry] = {}
    for relpath, data in items.items():
        if not isinstance(relpath, str) or not isinstance(data, dict):
            continue
        try:
            sha = str(data["sha256"])
            if len(sha) != 64:
                continue
            out[relpath] = CacheEntry(
                size=int(data["size"]),
                mtime=float(data["mtime"]),
                inode=int(data["inode"]),
                sha256=sha,
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save(dir_root: Path, entries: dict[str, CacheEntry]) -> None:
    """Persist *entries* atomically under *dir_root*."""

    dir_root = Path(dir_root)
    dir_root.mkdir(parents=True, exist_ok=True)
    path = cache_path(dir_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": CACHE_VERSION,
        "files": {
            relpath: asdict(entry)
            for relpath, entry in sorted(entries.items())
        },
    }
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def lookup(
    cache: dict[str, CacheEntry],
    entry: FileEntry,
    stat_info: os.stat_result,
) -> str | None:
    """Return the cached sha256 if file identity still matches."""

    cached = cache.get(entry.relpath)
    if cached is None:
        return None
    inode = int(getattr(stat_info, "st_ino", 0))
    if (
        cached.size == stat_info.st_size
        and cached.mtime == stat_info.st_mtime
        and cached.inode == inode
    ):
        return cached.sha256
    return None


def remove(dir_root: Path) -> bool:
    """Delete the cache file if present."""

    path = cache_path(dir_root)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
