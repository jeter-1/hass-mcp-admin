"""HAMCP-229: bounded multi-automation reliability batch analysis.

Offline, synthetic fixtures only. Each item reuses the unchanged single-item
service against the same reviewed reliability provider; these tests prove the
bounds, refusals, per-item isolation, truthful partial coverage, output
bounding, screening, single-tool parity and the absence of write reachability.
"""

import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.errors import (  # noqa: E402
    AutomationNotFoundError, ErrorCode, GovernanceError, InvalidRequestError,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CORE_INDEPENDENT_STATIC_TOOLS, STATIC_TOOL_CORE_REQUIREMENTS,
)
from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability, ProviderCompleteness, ProviderCoverage, ProviderResult,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES, DIRECT_HA_TOOL_EXCEPTIONS,
    PREVALIDATION_ENFORCEMENT_TOOLS, TOOL_CAPABILITY_POLICY, CapabilityRoute,
    routing_for_tool,
)
from ha_mcp_engineering.reliability import batch as batch_module  # noqa: E402
from ha_mcp_engineering.reliability.batch import (  # noqa: E402
    MAX_BATCH_ITEMS, ReliabilityBatchAnalyzer,
)
from ha_mcp_engineering.reliability.provider import DirectHaReliabilityProvider  # noqa: E402
from ha_mcp_engineering.reliability.runtime import RELIABILITY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server, registered_tools  # noqa: E402
from ha_mcp_engineering.tools.analysis import (  # noqa: E402
    automation_reliability_analysis, automation_reliability_batch_analysis,
)

from test_automation_reliability_analysis import bundle, coverage  # noqa: E402


INSTANT = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
TOOL = "automation_reliability_batch_analysis"
SYNTHETIC_SECRET = "synthetic-hamcp229-secret-value-0123456789"


def ids(count, prefix="auto"):
    return [f"{prefix}_{index:02d}" for index in range(count)]


