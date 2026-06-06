"""Local ``snapz web`` UI and API tests."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from snapz import api, cli, web_ui


@pytest.fixture
def env_root(monkeypatch, snap_root: Path) -> Path:
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(snap_root))
    return snap_root


def _json(url: str, path: str, *, method: str = "GET", body: dict | None = None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url + path, data=data, headers=headers, method=method)
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _text(url: str, path: str) -> str:
    with urlopen(url + path, timeout=5) as response:
        return response.read().decode("utf-8")


def test_web_command_is_registered(env_root, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["web", "--help"])
    out = capsys.readouterr().out

    assert exc_info.value.code == 0
    assert "start the local snapz client web UI" in out
    assert "--allow-remote" in out


def test_web_command_refuses_remote_bind(env_root, capsys):
    rc = cli.main(["web", "--host", "0.0.0.0", "--port", "0"])
    err = capsys.readouterr().err

    assert rc == cli.EXIT_ERROR
    assert "refusing to bind" in err


def test_web_ui_serves_html_and_health(config):
    server, thread = web_ui.run_in_thread(config=config)
    try:
        url = web_ui.server_url(server, "127.0.0.1")
        html = _text(url, "/")
        health = _json(url, "/api/health")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "snapz Client" in html
    assert "Local snapshot control panel" in html
    assert health["status"] == "ok"
    assert health["service"] == "snapz-client-web"


def test_web_api_lists_and_creates_snapshots(config, project_dir):
    server, thread = web_ui.run_in_thread(config=config)
    try:
        url = web_ui.server_url(server, "127.0.0.1")
        created = _json(
            url,
            "/api/snapshots",
            method="POST",
            body={
                "name": "v1",
                "note": "from web",
                "path": str(project_dir),
            },
        )
        listed = _json(url, f"/api/snapshots?path={project_dir}")
        overview = _json(url, "/api/overview")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert created["snapshot"]["name"] == "v1"
    assert listed["path"] == str(project_dir)
    assert [snap["name"] for snap in listed["snapshots"]] == ["v1"]
    assert overview["total_snapshots"] == 1
    assert api.show(project_dir, "v1", config=config).note == "from web"


def test_web_api_snapshot_not_found_returns_404(config, project_dir):
    server, thread = web_ui.run_in_thread(config=config)
    try:
        url = web_ui.server_url(server, "127.0.0.1")
        try:
            _json(url, f"/api/snapshots/missing?path={project_dir}")
        except HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover - defensive
            raise AssertionError("expected HTTP 404")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 404
    assert "missing" in payload["error"]
