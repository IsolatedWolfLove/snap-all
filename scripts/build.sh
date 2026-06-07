#!/usr/bin/env bash
# Automated build for snapz-cli release artifacts.
#
# Outputs to ``dist/``:
#   snapz-<version>.tar.gz                  source distribution
#   snapz_cli-<version>-py3-none-any.whl    universal wheel
#   snapz.pyz                               shiv zipapp for the client command
#   snapz-server.pyz                        shiv zipapp for the standalone server
#   snapz-cli_<version>_all.deb             Debian package with /usr/bin/snapz
#   snapz-server_<version>_all.deb          Debian package with /usr/bin/snapz-server
#
# Usage:
#   ./scripts/build.sh              # all targets (wheel + sdist + pyz + deb)
#   ./scripts/build.sh wheel        # sdist + wheel only
#   ./scripts/build.sh pyz          # shiv .pyz only (rebuilds wheel if missing)
#   ./scripts/build.sh deb          # Debian .deb package (rebuilds .pyz first)
#   ./scripts/build.sh smoke        # run --version against the freshly built artifacts
#   ./scripts/build.sh --clean      # delete dist/, build/, .build-venv/
#
#   ./scripts/build.sh --lang zh all   # bake `DEFAULT_LANG = "zh"` into the artifact
#                                      # so `snapz --help` defaults to Chinese (the
#                                      # SNAPZ_LANG env var still wins at runtime).
#
# Override the host Python with PYTHON=/path/to/python3.
set -euo pipefail

# ROS / similar stacks inject PYTHONPATH; keep the build hermetic.
unset PYTHONPATH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DIST="$ROOT/dist"
WORK="$ROOT/build"
VENV="$ROOT/.build-venv"
PY_BIN="${PYTHON:-python3}"
I18N_FILE="$ROOT/snapz/i18n.py"
LANG_BAKE=""           # set by --lang; empty = leave DEFAULT_LANG alone

log()  { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }

venv_python() {
    if [ -x "$VENV/bin/python" ]; then
        printf '%s/bin/python' "$VENV"
    elif [ -x "$VENV/Scripts/python.exe" ]; then
        printf '%s/Scripts/python.exe' "$VENV"
    else
        printf '%s/bin/python' "$VENV"
    fi
}

venv_pip() {
    if [ -x "$VENV/bin/pip" ]; then
        printf '%s/bin/pip' "$VENV"
    elif [ -x "$VENV/Scripts/pip.exe" ]; then
        printf '%s/Scripts/pip.exe' "$VENV"
    else
        printf '%s/bin/pip' "$VENV"
    fi
}

venv_shiv() {
    if [ -x "$VENV/bin/shiv" ]; then
        printf '%s/bin/shiv' "$VENV"
    elif [ -x "$VENV/Scripts/shiv.exe" ]; then
        printf '%s/Scripts/shiv.exe' "$VENV"
    else
        printf '%s/bin/shiv' "$VENV"
    fi
}

# Patch DEFAULT_LANG = "en" -> DEFAULT_LANG = "<lang>" before packaging,
# then restore it on exit so the working tree stays clean. Empty arg = no-op.
patch_default_lang() {
    local lang="$1"
    [ -z "$lang" ] && return 0
    case "$lang" in
        en|zh) ;;
        *) echo "unknown lang: $lang (supported: en, zh)" >&2; exit 2 ;;
    esac
    if ! grep -q '^DEFAULT_LANG = "en"' "$I18N_FILE"; then
        warn "  cannot find 'DEFAULT_LANG = \"en\"' in $I18N_FILE — skipping bake"
        return 0
    fi
    log "baking DEFAULT_LANG = \"$lang\" into $I18N_FILE (will be reverted on exit)"
    cp -f "$I18N_FILE" "$I18N_FILE.bak.build"
    sed -i "s/^DEFAULT_LANG = \"en\"\$/DEFAULT_LANG = \"$lang\"/" "$I18N_FILE"
    # Sanity: make sure the substitution actually landed.
    grep -q "^DEFAULT_LANG = \"$lang\"\$" "$I18N_FILE" || {
        mv -f "$I18N_FILE.bak.build" "$I18N_FILE"
        echo "DEFAULT_LANG patch failed; aborting" >&2
        exit 2
    }
}

restore_default_lang() {
    if [ -f "$I18N_FILE.bak.build" ]; then
        mv -f "$I18N_FILE.bak.build" "$I18N_FILE"
    fi
}
trap restore_default_lang EXIT

cleanup() {
    log "removing dist/, build/, .build-venv/"
    rm -rf "$DIST" "$WORK" "$VENV"
}

