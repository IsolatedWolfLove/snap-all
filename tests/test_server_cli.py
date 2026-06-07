"""snapz-server CLI startup behavior."""

from __future__ import annotations

from pathlib import Path

from snapz_server import cli


class _FakeServer:
    server_address = ("127.0.0.1", 0)
    tls_certfile = ""
    tls_client_ca = ""

    def serve_forever(self) -> None:
        return None

    def server_close(self) -> None:
        return None


def test_run_uses_server_env_defaults(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_make_server(data_dir, **kwargs):
        calls.append({"data_dir": data_dir, **kwargs})
        return _FakeServer()

    monkeypatch.setenv("SNAPZ_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("SNAPZ_SERVER_PORT", "9876")
    monkeypatch.setenv("SNAPZ_SERVER_MAX_BUNDLE_MB", "25")
    monkeypatch.setattr(cli, "make_server", fake_make_server)

    rc = cli.main(["--data", str(tmp_path / "server"), "run"])

    assert rc == cli.EXIT_OK
    assert calls == [
        {
            "data_dir": (tmp_path / "server").resolve(),
            "host": "0.0.0.0",
            "port": 9876,
            "admin_token": None,
            "cors_origins": [],
            "max_bundle_bytes": None,
            "tls_certfile": None,
            "tls_keyfile": None,
            "tls_client_ca": None,
        }
    ]
    assert "listening on http://127.0.0.1:0" in capsys.readouterr().out


def test_run_reads_server_config_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []
    config_path = tmp_path / "snapz-server.env"
    data_dir = tmp_path / "configured-data"
    config_path.write_text(
        "\n".join([
            f"SNAPZ_SERVER_DATA={data_dir}",
            "SNAPZ_SERVER_HOST=0.0.0.0",
            "SNAPZ_SERVER_PORT=9876",
            "SNAPZ_SERVER_MAX_BUNDLE_MB=25",
        ])
        + "\n",
        encoding="utf-8",
    )

    def fake_make_server(data_dir_arg, **kwargs):
        calls.append({"data_dir": data_dir_arg, **kwargs})
        return _FakeServer()

    monkeypatch.delenv("SNAPZ_SERVER_DATA", raising=False)
    monkeypatch.delenv("SNAPZ_SERVER_HOST", raising=False)
    monkeypatch.delenv("SNAPZ_SERVER_PORT", raising=False)
    monkeypatch.delenv("SNAPZ_SERVER_MAX_BUNDLE_MB", raising=False)
    monkeypatch.setattr(cli, "make_server", fake_make_server)

    rc = cli.main(["--config", str(config_path), "run"])

    assert rc == cli.EXIT_OK
    assert calls[0]["data_dir"] == data_dir.resolve()
    assert calls[0]["host"] == "0.0.0.0"
    assert calls[0]["port"] == 9876
    assert calls[0]["max_bundle_bytes"] is None


def test_run_cli_max_bundle_overrides_env(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_make_server(data_dir, **kwargs):
        calls.append({"data_dir": data_dir, **kwargs})
        return _FakeServer()

    monkeypatch.setenv("SNAPZ_SERVER_MAX_BUNDLE_MB", "25")
    monkeypatch.setattr(cli, "make_server", fake_make_server)

    rc = cli.main([
        "--data",
        str(tmp_path / "server"),
        "run",
        "--max-bundle-mb",
        "3",
    ])

    assert rc == cli.EXIT_OK
    assert calls[0]["max_bundle_bytes"] == 3 * 1024 * 1024


def test_run_reports_invalid_env_port(monkeypatch, capsys) -> None:
    def fail_make_server(*_args, **_kwargs):
        raise AssertionError("server should not be constructed")

    monkeypatch.setenv("SNAPZ_SERVER_PORT", "not-a-port")
    monkeypatch.setattr(cli, "make_server", fail_make_server)

    rc = cli.main(["run"])

    assert rc == cli.EXIT_ERROR
    assert "invalid SNAPZ_SERVER_PORT" in capsys.readouterr().err


def test_init_creates_config_service_and_enables_systemd(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    systemctl_calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_systemctl(args):
        systemctl_calls.append(args)
        return Result()

    config_path = tmp_path / "snapz-server.env"
    service_file = tmp_path / "snapz-server.service"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(cli, "_run_systemctl", fake_systemctl)

    rc = cli.main([
        "init",
        "--config",
        str(config_path),
        "--service-file",
        str(service_file),
        "--server-bin",
        "/opt/snapz-server",
        "--data",
        str(data_dir),
        "--host",
        "0.0.0.0",
        "--port",
        "9999",
        "--admin-token",
        "admin-secret",
    ])

    assert rc == cli.EXIT_OK
    config_text = config_path.read_text(encoding="utf-8")
    assert f"SNAPZ_SERVER_DATA={data_dir.resolve()}" in config_text
    assert "SNAPZ_SERVER_HOST=0.0.0.0" in config_text
    assert "SNAPZ_SERVER_PORT=9999" in config_text
    assert "SNAPZ_SERVER_ADMIN_TOKEN=admin-secret" in config_text
    assert config_path.stat().st_mode & 0o777 == 0o600
    service_text = service_file.read_text(encoding="utf-8")
    assert f"EnvironmentFile=-{config_path}" in service_text
    assert f"ExecStart=/opt/snapz-server --config {config_path} run" in service_text
    assert (data_dir / "server.sqlite3").exists()
    assert systemctl_calls == [
        ["daemon-reload"],
        ["enable", "--now", "snapz-server"],
    ]
    assert "created config" in capsys.readouterr().out


def test_init_keeps_existing_config_without_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "snapz-server.env"
    service_file = tmp_path / "snapz-server.service"
    existing_data = tmp_path / "existing-data"
    requested_data = tmp_path / "requested-data"
    config_path.write_text(
        f"SNAPZ_SERVER_DATA={existing_data}\n",
        encoding="utf-8",
    )
    service_file.write_text("custom service\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_run_systemctl", lambda _args: type("R", (), {"returncode": 0})())

    rc = cli.main([
        "init",
        "--config",
        str(config_path),
        "--service-file",
        str(service_file),
        "--data",
        str(requested_data),
    ])

    assert rc == cli.EXIT_OK
    assert config_path.read_text(encoding="utf-8") == f"SNAPZ_SERVER_DATA={existing_data}\n"
    assert service_file.read_text(encoding="utf-8") == "custom service\n"
    assert (existing_data / "server.sqlite3").exists()
    assert not requested_data.exists()


def test_update_preserves_existing_config(tmp_path: Path, monkeypatch) -> None:
    from snapz import self_update

    calls = []
    config_path = tmp_path / "snapz-server.env"
    config_path.write_text("SNAPZ_SERVER_DATA=/custom\n", encoding="utf-8")

    plan = self_update.DebUpdatePlan(
        tag="v9.0.0",
        package=self_update.SERVER_PACKAGE_NAME,
        asset_name="snapz-server_9.0.0_all.deb",
        download_url="https://example.test/snapz-server_9.0.0_all.deb",
        language="en",
    )
    result = self_update.DebUpdateResult(
        ok=True,
        plan=plan,
        deb_path=Path("/tmp/snapz-server_9.0.0_all.deb"),
        command=["apt", "install", "-y", "/tmp/snapz-server_9.0.0_all.deb"],
        returncode=0,
    )

    def fake_plan_update(**kwargs):
        calls.append(("plan", kwargs))
        return plan

    def fake_install_plan(plan_arg):
        calls.append(("install", plan_arg))
        return result

    monkeypatch.setattr(self_update, "plan_update", fake_plan_update)
    monkeypatch.setattr(self_update, "install_plan", fake_install_plan)

    rc = cli.main(["update", "--config", str(config_path)])

    assert rc == cli.EXIT_OK
    assert config_path.read_text(encoding="utf-8") == "SNAPZ_SERVER_DATA=/custom\n"
    assert calls[0][0] == "plan"
    assert calls[0][1]["package"] == self_update.SERVER_PACKAGE_NAME
    assert calls[1] == ("install", plan)
