"""Offline controls for the HAMCP-179 entity metadata receipt, not Core execution.

Receipts here are synthetic (tests/entity_metadata_receipts.py). The actual
contract runs only inside the disposable Real HA job on each window Core slot.
"""
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import entity_metadata_contract_acceptance as lane  # noqa: E402
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
from entity_metadata_receipts import synthetic_receipt  # noqa: E402
from test_alarmo_interval_observer import (CORE, NOT_SLOTS, NOT_STRINGS, OTHER_CORE,  # noqa: E402
                                           SLOT_VERSIONS, cleanup_value)

CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)
REFUSED = (AssertionError, ValueError, KeyError, TypeError)


def scenario(name, **changes):
    def mutate(value):
        value["scenarios"][name].update(changes)
    return mutate


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.value = synthetic_receipt(CORE)

    def verify(self, value, core_version=CORE, support_slot=CORE_SLOT):
        return lane.verify_result(value, core_version=core_version, support_slot=support_slot)

    def test_receipt_follows_every_window_slot(self):
        for slot, version in SLOT_VERSIONS.items():
            with self.subTest(slot=slot):
                summary = lane.verify_result(synthetic_receipt(version), core_version=version, support_slot=slot)
                self.assertEqual(summary, {
                    "result": "PASS", "core": version, "support_slot": slot,
                    "contract_fingerprint": lane.source_profile()["contract_fingerprint"],
                    "scenarios": len(lane.SCENARIOS), "authority_transition": [21, 22], "fixture_cleanup": True})

    def test_reference_is_the_dormant_profile_and_adapter_from_source(self):
        reference = lane.source_profile()
        self.assertEqual(reference["capability_id"], "core.entity_registry_metadata_operation")
        self.assertEqual(reference["adapter_id"], "compiled-core-entity-registry-metadata-operation-v1")
        self.assertRegex(reference["contract_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("config/label_registry/create", lane.REVIEWED_COMMANDS)

    def test_refuses_wrong_core_slot_or_missing_binding_arguments(self):
        for other in [v for v in SLOT_VERSIONS.values() if v != CORE] + list(NOT_SLOTS):
            with self.subTest(caller_core=other), self.assertRaises(REFUSED):
                self.verify(copy.deepcopy(self.value), core_version=other)
        for wrong in NOT_STRINGS:
            value = copy.deepcopy(self.value)
            value["core"] = wrong
            with self.subTest(core=wrong), self.assertRaises(REFUSED):
                self.verify(value, core_version=wrong)
        wrong_slots = [slot for slot in ("current", "candidate", "rollback") if slot != CORE_SLOT]
        for wrong in [*wrong_slots, "", CORE_SLOT.title(), " " + CORE_SLOT, *NOT_STRINGS]:
            value = copy.deepcopy(self.value)
            value["support_slot"] = wrong
            with self.subTest(nested_slot=wrong), self.assertRaises(REFUSED):
                self.verify(value)
            with self.subTest(caller_slot=wrong), self.assertRaises(REFUSED):
                self.verify(copy.deepcopy(self.value), support_slot=wrong)
        with self.assertRaises(TypeError):
            lane.verify_result(copy.deepcopy(self.value))
        with self.assertRaises(TypeError):
            lane.verify_result(copy.deepcopy(self.value), core_version=CORE)

    def test_refuses_wrong_image_digest_fingerprint_source_or_authority(self):
        other = alarmo.slot_binding(OTHER_CORE)
        fingerprint = "sha256:" + "0" * 64

        def wrong_fingerprint(value):
            value["contract_fingerprint"] = fingerprint
            value["added_capability"]["contract_fingerprint"] = fingerprint

        for mutate in (
                lambda v: v.update(core=OTHER_CORE),
                lambda v: v.update(result="FAIL"),
                lambda v: v.update(core_image=other["image"]),
                lambda v: v.update(image_index_digest=other["image_index_digest"]),
                lambda v: v.update(image_index_digest="sha256:" + "0" * 64),
                lambda v: v.update(image_receipt_sha256="1" * 63),
                wrong_fingerprint,
                lambda v: v.update(contract_fingerprint=fingerprint),
                lambda v: v["added_capability"].update(contract_fingerprint=fingerprint),
                lambda v: v.update(adapter_id="compiled-core-entity-registry-metadata-operation-v2"),
                lambda v: v["added_capability"].update(profile_version=v["profile_version"] + 1),
                lambda v: v["added_capability"].pop("adapter_id"),
                lambda v: v["engineering_source"].update(runtime_tree="0" * 40),
                lambda v: v["engineering_source"].update(commit="0" * 39),
                lambda v: v.update(authority_transition=[22, 22]),
                lambda v: v.update(authority_transition=[21, 21]),
                lambda v: v.update(authority_instances="shared_registry"),
                lambda v: v.update(production_authority=True),
                lambda v: v.update(fixture_cleanup=False),
                lambda v: v.update(provider_identity="supervisor"),
                lambda v: v.update(predecessor_capabilities_sha256=""),
                lambda v: v["predecessor_admission"].update(acquired=True),
                lambda v: v["predecessor_admission"].update(ha_requests=1),
                lambda v: v["predecessor_admission"].update(ha_updates=1),
                lambda v: v["predecessor_admission"].update(reason="denied"),
                lambda v: v["predecessor_admission"].update(control_capability_acquired=False),
                lambda v: v["engineering_command_types"].append("config/label_registry/create"),
                lambda v: v["engineering_command_types"].append("call_service"),
                lambda v: v.update(engineering_command_types=[lane.GET]),
                lambda v: v.update(synthetic_labels=list(reversed(v["synthetic_labels"]))),
                lambda v: v["synthetic_labels"].append(lane.UNKNOWN_LABEL),
                lambda v: v.update(synthetic_labels=[v["synthetic_labels"][0], lane.UNKNOWN_LABEL]),
                lambda v: v.pop("scenarios"),
                lambda v: v.pop("engineering_source"),
        ):
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.subTest(mutation=inspect.getsource(mutate).strip()), self.assertRaises(REFUSED):
                self.verify(value)

    def test_refuses_missing_reordered_or_weakened_scenarios(self):
        names = list(lane.SCENARIOS)

        def drop(name):
            return lambda v: v["scenarios"].pop(name)

        def reorder(value):
            value["scenarios"] = dict(reversed(list(value["scenarios"].items())))

        def extra(value):
            value["scenarios"]["label_creation"] = {"result": "PASS"}

        mutations = [drop(name) for name in names] + [reorder, extra] + [
            scenario(name, result="FAIL") for name in names] + [
            scenario("closed_field_update", readback={**self.value["scenarios"]["closed_field_update"]["readback"],
                                                      "icon": "mdi:lamp"}),
            scenario("closed_field_update", dispatch_count=2),
            scenario("closed_field_update", unmanaged_fields_preserved=False),
            scenario("closed_field_update", task_state="succeeded_unverified"),
            scenario("restore", readback={**self.value["scenarios"]["restore"]["readback"], "name": "left"}),
            scenario("no_change", plan_created=True),
            scenario("no_change", ha_updates=1),
            scenario("not_found", error_code="ENTITY_NOT_FOUND"),
            scenario("not_found", provider_dispatch_occurred=True),
            scenario("area_requires_own_name", raw={"area_without_name": "accepted", "name_with_area": "accepted",
                                                    "clear_name_with_area": "invalid_info"}),
            scenario("area_requires_own_name", raw={"area_without_name": "invalid_info",
                                                    "name_with_area": "accepted",
                                                    "clear_name_with_area": "accepted"}),
            scenario("area_requires_own_name", planner={"area_without_name": None,
                                                        "clear_name_with_area": None}),
            scenario("area_requires_own_name", restored=False),
            scenario("area_requires_own_name", ha_updates=1),
            scenario("unknown_label", raw_readback=[self.value["synthetic_labels"][0], lane.UNKNOWN_LABEL]),
            scenario("unknown_label", planner_reason=None),
            scenario("unknown_label", ha_updates=1),
            scenario("alias_normalization", raw_readback=[" Lamp ", "", None]),
            scenario("alias_normalization", raw_readback=["Lamp", "", None]),
            scenario("alias_normalization", ha_commands=1),
            scenario("response_lost_applied", replay_updates=1),
            scenario("response_lost_applied", replay_reused=False),
            scenario("response_lost_applied", dispatch_count=2),
            scenario("response_lost_applied", indeterminate=False),
            scenario("response_lost_unapplied", replay="redispatch_permitted"),
            scenario("response_lost_unapplied", replay_updates=1),
            scenario("response_lost_unapplied", ha_updates=1),
            scenario("response_lost_unapplied", task_state="succeeded_verified"),
            scenario("response_lost_unapplied", readback_unchanged=False),
        ]
        for mutate in mutations:
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.subTest(index=mutations.index(mutate)), self.assertRaises(REFUSED):
                self.verify(value)

    def test_receipt_bytes_are_canonical(self):
        raw = lane.receipt_bytes(self.value)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw), self.value)
        self.assertEqual(raw, lane.receipt_bytes(json.loads(raw)))


