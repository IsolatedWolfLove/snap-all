"""Debian release self-update helpers."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

from snapz import self_update


def _release() -> dict:
    return {
        "tag_name": "v9.0.0",
        "assets": [
            {
                "name": "snapz-cli_9.0.0_all.deb",
                "browser_download_url": "https://example.test/en.deb",
            },
            {
                "name": "snapz-cli_9.0.0_all.zh.deb",
                "browser_download_url": "https://example.test/zh.deb",
            },
            {
                "name": "snapz-server_9.0.0_all.deb",
                "browser_download_url": "https://example.test/server-en.deb",
            },
            {
                "name": "snapz-server_9.0.0_all.zh.deb",
                "browser_download_url": "https://example.test/server-zh.deb",
            },
            {
                "name": "snapz.pyz",
                "browser_download_url": "https://example.test/snapz.pyz",
            },
        ],
    }


def test_select_deb_asset_prefers_chinese_package():
    plan = self_update.select_deb_asset(_release(), language="zh")

    assert plan.asset_name == "snapz-cli_9.0.0_all.zh.deb"
    assert plan.package == self_update.CLIENT_PACKAGE_NAME
    assert plan.download_url == "https://example.test/zh.deb"
    assert plan.language == "zh"


def test_select_deb_asset_english_skips_chinese_package():
    plan = self_update.select_deb_asset(_release(), language="en")

    assert plan.asset_name == "snapz-cli_9.0.0_all.deb"
    assert plan.download_url == "https://example.test/en.deb"


def test_select_deb_asset_uses_requested_package():
    plan = self_update.select_deb_asset(
        _release(),
        language="zh",
        package=self_update.SERVER_PACKAGE_NAME,
    )

    assert plan.asset_name == "snapz-server_9.0.0_all.zh.deb"
    assert plan.package == self_update.SERVER_PACKAGE_NAME
    assert plan.download_url == "https://example.test/server-zh.deb"


def test_select_deb_asset_falls_back_to_english_when_chinese_missing():
    release = _release()
    release["assets"] = [
        asset
        for asset in release["assets"]
        if asset["name"] == "snapz-cli_9.0.0_all.deb"
    ]

    plan = self_update.select_deb_asset(release, language="zh")

    assert plan.asset_name == "snapz-cli_9.0.0_all.deb"


def test_plan_update_fetches_release_with_user_agent():
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(_release()).encode("utf-8")

    def fake_open(request):
        seen.append(request)
        return Response()

    plan = self_update.plan_update(
        language="en",
        package=self_update.SERVER_PACKAGE_NAME,
        opener=fake_open,
    )

    assert plan.asset_name == "snapz-server_9.0.0_all.deb"
    assert seen[0].headers["User-agent"] == "snapz-update"


def test_install_deb_uses_apt_when_available(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_which(name):
        if name == "apt":
            return "/usr/bin/apt"
        if name == "sudo":
            return None
        return None

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(self_update.shutil, "which", fake_which)
    monkeypatch.setattr(self_update.os, "geteuid", lambda: 0, raising=False)

    result = self_update.install_deb(tmp_path / "snapz.deb", runner=fake_run)

    assert result.returncode == 0
    assert calls == [["/usr/bin/apt", "install", "-y", str(tmp_path / "snapz.deb")]]


def test_install_deb_uses_sudo_for_non_root(monkeypatch, tmp_path):
    def fake_which(name):
        return {
            "apt": "/usr/bin/apt",
            "sudo": "/usr/bin/sudo",
        }.get(name)

    monkeypatch.setattr(self_update.shutil, "which", fake_which)
    monkeypatch.setattr(self_update.os, "geteuid", lambda: 1000, raising=False)

    command = self_update.installer_command(tmp_path / "snapz.deb")

    assert command[:2] == ["/usr/bin/sudo", "/usr/bin/apt"]
