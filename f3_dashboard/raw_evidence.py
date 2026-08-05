"""Strict construction of complete internal dashboard preread evidence."""

from __future__ import annotations

from datetime import datetime
import hmac
from typing import Final

from .constants import (
    CANONICAL_URL_PATH,
    MAX_RAW_CONFIG_BYTES,
    PROTOCOL_VERSION,
    RAW_EVIDENCE_MODEL,
    SUPPORTED_UPSTREAM_VERSIONS,
    UPSTREAM_CONFIG_HASH,
)
from .errors import RawEvidenceError
from .json_codec import clone_json, engineering_sha256, serialized_size, upstream_config_hash
from .models import DashboardPreread, RawDashboardEvidence


EXPECTED_RELEASES: Final = {
    "7.14.2": (
        "ha-mcp-v7.14.2-7917b2d3",
        "ha_mcp_dashboard_read_v2",
    ),
    "8.0.0": (
        "ha-mcp-v8.0.0-d65630f6",
        "ha_mcp_dashboard_read_v3",
    ),
}


def _require_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise RawEvidenceError("Preread timestamp is invalid")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RawEvidenceError("Preread timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise RawEvidenceError("Preread timestamp must include a timezone")


def build_raw_dashboard_evidence(
    preread: DashboardPreread,
    *,
    requested_url_path: str,
) -> RawDashboardEvidence:
    """Validate an exact, complete, storage-mode preread and bind its hashes."""

    if (
        not isinstance(requested_url_path, str)
        or requested_url_path != requested_url_path.strip()
        or not CANONICAL_URL_PATH.fullmatch(requested_url_path)
        or requested_url_path == "default"
    ):
        raise RawEvidenceError("Dashboard url_path is not exact and canonical")
    if preread.canonical_url_path != requested_url_path:
        raise RawEvidenceError("Preread target identity does not match the request")
    if preread.upstream_version not in SUPPORTED_UPSTREAM_VERSIONS:
        raise RawEvidenceError("Unsupported exact upstream release")
    if preread.protocol_version != PROTOCOL_VERSION:
        raise RawEvidenceError("Unsupported MCP protocol")
    expected_entry, expected_contract = EXPECTED_RELEASES[preread.upstream_version]
    if preread.compatibility_entry != expected_entry:
        raise RawEvidenceError("Compatibility entry does not match the exact release")
    if preread.dashboard_contract_model != expected_contract:
        raise RawEvidenceError("Dashboard read contract does not match the release")
    if preread.completeness != "complete":
        raise RawEvidenceError("Partial dashboard prereads are prohibited")
    if not preread.configuration_returned:
        raise RawEvidenceError("Exact dashboard configuration is missing")
    if preread.sanitized or preread.truncated:
        raise RawEvidenceError("Sanitized or truncated dashboard data is prohibited")
    if not isinstance(preread.configuration, dict):
        raise RawEvidenceError("Dashboard configuration must be an object")
    _require_timestamp(preread.preread_at)

    exact_rows = [
        row for row in preread.inventory if row.url_path == requested_url_path
    ]
    if len(exact_rows) != 1:
        raise RawEvidenceError("Dashboard inventory identity is absent or ambiguous")
    if exact_rows[0].mode != "storage":
        raise RawEvidenceError("Only an explicit storage-mode dashboard is eligible")

    if not isinstance(preread.config_hash, str) or not UPSTREAM_CONFIG_HASH.fullmatch(
        preread.config_hash
    ):
        raise RawEvidenceError("Upstream config_hash is missing or malformed")
    try:
        expected_upstream_hash = upstream_config_hash(preread.configuration)
        evidence_hash = engineering_sha256(preread.configuration)
        size = serialized_size(preread.configuration, ensure_ascii=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RawEvidenceError("Dashboard configuration is not exact JSON data") from exc
    if not hmac.compare_digest(preread.config_hash, expected_upstream_hash):
        raise RawEvidenceError("Upstream config_hash does not match the raw configuration")
    if size > MAX_RAW_CONFIG_BYTES:
        raise RawEvidenceError("Raw dashboard configuration exceeds the reviewed bound")

    return RawDashboardEvidence(
        model=RAW_EVIDENCE_MODEL,
        canonical_url_path=requested_url_path,
        storage_mode_confirmed=True,
        configuration=clone_json(preread.configuration),
        upstream_config_hash=preread.config_hash,
        engineering_config_sha256=evidence_hash,
        serialized_size_bytes=size,
        preread_at=preread.preread_at,
        upstream_version=preread.upstream_version,
        protocol_version=preread.protocol_version,
        compatibility_entry=preread.compatibility_entry,
        dashboard_contract_model=preread.dashboard_contract_model,
        completeness=preread.completeness,
    )
