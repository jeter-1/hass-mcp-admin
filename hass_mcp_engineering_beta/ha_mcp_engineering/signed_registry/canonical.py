"""Canonical JSON and content digests for signed compatibility registries."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Serialize one JSON value deterministically as strict UTF-8."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(value: Any) -> str:
    """Return the repository-standard prefixed digest of canonical JSON."""

    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()