class VerifierMutationTests(unittest.TestCase):
    """Each named refusal is load-bearing: removing it lets the bad receipt through."""

    LINES = {
        "core": ('assert result["result"] == "PASS" and result["core"] == core_version',),
        "support_slot": ('assert type(result.get("support_slot")) is str and result["support_slot"] == support_slot',),
        "image_index_digest": ('assert result["image_index_digest"] == binding["image_index_digest"]',),
        "contract_fingerprint": ('assert result["added_capability"] == reference',
                                 'assert all(result[key] == reference[key] for key in REFERENCE_KEYS)'),
        "runtime_tree": ('assert result["engineering_source"]["runtime_tree"] == '
                         'engineering_source()["runtime_tree"]',),
        "command_types": ('assert set(result["engineering_command_types"]) <= REVIEWED_COMMANDS',),
    }

    def bad(self, name):
        value = synthetic_receipt(CORE)
        if name == "core":
            value["core"] = OTHER_CORE
        elif name == "support_slot":
            value["support_slot"] = next(slot for slot in SLOT_VERSIONS if slot != CORE_SLOT)
        elif name == "image_index_digest":
            value["image_index_digest"] = "sha256:" + "0" * 64
        elif name == "contract_fingerprint":
            value["contract_fingerprint"] = value["added_capability"]["contract_fingerprint"] = "sha256:" + "0" * 64
        elif name == "runtime_tree":
            value["engineering_source"]["runtime_tree"] = "0" * 40
        else:
            value["engineering_command_types"].append("config/label_registry/create")
        return value

    def mutated(self, lines):
        source = inspect.getsource(lane.verify_result)
        for line in lines:
            self.assertEqual(source.count(line), 1, line)
            source = source.replace(line, "pass")
        namespace = dict(vars(lane))
        exec(compile(source, lane.__file__, "exec"), namespace)
        return namespace["verify_result"]

    def test_key_refusals_are_load_bearing(self):
        for name, lines in self.LINES.items():
            with self.subTest(refusal=name):
                with self.assertRaises(REFUSED):
                    lane.verify_result(self.bad(name), core_version=CORE, support_slot=CORE_SLOT)
                accepted = self.mutated(lines)(self.bad(name), core_version=CORE, support_slot=CORE_SLOT)
                self.assertEqual(accepted["result"], "PASS")


