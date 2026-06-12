"""Non-blocking GitHub release update checks for the CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from snapz import preferences
from snapz import style as st
from snapz.i18n import t

STATE_FILENAME = "_update_check.json"
WORKER_COMMAND = "__snapz-update-check"
CHECK_INTERVAL_DAYS = 1
DEFAULT_TIMEOUT_SECONDS = 4.0

GITHUB_RELEASE_API = (
    "https://api.github.com/repos/IsolatedWolfLove/snap-all/releases/latest"
)
GITHUB_TAGS_API = (
    "https://api.github.com/repos/IsolatedWolfLove/snap-all/tags?per_page=1"
)

SKIP_COMMANDS = frozenset({
    "config",
    "update",
    "uninstall",
    WORKER_COMMAND,
})


@dataclass(frozen=True)
class LatestVersion:
    tag: str
    version: str
    source: str


def state_path(root: Path) -> Path:
    return Path(root) / STATE_FILENAME


def load_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(root: Path, data: dict[str, Any]) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = state_path(root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _now() -> datetime:
    return datetime.now().astimezone()


def _today_key(now: datetime | None = None) -> str:
    return (now or _now()).date().isoformat()


def _now_iso(now: datetime | None = None) -> str:
    return (now or _now()).isoformat(timespec="seconds")


def normalize_version(value: str) -> str:
    return value.strip().lstrip("vV")


def _version_key(value: str) -> tuple[int, ...]:
    parts = [int(p) for p in re.findall(r"\d+", normalize_version(value))]
    return tuple(parts)


def version_is_newer(latest: str, current: str) -> bool:
    latest_key = _version_key(latest)
    current_key = _version_key(current)
    return bool(latest_key and current_key and latest_key > current_key)


def _request_json(url: str, *, timeout: float) -> Any:
    from urllib.request import Request, urlopen

    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("update check URL must use http or https")
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "snapz-update-check",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _latest_from_tag(tag: str, *, source: str) -> LatestVersion:
    tag = tag.strip()
    if not tag:
        raise ValueError("empty release tag")
    return LatestVersion(tag=tag, version=normalize_version(tag), source=source)


def fetch_latest_version(
    *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> LatestVersion:
    """Return the newest published release/tag from GitHub."""

    errors: list[str] = []
    try:
        release = _request_json(GITHUB_RELEASE_API, timeout=timeout)
        if isinstance(release, dict):
            tag = str(release.get("tag_name") or "")
            if tag:
                return _latest_from_tag(tag, source="release")
    except Exception as exc:
        errors.append(f"release: {exc}")

    try:
        tags = _request_json(GITHUB_TAGS_API, timeout=timeout)
        if isinstance(tags, list) and tags:
            tag = str(tags[0].get("name") or "")
            if tag:
                return _latest_from_tag(tag, source="tag")
    except Exception as exc:
        errors.append(f"tag: {exc}")

    detail = "; ".join(errors) if errors else "no tag returned"
    raise RuntimeError(f"could not fetch latest snapz version: {detail}")


def _config_enabled(root: Path) -> bool:
    try:
        value = preferences.get_config_value(root, "update_check.enabled")
    except (KeyError, ValueError):
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "n"}


def _check_due(state: dict[str, Any], *, now: datetime | None = None) -> bool:
    return state.get("last_check_date") != _today_key(now)


def cached_update_notice(root: Path, current_version: str) -> str | None:
    state = load_state(root)
    latest = str(state.get("latest_version") or "")
    if not latest or not version_is_newer(latest, current_version):
        return None
    return t(
        "update_check.notice",
        current=current_version,
        latest=latest,
    )


def _print_notice(root: Path, current_version: str) -> bool:
    notice = cached_update_notice(root, current_version)
    if not notice:
        return False
    print(st.warn(notice), file=sys.stderr)
    return True


def _build_worker_command(
    *,
    argv0: str,
    root: Path,
    current_version: str,
) -> list[str]:
    target = argv0
    if target and os.sep not in target and not Path(target).exists():
        import shutil

        target = shutil.which(target) or target

    if target and (Path(target).exists() or os.sep in target):
        return [
            sys.executable,
            target,
            WORKER_COMMAND,
            "--root",
            str(root),
            "--current-version",
            current_version,
        ]

    return [
        sys.executable,
        "-m",
        "snapz",
        WORKER_COMMAND,
        "--root",
        str(root),
        "--current-version",
        current_version,
    ]


def _reserve_check(
    root: Path,
    *,
    current_version: str,
    now: datetime | None = None,
) -> None:
    state = load_state(root)
    state["last_check_date"] = _today_key(now)
    state["last_check_started_at"] = _now_iso(now)
    state["last_check_current_version"] = current_version
    save_state(root, state)


def maybe_start(
    root: Path,
    *,
    current_version: str,
    argv0: str,
    command: str | None,
    json_requested: bool = False,
    now: datetime | None = None,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    force: bool = False,
) -> str:
    """Print cached notices and launch the daily background check if due."""

    root = Path(root)
    if not force:
        if os.environ.get("SNAPZ_NO_UPDATE_CHECK"):
            return "skipped"
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return "skipped"
        if os.environ.get("_ARGCOMPLETE"):
            return "skipped"
    if json_requested or command in SKIP_COMMANDS:
        return "skipped"
    if not _config_enabled(root):
        return "disabled"

    notified = _print_notice(root, current_version)
    if not _check_due(load_state(root), now=now):
        return "notified" if notified else "not-due"

    _reserve_check(root, current_version=current_version, now=now)
    command_line = _build_worker_command(
        argv0=argv0,
        root=root,
        current_version=current_version,
    )
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        popen(command_line, **kwargs)
    except Exception as exc:
        state = load_state(root)
        state["last_spawn_error"] = f"{type(exc).__name__}: {exc}"
        save_state(root, state)
        return "spawn-failed"
    return "started"


def run_check(
    root: Path,
    *,
    current_version: str,
    fetcher: Callable[[], LatestVersion] = fetch_latest_version,
    now: datetime | None = None,
) -> int:
    root = Path(root)
    state = load_state(root)
    state["last_check_completed_at"] = _now_iso(now)
    try:
        latest = fetcher()
    except Exception as exc:
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        save_state(root, state)
        return 1

    state["latest_tag"] = latest.tag
    state["latest_version"] = latest.version
    state["latest_source"] = latest.source
    state["checked_current_version"] = current_version
    state["update_available"] = version_is_newer(latest.version, current_version)
    state.pop("last_error", None)
    save_state(root, state)
    return 0


def run_worker_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"snapz {WORKER_COMMAND}")
    parser.add_argument("--root", required=True)
    parser.add_argument("--current-version", required=True)
    args = parser.parse_args(argv)
    return run_check(Path(args.root), current_version=args.current_version)
