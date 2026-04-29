"""Path filtering for snapshot creation.

We support a simplified ``gitignore``-style matcher:

- One pattern per line; blank lines and ``#`` comments are skipped.
- Trailing ``/`` restricts to directories.
- Patterns containing ``/`` are matched against the path relative to the
  source root; otherwise they match any path component.
- Negation (``!``) is **not** supported in MVP — keeping the matcher
  predictable and bug-free is more important here than feature parity.

This is intentionally simpler than the real git semantics. If the user
needs full gitignore behaviour we can swap in :mod:`pathspec` later
without changing callers.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_PATTERNS: tuple[str, ...] = (
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".venv/",
    "venv/",
    "node_modules/",
    "dist/",
    "build/",
    ".eggs/",
    "*.egg-info/",
    ".DS_Store",
    "Thumbs.db",
)


@dataclass(frozen=True)
class _Rule:
    """Compiled ignore pattern."""

    pattern: str
    dir_only: bool
    anchored: bool  # contains "/" -> match against rel path


def _compile(patterns: Iterable[str]) -> tuple[_Rule, ...]:
    rules: list[_Rule] = []
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        anchored = "/" in line
        line = line.lstrip("/")
        if not line:
            continue
        rules.append(_Rule(pattern=line, dir_only=dir_only, anchored=anchored))
    return tuple(rules)


@dataclass
class IgnoreMatcher:
    """Decides whether a relative path should be excluded."""

    rules: tuple[_Rule, ...] = field(default_factory=tuple)

    def match(self, rel_path: str, is_dir: bool) -> bool:
        """Return True if *rel_path* should be excluded."""

        rel_path = rel_path.replace("\\", "/").lstrip("./")
        if not rel_path:
            return False
        parts = rel_path.split("/")
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.anchored:
                if fnmatch.fnmatch(rel_path, rule.pattern):
                    return True
            else:
                if any(fnmatch.fnmatch(part, rule.pattern) for part in parts):
                    return True
        return False

    def extended(self, more: Sequence[str]) -> "IgnoreMatcher":
        return IgnoreMatcher(rules=self.rules + _compile(more))


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return text.splitlines()


def build_matcher(
    source: Path,
    *,
    apply_defaults: bool = True,
    apply_gitignore: bool = True,
    apply_snapzignore: bool = True,
    extra_patterns: Sequence[str] = (),
    local_excludes_path: Path | None = None,
) -> IgnoreMatcher:
    """Assemble an :class:`IgnoreMatcher` for snapshotting *source*.

    The order is: defaults → ``.gitignore`` (root) → ``.snapzignore``
    (root) → ``local_excludes_path`` (per-store opt-out list, never
    committed to the source repo) → caller-supplied extras.
    Sub-directory ``.gitignore`` files are intentionally not walked in
    MVP — patterns at the source root cover the vast majority of
    real-world cases.
    """

    patterns: list[str] = []
    if apply_defaults:
        patterns.extend(DEFAULT_PATTERNS)
    if apply_gitignore:
        patterns.extend(_read_lines(source / ".gitignore"))
        patterns.extend(_read_lines(source / ".git" / "info" / "exclude"))
    if apply_snapzignore:
        patterns.extend(_read_lines(source / ".snapzignore"))
    if local_excludes_path is not None:
        patterns.extend(_read_lines(local_excludes_path))
    patterns.extend(extra_patterns)

    # Always exclude the storage root itself if the user happens to be
    # running ``snapz`` over their home directory.
    patterns.append(".snapz-all/")

    return IgnoreMatcher(rules=_compile(patterns))
