"""Offline controls for the dormant HAMCP-155 seq-13 delete lane, not Core execution.

Receipts here are synthetic and structurally valid for the offline verifier
only. The lane itself runs only inside the disposable Real HA job, and only
once the seq-13 batch evidence run sets ``REAL_HA_SEQ13_LANES=enabled``.
"""
import ast
import asyncio
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
import configuration_delete_contract_acceptance as lane  # noqa: E402
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
from alarmo_inspection_contract_acceptance import slot_binding  # noqa: E402
from entity_metadata_contract_acceptance import load_receipt, receipt_bytes  # noqa: E402
import real_ha_contract_tests as harness  # noqa: E402
from test_alarmo_interval_observer import CORE, SLOT_VERSIONS  # noqa: E402
from tests.hamcp155_fixtures import ConfigurationDeleteServiceTestCase  # noqa: E402

CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)
REFUSED = (AssertionError, ValueError, KeyError, TypeError)
# Core names an entity from its alias, not its config ID (M1): never derivable.
ENTITY = {"automation": "automation.hamcp155_hamcp155_auto_review",
          "script": "script.hamcp155_hamcp155_script_review"}


def absent(resource_type):
    return {"config_status": 404, "registry_entry": None, "registry_entity_id": None,
            "entity_id": ENTITY[resource_type], "state_status": 404}


