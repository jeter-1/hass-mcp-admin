"""HAMCP-226/228 review 1 corrections: H1 screening and M3 exact accounting.

H1: every HAMCP-132 default-mode credential case (``test_pr294_*`` shapes,
collected in ``hamcp226_credential_cases``) is planted in each Home Assistant
registry and state field the integrity and dependency paths read. The needle
never reaches the service result or the tool response; the source screen
reports what it withheld, and the final-envelope screen behind it withholds a
field the source screens did not cover.

M3: over-bound script sources are counted exactly at the production limits
(listed + omitted = total) one below, at and one above each limit.

The H2, M1 and M2 tests are in ``test_hamcp228_baseline_store``. Synthetic
peers only; no network.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

import hamcp226_credential_cases as cases  # noqa: E402
import test_hamcp226_named_sources as n  # noqa: E402
from ha_mcp_engineering import untrusted_text  # noqa: E402
from ha_mcp_engineering.configuration_definitions import credentials  # noqa: E402
from ha_mcp_engineering.dependency import bounded_sources as bs  # noqa: E402
from ha_mcp_engineering.dependency import script_sources as ss  # noqa: E402
from ha_mcp_engineering.dependency.service import AnalysisOutput, EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.integrity.service import ConfigurationIntegrityAnalysisService  # noqa: E402
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE  # noqa: E402
from ha_mcp_engineering.tools import analysis  # noqa: E402
from ha_mcp_engineering.tools import audit_baseline as audit_tool  # noqa: E402

MARKERS = {credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER}
# Fields each integrity inventory screens at the source, and its rows' fields
# that no path projects (they must not appear either).
REGISTRY_SCREENED = ("platform", "disabled_by")
STATE_SCREENED = ("state", "last_changed")
REGISTRY_UNPROJECTED = ("name", "original_name", "aliases", "device_id", "area_id", "original_device_class")
STATE_UNPROJECTED = ("friendly_name", "unit_of_measurement")


def planted(where, field, value):
    """The house with ``value`` in one registry or state field of the dead target."""

    rest, websocket = n.house()
    if where == "state":
        row = next(item for item in rest.states if item["entity_id"] == n.DEAD)
        if field == "entity_id":
            rest.states.append({"entity_id": value, "state": "on", "last_changed": n.CHANGED, "attributes": {}})
        elif field in STATE_UNPROJECTED:
            row["attributes"][field] = value
        else:
            row[field] = value
        return rest, websocket
    original = websocket.command

    async def command(payload):
        rows = await original(payload)
        if field == "entity_id":
            rows.append({"entity_id": value, "platform": "demo", "disabled_by": None})
        else:
            for item in rows:
                item[field] = [value] if field == "aliases" else value
        return rows

    websocket.command = command
    return rest, websocket


async def integrity_tool(rest, websocket):
    index, _provider = await n.build(rest, websocket)
    service = ConfigurationIntegrityAnalysisService(
        DirectHaIntegrityProvider(index, rest, websocket, secret=n.SECRET), cursor_key=b"k" * 32)
    with patch.object(analysis.CONFIGURATION_INTEGRITY_ANALYSIS, "require", return_value=service):
        return await analysis.configuration_integrity_analysis(
            source_types=list(n.SOURCE_TYPES), include_orphan_candidates=True, detail_level="evidence",
            limit=20, refresh_index=True)


async def dependency_tool(rest, websocket, entity_id=n.DEAD):
    index, _provider = await n.build(rest, websocket)
    with patch.object(analysis.DEPENDENCY_ANALYSIS, "require", return_value=EntityDependencyAnalysisService(index)):
        return await analysis.entity_dependency_analysis(entity_id=entity_id, detail_level="evidence",
                                                         refresh_index=True)


def coverage(result, source_type):
    return next(item for item in result.data["source_coverage_matrix"] if item["source_type"] == source_type)


class RegistryAndStateFieldScreenTests(unittest.IsolatedAsyncioTestCase):
    """Review 1 (H1): Home Assistant's registry and state text, every field."""

    def setUp(self):
        self.telemetry, self.token = begin_request()

    def tearDown(self):
        end_request(self.token)

    async def assert_never_shown(self, label, rest_ws_factory, needle):
        service = await n.integrity(*rest_ws_factory())
        text = n.rendered(service)
        self.assertNotIn(needle, text, f"{label}: integrity service result")
        self.assertNotIn("REDACTED", text, f"{label}: redacted in part")
        response = await integrity_tool(*rest_ws_factory())
        self.assertTrue(json.loads(response)["success"], label)
        self.assertNotIn(needle, response, f"{label}: integrity tool response")
        response = await dependency_tool(*rest_ws_factory())
        self.assertTrue(json.loads(response)["success"], label)
        self.assertNotIn(needle, response, f"{label}: dependency tool response")
        return service

    async def test_screened_text_fields_are_withheld_and_reported_at_the_source(self):
        for where, fields, inventory in (("registry", REGISTRY_SCREENED, "entity_registry"),
                                         ("state", STATE_SCREENED, "current_states")):
            for field in fields:
                for label, (value, needle) in cases.withheld_cases().items():
                    with self.subTest(field=f"{where}:{field}", case=label):
                        result = await self.assert_never_shown(
                            f"{where}:{field}:{label}", lambda: planted(where, field, value), needle)
                        # The source screen withheld it (not only the envelope).
                        self.assertTrue(any("text field(s) were withheld whole by the credential screen" in item
                                            for item in result.warnings), result.warnings)
                        self.assertEqual(coverage(result, inventory)["completeness"], "complete")

    async def test_credential_shaped_entity_ids_drop_the_row_and_mark_the_inventory_partial(self):
        for where, inventory in (("registry", "entity_registry"), ("state", "current_states")):
            for label, (value, needle) in cases.withheld_cases().items():
                with self.subTest(where=where, case=label):
                    result = await self.assert_never_shown(
                        f"{where}:entity_id:{label}", lambda: planted(where, "entity_id", value), needle)
                    item = coverage(result, inventory)
                    self.assertEqual((item["completeness"], item["failed_items"]), ("partial", 1))
                    self.assertIn(f"1 {inventory} record(s) were withheld whole by the credential screen; "
                                  "the inventory is partial.", item["warnings"])

    async def test_unprojected_fields_never_appear(self):
        for where, fields in (("registry", REGISTRY_UNPROJECTED), ("state", STATE_UNPROJECTED)):
            for field in fields:
                for label, (value, needle) in cases.withheld_cases().items():
                    with self.subTest(field=f"{where}:{field}", case=label):
                        await self.assert_never_shown(f"{where}:{field}:{label}",
                                                      lambda: planted(where, field, value), needle)

    async def test_benign_registry_and_state_text_is_shown_unchanged(self):
        for value in ("demo_platform", "user"):
            with self.subTest(value=value):
                result = await n.integrity(*planted("registry", "platform", value))
                self.assertFalse(any("withheld whole" in item for item in result.warnings), result.warnings)
                self.assertNotIn(credentials.WITHHELD_MARKER, n.rendered(result))

    async def test_dependency_target_metadata_is_screened_before_its_cut(self):
        # The state is cut to 128 characters; the screen runs on the original.
        for label, (value, needle) in cases.withheld_cases().items():
            with self.subTest(case=label):
                padded = "x" * 120 + " " + value
                index, _provider = await n.build(*planted("state", "state", padded))
                output = await EntityDependencyAnalysisService(index).analyze(
                    entity_id=n.DEAD, detail_level="evidence", refresh_index=True)
                self.assertIn(output.data["target"]["state"], MARKERS)
                self.assertNotIn(needle, n.rendered(output))


