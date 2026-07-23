"""Blocking contracts against one disposable Home Assistant Core instance.

This script is CI-only. It bootstraps a temporary administrator in the
throwaway container, never prints its credentials, and exercises the project
clients rather than the deployed Home Assistant environment.
"""

from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
import secrets
import sys
import tempfile

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients import (  # noqa: E402
    HomeAssistantRestClient,
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
    normalize_resource_config,
    resource_fingerprint,
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


HA_URL = os.environ.get("REAL_HA_URL", "http://127.0.0.1:8123").rstrip("/")
CLIENT_ID = f"{HA_URL}/"
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


def _assert_exact_resource(
    resource_type: str,
    resource_id: str,
    desired: dict,
    actual: dict | None,
) -> str:
    """Require exact identity and normalized desired/readback equality."""

    assert resource_identity_matches(resource_type, resource_id, actual)
    assert normalize_resource_config(
        resource_type, actual
    ) == normalize_resource_config(resource_type, desired)
    desired_fingerprint = resource_fingerprint(resource_type, desired)
    assert resource_fingerprint(resource_type, actual) == desired_fingerprint
    return desired_fingerprint


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
                item["semantic_projection"]["status"] == "complete"
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
    try:
        token = await bootstrap_disposable_admin()
        configured = settings(token)
        rest = HomeAssistantRestClient(configured)
        websocket = HomeAssistantWebSocketClient(configured)
        gateway = ConfigurationResourceGateway(rest, websocket)
        legacy_automation_gateway = AutomationGateway(rest)
        phase = "runtime_readiness"
        await wait_for_runtime_ready(rest)

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

        phase = "direct_resource_updates"
        await _run_direct_update_contract(gateway)

        phase = "strict_configuration_validation"
        _assert_strict_configuration_check(await gateway.validate_all())

        phase = "rest_and_websocket_inventory"
        runtime_config = await rest.request("GET", "/config")
        assert isinstance(runtime_config, dict)
        assert runtime_config.get("version") == "2026.7.2"
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
        if gateway is not None:
            try:
                await _cleanup_configuration_resources(gateway)
            except Exception as cleanup_error:
                if failure is None:
                    phase = "configuration_fixture_cleanup"
                    failure = cleanup_error
    if failure is not None:
        setattr(failure, "contract_phase", phase)
        raise failure


def main() -> int:
    try:
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
        print(
            "Real Home Assistant contract failure: "
            f"phase={getattr(exc, 'contract_phase', 'unknown')} "
            f"type={type(exc).__name__} code={error_code or 'unclassified'} "
            f"details={safe_details}",
            file=sys.stderr,
        )
        return 1
    print("Real Home Assistant 2026.7.2 contract assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
