"""Exact reviewed ha-mcp image acceptance for the read-only gateway.

This script is intentionally transport-level.  CI starts the reviewed image,
the current Engineering image, and the synthetic read-only HA fixture before
invoking it.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.tools import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
    get_registered_server,
    registered_tools,
)
from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadGatewayTransport,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    PlanStatus,
)
from ha_mcp_engineering.governance.operational import (  # noqa: E402
    BackupAdministrationGateway,
)
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
    UPSTREAM_PROVIDER_CONTRACT_FIELDS,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentity,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_annotation_fingerprint,
    runtime_description_fingerprint,
    schema_fingerprint,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402
from ha_mcp_engineering.request_context import current_request_id  # noqa: E402


EXPECTED_ENGINEERING_BASELINE_COUNT = ENGINEERING_STATIC_TOOL_COUNT
ACCEPTANCE_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_ITEMS = 32
MAX_FAILURE_MESSAGE_CHARS = 512
EXPECTED_STOCK_COUNTS_BY_VERSION = {
    "7.14.1": {
        "automatic_read": 26,
        "mixed_or_requires_wrapper": 14,
        "persistent_write": 32,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "7.14.2": {
        "automatic_read": 26,
        "mixed_or_requires_wrapper": 14,
        "persistent_write": 32,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "8.0.0": {
        "automatic_read": 24,
        "held_for_canary": 2,
        "mixed_or_requires_wrapper": 14,
        "persistent_write": 32,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "8.1.0": {
        "automatic_read": 24,
        "held_for_canary": 2,
        "mixed_or_requires_wrapper": 13,
        "persistent_write": 33,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "8.1.1": {
        "automatic_read": 25,
        "held_for_canary": 1,
        "mixed_or_requires_wrapper": 13,
        "persistent_write": 33,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "8.2.0": {
        "automatic_read": 25,
        "held_for_canary": 1,
        "mixed_or_requires_wrapper": 13,
        "persistent_write": 33,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
    "8.4.1": {
        "automatic_read": 25,
        "held_for_canary": 1,
        "mixed_or_requires_wrapper": 13,
        "persistent_write": 33,
        "physical_or_high_risk_action": 4,
        "prohibited": 1,
        "unsupported": 1,
    },
}


def expected_dashboard_attestation_status(version: str) -> str:
    """Return the exact reviewed dashboard disposition for a release."""

    return "reviewed"


class _HeldDispositionRecordingTransport(McpReadGatewayTransport):
    """Record the exact local authority refusal before MCP session teardown."""

    validator_invoked: bool = False
    validator_refusal_category: str | None = None
    validator_refusal_dispatched: bool | None = None

    async def execute_read(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        catalog_validator: Any,
        **kwargs: Any,
    ):
        def record_refusal(catalog: Any) -> None:
            self.validator_invoked = True
            try:
                catalog_validator(catalog)
            except (
                OperationalBackupProviderError,
                OperationalLifecycleProviderError,
            ) as exc:
                self.validator_refusal_category = exc.category
                self.validator_refusal_dispatched = exc.dispatched
                raise

        return await super().execute_read(
            tool_name,
            arguments,
            catalog_validator=record_refusal,
            **kwargs,
        )


async def held_operational_provider_acceptance(
    release: Any,
    *,
    endpoint: str,
    fixture_stats_url: str,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Exercise held providers and prove they cannot reach a tool call."""

    dispositions = {
        surface: release.provider_disposition(surface)
        for surface in ("backup", "lifecycle")
    }
    require(
        dispositions == {"backup": "held", "lifecycle": "held"},
        "unreviewed operational provider authority was not held",
    )
    before = fixture_stats(fixture_stats_url)
    backup_transport = _HeldDispositionRecordingTransport(
        endpoint,
        timeout_seconds=30.0,
        client_version=SERVER_VERSION,
    )
    backup = ReviewedOperationalBackupProvider()
    backup.configure(
        settings,
        transport=backup_transport,
    )
    backup_dispatch_prepared = False

    async def prepare_backup_dispatch() -> None:
        nonlocal backup_dispatch_prepared
        backup_dispatch_prepared = True

    try:
        await backup.create_full_backup(
            "Exact held 8.4.1 backup",
            before_dispatch=prepare_backup_dispatch,
        )
    except OperationalBackupProviderError as exc:
        require(
            exc.dispatched is False,
            "held backup provider did not fail before dispatch",
        )
    else:
        raise AcceptanceFailure("held backup provider became actionable")

    lifecycle_transport = _HeldDispositionRecordingTransport(
        endpoint,
        timeout_seconds=30.0,
        client_version=SERVER_VERSION,
    )
    lifecycle = ReviewedOperationalLifecycleProvider()
    lifecycle.configure(
        settings,
        transport=lifecycle_transport,
    )
    lifecycle_dispatch_prepared = False

    async def prepare_lifecycle_dispatch() -> None:
        nonlocal lifecycle_dispatch_prepared
        lifecycle_dispatch_prepared = True

    try:
        await lifecycle.restart_addon(
            "abcdef12_ha_mcp",
            before_dispatch=prepare_lifecycle_dispatch,
        )
    except OperationalLifecycleProviderError as exc:
        require(
            exc.dispatched is False,
            "held lifecycle provider did not fail before dispatch",
        )
    else:
        raise AcceptanceFailure("held lifecycle provider became actionable")

    backup_health = backup.health_snapshot()
    lifecycle_health = lifecycle.health_snapshot()
    require(
        backup_transport.validator_invoked
        and backup_transport.validator_refusal_category
        == "upstream_version_mismatch"
        and backup_transport.validator_refusal_dispatched is False,
        "backup provider did not enforce the held release disposition",
    )
    require(
        lifecycle_transport.validator_invoked
        and lifecycle_transport.validator_refusal_category
        == "upstream_version_mismatch"
        and lifecycle_transport.validator_refusal_dispatched is False,
        "lifecycle provider did not enforce the held release disposition",
    )
    require(
        backup_health.get("request_count") == 1
        and backup_health.get("dispatch_count") == 0
        and backup_health.get("fallback_count") == 0
        and backup_dispatch_prepared is False,
        "held backup provider accounting changed",
    )
    require(
        (lifecycle_health.get("request_counts") or {}).get(
            "restart_addon"
        )
        == 1
        and sum(
            (lifecycle_health.get("dispatch_counts") or {}).values()
        )
        == 0
        and lifecycle_health.get("fallback_count") == 0
        and lifecycle_dispatch_prepared is False,
        "held lifecycle provider accounting changed",
    )
    after = fixture_stats(fixture_stats_url)
    require(
        before.get("rest_reads") == after.get("rest_reads")
        and before.get("websocket_reads")
        == after.get("websocket_reads")
        and before.get("http_mutations")
        == after.get("http_mutations")
        and before.get("websocket_mutations")
        == after.get("websocket_mutations"),
        "held operational provider reached Home Assistant",
    )
    return (
        {
            "status": "quarantined",
            "provider_disposition": dispositions["backup"],
            "provider_attempt_count": 1,
            "provider_dispatch_count": backup_health.get(
                "dispatch_count"
            ),
            "fallback_count": backup_health.get("fallback_count"),
        },
        {
            "status": "quarantined",
            "provider_disposition": dispositions["lifecycle"],
            "provider_attempt_count": 1,
            "provider_dispatch_count": sum(
                (lifecycle_health.get("dispatch_counts") or {}).values()
            ),
            "fallback_count": lifecycle_health.get("fallback_count"),
        },
    )


