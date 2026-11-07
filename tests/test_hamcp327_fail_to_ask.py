"""HAMCP-327: control_helper fails to ask, never acts blind (folds HAMCP-301).

Live acceptance of HAMCP-311 showed every helper's safety evidence as
``unavailable`` / ``evidence_failed`` while the shared dependency index was
valid with partial coverage. Cause: the target-scoped binding reports
``completeness: failed`` for ANY coverage reason, and the reader treated that
as unestablished evidence, so the change proceeded without a check.

These tests run ``control_helper``'s service over the SHIPPED evidence reader
and a real ``DependencyIndex``. Every configuration, state, version and
transport response is synthetic and offline; nothing here is live evidence.
The bounded automation reproduces the HAMCP-301 C-iv shape: its configuration
path exceeds ``MAX_OBLIGATION_VALUE_BYTES`` while the helper id is intact.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering import dependency as dependency_package  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import MAX_OBLIGATION_VALUE_BYTES  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    MAX_PENDING_AUTOMATION_WRITES,
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.helper_value import safety as safety_module  # noqa: E402
from ha_mcp_engineering.helper_value.contracts import digest  # noqa: E402
from ha_mcp_engineering.helper_value.safety import (  # noqa: E402
    CONFIRMATION,
    assess,
    read_helper_dependency_evidence,
)
from ha_mcp_engineering.helper_value.service import (  # noqa: E402
    HelperCoreAuthority,
    HelperService,
    request_summary,
)
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from tests import test_typed_helper_value as typed  # noqa: E402

GUEST = "input_boolean.guest_mode"
ACCEPTANCE = "input_boolean.engineering_acceptance"
SEASON = "input_select.synthetic"
SUPPORTED = supported_home_assistant_versions()[-1]
NO_CONSUMERS = {"known_consumer_count": None, "known_consumers": None,
                "template_reader_count": None, "template_readers": None}


def _nest(inner, depth):
    for _ in range(depth):
        inner = [{"choose": [{"conditions": [], "sequence": inner}]}]
    return inner


def _bounded(entity_id):
    """A deep lock automation: its condition path exceeds the provenance bound."""
    gate = {"choose": [{
        "conditions": [{"condition": "state", "entity_id": entity_id, "state": "on"}],
        "sequence": [{"action": "lock.lock", "target": {"entity_id": "lock.synthetic_front"}}],
    }]}
    return {"alias": "Front door lock bedtime",
            "triggers": [{"trigger": "time", "at": "22:00:00"}],
            "actions": [{"repeat": {"count": 3, "sequence": _nest([gate], 5)}}]}


def configs(*shapes):
    result = {}
    if "exact" in shapes:
        result["guest_unlock"] = {
            "alias": "Unlock front door for guests",
            "triggers": [{"trigger": "state", "entity_id": GUEST, "to": "on"}],
            "actions": [{"action": "lock.unlock", "target": {"entity_id": "lock.synthetic_front"}}]}
        result["guest_lights"] = {
            "alias": "Guest lights",
            "triggers": [{"trigger": "state", "entity_id": GUEST}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.synthetic_porch"}}]}
    if "bounded" in shapes:
        # Josh's three bounded automations read other entities.
        result["front_door_lock_bedtime"] = _bounded("binary_sensor.synthetic_bed")
    if "bounded_guest" in shapes:
        # C-iv: the same bounded shape whose literal read is the helper itself.
        result["front_door_lock_bedtime"] = _bounded(GUEST)
    if "noise" in shapes:
        result["noise"] = {
            "alias": "Unrelated",
            "triggers": [{"trigger": "state", "entity_id": "sensor.synthetic_other"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.synthetic_other"}}]}
    return result


class Rest:
    def __init__(self, configurations, *, version=SUPPORTED):
        self.configurations, self.version = configurations, version

    async def request(self, method, path, **_):
        assert method == "GET", path
        if path == "/config":
            return {"version": self.version}
        if path == "/states":
            states = [{"entity_id": entity, "state": "off", "attributes": {}}
                      for entity in (GUEST, ACCEPTANCE, "input_boolean.synthetic")]
            states.append({"entity_id": SEASON, "state": "Home",
                           "attributes": {"options": ["Home", "Away", "Night"]}})
            states += [{"entity_id": f"automation.{key}", "state": "on",
                        "attributes": {"id": key, "friendly_name": value["alias"]}}
                       for key, value in self.configurations.items()]
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return deepcopy(self.configurations[path.removeprefix(prefix)])
        raise AssertionError(path)


class WebSocket:
    async def command(self, payload):
        if payload in ({"type": "config/entity_registry/list"}, {"type": "config/label_registry/list"}):
            return []
        raise AssertionError(payload)


def index_for(*shapes, version=SUPPORTED):
    return DependencyIndex(DirectHaDependencyProvider(Rest(configs(*shapes), version=version),
                                                      WebSocket(), concurrency=2))


class Hamcp327Base(unittest.IsolatedAsyncioTestCase):
    version = typed.HelperTests.version
    shapes = ("exact", "bounded", "noise")

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core = typed.Clock(), typed.SignedCore()
        self.transport = typed.HelperTransport(self.version)
        self.use_index(index_for(*self.shapes))
        # The service's default reader is the shipped production reader.
        self.service = HelperService(self.tmp.name, HelperProvider(self.transport, lambda *a: self.transport.authority),
                                     HelperCoreAuthority(self.core), now=self.clock, sleep=self.clock.sleep)
        self.addAsyncCleanup(self.service.close)
        self.telemetry, token = begin_request("hamcp327-helper-request")
        self.addCleanup(end_request, token)

    def use_index(self, index):
        self.runtime = SimpleNamespace(require=lambda: SimpleNamespace(index=index))
        patcher = patch.object(dependency_package, "DEPENDENCY_ANALYSIS", self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)

    request = typed.HelperTests.request
    settled = typed.HelperTests.settled

    async def call(self, request):
        self.telemetry.ordinary_helper_value_binding = digest(request.model_dump())
        return await self.service.control(request)

    def dispatches(self):
        return [args for tool, args in self.transport.calls if tool == "ha_call_service"]

    async def assert_asks(self, request, reason):
        result = await self.call(request)
        self.assertEqual(result["status"], "confirmation_required", result)
        self.assertEqual(result["reason"], reason)
        self.assertEqual((result["dispatch"], result["task_created"]), ("no_dispatch", False))
        self.assertIsNone(self.service.load(request.task_id))
        self.assertEqual(self.dispatches(), [])
        self.assertEqual(self.transport.writes, 0)
        summary = request_summary(self.telemetry)
        self.assertEqual(summary["safety_gate"], "confirmation_required")
        self.assertEqual(summary["safety_confirmation_reason"], reason)
        self.assertEqual(summary["helper_value_outcome"], "confirmation_required")
        self.assertNotIn("task_id", summary)
        return result


class PartialCoverageEvidenceTests(Hamcp327Base):
    """Live symptom: a valid index with partial coverage is established evidence."""

    async def test_root_cause_shape_partial_coverage_binding_is_available(self):
        for entity in (GUEST, ACCEPTANCE, SEASON):
            with self.subTest(entity=entity):
                evidence = await read_helper_dependency_evidence(entity)
                self.assertTrue(evidence["available"], evidence)
                binding = evidence["binding"]
                # The binding still says ``failed``: only the reader changed.
                self.assertEqual(binding["completeness"], "failed")
                self.assertFalse(binding["coverage_complete"])
                self.assertTrue(binding["execution_contract_complete"])
                self.assertIn("obligation_coverage_failure", binding["coverage_failure_reason_codes"])

    async def test_partial_coverage_lists_known_consumers_and_discloses_coverage(self):
        safety = assess(await read_helper_dependency_evidence(GUEST))
        self.assertEqual(safety["evidence"], "available")
        self.assertTrue(safety["safety_critical"])
        self.assertEqual(safety["known_consumer_count"], 2)
        self.assertEqual({item["automation_id"] for item in safety["known_consumers"]},
                         {"automation.guest_unlock", "automation.guest_lights"})
        self.assertEqual(safety["consequential_automations"], [
            {"automation_id": "automation.guest_unlock", "name": "Unlock front door for guests",
             "physical_consequence": "safety_critical"}])
        self.assertFalse(safety["coverage_complete"])
        self.assertIn("obligation_coverage_failure", safety["coverage_reason_codes"])
        self.assertEqual(safety["template_reader_count"], 1)

    async def test_guest_mode_with_partial_coverage_asks_then_acknowledged_call_succeeds(self):
        request = self.request(GUEST, "turn_on")
        result = await self.assert_asks(request, "safety_critical_consumers")
        self.assertEqual([item["automation_id"] for item in result["consequential_automations"]],
                         ["automation.guest_unlock"])
        self.assertEqual(result["consumers"]["known_consumer_count"], 2)
        self.assertFalse(result["coverage_complete"])
        self.settled()

        result = await self.call(request.model_copy(update={"acknowledge_safety_critical": True}))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertTrue(result["safety"]["safety_critical"])
        self.assertEqual(result["safety"]["confirmation"], CONFIRMATION)
        self.assertIs(result["safety"]["external_approval"], False)
        summary = request_summary(self.telemetry)
        self.assertEqual(summary["safety_gate"], "acknowledged")
        self.assertEqual(summary["safety_confirmation_reason"], "safety_critical_consumers")
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.transport.states[GUEST]["state"], "on")
        self.settled()

    async def test_bounded_unrelated_lock_is_disclosed_not_warned(self):
        # Usability consequence on an installation shaped like Josh's: the
        # bounded lock automation is in scope for EVERY helper as opaque. It is
        # disclosed (HAMCP-311 keeps it information only) and never asks.
        for entity, action, value in ((ACCEPTANCE, "turn_on", None), (SEASON, "select_option", "Away")):
            with self.subTest(entity=entity):
                result = await self.call(self.request(entity, action, value))
                self.assertEqual(result["status"], "succeeded_verified", result)
                safety = result["safety"]
                self.assertFalse(safety["safety_critical"])
                self.assertIsNone(safety["confirmation_reason"])
                self.assertEqual(safety["known_consumers"], [])
                self.assertEqual(safety["known_consumer_count"], 0)
                self.assertEqual(safety["template_reader_count"], 1)
                self.assertEqual(safety["potential_physical_consequence"], "safety_critical")
                self.assertIn("not proven to react to this helper", safety["consequence"])
                self.assertEqual(request_summary(self.telemetry)["safety_gate"], "not_safety_critical")
        self.assertEqual(self.transport.writes, 2)
        self.settled()


class IntactIdBoundedTests(Hamcp327Base):
    """HAMCP-301 C-iv: an intact literal id inside an over-bound provenance path."""

    shapes = ("bounded_guest", "noise")

    async def test_fixture_reproduces_the_c_iv_shape(self):
        index = self.runtime.require().index
        snapshot, *_ = await index.get(refresh=True)
        failed = [item for item in snapshot.obligations
                  if item.coverage_failure_authority and GUEST in item.exact_entity_ids]
        self.assertTrue(failed)
        self.assertTrue(all(len(str(item.config_path).encode()) > MAX_OBLIGATION_VALUE_BYTES for item in failed))
        evidence = await read_helper_dependency_evidence(GUEST)
        self.assertEqual(evidence["binding"]["exact_dependency_obligation_count"], 0)

    async def test_intact_id_safety_critical_consumer_asks_without_dispatch(self):
        request = self.request(GUEST, "turn_on")
        result = await self.assert_asks(request, "safety_critical_consumers")
        self.assertEqual(result["consequential_automations"], [
            {"automation_id": "automation.front_door_lock_bedtime", "name": "Front door lock bedtime",
             "physical_consequence": "safety_critical"}])
        self.settled()
        result = await self.call(request.model_copy(update={"acknowledge_safety_critical": True}))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertEqual(len(self.dispatches()), 1)
        self.settled()


class UnattributedGapTests(Hamcp327Base):
    """HAMCP-327 R1-H1: an unrelated, unattributable gap must not hide C-iv.

    Overflowing the provider's bounded pending-write journal (a synthetic
    governed-write count, no other change) is a coverage gap no named
    automation owns. Every automation then carries
    ``conservative_consequence_scope`` instead of its own coverage-failure
    marker; the literal consumer's evidence is otherwise identical.
    """

    shapes = ("bounded_guest", "noise")

    async def asyncSetUp(self):
        await super().asyncSetUp()
        provider = self.runtime.require().index.provider
        for number in range(MAX_PENDING_AUTOMATION_WRITES + 1):
            provider.note_automation_write(f"synthetic_unrelated_{number}")

    async def test_fixture_reproduces_the_unattributed_gap_shape(self):
        evidence = await read_helper_dependency_evidence(GUEST)
        self.assertTrue(evidence["available"], evidence)
        binding = evidence["binding"]
        self.assertTrue(binding["execution_contract_complete"])
        self.assertEqual(binding["physical_consequence"], "safety_critical")
        self.assertEqual(binding["exact_dependency_obligation_count"], 0)
        consumer = next(item for item in binding["downstream_profiles"]
                        if item["automation_id"] == "automation.front_door_lock_bedtime")
        self.assertEqual(consumer["evidence_class"], "referenced")
        self.assertEqual(consumer["physical_consequence"], "safety_critical")
        self.assertIn("choose_condition", consumer["relationships"])
        self.assertIn("conservative_consequence_scope", consumer["relationships"])
        self.assertNotIn("coverage_failure_consequence_scope", consumer["relationships"])

    async def test_literal_critical_consumer_asks_despite_unrelated_gap(self):
        request = self.request(GUEST, "turn_on")
        result = await self.assert_asks(request, "safety_critical_consumers")
        self.assertEqual(result["consequential_automations"], [
            {"automation_id": "automation.front_door_lock_bedtime", "name": "Front door lock bedtime",
             "physical_consequence": "safety_critical"}])
        self.assertEqual(self.transport.states[GUEST]["state"], "off")
        self.settled()
        result = await self.call(request.model_copy(update={"acknowledge_safety_critical": True}))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertTrue(result["safety"]["safety_critical"])
        self.assertEqual(request_summary(self.telemetry)["safety_gate"], "acknowledged")
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.transport.states[GUEST]["state"], "on")
        self.settled()

    async def test_conservative_only_consumer_of_another_helper_stays_informational(self):
        # The same gap puts the lock automation in scope for every helper; for
        # one it does not reference, it stays information only (HAMCP-311).
        result = await self.call(self.request(ACCEPTANCE, "turn_on"))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertFalse(result["safety"]["safety_critical"])
        self.assertIsNone(result["safety"]["confirmation_reason"])
        self.assertEqual(result["safety"]["known_consumers"], [])
        self.assertEqual(request_summary(self.telemetry)["safety_gate"], "not_safety_critical")
        self.assertEqual(len(self.dispatches()), 1)
        self.settled()


class UnreferencedHelperTests(Hamcp327Base):
    shapes = ("exact", "noise")

    async def test_unreferenced_helper_is_ordinary_control_with_disclosure(self):
        result = await self.call(self.request(ACCEPTANCE, "turn_on"))
        self.assertEqual(result["status"], "succeeded_verified", result)
        safety = result["safety"]
        self.assertEqual(safety["evidence"], "available")
        self.assertTrue(safety["coverage_complete"])
        self.assertEqual(safety["coverage_reason_codes"], [])
        self.assertEqual(safety["known_consumers"], [])
        self.assertIsNone(safety["template_readers"])
        self.assertEqual(safety["consequence"], "No automation with exact evidence references this helper.")
        self.assertEqual(request_summary(self.telemetry)["safety_gate"], "not_safety_critical")
        self.assertNotIn("safety_confirmation_reason", request_summary(self.telemetry))
        self.assertEqual(len(self.dispatches()), 1)
        self.settled()


class UnestablishedEvidenceTests(Hamcp327Base):
    """Evidence that cannot be established asks; it is never a silent pass."""

    async def assert_unavailable(self, entity, category):
        request = self.request(entity, "turn_on")
        result = await self.assert_asks(request, "evidence_unavailable")
        self.assertEqual(result["evidence"], "unavailable")
        self.assertEqual(result["failure_category"], category)
        self.assertEqual(result["consumers"], NO_CONSUMERS)
        self.assertNotIn("consequential_automations", result)
        self.assertIn("could not be checked", result["next_step"])
        self.settled()
        return request

    async def test_runtime_unavailable_asks(self):
        def unavailable():
            raise RuntimeError("synthetic-private-runtime-text")
        self.runtime.require = unavailable
        request = await self.assert_unavailable(GUEST, "RuntimeError")
        self.assertNotIn("synthetic-private-runtime-text", json.dumps(request_summary(self.telemetry)))

    async def test_index_failure_asks(self):
        index = self.runtime.require().index
        async def failing(*args, **kwargs):
            raise OSError("synthetic-private-index-text")
        with patch.object(index, "get", failing):
            await self.assert_unavailable(GUEST, "OSError")

    async def test_unadmitted_home_assistant_version_asks(self):
        self.use_index(index_for(*self.shapes, version="2020.1.0"))
        evidence = await read_helper_dependency_evidence(GUEST)
        self.assertFalse(evidence["available"])
        await self.assert_unavailable(GUEST, evidence["failure_category"])
        self.assertTrue(evidence["failure_category"].startswith("home_assistant_version_"))

    async def test_wrong_binding_model_asks(self):
        builder, _ = safety_module._builder("input_boolean")
        with patch.object(safety_module, "_builder", lambda domain: (builder, "synthetic_other_model")):
            await self.assert_unavailable(GUEST, "evidence_model_mismatch")

    async def test_builder_exception_asks(self):
        def broken(snapshot, **kwargs):
            raise ValueError("synthetic-private-builder-text")
        with patch.object(safety_module, "_builder", lambda domain: (broken, None)):
            await self.assert_unavailable(GUEST, "ValueError")

    async def test_incomplete_execution_contract_without_codes_asks(self):
        builder, model = safety_module._builder("input_boolean")
        def incomplete(snapshot, **kwargs):
            return {**builder(snapshot, **kwargs), "execution_contract_complete": False,
                    "execution_block_reason_codes": []}
        with patch.object(safety_module, "_builder", lambda domain: (incomplete, model)):
            await self.assert_unavailable(GUEST, "evidence_incomplete")

    async def test_acknowledged_call_proceeds_and_is_attributed(self):
        self.runtime.require = lambda: (_ for _ in ()).throw(RuntimeError("synthetic"))
        request = await self.assert_unavailable(ACCEPTANCE, "RuntimeError")
        result = await self.call(request.model_copy(update={"acknowledge_safety_critical": True}))
        self.assertEqual(result["status"], "succeeded_verified", result)
        self.assertEqual(result["safety"]["evidence"], "unavailable")
        self.assertEqual(result["safety"]["confirmation_reason"], "evidence_unavailable")
        self.assertEqual(result["safety"]["confirmation"], CONFIRMATION)
        summary = request_summary(self.telemetry)
        self.assertEqual(summary["safety_gate"], "acknowledged_evidence_unavailable")
        self.assertEqual(summary["safety_confirmation"], CONFIRMATION)
        self.assertIs(summary["external_approval"], False)
        self.assertEqual(len(self.dispatches()), 1)
        self.settled()


class FailurePreservationTests(Hamcp327Base):
    """The real reader keeps every pre-declaration refusal and no-op path."""

    async def test_binding_withdrawn_during_real_evidence_read_refuses(self):
        original = self.service.dependency_reader
        async def withdrawing(entity_id):
            self.telemetry.ordinary_helper_value_binding = "withdrawn"
            return await original(entity_id)
        self.service.dependency_reader = withdrawing
        request = self.request(ACCEPTANCE, "turn_on")
        with self.assertRaisesRegex(typed.HelperRefusal, "authorization"):
            await self.call(request)
        self.assertIsNone(self.service.load(request.task_id))
        self.assertEqual(self.transport.writes, 0)
        self.settled()

    async def test_core_retired_during_real_evidence_read_never_dispatches(self):
        original = self.service.dependency_reader
        async def retiring(entity_id):
            self.core.allowed = False
            return await original(entity_id)
        self.service.dependency_reader = retiring
        for entity in (ACCEPTANCE, GUEST):
            with self.subTest(entity=entity):
                self.core.allowed = True
                request = self.request(entity, "turn_on", acknowledge_safety_critical=True)
                result = await self.call(request)
                self.assertIn(result["status"], {"provider_unavailable", "refused"}, result)
                self.assertFalse(result["dispatch_intent_recorded"])
        self.assertEqual(self.transport.writes, 0)
        self.settled()

    async def test_already_current_value_with_failing_reader_is_still_no_change(self):
        # R1-M1: the no-op reads evidence only to disclose it; a reader
        # failure is reported as unknown and never changes the no-op.
        reads = []
        async def failing(entity_id):
            reads.append(entity_id)
            raise RuntimeError("synthetic-private-reader-text")
        self.service.dependency_reader = failing
        request = self.request(GUEST, "turn_off")
        try:
            result = await self.call(request)
        except Exception as exc:
            self.fail(f"a disclosure failure must not fail the no-op: {type(exc).__name__}")
        self.assertEqual(result["status"], "no_change", result)
        self.assertEqual(reads, [GUEST])
        safety = result["safety"]
        self.assertEqual((safety.get("evaluated"), safety.get("reason"), safety.get("evidence"),
                          safety.get("failure_category")),
                         (False, "value_already_current", "unavailable", "RuntimeError"))
        self.assertEqual({key: safety[key] for key in NO_CONSUMERS}, NO_CONSUMERS)
        self.assertNotIn("synthetic-private-reader-text", json.dumps(result))
        self.assertEqual(request_summary(self.telemetry)["safety_gate"], "not_evaluated_value_already_current")
        self.assertEqual(self.dispatches(), [])
        self.assertEqual(self.transport.writes, 0)

    async def test_unacknowledged_guest_mode_never_dispatches_in_any_evidence_state(self):
        # R1-L1: capture the index before replacing ``require``, so the first
        # state really reads available evidence.
        index = self.runtime.require().index
        def unavailable():
            raise RuntimeError("synthetic")
        for require, reason in ((lambda: SimpleNamespace(index=index), "safety_critical_consumers"),
                                (unavailable, "evidence_unavailable")):
            with self.subTest(reason=reason):
                self.runtime.require = require
                result = await self.call(self.request(GUEST, "turn_on"))
                self.assertEqual(result["status"], "confirmation_required", result)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result.get("evidence"),
                                 "unavailable" if reason == "evidence_unavailable" else None)
                self.assertEqual(self.dispatches(), [])
                self.assertEqual(self.transport.writes, 0)
        self.assertEqual(self.transport.states[GUEST]["state"], "off")


class ResultDisclosureTests(Hamcp327Base):
    """HAMCP-327 R1-M1: every result lists the known consumers."""

    async def test_no_change_lists_known_consumers_from_real_index(self):
        result = await self.call(self.request(GUEST, "turn_off"))
        self.assertEqual(result["status"], "no_change", result)
        safety = result["safety"]
        self.assertEqual((safety.get("evaluated"), safety.get("reason"), safety.get("disclosure")),
                         (False, "value_already_current", "informational_read_after_result"))
        self.assertEqual(safety["evidence"], "available")
        self.assertEqual(safety["known_consumer_count"], 2)
        self.assertEqual({item["automation_id"] for item in safety["known_consumers"]},
                         {"automation.guest_unlock", "automation.guest_lights"})
        self.assertEqual([item["automation_id"] for item in safety["consequential_automations"]],
                         ["automation.guest_unlock"])
        self.assertEqual(safety["template_reader_count"], 1)
        self.assertFalse(safety["coverage_complete"])
        # No gate was evaluated: the no-op never asks.
        self.assertNotIn("confirmation_reason", safety)
        self.assertNotIn("safety_critical", safety)
        summary = request_summary(self.telemetry)
        self.assertEqual(summary["safety_gate"], "not_evaluated_value_already_current")
        self.assertNotIn("safety_confirmation_reason", summary)
        self.assertEqual(self.dispatches(), [])
        self.assertEqual(self.transport.writes, 0)

    async def test_reconciliation_discloses_and_never_replays(self):
        request = self.request(GUEST, "turn_on")
        await self.assert_asks(request, "safety_critical_consumers")
        confirmed = request.model_copy(update={"acknowledge_safety_critical": True})
        first = await self.call(confirmed)
        self.assertEqual(first["status"], "succeeded_verified", first)
        for _ in range(2):
            again = await self.call(confirmed)
            self.assertEqual(again["status"], "succeeded_verified", again)
            safety = again["safety"]
            self.assertEqual((safety.get("evaluated"), safety.get("reason"), safety.get("disclosure")),
                             (False, "reconciliation_of_existing_task", "informational_read_after_result"))
            self.assertEqual(safety["known_consumer_count"], 2)
            self.assertEqual([item["automation_id"] for item in safety["consequential_automations"]],
                             ["automation.guest_unlock"])
            self.assertNotIn("confirmation_reason", safety)
        # The unacknowledged original is now rebound, never a second dispatch.
        with self.assertRaisesRegex(typed.HelperRefusal, "rebound"):
            await self.call(request)
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.transport.writes, 1)
        self.settled()

    async def test_reconciliation_with_unavailable_evidence_is_unchanged(self):
        request = self.request(ACCEPTANCE, "turn_on")
        first = await self.call(request)
        self.assertEqual(first["status"], "succeeded_verified", first)
        self.runtime.require = lambda: (_ for _ in ()).throw(RuntimeError("synthetic"))
        again = await self.call(request)
        self.assertEqual(again["status"], "succeeded_verified", again)
        self.assertEqual({key: again[key] for key in ("task_id", "provider_attempt_count")},
                         {key: first[key] for key in ("task_id", "provider_attempt_count")})
        safety = again["safety"]
        self.assertEqual((safety.get("reason"), safety.get("evidence"), safety.get("failure_category")),
                         ("reconciliation_of_existing_task", "unavailable", "RuntimeError"))
        self.assertEqual({key: safety[key] for key in NO_CONSUMERS}, NO_CONSUMERS)
        self.assertEqual(len(self.dispatches()), 1)
        self.settled()


if __name__ == "__main__":
    unittest.main()
