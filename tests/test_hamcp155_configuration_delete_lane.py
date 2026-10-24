"""Offline controls for the dormant HAMCP-155 seq-13 delete lane, not Core execution.

Receipts here are synthetic and structurally valid for the offline verifier
only. The lane itself runs only inside the disposable Real HA job, and only
once the seq-13 batch evidence run sets ``REAL_HA_SEQ13_LANES=enabled``.
"""
import asyncio
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import configuration_delete_contract_acceptance as lane  # noqa: E402
from alarmo_inspection_contract_acceptance import slot_binding  # noqa: E402
from test_alarmo_interval_observer import CORE, SLOT_VERSIONS  # noqa: E402

CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)
REFUSED = (AssertionError, ValueError, KeyError, TypeError)
ABSENT = {"config_status": 404, "registry_entry": None, "state_status": 404}


def synthetic_receipt(core_version=CORE):
    binding = slot_binding(core_version)
    reference = lane.source_profile()
    deleted = {"result": "PASS", "task_state": "succeeded_verified", "dispatch_counts": [1],
               "write_methods": ["DELETE"], "ha_writes": 1, "protected": False, "risk_level": "medium",
               "preflight_present": True, "readback": dict(ABSENT), "codes": ["exact_readback_verified"]}
    recreated = {"result": "PASS", "rollback_status": "rollback_plan_created",
                 "rollback_dispatched_on_create": False, "operations_excluded": [],
                 "task_state": "succeeded_verified", "dispatch_counts": [1], "write_methods": ["POST"],
                 "ha_writes": 1, "config_matches_pre_image": True, "registry_entry_id_restored": True,
                 "state_present": True}
    return {
        "result": "PASS", "lane": "configuration_delete", "core": core_version,
        "support_slot": binding["slot"], "core_image": binding["image"],
        "image_index_digest": binding["image_index_digest"], "image_receipt_sha256": "1" * 64,
        "engineering_source": lane.engineering_source(), "added_capability": dict(reference),
        "predecessor_capabilities_sha256": "2" * 64, "authority_transition": [22, 23],
        "authority_instances": "separate_ephemeral_registries", "production_authority": False,
        "provider_identity": "synthetic_disposable_lane",
        "predecessor_admission": {"route_acquired": False, "service_reason_code": lane.PENDING,
                                  "plan_created": False, "ha_requests": 0, "ha_writes": 0,
                                  "control_capability_acquired": True},
        "engineering_write_methods": ["DELETE", "POST"],
        "scenarios": {
            "delete_automation": copy.deepcopy(deleted), "delete_script": copy.deepcopy(deleted),
            "recreate_automation": dict(recreated), "recreate_script": dict(recreated),
            "protected_effect": {"result": "PASS", "plan_created": True, "protected": True,
                                 "basis": [lane.BASIS_PROTECTED_EFFECT], "risk_level": "high",
                                 "removes_statement": True, "ha_writes": 0},
            "not_found": {"result": "PASS", "preflight_config_status": 404, "reason_code": lane.NOT_FOUND,
                          "plan_created": False, "provider_dispatch_occurred": False, "ha_writes": 0},
            "stale_state": {"result": "PASS", "task_state": "failed_pre_dispatch", "dispatch_counts": [0],
                            "codes": [lane.STALE], "ha_writes": 0, "drifted_config_retained": True},
        },
        "fixture_cleanup": True, "fixture_absence": dict.fromkeys(lane.FIXTURE_ABSENCE, True),
    }


def scenario(name, **changes):
    def mutate(value):
        value["scenarios"][name].update(changes)
    return mutate


def top(**changes):
    def mutate(value):
        value.update(changes)
    return mutate


