"""HAMCP-226: unavailable references and named bound overruns, end to end.

The real dependency provider reads synthetic Home Assistant peers (in-process
fakes, no network): automation states (aliases are ``friendly_name``),
stored automation configurations and the entity registry. The index it
builds feeds the integrity analysis, ``entity_dependency_analysis`` and the
helper dependency binding, so every path a name takes to the caller runs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT / "tests"))

from ha_mcp_engineering.configuration_definitions import credentials as c  # noqa: E402
from ha_mcp_engineering.dependency import bounded_sources as bs  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import SOURCE_TYPES  # noqa: E402
from ha_mcp_engineering.dependency.obligation_ledger import MAX_TEMPLATE_SOURCE_CHARS  # noqa: E402
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.governance.helper_dependency import HelperDependencyRiskService  # noqa: E402
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402
from ha_mcp_engineering.integrity.service import ConfigurationIntegrityAnalysisService  # noqa: E402

import hamcp226_credential_cases as cases  # noqa: E402

SECRET = cases.KNOWN_SECRET
DEAD = "light.office_tv_lights"
HELPER = "input_boolean.lighting_mode"
CHANGED = "2026-10-21T07:15:02.123456+00:00"


def big_template() -> str:
    return "{{ " + "x" * (MAX_TEMPLATE_SOURCE_CHARS + 16) + " }}"


def automation(object_id, alias, config, *, state="on"):
    return {
        "state": {
            "entity_id": f"automation.{object_id}",
            "state": state,
            "attributes": {"id": object_id, "friendly_name": alias},
        },
        "config": config,
    }


def house(**overrides):
    """Synthetic peers: one dead target, one over-bound writer, one clean one."""

    sources = {
        "office_tv": automation(
            "office_tv", "Office TV",
            {"id": "office_tv", "alias": "Office TV",
             "trigger": [{"platform": "time", "at": "07:00:00"}],
             "action": [{"service": "light.turn_on", "target": {"entity_id": DEAD}}]},
        ),
        "token_refresh": automation(
            "token_refresh", "Token refresh",
            {"id": "token_refresh", "alias": "Token refresh",
             "trigger": [{"platform": "time", "at": "03:00:00"}],
             "action": [{"service": "input_boolean.turn_on",
                         "target": {"entity_id": big_template()}}]},
        ),
        "clean": automation(
            "clean", "Hallway",
            {"id": "clean", "alias": "Hallway",
             "trigger": [{"platform": "state", "entity_id": "sensor.live"}],
             "action": []},
        ),
    }
    sources.update(overrides)
    states = [item["state"] for item in sources.values()] + [
        {"entity_id": DEAD, "state": "unavailable", "last_changed": CHANGED,
         "attributes": {"friendly_name": "Office TV lights"}},
        {"entity_id": "sensor.live", "state": "21.5", "last_changed": CHANGED,
         "attributes": {"friendly_name": "Live"}},
        {"entity_id": HELPER, "state": "off", "last_changed": CHANGED,
         "attributes": {"friendly_name": "Lighting mode"}},
    ]
    return Rest(states, {key: item["config"] for key, item in sources.items()}), WebSocket()


class Rest:
    def __init__(self, states, configs):
        self.states = states
        self.configs = configs
        self.calls = []

    async def request(self, method, path, **_options):
        self.calls.append((method, path))
        if path == "/states":
            return copy.deepcopy(self.states)
        object_id = unquote(path.split("/config/automation/config/", 1)[-1])
        if object_id in self.configs:
            return copy.deepcopy(self.configs[object_id])
        raise RuntimeError("synthetic not found")


class WebSocket:
    def __init__(self):
        self.calls = []

    async def command(self, payload):
        self.calls.append(payload)
        if payload.get("type") == "config/entity_registry/list":
            return [{"entity_id": DEAD, "platform": "demo", "disabled_by": None},
                    {"entity_id": HELPER, "platform": "input_boolean", "disabled_by": None}]
        raise RuntimeError("synthetic unsupported command")


async def build(rest, websocket):
    provider = DirectHaDependencyProvider(rest, websocket, secret=SECRET, concurrency=2)
    return DependencyIndex(provider), provider


async def integrity(rest, websocket, **options):
    index, _provider = await build(rest, websocket)
    service = ConfigurationIntegrityAnalysisService(
        DirectHaIntegrityProvider(index, rest, websocket, secret=SECRET),
        cursor_key=b"k" * 32,
    )
    result = await service.analyze(
        source_types=list(SOURCE_TYPES), include_orphan_candidates=False,
        detail_level="evidence", limit=100, refresh_index=True, **options,
    )
    return result


async def dependency(rest, websocket, entity_id):
    index, _provider = await build(rest, websocket)
    return await EntityDependencyAnalysisService(index).analyze(
        entity_id=entity_id, detail_level="evidence", refresh_index=True,
        source_types=["automation", "blueprint"],
    )


async def helper(rest, websocket):
    index, _provider = await build(rest, websocket)
    return (await HelperDependencyRiskService(index).assess(HELPER, refresh=True))["binding"]


def rendered(result) -> str:
    data = result.data if hasattr(result, "data") else result
    warnings = getattr(result, "warnings", [])
    return json.dumps({"data": data, "warnings": warnings}, sort_keys=True, default=str)


def findings_of(result, finding_type):
    return [item for item in result.data["findings"] if item["finding_type"] == finding_type]


class UnavailableReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_haac_e002_dead_target_is_a_finding_with_evidence(self):
        result = await integrity(*house())
        found = findings_of(result, "unavailable_entity_reference")
        self.assertEqual([item.get("target_entity_id") for item in found], [DEAD])
        self.assertEqual(found[0]["target_state"], "unavailable")
        self.assertEqual(found[0]["target_last_changed"], CHANGED)
        self.assertEqual(found[0]["severity"], "medium")
        self.assertEqual(found[0]["source_id"], "office_tv")
        # An available present target stays silent; nothing is reported missing.
        self.assertFalse(any(item.get("target_entity_id") == "sensor.live"
                             for item in result.data["findings"]))
        self.assertEqual(findings_of(result, "missing_entity_reference"), [])

    async def test_one_finding_per_state_and_precedence(self):
        expected = {
            "unavailable": ("unavailable_entity_reference", "medium"),
            "unknown": ("unavailable_entity_reference", "low"),
            "on": (None, None),
        }
        for value, (finding_type, severity) in expected.items():
            with self.subTest(state=value):
                rest, websocket = house()
                next(item for item in rest.states if item["entity_id"] == DEAD)["state"] = value
                found = [item for item in (await integrity(rest, websocket)).data["findings"]
                         if item.get("target_entity_id") == DEAD]
                self.assertEqual([item["finding_type"] for item in found],
                                 [finding_type] if finding_type else [])
                if finding_type:
                    self.assertEqual(found[0]["severity"], severity)

    async def test_disabled_source_lowers_severity(self):
        rest, websocket = house()
        next(item for item in rest.states if item["entity_id"] == "automation.office_tv")["state"] = "off"
        found = findings_of(await integrity(rest, websocket), "unavailable_entity_reference")
        self.assertEqual(found[0]["severity"], "low")

    async def test_disabled_registry_entry_takes_precedence(self):
        rest, websocket = house()
        original = websocket.command

        async def command(payload):
            rows = await original(payload)
            for row in rows:
                if row["entity_id"] == DEAD:
                    row["disabled_by"] = "user"
            return rows

        websocket.command = command
        found = [item for item in (await integrity(rest, websocket)).data["findings"]
                 if item.get("target_entity_id") == DEAD]
        self.assertEqual([item["finding_type"] for item in found], ["disabled_entity_reference"])

    async def test_absent_target_stays_missing(self):
        rest, websocket = house()
        rest.states = [item for item in rest.states if item["entity_id"] != DEAD]
        original = websocket.command

        async def command(payload):
            return [row for row in await original(payload) if row["entity_id"] != DEAD]

        websocket.command = command
        found = [item for item in (await integrity(rest, websocket)).data["findings"]
                 if item.get("target_entity_id") == DEAD]
        self.assertEqual([item["finding_type"] for item in found], ["missing_entity_reference"])

    async def test_malformed_last_changed_is_dropped_not_shown(self):
        for value in ("yesterday", "2026-10-21T07:15:02+00:00 " + SECRET, 12):
            with self.subTest(kind=type(value).__name__):
                rest, websocket = house()
                next(item for item in rest.states if item["entity_id"] == DEAD)["last_changed"] = value
                result = await integrity(rest, websocket)
                found = findings_of(result, "unavailable_entity_reference")
                self.assertIsNone(found[0].get("target_last_changed"))
                self.assertEqual(found[0]["target_state"], "unavailable")
                self.assertNotIn(SECRET, rendered(result))

    async def test_target_availability_in_dependency_analysis(self):
        for target, state in ((DEAD, "unavailable"), ("sensor.live", "available"),
                              ("sensor.nowhere", "absent")):
            with self.subTest(target=target):
                result = await dependency(*house(), target)
                availability = result.data["target_availability"]
                self.assertEqual(availability["state"], state)
                self.assertEqual(availability["last_changed"], None if state == "absent" else CHANGED)


class NamedBoundedSourceTests(unittest.IsolatedAsyncioTestCase):
    def assert_named(self, data):
        entries = data["bounded_sources"]
        self.assertEqual(
            [(item["source_id"], item["reason_code"], item["bound"], item["bound_value"])
             for item in entries],
            [("token_refresh", "template_source_limit_exceeded",
              "MAX_TEMPLATE_SOURCE_CHARS", MAX_TEMPLATE_SOURCE_CHARS)],
        )
        self.assertEqual(entries[0]["source_entity_id"], "automation.token_refresh")
        self.assertEqual(data["bounded_sources_omitted"], 0)
        self.assertEqual(data["bounded_sources_withheld"], 0)
        self.assertEqual(data["bounded_source_total"], 1)

    async def test_integrity_names_the_over_bound_automation(self):
        result = await integrity(*house())
        self.assert_named(result.data)
        self.assertTrue(any("bounded_sources" in item for item in result.warnings))

    async def test_dependency_names_it_whatever_the_target(self):
        # sensor.live is referenced only by "clean"; the over-bound writer is
        # still listed because it could reference any target (HAAC-E002a).
        result = await dependency(*house(), "sensor.live")
        self.assert_named(result.data)
        self.assertTrue(result.partial)
        self.assertTrue(any("bounded_sources" in item for item in result.warnings))
        self.assertNotEqual(result.data["assessment"]["rename_or_removal_status"],
                            "no_references_detected_within_coverage")

    async def test_helper_binding_names_the_hidden_writer(self):
        binding = await helper(*house())
        self.assert_named(binding)
        self.assertFalse(binding["coverage_complete"])

    async def test_helper_bounded_sources_are_not_approval_material(self):
        first = await helper(*house())
        rest, websocket = house(token_refresh=automation(
            "token_refresh", "Token refresh",
            {"id": "token_refresh", "alias": "Token refresh",
             "trigger": [{"platform": "time", "at": "03:00:00"}],
             "action": [{"service": "input_boolean.turn_on",
                         "target": {"entity_id": big_template()}}]}))
        second = await helper(rest, websocket)
        self.assertEqual(first["evidence_fingerprint"], second["evidence_fingerprint"])

    async def test_clean_house_lists_nothing(self):
        rest, websocket = house()
        del rest.configs["token_refresh"]
        rest.states = [item for item in rest.states if item["entity_id"] != "automation.token_refresh"]
        for data in ((await integrity(rest, websocket)).data,
                     (await dependency(rest, websocket, "sensor.live")).data,
                     await helper(rest, websocket)):
            self.assertEqual(data["bounded_sources"], [])
            self.assertEqual(data["bounded_source_total"], 0)


def planted(alias, object_id="token_refresh"):
    """The over-bound automation and a helper writer, both carrying ``alias``."""

    over = automation(
        object_id, alias,
        {"id": object_id, "alias": alias,
         "trigger": [{"platform": "time", "at": "03:00:00"}],
         "action": [{"service": "input_boolean.turn_on", "target": {"entity_id": big_template()}}]},
    )
    over["state"]["entity_id"] = "automation.token_refresh"
    writer = automation(
        "writer", alias,
        {"id": "writer", "alias": alias,
         "trigger": [{"platform": "time", "at": "04:00:00"}],
         "action": [{"service": "input_boolean.toggle", "target": {"entity_id": HELPER}},
                    {"service": "light.turn_on", "target": {"entity_id": DEAD}}]},
    )
    return house(token_refresh=over, writer=writer)


async def every_path(alias, object_id="token_refresh"):
    """``{path: (rendered output, result)}`` for each analysis a name reaches."""

    output = {}
    result = await integrity(*planted(alias, object_id))
    output["integrity"] = (rendered(result), result)
    for target in (HELPER, DEAD, "sensor.live"):
        result = await dependency(*planted(alias, object_id), target)
        output["dependency:" + target] = (rendered(result), result)
    binding = await helper(*planted(alias, object_id))
    output["helper"] = (rendered(binding), binding)
    return output


def writer_finding(result, finding_type="unavailable_entity_reference"):
    return next(item for item in result.data["findings"]
                if item.get("source_id") == "writer"
                and item.get("finding_type", finding_type) == finding_type)


class DefaultModeScreenTests(unittest.IsolatedAsyncioTestCase):
    """Owner direction: Home Assistant names use the HAMCP-132 default mode."""

    async def test_token_refresh_and_benign_names_are_shown(self):
        for alias in cases.BENIGN:
            with self.subTest(alias=alias):
                paths = await every_path(alias)
                self.assertEqual(writer_finding(paths["integrity"][1])["source_name"], alias)
                self.assertEqual(writer_finding(paths["dependency:" + HELPER][1])["source_name"], alias)
                for name, (text, _result) in paths.items():
                    for marker in (c.WITHHELD_MARKER, c.UNVERIFIABLE_MARKER, "REDACTED"):
                        self.assertNotIn(marker, text, name)

    async def test_credential_aliases_are_withheld_on_every_path(self):
        for label, (value, needle) in cases.withheld_cases().items():
            with self.subTest(case=label):
                paths = await every_path(value)
                for name, (text, _result) in paths.items():
                    self.assertNotIn(needle, text, f"{label} survives in {name}")
                    self.assertNotIn("REDACTED", text, f"{label} is redacted in part in {name}")
                found = writer_finding(paths["integrity"][1])
                self.assertIn(found["source_name"], {c.WITHHELD_MARKER, c.UNVERIFIABLE_MARKER})
                self.assertEqual(found["target_entity_id"], DEAD, "the finding itself is kept")
                result = paths["dependency:" + HELPER][1]
                self.assertIn(writer_finding(result)["source_name"],
                              {c.WITHHELD_MARKER, c.UNVERIFIABLE_MARKER})
                # Withholding a name never hides the reference it names.
                self.assertEqual(result.data["overview"]["direct_reference_count"], 1)
                self.assertEqual(result.data["assessment"]["rename_or_removal_status"], "not_safe")
                self.assertTrue(any("withheld whole by the credential screen" in item
                                    for item in result.warnings))

    async def test_unverifiable_aliases_are_never_shown_as_written(self):
        for label, value in cases.unverifiable_cases().items():
            with self.subTest(case=label):
                paths = await every_path(value)
                self.assertNotEqual(writer_finding(paths["integrity"][1])["source_name"], value)
                self.assertNotEqual(writer_finding(paths["dependency:" + HELPER][1])["source_name"], value)

    async def test_credential_identifier_fails_closed_and_is_never_shown(self):
        # The provider does not read a configuration under a credential-shaped
        # id: it is a coverage failure, so nothing can be ruled out.
        for label, (value, needle) in cases.withheld_cases().items():
            with self.subTest(case=label):
                paths = await every_path("Token refresh", value)
                for name, (text, _result) in paths.items():
                    self.assertNotIn(needle, text, f"{label} survives in {name}")
                self.assertTrue(paths["integrity"][1].partial)
                result = paths["dependency:sensor.live"][1]
                self.assertTrue(result.partial)
                self.assertNotEqual(result.data["assessment"]["rename_or_removal_status"],
                                    "no_references_detected_within_coverage")
                self.assertFalse(paths["helper"][1]["coverage_complete"])


class ExtractionScreenWindowTests(unittest.TestCase):
    """The screen reads the shown prefix plus a margin, not a whole 64 KiB template."""

    def test_credential_crossing_the_cut_is_withheld_whole(self):
        from ha_mcp_engineering.dependency import extraction
        for offset in (0, 100, 159, 160 + extraction._SCREEN_MARGIN - 200):
            with self.subTest(offset=offset):
                value = "x" * offset + cases.credential_url() + "y" * 70_000
                self.assertEqual(extraction._bounded(value), c.WITHHELD_MARKER)

    def test_credential_straddling_the_cut_is_withheld_whole(self):
        # Property: everything output (the first 160 characters) is screened,
        # with a 1,024-character margin past the cut. A credential that starts
        # inside the shown part and ends past the cut is withheld whole; its
        # shown head is never returned on its own.
        from ha_mcp_engineering.dependency import extraction
        limit = 160
        for kind, credential, secret in (("url", cases.credential_url(), ""),
                                         ("known_secret", SECRET, SECRET)):
            for inside in (1, len(credential) // 2, len(credential) - 1):
                with self.subTest(kind=kind, inside=inside):
                    offset = limit - inside
                    self.assertGreater(offset + len(credential), limit)
                    value = "x" * offset + credential + "y" * 70_000
                    self.assertEqual(extraction._bounded(value, limit, secret), c.WITHHELD_MARKER)
                    # Without the screen, the bound alone would show the head.
                    unscreened = extraction.redact_data(value, secret="", max_string=limit)
                    self.assertIn(credential[:inside], str(unscreened))

    def test_text_past_the_window_is_never_shown(self):
        from ha_mcp_engineering.dependency import extraction
        value = "x" * (160 + extraction._SCREEN_MARGIN) + cases.credential_url()
        shown = extraction._bounded(value)
        self.assertNotIn(cases.DB_PASSWORD, shown)
        self.assertTrue(shown.endswith("<truncated>"))

    def test_known_secret_is_withheld_not_redacted_in_part(self):
        from ha_mcp_engineering.dependency import extraction
        self.assertEqual(extraction._bounded("Token refresh " + SECRET, secret=SECRET), c.WITHHELD_MARKER)
        self.assertEqual(extraction._bounded("Token refresh", secret=SECRET), "Token refresh")


class BoundTableTests(unittest.TestCase):
    def test_bound_values_are_the_enforcing_constants(self):
        from ha_mcp_engineering.dependency import extraction, obligation_ledger
        table = bs.bound_table()
        self.assertEqual(table["configuration_obligation_limit_exceeded"],
                         ("MAX_DOCUMENT_OBLIGATIONS", extraction.MAX_DOCUMENT_OBLIGATIONS))
        self.assertEqual(table["template_source_limit_exceeded"],
                         ("MAX_TEMPLATE_SOURCE_CHARS", obligation_ledger.MAX_TEMPLATE_SOURCE_CHARS))
        self.assertTrue(table.keys().isdisjoint(bs.READ_FAILURE_REASON_CODES))

    def obligation(self, index, reason="configuration_obligation_limit_exceeded", **fields):
        values = {"source_type": "automation", "source_id": f"auto_{index:03d}",
                  "source_entity_id": f"automation.auto_{index:03d}", "reason_code": reason,
                  "outcome": "coverage_failure"}
        values.update(fields)
        return type("Obligation", (), values)()

    def test_unknown_reason_is_unclassified_and_still_listed(self):
        result = bs.bounded_source_entries([self.obligation(1, "future_limit_exceeded")])
        self.assertEqual(result.entries[0]["bound"], bs.UNCLASSIFIED)
        self.assertIsNone(result.entries[0]["bound_value"])
        self.assertEqual(result.entries[0]["reason_code"], "future_limit_exceeded")

    def test_read_failures_and_other_outcomes_are_not_listed(self):
        values = [self.obligation(1, "automation_config_unreadable"),
                  self.obligation(2, outcome="exact_dependency")]
        self.assertEqual(bs.bounded_source_entries(values).total, 0)

    def test_bound_at_fifty_with_deterministic_order(self):
        values = [self.obligation(index) for index in reversed(range(53))]
        result = bs.bounded_source_entries(values)
        self.assertEqual(len(result.entries), bs.MAX_BOUNDED_SOURCES)
        self.assertEqual(result.omitted, 3)
        self.assertEqual(result.total, 53)
        self.assertEqual([item["source_id"] for item in result.entries],
                         [f"auto_{index:03d}" for index in range(50)])
        self.assertEqual(bs.bounded_source_entries(list(reversed(values))), result)
        warnings = bs.screening_warnings(result)
        self.assertTrue(any("omitted after the first 50" in item for item in warnings))

    def test_fifty_exactly_omits_nothing(self):
        result = bs.bounded_source_entries([self.obligation(index) for index in range(50)])
        self.assertEqual((len(result.entries), result.omitted), (50, 0))

    def test_one_entry_per_source_and_reason(self):
        values = [self.obligation(1), self.obligation(1),
                  self.obligation(1, "template_source_limit_exceeded")]
        self.assertEqual(bs.bounded_source_entries(values).total, 2)

    def test_source_type_filter(self):
        values = [self.obligation(1), self.obligation(2, source_type="script")]
        result = bs.bounded_source_entries(values, source_types=["script"])
        self.assertEqual([item["source_type"] for item in result.entries], ["script"])

    def test_withheld_identifier_is_counted_not_named(self):
        for label, (value, needle) in cases.withheld_cases().items():
            with self.subTest(case=label):
                result = bs.bounded_source_entries(
                    [self.obligation(1, source_id=value), self.obligation(2)], known_secrets=(SECRET,))
                self.assertEqual([item["source_id"] for item in result.entries], ["auto_002"])
                self.assertEqual((result.withheld, result.total), (1, 2))
                self.assertNotIn(needle, json.dumps(result.public()))
                self.assertTrue(any("could not be named" in item for item in bs.screening_warnings(result)))

    def test_screened_reason_becomes_unclassified_but_source_is_named(self):
        value = self.obligation(1, cases.credential_url())
        result = bs.bounded_source_entries([value], known_secrets=(SECRET,))
        self.assertEqual(result.entries[0]["source_id"], "auto_001")
        self.assertEqual(result.entries[0]["reason_code"], bs.UNCLASSIFIED)
        self.assertNotIn(cases.DB_PASSWORD, json.dumps(result.public()))


if __name__ == "__main__":
    unittest.main()
