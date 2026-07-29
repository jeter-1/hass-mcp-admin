"""Synthetic, test-only signing helpers for the inert registry foundation."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from ha_mcp_engineering.signed_registry import (
    TrustAnchorStore,
    canonical_json,
)


TEST_KEY_ID = "synthetic-test-key-v1"
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
FINGERPRINT = "1" * 64
SECOND_FINGERPRINT = "2" * 64
DIGEST = "sha256:" + "3" * 64
SECOND_DIGEST = "sha256:" + "4" * 64
COMMIT = "5" * 40
SECOND_COMMIT = "6" * 40


def utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def reviewed_entry(
    *,
    entry_id: str = "ha-mcp-v7.14.2-synthetic",
    version: str = "7.14.2",
    image_index_digest: str = DIGEST,
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "approval_status": "reviewed",
        "server_name": "ha-mcp",
        "version": version,
        "allowed_protocol_versions": ["2025-03-26"],
        "source_repository": (
            "https://github.com/homeassistant-ai/ha-mcp"
        ),
        "release_tag": f"v{version}",
        "source_commit": COMMIT,
        "image_index_digest": image_index_digest,
        "architecture_image_digests": {
            "linux/amd64": SECOND_DIGEST,
            "linux/arm64": "sha256:" + "7" * 64,
        },
        "image_revision": SECOND_COMMIT,
        "advertised_tool_count": 1,
        "catalog_fingerprint": FINGERPRINT,
        "capture_resource": (
            f"docs/evidence/upstream-read-compatibility/"
            f"ha-mcp-{version}.json"
        ),
        "capture_sha256": "sha256:" + "8" * 64,
        "capture_format_version": 1,
        "policy_resource": "upstream_tool_policy_7_14_2.json",
        "policy_sha256": "sha256:" + "9" * 64,
        "review_provenance": [
            "Synthetic reviewed evidence for registry unit tests."
        ],
        "review_date": "2026-07-29",
        "dashboard_attestation": {
            "status": "reviewed",
            "entry_id": entry_id,
            "attestation_fingerprint": SECOND_FINGERPRINT,
            "compiled_constraints_fingerprint": "a" * 64,
        },
        "error_contract_fingerprint": "b" * 64,
        "entity_lookup_missing_resource_status": (
            "ambiguous_upstream_service_call_failed"
        ),
        "tool_contracts": [
            {
                "tool_name": "ha_search",
                "input_schema_fingerprint": "c" * 64,
                "description_fingerprint": "d" * 64,
                "annotation_fingerprint": "e" * 64,
                "output_contract_fingerprint": "f" * 64,
                "runtime_contract_fingerprint": "0" * 64,
                "policy_classification": "automatic_read",
                "reviewed_automatic_read": True,
                "quarantine_reason": None,
                "argument_restrictions": [],
            }
        ],
        "provider_argument_constraints": [
            {
                "provider_id": "upstream_read_gateway",
                "tool_name": "ha_search",
                "constraints_fingerprint": "c" * 64,
            }
        ],
    }


def revocation(
    *,
    entry_id: str = "ha-mcp-v7.14.1-synthetic",
    version: str = "7.14.1",
    image_index_digest: str = SECOND_DIGEST,
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "server_name": "ha-mcp",
        "version": version,
        "image_index_digest": image_index_digest,
        "revoked_at": utc(NOW - timedelta(minutes=2)),
        "reason": "Synthetic test-only revocation.",
    }


class RegistrySigner:
    """Ephemeral private signing material that exists only in tests."""

    def __init__(
        self,
        *,
        key_id: str = TEST_KEY_ID,
        private_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.key_id = key_id
        self.private_key = (
            private_key or Ed25519PrivateKey.generate()
        )

    @property
    def trust_anchors(self) -> TrustAnchorStore:
        return TrustAnchorStore(
            {self.key_id: self.private_key.public_key()}
        )

    @property
    def public_key_base64(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")

    def unsigned(
        self,
        *,
        sequence: int = 1,
        previous_registry_sha256: str | None = None,
        generated_at: datetime | None = None,
        expires_at: datetime | None = None,
        entries: list[dict[str, Any]] | None = None,
        revocations: list[dict[str, Any]] | None = None,
        registry_id: str = "ha-mcp-reviewed-releases",
        schema_version: int = 1,
    ) -> dict[str, Any]:
        generated_at = generated_at or NOW - timedelta(minutes=1)
        expires_at = expires_at or NOW + timedelta(days=1)
        return {
            "schema_version": schema_version,
            "registry_id": registry_id,
            "sequence": sequence,
            "generated_at": utc(generated_at),
            "expires_at": utc(expires_at),
            "previous_registry_sha256": previous_registry_sha256,
            "key_id": self.key_id,
            "entries": (
                [reviewed_entry()]
                if entries is None
                else deepcopy(entries)
            ),
            "revocations": (
                [] if revocations is None else deepcopy(revocations)
            ),
        }

    def sign_mapping(
        self,
        unsigned: dict[str, Any],
    ) -> dict[str, Any]:
        signature = self.private_key.sign(canonical_json(unsigned))
        return {
            **deepcopy(unsigned),
            "signature": base64.b64encode(signature).decode("ascii"),
        }

    def raw(self, **kwargs: Any) -> bytes:
        return canonical_json(self.sign_mapping(self.unsigned(**kwargs)))

    def raw_with_order(
        self,
        unsigned: dict[str, Any],
        order: tuple[str, ...],
    ) -> bytes:
        signed = self.sign_mapping(unsigned)
        ordered = {key: signed[key] for key in order}
        return json.dumps(
            ordered,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