class RetainedReceiptTests(unittest.TestCase):
    def cleanup(self, value, core=CORE, standalone=None):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "result.json", Path(directory) / "cleanup.json"
            source.write_text(json.dumps(value))
            receipt = None
            if standalone is not None:
                receipt = Path(directory) / "entity-metadata.json"
                receipt.write_bytes(standalone)
            with patch("subprocess.run", return_value=SimpleNamespace(stdout=b"")), patch(
                    "automation_baseline_contract_acceptance.verify_result", return_value={"result": "PASS"}):
                result = alarmo.verify_disposable_cleanup(source, output, core, receipt)
            self.assertEqual(json.loads(output.read_text()), result)
            return result

    def test_cleanup_binds_the_standalone_receipt_bytes_on_every_slot(self):
        for slot, version in SLOT_VERSIONS.items():
            value = cleanup_value(version)
            raw = lane.receipt_bytes(value["entity_metadata"])
            with self.subTest(slot=slot):
                nested = self.cleanup(value, version, raw)["required_entity_metadata"]
                self.assertEqual((nested["result"], nested["core"], nested["support_slot"]), ("PASS", version, slot))
                self.assertEqual(nested["receipt_sha256"], hashlib.sha256(raw).hexdigest())

    def test_cleanup_refuses_divergent_standalone_or_wrong_nested_receipt(self):
        value = cleanup_value(CORE)
        tampered = copy.deepcopy(value["entity_metadata"])
        tampered["scenarios"]["no_change"]["ha_updates"] = 1
        for standalone in (lane.receipt_bytes(tampered), b"", lane.receipt_bytes(value["entity_metadata"])[:-1]):
            with self.subTest(standalone=len(standalone)), self.assertRaisesRegex(ValueError, "differs"):
                self.cleanup(value, CORE, standalone)
        for mutate in (lambda v: v.pop("entity_metadata"),
                       lambda v: v.update(entity_metadata=synthetic_receipt(OTHER_CORE)),
                       lambda v: v["entity_metadata"].update(result="FAIL"),
                       lambda v: v["entity_metadata"].update(image_index_digest="sha256:" + "0" * 64)):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.subTest(mutation=inspect.getsource(mutate).strip()), self.assertRaises(REFUSED):
                self.cleanup(changed)


class LaneGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_retains_locations_without_exception_or_argument_content(self):
        private = "synthetic-do-not-retain"
        with patch.object(lane, "_run_disposable", side_effect=AssertionError(private)):
            with self.assertRaises(AssertionError) as found:
                await lane.run_disposable(private, expected_image=private, core_version=private)
        self.assertEqual(found.exception.contract_missing_key, "entity_metadata")
        diagnostic = found.exception.contract_diagnostic
        self.assertEqual(set(diagnostic), {"entity_metadata_source_lines", "entity_metadata_stage"})
        self.assertTrue(all(type(line) is int for line in diagnostic["entity_metadata_source_lines"]))
        self.assertEqual(diagnostic["entity_metadata_stage"], "binding")
        self.assertNotIn(private, json.dumps(diagnostic))
        with patch.object(lane, "_run_disposable", side_effect=lane.RawRefusal("config/x", "invalid_info")):
            with self.assertRaises(lane.RawRefusal) as found:
                await lane.run_disposable(private, expected_image=private, core_version=private)
        self.assertIn("raw_refusal", found.exception.contract_diagnostic)
        self.assertNotIn(private, json.dumps(found.exception.contract_diagnostic))

    async def test_lane_refuses_unknown_core_wrong_lane_image_or_remote_host_before_reads(self):
        binding = alarmo.slot_binding(CORE)
        local = SimpleNamespace(ha_url="http://127.0.0.1:8123")
        cases = [
            (local, binding["image"], OTHER_CORE, binding["lane"]),
            (local, binding["image"], CORE, alarmo.slot_binding(OTHER_CORE)["lane"]),
            (local, binding["image"], CORE, None),
            (local, alarmo.slot_binding(OTHER_CORE)["image"], CORE, binding["lane"]),
            (local, binding["image"].split("@")[0], CORE, binding["lane"]),
            (SimpleNamespace(ha_url="http://homeassistant.local:8123"), binding["image"], CORE, binding["lane"]),
        ]
        hidden = ("HA_CONTRACT_LANE", "REAL_HA_ALARMO_IMAGE_RESULT", "REAL_HA_ENTITY_METADATA_RESULT")
        for configured, image, version, lane_name in cases:
            environ = {k: v for k, v in os.environ.items() if k not in hidden}
            if lane_name is not None:
                environ["HA_CONTRACT_LANE"] = lane_name
            with self.subTest(image=image, version=version, lane=lane_name, url=configured.ha_url), \
                    patch.dict(os.environ, environ, clear=True), \
                    patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                    self.assertRaisesRegex(ValueError, "Exact owned disposable entity metadata lane required"):
                # No image receipt is configured, so passing the guard would raise KeyError instead.
                await lane._run_disposable(configured, expected_image=image, core_version=version, stage=["x"])
            client.assert_not_called()
        for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
            environ = {k: v for k, v in os.environ.items() if k not in hidden}
            environ["HA_CONTRACT_LANE"] = binding["lane"]
            with self.subTest(unknown=unknown), patch.dict(os.environ, environ, clear=True), \
                    self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                await lane._run_disposable(local, expected_image=binding["image"], core_version=unknown, stage=["x"])

    async def test_lane_refuses_a_wrong_slot_image_receipt_before_any_client(self):
        binding = alarmo.slot_binding(CORE)
        other = alarmo.slot_binding(OTHER_CORE)
        receipts = [
            {"result": "PASS", "index": other["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
            {"result": "PASS", "index": binding["image_index_digest"], "core_version": OTHER_CORE,
             "support_slot": CORE_SLOT},
            {"result": "PASS", "index": binding["image_index_digest"], "core_version": CORE,
             "support_slot": other["slot"]},
            {"result": "FAIL", "index": binding["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
        ]
        for image in receipts:
            with tempfile.TemporaryDirectory() as directory:
                image_path, output = Path(directory) / "image.json", Path(directory) / "entity-metadata.json"
                image_path.write_text(json.dumps(image))
                environ = {"HA_CONTRACT_LANE": binding["lane"], "REAL_HA_ALARMO_IMAGE_RESULT": str(image_path),
                           "REAL_HA_ENTITY_METADATA_RESULT": str(output)}
                with self.subTest(image=image), patch.dict(os.environ, environ), \
                        patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                        self.assertRaises(AssertionError):
                    await lane._run_disposable(SimpleNamespace(ha_url="http://127.0.0.1:8123"),
                                               expected_image=binding["image"], core_version=CORE, stage=["x"])
                client.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