class FinalEnvelopeBackstopTests(unittest.IsolatedAsyncioTestCase):
    """Review 1 (H1): a field the source screens do not cover is still withheld."""

    def setUp(self):
        self.telemetry, self.token = begin_request()

    def tearDown(self):
        end_request(self.token)

    def output(self, extra):
        return AnalysisOutput({"findings": [], "final_assessment": "no_findings_within_coverage", **extra},
                              [], {"detail_level": "summary"}, False)

    async def test_integrity_and_dependency_envelopes_withhold_a_new_unscreened_field(self):
        value, needle = cases.withheld_cases()["postgresql"]
        for name, runtime, call in (
            ("integrity", analysis.CONFIGURATION_INTEGRITY_ANALYSIS,
             lambda: analysis.configuration_integrity_analysis(source_types=["automation"])),
            ("dependency", analysis.DEPENDENCY_ANALYSIS,
             lambda: analysis.entity_dependency_analysis(entity_id=n.DEAD)),
        ):
            with self.subTest(name):
                self.telemetry.completeness = self.telemetry.result_status = None
                service = SimpleNamespace(provider=None, index=None)
                method = "analyze"
                setattr(service, method, unittest.mock.AsyncMock(return_value=self.output(
                    {"added_later": value, "nested": [{"text": "kept"}], "already": credentials.WITHHELD_MARKER})))
                with patch.object(runtime, "require", return_value=service):
                    response = await call()
                data = json.loads(response)
                self.assertTrue(data["success"], data)
                self.assertNotIn(needle, response)
                self.assertEqual(data["data"]["added_later"], credentials.WITHHELD_MARKER)
                self.assertEqual(data["data"]["nested"], [{"text": "kept"}])
                self.assertIn(untrusted_text.envelope_warning(1), data["warnings"])
                self.assertEqual((self.telemetry.completeness, self.telemetry.result_status),
                                 ("partial", "partial"))

    async def test_a_withheld_response_key_refuses_the_response(self):
        value, needle = cases.withheld_cases()["postgresql"]
        service = SimpleNamespace(provider=None, index=None,
                                  analyze=unittest.mock.AsyncMock(return_value=self.output({value: 1})))
        with patch.object(analysis.DEPENDENCY_ANALYSIS, "require", return_value=service):
            response = await analysis.entity_dependency_analysis(entity_id=n.DEAD)
        data = json.loads(response)
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "analysis_unavailable")
        self.assertNotIn(needle, response)

    def test_screen_envelope_counts_only_newly_withheld_leaves(self):
        value, needle = cases.withheld_cases()["postgresql"]
        tree = {"a": [value, credentials.WITHHELD_MARKER, ("t", value)], "b": {"c": "Küche"}, "n": 3}
        screened, hits = untrusted_text.screen_envelope(tree)
        self.assertEqual(hits, 2)
        self.assertEqual(screened, {"a": [credentials.WITHHELD_MARKER, credentials.WITHHELD_MARKER,
                                          ("t", credentials.WITHHELD_MARKER)], "b": {"c": "Küche"}, "n": 3})
        self.assertIn(needle, json.dumps(tree), "the input is not modified")
        with self.assertRaises(untrusted_text.EnvelopeKeyWithheld):
            untrusted_text.screen_envelope({"ok": {value: 1}})

    async def test_capture_page_with_an_unscreened_field_is_refused_not_rewritten(self):
        value, needle = cases.withheld_cases()["postgresql"]
        page = {"baseline_header": {"inventory": {"completeness": "complete", "scope": "loaded"},
                                    "installation": {"status": "established"},
                                    "consistency": {"inventory_drift": "none_observed_at_capture_fences"}},
                "diagnostics": {}, "snapshot": {"capture_id": "a" * 32, "resumed_from": None},
                "records": [{"configuration_id": "1", "added_later": value}]}
        service = SimpleNamespace(provider=None, capture=unittest.mock.AsyncMock(return_value=page))
        with patch.object(AUTOMATION_BASELINE_CAPTURE, "require", return_value=service):
            response = await audit_tool.capture_automation_baseline()
        data = json.loads(response)
        self.assertFalse(data["success"], data)
        self.assertEqual(data["error_code"], "analysis_unavailable")
        self.assertIn("redacted_identity", response)
        self.assertNotIn(needle, response)

    async def test_compare_result_with_an_unscreened_field_is_withheld_and_partial(self):
        value, needle = cases.withheld_cases()["postgresql"]
        result = {"mode": "capture", "counts": {"CHANGED": 1}, "screened_withheld_count": 0,
                  "pagination": {"returned": 1, "total_records": 1},
                  "records": [{"configuration_id": "1", "added_later": value, "already": credentials.WITHHELD_MARKER}]}
        service = SimpleNamespace(provider=None, compare=unittest.mock.AsyncMock(return_value=result))
        with patch.object(AUTOMATION_BASELINE_CAPTURE, "require", return_value=service):
            response = await audit_tool.compare_automation_baselines(earlier_capture_id="a" * 32,
                                                                     later_capture_id="b" * 32)
        data = json.loads(response)
        self.assertTrue(data["success"], data)
        self.assertNotIn(needle, response)
        self.assertEqual(data["data"]["records"], [{"configuration_id": "1", "added_later": credentials.WITHHELD_MARKER,
                                                    "already": credentials.WITHHELD_MARKER}])
        self.assertEqual(data["data"]["screened_withheld_count"], 1)
        self.assertIn("Some values were withheld by the credential screen.", data["warnings"])
        self.assertEqual((self.telemetry.completeness, self.telemetry.result_status), ("partial", "partial"))

    async def test_helper_bounded_sources_disclosure_has_its_own_backstop(self):
        value, needle = cases.withheld_cases()["postgresql"]
        real = bs.BoundedSources.public

        def unscreened(self):
            public = real(self)
            return {**public, "bounded_sources": [{**entry, "source_name": value}
                                                  for entry in public["bounded_sources"]]}

        with patch.object(bs.BoundedSources, "public", unscreened):
            binding = await n.helper(*n.house())
        self.assertTrue(binding["bounded_sources"])
        self.assertNotIn(needle, json.dumps(binding))
        self.assertEqual({entry["source_name"] for entry in binding["bounded_sources"]},
                         {credentials.WITHHELD_MARKER})