class ReceiptTests(unittest.TestCase):
    def verify(self, value):
        return lane.verify_result(value, core_version=CORE, support_slot=CORE_SLOT)

    def test_synthetic_receipt_verifies(self):
        summary = self.verify(synthetic_receipt())
        self.assertEqual(summary["authority_transition"], [22, 23])
        self.assertEqual(summary["scenarios"], len(lane.SCENARIOS))
        self.assertEqual(summary["contract_fingerprint"], lane.source_profile()["contract_fingerprint"])

    def test_reference_is_the_dormant_delete_profile_from_source(self):
        reference = lane.source_profile()
        self.assertEqual(reference["capability_id"], "core.configuration_delete_operation")
        self.assertEqual(reference["adapter_id"], lane.ADAPTER)
        shipped = {item["capability_id"] for item in lane.shipped_references()}
        self.assertEqual(len(shipped), lane.SHIPPED_SIGNED_COUNT)
        self.assertFalse(shipped & lane.DORMANT)

    def test_refusals_are_expected_outcomes_with_exact_codes(self):
        """A refusal recorded with another code, or a write, is a finding."""
        cases = {
            "predecessor_admitted": top(predecessor_admission={
                **synthetic_receipt()["predecessor_admission"], "route_acquired": True}),
            "predecessor_other_reason": top(predecessor_admission={
                **synthetic_receipt()["predecessor_admission"], "service_reason_code": "core_capability_unavailable"}),
            "predecessor_read": top(predecessor_admission={
                **synthetic_receipt()["predecessor_admission"], "ha_requests": 1}),
            "predecessor_stored": top(predecessor_admission={
                **synthetic_receipt()["predecessor_admission"], "plan_created": True}),
            "not_found_other_reason": scenario("not_found", reason_code="configuration_delete_invalid_value"),
            "not_found_plan_created": scenario("not_found", plan_created=True),
            "not_found_target_present": scenario("not_found", preflight_config_status=200),
            "stale_dispatched": scenario("stale_state", dispatch_counts=[1]),
            "stale_wrong_code": scenario("stale_state", codes=["delete_target_missing"]),
            "stale_wrote": scenario("stale_state", ha_writes=1),
            "stale_target_lost": scenario("stale_state", drifted_config_retained=False),
            "stale_verified": scenario("stale_state", task_state="succeeded_verified"),
        }
        for name, mutate in cases.items():
            value = synthetic_receipt()
            mutate(value)
            with self.subTest(name), self.assertRaises(REFUSED):
                self.verify(value)

    def test_every_claimed_read_is_asserted(self):
        """Lesson 3: config 404, registry entry gone and state gone, each on its own."""
        for resource_type in ("automation", "script"):
            for key, present in (("config_status", 200), ("registry_entry", "abc123"),
                                 ("state_status", 200), ("config_status", 500)):
                value = synthetic_receipt()
                value["scenarios"]["delete_" + resource_type]["readback"][key] = present
                with self.subTest(resource_type=resource_type, key=key, value=present), \
                        self.assertRaises(REFUSED):
                    self.verify(value)
            value = synthetic_receipt()
            del value["scenarios"]["delete_" + resource_type]["readback"]["state_status"]
            with self.subTest(resource_type=resource_type, missing="state_status"), self.assertRaises(REFUSED):
                self.verify(value)

    def test_success_and_recreate_controls(self):
        cases = {
            "delete_unverified": scenario("delete_automation", task_state="failed_post_dispatch"),
            "delete_twice": scenario("delete_script", dispatch_counts=[2], ha_writes=2),
            "delete_wrong_method": scenario("delete_automation", write_methods=["POST"]),
            "delete_not_present_first": scenario("delete_automation", preflight_present=False),
            "ordinary_elevated": scenario("delete_automation", protected=True),
            "recreate_drifted": scenario("recreate_automation", config_matches_pre_image=False),
            "recreate_new_registry_id": scenario("recreate_script", registry_entry_id_restored=False),
            "recreate_self_dispatched": scenario("recreate_automation", rollback_dispatched_on_create=True),
            "recreate_excluded": scenario("recreate_script", operations_excluded=["0"]),
            "protected_standard": scenario("protected_effect", protected=False),
            "protected_no_basis": scenario("protected_effect", basis=["delete_protected_dependency"]),
            "protected_no_statement": scenario("protected_effect", removes_statement=False),
            "protected_medium": scenario("protected_effect", risk_level="medium"),
            "scenario_failed": scenario("stale_state", result="FAIL"),
            "scenario_missing": lambda value: value["scenarios"].pop("recreate_script"),
            "cleanup_incomplete": top(fixture_absence={**dict.fromkeys(lane.FIXTURE_ABSENCE, True),
                                                       "states": False}),
            "production_authority": top(production_authority=True),
            "other_transition": top(authority_transition=[21, 22]),
            "unexpected_write_method": top(engineering_write_methods=["DELETE", "PATCH", "POST"]),
            "other_adapter": top(added_capability={**lane.source_profile(), "adapter_id": "other"}),
        }
        for name, mutate in cases.items():
            value = synthetic_receipt()
            mutate(value)
            with self.subTest(name), self.assertRaises(REFUSED):
                self.verify(value)


