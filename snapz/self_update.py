"""Install snapz release ``.deb`` assets from GitHub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

GITHUB_RELEASE_API = (
    "https://api.github.com/repos/IsolatedWolfLove/snap-all/releases/latest"
)
CLIENT_PACKAGE_NAME = "snapz-cli"
SERVER_PACKAGE_NAME = "snapz-server"


@dataclass(frozen=True)
class DebUpdatePlan:
    tag: str
    package: str
    asset_name: str
    download_url: str
    language: str


@dataclass(frozen=True)
class DebUpdateResult:
    ok: bool
    plan: DebUpdatePlan
    deb_path: Path
    command: list[str]
    returncode: int


UrlOpener = Callable[[urllib.request.Request], Any]
Runner = Callable[..., subprocess.CompletedProcess[str]]


def language_deb_suffix(language: str) -> str:
    return ".zh.deb" if language == "zh" else ".deb"


def select_deb_asset(
    release: dict[str, Any],
    *,
    language: str,
    package: str = CLIENT_PACKAGE_NAME,
) -> DebUpdatePlan:
    if not package:
        raise ValueError("package name is required")
    assets = [
        item for item in release.get("assets") or []
        if isinstance(item, dict)
    ]
    suffix = language_deb_suffix(language)
    exact = _find_asset(assets, package=package, suffix=suffix)
    if exact is None and language == "zh":
        exact = _find_asset(assets, package=package, suffix=".deb")
    if exact is None:
        raise ValueError(f"release has no {package} {suffix} asset")
    name = str(exact.get("name") or "")
    url = str(
        exact.get("browser_download_url")
        or exact.get("url")
        or ""
    )
    if not name or not url:
        raise ValueError("release deb asset is missing name or download URL")
    return DebUpdatePlan(
        tag=str(release.get("tag_name") or ""),
        package=package,
        asset_name=name,
        download_url=url,
        language=language,
    )


def fetch_latest_release(
    *,
    release_url: str = GITHUB_RELEASE_API,
    opener: UrlOpener = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        release_url,
        headers={"User-Agent": "snapz-update"},
    )
    with opener(request) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GitHub release response must be an object")
    return data


def download_asset(
    plan: DebUpdatePlan,
    destination: Path,
    *,
    opener: UrlOpener = urllib.request.urlopen,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        plan.download_url,
        headers={"User-Agent": "snapz-update"},
    )
    with opener(request) as response:
        data = response.read()
    destination.write_bytes(data)
    return destination


def install_deb(
    deb_path: Path,
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    command = installer_command(deb_path)
    return runner(command, check=False, text=True)


def deb_package_installed(
    package: str,
    *,
    runner: Runner = subprocess.run,
) -> bool:
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return False
    result = runner(
        [dpkg_query, "-W", "-f=${Status}", package],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode == 0 and "install ok installed" in str(result.stdout)


def remove_deb_package(
    package: str,
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    command = remover_command(package)
    return runner(command, check=False, text=True)


def installer_command(deb_path: Path) -> list[str]:
    apt = shutil.which("apt")
    dpkg = shutil.which("dpkg")
    if apt:
        command = [apt, "install", "-y", str(deb_path)]
    elif dpkg:
        command = [dpkg, "-i", str(deb_path)]
    else:
        raise RuntimeError("apt or dpkg is required to install .deb packages")
    return _with_sudo_if_needed(command)


def remover_command(package: str) -> list[str]:
    if not package:
        raise ValueError("package name is required")
    apt = shutil.which("apt")
    dpkg = shutil.which("dpkg")
    if apt:
        command = [apt, "remove", "-y", package]
    elif dpkg:
        command = [dpkg, "-r", package]
    else:
        raise RuntimeError("apt or dpkg is required to remove .deb packages")
    return _with_sudo_if_needed(command)


def _with_sudo_if_needed(command: list[str]) -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo:
            return [sudo, *command]
    return command


def update_from_release(
    *,
    language: str,
    package: str = CLIENT_PACKAGE_NAME,
    release_url: str = GITHUB_RELEASE_API,
    opener: UrlOpener = urllib.request.urlopen,
    runner: Runner = subprocess.run,
) -> DebUpdateResult:
    plan = plan_update(
        language=language,
        package=package,
        release_url=release_url,
        opener=opener,
    )
    return install_plan(plan, opener=opener, runner=runner)


def plan_update(
    *,
    language: str,
    package: str = CLIENT_PACKAGE_NAME,
    release_url: str = GITHUB_RELEASE_API,
    opener: UrlOpener = urllib.request.urlopen,
) -> DebUpdatePlan:
    release = fetch_latest_release(release_url=release_url, opener=opener)
    return select_deb_asset(release, language=language, package=package)


def install_plan(
    plan: DebUpdatePlan,
    *,
    opener: UrlOpener = urllib.request.urlopen,
    runner: Runner = subprocess.run,
) -> DebUpdateResult:
    with tempfile.TemporaryDirectory(prefix="snapz-update-") as tmpdir:
        deb_path = Path(tmpdir) / plan.asset_name
        download_asset(plan, deb_path, opener=opener)
        proc = install_deb(deb_path, runner=runner)
        return DebUpdateResult(
            ok=proc.returncode == 0,
            plan=plan,
            deb_path=deb_path,
            command=list(proc.args) if isinstance(proc.args, list) else [],
            returncode=int(proc.returncode),
        )


def _find_asset(
    assets: list[dict[str, Any]],
    *,
    package: str,
    suffix: str,
) -> dict[str, Any] | None:
    for item in assets:
        name = str(item.get("name") or "")
        if suffix == ".deb" and name.endswith(".zh.deb"):
            continue
        if (
            name.startswith(f"{package}_")
            and name.endswith(suffix)
            and "_all" in name
        ):
            return item
    return None
