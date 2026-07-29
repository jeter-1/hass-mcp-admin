"""Beta 2 governed reload/restart lifecycle and exact-provider regressions."""

from __future__ import annotations

import asyncio
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
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
)
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadCatalog,
    McpReadResult,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantUnavailableError,
    error_definition,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangePlan,
    PlanStatus,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    ENGINEERING_ADDON_SLUG,
    LifecycleGatewayError,
    OperationalLifecycleGateway,
    RESTART_DISRUPTION_PROBE_ATTEMPTS,
    RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS,
    RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS,
    UPSTREAM_PROVIDER_CONTRACT_FIELDS,
    _addon_target_class,
    _upstream_readmission_matches,
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
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SelfAddonIdentityError,
    SupervisorSelfAddonIdentity,
    SupervisorSelfAddonIdentityResolver,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    registered_tools,
)


UPSTREAM_ADDON_SLUG = "abcdef12_ha_mcp"
UPSTREAM_ADDON_NAME = "Home Assistant MCP Server"


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


def upstream_addon_identity(
    slug: str = UPSTREAM_ADDON_SLUG,
    *,
    status: str = "bound",
) -> dict:
    if status != "bound":
        return {"status": status}
    return {
        "status": "bound",
        "slug": slug,
        "name": UPSTREAM_ADDON_NAME,
        "installed_version": "7.14.2",
        "repository": "abcdef12",
        "endpoint_host": slug.replace("_", "-"),
        "identity_source": (
            "configured_endpoint_supervisor_dns_and_reviewed_admission"
        ),
        "inventory_arguments": {
            "source": "installed",
            "include_stats": False,
        },
        "admission_evidence": provider_evidence("restart_addon"),
        "provider_contract": provider_evidence("restart_addon"),
    }


def lifecycle_settings(endpoint: str) -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-ha-token",
        access_secret="synthetic-engineering-access-secret",
        port=8100,
        audit_path="audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=endpoint,
    )


class FakeLifecycleGateway:
    def __init__(self) -> None:
        self.dispatch_count = 0
        self.planning_count = 0
        self.verification_count = 0
        self.validation_status = "valid"
        self.verification_status = "verified"
        self.home_assistant_verification_results = []
        self.home_assistant_verification_calls = []
        self.now = lambda: datetime(
            2026, 7, 27, 12, 0, tzinfo=timezone.utc
        )
        self.mode = "success"
        self.addon = {
            "slug": "local_test_addon",
            "name": "Test add-on",
            "version": "1.2.3",
            "state": "started",
        }
        self.process_instance_id = "process-one"
        self.runtime = {
            "server_version": "2.2.0-beta.1",
            "build_sha": "a" * 40,
            "registered_tool_count": 74,
            "engineering_tool_count": 48,
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
        }

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
                raise LifecycleGatewayError("addon_not_found")
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
            if baseline["target_class"] == "engineering_addon":
                baseline["runtime"] = deepcopy(self.runtime)
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
                    "server_version": "2.2.0-beta.1",
                    "build_sha": "a" * 40,
                    "registered_tool_count": 74,
                    "engineering_tool_count": 48,
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
        result = self._verification()
        if result["status"] == "verified":
            target_class = (
                _kwargs.get("baseline", {}).get("target_class")
            )
            result["evidence"]["restart_proof"] = (
                "process_identity"
                if target_class == "engineering_addon"
                else "provider_acknowledgement"
            )
        return result

    async def verify_home_assistant_restart(self, **_kwargs):
        self.home_assistant_verification_calls.append(deepcopy(_kwargs))
        if self.home_assistant_verification_results:
            self.verification_count += 1
            result = deepcopy(
                self.home_assistant_verification_results.pop(0)
            )
            evidence = result.get("evidence")
            if (
                isinstance(evidence, dict)
                and evidence.get("outage_observed") is True
                and "outage_observed_at" not in evidence
            ):
                evidence["outage_observed_at"] = self.now().isoformat()
            if (
                isinstance(evidence, dict)
                and evidence.pop("_successful_identity_read", False)
            ):
                advance = getattr(self.now, "advance", None)
                if callable(advance):
                    advance(microseconds=1)
                evidence["reconnected_at"] = self.now().isoformat()
            return result
        self.verification_count += 1
        if self.mode == "disruption":
            return {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "outage_observed_at": self.now().isoformat(),
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            }
        if self.verification_status == "verified":
            outage_observed_at = self.now().isoformat()
            advance = getattr(self.now, "advance", None)
            if callable(advance):
                advance(microseconds=1)
            return {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "outage_observed_at": outage_observed_at,
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "reconnected_at": self.now().isoformat(),
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe",
                        "home_assistant_core_reconnected",
                    ],
                    "redispatch_performed": False,
                },
            }
        return {
            "status": self.verification_status,
            "mismatch_fields": ["fixture"],
            "evidence": {
                "redispatch_performed": False,
                "fallback_occurred": False,
            },
        }

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


class SelfRestartRecoveryGateway(FakeLifecycleGateway):
    def __init__(self, process_instance_id: str) -> None:
        super().__init__()
        self.process_instance_id = process_instance_id
        self.missing_readback = False
        self.verification_entered: asyncio.Event | None = None
        self.verification_release: asyncio.Event | None = None
        self.fail_targets: set[str] = set()

    async def verify_addon_restart(
        self,
        slug,
        *,
        baseline,
        provider_response_received,
        provider_evidence,
    ):
        self.verification_count += 1
        if slug in self.fail_targets:
            raise RuntimeError("synthetic readback failure")
        if self.verification_entered is not None:
            self.verification_entered.set()
        if self.verification_release is not None:
            await self.verification_release.wait()
        if self.missing_readback:
            return {
                "status": "pending",
                "mismatch_fields": ["addon_unavailable"],
                "evidence": {
                    "redispatch_performed": False,
                    "fallback_occurred": False,
                },
            }
        target_class = baseline.get("target_class")
        process_changed = (
            baseline.get("process_instance_id")
            != self.process_instance_id
        )
        if target_class == "engineering_addon" and not process_changed:
            return {
                "status": "pending",
                "mismatch_fields": ["restart_evidence"],
                "evidence": {
                    "process_instance_changed": False,
                    "redispatch_performed": False,
                    "fallback_occurred": False,
                },
            }
        restart_proof = (
            "process_identity"
            if target_class == "engineering_addon"
            else "provider_acknowledgement"
            if provider_response_received
            else None
        )
        if restart_proof is None:
            return {
                "status": "pending",
                "mismatch_fields": ["restart_evidence"],
                "evidence": {
                    "provider_response_received": False,
                    "redispatch_performed": False,
                    "fallback_occurred": False,
                },
            }
        return {
            "status": "verified",
            "mismatch_fields": [],
            "evidence": {
                "addon": deepcopy(baseline.get("addon")),
                "restart_proof": restart_proof,
                "process_instance_changed": process_changed,
                "redispatch_performed": False,
                "fallback_occurred": False,
            },
        }


class GovernedOperationalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.lifecycle = FakeLifecycleGateway()
        self.lifecycle.now = self.clock
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

    async def test_unapproved_restarts_never_attempt_apply_or_dispatch(self):
        for operation, target in (
            ("restart_addon", "local_test_addon"),
            ("restart_home_assistant", "core"),
        ):
            with self.subTest(operation=operation):
                created = await self.create_for(operation, target)
                plan = created["plan"]

                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(
                        plan["plan_id"], plan["plan_hash"]
                    )

                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
                )
                persisted = self.repository.get(plan["plan_id"])
                self.assertEqual(persisted.approval.state.value, "required")
                self.assertEqual(
                    persisted.operational.dispatch["attempt_count"], 0
                )
                self.assertEqual(
                    persisted.operational.verification.status,
                    "not_run",
                )
                self.assertFalse(
                    persisted.operational.verification.evidence.get(
                        "outage_observed", False
                    )
                )
                self.assertEqual(self.lifecycle.dispatch_count, 0)
                self.assertEqual(self.lifecycle.verification_count, 0)
                metrics = self.service.health_summary()[
                    "operational_administration"
                ]["operations"][operation]
                self.assertEqual(metrics["apply_attempts"], 0)
                self.assertEqual(metrics["dispatch_attempts"], 0)
                self.assertEqual(metrics["active_reconciliations"], 0)
                self.assertFalse(
                    any(
                        event.event == f"{operation}_apply_attempted"
                        for event in persisted.events
                    )
                )
                audit_records = [
                    json.loads(line)
                    for line in self.audit_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                self.assertFalse(
                    any(
                        record.get("plan_id") == plan["plan_id"]
                        and record.get("event")
                        == f"{operation}_apply_attempted"
                        for record in audit_records
                    )
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
        self.assertFalse(
            persisted.operational.verification.evidence.get(
                "outage_observed", False
            )
        )

    async def test_home_assistant_pre_dispatch_failure_is_not_outage(self):
        created = await self.service.create_home_assistant_restart_plan()
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
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 0
        )
        self.assertFalse(
            persisted.operational.verification.evidence.get(
                "outage_observed", False
            )
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

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
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_addon_restart_plan(
                addon_slug="missing_addon"
            )
        self.assertEqual(raised.exception.code, ErrorCode.ADDON_NOT_FOUND)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(self.repository.list()), 0)
        self.assertEqual(self.lifecycle.dispatch_count, 0)
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

    async def test_home_assistant_restart_evidence_merges_monotonically(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_timeout",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            },
        ]

        first = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(first["status"], "verification_pending")
        first_plan = self.repository.get(plan["plan_id"])
        first_evidence = first_plan.operational.verification.evidence
        self.assertTrue(first_evidence["outage_observed"])
        self.assertEqual(first_evidence["unavailable_observation_count"], 1)
        first_unavailable_at = first_evidence["first_unavailable_at"]
        observation_deadline = first_plan.operational.dispatch[
            "outage_observation_deadline"
        ]

        self.clock.advance(seconds=5)
        second = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(second["status"], "verification_pending")
        second_plan = self.repository.get(plan["plan_id"])
        second_evidence = second_plan.operational.verification.evidence
        self.assertEqual(
            second_evidence["first_unavailable_at"],
            first_unavailable_at,
        )
        self.assertEqual(
            second_evidence["last_unavailable_at"],
            self.clock().isoformat(),
        )
        self.assertEqual(second_evidence["unavailable_observation_count"], 2)

        self.clock.advance(seconds=30)
        recovered = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(recovered["status"], "applied")
        persisted = self.repository.get(plan["plan_id"])
        evidence = persisted.operational.verification.evidence
        self.assertTrue(evidence["outage_observed"])
        self.assertEqual(
            evidence["first_unavailable_at"], first_unavailable_at
        )
        self.assertEqual(
            evidence["last_unavailable_at"],
            second_evidence["last_unavailable_at"],
        )
        self.assertEqual(evidence["unavailable_observation_count"], 2)
        self.assertTrue(evidence["home_assistant_reconnected"])
        self.assertEqual(
            evidence["reconnected_at"], self.clock().isoformat()
        )
        self.assertEqual(
            evidence["restart_evidence_sources"],
            [
                "home_assistant_core_connection_probe",
                "home_assistant_core_reconnected",
            ],
        )
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertEqual(
            persisted.execution_outcome, "applied_verified"
        )
        self.assertEqual(
            persisted.operational.final_outcome,
            "restart_home_assistant_and_verified",
        )
        self.assertEqual(
            persisted.operational.verification.status, "verified"
        )
        self.assertTrue(
            persisted.operational.verification.operation_completed
        )
        self.assertEqual(
            persisted.operational.verification.mismatch_fields, []
        )
        self.assertTrue(evidence["restart_dispatch_confirmed"])
        self.assertFalse(evidence["redispatch_performed"])
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(
            persisted.operational.dispatch[
                "outage_observation_deadline"
            ],
            observation_deadline,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        metrics = self.service.health_summary()[
            "operational_administration"
        ]["operations"]["restart_home_assistant"]
        self.assertEqual(metrics["dispatch_attempts"], 1)
        self.assertEqual(metrics["dispatch_successes"], 1)
        self.assertEqual(metrics["verified_successes"], 1)
        self.assertEqual(metrics["verification_pending_plans"], 0)
        self.assertEqual(metrics["indeterminate_outcomes"], 0)
        self.assertEqual(
            metrics["last_successful_operation_timestamp"],
            persisted.applied_at,
        )
        self.assertEqual(
            metrics["no_blind_redispatch_preventions"], 2
        )
        repeated = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(repeated["status"], "already_applied")
        self.assertFalse(repeated["redispatch_performed"])
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_home_assistant_restart_without_outage_stays_pending(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            }
        ]

        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(result["status"], "verification_pending")
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(
            persisted.status, PlanStatus.VERIFICATION_REQUIRED
        )
        self.assertIn(
            "restart_evidence",
            persisted.operational.verification.mismatch_fields,
        )
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_late_onset_outage_at_t_plus_60_is_reconciled(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            },
        ]

        initial = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(initial["status"], "verification_pending")
        dispatched = self.repository.get(plan["plan_id"])
        attempted_at = datetime.fromisoformat(
            dispatched.operational.dispatch["attempted_at"]
        )
        deadline = datetime.fromisoformat(
            dispatched.operational.dispatch[
                "outage_observation_deadline"
            ]
        )
        self.assertEqual(
            deadline - attempted_at,
            timedelta(
                seconds=RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
            ),
        )

        self.clock.value = attempted_at + timedelta(seconds=60)
        unavailable = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(unavailable["status"], "verification_pending")
        outage_evidence = self.repository.get(
            plan["plan_id"]
        ).operational.verification.evidence
        self.assertEqual(
            outage_evidence["first_unavailable_at"],
            self.clock().isoformat(),
        )
        self.assertEqual(
            outage_evidence["outage_window_status"], "qualified"
        )

        self.clock.advance(seconds=1)
        recovered = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(recovered["status"], "applied")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertFalse(recovered["redispatch_performed"])

    async def test_late_unrelated_outage_cannot_verify_old_restart(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            }
        ]
        pending = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(pending["status"], "verification_pending")
        dispatched = self.repository.get(plan["plan_id"])
        deadline = datetime.fromisoformat(
            dispatched.operational.dispatch[
                "outage_observation_deadline"
            ]
        )
        self.clock.value = deadline + timedelta(seconds=1)

        recovered_gateway = FakeLifecycleGateway()
        recovered_gateway.now = self.clock
        recovered_gateway.home_assistant_verification_results = [
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            },
        ]
        recovered_service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(
                str(Path(self.temp.name) / "late-outage-audit.jsonl"),
                "beta2-test-access-secret",
            ),
            now=self.clock,
            lifecycle_gateway=recovered_gateway,
        )

        first = await recovered_service.reconcile_operational_plans(
            trigger="startup"
        )
        second = await recovered_service.reconcile_operational_plans(
            trigger="periodic"
        )

        self.assertEqual(first["completed"], 0)
        self.assertEqual(second["completed"], 0)
        persisted = recovered_service.repository.get(plan["plan_id"])
        evidence = persisted.operational.verification.evidence
        self.assertFalse(evidence["outage_observed"])
        self.assertEqual(
            evidence["outage_window_status"], "expired"
        )
        self.assertIn(
            "restart_evidence_window_expired",
            persisted.operational.verification.mismatch_fields,
        )
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertEqual(recovered_gateway.dispatch_count, 0)
        self.assertFalse(evidence["redispatch_performed"])

    async def test_qualified_outage_allows_recovery_after_deadline(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            }
        ]
        pending = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(pending["status"], "verification_pending")
        dispatched = self.repository.get(plan["plan_id"])
        attempted_at = datetime.fromisoformat(
            dispatched.operational.dispatch["attempted_at"]
        )
        self.clock.value = attempted_at + timedelta(seconds=179)
        unavailable = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(
            unavailable["status"], "verification_pending"
        )
        dispatched = self.repository.get(plan["plan_id"])
        evidence_before = deepcopy(
            dispatched.operational.verification.evidence
        )
        deadline = datetime.fromisoformat(
            dispatched.operational.dispatch[
                "outage_observation_deadline"
            ]
        )
        self.clock.value = deadline + timedelta(hours=1)

        recovered_gateway = FakeLifecycleGateway()
        recovered_gateway.now = self.clock
        recovered_gateway.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            }
        ]
        recovered_service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(
                str(Path(self.temp.name) / "late-recovery-audit.jsonl"),
                "beta2-test-access-secret",
            ),
            now=self.clock,
            lifecycle_gateway=recovered_gateway,
        )

        reconciled = await recovered_service.reconcile_operational_plans(
            trigger="startup"
        )

        self.assertEqual(reconciled["completed"], 1)
        persisted = recovered_service.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertTrue(
            persisted.operational.verification.evidence[
                "outage_observed"
            ]
        )
        self.assertEqual(
            persisted.operational.verification.evidence[
                "first_unavailable_at"
            ],
            evidence_before["first_unavailable_at"],
        )
        self.assertEqual(
            persisted.operational.verification.evidence[
                "reconnected_at"
            ],
            self.clock().isoformat(),
        )
        self.assertEqual(
            persisted.operational.dispatch[
                "outage_observation_deadline"
            ],
            deadline.isoformat(),
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_reconnection_timestamp_requires_successful_identity_read(
        self,
    ):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "failure_category": "provider_unavailable",
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["upstream_admission"],
                "evidence": {
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            },
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_timeout",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            },
        ]

        outage = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(outage["status"], "verification_pending")
        evidence = self.repository.get(
            plan["plan_id"]
        ).operational.verification.evidence
        self.assertTrue(evidence["restart_dispatch_confirmed"])
        self.assertNotIn("reconnected_at", evidence)

        self.clock.advance(seconds=1)
        acknowledgement_only = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(
            acknowledgement_only["status"], "verification_pending"
        )
        evidence = self.repository.get(
            plan["plan_id"]
        ).operational.verification.evidence
        self.assertNotIn("home_assistant_reconnected", evidence)
        self.assertNotIn("reconnected_at", evidence)

        self.clock.advance(seconds=1)
        unavailable = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(unavailable["status"], "verification_pending")
        self.assertNotIn(
            "reconnected_at",
            self.repository.get(
                plan["plan_id"]
            ).operational.verification.evidence,
        )

        self.clock.advance(seconds=1)
        identity_read = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(identity_read["status"], "verification_pending")
        persisted_before_recreation = self.repository.get(plan["plan_id"])
        reconnected_at = persisted_before_recreation.operational.verification.evidence[
            "reconnected_at"
        ]
        self.assertTrue(
            persisted_before_recreation.operational.verification.evidence[
                "home_assistant_reconnected"
            ]
        )

        self.clock.advance(seconds=1)
        later_unavailable = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(
            later_unavailable["status"], "verification_pending"
        )
        self.assertEqual(
            self.repository.get(
                plan["plan_id"]
            ).operational.verification.evidence["reconnected_at"],
            reconnected_at,
        )

        self.clock.advance(seconds=1)
        recovered_gateway = FakeLifecycleGateway()
        recovered_gateway.now = self.clock
        recovered_gateway.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            }
        ]
        recovered_service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(
                str(Path(self.temp.name) / "reconnection-audit.jsonl"),
                "beta2-test-access-secret",
            ),
            now=self.clock,
            lifecycle_gateway=recovered_gateway,
        )

        reconciled = await recovered_service.reconcile_operational_plans(
            trigger="startup"
        )

        self.assertEqual(reconciled["completed"], 1)
        persisted = recovered_service.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertEqual(
            persisted.operational.verification.evidence["reconnected_at"],
            reconnected_at,
        )
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertEqual(recovered_gateway.dispatch_count, 0)

    async def test_outage_evidence_boundaries_are_inclusive(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan_public = await self.grant(created)
        plan = self.repository.get(plan_public["plan_id"])
        plan.approval.state = ApprovalState.CONSUMED
        attempted_at = self.clock()
        self.assertAlmostEqual(
            RESTART_DISRUPTION_PROBE_ATTEMPTS
            * RESTART_DISRUPTION_PROBE_INTERVAL_SECONDS,
            15.0,
        )
        self.assertEqual(
            RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS, 180.0
        )
        deadline = attempted_at + timedelta(
            seconds=RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
        )
        plan.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "attempted_at": attempted_at.isoformat(),
                "outage_observation_deadline": deadline.isoformat(),
            }
        )

        for label, observed_at, expected in (
            (
                "before_dispatch",
                attempted_at - timedelta(microseconds=1),
                False,
            ),
            ("at_dispatch", attempted_at, True),
            (
                "during_initial_probe_budget",
                attempted_at + timedelta(seconds=14),
                True,
            ),
            (
                "late_onset_after_initial_probe_budget",
                attempted_at + timedelta(seconds=60),
                True,
            ),
            ("at_deadline", deadline, True),
            (
                "after_deadline",
                deadline + timedelta(microseconds=1),
                False,
            ),
        ):
            with self.subTest(label=label):
                evidence = {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "first_unavailable_at": observed_at.isoformat(),
                    "last_unavailable_at": observed_at.isoformat(),
                    "unavailable_observation_count": 1,
                    "outage_failure_category": (
                        "provider_unavailable"
                    ),
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                }
                state = (
                    self.service._home_assistant_outage_evidence_state(
                        plan,
                        evidence,
                        now=observed_at.isoformat(),
                    )
                )
                self.assertEqual(state["authoritative"], expected)

    async def test_malformed_outage_evidence_matrix_fails_closed(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan_public = await self.grant(created)
        plan = self.repository.get(plan_public["plan_id"])
        plan.approval.state = ApprovalState.CONSUMED
        attempted_at = self.clock()
        deadline = attempted_at + timedelta(
            seconds=RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
        )
        plan.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "attempted_at": attempted_at.isoformat(),
                "outage_observation_deadline": deadline.isoformat(),
            }
        )
        complete = {
            "outage_observed": True,
            "home_assistant_core_unavailable": True,
            "first_unavailable_at": attempted_at.isoformat(),
            "last_unavailable_at": attempted_at.isoformat(),
            "unavailable_observation_count": 1,
            "outage_failure_category": "provider_unavailable",
            "restart_evidence_sources": [
                "home_assistant_core_connection_probe"
            ],
        }
        raw_state = (
            self.service._home_assistant_outage_evidence_state(
                plan,
                {"outage_observed": True},
                now=attempted_at.isoformat(),
            )
        )
        self.assertFalse(raw_state["authoritative"])
        cases = {
            "missing_first": {"first_unavailable_at": None},
            "invalid_first": {"first_unavailable_at": "invalid"},
            "first_before_dispatch": {
                "first_unavailable_at": (
                    attempted_at - timedelta(microseconds=1)
                ).isoformat()
            },
            "first_after_deadline": {
                "first_unavailable_at": (
                    deadline + timedelta(microseconds=1)
                ).isoformat()
            },
            "missing_last": {"last_unavailable_at": None},
            "last_before_first": {
                "last_unavailable_at": (
                    attempted_at - timedelta(microseconds=1)
                ).isoformat()
            },
            "last_after_deadline": {
                "last_unavailable_at": (
                    deadline + timedelta(microseconds=1)
                ).isoformat()
            },
            "missing_source": {"restart_evidence_sources": []},
            "unapproved_source": {
                "restart_evidence_sources": [
                    "home_assistant_core_reconnected"
                ]
            },
            "missing_category": {"outage_failure_category": None},
            "disallowed_category": {
                "outage_failure_category": "provider_error"
            },
            "missing_count": {"unavailable_observation_count": None},
            "zero_count": {"unavailable_observation_count": 0},
            "negative_count": {"unavailable_observation_count": -1},
        }
        for label, replacement in cases.items():
            with self.subTest(label=label):
                evidence = {**complete, **replacement}
                state = (
                    self.service._home_assistant_outage_evidence_state(
                        plan,
                        evidence,
                        now=attempted_at.isoformat(),
                    )
                )
                self.assertFalse(state["authoritative"])

        plan_cases = {
            "dispatch_not_persisted": {"dispatched": False},
            "attempt_count_zero": {"attempt_count": 0},
            "missing_deadline": {"outage_observation_deadline": None},
            "invalid_deadline": {
                "outage_observation_deadline": "invalid"
            },
            "extended_deadline": {
                "outage_observation_deadline": (
                    deadline + timedelta(seconds=1)
                ).isoformat()
            },
            "extended_deadline_one_hour": {
                "outage_observation_deadline": (
                    deadline + timedelta(hours=1)
                ).isoformat()
            },
            "shortened_deadline": {
                "outage_observation_deadline": (
                    deadline - timedelta(seconds=1)
                ).isoformat()
            },
        }
        for label, replacement in plan_cases.items():
            with self.subTest(label=label):
                changed = deepcopy(plan)
                changed.operational.dispatch.update(replacement)
                state = (
                    self.service._home_assistant_outage_evidence_state(
                        changed,
                        complete,
                        now=attempted_at.isoformat(),
                    )
                )
                self.assertFalse(state["authoritative"])

        unconsumed = deepcopy(plan)
        unconsumed.approval.state = ApprovalState.APPROVED
        state = self.service._home_assistant_outage_evidence_state(
            unconsumed,
            complete,
            now=attempted_at.isoformat(),
        )
        self.assertFalse(state["authoritative"])

    async def test_legacy_disruption_flag_is_not_authoritative_outage(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        original_hash = plan["plan_hash"]
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "redispatch_performed": False,
                },
            }
        ]
        first = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(first["status"], "verification_pending")
        persisted = self.repository.get(plan["plan_id"])
        persisted.operational.verification.evidence = {
            "outage_observed": True,
            "expected_disruption_observed": True,
            "restart_dispatch_confirmed": True,
            "redispatch_performed": False,
        }
        self.repository.save(persisted)
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "redispatch_performed": False,
                },
            }
        ]

        resumed = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )

        self.assertEqual(resumed["status"], "verification_pending")
        self.assertFalse(
            self.lifecycle.home_assistant_verification_calls[-1][
                "authoritative_outage_observed"
            ]
        )
        self.assertTrue(
            self.lifecycle.home_assistant_verification_calls[-1][
                "outage_observation_window_open"
            ]
        )
        restored = self.repository.get(plan["plan_id"])
        self.assertEqual(self.service.plan_hash(restored), original_hash)
        self.assertFalse(
            restored.operational.verification.evidence.get(
                "outage_observed", False
            )
        )
        self.assertIn(
            "restart_evidence",
            restored.operational.verification.mismatch_fields,
        )
        self.assertEqual(
            restored.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_home_assistant_restart_reconciles_after_process_restart(
        self,
    ):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self.grant(created)
        original_hash = plan["plan_hash"]
        self.lifecycle.home_assistant_verification_results = [
            {
                "status": "pending",
                "mismatch_fields": ["home_assistant_recovery"],
                "evidence": {
                    "outage_observed": True,
                    "home_assistant_core_unavailable": True,
                    "failure_category": "provider_unavailable",
                    "restart_evidence_sources": [
                        "home_assistant_core_connection_probe"
                    ],
                    "redispatch_performed": False,
                },
            }
        ]
        pending = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(pending["status"], "verification_pending")
        self.assertEqual(self.lifecycle.dispatch_count, 1)

        recovered_gateway = FakeLifecycleGateway()
        recovered_gateway.now = self.clock
        recovered_gateway.home_assistant_verification_results = [
            {
                "status": "verified",
                "mismatch_fields": [],
                "evidence": {
                    "restart_dispatch_confirmed": True,
                    "home_assistant_reconnected": True,
                    "_successful_identity_read": True,
                    "home_assistant_identity_unchanged": True,
                    "post_restart_configuration_valid": True,
                    "restart_evidence_sources": [
                        "home_assistant_core_reconnected"
                    ],
                    "redispatch_performed": False,
                },
            }
        ]
        recovered_service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(
                str(Path(self.temp.name) / "recovered-audit.jsonl"),
                "beta2-test-access-secret",
            ),
            now=self.clock,
            lifecycle_gateway=recovered_gateway,
        )
        reconciled = await recovered_service.reconcile_operational_plans(
            trigger="startup"
        )

        self.assertEqual(reconciled["completed"], 1)
        persisted = recovered_service.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertEqual(
            recovered_service.plan_hash(persisted), original_hash
        )
        self.assertTrue(
            persisted.operational.verification.evidence[
                "outage_observed"
            ]
        )
        self.assertTrue(
            persisted.operational.verification.evidence[
                "home_assistant_reconnected"
            ]
        )
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(recovered_gateway.dispatch_count, 0)
        self.assertEqual(recovered_gateway.verification_count, 1)

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

    async def test_startup_reconciliation_verifies_self_restart_readback_only(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug=ENGINEERING_ADDON_SLUG
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(self.lifecycle.dispatch_count, 1)

        reloaded_repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        recovered_lifecycle = SelfRestartRecoveryGateway("process-two")
        recovered_service = ChangeGovernanceService(
            reloaded_repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=recovered_lifecycle,
        )
        from ha_mcp_engineering.application import (  # noqa: E402
            GOVERNANCE,
            _run_operational_reconciliation_pass,
        )

        with patch.object(GOVERNANCE, "service", recovered_service):
            await _run_operational_reconciliation_pass("startup")
        self.assertEqual(recovered_lifecycle.dispatch_count, 0)
        self.assertEqual(recovered_lifecycle.verification_count, 1)
        recovered = reloaded_repository.get(plan["plan_id"])
        self.assertEqual(recovered.status, PlanStatus.APPLIED)
        self.assertEqual(
            recovered.operational.dispatch["attempt_count"], 1
        )
        self.assertEqual(
            recovered.operational.verification.evidence[
                "restart_proof"
            ],
            "process_identity",
        )
        self.assertTrue(
            any(
                event.event
                == "restart_addon_startup_reconciliation_started"
                for event in recovered.events
            )
        )
        public = recovered_service.get_plan(plan["plan_id"])
        self.assertEqual(public["status"], "applied")
        self.assertEqual(
            public["operational"]["verification"]["evidence"][
                "restart_proof"
            ],
            "process_identity",
        )
        verification_count = recovered_lifecycle.verification_count
        second = await recovered_service.reconcile_operational_plans(
            trigger="periodic"
        )
        self.assertEqual(second["checked"], 0)
        self.assertEqual(
            recovered_lifecycle.verification_count,
            verification_count,
        )

    async def test_self_restart_reconciliation_requires_changed_process_and_readback(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug=ENGINEERING_ADDON_SLUG
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        await self.service.apply(plan["plan_id"], plan["plan_hash"])

        repository = ChangePlanRepository(Path(self.temp.name) / "plans")
        unchanged = SelfRestartRecoveryGateway("process-one")
        service = ChangeGovernanceService(
            repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=unchanged,
        )
        first = await service.reconcile_operational_plans(
            trigger="startup"
        )
        self.assertEqual(first["pending"], 1)
        self.assertEqual(
            repository.get(plan["plan_id"]).status,
            PlanStatus.VERIFICATION_REQUIRED,
        )

        unchanged.process_instance_id = "process-two"
        unchanged.missing_readback = True
        second = await service.reconcile_operational_plans(
            trigger="periodic"
        )
        self.assertEqual(second["pending"], 1)
        persisted = repository.get(plan["plan_id"])
        self.assertEqual(
            persisted.status, PlanStatus.VERIFICATION_REQUIRED
        )
        self.assertNotIn(
            "restart_proof",
            persisted.operational.verification.evidence,
        )
        self.assertEqual(unchanged.dispatch_count, 0)

    async def test_apply_and_background_reconciliation_share_plan_lock(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug=ENGINEERING_ADDON_SLUG
        )
        plan = await self.grant(created)
        self.lifecycle.mode = "ambiguous"
        self.lifecycle.verification_status = "pending"
        await self.service.apply(plan["plan_id"], plan["plan_hash"])

        repository = ChangePlanRepository(Path(self.temp.name) / "plans")
        recovered = SelfRestartRecoveryGateway("process-two")
        recovered.verification_entered = asyncio.Event()
        recovered.verification_release = asyncio.Event()
        service = ChangeGovernanceService(
            repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=recovered,
        )
        apply_task = asyncio.create_task(
            service.apply(plan["plan_id"], plan["plan_hash"])
        )
        await recovered.verification_entered.wait()
        reconciliation = await service.reconcile_operational_plans(
            trigger="periodic"
        )
        self.assertEqual(reconciliation["checked"], 0)
        self.assertEqual(reconciliation["pending"], 1)
        recovered.verification_release.set()
        applied = await apply_task
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(recovered.dispatch_count, 0)
        self.assertEqual(
            repository.get(plan["plan_id"]).status,
            PlanStatus.APPLIED,
        )

    async def test_reconciliation_is_bounded_and_isolates_plan_failure(self):
        plan_ids = []
        for slug in ("broken_addon", "healthy_addon"):
            created = await self.service.create_addon_restart_plan(
                addon_slug=slug
            )
            plan = await self.grant(created)
            self.lifecycle.mode = "ambiguous"
            self.lifecycle.verification_status = "pending"
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
            persisted = self.repository.get(plan["plan_id"])
            persisted.operational.dispatch[
                "provider_response_received"
            ] = True
            self.repository.save(persisted)
            plan_ids.append(plan["plan_id"])

        repository = ChangePlanRepository(Path(self.temp.name) / "plans")
        recovered = SelfRestartRecoveryGateway("process-two")
        recovered.fail_targets.add("broken_addon")
        service = ChangeGovernanceService(
            repository,
            LegacyGateway(),
            now=self.clock,
            lifecycle_gateway=recovered,
        )
        bounded = await service.reconcile_operational_plans(
            trigger="startup", max_plans=1
        )
        self.assertEqual(bounded["checked"], 1)
        self.assertTrue(bounded["bounded"])

        result = await service.reconcile_operational_plans(
            trigger="periodic"
        )
        self.assertGreaterEqual(
            bounded["completed"] + result["completed"], 1
        )
        self.assertGreaterEqual(
            bounded["failed"] + result["failed"], 1
        )
        self.assertEqual(recovered.dispatch_count, 0)
        statuses = {
            repository.get(plan_id).target_id: repository.get(
                plan_id
            ).status
            for plan_id in plan_ids
        }
        self.assertEqual(
            statuses["healthy_addon"], PlanStatus.APPLIED
        )
        self.assertEqual(
            statuses["broken_addon"],
            PlanStatus.VERIFICATION_REQUIRED,
        )

    async def test_historical_verification_without_restart_proof_is_readable(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan = await self.grant(created)
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        persisted = self.repository.get(plan["plan_id"])
        original_hash = self.service.plan_hash(persisted)
        historical = persisted.to_dict()
        historical["operational"]["verification"]["evidence"].pop(
            "restart_proof", None
        )

        restored = ChangePlan.from_dict(historical)
        self.assertEqual(self.service.plan_hash(restored), original_hash)
        self.assertNotIn(
            "restart_proof",
            restored.operational.verification.evidence,
        )
        self.repository.save(restored)
        self.assertEqual(
            self.repository.get(plan["plan_id"]).status,
            PlanStatus.APPLIED,
        )

    async def test_historical_other_addon_self_plan_is_not_reclassified(self):
        slug = "df26dea6_hass_mcp_engineering_beta"
        created = await self.service.create_addon_restart_plan(
            addon_slug=slug
        )
        persisted = self.repository.get(created["plan"]["plan_id"])
        original_hash = self.service.plan_hash(persisted)
        self.assertEqual(
            persisted.operational.baseline["target_class"],
            "other_addon",
        )
        self.assertNotIn(
            "target_identity", persisted.operational.baseline
        )
        restored = ChangePlan.from_dict(persisted.to_dict())
        self.assertEqual(self.service.plan_hash(restored), original_hash)
        plan = await self.grant(created)

        async def corrected_planning_evidence(_operation, target):
            addon = deepcopy(self.lifecycle.addon)
            addon["slug"] = target
            return {
                "provider": provider_evidence("restart_addon"),
                "baseline": {
                    "addon": addon,
                    "target_class": "engineering_addon",
                    "target_identity": {
                        "requested_slug": target,
                        "resolved_slug": target,
                        "resolved_name": addon["name"],
                        "resolved_version": addon["version"],
                        "resolved_repository": "df26dea6",
                        "identity_source": "supervisor_self_info",
                        "authoritative_self_match": True,
                        "target_class": "engineering_addon",
                    },
                    "process_instance_id": "process-one",
                    "runtime": deepcopy(self.lifecycle.runtime),
                },
            }

        self.lifecycle.planning_evidence = corrected_planning_evidence
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        reloaded = self.repository.get(plan["plan_id"])
        self.assertEqual(self.service.plan_hash(reloaded), original_hash)
        self.assertEqual(
            reloaded.operational.baseline["target_class"],
            "other_addon",
        )
        self.assertNotIn(
            "target_identity", reloaded.operational.baseline
        )

    async def test_historical_other_addon_upstream_plan_is_not_reclassified(
        self,
    ):
        created = await self.service.create_addon_restart_plan(
            addon_slug=UPSTREAM_ADDON_SLUG
        )
        persisted = self.repository.get(created["plan"]["plan_id"])
        original_hash = self.service.plan_hash(persisted)
        self.assertEqual(
            persisted.operational.baseline["target_class"],
            "other_addon",
        )
        restored = ChangePlan.from_dict(persisted.to_dict())
        self.assertEqual(self.service.plan_hash(restored), original_hash)
        plan = await self.grant(created)

        async def corrected_planning_evidence(_operation, target):
            binding = upstream_addon_identity(target)
            return {
                "provider": provider_evidence("restart_addon"),
                "baseline": {
                    "addon": {
                        "slug": target,
                        "name": UPSTREAM_ADDON_NAME,
                        "version": "7.14.2",
                        "state": "started",
                    },
                    "target_class": "upstream_ha_mcp_addon",
                    "target_identity": {
                        "requested_slug": target,
                        "resolved_slug": target,
                        "resolved_name": UPSTREAM_ADDON_NAME,
                        "resolved_version": "7.14.2",
                        "resolved_repository": "abcdef12",
                        "identity_source": binding["identity_source"],
                        "authoritative_self_match": False,
                        "authoritative_upstream_match": True,
                        "target_class": "upstream_ha_mcp_addon",
                    },
                    "upstream_addon_identity": binding,
                    "process_instance_id": "process-one",
                    "runtime": deepcopy(self.lifecycle.runtime),
                },
            }

        self.lifecycle.planning_evidence = corrected_planning_evidence
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        reloaded = self.repository.get(plan["plan_id"])
        self.assertEqual(self.service.plan_hash(reloaded), original_hash)
        self.assertEqual(
            reloaded.operational.baseline["target_class"],
            "other_addon",
        )

    async def test_upstream_provider_binding_drift_blocks_apply_dispatch(
        self,
    ):
        binding = upstream_addon_identity()

        async def planning_evidence(_operation, target):
            current = deepcopy(binding)
            return {
                "provider": provider_evidence("restart_addon"),
                "baseline": {
                    "addon": {
                        "slug": target,
                        "name": UPSTREAM_ADDON_NAME,
                        "version": "7.14.2",
                        "state": "started",
                    },
                    "target_class": "upstream_ha_mcp_addon",
                    "target_identity": {
                        "requested_slug": target,
                        "resolved_slug": target,
                        "resolved_name": UPSTREAM_ADDON_NAME,
                        "resolved_version": "7.14.2",
                        "resolved_repository": "abcdef12",
                        "identity_source": current["identity_source"],
                        "authoritative_self_match": False,
                        "authoritative_upstream_match": True,
                        "target_class": "upstream_ha_mcp_addon",
                    },
                    "upstream_addon_identity": current,
                    "process_instance_id": "process-one",
                    "runtime": deepcopy(self.lifecycle.runtime),
                },
            }

        self.lifecycle.planning_evidence = planning_evidence
        created = await self.service.create_addon_restart_plan(
            addon_slug=UPSTREAM_ADDON_SLUG
        )
        plan = await self.grant(created)
        binding["provider_contract"]["compatibility_entry_id"] = (
            "ha-mcp-v7.14.2-conflicting"
        )

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.approval.state.value, "approved")
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 0
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_incomplete_historical_self_restart_plan_fails_closed(self):
        created = await self.service.create_addon_restart_plan(
            addon_slug=ENGINEERING_ADDON_SLUG
        )
        persisted = self.repository.get(created["plan"]["plan_id"])
        persisted.operational.baseline.pop("runtime")
        persisted.current_state_fingerprint = stable_hash(
            persisted.operational.baseline
        )
        self.repository.save(persisted)
        historical_hash = self.service.plan_hash(persisted)
        created["plan"]["plan_hash"] = historical_hash
        plan = await self.grant(created)

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], historical_hash
            )

        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        reloaded = self.repository.get(plan["plan_id"])
        self.assertEqual(reloaded.approval.state.value, "approved")
        self.assertEqual(
            reloaded.operational.dispatch["attempt_count"], 0
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
        self.addons = [
            {
                "slug": "local_test_addon",
                "name": "Fixture",
                "version": "1.0.0",
                "state": "started",
                "repository": "local",
            },
            {
                "slug": "df26dea6_hass_mcp_engineering_beta",
                "name": "HA MCP Engineering Server Beta",
                "version": "2.1.1-beta.2",
                "state": "started",
                "repository": "df26dea6",
            },
            {
                "slug": UPSTREAM_ADDON_SLUG,
                "name": UPSTREAM_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
                "repository": "abcdef12",
            },
        ]

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
        elif arguments.get("source") == "installed":
            payload = {
                "success": True,
                "addons": deepcopy(self.addons),
                "summary": {
                    "total_installed": len(self.addons),
                },
            }
        else:
            addon = next(
                (
                    item
                    for item in self.addons
                    if item["slug"] == arguments["slug"]
                ),
                None,
            )
            payload = {
                "success": True,
                "addon": deepcopy(addon),
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


class SupervisorSelfIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_self_info_is_authoritative_and_bounded(self):
        payload = json.dumps(
            {
                "result": "ok",
                "data": {
                    "slug": "df26dea6_hass_mcp_engineering_beta",
                    "name": "HA MCP Engineering Server Beta",
                    "version": "2.1.1-beta.2",
                    "repository": "df26dea6",
                },
            }
        ).encode()

        async def fetch():
            return 200, payload

        identity = await SupervisorSelfAddonIdentityResolver(
            base_url="http://supervisor",
            token="synthetic-supervisor-token",
            timeout_seconds=5,
            fetcher=fetch,
        ).resolve()
        self.assertEqual(
            identity.slug,
            "df26dea6_hass_mcp_engineering_beta",
        )
        self.assertEqual(identity.as_dict()["identity_source"], "supervisor_self_info")
        self.assertTrue(identity.as_dict()["authoritative"])

    async def test_malformed_or_conflicting_self_info_fails_closed(self):
        payloads = (
            b'{"result":"ok","data":{"slug":"one","slug":"two"}}',
            b'{"result":"ok","data":{"slug":"self","version":NaN}}',
            b'{"result":"ok","data":{"slug":"self"}}',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                async def fetch(value=payload):
                    return 200, value

                with self.assertRaises(SelfAddonIdentityError):
                    await SupervisorSelfAddonIdentityResolver(
                        base_url="http://supervisor",
                        token="synthetic-supervisor-token",
                        timeout_seconds=5,
                        fetcher=fetch,
                    ).resolve()


class ExactOperationalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefixed_engineering_slug_is_exact_self_target(self):
        slug = "df26dea6_hass_mcp_engineering_beta"
        runtime = {
            "server_version": "2.2.0-beta.1",
            "build_sha": "a" * 40,
            "registered_tool_count": 74,
            "engineering_tool_count": 48,
            "delegated_tool_count": 26,
            "governance_storage_status": "healthy",
            "governance_plan_count": 1,
            "audit_storage_status": "healthy",
            "audit_write_failures": 0,
            "fallback_count": 0,
        }

        class AddonProvider:
            async def probe(self, _operation):
                return SimpleNamespace(
                    as_dict=lambda: provider_evidence("restart_addon")
                )

            async def get_addon(self, requested_slug):
                return {
                    "slug": requested_slug,
                    "name": "HA MCP Engineering Server Beta",
                    "version": "2.2.0-beta.1",
                    "state": "started",
                    "repository": "df26dea6",
                }

        async def fetch_self():
            return 200, json.dumps(
                {
                    "result": "ok",
                    "data": {
                        "slug": slug,
                        "name": "HA MCP Engineering Server Beta",
                        "version": "2.2.0-beta.1",
                        "repository": "df26dea6",
                    },
                }
            ).encode()

        self_resolver = SupervisorSelfAddonIdentityResolver(
            base_url="http://supervisor",
            token="synthetic-supervisor-token",
            timeout_seconds=5,
            fetcher=fetch_self,
        )

        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="original-process",
            self_addon_identity_resolver=self_resolver.resolve,
        )
        with tempfile.TemporaryDirectory() as directory:
            service = ChangeGovernanceService(
                ChangePlanRepository(Path(directory) / "plans"),
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )
            created = await service.create_addon_restart_plan(
                addon_slug=slug
            )
            persisted = service.repository.get(
                created["plan"]["plan_id"]
            )

        baseline = persisted.operational.baseline
        self.assertEqual(baseline["target_class"], "engineering_addon")
        self.assertEqual(
            baseline["target_identity"],
            {
                "requested_slug": slug,
                "resolved_slug": slug,
                "resolved_name": "HA MCP Engineering Server Beta",
                "resolved_version": "2.2.0-beta.1",
                "resolved_repository": "df26dea6",
                "identity_source": "supervisor_self_info",
                "authoritative_self_match": True,
                "authoritative_upstream_match": False,
                "target_class": "engineering_addon",
            },
        )
        self.assertEqual(
            baseline["process_instance_id"], "original-process"
        )
        self.assertEqual(baseline["runtime"], runtime)
        self.assertIn(
            "engineering_process_recovery_when_self_restart",
            persisted.operational.verification_contract["required"],
        )
        pending = await gateway.verify_addon_restart(
            slug,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence={},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertNotIn(
            "restart_proof", pending["evidence"]
        )
        gateway.process_instance_id = "restarted-process"
        verified = await gateway.verify_addon_restart(
            slug,
            baseline=baseline,
            provider_response_received=False,
            provider_evidence={},
        )
        self.assertEqual(
            verified["evidence"]["restart_proof"],
            "process_identity",
        )

    async def test_prefixed_upstream_slug_is_bound_to_admitted_endpoint(
        self,
    ):
        transport = FakeMcpTransport()
        provider = ReviewedOperationalLifecycleProvider()
        provider.configure(
            lifecycle_settings(
                "http://abcdef12-ha-mcp:9583/"
                "synthetic-upstream-secret/mcp"
            ),
            transport=transport,
        )

        async def self_identity():
            return SupervisorSelfAddonIdentity(
                slug="df26dea6_hass_mcp_engineering_beta",
                name="HA MCP Engineering Server Beta",
                version="2.1.1-beta.2",
                repository="df26dea6",
            )

        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {
                "upstream_version": "7.14.2",
                "upstream_protocol": "2025-03-26",
                "upstream_catalog_fingerprint": (
                    "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c"
                ),
                "upstream_admission_status": "admitted_exact",
                "fallback_count": 0,
            },
            process_instance_id="process",
            self_addon_identity_resolver=self_identity,
        )
        with tempfile.TemporaryDirectory() as directory:
            service = ChangeGovernanceService(
                ChangePlanRepository(Path(directory) / "plans"),
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )
            created = await service.create_addon_restart_plan(
                addon_slug=UPSTREAM_ADDON_SLUG
            )
            persisted = service.repository.get(
                created["plan"]["plan_id"]
            )

        baseline = persisted.operational.baseline
        self.assertEqual(
            baseline["target_class"], "upstream_ha_mcp_addon"
        )
        self.assertTrue(
            baseline["target_identity"][
                "authoritative_upstream_match"
            ]
        )
        self.assertEqual(
            baseline["upstream_addon_identity"]["slug"],
            UPSTREAM_ADDON_SLUG,
        )
        self.assertEqual(
            baseline["upstream_addon_identity"]["endpoint_host"],
            "abcdef12-ha-mcp",
        )
        self.assertEqual(
            baseline["upstream_addon_identity"][
                "inventory_arguments"
            ],
            {"source": "installed", "include_stats": False},
        )
        self.assertEqual(
            tuple(
                baseline["upstream_addon_identity"][
                    "provider_contract"
                ][field]
                for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
            ),
            tuple(
                persisted.operational.provider_capability_evidence[
                    field
                ]
                for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS
            ),
        )
        self.assertIn(
            "upstream_readmission_when_applicable",
            persisted.operational.verification_contract["required"],
        )
        self.assertNotIn(
            "ha_manage_addon",
            [tool_name for tool_name, _arguments in transport.calls],
        )

    async def test_inventory_arguments_match_both_reviewed_releases(self):
        for version in ("7.14.1", "7.14.2"):
            with self.subTest(version=version):
                transport = FakeMcpTransport(version)
                for addon in transport.addons:
                    if addon["slug"] == UPSTREAM_ADDON_SLUG:
                        addon["version"] = version
                provider = ReviewedOperationalLifecycleProvider()
                provider.configure(
                    lifecycle_settings(
                        "http://abcdef12-ha-mcp:9583/"
                        "synthetic-upstream-secret/mcp"
                    ),
                    transport=transport,
                )
                addon = await provider.get_addon(
                    UPSTREAM_ADDON_SLUG
                )
                self.assertEqual(
                    addon["upstream_addon_identity"]["status"],
                    "bound",
                )
                self.assertEqual(
                    transport.calls[:2],
                    [
                        (
                            "ha_get_addon",
                            {
                                "source": "installed",
                                "include_stats": False,
                            },
                        ),
                        (
                            "ha_get_addon",
                            {"slug": UPSTREAM_ADDON_SLUG},
                        ),
                    ],
                )

    async def test_unavailable_self_identity_cannot_default_to_other_addon(self):
        class AddonProvider:
            get_addon_calls = 0

            async def probe(self, _operation):
                return SimpleNamespace(
                    as_dict=lambda: provider_evidence("restart_addon")
                )

            async def get_addon(self, _slug):
                self.get_addon_calls += 1
                return {}

        async def unavailable():
            raise SelfAddonIdentityError()

        provider = AddonProvider()
        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
            self_addon_identity_resolver=unavailable,
        )
        with self.assertRaises(LifecycleGatewayError) as raised:
            await gateway.planning_evidence(
                "restart_addon", "possible_self_addon"
            )
        self.assertEqual(
            raised.exception.category,
            "self_addon_identity_unavailable",
        )
        self.assertEqual(provider.get_addon_calls, 0)

    async def test_missing_addon_is_domain_outcome_not_provider_failure(self):
        provider = ReviewedOperationalLifecycleProvider()
        transport = FakeMcpTransport()
        provider._transport = transport
        await provider.probe("restart_addon")
        before = provider.health_snapshot()
        telemetry, token = begin_request("missing-addon-provider-123")

        try:
            with self.assertRaises(
                OperationalLifecycleProviderError
            ) as raised:
                await provider.get_addon("ha_mcp")
        finally:
            end_request(token)

        self.assertEqual(raised.exception.category, "addon_not_found")
        self.assertFalse(raised.exception.dispatched)
        self.assertEqual(telemetry.upstream_request_count, 1)
        self.assertGreaterEqual(telemetry.upstream_duration_ms, 0.0)
        after = provider.health_snapshot()
        self.assertEqual(after["operational_status"], "available")
        self.assertEqual(after["failure_counts"], before["failure_counts"])
        self.assertEqual(after["last_failure_category"], None)
        self.assertEqual(
            after["selected_compatibility_entry_id"],
            before["selected_compatibility_entry_id"],
        )
        self.assertEqual(
            after["observed_upstream_version"], "7.14.2"
        )
        self.assertEqual(
            after["domain_outcome_counts"], {"addon_not_found": 1}
        )
        self.assertEqual(after["fallback_count"], 0)
        self.assertFalse(
            error_definition(ErrorCode.ADDON_NOT_FOUND).retryable
        )
        self.assertEqual(
            transport.calls,
            [
                (
                    "ha_get_addon",
                    {"source": "installed", "include_stats": False},
                )
            ],
        )

    async def test_ambiguous_upstream_endpoint_binding_creates_no_plan(
        self,
    ):
        transport = FakeMcpTransport()
        transport.addons.append(
            {
                "slug": "abcdef12-ha-mcp",
                "name": "Unrelated lookalike",
                "version": "7.14.2",
                "state": "started",
                "repository": "unrelated",
            }
        )
        provider = ReviewedOperationalLifecycleProvider()
        provider.configure(
            lifecycle_settings(
                "http://abcdef12-ha-mcp:9583/"
                "synthetic-upstream-secret/mcp"
            ),
            transport=transport,
        )

        async def self_identity():
            return SupervisorSelfAddonIdentity(
                slug="df26dea6_hass_mcp_engineering_beta",
                name="HA MCP Engineering Server Beta",
                version="2.1.1-beta.2",
                repository="df26dea6",
            )

        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
            self_addon_identity_resolver=self_identity,
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(
                Path(directory) / "plans"
            )
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )
            with self.assertRaises(GovernanceError) as raised:
                await service.create_addon_restart_plan(
                    addon_slug="local_test_addon"
                )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
            )
            self.assertEqual(repository.list(), [])
        self.assertNotIn(
            "ha_manage_addon",
            [tool_name for tool_name, _arguments in transport.calls],
        )

    async def test_real_addon_provider_failure_still_degrades_health(self):
        provider = ReviewedOperationalLifecycleProvider()
        transport = FakeMcpTransport()
        transport.raw_result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": {
                                "code": "SERVICE_CALL_FAILED",
                                "message": "bounded fixture failure",
                            },
                        }
                    ),
                }
            ],
            "isError": True,
        }
        provider._transport = transport
        with self.assertRaises(
            OperationalLifecycleProviderError
        ) as raised:
            await provider.get_addon("local_test_addon")
        self.assertEqual(raised.exception.category, "operation_failed")
        health = provider.health_snapshot()
        self.assertEqual(health["operational_status"], "unavailable")
        self.assertEqual(
            health["failure_counts"], {"operation_failed": 1}
        )
        self.assertEqual(health["domain_outcome_counts"], {})

    async def test_addon_transport_failures_still_degrade_health(self):
        class FailingTransport:
            def __init__(self, category):
                self.category = category

            async def execute_read(self, *_args, **_kwargs):
                raise DashboardTransportError(self.category)

        cases = {
            "authentication_failed": "permission_failure",
            "connection_failed": "provider_unavailable",
            "timeout": "provider_timeout",
        }
        for transport_category, expected in cases.items():
            with self.subTest(category=transport_category):
                provider = ReviewedOperationalLifecycleProvider()
                provider._transport = FailingTransport(
                    transport_category
                )
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as raised:
                    await provider.get_addon("local_test_addon")
                self.assertEqual(
                    raised.exception.category, expected
                )
                health = provider.health_snapshot()
                self.assertEqual(
                    health["operational_status"], "unavailable"
                )
                self.assertEqual(
                    health["failure_counts"], {expected: 1}
                )
                self.assertEqual(
                    health["domain_outcome_counts"], {}
                )

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
        self.assertEqual(
            verified["evidence"]["restart_proof"],
            "provider_acknowledgement",
        )

    async def test_missing_addon_readback_remains_verification_pending(self):
        class AddonProvider:
            async def get_addon(self, _slug):
                raise OperationalLifecycleProviderError(
                    "resource_not_found", dispatched=False
                )

        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        pending = await gateway.verify_addon_restart(
            "missing_after_dispatch",
            baseline={
                "addon": {
                    "slug": "missing_after_dispatch",
                    "name": "Fixture",
                    "version": "1.0.0",
                    "state": "started",
                },
                "target_class": "other_addon",
            },
            provider_response_received=True,
            provider_evidence={},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(
            pending["mismatch_fields"], ["addon_unavailable"]
        )
        self.assertNotIn(
            "restart_proof", pending["evidence"]
        )

    async def test_upstream_addon_requires_exact_readmission(self):
        class AddonProvider:
            observed_version = "7.14.1"
            upstream_identity = None
            restart_dispatch_count = 0

            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "state": "started",
                    "repository": "abcdef12",
                    "upstream_addon_identity": deepcopy(
                        self.upstream_identity
                    ),
                }

            async def probe(self, operation):
                self.operation = operation
                evidence = provider_evidence("restart_addon")
                evidence["server_version"] = self.observed_version
                return SimpleNamespace(
                    as_dict=lambda: deepcopy(evidence)
                )

        provider = AddonProvider()
        runtime = {
            "upstream_version": "7.14.2",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": (
                "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c"
            ),
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="process",
        )
        baseline = {
            "addon": {
                "slug": UPSTREAM_ADDON_SLUG,
                "name": UPSTREAM_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
            },
            "target_class": "upstream_ha_mcp_addon",
            "runtime": deepcopy(runtime),
            "upstream_addon_identity": upstream_addon_identity(),
        }
        planned_provider = provider_evidence("restart_addon")
        pending = await gateway.verify_addon_restart(
            UPSTREAM_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence=planned_provider,
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(
            pending["mismatch_fields"],
            ["upstream_addon_identity"],
        )
        self.assertNotIn("restart_proof", pending["evidence"])
        provider.observed_version = "7.14.2"
        provider.upstream_identity = upstream_addon_identity()
        provider.upstream_identity.pop("provider_contract")
        verified = await gateway.verify_addon_restart(
            UPSTREAM_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence=planned_provider,
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(
            verified["evidence"]["restart_proof"],
            "upstream_readmission",
        )
        self.assertEqual(provider.restart_dispatch_count, 0)

    async def test_upstream_restart_binding_drift_never_verifies(self):
        class AddonProvider:
            def __init__(self, identity):
                self.identity = identity
                self.probe_evidence = provider_evidence(
                    "restart_addon"
                )
                self.restart_dispatch_count = 0

            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "state": "started",
                    "repository": "abcdef12",
                    "upstream_addon_identity": deepcopy(
                        self.identity
                    ),
                }

            async def probe(self, _operation):
                return SimpleNamespace(
                    as_dict=lambda: deepcopy(self.probe_evidence)
                )

        planned_provider = provider_evidence("restart_addon")
        runtime = {
            "upstream_version": "7.14.2",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": planned_provider[
                "catalog_fingerprint"
            ],
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        baseline = {
            "addon": {
                "slug": UPSTREAM_ADDON_SLUG,
                "name": UPSTREAM_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
            },
            "target_class": "upstream_ha_mcp_addon",
            "runtime": deepcopy(runtime),
            "upstream_addon_identity": upstream_addon_identity(),
        }
        cases = {}
        endpoint_drift = upstream_addon_identity()
        endpoint_drift["endpoint_host"] = "different-ha-mcp"
        endpoint_drift["slug"] = "different_ha_mcp"
        cases["endpoint_and_slug_drift"] = endpoint_drift
        cases["ambiguous"] = {
            "status": "ambiguous",
            "endpoint_host": "abcdef12-ha-mcp",
        }
        cases["alias"] = {
            "status": "unavailable",
            "endpoint_host": "ha-mcp-alias",
        }
        cases["ip"] = {
            "status": "unavailable",
            "endpoint_host": "127.0.0.1",
        }
        same_release_other_addon = upstream_addon_identity(
            "fedcba98_ha_mcp"
        )
        cases["same_release_other_addon"] = (
            same_release_other_addon
        )
        for name, identity in cases.items():
            with self.subTest(name=name):
                provider = AddonProvider(identity)
                gateway = OperationalLifecycleGateway(
                    provider,
                    None,
                    None,
                    configuration_validator=lambda: None,
                    runtime_snapshot=lambda: deepcopy(runtime),
                    process_instance_id="recovered-process",
                )
                result = await gateway.verify_addon_restart(
                    UPSTREAM_ADDON_SLUG,
                    baseline=deepcopy(baseline),
                    provider_response_received=True,
                    provider_evidence=planned_provider,
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["mismatch_fields"],
                    ["upstream_addon_identity"],
                )
                self.assertNotIn(
                    "restart_proof", result["evidence"]
                )
                self.assertEqual(
                    provider.restart_dispatch_count, 0
                )

        provider = AddonProvider(upstream_addon_identity())
        provider.probe_evidence["argument_constraints"] = {
            "drift": True
        }
        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="recovered-process",
        )
        result = await gateway.verify_addon_restart(
            UPSTREAM_ADDON_SLUG,
            baseline=deepcopy(baseline),
            provider_response_received=True,
            provider_evidence=planned_provider,
        )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("restart_proof", result["evidence"])

    async def test_upstream_reconciliation_retries_binding_without_redispatch(
        self,
    ):
        class AddonProvider:
            def __init__(self):
                self.identity = upstream_addon_identity()
                self.identity.pop("provider_contract")
                self.probe_evidence = provider_evidence(
                    "restart_addon"
                )
                self.restart_dispatch_count = 0

            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "state": "started",
                    "repository": "abcdef12",
                    "upstream_addon_identity": deepcopy(
                        self.identity
                    ),
                }

            async def probe(self, _operation):
                return SimpleNamespace(
                    as_dict=lambda: deepcopy(self.probe_evidence)
                )

        provider = AddonProvider()
        runtime = {
            "upstream_version": "7.14.2",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": provider.probe_evidence[
                "catalog_fingerprint"
            ],
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        gateway = OperationalLifecycleGateway(
            provider,
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="recovered-process",
            self_addon_identity_resolver=lambda: asyncio.sleep(
                0,
                result=SupervisorSelfAddonIdentity(
                    slug="df26dea6_hass_mcp_engineering_beta",
                    name="HA MCP Engineering Server Beta",
                    version="2.1.1-beta.2",
                    repository="df26dea6",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(
                Path(directory) / "plans"
            )
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )
            created = await service.create_addon_restart_plan(
                addon_slug=UPSTREAM_ADDON_SLUG
            )
            plan = repository.get(created["plan"]["plan_id"])
            original_hash = service.plan_hash(plan)
            plan.approval.state = ApprovalState.CONSUMED
            plan.operational.dispatch.update(
                {
                    "attempt_count": 1,
                    "dispatched": True,
                    "provider_response_received": True,
                }
            )
            plan.status = PlanStatus.VERIFICATION_REQUIRED
            repository.save(plan)

            provider.identity = None
            recovered = ChangeGovernanceService(
                ChangePlanRepository(Path(directory) / "plans"),
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit-recovered.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )
            first = await recovered.reconcile_operational_plans(
                trigger="startup"
            )
            self.assertEqual(first["pending"], 1)
            self.assertEqual(provider.restart_dispatch_count, 0)
            pending = repository.get(plan.plan_id)
            self.assertEqual(
                pending.status, PlanStatus.VERIFICATION_REQUIRED
            )
            self.assertEqual(
                recovered.plan_hash(pending), original_hash
            )

            provider.identity = upstream_addon_identity()
            provider.identity.pop("provider_contract")
            second = await recovered.reconcile_operational_plans(
                trigger="periodic"
            )
            self.assertEqual(second["completed"], 1)
            verified = repository.get(plan.plan_id)
            self.assertEqual(verified.status, PlanStatus.APPLIED)
            self.assertEqual(
                verified.operational.verification.evidence[
                    "restart_proof"
                ],
                "upstream_readmission",
            )
            self.assertEqual(
                verified.operational.dispatch["attempt_count"], 1
            )
            self.assertEqual(provider.restart_dispatch_count, 0)
            self.assertEqual(
                recovered.plan_hash(verified), original_hash
            )

    async def test_historical_upstream_restart_without_binding_fails_closed(
        self,
    ):
        class AddonProvider:
            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "state": "started",
                    "upstream_addon_identity": (
                        upstream_addon_identity()
                    ),
                }

        baseline = {
            "addon": {
                "slug": UPSTREAM_ADDON_SLUG,
                "name": UPSTREAM_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
            },
            "target_class": "upstream_ha_mcp_addon",
        }
        original = stable_hash(baseline)
        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="recovered-process",
        )
        result = await gateway.verify_addon_restart(
            UPSTREAM_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=True,
            provider_evidence=provider_evidence("restart_addon"),
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["evidence"]["identity_status"],
            "baseline_incomplete",
        )
        self.assertNotIn("restart_proof", result["evidence"])
        self.assertEqual(stable_hash(baseline), original)

    def test_upstream_readmission_requires_all_eight_provider_fields(self):
        planned = provider_evidence("restart_addon")
        runtime = {
            "upstream_version": "7.14.2",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": planned[
                "catalog_fingerprint"
            ],
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        self.assertTrue(
            _upstream_readmission_matches(
                planned, deepcopy(planned), runtime
            )
        )
        for field in UPSTREAM_PROVIDER_CONTRACT_FIELDS:
            with self.subTest(field=field):
                mismatched = deepcopy(planned)
                mismatched[field] = None
                self.assertFalse(
                    _upstream_readmission_matches(
                        planned, mismatched, runtime
                    )
                )

    async def test_engineering_self_restart_requires_new_process_instance(self):
        class AddonProvider:
            async def get_addon(self, slug):
                return {
                    "slug": slug,
                    "name": "Engineering",
                    "version": "2.2.0-beta.1",
                    "state": "started",
                }

        runtime = {
            "server_version": "2.2.0-beta.1",
            "build_sha": "a" * 40,
            "registered_tool_count": 74,
            "engineering_tool_count": 48,
            "delegated_tool_count": 26,
            "governance_storage_status": "healthy",
            "governance_plan_count": 1,
            "audit_storage_status": "healthy",
            "audit_write_failures": 0,
            "fallback_count": 0,
        }
        gateway = OperationalLifecycleGateway(
            AddonProvider(),
            None,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="original-process",
        )
        baseline = {
            "addon": {
                "slug": ENGINEERING_ADDON_SLUG,
                "name": "Engineering",
                "version": "2.2.0-beta.1",
                "state": "started",
            },
            "target_class": "engineering_addon",
            "process_instance_id": "original-process",
            "runtime": deepcopy(runtime),
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
        self.assertEqual(
            verified["evidence"]["restart_proof"],
            "process_identity",
        )

        runtime["audit_storage_status"] = "unhealthy"
        pending = await gateway.verify_addon_restart(
            ENGINEERING_ADDON_SLUG,
            baseline=baseline,
            provider_response_received=False,
            provider_evidence={},
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn(
            "engineering_runtime", pending["mismatch_fields"]
        )

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
                authoritative_outage_observed=False,
                outage_observation_window_open=True,
                outage_observation_deadline=(
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            )
        self.assertEqual(result["status"], "pending")
        self.assertTrue(
            result["evidence"]["expected_disruption_observed"]
        )

    async def test_late_onset_outage_follows_initial_probe_budget(self):
        started_at = datetime(
            2026, 7, 28, 22, 23, 11, tzinfo=timezone.utc
        )

        class DeterministicDateTime(datetime):
            value = started_at

            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return cls.value.replace(tzinfo=None)
                return cls.value.astimezone(tz)

        class Rest:
            def __init__(self):
                self.available = True
                self.successful_reads = []

            async def request(self, _method, _path):
                if not self.available:
                    raise HomeAssistantUnavailableError()
                self.successful_reads.append(
                    DeterministicDateTime.now(timezone.utc)
                )
                return {
                    "location_name": "Test Home",
                    "version": "2026.7.4",
                }

        class ProviderEvidence:
            server_version = "7.14.2"

            def as_dict(self):
                return {"server_version": self.server_version}

        class Provider:
            async def probe(self, _operation):
                return ProviderEvidence()

        runtime = {
            "server_version": "2.2.0-beta.1",
            "build_sha": "a" * 40,
            "registered_tool_count": 74,
            "engineering_tool_count": 48,
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
        rest = Rest()
        gateway = OperationalLifecycleGateway(
            Provider(),
            rest,
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: deepcopy(runtime),
            process_instance_id="process",
        )

        async def valid_configuration():
            return {"status": "valid"}

        async def advance_clock(seconds):
            DeterministicDateTime.value += timedelta(seconds=seconds)

        gateway.configuration_validation = valid_configuration
        baseline = {
            "home_assistant": {
                "location_name": "Test Home",
                "version": "2026.7.4",
            },
            "runtime": deepcopy(runtime),
        }
        deadline = started_at + timedelta(
            seconds=RESTART_OUTAGE_ELIGIBILITY_WINDOW_SECONDS
        )
        with patch(
            "ha_mcp_engineering.governance.operational_lifecycle.datetime",
            DeterministicDateTime,
        ), patch(
            "ha_mcp_engineering.governance.operational_lifecycle."
            "asyncio.sleep",
            advance_clock,
        ):
            initial = await gateway.verify_home_assistant_restart(
                baseline=baseline,
                restart_dispatch_confirmed=True,
                authoritative_outage_observed=False,
                outage_observation_window_open=True,
                outage_observation_deadline=deadline.isoformat(),
            )
            self.assertEqual(initial["status"], "pending")
            self.assertEqual(
                len(rest.successful_reads),
                RESTART_DISRUPTION_PROBE_ATTEMPTS + 1,
            )
            initial_probe_reads = rest.successful_reads[
                :RESTART_DISRUPTION_PROBE_ATTEMPTS
            ]
            self.assertEqual(
                len(initial_probe_reads),
                RESTART_DISRUPTION_PROBE_ATTEMPTS,
            )
            self.assertEqual(initial_probe_reads[0], started_at)
            self.assertEqual(
                initial_probe_reads[-1],
                started_at
                + timedelta(
                    seconds=RESTART_DISRUPTION_PROBE_ATTEMPTS - 1
                ),
            )
            self.assertFalse(initial["evidence"]["outage_observed"])

            DeterministicDateTime.value = started_at + timedelta(
                seconds=60
            )
            rest.available = False
            unavailable = await gateway.verify_home_assistant_restart(
                baseline=baseline,
                restart_dispatch_confirmed=True,
                authoritative_outage_observed=False,
                outage_observation_window_open=True,
                outage_observation_deadline=deadline.isoformat(),
            )
            self.assertTrue(unavailable["evidence"]["outage_observed"])
            self.assertEqual(
                unavailable["evidence"]["outage_observed_at"],
                DeterministicDateTime.value.isoformat(),
            )

            DeterministicDateTime.value = deadline + timedelta(seconds=1)
            rest.available = True
            recovered = await gateway.verify_home_assistant_restart(
                baseline=baseline,
                restart_dispatch_confirmed=True,
                authoritative_outage_observed=True,
                outage_observation_window_open=False,
                outage_observation_deadline=deadline.isoformat(),
            )
            self.assertEqual(recovered["status"], "verified")
            self.assertEqual(
                recovered["evidence"]["reconnected_at"],
                DeterministicDateTime.value.isoformat(),
            )

    async def test_core_proxy_unavailability_is_restart_disruption(self):
        class Rest:
            async def request(self, _method, _path):
                raise HomeAssistantApiError(details={"status": 503})

        gateway = OperationalLifecycleGateway(
            SimpleNamespace(),
            Rest(),
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        result = await gateway.verify_home_assistant_restart(
            baseline={},
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=False,
            outage_observation_window_open=True,
            outage_observation_deadline=(
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        )

        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["evidence"]["outage_observed"])
        self.assertEqual(
            result["evidence"]["failure_category"],
            "provider_unavailable",
        )
        self.assertEqual(
            result["evidence"]["restart_evidence_sources"],
            ["home_assistant_core_connection_probe"],
        )

    async def test_core_api_error_is_not_restart_disruption(self):
        class Rest:
            async def request(self, _method, _path):
                raise HomeAssistantApiError(details={"status": 500})

        gateway = OperationalLifecycleGateway(
            SimpleNamespace(),
            Rest(),
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        result = await gateway.verify_home_assistant_restart(
            baseline={},
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=False,
            outage_observation_window_open=True,
            outage_observation_deadline=(
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(
            result["evidence"]["failure_category"], "provider_error"
        )
        self.assertNotIn("outage_observed", result["evidence"])

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
            "server_version": "2.2.0-beta.1",
            "build_sha": "a" * 40,
            "registered_tool_count": 74,
            "engineering_tool_count": 48,
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
            authoritative_outage_observed=True,
            outage_observation_window_open=False,
            outage_observation_deadline=None,
        )
        self.assertEqual(verified["status"], "verified")

        runtime["dependency_prewarm_state"] = "building"
        pending = await gateway.verify_home_assistant_restart(
            baseline=baseline,
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=True,
            outage_observation_window_open=False,
            outage_observation_deadline=None,
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
            authoritative_outage_observed=True,
            outage_observation_window_open=False,
            outage_observation_deadline=None,
        )
        self.assertEqual(pending["status"], "pending")
        self.assertIn("engineering_runtime", pending["mismatch_fields"])

    async def test_upstream_failure_is_not_home_assistant_outage_evidence(
        self,
    ):
        class Provider:
            async def probe(self, _operation):
                raise OperationalLifecycleProviderError(
                    "provider_unavailable", dispatched=False
                )

        class Rest:
            async def request(self, _method, _path):
                return {
                    "location_name": "Test Home",
                    "version": "2026.7.4",
                }

        gateway = OperationalLifecycleGateway(
            Provider(),
            Rest(),
            None,
            configuration_validator=lambda: None,
            runtime_snapshot=lambda: {},
            process_instance_id="process",
        )
        with patch(
            "ha_mcp_engineering.governance.operational_lifecycle."
            "RESTART_DISRUPTION_PROBE_ATTEMPTS",
            1,
        ):
            result = await gateway.verify_home_assistant_restart(
                baseline={},
                restart_dispatch_confirmed=True,
                authoritative_outage_observed=False,
                outage_observation_window_open=True,
                outage_observation_deadline=(
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(
            result["mismatch_fields"], ["upstream_admission"]
        )
        self.assertNotIn("outage_observed", result["evidence"])
        self.assertNotIn(
            "home_assistant_core_unavailable", result["evidence"]
        )

        recovered = await gateway.verify_home_assistant_restart(
            baseline={},
            restart_dispatch_confirmed=True,
            authoritative_outage_observed=True,
            outage_observation_window_open=False,
            outage_observation_deadline=None,
        )
        self.assertEqual(recovered["status"], "pending")
        self.assertTrue(
            recovered["evidence"]["home_assistant_reconnected"]
        )
        self.assertIsNotNone(
            datetime.fromisoformat(
                recovered["evidence"]["reconnected_at"]
            ).tzinfo
        )

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
        self_identity = SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version="2.1.1-beta.2",
            repository="df26dea6",
        )
        self.assertEqual(
            _addon_target_class(
                self_identity.slug,
                {
                    "slug": self_identity.slug,
                    "name": self_identity.name,
                    "version": self_identity.version,
                    "repository": self_identity.repository,
                },
                self_identity,
                upstream_addon_identity(),
            ),
            "engineering_addon",
        )
        self.assertEqual(
            _addon_target_class(
                UPSTREAM_ADDON_SLUG,
                {
                    "slug": UPSTREAM_ADDON_SLUG,
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "repository": "abcdef12",
                },
                self_identity,
                upstream_addon_identity(),
            ),
            "upstream_ha_mcp_addon",
        )
        self.assertEqual(
            _addon_target_class(
                "lookalike_ha_mcp",
                {
                    "slug": "lookalike_ha_mcp",
                    "name": UPSTREAM_ADDON_NAME,
                    "version": "7.14.2",
                    "repository": "lookalike",
                },
                self_identity,
                upstream_addon_identity(),
            ),
            "other_addon",
        )
        with self.assertRaises(LifecycleGatewayError) as raised:
            _addon_target_class(
                "conflicting_copy",
                {
                    "slug": "conflicting_copy",
                    "name": self_identity.name,
                    "version": self_identity.version,
                    "repository": self_identity.repository,
                },
                self_identity,
                upstream_addon_identity(),
            )
        self.assertEqual(
            raised.exception.category,
            "self_addon_identity_unavailable",
        )
        for status in ("unavailable", "ambiguous", "conflicting"):
            with self.subTest(status=status), self.assertRaises(
                LifecycleGatewayError
            ) as raised:
                _addon_target_class(
                    "unrelated",
                    {
                        "slug": "unrelated",
                        "name": "Unrelated",
                        "version": "1.0.0",
                    },
                    self_identity,
                    upstream_addon_identity(status=status),
                )
            self.assertEqual(
                raised.exception.category,
                "upstream_addon_identity_unavailable",
            )

    def test_public_tool_schemas_are_bounded_and_catalog_is_48(self):
        tools = registered_tools(get_registered_server())
        self.assertEqual(len(tools), 48)
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
        self.assertEqual(
            set(tools["get_execution_task"].parameters["properties"]),
            {"task_id"},
        )
        self.assertEqual(
            set(tools["list_execution_tasks"].parameters["properties"]),
            {"state", "terminal_outcome", "plan_id", "limit"},
        )
        self.assertEqual(
            set(tools["cancel_execution_task"].parameters["properties"]),
            {"task_id"},
        )
        for name in (
            "ha_reload_core",
            "ha_manage_addon",
            "ha_restart",
        ):
            self.assertNotIn(name, tools)
