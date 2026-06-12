"""Shared helpers for snapshot list views."""

from __future__ import annotations

from snapz import api
from snapz.config import RuntimeConfig
from snapz.store import DirEntry


def is_pulled_remote_archive(entry: DirEntry) -> bool:
    return entry.archived and entry.key.startswith("remote-src_")


def alist_entries(config: RuntimeConfig) -> list[DirEntry]:
    return [
        entry
        for entry in api.list_all(config=config, include_archived=True)
        if not entry.archived or is_pulled_remote_archive(entry)
    ]