DELEGATED_READ_CALLS = {
    "ha_config_get_automation": {"identifier": "gateway_fixture"},
    "ha_config_get_calendar_events": {
        "entity_id": "calendar.fixture",
        "start": "2026-07-21T00:00:00+00:00",
        "end": "2026-07-22T00:00:00+00:00",
        "max_results": 5,
    },
    "ha_config_get_category": {"scope": "automation"},
    "ha_config_get_label": {},
    "ha_config_get_scene": {"scene_id": "gateway_fixture"},
    "ha_config_get_script": {"script_id": "gateway_fixture"},
    "ha_config_list_dashboard_resources": {"limit": 5},
    "ha_config_list_groups": {"limit": 5},
    "ha_config_list_helpers": {
        "helper_type": "input_boolean",
        "limit": 5,
    },
    "ha_eval_template": {"template": "{{ 1 + 1 }}"},
    "ha_get_automation_traces": {
        "automation_id": "automation.gateway_fixture",
        "limit": 5,
    },
    "ha_get_blueprint": {"domain": "automation"},
    "ha_get_device": {"limit": 5},
    "ha_get_entity": {"entity_id": "sun.sun"},
    "ha_get_entity_exposure": {"entity_id": "sun.sun"},
    "ha_get_hacs_info": {
        "action": "search",
        "query": "mushroom",
        "installed_only": True,
        "max_results": 5,
    },
    "ha_get_history": {
        "entity_ids": "sun.sun",
        "start_time": "24h",
        "limit": 5,
    },
    "ha_get_overview": {
        "detail_level": "minimal",
        "domains": ["sun"],
        "limit": 5,
        "include_notifications": False,
    },
    "ha_get_skill_guide": {},
    "ha_get_state": {"entity_id": "sun.sun"},
    "ha_get_todo": {},
    "ha_get_zone": {},
    "ha_list_floors_areas": {},
    "ha_list_services": {"limit": 5},
    "ha_search": {"domain_filter": "sun", "limit": 5},
}
UPSTREAM_ADDON_INVENTORY_ARGUMENTS = {
    "source": "installed",
    "include_stats": False,
}
UPSTREAM_ERROR_CALLS = {
    "missing_operation": {
        "tool": "ha_get_operation_status",
        "reviewed_versions": ("7.14.1", "7.14.2"),
        "arguments": {
            "operation_id": ["synthetic-missing-operation"],
            "timeout_seconds": 0,
        },
        "upstream_code": "RESOURCE_NOT_FOUND",
        "public_code": "provider_error",
        "failure_category": "upstream_error",
        "retryable": True,
        "fixture_counter": None,
    },
    "provider_failure": {
        "tool": "ha_get_state",
        "arguments": {
            "entity_id": "sensor.issue_57_synthetic_provider_failure"
        },
        "upstream_code": "SERVICE_CALL_FAILED",
        "public_code": "provider_error",
        "failure_category": "upstream_error",
        "retryable": True,
        "fixture_counter": (
            "rest_reads",
            "/api/states/{entity_id}",
        ),
    },
    "validation": {
        "tool": "ha_search",
        "shape_name": "invalid_search",
        "arguments": {"search_types": []},
        "upstream_code": "VALIDATION_FAILED",
        "public_code": "invalid_request",
        "failure_category": "invalid_request",
        "retryable": False,
        "fixture_counter": None,
    },
    "missing_entity": {
        "tool": "ha_get_state",
        "shape_name": "missing_state",
        "arguments": {"entity_id": "sensor.issue_57_missing_entity"},
        "upstream_code": "ENTITY_NOT_FOUND",
        "public_code": "entity_not_found",
        "failure_category": "entity_not_found",
        "retryable": False,
        "fixture_counter": (
            "rest_reads",
            "/api/states/{entity_id}",
        ),
    },
    "missing_automation": {
        "tool": "ha_config_get_automation",
        "shape_name": "missing_automation",
        "arguments": {"identifier": "issue_57_missing_automation"},
        "upstream_code": "RESOURCE_NOT_FOUND",
        "public_code": "automation_not_found",
        "failure_category": "automation_not_found",
        "retryable": False,
        "fixture_counter": (
            "rest_reads",
            "/api/config/automation/config/{id}",
        ),
    },
    "missing_registry_entity": {
        "tool": "ha_get_entity",
        "shape_name": "missing_registry_entity",
        "arguments": {
            "entity_id": (
                "sensor.compatibility_review_missing_registry_entity"
            )
        },
        "upstream_code": "SERVICE_CALL_FAILED",
        "public_code": "entity_not_found",
        "failure_category": "entity_not_found",
        "retryable": False,
        "fixture_counter": (
            "websocket_reads",
            "config/entity_registry/get",
        ),
    },
}
EXPECTED_ERROR_SHAPE_FINGERPRINTS = {
    "invalid_search": {
        "legacy": (
            "63e37a2f037ff46e9908c41745aca0e368c0cb6811a28104c990113055abdfee"
        ),
        "8.4.1": (
            "fc0f1e8bf02be61d2056f1c6f11fb7b861a74ecd98978a5a38076617ac5bf939"
        ),
    },
    "missing_state": {
        "legacy": (
            "8a705d923e27b7f0bd5675c49b972697874db226303b0ad8e159b83793f1950c"
        ),
    },
    "missing_automation": {
        "legacy": (
            "965faf0ef1864aad32d79da308763a92f024cf2d70cde40344832e76dbe85ba5"
        ),
    },
    "missing_registry_entity": {
        "legacy": (
            "3e1148ad27428880af39facca3605d530996850931d3e55c5d908f69ecc2d9c8"
        ),
    },
}
EXPECTED_OPERATIONAL_ERROR_CALLS = sum(
    1
    for value in UPSTREAM_ERROR_CALLS.values()
    if value["failure_category"] == "upstream_error"
)
EXPECTED_OUTCOME_CATEGORY_COUNTS: dict[str, int] = {}
for expected_error in UPSTREAM_ERROR_CALLS.values():
    category = expected_error["failure_category"]
    EXPECTED_OUTCOME_CATEGORY_COUNTS[category] = (
        EXPECTED_OUTCOME_CATEGORY_COUNTS.get(category, 0) + 1
    )
_expected_last_outcome = next(reversed(UPSTREAM_ERROR_CALLS.values()))
EXPECTED_LAST_CALL_FAILURE_CATEGORY = (
    _expected_last_outcome["failure_category"]
    if _expected_last_outcome["failure_category"] == "upstream_error"
    else None
)


def expected_successful_delegated_calls(
    total_calls: int,
    operational_error_calls: int = EXPECTED_OPERATIONAL_ERROR_CALLS,
) -> int:
    return total_calls - operational_error_calls


class AcceptanceFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message[:MAX_FAILURE_MESSAGE_CHARS])
        self.diagnostics = diagnostics or {}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def _exception_leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        values: list[BaseException] = []
        for nested in exc.exceptions[:MAX_DIAGNOSTIC_ITEMS]:
            values.extend(_exception_leaves(nested))
            if len(values) >= MAX_DIAGNOSTIC_ITEMS:
                break
        return values[:MAX_DIAGNOSTIC_ITEMS]
    return [exc]


def _bounded_failure_result(exc: BaseException) -> dict[str, Any]:
    leaves = _exception_leaves(exc)
    acceptance = next(
        (item for item in leaves if isinstance(item, AcceptanceFailure)),
        None,
    )
    governance = next(
        (item for item in leaves if isinstance(item, GovernanceError)),
        None,
    )
    failure = {
        "category": (
            "acceptance_failure"
            if acceptance is not None
            else "acceptance_execution_failure"
        ),
        "message": (
            str(acceptance)[:MAX_FAILURE_MESSAGE_CHARS]
            if acceptance is not None
            else "The bounded exact-image acceptance did not complete."
        ),
        "exception_types": sorted(
            {type(item).__name__[:128] for item in leaves}
        )[:MAX_DIAGNOSTIC_ITEMS],
    }
    if governance is not None:
        failure["error_code"] = governance.code.value
    return {
        "result": "FAIL",
        "failure": failure,
        "diagnostics": (
            acceptance.diagnostics
            if isinstance(acceptance, AcceptanceFailure)
            else {}
        ),
    }


async def _grant_authority_v3_bundle(
    service: ChangeGovernanceService,
    plan_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Grant every server-required action through the production authority."""

    plan_id = str(plan_snapshot["plan_id"])
    plan_hash = str(plan_snapshot["plan_hash"])
    pending = service.approve(plan_id, plan_hash)
    for _ in range(2):
        _review, csrf = await service.issue_external_csrf(
            plan_id, pending["challenge_id"]
        )
        result = await service.decide_external_approval(
            plan_id=plan_id,
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan_hash,
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=(
                "home_assistant_admin_ingress:"
                "synthetic-exact-image-acceptance-admin"
            ),
        )
        if result.get("status") == "approved":
            return result
        require(
            result.get("status") == "approval_pending",
            "authority-v3 approval did not advance deterministically",
        )
        pending = result
    raise AcceptanceFailure(
        "authority-v3 approval did not reach a terminal grant"
    )


async def _seed_dispatched_lifecycle_recovery(
    service: ChangeGovernanceService,
    plan_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Seed one crash-recovery boundary without invoking the provider."""

    require(
        plan_snapshot.get("policy_decision", {}).get("policy_class")
        == "elevated_admin",
        "lifecycle recovery fixture was not elevated",
    )
    require(
        plan_snapshot.get("policy_decision", {}).get(
            "required_acknowledgements"
        )
        == ["plan_approval", "elevated_risk_acknowledgement"],
        "lifecycle recovery fixture omitted required acknowledgements",
    )
    await _grant_authority_v3_bundle(service, plan_snapshot)
    plan_id = str(plan_snapshot["plan_id"])
    plan_hash = str(plan_snapshot["plan_hash"])
    plan = service.repository.get(plan_id)
    require(
        plan is not None and plan.operational is not None,
        "approved lifecycle plan was not persisted",
    )
    assert plan is not None and plan.operational is not None
    task, reused = service._resolve_task_for_apply(plan, plan_hash)
    require(
        task is not None and reused is False,
        "lifecycle recovery fixture did not reserve one durable task",
    )
    assert task is not None
    service._record_task_event(
        task,
        "preflight_started",
        new_state=ExecutionTaskState.PREFLIGHT,
        changes={"started_at": service._timestamp()},
    )
    service._active_task_ids_by_plan[plan.plan_id] = task.task_id
    try:
        service._consume_approval_bundle(plan)
        attempted_at = service._timestamp()
        plan.status = PlanStatus.APPLYING
        plan.execution_outcome = "dispatching"
        plan.apply_request_id = current_request_id()
        plan.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "request_id": plan.apply_request_id,
                "attempted_at": attempted_at,
                "provider_response_received": True,
                "provider_response_at": attempted_at,
                "provider_result": {"result": "accepted"},
            }
        )
        service._record(
            plan,
            f"{plan.operation.value}_dispatch_recorded",
            "success",
        )
        service._record(
            plan,
            f"{plan.operation.value}_provider_completed",
            "success",
        )
        plan.status = PlanStatus.VERIFICATION_REQUIRED
        plan.execution_outcome = "verification_pending"
        plan.operational.final_outcome = "verification_pending"
        plan.operational.verification.status = "verification_pending"
        service.repository.save(plan)
    finally:
        service._active_task_ids_by_plan.pop(plan.plan_id, None)

    persisted = service.repository.get(plan.plan_id)
    reserved = service.task_repository.get_for_plan(plan.plan_id)
    require(
        persisted is not None
        and reserved is not None
        and reserved.task_id == task.task_id,
        "lifecycle recovery authority was not durably materialized",
    )
    assert persisted is not None and reserved is not None
    service._require_policy_snapshot(persisted)
    event_types = [event.event_type for event in reserved.events]
    require(
        {
            "task_created",
            "approval_consumed",
            "dispatch_attempted",
        }.issubset(event_types),
        "lifecycle recovery task omitted transaction events",
    )
    require(
        event_types.index("task_created")
        < event_types.index("approval_consumed")
        < event_types.index("dispatch_attempted"),
        "lifecycle recovery task ordering was not durable",
    )
    require(
        reserved.approval_reference.get("authority_version") == 3
        and reserved.approval_reference.get("approval_bundle_state")
        == "consumed"
        and reserved.approval_reference.get("same_principal_confirmed")
        is True
        and len(reserved.provider_attempts) == 1,
        "lifecycle recovery task omitted authority-v3 evidence",
    )
    return {
        "plan_id": plan.plan_id,
        "task_id": reserved.task_id,
        "plan_hash": plan_hash,
        "policy_decision_hash": (
            persisted.policy_decision.policy_decision_hash
            if persisted.policy_decision is not None
            else None
        ),
    }


