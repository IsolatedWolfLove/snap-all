"""Server startup configuration helpers."""

from __future__ import annotations

import os
import ssl
from typing import Iterable


DEFAULT_MAX_BUNDLE_BYTES = 10 * 1024 * 1024 * 1024


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