def synthetic_receipt(core_version=CORE):
    binding = slot_binding(core_version)
    reference = lane.source_profile()

    def deleted(resource_type):
        return {"result": "PASS", "task_state": "succeeded_verified", "dispatch_counts": [1],
                "write_methods": ["DELETE"], "ha_writes": 1, "write_routes_bound": True, "config_checks": 0,
                "entity_id": ENTITY[resource_type], "protected": False, "risk_level": "medium",
                "preflight_present": True, "readback": absent(resource_type),
                "codes": ["exact_readback_verified"]}

    def recreated(resource_type):
        return {"result": "PASS", "rollback_status": "rollback_plan_created",
                "rollback_dispatched_on_create": False, "operations_excluded": [],
                "task_state": "succeeded_verified", "dispatch_counts": [1], "write_methods": ["POST"],
                "ha_writes": 1, "write_routes_bound": True, "config_checks": lane.RECREATE_CONFIG_CHECKS,
                "config_matches_pre_image": True, "entity_id": ENTITY[resource_type],
                "registry_entry_id_restored": True, "entity_id_restored": True, "state_present": True}
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
            "delete_automation": deleted("automation"), "delete_script": deleted("script"),
            "recreate_automation": recreated("automation"), "recreate_script": recreated("script"),
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

    def test_f3_entry_admission_covers_exactly_the_dormant_profiles(self):
        """M3: every dormant profile is checked before F3 persists anything."""
        from ha_mcp_engineering.ha_core_readmission.routes import (
            F3_ENTRY_ADMISSION_PROFILES, f3_entry_admission_profiles)
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
        self.assertEqual(F3_ENTRY_ADMISSION_PROFILES, lane.DORMANT)
        # Derived, so a merged dormant profile (e.g. #303's labels) must join the entry check.
        compiled = {profile.capability_id for profile in CORE_RUNTIME_CAPABILITY_PROFILES}
        shipped = {item["capability_id"] for item in lane.shipped_references()}
        self.assertEqual(F3_ENTRY_ADMISSION_PROFILES, frozenset(compiled - shipped))
        expected = {
            "delete_automation_configuration": (lane.CAPABILITY,),
            "delete_script_configuration": (lane.CAPABILITY,),
            "update_entity_registry_availability_configuration": ("core.entity_registry_availability_operation",),
            "update_entity_assistant_exposure_configuration": ("core.entity_assistant_exposure_operation",),
            # Signed profiles keep their existing dispatch-time checks.
            "update_automation_configuration": (),
            "create_script_configuration": (),
            "update_input_boolean_configuration": (),
            "delete_automation": (),
        }
        for identity, profiles in expected.items():
            with self.subTest(identity=identity):
                self.assertEqual(f3_entry_admission_profiles([identity]), profiles)
        self.assertEqual(f3_entry_admission_profiles(
            ["delete_script_configuration", "update_entity_assistant_exposure_configuration",
             "delete_automation_configuration"]),
            tuple(sorted({lane.CAPABILITY, "core.entity_assistant_exposure_operation"})))

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
            for key in ("state_status", "entity_id", "registry_entity_id"):
                value = synthetic_receipt()
                del value["scenarios"]["delete_" + resource_type]["readback"][key]
                with self.subTest(resource_type=resource_type, missing=key), self.assertRaises(REFUSED):
                    self.verify(value)

    def test_absence_is_bound_to_the_captured_entity_identity(self):
        """M1: absence at another, guessed or mistyped entity ID is no evidence."""
        other = {"automation": "automation.hamcp155_auto_review", "script": "script.hamcp155_script_review"}
        for resource_type in ("automation", "script"):
            name = "delete_" + resource_type
            cases = {
                "guessed_readback": lambda v: v["scenarios"][name]["readback"].update(
                    entity_id=other[resource_type]),
                "registry_still_names_it": lambda v: v["scenarios"][name]["readback"].update(
                    registry_entity_id=ENTITY[resource_type]),
                "captured_other_type": lambda v: v["scenarios"][name].update(
                    entity_id=ENTITY["script" if resource_type == "automation" else "automation"]),
                "captured_invalid": lambda v: v["scenarios"][name].update(entity_id="Not An Entity"),
                "captured_missing": lambda v: v["scenarios"][name].pop("entity_id"),
                "readback_extra_key": lambda v: v["scenarios"][name]["readback"].update(state=None),
                "recreate_other_identity": lambda v: v["scenarios"]["recreate_" + resource_type].update(
                    entity_id=other[resource_type]),
                "recreate_entity_moved": lambda v: v["scenarios"]["recreate_" + resource_type].update(
                    entity_id_restored=False),
            }
            for case, mutate in cases.items():
                value = synthetic_receipt()
                mutate(value)
                with self.subTest(resource_type=resource_type, case=case), self.assertRaises(REFUSED):
                    self.verify(value)

    def test_config_checks_are_an_exact_bounded_allowlist(self):
        """M2: exactly two config checks for a recreate, none for a delete."""
        for resource_type in ("automation", "script"):
            for name, count in (("delete_" + resource_type, 1),
                                ("recreate_" + resource_type, lane.RECREATE_CONFIG_CHECKS - 1),
                                ("recreate_" + resource_type, lane.RECREATE_CONFIG_CHECKS + 1),
                                ("recreate_" + resource_type, 0)):
                value = synthetic_receipt()
                value["scenarios"][name]["config_checks"] = count
                with self.subTest(name=name, config_checks=count), self.assertRaises(REFUSED):
                    self.verify(value)
            for name in ("delete_" + resource_type, "recreate_" + resource_type):
                for key, changed in (("write_routes_bound", False), ("config_checks", None)):
                    value = synthetic_receipt()
                    value["scenarios"][name][key] = changed
                    with self.subTest(name=name, key=key), self.assertRaises(REFUSED):
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


