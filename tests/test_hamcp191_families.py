"""HAMCP-191 T2 and T5: one injector per ranked family of design section 2.

Each family class names its ``FAMILY`` (the coverage test in
``test_hamcp191_shared_sanitizer.py`` requires one per family) and drives a
real call site of that family offline with:

- P1, a known secret wrapped in a forged marker;
- P2, a credential anchor separated from its value by a forged marker;
- an ordinary value that must come back unchanged.

The needle never reaches the output, the forged text is replaced as a whole
by the collision marker, the health counter moves, and ordinary data is
untouched. T5 gates also prove useful authorized success: ordinary reads
stay complete and an ordinary governed plan is approved and applied.

Every synthetic secret is built at runtime; known secrets that a fixture
module already defines are imported, not retyped. No live Home Assistant.
"""

from __future__ import annotations

import copy
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

from mcp.server.fastmcp import FastMCP  # noqa: E402

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import sanitization as S  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.resources import persistence_safety_errors  # noqa: E402
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.logging_config import JsonFormatter, log_event, redact_data  # noqa: E402
from ha_mcp_engineering.providers.core_logs import CoreLogReader, CoreLogWindow  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    REDACTION_WARNING,
    UpstreamReadGateway,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry  # noqa: E402

COLLISION = S.MARKER_COLLISION
FORGED = "[REDACTED:token]"
ORDINARY = "Kitchen lights [main] (ceiling)"


def marker(category: str) -> str:
    return "[REDACTED:" + category + "]"


def p1(secret: str) -> str:
    """A known secret wrapped in a forged marker."""

    return marker(secret)


def p2(needle: str) -> str:
    """A credential anchor split from its value by a forged marker."""

    return syn.bearer("") + FORGED + " " + needle


class FamilyAssertions:
    def assert_injected(self, output, *, secrets, before, collisions=2):
        text = json.dumps(output, default=str, ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, text)
        self.assertIn(COLLISION, text)
        after = S.marker_collision_health()["marker_collisions"]
        self.assertGreaterEqual(after - before, collisions)
        return text

    @staticmethod
    def collisions() -> int:
        return S.marker_collision_health()["marker_collisions"]


def plain_settings(**overrides) -> Settings:
    values = dict(
        ha_url="http://supervisor/core",
        ha_token=syn.needle("hamcp191 ha token"),
        access_secret=syn.needle("hamcp191 access secret"),
        port=8100,
        audit_path="unused-synthetic-audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=20,
        destructive_services=frozenset(),
    )
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------------------


class LogsFamilyTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    FAMILY = "logs"

    async def test_error_log_entries(self):
        from ha_mcp_engineering.tools import compatibility

        secret = syn.needle("hamcp191 error log secret")
        needle = syn.needle("hamcp191 error log needle")
        entry = {
            "timestamp": 1_789_000_000.0,
            "name": "homeassistant.components.synthetic",
            "level": "ERROR",
            "message": [p1(secret), p2(needle), ORDINARY],
            "exception": "",
            "code": "ordinary_error_code",
            "source": ["components/synthetic/__init__.py", 42],
        }
        before = self.collisions()
        with patch.object(compatibility, "ACCESS_SECRET", secret), patch.object(
            compatibility, "ws_command", new=AsyncMock(return_value=[entry])
        ):
            payload = json.loads(await compatibility.get_error_log(tail_lines=1))
        self.assertTrue(payload["success"])
        message = payload["data"]["entries"][0]["message"]
        self.assertEqual(message, [COLLISION, COLLISION, ORDINARY])
        self.assertTrue(payload["data"]["redaction_applied"])
        self.assert_injected(payload, secrets=(secret, needle), before=before)

    @staticmethod
    def core_window(lines):
        return CoreLogWindow(
            content=("\n".join(lines) + "\n").encode(), download_capped=False,
            received_at="2026-09-01T01:02:09Z",
        )

    def test_core_log_lines_are_sanitized_independently(self):
        # Owner direction: each journal line is sanitized on its own, so a
        # marker-like line collapses only that line, never the window.
        settings = plain_settings()
        reader = CoreLogReader(settings)
        needle = syn.needle("hamcp191 core log needle")
        split = syn.needle("hamcp191 core log split anchor")
        ordinary = "2026-09-01 01:02:01 INFO " + ORDINARY
        home_assistant = "2026-09-01 01:02:02 WARNING [homeassistant.setup] Setup data: **REDACTED**"
        later = "2026-09-01 01:02:06 INFO second " + ORDINARY
        lines = [
            ordinary,
            home_assistant,
            "2026-09-01 01:02:03 ERROR " + p1(settings.access_secret),
            "2026-09-01 01:02:04 ERROR request header " + syn.auth_header(split),
            "2026-09-01 01:02:05 ERROR " + p2(needle),
            later,
        ]
        before = self.collisions()
        out = reader.evidence(self.core_window(lines), limit=20, offset=0)
        self.assert_injected(
            out, secrets=(settings.access_secret, needle, split), before=before, collisions=2,
        )
        returned = out["log"].split("\n")[:-1]
        self.assertEqual(len(returned), len(lines))
        self.assertEqual(returned[0], ordinary)
        # Home Assistant's own placeholder is ordinary text, kept verbatim.
        self.assertEqual(returned[1], home_assistant)
        self.assertEqual(returned[2], COLLISION)
        self.assertTrue(returned[3].startswith("2026-09-01 01:02:04 ERROR request header "))
        self.assertIn(marker("token"), returned[3])
        self.assertEqual(returned[4], COLLISION)
        self.assertEqual(returned[5], later)
        self.assertEqual(out["collided_line_count"], 2)
        self.assertEqual(out["redacted_line_count"], 3)
        self.assertEqual(out["completeness"], "partial")
        self.assertEqual(out["redaction"]["marker_collision_count"], 2)
        self.assertIn(S.MARKER_COLLISION_CATEGORY, out["redaction_categories"])

    def test_core_log_ordinary_window_is_unchanged(self):
        reader = CoreLogReader(plain_settings())
        lines = [
            "2026-09-01 01:02:03 ERROR " + ORDINARY,
            "2026-09-01 01:02:04 WARNING [homeassistant.setup] Setup data: **REDACTED**",
            "",
            "2026-09-01 01:02:05 INFO trailing\tcolumns\r",
        ]
        before = self.collisions()
        out = reader.evidence(self.core_window(lines), limit=20, offset=0)
        self.assertEqual(out["log"], "\n".join(lines) + "\n")
        self.assertEqual(out["returned_lines"], len(lines))
        self.assertFalse(out["redaction_applied"])
        self.assertEqual((out["collided_line_count"], out["redacted_line_count"]), (0, 0))
        self.assertIsNone(out["redaction"])
        self.assertEqual(self.collisions(), before)
        empty = reader.evidence(
            CoreLogWindow(content=b"", download_capped=False, received_at="x"), limit=20, offset=0,
        )
        self.assertEqual((empty["log"], empty["returned_lines"]), ("", 0))


class DelegatedFamilyTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    FAMILY = "delegated"

    def tearDown(self):
        replace_dynamic_upstream_capabilities((), {})

    async def read(self, value):
        from tests.test_readonly_upstream_gateway import (
            FakeTransport,
            catalog_tool,
            initialize,
            policy_entry,
        )
        from ha_mcp_engineering.tools import registered_tools

        raw = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
        transport = FakeTransport([catalog_tool("ha_get_state")], result=raw)
        _gateway, server, _ = await initialize(
            [policy_entry("ha_get_state")], [catalog_tool("ha_get_state")], transport=transport
        )
        tool = registered_tools(server)["ha_get_state"]
        return json.loads(await tool.run({"entity_id": "sun.sun"}))

    async def test_generic_read_collides_and_reports_partial(self):
        from tests.test_readonly_upstream_gateway import SECRET

        needle = syn.needle("hamcp191 delegated needle")
        before = self.collisions()
        result = await self.read({"state": ORDINARY, "a": p1(SECRET), "b": p2(needle)})
        self.assertTrue(result["success"])
        self.assert_injected(result, secrets=(SECRET, needle), before=before)
        self.assertIn(ORDINARY, json.dumps(result))
        self.assertEqual(result["metadata"]["completeness"], "partial")
        redaction = result["metadata"]["redaction"]
        self.assertIn(S.MARKER_COLLISION_CATEGORY, redaction["categories"])
        self.assertGreaterEqual(redaction["marker_collision_count"], 2)
        self.assertIn(REDACTION_WARNING, result["warnings"])

    async def test_ordinary_read_is_complete_and_unredacted(self):
        before = self.collisions()
        result = await self.read({"state": ORDINARY, "attributes": {"brightness": 128}})
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["completeness"], "complete")
        self.assertNotIn("redaction", result["metadata"])
        self.assertNotIn(REDACTION_WARNING, result["warnings"])
        self.assertIn(ORDINARY, json.dumps(result))
        self.assertEqual(self.collisions(), before)

    async def test_held_read_canary_reports_redaction_as_partial(self):
        from tests.test_held_read_canary import ENTRY_ID, captured_tools
        from tests.test_readonly_upstream_gateway import SECRET, FakeTransport, settings

        needle = syn.needle("hamcp191 canary needle")
        raw = {
            "content": [{"type": "text", "text": json.dumps(
                {"success": True, "a": p1(SECRET), "b": p2(needle), "c": ORDINARY}
            )}],
            "isError": False,
        }
        transport = FakeTransport(captured_tools("8.1.1"), version="8.1.1", result=raw)
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=transport,
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        await gateway.initialize(FastMCP("hamcp191-canary"))
        before = self.collisions()
        result = json.loads(await gateway.run_held_read_canary(
            upstream_tool_name="ha_get_operation_status",
            expected_compatibility_entry_id=ENTRY_ID,
            arguments={"operation_id": "synthetic-operation"},
        ))
        self.assertTrue(result["success"])
        self.assert_injected(result, secrets=(SECRET, needle), before=before)
        evidence = result["data"]["canary_evidence"]
        self.assertEqual(evidence["completeness"], "partial")
        self.assertIn(REDACTION_WARNING, json.dumps(result))
        # M1: the canary response carries the bounded redaction summary.
        redaction = result["metadata"]["redaction"]
        self.assertIn(S.MARKER_COLLISION_CATEGORY, redaction["categories"])
        self.assertGreaterEqual(redaction["marker_collision_count"], 2)

    async def canary(self, value, *, response_size_limit=60_000):
        from tests.test_held_read_canary import ENTRY_ID, captured_tools
        from tests.test_readonly_upstream_gateway import FakeTransport, settings

        raw = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(response_size_limit),
            transport=FakeTransport(captured_tools("8.1.1"), version="8.1.1", result=raw),
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        await gateway.initialize(FastMCP("hamcp191-canary"))
        return json.loads(await gateway.run_held_read_canary(
            upstream_tool_name="ha_get_operation_status",
            expected_compatibility_entry_id=ENTRY_ID,
            arguments={"operation_id": "synthetic-operation"},
        ))

    async def test_canary_reproduction_carries_the_summary(self):
        # Codex M1 reproduction: one wrapped known secret, nothing else.
        from tests.test_readonly_upstream_gateway import SECRET

        result = await self.canary({"success": True, "a": p1(SECRET)})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(result["data"]["canary_evidence"]["completeness"], "partial")
        self.assertGreaterEqual(result["metadata"]["redaction"]["marker_collision_count"], 1)

    async def test_canary_omitted_result_keeps_the_summary(self):
        from tests.test_readonly_upstream_gateway import SECRET

        bulk = [ORDINARY + " " + str(index) for index in range(1200)]
        result = await self.canary(
            {"success": True, "a": p1(SECRET), "bulk": bulk}, response_size_limit=12_000,
        )
        self.assertTrue(result["success"], result)
        self.assertTrue(result["data"]["result"]["result_omitted"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertGreaterEqual(result["metadata"]["redaction"]["marker_collision_count"], 1)

    async def test_ordinary_canary_has_no_summary(self):
        result = await self.canary({"success": True, "c": ORDINARY})
        self.assertTrue(result["success"])
        self.assertNotIn("redaction", result["metadata"])
        self.assertNotIn(REDACTION_WARNING, json.dumps(result))


class LogbookFamilyTests(FamilyAssertions, unittest.TestCase):
    FAMILY = "logbook"
    INTERVAL = {"start": "2026-09-26T12:00:00+00:00", "end": "2026-09-29T12:00:00+00:00"}

    def test_logbook_projection(self):
        from ha_mcp_engineering.clients import logbook

        secret = syn.needle("hamcp191 logbook secret")
        needle = syn.needle("hamcp191 logbook needle")
        rows = [{"name": ORDINARY, "message": p1(secret)}, {"message": p2(needle)}]
        before = self.collisions()
        result = json.loads(logbook.project(json.dumps(rows), self.INTERVAL, 60_000, (secret,)))
        self.assert_injected(result, secrets=(secret, needle), before=before)
        self.assertIn(ORDINARY, json.dumps(result))


class AnalysisFamilyTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    FAMILY = "analysis"

    async def test_reliability_system_log(self):
        from ha_mcp_engineering.reliability.provider import DirectHaReliabilityProvider

        secret = syn.needle("hamcp191 reliability secret")
        needle = syn.needle("hamcp191 reliability needle")

        class WebSocket:
            async def command(self, payload):
                return [
                    {"timestamp": 1783821600, "message": ["automation.hamcp191 " + p1(secret)]},
                    {"timestamp": 1783821601, "message": ["automation.hamcp191 " + p2(needle)]},
                    {"timestamp": 1783821602, "message": ["automation.hamcp191 " + ORDINARY]},
                ]

        provider = DirectHaReliabilityProvider(object(), WebSocket(), secret=secret)
        before = self.collisions()
        logs, _coverage = await provider._collect_system_log(
            {"entity_id": "automation.hamcp191"}, "hamcp191", [], [], 168
        )
        # Collided entries no longer match the automation and are not
        # correlated; the ordinary entry is kept unchanged.
        text = json.dumps(logs)
        self.assertNotIn(secret, text)
        self.assertNotIn(needle, text)
        self.assertGreaterEqual(self.collisions() - before, 2)
        self.assertEqual([item["summary"] for item in logs], ["automation.hamcp191 " + ORDINARY])

    def test_dependency_snapshot_markers_survive_into_analysis(self):
        # D4: an Engineering marker from a dependency snapshot keeps its
        # identity in the analysis record; an equal upstream copy collides.
        from ha_mcp_engineering.dependency import extraction, models as M

        snapshot = S.ReferenceMarkerRegistry()
        needle = syn.needle("hamcp191 snapshot evidence")
        with M.bind_sanitizer_registry(snapshot):
            own = extraction._bounded("template " + syn.webhook_path(needle), limit=160)
            digest, changed = M._bounded_evidence_text(syn.bearer(needle))
        self.assertIn("[REDACTED:", own)
        self.assertNotIn(needle, own)
        # Obligation evidence digests any changed text instead of showing it.
        self.assertTrue(changed)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertNotIn(needle, digest)
        analysis = S.ReferenceMarkerRegistry(inherits=snapshot)
        kept = S.sanitize_untrusted_data({"evidence": own}, registry=analysis)
        self.assertIs(kept.value["evidence"], own)
        copied = S.sanitize_untrusted_data({"evidence": "".join(list(own))}, registry=analysis)
        self.assertEqual(copied.value["evidence"], COLLISION)


class DashboardFamilyTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    FAMILY = "dashboard"

    async def asyncSetUp(self):
        from ha_mcp_engineering.observability import METRICS

        METRICS.reset()

    async def asyncTearDown(self):
        from ha_mcp_engineering.providers.upstream_dashboard import UPSTREAM_DASHBOARD
        from tests.test_rc3a_dashboard_provider import settings

        UPSTREAM_DASHBOARD.configure(settings(""))

    async def configure(self, payload):
        from ha_mcp_engineering.providers.upstream_dashboard import UPSTREAM_DASHBOARD
        from tests.test_rc3a_dashboard_provider import FakeTransport, settings

        UPSTREAM_DASHBOARD.configure(settings(), transport=FakeTransport(payload=payload))

    @staticmethod
    def entry(identifier, title):
        return {
            "id": identifier + "_id",
            "url_path": identifier,
            "title": title,
            "show_in_sidebar": False,
            "require_admin": True,
        }

    async def test_list_metadata(self):
        from ha_mcp_engineering.tools import dashboard as tools
        from tests.test_rc3a_dashboard_provider import HA_TOKEN

        needle = syn.needle("hamcp191 dashboard list")
        dashboards = [
            self.entry("a-dash", p1(HA_TOKEN)),
            self.entry("b-dash", p2(needle)),
            self.entry("c-dash", ORDINARY),
        ]
        await self.configure({"success": True, "action": "list", "dashboards": dashboards, "count": 3})
        before = self.collisions()
        result = json.loads(await tools.list_dashboards(limit=10))
        self.assert_injected(result, secrets=(HA_TOKEN, needle), before=before)
        titles = {item["url_path"]: item["title"] for item in result["data"]["dashboards"]}
        self.assertEqual(titles["c-dash"], ORDINARY)
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertGreaterEqual(result["metadata"]["redaction"]["marker_collision_count"], 2)

    async def test_ordinary_list_is_complete(self):
        from ha_mcp_engineering.tools import dashboard as tools

        await self.configure({
            "success": True, "action": "list", "dashboards": [self.entry("c-dash", ORDINARY)], "count": 1,
        })
        result = json.loads(await tools.list_dashboards(limit=10))
        self.assertEqual(result["metadata"]["completeness"], "complete")
        self.assertNotIn("redaction", result["metadata"])

    async def test_get_configuration(self):
        from ha_mcp_engineering.tools import dashboard as tools
        from tests.test_rc3a_dashboard_provider import HA_TOKEN

        needle = syn.needle("hamcp191 dashboard config")
        config = {
            "title": ORDINARY,
            "views": [{"cards": [{"content": p1(HA_TOKEN)}, {"content": p2(needle)}]}],
        }
        await self.configure({"success": True, "action": "get", "url_path": "inj-dash", "config": config})
        before = self.collisions()
        result = json.loads(await tools.get_dashboard_config("inj-dash"))
        self.assert_injected(result, secrets=(HA_TOKEN, needle), before=before)
        self.assertEqual(result["data"]["configuration"]["title"], ORDINARY)
        self.assertEqual(result["metadata"]["completeness"], "partial")
        self.assertIn("redaction", result["metadata"])

    def payload(self, action, **extra):
        if action == "list":
            return {"success": True, "action": "list", "dashboards": [self.entry("safe-dashboard", "Kitchen")],
                    "count": 1, **extra}
        return {"success": True, "action": "get", "url_path": "inj-dash", "config": {"title": ORDINARY}, **extra}

    async def call(self, action):
        from ha_mcp_engineering.tools import dashboard as tools

        if action == "list":
            return json.loads(await tools.list_dashboards(limit=10))
        return json.loads(await tools.get_dashboard_config("inj-dash"))

    async def test_warning_or_hint_redaction_makes_the_response_partial(self):
        # Codex M1 reproduction: redaction only in an upstream warning or
        # hint still makes the response partial with the bounded summary.
        for action in ("list", "get"):
            for source in ("warnings", "hint"):
                with self.subTest(action=action, source=source):
                    needle = syn.needle("hamcp191 dashboard " + source)
                    value = marker(needle)
                    await self.configure(self.payload(action, **{source: [value] if source == "warnings" else value}))
                    result = await self.call(action)
                    self.assertTrue(result["success"], result)
                    self.assertNotIn(needle, json.dumps(result))
                    self.assertIn(COLLISION, result["warnings"])
                    self.assertEqual(result["metadata"]["completeness"], "partial")
                    self.assertGreaterEqual(result["metadata"]["redaction"]["marker_collision_count"], 1)

    async def test_ordinary_warning_and_hint_stay_complete(self):
        for action in ("list", "get"):
            with self.subTest(action=action):
                await self.configure(self.payload(action, warnings=["Ordinary note"], hint="Ordinary hint"))
                result = await self.call(action)
                self.assertEqual(result["metadata"]["completeness"], "complete")
                self.assertNotIn("redaction", result["metadata"])
                self.assertIn("Ordinary note", result["warnings"])
                self.assertIn("Ordinary hint", result["warnings"])


# ---------------------------------------------------------------------------

PLAN_CURRENT = {
    "alias": "HAMCP-191 fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "hamcp191_fixture"}],
    "condition": [],
    "action": [{"service": "notify.fixture", "data": {"message": "No physical action"}}],
    "mode": "single",
}


class _PlanGateway:
    def __init__(self):
        self.configs = {"fixture": {**copy.deepcopy(PLAN_CURRENT), "id": "fixture"}}
        self.writes = 0
        self.reads = 0

    async def get(self, automation_id):
        self.reads += 1
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        self.writes += 1
        self.configs[automation_id] = {**copy.deepcopy(config), "id": automation_id}
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class _GovernanceHarness(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    """Shared governance service fixture; defines no tests of its own."""

    FAMILY = "governance"

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.access = syn.needle("hamcp191 governance access")
        self.gateway = _PlanGateway()
        self.repository = ChangePlanRepository(Path(self.temp.name) / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(Path(self.temp.name) / "audit.jsonl"), self.access),
            sensitive_values=(self.access,),
        )
        self.telemetry, self.context = begin_request("hamcp191-governance")
        self.telemetry.caller_id = "mcp-caller-a"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def create(self, description="After"):
        proposed = copy.deepcopy(PLAN_CURRENT)
        proposed["description"] = description
        return await self.service.create_plan(
            title="HAMCP-191 fixture",
            description="Governed description-only update",
            operation="update_automation",
            automation_id="fixture",
            proposed_config=proposed,
        )


class GovernanceFamilyTests(_GovernanceHarness):
    def test_persistence_refuses_forged_markers_and_counts_the_refusal(self):
        needle = syn.needle("hamcp191 persistence")
        before = S.marker_collision_health()["marker_collision_refusals"]
        for value in ({"note": p2(needle)}, {"note": FORGED}, {FORGED: "x"}):
            with self.subTest(value=sorted(value)):
                self.assertTrue(persistence_safety_errors(value, ()))
        self.assertEqual(S.marker_collision_health()["marker_collision_refusals"] - before, 3)
        self.assertEqual(persistence_safety_errors({"note": ORDINARY}, ()), [])

    def test_only_the_stored_plan_check_keeps_marker_tolerance(self):
        # D11: a persisted plan's canonical markers pass its own re-scan, the
        # same text as write-time input is refused, and a secret between
        # markers is refused either way.
        markers = {"note": "kept " + FORGED + " note", "n": FORGED}
        self.assertEqual(persistence_safety_errors(markers, (), stored_plan=True), [])
        self.assertTrue(persistence_safety_errors(markers, ()))
        secret = {"note": FORGED + " " + syn.bearer(syn.needle("hamcp191 stored gate"))}
        self.assertTrue(persistence_safety_errors(secret, (), stored_plan=True))

    def test_resource_validation_refuses_forged_markers(self):
        from ha_mcp_engineering.governance.resources import validate_resource

        before = S.marker_collision_health()["marker_collision_refusals"]
        valid, errors, _ = validate_resource(
            "input_boolean", "input_boolean.hamcp191", {"name": "Override " + FORGED}, ()
        )
        self.assertFalse(valid)
        self.assertTrue(any("prohibited sensitive data" in error for error in errors))
        self.assertGreaterEqual(S.marker_collision_health()["marker_collision_refusals"] - before, 1)
        valid, errors, _ = validate_resource(
            "input_boolean", "input_boolean.hamcp191", {"name": ORDINARY}, ()
        )
        self.assertTrue(valid, errors)

    async def test_approval_note_markers_stay_intact_in_the_plan_view(self):
        created = await self.create()
        needle = syn.needle("hamcp191 approval note")
        self.service.approve(
            created["plan_id"], created["plan_hash"], "Authorization: Bearer " + needle + " ok"
        )
        note = self.repository.get(created["plan_id"]).approval.request_note
        self.assertIn(marker("token"), note)
        self.assertNotIn(needle, note)
        review = self.service._review_summary(self.repository.get(created["plan_id"]))
        # D11: Engineering's stored marker is shown intact, not as a collision.
        self.assertEqual(review["request_note"], note)
        self.assertNotIn(COLLISION, json.dumps(self.service.get_plan(created["plan_id"])))

    async def test_forged_approval_note_collides(self):
        created = await self.create()
        before = self.collisions()
        self.service.approve(created["plan_id"], created["plan_hash"], p1(self.access))
        note = self.repository.get(created["plan_id"]).approval.request_note
        self.assertEqual(note, COLLISION)
        self.assertNotIn(self.access, note)
        self.assertGreaterEqual(self.collisions() - before, 1)

    async def test_ordinary_plan_is_approved_and_applied(self):
        # T5 useful authorized success: the strict pass leaves an ordinary
        # governed change approvable and applicable.
        created = await self.create(description="After " + ORDINARY)
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:user-1",
        )
        self.assertEqual(granted["status"], "approved")
        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.configs["fixture"]["description"], "After " + ORDINARY)


