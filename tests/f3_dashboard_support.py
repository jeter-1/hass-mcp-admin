"""Synthetic-only helpers for the isolated F3-B dashboard tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3_dashboard.identity import (  # noqa: E402
    build_operational_identity,
    build_provider_authority,
    reviewed_tool_contract_hash,
)
from ha_mcp_engineering.f3_dashboard.json_codec import (  # noqa: E402
    engineering_sha256,
    upstream_config_hash,
)
from ha_mcp_engineering.f3_dashboard.models import (
    DashboardInventoryRow,
    DashboardPreread,
    DashboardUpdateProposal,
)
from ha_mcp_engineering.f3_dashboard.planning import create_dashboard_update_plan
from ha_mcp_engineering.f3_dashboard.provider import EXACT_CONTRACTS
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)


FIXTURE = Path(__file__).parent / "fixtures" / "f3_dashboard" / "storage_dashboard.json"


def load_dashboard() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_home_dashboard() -> dict[str, Any]:
    return json.loads(
        (FIXTURE.parent / "home_dashboard_existing.json").read_text(encoding="utf-8")
    )


def home_dashboard_patch_operations() -> list[dict[str, Any]]:
    return json.loads(
        (FIXTURE.parent / "home_dashboard_patch.json").read_text(encoding="utf-8")
    )


def make_preread(
    configuration: dict[str, Any] | None = None,
    *,
    url_path: str = "synthetic-dashboard",
    version: str = "8.1.1",
    mode: str = "storage",
    completeness: str = "complete",
    configuration_returned: bool = True,
    sanitized: bool = False,
    truncated: bool = False,
    config_hash: str | None = None,
) -> DashboardPreread:
    config = deepcopy(configuration if configuration is not None else load_dashboard())
    contract = (
        "ha_mcp_dashboard_read_v2"
        if version == "7.14.2"
        else "ha_mcp_dashboard_read_v3"
    )
    compatibility = {
        "7.14.2": "ha-mcp-v7.14.2-7917b2d3",
        "8.0.0": "ha-mcp-v8.0.0-d65630f6",
        "8.1.1": "ha-mcp-v8.1.1-e1d76a6e",
        "8.2.0": "ha-mcp-v8.2.0-dbcfc0ee",
        "8.4.1": "ha-mcp-v8.4.1-7823b365",
        "8.4.3": "ha-mcp-v8.4.3-d5cea47a",
        "8.5.0": "ha-mcp-v8.5.0-e1538bcd",
    }[version]
    release = load_reviewed_upstream_release_registry().by_version[version]
    getter = release.tool_contracts_by_name["ha_config_get_dashboard"]
    setter = EXACT_CONTRACTS[version]
    authority = build_provider_authority(
        provider_slug="ha_mcp",
        server_name="ha-mcp",
        upstream_version=version,
        protocol_version="2025-03-26",
        compatibility_entry=compatibility,
        source_commit=release.source_commit,
        image_index_digest=release.image_index_digest,
        contract_family=contract,
        dashboard_attestation_fingerprint=(
            release.dashboard_attestation_fingerprint or "0" * 64
        ),
        compiled_constraints_fingerprint=(
            release.dashboard_compiled_constraints_fingerprint or "0" * 64
        ),
        getter_contract_hash=reviewed_tool_contract_hash(getter),
        setter_contract_hash=engineering_sha256(asdict(setter)),
        catalog_fingerprint=release.catalog_fingerprint,
    )
    upstream_hash = config_hash or upstream_config_hash(config)
    operational_identity = build_operational_identity(
        authority,
        target_url_path=url_path,
        storage_mode="storage",
        baseline_upstream_config_hash=upstream_hash,
        baseline_engineering_sha256=engineering_sha256(config),
    )
    return DashboardPreread(
        inventory=(DashboardInventoryRow(url_path=url_path, mode=mode),),
        canonical_url_path=url_path,
        configuration=config,
        config_hash=upstream_hash,
        completeness=completeness,
        configuration_returned=configuration_returned,
        sanitized=sanitized,
        truncated=truncated,
        preread_at="2026-08-04T12:00:00+00:00",
        upstream_version=version,
        protocol_version="2025-03-26",
        compatibility_entry=compatibility,
        dashboard_contract_model=contract,
        operational_identity=operational_identity,
    )


class FakeExactReader:
    def __init__(self, preread: DashboardPreread) -> None:
        self.value = preread
        self.preread_count = 0
        self.mutation_count = 0

    async def preread(self, *, url_path: str) -> DashboardPreread:
        self.preread_count += 1
        return self.value


async def make_proposal(
    *,
    preread: DashboardPreread | None = None,
    operations: list[dict[str, Any]] | None = None,
    artifact_store: Any = None,
    url_path: str = "synthetic-dashboard",
    plan_id: str = "plan000000000001",
) -> tuple[DashboardUpdateProposal, FakeExactReader]:
    exact = preread or make_preread(url_path=url_path)
    reader = FakeExactReader(exact)
    proposal = await create_dashboard_update_plan(
        reader=reader,
        url_path=url_path,
        operations=operations
        or [
            {
                "operation_id": "rename-title",
                "operation": "replace",
                "path": "/title",
                "value": "Synthetic F3 dashboard updated",
            }
        ],
        title="Synthetic dashboard update",
        description="Offline fixture-only plan.",
        expiration_minutes=30,
        requested_by="test.operator",
        provider_evidence=EXACT_CONTRACTS[exact.upstream_version],
        authoritative_provider_slug="ha_mcp",
        artifact_store=artifact_store,
        now=datetime(2026, 8, 4, 12, 5, tzinfo=timezone.utc),
        plan_id=plan_id,
    )
    return proposal, reader


# HAMCP-143: one synthetic boundary pair per publicly reachable compiler
# limit.  Every case carries a private marker in its keys, paths or values so
# tests can prove the refusal diagnostics never echo caller or dashboard data.
PRIVATE_KEY = "synthetic_private_marker"
PRIVATE_TEXT = "synthetic-private-text"


@dataclass(frozen=True)
class CompilerLimitCase:
    constraint: str
    stage: str
    code: str
    limit: int
    observed: int | None
    configuration: dict[str, Any]
    over_operations: list[dict[str, Any]]
    at_limit_operations: list[dict[str, Any]]
    # None when the at-limit patch compiles; otherwise the different reviewed
    # constraint that refuses it first (the tested bound itself is not hit).
    at_limit_refusal: str | None = None
    # The at-limit dashboard when the boundary is set by its size.
    at_limit_configuration: dict[str, Any] | None = None

    @property
    def at_limit_config(self) -> dict[str, Any]:
        if self.at_limit_configuration is None:
            return self.configuration
        return self.at_limit_configuration

    def expected_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "reason": self.code,
            "dashboard_error_code": self.code,
            "constraint": self.constraint,
            "stage": self.stage,
            "limit": self.limit,
        }
        if self.observed is not None:
            details["observed"] = self.observed
        return details


def _patch_op(
    operation_id: str, kind: str, path: str, value: Any = ...
) -> dict[str, Any]:
    row: dict[str, Any] = {"operation_id": operation_id, "operation": kind, "path": path}
    if value is not ...:
        row["value"] = value
    return row


def _nested(depth: int) -> Any:
    value: Any = PRIVATE_TEXT
    for _ in range(depth):
        value = [value]
    return value


def _nested_mapping(levels: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current = root
    for _ in range(levels):
        current[PRIVATE_KEY] = {}
        current = current[PRIVATE_KEY]
    return root


def _size(value: Any, *, ensure_ascii: bool = False) -> int:
    return len(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii
        ).encode("utf-8")
    )


def _padded_text(target_bytes: int) -> str:
    """A JSON string whose canonical serialization is exactly target_bytes."""

    return PRIVATE_TEXT + "x" * (target_bytes - 2 - len(PRIVATE_TEXT))


def _patch_bytes_operations(target: int) -> list[dict[str, Any]]:
    operations = [
        _patch_op(f"bytes_{index}", "add", f"/{PRIVATE_KEY}_{index}", "")
        for index in range(3)
    ]
    remaining = target - _size(operations)
    for index, row in enumerate(operations):
        share = remaining // 3 + (1 if index < remaining % 3 else 0)
        row["value"] = (PRIVATE_TEXT + "x" * share)[:share] if share else ""
    assert _size(operations) == target
    return operations


def _patch_node_operations(nodes: int) -> list[dict[str, Any]]:
    # One list node plus, per operation, the row, three strings and a value
    # list whose own node is counted with its items.
    items = nodes - 1 - 3 * 5
    shares = [items // 3 + (1 if index < items % 3 else 0) for index in range(3)]
    return [
        _patch_op(
            f"nodes_{index}", "add", f"/{PRIVATE_KEY}_{index}",
            [PRIVATE_TEXT] + [0] * (share - 1),
        )
        for index, share in enumerate(shares)
    ]


def _result_bytes_case(target: int) -> list[dict[str, Any]]:
    base = {PRIVATE_KEY: _padded_text(39_000), "value": 0}
    text = PRIVATE_TEXT
    result = dict(base, value=text)
    text += "x" * (target - _size(result, ensure_ascii=True))
    assert _size(dict(base, value=text), ensure_ascii=True) == target
    return [_patch_op("result_bytes", "replace", "/value", text)]


def _growth_operations(growth: int) -> list[dict[str, Any]]:
    base = {PRIVATE_KEY: 0}
    text = "é" * 2_730
    text += "x" * (growth - (_size({PRIVATE_KEY: text}, ensure_ascii=True) - _size(base)))
    assert _size({PRIVATE_KEY: text}, ensure_ascii=True) - _size(base) == growth
    return [_patch_op("growth", "replace", f"/{PRIVATE_KEY}", text)]


def compiler_limit_cases() -> tuple[CompilerLimitCase, ...]:
    invalid = "invalid_dashboard_patch"
    failed = "dashboard_patch_compilation_failed"
    pointer_parent = "/" + "/".join([PRIVATE_KEY] * 31)
    return (
        CompilerLimitCase(
            "operation_count", "validation", invalid, 16, 17, {},
            [_patch_op(f"op_{i}", "add", f"/{PRIVATE_KEY}_{i}", PRIVATE_TEXT) for i in range(17)],
            [_patch_op(f"op_{i}", "add", f"/{PRIVATE_KEY}_{i}", PRIVATE_TEXT) for i in range(16)],
        ),
        CompilerLimitCase(
            "pointer_length", "validation", invalid, 1_024, 1_025, {},
            [_patch_op("long", "add", "/" + PRIVATE_KEY + "x" * (1_024 - len(PRIVATE_KEY)), 0)],
            [_patch_op("long", "add", "/" + PRIVATE_KEY + "x" * (1_023 - len(PRIVATE_KEY)), 0)],
        ),
        CompilerLimitCase(
            "pointer_depth", "validation", invalid, 32, 33, _nested_mapping(32),
            [_patch_op("deep", "add", pointer_parent + f"/{PRIVATE_KEY}/leaf", PRIVATE_TEXT)],
            [_patch_op("deep", "add", pointer_parent + "/leaf", PRIVATE_TEXT)],
        ),
        CompilerLimitCase(
            "value_json_depth", "validation", invalid, 48, None, {},
            [_patch_op("deep_value", "add", f"/{PRIVATE_KEY}", _nested(49))],
            [_patch_op("deep_value", "add", f"/{PRIVATE_KEY}", _nested(48))],
            at_limit_refusal="patch_json_depth",
        ),
        CompilerLimitCase(
            "value_json_nodes", "validation", invalid, 10_000, None, {},
            [_patch_op("wide_value", "add", f"/{PRIVATE_KEY}", [PRIVATE_TEXT] + [0] * 9_999)],
            [_patch_op("wide_value", "add", f"/{PRIVATE_KEY}", [PRIVATE_TEXT] + [0] * 9_998)],
            at_limit_refusal="operation_value_bytes",
        ),
        CompilerLimitCase(
            "operation_value_bytes", "validation", invalid, 8_192, 8_193, {},
            [_patch_op("large_value", "add", f"/{PRIVATE_KEY}", _padded_text(8_193))],
            [_patch_op("large_value", "add", f"/{PRIVATE_KEY}", _padded_text(8_192))],
        ),
        CompilerLimitCase(
            "patch_json_depth", "validation", invalid, 48, None, {},
            [_patch_op("deep_patch", "add", f"/{PRIVATE_KEY}", _nested(47))],
            [_patch_op("deep_patch", "add", f"/{PRIVATE_KEY}", _nested(46))],
        ),
        CompilerLimitCase(
            "patch_json_nodes", "validation", invalid, 10_000, None, {},
            _patch_node_operations(10_001),
            _patch_node_operations(10_000),
            at_limit_refusal="patch_bytes",
        ),
        CompilerLimitCase(
            "patch_bytes", "validation", invalid, 16_384, 16_385, {},
            _patch_bytes_operations(16_385),
            _patch_bytes_operations(16_384),
        ),
        CompilerLimitCase(
            "traversal_nodes", "compilation", failed, 10_000, None,
            {PRIVATE_KEY: [0] * 9_998},
            [_patch_op("broad", "replace", f"/{PRIVATE_KEY}", PRIVATE_TEXT)],
            [_patch_op("broad", "replace", f"/{PRIVATE_KEY}", PRIVATE_TEXT)],
            at_limit_refusal="semantic_leaf_changes",
            at_limit_configuration={PRIVATE_KEY: [0] * 9_997},
        ),
        CompilerLimitCase(
            "result_json_nodes", "compilation", invalid, 10_000, None,
            {PRIVATE_KEY: [0] * 9_998},
            [_patch_op("insert", "add", f"/{PRIVATE_KEY}/0", 0)],
            [_patch_op("insert", "add", f"/{PRIVATE_KEY}/0", 0)],
            at_limit_configuration={PRIVATE_KEY: [0] * 9_997},
        ),
        CompilerLimitCase(
            "result_json_depth", "compilation", invalid, 48, None, _nested_mapping(10),
            [_patch_op("deep_result", "add", "/" + "/".join([PRIVATE_KEY] * 10) + "/leaf", _nested(38))],
            [_patch_op("deep_result", "add", "/" + "/".join([PRIVATE_KEY] * 10) + "/leaf", _nested(37))],
        ),
        CompilerLimitCase(
            "result_config_bytes", "compilation", failed, 40_000, 40_001,
            {PRIVATE_KEY: _padded_text(39_000), "value": 0},
            _result_bytes_case(40_001),
            _result_bytes_case(40_000),
        ),
        CompilerLimitCase(
            "config_growth_bytes", "compilation", failed, 16_384, 16_385,
            {PRIVATE_KEY: 0},
            _growth_operations(16_385),
            _growth_operations(16_384),
        ),
    )