class ReadbackIdentityTests(unittest.IsolatedAsyncioTestCase):
    """M1: the lane reads state at Core's registered or captured entity ID only."""

    target = "hamcp155_auto_review"

    class Rest:
        def __init__(self, states):
            self.states = set(states)
            self.paths = []

        async def call(self, method, path):
            self.paths.append((method, str(path)))
            if str(path).startswith("/states/"):
                return (200, {}) if str(path)[len("/states/"):] in self.states else (404, None)
            return 404, None

    class Raw:
        def __init__(self, entries):
            self.entries = entries

        async def ok(self, payload):
            assert payload == {"type": lane.REGISTRY_LIST}
            return self.entries

    def entry(self, entity_id=ENTITY["automation"]):
        return {"platform": "automation", "unique_id": self.target, "id": "regreview", "entity_id": entity_id}

    async def test_registered_identity_is_read_and_reported(self):
        rest = self.Rest({ENTITY["automation"]})
        readback, _ = await lane.independent_readback(rest, self.Raw([self.entry()]), "automation", self.target)
        self.assertEqual(readback["entity_id"], ENTITY["automation"])
        self.assertEqual(readback["registry_entity_id"], ENTITY["automation"])
        self.assertEqual(readback["state_status"], 200)
        self.assertIn(("GET", "/states/" + ENTITY["automation"]), rest.paths)
        self.assertNotIn(("GET", "/states/automation." + self.target), rest.paths)

    async def test_captured_identity_proves_absence_after_the_delete(self):
        rest = self.Rest(set())
        readback, _ = await lane.independent_readback(
            rest, self.Raw([]), "automation", self.target, ENTITY["automation"])
        self.assertTrue(lane._absent(readback))
        self.assertEqual(rest.paths[-1], ("GET", "/states/" + ENTITY["automation"]))
        # The state lingering at the captured ID is not absence.
        readback, _ = await lane.independent_readback(
            self.Rest({ENTITY["automation"]}), self.Raw([]), "automation", self.target, ENTITY["automation"])
        self.assertFalse(lane._absent(readback))

    async def test_no_identity_or_an_invalid_one_refuses(self):
        cases = {
            "nothing_captured": (self.Raw([]), None),
            "registry_without_entity_id": (self.Raw([{**self.entry(), "entity_id": None}]), None),
            "registry_other_type": (self.Raw([self.entry("script.hamcp155_review")]), None),
            "captured_other_type": (self.Raw([]), "script.hamcp155_review"),
            "captured_invalid": (self.Raw([]), "automation.Not Valid"),
        }
        for name, (raw, captured) in cases.items():
            rest = self.Rest({ENTITY["automation"], "automation." + self.target})
            with self.subTest(name), self.assertRaises(ValueError):
                await lane.independent_readback(rest, raw, "automation", self.target, captured)
            self.assertFalse([path for path in rest.paths if path[1].startswith("/states/")], name)

    def test_absent_requires_the_exact_readback_shape(self):
        valid = absent("automation")
        self.assertTrue(lane._absent(valid))
        for key in sorted(valid):
            with self.subTest(missing=key):
                self.assertFalse(lane._absent({k: v for k, v in valid.items() if k != key}))
        self.assertFalse(lane._absent({"config_status": 404, "registry_entry": None, "state_status": 404}))
        self.assertFalse(lane._absent({**valid, "entity_id": None}))


class ObservedRestTests(unittest.IsolatedAsyncioTestCase):
    """M2: only the exact config check is allowlisted; any other non-GET is a write."""

    class Inner:
        def __init__(self):
            self.requests = []

        async def request(self, method, path, *args, **kwargs):
            self.requests.append((method, str(path)))
            return {}

    async def test_only_the_exact_config_check_is_set_apart(self):
        from ha_mcp_engineering.clients.paths import rest_path
        inner = self.Inner()
        observed = lane.ObservedRest(inner)
        route = lane.config_route("automation", "hamcp155_auto_review")
        await observed.request("POST", rest_path("/config/core/check_config"))
        await observed.request("GET", route)
        await observed.request("POST", route, {})
        await observed.request("POST", rest_path("/config/core/check_config"))
        self.assertEqual(observed.config_checks, 2)
        self.assertEqual(observed.writes, ["POST"])
        self.assertEqual(observed.write_routes, [str(route)])
        self.assertEqual(len(inner.requests), 4)

    async def test_every_other_non_get_request_is_a_write(self):
        inner = self.Inner()
        observed = lane.ObservedRest(inner)
        for method, path in (("DELETE", "/config/core/check_config"), ("PUT", "/config/core/check_config"),
                             ("post", "/config/core/check_config"), ("POST", "/config/core/check_config/"),
                             ("POST", "/template"), ("DELETE", "/config/script/config/hamcp155_x")):
            await observed.request(method, path)
        self.assertEqual(observed.config_checks, 0)
        self.assertEqual(len(observed.writes), 6)
        self.assertEqual(len(observed.write_routes), 6)


