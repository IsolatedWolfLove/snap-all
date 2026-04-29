"""Misc helpers shared across modules."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

# Snapshot names: alphanumerics, dot, dash, underscore. Must not start with a
# dot to keep listings free of hidden entries, and must not contain path
# separators.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def resolve_path(arg: str | Path) -> Path:
    """Expand ``~`` and resolve ``.``/``..``/relative paths to absolute."""

    return Path(arg).expanduser().resolve()


def compute_key(abspath: Path) -> str:
    """Return the on-disk key for a target directory.

    Format: ``<sha1[:12]>-<basename>``. Basename is sanitised so the
    folder is always a valid filename across filesystems.
    """

    abspath = Path(abspath)
    digest = hashlib.sha1(str(abspath).encode("utf-8")).hexdigest()[:12]
    base = abspath.name or "root"
    safe_base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:48]
    if not safe_base:
        safe_base = "root"
    return f"{digest}-{safe_base}"


def validate_snapshot_name(name: str) -> str:
    """Return *name* if valid, otherwise raise ``ValueError``."""

    if not name:
        raise ValueError("snapshot name must not be empty")
    if not _NAME_RE.match(name):
        raise ValueError(
            "invalid snapshot name: only [A-Za-z0-9._-] are allowed and the "
            "name must start with an alphanumeric character"
        )
    return name


def auto_name(now: datetime | None = None) -> str:
    """Generate a default timestamped snapshot name."""

    now = now or datetime.now()
    return now.strftime("auto-%Y%m%d-%H%M%S")


# ``auto-`` covers both timestamped auto-saves (``auto-YYYYMMDD-HHMMSS``)
# and the pre-op safety snapshots (``auto-pre-restore-…`` /
# ``auto-pre-revert-…``). User-facing listings hide these by default; the
# undo subsystem reads them through the ``UNDO_PREFIXES`` filter below.
_AUTO_PREFIX = "auto-"
UNDO_PREFIXES: tuple[str, ...] = ("auto-pre-restore-", "auto-pre-revert-")


def is_auto_snapshot(name: str) -> bool:
    """Return True for snapshots created automatically by snapz itself.

    These are hidden from user-facing listings/pickers by default — they
    exist purely to power ``snapz undo`` and the safety-net rollback
    chain. Pass ``--all`` (or call ``api.list_snapshots`` directly) to
    see them.
    """

    return name.startswith(_AUTO_PREFIX)


def is_undo_snapshot(name: str) -> bool:
    """Return True for the pre-op safety snapshots that ``snapz undo``
    can roll back to.
    """

    return name.startswith(UNDO_PREFIXES)


def format_size(num_bytes: int | float) -> str:
    """Pretty-print a byte count."""

    if num_bytes < 0:
        return f"-{format_size(-num_bytes)}"
    size = float(num_bytes)
    for unit in _SIZE_UNITS:
        if size < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """Pretty-print a duration in seconds."""

    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{int(sec):02d}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h{minutes:02d}m"


def format_iso(value: str | datetime) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM``."""

    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
    return dt.strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    """Current local time, ISO-formatted to second precision."""

    return datetime.now().replace(microsecond=0).isoformat()
