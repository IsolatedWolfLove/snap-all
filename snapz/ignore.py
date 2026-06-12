"""Path filtering for snapshot creation.

The matcher prefers :mod:`pathspec`'s ``gitwildmatch`` implementation so
``.gitignore`` / ``.snapzignore`` support negation and nested ignore
files. A small fallback keeps the module importable in incomplete dev
environments, but release installs depend on ``pathspec``.
"""

from __future__ import annotations

import fnmatch
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # pragma: no cover - dependency availability is environment-level
    from pathspec.patterns.gitwildmatch import GitWildMatchPattern
except ImportError:  # pragma: no cover
    GitWildMatchPattern = None  # type: ignore[assignment]

DEFAULT_PATTERNS: tuple[str, ...] = (
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "log/",
    "logs/",
    "*.log",
    ".cache/",
    ".parcel-cache/",
    ".turbo/",
    ".yarn/cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".venv/",
    "venv/",
    "node_modules/",
    "dist/",
    "build/",
    "install/",
    ".eggs/",
    "*.egg-info/",
    ".snapz-id",
    ".DS_Store",
    "Thumbs.db",
)


@dataclass(frozen=True)
class _Rule:
    """Compiled ignore pattern."""

    pattern: str
    dir_only: bool
    anchored: bool  # contains "/" -> match against rel path
    include: bool = True


@dataclass(frozen=True)
class _SpecGroup:
    base: str
    patterns: tuple[Any, ...]


def _compile(patterns: Iterable[str]) -> tuple[_Rule, ...]:
    rules: list[_Rule] = []
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        include = True
        if line.startswith("!"):
            include = False
            line = line[1:].strip()
            if not line:
                continue
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        anchored = "/" in line
        line = line.lstrip("/")
        if not line:
            continue
        rules.append(_Rule(
            pattern=line, dir_only=dir_only, anchored=anchored, include=include,
        ))
    return tuple(rules)


def _compile_spec(lines: Iterable[str]) -> tuple[Any, ...]:
    if GitWildMatchPattern is None:
        return ()
    patterns: list[Any] = []
    for raw in lines:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                pat = GitWildMatchPattern(raw)
        except Exception:  # nosec B112
            continue
        if getattr(pat, "include", None) is not None:
            patterns.append(pat)
    return tuple(patterns)


@dataclass
class IgnoreMatcher:
    """Decides whether a relative path should be excluded."""

    rules: tuple[_Rule, ...] = field(default_factory=tuple)
    spec_groups: tuple[_SpecGroup, ...] = field(default_factory=tuple)

    def match(self, rel_path: str, is_dir: bool) -> bool:
        """Return True if *rel_path* should be excluded."""

        rel_path = rel_path.replace("\\", "/")
        while rel_path.startswith("./"):
            rel_path = rel_path[2:]
        if not rel_path:
            return False
        if self.spec_groups:
            ignored = False
            for group in self.spec_groups:
                base = group.base
                if base:
                    if rel_path == base:
                        local = ""
                    elif rel_path.startswith(base + "/"):
                        local = rel_path[len(base) + 1:]
                    else:
                        continue
                else:
                    local = rel_path
                if not local:
                    local = "."
                candidate = local.rstrip("/") + ("/" if is_dir else "")
                ignored = _apply_patterns(
                    group.patterns,
                    candidate,
                    local,
                    is_dir,
                    ignored,
                )
            return ignored

        ignored = False
        for rule in self.rules:
            if _matches_rule(rule, rel_path, is_dir):
                ignored = rule.include
        return ignored

    def match_dir_early(self, rel_path: str) -> bool:
        """Return True when *rel_path* can be safely pruned as a directory.

        This is deliberately conservative. If any negation pattern could
        re-include something below the directory, the caller should descend
        and let :meth:`match` decide per path.
        """

        rel_path = rel_path.replace("\\", "/").strip("/")
        if not rel_path:
            return False

        if self.spec_groups:
            if not self.match(rel_path, is_dir=True):
                return False
            for group in self.spec_groups:
                local = _local_for_group(group.base, rel_path)
                if local is not None:
                    if _has_negation_below(group.patterns, local):
                        return False
                    continue
                if group.base and (
                    rel_path == group.base or group.base.startswith(rel_path + "/")
                ):
                    if _has_negation_below(group.patterns, "."):
                        return False
            return True
        if self.match(rel_path, is_dir=True):
            return not _has_negation_below(self.rules, rel_path)
        return False

    def extended(self, more: Sequence[str]) -> "IgnoreMatcher":
        groups = self.spec_groups
        rules = self.rules + _compile(more)
        if GitWildMatchPattern is not None:
            compiled = _compile_spec(more)
            if compiled:
                groups = groups + (_SpecGroup(base="", patterns=compiled),)
                return IgnoreMatcher(rules=rules, spec_groups=groups)
        compiled = _compile(more)
        if compiled:
            groups = groups + (_SpecGroup(base="", patterns=compiled),)
        return IgnoreMatcher(
            rules=rules,
            spec_groups=groups,
        )


