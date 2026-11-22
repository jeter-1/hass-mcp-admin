"""HAMCP-371 D5: the 9.2.0 platform reads' counts and warning rule.

``upstream_reads_9_2_platform.project`` is reached from one 9.2.0 gate in the
shared ``upstream_platform_reads.project_result``. These tests cover:

* the module against the committed output-key contract: ``KNOWN`` is the
  contract's admitted, consumed-structure and recomputed keys, and its name
  walk equals the generator's on the hash-bound 9.2.0 receipts;
* r20 (system health) on both 9.2.0 receipt sets: the 18 names the rebuild
  drops are withheld and counted, never dropped silently, including health
  ``info`` keys outside the reviewed set;
* r26 (config entries) on both receipt sets, and a synthetic config entry;
* r27 (installed apps): no successful 9.2.0 receipt exists (no Supervisor in
  the local lane, 05:35 ruling), so the projection is tested with a synthetic
  payload only;
* every upstream warning replaced and counted, with Engineering's own codes
  kept, and the 999 cap;
* the gate is keyed on the module's ``RELEASES``: with 9.2.0 taken out of it,
  the module is never called and the result equals the shipped rebuild.
  HAMCP-437: only 9.2.0 is admitted (D1), so the 8.5.0/9.1.2 loop is retired.

Receipt payloads are hash-checked through the replay module; other values are
synthetic. No endpoint is contacted.
"""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_platform_reads as platform  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_platform as platform_9_2  # noqa: E402
import test_hamcp129_platform_reads as shipped_tests  # noqa: E402
import test_hamcp371_real_core_replay as replay  # noqa: E402

HEALTH, APP, INTEGRATION = platform_9_2.HEALTH, platform_9_2.APP, platform_9_2.INTEGRATION
READS = replay.READS
COUNTS = ("fields_withheld", "fields_dropped_by_policy", "upstream_warnings_replaced")
INJECTED = "hamcp371_d5_injected"


def project(version, tool, payload, arguments=None):
    entry = shipped_tests.policy_entry(version, tool)
    return platform.project_result(entry, deepcopy(payload), arguments or {})


def receipts(call_id):
    for set_name in ("A/9.2.0", "B/9.2.0"):
        for call, rec in replay.public_calls(set_name):
            if call["id"] == call_id:
                yield set_name, call, rec["exchange"]["response"]["result"]["structuredContent"]


def synthetic_apps(*apps, warnings=None):
    return shipped_tests.apps_payload(list(apps), warnings=warnings)


class Contract(unittest.TestCase):

    def test_releases(self):
        self.assertEqual(platform_9_2.RELEASES, frozenset({"9.2.0"}))

    def test_known_is_the_contract(self):
        for tool in (HEALTH, APP, INTEGRATION):
            row = READS[tool]
            with self.subTest(tool=tool):
                self.assertEqual(row["projection"], "rebuild")
                self.assertEqual(row["open_containers"], [])
                self.assertEqual(row["policy_drops"], [])
                self.assertEqual(platform_9_2.KNOWN[tool], frozenset(
                    set(row["admitted"]) | set(row["consumed_structure"])
                    | set(row["recomputed_by_engineering"])))
                self.assertEqual(tuple(row["qualified_containers"]),
                                 platform_9_2.QUALIFY.get(tool, ()))

    def test_the_walk_equals_the_generator_on_the_receipts(self):
        for call_id, tool in (("r20-system-health", HEALTH), ("r26-integrations", INTEGRATION)):
            for set_name, _call, content in receipts(call_id):
                with self.subTest(set=set_name, call=call_id):
                    self.assertEqual(platform_9_2.names(content, tool),
                                     replay.generator.names(content, tool))

    def test_the_contract_records_r20_withheld_names(self):
        self.assertEqual(len(READS[HEALTH]["withheld_by_rebuild"]), 18)
        self.assertIn("homeassistant.config_dir", READS[HEALTH]["withheld_by_rebuild"])