ensure_venv() {
    local vpy vpip
    if [ ! -x "$VENV/bin/python" ] && [ ! -x "$VENV/Scripts/python.exe" ]; then
        log "creating build venv at $VENV"
        "$PY_BIN" -m venv "$VENV"
    fi
    vpy="$(venv_python)"
    vpip="$(venv_pip)"
    if ! "$vpy" -c 'import build, shiv, zstandard' >/dev/null 2>&1; then
        log "installing build dependencies"
        "$vpip" install --upgrade --quiet pip wheel build shiv zstandard
    fi
    mkdir -p "$DIST"
}

check_py310_compat() {
    log "checking Python 3.10 f-string compatibility"
    "$PY_BIN" "$ROOT/tools/check_py310_fstrings.py" "$ROOT/snapz" "$ROOT/snapz_server"
}

resolve_wheel() {
    local version
    version="$(project_version)"
    if [ -n "$version" ]; then
        ls -1t "$DIST"/snapz_cli-"$version"-*.whl 2>/dev/null | head -n 1 || true
    else
        ls -1t "$DIST"/snapz_cli-*.whl 2>/dev/null | head -n 1 || true
    fi
}

project_name() {
    sed -n 's/^name = "\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -n 1
}

project_version() {
    sed -n 's/^version = "\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -n 1
}

build_wheel() {
    check_py310_compat
    ensure_venv
    mkdir -p "$WORK"
    log "building sdist + wheel -> $DIST"
    (cd "$WORK" && "$(venv_python)" -m build "$ROOT" --outdir "$DIST") >/dev/null
}

build_pyz() {
    ensure_venv
    local wheel
    wheel="$(resolve_wheel)"
    if [ -z "$wheel" ]; then
        build_wheel
        wheel="$(resolve_wheel)"
    fi
    log "building shiv zipapps from $(basename "$wheel")"
    "$(venv_shiv)" \
        -c snapz \
        -o "$DIST/snapz.pyz" \
        -p "/usr/bin/env python3" \
        "$wheel" \
        "zstandard" >/dev/null
    chmod +x "$DIST/snapz.pyz"
    "$(venv_shiv)" \
        -c snapz-server \
        -o "$DIST/snapz-server.pyz" \
        -p "/usr/bin/env python3" \
        "$wheel" \
        "zstandard" >/dev/null
    chmod +x "$DIST/snapz-server.pyz"
}

write_deb_control() {
    local deb_root="$1"
    local package="$2"
    local version="$3"
    local installed_size="$4"
    local summary="$5"
    local description="$6"
    local extra_fields="${7:-}"
    cat > "$deb_root/DEBIAN/control" <<EOF
Package: $package
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: snapz contributors
Depends: python3 (>= 3.10)
EOF
    if [ -n "$extra_fields" ]; then
        printf '%s\n' "$extra_fields" >> "$deb_root/DEBIAN/control"
    fi
    cat >> "$deb_root/DEBIAN/control" <<EOF
Installed-Size: $installed_size
Homepage: https://github.com/IsolatedWolfLove/snap-all
Description: $summary
 $description
EOF
    chmod 0644 "$deb_root/DEBIAN/control"
}

build_single_deb() {
    local package="$1"
    local version="$2"
    local source_pyz="$3"
    local command_name="$4"
    local summary="$5"
    local description="$6"
    local extra_fields="${7:-}"
    local deb_root deb_name installed_size

    deb_root="$WORK/deb/${package}_${version}_all"
    deb_name="$DIST/${package}_${version}_all.deb"
    rm -rf "$deb_root"
    mkdir -p \
        "$deb_root/DEBIAN" \
        "$deb_root/usr/bin" \
        "$deb_root/usr/share/doc/$package"

    install -m 0755 "$source_pyz" "$deb_root/usr/bin/$command_name"
    install -m 0644 "$ROOT/README.md" "$deb_root/usr/share/doc/$package/README.md"
    install -m 0644 "$ROOT/LICENSE" "$deb_root/usr/share/doc/$package/copyright"

    installed_size="$(du -sk "$deb_root/usr" | awk '{sum += $1} END {print sum}')"
    write_deb_control \
        "$deb_root" \
        "$package" \
        "$version" \
        "$installed_size" \
        "$summary" \
        "$description" \
        "$extra_fields"

    log "building Debian package -> $deb_name"
    dpkg-deb --build --root-owner-group "$deb_root" "$deb_name" >/dev/null
}

