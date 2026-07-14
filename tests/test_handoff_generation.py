import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES, CAPABILITY_PROVIDER_MATRIX, PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.errors import GovernanceError, InvalidRequestError  # noqa: E402
from ha_mcp_engineering.handoff.models import (  # noqa: E402
    HandoffEvidenceBundle, HandoffEvidenceReference, HandoffItem,
)
from ha_mcp_engineering.handoff.provider import EngineeringHandoffProvider  # noqa: E402
from ha_mcp_engineering.handoff.service import HandoffGenerationService  # noqa: E402
from ha_mcp_engineering.incident.models import IncidentSourceCoverage  # noqa: E402
from ha_mcp_engineering.observability import METRICS, RuntimeMetrics  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability, ProviderCompleteness, ProviderCoverage, ProviderResult,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES, CapabilityRoute, routing_for_tool,
)
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402


class FakeIndex:
    def __init__(self):
        self.identity = {"valid": True, "generation": 7, "fingerprint": "idx-7"}

    def health(self):
        return dict(self.identity)


class FakeProvider:
    provider_id = "engineering"

    def __init__(self, *, item_count=5, partial=False, actual_failure=False):
        self.index = FakeIndex()
        self.calls = 0
        self.item_count = item_count
        self.partial = partial
        self.actual_failure = actual_failure

    async def fetch(self, request):
        self.calls += 1
        evidence = {}
        items = []
        for number in range(self.item_count):
            ref = f"ev-{number}"
            evidence[ref] = HandoffEvidenceReference(
                ref, "test", f"source-{number}", f"Evidence {number}"
            )
            items.append(HandoffItem(
                f"item-{number}",
                "current_state" if number == 0 else "outstanding_work",
                "fact" if number == 0 else "recommendation",
                f"Item {number}", f"Summary {number}",
                "current" if number == 0 else "open",
                "info" if number == 0 else "medium",
                "confirmed" if number == 0 else "medium",
                supporting_evidence_reference_ids=(ref,),
                manual_review_required=number > 0,
                requires_authorization=number > 1,
                authorization_type="governed_change_plan" if number > 1 else "none",
                recommendation_category="governed_change_candidate" if number > 0 else None,
            ))
        coverage = [IncidentSourceCoverage(
            "test", "engineering", "handoff_generation",
            "failed" if self.actual_failure else "partial" if self.partial else "complete",
            True, True, self.item_count, 1 if self.actual_failure else 0,
            ["Bounded limitation."] if self.partial else [], 1.0, False,
            "provider_upstream_error" if self.actual_failure else None, False,
            ["bounded_test_limitation"] if self.partial else [],
        )]
        bundle = HandoffEvidenceBundle(
            scope={"focus_entity_ids": [], "automation_ids": [], "automation_entity_ids": [], "change_plan_ids": [], "lookback_hours": 168, "contexts_requested": []},
            items=items, evidence=evidence, coverage=coverage,
            index={"requested": True, "generation": 7, "fingerprint": "idx-7", "cache_hit": True},
        )
        return ProviderResult(
            "engineering", ProviderCapability.HANDOFF_GENERATION,
            ProviderCompleteness.PARTIAL if bundle.source_partial else ProviderCompleteness.COMPLETE,
            coverage=ProviderCoverage(1, 0 if bundle.source_partial else 1), data=bundle,
        )

    def active_index_identity(self):
        return self.index.health()


def request(**overrides):
    values = {
        "handoff_type": "system_status", "title": "",
        "focus_entity_ids": [], "automation_ids": [], "change_plan_ids": [],
        "lookback_hours": 168, "include_runtime_health": True,
        "include_governance_context": True, "include_dependency_context": True,
        "include_integrity_context": True, "include_reliability_context": True,
        "include_incident_context": True, "include_recommendations": True,
        "detail_level": "standard", "output_format": "structured",
        "limit": 20, "cursor": "", "refresh_index": False,
    }
    values.update(overrides)
    return values


class HandoffServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fresh = RuntimeMetrics()
        METRICS.__dict__.clear()
        METRICS.__dict__.update(fresh.__dict__)

    async def test_system_status_is_evidence_backed_and_bounded(self):
        service = HandoffGenerationService(FakeProvider(item_count=2), clock=lambda: datetime(2026, 7, 13, tzinfo=timezone.utc))
        output = await service.generate(**request())
        self.assertEqual(output.data["handoff_type"], "system_status")
        self.assertEqual(output.data["handoff_id"], output.data["handoff_id"])
        self.assertFalse(output.data["authorization_boundaries"]["handoff_is_authorization"])
        self.assertEqual(output.data["item_count"], 2)
        self.assertEqual(sum(output.data["items_by_section"].values()), 2)
        self.assertTrue(all(item["supporting_evidence_reference_ids"] for item in output.data["handoff_items"]))

    async def test_validation_precedes_provider_access(self):
        provider = FakeProvider()
        service = HandoffGenerationService(provider)
        cases = (
            request(handoff_type="focused_review"),
            request(handoff_type="incident"),
            request(handoff_type="change"),
            request(handoff_type="focused_review", focus_entity_ids=["../config"]),
            request(output_format="html"),
            request(limit=101),
        )
        for values in cases:
            with self.assertRaises(InvalidRequestError):
                await service.generate(**values)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(len(service.snapshots.values), 0)

    async def test_all_handoff_types_validate(self):
        for values in (
            request(handoff_type="system_status"),
            request(handoff_type="focused_review", focus_entity_ids=["sensor.example"]),
            request(handoff_type="incident", automation_ids=["12345"]),
            request(handoff_type="change", change_plan_ids=["plan-1"]),
        ):
            output = await HandoffGenerationService(FakeProvider(item_count=1)).generate(**values)
            self.assertIn(output.data["handoff_status"], {"ready", "ready_with_open_items", "blocked", "incomplete"})

    async def test_signed_snapshot_continuation_is_upstream_free(self):
        provider = FakeProvider(item_count=5)
        service = HandoffGenerationService(provider, cursor_key=b"x" * 32)
        first = await service.generate(**request(limit=2, refresh_index=True, output_format="both"))
        self.assertTrue(first.data["pagination"]["has_more"])
        cursor = first.data["pagination"]["next_cursor"]
        second = await service.generate(**request(limit=2, cursor=cursor, refresh_index=False, output_format="both"))
        third = await service.generate(**request(limit=2, cursor=second.data["pagination"]["next_cursor"], output_format="both"))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(first.data["handoff_id"], second.data["handoff_id"])
        self.assertEqual(first.data["generated_at"], third.data["generated_at"])
        self.assertEqual(first.data["item_count"], third.data["item_count"])
        self.assertTrue(second.data["timing_details"]["snapshot_lookup_only"])
        self.assertFalse(second.data["timing_details"]["handoff_regenerated"])
        self.assertFalse(second.data["timing_details"]["markdown_regenerated"])
        self.assertIn("# ", second.data["rendered_markdown"])
        health = METRICS.snapshot()["handoff_generation"]
        self.assertEqual(health["request_count"], 3)
        self.assertEqual(health["handoff_count"], 1)
        self.assertEqual(health["cursor_continuations"], 2)

    async def test_tampering_and_output_mismatch_fail_closed(self):
        service = HandoffGenerationService(FakeProvider(item_count=3), cursor_key=b"y" * 32)
        first = await service.generate(**request(limit=1, output_format="structured"))
        cursor = first.data["pagination"]["next_cursor"]
        with self.assertRaises(GovernanceError) as mismatch:
            await service.generate(**request(limit=1, cursor=cursor, output_format="markdown"))
        self.assertIn(mismatch.exception.code.value, {"invalid_cursor", "stale_cursor"})
        with self.assertRaises(GovernanceError) as tampered:
            await service.generate(**request(limit=1, cursor=cursor[:-1] + ("A" if cursor[-1] != "A" else "B")))
        self.assertEqual(tampered.exception.code.value, "invalid_cursor")

    async def test_replaced_index_makes_cursor_stale(self):
        provider = FakeProvider(item_count=3)
        service = HandoffGenerationService(provider)
        first = await service.generate(**request(limit=1))
        provider.index.identity["generation"] = 8
        with self.assertRaises(GovernanceError) as error:
            await service.generate(**request(limit=1, cursor=first.data["pagination"]["next_cursor"]))
        self.assertEqual(error.exception.code.value, "stale_cursor")

    async def test_partial_coverage_is_not_a_source_failure(self):
        output = await HandoffGenerationService(FakeProvider(item_count=1, partial=True)).generate(**request())
        self.assertTrue(output.partial)
        health = METRICS.snapshot()["handoff_generation"]
        self.assertEqual(health["partial_count"], 1)
        self.assertEqual(health["source_failures"], 0)
        self.assertEqual(health["coverage_limitation_events"], 1)

    async def test_actual_failure_counts_as_source_failure(self):
        output = await HandoffGenerationService(FakeProvider(item_count=1, actual_failure=True)).generate(**request())
        self.assertTrue(output.partial)
        self.assertEqual(METRICS.snapshot()["handoff_generation"]["source_failures"], 1)


