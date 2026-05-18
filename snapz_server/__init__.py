"""Private snapz sync server.

The server package is intentionally separate from the end-user ``snapz``
CLI surface. It provides the ``snapz-server`` executable: HTTP sync API,
metadata database, object storage, and a small management web UI.
"""

from __future__ import annotations

__version__ = "0.4.0"
