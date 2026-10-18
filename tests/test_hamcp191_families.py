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
from ha_mcp_engineering.governance.resources import persistence_safety_errors  # noqa: E402
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

    def test_core_log_window_is_one_string(self):
        # The Core log window is one sanitized string, so D2 replaces all of
        # it when any line carries a forged marker; ordinary logs are kept.
        settings = plain_settings()
        reader = CoreLogReader(settings)
        needle = syn.needle("hamcp191 core log needle")
        lines = (
            "2026-09-01 01:02:03 ERROR " + p1(settings.access_secret) + "\n"
            "2026-09-01 01:02:04 ERROR " + p2(needle) + "\n"
        )
        window = CoreLogWindow(
            content=lines.encode(), download_capped=False, received_at="2026-09-01T01:02:05Z"
        )
        before = self.collisions()
        out = reader.evidence(window, limit=20, offset=0)
        self.assertEqual(out["log"], COLLISION)
        self.assert_injected(out, secrets=(settings.access_secret, needle), before=before, collisions=1)
        ordinary = "2026-09-01 01:02:03 ERROR " + ORDINARY + "\n"
        clean = reader.evidence(
            CoreLogWindow(content=ordinary.encode(), download_capped=False, received_at="x"),
            limit=20,
            offset=0,
        )
        self.assertEqual(clean["log"], ordinary)


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

    async def get(self, automation_id):
        return copy.deepcopy(self.configs.get(automation_id))

    async def write(self, automation_id, config):
        self.writes += 1
        self.configs[automation_id] = {**copy.deepcopy(config), "id": automation_id}
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


class GovernanceFamilyTests(FamilyAssertions, unittest.IsolatedAsyncioTestCase):
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

    def test_redact_data_keeps_the_requests_own_markers(self):
        registry = S.ReferenceMarkerRegistry()
        own = redact_data({"v": syn.bearer(syn.needle("hamcp191 own"))}, registry=registry)["v"]
        self.assertIn(marker("token"), own)
        self.assertIs(redact_data({"v": own}, registry=registry)["v"], own)
        self.assertEqual(redact_data({"v": "".join(list(own))}, registry=registry)["v"], COLLISION)


if __name__ == "__main__":
    unittest.main()
