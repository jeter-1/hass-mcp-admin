"""End-to-end acceptance for the bounded existing-dashboard update MVP."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard_support import (  # noqa: E402
    home_dashboard_patch_operations,
    load_home_dashboard,
    make_preread,
)
from ha_mcp_engineering.approval_web import _render_review  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
    McpDashboardRead,
    validate_dashboard_write_arguments,
)
from ha_mcp_engineering.errors import (  # noqa: E402
    DashboardProviderError,
    ErrorCode,
    GovernanceError,
)
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    ArtifactStorageError,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)


class _UnusedConfigurationGateway(ConfigurationResourceGateway):
    def __init__(self):
        self.rest_client = object()
        self.websocket_client = object()

    async def get(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def read(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def validate_all(self):
        raise AssertionError("configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("configuration gateway must not be used")


class _DashboardGateway:
    def __init__(self) -> None:
        self.configuration = {"title": "Before", "views": []}
        self.version = "8.1.1"
        self.preread_count = 0
        self.best_practice_count = 0
        self.write_count = 0
        self.fail_after_write = False
        self.fail_before_write = False
        self.structured_rejection = False
        self.mismatched_write = False
        self.last_write: dict[str, object] | None = None

    async def preread(self, *, url_path: str):
        self.preread_count += 1
        return make_preread(
            deepcopy(self.configuration),
            url_path=url_path,
            version=self.version,
        )

    async def best_practice_key(self) -> str:
        self.best_practice_count += 1
        return "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd"

    async def write(self, **arguments):
        self.write_count += 1
        self.last_write = deepcopy(arguments)
        if self.structured_rejection:
            raise DashboardProviderError(
                ErrorCode.UPSTREAM_DASHBOARD_UPSTREAM_ERROR,
                details={
                    "provider": "upstream_dashboard",
                    "failure_category": "upstream_error",
                    "provider_failure_kind": "structured_provider_rejection",
                    "provider_response_received": True,
                    "upstream_dispatch_occurred": True,
                    "upstream_error_code": "VALIDATION_INVALID_PARAMETER",
                    "upstream_action": "set",
                },
            )
        if self.fail_before_write:
            raise RuntimeError("synthetic pre-provider response failure")
        self.configuration = deepcopy(arguments["configuration"])
        if self.mismatched_write:
            self.configuration["title"] = "Unexpected provider result"
        if self.fail_after_write:
            raise RuntimeError("synthetic lost provider response")
        return {
            "provider": "upstream_dashboard",
            "provider_operation": "ha_config_set_dashboard",
            "provider_response_received": True,
            "success_claimed": True,
            "update_claimed": True,
            "target": arguments["url_path"],
            "fallback_occurred": False,
            "non_atomic": True,
        }


class _ExactProviderTransport:
    def __init__(self, *, omit_tool: str | None = None) -> None:
        capture = json.loads(
            (
                ROOT
                / "docs"
                / "evidence"
                / "upstream-read-compatibility"
                / "ha-mcp-8.1.1.json"
            ).read_text(encoding="utf-8")
        )
        tools = [
            tool for tool in capture["tools"]
            if tool.get("name") != omit_tool
        ]
        self.handshake = McpDashboardHandshake(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version="8.1.1",
            tools=tuple(tools),
            connection_latency_ms=1.0,
        )
        self.guide_count = 0
        self.write_count = 0
        self.write_is_error = False
        self.write_payload = {
            "success": True,
            "action": "update",
            "url_path": "operations",
            "dashboard_created": False,
            "config_updated": True,
            "metadata_updated": False,
        }

    async def execute_best_practices_read(self, validator):
        validator(self.handshake)
        self.guide_count += 1
        return McpDashboardRead(
            self.handshake,
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "# Dashboard guide\n"
                            "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd"
                        ),
                    }
                ],
                "isError": False,
            },
            tool_call_latency_ms=1.0,
        )

    async def execute_dashboard_write(self, arguments, validator):
        validator(self.handshake)
        self.write_count += 1
        self.arguments = deepcopy(arguments)
        return McpDashboardRead(
            self.handshake,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(self.write_payload),
                    }
                ],
                "isError": self.write_is_error,
            },
            tool_call_latency_ms=1.0,
        )


async def _provider_identity():
    return {
        "slug": "ha-mcp",
        "evidence_hash": "a" * 64,
    }


class DashboardWriteArgumentTests(unittest.TestCase):
    def test_only_exact_full_result_arguments_are_accepted(self):
        valid = {
            "url_path": "operations",
            "config": {"title": "After", "views": []},
            "config_hash": "0" * 16,
            "MandatoryBPS": False,
            "return_screenshot": False,
            "BestPracticeKey": (
                "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd"
            ),
        }
        validate_dashboard_write_arguments(valid)
        invalid = (
            {**valid, "python_transform": "config.clear()"},
            {**valid, "return_screenshot": True},
            {**valid, "MandatoryBPS": True},
            {**valid, "config_hash": "wrong"},
            {**valid, "BestPracticeKey": "wrong"},
            {key: value for key, value in valid.items() if key != "config"},
        )
        for candidate in invalid:
            with self.subTest(candidate=set(candidate)), self.assertRaises(
                DashboardTransportError
            ):
                validate_dashboard_write_arguments(candidate)


class DashboardWriteProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_catalog_guide_and_setter_contract_succeed_once(self):
        transport = _ExactProviderTransport()
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        key = await provider.best_practices_acknowledgement_key()
        result = await provider.execute_governed_dashboard_update(
            url_path="operations",
            configuration={"title": "After", "views": []},
            config_hash="0" * 16,
            best_practice_key=key,
        )

        self.assertEqual(transport.guide_count, 1)
        self.assertEqual(transport.write_count, 1)
        self.assertEqual(result["provider"], "upstream_dashboard")
        self.assertEqual(
            result["provider_operation"], "ha_config_set_dashboard"
        )
        self.assertFalse(result["fallback_occurred"])
        self.assertFalse(transport.arguments["return_screenshot"])
        self.assertNotIn("python_transform", transport.arguments)

    async def test_catalog_mismatch_fails_before_either_tool_dispatch(self):
        transport = _ExactProviderTransport(
            omit_tool="ha_config_set_dashboard"
        )
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        with self.assertRaises(DashboardProviderError):
            await provider.best_practices_acknowledgement_key()

        self.assertEqual(transport.guide_count, 0)
        self.assertEqual(transport.write_count, 0)

    async def test_invalid_setter_response_is_failure_without_retry(self):
        transport = _ExactProviderTransport()
        transport.write_payload["config_updated"] = False
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        key = await provider.best_practices_acknowledgement_key()
        with self.assertRaises(DashboardProviderError):
            await provider.execute_governed_dashboard_update(
                url_path="operations",
                configuration={"title": "After", "views": []},
                config_hash="0" * 16,
                best_practice_key=key,
            )

        self.assertEqual(transport.write_count, 1)

    async def test_structured_setter_rejection_preserves_bounded_evidence(self):
        transport = _ExactProviderTransport()
        transport.write_is_error = True
        transport.write_payload = {
            "success": False,
            "action": "set",
            "url_path": "map",
            "error": {
                "code": "VALIDATION_INVALID_PARAMETER",
                "message": "synthetic rejected payload must not be retained",
            },
        }
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        with self.assertRaises(DashboardProviderError) as caught:
            await provider.execute_governed_dashboard_update(
                url_path="map",
                configuration={"title": "After", "views": []},
                config_hash="0" * 16,
                best_practice_key=(
                    "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd"
                ),
            )

        self.assertEqual(transport.write_count, 1)
        self.assertTrue(caught.exception.details["provider_response_received"])
        self.assertEqual(
            caught.exception.details["provider_failure_kind"],
            "structured_provider_rejection",
        )
        self.assertEqual(
            caught.exception.details["upstream_error_code"],
            "VALIDATION_INVALID_PARAMETER",
        )
        self.assertEqual(caught.exception.details["upstream_action"], "set")
        self.assertNotIn(
            "synthetic rejected payload",
            json.dumps(caught.exception.details),
        )


class DashboardUpdateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dashboard = _DashboardGateway()
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            _UnusedConfigurationGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "test-access-secret"),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_provider_identity,
        )
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def _plan(self):
        return await self.service.create_dashboard_update_plan(
            title="Rename operations dashboard",
            description="Bounded existing-dashboard update.",
            url_path="main-operations",
            patch_operations=[
                {
                    "operation_id": "rename",
                    "operation": "replace",
                    "path": "/title",
                    "value": "After",
                }
            ],
            expiration_minutes=30,
        )

    async def _approve(self, created):
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        self.assertEqual(review["operation"], "update_dashboard")
        self.assertTrue(review["operational_review"]["provider_arguments"]["non_atomic"])
        self.assertIn("dashboard_review", review)
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:test-reviewer",
        )
        if granted["status"] == "approval_pending":
            _, csrf = await self.service.issue_external_csrf(
                created["plan_id"], granted["challenge_id"]
            )
            granted = await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=granted["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=granted["approval_kind"],
                approval_action=granted["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:test-reviewer"
                ),
            )
        return granted

    def _execution_evidence(self, task_id: str):
        declarations = self.runtime.children.declarations_for_task(task_id)
        return [
            self.runtime.children.get(item["child_id"]).to_dict()
            for item in declarations
        ]

    async def test_approved_update_dispatches_once_and_exactly_verifies(self):
        created = await self._plan()
        self.assertNotIn("proposed_config", created)
        self.assertNotIn("current_config", created)
        self.assertEqual(
            created["dry_run_results"]["semantic_diff"]["entries"][0][
                "proposed"
            ]["preview"],
            "After",
        )
        await self._approve(created)

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(
            result["task_state"],
            "succeeded_verified",
            self._execution_evidence(result["task_id"]),
        )
        self.assertEqual(self.dashboard.write_count, 1)
        self.assertEqual(self.dashboard.configuration["title"], "After")
        self.assertEqual(
            set(self.dashboard.last_write),
            {
                "url_path",
                "configuration",
                "config_hash",
                "best_practice_key",
            },
        )
        self.assertNotIn(
            "I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-0123abcd",
            (self.root / "audit.jsonl").read_text(encoding="utf-8"),
        )
        second = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(second["task_id"], result["task_id"])
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_map_title_canary_remains_completely_reviewable(self):
        self.dashboard.version = "8.2.0"
        self.dashboard.configuration = {"title": "Map", "views": []}
        created = await self.service.create_dashboard_update_plan(
            title="Rename map dashboard",
            description="Previously accepted bounded title canary.",
            url_path="map",
            patch_operations=[
                {
                    "operation_id": "rename-map-title",
                    "operation": "replace",
                    "path": "/title",
                    "value": "Map canary updated",
                }
            ],
            expiration_minutes=30,
        )

        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        projection = review["dashboard_review"]["approval_projection"]
        self.assertTrue(projection["complete"])
        self.assertEqual(projection["operation_count"], 1)
        self.assertEqual(
            projection["operations"][0]["proposed"]["value"],
            "Map canary updated",
        )
        html = _render_review("", review, csrf)
        self.assertIn("Map canary updated", html)
        self.assertIn("Approve exact plan", html)
        self.assertEqual(self.dashboard.write_count, 0)

        await self._approve(created)
        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.dashboard.configuration["title"], "Map canary updated")
        self.assertEqual(self.dashboard.write_count, 1)
        duplicate = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(duplicate["status"], "already_applied")
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_realistic_home_delta_is_approval_eligible_and_complete(self):
        self.dashboard.version = "8.2.0"
        self.dashboard.configuration = load_home_dashboard()
        created = await self.service.create_dashboard_update_plan(
            title="Update Home dashboard status",
            description="Cleaner, Outdoor, and Needs Attention.",
            url_path="home",
            patch_operations=home_dashboard_patch_operations(),
            expiration_minutes=30,
        )

        self.assertNotIn("proposed_config", created)
        self.assertNotIn(
            "sensor.local_outdoor_temperature", json.dumps(created)
        )
        self.assertEqual(
            created["dry_run_results"]["semantic_leaf_change_count"], 51
        )
        self.assertEqual(
            created["proposed_config_hash"],
            "4c3b81d8fff6e2d54754a5e87f90f4972b4e4fb8e8c99e2144d0f9180611e466",
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        projection = review["dashboard_review"]["approval_projection"]
        self.assertEqual(projection["operation_count"], 4)
        html = _render_review("", review, csrf)
        for expected in (
            "Cleaner",
            "Outdoor",
            "sensor.local_outdoor_temperature",
            "Needs Attention",
            "garage_presence",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("<collection preview omitted>", html)
        self.assertIn("Approve exact plan", html)
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_compiler_diagnostics_survive_governance_mapping(self):
        self.dashboard.configuration = {
            "values": {f"item_{index}": False for index in range(257)}
        }
        with self.assertRaises(GovernanceError) as caught:
            await self.service.create_dashboard_update_plan(
                title="Too many semantic leaves",
                description="Structured compiler diagnostic.",
                url_path="main-operations",
                patch_operations=[
                    {
                        "operation_id": "replace-values",
                        "operation": "replace",
                        "path": "/values",
                        "value": {
                            f"item_{index}": True for index in range(257)
                        },
                    }
                ],
                expiration_minutes=30,
            )

        self.assertEqual(
            caught.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
        )
        self.assertEqual(
            caught.exception.details,
            {
                "reason": "dashboard_patch_limit_exceeded",
                "dashboard_error_code": "dashboard_patch_compilation_failed",
                "constraint": "semantic_leaf_changes",
                "observed": 257,
                "limit": 256,
                "stage": "compilation",
            },
        )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_approval_projection_overflow_fails_during_planning(self):
        with patch(
            "ha_mcp_engineering.f3_dashboard.approval_projection."
            "MAX_DASHBOARD_APPROVAL_PROJECTION_BYTES",
            128,
        ), self.assertRaises(GovernanceError) as caught:
            await self._plan()

        self.assertEqual(
            caught.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED
        )
        self.assertEqual(
            caught.exception.details["reason"],
            "approval_projection_too_large",
        )
        self.assertEqual(
            caught.exception.details["constraint"],
            "approval_projection_bytes",
        )
        self.assertGreater(
            caught.exception.details["observed"],
            caught.exception.details["limit"],
        )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_incomplete_dashboard_review_cannot_be_approved(self):
        created = await self._plan()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        plan = self.repository.get(created["plan_id"])
        del plan.proposed_config["dashboard_update"]["approval_projection"]
        self.repository.save(plan)

        self.assertEqual(
            self.service._configuration_projection_error(plan),
            "approval_projection_malformed",
        )

        with self.assertRaises(GovernanceError) as caught:
            await self.service.issue_external_csrf(
                created["plan_id"], pending["challenge_id"]
            )
        self.assertEqual(
            caught.exception.code,
            ErrorCode.POLICY_SNAPSHOT_MISMATCH,
        )
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_stale_dashboard_fails_before_dispatch(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.configuration["title"] = "External edit"

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.dashboard.write_count, 0)
        diagnostics = {
            code
            for child in self._execution_evidence(result["task_id"])
            for event in child["events"]
            for code in event["diagnostic_codes"]
        }
        self.assertIn("stale_or_provider_contract_mismatch", diagnostics)

    async def test_ha_mcp_8_1_1_hyphenless_target_fails_during_planning(self):
        with self.assertRaises(GovernanceError) as caught:
            await self.service.create_dashboard_update_plan(
                title="Rename map dashboard",
                description="Known 8.1.1 compatibility rejection.",
                url_path="map",
                patch_operations=[
                    {
                        "operation_id": "rename",
                        "operation": "replace",
                        "path": "/title",
                        "value": "After",
                    }
                ],
                expiration_minutes=30,
            )

        self.assertEqual(
            caught.exception.details["reason"],
            "dashboard_write_existing_hyphenless_path_incompatible",
        )
        self.assertEqual(self.dashboard.best_practice_count, 0)
        self.assertEqual(self.dashboard.write_count, 0)
        self.assertEqual(self.repository.list(), [])

    async def test_lost_response_is_read_back_without_redispatch(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.fail_after_write = True

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.dashboard.write_count, 1)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_failed_setter_is_truthful_and_never_retried(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.fail_before_write = True

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertIn(
            result["task_state"],
            {"failed_post_dispatch", "manual_review_required"},
        )
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_mismatched_readback_is_truthful_and_never_redispatched(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.mismatched_write = True

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(
            result["execution_task"]["terminal_outcome"],
            "failed_post_dispatch",
        )
        self.assertEqual(self.dashboard.write_count, 1)
        with self.assertRaises(GovernanceError):
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_structured_rejection_and_unchanged_reread_is_not_mismatch(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.structured_rejection = True

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(self.dashboard.write_count, 1)
        self.assertEqual(self.dashboard.configuration["title"], "Before")
        evidence = self._execution_evidence(result["task_id"])
        self.assertEqual(len(evidence), 1)
        child = evidence[0]
        self.assertTrue(child["provider_response_received"])
        self.assertEqual(child["normalized_outcome"], "failed_post_dispatch")
        diagnostics = {
            code
            for event in child["events"]
            for code in event["diagnostic_codes"]
        }
        self.assertIn("structured_provider_rejection_received", diagnostics)
        self.assertIn("provider_rejection_confirmed_no_change", diagnostics)
        self.assertIn(
            "upstream_error_validation_invalid_parameter", diagnostics
        )
        self.assertNotIn("exact_readback_mismatch", diagnostics)
        self.assertNotEqual(child["normalized_outcome"], "verification_mismatch")

        with self.assertRaises(GovernanceError):
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(self.dashboard.write_count, 1)
        with self.assertRaises(GovernanceError):
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(self.dashboard.write_count, 1)

    async def test_missing_or_tampered_private_artifact_cannot_dispatch(self):
        for mode in ("missing", "tampered"):
            with self.subTest(mode=mode):
                created = await self._plan()
                await self._approve(created)
                artifact = (
                    self.service.dashboard_artifacts.root
                    / f"{created['plan_id']}.json"
                )
                if mode == "missing":
                    artifact.unlink()
                else:
                    artifact.write_text("{}", encoding="utf-8")
                with self.assertRaises(ArtifactStorageError):
                    await self.service.apply(
                        created["plan_id"], created["plan_hash"]
                    )
                self.assertEqual(self.dashboard.write_count, 0)


if __name__ == "__main__":
    unittest.main()