def _bounded_catalog_diagnostics(
    health: dict[str, Any],
    *,
    expected_names: set[str],
    observed_names: set[str],
    readiness: dict[str, Any],
) -> dict[str, Any]:
    gateway_states = find_values(health, "upstream_read_gateway")
    gateway = next(
        (item for item in gateway_states if isinstance(item, dict)),
        {},
    )
    scalar_fields = (
        "configured",
        "initialized",
        "generic_delegation_available",
        "admission_complete",
        "compatibility_status",
        "admission_status",
        "reconciliation_active",
        "reconciliation_status",
        "discovery_attempt_count",
        "retry_count",
        "last_failure_category",
        "last_discovery_failure_category",
        "last_call_failure_category",
        "upstream_server_name",
        "upstream_server_version",
        "observed_upstream_server_name",
        "observed_upstream_server_version",
        "observed_protocol_version",
        "reviewed_upstream_version",
        "upstream_advertised_tool_count",
        "observed_advertised_tool_count",
        "reviewed_automatic_read_count",
        "exact_matched_automatic_read_count",
        "dynamically_exposed_count",
        "missing_automatic_read_count",
        "quarantined_automatic_read_count",
        "unreviewed_observed_tool_count",
        "recommended_action",
    )
    bounded_gateway: dict[str, Any] = {}
    for name in scalar_fields:
        value = gateway.get(name)
        if isinstance(value, str):
            bounded_gateway[name] = value[:256]
        elif isinstance(value, (bool, int)) or value is None:
            bounded_gateway[name] = value
    for name in (
        "failure_counts",
        "quarantine_reason_counts",
        "blocked_classification_counts",
    ):
        value = gateway.get(name)
        if isinstance(value, dict):
            bounded_gateway[name] = {
                str(key)[:128]: count
                for key, count in sorted(
                    value.items(), key=lambda item: str(item[0])
                )[:MAX_DIAGNOSTIC_ITEMS]
                if isinstance(count, int)
            }
    bounded_gateway["missing_tools"] = [
        str(item)[:128]
        for item in gateway.get("missing_tools", [])
        if isinstance(item, str)
    ][:MAX_DIAGNOSTIC_ITEMS]
    bounded_gateway["quarantined_tools"] = [
        {
            name: str(item.get(name))[:128]
            for name in (
                "upstream_name",
                "exposed_name",
                "reason",
                "expected_fingerprint",
                "observed_fingerprint",
            )
            if item.get(name) is not None
        }
        for item in gateway.get("quarantined_tools", [])
        if isinstance(item, dict)
    ][:MAX_DIAGNOSTIC_ITEMS]
    return {
        "initial_catalog_readiness": readiness,
        "missing_expected_tools": sorted(expected_names - observed_names)[
            :MAX_DIAGNOSTIC_ITEMS
        ],
        "unexpected_tools": sorted(observed_names - expected_names)[
            :MAX_DIAGNOSTIC_ITEMS
        ],
        "upstream_read_gateway": bounded_gateway,
    }


def engineering_readiness(endpoint: str) -> dict[str, Any]:
    parts = urlsplit(endpoint)
    ready_url = urlunsplit((parts.scheme, parts.netloc, "/ready", "", ""))
    try:
        with urlopen(ready_url, timeout=5) as response:  # noqa: S310 - fixed CI endpoint
            status = response.status
            raw = response.read(1024)
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(1024)
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        value = {}
    return {
        "http_status": status,
        "ready": value.get("ready") is True,
        "initial_reconciliation_required": (
            value.get("initial_reconciliation_required") is True
        ),
        "initial_reconciliation_complete": (
            value.get("initial_reconciliation_complete") is True
        ),
        "status": (
            value.get("status")[:64]
            if isinstance(value.get("status"), str)
            else "unknown"
        ),
    }


async def list_all_tools(session: ClientSession) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        result = await session.list_tools(cursor)
        values.extend(
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in result.tools
        )
        cursor = result.nextCursor
        if not cursor:
            return values
        require(cursor not in seen, "catalog cursor repeated")
        seen.add(cursor)


def decode_tool_result(
    result: Any,
    *,
    context: str = "unspecified_tool_result",
) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" not in structured:
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                records: list[dict[str, Any]] = []
                for line in text.splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        records = []
                        break
                    if not isinstance(record, dict):
                        records = []
                        break
                    records.append(record)
                if records:
                    return {"records": records}
                continue
            if isinstance(value, dict):
                return value
    raise AcceptanceFailure(
        "tool result did not contain a bounded JSON object",
        diagnostics={"result_context": context[:128]},
    )


