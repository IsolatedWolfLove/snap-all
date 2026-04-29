"""User-editable preferences and per-source-dir local excludes.

Two storage areas are managed here:

1. **Global config** at ``<store_root>/_config.json`` — small JSON dict
   with predefined keys (see :data:`KNOWN_CONFIG_KEYS`). Surfaced via
   ``snapz config get|set|unset|list``.
2. **Local excludes** at ``<store_root>/<key>/_local_excludes`` —
   gitignore-style patterns specific to one source directory. Loaded
   by :func:`snapz.ignore.build_matcher` and never committed to the
   user's project (the file lives entirely under ``~/.snapz-all/``).

Both are intentionally tiny formats: human-readable, hand-editable,
and forward-compatible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

LOCAL_EXCLUDES_FILENAME = "_local_excludes"
CONFIG_FILENAME = "_config.json"


# Whitelist of known config keys. Setting unknown keys is rejected so
# typos don't silently disable behaviour, mirroring ``conda config``.
#
# Each spec carries a value type + default + one-liner help string.
KNOWN_CONFIG_KEYS: dict[str, dict[str, Any]] = {
    "save_picker": {
        "type": "bool",
        "default": False,
        "help": (
            "When true, ``snapz save`` opens an interactive picker after "
            "walking the tree so you can add large files / directories "
            "to the local excludes list."
        ),
    },
    "color": {
        "type": "str",
        "default": "auto",
        "help": (
            "Color output mode: ``auto`` (TTY-detect, default), "
            "``always``, or ``never``."
        ),
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


_BOOL_TRUE = frozenset({"true", "1", "yes", "on", "y"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off", "n"})


def _coerce(key: str, value: str | bool) -> Any:
    spec = KNOWN_CONFIG_KEYS.get(key)
    if spec is None:
        raise KeyError(f"unknown config key: {key!r}")
    if spec["type"] == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
        raise ValueError(f"expected bool for {key!r}, got: {value!r}")
    return str(value)


def config_path(root: Path) -> Path:
    return root / CONFIG_FILENAME


def load_config(root: Path) -> dict[str, Any]:
    """Read the on-disk config; missing/malformed file yields ``{}``."""

    path = config_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(root: Path, data: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = config_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def get_config_value(root: Path, key: str) -> Any:
    """Effective value for *key*: on-disk override or registered default."""

    data = load_config(root)
    if key in data:
        return data[key]
    spec = KNOWN_CONFIG_KEYS.get(key)
    if spec is None:
        raise KeyError(f"unknown config key: {key!r}")
    return spec["default"]


def set_config_value(root: Path, key: str, value: str | bool) -> Any:
    parsed = _coerce(key, value)
    data = load_config(root)
    data[key] = parsed
    save_config(root, data)
    return parsed


def unset_config_value(root: Path, key: str) -> bool:
    data = load_config(root)
    if key not in data:
        return False
    del data[key]
    save_config(root, data)
    return True


def effective_config(root: Path) -> dict[str, Any]:
    """Defaults overlaid with on-disk overrides — what code actually sees."""

    out = {k: spec["default"] for k, spec in KNOWN_CONFIG_KEYS.items()}
    out.update(load_config(root))
    return out


# ---------------------------------------------------------------------------
# Local excludes (per source dir)
# ---------------------------------------------------------------------------


def local_excludes_path(dir_root: Path) -> Path:
    return dir_root / LOCAL_EXCLUDES_FILENAME


def read_local_excludes(dir_root: Path) -> list[str]:
    """Return all non-empty, non-comment patterns currently stored."""

    path = local_excludes_path(dir_root)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def append_local_excludes(dir_root: Path, patterns: Iterable[str]) -> int:
    """Append *patterns* (skipping duplicates and empty lines).

    Returns the number of new entries actually written. Creates the
    file with a leading comment header if it didn't exist before.
    """

    cleaned = [p.strip() for p in patterns if p and p.strip()]
    if not cleaned:
        return 0

    dir_root.mkdir(parents=True, exist_ok=True)
    existing = set(read_local_excludes(dir_root))
    new = [p for p in cleaned if p not in existing]
    if not new:
        return 0

    path = local_excludes_path(dir_root)
    write_header = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if write_header:
            f.write(
                "# snapz local excludes — gitignore-style patterns,\n"
                "# applied on top of .gitignore / .snapzignore for this dir only.\n"
                "# Edit by hand or via ``snapz diff --tui`` / save picker.\n"
            )
        for pat in new:
            f.write(pat + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # De-dup added count against pre-existing.
    return len(new)


