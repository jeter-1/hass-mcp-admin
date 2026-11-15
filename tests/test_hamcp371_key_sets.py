"""HAMCP-371 S10: the closed 9.2.0 key sets (21:36 record, items 7 to 12).

``upstream_reads_9_2_keys.ROWS`` must equal the committed output-key contract
(``tests/fixtures/ha_mcp_9_2_0/output-key-contract.json``), and its engine
must withhold and count, pass open containers whole, treat map keys as data,
replace unreviewed warnings with the fixed code and fail closed on shapes it
cannot count. The real-Core replay is tests/test_hamcp371_real_core_replay.py.

Synthetic values only.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.providers import upstream_reads_9_1_config as config_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402

CONTRACT = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_2_0/output-key-contract.json").read_text(encoding="utf-8"))
# Rows with their own shipped or 9.2.0 module, not routed through ROWS.
OWN_MODULES = {"ha_get_blueprint", "ha_get_device", "ha_get_history",
               "ha_get_system_health", "ha_get_integration", "ha_get_app"}


def _echo(payload, arguments):
    return dict(payload)


def _complete(payload):
    return False, []


class RowsEqualTheContract(unittest.TestCase):

    def test_rules(self):
        rules = CONTRACT["rules"]
        self.assertEqual(keys.OPEN_RULED, frozenset(rules["open_containers_ruled"]))
        self.assertEqual(list(keys.ENGINEERING_ADDED), rules["engineering_added"])
        self.assertEqual(keys.REPLACED_WARNING, rules["replaced_warning_code"])
        self.assertEqual(keys.COUNT_CAP, rules["count_cap"])

    def test_rows(self):
        self.assertEqual(set(keys.ROWS), set(CONTRACT["reads"]) - OWN_MODULES)
        for tool, row in keys.ROWS.items():
            source = CONTRACT["reads"][tool]
            with self.subTest(tool=tool):
                self.assertEqual(row.admitted, frozenset(source["admitted"]))
                self.assertEqual(row.drops, frozenset(source["policy_drops"]))
                self.assertEqual(row.opened, frozenset(source["open_containers"]) | keys.OPEN_RULED)
                self.assertEqual(row.maps, source["map_containers"])
                self.assertEqual(tuple(row.warnings), tuple(source["reviewed_warnings"]))
                self.assertFalse(row.admitted & set(keys.ENGINEERING_ADDED))

    def test_every_row_has_a_9_2_projection(self):
        for tool in keys.ROWS:
            with self.subTest(tool=tool):
                self.assertIsNotNone(reads_9_2._projection(tool))

    def test_prefix_rules_are_the_two_reviewed_families(self):
        # HAMCP-371 E1 (13:25 ruling): the ha_get_state visibility prefix had no
        # 9.2.0 receipt and was withdrawn with the state ``warnings`` key.
        with_prefixes = {tool for tool, row in keys.ROWS.items() if row.prefixes}
        self.assertEqual(with_prefixes, {"ha_config_get_automation", "ha_list_services"})

    def test_typed_keys_are_the_s10_2_ruling(self):
        typed = {tool: row.types for tool, row in keys.ROWS.items() if row.types}
        self.assertEqual(typed, {"ha_list_services": {"collapsed": bool, "integration": str}})
        self.assertEqual(CONTRACT["reads"]["ha_list_services"]["value_types"],
                         {"collapsed": "bool", "integration": "str"})


class Engine(unittest.TestCase):

    def project(self, tool, payload):
        return keys.projection(tool, _echo)(payload, {})

    def test_withheld_and_policy_drops_are_counted_apart(self):
        row = keys.ROWS["ha_config_get_automation"]
        drop = sorted(row.drops)[0]
        out = self.project("ha_config_get_automation", {
            "success": True, "hamcp371_a": 1, "hamcp371_b": {"hamcp371_c": 2}, drop: {"x": 1}})
        self.assertNotIn("hamcp371_a", out)
        self.assertNotIn(drop, out)
        self.assertEqual(out["fields_withheld"], 3)
        self.assertEqual(out["fields_dropped_by_policy"], 1)
        self.assertEqual(out["upstream_warnings_replaced"], 0)

    def test_counts_are_distinct_names_and_capped(self):
        rows = [{"hamcp371_same": i} for i in range(5)]
        out = self.project("ha_search", {"success": True, "entities": rows})
        self.assertEqual(out["fields_withheld"], 1)
        many = {f"hamcp371_{i}": i for i in range(1500)}
        out = self.project("ha_search", {"success": True, **many})
        self.assertEqual(out["fields_withheld"], keys.COUNT_CAP)
        self.assertNotIn("hamcp371_1", json.dumps(out))

    def test_open_containers_pass_whole_and_uncounted(self):
        out = self.project("ha_get_state", {
            "success": True, "attributes": {"hamcp371_attr": {"deep": 1}}})
        self.assertEqual(out["attributes"], {"hamcp371_attr": {"deep": 1}})
        self.assertEqual(out["fields_withheld"], 0)

    def test_map_keys_are_data(self):
        payload = {"success": True, "services": {"light.hamcp371": {
            "name": "x", "fields": {"hamcp371_field": {"description": "d", "hamcp371_inner": 1}}}}}
        out = self.project("ha_list_services", payload)
        self.assertEqual(out["services"], {"light.hamcp371": {
            "name": "x", "fields": {"hamcp371_field": {"description": "d"}}}})
        self.assertEqual(out["fields_withheld"], 1)

    def test_unreviewed_warnings_are_replaced_once_and_counted(self):
        # ha_list_services: E1 withdrew ``warnings`` from the overview row.
        out = self.project("ha_list_services", {
            "success": True, "warnings": ["hamcp371 one", 7, "hamcp371 two"]})
        self.assertEqual(out["warnings"], [keys.REPLACED_WARNING])
        self.assertEqual(out["upstream_warnings_replaced"], 3)

    def test_reviewed_prefixes(self):
        language = keys._SERVICES_LANGUAGE_PREFIX + ": synthetic error 371"
        out = self.project("ha_list_services", {"success": True, "warnings": [language, "other"]})
        self.assertEqual(out["warnings"], [keys.SERVICES_LANGUAGE_WARNING, keys.REPLACED_WARNING])
        self.assertNotIn("synthetic error 371", json.dumps(out))
        self.assertEqual(out["upstream_warnings_replaced"], 1)
        # A prefix is not a substring match.
        out = self.project("ha_list_services", {"success": True, "warnings": ["x " + language]})
        self.assertEqual(out["warnings"], [keys.REPLACED_WARNING])

    def test_state_warnings_are_withheld_not_reviewed(self):
        # E1: no 9.2.0 receipt shows ha_get_state ``warnings``; it is withheld
        # and counted, and no longer marks the read partial.
        out = self.project("ha_get_state", {"success": True, "warnings": ["hamcp371 w"]})
        self.assertNotIn("warnings", out)
        self.assertEqual(out["fields_withheld"], 1)
        self.assertEqual(out["upstream_warnings_replaced"], 0)

    def test_typed_service_keys(self):
        # S10-2: target.entity[].integration is a str and a field section's
        # collapsed is a bool (Core 2026.11.0 selector.py:178,212 and
        # service.py:996,1004); any other value type is withheld and counted.
        def services(integration, collapsed):
            return {"success": True, "services": {"light.turn_on": {
                "target": {"entity": [{"integration": integration, "domain": ["light"]}]},
                "fields": {"advanced": {"collapsed": collapsed, "fields": {}}}}}}
        out = self.project("ha_list_services", services("hue", True))
        service = out["services"]["light.turn_on"]
        self.assertEqual(service["target"]["entity"][0]["integration"], "hue")
        self.assertIs(service["fields"]["advanced"]["collapsed"], True)
        self.assertEqual(out["fields_withheld"], 0)
        for integration, collapsed in ((7, True), ("hue", "yes"), (None, 1), (["hue"], False)):
            with self.subTest(integration=integration, collapsed=collapsed):
                out = self.project("ha_list_services", services(integration, collapsed))
                service = out["services"]["light.turn_on"]
                wrong = (not isinstance(integration, str)) + (type(collapsed) is not bool)
                self.assertEqual("integration" in service["target"]["entity"][0],
                                 isinstance(integration, str))
                self.assertEqual("collapsed" in service["fields"]["advanced"],
                                 type(collapsed) is bool)
                self.assertEqual(out["fields_withheld"], wrong)

    def test_fails_closed(self):
        for payload in ([], "text", None, 1):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.project("ha_search", payload)
        for returned in ([], {"fields_withheld": 0}, {"upstream_warnings_replaced": 0}):
            with self.subTest(returned=returned), self.assertRaises(ValueError):
                keys.projection("ha_search", lambda p, a, r=returned: r)({"success": True}, {})


class Completeness(unittest.TestCase):

    def test_9_1_texts_name_9_2_0(self):
        for old, new in keys.TEXTS.items():
            with self.subTest(old=old):
                self.assertIn(old, (config_9_1.GROUP_FIELDS_UNAVAILABLE_WARNING,
                                    config_9_1.TEMPLATE_FIELDS_UNAVAILABLE_WARNING))
                self.assertIn("9.2.0", new)
                self.assertNotIn("9.1", new)
        rule = keys.completeness(lambda p: (True, [config_9_1.GROUP_FIELDS_UNAVAILABLE_WARNING]), keys.TEXTS)
        self.assertEqual(rule({}), (True, [keys.TEXTS[config_9_1.GROUP_FIELDS_UNAVAILABLE_WARNING]]))

    def test_a_replaced_warning_makes_the_read_partial(self):
        rule = keys.completeness(_complete, keys.TEXTS)
        self.assertEqual(rule({"upstream_warnings_replaced": 0}), (False, []))
        self.assertEqual(rule({"upstream_warnings_replaced": 2}), (True, [keys.REPLACED_PARTIAL]))
        rule = keys.completeness(lambda p: (True, [keys.REPLACED_PARTIAL]))
        self.assertEqual(rule({"upstream_warnings_replaced": 1}), (True, [keys.REPLACED_PARTIAL]))


if __name__ == "__main__":
    unittest.main()
