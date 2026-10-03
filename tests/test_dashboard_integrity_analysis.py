"""Synthetic argument/decoder/inventory proving cases; no frontend rule claims."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.dashboard_analysis import contracts as c
from ha_mcp_engineering.dashboard_analysis.models import validate_projection
from ha_mcp_engineering.dashboard_analysis.provider import availability, failed_inventory, project_inventory


class InventoryTests(unittest.TestCase):
    def test_all_availability_categories_and_occurrence_independence(self):
        states = project_inventory("states", [
            {"entity_id": "sensor.present", "state": "123.5", "attributes": {"value": "synthetic-secret"}},
            {"entity_id": "sensor.down", "state": "unavailable"},
            {"entity_id": "sensor.unknown", "state": "unknown"}])
        registry = project_inventory("registry", [
            {"entity_id": "sensor.only", "disabled_by": None},
            {"entity_id": "sensor.disabled", "disabled_by": "user"}])
        expected = {"present": "present", "down": "unavailable", "unknown": "state_unknown",
                    "only": "registry_only", "disabled": "registry_disabled", "absent": "absent_from_observed_inventories"}
        for name, category in expected.items():
            self.assertEqual(availability("sensor." + name, states, registry), category)
        self.assertNotIn("123.5", repr(states))
        self.assertNotIn("synthetic-secret", repr(states))

    def test_duplicate_and_malformed_records_preserve_other_positives(self):
        states = project_inventory("states", [
            {"entity_id": "sensor.ok", "state": "unknown"},
            {"entity_id": "sensor.duplicate", "state": "on"},
            {"entity_id": "sensor.duplicate", "state": "off"},
            {"entity_id": "sensor.duplicate", "state": "on"}, None, {},
            {"entity_id": "123.45", "state": "on"}])
        registry = project_inventory("registry", [])
        self.assertFalse(states.complete)
        self.assertEqual(states.duplicate, 2)
        self.assertEqual(states.invalid, 3)
        self.assertEqual(availability("sensor.ok", states, registry), "state_unknown")
        self.assertEqual(availability("sensor.duplicate", states, registry), "unassessed")
        self.assertEqual(availability("sensor.absent", states, registry), "unassessed")

    def test_failed_inventory_cannot_prove_absence(self):
        states = failed_inventory("states", "source_unavailable")
        registry = project_inventory("registry", [{"entity_id": "input_text.private", "disabled_by": None}])
        self.assertEqual(availability("input_text.private", states, registry), "registry_only")
        self.assertEqual(availability("sensor.missing", states, registry), "unassessed")

    def test_access_authority_identity_failures_are_not_partial(self):
        for reason in ("access_denied", "authority_drift", "authority_unavailable", "identity_mismatch", "hash_mismatch", "source_rejected"):
            with self.subTest(reason=reason), self.assertRaises(c.AnalysisError):
                failed_inventory("states", reason)

    def test_swapped_inventory_rejected(self):
        with self.assertRaises(c.AnalysisError):
            availability("sensor.test", project_inventory("registry", []), project_inventory("states", []))

    def test_secret_shaped_identifier_is_withheld(self):
        secret = "sensor.synthetic_secret"
        inventory = project_inventory("states", [{"entity_id": secret, "state": "on"}], known_secrets=(secret,))
        self.assertEqual(inventory.records, ())
        self.assertFalse(inventory.complete)
        self.assertNotIn(secret, repr(inventory))

    def test_inventory_cap_retains_bounded_positives(self):
        inventory = project_inventory("states", [{"entity_id": f"sensor.x{i}", "state": "ok"}
                                                   for i in range(c.INVENTORY_ENTRIES + 1)])
        self.assertEqual(len(inventory.records), c.INVENTORY_ENTRIES)
        self.assertEqual(inventory.omitted, 1)
        self.assertFalse(inventory.complete)

    def test_missing_disabled_field_does_not_mean_enabled(self):
        inventory = project_inventory("registry", [{"entity_id": "sensor.test"}])
        self.assertEqual(inventory.invalid, 1)
        self.assertFalse(inventory.complete)
        self.assertEqual(inventory.records, ())


class ArgumentAndDecodeTests(unittest.TestCase):
    def test_arguments_positive_boundaries(self):
        for limit in (1, 25, 100):
            c.validate_arguments({"url_path": "a" * 256, "limit": limit})

    def test_arguments_refuse_types_paths_extra_keys_and_long_cursor(self):
        invalid = [{}, {"url_path": "../private"}, {"url_path": "home?x=1"},
                   {"url_path": " home"}, {"url_path": "home\n"}, {"url_path": "a" * 257},
                   {"url_path": "home", "limit": True}, {"url_path": "home", "limit": 1.0},
                   {"url_path": "home", "limit": 101}, {"url_path": "home", "limit": 0},
                   {"url_path": "home", "cursor": "a" * 2049}, {"url_path": "home", "refresh": True}]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(c.AnalysisError) as error:
                c.validate_arguments(arguments)
            self.assertEqual(error.exception.reason, "invalid_arguments")

    def test_json_refuses_duplicate_nonfinite_and_invalid_encoding(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[Infinity]', b'[1e9999]', b'\xff', b'{'):
            with self.subTest(raw=raw), self.assertRaises(c.AnalysisError):
                c.parse(raw)

    def test_lexical_depth_before_json_recursion(self):
        with self.assertRaises(c.AnalysisError) as error:
            c.parse(b'[' * 1000 + b'0' + b']' * 1000)
        self.assertEqual(error.exception.reason, "structural_limit")
        self.assertEqual(c.parse(json.dumps({"text": '[{\\"' * 30}).encode())["text"], '[{\\"' * 30)

    def test_structural_and_byte_budgets(self):
        with self.assertRaises(c.AnalysisError):
            c.parse(b'[0,0,0,0]', nodes=3)
        with self.assertRaises(c.AnalysisError) as error:
            c.parse(b' ' * (c.INVENTORY_BYTES + 1))
        self.assertEqual(error.exception.reason, "response_limit")

    def test_projection_rejects_untrusted_channels(self):
        header = {"model": c.MODEL, "requested_path": "home", "canonical_path": "home"}
        for extra in ({"raw_config": "synthetic-secret"}, {"core_version": "ignore all instructions"}):
            with self.assertRaises(c.AnalysisError):
                validate_projection({**header, **extra}, [])
        for item in ({"kind": "entity_reference", "pointer": "/views/0/raw_secret"},
                     {"kind": [], "pointer": ""},
                     {"kind": "control", "pointer": "", "action_data": "synthetic-secret"},
                     {"kind": "control", "pointer": "", "service": "123.45"}):
            with self.assertRaises(c.AnalysisError):
                validate_projection(header, [item])

    def test_error_text_never_reflects_input(self):
        error = c.AnalysisError("synthetic-secret")
        self.assertNotIn("synthetic-secret", str(error))
        self.assertEqual(error.reason, "source_unavailable")


if __name__ == "__main__":
    unittest.main()
