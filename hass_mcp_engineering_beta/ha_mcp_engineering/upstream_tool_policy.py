"""Deterministic, fail-closed policy for reviewed upstream MCP tools."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


POLICY_PATH = Path(__file__).with_name("upstream_tool_policy.json")
POLICY_SCHEMA_VERSION = 1
REVIEWED_UPSTREAM_SERVER = "ha-mcp"
REVIEWED_UPSTREAM_VERSION = "7.14.1"
CLASSIFICATIONS = frozenset(
    {
        "automatic_read",
        "mixed_or_requires_wrapper",
        "persistent_write",
        "physical_or_high_risk_action",
        "prohibited",
        "unsupported",
    }
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class UpstreamToolPolicyError(ValueError):
    """A committed policy document is malformed or internally inconsistent."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def schema_fingerprint(schema: Any) -> str:
    return hashlib.sha256(canonical_json(schema)).hexdigest()


def catalog_fingerprint(tools: list[dict[str, Any]]) -> str:
    ordered = sorted(tools, key=lambda item: str(item.get("name", "")))
    return hashlib.sha256(canonical_json(ordered)).hexdigest()


@dataclass(frozen=True)
class ReviewedToolAnnotations:
    """Binary-owned MCP annotations reviewed with an exact upstream schema."""

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewedToolAnnotations":
        if not isinstance(value, dict) or set(value) != {
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        }:
            raise UpstreamToolPolicyError("policy_annotations_fields_invalid")
        if any(not isinstance(item, bool) for item in value.values()):
            raise UpstreamToolPolicyError("policy_annotations_value_invalid")
        return cls(
            read_only=value["readOnlyHint"],
            destructive=value["destructiveHint"],
            idempotent=value["idempotentHint"],
            open_world=value["openWorldHint"],
        )


@dataclass(frozen=True)
class UpstreamToolPolicyEntry:
    upstream_name: str
    exposed_name: str
    description: str
    classification: str
    input_schema_fingerprint: str
    reason: str
    collision_status: str
    collision_policy: str
    argument_restrictions: tuple[str, ...]
    response_limit_bytes: int
    timeout_seconds: float
    source_evidence: tuple[str, ...]
    reviewed_annotations: ReviewedToolAnnotations

    @classmethod
    def from_mapping(cls, value: Any) -> "UpstreamToolPolicyEntry":
        if not isinstance(value, dict):
            raise UpstreamToolPolicyError("policy_entry_invalid")
        expected = {
            "upstream_name",
            "exposed_name",
            "description",
            "classification",
            "input_schema_fingerprint",
            "reason",
            "collision_status",
            "collision_policy",
            "argument_restrictions",
            "response_limit_bytes",
            "timeout_seconds",
            "source_evidence",
            "reviewed_annotations",
        }
        if set(value) != expected:
            raise UpstreamToolPolicyError("policy_entry_fields_invalid")
        upstream_name = value["upstream_name"]
        exposed_name = value["exposed_name"]
        classification = value["classification"]
        fingerprint = value["input_schema_fingerprint"]
        if not isinstance(upstream_name, str) or not _TOOL_NAME.fullmatch(upstream_name):
            raise UpstreamToolPolicyError("policy_upstream_name_invalid")
        if not isinstance(exposed_name, str) or not _TOOL_NAME.fullmatch(exposed_name):
            raise UpstreamToolPolicyError("policy_exposed_name_invalid")
        if classification not in CLASSIFICATIONS:
            raise UpstreamToolPolicyError("policy_classification_invalid")
        if not isinstance(fingerprint, str) or not _HEX_64.fullmatch(fingerprint):
            raise UpstreamToolPolicyError("policy_schema_fingerprint_invalid")
        description = value["description"]
        reason = value["reason"]
        if not isinstance(description, str) or not 1 <= len(description) <= 500:
            raise UpstreamToolPolicyError("policy_description_invalid")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 1_000:
            raise UpstreamToolPolicyError("policy_reason_invalid")
        if value["collision_status"] not in {"none", "collides"}:
            raise UpstreamToolPolicyError("policy_collision_status_invalid")
        if value["collision_policy"] != "alias_upstream_on_collision":
            raise UpstreamToolPolicyError("policy_collision_policy_invalid")
        restrictions = value["argument_restrictions"]
        evidence = value["source_evidence"]
        if not isinstance(restrictions, list) or any(
            not isinstance(item, str) or len(item) > 256 for item in restrictions
        ):
            raise UpstreamToolPolicyError("policy_argument_restrictions_invalid")
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(item, str) or not 1 <= len(item) <= 512 for item in evidence
        ):
            raise UpstreamToolPolicyError("policy_source_evidence_invalid")
        response_limit = value["response_limit_bytes"]
        timeout = value["timeout_seconds"]
        if isinstance(response_limit, bool) or not isinstance(response_limit, int):
            raise UpstreamToolPolicyError("policy_response_limit_invalid")
        if not 4_096 <= response_limit <= 1_000_000:
            raise UpstreamToolPolicyError("policy_response_limit_invalid")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise UpstreamToolPolicyError("policy_timeout_invalid")
        if not 1 <= float(timeout) <= 300:
            raise UpstreamToolPolicyError("policy_timeout_invalid")
        reviewed_annotations = ReviewedToolAnnotations.from_mapping(
            value["reviewed_annotations"]
        )
        if classification == "automatic_read" and (
            not reviewed_annotations.read_only
            or reviewed_annotations.destructive
        ):
            raise UpstreamToolPolicyError("policy_automatic_read_annotations_invalid")
        return cls(
            upstream_name=upstream_name,
            exposed_name=exposed_name,
            description=description,
            classification=classification,
            input_schema_fingerprint=fingerprint,
            reason=reason,
            collision_status=value["collision_status"],
            collision_policy=value["collision_policy"],
            argument_restrictions=tuple(restrictions),
            response_limit_bytes=response_limit,
            timeout_seconds=float(timeout),
            source_evidence=tuple(evidence),
            reviewed_annotations=reviewed_annotations,
        )


