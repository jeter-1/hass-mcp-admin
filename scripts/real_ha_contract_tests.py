"""Blocking contracts against one disposable Home Assistant Core instance.

This script is CI-only. It bootstraps a temporary administrator in the
throwaway container, never prints its credentials, and exercises the project
clients rather than the deployed Home Assistant environment.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Any

import aiohttp
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients import (  # noqa: E402
    HomeAssistantRestClient,
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.service import (  # noqa: E402
    EntityDependencyAnalysisService,
)
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.impact.service import (  # noqa: E402
    ChangeImpactAnalysisService,
)
from ha_mcp_engineering.providers.ha_2026_8_device_compatibility import (  # noqa: E402
    ADAPTER_IDS_BY_HA_VERSION as HA_DEVICE_ADAPTER_IDS_BY_HA_VERSION,
    adapt_ha_get_device_composite_result,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
    compare_resource_verification,
    resource_identity_matches,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    normalize_automation,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    AutomationGateway,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.trace_normalization import fetch_normalized_trace_list  # noqa: E402


def _environment_path(name: str) -> Path | None:
    """Return one explicitly configured path without treating cwd as a value."""

    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


HA_URL = os.environ.get("REAL_HA_URL", "http://127.0.0.1:8123").rstrip("/")
CLIENT_ID = f"{HA_URL}/"
EXPECTED_HA_VERSION = os.environ.get("REAL_HA_EXPECTED_VERSION", "2026.7.2")
DEVICE_FIXTURE_PATH = _environment_path("REAL_HA_DEVICE_FIXTURE")
TOKEN_PATH = _environment_path("REAL_HA_TOKEN_FILE")
UPSTREAM_IMAGE = os.environ.get("REAL_HA_UPSTREAM_IMAGE", "")
UPSTREAM_VERSION = os.environ.get("REAL_HA_UPSTREAM_VERSION", "8.2.0")
UPSTREAM_CONTAINER = os.environ.get(
    "REAL_HA_UPSTREAM_CONTAINER", "beta23-real-ha-upstream"
)
HA_CONTRACT_CONTAINER = os.environ.get(
    "HA_CONTRACT_CONTAINER", "beta25-real-ha"
)
UPSTREAM_PORT = int(os.environ.get("REAL_HA_UPSTREAM_PORT", "18086"))
UPSTREAM_SECRET_PATH = "/beta23-real-ha-mcp"
MIGRATION_AUTOMATION_ID = "beta23_composite_device_reference"
FIXTURE_PLATFORM = "beta23_device_fixture"
RESOURCE_ORDER = (
    "input_boolean",
    "input_number",
    "script",
    "automation",
)
RESOURCE_IDS = {
    "input_boolean": "input_boolean.dev14_real_contract_boolean",
    "input_number": "input_number.dev14_real_contract_number",
    "script": "dev14_real_contract_script",
    "automation": "dev14_real_contract_automation",
}
CREATE_CONFIGS = {
    "input_boolean": {
        "name": "Dev14 Real Contract Boolean",
        "icon": "mdi:toggle-switch",
    },
    "input_number": {
        "name": "Dev14 Real Contract Number",
        "min": 0,
        "max": 100,
        "step": 1,
        "mode": "slider",
        "unit_of_measurement": "contract_units",
        "icon": "mdi:numeric",
    },
    "script": {
        "alias": "Dev14 real contract script",
        "description": "Behavior-free event-only disposable fixture",
        "mode": "single",
        "sequence": [
            {
                "event": "dev14_real_contract_script_observed",
                "event_data": {"source": "disposable_contract"},
            }
        ],
    },
    "automation": {
        "alias": "Dev14 real contract automation",
        "description": "Behavior-free event-only disposable fixture",
        "trigger": [
            {
                "platform": "event",
                "event_type": "dev14_real_contract_trigger",
            }
        ],
        "condition": [],
        "action": [
            {
                "event": "dev14_real_contract_automation_observed",
                "event_data": {"source": "disposable_contract"},
            }
        ],
        "mode": "single",
    },
}
UPDATE_CONFIGS = {
    "input_boolean": {
        **CREATE_CONFIGS["input_boolean"],
        "icon": "mdi:toggle-switch-off",
    },
    "input_number": {
        **CREATE_CONFIGS["input_number"],
        "max": 200,
        "step": 2,
    },
    "script": {
        **CREATE_CONFIGS["script"],
        "description": "Updated behavior-free event-only disposable fixture",
    },
    "automation": {
        **CREATE_CONFIGS["automation"],
        "description": "Updated behavior-free event-only disposable fixture",
    },
}
LEGACY_AUTOMATION_CONFIG = {
    **CREATE_CONFIGS["automation"],
    "description": (
        "Intermediate event-only legacy automation compatibility fixture"
    ),
}
F2_STANDARD_HELPER_CONFIG = {
    **CREATE_CONFIGS["input_boolean"],
    "icon": "mdi:shield-check",
}
F2_ELEVATED_AUTOMATION_CONFIG = {
    **LEGACY_AUTOMATION_CONFIG,
    "description": "Future physical action accepted through elevated policy",
    "action": [
        {
            "service": "light.turn_on",
            "target": {"entity_id": "light.disposable_f2_fixture"},
        }
    ],
}
F2_PROHIBITED_AUTOMATION_CONFIG = {
    **F2_ELEVATED_AUTOMATION_CONFIG,
    "description": "Safety-critical action prohibited by F2 policy",
    "action": [
        {
            "service": "lock.unlock",
            "target": {"entity_id": "lock.disposable_f2_fixture"},
        }
    ],
}
F2_PROHIBITED_DEVICE_TARGET_AUTOMATION_CONFIG = {
    **F2_ELEVATED_AUTOMATION_CONFIG,
    "description": (
        "Safety-critical action with a device target prohibited by F2 policy"
    ),
    "action": [
        {
            "service": "lock.unlock",
            "target": {"device_id": "disposable_nonexistent_lock_device"},
        }
    ],
}

F2_ADMIN_A = "home_assistant_admin_ingress:disposable-f2-admin-a"
F2_ADMIN_B = "home_assistant_admin_ingress:disposable-f2-admin-b"

_DIAGNOSTIC_ID_PREFIX_LENGTH = 12
_DIAGNOSTIC_MAX_CAUSE_DEPTH = 5
_DIAGNOSTIC_MAX_MISMATCHES = 10
_DIAGNOSTIC_MAX_TEXT_LENGTH = 128
_SAFE_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)
_SAFE_PROVIDER_CATEGORIES = frozenset(
    {
        "configuration_invalid",
        "indeterminate_dispatch",
        "invalid_request",
        "invalid_response",
        "operation_failed",
        "operation_rejected",
        "permission_failure",
        "protocol_error",
        "provider_error",
        "provider_timeout",
        "provider_unavailable",
        "service_unavailable",
    }
)


def _bounded_diagnostic_token(
    value: object,
    *,
    maximum: int = _DIAGNOSTIC_MAX_TEXT_LENGTH,
    punctuation: str = "_-/.:",
) -> str | None:
    """Project one bounded machine-owned token, never arbitrary text."""

    if not isinstance(value, str) or not (0 < len(value) <= maximum):
        return None
    if any(
        not (character.isalnum() or character in punctuation)
        for character in value
    ):
        return None
    return value


def _short_diagnostic_identifier(value: object) -> str | None:
    """Return only the bounded prefix of one opaque repository identifier."""

    token = _bounded_diagnostic_token(value, punctuation="_-")
    return (
        token[:_DIAGNOSTIC_ID_PREFIX_LENGTH]
        if token is not None
        else None
    )


def _bounded_error_code(value: object) -> str | None:
    candidate = getattr(value, "value", value)
    return _bounded_diagnostic_token(
        candidate,
        maximum=64,
        punctuation="_-",
    )


def _bounded_exception_chain(error: BaseException) -> list[dict[str, object]]:
    """Return a fixed, redacted projection of a bounded exception chain."""

    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while (
        current is not None
        and len(chain) < _DIAGNOSTIC_MAX_CAUSE_DEPTH
        and id(current) not in seen
    ):
        seen.add(id(current))
        item: dict[str, object] = {
            "exception_type": (
                _bounded_diagnostic_token(
                    type(current).__name__, punctuation="_"
                )
                or "unknown"
            )
        }
        code = _bounded_error_code(getattr(current, "code", None))
        if code is not None:
            item["error_code"] = code
        details = getattr(current, "details", None)
        if isinstance(details, dict):
            status = details.get("status")
            if isinstance(status, int) and 100 <= status <= 599:
                item["http_status"] = status
            method = details.get("method")
            if method in _SAFE_HTTP_METHODS:
                item["http_method"] = method
            endpoint_category = _bounded_diagnostic_token(
                details.get("endpoint_category"),
                punctuation="_-/",
            )
            if endpoint_category is not None:
                item["endpoint_category"] = endpoint_category
            for category_key in (
                "provider_category",
                "failure_category",
            ):
                provider_category = details.get(category_key)
                if provider_category in _SAFE_PROVIDER_CATEGORIES:
                    item["provider_category"] = provider_category
                    break
        chain.append(item)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return chain


def _bounded_nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _diagnostic_classification(
    *,
    write_attempted: bool | None,
    write_completed: bool | None,
    readback_completed: bool | None,
    desired_state_proven: bool | None,
    successful_write_count: int | None,
    mismatch_categories: list[str],
    cause_chain: list[dict[str, object]],
) -> str:
    explicit_rejection = any(
        isinstance(item.get("http_status"), int)
        and int(item["http_status"]) >= 400
        or item.get("provider_category") == "operation_rejected"
        for item in cause_chain
    )
    if (
        write_attempted is True
        and explicit_rejection
        and successful_write_count == 0
        and write_completed is not True
    ):
        return "transport_rejected"
    if (
        write_attempted is True
        and write_completed is True
        and readback_completed is True
        and desired_state_proven is False
        and bool(mismatch_categories)
    ):
        return "write_completed_readback_mismatch"
    return "write_outcome_indeterminate"


def _bounded_f2_failure_evidence(
    *,
    scenario: str,
    operation_id: str | None,
    plan: object | None,
    task: dict[str, Any] | None,
    observed_mutation_baseline: int,
    observed_mutation_count: int,
    error: BaseException,
) -> dict[str, object]:
    """Project persisted F2 failure evidence without payload or identity data."""

    details = getattr(error, "details", None)
    if not isinstance(details, dict):
        details = {}
    plan_failure = getattr(plan, "failure_information", None)
    if not isinstance(plan_failure, dict):
        plan_failure = {}

    active_operation = None
    operations = getattr(plan, "operations", None)
    if isinstance(operations, list):
        active_operation = next(
            (
                item
                for item in operations
                if getattr(item, "operation_id", None) == operation_id
            ),
            None,
        )
    receipt = getattr(active_operation, "execution_receipt", None)
    if not isinstance(receipt, dict):
        receipt = {}
    operation_failure = getattr(
        active_operation, "failure_information", None
    )
    if not isinstance(operation_failure, dict):
        operation_failure = {}
    verification = getattr(active_operation, "verification", None)

    def count(name: str) -> int | None:
        value = details.get(name, plan_failure.get(name))
        return _bounded_nonnegative_integer(value)

    attempted_write_count = count("attempted_write_count")
    successful_write_count = count("successful_write_count")
    verified_write_count = count("verified_write_count")
    ambiguous_write_count = count("ambiguous_write_count")

    write_attempted = (
        receipt.get("write_attempted")
        if isinstance(receipt.get("write_attempted"), bool)
        else None
    )
    write_completed = (
        receipt.get("write_completed")
        if isinstance(receipt.get("write_completed"), bool)
        else None
    )
    readback_completed = (
        receipt.get("readback_completed")
        if isinstance(receipt.get("readback_completed"), bool)
        else None
    )
    readback_attempted: bool | None = None
    if (
        readback_completed is True
        or successful_write_count not in {None, 0}
        or receipt.get("write_result") == "ambiguous"
        or operation_failure.get("readback_failure_category") is not None
    ):
        readback_attempted = True

    mismatch_values: list[object] = []
    for source in (
        details.get("mismatch_fields"),
        getattr(verification, "mismatch_fields", None),
    ):
        if isinstance(source, list):
            mismatch_values.extend(source)
    mismatch_categories = sorted(
        {
            token
            for item in mismatch_values
            if (
                token := _bounded_diagnostic_token(
                    item, punctuation="_-:"
                )
            )
            is not None
        }
    )[:_DIAGNOSTIC_MAX_MISMATCHES]

    desired_state_proven = details.get("desired_state_proven")
    if not isinstance(desired_state_proven, bool):
        desired_state_proven = operation_failure.get(
            "desired_state_proven"
        )
    if not isinstance(desired_state_proven, bool):
        desired_state_proven = (
            False
            if write_completed is True
            and readback_completed is True
            and bool(mismatch_categories)
            else None
        )

    operation_status = _bounded_error_code(
        getattr(active_operation, "execution_status", None)
    )
    completed_operation_count = (
        sum(
            _bounded_error_code(getattr(item, "execution_status", None))
            == "applied_verified"
            for item in operations
        )
        if isinstance(operations, list)
        else None
    )
    failed_operation_id = _bounded_diagnostic_token(
        plan_failure.get("failed_operation_id")
        or details.get("operation_id")
        or operation_id,
        punctuation="_-",
    )
    cause_chain = _bounded_exception_chain(error)

    provider_attempts = task.get("provider_attempts") if task else None
    provider_attempt_count = (
        len(provider_attempts)
        if isinstance(provider_attempts, list)
        else None
    )
    provider_response_received: bool | None = None
    if isinstance(provider_attempts, list) and provider_attempts:
        candidate = provider_attempts[-1].get("response_received")
        if isinstance(candidate, bool):
            provider_response_received = candidate

    approval = getattr(plan, "approval", None)
    approval_state = _bounded_error_code(
        getattr(approval, "state", None)
    )
    mutation_delta = max(
        0, observed_mutation_count - observed_mutation_baseline
    )
    evidence: dict[str, object] = {
        "phase": "f2_policy_acceptance",
        "scenario": scenario,
        "operation_id": _bounded_diagnostic_token(
            operation_id, punctuation="_-"
        ),
        "plan_id_short": _short_diagnostic_identifier(
            getattr(plan, "plan_id", None)
        ),
        "task_id_short": _short_diagnostic_identifier(
            task.get("task_id") if task else None
        ),
        "plan_status": _bounded_error_code(
            getattr(plan, "status", None)
        ),
        "execution_outcome": _bounded_diagnostic_token(
            getattr(plan, "execution_outcome", None), punctuation="_-"
        ),
        "task_state": _bounded_diagnostic_token(
            task.get("state") if task else None, punctuation="_-"
        ),
        "task_terminal_outcome": _bounded_diagnostic_token(
            task.get("terminal_outcome") if task else None,
            punctuation="_-",
        ),
        "approval_authority_version": (
            getattr(approval, "authority_version", None)
            if isinstance(getattr(approval, "authority_version", None), int)
            else None
        ),
        "approval_bundle_state": _bounded_diagnostic_token(
            getattr(approval, "bundle_state", None), punctuation="_-"
        ),
        "approval_consumed": (
            approval_state == "consumed"
            if approval_state is not None
            else None
        ),
        "provider_attempt_count": provider_attempt_count,
        "provider_response_received": provider_response_received,
        "operation_execution_status": operation_status,
        "write_attempted": write_attempted,
        "write_completed": write_completed,
        "write_verified": (
            operation_status == "applied_verified"
            if operation_status is not None
            else None
        ),
        "readback_attempted": readback_attempted,
        "readback_completed": readback_completed,
        "desired_state_proven": desired_state_proven,
        "attempted_write_count": attempted_write_count,
        "successful_write_count": successful_write_count,
        "verified_write_count": verified_write_count,
        "ambiguous_write_count": ambiguous_write_count,
        "completed_operation_count": completed_operation_count,
        "failed_operation_id": failed_operation_id,
        "mismatch_categories": mismatch_categories,
        "observed_mutation": (
            "recorded" if mutation_delta > 0 else "not_recorded"
        ),
        "diagnostic_classification": _diagnostic_classification(
            write_attempted=write_attempted,
            write_completed=write_completed,
            readback_completed=readback_completed,
            desired_state_proven=desired_state_proven,
            successful_write_count=successful_write_count,
            mismatch_categories=mismatch_categories,
            cause_chain=cause_chain,
        ),
        "cause_chain": cause_chain,
        "cleanup_attempted": None,
        "cleanup_succeeded": None,
        "cleanup_failure_category": None,
    }
    return evidence


def _attach_cleanup_evidence(
    error: BaseException,
    *,
    attempted: bool,
    succeeded: bool | None,
    failure: BaseException | None,
) -> None:
    diagnostic = getattr(error, "contract_diagnostic", None)
    if not isinstance(diagnostic, dict):
        return
    updated = dict(diagnostic)
    updated["cleanup_attempted"] = attempted
    updated["cleanup_succeeded"] = succeeded
    updated["cleanup_failure_category"] = (
        _bounded_diagnostic_token(type(failure).__name__, punctuation="_")
        if failure is not None
        else None
    )
    setattr(error, "contract_diagnostic", updated)


async def _json_request(session, method, path, *, json_body=None, data=None, token=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.request(
        method,
        f"{HA_URL}{path}",
        json=json_body,
        data=data,
        headers=headers,
    ) as response:
        if response.status >= 400:
            raise RuntimeError(f"Disposable Home Assistant bootstrap failed at {path} ({response.status})")
        return await response.json(content_type=None)


async def bootstrap_disposable_admin() -> str:
    """Complete first-run onboarding and return an in-memory access token."""

    username = f"contract_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(120):
            try:
                async with session.get(f"{HA_URL}/api/onboarding") as response:
                    if response.status == 200:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Disposable Home Assistant did not become ready within 120 seconds")

        onboarding = await _json_request(session, "GET", "/api/onboarding")
        if any(item.get("done") for item in onboarding if item.get("step") == "user"):
            raise RuntimeError("Disposable Home Assistant configuration was not fresh")
        user = await _json_request(
            session,
            "POST",
            "/api/onboarding/users",
            json_body={
                "client_id": CLIENT_ID,
                "name": "Beta 25 Contract Administrator",
                "username": username,
                "password": password,
                "language": "en",
            },
        )
        auth_code = user.get("auth_code")
        if not auth_code:
            raise RuntimeError("Disposable Home Assistant did not return an onboarding authorization code")
        token = await _json_request(
            session,
            "POST",
            "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": CLIENT_ID,
            },
        )
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("Disposable Home Assistant did not issue an access token")
        # Complete only supported onboarding steps; failures here are contract
        # failures because the temporary instance must represent a usable Core.
        steps = {item.get("step"): bool(item.get("done")) for item in onboarding}
        if not steps.get("core_config"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/core_config",
                json_body={},
                token=access_token,
            )
        if not steps.get("integration"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/integration",
                json_body={"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID},
                token=access_token,
            )
        if not steps.get("analytics"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/analytics",
                json_body={},
                token=access_token,
            )
        return access_token


def settings(token: str) -> Settings:
    return Settings(
        ha_url=HA_URL,
        ha_token=token,
        access_secret="disposable-contract-access-secret",
        port=8100,
        audit_path="/tmp/disposable-contract-audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        ha_timeout_seconds=30,
    )


async def wait_for_runtime_ready(rest: HomeAssistantRestClient) -> dict:
    """Wait for Core and the required integrations to finish starting."""

    required_components = {
        "automation",
        "config",
        "input_boolean",
        "input_number",
        "script",
        "system_log",
        "websocket_api",
    }
    for _ in range(120):
        try:
            runtime_config = await rest.request("GET", "/config")
            components = set(runtime_config.get("components", []))
            if (
                runtime_config.get("state") == "RUNNING"
                and required_components.issubset(components)
            ):
                return runtime_config
        except Exception:
            # Startup may briefly reject authenticated API requests. The
            # bounded deadline below turns persistent failure into a contract
            # failure without exposing a response body or credential.
            pass
        await asyncio.sleep(1)
    raise RuntimeError("Disposable Home Assistant did not finish required integration setup")


async def _advance_config_flow(
    session: aiohttp.ClientSession,
    token: str,
    handler: str,
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Complete one supported config flow using only authenticated Core APIs."""

    result = await _json_request(
        session,
        "POST",
        "/api/config/config_entries/flow",
        json_body={"handler": handler, "show_advanced_options": False},
        token=token,
    )
    for user_input in inputs:
        if result.get("type") not in {"form", "menu"}:
            raise RuntimeError("Disposable Home Assistant config flow ended early")
        flow_id = result.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id:
            raise RuntimeError("Disposable Home Assistant config flow omitted its id")
        result = await _json_request(
            session,
            "POST",
            f"/api/config/config_entries/flow/{flow_id}",
            json_body=user_input,
            token=token,
        )
    if result.get("type") != "create_entry":
        raise RuntimeError("Disposable Home Assistant config entry was not created")
    return result