class ObservedRestExecutionTests(ConfigurationDeleteServiceTestCase):
    """M2 reproduction on the real service: a rollback recreate sends exactly two
    allowlisted config checks and one POST to its bound route; a delete, none."""

    async def test_delete_and_recreate_requests_are_observed_exactly(self):
        for resource_type in ("automation", "script"):
            target = "review_observed_" + resource_type
            with self.subTest(resource_type=resource_type):
                self.add(resource_type, target)
                observed = lane.ObservedRest(self.config_rest)
                self.resource_gateway.rest_client = observed
                route = str(lane.config_route(resource_type, target))
                created = await self.approved(resource_type, target)
                deleted = await self.apply(created)
                self.assertEqual(deleted["task_state"], "succeeded_verified")
                self.assertEqual((observed.writes, observed.write_routes, observed.config_checks),
                                 (["DELETE"], [route], 0))
                rolled = await self.service.rollback_change(created["plan_id"])
                recreate = self.service.get_plan(rolled["rollback_plan_id"])
                await self.approve(recreate)
                observed.writes.clear()
                observed.write_routes.clear()
                start = len(self.config_rest.requests)
                result = await self.apply(recreate)
                self.assertEqual(result["task_state"], "succeeded_verified")
                self.assertEqual((observed.writes, observed.write_routes, observed.config_checks),
                                 (["POST"], [route], lane.RECREATE_CONFIG_CHECKS))
                checks = [r for r in self.config_rest.requests[start:] if (r[0], str(r[1])) == lane.CHECK_CONFIG]
                self.assertEqual(len(checks), lane.RECREATE_CONFIG_CHECKS)


