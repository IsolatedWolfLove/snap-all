"""Release package contract tests."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_debian_package_preserves_server_runtime_config_on_upgrade() -> None:
    script = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")

    assert 'cat > "$deb_root/etc/default/snapz-server"' in script
    assert 'cat > "$deb_root/DEBIAN/conffiles"' in script
    assert "/etc/default/snapz-server" in script
    assert "EnvironmentFile=-/etc/default/snapz-server" in script
    assert "ExecStart=/usr/bin/snapz-server --config /etc/default/snapz-server run" in script
    assert "dpkg-deb --ctrl-tarfile" in script
