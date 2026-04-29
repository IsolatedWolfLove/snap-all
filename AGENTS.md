# Repository Guidelines

## Project Structure & Module Organization

`snapz/` contains the Python package for the `snapz` CLI. Important modules include `cli.py` for command parsing, `api.py` for public operations, `store.py` and `cas.py` for snapshot storage, `archive.py` for packing/restoring, and `tui.py` for curses UI flows. Tests live in `tests/` and mirror feature areas with files such as `test_cli.py`, `test_restore.py`, and `test_cas.py`. Release automation is in `scripts/build.sh`; generated artifacts go to `dist/` and build scratch space uses `.build-venv/` and `build/`.

## Build, Test, and Development Commands

- `python3 -m venv .venv && .venv/bin/pip install -e .[dev]`: create a local editable development environment.
- `.venv/bin/pytest`: run the full test suite using the `pyproject.toml` pytest settings.
- `.venv/bin/pytest tests/test_cli.py`: run a focused test file while iterating.
- `.venv/bin/python -m snapz --help`: exercise the CLI from the working tree.
- `./scripts/build.sh all`: clean, build wheel/sdist plus `dist/snapz.pyz`, then smoke-test artifacts.
- `./scripts/build.sh smoke`: validate existing release artifacts without rebuilding.

## Coding Style & Naming Conventions

Target Python 3.10+. Use 4-space indentation, `from __future__ import annotations` in Python modules, `pathlib.Path` for filesystem paths, and typed dataclasses or small helpers where they clarify data flow. Keep CLI I/O in `snapz/cli.py`; shared behavior belongs in `api.py`, storage modules, or utilities. Test functions use `test_<behavior>` names, and fixtures should stay deterministic by isolating `SNAPZ_ALL_ROOT`.

## Testing Guidelines

Pytest is the test framework. Add or update tests for every behavior change, especially destructive operations such as restore, revert, prune, delete, and undo. Prefer temporary directories and monkeypatched environment variables over touching the real `~/.snapz-all`. Use `pytest-cov` when checking coverage locally, for example `.venv/bin/pytest --cov=snapz`.

## Commit & Pull Request Guidelines

This checkout does not include Git history, so use concise imperative commit subjects such as `Add JSON output for stats` or `Fix restore cleanup ordering`. Pull requests should describe the user-visible change, list tests run, link related issues, and include terminal output or screenshots for TUI-facing changes when useful.

## Security & Configuration Tips

Do not commit generated artifacts, caches, virtual environments, or local snapshot stores. `.gitignore` already excludes `dist/`, `build/`, `.venv/`, `.build-venv/`, `__pycache__/`, coverage output, and pytest caches. Keep destructive CLI paths behind dry-run, confirmation, or `-y` test coverage.
