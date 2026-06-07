"""Release package contract tests."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_debian_build_creates_separate_client_and_server_packages() -> None:
    script = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")

    assert '"$DIST/${package}_${version}_all.deb"' in script
    assert '"$DIST/snapz-server_${version}_all.deb"' in script
    assert '"$DIST/snapz.pyz"' in script
    assert '"snapz"' in script
    assert '"$DIST/snapz-server.pyz"' in script
    assert '"snapz-server"' in script
    assert "if grep -q 'usr/bin/snapz-server$' \"$client_contents\"" in script
    assert "if grep -q 'usr/bin/snapz$' \"$server_contents\"" in script


def test_server_debian_package_leaves_config_to_init_command() -> None:
    script = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")
    build_deb_section = script.split("build_deb() {", 1)[1].split("smoke() {", 1)[0]

    assert 'cat > "$deb_root/etc/default/snapz-server"' not in build_deb_section
    assert 'cat > "$deb_root/DEBIAN/conffiles"' not in build_deb_section
    assert "lib/systemd/system" not in build_deb_section
    assert "snapz-server init to create config and systemd service files" in script
