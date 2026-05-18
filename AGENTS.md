# Repository Guidelines

## Project Structure & Module Organization

`snapz/` contains the main Python CLI package and shared snapshot logic. `snapz_server/` contains the standalone sync/admin server. Tests live in `tests/` and follow the package features closely, with shared fixtures in `tests/conftest.py`. `scripts/build.sh` owns release packaging. `docs/` and `README*.md` contain user documentation. `web/vue-vben-admin-snapz/` is a drop-in Vue Vben Admin module, not a standalone app in this repository.

## Build, Test, and Development Commands

Create a local development environment with:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
```

Run the test suite with `python3 -m pytest`; pytest is configured in `pyproject.toml` to discover `tests/test_*.py` and run quietly. Build release artifacts with `./scripts/build.sh all`, which produces the wheel, sdist, and `snapz.pyz` / `snapz-server.pyz` in `dist/`. Use `./scripts/build.sh smoke` to validate built artifacts and `./scripts/build.sh --clean` to remove build outputs.

## Coding Style & Naming Conventions

Target Python 3.10+. Use 4-space indentation, type hints where they clarify public contracts, and `from __future__ import annotations` in new Python modules to match existing files. Keep CLI-facing code in `snapz/cli.py` or `snapz_server/cli.py`; keep reusable behavior in API/store/archive/helper modules. Name tests `test_<feature>.py` and test functions `test_<behavior>()`.

## Testing Guidelines

Prefer focused pytest tests with temporary paths and fixtures instead of touching real user state. Use `snap_root`, `config`, and `project_dir` from `tests/conftest.py` when testing snapshot behavior. Add regression tests for CLI output, destructive-action safeguards, archive compatibility, server database behavior, and remote sync when those areas change.

## Commit & Pull Request Guidelines

The visible Git history currently only shows an initial `init` commit, so no strict commit convention is established. Use short imperative subjects such as `add remote sync tests` or `fix restore clean preview`. Pull requests should describe the user-visible change, list tests run, link related issues, and include screenshots only for TUI or web admin UI changes.

## Security & Configuration Tips

Do not hard-code tokens, passwords, TLS keys, or real server URLs. Use temporary directories in tests, `SNAPZ_LANG` for runtime language overrides, and documented `snapz-server --data ...` options for local server work.
