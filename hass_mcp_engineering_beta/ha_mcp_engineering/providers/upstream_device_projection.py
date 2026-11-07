"""Compiled ``ha_get_device`` allowlist projection (HAMCP-319 section 6.5, DCL2).

No Thread, Matter, Zigbee or Z-Wave dataset or credential material passes any
reviewed version. This adds no upstream call and no Core authority.
"""
from __future__ import annotations

from typing import Any

REVIEWED_VERSIONS = frozenset({"8.5.0", "9.1.0"})


def project_device_result(payload: Any, *, upstream_version: str) -> Any:
    """Return the allowlisted device result; ``ValueError`` fails closed."""

    if upstream_version != "9.1.0":
        return payload
    raise ValueError("device_projection_unavailable")