async def _wait_for_writer_fixture(
    websocket: HomeAssistantWebSocketClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Wait until both writer-owned entities share one pre-2026.8 device."""

    for _ in range(60):
        entities = await websocket.command(
            {"type": "config/entity_registry/list"}
        )
        fixture_entities = [
            item
            for item in entities
            if isinstance(item, dict) and item.get("platform") == FIXTURE_PLATFORM
        ]
        device_ids = {
            item.get("device_id") for item in fixture_entities if item.get("device_id")
        }
        if len(fixture_entities) == 2 and len(device_ids) == 1:
            devices = await websocket.command(
                {"type": "config/device_registry/list"}
            )
            return fixture_entities, devices
        await asyncio.sleep(1)
    raise RuntimeError("The exact 2026.7.2 writer did not create one composite device")


async def prepare_migration_fixture() -> None:
    """Use exact HA 2026.7.2 to persist the historical upgrade fixture."""

    if DEVICE_FIXTURE_PATH is None or TOKEN_PATH is None:
        raise RuntimeError("Fixture and token paths are required in preparation mode")
    token = await bootstrap_disposable_admin()
    configured = settings(token)
    rest = HomeAssistantRestClient(configured)
    websocket = HomeAssistantWebSocketClient(configured)
    await wait_for_runtime_ready(rest)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for slot in ("a", "b"):
            await _advance_config_flow(
                session,
                token,
                FIXTURE_PLATFORM,
                [{"slot": slot}],
            )
        await _advance_config_flow(
            session,
            token,
            "ha_mcp_tools",
            [{"next_step_id": "tools"}, {}],
        )

    fixture_entities, devices = await _wait_for_writer_fixture(websocket)
    old_device_id = str(fixture_entities[0]["device_id"])
    composite = next(
        (
            item
            for item in devices
            if isinstance(item, dict) and item.get("id") == old_device_id
        ),
        None,
    )
    if not isinstance(composite, dict):
        raise RuntimeError("The historical composite device was not enumerable")
    config_entry_ids = {
        str(item.get("config_entry_id"))
        for item in fixture_entities
        if item.get("config_entry_id")
    }
    if len(config_entry_ids) != 2 or set(composite.get("config_entries", ())) != config_entry_ids:
        raise RuntimeError("The writer did not bind both config entries to one device")

    entity_ids = sorted(str(item["entity_id"]) for item in fixture_entities)
    automation = {
        "id": MIGRATION_AUTOMATION_ID,
        "alias": "Beta 23 composite device reference",
        "description": "Untriggered disposable direct-device reference fixture",
        "trigger": [
            {
                "platform": "event",
                "event_type": "beta23_composite_contract_never_fired",
            }
        ],
        "condition": [],
        "action": [
            {
                "service": "switch.turn_on",
                "target": {"device_id": old_device_id},
            },
            {
                "event": "beta23_composite_contract_observed",
                "event_data": {"entity_id": entity_ids[0]},
            },
        ],
        "mode": "single",
    }
    await rest.request(
        "POST",
        f"/config/automation/config/{MIGRATION_AUTOMATION_ID}",
        automation,
    )
    await rest.request("POST", "/services/automation/reload", {})

    DEVICE_FIXTURE_PATH.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "writer_version": "2026.7.2",
                "old_composite_device_id": old_device_id,
                "config_entry_ids": sorted(config_entry_ids),
                "primary_config_entry_id": composite.get(
                    "primary_config_entry"
                ),
                "entity_ids": entity_ids,
                "automation_id": MIGRATION_AUTOMATION_ID,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    TOKEN_PATH.write_text(token, encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    # Registry stores use delayed writes. Keep the exact writer alive long
    # enough to persist through its own storage layer before graceful shutdown.
    await asyncio.sleep(12)


def _load_prepared_token() -> str:
    """Load the ephemeral token retained only for this disposable upgrade."""

    if TOKEN_PATH is None or not TOKEN_PATH.is_file():
        raise RuntimeError("The disposable writer token is unavailable")
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("The disposable writer token is empty")
    return token


def _load_device_fixture() -> dict[str, Any]:
    """Read the bounded, non-secret migration marker produced by 2026.7.2."""

    if DEVICE_FIXTURE_PATH is None or not DEVICE_FIXTURE_PATH.is_file():
        raise RuntimeError("The disposable device migration marker is unavailable")
    value = json.loads(DEVICE_FIXTURE_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("writer_version") != "2026.7.2"
        or value.get("automation_id") != MIGRATION_AUTOMATION_ID
    ):
        raise RuntimeError("The disposable device migration marker is invalid")
    return value


class _ObservedConfigurationGateway:
    """Record bounded mutation results while delegating to the real gateway."""

    def __init__(self, gateway: ConfigurationResourceGateway):
        self.gateway = gateway
        self.mutations: list[dict[str, object]] = []

    async def read(
        self, resource_type: str, resource_id: str
    ) -> dict | None:
        return await self.gateway.read(resource_type, resource_id)

    async def write(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        approved_config: dict,
    ):
        result = await self.gateway.write(
            action,
            resource_type,
            resource_id,
            approved_config,
        )
        self.mutations.append(
            {
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "result": copy.deepcopy(result),
            }
        )
        return result

    async def validate_all(self):
        return await self.gateway.validate_all()


class _LegacyAutomationCompatibilityGateway:
    """Expose the contract-v1 automation interface over the reviewed gateway."""

    def __init__(self, gateway: ConfigurationResourceGateway):
        self.gateway = gateway

    async def get(self, automation_id: str) -> dict | None:
        return await self.gateway.read("automation", automation_id)

    async def write(self, automation_id: str, config: dict):
        return await self.gateway.write(
            "update", "automation", automation_id, config
        )

    async def validate(self):
        return await self.gateway.validate_all()


def _assert_exact_resource(
    resource_type: str,
    resource_id: str,
    desired: dict,
    actual: dict | None,
) -> str:
    """Require exact identity and normalized desired/readback equality."""

    assert resource_identity_matches(resource_type, resource_id, actual)
    comparison = compare_resource_verification(
        resource_type, desired, actual
    )
    assert comparison.semantic_match
    return comparison.binding_approved_fingerprint


def _assert_strict_configuration_check(result) -> None:
    """Require the exact successful response shape pinned by contract v2."""

    assert isinstance(result, dict)
    assert set(result) == {"result", "errors", "warnings"}
    assert result["result"] == "valid"
    assert result["errors"] is None
    assert result["warnings"] is None


def _configuration_operations() -> list[dict]:
    """Build one linearly ordered, behavior-free four-resource create plan."""

    operations = []
    prior_operation_id = None
    for resource_type in RESOURCE_ORDER:
        operation_id = f"create_{resource_type}"
        operation = {
            "operation_id": operation_id,
            "resource_type": (
                "helper"
                if resource_type in {"input_boolean", "input_number"}
                else resource_type
            ),
            "action": "create",
            "target_id": RESOURCE_IDS[resource_type],
            "depends_on": (
                [prior_operation_id] if prior_operation_id else []
            ),
            "proposed_config": copy.deepcopy(
                CREATE_CONFIGS[resource_type]
            ),
        }
        if resource_type in {"input_boolean", "input_number"}:
            operation["helper_type"] = resource_type
        operations.append(operation)
        prior_operation_id = operation_id
    return operations


async def _run_governed_configuration_contract(
    gateway: ConfigurationResourceGateway,
    token: str,
) -> None:
    """Exercise planning, external approval, apply, and idempotent reapply."""

    observed = _ObservedConfigurationGateway(gateway)
    with tempfile.TemporaryDirectory(
        prefix="dev14-real-ha-contract-"
    ) as directory:
        contract_root = Path(directory)
        audit_secret = "disposable-dev14-contract-audit-secret"
        service = ChangeGovernanceService(
            ChangePlanRepository(contract_root / "plans"),
            observed,
            AuditLogger(
                str(contract_root / "audit.jsonl"),
                audit_secret,
            ),
            sensitive_values=(audit_secret, token),
        )
        telemetry, context = begin_request(
            "dev14-real-ha-configuration-contract"
        )
        telemetry.caller_id = "dev14-real-ha-contract-caller"
        try:
            created = await service.create_configuration_plan(
                title="Dev14 disposable real Home Assistant contract",
                description=(
                    "Create four behavior-free fixtures in the disposable "
                    "pinned Home Assistant instance."
                ),
                operations=_configuration_operations(),
                caller_context={
                    "environment": "disposable_pinned_home_assistant"
                },
            )
            assert created["contract_version"] == 2
            assert created["operation"] == "configuration_plan"
            assert created["status"] == "awaiting_approval"
            assert created["execution_outcome"] == "not_started"
            assert len(created["operations"]) == len(RESOURCE_ORDER)

            pending = service.approve(
                created["plan_id"], created["plan_hash"]
            )
            assert pending["status"] == "approval_pending"
            assert (
                pending["approval_lifecycle"]
                == "approval_pending_external"
            )
            assert pending["approval_state"] == "external_pending"
            assert pending["bound_plan_hash"] == created["plan_hash"]
            assert pending["external_approval_required"] is True

            review, csrf = await service.issue_external_csrf(
                created["plan_id"], pending["challenge_id"]
            )
            assert review["plan_hash"] == created["plan_hash"]
            assert review["approval_state"] == "external_pending"
            assert review["operation_count"] == len(RESOURCE_ORDER)
            assert all(
                item["semantic_projection"]["projection_complete"] is True
                for item in review["operation_summaries"]
            )

            approved = await service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind="apply",
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:"
                    "disposable-contract-reviewer"
                ),
            )
            assert approved["status"] == "approved"
            assert approved["approval_kind"] == "apply"

            applied = await service.apply(
                created["plan_id"], created["plan_hash"]
            )
            assert applied["status"] == "applied"
            assert applied["execution_outcome"] == "applied"
            assert applied["configuration_check_status"] == "valid"
            assert applied["hash_validation"] == {
                "performed": True,
                "result": "matched",
            }
            assert len(observed.mutations) == len(RESOURCE_ORDER)
            assert [
                mutation["resource_type"]
                for mutation in observed.mutations
            ] == list(RESOURCE_ORDER)
            assert [
                mutation["action"] for mutation in observed.mutations
            ] == ["create"] * len(RESOURCE_ORDER)
            assert [
                receipt["target_id"]
                for receipt in applied["operations"]
            ] == [
                RESOURCE_IDS[resource_type]
                for resource_type in RESOURCE_ORDER
            ]

            expected_fingerprints = {
                resource_type: _assert_exact_resource(
                    resource_type,
                    RESOURCE_IDS[resource_type],
                    CREATE_CONFIGS[resource_type],
                    await gateway.read(
                        resource_type, RESOURCE_IDS[resource_type]
                    ),
                )
                for resource_type in RESOURCE_ORDER
            }
            for receipt in applied["operations"]:
                resolved_type = (
                    receipt["helper_type"]
                    if receipt["resource_type"] == "helper"
                    else receipt["resource_type"]
                )
                expected_fingerprint = expected_fingerprints[resolved_type]
                assert receipt["execution_status"] == "applied_verified"
                assert (
                    receipt["verification"]["desired_fingerprint"]
                    == expected_fingerprint
                )
                assert (
                    receipt["verification"]["actual_fingerprint"]
                    == expected_fingerprint
                )
                assert (
                    receipt["execution_receipt"][
                        "resulting_fingerprint"
                    ]
                    == expected_fingerprint
                )

            for mutation in observed.mutations:
                resource_type = str(mutation["resource_type"])
                result = mutation["result"]
                if resource_type in {"automation", "script"}:
                    assert result == {"result": "ok"}
                    continue
                _assert_exact_resource(
                    resource_type,
                    str(mutation["resource_id"]),
                    CREATE_CONFIGS[resource_type],
                    result if isinstance(result, dict) else None,
                )

            mutation_count = len(observed.mutations)
            reapplied = await service.apply(
                created["plan_id"], created["plan_hash"]
            )
            assert reapplied["status"] == "already_applied"
            assert reapplied["execution_outcome"] == "applied"
            assert reapplied["hash_validation"] == {
                "performed": True,
                "result": "matched",
            }
            assert len(observed.mutations) == mutation_count
            assert all(
                item["execution_status"] == "applied_verified"
                for item in reapplied["operations"]
            )
        finally:
            end_request(context)


async def _run_legacy_automation_compatibility_contract(
    gateway: AutomationGateway,
) -> None:
    """Preserve the preexisting automation-only real-HA contract."""

    automation_id = RESOURCE_IDS["automation"]
    before = await gateway.get(automation_id)
    assert before and before.get("id") == automation_id
    assert normalize_automation(before) == normalize_automation(
        CREATE_CONFIGS["automation"]
    )

    result = await gateway.write(
        automation_id,
        copy.deepcopy(LEGACY_AUTOMATION_CONFIG),
    )
    assert result == {"result": "ok"}
    readback = await gateway.get(automation_id)
    assert readback and readback.get("id") == automation_id
    assert normalize_automation(readback) == normalize_automation(
        LEGACY_AUTOMATION_CONFIG
    )
    _assert_strict_configuration_check(await gateway.validate())


async def _decide_f2_action(
    service: ChangeGovernanceService,
    created: dict,
    pending: dict,
    *,
    principal: str,
) -> dict:
    """Complete exactly the active bounded administrator action."""

    review, csrf = await service.issue_external_csrf(
        created["plan_id"], pending["challenge_id"]
    )
    assert review["plan_hash"] == created["plan_hash"]
    assert review["approval_action"] == pending["approval_action"]
    return await service.decide_external_approval(
        plan_id=created["plan_id"],
        challenge_id=pending["challenge_id"],
        expected_plan_hash=created["plan_hash"],
        approval_kind="apply",
        approval_action=pending["approval_action"],
        csrf_nonce=csrf,
        decision="approve",
        approver_principal=principal,
    )


def _assert_single_task_dispatch(
    service: ChangeGovernanceService,
    plan_id: str,
    task_id: str,
) -> dict:
    tasks = service.list_execution_tasks(plan_id=plan_id)
    assert tasks["count"] == 1
    task = service.get_execution_task(task_id)
    assert task["task_id"] == task_id
    assert task["state"] == "succeeded_verified"
    assert task["provider_attempt_count"] == 1
    assert task["provider_attempts"][0]["response_received"] is True
    assert isinstance(
        task["provider_attempts"][0].get("response_recorded_at"), str
    )
    assert task["verification_summary"][
        "provider_response_received"
    ] is True
    assert sum(
        event["event_type"] == "dispatch_attempted"
        for event in task["lifecycle_events"]
    ) == 1
    return task


async def _run_f2_policy_acceptance_contract(
    gateway: ConfigurationResourceGateway,
    token: str,
) -> dict[str, object]:
    """Exercise standard, elevated, and prohibited F2 policy in disposable HA."""

    observed = _ObservedConfigurationGateway(gateway)
    scenario = "setup"
    active_plan_id: str | None = None
    active_operation_id: str | None = None
    observed_mutation_baseline = 0
    with tempfile.TemporaryDirectory(
        prefix="f2-real-ha-contract-"
    ) as directory:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(directory) / "plans"),
            observed,
            sensitive_values=(token,),
        )
        telemetry, context = begin_request(
            "f2-real-ha-policy-contract"
        )
        telemetry.caller_id = "f2-real-ha-contract-caller"
        try:
            scenario = "standard_admin"
            active_operation_id = "standard_helper_update"
            observed_mutation_baseline = len(observed.mutations)
            standard = await service.create_configuration_plan(
                title="F2 disposable standard plan",
                description="Update one harmless helper configuration.",
                operations=[
                    {
                        "operation_id": "standard_helper_update",
                        "resource_type": "helper",
                        "helper_type": "input_boolean",
                        "action": "update",
                        "target_id": RESOURCE_IDS["input_boolean"],
                        "depends_on": [],
                        "proposed_config": copy.deepcopy(
                            F2_STANDARD_HELPER_CONFIG
                        ),
                    }
                ],
            )
            active_plan_id = standard["plan_id"]
            assert standard["policy_decision"]["policy_class"] == (
                "standard_admin"
            )
            assert standard["policy_decision"][
                "required_acknowledgements"
            ] == ["plan_approval"]
            assert len(observed.mutations) == 0
            standard_pending = service.approve(
                standard["plan_id"], standard["plan_hash"]
            )
            standard_approved = await _decide_f2_action(
                service,
                standard,
                standard_pending,
                principal=F2_ADMIN_A,
            )
            assert standard_approved["status"] == "approved"
            assert len(observed.mutations) == 0
            standard_applied = await service.apply(
                standard["plan_id"], standard["plan_hash"]
            )
            assert standard_applied["status"] == "applied"
            assert len(observed.mutations) == 1
            _assert_exact_resource(
                "input_boolean",
                RESOURCE_IDS["input_boolean"],
                F2_STANDARD_HELPER_CONFIG,
                await gateway.read(
                    "input_boolean", RESOURCE_IDS["input_boolean"]
                ),
            )
            _assert_single_task_dispatch(
                service,
                standard["plan_id"],
                standard_applied["task_id"],
            )
            standard_duplicate = await service.apply(
                standard["plan_id"], standard["plan_hash"]
            )
            assert standard_duplicate["status"] == "already_applied"
            assert standard_duplicate["task_id"] == standard_applied["task_id"]
            assert standard_duplicate["task_reused"] is True
            assert len(observed.mutations) == 1
            standard_task = _assert_single_task_dispatch(
                service,
                standard["plan_id"],
                standard_applied["task_id"],
            )
            assert sum(
                event["event_type"] == "duplicate_apply_prevented"
                for event in standard_task["lifecycle_events"]
            ) == 1
            standard_events = [
                event["event_type"]
                for event in standard_task["lifecycle_events"]
            ]
            assert standard_events.index("task_created") < (
                standard_events.index("approval_consumed")
            ) < standard_events.index("dispatch_attempted")
            assert standard_task["approval_reference"][
                "approval_bundle_state"
            ] == "consumed"

            scenario = "elevated_admin"
            active_operation_id = "elevated_automation_update"
            observed_mutation_baseline = len(observed.mutations)
            traces_before = await fetch_normalized_trace_list(
                gateway.websocket_client.command,
                RESOURCE_IDS["automation"],
                known_secrets=(token,),
            )
            trace_ids_before = tuple(
                trace.run_id for trace in traces_before.headers
            )
            elevated = await service.create_configuration_plan(
                title="F2 disposable elevated plan",
                description=(
                    "Configure, but do not trigger, one future physical action."
                ),
                operations=[
                    {
                        "operation_id": "elevated_automation_update",
                        "resource_type": "automation",
                        "action": "update",
                        "target_id": RESOURCE_IDS["automation"],
                        "depends_on": [],
                        "proposed_config": copy.deepcopy(
                            F2_ELEVATED_AUTOMATION_CONFIG
                        ),
                    }
                ],
            )
            active_plan_id = elevated["plan_id"]
            assert elevated["policy_decision"]["policy_class"] == (
                "elevated_admin"
            )
            assert elevated["policy_decision"]["risk_delta"] == "moderate"
            assert elevated["policy_decision"][
                "physical_consequence"
            ] == "direct"
            assert elevated["policy_decision"][
                "required_acknowledgements"
            ] == [
                "plan_approval",
                "elevated_risk_acknowledgement",
            ]
            mutation_count = len(observed.mutations)
            elevated_pending = service.approve(
                elevated["plan_id"], elevated["plan_hash"]
            )
            _review, out_of_sequence_csrf = (
                await service.issue_external_csrf(
                    elevated["plan_id"],
                    elevated_pending["challenge_id"],
                )
            )
            try:
                await service.decide_external_approval(
                    plan_id=elevated["plan_id"],
                    challenge_id=elevated_pending["challenge_id"],
                    expected_plan_hash=elevated["plan_hash"],
                    approval_kind="apply",
                    approval_action=(
                        "elevated_risk_acknowledgement"
                    ),
                    csrf_nonce=out_of_sequence_csrf,
                    decision="approve",
                    approver_principal=F2_ADMIN_A,
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.APPROVAL_SEQUENCE_FAILURE
            else:
                raise AssertionError(
                    "elevated risk was acknowledged before plan approval"
                )
            elevated_ack_pending = await _decide_f2_action(
                service,
                elevated,
                elevated_pending,
                principal=F2_ADMIN_A,
            )
            assert elevated_ack_pending["status"] == "approval_pending"
            assert elevated_ack_pending["approval_action"] == (
                "elevated_risk_acknowledgement"
            )
            assert len(observed.mutations) == mutation_count
            try:
                await service.apply(
                    elevated["plan_id"], elevated["plan_hash"]
                )
            except GovernanceError as exc:
                assert exc.code == (
                    ErrorCode.ELEVATED_RISK_ACKNOWLEDGEMENT_REQUIRED
                )
            else:
                raise AssertionError(
                    "one elevated approval unexpectedly authorized apply"
                )
            assert service.list_execution_tasks(
                plan_id=elevated["plan_id"]
            )["count"] == 0

            _review, wrong_admin_csrf = await service.issue_external_csrf(
                elevated["plan_id"],
                elevated_ack_pending["challenge_id"],
            )
            try:
                await service.decide_external_approval(
                    plan_id=elevated["plan_id"],
                    challenge_id=elevated_ack_pending["challenge_id"],
                    expected_plan_hash=elevated["plan_hash"],
                    approval_kind="apply",
                    approval_action="elevated_risk_acknowledgement",
                    csrf_nonce=wrong_admin_csrf,
                    decision="approve",
                    approver_principal=F2_ADMIN_B,
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.APPROVAL_PRINCIPAL_MISMATCH
            else:
                raise AssertionError(
                    "a different administrator acknowledged elevated risk"
                )
            assert len(observed.mutations) == mutation_count

            elevated_approved = await _decide_f2_action(
                service,
                elevated,
                elevated_ack_pending,
                principal=F2_ADMIN_A,
            )
            assert elevated_approved["status"] == "approved"
            elevated_applied = await service.apply(
                elevated["plan_id"], elevated["plan_hash"]
            )
            assert elevated_applied["status"] == "applied"
            assert len(observed.mutations) == mutation_count + 1
            _assert_exact_resource(
                "automation",
                RESOURCE_IDS["automation"],
                F2_ELEVATED_AUTOMATION_CONFIG,
                await gateway.read(
                    "automation", RESOURCE_IDS["automation"]
                ),
            )
            elevated_receipt = elevated_applied["operations"][0][
                "execution_receipt"
            ]
            assert elevated_receipt[
                "raw_approved_fingerprint"
            ] != elevated_receipt["raw_observed_fingerprint"]
            assert elevated_receipt[
                "normalized_approved_fingerprint"
            ] == elevated_receipt["normalized_observed_fingerprint"]
            assert elevated_receipt[
                "canonicalization_categories"
            ] == ["automation_action_service_alias"]
            assert elevated_receipt[
                "semantic_verification_result"
            ] == "matched"
            elevated_task = _assert_single_task_dispatch(
                service,
                elevated["plan_id"],
                elevated_applied["task_id"],
            )
            assert elevated_task["approval_reference"][
                "same_principal_confirmed"
            ] is True
            assert elevated_task["approval_reference"][
                "bound_plan_hash"
            ] == elevated["plan_hash"]
            assert elevated_task["approval_reference"][
                "policy_decision_hash"
            ] == elevated["policy_decision"]["policy_decision_hash"]
            acknowledgement = elevated_task["approval_reference"][
                "elevated_risk_acknowledgement"
            ]
            assert acknowledgement["bound_plan_hash"] == elevated[
                "plan_hash"
            ]
            assert acknowledgement["policy_decision_hash"] == elevated[
                "policy_decision"
            ]["policy_decision_hash"]
            elevated_events = [
                event["event_type"]
                for event in elevated_task["lifecycle_events"]
            ]
            assert elevated_events.index("task_created") < (
                elevated_events.index("approval_consumed")
            ) < elevated_events.index("dispatch_attempted")
            traces_after = await fetch_normalized_trace_list(
                gateway.websocket_client.command,
                RESOURCE_IDS["automation"],
                known_secrets=(token,),
            )
            assert tuple(
                trace.run_id for trace in traces_after.headers
            ) == trace_ids_before
            elevated_duplicate = await service.apply(
                elevated["plan_id"], elevated["plan_hash"]
            )
            assert elevated_duplicate["status"] == "already_applied"
            assert elevated_duplicate["task_id"] == elevated_applied["task_id"]
            assert elevated_duplicate["task_reused"] is True
            assert len(observed.mutations) == mutation_count + 1
            elevated_task = _assert_single_task_dispatch(
                service,
                elevated["plan_id"],
                elevated_applied["task_id"],
            )
            assert sum(
                event["event_type"] == "duplicate_apply_prevented"
                for event in elevated_task["lifecycle_events"]
            ) == 1

            scenario = "prohibited"
            active_operation_id = "prohibited_automation_update"
            observed_mutation_baseline = len(observed.mutations)
            prohibited_health_baseline = service.health_summary()
            prohibited = await service.create_configuration_plan(
                title="F2 disposable prohibited plan",
                description="Attempt to configure a safety-critical action.",
                operations=[
                    {
                        "operation_id": "prohibited_automation_update",
                        "resource_type": "automation",
                        "action": "update",
                        "target_id": RESOURCE_IDS["automation"],
                        "depends_on": [],
                        "proposed_config": copy.deepcopy(
                            F2_PROHIBITED_AUTOMATION_CONFIG
                        ),
                    }
                ],
            )
            active_plan_id = prohibited["plan_id"]
            assert prohibited["policy_decision"]["policy_class"] == (
                "prohibited"
            )
            assert prohibited["policy_decision"][
                "physical_consequence"
            ] == "safety_critical"
            assert prohibited["policy_decision"][
                "required_acknowledgements"
            ] == []
            assert prohibited["status"] == "prohibited"
            assert prohibited["approval"]["state"] == "prohibited"
            assert prohibited["approval_lifecycle"] == "prohibited"
            assert prohibited["approval_actionable"] is False
            assert prohibited["approval_challenge_created"] is False
            try:
                service.approve(
                    prohibited["plan_id"], prohibited["plan_hash"]
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.PROHIBITED_CHANGE
            else:
                raise AssertionError(
                    "a prohibited plan created an approval challenge"
                )
            try:
                await service.apply(
                    prohibited["plan_id"], prohibited["plan_hash"]
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.PROHIBITED_CHANGE
            else:
                raise AssertionError("a prohibited plan reached apply")
            assert service.list_execution_tasks(
                plan_id=prohibited["plan_id"]
            )["count"] == 0
            prohibited_plan = service.repository.get(
                prohibited["plan_id"]
            )
            assert prohibited_plan is not None
            assert prohibited_plan.approval.bundle_state == "prohibited"
            assert prohibited_plan.approval.challenge_id is None
            assert prohibited_plan.approval.state.value == "required"
            prohibited_health = service.health_summary()
            assert prohibited_health["plans_awaiting_approval"] == (
                prohibited_health_baseline["plans_awaiting_approval"]
            )
            assert prohibited_health["plans_requiring_approval"] == (
                prohibited_health_baseline["plans_requiring_approval"]
            )
            assert prohibited_health["pending_plan_approvals"] == (
                prohibited_health_baseline["pending_plan_approvals"]
            )
            assert prohibited_health[
                "pending_elevated_acknowledgements"
            ] == prohibited_health_baseline[
                "pending_elevated_acknowledgements"
            ]
            assert prohibited_health["prohibited_policy_decisions"] == (
                prohibited_health_baseline["prohibited_policy_decisions"]
                + 1
            )
            assert len(observed.mutations) == mutation_count + 1

            scenario = "prohibited_non_entity_target"
            active_operation_id = "prohibited_device_target_update"
            observed_mutation_baseline = len(observed.mutations)
            prohibited_device_target = (
                await service.create_configuration_plan(
                    title="F2 disposable device-target prohibited plan",
                    description=(
                        "Reject a safety-critical service before dispatch."
                    ),
                    operations=[
                        {
                            "operation_id": (
                                "prohibited_device_target_update"
                            ),
                            "resource_type": "automation",
                            "action": "update",
                            "target_id": RESOURCE_IDS["automation"],
                            "depends_on": [],
                            "proposed_config": copy.deepcopy(
                                F2_PROHIBITED_DEVICE_TARGET_AUTOMATION_CONFIG
                            ),
                        }
                    ],
                )
            )
            active_plan_id = prohibited_device_target["plan_id"]
            device_policy = prohibited_device_target["policy_decision"]
            assert device_policy["policy_class"] == "prohibited"
            assert device_policy["physical_consequence"] == (
                "safety_critical"
            )
            assert device_policy["required_acknowledgements"] == []
            try:
                service.approve(
                    prohibited_device_target["plan_id"],
                    prohibited_device_target["plan_hash"],
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.PROHIBITED_CHANGE
            else:
                raise AssertionError(
                    "a device-target prohibited plan created an approval"
                )
            try:
                await service.apply(
                    prohibited_device_target["plan_id"],
                    prohibited_device_target["plan_hash"],
                )
            except GovernanceError as exc:
                assert exc.code == ErrorCode.PROHIBITED_CHANGE
            else:
                raise AssertionError(
                    "a device-target prohibited plan reached apply"
                )
            assert service.list_execution_tasks(
                plan_id=prohibited_device_target["plan_id"]
            )["count"] == 0
            prohibited_device_plan = service.repository.get(
                prohibited_device_target["plan_id"]
            )
            assert prohibited_device_plan is not None
            assert prohibited_device_plan.approval.bundle_state == (
                "prohibited"
            )
            assert prohibited_device_plan.approval.challenge_id is None
            prohibited_device_public = service.get_plan(
                prohibited_device_target["plan_id"]
            )
            assert prohibited_device_public["status"] == "prohibited"
            assert prohibited_device_public["approval"]["state"] == (
                "prohibited"
            )
            assert prohibited_device_public["approval_actionable"] is False
            prohibited_device_health = service.health_summary()
            assert prohibited_device_health["plans_awaiting_approval"] == (
                prohibited_health_baseline["plans_awaiting_approval"]
            )
            assert prohibited_device_health["plans_requiring_approval"] == (
                prohibited_health_baseline["plans_requiring_approval"]
            )
            assert prohibited_device_health[
                "prohibited_policy_decisions"
            ] == (
                prohibited_health_baseline[
                    "prohibited_policy_decisions"
                ]
                + 2
            )
            assert len(observed.mutations) == mutation_count + 1

            scenario = "persisted_beta6_prohibited_upgrade"
            active_operation_id = "persisted_prohibited_automation_update"
            observed_mutation_baseline = len(observed.mutations)
            upgrade_health_baseline = service.health_summary()
            legacy_gateway = _LegacyAutomationCompatibilityGateway(gateway)
            historical_fixture_names = (
                "beta6_prohibited_superseded_contract_v2_a.json",
                "beta6_legacy_prohibited_expired_automation_a.json",
                "beta6_legacy_prohibited_expired_automation_b.json",
            )
            historical_records = []
            for fixture_name in historical_fixture_names:
                historical_fixture_path = (
                    ROOT / "tests" / "fixtures" / fixture_name
                )
                historical_bytes = historical_fixture_path.read_bytes()
                historical_value = json.loads(historical_bytes)
                active_plan_id = historical_value["plan_id"]
                if "contract_v2" in fixture_name:
                    assert historical_value["contract_version"] == 2
                    assert historical_value["operations"]
                    assert {
                        operation["execution_status"]
                        for operation in historical_value["operations"]
                    } == {"pending"}
                else:
                    assert "contract_version" not in historical_value
                    assert "operations" not in historical_value
                    assert historical_value["operation"] == (
                        "update_automation"
                    )
                    assert historical_value["target"]["target_type"] == (
                        "automation"
                    )
                    assert historical_value["status"] == "expired"
                historical_path = (
                    Path(directory) / "plans" / f"{active_plan_id}.json"
                )
                assert not historical_path.exists()
                historical_path.write_bytes(historical_bytes)
                historical_records.append(
                    (
                        active_plan_id,
                        historical_path,
                        historical_path.read_bytes(),
                    )
                )

            recovered = ChangeGovernanceService(
                service.repository,
                legacy_gateway,
                sensitive_values=(token,),
            )
            for historical_plan_id, _path, _before in historical_records:
                historical_public = recovered.get_plan(historical_plan_id)
                assert historical_public["status"] == "prohibited"
                assert historical_public["approval"]["state"] == (
                    "prohibited"
                )
                assert historical_public["approval_lifecycle"] == (
                    "prohibited"
                )
                assert historical_public["approval_bundle_state"] == (
                    "prohibited"
                )
                assert historical_public["approval_actionable"] is False
                assert historical_public[
                    "approval_challenge_created"
                ] is False
                assert historical_public["apply_allowed"] is False
                assert historical_public["next_required_operation"] is None
            assert recovered.list_plans(limit=100)["count"] >= len(
                historical_records
            )
            historical_listing = recovered.list_plans(
                status="prohibited", limit=100
            )
            prohibited_plan_ids = {
                item["plan_id"] for item in historical_listing["plans"]
            }
            assert all(
                historical_plan_id in prohibited_plan_ids
                for historical_plan_id, _path, _before in historical_records
            )
            assert historical_listing["partial"] is False
            assert historical_listing["projection_failure_count"] == 0
            awaiting_listing = recovered.list_plans(
                status="awaiting_approval", limit=100
            )
            awaiting_plan_ids = {
                item["plan_id"] for item in awaiting_listing["plans"]
            }
            assert all(
                historical_plan_id not in awaiting_plan_ids
                for historical_plan_id, _path, _before in historical_records
            )
            assert all(
                review["plan_id"]
                not in {
                    historical_plan_id
                    for historical_plan_id, _path, _before
                    in historical_records
                }
                for review in recovered.pending_external_reviews()
            )
            upgrade_health = recovered.health_summary()
            assert upgrade_health["prohibited_policy_decisions"] == (
                upgrade_health_baseline["prohibited_policy_decisions"]
                + len(historical_records)
            )
            assert upgrade_health["projection_failure_count"] == 0
            assert upgrade_health["policy_class_accounting_valid"] is True
            for counter in (
                "plans_awaiting_approval",
                "plans_requiring_approval",
                "pending_plan_approvals",
                "pending_elevated_acknowledgements",
                "pending_challenge_count",
            ):
                assert upgrade_health[counter] == upgrade_health_baseline[counter]
            for historical_plan_id, historical_path, persisted_before in (
                historical_records
            ):
                assert recovered.list_execution_tasks(
                    plan_id=historical_plan_id
                )["count"] == 0
                assert historical_path.read_bytes() == persisted_before
            assert len(observed.mutations) == observed_mutation_baseline
            return {
                "completed_scenarios": [
                    "standard_admin",
                    "elevated_admin",
                    "prohibited",
                    "prohibited_non_entity_target",
                    "persisted_beta6_prohibited_upgrade",
                    "persisted_beta6_legacy_expired_upgrade",
                ],
                "standard_task_id": standard_applied["task_id"],
                "elevated_task_id": elevated_applied["task_id"],
                "configuration_mutation_count": len(observed.mutations),
                "fallback_count": 0,
                "physical_actuation_observed": False,
            }
        except Exception as exc:
            # Capture persisted failure evidence before TemporaryDirectory
            # removes the plan and task repositories. Diagnostics are
            # best-effort and must never replace the primary exception.
            try:
                persisted_plan = (
                    service.repository.get(active_plan_id)
                    if active_plan_id is not None
                    else None
                )
                task = None
                if active_plan_id is not None:
                    task_list = service.list_execution_tasks(
                        plan_id=active_plan_id
                    )
                    if task_list["count"] == 1:
                        task = service.get_execution_task(
                            task_list["tasks"][0]["task_id"]
                        )
                setattr(
                    exc,
                    "contract_diagnostic",
                    _bounded_f2_failure_evidence(
                        scenario=scenario,
                        operation_id=active_operation_id,
                        plan=persisted_plan,
                        task=task,
                        observed_mutation_baseline=(
                            observed_mutation_baseline
                        ),
                        observed_mutation_count=len(observed.mutations),
                        error=exc,
                    ),
                )
            except Exception:
                # Preserve the original contract failure even if diagnostic
                # materialization itself cannot complete.
                pass
            if scenario in {
                "setup",
                "standard_admin",
                "elevated_admin",
                "prohibited",
            }:
                setattr(exc, "contract_scenario", scenario)
            if isinstance(exc, KeyError) and exc.args:
                missing_key = exc.args[0]
                if (
                    isinstance(missing_key, str)
                    and 0 < len(missing_key) <= 64
                    and all(
                        character.isalnum() or character in "_-"
                        for character in missing_key
                    )
                ):
                    setattr(exc, "contract_missing_key", missing_key)
            raise
        finally:
            end_request(context)


async def _run_direct_update_contract(
    gateway: ConfigurationResourceGateway,
) -> None:
    """Update and reread all four governed resource adapters."""

    for resource_type in RESOURCE_ORDER:
        result = await gateway.update(
            resource_type,
            RESOURCE_IDS[resource_type],
            copy.deepcopy(UPDATE_CONFIGS[resource_type]),
        )
        if resource_type in {"automation", "script"}:
            assert result == {"result": "ok"}
        else:
            _assert_exact_resource(
                resource_type,
                RESOURCE_IDS[resource_type],
                UPDATE_CONFIGS[resource_type],
                result,
            )
        readback = await gateway.read(
            resource_type, RESOURCE_IDS[resource_type]
        )
        _assert_exact_resource(
            resource_type,
            RESOURCE_IDS[resource_type],
            UPDATE_CONFIGS[resource_type],
            readback,
        )


async def _assert_http_configuration_contract() -> None:
    """Prove the 2026.8 YAML-to-storage assumption without changing ports."""

    projection_code = (
        "import json,pathlib;"
        "root=json.loads(pathlib.Path('/config/.storage/http').read_text());"
        "data=root.get('data') if isinstance(root.get('data'),dict) else {};"
        "stable=data.get('stable') if isinstance(data.get('stable'),dict) else {};"
        "print(json.dumps({"
        "'version':root.get('version'),"
        "'server_port':data.get('server_port'),"
        "'has_yaml_migration_done':'yaml_migration_done' in data,"
        "'yaml_migration_done':data.get('yaml_migration_done'),"
        "'has_pending':'pending' in data,"
        "'pending':data.get('pending'),"
        "'has_stable':'stable' in data,"
        "'stable_server_port':stable.get('server_port'),"
        "'stable_error':stable.get('error')"
        "},sort_keys=True,separators=(',',':')))"
    )
    stored = json.loads(
        await _docker_command(
            "exec",
            HA_CONTRACT_CONTAINER,
            "python",
            "-c",
            projection_code,
        )
    )
    if EXPECTED_HA_VERSION == "2026.7.2":
        assert stored.get("version") == 1
        assert stored.get("server_port") == 8123
        assert stored.get("has_yaml_migration_done") is False
        assert stored.get("has_pending") is False
        assert stored.get("has_stable") is False
        return
    assert EXPECTED_HA_VERSION in {"2026.8.0", "2026.8.1"}
    assert stored.get("version") == 2
    assert stored.get("has_yaml_migration_done") is True
    assert stored.get("yaml_migration_done") is True
    assert stored.get("has_pending") is True
    assert stored.get("pending") is None
    assert stored.get("has_stable") is True
    assert stored.get("stable_server_port") == 8123
    assert stored.get("stable_error") is None


def _decode_tool_result(result: Any) -> dict[str, Any]:
    """Decode one bounded exact-upstream MCP tool response."""

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" not in structured:
        return structured
    for item in getattr(result, "content", ()):
        text = getattr(item, "text", None)
        if not isinstance(text, str) or len(text.encode("utf-8")) > 100_000:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("Exact upstream ha_get_device returned no bounded object")


async def _docker_command(*arguments: str, allow_failure: bool = False) -> str:
    """Run one bounded Docker command without exposing arguments or stderr."""

    process = await asyncio.create_subprocess_exec(
        "docker",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("A bounded disposable Docker operation timed out")
    if process.returncode != 0 and not allow_failure:
        raise RuntimeError("A bounded disposable Docker operation failed")
    return stdout.decode("utf-8", errors="replace")[:256]


async def _start_exact_upstream(token: str) -> None:
    """Start the exact reviewed ha-mcp release against disposable HA only."""

    if not UPSTREAM_IMAGE:
        raise RuntimeError("The exact upstream image identity is required")
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="beta23-real-ha-upstream-",
        suffix=".env",
        delete=False,
    ) as handle:
        environment_path = Path(handle.name)
        handle.write(f"HOMEASSISTANT_URL={HA_URL}\n")
        handle.write(f"HOMEASSISTANT_TOKEN={token}\n")
        handle.write("MCP_HOST=127.0.0.1\n")
        handle.write(f"MCP_PORT={UPSTREAM_PORT}\n")
        handle.write(f"MCP_SECRET_PATH={UPSTREAM_SECRET_PATH}\n")
        handle.write("HA_MCP_DISABLE_SETTINGS_UI=true\n")
    environment_path.chmod(0o600)
    try:
        await _docker_command(
            "run",
            "--detach",
            "--network",
            "host",
            "--name",
            UPSTREAM_CONTAINER,
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home/mcpuser/.ha-mcp",
            "--env-file",
            str(environment_path),
            UPSTREAM_IMAGE,
            "ha-mcp-web",
        )
    finally:
        environment_path.unlink(missing_ok=True)


async def _remove_exact_upstream() -> None:
    """Remove only the named disposable exact-upstream container."""

    await _docker_command("rm", "-f", UPSTREAM_CONTAINER, allow_failure=True)


async def _call_exact_upstream_get_device(device_id: str) -> dict[str, Any]:
    """Call public ha_get_device by the pre-migration composite ID."""

    endpoint = f"http://127.0.0.1:{UPSTREAM_PORT}{UPSTREAM_SECRET_PATH}"
    for _ in range(60):
        try:
            async with streamablehttp_client(endpoint) as (
                read_stream,
                write_stream,
                _session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "ha-mcp"
                    assert initialized.serverInfo.version == UPSTREAM_VERSION
                    return _decode_tool_result(
                        await session.call_tool(
                            "ha_get_device",
                            {"device_id": device_id},
                        )
                    )
        except Exception:
            await asyncio.sleep(1)
    raise RuntimeError("Exact upstream ha_get_device did not become available")


def _contains_exact_value(value: Any, key: str, expected: str) -> bool:
    """Return whether a nested configuration contains one exact key/value."""

    if isinstance(value, dict):
        return value.get(key) == expected or any(
            _contains_exact_value(item, key, expected) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_exact_value(item, key, expected) for item in value)
    return False


_DEVICE_CONTRACT_SCENARIOS = frozenset(
    {
        "component_lookup",
        "dependency_index",
        "direct_device_target",
        "impact_analysis",
        "persisted_references",
        "registry_shape",
        "split_projection",
        "upstream_device_identity",
        "upstream_device_shape",
        "upstream_entity_count",
        "upstream_entity_identity",
        "upstream_query_mode",
        "upstream_response_adapter",
        "upstream_success",
    }
)


def _assert_device_contract(condition: bool, scenario: str) -> None:
    """Raise one bounded, stage-attributed device-contract assertion."""

    if scenario not in _DEVICE_CONTRACT_SCENARIOS:
        raise ValueError("Unknown device contract scenario")
    if condition:
        return
    error = AssertionError()
    setattr(error, "contract_scenario", scenario)
    raise error


def _bounded_device_lookup_shape(payload: object) -> dict[str, object]:
    """Project only reviewed structural fields from one disposable lookup.

    The real-HA job needs enough evidence to review an upstream response-contract
    transition without ever printing a raw provider body.  Values are limited to
    the synthetic registry identifiers created by this disposable fixture plus
    field names, counts, and booleans.  Names, connections, identifiers, options,
    attributes, and arbitrary upstream values are deliberately excluded.
    """

    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}

    def bounded_token(value: object) -> str | None:
        return _bounded_diagnostic_token(
            value,
            maximum=128,
            punctuation="_-/.",
        )

    def entity_shape(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {"row_type": type(value).__name__}
        return {
            "fields": sorted(
                key
                for key in value
                if bounded_token(key) is not None
            )[:32],
            "entity_id": bounded_token(value.get("entity_id")),
            "device_id": bounded_token(value.get("device_id")),
            "config_entry_id": bounded_token(
                value.get("config_entry_id")
            ),
            "platform": bounded_token(value.get("platform")),
        }

    entities = payload.get("entities")
    projected_entities = (
        [entity_shape(item) for item in entities[:4]]
        if isinstance(entities, list)
        else []
    )
    device = payload.get("device")
    if isinstance(device, dict):
        device_entities = device.get("entities")
        config_entries = device.get("config_entries")
        projected_device: dict[str, object] = {
            "fields": sorted(
                key
                for key in device
                if bounded_token(key) is not None
            )[:48],
            "device_id": bounded_token(device.get("device_id")),
            "config_entries": [
                token
                for item in (
                    config_entries[:8]
                    if isinstance(config_entries, list)
                    else []
                )
                if (token := bounded_token(item)) is not None
            ],
            "entity_count": (
                len(device_entities)
                if isinstance(device_entities, list)
                else None
            ),
            "entities": (
                [entity_shape(item) for item in device_entities[:4]]
                if isinstance(device_entities, list)
                else []
            ),
        }
    else:
        projected_device = {"payload_type": type(device).__name__}

    return {
        "fields": sorted(
            key for key in payload if bounded_token(key) is not None
        )[:32],
        "success": payload.get("success")
        if isinstance(payload.get("success"), bool)
        else None,
        "queried_by": bounded_token(payload.get("queried_by")),
        "entity_count": payload.get("entity_count")
        if isinstance(payload.get("entity_count"), int)
        else None,
        "entities_length": len(entities)
        if isinstance(entities, list)
        else None,
        "entities": projected_entities,
        "device": projected_device,
    }


def _expected_device_response_adapter(
    *,
    home_assistant_version: str,
) -> str | None:
    """Return the adapter reviewed for the exact Home Assistant release."""

    if home_assistant_version == "2026.7.2":
        return None
    try:
        return HA_DEVICE_ADAPTER_IDS_BY_HA_VERSION[home_assistant_version]
    except KeyError:
        raise ValueError(
            "Unsupported Home Assistant contract version"
        ) from None


async def _run_device_migration_contract(
    rest: HomeAssistantRestClient,
    websocket: HomeAssistantWebSocketClient,
    token: str,
) -> None:
    """Exercise registry, upstream lookup, dependency, and impact compatibility."""

    fixture = _load_device_fixture()
    old_device_id = str(fixture["old_composite_device_id"])
    entity_ids = [str(item) for item in fixture["entity_ids"]]
    config_entry_ids = {str(item) for item in fixture["config_entry_ids"]}
    primary_config_entry_id = fixture.get("primary_config_entry_id")
    _assert_device_contract(
        primary_config_entry_id is None
        or isinstance(primary_config_entry_id, str),
        "registry_shape",
    )
    _assert_device_contract(len(entity_ids) == 2, "registry_shape")
    _assert_device_contract(len(config_entry_ids) == 2, "registry_shape")

    devices = await websocket.command({"type": "config/device_registry/list"})
    entities = await websocket.command({"type": "config/entity_registry/list"})
    fixture_entities = [
        item
        for item in entities
        if isinstance(item, dict) and item.get("entity_id") in entity_ids
    ]
    _assert_device_contract(len(fixture_entities) == 2, "registry_shape")
    _assert_device_contract(
        {
            str(item.get("config_entry_id")) for item in fixture_entities
        }
        == config_entry_ids,
        "registry_shape",
    )

    if EXPECTED_HA_VERSION == "2026.7.2":
        expected_device_ids = {old_device_id}
        composite = next(
            item
            for item in devices
            if isinstance(item, dict) and item.get("id") == old_device_id
        )
        _assert_device_contract(
            set(composite.get("config_entries", ())) == config_entry_ids,
            "registry_shape",
        )
        _assert_device_contract(
            composite.get("primary_config_entry") == primary_config_entry_id,
            "registry_shape",
        )
    else:
        _assert_device_contract(
            EXPECTED_HA_VERSION in {"2026.8.0", "2026.8.1"},
            "split_projection",
        )
        _assert_device_contract(
            not any(
                isinstance(item, dict) and item.get("id") == old_device_id
                for item in devices
            ),
            "split_projection",
        )
        composite_splits = await websocket.command(
            {"type": "config/device_registry/list_composite_splits"}
        )
        split_contract = composite_splits.get(old_device_id)
        _assert_device_contract(
            isinstance(split_contract, dict), "split_projection"
        )
        expected_device_ids = {
            str(item) for item in split_contract.get("split_ids", ())
        }
        _assert_device_contract(
            len(expected_device_ids) == 2, "split_projection"
        )
        splits = [
            item
            for item in devices
            if isinstance(item, dict)
            and item.get("id") in expected_device_ids
        ]
        _assert_device_contract(len(splits) == 2, "split_projection")
        _assert_device_contract(
            {str(item.get("config_entry_id")) for item in splits}
            == config_entry_ids,
            "split_projection",
        )
        _assert_device_contract(
            all(
                set(item.get("config_entries", ()))
                == {item.get("config_entry_id")}
                for item in splits
            ),
            "split_projection",
        )
        _assert_device_contract(
            {str(item["id"]) for item in splits} == expected_device_ids,
            "split_projection",
        )
        expected_primary_id = next(
            (
                item["id"]
                for item in splits
                if item.get("config_entry_id") == primary_config_entry_id
            ),
            None,
        )
        _assert_device_contract(
            split_contract.get("primary_id") == expected_primary_id,
            "split_projection",
        )
    _assert_device_contract(
        {str(item.get("device_id")) for item in fixture_entities}
        == expected_device_ids,
        "split_projection",
    )

    component_lookup = await websocket.command(
        {
            "type": "ha_mcp_tools/device_get",
            "device_id": old_device_id,
            "include_entities": True,
        }
    )
    component_device = component_lookup.get("device")
    _assert_device_contract(
        isinstance(component_device, dict), "component_lookup"
    )
    _assert_device_contract(
        component_device.get("id") == old_device_id, "component_lookup"
    )
    _assert_device_contract(
        {
            str(item.get("entity_id"))
            for item in component_lookup.get("entities", ())
            if isinstance(item, dict)
        }
        == set(entity_ids),
        "component_lookup",
    )

    automation = await rest.request(
        "GET", f"/config/automation/config/{MIGRATION_AUTOMATION_ID}"
    )
    _assert_device_contract(
        _contains_exact_value(automation, "device_id", old_device_id),
        "persisted_references",
    )
    _assert_device_contract(
        _contains_exact_value(automation, "entity_id", entity_ids[0]),
        "persisted_references",
    )
    await websocket.command(
        {
            "type": "call_service",
            "domain": "switch",
            "service": "turn_on",
            "service_data": {},
            "target": {"device_id": old_device_id},
        }
    )
    for entity_id in entity_ids:
        state = await rest.request("GET", f"/states/{entity_id}")
        _assert_device_contract(
            state.get("state") == "on", "direct_device_target"
        )
    await websocket.command(
        {
            "type": "call_service",
            "domain": "switch",
            "service": "turn_off",
            "service_data": {},
            "target": {"device_id": old_device_id},
        }
    )
    for entity_id in entity_ids:
        state = await rest.request("GET", f"/states/{entity_id}")
        _assert_device_contract(
            state.get("state") == "off", "direct_device_target"
        )

    await _start_exact_upstream(token)
    raw_upstream_lookup = await _call_exact_upstream_get_device(old_device_id)
    upstream_lookup, response_adapter = (
        await adapt_ha_get_device_composite_result(
            raw_upstream_lookup,
            arguments={"device_id": old_device_id},
            upstream_version=UPSTREAM_VERSION,
            rest_client=rest,
            websocket_client=websocket,
        )
    )
    print(
        "Composite device contract evidence: "
        + json.dumps(
            {
                "model": "composite-device-response-contract-v1",
                "home_assistant_version": EXPECTED_HA_VERSION,
                "upstream_version": UPSTREAM_VERSION,
                "requested_device_id": old_device_id,
                "expected_entity_ids": entity_ids,
                "expected_split_device_ids": sorted(expected_device_ids),
                "expected_entity_count": len(entity_ids),
                "response_adapter": response_adapter,
                "raw_lookup": _bounded_device_lookup_shape(
                    raw_upstream_lookup
                ),
                "adapted_lookup": _bounded_device_lookup_shape(
                    upstream_lookup
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    expected_adapter = _expected_device_response_adapter(
        home_assistant_version=EXPECTED_HA_VERSION,
    )
    _assert_device_contract(
        response_adapter == expected_adapter, "upstream_response_adapter"
    )
    _assert_device_contract(
        upstream_lookup.get("success") is True, "upstream_success"
    )
    _assert_device_contract(
        upstream_lookup.get("queried_by") == "device_id",
        "upstream_query_mode",
    )
    upstream_device = upstream_lookup.get("device")
    _assert_device_contract(
        isinstance(upstream_device, dict), "upstream_device_shape"
    )
    _assert_device_contract(
        upstream_device.get("device_id") == old_device_id,
        "upstream_device_identity",
    )
    _assert_device_contract(
        upstream_lookup.get("entity_count") == 2,
        "upstream_entity_count",
    )
    _assert_device_contract(
        {
            str(item.get("entity_id"))
            for item in upstream_lookup.get("entities", ())
            if isinstance(item, dict)
        }
        == set(entity_ids),
        "upstream_entity_identity",
    )

    index = DependencyIndex(
        DirectHaDependencyProvider(rest, websocket),
        soft_ttl_seconds=60,
        hard_ttl_seconds=120,
    )
    dependency = await EntityDependencyAnalysisService(index).analyze(
        entity_id=entity_ids[0],
        detail_level="evidence",
        source_types=["automation"],
        refresh_index=True,
    )
    _assert_device_contract(
        dependency.data["target"]["device_id"] in expected_device_ids,
        "dependency_index",
    )
    _assert_device_contract(
        dependency.data["overview"]["dependency_status"] == "referenced",
        "dependency_index",
    )
    _assert_device_contract(
        any(
            item.get("source_id") == MIGRATION_AUTOMATION_ID
            for item in dependency.data["findings"]
        ),
        "dependency_index",
    )

    impact = await ChangeImpactAnalysisService(
        DirectHaImpactProvider(
            index,
            rest,
            websocket,
            ha_token=token,
        )
    ).analyze(
        entity_id=entity_ids[0],
        operation="disable_entity",
        include_indirect=False,
        source_types=["automation"],
        detail_level="evidence",
    )
    _assert_device_contract(
        impact.data["target_entity_summary"]["device_id"]
        in expected_device_ids,
        "impact_analysis",
    )
    rules = {item["rule_id"] for item in impact.data["findings"]}
    _assert_device_contract(
        "direct_automation_reference" in rules, "impact_analysis"
    )
    _assert_device_contract(
        "device_registry_relationship" in rules, "impact_analysis"
    )
    _assert_device_contract(
        "disable_runtime_availability_risk" in rules, "impact_analysis"
    )


async def _cleanup_configuration_resources(
    gateway: ConfigurationResourceGateway,
) -> None:
    """Delete only fixed disposable fixtures and prove their absence."""

    for resource_type in reversed(RESOURCE_ORDER):
        resource_id = RESOURCE_IDS[resource_type]
        if await gateway.read(resource_type, resource_id) is None:
            continue
        if resource_type in {"input_boolean", "input_number"}:
            object_id = resource_id.split(".", 1)[1]
            await gateway.websocket_client.command(
                {
                    "type": f"{resource_type}/delete",
                    f"{resource_type}_id": object_id,
                }
            )
        else:
            await gateway.rest_client.request(
                "DELETE",
                f"/config/{resource_type}/config/{resource_id}",
            )
        assert await gateway.read(resource_type, resource_id) is None


async def run_contracts() -> None:
    phase = "bootstrap"
    gateway = None
    failure = None
    cleanup_attempted = False
    cleanup_succeeded: bool | None = None
    cleanup_failure: BaseException | None = None
    try:
        token = (
            _load_prepared_token()
            if TOKEN_PATH is not None
            else await bootstrap_disposable_admin()
        )
        configured = settings(token)
        rest = HomeAssistantRestClient(configured)
        websocket = HomeAssistantWebSocketClient(configured)
        gateway = ConfigurationResourceGateway(rest, websocket)
        legacy_automation_gateway = AutomationGateway(rest)
        phase = "runtime_readiness"
        await wait_for_runtime_ready(rest)

        phase = "http_configuration_migration"
        await _assert_http_configuration_contract()

        phase = "device_registry_migration_and_analysis"
        await _run_device_migration_contract(rest, websocket, token)

        phase = "fresh_resource_preflight"
        for resource_type in RESOURCE_ORDER:
            assert (
                await gateway.read(
                    resource_type, RESOURCE_IDS[resource_type]
                )
                is None
            )

        phase = "governed_configuration_plan"
        await _run_governed_configuration_contract(gateway, token)

        phase = "legacy_automation_compatibility"
        await _run_legacy_automation_compatibility_contract(
            legacy_automation_gateway
        )

        phase = "f2_policy_acceptance"
        await _run_f2_policy_acceptance_contract(gateway, token)

        phase = "direct_resource_updates"
        await _run_direct_update_contract(gateway)

        phase = "strict_configuration_validation"
        _assert_strict_configuration_check(await gateway.validate_all())

        phase = "rest_and_websocket_inventory"
        runtime_config = await rest.request("GET", "/config")
        assert isinstance(runtime_config, dict)
        assert runtime_config.get("version") == EXPECTED_HA_VERSION
        states = await rest.request("GET", "/states")
        assert isinstance(states, list)
        websocket_states = await websocket.command({"type": "get_states"})
        entity_registry = await websocket.command({"type": "config/entity_registry/list"})
        area_registry = await websocket.command({"type": "config/area_registry/list"})
        services = await websocket.command({"type": "get_services"})
        system_log = await websocket.command({"type": "system_log/list"})
        assert isinstance(entity_registry, list)
        assert isinstance(area_registry, list)
        assert isinstance(websocket_states, list)
        assert isinstance(services, dict)
        assert isinstance(system_log, list)
        assert services
        assert all(
            isinstance(item, dict) and "entity_id" in item and "state" in item
            for item in states
        )
        assert all(
            isinstance(item, dict) and "entity_id" in item and "state" in item
            for item in websocket_states
        )
        assert all(
            isinstance(item, dict) and "area_id" in item and "name" in item
            for item in area_registry
        )
        assert all(
            isinstance(item, dict) and "entity_id" in item and "platform" in item
            for item in entity_registry
        )
        assert all(
            isinstance(item, dict)
            and {"name", "message", "level", "timestamp"}.issubset(item)
            for item in system_log
        )

        # The trace contract is required, not silently skipped. Reloading and
        # firing this isolated event occur only in the disposable container.
        phase = "trace_generation"
        await rest.request("POST", "/services/automation/reload", {})
        await rest.request(
            "POST",
            "/events/dev14_real_contract_trigger",
            {"source": "disposable_contract"},
        )
        normalized = None
        for _ in range(30):
            await asyncio.sleep(1)
            normalized = await fetch_normalized_trace_list(
                websocket.command,
                RESOURCE_IDS["automation"],
                known_secrets=(token,),
            )
            if normalized.headers:
                break
        assert normalized is not None and normalized.headers
        trace = normalized.headers[0]
        run_id = trace.run_id
        assert run_id
        assert trace.started_at
        detail = await websocket.command(
            {
                "type": "trace/get",
                "domain": "automation",
                "item_id": RESOURCE_IDS["automation"],
                "run_id": run_id,
            }
        )
        assert isinstance(detail, dict)
        assert isinstance(detail.get("trace"), dict)
        assert isinstance(detail.get("config"), dict)
    except Exception as exc:
        failure = exc
    finally:
        try:
            await _remove_exact_upstream()
        except Exception as upstream_cleanup_error:
            if failure is None:
                phase = "exact_upstream_cleanup"
                failure = upstream_cleanup_error
        if gateway is not None:
            cleanup_attempted = True
            try:
                await _cleanup_configuration_resources(gateway)
                cleanup_succeeded = True
            except Exception as cleanup_error:
                cleanup_succeeded = False
                cleanup_failure = cleanup_error
                if failure is None:
                    phase = "configuration_fixture_cleanup"
                    failure = cleanup_error
    if failure is not None:
        _attach_cleanup_evidence(
            failure,
            attempted=cleanup_attempted,
            succeeded=cleanup_succeeded,
            failure=cleanup_failure,
        )
        setattr(failure, "contract_phase", phase)
        raise failure


def _parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run disposable Home Assistant compatibility contracts."
    )
    parser.add_argument(
        "--prepare-migration-fixture",
        action="store_true",
        help="Persist the fixture through exact Home Assistant 2026.7.2.",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    parsed = _parse_args([] if arguments is None else arguments)
    try:
        if parsed.prepare_migration_fixture:
            asyncio.run(prepare_migration_fixture())
        else:
            asyncio.run(run_contracts())
    except Exception as exc:
        # Client exceptions and these selected fields are intentionally safe;
        # never print response bodies, paths, tokens, or onboarding values.
        details = getattr(exc, "details", {})
        safe_details = {
            key: details.get(key)
            for key in ("status", "method", "endpoint_category")
            if details.get(key) is not None
        }
        error_code = getattr(getattr(exc, "code", None), "value", None)
        scenario = getattr(exc, "contract_scenario", "unknown")
        if scenario not in {
            "setup",
            "standard_admin",
            "elevated_admin",
            "prohibited",
            *_DEVICE_CONTRACT_SCENARIOS,
        }:
            scenario = "unknown"
        missing_key = getattr(exc, "contract_missing_key", None)
        if (
            not isinstance(missing_key, str)
            or not (0 < len(missing_key) <= 64)
            or any(
                not (character.isalnum() or character in "_-")
                for character in missing_key
            )
        ):
            missing_key = None
        diagnostic = getattr(exc, "contract_diagnostic", None)
        diagnostic_json = (
            json.dumps(
                diagnostic,
                sort_keys=True,
                separators=(",", ":"),
            )
            if isinstance(diagnostic, dict)
            else "null"
        )
        print(
            "Real Home Assistant contract failure: "
            f"phase={getattr(exc, 'contract_phase', 'unknown')} "
            f"scenario={scenario} "
            f"type={type(exc).__name__} code={error_code or 'unclassified'} "
            f"missing_key={missing_key or 'none'} details={safe_details} "
            f"diagnostic={diagnostic_json}",
            file=sys.stderr,
        )
        return 1
    if parsed.prepare_migration_fixture:
        print("Exact Home Assistant 2026.7.2 migration fixture prepared.")
    else:
        print(
            f"Real Home Assistant {EXPECTED_HA_VERSION} contract assertions passed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
