"""Replay the sanitized Beta 52 HAMCP-089 production capture offline.

The Engineering runtime and the reusable replay transports are imported only
from ``--source-root``.  The fixture contributes source configurations and
bounded registry evidence; persisted obligations, profiles, classifications,
and target outcomes are never injected into the dependency or risk layers.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace


_OBLIGATION_NORMALIZED_FIELDS = (
    "candidate_entity_ids",
    "configuration_path",
    "context_provenance",
    "external_template_name",
    "ledger_outcome",
    "limit_exceeded",
    "literal_selectors",
    "lock_projection",
    "obligation_kind",
    "possible_entity_domains",
    "reason_code",
    "relation",
    "semantic_category",
    "semantic_registry_version",
    "source_object_id",
    "target_outcome",
    "target_selector_scope",
)

_PROFILE_NORMALIZED_FIELDS = (
    "action_domain_count",
    "action_domains",
    "analysis_complete",
    "automation_id",
    "automation_resource_id",
    "complete",
    "effect_data_count",
    "effect_projection_clipped",
    "effect_projection_model",
    "effect_target_count",
    "effect_targets",
    "physical_consequence",
    "presentation_truncated",
    "processing_action_depth_limit",
    "processing_action_step_limit",
    "processing_effect_depth_limit",
    "processing_effect_node_limit",
    "processing_limit_exceeded",
    "processing_limit_reason",
    "processing_observed_action_step_count",
    "processing_observed_effect_node_count",
    "reason_code_count",
    "reason_codes",
    "relationships",
    "semantic_complete",
    "service_count",
    "services",
    "truncated",
)


def _normalized_semantic_rows(
    values: list[dict], fields: tuple[str, ...]
) -> list[dict]:
    """Return the complete semantic row multiset without hash identities."""

    rows = [
        {field: copy.deepcopy(value.get(field)) for field in fields}
        for value in values
    ]
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )


def _source_commit(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _require_source_imports(
    source_root: Path, values: tuple[object, ...]
) -> None:
    for value in values:
        module = sys.modules[value.__module__]
        module_path = Path(module.__file__).resolve()
        if not module_path.is_relative_to(source_root):
            raise AssertionError(
                "historical replay imported outside source root: "
                f"{module_path}"
            )


def _observed_home_assistant_version(fixture: dict) -> str:
    for item in fixture["plan_summary"]["risk"]["evidence"]:
        if item.get("trigger") == "reviewed_semantics_version_admission":
            version = item.get("observed_version")
            if isinstance(version, str) and version:
                return version
    raise AssertionError("captured Home Assistant version is unavailable")


def _transport_fixture(fixture: dict) -> dict:
    """Project only captured source and registry evidence for replay I/O."""

    sources_by_resource: dict[str, str] = {}
    for item in fixture["plan_to_source_reconciliation"]:
        resource_id = item["automation_resource_id"]
        source = item["source_object_id"]
        previous = sources_by_resource.setdefault(resource_id, source)
        if previous != source:
            raise AssertionError("resource-to-source evidence is conflicting")

    configurations = []
    for item in fixture["source_configurations"]:
        resource_id = item["resource_id"]
        source = sources_by_resource.get(resource_id)
        if not isinstance(source, str) or not source.startswith("automation."):
            raise AssertionError("captured automation identity is unavailable")
        configurations.append(
            {
                "source": source,
                "resource_id": resource_id,
                "configuration": copy.deepcopy(item["configuration"]),
            }
        )

    labels = []
    for item in fixture["membership_evidence"]["labels"]:
        selector = item["selector"]
        members = list(item["members"])
        labels.append(
            {
                # The sanitized replay explicitly binds each literal selector
                # to this captured membership.  Reusing the selector as the
                # synthetic registry ID exercises Home Assistant's reviewed
                # ID-first lookup without inventing a raw identity.
                "label_id": selector,
                "members": members,
                "member_domains": sorted(
                    {member.partition(".")[0] for member in members}
                ),
            }
        )

    target = fixture["read_only_accounting"]["target_state_baseline"][
        "entity_id"
    ]
    target_labels = fixture["membership_evidence"]["target_labels"]
    return {
        "provenance": {
            "home_assistant_version": _observed_home_assistant_version(
                fixture
            )
        },
        "configurations": configurations,
        "membership_evidence": {
            "domain_evidence": {},
            "labels": labels,
            "groups": [
                {
                    "group_id": item["group_id"],
                    "members": list(item["expanded_members"]),
                }
                for item in fixture["membership_evidence"]["groups"]
            ],
            "zones": [],
            "target_helper": {
                "entity_id": target,
                "labels": list(target_labels),
            },
        },
        "target_entity_id": target,
    }


async def _replay(
    source_root: Path,
    fixture_path: Path,
    *,
    entity_registry_mode: str = "complete",
) -> dict:
    source_root = source_root.resolve()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    transport_fixture = _transport_fixture(fixture)

    sys.path.insert(0, str(source_root))
    sys.path.insert(0, str(source_root / "hass_mcp_engineering_beta"))

    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import (
        DirectHaDependencyProvider,
        MAX_EXPAND_SNAPSHOT_ENTITIES,
    )
    from ha_mcp_engineering.f3.operational_locks import (
        OperationalLockSetCalculator,
    )
    from ha_mcp_engineering.governance.helper_dependency import (
        HelperDependencyRiskService,
    )
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from tests.test_beta37_exact_helper_state import (
        Clock,
        FakeHelperStateGateway,
        UnusedLegacyGateway,
    )
    from tests.test_beta50_helper_production_target_scope import (
        CapturedBeta50ReplayRest,
        CapturedBeta50ReplayWebSocket,
    )

    _require_source_imports(
        source_root,
        (
            DependencyIndex,
            DirectHaDependencyProvider,
            OperationalLockSetCalculator,
            HelperDependencyRiskService,
            ChangeGovernanceService,
            ChangePlanRepository,
            Clock,
            FakeHelperStateGateway,
            UnusedLegacyGateway,
            CapturedBeta50ReplayRest,
            CapturedBeta50ReplayWebSocket,
        ),
    )

    class ReplayRest(CapturedBeta50ReplayRest):
        async def request(self, method: str, path: str):
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                raise AssertionError(
                    f"offline replay refused non-read REST request: {method}"
                )
            return await super().request(method, path)

    class ReplayWebSocket(CapturedBeta50ReplayWebSocket):
        async def command(self, payload: dict):
            if payload not in (
                {"type": "config/entity_registry/list"},
                {"type": "config/label_registry/list"},
            ):
                raise AssertionError(
                    "offline replay refused non-read WebSocket request"
                )
            result = await super().command(payload)
            if payload != {"type": "config/entity_registry/list"}:
                return result
            result = list(result)
            selected = next(
                (
                    item
                    for item in result
                    if isinstance(item.get("labels"), list)
                    and len(item["labels"]) >= 2
                ),
                None,
            )
            if selected is None:
                raise AssertionError(
                    "replay has no entity shared by two literal labels"
                )
            if entity_registry_mode == "identical_duplicate":
                result.append(copy.deepcopy(selected))
            elif entity_registry_mode == "conflicting_duplicate":
                conflict = copy.deepcopy(selected)
                conflict["labels"] = []
                result.append(conflict)
            elif entity_registry_mode == "malformed_relevant":
                selected["entity_id"] = "malformed"
            elif entity_registry_mode == "raw_overflow":
                result.extend(
                    {
                        "entity_id": (
                            f"sensor.synthetic_overflow_{index:05d}"
                        ),
                        "labels": [],
                        "platform": "synthetic",
                    }
                    for index in range(
                        MAX_EXPAND_SNAPSHOT_ENTITIES + 1 - len(result)
                    )
                )
            elif entity_registry_mode != "complete":
                raise AssertionError(entity_registry_mode)
            return result

    rest = ReplayRest(transport_fixture)
    websocket = ReplayWebSocket(transport_fixture, rest.ids)
    index = DependencyIndex(
        DirectHaDependencyProvider(rest, websocket, concurrency=4)
    )
    snapshot, rebuilt, _lookup_ms = await index.get(refresh=True)
    if not rebuilt:
        raise AssertionError("replay did not build a fresh dependency index")

    target = transport_fixture["target_entity_id"]
    risk_reader = HelperDependencyRiskService(index).assess
    helper = FakeHelperStateGateway()
    helper.entity_id = target
    with tempfile.TemporaryDirectory() as temporary:
        governance = ChangeGovernanceService(
            ChangePlanRepository(Path(temporary) / "plans"),
            UnusedLegacyGateway(),
            now=Clock(),
            helper_state_gateway=helper,
            helper_dependency_risk_reader=risk_reader,
            plan_observability_cursor_key=b"beta52-production-replay" * 2,
        )
        created = await governance.create_helper_state_plan(
            entity_id=target,
            desired_state="on",
        )
    plan = created["plan"]
    binding = plan["operational"]["baseline"]["dependency_risk"]
    operation = SimpleNamespace(
        validate=lambda: None,
        operation="set_input_boolean_state",
        target=SimpleNamespace(target_id=target),
        authoritative_provider_slug="direct_home_assistant_state",
        baseline={"dependency_risk": binding},
    )
    lock_keys = [
        item.key
        for item in OperationalLockSetCalculator().calculate(operation)
    ]

    obligations = binding["obligation_evidence"]
    source_aliases: dict[str, str] = {}
    resource_aliases: dict[str, str] = {}
    source_identity_projection = []
    for item in transport_fixture["configurations"]:
        configuration_id = item["configuration"].get("id")
        if not isinstance(configuration_id, str) or not configuration_id:
            raise AssertionError(
                "captured configuration ID is unavailable for normalization"
            )
        runtime_source = f"automation.{configuration_id}"
        captured_source = item["source"]
        resource_id = item["resource_id"]
        source_aliases[runtime_source] = captured_source
        resource_aliases[configuration_id] = resource_id
        source_identity_projection.append(
            {
                "runtime_source_object_id": runtime_source,
                "captured_source_object_id": captured_source,
                "captured_resource_id": resource_id,
            }
        )

    normalized_obligations = copy.deepcopy(obligations)
    for item in normalized_obligations:
        source = item.get("source_object_id")
        if source not in source_aliases:
            raise AssertionError(
                f"unmapped replay obligation source identity: {source}"
            )
        item["source_object_id"] = source_aliases[source]

    normalized_profiles = copy.deepcopy(binding["downstream_profiles"])
    for item in normalized_profiles:
        automation_id = item.get("automation_id")
        resource_id = item.get("automation_resource_id")
        if automation_id not in source_aliases:
            raise AssertionError(
                f"unmapped replay profile source identity: {automation_id}"
            )
        if resource_id not in resource_aliases:
            raise AssertionError(
                f"unmapped replay profile resource identity: {resource_id}"
            )
        item["automation_id"] = source_aliases[automation_id]
        item["automation_resource_id"] = resource_aliases[resource_id]

    source_counts = Counter(
        item["source_object_id"] for item in obligations
    )
    path_counts = Counter(
        item["configuration_path"] for item in obligations
    )
    kind_counts = Counter(item["obligation_kind"] for item in obligations)
    reason_counts = Counter(item["reason_code"] for item in obligations)
    scope_counts = Counter(
        item["target_selector_scope"] for item in obligations
    )
    lock_projection_counts = Counter(
        item["lock_projection"] for item in obligations
    )
    write_requests = [
        (method, path)
        for method, path in rest.calls
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}
    ]
    return {
        "source_release_commit": _source_commit(source_root),
        "entity_registry_mode": entity_registry_mode,
        "source_imports_verified": True,
        "risk_model": binding["model"],
        "exact_dependency_count": binding[
            "exact_dependency_obligation_count"
        ],
        "target_capable_opaque_obligation_count": binding[
            "opaque_obligation_count"
        ],
        "downstream_profile_count": len(binding["downstream_profiles"]),
        "coverage_complete": binding["coverage_complete"],
        "evidence_complete": binding["evidence_complete"],
        "semantic_precision": binding["semantic_precision"],
        "physical_consequence": binding["physical_consequence"],
        "risk_level": plan["risk"]["level"],
        "policy_class": plan["policy_decision"]["policy_class"],
        "execution_eligible": binding["execution_eligible"],
        "approval_actionable": plan["approval_actionable"],
        "source_obligation_counts": dict(sorted(source_counts.items())),
        "configuration_path_counts": dict(sorted(path_counts.items())),
        "obligation_kind_counts": dict(sorted(kind_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "target_selector_scope_counts": dict(sorted(scope_counts.items())),
        "lock_projection_counts": dict(
            sorted(lock_projection_counts.items())
        ),
        "normalized_obligation_rows": _normalized_semantic_rows(
            normalized_obligations, _OBLIGATION_NORMALIZED_FIELDS
        ),
        "normalized_downstream_profile_rows": _normalized_semantic_rows(
            normalized_profiles, _PROFILE_NORMALIZED_FIELDS
        ),
        "source_identity_projection": sorted(
            source_identity_projection,
            key=lambda item: item["captured_resource_id"],
        ),
        "coverage_failure_reason_codes": binding.get(
            "coverage_failure_reason_codes", []
        ),
        "evidence_fingerprint": binding.get("evidence_fingerprint"),
        "dependency_index_fingerprint": binding.get(
            "dependency_index_fingerprint"
        ),
        "label_membership_fingerprints": dict(
            sorted(snapshot.label_membership_fingerprints.items())
        ),
        "selector_authority_diagnostics": binding.get(
            "selector_authority_diagnostics", []
        ),
        "lock_keys": lock_keys,
        "conservative_downstream_locking": bool(
            binding["dependency_lock_projection"][
                "conservative_helper_dependency"
            ]
            or any(key.startswith("automation:") for key in lock_keys)
        ),
        "provider_dispatch_count": len(write_requests)
        + helper.dispatch_count,
        "rest_read_count": len(rest.calls),
        "websocket_read_count": len(websocket.calls),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument(
        "--entity-registry-mode",
        choices=(
            "complete",
            "identical_duplicate",
            "conflicting_duplicate",
            "malformed_relevant",
            "raw_overflow",
        ),
        default="complete",
    )
    arguments = parser.parse_args()
    result = asyncio.run(
        _replay(
            arguments.source_root,
            arguments.fixture.resolve(),
            entity_registry_mode=arguments.entity_registry_mode,
        )
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