class WiringTests(unittest.TestCase):
    def test_lane_refuses_unless_exactly_enabled(self):
        for flag in (None, "", "Enabled", "true", "1", "dormant", " enabled"):
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

    def flag(self, value):
        environment = {k: v for k, v in os.environ.items() if k != lane.ENABLE_FLAG}
        if value is not None:
            environment[lane.ENABLE_FLAG] = value
        return patch.dict(os.environ, environment, clear=True)

    def harness_hook(self):
        """The real disposable-slot block, executed alone with synthetic lanes."""
        tree = ast.parse((ROOT / "scripts" / "real_ha_contract_tests.py").read_text())
        owner = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                     and n.name == "_run_core_2026_9_child_contract")
        block = next(n for n in ast.walk(owner) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "_alarmo_slot_required(EXPECTED_HA_VERSION)")
        wrapper = ast.AsyncFunctionDef(name="invoke", args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[block], decorator_list=[])
        return ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))

    def run_hook(self, flag, lane_result):
        """Returns (retained receipt, lane mock); raises what the hook raises."""
        modules = {}
        for name in ("alarmo_inspection_contract_acceptance", "automation_baseline_contract_acceptance",
                     "entity_metadata_contract_acceptance", "configuration_delete_contract_acceptance"):
            modules[name] = ModuleType(name)
            modules[name].run_disposable = AsyncMock(return_value={"result": "PASS"})
        modules["entity_metadata_contract_acceptance"].load_receipt = load_receipt
        delete_lane = modules["configuration_delete_contract_acceptance"].run_disposable
        delete_lane.return_value = lane_result
        configured = object()
        context = dict(EXPECTED_HA_VERSION=CORE, configured=configured, core_runtime=object(),
                       _alarmo_slot_required=lambda version: True,
                       _environment_path=lambda name: Path("synthetic.json"), os=os, Path=Path, json=json)
        exec(compile(self.harness_hook(), "bounded-ci-hook", "exec"), context)
        with tempfile.TemporaryDirectory() as directory, self.flag(flag), \
                patch.dict(sys.modules, modules):
            retained = Path(directory) / "combined.json"
            retained.write_text(json.dumps({"result": "PASS"}))
            with patch.dict(os.environ, {"REAL_HA_ALARMO_RESULT": str(retained),
                                         "HA_CONTRACT_IMAGE": "image@sha256:" + "a" * 64}):
                try:
                    asyncio.run(context["invoke"]())
                finally:
                    self.retained = json.loads(retained.read_text())
        if delete_lane.await_count:
            delete_lane.assert_awaited_once_with(configured, expected_image="image@sha256:" + "a" * 64,
                                                 core_version=CORE)
        return self.retained, delete_lane

    def test_dormant_harness_never_invokes_the_lane(self):
        for flag in (None, "", "Enabled", "true", "1", "dormant", " enabled"):
            with self.subTest(flag=flag):
                retained, delete_lane = self.run_hook(flag, {"result": "PASS"})
                delete_lane.assert_not_called()
                self.assertNotIn("seq13_configuration_delete", retained)
                self.assertEqual(retained["entity_metadata"], {"result": "PASS"})

    def test_enabled_harness_invokes_the_lane_and_retains_its_receipt(self):
        receipt = {"result": "PASS", "lane": "configuration_delete"}
        retained, delete_lane = self.run_hook("enabled", receipt)
        self.assertEqual(delete_lane.await_count, 1)
        self.assertEqual(retained["seq13_configuration_delete"], receipt)
        self.assertEqual(retained["entity_metadata"], {"result": "PASS"})
        for result in ({"result": "FAIL"}, {"result": "NOT_RUN"}, {}):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.run_hook("enabled", result)
            # A failed lane retains nothing; the required receipt is untouched.
            self.assertEqual(self.retained, {"result": "PASS"})

    def test_harness_runs_the_lane_after_the_existing_scenarios(self):
        source = inspect.getsource(harness._run_core_2026_9_child_contract)
        entity = source.index("entity_metadata_result = await run_entity_metadata(")
        call = source.index('if os.environ.get("REAL_HA_SEQ13_LANES") == "enabled":')
        retained = source.index("retained.update(seq13_results)")
        written = source.index("receipt.write_text(", retained)
        self.assertTrue(entity < call < retained < written)
        self.assertEqual(source.count("configuration_delete_contract_acceptance"), 1)
        self.assertEqual(inspect.getsource(harness).count("configuration_delete_contract_acceptance"), 1)

    def test_cleanup_verification_requires_the_receipt_when_enabled(self):
        cleanup = inspect.getsource(alarmo.verify_disposable_cleanup)
        self.assertIn("verify_seq13_configuration_delete(value, configuration_delete_receipt,", cleanup)
        self.assertIn('os.environ.get("REAL_HA_CONFIGURATION_DELETE_RESULT")', inspect.getsource(alarmo.main))
        value = synthetic_receipt()
        canonical = receipt_bytes(value)
        with tempfile.TemporaryDirectory() as directory:
            standalone = Path(directory) / "seq13-configuration-delete.json"
            standalone.write_bytes(canonical)

            def verify(retained, path=standalone):
                return alarmo.verify_seq13_configuration_delete(
                    retained, path, core_version=CORE, support_slot=CORE_SLOT)

            with self.flag("enabled"):
                summary = verify({"seq13_configuration_delete": load_receipt(canonical)})
                self.assertEqual(summary["required_seq13_configuration_delete"]["receipt_sha256"],
                                 hashlib.sha256(canonical).hexdigest())
                self.assertEqual(summary["required_seq13_configuration_delete"]["result"], "PASS")
                failed = {**copy.deepcopy(value), "result": "FAIL"}
                partial = copy.deepcopy(value)
                partial["scenarios"].pop("recreate_script")
                guessed = copy.deepcopy(value)
                guessed["scenarios"]["delete_automation"]["readback"]["entity_id"] = "automation.hamcp155_x"
                cases = {
                    "missing_retained": ({}, standalone),
                    "missing_standalone": ({"seq13_configuration_delete": value}, None),
                    "absent_standalone": ({"seq13_configuration_delete": value}, Path(directory) / "none.json"),
                    "fail": ({"seq13_configuration_delete": failed}, standalone),
                    "partial": ({"seq13_configuration_delete": partial}, standalone),
                    "guessed_identity": ({"seq13_configuration_delete": guessed}, standalone),
                }
                for name, (retained, path) in cases.items():
                    with self.subTest(name), self.assertRaises(REFUSED):
                        verify(retained, path)
                standalone.write_bytes(canonical + b" ")
                with self.subTest("standalone_differs"), self.assertRaises(ValueError):
                    verify({"seq13_configuration_delete": value})
            for flag in (None, "dormant", "Enabled"):
                with self.subTest(flag=flag), self.flag(flag):
                    self.assertEqual(verify({}), {})
                    with self.assertRaises(ValueError):
                        verify({"seq13_configuration_delete": value})

    def test_shipped_lanes_never_sign_the_delete_profile(self):
        for name in ("alarmo_inspection_contract_acceptance.py", "automation_baseline_contract_acceptance.py",
                     "entity_metadata_contract_acceptance.py"):
            with self.subTest(name):
                self.assertIn("core.configuration_delete_operation", (ROOT / "scripts" / name).read_text())