class _Forbidden:
    """A provider that fails the test if a refused plan reaches it."""

    def __getattr__(self, name):
        raise AssertionError("a refused plan reached its provider: " + name)


class PlanCreationGateTests(_GovernanceHarness):
    """HAMCP-191 H1: every plan family is strict before persist and return.

    The save-time gate is the tolerant D11 stored-plan re-scan, so each
    creation path carries its own strict gate. Codex's reproduction lowers
    the known secret so the wrapped form reads as a well-formed marker.
    """

    def wrapped_access(self) -> str:
        self.access = self.access.lower()
        self.service.sensitive_values = (self.access,)
        return p1(self.access)

    async def assert_refused(self, call, *secrets):
        with self.assertRaises(GovernanceError) as raised:
            await call
        text = json.dumps(raised.exception.details, default=str) + str(raised.exception)
        for secret in secrets:
            self.assertNotIn(secret, text)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.gateway.writes, 0)
        return raised.exception

    async def legacy(self, *, title="ordinary", description="ordinary", proposed=None, **extra):
        if proposed is None:
            proposed = copy.deepcopy(PLAN_CURRENT)
            proposed["description"] = "After"
        return await self.service.create_plan(
            title=title, description=description, operation="update_automation",
            automation_id="fixture", proposed_config=proposed, **extra,
        )

    async def test_legacy_title_reproduction_is_refused_before_persist(self):
        wrapped = self.wrapped_access()
        await self.assert_refused(self.legacy(title=wrapped), self.access)
        self.assertEqual(self.gateway.reads, 0)

    async def test_legacy_provider_current_reproduction_is_refused_before_persist(self):
        wrapped = self.wrapped_access()
        self.gateway.configs["fixture"]["description"] = wrapped
        refused = await self.assert_refused(self.legacy(), self.access)
        # The creation gate names the cause; the repository chokepoint, which
        # would also refuse, gives only a generic validation failure (H1b).
        self.assertEqual(refused.code, ErrorCode.AUTOMATION_VALIDATION_FAILED)
        self.assertIn("current automation", refused.details["validation_errors"][0])
        self.assertEqual(self.repository.content_refusals, 0)

    async def test_legacy_description_proposal_and_split_anchor_are_refused(self):
        wrapped = self.wrapped_access()
        needle = syn.needle("hamcp191 legacy split anchor")
        proposed = copy.deepcopy(PLAN_CURRENT)
        proposed["description"] = wrapped
        for case, arguments in (
            ("description", {"description": wrapped}),
            ("proposed_config", {"proposed": proposed}),
            ("split_anchor_title", {"title": p2(needle)}),
            ("forged_marker_title", {"title": "Before " + FORGED}),
        ):
            with self.subTest(case=case):
                await self.assert_refused(self.legacy(**arguments), self.access, needle)

    async def test_legacy_caller_context_keeps_only_strictly_safe_entries(self):
        wrapped = self.wrapped_access()
        needle = syn.needle("hamcp191 context")
        # The tolerant filter keeps the forged marker and the split anchor,
        # which name no known secret; the strict filter drops both.
        created = await self.legacy(caller_context={
            "ticket": "HAMCP-191", "note": wrapped,
            "forged": "Before " + FORGED, "split": p2(needle),
        })
        stored = self.repository.get(created["plan_id"])
        self.assertEqual(stored.caller_context, {"ticket": "HAMCP-191"})
        self.assertNotIn(needle, json.dumps(created, default=str))
        self.assertNotIn(self.access, json.dumps(stored.to_dict(), default=str))
        self.assertNotIn(self.access, json.dumps(created, default=str))

    async def test_ordinary_legacy_plan_is_unchanged(self):
        created = await self.legacy(
            title="HAMCP-191 " + ORDINARY, description="Governed " + ORDINARY,
            caller_context={"ticket": "HAMCP-191"},
        )
        stored = self.repository.get(created["plan_id"])
        self.assertEqual(stored.title, "HAMCP-191 " + ORDINARY)
        self.assertEqual(stored.description, "Governed " + ORDINARY)
        self.assertEqual(stored.caller_context, {"ticket": "HAMCP-191"})
        self.assertEqual(stored.proposed_config["description"], "After")
        self.assertEqual(stored.current_config["description"], "Before")
        self.assertEqual(self.gateway.reads, 1)
        self.assertNotIn(COLLISION, json.dumps(created, default=str))

    async def test_dashboard_plan_title_is_refused_before_any_read(self):
        wrapped = self.wrapped_access()
        self.service.dashboard_gateway = _Forbidden()
        self.service.dashboard_artifacts = _Forbidden()
        for arguments in (
            {"title": wrapped, "description": "ordinary"},
            {"title": "ordinary", "description": wrapped},
        ):
            with self.subTest(field=sorted(k for k, v in arguments.items() if v == wrapped)):
                error = await self.assert_refused(
                    self.service.create_dashboard_update_plan(
                        url_path="hamcp191-dash", patch_operations=[], **arguments,
                    ),
                    self.access,
                )
                self.assertEqual(error.details["reason"], "dashboard_contains_prohibited_sensitive_data")

    async def test_configuration_plan_title_is_refused_before_any_read(self):
        wrapped = self.wrapped_access()
        await self.assert_refused(
            self.service.create_configuration_plan(
                title=wrapped, description="ordinary",
                operations=[{
                    "operation_id": "update", "resource_type": "automation", "action": "update",
                    "target_id": "fixture", "depends_on": [], "proposed_config": copy.deepcopy(PLAN_CURRENT),
                }],
            ),
            self.access,
        )
        self.assertEqual(self.gateway.reads, 0)


class OperationalCreationGateTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
    """H1 per family: backup, helper state, reload and restarts.

    Home Assistant text these plans keep (a helper's last_changed, a
    location name) and the service-level backup title pass the strict gate
    before persistence; the ordinary plan of each family is still created.
    """

    FAMILY = "governance"

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.access = syn.needle("hamcp191 operational access").lower()
        self.wrapped = p1(self.access)
        self.telemetry, self.context = begin_request("hamcp191-operational")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def service(self, **gateways):
        root = Path(tempfile.mkdtemp(dir=self.temp.name))
        self.repository = ChangePlanRepository(root / "plans")
        return ChangeGovernanceService(
            self.repository,
            _PlanGateway(),
            AuditLogger(str(root / "audit.jsonl"), self.access),
            sensitive_values=(self.access,),
            **gateways,
        )

    async def assert_refused(self, call):
        with self.assertRaises(GovernanceError) as raised:
            await call
        self.assertNotIn(self.access, json.dumps(raised.exception.details, default=str))
        self.assertEqual(raised.exception.details.get("reason"), "plan_contains_prohibited_sensitive_data")
        self.assertEqual(self.repository.list(), [])

    async def test_backup_plan(self):
        from tests.test_2_1a_operational_backup import FakeOperationalGateway

        service = self.service(operational_gateway=FakeOperationalGateway())
        await self.assert_refused(service.create_backup_plan(backup_name="Nightly safe backup", title=self.wrapped))
        await self.assert_refused(service.create_backup_plan(
            backup_name="Nightly safe backup", description="Before " + FORGED,
        ))
        created = await service.create_backup_plan(
            backup_name="Nightly safe backup", caller_context={"ticket": "HAMCP-191", "note": self.wrapped},
        )
        stored = self.repository.get(created["plan"]["plan_id"])
        self.assertEqual(stored.caller_context, {"ticket": "HAMCP-191"})
        self.assertNotIn(self.access, json.dumps(stored.to_dict(), default=str))

    async def test_a_refused_plan_never_supersedes_the_prior_plan(self):
        # Operational creation supersedes the prior plan before its first
        # save, so only the strict creation gate keeps a refused plan from
        # discarding a pending plan's approval (H1e); the chokepoint would
        # refuse only after supersession.
        from tests.test_2_1a_operational_backup import FakeOperationalGateway

        service = self.service(operational_gateway=FakeOperationalGateway())
        prior = (await service.create_backup_plan(backup_name="Nightly safe backup"))["plan"]["plan_id"]
        await self.assert_refused_beside(
            service.create_backup_plan(backup_name="Nightly safe backup", title=self.wrapped), prior,
        )
        self.assertEqual(self.repository.get(prior).status, PlanStatus.AWAITING_APPROVAL)
        self.assertEqual(self.repository.content_refusals, 0)
        # Control: an ordinary replacement does supersede it.
        await service.create_backup_plan(backup_name="Nightly safe backup")
        self.assertEqual(self.repository.get(prior).status, PlanStatus.SUPERSEDED)

    async def assert_refused_beside(self, call, prior):
        with self.assertRaises(GovernanceError) as raised:
            await call
        self.assertNotIn(self.access, json.dumps(raised.exception.details, default=str))
        self.assertEqual(raised.exception.details.get("reason"), "plan_contains_prohibited_sensitive_data")
        self.assertEqual([plan.plan_id for plan in self.repository.list()], [prior])

    async def test_helper_state_plan(self):
        from tests.test_beta37_exact_helper_state import FakeDependencyRiskReader, FakeHelperStateGateway

        helper = FakeHelperStateGateway()
        service = self.service(
            helper_state_gateway=helper,
            helper_dependency_risk_reader=FakeDependencyRiskReader(helper.entity_id),
        )
        ordinary = helper.last_changed
        helper.last_changed = self.wrapped
        await self.assert_refused(service.create_helper_state_plan(entity_id=helper.entity_id, desired_state="on"))
        helper.last_changed = ordinary
        created = await service.create_helper_state_plan(entity_id=helper.entity_id, desired_state="on")
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(len(self.repository.list()), 1)

    async def test_restart_and_reload_plans(self):
        from tests.test_2_1a_beta2_operational_lifecycle import FakeLifecycleGateway

        wrapped = self.wrapped

        class Injected(FakeLifecycleGateway):
            inject = False

            async def planning_evidence(self, operation, target):
                evidence = await super().planning_evidence(operation, target)
                text = json.dumps(evidence)
                if self.inject:
                    text = text.replace("Test Home", wrapped).replace("automation.reload", wrapped)
                return json.loads(text)

        for name, create in (
            ("home_assistant_restart", lambda service: service.create_home_assistant_restart_plan()),
            ("controlled_reload", lambda service: service.create_reload_plan(reload_target="automation")),
        ):
            with self.subTest(family=name):
                lifecycle = Injected()
                service = self.service(lifecycle_gateway=lifecycle)
                lifecycle.inject = True
                await self.assert_refused(create(service))
                lifecycle.inject = False
                created = await create(service)
                self.assertEqual(created["status"], "awaiting_approval")
                self.assertNotIn(self.access, json.dumps(created, default=str))
                self.assertEqual(len(self.repository.list()), 1)


