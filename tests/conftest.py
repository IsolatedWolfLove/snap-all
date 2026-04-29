"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from snapz.config import RuntimeConfig


@pytest.fixture
def snap_root(tmp_path: Path) -> Path:
    """A throw-away ``~/.snapz-all`` for each test."""

    root = tmp_path / "snapz-all"
    root.mkdir()
    return root


@pytest.fixture
def config(snap_root: Path) -> RuntimeConfig:
    return RuntimeConfig(root=snap_root)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A small project tree to snapshot.

    Layout::

        proj/
        ├── README.md
        ├── src/
        │   ├── main.py
        │   └── lib.py
        ├── data/
        │   └── input.txt
        └── .gitignore   (excludes ``ignored/``)
        ├── ignored/
        │   └── secret.bin
        └── __pycache__/
            └── x.pyc
    """

    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "ignored").mkdir()
    (root / "__pycache__").mkdir()

    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "data" / "input.txt").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    (root / "ignored" / "secret.bin").write_bytes(b"\x00\x01\x02")
    (root / "__pycache__" / "x.pyc").write_bytes(b"junk")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    return root
