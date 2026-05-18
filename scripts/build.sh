#!/usr/bin/env bash
# Automated build for snapz-cli release artifacts.
#
# Outputs to ``dist/``:
#   snapz-<version>.tar.gz                  source distribution
#   snapz_cli-<version>-py3-none-any.whl    universal wheel
#   snapz.pyz                               shiv zipapp for the client command
#   snapz-server.pyz                        shiv zipapp for the standalone server
#
# Usage:
#   ./scripts/build.sh              # all targets (wheel + sdist + pyz)
#   ./scripts/build.sh wheel        # sdist + wheel only
#   ./scripts/build.sh pyz          # shiv .pyz only (rebuilds wheel if missing)
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
    if [ ! -x "$VENV/bin/python" ]; then
        log "creating build venv at $VENV"
        "$PY_BIN" -m venv "$VENV"
        "$VENV/bin/pip" install --upgrade --quiet pip wheel build shiv zstandard
    fi
    mkdir -p "$DIST"
}

resolve_wheel() {
    ls -1t "$DIST"/snapz_cli-*.whl 2>/dev/null | head -n 1 || true
}

build_wheel() {
    ensure_venv
    log "building sdist + wheel -> $DIST"
    "$VENV/bin/python" -m build --outdir "$DIST" >/dev/null
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
    "$VENV/bin/shiv" \
        -c snapz \
        -o "$DIST/snapz.pyz" \
        -p "/usr/bin/env python3" \
        "$wheel" \
        "zstandard" >/dev/null
    chmod +x "$DIST/snapz.pyz"
    "$VENV/bin/shiv" \
        -c snapz-server \
        -o "$DIST/snapz-server.pyz" \
        -p "/usr/bin/env python3" \
        "$wheel" \
        "zstandard" >/dev/null
    chmod +x "$DIST/snapz-server.pyz"
}

smoke() {
    log "smoke-testing artifacts"
    local fail=0
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
        "$VENV/bin/pip" install --dry-run --quiet "$wheel" || fail=1
    else
        warn "  wheel missing"
        fail=1
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
    smoke)         smoke ;;
    all)
        cleanup
        build_wheel
        build_pyz
        smoke
        ;;
    *)
        echo "unknown target: $target" >&2
        echo "usage: $0 [--lang en|zh] [all|wheel|pyz|smoke|--clean]" >&2
        exit 2
        ;;
esac

if [ -d "$DIST" ]; then
    echo
    log "dist/ contents:"
    ls -lh "$DIST" | sed '1d'
fi