class Receipts(unittest.TestCase):

    def test_r20_counts_the_names_the_rebuild_drops(self):
        cases = list(receipts("r20-system-health"))
        self.assertEqual(len(cases), 2)
        for set_name, call, content in cases:
            with self.subTest(set=set_name):
                out = project("9.2.0", HEALTH, content, call["public_arguments"])
                withheld = set(READS[HEALTH]["withheld_by_rebuild"]) & replay.generator.names(content, HEALTH)
                self.assertEqual(len(withheld), 18)
                self.assertEqual(out["fields_withheld"], 18)
                self.assertEqual(out["fields_dropped_by_policy"], 0)
                self.assertEqual(out["upstream_warnings_replaced"], 0)
                self.assertEqual(out["warnings"], [])
                self.assertEqual(out["completeness"], {"status": "complete", "reasons": []})
                # The shipped rebuild is otherwise unchanged.
                shipped = platform._project_health(deepcopy(content), platform.normalized_arguments(
                    HEALTH, call["public_arguments"]))
                self.assertEqual({k: v for k, v in out.items() if k not in COUNTS}, shipped)

    def test_r20_an_unreviewed_info_key_is_withheld_and_counted(self):
        _set, call, content = next(receipts("r20-system-health"))
        base = project("9.2.0", HEALTH, content, call["public_arguments"])
        injected = deepcopy(content)
        injected["health_info"]["data"]["homeassistant"]["info"][INJECTED] = "x"
        injected["health_info"]["data"]["recorder"]["info"][INJECTED] = "y"
        out = project("9.2.0", HEALTH, injected, call["public_arguments"])
        # Two qualified names: homeassistant.<k> and recorder.<k>.
        self.assertEqual(out["fields_withheld"], base["fields_withheld"] + 2)
        self.assertNotIn(INJECTED, json.dumps(out))
        # A new health domain's name is data (the shipped rebuild lists it in
        # other_domains); its info keys are withheld and counted.
        injected = deepcopy(content)
        injected["health_info"]["data"]["hamcp371_domain"] = {"info": {"hamcp371_a": 1, "hamcp371_b": 2}}
        out = project("9.2.0", HEALTH, injected, call["public_arguments"])
        self.assertEqual(out["fields_withheld"], base["fields_withheld"] + 2)
        self.assertIn("hamcp371_domain", out["platform"]["other_domains"])
        self.assertNotIn("hamcp371_a", json.dumps(out))

    def test_r26_counts_the_names_the_rebuild_drops(self):
        cases = list(receipts("r26-integrations"))
        self.assertEqual(len(cases), 2)
        for set_name, call, content in cases:
            with self.subTest(set=set_name):
                out = project("9.2.0", INTEGRATION, content, call["public_arguments"])
                withheld = (set(READS[INTEGRATION]["withheld_by_rebuild"])
                            & replay.generator.names(content, INTEGRATION))
                self.assertTrue(withheld)
                self.assertEqual(out["fields_withheld"], len(withheld))
                self.assertEqual(out["upstream_warnings_replaced"], 0)


