"""Immutable beta/RC server identity and validated build metadata."""

import os
import re
from datetime import datetime


_UNKNOWN_BUILD_VALUE = "unknown"
_BUILD_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_BUILD_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _normalize_build_sha(value: str | None) -> str:
    """Accept only a complete lowercase Git object ID from the image build."""
    candidate = (value or "").strip()
    return candidate if _BUILD_SHA.fullmatch(candidate) else _UNKNOWN_BUILD_VALUE


def _normalize_build_time(value: str | None) -> str:
    """Accept only a bounded UTC RFC3339 timestamp with second precision."""
    candidate = (value or "").strip()
    if not _BUILD_TIME.fullmatch(candidate):
        return _UNKNOWN_BUILD_VALUE
    try:
        datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _UNKNOWN_BUILD_VALUE
    return candidate

SERVER_NAME = "HA MCP Engineering Server Beta"
SERVER_ID = "hass-mcp-engineering-beta"
SERVER_VERSION = "2.0.0-rc.2.rc3a.1"
SCHEMA_VERSION = "1"
BUILD_SHA = _normalize_build_sha(os.environ.get("HAMCP_BUILD_SHA"))
BUILD_TIME = _normalize_build_time(os.environ.get("HAMCP_BUILD_TIME"))
