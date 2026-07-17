import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITY_PROVIDER_MATRIX,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.dependency.models import SOURCE_TYPES  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    InvalidRequestError,
)
from ha_mcp_engineering.integrity.models import (  # noqa: E402
    FINDING_TYPES,
    IntegrityEvidenceBundle,
    IntegritySourceCoverage,
)
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402
from ha_mcp_engineering.integrity.rules import classify_integrity  # noqa: E402
from ha_mcp_engineering.integrity.service import (  # noqa: E402
    ConfigurationIntegrityAnalysisService,
)
from ha_mcp_engineering.observability import METRICS  # noqa: E402
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderResult,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    ANALYTICAL_PROVIDER_POLICIES,
    CapabilityRoute,
    routing_for_tool,
)
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402


ANALYSIS_TIME = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def coverage(*, requested=None, unsupported=()):
    requested = set(SOURCE_TYPES if requested is None else requested)
    values = [
        IntegritySourceCoverage(
            "current_states",
            "direct_ha_api",
            "current_entity_state",
            "complete",
            items_examined=2,
        ),
        IntegritySourceCoverage(
            "entity_registry",
            "direct_ha_api",
            "entity_registry_read",
            "complete",
            items_examined=2,
        ),
    ]
    for source_type in SOURCE_TYPES:
        selected = source_type in requested
        values.append(
            IntegritySourceCoverage(
                source_type,
                "engineering" if selected else "none",
                f"{source_type}_configuration",
                "not_supported"
                if source_type in unsupported
                else "complete"
                if selected
                else "not_requested",
                requested=selected,
                required_for_assessment=selected,
            )
        )
    return values


def reference(
    target,
    *,
    source_id="auto-1",
    source_type="automation",
    path="$.condition[0].entity_id",
    source_state="on",
):
    return {
        "evidence_id": "ev_"
        + hashlib.sha256(
            f"{target}:{source_type}:{source_id}:{path}".encode()
        ).hexdigest()[:20],
        "target_entity_id": target,
        "source_type": source_type,
        "source_id": source_id,
        "source_entity_id": f"{source_type}.{source_id}",
        "source_name": source_id,
        "source_state": source_state,
        "config_path": path,
        "relation": "condition",
        "evidence_summary": "Exact static reference.",
        "excerpt": "sensor.example",
    }


def dynamic(*, source_id="auto-1", source_type="automation", path="$.action[0]"):
    return {
        "evidence_id": f"dyn_{source_type}_{source_id}_{len(path)}",
        "source_type": source_type,
        "source_id": source_id,
        "source_entity_id": f"{source_type}.{source_id}",
        "source_name": source_id,
        "source_state": "on",
        "config_path": path,
        "warning": "A template may select an entity dynamically.",
        "excerpt": "{{ states(variable) }}",
    }


def bundle(
    *,
    references=None,
    dynamics=None,
    states=None,
    registry=None,
    requested=None,
    unsupported=(),
    generation=1,
    fingerprint="index-one",
    cache_hit=True,
):
    requested_values = list(SOURCE_TYPES if requested is None else requested)
    return IntegrityEvidenceBundle(
        exact_references=list(references or []),
        dynamic_references=list(dynamics or []),
        current_states=dict(states or {}),
        entity_registry=dict(registry or {}),
        states_available=True,
        registry_available=True,
        coverage=coverage(requested=requested_values, unsupported=unsupported),
        index={
            "generation": generation,
            "fingerprint": fingerprint,
            "built_at": "2026-07-20T11:59:00Z",
            "cache_hit": cache_hit,
            "refreshed": not cache_hit,
            "lookup_duration_ms": 1.0,
            "current_index_build_duration_ms": 4.0 if not cache_hit else 0.0,
            "original_build_duration_ms": 4.0,
        },
        evidence_collection_duration_ms=5.0,
        orphan_scope_complete=set(requested_values) == set(SOURCE_TYPES),
    )


