"""snapz: lightweight directory snapshot CLI.

Public API entry points live in :mod:`snapz.api`.
"""

__version__ = "0.2.0"

from snapz.api import (
    add_local_excludes,
    delete,
    diff,
    export,
    gc,
    list_all,
    list_snapshots,
    rename,
    restore,
    restore_estimate,
    save,
    show,
)

__all__ = [
    "__version__",
    "save",
    "list_snapshots",
    "list_all",
    "delete",
    "rename",
    "show",
    "restore",
    "restore_estimate",
    "export",
    "gc",
    "diff",
    "add_local_excludes",
]