def _shape_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(name): _shape_projection(item)
            for name, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [_shape_projection(item) for item in value[:32]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unsupported"


def decode_upstream_error_evidence(result: Any) -> dict[str, str]:
    require(
        getattr(result, "isError", False) is True,
        "pinned upstream error call did not set isError=true",
    )
    content = getattr(result, "content", [])
    require(
        isinstance(content, list) and len(content) == 1,
        "pinned upstream error call returned an ambiguous content envelope",
    )
    text = getattr(content[0], "text", None)
    require(
        isinstance(text, str) and len(text.encode("utf-8")) <= 16_384,
        "pinned upstream error text was missing or oversized",
    )
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise AcceptanceFailure(
            "pinned upstream error text was not JSON"
        ) from exc
    require(
        isinstance(value, dict)
        and value.get("success") is False
        and isinstance(value.get("error"), dict)
        and isinstance(value["error"].get("code"), str),
        "pinned upstream error envelope shape changed",
    )
    return {
        "structured_code": value["error"]["code"],
        "shape_fingerprint": schema_fingerprint(
            _shape_projection(value)
        ),
    }


def find_values(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                found.append(item)
            found.extend(find_values(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_values(item, key))
    return found


def find_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for item in value.values():
            found.extend(find_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_dicts(item))
    return found


def bounded_audit_outcome_diagnostics(
    audit: dict[str, Any],
    error_calls: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return only safe classification fields for relevant audit records."""

    values: dict[str, Any] = {}
    records = find_dicts(audit)
    for error_name, evidence in error_calls.items():
        candidates = []
        for record in records:
            if record.get("tool_name") != evidence["tool"]:
                continue
            parameters = record.get("parameters")
            candidates.append(
                {
                    "expected_request": (
                        record.get("request_id")
                        == evidence["request_id"]
                    ),
                    "tool_name": str(record.get("tool_name", ""))[:64],
                    "result_status": str(
                        record.get("result_status", "")
                    )[:64],
                    "error_code": str(record.get("error_code", ""))[:64],
                    "provider": (
                        str(parameters.get("provider", ""))[:64]
                        if isinstance(parameters, dict)
                        else ""
                    ),
                }
            )
            if len(candidates) >= 8:
                break
        values[error_name] = {
            "expected_tool": evidence["tool"],
            "expected_error_code": evidence["public_error_code"],
            "candidates": candidates,
        }
    return {"audit_error_outcomes": values}


def fixture_stats(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed CI fixture URL
        return json.load(response)


def fixture_counter(
    stats: dict[str, Any],
    counter: tuple[str, str],
) -> int:
    section, key = counter
    values = stats.get(section)
    if not isinstance(values, dict):
        return 0
    value = values.get(key, 0)
    return value if isinstance(value, int) else 0


async def inspect_upstream(
    endpoint: str,
    *,
    expected_upstream_version: str,
) -> tuple[list[dict[str, Any]], str, dict[str, dict[str, Any]]]:
    error_envelopes: dict[str, dict[str, Any]] = {}
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(initialized.serverInfo.name == "ha-mcp", "upstream name mismatch")
            require(
                initialized.serverInfo.version == expected_upstream_version,
                "upstream version mismatch",
            )
            tools = await list_all_tools(session)
            tool_names = {item.get("name") for item in tools}
            if expected_upstream_version == "8.4.1":
                require(
                    "ha_get_addon" not in tool_names
                    and {"ha_get_app", "ha_manage_app"} <= tool_names,
                    "8.4.1 app-tool transition did not match exact evidence",
                )
            else:
                addon_inventory = decode_tool_result(
                    await session.call_tool(
                        "ha_get_addon",
                        UPSTREAM_ADDON_INVENTORY_ARGUMENTS,
                    )
                )
                require(
                    addon_inventory.get("success") is True,
                    "pinned upstream rejected exact add-on inventory arguments",
                )
                addons = addon_inventory.get("addons")
                require(
                    isinstance(addons, list)
                    and any(
                        isinstance(addon, dict)
                        and addon.get("slug") == "abcdef12_ha_mcp"
                        and addon.get("version")
                        == expected_upstream_version
                        for addon in addons
                    ),
                    "pinned upstream add-on inventory identity was incomplete",
                )
            for name, expected in UPSTREAM_ERROR_CALLS.items():
                reviewed_versions = expected.get("reviewed_versions")
                if (
                    reviewed_versions is not None
                    and expected_upstream_version not in reviewed_versions
                ):
                    continue
                result = await session.call_tool(
                    expected["tool"],
                    expected["arguments"],
                )
                evidence = decode_upstream_error_evidence(result)
                code = evidence["structured_code"]
                require(
                    code == expected["upstream_code"],
                    f"pinned upstream {name} error code changed",
                )
                shape_name = expected.get("shape_name")
                if isinstance(shape_name, str):
                    expected_shapes = EXPECTED_ERROR_SHAPE_FINGERPRINTS[
                        shape_name
                    ]
                    expected_shape = expected_shapes.get(
                        expected_upstream_version,
                        expected_shapes.get("legacy"),
                    )
                    require(
                        evidence["shape_fingerprint"] == expected_shape,
                        f"pinned upstream {name} error shape changed",
                    )
                error_envelopes[name] = {
                    "tool": expected["tool"],
                    "is_error": True,
                    "upstream_code": code,
                    "shape_fingerprint": evidence[
                        "shape_fingerprint"
                    ],
                }
    return tools, catalog_fingerprint(tools), error_envelopes


async def inspect_engineering(
    endpoint: str,
    fixture_stats_url: str,
    upstream_names: set[str],
    *,
    expected_upstream_version: str,
    policy: Any,
    release: Any,
) -> dict[str, Any]:
    readiness = engineering_readiness(endpoint)
    if readiness["http_status"] != 200 or readiness["ready"] is not True:
        raise AcceptanceFailure(
            "Engineering did not publish a ready initial catalog.",
            diagnostics={"initial_catalog_readiness": readiness},
        )
    base_names = {
        tool.name for tool in registered_tools(get_registered_server()).values()
    }
    require(
        len(base_names) == EXPECTED_ENGINEERING_BASELINE_COUNT,
        (
            "local Engineering baseline is not "
            f"{EXPECTED_ENGINEERING_BASELINE_COUNT} tools"
        ),
    )
    automatic = {
        entry.exposed_name
        for entry in policy.tools
        if entry.classification == "automatic_read"
    }
    held = {
        entry.exposed_name
        for entry in policy.tools
        if entry.classification == "held_for_canary"
    }
    delegated_read_calls = {
        name: arguments
        for name, arguments in DELEGATED_READ_CALLS.items()
        if name in automatic
    }
    error_call_contracts = {
        name: expected
        for name, expected in UPSTREAM_ERROR_CALLS.items()
        if expected["tool"] in automatic
        and (
            expected.get("reviewed_versions") is None
            or expected_upstream_version in expected["reviewed_versions"]
        )
    }
    expected_error_tools = {
        expected["tool"] for expected in error_call_contracts.values()
    }
    require(
        set(delegated_read_calls) | expected_error_tools == automatic,
        "the exact-image harness does not exercise every admitted read",
    )
    partial_search_enabled = "ha_search" in automatic
    expected_operational_error_calls = sum(
        1
        for value in error_call_contracts.values()
        if value["failure_category"] == "upstream_error"
    )
    expected_outcome_category_counts: dict[str, int] = {}
    for expected_error in error_call_contracts.values():
        category = expected_error["failure_category"]
        expected_outcome_category_counts[category] = (
            expected_outcome_category_counts.get(category, 0) + 1
        )
    expected_last_call_failure_category = next(
        (
            value["failure_category"]
            if value["failure_category"] == "upstream_error"
            else None
            for value in reversed(tuple(error_call_contracts.values()))
        ),
        None,
    )
    async with streamablehttp_client(endpoint) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            require(
                initialized.serverInfo.name == "ha-engineering-beta",
                "Engineering server name mismatch",
            )
            advertised = await list_all_tools(session)
            advertised_by_name = {item["name"]: item for item in advertised}
            names = set(advertised_by_name)
            if "get_server_health" not in names:
                raise AcceptanceFailure(
                    "The bounded Engineering health tool is missing.",
                    diagnostics={
                        "initial_catalog_readiness": readiness,
                        "missing_expected_tools": ["get_server_health"],
                        "observed_tool_count": len(names),
                    },
                )
            health_before_result = await session.call_tool(
                "get_server_health", {}
            )
            health_before = decode_tool_result(
                health_before_result,
                context="engineering_health_before_calls",
            )
            if not base_names <= names or not automatic <= names:
                raise AcceptanceFailure(
                    "The first accepted Engineering catalog is incomplete.",
                    diagnostics=_bounded_catalog_diagnostics(
                        health_before,
                        expected_names=base_names | automatic,
                        observed_names=names,
                        readiness=readiness,
                    ),
                )
            require(not held & names, "held-for-canary tool became callable")
            require("ha_get_logs" not in names, "raw log delegation is reachable")
            require("ha_call_service" not in names, "write-classified tool is advertised")
            require(len(names) == len(base_names | automatic), "unexpected tool exposed")
            for entry in policy.tools:
                if entry.classification != "automatic_read":
                    continue
                annotations = advertised_by_name[entry.exposed_name].get("annotations", {})
                expected_annotations = {
                    "readOnlyHint": entry.reviewed_annotations.read_only,
                    "destructiveHint": entry.reviewed_annotations.destructive,
                    "idempotentHint": entry.reviewed_annotations.idempotent,
                    "openWorldHint": entry.reviewed_annotations.open_world,
                }
                require(
                    all(
                        annotations.get(key) == expected
                        for key, expected in expected_annotations.items()
                    ),
                    f"reviewed annotation mismatch: {entry.exposed_name}",
                )

            direct_before = find_values(health_before, "requests_by_provider")
            fallback_before = find_values(health_before, "fallback_count")
            routing_before = next(
                (
                    item
                    for item in find_values(health_before, "provider_routing")
                    if isinstance(item, dict)
                ),
                {},
            )
            require(bool(routing_before), "provider-routing metrics missing before calls")

            calls: dict[str, dict[str, Any]] = {}
            for name, arguments in delegated_read_calls.items():
                result = await session.call_tool(name, arguments)
                value = decode_tool_result(
                    result,
                    context=f"delegated_read:{name}",
                )
                require(value.get("success") is True, f"{name} did not succeed: {value.get('error_code')}")
                metadata = value.get("metadata") or {}
                require(metadata.get("provider") == "upstream_read_gateway", f"{name} provider mismatch")
                require(metadata.get("fallback") == "none", f"{name} fallback mismatch")
                require(
                    metadata.get("upstream_version")
                    == expected_upstream_version,
                    f"{name} version mismatch",
                )
                if name == "ha_search":
                    data = value.get("data") or {}
                    upstream_partial = data.get("partial")
                    require(
                        isinstance(upstream_partial, bool),
                        "ha_search did not return an exact partial boolean",
                    )
                    locally_bounded = (
                        "The untrusted upstream response was safely bounded."
                        in (value.get("warnings") or [])
                    )
                    expected = (
                        "partial" if upstream_partial or locally_bounded else "complete"
                    )
                    require(
                        metadata.get("completeness") == expected,
                        "ha_search completeness did not preserve upstream semantics",
                    )
                calls[name] = {
                    "tool": name,
                    "request_id": value.get("request_id"),
                    "provider": metadata.get("provider"),
                    "completeness": metadata.get("completeness"),
                }

            if partial_search_enabled:
                partial_search = decode_tool_result(
                    await session.call_tool(
                        "ha_search",
                        {
                            "query": "gateway_fixture",
                            "search_types": ["automation"],
                            "limit": 5,
                        },
                    ),
                    context="delegated_read:ha_search_partial",
                )
                partial_metadata = partial_search.get("metadata") or {}
                partial_data = partial_search.get("data") or {}
                require(partial_search.get("success") is True, "partial ha_search failed")
                require(partial_data.get("partial") is True, "fixture did not induce partial ha_search")
                partial_automations = partial_data.get("automations")
                require(
                    isinstance(partial_automations, list)
                    and any(
                        isinstance(item, dict)
                        and item.get("entity_id") == "automation.gateway_fixture"
                        for item in partial_automations
                    ),
                    "partial ha_search did not retain the known usable automation evidence",
                )
                require(
                    partial_metadata.get("completeness") == "partial",
                    "Engineering reported partial ha_search as complete",
                )
                require(
                    partial_metadata.get("provider") == "upstream_read_gateway",
                    "partial ha_search provider mismatch",
                )
                require(
                    partial_metadata.get("fallback") == "none",
                    "partial ha_search fallback mismatch",
                )
                calls["ha_search_partial"] = {
                    "tool": "ha_search",
                    "request_id": partial_search.get("request_id"),
                    "provider": partial_metadata.get("provider"),
                    "completeness": partial_metadata.get("completeness"),
                }

            stats_before_invalid = fixture_stats(fixture_stats_url)
            invalid = decode_tool_result(
                await session.call_tool("ha_get_state", {"unknown": "value"}),
                context="prevalidation:ha_get_state",
            )
            require(invalid.get("success") is False, "invalid arguments unexpectedly succeeded")
            require(invalid.get("error_code") == "invalid_request", "invalid arguments were not prevalidated")
            require(
                fixture_stats(fixture_stats_url) == stats_before_invalid,
                "invalid arguments reached upstream Home Assistant",
            )

            health_before_errors = decode_tool_result(
                await session.call_tool("get_server_health", {}),
                context="engineering_health_before_errors",
            )
            routing_before_errors = next(
                (
                    item
                    for item in find_values(
                        health_before_errors, "provider_routing"
                    )
                    if isinstance(item, dict)
                ),
                {},
            )
            gateway_before_errors = next(
                (
                    item
                    for item in find_values(
                        health_before_errors, "upstream_read_gateway"
                    )
                    if isinstance(item, dict)
                ),
                {},
            )
            require(
                bool(routing_before_errors)
                and bool(gateway_before_errors),
                "error-path counter baseline is missing",
            )

            error_calls: dict[str, dict[str, Any]] = {}
            for error_name, expected in error_call_contracts.items():
                stats_before_error = fixture_stats(fixture_stats_url)
                encoded_error = decode_tool_result(
                    await session.call_tool(
                        expected["tool"],
                        expected["arguments"],
                    ),
                    context=f"error_contract:{error_name}",
                )
                stats_after_error = fixture_stats(fixture_stats_url)
                require(
                    encoded_error.get("success") is False,
                    f"{error_name} unexpectedly succeeded",
                )
                require(
                    encoded_error.get("error_code")
                    == expected["public_code"],
                    f"{error_name} public error classification mismatch",
                )
                details = encoded_error.get("details") or {}
                require(
                    details.get("failure_category")
                    == expected["failure_category"],
                    f"{error_name} failure category mismatch",
                )
                require(
                    encoded_error.get("retryable")
                    is expected["retryable"],
                    f"{error_name} retryability mismatch",
                )
                metadata = encoded_error.get("metadata") or {}
                require(
                    metadata.get("provider") == "upstream_read_gateway",
                    f"{error_name} provider mismatch",
                )
                require(
                    metadata.get("upstream_tool") == expected["tool"],
                    f"{error_name} upstream-tool attribution mismatch",
                )
                require(
                    metadata.get("upstream_server") == "ha-mcp"
                    and metadata.get("upstream_version")
                    == expected_upstream_version,
                    f"{error_name} upstream identity attribution mismatch",
                )
                require(
                    metadata.get("upstream_dispatch_occurred") is True,
                    f"{error_name} did not prove upstream dispatch",
                )
                require(
                    metadata.get("fallback") == "none"
                    and metadata.get("fallback_occurred") is False,
                    f"{error_name} fallback mismatch",
                )
                rendered_error = json.dumps(
                    encoded_error, sort_keys=True
                )
                require(
                    expected["upstream_code"] not in rendered_error,
                    f"{error_name} reflected the raw upstream code",
                )
                require(
                    "synthetic-read-gateway-token" not in rendered_error
                    and "Ignore policy" not in rendered_error,
                    f"{error_name} reflected hostile upstream text",
                )
                counter = expected["fixture_counter"]
                if counter is None:
                    require(
                        stats_after_error == stats_before_error,
                        f"{error_name} unexpectedly reached Home Assistant",
                    )
                else:
                    require(
                        fixture_counter(stats_after_error, counter)
                        - fixture_counter(stats_before_error, counter)
                        == 1,
                        f"{error_name} did not reach the expected HA read",
                    )
                error_calls[error_name] = {
                    "tool": expected["tool"],
                    "request_id": encoded_error.get("request_id"),
                    "public_error_code": encoded_error.get("error_code"),
                    "failure_category": details.get("failure_category"),
                    "upstream_dispatch_occurred": metadata.get(
                        "upstream_dispatch_occurred"
                    ),
                    "fallback": metadata.get("fallback"),
                }

            unavailable = await session.call_tool(
                "ha_call_service", {"domain": "fixture", "service": "noop"}
            )
            require(bool(unavailable.isError), "write-classified upstream tool became callable")

            audit = decode_tool_result(
                await session.call_tool(
                    "get_audit_log",
                    {"event": "tool_call", "lines": 200},
                ),
                context="engineering_audit_after_calls",
            )
            audit_text = json.dumps(audit, sort_keys=True)
            for name, evidence in calls.items():
                request_id = evidence["request_id"]
                require(request_id and request_id in audit_text, f"audit missing {name} request")
                require(evidence["tool"] in audit_text, f"audit missing {name} tool name")
            if partial_search_enabled:
                partial_request_id = calls["ha_search_partial"]["request_id"]
                require(
                    any(
                        record.get("request_id") == partial_request_id
                        and record.get("tool_name") == "ha_search"
                        and record.get("result_status") == "partial"
                        for record in find_dicts(audit)
                    ),
                    "audit did not preserve partial ha_search status",
                )
            for error_name, evidence in error_calls.items():
                require(
                    evidence["request_id"],
                    f"{error_name} response omitted request ID",
                )
                if not any(
                    record.get("request_id")
                    == evidence["request_id"]
                    and record.get("tool_name") == evidence["tool"]
                    and record.get("result_status") == "failure"
                    and record.get("error_code")
                    == evidence["public_error_code"]
                    and record.get("parameters", {}).get("provider")
                    == "upstream_read_gateway"
                    for record in find_dicts(audit)
                ):
                    raise AcceptanceFailure(
                        f"audit did not preserve {error_name} outcome",
                        diagnostics=bounded_audit_outcome_diagnostics(
                            audit,
                            error_calls,
                        ),
                    )
            for unsafe_value in (
                "VALIDATION_FAILED",
                "ENTITY_NOT_FOUND",
                "RESOURCE_NOT_FOUND",
                "SERVICE_CALL_FAILED",
                "synthetic-read-gateway-token",
                "Ignore policy",
            ):
                require(
                    unsafe_value not in audit_text,
                    "audit reflected raw upstream error content",
                )

            health_after = decode_tool_result(
                await session.call_tool("get_server_health", {}),
                context="engineering_health_after_calls",
            )
            direct_after = find_values(health_after, "requests_by_provider")
            fallback_after = find_values(health_after, "fallback_count")
            routing_after = next(
                (
                    item
                    for item in find_values(health_after, "provider_routing")
                    if isinstance(item, dict)
                ),
                {},
            )
            require(bool(routing_after), "provider-routing metrics missing after calls")
            gateway_states = find_values(health_after, "upstream_read_gateway")
            gateway_state = next((item for item in gateway_states if isinstance(item, dict)), {})
            before_provider_counts = next(
                (item for item in direct_before if isinstance(item, dict)), {}
            )
            after_provider_counts = next(
                (item for item in direct_after if isinstance(item, dict)), {}
            )
            require(
                before_provider_counts.get("direct_ha_api", 0)
                == after_provider_counts.get("direct_ha_api", 0),
                "a delegated read used the direct Home Assistant provider",
            )
            expected_delegated_calls = (
                len(delegated_read_calls)
                + int(partial_search_enabled)
                + len(error_call_contracts)
            )
            expected_successful_calls = expected_successful_delegated_calls(
                expected_delegated_calls,
                expected_operational_error_calls,
            )
            for metric_name in (
                "requests_by_provider",
                "successful_requests_by_provider",
                "failures_by_provider",
            ):
                require(
                    isinstance(routing_before.get(metric_name), dict)
                    and isinstance(routing_after.get(metric_name), dict),
                    f"provider-routing metric missing: {metric_name}",
                )
            before_requests = routing_before["requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_requests = routing_after["requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            before_successes = routing_before["successful_requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_successes = routing_after["successful_requests_by_provider"].get(
                "upstream_read_gateway", 0
            )
            before_failures = routing_before["failures_by_provider"].get(
                "upstream_read_gateway", 0
            )
            after_failures = routing_after["failures_by_provider"].get(
                "upstream_read_gateway", 0
            )
            require(
                after_requests - before_requests == expected_delegated_calls,
                "upstream read-gateway request accounting mismatch",
            )
            require(
                after_successes - before_successes
                == expected_successful_calls,
                "successful upstream read-gateway accounting mismatch",
            )
            require(
                after_failures - before_failures
                == expected_operational_error_calls,
                "actual provider failure accounting mismatch",
            )
            require(
                routing_after.get("partial_results", 0)
                - routing_before.get("partial_results", 0)
                == int(partial_search_enabled),
                "partial delegated-read accounting mismatch",
            )
            for metric_name in (
                "fallback_attempts",
                "fallback_successes",
                "prohibited_fallback_attempts",
            ):
                require(
                    routing_after.get(metric_name) == routing_before.get(metric_name),
                    f"provider-routing fallback metric changed: {metric_name}",
                )
            for metric_name in (
                "requests_by_provider",
                "successful_requests_by_provider",
                "failures_by_provider",
            ):
                require(
                    isinstance(
                        routing_before_errors.get(metric_name), dict
                    ),
                    f"error-path metric missing before calls: {metric_name}",
                )
            require(
                routing_after["requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors["requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                == len(error_call_contracts),
                "error-path provider request accounting mismatch",
            )
            require(
                routing_after["successful_requests_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors[
                    "successful_requests_by_provider"
                ].get("upstream_read_gateway", 0)
                == (
                    len(error_call_contracts)
                    - expected_operational_error_calls
                ),
                "domain outcomes changed provider success accounting",
            )
            require(
                routing_after["failures_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                - routing_before_errors["failures_by_provider"].get(
                    "upstream_read_gateway", 0
                )
                == expected_operational_error_calls,
                "domain outcomes inflated operational provider failures",
            )
            gateway_failure_before = (
                gateway_before_errors.get("failure_counts") or {}
            )
            gateway_failure_after = gateway_state.get("failure_counts") or {}
            for category, expected_delta in (
                expected_outcome_category_counts.items()
            ):
                require(
                    gateway_failure_after.get(category, 0)
                    - gateway_failure_before.get(category, 0)
                    == expected_delta,
                    f"gateway outcome accounting mismatch: {category}",
                )
            require(
                gateway_state.get("last_call_failure_category")
                == expected_last_call_failure_category,
                "last operational gateway failure category mismatch",
            )
            require(fallback_before == fallback_after, "fallback counters changed")
            require(gateway_state.get("fallback_count") == 0, "gateway fallback occurred")
            require(
                gateway_state.get("dynamically_exposed_count") == len(automatic),
                "dynamic exposure count mismatch",
            )
            require(
                set(gateway_state.get("reviewed_supported_versions") or ())
                >= {"7.14.1", "7.14.2"},
                "compiled reviewed-version diagnostics are incomplete",
            )
            require(
                gateway_state.get("selected_compatibility_entry_id")
                == release.entry_id,
                "selected compatibility entry mismatch",
            )
            require(
                gateway_state.get("reviewed_source_commit")
                == release.source_commit
                and gateway_state.get("reviewed_image_index_digest")
                == release.image_index_digest
                and gateway_state.get("reviewed_image_revision")
                == release.image_revision,
                "reviewed source/image evidence mismatch",
            )
            require(
                gateway_state.get("observed_protocol_version")
                == "2025-03-26"
                and gateway_state.get(
                    "reviewed_allowed_protocol_versions"
                )
                == ["2025-03-26"],
                "observed/reviewed protocol evidence mismatch",
            )
            require(
                gateway_state.get(
                    "runtime_artifact_provenance_observed"
                )
                is False
                and gateway_state.get(
                    "runtime_source_commit_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_image_index_digest_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_architecture_image_digest_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_image_revision_observed"
                )
                is None
                and gateway_state.get(
                    "runtime_artifact_provenance_status"
                )
                == "unobserved_by_mcp_discovery",
                "runtime artifact provenance was falsely claimed",
            )
            require(
                gateway_state.get("catalog_comparison_status") == "exact",
                "active catalog compatibility diagnostics are not exact",
            )
            require(
                gateway_state.get("dashboard_attestation_status")
                == expected_dashboard_attestation_status(
                    expected_upstream_version
                ),
                "active dashboard compatibility disposition is not exact",
            )
            require(
                gateway_state.get("observed_catalog_matches_reviewed_stock_fixture") is True,
                "exact image was not recognized as the stock reviewed fixture",
            )

    stats = fixture_stats(fixture_stats_url)
    require(not stats["http_mutations"], "an HTTP mutation reached the HA fixture")
    require(not stats["websocket_mutations"], "a WebSocket mutation reached the HA fixture")
    return {
        "engineering_tool_count": len(base_names | automatic),
        "base_engineering_tool_count": len(base_names),
        "dynamic_tool_count": len(automatic),
        "delegated_read_calls": calls,
        "error_calls": error_calls,
        "error_counter_snapshots": {
            "provider_routing_before": routing_before_errors,
            "provider_routing_after": routing_after,
            "gateway_failures_before": gateway_failure_before,
            "gateway_failures_after": gateway_failure_after,
        },
        "upstream_name_count": len(upstream_names),
        "direct_provider_snapshots": {"before": direct_before, "after": direct_after},
        "fallback_snapshots": {"before": fallback_before, "after": fallback_after},
        "initial_catalog_readiness": readiness,
        "fixture_stats": stats,
    }


async def inspect_operational_backup(
    *,
    upstream_endpoint: str,
    engineering_endpoint: str,
    fixture_stats_url: str,
    ha_url: str,
    ha_token: str,
    expected_upstream_version: str,
    release: Any,
) -> dict[str, Any]:
    """Exercise the public proposal and exact runtime provider against the image."""

    backup_name = (
        f"Exact image governed backup {expected_upstream_version}"
    )
    before = fixture_stats(fixture_stats_url)
    creates_before = len(before.get("operational_backup_creates") or [])
    async with streamablehttp_client(engineering_endpoint) as (
        read,
        write,
        _session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            proposal = decode_tool_result(
                await session.call_tool(
                    "create_backup_plan",
                    {"backup_name": backup_name},
                )
            )
    require(
        proposal.get("success") is True,
        "governed backup proposal failed in the exact image",
    )
    proposal_data = proposal.get("data") or {}
    require(
        proposal_data.get("proposal_only") is True
        and proposal_data.get("provider_dispatch_occurred") is False,
        "governed backup planning did not remain proposal-only",
    )
    after_proposal = fixture_stats(fixture_stats_url)
    require(
        len(after_proposal.get("operational_backup_creates") or [])
        == creates_before,
        "backup planning dispatched an operational write",
    )

    settings = Settings(
        ha_url=ha_url,
        ha_token=ha_token,
        access_secret="synthetic-exact-image-engineering-secret",
        port=0,
        audit_path="/tmp/synthetic-exact-image-audit.jsonl",
        rate_limit_per_minute=1,
        rate_limit_burst=1,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=upstream_endpoint,
    )
    provider = ReviewedOperationalBackupProvider()
    provider.configure(settings)
    gateway = BackupAdministrationGateway(
        provider,
        HomeAssistantWebSocketClient(settings),
    )
    planning = await gateway.planning_evidence()
    provider_evidence = planning.get("provider") or {}
    require(
        provider_evidence.get("server_version")
        == expected_upstream_version
        and provider_evidence.get("compatibility_entry_id")
        == release.entry_id,
        "operational provider selected the wrong reviewed release",
    )
    dispatch_persisted = False

    async def before_dispatch() -> None:
        nonlocal dispatch_persisted
        require(
            not dispatch_persisted,
            "operational dispatch callback ran more than once",
        )
        dispatch_persisted = True

    dispatched = await gateway.create_full_backup(
        backup_name,
        before_dispatch=before_dispatch,
    )
    require(
        dispatch_persisted,
        "operational dispatch did not persist evidence before provider call",
    )
    verification = await gateway.verify_full_backup(
        requested_name=backup_name,
        baseline_ids=list(
            (planning.get("baseline") or {}).get("backup_ids") or []
        ),
        apply_started_at=datetime.now(timezone.utc).isoformat(),
        backup_id=dispatched.backup_id,
        operation_id=dispatched.operation_id,
    )
    require(
        verification.get("status") == "verified",
        "independent backup/info verification did not pass",
    )
    after = fixture_stats(fixture_stats_url)
    creates = after.get("operational_backup_creates") or []
    require(
        len(creates) - creates_before == 1,
        "exactly one governed backup creation was not observed",
    )
    reached = creates[-1]
    require(
        reached
        == {
            "name": backup_name,
            "agent_ids": ["hassio.local"],
            "include_homeassistant": True,
            "include_database": False,
            "include_all_addons": True,
            "password_present": True,
        },
        "the pinned image received arguments outside the reviewed contract",
    )
    health = provider.health_snapshot()
    require(
        health.get("dispatch_count") == 1
        and health.get("fallback_count") == 0,
        "operational provider accounting or fallback policy changed",
    )
    require(
        not after.get("http_mutations")
        and not after.get("websocket_mutations"),
        "an unreviewed mutation reached the HA fixture",
    )
    return {
        "proposal_only": True,
        "provider": provider_evidence.get("provider"),
        "compatibility_entry_id": provider_evidence.get(
            "compatibility_entry_id"
        ),
        "dispatch_count": health.get("dispatch_count"),
        "verified_backup_id": verification.get("evidence", {}).get(
            "backup_id"
        ),
        "archive_integrity_validated": verification.get(
            "evidence", {}
        ).get("archive_integrity_validated"),
        "fallback_count": health.get("fallback_count"),
        "exact_arguments": reached,
    }


class _AcceptanceLegacyGateway:
    """Unused configuration boundary required by the production service."""


async def inspect_operational_lifecycle(
    *,
    upstream_endpoint: str,
    configured_upstream_endpoint: str,
    expected_upstream_version: str,
    release: Any,
) -> dict[str, Any]:
    """Exercise production upstream planning and recovered verification."""

    settings = Settings(
        ha_url="http://synthetic-home-assistant.invalid",
        ha_token="synthetic-ha-token",
        access_secret="synthetic-exact-image-engineering-secret",
        port=0,
        audit_path="/tmp/synthetic-lifecycle-audit.jsonl",
        rate_limit_per_minute=1,
        rate_limit_burst=1,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=configured_upstream_endpoint,
    )
    provider = ReviewedOperationalLifecycleProvider()
    transport = McpReadGatewayTransport(
        upstream_endpoint,
        timeout_seconds=60.0,
        client_version=SERVER_VERSION,
    )
    provider.configure(settings, transport=transport)
    runtime = {
        "upstream_version": expected_upstream_version,
        "upstream_protocol": "2025-03-26",
        "upstream_catalog_fingerprint": (
            release.policy.reviewed_stock_catalog_fingerprint
        ),
        "upstream_admission_status": "admitted_exact",
        "fallback_count": 0,
    }

    async def self_identity() -> SupervisorSelfAddonIdentity:
        return SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version=SERVER_VERSION,
            repository="df26dea6",
        )

    def gateway(process_instance_id: str) -> OperationalLifecycleGateway:
        return OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: dict(runtime),
            process_instance_id=process_instance_id,
            self_addon_identity_resolver=self_identity,
        )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = ChangePlanRepository(root / "plans")
        service = ChangeGovernanceService(
            repository,
            _AcceptanceLegacyGateway(),
            AuditLogger(
                str(root / "audit.jsonl"),
                "synthetic-exact-image-engineering-secret",
            ),
            lifecycle_gateway=gateway("planning-process"),
        )
        proposal = await service.create_addon_restart_plan(
            addon_slug="abcdef12_ha_mcp"
        )
        plan = repository.get(proposal["plan"]["plan_id"])
        require(
            plan is not None and plan.operational is not None,
            "lifecycle plan was not persisted",
        )
        baseline = plan.operational.baseline
        binding = baseline.get("upstream_addon_identity")
        require(
            baseline.get("target_class") == "upstream_ha_mcp_addon",
            "exact-image lifecycle planning misclassified the upstream add-on",
        )
        require(
            isinstance(binding, dict)
            and binding.get("slug") == "abcdef12_ha_mcp"
            and binding.get("endpoint_host") == "abcdef12-ha-mcp"
            and binding.get("installed_version") == expected_upstream_version,
            "exact-image lifecycle planning did not persist the authoritative binding",
        )
        provider_contract = binding.get("provider_contract") or {}
        require(
            all(
                field in provider_contract
                for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
            ),
            "exact-image lifecycle plan omitted provider readmission fields",
        )
        require(
            "upstream_readmission_when_applicable"
            in plan.operational.verification_contract.get("required", []),
            "exact-image lifecycle plan omitted upstream readmission proof",
        )
        initial_hash = service.plan_hash(plan)
        recovery_seed = await _seed_dispatched_lifecycle_recovery(
            service, proposal["plan"]
        )
        recovered = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            _AcceptanceLegacyGateway(),
            AuditLogger(
                str(root / "audit-recovered.jsonl"),
                "synthetic-exact-image-engineering-secret",
            ),
            lifecycle_gateway=gateway("recovered-process"),
        )
        positive = await recovered.reconcile_operational_plans(
            trigger="startup"
        )
        verified = repository.get(plan.plan_id)
        verified_task = recovered.task_repository.get_for_plan(
            plan.plan_id
        )
        require(
            positive.get("completed") == 1
            and verified is not None
            and verified.status == PlanStatus.APPLIED
            and verified.operational is not None
            and verified_task is not None
            and verified_task.task_id == recovery_seed["task_id"]
            and verified_task.state
            == ExecutionTaskState.SUCCEEDED_VERIFIED
            and len(verified_task.provider_attempts) == 1
            and verified.operational.verification.evidence.get(
                "restart_proof"
            )
            == "upstream_readmission",
            "recovered exact-image lifecycle did not verify exact readmission",
        )
        require(
            recovered.plan_hash(verified) == initial_hash,
            "recovered verification changed the immutable plan hash",
        )

        provider.configure(settings, transport=transport)
        drift_service = ChangeGovernanceService(
            ChangePlanRepository(root / "drift-plans"),
            _AcceptanceLegacyGateway(),
            AuditLogger(
                str(root / "audit-drift.jsonl"),
                "synthetic-exact-image-engineering-secret",
            ),
            lifecycle_gateway=gateway("drift-planning-process"),
        )
        drift_proposal = await drift_service.create_addon_restart_plan(
            addon_slug="abcdef12_ha_mcp"
        )
        drift_plan = drift_service.repository.get(
            drift_proposal["plan"]["plan_id"]
        )
        require(
            drift_plan is not None and drift_plan.operational is not None,
            "binding-drift plan was not persisted",
        )
        await _seed_dispatched_lifecycle_recovery(
            drift_service, drift_proposal["plan"]
        )
        alias_settings = replace(
            settings,
            upstream_dashboard_mcp_url=(
                "http://upstream-alias:18086/"
                "synthetic-upstream-secret/mcp"
            ),
        )
        provider.configure(alias_settings, transport=transport)
        drift_recovered = ChangeGovernanceService(
            ChangePlanRepository(root / "drift-plans"),
            _AcceptanceLegacyGateway(),
            AuditLogger(
                str(root / "audit-drift-recovered.jsonl"),
                "synthetic-exact-image-engineering-secret",
            ),
            lifecycle_gateway=gateway("drift-recovered-process"),
        )
        drift_result = await drift_recovered.reconcile_operational_plans(
            trigger="startup"
        )
        refused = drift_recovered.repository.get(drift_plan.plan_id)
        require(
            drift_result.get("failed") == 1
            and refused is not None
            and refused.status == PlanStatus.VERIFICATION_FAILED
            and refused.operational is not None
            and refused.operational.verification.evidence.get(
                "restart_proof"
            )
            is None,
            "recovered exact-image lifecycle accepted changed binding evidence",
        )

    health = provider.health_snapshot()
    require(
        sum((health.get("dispatch_counts") or {}).values()) == 0
        and health.get("fallback_count") == 0,
        "lifecycle acceptance dispatched a restart or used fallback",
    )
    return {
        "proposal_only": True,
        "target_class": baseline.get("target_class"),
        "restart_proof_requirement": (
            "upstream_readmission"
        ),
        "bound_slug": binding.get("slug"),
        "endpoint_host": binding.get("endpoint_host"),
        "installed_version": binding.get("installed_version"),
        "provider_contract_fields": list(
            UPSTREAM_PROVIDER_CONTRACT_FIELDS
        ),
        "startup_reconciliation_verified": True,
        "binding_drift_refused": True,
        "dispatch_count": 0,
        "fallback_count": health.get("fallback_count"),
    }


async def inspect_approval_notification(
    *,
    engineering_endpoint: str,
    fixture_stats_url: str,
) -> dict[str, Any]:
    """Exercise the baked resolver and advisory notify route end to end."""

    proposed = {
        "alias": "Gateway Fixture",
        "description": "Beta 32 exact-image notification fixture",
        "triggers": [],
        "conditions": [],
        "actions": [],
        "mode": "single",
    }
    async with streamablehttp_client(engineering_endpoint) as (
        read,
        write,
        _session_id,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            created = decode_tool_result(
                await session.call_tool(
                    "create_change_plan",
                    {
                        "title": "Beta 32 exact-image notification",
                        "description": (
                            "Synthetic advisory submission acceptance"
                        ),
                        "operation": "update_automation",
                        "automation_id": "gateway_fixture",
                        "proposed_config": proposed,
                    },
                )
            )
            require(
                created.get("success") is True,
                "exact-image notification plan creation failed",
            )
            created_data = created.get("data") or {}
            plan_id = created_data.get("plan_id")
            plan_hash = created_data.get("plan_hash")
            require(
                isinstance(plan_id, str)
                and isinstance(plan_hash, str),
                "exact-image notification plan identity was incomplete",
            )
            pending = decode_tool_result(
                await session.call_tool(
                    "approve_change_plan",
                    {
                        "plan_id": plan_id,
                        "expected_plan_hash": plan_hash,
                    },
                )
            )
            require(
                pending.get("success") is True,
                "exact-image approval challenge request failed",
            )
            pending_data = pending.get("data") or {}
            challenge_id = pending_data.get("challenge_id")
            require(
                pending_data.get("status") == "approval_pending"
                and isinstance(challenge_id, str),
                "notification delivery changed approval authority",
            )
            notification_projection = (
                pending_data.get("approval_notification") or {}
            )
            require(
                notification_projection.get("authority") == "none",
                "notification projection claimed approval authority",
            )

            expected_ingress_path = (
                "/hassio/ingress/"
                "df26dea6_hass_mcp_engineering_beta/plans/"
                f"{plan_id}"
            )
            expected_ios_url = (
                f"homeassistant://navigate{expected_ingress_path}"
            )
            expected_android_target = (
                f"deep-link://{expected_ios_url}"
            )
            notification_key = (
                "ha_mcp_approval_"
                + hashlib.sha256(challenge_id.encode()).hexdigest()[:24]
            )
            expected_ingress_path_hash = hashlib.sha256(
                expected_ingress_path.encode()
            ).hexdigest()
            expected_ios_url_hash = hashlib.sha256(
                expected_ios_url.encode()
            ).hexdigest()
            expected_android_target_hash = hashlib.sha256(
                expected_android_target.encode()
            ).hexdigest()
            expected_action_target_hash = expected_ingress_path_hash
            expected_tag_hash = hashlib.sha256(
                notification_key.encode()
            ).hexdigest()
            notification_health: dict[str, Any] = {}
            matching_calls: list[dict[str, Any]] = []
            for _attempt in range(100):
                health = decode_tool_result(
                    await session.call_tool(
                        "get_server_health", {"check_ha": False}
                    )
                )
                notification_health = (
                    ((health.get("data") or {}).get("governance") or {}).get(
                        "approval_notifications"
                    )
                    or {}
                )
                current_stats = fixture_stats(fixture_stats_url)
                matching_calls = [
                    call
                    for call in (
                        current_stats.get("approval_notification_calls")
                        or []
                    )
                    if call.get("ingress_path_sha256")
                    == expected_ingress_path_hash
                    and call.get("ios_url_sha256")
                    == expected_ios_url_hash
                    and call.get("android_click_action_sha256")
                    == expected_android_target_hash
                    and call.get("action_uri_sha256")
                    == expected_action_target_hash
                    and call.get("tag_sha256") == expected_tag_hash
                ]
                if (
                    len(matching_calls) == 1
                    and notification_health.get("submitted", 0) >= 1
                ):
                    break
                await asyncio.sleep(0.1)

    after_stats = fixture_stats(fixture_stats_url)
    matching_calls = [
        call
        for call in (
            after_stats.get("approval_notification_calls") or []
        )
        if call.get("ingress_path_sha256")
        == expected_ingress_path_hash
        and call.get("ios_url_sha256") == expected_ios_url_hash
        and call.get("android_click_action_sha256")
        == expected_android_target_hash
        and call.get("tag_sha256") == expected_tag_hash
    ]
    require(
        len(matching_calls) == 1,
        "exact-image notification did not make exactly one allowlisted call",
    )
    call = matching_calls[0]
    require(
        call.get("ingress_path_sha256") == expected_ingress_path_hash
        and call.get("ios_url_sha256") == expected_ios_url_hash,
        "exact-image notification did not carry the exact iOS Ingress link",
    )
    require(
        call.get("android_click_action_sha256")
        == expected_android_target_hash
        and call.get("action_uri_sha256")
        == expected_action_target_hash
        and call.get("action_uri_matches_cross_platform_target") is True
        and call.get("authority_material_present") is False
        and call.get("authentication_required_present") is False,
        "exact-image notification did not satisfy the platform link contract",
    )
    require(
        after_stats.get("supervisor_self_info_payload_bytes", 0)
        > 32 * 1024,
        "exact-image Supervisor self-info fixture was not larger than 32 KiB",
    )
    require(
        after_stats.get("supervisor_self_info_fragment_bytes") == 1024
        and after_stats.get("supervisor_self_info_fragment_count", 0) > 1,
        "exact-image Supervisor self-info fixture was not fragmented",
    )
    rest_reads = after_stats.get("rest_reads") or {}
    require(
        rest_reads.get("/addons/self/info", 0) >= 1,
        "baked Engineering runtime did not use Supervisor self info",
    )
    require(
        notification_health.get("configured") is True
        and notification_health.get("worker_running") is True
        and notification_health.get("submitted", 0) >= 1
        and notification_health.get("delivered") == 0
        and notification_health.get("handset_delivery_observable") is False
        and notification_health.get("failed") == 0
        and notification_health.get("addon_identity_status")
        == "verified_supervisor_self_info"
        and notification_health.get("addon_identity_failure_category")
        is None
        and notification_health.get("authority") == "none"
        and notification_health.get("fallback_count") == 0,
        "exact-image notification health was not successful and advisory",
    )
    return {
        "supervisor_self_info_payload_bytes": after_stats.get(
            "supervisor_self_info_payload_bytes"
        ),
        "supervisor_self_info_fragment_bytes": after_stats.get(
            "supervisor_self_info_fragment_bytes"
        ),
        "supervisor_self_info_fragment_count": after_stats.get(
            "supervisor_self_info_fragment_count"
        ),
        "fragmented_response_fully_consumed": True,
        "supervisor_self_info_requests": rest_reads.get(
            "/addons/self/info"
        ),
        "notification_dispatch_count": len(matching_calls),
        "exact_ingress_plan_link": True,
        "configured": notification_health.get("configured"),
        "worker_running": notification_health.get("worker_running"),
        "submitted": notification_health.get("submitted"),
        "delivered": notification_health.get("delivered"),
        "handset_delivery_observable": notification_health.get(
            "handset_delivery_observable"
        ),
        "failed": notification_health.get("failed"),
        "addon_identity_status": notification_health.get(
            "addon_identity_status"
        ),
        "addon_identity_failure_category": notification_health.get(
            "addon_identity_failure_category"
        ),
        "authority": notification_health.get("authority"),
        "fallback_count": notification_health.get("fallback_count"),
        "approval_performed": False,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_reviewed_upstream_release_registry()
    release = registry.by_version.get(args.expected_upstream_version)
    require(
        release is not None,
        "requested exact-image version has no compiled review entry",
    )
    assert release is not None
    policy = release.policy
    (
        upstream_tools,
        observed_fingerprint,
        upstream_error_envelopes,
    ) = await inspect_upstream(
        args.upstream_endpoint,
        expected_upstream_version=args.expected_upstream_version,
    )
    require(len(upstream_tools) == policy.reviewed_stock_catalog_tool_count, "stock catalog count mismatch")
    observed_by_name = {tool["name"]: tool for tool in upstream_tools}
    missing_names = sorted(set(policy.by_name) - set(observed_by_name))
    extra_names = sorted(set(observed_by_name) - set(policy.by_name))
    schema_mismatches = sorted(
        name
        for name in set(observed_by_name) & set(policy.by_name)
        if schema_fingerprint(observed_by_name[name]["inputSchema"])
        != policy.by_name[name].input_schema_fingerprint
    )
    require(
        not missing_names and not extra_names and not schema_mismatches,
        "stock policy mismatch: "
        f"missing={missing_names[:20]} extra={extra_names[:20]} "
        f"schema={schema_mismatches[:20]}",
    )
    require(
        observed_fingerprint == policy.reviewed_stock_catalog_fingerprint,
        "stock catalog fingerprint mismatch: "
        f"observed={observed_fingerprint} "
        f"expected={policy.reviewed_stock_catalog_fingerprint}",
    )
    reviewed_descriptions = (
        policy.reviewed_runtime_description_fingerprints_by_name
    )
    description_mismatches = sorted(
        name
        for name, expected in reviewed_descriptions.items()
        if runtime_description_fingerprint(
            observed_by_name[name].get("description")
        )
        != expected
    )
    require(
        not description_mismatches,
        "reviewed runtime description mismatch: "
        f"tools={description_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    reviewed_annotations = (
        policy.reviewed_runtime_annotation_fingerprints_by_name
    )
    annotation_mismatches = sorted(
        name
        for name, expected in reviewed_annotations.items()
        if runtime_annotation_fingerprint(
            observed_by_name[name].get("annotations")
        )
        != expected
    )
    require(
        not annotation_mismatches,
        "reviewed runtime annotation mismatch: "
        f"tools={annotation_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    reviewed_output_schemas = (
        policy.reviewed_runtime_output_schema_fingerprints_by_name
    )
    output_schema_mismatches: list[str] = []
    for name, expected in reviewed_output_schemas.items():
        observed_schema = observed_by_name[name].get("outputSchema")
        try:
            actual = (
                schema_fingerprint(observed_schema)
                if isinstance(observed_schema, dict)
                else None
            )
        except (TypeError, ValueError, OverflowError):
            actual = None
        if actual != expected:
            output_schema_mismatches.append(name)
    require(
        not output_schema_mismatches,
        "reviewed runtime output-schema mismatch: "
        f"tools={output_schema_mismatches[:MAX_DIAGNOSTIC_ITEMS]}",
    )
    require(
        policy.classification_counts
        == EXPECTED_STOCK_COUNTS_BY_VERSION.get(args.expected_upstream_version),
        "stock classification counts mismatch",
    )
    engineering = await inspect_engineering(
        args.engineering_endpoint,
        args.fixture_stats_url,
        set(observed_by_name),
        expected_upstream_version=args.expected_upstream_version,
        policy=policy,
        release=release,
    )
    if args.expected_upstream_version == "8.4.1":
        held_settings = Settings(
            ha_url=args.ha_url,
            ha_token=args.ha_token,
            access_secret="synthetic-exact-image-engineering-secret",
            port=0,
            audit_path="/tmp/synthetic-held-provider-audit.jsonl",
            rate_limit_per_minute=1,
            rate_limit_burst=1,
            destructive_services=frozenset(),
            upstream_dashboard_mcp_url=args.upstream_endpoint,
        )
        (
            operational_backup,
            operational_lifecycle,
        ) = await held_operational_provider_acceptance(
            release,
            endpoint=args.upstream_endpoint,
            fixture_stats_url=args.fixture_stats_url,
            settings=held_settings,
        )
    else:
        operational_backup = await inspect_operational_backup(
            upstream_endpoint=args.upstream_endpoint,
            engineering_endpoint=args.engineering_endpoint,
            fixture_stats_url=args.fixture_stats_url,
            ha_url=args.ha_url,
            ha_token=args.ha_token,
            expected_upstream_version=args.expected_upstream_version,
            release=release,
        )
        operational_lifecycle = await inspect_operational_lifecycle(
            upstream_endpoint=args.upstream_endpoint,
            configured_upstream_endpoint=(
                args.configured_upstream_endpoint
            ),
            expected_upstream_version=args.expected_upstream_version,
            release=release,
        )
    approval_notification = await inspect_approval_notification(
        engineering_endpoint=args.engineering_endpoint,
        fixture_stats_url=args.fixture_stats_url,
    )
    return {
        "result": "PASS",
        "upstream_version": args.expected_upstream_version,
        "observed_catalog_count": len(upstream_tools),
        "observed_catalog_fingerprint": observed_fingerprint,
        "reviewed_runtime_description_fingerprint_count": len(
            reviewed_descriptions
        ),
        "reviewed_runtime_annotation_fingerprint_count": len(
            reviewed_annotations
        ),
        "reviewed_runtime_output_schema_fingerprint_count": len(
            reviewed_output_schemas
        ),
        "upstream_error_envelopes": upstream_error_envelopes,
        "classification_counts": policy.classification_counts,
        "operational_backup": operational_backup,
        "operational_lifecycle": operational_lifecycle,
        "approval_notification": approval_notification,
        **engineering,
    }


def main() -> None:
    for logger_name in ("mcp.client.streamable_http", "httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.propagate = False
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-endpoint", required=True)
    parser.add_argument(
        "--configured-upstream-endpoint", required=True
    )
    parser.add_argument("--expected-upstream-version", required=True)
    parser.add_argument("--engineering-endpoint", required=True)
    parser.add_argument("--fixture-stats-url", required=True)
    parser.add_argument("--ha-url", required=True)
    parser.add_argument("--ha-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(
            asyncio.wait_for(run(args), timeout=ACCEPTANCE_TIMEOUT_SECONDS)
        )
    except Exception as exc:
        failure = _bounded_failure_result(exc)
        args.output.write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "exact-image read gateway acceptance failed; "
            "see the bounded result artifact"
        ) from None
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "exact-image read gateway acceptance: PASS "
        f"({result['observed_catalog_count']} advertised, "
        f"{result['dynamic_tool_count']} delegated)"
    )


if __name__ == "__main__":
    main()