class GovernanceInterpretationTests(unittest.TestCase):
    def provider(self):
        return EngineeringHandoffProvider(
            governance=SimpleNamespace(), incident=SimpleNamespace(),
            dependency_index=FakeIndex(), rest_client=SimpleNamespace(),
            health=SimpleNamespace(),
        )

    def plan(self, status, verification="not_run"):
        return SimpleNamespace(
            plan_id="plan-1", title="Test plan", updated_at="2026-07-13T00:00:00Z",
            status=SimpleNamespace(value=status), verification=SimpleNamespace(status=verification),
        )

    def test_only_applied_and_verified_is_completed(self):
        provider = self.provider()
        for status in ("awaiting_approval", "approved", "applying"):
            item = provider._plan_item(self.plan(status), {})
            self.assertNotEqual(item.section, "completed_work")
            self.assertTrue(item.requires_authorization)
        verified = provider._plan_item(self.plan("applied", "passed"), {})
        self.assertEqual(verified.section, "completed_work")
        self.assertEqual(verified.status, "verified")
        self.assertFalse(verified.requires_authorization)

    def test_failed_and_rolled_back_are_not_active_completion(self):
        provider = self.provider()
        failed = provider._plan_item(self.plan("verification_failed", "failed"), {})
        rolled = provider._plan_item(self.plan("rolled_back", "passed"), {})
        self.assertEqual(failed.status, "failed")
        self.assertEqual(rolled.status, "rolled_back")
        self.assertNotEqual(rolled.section, "completed_work")


class PublicContractTests(unittest.TestCase):
    def test_metadata_routing_and_catalog(self):
        item = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == "handoff_generation")
        self.assertEqual(item["policy"], "bounded_handoff_generation_read")
        self.assertEqual(item["risk"], "read")
        self.assertEqual(routing_for_tool("handoff_generation").route, CapabilityRoute.ENGINEERING_NATIVE)
        self.assertEqual(ANALYTICAL_PROVIDER_POLICIES["handoff_generation"]["writes_allowed"], "none")
        self.assertEqual(PLANNED_CAPABILITIES, ())
        matrix = next(item for item in CAPABILITY_PROVIDER_MATRIX if item["tool"] == "handoff_generation")
        self.assertEqual(matrix["selected_provider"], "engineering")
        self.assertEqual(matrix["fallback_policy"], "none")

    def test_tools_list_has_38_and_schema_is_bounded(self):
        tools = get_registered_server()._tool_manager.list_tools()
        self.assertEqual(len(tools), 38)
        tool = next(item for item in tools if item.name == "handoff_generation")
        props = tool.parameters["properties"]
        self.assertEqual(props["handoff_type"]["default"], "system_status")
        self.assertEqual(props["lookback_hours"]["maximum"], 720)
        self.assertEqual(props["limit"]["maximum"], 100)
        self.assertEqual(props["focus_entity_ids"]["maxItems"], 20)
        self.assertEqual(props["output_format"]["default"], "structured")
        json.dumps(tool.parameters)

    def test_production_runtime_has_no_handoff_tool(self):
        source = (ROOT / "hass_mcp_admin" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("handoff_generation", source)


if __name__ == "__main__":
    unittest.main()