class IdentifierFamilyTests(FamilyAssertions, unittest.TestCase):
    FAMILY = "identifier"

    def test_identifier_projectors_withhold_forged_identifiers(self):
        from ha_mcp_engineering.audit_baseline.capture_provider import Projector as BaselineProjector
        from ha_mcp_engineering.dashboard_analysis.provider import safe_entity
        from ha_mcp_engineering.integration_inspection.projection import Projector as InspectionProjector

        secret = syn.needle("hamcp191 identifier secret")
        needle = syn.needle("hamcp191 identifier needle")
        baseline = BaselineProjector((secret,))
        inspection = InspectionProjector(known_secrets=(secret,))
        for name, project in (
            ("baseline", baseline.identifier),
            ("inspection", lambda value: inspection.identifier(value, entity=False)),
            ("dashboard", lambda value: safe_entity(value, known_secrets=(secret,))),
        ):
            with self.subTest(projector=name):
                self.assertIsNone(project(p1(secret)))
                self.assertIsNone(project("sensor.x" + FORGED))
                self.assertIsNone(project(p2(needle)))
                self.assertEqual(project("sensor.kitchen_lights"), "sensor.kitchen_lights")


class AuditFamilyTests(FamilyAssertions, unittest.TestCase):
    FAMILY = "audit"

    def test_audit_record_on_disk(self):
        access = syn.needle("hamcp191 audit access")
        needle = syn.needle("hamcp191 audit needle")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            before = self.collisions()
            AuditLogger(str(path), access).write({
                "tool_name": "server_info",
                "parameters": {"a": p1(access), "b": p2(needle), "c": ORDINARY},
            })
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assert_injected(record, secrets=(access, needle), before=before)
        self.assertEqual(record["parameters"]["c"], ORDINARY)

    def test_structured_log_line(self):
        access = syn.needle("hamcp191 log access")
        needle = syn.needle("hamcp191 log needle")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.hamcp191-family")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        self.addCleanup(setattr, logger, "handlers", [])
        before = self.collisions()
        log_event(
            logger, logging.INFO, "hamcp191_family", "Safe message.",
            context={"a": p1(access), "b": p2(needle), "c": ORDINARY}, secret=access,
        )
        logger.info("upstream said " + p2(needle))
        output = stream.getvalue()
        for line in output.splitlines():
            json.loads(line)
        self.assertNotIn(access, output)
        self.assertNotIn(needle, output)
        self.assertIn(COLLISION, output)
        self.assertIn(ORDINARY, output)
        self.assertGreaterEqual(self.collisions() - before, 2)

    def structured(self, context, secret=None):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("ha_mcp_engineering.hamcp191-counters")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        self.addCleanup(setattr, logger, "handlers", [])
        log_event(logger, logging.INFO, "hamcp191_counters", "Ordinary message.", context=context, secret=secret)
        return json.loads(stream.getvalue().splitlines()[-1])

    @staticmethod
    def counters(record):
        return record["redaction_applied"], record["redacted_field_count"], record["redaction_categories"]

    def test_structured_log_counters_are_truthful(self):
        # L1: the counters include the context pass, not only the formatter.
        self.assertEqual(self.counters(self.structured({"value": ORDINARY})), (False, 0, []))
        needle = syn.needle("hamcp191 log probe")
        collided = self.structured({"value": marker(needle)})
        self.assertNotIn(needle, json.dumps(collided))
        self.assertEqual(self.counters(collided), (True, 1, [S.MARKER_COLLISION_CATEGORY]))
        access = syn.needle("hamcp191 log counter access")
        plain = self.structured({"value": "before " + access}, secret=access)
        self.assertNotIn(access, json.dumps(plain))
        applied, count, categories = self.counters(plain)
        self.assertTrue(applied)
        self.assertEqual(count, 1)
        self.assertNotIn(S.MARKER_COLLISION_CATEGORY, categories)

    def test_redact_data_keeps_the_requests_own_markers(self):
        registry = S.ReferenceMarkerRegistry()
        own = redact_data({"v": syn.bearer(syn.needle("hamcp191 own"))}, registry=registry)["v"]
        self.assertIn(marker("token"), own)
        self.assertIs(redact_data({"v": own}, registry=registry)["v"], own)
        self.assertEqual(redact_data({"v": "".join(list(own))}, registry=registry)["v"], COLLISION)


if __name__ == "__main__":
    unittest.main()