build_deb() {
    if ! command -v dpkg-deb >/dev/null 2>&1; then
        echo "dpkg-deb not found; install dpkg-dev or dpkg first" >&2
        exit 2
    fi
    build_pyz
    local package version
    package="$(project_name)"
    version="$(project_version)"
    if [ -z "$package" ] || [ -z "$version" ]; then
        echo "cannot read project name/version from pyproject.toml" >&2
        exit 2
    fi
    build_single_deb \
        "$package" \
        "$version" \
        "$DIST/snapz.pyz" \
        "snapz" \
        "Lightweight directory snapshot CLI" \
        "snapz creates restorable directory snapshots and stores them under ~/.snapz-all."
    build_single_deb \
        "snapz-server" \
        "$version" \
        "$DIST/snapz-server.pyz" \
        "snapz-server" \
        "Standalone snapz remote sync server" \
        "snapz-server serves multi-tenant remote sync APIs. Run snapz-server init to create config and systemd service files." \
        "Replaces: snapz-cli (<< $version)"
}

smoke() {
    log "smoke-testing artifacts"
    local fail=0
    check_py310_compat || fail=1
    local shiv_root="${SHIV_ROOT:-$WORK/shiv-root}"
    mkdir -p "$shiv_root"
    if [ -f "$DIST/snapz.pyz" ]; then
        log "  ./dist/snapz.pyz --version"
        SHIV_ROOT="$shiv_root" "$DIST/snapz.pyz" --version || fail=1
    else
        warn "  $DIST/snapz.pyz missing"
        fail=1
    fi
    if [ -f "$DIST/snapz-server.pyz" ]; then
        log "  ./dist/snapz-server.pyz --version"
        SHIV_ROOT="$shiv_root" "$DIST/snapz-server.pyz" --version || fail=1
    else
        warn "  $DIST/snapz-server.pyz missing"
        fail=1
    fi
    local wheel
    wheel="$(resolve_wheel)"
    if [ -n "$wheel" ]; then
        log "  pip install --dry-run $(basename "$wheel")"
        "$(venv_pip)" install --dry-run --no-deps --quiet "$wheel" || fail=1
    else
        warn "  wheel missing"
        fail=1
    fi
    local package version client_deb server_deb
    package="$(project_name)"
    version="$(project_version)"
    client_deb="$DIST/${package}_${version}_all.deb"
    server_deb="$DIST/snapz-server_${version}_all.deb"
    if [ -f "$client_deb" ]; then
        log "  dpkg-deb --info $(basename "$client_deb")"
        dpkg-deb --info "$client_deb" >/dev/null || fail=1
        local client_contents="$WORK/deb-client-contents.txt"
        dpkg-deb --contents "$client_deb" > "$client_contents" || fail=1
        grep -q 'usr/bin/snapz$' "$client_contents" || fail=1
        if grep -q 'usr/bin/snapz-server$' "$client_contents"; then
            fail=1
        fi
    fi
    if [ -f "$server_deb" ]; then
        log "  dpkg-deb --info $(basename "$server_deb")"
        dpkg-deb --info "$server_deb" >/dev/null || fail=1
        local server_contents="$WORK/deb-server-contents.txt"
        dpkg-deb --contents "$server_deb" > "$server_contents" || fail=1
        grep -q 'usr/bin/snapz-server$' "$server_contents" || fail=1
        if grep -q 'usr/bin/snapz$' "$server_contents"; then
            fail=1
        fi
        if grep -q 'etc/default/snapz-server$' "$server_contents"; then
            fail=1
        fi
        if grep -q 'lib/systemd/system/snapz-server.service$' "$server_contents"; then
            fail=1
        fi
        if dpkg-deb --ctrl-tarfile "$server_deb" | tar -tf - | grep -q './conffiles'; then
            fail=1
        fi
    fi
    return "$fail"
}

# --lang LANG is consumed before the positional target so the rest of the
# argv handling stays unchanged. Both `--lang zh all` and `--lang=zh all`
# are accepted.
while [ $# -gt 0 ]; do
    case "$1" in
        --lang)   LANG_BAKE="${2:-}"; shift 2 ;;
        --lang=*) LANG_BAKE="${1#--lang=}"; shift ;;
        *)        break ;;
    esac
done

target="${1:-all}"
patch_default_lang "$LANG_BAKE"

case "$target" in
    --clean|clean) cleanup ;;
    wheel)         build_wheel ;;
    pyz)           build_pyz ;;
    deb)           build_deb ;;
    smoke)         smoke ;;
    all)
        cleanup
        build_wheel
        build_deb
        smoke
        ;;
    *)
        echo "unknown target: $target" >&2
        echo "usage: $0 [--lang en|zh] [all|wheel|pyz|deb|smoke|--clean]" >&2
        exit 2
        ;;
esac

if [ -d "$DIST" ]; then
    echo
    log "dist/ contents:"
    ls -lh "$DIST" | sed '1d'
fi