@dataclass(frozen=True)
class UpstreamToolPolicy:
    schema_version: int
    upstream_server: str
    reviewed_upstream_version: str
    reviewed_source_tag: str
    reviewed_source_commit: str
    reviewed_stock_catalog_tool_count: int
    reviewed_stock_catalog_fingerprint: str
    tools: tuple[UpstreamToolPolicyEntry, ...]

    @property
    def by_name(self) -> dict[str, UpstreamToolPolicyEntry]:
        return {entry.upstream_name: entry for entry in self.tools}

    @property
    def classification_counts(self) -> dict[str, int]:
        counts = Counter(entry.classification for entry in self.tools)
        return {name: counts.get(name, 0) for name in sorted(CLASSIFICATIONS)}


def load_upstream_tool_policy(path: Path = POLICY_PATH) -> UpstreamToolPolicy:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpstreamToolPolicyError("policy_document_unreadable") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "upstream_server",
        "reviewed_upstream_version",
        "reviewed_source_tag",
        "reviewed_source_commit",
        "reviewed_stock_catalog_tool_count",
        "reviewed_stock_catalog_fingerprint",
        "tools",
    }:
        raise UpstreamToolPolicyError("policy_document_fields_invalid")
    if value["schema_version"] != POLICY_SCHEMA_VERSION:
        raise UpstreamToolPolicyError("policy_schema_version_invalid")
    if value["upstream_server"] != REVIEWED_UPSTREAM_SERVER:
        raise UpstreamToolPolicyError("policy_server_invalid")
    if value["reviewed_upstream_version"] != REVIEWED_UPSTREAM_VERSION:
        raise UpstreamToolPolicyError("policy_version_invalid")
    if value["reviewed_source_tag"] != "v7.14.1":
        raise UpstreamToolPolicyError("policy_source_tag_invalid")
    if value["reviewed_source_commit"] != "255acec1affa6528004a122eb83e30aee9c77713":
        raise UpstreamToolPolicyError("policy_source_commit_invalid")
    stock_tool_count = value["reviewed_stock_catalog_tool_count"]
    if (
        isinstance(stock_tool_count, bool)
        or not isinstance(stock_tool_count, int)
        or not 1 <= stock_tool_count <= 512
    ):
        raise UpstreamToolPolicyError("policy_stock_catalog_count_invalid")
    if not isinstance(value["reviewed_stock_catalog_fingerprint"], str) or not _HEX_64.fullmatch(
        value["reviewed_stock_catalog_fingerprint"]
    ):
        raise UpstreamToolPolicyError("policy_stock_catalog_fingerprint_invalid")
    if not isinstance(value["tools"], list) or not value["tools"]:
        raise UpstreamToolPolicyError("policy_tools_invalid")
    entries = tuple(UpstreamToolPolicyEntry.from_mapping(item) for item in value["tools"])
    if len(entries) != stock_tool_count:
        raise UpstreamToolPolicyError("policy_stock_catalog_count_invalid")
    names = [entry.upstream_name for entry in entries]
    if names != sorted(names) or len(names) != len(set(names)):
        raise UpstreamToolPolicyError("policy_tool_order_or_uniqueness_invalid")
    exposed = [entry.exposed_name for entry in entries]
    if len(exposed) != len(set(exposed)):
        raise UpstreamToolPolicyError("policy_exposed_name_duplicate")
    return UpstreamToolPolicy(
        schema_version=value["schema_version"],
        upstream_server=value["upstream_server"],
        reviewed_upstream_version=value["reviewed_upstream_version"],
        reviewed_source_tag=value["reviewed_source_tag"],
        reviewed_source_commit=value["reviewed_source_commit"],
        reviewed_stock_catalog_tool_count=stock_tool_count,
        reviewed_stock_catalog_fingerprint=value[
            "reviewed_stock_catalog_fingerprint"
        ],
        tools=entries,
    )