class Synthetic(unittest.TestCase):

    def test_integration_unknown_entry_key_is_counted(self):
        entry = shipped_tests.config_entry(1)
        base = project("9.2.0", INTEGRATION, shipped_tests.entries_payload([entry]))
        entry[INJECTED] = {"hamcp371_inner": 1}
        out = project("9.2.0", INTEGRATION, shipped_tests.entries_payload([entry]))
        self.assertEqual(out["fields_withheld"], base["fields_withheld"] + 2)
        self.assertNotIn(INJECTED, json.dumps(out))

    def test_app_synthetic_payload(self):
        # Disclosed: no successful 9.2.0 r27 receipt (no Supervisor locally).
        payload = synthetic_apps(shipped_tests.app("core_ssh", stats=True),
                                 shipped_tests.app("core_mosquitto", state="stopped"))
        out = project("9.2.0", APP, payload, {"include_stats": True})
        shipped = platform._project_apps(deepcopy(payload), platform.normalized_arguments(
            APP, {"include_stats": True}))
        self.assertEqual({k: v for k, v in out.items() if k not in COUNTS}, shipped)
        expected = platform_9_2.names(payload, APP) - platform_9_2.KNOWN[APP]
        self.assertIn("ingress_url", expected)
        self.assertIn("network_rx", expected)
        self.assertEqual(out["fields_withheld"], len(expected))
        self.assertEqual(out["fields_dropped_by_policy"], 0)
        self.assertNotIn(shipped_tests.CANARY, json.dumps(out))

    def test_warnings_are_replaced_and_counted(self):
        out = project("9.2.0", HEALTH, shipped_tests.health_payload(
            baseline=False, warnings=["hamcp371 one", 7, {"x": 1}]))
        self.assertEqual(out["warnings"], ["baseline_unavailable", keys.REPLACED_WARNING])
        self.assertEqual(out["upstream_warnings_replaced"], 3)
        self.assertEqual(out["completeness"]["status"], "partial")
        self.assertIn("upstream_warnings_replaced", out["completeness"]["reasons"])
        partial, texts = platform.completeness(out)
        self.assertTrue(partial)
        self.assertIn("upstream_warnings_replaced", texts[0])
        self.assertNotIn("hamcp371 one", json.dumps(out))

        payload = synthetic_apps(shipped_tests.app("core_ssh", stats=False),
                                 warnings=["hamcp371 app warning"])
        payload["addons"][0]["stats"] = None
        out = project("9.2.0", APP, payload, {"include_stats": True})
        # The shipped rule drops upstream_warnings_withheld beside a stats
        # code; on 9.2.0 both Engineering's code and the replacement remain.
        self.assertEqual(out["warnings"], ["stats_unavailable:core_ssh", keys.REPLACED_WARNING])
        self.assertEqual(out["upstream_warnings_replaced"], 1)
        self.assertEqual(out["completeness"]["reasons"],
                         ["stats_unavailable", "upstream_warnings_replaced"])

        out = project("9.2.0", INTEGRATION, shipped_tests.entries_payload(
            [shipped_tests.config_entry(1)], warnings="hamcp371 one string"))
        self.assertEqual(out["warnings"], [keys.REPLACED_WARNING])
        self.assertEqual(out["upstream_warnings_replaced"], 1)
        self.assertNotIn(platform_9_2.SHIPPED_WITHHELD, json.dumps(out))

    def test_counts_are_capped(self):
        entry = shipped_tests.config_entry(1, **{f"hamcp371_{i}": i for i in range(1500)})
        out = project("9.2.0", INTEGRATION, shipped_tests.entries_payload(
            [entry], warnings=["w"] * 1500))
        self.assertEqual(out["fields_withheld"], keys.COUNT_CAP)
        self.assertEqual(out["upstream_warnings_replaced"], keys.COUNT_CAP)

    def test_shipped_refusals_still_hold(self):
        with self.assertRaises(ValueError):
            project("9.2.0", INTEGRATION, shipped_tests.entries_payload(
                [shipped_tests.config_entry(1, options={"x": 1})]))
        with self.assertRaises(ValueError):
            project("9.2.0", HEALTH, {"success": False})


class OutsideTheModuleSet(unittest.TestCase):

    def test_a_release_outside_the_module_set_never_calls_it(self):
        payloads = {
            HEALTH: (shipped_tests.health_payload(warnings=["hamcp371"]), {}),
            APP: (synthetic_apps(shipped_tests.app("core_ssh"), warnings=["hamcp371"]), {}),
            INTEGRATION: (shipped_tests.entries_payload(
                [shipped_tests.config_entry(1, **{INJECTED: 1})], warnings=["hamcp371"]), {}),
        }
        shipped = {HEALTH: platform._project_health, APP: platform._project_apps,
                   INTEGRATION: platform._project_integrations}
        with patch.object(platform_9_2, "RELEASES", frozenset()):
            for tool, (payload, arguments) in payloads.items():
                with self.subTest(tool=tool), patch.object(
                        platform_9_2, "project", side_effect=AssertionError("9.2.0 only")) as spy:
                    out = project("9.2.0", tool, payload, arguments)
                    spy.assert_not_called()
                    self.assertFalse(set(COUNTS) & set(out))
                    self.assertIn(platform_9_2.SHIPPED_WITHHELD, out["warnings"])
                    self.assertEqual(out, shipped[tool](deepcopy(payload), platform.normalized_arguments(
                        tool, arguments)))


if __name__ == "__main__":
    unittest.main()