class FakeProvider:
    provider_id = "engineering"

    def __init__(self, value):
        self.value = value
        self.calls = []
        self.identity_calls = 0
        self.identity = {
            "valid": True,
            "generation": value.index["generation"],
            "fingerprint": value.index["fingerprint"],
        }

    async def fetch(self, request):
        self.calls.append(request)
        return ProviderResult(
            provider_id="engineering",
            capability=ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
            completeness=(
                ProviderCompleteness.COMPLETE
                if self.value.required_coverage_complete
                else ProviderCompleteness.PARTIAL
            ),
            coverage=ProviderCoverage(1, 1),
            data=self.value,
        )

    def active_index_identity(self):
        self.identity_calls += 1
        return dict(self.identity)


class ClassificationTests(unittest.TestCase):
    def classify(self, value, **options):
        return classify_integrity(
            value,
            finding_types=options.get("finding_types", list(FINDING_TYPES)),
            include_orphan_candidates=options.get("include_orphans", True),
        )

    def test_present_state_is_not_missing_even_without_registry(self):
        value = bundle(
            references=[reference("sensor.live")],
            states={"sensor.live": {"state": "unavailable"}},
        )
        findings, _, _ = self.classify(value, include_orphans=False)
        self.assertEqual(findings, [])

    def test_missing_reference_enabled_is_high_and_disabled_source_is_medium(self):
        enabled, _, _ = self.classify(
            bundle(references=[reference("sensor.missing")]),
            include_orphans=False,
        )
        disabled, _, _ = self.classify(
            bundle(
                references=[reference("sensor.missing", source_state="off")]
            ),
            include_orphans=False,
        )
        self.assertEqual(enabled[0].finding_type, "missing_entity_reference")
        self.assertEqual(enabled[0].severity, "high")
        self.assertEqual(disabled[0].severity, "medium")

    def test_user_and_integration_disabled_registry_entities_are_distinct(self):
        refs = [
            reference("sensor.user_disabled", source_id="a"),
            reference("sensor.integration_disabled", source_id="b"),
        ]
        registry = {
            "sensor.user_disabled": {"platform": "demo", "disabled_by": "user"},
            "sensor.integration_disabled": {
                "platform": "demo",
                "disabled_by": "integration",
            },
        }
        findings, _, _ = self.classify(
            bundle(references=refs, registry=registry), include_orphans=False
        )
        self.assertEqual(
            {item.disabled_classification for item in findings},
            {"user_disabled", "integration_disabled"},
        )
        self.assertTrue(
            all(item.finding_type == "disabled_entity_reference" for item in findings)
        )

    def test_registry_only_reference_is_conservative(self):
        findings, _, _ = self.classify(
            bundle(
                references=[reference("sensor.registry_only")],
                registry={
                    "sensor.registry_only": {
                        "platform": "demo",
                        "disabled_by": None,
                    }
                },
            ),
            include_orphans=False,
        )
        self.assertEqual(findings[0].finding_type, "registry_only_entity_reference")
        self.assertEqual(findings[0].confidence, "exact")
        self.assertIn("cannot resolve", findings[0].consequence)

    def test_registry_only_exact_consumer_is_not_also_an_orphan_candidate(self):
        findings, _, _ = self.classify(
            bundle(
                references=[reference("sensor.registry_only")],
                registry={
                    "sensor.registry_only": {
                        "platform": "demo",
                        "disabled_by": None,
                    }
                },
            )
        )
        self.assertEqual(
            [item.finding_type for item in findings],
            ["registry_only_entity_reference"],
        )

    def test_paths_group_within_source_but_sources_remain_separate(self):
        refs = [
            reference("sensor.missing", path="$.a"),
            reference("sensor.missing", path="$.b"),
            reference("sensor.missing", source_id="auto-2", path="$.a"),
        ]
        findings, _, _ = self.classify(
            bundle(references=refs), include_orphans=False
        )
        self.assertEqual(len(findings), 2)
        grouped = next(item for item in findings if item.source_id == "auto-1")
        self.assertEqual(grouped.configuration_paths, ("$.a", "$.b"))
        first_ids = [item.finding_id for item in findings]
        second, _, _ = self.classify(bundle(references=list(reversed(refs))), include_orphans=False)
        self.assertEqual(first_ids, [item.finding_id for item in second])

    def test_orphan_candidates_are_one_per_registry_entry_and_never_safe(self):
        findings, _, _ = self.classify(
            bundle(
                registry={
                    "sensor.a": {"platform": "demo", "disabled_by": None},
                    "sensor.b": {"platform": "demo", "disabled_by": "user"},
                }
            )
        )
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(item.severity == "low" for item in findings))
        self.assertTrue(all(not item.remediation_required for item in findings))
        self.assertTrue(all("manual cleanup candidate only" in item.consequence for item in findings))

    def test_orphans_are_suppressed_when_source_filter_hides_consumers(self):
        value = bundle(
            registry={"sensor.a": {"platform": "demo", "disabled_by": None}},
            requested=["automation"],
        )
        findings, _, warnings = self.classify(value)
        self.assertEqual(findings, [])
        self.assertIn("source filtering", warnings[0])

    def test_dynamic_reference_has_no_invented_target(self):
        findings, evidence, _ = self.classify(
            bundle(dynamics=[dynamic(path="$.condition[0].value_template")]),
            include_orphans=False,
        )
        item = findings[0]
        self.assertEqual(item.finding_type, "unresolved_dynamic_reference")
        self.assertIsNone(item.target_entity_id)
        self.assertEqual(item.confidence, "limited")
        self.assertTrue(item.manual_review_required)
        self.assertIn("$.condition[0].value_template", item.configuration_paths)
        self.assertNotIn("target_entity_id", evidence[item.evidence_references[0]].public())


class ServiceContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        METRICS.reset()

    async def test_refresh_first_page_has_multiple_upstream_free_continuations(self):
        refs = [
            reference(f"sensor.missing_{index}", source_id=f"auto-{index}")
            for index in range(7)
        ]
        provider = FakeProvider(bundle(references=refs, cache_hit=False))
        service = ConfigurationIntegrityAnalysisService(
            provider, clock=lambda: ANALYSIS_TIME, cursor_key=b"k" * 32
        )
        first = await service.analyze(
            source_types=list(SOURCE_TYPES),
            finding_types=["missing_entity_reference"],
            include_orphan_candidates=False,
            detail_level="standard",
            limit=2,
            refresh_index=True,
        )
        pages = [first]
        cursor = first.data["pagination"]["next_cursor"]
        while cursor:
            pages.append(
                await service.analyze(
                    source_types=list(SOURCE_TYPES),
                    finding_types=["missing_entity_reference"],
                    include_orphan_candidates=False,
                    detail_level="standard",
                    limit=2,
                    cursor=cursor,
                    refresh_index=False,
                )
            )
            cursor = pages[-1].data["pagination"]["next_cursor"]
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(pages), 4)
        self.assertEqual(
            {page.data["analysis_timestamp"] for page in pages},
            {"2026-07-20T12:00:00Z"},
        )
        self.assertEqual({page.data["finding_count"] for page in pages}, {7})
        health = METRICS.snapshot()["configuration_integrity_analysis"]
        self.assertEqual(health["request_count"], 4)
        self.assertEqual(health["cursor_continuations"], 3)
        self.assertEqual(health["finding_count"], 7)
        self.assertEqual(health["finding_truncation_events"], 0)
        self.assertEqual(health["partial_count"], 1)
        self.assertEqual(health["successful_count"], 0)
        provider_health = METRICS.snapshot()["provider_routing"]
        self.assertEqual(provider_health["requests_by_provider"]["engineering"], 1)
        self.assertEqual(
            provider_health["successful_requests_by_provider"]["engineering"],
            1,
        )

    async def test_counter_invariants_and_dynamic_review(self):
        provider = FakeProvider(
            bundle(
                references=[
                    reference("sensor.missing", source_id="one"),
                    reference("sensor.disabled", source_id="two"),
                ],
                dynamics=[dynamic(source_id="three")],
                registry={
                    "sensor.disabled": {
                        "platform": "demo",
                        "disabled_by": "user",
                    }
                },
            )
        )
        output = await ConfigurationIntegrityAnalysisService(
            provider, clock=lambda: ANALYSIS_TIME
        ).analyze(include_orphan_candidates=False)
        data = output.data
        self.assertEqual(sum(data["findings_by_severity"].values()), data["finding_count"])
        self.assertEqual(sum(data["findings_by_type"].values()), data["finding_count"])
        self.assertEqual(data["unresolved_dynamic_reference_count"], 1)
        self.assertTrue(data["manual_review_required"])
        self.assertEqual(data["dynamic_reference_summary"]["target_entity_ids_inferred"], False)

    async def test_unsupported_requested_source_is_explicitly_partial(self):
        provider = FakeProvider(bundle(unsupported=("dashboard",)))
        output = await ConfigurationIntegrityAnalysisService(provider).analyze()
        item = next(
            value
            for value in output.data["source_coverage_matrix"]
            if value["source_type"] == "dashboard"
        )
        self.assertEqual(item["completeness"], "not_supported")
        self.assertEqual(output.data["final_assessment"], "assessment_incomplete")
        self.assertTrue(output.partial)

    async def test_out_of_scope_dynamic_reference_does_not_require_review(self):
        value = bundle(requested=["automation"])
        value.dynamic_outside_requested_scope_count = 3
        provider = FakeProvider(value)
        output = await ConfigurationIntegrityAnalysisService(provider).analyze(
            source_types=["automation"], include_orphan_candidates=False
        )
        self.assertFalse(output.data["manual_review_required"])
        self.assertEqual(
            output.data["dynamic_reference_summary"][
                "outside_requested_scope_count"
            ],
            3,
        )
        self.assertEqual(
            output.data["dynamic_reference_summary"][
                "unresolved_in_requested_scope_count"
            ],
            0,
        )

    async def test_tampered_mismatched_and_replaced_index_cursors_fail_closed(self):
        refs = [reference(f"sensor.missing_{i}", source_id=str(i)) for i in range(3)]
        provider = FakeProvider(bundle(references=refs))
        service = ConfigurationIntegrityAnalysisService(provider, cursor_key=b"x" * 32)
        first = await service.analyze(limit=1, include_orphan_candidates=False)
        cursor = first.data["pagination"]["next_cursor"]
        with self.assertRaises(GovernanceError) as tampered:
            await service.analyze(
                limit=1,
                cursor=cursor[:-1] + ("a" if cursor[-1] != "a" else "b"),
                include_orphan_candidates=False,
            )
        self.assertEqual(tampered.exception.code, ErrorCode.INVALID_CURSOR)
        with self.assertRaises(GovernanceError) as mismatch:
            await service.analyze(
                source_types=["automation"],
                limit=1,
                cursor=cursor,
                include_orphan_candidates=False,
            )
        self.assertEqual(mismatch.exception.code, ErrorCode.STALE_CURSOR)
        provider.identity["generation"] = 2
        with self.assertRaises(GovernanceError) as replaced:
            await service.analyze(
                limit=1, cursor=cursor, include_orphan_candidates=False
            )
        self.assertEqual(replaced.exception.code, ErrorCode.STALE_CURSOR)

    async def test_expired_snapshot_uses_stale_cursor_contract(self):
        provider = FakeProvider(
            bundle(references=[reference(f"sensor.missing_{i}") for i in range(2)])
        )
        service = ConfigurationIntegrityAnalysisService(provider)
        first = await service.analyze(limit=1, include_orphan_candidates=False)
        cursor = first.data["pagination"]["next_cursor"]
        for snapshot in service.pagination_snapshots._values.values():
            snapshot.expires_at = 0.0
        with self.assertRaises(GovernanceError) as expired:
            await service.analyze(
                limit=1, cursor=cursor, include_orphan_candidates=False
            )
        self.assertEqual(expired.exception.code, ErrorCode.STALE_CURSOR)
        self.assertEqual(expired.exception.details["reason"], "snapshot_expired")

    async def test_validation_is_field_level_and_provider_free(self):
        provider = FakeProvider(bundle())
        service = ConfigurationIntegrityAnalysisService(provider)
        cases = (
            ({"detail_level": "raw"}, "detail_level"),
            ({"limit": 0}, "limit"),
            ({"limit": 101}, "limit"),
            ({"source_types": ["yaml"]}, "source_types"),
            ({"finding_types": ["orphan"]}, "finding_types"),
            ({"include_orphan_candidates": "yes"}, "include_orphan_candidates"),
            (
                {"cursor": "opaque", "refresh_index": True},
                "refresh_index",
            ),
            (
                {"source_types": ["automation"] * (len(SOURCE_TYPES) + 1)},
                "source_types",
            ),
        )
        for values, field in cases:
            with self.subTest(field=field), self.assertRaises(InvalidRequestError) as error:
                await service.analyze(**values)
            self.assertEqual(error.exception.details["field"], field)
            self.assertEqual(
                error.exception.details["operation"],
                "configuration_integrity_analysis",
            )
        self.assertEqual(provider.calls, [])
        snapshot = METRICS.snapshot()
        health = snapshot["configuration_integrity_analysis"]
        self.assertEqual(health["failed_count"], 0)
        self.assertEqual(health["cursor_failure_count"], 1)
        self.assertEqual(health["finding_count"], 0)
        self.assertEqual(
            snapshot["validation_error_counts"]["request_validation"],
            len(cases) - 1,
        )
        self.assertEqual(snapshot["cursor_error_counts"]["invalid_cursor"], 1)


class ProviderAndPublicContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_uses_one_state_and_one_registry_inventory(self):
        class Snapshot:
            findings = []
            dynamic_references = []
            coverage = []
            fingerprint = "fp"
            generation = 1
            built_at = "2026-07-20T00:00:00Z"
            build_duration_ms = 3.0

        class Index:
            calls = 0

            async def get(self, *, refresh=False):
                self.calls += 1
                return Snapshot(), False, 0.5

            def active_identity(self):
                return {"valid": True, "generation": 1, "fingerprint": "fp"}

        class Rest:
            calls = []

            async def request(self, method, path):
                self.calls.append((method, path))
                return [{"entity_id": "sensor.live", "state": "unavailable"}]

        class WebSocket:
            calls = []

            async def command(self, payload):
                self.calls.append(payload)
                return [
                    {
                        "entity_id": "sensor.registered",
                        "platform": "demo",
                        "disabled_by": None,
                    }
                ]

        index, rest, websocket = Index(), Rest(), WebSocket()
        provider = DirectHaIntegrityProvider(index, rest, websocket)
        result = await provider.fetch(
            type(
                "Request",
                (),
                {
                    "capability": ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
                    "query": {
                        "source_types": list(SOURCE_TYPES),
                        "refresh_index": False,
                    },
                },
            )()
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(index.calls, 1)
        self.assertEqual(rest.calls, [("GET", "/states")])
        self.assertEqual(websocket.calls, [{"type": "config/entity_registry/list"}])

    async def test_inventory_failure_is_visible_and_never_becomes_clean(self):
        class Snapshot:
            findings = []
            dynamic_references = []
            coverage = []
            fingerprint = "fp"
            generation = 1
            built_at = "2026-07-20T00:00:00Z"
            build_duration_ms = 3.0

        class Index:
            async def get(self, *, refresh=False):
                return Snapshot(), False, 0.5

            def active_identity(self):
                return {"valid": True, "generation": 1, "fingerprint": "fp"}

        class FailingRest:
            async def request(self, method, path):
                raise TimeoutError("untrusted upstream detail")

        class WebSocket:
            async def command(self, payload):
                return []

        result = await DirectHaIntegrityProvider(
            Index(), FailingRest(), WebSocket()
        ).fetch(
            type(
                "Request",
                (),
                {
                    "capability": ProviderCapability.CONFIGURATION_INTEGRITY_ANALYSIS,
                    "query": {"source_types": ["automation"], "refresh_index": False},
                },
            )()
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.completeness, ProviderCompleteness.PARTIAL)
        states = next(
            item
            for item in result.data.coverage
            if item.source_type == "current_states"
        )
        self.assertEqual(states.completeness, "failed")
        self.assertNotIn("untrusted upstream detail", json.dumps(states.public()))

    def test_capability_routing_catalog_schema_and_tool_count(self):
        route = routing_for_tool("configuration_integrity_analysis")
        self.assertEqual(route.route, CapabilityRoute.ENGINEERING_NATIVE)
        self.assertEqual(route.preferred_provider, "engineering")
        self.assertEqual(
            ANALYTICAL_PROVIDER_POLICIES["configuration_integrity_analysis"]["policy_id"],
            "global_configuration_integrity_read",
        )
        capability = next(
            item
            for item in BETA_NATIVE_CAPABILITIES
            if item["tool"] == "configuration_integrity_analysis"
        )
        self.assertEqual(
            capability,
            {
                "tool": "configuration_integrity_analysis",
                "category": "analysis",
                "status": "beta_native",
                "risk": "read",
                "additive": True,
                "routing": "engineering_native",
                "provider": "engineering",
                "policy": "global_configuration_integrity_read",
            },
        )
        matrix = next(
            item
            for item in CAPABILITY_PROVIDER_MATRIX
            if item["tool"] == "configuration_integrity_analysis"
        )
        self.assertEqual(matrix["standard_ha_mcp_coverage"], "unavailable")
        self.assertEqual(matrix["fallback_policy"], "none")
        self.assertEqual(
            {item["capability"] for item in PLANNED_CAPABILITIES},
            set(),
        )
        tools = get_registered_server()._tool_manager._tools
        self.assertEqual(len(tools), 40)
        self.assertIn("configuration_integrity_analysis", tools)
        schema = tools["configuration_integrity_analysis"].parameters
        json.dumps(schema)
        self.assertEqual(
            set(schema["properties"]),
            {
                "source_types",
                "finding_types",
                "include_orphan_candidates",
                "detail_level",
                "limit",
                "cursor",
                "refresh_index",
            },
        )
        self.assertEqual(schema.get("required", []), [])
        self.assertEqual(schema["properties"]["limit"]["maximum"], 100)
        self.assertEqual(schema["properties"]["limit"]["minimum"], 1)
        self.assertEqual(schema["properties"]["limit"]["default"], 20)
        self.assertEqual(schema["properties"]["include_orphan_candidates"]["default"], True)
        self.assertEqual(schema["properties"]["detail_level"]["default"], "standard")
        self.assertEqual(schema["properties"]["cursor"]["default"], "")
        self.assertEqual(schema["properties"]["refresh_index"]["default"], False)


class AuditContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_audits_bounded_intent_and_never_raw_cursor(self):
        secret = "integrity-audit-access-secret"
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            settings = Settings(
                ha_url="http://supervisor/core",
                ha_token="test-token",
                access_secret=secret,
                port=8100,
                audit_path=str(audit_path),
                rate_limit_per_minute=120,
                rate_limit_burst=25,
                destructive_services=frozenset(),
                audit_enabled=True,
                audit_max_payload_chars=8192,
                log_level="INFO",
                ha_timeout_seconds=60,
                response_size_limit=60_000,
                redaction_enabled=True,
            )

            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})

            gateway = AuthenticatedMcpGateway(
                app, settings, AuditLogger(str(audit_path), secret)
            )
            cursor = "signed-secret-like-cursor-material"
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "integrity-audit-request",
                    "method": "tools/call",
                    "params": {
                        "name": "configuration_integrity_analysis",
                        "arguments": {
                            "source_types": ["automation"],
                            "finding_types": ["missing_entity_reference"],
                            "include_orphan_candidates": False,
                            "detail_level": "summary",
                            "limit": 5,
                            "cursor": cursor,
                            "refresh_index": False,
                        },
                    },
                }
            ).encode()
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                return None

            await gateway(
                {
                    "type": "http",
                    "method": "POST",
                    "path": f"/{secret}/mcp",
                    "raw_path": f"/{secret}/mcp".encode(),
                    "headers": [],
                    "client": ("127.0.0.1", 1),
                },
                receive,
                send,
            )
            record = json.loads(audit_path.read_text().splitlines()[-1])
            self.assertEqual(record["tool_name"], "configuration_integrity_analysis")
            self.assertEqual(record["access"], "read")
            self.assertEqual(record["capability_classification"], "beta_native")
            self.assertTrue(record["parameters"]["cursor_present"])
            self.assertNotIn("cursor", record["parameters"])
            self.assertNotIn(cursor, audit_path.read_text())
            self.assertNotIn(secret, audit_path.read_text())


if __name__ == "__main__":
    unittest.main()