class PerIdProvider:
    """Synthetic reliability provider: one outcome per automation ID."""

    provider_id = "engineering"
    available = True

    def __init__(self, outcomes=None, *, default=None, delay=0.0, on_fetch=None, secret=""):
        self.outcomes = outcomes or {}
        self.default = default
        self.delay = delay
        self.on_fetch = on_fetch
        self.secret = secret
        self.calls = []

    async def fetch(self, request):
        automation_id = request.query["automation_id"]
        self.calls.append(copy.deepcopy(request.query))
        if self.on_fetch:
            self.on_fetch(automation_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        value = self.outcomes.get(automation_id, self.default)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            value = bundle()
        value = copy.deepcopy(value)
        value.automation_id = automation_id
        value.automation = {**value.automation, "id": automation_id, "entity_id": f"automation.{automation_id}"}
        return ProviderResult(
            provider_id="engineering",
            capability=ProviderCapability.RELIABILITY_ANALYSIS,
            completeness=ProviderCompleteness.PARTIAL if value.partial else ProviderCompleteness.COMPLETE,
            coverage=ProviderCoverage(len(value.coverage), len(value.coverage)),
            data=value,
        )


def service_for(provider):
    return AutomationReliabilityAnalysisService(provider, clock=lambda: INSTANT)


def run(coro):
    return asyncio.run(coro)


class ValidationTests(unittest.TestCase):
    """Whole-request refusal happens before any Home Assistant read."""

    def refused(self, **kwargs):
        provider = PerIdProvider()
        analyzer = ReliabilityBatchAnalyzer()
        arguments = {"automation_ids": ["one"], **kwargs}
        with self.assertRaises(InvalidRequestError) as raised:
            run(analyzer.analyze(service_for(provider), **arguments))
        self.assertEqual(provider.calls, [])
        self.assertFalse(analyzer.in_flight)
        return raised.exception.details

    def test_empty_list_is_refused(self):
        self.assertEqual(self.refused(automation_ids=[])["reason"], "empty_automation_ids")

    def test_more_than_max_items_is_refused(self):
        details = self.refused(automation_ids=ids(MAX_BATCH_ITEMS + 1))
        self.assertEqual(details["reason"], "too_many_automation_ids")
        self.assertEqual(details["maximum"], 20)
        self.assertEqual(details["received"], 21)

    def test_max_items_with_duplicates_still_counts_raw_length(self):
        details = self.refused(automation_ids=["same"] * (MAX_BATCH_ITEMS + 1))
        self.assertEqual(details["reason"], "too_many_automation_ids")

    def test_entity_ids_and_bad_identifiers_report_offending_indexes(self):
        details = self.refused(automation_ids=["good", "automation.kitchen", "", "x" * 129, 7, "a/b"])
        self.assertEqual(details["reason"], "invalid_identifier")
        self.assertEqual(details["invalid_indexes"], [1, 2, 3, 4, 5])

    def test_traversal_and_encoded_identifiers_are_refused_before_any_read(self):
        # The HAMCP-200 traversal corpus (excluding "1\n", which strips to the
        # valid ID "1"); one bad entry refuses the whole batch.
        corpus = (
            "..", ".", "../../hassio/addons", "1/../../../hassio/addons",
            "..%2f..%2fhassio%2faddons", "%252e%252e%252fhassio",
            "..\\..\\hassio\\addons", "1%00", "1\x00", "1?x=1", "1#fragment", "a.b",
        )
        for value in corpus:
            with self.subTest(value=value):
                details = self.refused(automation_ids=["good", value])
                self.assertEqual(details["reason"], "invalid_identifier")
                self.assertEqual(details["invalid_indexes"], [1])

    def test_non_list_is_refused(self):
        self.assertEqual(self.refused(automation_ids="one")["reason"], "invalid_automation_ids")

    def test_out_of_range_bounds_are_refused(self):
        for field, value in (
            ("trace_limit", 11), ("trace_limit", 0), ("trace_limit", True),
            ("lookback_hours", 0), ("lookback_hours", 721), ("lookback_hours", "5"),
        ):
            with self.subTest(field=field, value=value):
                details = self.refused(**{field: value})
                self.assertEqual(details["reason"], "out_of_range")
                self.assertEqual(details["field"], field)

    def test_duplicates_collapse_to_first_occurrence_and_are_reported(self):
        provider = PerIdProvider()
        output = run(ReliabilityBatchAnalyzer().analyze(
            service_for(provider), automation_ids=["b", "a", "b", " a", "c", "b"],
        ))
        self.assertEqual([call["automation_id"] for call in provider.calls], ["b", "a", "c"])
        self.assertEqual(output.data["request"]["automation_ids"], ["b", "a", "c"])
        self.assertEqual(output.data["request"]["duplicates_removed"], ["b", "a"])
        self.assertEqual(output.data["coverage"]["requested"], 6)
        self.assertEqual(output.data["coverage"]["deduplicated"], 3)
        self.assertEqual([row["automation_id"] for row in output.data["items"]], ["b", "a", "c"])

    def test_public_schema_bounds_and_registration(self):
        tools = list(registered_tools(get_registered_server()).values())
        self.assertEqual(len(tools), 85)  # + HAMCP-290 get_marketplace_inventory; HAMCP-134: + security metadata; HAMCP-297: + device configuration
        matches = [tool for tool in tools if tool.name == TOOL]
        self.assertEqual(len(matches), 1)
        schema = matches[0].parameters
        self.assertEqual(set(schema["properties"]), {"automation_ids", "lookback_hours", "trace_limit"})
        self.assertEqual(schema["required"], ["automation_ids"])
        self.assertEqual(schema["properties"]["automation_ids"]["maxItems"], 20)
        self.assertEqual(schema["properties"]["automation_ids"]["minItems"], 1)
        self.assertEqual(schema["properties"]["trace_limit"]["maximum"], 10)
        self.assertEqual(schema["properties"]["trace_limit"]["default"], 5)
        self.assertEqual(schema["properties"]["lookback_hours"]["maximum"], 720)
        self.assertEqual(schema["properties"]["lookback_hours"]["default"], 168)


class RequestLevelFailureTests(unittest.TestCase):
    def test_unconfigured_service_is_a_failure_response_with_no_fallback(self):
        with patch.object(RELIABILITY_ANALYSIS, "service", None):
            output = json.loads(run(automation_reliability_batch_analysis(automation_ids=["one"])))
        self.assertFalse(output["success"])
        self.assertEqual(output["operation"], TOOL)
        self.assertEqual(output["error_code"], ErrorCode.INTERNAL_SERVER_ERROR.value)
        self.assertEqual(output["metadata"]["routing"]["provider"], "engineering")
        self.assertFalse(output["metadata"]["routing"]["fallback_occurred"])

    def test_tool_validation_refusal_is_a_failure_response_with_no_reads(self):
        provider = PerIdProvider()
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            output = json.loads(run(automation_reliability_batch_analysis(
                automation_ids=["automation.kitchen"],
            )))
        self.assertFalse(output["success"])
        self.assertEqual(output["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertEqual(output["details"]["invalid_indexes"], [0])
        self.assertEqual(provider.calls, [])

    def test_concurrent_second_batch_is_refused_retryably_before_any_read(self):
        async def scenario():
            release = asyncio.Event()
            first_provider = PerIdProvider()

            async def blocking_fetch(request, _fetch=first_provider.fetch):
                await release.wait()
                return await _fetch(request)

            first_provider.fetch = blocking_fetch
            second_provider = PerIdProvider()
            analyzer = ReliabilityBatchAnalyzer()
            first = asyncio.create_task(analyzer.analyze(service_for(first_provider), automation_ids=["one"]))
            await asyncio.sleep(0)
            self.assertTrue(analyzer.in_flight)
            with self.assertRaises(GovernanceError) as raised:
                await analyzer.analyze(service_for(second_provider), automation_ids=["two"])
            self.assertEqual(raised.exception.code, ErrorCode.RATE_LIMIT_EXCEEDED)
            self.assertTrue(raised.exception.retryable)
            self.assertEqual(raised.exception.details["reason"], "batch_in_progress")
            self.assertEqual(second_provider.calls, [])
            release.set()
            result = await first
            self.assertFalse(analyzer.in_flight)
            self.assertEqual(result.data["batch_result_status"], "success")
            # The flag is released after a batch, so a later batch runs.
            await analyzer.analyze(service_for(second_provider), automation_ids=["two"])
            self.assertEqual(len(second_provider.calls), 1)

        run(scenario())


class ItemIsolationTests(unittest.TestCase):
    def test_not_found_and_provider_timeout_do_not_abort_neighbours(self):
        provider = PerIdProvider({
            "missing": AutomationNotFoundError(),
            "slow": asyncio.TimeoutError(),
            "boom": RuntimeError("synthetic"),
        })
        output = run(ReliabilityBatchAnalyzer().analyze(
            service_for(provider), automation_ids=["ok_1", "missing", "slow", "boom", "ok_2"],
        ))
        rows = {row["automation_id"]: row for row in output.data["items"]}
        self.assertEqual(rows["ok_1"]["status"], "analyzed")
        self.assertEqual(rows["ok_2"]["status"], "analyzed")
        self.assertEqual((rows["missing"]["status"], rows["missing"]["reason"]), ("failed", "automation_not_found"))
        self.assertEqual((rows["slow"]["status"], rows["slow"]["reason"]), ("failed", "provider_timeout"))
        self.assertEqual((rows["boom"]["status"], rows["boom"]["reason"]), ("failed", "internal_error"))
        self.assertEqual(output.data["batch_result_status"], "partial")
        self.assertTrue(output.partial)
        coverage = output.data["coverage"]
        self.assertEqual((coverage["analyzed_complete"], coverage["failed"]), (2, 3))
        # Ordinary failures are not resubmittable budget gaps.
        self.assertEqual(coverage["remaining_automation_ids"], [])

    def test_unusable_provider_result_maps_to_analysis_unavailable(self):
        class Unusable(PerIdProvider):
            async def fetch(self, request):
                self.calls.append(request.query)
                return ProviderResult(
                    provider_id="engineering",
                    capability=ProviderCapability.RELIABILITY_ANALYSIS,
                    completeness=ProviderCompleteness.FAILED,
                    coverage=ProviderCoverage(1, 0),
                )

        output = run(ReliabilityBatchAnalyzer().analyze(service_for(Unusable()), automation_ids=["x"]))
        self.assertEqual(output.data["items"][0]["reason"], "analysis_unavailable")
        self.assertEqual(output.data["items"][0]["error_code"], "analysis_unavailable")

    def test_all_items_failed_is_a_failed_batch(self):
        provider = PerIdProvider(default=AutomationNotFoundError())
        output = run(ReliabilityBatchAnalyzer().analyze(service_for(provider), automation_ids=["a", "b"]))
        self.assertEqual(output.data["batch_result_status"], "failed")
        self.assertEqual(output.data["coverage"]["failed"], 2)
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            public = json.loads(run(automation_reliability_batch_analysis(automation_ids=["a", "b"])))
        self.assertTrue(public["success"])
        self.assertEqual(public["data"]["batch_result_status"], "failed")

    def test_partial_item_evidence_makes_the_batch_partial(self):
        partial_bundle = bundle(coverage_items=[
            coverage("automation_config"), coverage("automation_state"),
            coverage("blueprint_source", "not_requested", examined=0),
            coverage("entity_state", examined=0), coverage("entity_registry", examined=0),
            coverage("automation_traces", "partial", failed=1),
            coverage("system_log", examined=0),
            coverage("logbook_history", "not_requested", examined=0),
        ])
        self.assertTrue(partial_bundle.partial)
        provider = PerIdProvider({"p": partial_bundle})
        output = run(ReliabilityBatchAnalyzer().analyze(service_for(provider), automation_ids=["ok", "p"]))
        rows = {row["automation_id"]: row for row in output.data["items"]}
        self.assertEqual(rows["p"]["result_status"], "partial")
        self.assertEqual(output.data["coverage"]["analyzed_partial"], 1)
        self.assertEqual(output.data["batch_result_status"], "partial")

    def test_twenty_healthy_items_succeed_in_one_call(self):
        provider = PerIdProvider()
        output = run(ReliabilityBatchAnalyzer().analyze(service_for(provider), automation_ids=ids(20)))
        self.assertEqual(output.data["batch_result_status"], "success")
        self.assertEqual(output.data["coverage"]["analyzed_complete"], 20)
        self.assertFalse(output.partial)
        self.assertEqual(len(provider.calls), 20)
        # One analysis instant for the whole batch: one shared window.
        self.assertEqual({call["analysis_timestamp"] for call in provider.calls}, {"2026-07-12T12:00:00.000000Z"})


class TimeBudgetTests(unittest.TestCase):
    def test_items_after_the_deadline_are_skipped_without_reads_in_order(self):
        clock = {"now": 0.0}

        def advance(_automation_id):
            clock["now"] += 21.0

        provider = PerIdProvider(on_fetch=advance)
        analyzer = ReliabilityBatchAnalyzer(concurrency=1, monotonic=lambda: clock["now"])
        output = run(analyzer.analyze(service_for(provider), automation_ids=["a", "b", "c", "d"]))
        self.assertEqual([call["automation_id"] for call in provider.calls], ["a", "b"])
        rows = output.data["items"]
        self.assertEqual([row["status"] for row in rows], ["analyzed", "analyzed", "skipped_budget", "skipped_budget"])
        self.assertEqual([row["reason"] for row in rows[2:]], ["time_budget", "time_budget"])
        self.assertEqual(output.data["coverage"]["remaining_automation_ids"], ["c", "d"])
        self.assertEqual(output.data["batch_result_status"], "partial")

    def test_started_item_cut_by_the_budget_is_time_budget_exceeded(self):
        provider = PerIdProvider({"slow": None}, delay=0.0)
        slow = PerIdProvider(delay=5.0)

        class Mixed:
            provider_id = "engineering"
            available = True
            calls = []

            async def fetch(self, request):
                self.calls.append(request.query["automation_id"])
                target = slow if request.query["automation_id"] == "slow" else provider
                return await target.fetch(request)

        analyzer = ReliabilityBatchAnalyzer(batch_seconds=0.3, start_reserve_seconds=0.01, concurrency=1)
        output = run(analyzer.analyze(service_for(Mixed()), automation_ids=["fast", "slow", "after"]))
        rows = {row["automation_id"]: row for row in output.data["items"]}
        self.assertEqual(rows["fast"]["status"], "analyzed")
        self.assertEqual((rows["slow"]["status"], rows["slow"]["reason"]), ("failed", "time_budget_exceeded"))
        self.assertEqual((rows["after"]["status"], rows["after"]["reason"]), ("skipped_budget", "time_budget"))
        self.assertEqual(output.data["coverage"]["remaining_automation_ids"], ["slow", "after"])
        self.assertNotIn("after", Mixed.calls)


class OutputBudgetTests(unittest.TestCase):
    def test_overflowing_digests_become_output_budget_and_every_id_is_present(self):
        provider = PerIdProvider()
        full = run(ReliabilityBatchAnalyzer().analyze(service_for(provider), automation_ids=ids(6)))
        digest_size = len(batch_module._compact(full.data["items"][0]))
        fixed = sum(batch_module._fixed_item_cost(item) for item in ids(6))
        limit = batch_module.ENVELOPE_RESERVE_CHARS + fixed + 2 * digest_size + digest_size // 2
        output = run(ReliabilityBatchAnalyzer().analyze(
            service_for(PerIdProvider()), automation_ids=ids(6), response_limit=limit,
        ))
        rows = output.data["items"]
        self.assertEqual([row["automation_id"] for row in rows], ids(6))
        self.assertEqual([row["status"] for row in rows], ["analyzed"] * 2 + ["skipped_budget"] * 4)
        self.assertTrue(all(row["reason"] == "output_budget" for row in rows[2:]))
        self.assertEqual(output.data["coverage"]["remaining_automation_ids"], ids(6)[2:])
        self.assertEqual(output.data["batch_result_status"], "partial")

    def test_twenty_max_length_ids_with_large_names_render_without_truncation(self):
        long_ids = [f"{index:02d}" + "x" * 126 for index in range(20)]
        large = bundle()
        large.automation = {**large.automation, "friendly_name": "Synthetic long name " * 40}
        provider = PerIdProvider(default=large)
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            rendered = run(automation_reliability_batch_analysis(automation_ids=long_ids))
        self.assertLessEqual(len(rendered), 60_000)
        output = json.loads(rendered)  # a cut envelope would not parse
        self.assertTrue(output["success"])
        self.assertEqual([row["automation_id"] for row in output["data"]["items"]], long_ids)
        statuses = {row["status"] for row in output["data"]["items"]}
        self.assertLessEqual(statuses, {"analyzed", "skipped_budget"})
        skipped = [row for row in output["data"]["items"] if row["status"] == "skipped_budget"]
        self.assertTrue(all(row["reason"] == "output_budget" for row in skipped))
        self.assertEqual(
            output["data"]["coverage"]["remaining_automation_ids"],
            [row["automation_id"] for row in skipped],
        )

    def test_a_cut_envelope_is_refused_not_returned(self):
        provider = PerIdProvider()
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)), \
                patch("ha_mcp_engineering.tools.analysis.dump_json_with_truncation", return_value=("{}", True)):
            output = json.loads(run(automation_reliability_batch_analysis(automation_ids=["a"])))
        self.assertFalse(output["success"])
        self.assertEqual(output["details"]["reason"], "response_bound_exceeded")


class ScreeningTests(unittest.TestCase):
    def test_known_secret_in_a_friendly_name_is_withheld_by_the_final_screen(self):
        leaky = bundle()
        leaky.automation = {**leaky.automation, "friendly_name": SYNTHETIC_SECRET}
        provider = PerIdProvider({"leak": leaky}, secret=SYNTHETIC_SECRET)
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            rendered = run(automation_reliability_batch_analysis(automation_ids=["clean", "leak"]))
        self.assertNotIn(SYNTHETIC_SECRET, rendered)
        output = json.loads(rendered)
        self.assertTrue(output["success"])
        self.assertEqual(output["data"]["batch_result_status"], "partial")
        self.assertTrue(any("withheld" in warning.lower() for warning in output["warnings"]))


class ParityTests(unittest.TestCase):
    def test_digest_matches_the_single_tool_for_one_id(self):
        findings_bundle = bundle(
            state="off",
            references=[{"entity_id": "sensor.missing", "status": "missing", "config_path": "$.trigger[0].entity_id"}],
            config={"action": [{"delay": 5}]},
        )
        provider = PerIdProvider(default=findings_bundle)
        service = service_for(provider)
        with patch.object(RELIABILITY_ANALYSIS, "service", service):
            single = json.loads(run(automation_reliability_analysis(
                automation_id="parity", lookback_hours=24, trace_limit=5,
            )))
            batch = json.loads(run(automation_reliability_batch_analysis(
                automation_ids=["parity"], lookback_hours=24, trace_limit=5,
            )))
        self.assertTrue(single["success"])
        self.assertTrue(batch["success"])
        # The provider saw byte-identical evidence requests.
        self.assertEqual(provider.calls[0], provider.calls[1])
        digest = batch["data"]["items"][0]
        for key in (
            "overall_assessment", "result_status", "finding_counts_by_severity",
            "unique_root_cause_count", "configuration_fingerprint", "evidence_fingerprint",
            "target",
        ):
            with self.subTest(key=key):
                self.assertEqual(digest[key], single["data"][key])
        self.assertEqual(
            digest["lifecycle"]["hazards_detected"],
            single["data"]["lifecycle_analysis"]["hazards_detected"],
        )
        self.assertEqual(
            digest["source_completeness"],
            {item["source_type"]: item["completeness"] for item in single["data"]["evidence_source_coverage"]},
        )
        self.assertEqual(
            [group["root_cause_group_id"] for group in digest["root_cause_summaries"]],
            [group["root_cause_group_id"] for group in single["data"]["root_cause_groups"]][:3],
        )
        self.assertNotIn("findings", digest)
        self.assertNotIn("evidence_references", digest)


class LoopbackRest:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)

    async def request(self, method, path, *args, **kwargs):
        self.calls.append((method, path, args, kwargs))
        if path.startswith("/config/automation/config/"):
            automation_id = path.rsplit("/", 1)[-1]
            if automation_id in self.missing:
                raise AutomationNotFoundError()
            return {
                "alias": automation_id, "mode": "single",
                "trigger": [{"platform": "state", "entity_id": "sensor.shared"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.target"}}],
            }
        if path.startswith("/states/automation."):
            entity_id = path.removeprefix("/states/")
            return {"entity_id": entity_id, "state": "on", "attributes": {"friendly_name": entity_id}}
        if path in {"/states/sensor.shared", "/states/light.target"}:
            return {"entity_id": path.removeprefix("/states/"), "state": "on", "attributes": {}}
        raise AssertionError(f"unexpected REST call: {method} {path}")


class LoopbackWebSocket:
    def __init__(self, automation_ids):
        self.automation_ids = automation_ids
        self.calls = []

    async def command(self, payload, *args, **kwargs):
        self.calls.append(payload)
        if payload["type"] == "config/entity_registry/list":
            return [
                {"entity_id": f"automation.{item}", "unique_id": item, "disabled_by": None}
                for item in self.automation_ids
            ] + [{"entity_id": "sensor.shared", "disabled_by": None}]
        if payload["type"] == "trace/list":
            return []
        if payload["type"] == "system_log/list":
            return []
        raise AssertionError(f"unexpected WebSocket call: {payload}")


class NegativeReachabilityTests(unittest.TestCase):
    ALLOWED_WS = {"config/entity_registry/list", "trace/list", "trace/get", "system_log/list"}

    def test_batch_issues_only_the_single_tools_read_shapes(self):
        automation_ids = ids(20)
        rest = LoopbackRest(missing={"auto_03"})
        websocket = LoopbackWebSocket(automation_ids)
        provider = DirectHaReliabilityProvider(rest, websocket)
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            output = json.loads(run(automation_reliability_batch_analysis(automation_ids=automation_ids)))
        self.assertTrue(output["success"])
        self.assertEqual(output["data"]["coverage"]["failed"], 1)
        self.assertEqual(output["data"]["coverage"]["analyzed_complete"] + output["data"]["coverage"]["analyzed_partial"], 19)
        self.assertTrue(rest.calls)
        self.assertTrue(all(method == "GET" for method, *_ in rest.calls))
        for _method, path, *_ in rest.calls:
            self.assertTrue(
                path.startswith(("/config/automation/config/", "/states/")),
                path,
            )
            self.assertFalse(path.startswith("/services"), path)
        self.assertNotIn(("GET", "/states"), [(m, p) for m, p, *_ in rest.calls])
        self.assertLessEqual({payload["type"] for payload in websocket.calls}, self.ALLOWED_WS)
        for payload in websocket.calls:
            if payload["type"].startswith("trace/"):
                self.assertEqual(payload.get("domain"), "automation")
        routing = output["metadata"]["routing"]
        self.assertEqual(routing["provider"], "engineering")
        self.assertFalse(routing["fallback_occurred"])
        self.assertEqual(routing["direct_access_policy"]["policy_id"], "bounded_automation_set_reliability_read")
        self.assertEqual(routing["direct_access_policy"]["access"], "read")

    def test_routing_catalog_and_core_requirements_are_read_only_and_reuse_the_single_tool(self):
        decision = routing_for_tool(TOOL)
        self.assertEqual(decision.route, CapabilityRoute.ENGINEERING_NATIVE)
        self.assertEqual(decision.preferred_provider, "engineering")
        self.assertEqual(decision.fallback_providers, ())
        self.assertFalse(decision.explicit_direct_fallback_allowed)
        self.assertEqual(TOOL_CAPABILITY_POLICY[TOOL], ProviderCapability.RELIABILITY_ANALYSIS)
        policy = ANALYTICAL_PROVIDER_POLICIES[TOOL]
        self.assertEqual(policy["policy_id"], "bounded_automation_set_reliability_read")
        self.assertEqual((policy["access"], policy["writes_allowed"], policy["fallback_policy"]), ("read", "none", "none"))
        self.assertNotIn(TOOL, DIRECT_HA_TOOL_EXCEPTIONS)
        self.assertNotIn(TOOL, PREVALIDATION_ENFORCEMENT_TOOLS)
        self.assertNotIn(TOOL, CORE_INDEPENDENT_STATIC_TOOLS)
        self.assertEqual(
            STATIC_TOOL_CORE_REQUIREMENTS[TOOL],
            STATIC_TOOL_CORE_REQUIREMENTS["automation_reliability_analysis"],
        )
        entry = next(item for item in BETA_NATIVE_CAPABILITIES if item["tool"] == TOOL)
        self.assertEqual((entry["risk"], entry["provider"], entry["fallback"]), ("read", "engineering", "none"))

    def test_single_tool_schema_is_unchanged(self):
        tools = registered_tools(get_registered_server())
        single = next(tool for tool in tools.values() if tool.name == "automation_reliability_analysis")
        self.assertEqual(
            set(single.parameters["properties"]),
            {"automation_id", "lookback_hours", "trace_limit", "detail_level", "limit", "cursor"},
        )
        self.assertEqual(single.parameters["required"], ["automation_id"])


# Codex M1 (PR #402): raw public arguments are refused by the closed contract
# before the MCP SDK can coerce them or render them into an error. Every case
# carries a synthetic marker that must never come back to the caller.
MARKER = "HAMCP229RAWMARK"
RAW_REASONS = {
    "invalid_arguments", "unexpected_argument", "missing_argument",
    "invalid_automation_ids", "empty_automation_ids", "too_many_automation_ids",
    "invalid_identifier", "out_of_range",
}


def raw_refusal_cases():
    return (
        # Wrong types.
        ("ids_dict", {"automation_ids": {"synthetic": MARKER}}, "invalid_automation_ids"),
        ("ids_string", {"automation_ids": MARKER}, "invalid_automation_ids"),
        ("ids_member_dict", {"automation_ids": ["good", {"x": MARKER}]}, "invalid_identifier"),
        ("ids_member_int", {"automation_ids": ["good", 7]}, "invalid_identifier"),
        ("ids_member_list", {"automation_ids": [[MARKER]]}, "invalid_identifier"),
        ("trace_limit_marker_string", {"automation_ids": ["good"], "trace_limit": MARKER}, "out_of_range"),
        ("lookback_numeric_string", {"automation_ids": ["good"], "lookback_hours": "24"}, "out_of_range"),
        ("trace_limit_numeric_string", {"automation_ids": ["good"], "trace_limit": "5"}, "out_of_range"),
        ("lookback_float", {"automation_ids": ["good"], "lookback_hours": 24.0}, "out_of_range"),
        ("lookback_null", {"automation_ids": ["good"], "lookback_hours": None}, "out_of_range"),
        # Boolean versus integer.
        ("lookback_true", {"automation_ids": ["good"], "lookback_hours": True}, "out_of_range"),
        ("trace_limit_true", {"automation_ids": ["good"], "trace_limit": True}, "out_of_range"),
        ("trace_limit_false", {"automation_ids": ["good"], "trace_limit": False}, "out_of_range"),
        # Unknown and missing keys.
        ("unknown_key", {"automation_ids": ["good"], "detail_level": "full"}, "unexpected_argument"),
        ("unknown_marker_key", {"automation_ids": ["good"], MARKER: 1}, "unexpected_argument"),
        ("missing_ids", {"trace_limit": 5}, "missing_argument"),
        # Oversize lists.
        ("oversize", {"automation_ids": ids(MAX_BATCH_ITEMS + 1, prefix=MARKER)}, "too_many_automation_ids"),
        ("oversize_duplicates", {"automation_ids": [MARKER] * (MAX_BATCH_ITEMS + 1)}, "too_many_automation_ids"),
        ("empty", {"automation_ids": []}, "empty_automation_ids"),
        # Synthetic secret-looking values.
        ("secret_member", {"automation_ids": ["good", f"Bearer {SYNTHETIC_SECRET}"]}, "invalid_identifier"),
        ("secret_bound", {"automation_ids": ["good"], "lookback_hours": SYNTHETIC_SECRET}, "out_of_range"),
        ("secret_key", {"automation_ids": ["good"], SYNTHETIC_SECRET: True}, "unexpected_argument"),
        ("secret_nested", {"automation_ids": {"token": SYNTHETIC_SECRET}}, "invalid_automation_ids"),
    )


def secret_markers(arguments):
    """Every caller-supplied marker that must not be reflected."""

    text = json.dumps(arguments)
    return [value for value in (MARKER, SYNTHETIC_SECRET) if value in text]


class RawArgumentAssertions:
    def assert_refused_without_echo(self, rendered, expected_reason, arguments):
        for marker in secret_markers(arguments):
            self.assertNotIn(marker, rendered)
        self.assertNotIn("input_value", rendered)
        self.assertNotIn("validation error", rendered.lower())
        output = json.loads(rendered)
        self.assertIs(output["success"], False)
        self.assertEqual(output["operation"], TOOL)
        self.assertEqual(output["error"], "InvalidRequestError")
        self.assertEqual(output["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertFalse(output["retryable"])
        self.assertEqual(output["details"]["reason"], expected_reason)
        self.assertIn(output["details"]["reason"], RAW_REASONS)
        self.assertLessEqual(
            set(output["details"]),
            {"operation", "reason", "field", "minimum", "maximum", "received", "invalid_indexes"},
        )
        self.assertFalse(output["metadata"]["routing"]["fallback_occurred"])


def sdk_text(result):
    """The text content of a FastMCP ``call_tool`` result."""

    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


class RawArgumentSdkBoundaryTests(RawArgumentAssertions, unittest.TestCase):
    """The served FastMCP registration, not the Python function."""

    def call(self, arguments):
        return run(get_registered_server().call_tool(TOOL, arguments))

    def test_invalid_raw_arguments_are_refused_before_sdk_coercion_without_echo(self):
        for name, arguments, reason in raw_refusal_cases():
            with self.subTest(case=name):
                provider = PerIdProvider()
                with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)), \
                        patch.object(batch_module.RELIABILITY_BATCH, "analyze") as analyze:
                    rendered = sdk_text(self.call(copy.deepcopy(arguments)))
                self.assert_refused_without_echo(rendered, reason, arguments)
                self.assertEqual(provider.calls, [])
                analyze.assert_not_called()

    def test_registered_tool_keeps_the_public_schema_and_catalog(self):
        tools = registered_tools(get_registered_server())
        self.assertEqual(len(tools), 85)  # + HAMCP-290 get_marketplace_inventory; HAMCP-134: + security metadata; HAMCP-297: + device configuration
        tool = tools[TOOL]
        self.assertIsNone(tool.annotations)
        self.assertEqual(tool.description, automation_reliability_batch_analysis.__doc__)
        self.assertNotIn("additionalProperties", tool.parameters)
        self.assertEqual(tool.parameters["title"], f"{TOOL}Arguments")

    def test_valid_arguments_still_succeed_through_the_sdk(self):
        provider = PerIdProvider()
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            explicit = json.loads(sdk_text(self.call({
                "automation_ids": ids(MAX_BATCH_ITEMS), "lookback_hours": 24, "trace_limit": 3,
            })))
        self.assertTrue(explicit["success"], explicit)
        self.assertEqual(explicit["data"]["coverage"]["deduplicated"], MAX_BATCH_ITEMS)
        self.assertEqual({call["lookback_hours"] for call in provider.calls}, {24})
        provider = PerIdProvider()
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            defaults = json.loads(sdk_text(self.call({"automation_ids": ["b", "a", "b"]})))
        self.assertTrue(defaults["success"], defaults)
        self.assertEqual(defaults["data"]["request"]["duplicates_removed"], ["b"])
        self.assertEqual([call["automation_id"] for call in provider.calls], ["b", "a"])


class RawArgumentGatewayTests(RawArgumentAssertions, unittest.TestCase):
    """The production HTTP gateway and pinned SDK, with synthetic Core only."""

    @classmethod
    def setUpClass(cls):
        import dataclasses
        import tempfile

        from tests.support.process_isolation import restore_after_class

        # HAMCP-362: taken before the imports below. Importing test_beta_v2
        # and test_beta_observability here puts hass_mcp_admin on sys.path,
        # and a snapshot taken after them kept it there past this class.
        restore_after_class(cls)

        from tests import test_beta_v2 as beta
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from tests.test_beta_observability import preserve_create_application_state
        from ha_mcp_engineering.application import create_application
        from ha_mcp_engineering.governance import GOVERNANCE
        from ha_mcp_engineering.ha_core_readmission.runtime import CORE_READMISSION

        # HAMCP-362: create_application() configures the process-wide
        # singletons in place. Unrestored, HEALTH kept this class's governance
        # runtime after tearDownClass removed its storage directory, and a
        # later module's get_server_health failed with
        # change_plan_storage_error.
        preserve_create_application_state(cls)

        class SyntheticCoreSource:
            async def capture_core_snapshot(self):
                return beta.core_snapshot("2026.8.1", evidence=beta.core_capability_evidence())

        cls.beta = beta
        cls.tempdir = tempfile.TemporaryDirectory()
        settings = dataclasses.replace(
            beta.beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl")),
            rate_limit_per_minute=6000, rate_limit_burst=1000,
        )
        gateway = create_application(settings, core_snapshot_source=SyntheticCoreSource())
        asyncio.run(CORE_READMISSION.reconcile_once("startup"))
        asyncio.run(GOVERNANCE.require().f3_runtime.recover_once("synthetic-application-startup"))
        cls.client_context = SameThreadAsgiTestClient(gateway, lifespan_app=gateway.app, follow_redirects=False)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def post(self, arguments, request_id):
        response = self.client.post(
            f"/{self.beta.SECRET}/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                  "params": {"name": TOOL, "arguments": arguments}},
            headers={"accept": "application/json, text/event-stream",
                     "content-type": "application/json", "x-request-id": request_id},
        )
        line = next(item for item in response.text.replace("\r", "").splitlines()
                    if item.startswith("data: "))
        return response.text, json.loads(line.removeprefix("data: "))["result"]

    def test_invalid_raw_arguments_are_refused_over_http_without_echo_or_dispatch(self):
        for number, (name, arguments, reason) in enumerate(raw_refusal_cases()):
            with self.subTest(case=name):
                provider = PerIdProvider()
                with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)), \
                        patch.object(batch_module.RELIABILITY_BATCH, "analyze") as analyze:
                    body, result = self.post(copy.deepcopy(arguments), f"hamcp229-raw-{number}")
                for marker in secret_markers(arguments):
                    self.assertNotIn(marker, body)
                self.assertNotIn("input_value", body)
                self.assertFalse(result.get("isError"))
                self.assert_refused_without_echo(result["content"][0]["text"], reason, arguments)
                self.assertEqual(provider.calls, [])
                analyze.assert_not_called()

    def test_valid_arguments_still_succeed_over_http(self):
        provider = PerIdProvider()
        with patch.object(RELIABILITY_ANALYSIS, "service", service_for(provider)):
            _, result = self.post({"automation_ids": ["a", "b"], "lookback_hours": 1}, "hamcp229-raw-ok")
        output = json.loads(result["content"][0]["text"])
        self.assertTrue(output["success"], output)
        self.assertEqual([call["lookback_hours"] for call in provider.calls], [1, 1])


def tearDownModule():
    # HAMCP-288: create_application installs the process-wide secret-path
    # redaction; reset it so this module's synthetic gateway does not redact
    # another module's data in the same test process.
    from ha_mcp_engineering import secret_paths

    secret_paths.reset()


if __name__ == "__main__":
    unittest.main()