async def script_diagnostics(count, sites=2):
    """``count`` scripts, each with ``sites`` over-bound template sites (one reason)."""

    candidates = tuple((f"script.s{i:04d}", f"s{i:04d}") for i in range(count))
    template = n.big_template()
    config = {"sequence": [{"service": "light.turn_on", "target": {"entity_id": template}}] * sites}
    loaded = ss.LoadedScriptSource(
        outcomes={entity: ((True, {"config": copy.deepcopy(config)}), (True, {})) for entity, _key in candidates},
        available=frozenset(entity for entity, _key in candidates))
    return await ss.collect_script_diagnostics(
        [], [], registry_complete=True,
        reader_factory=lambda: SimpleNamespace(current=lambda: True, provenance="synthetic"),
        secret="", concurrency=4, selection=ss.ScriptSelection(candidates, (), False), loaded=loaded)


class ExactOverBoundAccountingTests(unittest.IsolatedAsyncioTestCase):
    """Review 1 (M3): listed + omitted = total at the production limits."""

    def assert_exact(self, result, total):
        self.assertEqual(result.total, total)
        self.assertEqual(len(result.entries), min(total, bs.MAX_BOUNDED_SOURCES))
        self.assertEqual(len(result.entries) + result.omitted, result.total)
        public = result.public()
        listed = public["bounded_sources"]
        self.assertEqual(len(listed) + public["bounded_sources_omitted"], public["bounded_source_total"])
        self.assertEqual(len({(e["source_id"], e["reason_code"]) for e in listed}), len(listed))

    async def test_presentation_limit_minus_one_at_and_plus_one(self):
        for count in (bs.MAX_BOUNDED_SOURCES - 1, bs.MAX_BOUNDED_SOURCES, bs.MAX_BOUNDED_SOURCES + 1):
            with self.subTest(count=count):
                diagnostics = await script_diagnostics(count)
                self.assertEqual(diagnostics.profile["successful_documents"], count)
                self.assert_exact(bs.bounded_source_entries(diagnostics.bounded_obligations), count)

    async def test_script_obligation_cap_minus_one_at_and_plus_one(self):
        # Two sites per script: the old per-scan obligation cap fell at
        # MAX_SCRIPT_BOUNDED_OBLIGATIONS // 2 scripts (the reviewer's 501).
        half = ss.MAX_SCRIPT_BOUNDED_OBLIGATIONS // 2
        for count in (half - 1, half, half + 1):
            with self.subTest(count=count):
                diagnostics = await script_diagnostics(count)
                self.assertEqual(diagnostics.profile["successful_documents"], count)
                self.assertEqual(len(diagnostics.bounded_obligations), count, "one terminal per pair")
                self.assert_exact(bs.bounded_source_entries(diagnostics.bounded_obligations), count)

    async def test_script_source_limit_minus_one_and_at(self):
        # MAX_SCRIPT_SOURCES is also the obligation cap: one pair per script
        # stays exact up to the last script a scan admits.
        for count in (ss.MAX_SCRIPT_SOURCES - 1, ss.MAX_SCRIPT_SOURCES):
            with self.subTest(count=count):
                diagnostics = await script_diagnostics(count, sites=1)
                self.assertEqual(diagnostics.profile["successful_documents"], count)
                self.assert_exact(bs.bounded_source_entries(diagnostics.bounded_obligations), count)

    async def test_more_sites_per_script_do_not_change_the_total(self):
        for sites in (1, 2, 5):
            with self.subTest(sites=sites):
                diagnostics = await script_diagnostics(bs.MAX_BOUNDED_SOURCES + 1, sites=sites)
                self.assert_exact(bs.bounded_source_entries(diagnostics.bounded_obligations),
                                  bs.MAX_BOUNDED_SOURCES + 1)


if __name__ == "__main__":
    unittest.main()
