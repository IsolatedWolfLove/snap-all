"""Server startup configuration helpers."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from typing import Iterable


DEFAULT_MAX_BUNDLE_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_COMPACT_ZSTD_LEVEL = 22
DEFAULT_COMPACT_MANIFEST_ZSTD_LEVEL = 22
DEFAULT_COMPACT_CHUNK_FILE_BYTES = 1024 * 1024
DEFAULT_COMPACT_CHUNK_MIN_BYTES = 256 * 1024
DEFAULT_COMPACT_CHUNK_AVG_BYTES = 1024 * 1024
DEFAULT_COMPACT_CHUNK_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_COMPACT_PACK_TARGET_BYTES = 256 * 1024 * 1024
DEFAULT_COMPACT_KEEP_INCOMING_DAYS = 1

STORAGE_LEGACY = "legacy"
STORAGE_HOT_COLD = "hot-cold"
PULL_MODE_COLD = "cold"
PULL_MODE_CLIENT_BUNDLE = "client-bundle"
PULL_MODE_RAW_STREAM = "raw-stream"
PULL_MODES = frozenset({PULL_MODE_COLD, PULL_MODE_CLIENT_BUNDLE, PULL_MODE_RAW_STREAM})


@dataclass(frozen=True)
class CompactConfig:
    storage: str = STORAGE_HOT_COLD
    zstd_level: int = DEFAULT_COMPACT_ZSTD_LEVEL
    manifest_zstd_level: int = DEFAULT_COMPACT_MANIFEST_ZSTD_LEVEL
    chunk_file_bytes: int = DEFAULT_COMPACT_CHUNK_FILE_BYTES
    chunk_min_bytes: int = DEFAULT_COMPACT_CHUNK_MIN_BYTES
    chunk_avg_bytes: int = DEFAULT_COMPACT_CHUNK_AVG_BYTES
    chunk_max_bytes: int = DEFAULT_COMPACT_CHUNK_MAX_BYTES
    pack_target_bytes: int = DEFAULT_COMPACT_PACK_TARGET_BYTES
    keep_incoming_days: int = DEFAULT_COMPACT_KEEP_INCOMING_DAYS
    scope: str = "tenant"
    default_pull_mode: str = PULL_MODE_COLD
    raw_stream_enabled: bool = False

    @property
    def hot_cold_enabled(self) -> bool:
        return self.storage == STORAGE_HOT_COLD

    @property
    def supported_pull_modes(self) -> tuple[str, ...]:
        modes = [PULL_MODE_COLD, PULL_MODE_CLIENT_BUNDLE]
        if self.raw_stream_enabled:
            modes.append(PULL_MODE_RAW_STREAM)
        return tuple(modes)


def resolve_cors_origins(origins: Iterable[str] | None = None) -> tuple[str, ...]:
    raw: list[str] = []
    if origins is not None:
        raw.extend(str(item) for item in origins)
    env = os.environ.get("SNAPZ_SERVER_CORS_ORIGIN", "")
    if env:
        raw.extend(env.split(","))
    return tuple(origin.strip().rstrip("/") for origin in raw if origin.strip())


def resolve_max_bundle_bytes(value: int | None = None) -> int:
    if value is None:
        raw = os.environ.get("SNAPZ_SERVER_MAX_BUNDLE_MB", "")
        if raw:
            try:
                value = int(raw) * 1024 * 1024
            except ValueError:
                value = DEFAULT_MAX_BUNDLE_BYTES
        else:
            value = DEFAULT_MAX_BUNDLE_BYTES
    return max(1, int(value))


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def resolve_pull_transfer_mode(value: str | None = None) -> str:
    raw = (
        value
        if value is not None
        else os.environ.get("SNAPZ_PULL_TRANSFER_MODE", PULL_MODE_COLD)
    )
    mode = str(raw or PULL_MODE_COLD).strip()
    if mode not in PULL_MODES:
        return PULL_MODE_COLD
    return mode


def resolve_compact_config() -> CompactConfig:
    storage = os.environ.get("SNAPZ_SERVER_STORAGE", STORAGE_HOT_COLD).strip()
    if storage not in {STORAGE_LEGACY, STORAGE_HOT_COLD}:
        storage = STORAGE_HOT_COLD
    scope = os.environ.get("SNAPZ_COMPACT_SCOPE", "tenant").strip() or "tenant"
    if scope != "tenant":
        scope = "tenant"
    return CompactConfig(
        storage=storage,
        zstd_level=_env_int(
            "SNAPZ_COMPACT_ZSTD_LEVEL",
            DEFAULT_COMPACT_ZSTD_LEVEL,
            min_value=1,
            max_value=22,
        ),
        manifest_zstd_level=_env_int(
            "SNAPZ_COMPACT_MANIFEST_ZSTD_LEVEL",
            DEFAULT_COMPACT_MANIFEST_ZSTD_LEVEL,
            min_value=1,
            max_value=22,
        ),
        chunk_file_bytes=_env_int(
            "SNAPZ_COMPACT_CHUNK_FILE_BYTES",
            DEFAULT_COMPACT_CHUNK_FILE_BYTES,
            min_value=1,
            max_value=1024 * 1024 * 1024,
        ),
        chunk_min_bytes=_env_int(
            "SNAPZ_COMPACT_CHUNK_MIN_BYTES",
            DEFAULT_COMPACT_CHUNK_MIN_BYTES,
            min_value=1,
            max_value=1024 * 1024 * 1024,
        ),
        chunk_avg_bytes=_env_int(
            "SNAPZ_COMPACT_CHUNK_AVG_BYTES",
            DEFAULT_COMPACT_CHUNK_AVG_BYTES,
            min_value=1,
            max_value=1024 * 1024 * 1024,
        ),
        chunk_max_bytes=_env_int(
            "SNAPZ_COMPACT_CHUNK_MAX_BYTES",
            DEFAULT_COMPACT_CHUNK_MAX_BYTES,
            min_value=1,
            max_value=1024 * 1024 * 1024,
        ),
        pack_target_bytes=_env_int(
            "SNAPZ_COMPACT_PACK_TARGET_BYTES",
            DEFAULT_COMPACT_PACK_TARGET_BYTES,
            min_value=1024 * 1024,
            max_value=1024 * 1024 * 1024 * 1024,
        ),
        keep_incoming_days=_env_int(
            "SNAPZ_COMPACT_KEEP_INCOMING_DAYS",
            DEFAULT_COMPACT_KEEP_INCOMING_DAYS,
            min_value=0,
            max_value=3650,
        ),
        scope=scope,
        default_pull_mode=resolve_pull_transfer_mode(),
        raw_stream_enabled=_env_bool("SNAPZ_ENABLE_RAW_STREAM_PULL", False),
    )


def resolve_tls_path(value: str | None, *, env_name: str) -> str:
    raw = value if value is not None else os.environ.get(env_name, "")
    return str(raw or "").strip()


def enable_tls(server) -> None:
    certfile = server.tls_certfile
    keyfile = server.tls_keyfile
    client_ca = server.tls_client_ca
    if client_ca and (not certfile or not keyfile):
        raise ValueError("TLS client CA requires TLS certfile and keyfile")
    if not certfile and not keyfile:
        return
    if not certfile or not keyfile:
        raise ValueError("both TLS certfile and keyfile are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    if client_ca:
        context.load_verify_locations(cafile=client_ca)
        context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
