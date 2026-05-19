"""Request and bundle payload parsing helpers."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from snapz.api import BUNDLE_META_NAME, _open_bundle_tar_reader


def decode_meta_header(raw: str) -> dict[str, Any]:
    if not raw:
        raise ValueError("missing X-Snapz-Source-Meta")
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True)
        data = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid X-Snapz-Source-Meta") from exc
    if not isinstance(data, dict):
        raise ValueError("source metadata must be an object")
    return data


def read_bundle_meta(bundle: Path) -> dict[str, Any]:
    with _open_bundle_tar_reader(bundle) as tar:
        try:
            member = tar.getmember(BUNDLE_META_NAME)
        except KeyError as exc:
            raise ValueError(f"bundle missing {BUNDLE_META_NAME}") from exc
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"bundle member is not readable: {BUNDLE_META_NAME}")
        try:
            data = json.loads(extracted.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"bundle has invalid JSON: {BUNDLE_META_NAME}") from exc
    if not isinstance(data, dict):
        raise ValueError("bundle metadata must be an object")
    return data
