"""Beta 2 governed reload/restart lifecycle and exact-provider regressions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadCatalog,
    McpReadResult,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    ENGINEERING_ADDON_SLUG,
    LifecycleGatewayError,
    OperationalLifecycleGateway,
    UPSTREAM_HA_MCP_ADDON_NAME,
    UPSTREAM_HA_MCP_ADDON_SLUG,
    _addon_target_class,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    registered_tools,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **values) -> None:
        self.value += timedelta(**values)


class LegacyGateway:
    async def get(self, _automation_id):
        return None

    async def write(self, *_args):
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}

    async def read(self, _resource_type, _resource_id):
        return None

    async def validate_all(self):
        return {"result": "valid", "errors": None}


def provider_evidence(operation: str) -> dict:
    constraints = {
        "controlled_reload": {
            "target_allowlist": [
                "automation",
                "input_boolean",
                "input_number",
                "script",
            ],
            "entry_id_allowed": False,
            "reload_all_allowed": False,
            "arbitrary_arguments_allowed": False,
        },
        "restart_addon": {
            "action": "restart",
            "slug": "exact_planned_value",
            "other_actions_allowed": False,
            "configuration_mutation_allowed": False,
            "proxy_allowed": False,
            "arbitrary_arguments_allowed": False,
        },
        "restart_home_assistant": {
            "confirm": True,
            "variants_allowed": False,
            "arbitrary_arguments_allowed": False,
        },
    }[operation]
    return {
        "provider": "upstream_operational_lifecycle",
        "server_name": "ha-mcp",
        "server_version": "7.14.2",
        "protocol_version": "2025-03-26",
        "compatibility_entry_id": "ha-mcp-v7.14.2-7917b2d3",
        "reviewed_source_commit": (
            "904c14ebbe76de700f7c3535f5cc71c017dca12e"
        ),
        "reviewed_image_index_digest": "sha256:" + "7" * 64,
        "catalog_fingerprint": (
            "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c"
        ),
        "tool_contract_fingerprints": {"fixture": "f" * 64},
        "argument_constraints": constraints,
        "runtime_artifact_observed": False,
        "fallback": "none",
        "fallback_occurred": False,
    }


class FakeLifecycleGateway:
    def __init__(self) -> None:
        self.dispatch_count = 0
        self.planning_count = 0
        self.verification_count = 0
        self.validation_status = "valid"
        self.verification_status = "verified"
        self.mode = "success"
        self.addon = {
            "slug": "local_test_addon",
            "name": "Test add-on",
            "version": "1.2.3",
            "state": "started",
        }
        self.process_instance_id = "process-one"

    async def planning_evidence(self, operation, target):
        self.planning_count += 1
        if self.mode == "unavailable":
            raise LifecycleGatewayError("provider_unavailable")
        if self.mode == "permission_failure":
            raise LifecycleGatewayError("permission_failure")
        baseline = {}
        if operation == "controlled_reload":
            if self.mode == "service_unavailable":
                raise LifecycleGatewayError("service_unavailable")
            baseline = {
                "configuration_validation": {
                    "status": self.validation_status,
                    "checked_at": "2026-07-27T12:00:00+00:00",
                },
                "service_available": self.mode != "service_unavailable",
                "service": f"{target}.reload",
                "domain_evidence": {
                    "domain": target,
                    "state_inventory_readable": True,
                    "matching_entity_count": 1,
                },
            }
        elif operation == "restart_addon":
            if target == "missing_addon":
                raise LifecycleGatewayError("resource_not_found")
            addon = deepcopy(self.addon)
            addon["slug"] = target
            baseline = {
                "addon": addon,
                "target_class": (
                    "engineering_addon"
                    if target == ENGINEERING_ADDON_SLUG
                    else "other_addon"
                ),
                "process_instance_id": self.process_instance_id,
            }
        else:
            baseline = {
                "configuration_validation": {
                    "status": self.validation_status,
                    "checked_at": "2026-07-27T12:00:00+00:00",
                },
                "home_assistant": {
                    "location_name": "Test Home",
                    "version": "2026.7.4",
                    "connected": True,
                },
                "runtime": {
                    "server_version": "2.1.0-beta.2",
                    "build_sha": "a" * 40,
                    "registered_tool_count": 71,
                    "engineering_tool_count": 45,
                    "delegated_tool_count": 26,
                    "governance_storage_status": "healthy",
                    "governance_plan_count": 1,
                    "audit_storage_status": "healthy",
                    "audit_write_failures": 0,
                    "dependency_index_state": "valid",
                    "dependency_prewarm_state": "complete",
                    "upstream_version": "7.14.2",
                    "upstream_protocol": "2025-03-26",
                    "upstream_catalog_fingerprint": (
                        "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c"
                    ),
                    "upstream_admission_status": "admitted_exact",
                    "fallback_count": 0,
                },
                "process_instance_id": self.process_instance_id,
            }
        return {
            "provider": provider_evidence(operation),
            "baseline": baseline,
        }

    async def dispatch_reload(self, _target, *, before_dispatch):
        return await self._dispatch(before_dispatch)

    async def dispatch_addon_restart(self, _slug, *, before_dispatch):
        return await self._dispatch(before_dispatch)

    async def dispatch_home_assistant_restart(self, *, before_dispatch):
        return await self._dispatch(before_dispatch)

    async def _dispatch(self, before_dispatch):
        if self.mode == "pre_dispatch_failure":
            raise LifecycleGatewayError("catalog_mismatch")
        await before_dispatch()
        self.dispatch_count += 1
        if self.mode == "ambiguous":
            raise LifecycleGatewayError(
                "provider_timeout", dispatched=True
            )
        return SimpleNamespace(
            provider_response_received=True,
            response={"success": True},
        )

    async def verify_reload(self, _target):
        return self._verification()

    async def verify_addon_restart(self, _slug, **_kwargs):
        return self._verification()

    async def verify_home_assistant_restart(self, **_kwargs):
        return self._verification()

    def _verification(self):
        self.verification_count += 1
        return {
            "status": self.verification_status,
            "mismatch_fields": (
                [] if self.verification_status == "verified" else ["fixture"]
            ),
            "evidence": {
                "redispatch_performed": False,
                "fallback_occurred": False,
                **(
                    {"expected_disruption_observed": True}
                    if self.mode == "disruption"
                    else {}
                ),
            },
        }

    def health_snapshot(self):
        return {
            "provider": "upstream_operational_lifecycle",
            "configured": True,
            "operational_status": "available",
            "dispatch_counts": {
                "fixture": self.dispatch_count,
            },
            "fallback_count": 0,
            "fallback_policy": "none",
        }


class GovernedOperationalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.lifecycle = FakeLifecycleGateway()
        self.repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            AuditLogger(
                str(self.audit_path), "beta2-test-access-secret"
            ),
            now=self.clock,
            sensitive_values=("beta2-test-access-secret",),
            lifecycle_gateway=self.lifecycle,
        )
        self.telemetry, self.context = begin_request("beta2-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def grant(self, created):
        plan = created["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=(
                "home_assistant_admin_ingress:fixture"
            ),
        )
        return plan

    async def create_for(self, operation, target="automation"):
        if operation == "controlled_reload":
            return await self.service.create_reload_plan(
                reload_target=target
            )
        if operation == "restart_addon":
            return await self.service.create_addon_restart_plan(
                addon_slug=target
            )
        return await self.service.create_home_assistant_restart_plan()

    async def test_all_three_plans_are_proposal_only_and_hash_bound(self):
        for operation, target, risk in (
            ("controlled_reload", "automation", "medium"),
            ("restart_addon", "local_test_addon", "high"),
            ("restart_home_assistant", "core", "high"),
        ):
            with self.subTest(operation=operation):
                created = await self.create_for(operation, target)
                plan = created["plan"]
                self.assertTrue(created["proposal_only"])
                self.assertFalse(
                    created["provider_dispatch_occurred"]
                )
                self.assertEqual(self.lifecycle.dispatch_count, 0)
                self.assertEqual(plan["risk"]["level"], risk)
                self.assertEqual(
                    plan["approval_lifecycle"],
                    "approval_not_requested",
                )
                persisted = self.repository.get(plan["plan_id"])
                self.assertEqual(
                    self.service.plan_hash(persisted),
                    plan["plan_hash"],
                )

    async def test_reload_allowlist_dispatches_once_and_verifies(self):
        for target in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            with self.subTest(target=target):
                created = await self.service.create_reload_plan(
                    reload_target=target
                )
                plan = await self.grant(created)
                result = await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
                self.assertEqual(result["status"], "applied")
                persisted = self.repository.get(plan["plan_id"])
                self.assertFalse(
                    persisted.operational.dispatch[
                        "validation_changed_since_planning"
                    ]
                )
                before = self.lifecycle.dispatch_count
                repeated = await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
                self.assertEqual(repeated["status"], "already_applied")
                self.assertEqual(self.lifecycle.dispatch_count, before)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_reload_plan(
                reload_target="all"
            )
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)

    async def test_apply_time_invalid_configuration_prevents_dispatch(self):
        created = await self.service.create_reload_plan(
            reload_target="automation"
        )
        plan = await self.grant(created)
        self.lifecycle.validation_status = "invalid"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.OPERATIONAL_VALIDATION_FAILED,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.approval.state.value, "approved")
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 0
        )

    async def test_planning_validation_and_provider_failures_dispatch_nothing(self):
        self.lifecycle.validation_status = "invalid"
        for create in (
            self.service.create_reload_plan(
                reload_target="automation"
            ),
            self.service.create_home_assistant_restart_plan(),
        ):
            with self.assertRaises(GovernanceError) as raised:
                await create
            self.assertEqual(
                raised.exception.code,
                ErrorCode.OPERATIONAL_VALIDATION_FAILED,
            )
        self.lifecycle.validation_status = "valid"
        self.lifecycle.mode = "unavailable"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_addon_restart_plan(
                addon_slug="local_test_addon"
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        self.lifecycle.mode = "permission_failure"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_addon_restart_plan(
                addon_slug="local_test_addon"
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.AUTHORIZATION_FAILURE,
        )
        self.lifecycle.mode = "service_unavailable"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_reload_plan(
                reload_target="automation"
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.OPERATIONAL_VALIDATION_FAILED,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_addon_target_drift_and_unknown_slug_fail_before_dispatch(self):
        with self.assertRaises(GovernanceError):
            await self.service.create_addon_restart_plan(
                addon_slug="missing_addon"
            )
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan = await self.grant(created)
        self.lifecycle.addon["version"] = "2.0.0"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_ambiguous_restart_is_reconciled_without_redispatch(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["status"], "verification_pending")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.lifecycle.mode = "success"
        self.lifecycle.verification_status = "verified"
        resumed = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resumed["status"], "applied")
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_restart_disruption_evidence_is_persisted_for_recovery(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.mode = "disruption"
        self.lifecycle.verification_status = "pending"
        pending = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(pending["status"], "verification_pending")
        persisted = self.repository.get(plan["plan_id"])
        self.assertTrue(
            persisted.operational.dispatch[
                "expected_disruption_observed"
            ]
        )
        self.lifecycle.mode = "success"
        self.lifecycle.verification_status = "verified"
        recovered = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(recovered["status"], "applied")
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_addon_and_home_assistant_restart_verify_once(self):
        for operation, target in (
            ("restart_addon", "local_test_addon"),
            ("restart_home_assistant", "core"),
        ):
            with self.subTest(operation=operation):
                created = await self.create_for(operation, target)
                plan = await self.grant(created)
                result = await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
                self.assertEqual(result["status"], "applied")
                persisted = self.repository.get(plan["plan_id"])
                self.assertEqual(
                    persisted.operational.dispatch["attempt_count"], 1
                )
                self.assertEqual(
                    persisted.operational.verification.status,
                    "verified",
                )
                self.assertEqual(
                    result["plan"]["authoritative_verification_field"],
                    "operational.verification",
                )
                self.assertNotIn("verification", result["plan"])
                before = self.lifecycle.dispatch_count
                await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
                self.assertEqual(self.lifecycle.dispatch_count, before)

    async def test_post_dispatch_verification_failure_is_not_redispatchable(self):
        created = await self.service.create_reload_plan(
            reload_target="script"
        )
        plan = await self.grant(created)
        self.lifecycle.verification_status = "failed"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.OPERATIONAL_VERIFICATION_FAILED,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        with self.assertRaises(GovernanceError) as repeated:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            repeated.exception.code,
            ErrorCode.DUPLICATE_APPLY_ATTEMPT,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_startup_reconciliation_resumes_same_plan_readback_only(self):
        created = (
            await self.service.create_home_assistant_restart_plan()
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(self.lifecycle.dispatch_count, 1)

        reloaded_repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        recovered_lifecycle = FakeLifecycleGateway()
        recovered_lifecycle.verification_status = "verified"
        recovered_service = ChangeGovernanceService(
            reloaded_repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=recovered_lifecycle,
        )
        reconciliation = (
            await recovered_service.reconcile_operational_plans()
        )
        self.assertEqual(reconciliation["completed"], 1)
        self.assertEqual(recovered_lifecycle.dispatch_count, 0)
        recovered = reloaded_repository.get(plan["plan_id"])
        self.assertEqual(recovered.status, PlanStatus.APPLIED)
        self.assertEqual(
            recovered.operational.dispatch["attempt_count"], 1
        )

    async def test_pre_dispatch_contract_drift_preserves_approval(self):
        created = await self.service.create_reload_plan(
            reload_target="script"
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "pre_dispatch_failure"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
        )
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.approval.state.value, "approved")
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 0
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_health_and_persisted_audit_are_operation_specific(self):
        created = await self.service.create_reload_plan(
            reload_target="input_boolean"
        )
        plan = await self.grant(created)
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        health = self.service.health_summary()[
            "operational_administration"
        ]
        metrics = health["operations"]["controlled_reload"]
        self.assertEqual(metrics["plans_created"], 1)
        self.assertEqual(metrics["apply_attempts"], 1)
        self.assertEqual(metrics["dispatch_attempts"], 1)
        self.assertEqual(metrics["verified_successes"], 1)
        self.assertEqual(
            metrics["provider_identity"],
            "upstream_operational_lifecycle",
        )
        self.assertEqual(metrics["provider_availability"], "available")
        self.assertEqual(metrics["provider_contract_status"], "exact")
        self.assertEqual(metrics["fallback_count"], 0)
        records = [
            json.loads(line)
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertTrue(
            any(
                record.get("event")
                == "controlled_reload_dispatch_recorded"
                and record.get("provider_dispatch_occurred") is True
                and record.get("fallback_occurred") is False
                for record in records
            )
        )


class FakeMcpTransport:
    def __init__(self, version: str = "7.14.2") -> None:
        capture = json.loads(
            (
                ROOT
                / "docs"
                / "evidence"
                / "upstream-read-compatibility"
                / f"ha-mcp-{version}.json"
            ).read_text(encoding="utf-8")
        )
        self.catalog = McpReadCatalog(
            protocol_version=capture["protocol_version"],
            server_name=capture["server_name"],
            server_version=capture["server_version"],
            tools=tuple(capture["tools"]),
            connection_latency_ms=1.0,
        )
        self.calls = []
        self.raw_result = None

    async def discover(self):
        return self.catalog

    async def execute_read(
        self,
        tool_name,
        arguments,
        *,
        timeout_seconds,
        catalog_validator,
        before_dispatch=None,
    ):
        catalog_validator(self.catalog)
        if before_dispatch is not None:
            value = before_dispatch()
            if hasattr(value, "__await__"):
                await value
        self.calls.append((tool_name, deepcopy(arguments)))
        if tool_name == "ha_reload_core":
            payload = {
                "success": True,
                "target": arguments["target"],
                "service": "automation.reload",
            }
        elif tool_name == "ha_manage_addon":
            payload = {
                "success": True,
                "action": "restart",
                "slug": arguments["slug"],
            }
        elif tool_name == "ha_restart":
            payload = {
                "success": True,
                "message": "Restart initiated.",
            }
        else:
            payload = {
                "success": True,
                "addon": {
                    "slug": arguments["slug"],
                    "name": "Fixture",
                    "version": "1.0.0",
                    "state": "started",
                },
            }
        result = (
            self.raw_result
            if self.raw_result is not None
            else {
                "content": [
                    {"type": "text", "text": json.dumps(payload)}
                ],
                "isError": False,
            }
        )
        return McpReadResult(
            protocol_version=self.catalog.protocol_version,
            server_name=self.catalog.server_name,
            server_version=self.catalog.server_version,
            call_result=result,
            connection_latency_ms=1.0,
            tool_call_latency_ms=1.0,
        )


class ExactOperationalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_addon_requires_independent_restart_evidence(self):
        class AddonProvider:
            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": "Fixture",
                    "version": "1.0.0",
                    "state": "started",
                }

        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        baseline = {
            "addon": {
                "slug": "local_test_addon",
                "name": "Fixture",
                "version": "1.0.0",
                "state": "started",
            },
            "target_class": "other_addon",
        }
        pending = await gateway.verify_addon_restart(
            "local_test_addon",
            baseline=baseline,
            provider_response_received=False,
            provider_evidence={},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn("restart_evidence", pending["mismatch_fields"])
        verified = await gateway.verify_addon_restart(
            "local_test_addon",
            baseline=baseline,
            provider_response_received=True,
            provider_evidence={},
        )
        self.assertEqual(verified["status"], "verified")

    async def test_upstream_addon_requires_exact_readmission(self):
        class AddonProvider:
            observed_version = "7.14.1"

            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": UPSTREAM_HA_MCP_ADDON_NAME,
                    "version": "7.14.2",
                    "state": "started",
                }

            async def probe(self, operation):
                self.operation = operation
                return SimpleNamespace(
                    server_version=self.observed_version
                )

        provider = AddonProvider()
        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        baseline = {
            "addon": {
                "slug": UPSTREAM_HA_MCP_ADDON_SLUG,
                "name": UPSTREAM_HA_MCP_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
            },
            "target_class": "upstream_ha_mcp_addon",
        }
        pending = await gateway.verify_addon_restart(
            UPSTREAM_HA_MCP_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence={"server_version": "7.14.2"},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn("restart_evidence", pending["mismatch_fields"])
        provider.observed_version = "7.14.2"
        verified = await gateway.verify_addon_restart(
            UPSTREAM_HA_MCP_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence={"server_version": "7.14.2"},
        )
        self.assertEqual(verified["status"], "verified")

    async def test_engineering_self_restart_requires_new_process_instance(self):
        class AddonProvider:
            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": "Engineering",
                    "version": "2.1.0-beta.2",
                    "state": "started",
                }

        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="original-process",
        )
        baseline = {
            "addon": {
                "slug": ENGINEERING_ADDON_SLUG,
                "name": "Engineering",
                "version": "2.1.0-beta.2",
                "state": "started",
            },
            "target_class": "engineering_addon",
            "process_instance_id": "original-process",
        }
        pending = await gateway.verify_addon_restart(
            ENGINEERING_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence={},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn("restart_evidence", pending["mismatch_fields"])
        gateway.process_instance_id = "restarted-process"
        verified = await gateway.verify_addon_restart(
            ENGINEERING_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=False,
            provider_evidence={},
        )
        self.assertEqual(verified["status"], "verified")

    async def test_home_assistant_restart_records_observed_disruption(self):
        class Rest:
            def __init__(self):
                self.calls = 0

            async def request(self, _method, _path):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "location_name": "Test Home",
                        "version": "2026.7.4",
                    }
                raise HomeAssistantUnavailableError()

        gateway = OperationalLifecycleGateway(
            SimpleNamespace(),
            Rest(),
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        with patch(
            "ha_mcp_engineering.governance.operational_lifecycle."
            "RESTART_DISRUPTION_PROBE_ATTEMPTS",
            2,
        ), patch(
            "ha_mcp_engineering.governance.operational_lifecycle."
            "RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS",
            0,
        ):
            result = await gateway.verify_home_assistant_restart(
                baseline={},
                restart_dispatch_confirmed=True,
                expected_disruption_observed=False,
            )
        self.assertEqual(result["status"], "pending")
        self.assertTrue(
            result["evidence"]["expected_disruption_observed"]
        )

    async def test_home_assistant_restart_requires_exact_runtime_recovery(self):
        class ProviderEvidence:
            server_version = "7.14.2"

            def as_dict(self):
                return {"server_version": self.server_version}

        class Provider:
            async def probe(self, operation):
                self.operation = operation
                return ProviderEvidence()

        class Rest:
            async def request(self, _method, _path):
                return {
                    "location_name": "Test Home",
                    "version": "2026.7.4",
                }

        runtime = {
            "server_version": "2.1.0-beta.2",
            "build_sha": "a" * 40,
            "registered_tool_count": 71,
            "engineering_tool_count": 45,
            "delegated_tool_count": 26,
            "governance_storage_status": "healthy",
            "governance_plan_count": 1,
            "audit_storage_status": "healthy",
            "audit_write_failures": 0,
            "dependency_index_state": "valid",
            "dependency_prewarm_state": "complete",
            "upstream_version": "7.14.2",
            "upstream_catalog_fingerprint": "catalog-fingerprint",
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        gateway = OperationalLifecycleGateway(
            Provider(),
            Rest(),
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="process",
        )

        async def valid_configuration():
            return {"status": "valid"}

        gateway.configuration_validation = valid_configuration
        baseline = {
            "home_assistant": {
                "location_name": "Test Home",
                "version": "2026.7.4",
            },
            "runtime": deepcopy(runtime),
        }
        verified = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            expected_disruption_observed=True,
        )
        self.assertEqual(verified["status"], "verified")

        runtime["dependency_prewarm_state"] = "building"
        pending = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            expected_disruption_observed=True,
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn(
            "dependency_index_recovery", pending["mismatch_fields"]
        )

        runtime["dependency_prewarm_state"] = "complete"
        runtime["engineering_tool_count"] = 44
        pending = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            expected_disruption_observed=True,
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn("engineering_runtime", pending["mismatch_fields"])

    async def test_only_constructed_reload_restart_arguments_are_dispatched(self):
        provider = ReviewedOperationalLifecycleProvider()
        transport = FakeMcpTransport()
        provider._transport = transport
        dispatched = []

        async def before_dispatch():
            dispatched.append(True)

        await provider.reload(
            "automation", before_dispatch=before_dispatch
        )
        await provider.restart_addon(
            "local_test_addon", before_dispatch=before_dispatch
        )
        await provider.restart_home_assistant(
            before_dispatch=before_dispatch
        )
        self.assertEqual(
            transport.calls,
            [
                ("ha_reload_core", {"target": "automations"}),
                (
                    "ha_manage_addon",
                    {
                        "slug": "local_test_addon",
                        "action": "restart",
                    },
                ),
                ("ha_restart", {"confirm": True}),
            ],
        )
        self.assertEqual(len(dispatched), 3)
        self.assertEqual(provider.health_snapshot()["fallback_count"], 0)

    async def test_unknown_targets_and_catalog_drift_fail_before_dispatch(self):
        provider = ReviewedOperationalLifecycleProvider()
        transport = FakeMcpTransport()
        provider._transport = transport
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.reload("all", before_dispatch=lambda: None)
        self.assertEqual(raised.exception.category, "invalid_request")
        changed = list(transport.catalog.tools)
        changed[0] = {**changed[0], "description": "drift"}
        transport.catalog = McpReadCatalog(
            protocol_version=transport.catalog.protocol_version,
            server_name=transport.catalog.server_name,
            server_version=transport.catalog.server_version,
            tools=tuple(changed),
            connection_latency_ms=1.0,
        )
        with self.assertRaises(OperationalLifecycleProviderError) as raised:
            await provider.restart_home_assistant(
                before_dispatch=lambda: None
            )
        self.assertEqual(raised.exception.category, "catalog_mismatch")
        self.assertEqual(transport.calls, [])

    async def test_untrusted_provider_result_fails_closed_after_one_dispatch(self):
        for text in (
            '{"success":true,"success":false}',
            '{"success":true,"nested":{"value":NaN}}',
            '"not-an-object"',
        ):
            with self.subTest(text=text):
                provider = ReviewedOperationalLifecycleProvider()
                transport = FakeMcpTransport()
                transport.raw_result = {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                }
                provider._transport = transport
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as raised:
                    await provider.reload(
                        "automation",
                        before_dispatch=lambda: None,
                    )
                self.assertTrue(raised.exception.dispatched)
                self.assertEqual(
                    raised.exception.category,
                    "invalid_response",
                )
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(
                    provider.health_snapshot()["fallback_count"], 0
                )

    def test_addon_target_classification_is_exact_not_name_heuristic(self):
        self.assertEqual(
            _addon_target_class(
                ENGINEERING_ADDON_SLUG,
                {"name": "anything"},
            ),
            "engineering_addon",
        )
        self.assertEqual(
            _addon_target_class(
                UPSTREAM_HA_MCP_ADDON_SLUG,
                {"name": UPSTREAM_HA_MCP_ADDON_NAME},
            ),
            "upstream_ha_mcp_addon",
        )
        self.assertEqual(
            _addon_target_class(
                "unrelated",
                {"name": "Looks like an MCP engineering server"},
            ),
            "other_addon",
        )

    def test_public_tool_schemas_are_bounded_and_catalog_is_45(self):
        tools = registered_tools(get_registered_server())
        self.assertEqual(len(tools), 45)
        reload_schema = tools["create_reload_plan"].parameters
        self.assertEqual(
            reload_schema["properties"]["reload_target"]["enum"],
            [
                "automation",
                "script",
                "input_boolean",
                "input_number",
            ],
        )
        addon_schema = tools["create_addon_restart_plan"].parameters
        self.assertEqual(
            set(addon_schema["properties"]),
            {"addon_slug", "expiration_minutes"},
        )
        restart_schema = tools[
            "create_home_assistant_restart_plan"
        ].parameters
        self.assertEqual(
            set(restart_schema["properties"]),
            {"expiration_minutes"},
        )
        for name in (
            "ha_reload_core",
            "ha_manage_addon",
            "ha_restart",
        ):
            self.assertNotIn(name, tools)