class WiringTests(unittest.TestCase):
    def test_lane_is_defined_but_not_wired(self):
        for flag in (None, "", "Enabled", "true", "1"):
            environment = {k: v for k, v in os.environ.items() if k != lane.ENABLE_FLAG}
            if flag is not None:
                environment[lane.ENABLE_FLAG] = flag
            with self.subTest(flag=flag), patch.dict(os.environ, environment, clear=True), \
                    patch.object(lane, "_run_disposable") as inner:
                self.assertFalse(lane.enabled())
                with self.assertRaises(ValueError) as raised:
                    asyncio.run(lane.run_disposable(object(), expected_image="x", core_version=CORE))
                inner.assert_not_called()
                self.assertEqual(raised.exception.contract_missing_key, "seq13_configuration_delete")

    def test_no_harness_or_workflow_calls_the_lane(self):
        for path in [ROOT / "scripts" / "real_ha_contract_tests.py",
                     *sorted((ROOT / ".github" / "workflows").glob("*.yml"))]:
            with self.subTest(path=path.name):
                self.assertNotIn("configuration_delete_contract_acceptance", path.read_text())

    def test_shipped_lanes_never_sign_the_delete_profile(self):
        for name in ("alarmo_inspection_contract_acceptance.py", "automation_baseline_contract_acceptance.py",
                     "entity_metadata_contract_acceptance.py"):
            with self.subTest(name):
                self.assertIn("core.configuration_delete_operation", (ROOT / "scripts" / name).read_text())


class CleanupTests(unittest.TestCase):
    def test_cleanup_reads_back_every_absence(self):
        class Rest:
            def __init__(self):
                self.configs = {("automation", "a"): {}, ("script", "b"): {}}
                self.states = {"automation.a", "script.b"}
                self.deleted = []

            async def call(self, method, path, body=None):
                parts = path.strip("/").split("/")
                if parts[0] == "states":
                    return (200, {}) if parts[1] in self.states else (404, None)
                key = (parts[1], parts[3])
                if method == "DELETE":
                    self.deleted.append(key)
                    self.configs.pop(key)
                    self.states.discard(key[0] + "." + key[1])
                    return 200, {}
                return (200, self.configs[key]) if key in self.configs else (404, None)

            async def ok(self, method, path, body=None):
                return (await self.call(method, path, body))[1]

        class Raw:
            def __init__(self, rest, lingering=None):
                self.rest = rest
                self.lingering = lingering

            async def ok(self, payload):
                assert payload == {"type": lane.REGISTRY_LIST}
                items = [{"platform": t, "unique_id": i, "id": t + i} for t, i in self.rest.configs]
                if self.lingering:
                    items.append({"platform": "script", "unique_id": "b", "id": "stale"})
                return items

        rest = Rest()
        owned = [("automation", "a"), ("script", "b")]
        absence = asyncio.run(lane.cleanup_fixtures(rest, Raw(rest), owned, attempts=1, delay=0))
        self.assertEqual(absence, dict.fromkeys(lane.FIXTURE_ABSENCE, True))
        self.assertEqual(rest.deleted, owned)

        # An acknowledged delete whose registry entry lingers is not absence.
        rest = Rest()
        absence = asyncio.run(lane.cleanup_fixtures(rest, Raw(rest, lingering=True), owned, attempts=2, delay=0))
        self.assertIs(absence["registry_entries"], False)
        self.assertIsNone(asyncio.run(lane.cleanup_fixtures(rest, Raw(rest), [], attempts=1, delay=0)))


if __name__ == "__main__":
    unittest.main()
