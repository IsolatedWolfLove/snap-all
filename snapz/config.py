"""Configuration & global paths.

The plan pins the storage root to ``~/.snapz-all/``. We expose an env
override (``SNAPZ_ALL_ROOT``) primarily so tests can redirect the root to
a temporary directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path("~/.snapz-all").expanduser()
DEFAULT_LARGE_FILE_BYTES = 100 * 1024 * 1024  # 100 MiB
REGISTRY_FILENAME = "registry.json"
DIR_META_FILENAME = "_meta.json"
ARCHIVE_SUFFIX_ZSTD = ".tar.zst"
ARCHIVE_SUFFIX_GZIP = ".tar.gz"
META_SUFFIX = ".meta.json"
SOURCE_MARKER_FILENAME = ".snapz-id"


def _default_save_workers() -> int:
    fallback = max(1, min(8, os.cpu_count() or 1))
    raw = os.environ.get("SNAPZ_SAVE_WORKERS")
    if not raw:
        return fallback
    try:
        parsed = int(raw)
    except ValueError:
        return fallback
    return max(1, parsed)


def storage_root() -> Path:
    """Return the configured storage root, honoring ``SNAPZ_ALL_ROOT``."""

    override = os.environ.get("SNAPZ_ALL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_ROOT


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime knobs threaded through the API.

    Field defaults match the documented MVP behaviour. Tests construct
    a custom instance instead of mutating module globals.
    """

    root: Path = field(default_factory=storage_root)
    large_file_bytes: int = DEFAULT_LARGE_FILE_BYTES
    follow_symlinks: bool = False
    use_zstd: bool = True  # falls back to gzip automatically when missing
    apply_default_ignores: bool = True
    apply_gitignore: bool = True
    apply_snapzignore: bool = True
    use_file_cache: bool = True
    save_workers: int = field(default_factory=_default_save_workers)

    def with_root(self, root: Path) -> "RuntimeConfig":
        return RuntimeConfig(
            root=Path(root),
            large_file_bytes=self.large_file_bytes,
            follow_symlinks=self.follow_symlinks,
            use_zstd=self.use_zstd,
            apply_default_ignores=self.apply_default_ignores,
            apply_gitignore=self.apply_gitignore,
            apply_snapzignore=self.apply_snapzignore,
            use_file_cache=self.use_file_cache,
            save_workers=self.save_workers,
        )


def default_config() -> RuntimeConfig:
    """Build a :class:`RuntimeConfig` from current environment."""

    return RuntimeConfig()
