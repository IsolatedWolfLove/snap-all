"""Lightweight scheduler integration for remote-only sync."""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CronInstallResult:
    marker: str
    command: str
    installed: bool
    updated: bool


class SchedulerError(RuntimeError):
    """Raised when the platform scheduler cannot be updated."""


def _cron_marker(root: Path) -> str:
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]
    return f"# snapz remote sync {digest}"


def _cron_command(root: Path, *, python: str | None = None) -> str:
    python = python or sys.executable
    env = f"SNAPZ_ALL_ROOT={shlex.quote(str(Path(root).resolve()))}"
    exe = shlex.quote(python)
    push = f"{env} {exe} -m snapz push all"
    pull = f"{env} {exe} -m snapz pull all"
    return f"0 */3 * * * {push} >/dev/null 2>&1; {pull} >/dev/null 2>&1"


def install_remote_sync_cron(root: Path, *, python: str | None = None) -> CronInstallResult:
    """Install or replace the snapz remote sync crontab entry for *root*."""

    marker = _cron_marker(root)
    command = _cron_command(root, python=python)
    current = _read_crontab()
    lines = _without_marker_block(current.splitlines(), marker)
    updated = len(lines) != len(current.splitlines())
    lines.extend([marker, command])
    payload = "\n".join(lines).rstrip() + "\n"
    _write_crontab(payload)
    return CronInstallResult(
        marker=marker,
        command=command,
        installed=True,
        updated=updated,
    )


def _without_marker_block(lines: list[str], marker: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.strip() == marker:
            skip_next = True
            continue
        out.append(line)
    return out


def _read_crontab() -> str:
    try:
        proc = subprocess.run(
            ["crontab", "-l"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SchedulerError("crontab command not found") from exc
    if proc.returncode == 0:
        return proc.stdout
    # Most cron implementations return non-zero when no crontab exists.
    if "no crontab" in (proc.stderr or "").lower():
        return ""
    if not proc.stdout.strip() and not proc.stderr.strip():
        return ""
    raise SchedulerError((proc.stderr or proc.stdout or "could not read crontab").strip())


def _write_crontab(payload: str) -> None:
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=payload,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SchedulerError("crontab command not found") from exc
    if proc.returncode != 0:
        raise SchedulerError((proc.stderr or proc.stdout or "could not write crontab").strip())