def _local_for_group(base: str, rel_path: str) -> str | None:
    if base:
        if rel_path == base:
            return "."
        if rel_path.startswith(base + "/"):
            return rel_path[len(base) + 1:] or "."
        return None
    return rel_path or "."


def _has_negation_below(patterns: Iterable[Any], rel_path: str) -> bool:
    rel = rel_path.strip("/")
    prefix = "" if rel == "." else rel + "/"
    for pattern in patterns:
        include = getattr(pattern, "include", None)
        if include is not False:
            continue
        raw = getattr(pattern, "pattern", "")
        raw = str(raw).lstrip("!").lstrip("/")
        if not raw:
            return True
        if "/" not in raw:
            return True
        if raw.startswith(prefix):
            return True
    return False


def _apply_patterns(
    patterns: tuple[Any, ...],
    candidate: str,
    rel_path: str,
    is_dir: bool,
    ignored: bool,
) -> bool:
    for pattern in patterns:
        if isinstance(pattern, _Rule):
            matched = (
                pattern.include
                if _matches_rule(pattern, rel_path, is_dir)
                else None
            )
        else:
            try:
                matched = pattern.match_file(candidate)
            except Exception:
                matched = None
        if matched is not None:
            ignored = bool(pattern.include)
    return ignored


def _matches_rule(rule: _Rule, rel_path: str, is_dir: bool) -> bool:
    if rule.dir_only and not is_dir:
        return False
    rel_path = rel_path.strip("/") or "."
    parts = rel_path.split("/")
    if rule.anchored:
        return fnmatch.fnmatch(rel_path, rule.pattern)
    return any(fnmatch.fnmatch(part, rule.pattern) for part in parts)


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return text.splitlines()


def _nested_ignore_groups(source: Path) -> list[tuple[str, list[str]]]:
    groups: list[tuple[str, list[str]]] = []
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        base_path = Path(dirpath)
        rel = "" if base_path == source else base_path.relative_to(source).as_posix()
        for filename in (".gitignore", ".snapzignore"):
            if filename not in filenames:
                continue
            if rel == "":
                continue
            lines = _read_lines(base_path / filename)
            if lines:
                groups.append((rel, lines))
    return groups


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
    committed to the source repo) → caller-supplied extras. Nested
    ``.gitignore`` and ``.snapzignore`` files are scoped to the directory
    that contains them.
    """

    patterns: list[str] = []
    groups: list[_SpecGroup] = []

    def add_group(base: str, lines: Sequence[str]) -> None:
        patterns.extend(lines if base == "" else ())
        compiled = _compile_spec(lines)
        if compiled:
            groups.append(_SpecGroup(base=base, patterns=compiled))
            return
        fallback = _compile(lines)
        if fallback:
            groups.append(_SpecGroup(base=base, patterns=fallback))

    if apply_defaults:
        add_group("", list(DEFAULT_PATTERNS))
    if apply_gitignore:
        add_group("", _read_lines(source / ".gitignore"))
        add_group("", _read_lines(source / ".git" / "info" / "exclude"))
    if apply_snapzignore:
        add_group("", _read_lines(source / ".snapzignore"))
    if apply_gitignore or apply_snapzignore:
        for base, lines in _nested_ignore_groups(source):
            add_group(base, lines)
    if local_excludes_path is not None:
        add_group("", _read_lines(local_excludes_path))
    add_group("", list(extra_patterns))

    # Always exclude the storage root itself if the user happens to be
    # running ``snapz`` over their home directory.
    add_group("", [".snapz-all/"])

    return IgnoreMatcher(rules=_compile(patterns), spec_groups=tuple(groups))
