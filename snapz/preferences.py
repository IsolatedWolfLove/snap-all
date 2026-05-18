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

UI_MODE_TUI = "tui"
UI_MODE_MINIMAL = "minimal"


# Whitelist of known config keys. Setting unknown keys is rejected so
# typos don't silently disable behaviour, mirroring ``conda config``.
#
# Each spec carries a value type + default + one-liner help string.
KNOWN_CONFIG_KEYS: dict[str, dict[str, Any]] = {
    "ui_mode": {
        "type": "str",
        "default": UI_MODE_TUI,
        "help": (
            "UI interaction mode: ``tui`` (default, interactive curses selectors) "
            "or ``minimal`` (plain text, no TUI prompts)."
        ),
        "choices": (UI_MODE_TUI, UI_MODE_MINIMAL),
    },
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
        "choices": ("auto", "always", "never"),
    },
    "retention.keep_last": {
        "type": "int",
        "default": 0,
        "help": "Default ``prune --keep-last`` value; 0 disables the rule.",
    },
    "retention.keep_daily": {
        "type": "int",
        "default": 0,
        "help": "Default ``prune --keep-daily`` value; 0 disables the rule.",
    },
    "retention.keep_weekly": {
        "type": "int",
        "default": 0,
        "help": "Default ``prune --keep-weekly`` value; 0 disables the rule.",
    },
    "retention.keep_within_days": {
        "type": "int",
        "default": 0,
        "help": "Default ``prune --keep-within-days`` value; 0 disables the rule.",
    },
    "retention.auto_prune_after_save": {
        "type": "bool",
        "default": False,
        "help": "When true, apply configured retention rules after each save.",
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
    if spec["type"] == "int":
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"expected integer for {key!r}, got: {value!r}") from None
        if parsed < 0:
            raise ValueError(f"expected non-negative integer for {key!r}")
        return parsed
    parsed = str(value)
    choices = spec.get("choices")
    if choices is not None and parsed not in choices:
        opts = ", ".join(str(c) for c in choices)
        raise ValueError(f"expected one of {opts} for {key!r}, got: {value!r}")
    return parsed


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


def get_ui_mode(root: Path) -> str:
    """Return the configured UI mode, defaulting to :data:`UI_MODE_TUI`.

    Reads ``ui_mode`` from the store config.  If the stored value is not one
    of the recognised modes (e.g. a hand-edited config with a typo) the
    default ``"tui"`` is returned so the tool stays usable.
    """

    value = get_config_value(root, "ui_mode")
    if value in (UI_MODE_TUI, UI_MODE_MINIMAL):
        return value
    return UI_MODE_TUI


def set_ui_mode(root: Path, mode: str) -> None:
    """Persist *mode* as the ``ui_mode`` config value.

    Raises :exc:`ValueError` if *mode* is not one of the recognised values.
    """

    set_config_value(root, "ui_mode", mode)


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

