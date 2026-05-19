"""Find paths across snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from snapz import cas
from snapz.config import RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store
from snapz._api_core import _source_path


# ---------------------------------------------------------------------------
# Find — locate paths across snapshots (v0.2)
# ---------------------------------------------------------------------------


@dataclass
class FindHit:
    """One ``(snapshot, path)`` pairing emitted by :func:`find`."""

    snapshot: SnapshotMeta
    path: str
    type: str                 # "file" | "symlink"
    size: Optional[int]
    sha256: Optional[str]
    target: Optional[str]     # symlinks only
    changed_from_prev: bool   # sha differs from the chronologically next-newer
                              # hit for this same path (False for the newest /
                              # for symlinks)


@dataclass
class FindResult:
    """Output of :func:`find` — grouped by source-relative path."""

    abspath: Path
    pattern: str
    by_path: dict[str, list[FindHit]]    # path -> hits, newest first

    @property
    def total_hits(self) -> int:
        return sum(len(v) for v in self.by_path.values())


def _match_path(pattern: str, path: str) -> bool:
    """Match *pattern* against a source-relative *path*.

    Supports:
    - exact match (``src/main.py``)
    - leading dir match (``src`` matches ``src/main.py``)
    - fnmatch wildcards (``*.py``, ``src/*.py``)
    - recursive wildcards (``**/*.py`` via :class:`pathlib.PurePosixPath`)
    """

    import fnmatch
    from pathlib import PurePosixPath

    if pattern == path:
        return True
    if "*" not in pattern and "?" not in pattern and "[" not in pattern:
        # Treat as a path/dir prefix.
        return path == pattern or path.startswith(pattern + "/")
    if "**" in pattern:
        try:
            return PurePosixPath(path).match(pattern)
        except (ValueError, TypeError):
            return False
    return fnmatch.fnmatchcase(path, pattern)


def find(
    path: str | Path,
    pattern: str,
    *,
    config: Optional[RuntimeConfig] = None,
    include_auto: bool = False,
) -> FindResult:
    """Locate every snapshot under *path* that contains *pattern*.

    *pattern* may be a literal path, a directory prefix, or an
    fnmatch / ``**`` glob. ``include_auto=True`` widens the scan to
    cover the auto-* safety snapshots (hidden from user-facing tools by
    default).

    Legacy ``.tar.zst`` snapshots are silently skipped — their content
    isn't indexed by manifest, so listing them would require unpacking
    every archive on every ``find`` call. CAS snapshots cover all
    new captures.
    """

    config = config or default_config()
    abspath = _source_path(path, config=config)
    store = Store(config)
    snaps_sorted = store.list_snapshots(abspath)

    by_path: dict[str, list[FindHit]] = {}
    for snap in snaps_sorted:
        if not include_auto and snap.name.startswith("auto-"):
            continue
        artifact = store.find_archive(abspath, snap.name)
        if artifact is None or not cas.is_manifest_artifact(artifact):
            continue
        try:
            manifest = cas.read_manifest(artifact)
        except (OSError, KeyError):
            continue
        for entry in manifest.entries:
            if entry.type not in ("file", "symlink"):
                continue
            if not _match_path(pattern, entry.path):
                continue
            by_path.setdefault(entry.path, []).append(FindHit(
                snapshot=snap,
                path=entry.path,
                type=entry.type,
                size=entry.size,
                sha256=entry.sha256,
                target=entry.target,
                changed_from_prev=False,    # filled in after grouping
            ))

    # Annotate "differs from chronologically next-newer hit". Since we
    # iterated snapshots newest-first, position 0 is newest; 1 differs
    # from 0 if its sha doesn't match.
    for hits in by_path.values():
        for i in range(1, len(hits)):
            prev = hits[i - 1]
            curr = hits[i]
            if curr.type == "file" and prev.type == "file":
                hits[i].changed_from_prev = (curr.sha256 != prev.sha256)
            elif curr.type == "symlink" and prev.type == "symlink":
                hits[i].changed_from_prev = (curr.target != prev.target)
            else:
                hits[i].changed_from_prev = True

    return FindResult(abspath=abspath, pattern=pattern, by_path=by_path)
