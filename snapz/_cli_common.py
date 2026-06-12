"""Shared helpers and constants for the snapz CLI."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tarfile
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from snapz import __version__, api, archive, events, preferences, remote
from snapz import style as st
from snapz.archive import FileEntry, WalkResult
from snapz.config import RuntimeConfig, default_config
from snapz.i18n import t
from snapz.store import DirEntry, SnapshotMeta, Store
from snapz.util import (
    auto_name,
    format_duration,
    format_iso,
    format_size,
    is_auto_snapshot,
    resolve_path,
    validate_snapshot_name,
)

EXIT_OK = 0
EXIT_USER_ABORT = 130
EXIT_ERROR = 1

SNAPZ_PACKAGE_NAME = "snapz-cli"
SNAPZ_GITHUB_REPO = "https://github.com/IsolatedWolfLove/snap-all.git"
SNAPZ_GITHUB_INSTALL_TARGET = (
    f"{SNAPZ_PACKAGE_NAME}[zstd] @ git+{SNAPZ_GITHUB_REPO}"
)

SUGGEST_EXCLUDE_DIRS = {
    ".cache",
    ".parcel-cache",
    ".turbo",
    "coverage",
    "tmp",
}
SUGGEST_EXCLUDE_SUFFIXES = {
    ".7z",
    ".avi",
    ".bak",
    ".bz2",
    ".dmg",
    ".gz",
    ".iso",
    ".log",
    ".mov",
    ".mp4",
    ".tar",
    ".tgz",
    ".webm",
    ".xz",
    ".zip",
    ".zst",
}

def _confirm(prompt: str, *, default_yes: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    # ``y``/``n`` are kept regardless of locale so input parsing is
    # uniform; only the visual hint can be translated.
    marker = t("confirm.default_yes" if default_yes else "confirm.default_no")
    if marker == "[Y/n]":
        suffix = f" {st.dim('[')}{st.bold('Y')}{st.dim('/n]')} "
    elif marker == "[y/N]":
        suffix = f" {st.dim('[y/')}{st.bold('N')}{st.dim(']')} "
    else:
        suffix = f" {st.dim(marker)} "
    try:
        answer = input(prompt + suffix).strip().lower()
    except EOFError:
        return False
    if not answer:
        return default_yes
    return answer in {"y", "yes"}

def _prompt(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" {st.dim('[' + default + ']')} " if default else " "
    try:
        value = input(prompt + suffix).strip()
    except EOFError:
        return default or ""
    return value or (default or "")

def _print_error(msg: str) -> None:
    print(f"{st.err_prefix()} {msg}", file=sys.stderr)

def _looks_binary(data: bytes) -> bool:
    """Cheap binary heuristic for terminal-safe previews/output."""

    return b"\x00" in data[:8192]

def _path_total_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for filename in filenames:
            try:
                total += (Path(dirpath) / filename).lstat().st_size
            except OSError:
                continue
    return total

def _format_data_size(num_bytes: int) -> str:
    return f"{format_size(num_bytes)} ({num_bytes / (1024 ** 3):.2f} GB)"

def _run_pip(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "pip", *args],
        check=False,
        text=True,
    )

def _delete_data_root(root: Path) -> bool:
    root = Path(root).expanduser()
    if not root.exists():
        return False
    resolved = root.resolve()
    unsafe_roots = {Path("/").resolve(), Path.home().resolve()}
    if resolved in unsafe_roots:
        raise ValueError(t("uninstall.refuse_delete_root", path=resolved))
    shutil.rmtree(resolved)
    return True

def _json_default(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")

def _emit_json(payload: Any) -> None:
    """Serialise *payload* to stdout. Dataclasses and ``Path`` are handled
    transparently so call sites can pass api result objects directly."""

    text = json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default)
    sys.stdout.write(text + "\n")

def _wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))

def _filter_user_visible(snaps: Iterable[SnapshotMeta], *, show_auto: bool) -> list[SnapshotMeta]:
    """Drop ``auto-*`` snapshots unless the caller opted into them."""

    rows = list(snaps)
    if show_auto:
        return rows
    return [s for s in rows if not is_auto_snapshot(s.name)]

def _pluralize(n: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if n == 1 else (plural or singular + 's')

def _kv(label: str, value: str, *, indent: int = 2, label_w: int = 11) -> str:
    pad = ' ' * indent
    return f"{pad}{st.label(label.ljust(label_w))} {value}"

def _stdout_is_tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()

def _resolve_snapshot_name(
    path: Path,
    given: Optional[str],
    config: RuntimeConfig,
    *,
    title_key: str,
    show_auto: bool = False,
) -> Optional[str]:
    """Pick a snapshot name interactively when *given* is missing.

    Returns the resolved name, or ``None`` to indicate the caller should
    treat this as an aborted/erroring command — *_print_error* is
    already called for the non-TTY case, so the caller just has to
    return the right exit code.

    The picker hides ``auto-*`` safety snapshots by default; pass
    ``show_auto=True`` (e.g. for ``rm --all``) to expose them so the
    user can still clean them up by hand if needed.
    """

    if given:
        return given
    if not _stdout_is_tty():
        _print_error(t("picker.no_name_given"))
        return None
    snaps = api.list_snapshots(path, config=config)
    visible = _filter_user_visible(snaps, show_auto=show_auto)
    if not visible:
        if snaps and not show_auto:
            _print_error(t("status.hidden_auto", n=len(snaps)).strip())
        else:
            _print_error(t("picker.no_snapshots_in", path=path))
        return None
    from snapz import tui
    chosen = tui.run_snapshot_picker(
        visible,
        title=t(title_key, path=str(path)),
    )
    if not chosen:
        print(st.muted(t("picker.cancelled")))
        return None
    return chosen

def _print_path_preview(label: str, paths: list[str], *, warn: bool = False) -> None:
    if not paths:
        return
    renderer = st.warn if warn else st.path
    shown = paths[:5]
    print(f"  {st.muted(label)}")
    for rel in shown:
        print(f"    {renderer(rel)}")
    remaining = len(paths) - len(shown)
    if remaining > 0:
        print(f"    {st.muted(t('restore.more_paths', n=remaining))}")

def _emit_abort() -> None:
    """Best-effort ``aborted.`` notice that survives a second SIGINT.

    A user mashing Ctrl-C can deliver another SIGINT *while* we're
    printing the notice; without this guard the second one bubbles all
    the way out and the bootloader (or shell) reports an "unhandled
    exception" instead of a clean exit.
    """

    try:
        sys.stderr.write("\n" + t("status.aborted") + "\n")
        sys.stderr.flush()
    except (KeyboardInterrupt, BrokenPipeError, OSError):
        pass


__all__ = [
    "Any", "Iterable", "Optional", "Path", "argparse", "getpass", "json",
    "os", "shutil", "subprocess", "sys", "tarfile", "time", "datetime",
    "__version__", "api", "archive", "events", "preferences", "remote", "st",
    "FileEntry", "WalkResult", "RuntimeConfig", "default_config", "t",
    "DirEntry", "SnapshotMeta", "Store", "auto_name", "format_duration",
    "format_iso", "format_size", "is_auto_snapshot", "resolve_path",
    "validate_snapshot_name", "EXIT_OK", "EXIT_USER_ABORT", "EXIT_ERROR",
    "SNAPZ_PACKAGE_NAME", "SNAPZ_GITHUB_REPO", "SNAPZ_GITHUB_INSTALL_TARGET",
    "SUGGEST_EXCLUDE_DIRS", "SUGGEST_EXCLUDE_SUFFIXES", "_confirm", "_prompt",
    "_print_error", "_looks_binary", "_path_total_bytes", "_format_data_size",
    "_run_pip", "_delete_data_root", "_json_default", "_emit_json",
    "_wants_json", "_filter_user_visible", "_pluralize", "_kv",
    "_stdout_is_tty", "_resolve_snapshot_name", "_print_path_preview",
    "_emit_abort",
]
