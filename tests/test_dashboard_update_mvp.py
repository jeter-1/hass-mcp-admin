"""End-to-end acceptance for the bounded existing-dashboard update MVP."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard_support import make_preread  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
    McpDashboardRead,
    validate_dashboard_write_arguments,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    DashboardProviderError,
    ErrorCode,
    GovernanceError,
)
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    ArtifactStorageError,
    RawEvidenceError,
)
from ha_mcp_engineering.f3_dashboard.gateway import (  # noqa: E402
    DashboardExecutionGateway,
)
from ha_mcp_engineering.f3_dashboard.json_codec import (  # noqa: E402
    upstream_config_hash,
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
    def __init__(self, *, version: str = "8.4.1") -> None:
        self.version = version
        self.configuration = {"title": "Before", "views": []}
        self.preread_count = 0
        self.best_practice_count = 0
        self.write_count = 0
        self.fail_after_write = False
        self.fail_before_write = False
        self.structured_rejection = False
        self.last_write: dict[str, object] | None = None
        self.best_practice_authority_hash: str | None = None

    async def preread(self, *, url_path: str):
        self.preread_count += 1
        return make_preread(
            deepcopy(self.configuration),
            url_path=url_path,
            version=self.version,
        )

    async def best_practice_key(
        self, *, expected_provider_authority_evidence_hash: str
    ) -> str:
        self.best_practice_count += 1
        self.best_practice_authority_hash = (
            expected_provider_authority_evidence_hash
        )
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
    def __init__(
        self, *, version: str = "8.1.1", omit_tool: str | None = None
    ) -> None:
        capture = json.loads(
            (
                ROOT
                / "docs"
                / "evidence"
                / "upstream-read-compatibility"
                / f"ha-mcp-{version}.json"
            ).read_text(encoding="utf-8")
        )
        review_path = (
            ROOT
            / "docs"
            / "evidence"
            / "upstream-read-compatibility"
            / f"ha-mcp-{version}-contract-review.json"
        )
        if review_path.exists():
            review = json.loads(review_path.read_text(encoding="utf-8"))
            runtime_order = review.get("runtime_catalog", {}).get(
                "runtime_tool_order"
            )
            if isinstance(runtime_order, list):
                captured_by_name = {
                    tool["name"]: tool for tool in capture["tools"]
                }
                capture["tools"] = [
                    captured_by_name[name] for name in runtime_order
                ]
        tools = [
            tool for tool in capture["tools"]
            if tool.get("name") != omit_tool
        ]
        self.handshake = McpDashboardHandshake(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version=version,
            tools=tuple(tools),
            connection_latency_ms=1.0,
        )
        self.guide_count = 0
        self.write_count = 0
        self.read_count = 0
        self.configuration = {"title": "Before", "views": []}
        self.write_is_error = False
        self.write_payload = {
            "success": True,
            "action": "update",
            "url_path": "operations",
            "dashboard_created": False,
            "config_updated": True,
            "metadata_updated": False,
        }

    async def execute_dashboard_read(self, arguments, validator):
        validator(self.handshake)
        self.read_count += 1
        if arguments.get("list_only") is True:
            payload = {
                "success": True,
                "action": "list",
                "dashboards": [
                    {
                        "id": "operations",
                        "url_path": "operations",
                        "mode": "storage",
                        "title": "Operations",
                    }
                ],
                "count": 1,
            }
        else:
            payload = {
                "success": True,
                "action": "get",
                "url_path": arguments["url_path"],
                "config": deepcopy(self.configuration),
                "config_hash": upstream_config_hash(self.configuration),
            }
        return McpDashboardRead(
            self.handshake,
            {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": False,
            },
            tool_call_latency_ms=1.0,
        )

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
        self.configuration = deepcopy(arguments["config"])
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


async def _unrelated_lifecycle_provider_identity():
    raise AssertionError(
        "dashboard authority must not be reconstructed from lifecycle health"
    )


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
    @staticmethod
    def _authority_hash(provider, transport) -> str:
        return provider._dashboard_provider_authority(transport.handshake)[
            "evidence_hash"
        ]

    async def test_exact_8_4_1_inventory_configuration_and_authority_succeed(self):
        transport = _ExactProviderTransport(version="8.4.1")
        provider = UpstreamDashboardProvider()
        provider.configure(
            Settings(
                ha_url="http://supervisor/core",
                ha_token="synthetic-dashboard-token",
                access_secret="synthetic-dashboard-access-secret",
                port=0,
                audit_path="synthetic-dashboard-audit.jsonl",
                rate_limit_per_minute=120,
                rate_limit_burst=25,
                destructive_services=frozenset(),
                upstream_dashboard_mcp_url=(
                    "http://127.0.0.1:18086/synthetic-upstream-secret/mcp"
                ),
            ),
            transport=transport,
        )
        gateway = DashboardExecutionGateway(provider, response_limit=60_000)

        preread = await gateway.preread(url_path="operations")

        self.assertEqual(preread.upstream_version, "8.4.1")
        self.assertEqual(preread.compatibility_entry, "ha-mcp-v8.4.1-7823b365")
        self.assertEqual(preread.operational_identity.target_url_path, "operations")
        self.assertEqual(
            preread.operational_identity.authority.provider_slug,
            "ha_mcp",
        )
        self.assertEqual(
            preread.operational_identity.authority.source_commit,
            "701a7c26ac0e2309c7883a627d31873ab1510077",
        )
        self.assertEqual(transport.read_count, 2)
        self.assertEqual(transport.write_count, 0)

    async def test_getter_without_setter_is_readable_but_not_plannable(self):
        transport = _ExactProviderTransport(
            version="8.4.1", omit_tool="ha_config_set_dashboard"
        )
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        inventory = await provider.list_dashboards(
            limit=5, response_limit=60_000
        )
        self.assertEqual(inventory.completeness, "complete")
        self.assertIsNone(
            inventory.provider_authority
        )

        gateway = DashboardExecutionGateway(provider, response_limit=60_000)
        with self.assertRaises(RawEvidenceError):
            await gateway.preread(url_path="operations")

        self.assertEqual(transport.read_count, 3)
        self.assertEqual(transport.write_count, 0)

    async def test_setter_without_getter_fails_before_provider_call(self):
        transport = _ExactProviderTransport(
            version="8.4.1", omit_tool="ha_config_get_dashboard"
        )
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        with self.assertRaises(DashboardProviderError):
            await provider.list_dashboards(limit=5, response_limit=60_000)

        self.assertEqual(transport.read_count, 0)
        self.assertEqual(transport.write_count, 0)

    async def test_exact_8_4_1_setter_is_binary_owned_and_callable_once(self):
        transport = _ExactProviderTransport(version="8.4.1")
        provider = UpstreamDashboardProvider()
        provider._transport = transport

        key = await provider.best_practices_acknowledgement_key()
        result = await provider.execute_governed_dashboard_update(
            url_path="operations",
            configuration={"title": "After", "views": []},
            config_hash=upstream_config_hash(transport.configuration),
            best_practice_key=key,
            expected_provider_authority_evidence_hash=self._authority_hash(
                provider, transport
            ),
        )

        self.assertEqual(transport.guide_count, 1)
        self.assertEqual(transport.write_count, 1)
        self.assertEqual(result["provider_operation"], "ha_config_set_dashboard")
        self.assertFalse(result["fallback_occurred"])
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
            expected_provider_authority_evidence_hash=self._authority_hash(
                provider, transport
            ),
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
                expected_provider_authority_evidence_hash=self._authority_hash(
                    provider, transport
                ),
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
                expected_provider_authority_evidence_hash=self._authority_hash(
                    provider, transport
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

    async def test_reviewed_handshake_swap_is_rejected_before_setter_call(self):
        transport = _ExactProviderTransport(version="8.4.1")
        provider = UpstreamDashboardProvider()
        provider._transport = transport
        gateway = DashboardExecutionGateway(provider, response_limit=60_000)
        preflight = await gateway.preread(url_path="operations")
        authority_hash = preflight.operational_identity.authority.evidence_hash
        key = await gateway.best_practice_key(
            expected_provider_authority_evidence_hash=authority_hash
        )

        transport.handshake = _ExactProviderTransport(
            version="8.2.0"
        ).handshake
        with self.assertRaises(DashboardProviderError) as caught:
            await gateway.write(
                url_path="operations",
                configuration={"title": "After", "views": []},
                config_hash=preflight.config_hash,
                best_practice_key=key,
                expected_provider_authority_evidence_hash=authority_hash,
            )

        self.assertEqual(transport.write_count, 0)
        self.assertFalse(caught.exception.details["upstream_dispatch_occurred"])
        self.assertEqual(
            caught.exception.code,
            ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
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
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
        )
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=_unrelated_lifecycle_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def _plan(self, *, proposed_title: str = "After"):
        return await self.service.create_dashboard_update_plan(
            title="Rename operations dashboard",
            description="Bounded existing-dashboard update.",
            url_path="main-operations",
            patch_operations=[
                {
                    "operation_id": "rename",
                    "operation": "replace",
                    "path": "/title",
                    "value": proposed_title,
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
        projection = review["dashboard_review"]["approval_projection"]
        self.assertTrue(projection["complete"])
        self.assertEqual(projection["operation_count"], 1)
        self.assertEqual(
            projection["operations"][0]["proposed"]["state"], "value"
        )
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
        self.assertNotEqual(granted["status"], "approval_pending")
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
                "expected_provider_authority_evidence_hash",
            },
        )
        self.assertEqual(
            self.dashboard.best_practice_authority_hash,
            self.dashboard.last_write[
                "expected_provider_authority_evidence_hash"
            ],
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

    async def test_exact_8_2_0_plan_uses_dashboard_not_lifecycle_authority(self):
        self.dashboard.version = "8.2.0"

        created = await self._plan()

        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(
            created["approval_lifecycle"], "approval_not_requested"
        )
        self.assertEqual(self.dashboard.preread_count, 1)
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_disclosed_consequence_classes_remain_owner_actionable(self):
        cases = (
            (
                "high",
                "service_or_action_invocation",
                {
                    "type": "button",
                    "entity": "light.synthetic_vanity",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "light.turn_off",
                        "target": {
                            "entity_id": "light.synthetic_vanity"
                        },
                    },
                },
            ),
            (
                "critical",
                "destructive_administrative_action",
                {
                    "type": "button",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "homeassistant.restart",
                    },
                },
            ),
            (
                "safety_critical",
                "high_consequence_action",
                {
                    "type": "button",
                    "entity": "lock.synthetic_entry",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "lock.unlock",
                        "target": {
                            "entity_id": "lock.synthetic_entry"
                        },
                    },
                },
            ),
            (
                "unknown",
                "unknown_action_semantics",
                {
                    "type": "button",
                    "tap_action": {"action": "assist"},
                },
            ),
            (
                "incompletely_analyzed",
                "templated_or_conditional_action",
                {
                    "type": "button",
                    "entity": "light.synthetic_vanity",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "light.turn_on",
                        "data": {"brightness": "{{ synthetic_level }}"},
                    },
                },
            ),
        )

        for consequence_class, expected_category, card in cases:
            with self.subTest(consequence_class=consequence_class):
                self.dashboard.configuration = {
                    "title": "Before",
                    "views": [{"title": "Main", "cards": []}],
                }
                writes_before = self.dashboard.write_count
                created = await self.service.create_dashboard_update_plan(
                    title=f"Add {consequence_class} dashboard action",
                    description="A disclosed exact configuration change.",
                    url_path="main-operations",
                    patch_operations=[
                        {
                            "operation_id": "append-action-card",
                            "operation": "add",
                            "path": "/views/0/cards/-",
                            "value": card,
                        }
                    ],
                    expiration_minutes=30,
                )

                stored = self.repository.get(created["plan_id"])
                self.assertIsNotNone(stored)
                risk = stored.proposed_config["dashboard_update"]["risk"]
                changed_categories = {
                    finding["category"]
                    for finding in risk["findings"]
                    if finding["introduced_or_changed"]
                }
                self.assertIn(expected_category, changed_categories)
                self.assertEqual(
                    created["policy_decision"]["policy_class"],
                    "elevated_admin",
                )
                self.assertEqual(
                    created["policy_decision"]["required_acknowledgements"],
                    ["plan_approval"],
                )
                self.assertTrue(created["approval_actionable"])
                granted = await self._approve(created)
                self.assertEqual(
                    granted["approval_bundle_state"], "fully_approved"
                )
                self.assertEqual(self.dashboard.write_count, writes_before)

                result = await self.service.apply(
                    created["plan_id"], created["plan_hash"]
                )
                self.assertEqual(
                    result["task_state"], "succeeded_verified"
                )
                self.assertEqual(
                    self.dashboard.write_count, writes_before + 1
                )

    async def test_concurrent_duplicate_apply_commits_at_most_once(self):
        created = await self._plan()
        await self._approve(created)

        results = await asyncio.gather(
            self.service.apply(created["plan_id"], created["plan_hash"]),
            self.service.apply(created["plan_id"], created["plan_hash"]),
            return_exceptions=True,
        )

        self.assertEqual(self.dashboard.write_count, 1)
        self.assertTrue(
            any(
                isinstance(item, dict)
                and item.get("task_state") == "succeeded_verified"
                for item in results
            )
        )

    async def test_second_plan_restores_original_configuration_exactly(self):
        original = deepcopy(self.dashboard.configuration)
        original_hash = upstream_config_hash(original)

        changed = await self._plan()
        await self._approve(changed)
        changed_result = await self.service.apply(
            changed["plan_id"], changed["plan_hash"]
        )
        self.assertEqual(changed_result["task_state"], "succeeded_verified")

        restored = await self._plan(proposed_title="Before")
        await self._approve(restored)
        restored_result = await self.service.apply(
            restored["plan_id"], restored["plan_hash"]
        )

        self.assertEqual(restored_result["task_state"], "succeeded_verified")
        self.assertEqual(self.dashboard.write_count, 2)
        self.assertEqual(self.dashboard.configuration, original)
        self.assertEqual(
            upstream_config_hash(self.dashboard.configuration), original_hash
        )

    async def test_stale_dashboard_fails_before_dispatch(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.configuration["title"] = "External edit"

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_provider_authority_drift_fails_before_dispatch(self):
        created = await self._plan()
        await self._approve(created)
        self.dashboard.version = "8.2.0"

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.dashboard.write_count, 0)

    async def test_ha_mcp_8_1_1_hyphenless_target_fails_during_planning(self):
        self.dashboard.version = "8.1.1"
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