class CleanupTests(unittest.TestCase):
    # The registered identities differ from "<type>.<config id>" (M1).
    IDENTITY = {("automation", "a"): "automation.hamcp155_alpha", ("script", "b"): "script.hamcp155_beta"}

    class Rest:
        def __init__(self, identity, lingering_state=False):
            self.identity = identity
            self.configs = {key: {} for key in identity}
            self.states = set(identity.values())
            self.lingering_state = lingering_state
            self.deleted = []

        async def call(self, method, path, body=None):
            parts = str(path).strip("/").split("/")
            if parts[0] == "states":
                return (200, {}) if parts[1] in self.states else (404, None)
            key = (parts[1], parts[3])
            if method == "DELETE":
                self.deleted.append(key)
                self.configs.pop(key)
                if not self.lingering_state:
                    self.states.discard(self.identity[key])
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
            items = [{"platform": t, "unique_id": i, "id": t + i, "entity_id": self.rest.identity[(t, i)]}
                     for t, i in self.rest.configs]
            if self.lingering:
                items.append({"platform": "script", "unique_id": "b", "id": "stale",
                              "entity_id": "script.hamcp155_beta"})
            return items

    def owned(self, **missing):
        return [{"resource_type": t, "target_id": i,
                 "entity_id": None if missing.get(t) else self.IDENTITY[(t, i)]} for t, i in self.IDENTITY]

    def cleanup(self, rest, raw, owned, attempts=1):
        return asyncio.run(lane.cleanup_fixtures(rest, raw, owned, attempts=attempts, delay=0))

    def test_cleanup_reads_back_every_absence(self):
        rest = self.Rest(self.IDENTITY)
        absence = self.cleanup(rest, self.Raw(rest), self.owned())
        self.assertEqual(absence, dict.fromkeys(lane.FIXTURE_ABSENCE, True))
        self.assertEqual(rest.deleted, list(self.IDENTITY))

        # An acknowledged delete whose registry entry lingers is not absence.
        rest = self.Rest(self.IDENTITY)
        absence = self.cleanup(rest, self.Raw(rest, lingering=True), self.owned(), attempts=2)
        self.assertIs(absence["registry_entries"], False)
        self.assertIsNone(self.cleanup(rest, self.Raw(rest), []))

    def test_cleanup_reads_state_at_the_captured_identity(self):
        # A state lingering at the captured ID is not absence, although the
        # guessed "<type>.<config id>" ID would read 404.
        rest = self.Rest(self.IDENTITY, lingering_state=True)
        absence = self.cleanup(rest, self.Raw(rest), self.owned(), attempts=2)
        self.assertIs(absence["states"], False)
        self.assertIs(absence["automation_config"], True)

    def test_uncaptured_identity_fails_closed(self):
        rest = self.Rest(self.IDENTITY)
        absence = self.cleanup(rest, self.Raw(rest), self.owned(script=True))
        self.assertIs(absence["states"], False)
        self.assertIs(absence["script_config"], True)
        self.assertIs(absence["registry_entries"], True)
        self.assertEqual(rest.deleted, list(self.IDENTITY))


if __name__ == "__main__":
    unittest.main()
