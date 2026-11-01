"""Offline refusal controls for the five exact-Core seq-15 evidence lanes.

These receipts are synthetic; they never substitute for disposable Core CI.
"""
import copy
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
import seq15_contract_acceptance as seq15  # noqa: E402
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
import real_ha_contract_tests  # noqa: E402
from entity_metadata_contract_acceptance import RawRefusal, load_receipt, receipt_bytes  # noqa: E402
from test_alarmo_interval_observer import NOT_SLOTS, NOT_STRINGS, SLOT_VERSIONS  # noqa: E402

CORE = seq15.TARGET_CORE
CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)
OTHER_CORES = [version for version in SLOT_VERSIONS.values() if version not in seq15.TARGET_CORES]
REFUSED = (AssertionError, ValueError, KeyError, TypeError)
FINGERPRINTS = {
    "scene_configuration": ["sha256:73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb"],
    "helper_options": ["sha256:ce2765dc5caf616ae199fdccf64ce6ec68e1386bb0949099d1896bcb50dd98d8"],
    "input_select_option": ["sha256:e2240555fb35042e47e7ee7afb7f5552977d019a27058fb2c2c5802674fee38d"],
    "script_traces": ["sha256:749904fe40b13db4289301d24b958836fc72c810829611729d8c26080f06ca03"],
    "dashboard_metadata": ["sha256:62cd6c1453b0bc0b52c6e5f9775d1897294f31b62792dd9674a61b9e8a54cfaa"],
}


def governed(*types, expected, readback=None):
    return {"result": "PASS", "task_state": "succeeded_verified", "dispatch_counts": [1] * len(types),
            "diagnostic_codes": [], "ha_writes": len(types), "write_types": list(types),
            "expected": copy.deepcopy(expected), "readback": copy.deepcopy(expected if readback is None else readback)}


def refused(reason_code, *, error_code="validation_failed", commands=2):
    return {"result": "PASS", "error_code": error_code, "reason_code": reason_code,
            "plan_created": False, "ha_writes": 0, "ha_commands": commands}


def stale():
    return {"result": "PASS", "task_state": "failed_pre_dispatch", "dispatch_counts": [0],
            "diagnostic_codes": [seq15.STALE], "ha_writes": 0, "write_types": [], "drift_retained": True}


def helper_scenarios():
    def edited(baseline, change):
        rollback = {**governed(seq15.OPTIONS_SUBMIT, expected=baseline), "status": "rollback_plan_created",
                    "defaults_not_restored": []}
        return {**governed(seq15.OPTIONS_SUBMIT, expected={**baseline, **change}), "baseline": baseline,
                "rollback": rollback}
    return {
        "filter": edited({"window_size": 1, "time_constant": 7, "precision": 2}, {"time_constant": 20}),
        "statistics": edited({"sampling_size": 20, "precision": 2}, {"sampling_size": 30}),
        "template_sensor": edited({"state": "{{ 21 }}", "unit_of_measurement": "W"}, {"unit_of_measurement": "kW"}),
        "template_binary_sensor": edited({"state": "{{ false }}"}, {"device_class": "door"}),
        "suggested_value": {"result": "PASS", "created": 7, "disclosed_baseline": 7, "raw_baseline": 7},
        "field_not_editable": refused("helper_options_field_not_editable", error_code="invalid_request", commands=0),
        "validation_would_fail": refused("helper_options_validation_would_fail"),
        "stale_state": stale(),
        "concurrent_flow": {**governed(seq15.OPTIONS_SUBMIT, expected={"time_constant": 12}),
                            "od7_overwrite_observed": True,
                            "rollback_reason": "helper_options_rollback_baseline_drift",
                            "disclosed_window": "accepted_od_7"},
    }


def input_select_scenarios():
    return {
        "flip": governed(seq15.SELECT_OPTION, expected="Away"),
        "rollback": {**governed(seq15.SELECT_OPTION, expected="Home"), "status": "rollback_plan_created",
                     "prior": "Home"},
        "already_desired": {"result": "PASS", "status": "no_change", "outcome": "already_desired",
                            "plan_created": False, "ha_writes": 0},
        "option_not_available": refused("option_not_available"),
        "options_changed": stale(),
    }


def script_trace_scenarios():
    return {
        "list": {"result": "PASS", "traces": 1, "run_listed": True, "sanitization_failed_closed": False},
        "get": {"result": "PASS", "run_matches": True, "secret_absent": True, "redacted": True,
                "long_value_bounded": True},
        "not_found": {"result": "PASS", "reason": "trace_not_found", "error_code": "resource_not_found",
                      "ha_commands": 1},
        "invalid_arguments": {"result": "PASS", "reason": "invalid_arguments", "error_code": "invalid_request",
                              "ha_commands": 0},
    }


DASHBOARD_START = {"title": "Seq15 Board", "icon": "mdi:view-dashboard", "show_in_sidebar": True,
                   "require_admin": False}
DASHBOARD_CHANGES = (("title", {"title": seq15.DASHBOARD_TITLE}), ("icon", {"icon": "mdi:map-marker"}),
                     ("icon_clear", {"icon": None}), ("show_in_sidebar", {"show_in_sidebar": False}),
                     ("require_admin", {"require_admin": True}),
                     ("all_fields", {"title": "Seq15 Floor", "icon": "mdi:floor-plan", "show_in_sidebar": True,
                                     "require_admin": False}))


def dashboard_scenarios():
    def update(baseline, expected, fields):
        return {**governed(seq15.DASHBOARD_UPDATE, expected=expected), "baseline": dict(baseline),
                "sent_fields": [sorted(fields)], "dashboard_ids_exact": True, "requested_fields": sorted(fields),
                "icon_key_absent": expected["icon"] is None}
    scenarios, current = {}, dict(DASHBOARD_START)
    for name, change in DASHBOARD_CHANGES:
        prior = current
        scenarios[name], current = update(current, {**current, **change}, change), {**current, **change}
    scenarios["rollback"] = {**update(current, prior, seq15.DASHBOARD_FIELDS), "status": "rollback_plan_created",
                             "prior": dict(prior)}
    scenarios["rollback"].pop("icon_key_absent")
    scenarios["unknown_id"] = {"result": "PASS", "error_code": "not_found", "inventory_unchanged": True}
    scenarios["immutable_fields"] = {"result": "PASS", "errors": dict.fromkeys(("url_path", "mode"), "invalid_format"), "row_unchanged": True}
    return {**scenarios,
            "no_change": refused(seq15.DASHBOARD_NO_CHANGE, error_code="invalid_request", commands=3),
            "default_dashboard": refused(seq15.DASHBOARD_DEFAULT_REFUSED, error_code="invalid_request", commands=0),
            "content_unchanged": {"result": "PASS", "raw_config_unchanged": True, "fingerprints_match": True,
                                  "fingerprint_reads": 21},
            "stale_state": stale(), "content_drift": stale()}


def scene_scenarios():
    from seq15_scene_contract import EDITS, SCOPE
    rows = {name: {**governed(seq15.SCENE_POST, expected={"id": "seq15_12345678", "name": "Exact "}),
                   "registry_identity_unchanged": True, "member_state_unchanged": True,
                   "full_entry_preserved": True, "verification_scope": SCOPE, "loaded_state": "not_verified"}
            for name in (*EDITS, "rollback")}
    rows["rollback"]["status"] = "rollback_plan_created"
    for name, reason in {"no_change": "no_change", "not_found": "target_not_found",
                         "unknown_member": "unknown_entities", "target_mismatch": "target_mismatch"}.items():
        rows[name] = refused("scene_configuration_" + reason)
    rows["stale_state"] = stale()
    rows["stored_mismatch"] = {"result": "PASS", "task_state": "failed_post_dispatch", "dispatch_counts": [1],
                               "ha_writes": 1, "write_types": [seq15.SCENE_POST], "diagnostic_codes": ["exact_readback_not_verified"],
                               "mismatch_retained": True, "member_state_unchanged": True}
    rows["id_assignment"] = {"result": "PASS", "assigned_id_valid": True, "unrequested_keys_preserved": True}
    return rows


SCENARIOS = {"helper_options": helper_scenarios, "input_select_option": input_select_scenarios,
             "script_traces": script_trace_scenarios, "dashboard_metadata": dashboard_scenarios,
             "scene_configuration": scene_scenarios}


def complete_receipt(lane_key, core_version=CORE):
    import hashlib
    from ha_mcp_engineering.signed_registry import canonical_json
    lane = seq15.LANES[lane_key]
    binding = alarmo.slot_binding(core_version)
    return {
        "result": "PASS", "lane": lane_key, "core": core_version, "support_slot": binding["slot"],
        "core_image": binding["image"], "image_index_digest": binding["image_index_digest"],
        "image_receipt_sha256": "1" * 64, "engineering_source": seq15.engineering_source(),
        "added_capabilities": seq15.source_profiles(lane),
        "predecessor_capabilities_sha256": hashlib.sha256(
            canonical_json(seq15.predecessor_references())).hexdigest(),
        "authority_transition": list(lane["authority_transition"]),
        "authority_instances": "separate_ephemeral_registries", "production_authority": False,
        "provider_identity": "synthetic_disposable_lane",
        "batch_health": {"compatible_count": 31, "withheld_count": 0},
        "predecessor_admission": {"route_acquired": False, "route_available": False,
                                  "service_reason_code": lane["predecessor_reason"],
                                  "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True},
        "engineering_command_types": sorted(lane["required_commands"]),
        "scenarios": SCENARIOS[lane_key](), "fixture_cleanup": True,
        "fixture_absence": dict.fromkeys(seq15.FIXTURE_ABSENCE, True)}


class InputSelectDriftFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_lane_drift_supplies_cores_required_name_and_preserves_state(self):
        current = {"state": "Home", "attributes": {"options": ["Home", "Away", "Night"]}}
        fixture, updates = {}, []

        async def ok(payload):
            if payload["type"] == "input_select/create":
                fixture.update(id="seq15_test", name=payload["name"], options=payload["options"])
                return dict(fixture)
            self.assertEqual(payload["type"], "input_select/update")
            # Exact Core STORAGE_FIELDS requires name AND options on update.
            self.assertEqual(payload["name"], fixture["name"])
            self.assertEqual(payload["input_select_id"], fixture["id"])
            self.assertEqual(set(payload), {"type", "input_select_id", "name", "options"})
            updates.append(payload)
            current["attributes"]["options"] = payload["options"]

        async def status(*_args):
            return 200, copy.deepcopy(current)

        async def plan(*_args):
            return {"status": "no_change", "outcome": "already_desired", "plan_created": False}

        async def refused_plan(*_args):
            return refused("option_not_available")

        async def apply_created(_created):
            current["state"] = "Home"
            return governed(seq15.SELECT_OPTION, expected="Home")

        async def rollback(_plan_id):
            return {"status": "rollback_plan_created"}, {"plan_id": "reverse"}

        async def apply(_runtime, arguments, *, observed=None, between=None):
            if between:
                await between()
                return stale()
            observed["plan"] = {"plan_id": "forward"}
            current["state"] = arguments["option"]
            return governed(seq15.SELECT_OPTION, expected=arguments["option"])

        result = await seq15._input_select_scenarios({
            "stage": ["test"], "owned": {}, "raw": SimpleNamespace(ok=ok, status=status),
            "runtime": None, "log": SimpleNamespace(writes=[]), "plan": plan,
            "refused_plan": refused_plan, "governed": apply, "rollback": rollback,
            "apply_created": apply_created})
        self.assertEqual(len(updates), 1)
        for name, value in result.items():
            seq15.CHECKS["input_select_option"](name, value)


class LaneDefinitionTests(unittest.TestCase):
    def test_lanes_bind_the_dormant_seq15_profiles_from_source(self):
        self.assertEqual(sorted(seq15.LANES),
                         ["dashboard_metadata", "helper_options", "input_select_option", "scene_configuration", "script_traces"])
        fingerprints = {key: [r["contract_fingerprint"] for r in seq15.source_profiles(lane)]
                        for key, lane in seq15.LANES.items()}
        self.assertEqual(fingerprints, FINGERPRINTS)
        for key, lane in seq15.LANES.items():
            with self.subTest(lane=key):
                self.assertEqual(lane["authority_transition"], [26, 27])
        self.assertEqual({c for lane in seq15.LANES.values() for c in lane["capabilities"]}, seq15.SEQ15_DORMANT)
        self.assertEqual(seq15.LANES["helper_options"]["tool"], "create_helper_options_plan")
        self.assertEqual(seq15.LANES["input_select_option"]["tool"], "create_input_select_option_plan")
        self.assertEqual(seq15.LANES["script_traces"]["tool"], "list_script_traces")
        self.assertEqual(seq15.LANES["dashboard_metadata"]["tool"], "create_dashboard_metadata_plan")
        self.assertEqual(seq15.LANES["dashboard_metadata"]["adapters"],
                         ("compiled-core-dashboard-metadata-operation-v1",))
        self.assertEqual(seq15.LANES["dashboard_metadata"]["output_env"], "REAL_HA_DASHBOARD_METADATA_RESULT")

    def test_adapter_drift_is_refused(self):
        lane = {**seq15.LANES["helper_options"], "adapters": ("compiled-core-other-v1",)}
        with self.assertRaisesRegex(ValueError, "adapter differs"):
            seq15.source_profiles(lane)

    def test_evidence_covers_both_exact_releases(self):
        self.assertEqual(seq15.TARGET_CORES, {"2026.10.1", "2026.10.3"})
        self.assertEqual(SLOT_VERSIONS["candidate"], "2026.10.3")
        self.assertEqual(seq15.TARGET_CORE, "2026.10.3")
        self.assertNotIn(seq15.TARGET_CORE, (SLOT_VERSIONS["current"], SLOT_VERSIONS["rollback"]))

    def test_predecessor_equals_the_signed_seq14_entry_without_any_seq15_profile(self):
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
        refs = seq15.predecessor_references()
        ids = {r["capability_id"] for r in refs}
        self.assertEqual(len(refs), 26)
        self.assertFalse(ids & (seq15.SEQ15_DORMANT | seq15.SEQ16_DORMANT))
        self.assertEqual(ids | seq15.SEQ15_DORMANT | seq15.SEQ16_DORMANT,
                         {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        # HAMCP-269 includes the scene profile in this five-capability batch.
        self.assertEqual(seq15.SEQ16_DORMANT, set())
        registry = json.loads(seq15.SIGNED_REGISTRY.read_text())
        (predecessor,) = [e for e in registry["envelopes"] if e["sequence"] == 14]
        (signed,) = [e for e in predecessor["entries"] if e["version"] == seq15.TARGET_CORE]
        self.assertEqual(sorted(c["capability_id"] for c in signed["capabilities"]), sorted(ids))

    def test_predecessor_refuses_when_the_signed_entry_differs(self):
        registry = json.loads(seq15.SIGNED_REGISTRY.read_text())

        def entry(value):
            (predecessor,) = [e for e in value["envelopes"] if e["sequence"] == 14]
            (found,) = [e for e in predecessor["entries"] if e["version"] == seq15.TARGET_CORE]
            return found
        mutations = [
            lambda v: entry(v)["capabilities"].pop(),
            lambda v: entry(v)["capabilities"][0].update(contract_fingerprint="sha256:" + "0" * 64),
            lambda v: entry(v)["capabilities"].append(
                {**entry(v)["capabilities"][0], "capability_id": seq15.HELPER_OPTIONS}),
            lambda v: entry(v).update(version="2026.10.2"),
        ]
        for index, mutate in enumerate(mutations):
            value = copy.deepcopy(registry)
            mutate(value)
            with tempfile.TemporaryDirectory() as directory, self.subTest(mutation=index):
                path = Path(directory) / "registry.json"
                path.write_text(json.dumps(value))
                with patch.object(seq15, "SIGNED_REGISTRY", path), self.assertRaises(REFUSED):
                    seq15.predecessor_references()

    def test_no_lane_reviews_a_never_sent_command_or_a_data_deletion(self):
        for key, lane in seq15.LANES.items():
            with self.subTest(lane=key):
                reviewed = lane["reviewed_commands"]
                self.assertLessEqual(lane["required_commands"], reviewed)
                self.assertFalse({c for c in reviewed if c in lane["never_sent"]
                                  or c.split(":")[0] in lane["never_sent"]})
                self.assertFalse({c for c in reviewed if c.endswith("/delete")})
                # Aborting an options flow is the only DELETE: it discards a form, never data.
                self.assertEqual({c for c in reviewed if c.startswith("DELETE ")},
                                 {seq15.OPTIONS_ABORT} if key == "helper_options" else set())
                self.assertEqual(reviewed & seq15.WRITE_REQUESTS,
                                 {"helper_options": {seq15.OPTIONS_SUBMIT},
                                  "input_select_option": {seq15.SELECT_OPTION},
                                  "script_traces": set(),
                                  "dashboard_metadata": {seq15.DASHBOARD_UPDATE},
                                  "scene_configuration": {seq15.SCENE_POST}}[key])
        self.assertIn("call_service", seq15.LANES["helper_options"]["never_sent"])
        self.assertIn("POST /config/config_entries/flow", seq15.LANES["helper_options"]["never_sent"])
        self.assertIn("call_service:input_select.set_options", seq15.LANES["input_select_option"]["never_sent"])
        self.assertIn("input_select/update", seq15.LANES["input_select_option"]["never_sent"])
        self.assertIn("trace/debug/continue", seq15.LANES["script_traces"]["never_sent"])
        dashboard = seq15.LANES["dashboard_metadata"]
        # One list, the content-fingerprint read and the one update; no create, save, delete or resource command.
        self.assertEqual(dashboard["reviewed_commands"], {"lovelace/dashboards/list", "lovelace/config",
                                                          "lovelace/dashboards/update"})
        self.assertEqual(dashboard["required_commands"], dashboard["reviewed_commands"])
        for command in ("lovelace/config/save", "lovelace/config/delete", "lovelace/dashboards/create",
                        "lovelace/dashboards/delete", "lovelace/resources/update", "frontend/set_user_data"):
            self.assertIn(command, dashboard["never_sent"])
        self.assertEqual(dashboard["predecessor_reason"], seq15.PENDING)


class RouteNamingTests(unittest.TestCase):
    def test_rest_requests_are_named_by_template_without_ids(self):
        flow = "0123456789abcdef0123456789abcdef"
        cases = {
            ("GET", "/states/input_select.house_mode"): "GET /states/{}",
            ("POST", "/config/config_entries/options/flow"): seq15.OPTIONS_OPEN,
            ("POST", "/config/config_entries/options/flow/" + flow): seq15.OPTIONS_SUBMIT,
            ("DELETE", "/config/config_entries/options/flow/" + flow): seq15.OPTIONS_ABORT,
            ("DELETE", "/config/config_entries/entry/01JSEQ15ENTRY"): "DELETE /config/config_entries/entry/{}",
            ("POST", "/config/config_entries/entry/01JSEQ15ENTRY/reload"):
                "POST /config/config_entries/entry/{}/reload",
            ("POST", "/config/config_entries/flow"): "POST /config/config_entries/flow",
        }
        for (method, path), name in cases.items():
            with self.subTest(path=path):
                self.assertEqual(seq15.observed_rest_route(method, path), name)
                self.assertNotIn("01JSEQ15ENTRY", name)
                self.assertNotIn(flow, name)

    def test_websocket_service_calls_are_named_by_domain_and_service(self):
        self.assertEqual(seq15.observed_ws_type({"type": "call_service", "domain": "input_select",
                                                 "service": "select_option"}), seq15.SELECT_OPTION)
        self.assertEqual(seq15.observed_ws_type({"type": "trace/get"}), seq15.TRACE_GET)

    def test_suggested_values_include_nested_sections_only_when_suggested(self):
        form = {"data_schema": [
            {"name": "window_size", "description": {"suggested_value": 1}},
            {"name": "precision"},
            {"name": "advanced_options", "schema": [
                {"name": "time_constant", "description": {"suggested_value": 7}},
                {"name": "unused", "description": {}}]},
            {"name": "empty_section", "schema": [{"name": "nothing"}]}]}
        self.assertEqual(seq15.suggested_values(form), {"window_size": 1, "advanced_options": {"time_constant": 7}})
        self.assertEqual(seq15.suggested_values({}), {})


class ReceiptTests(unittest.TestCase):
    def verify(self, lane_key, value, core_version=CORE, support_slot=CORE_SLOT):
        return seq15.verify_result(lane_key, value, core_version=core_version, support_slot=support_slot)

    def test_complete_receipt_verifies_for_every_lane(self):
        for lane_key, lane in seq15.LANES.items():
            with self.subTest(lane=lane_key):
                self.assertEqual(self.verify(lane_key, complete_receipt(lane_key)), {
                    "result": "PASS", "lane": lane_key, "core": CORE, "support_slot": CORE_SLOT,
                    "contract_fingerprints": FINGERPRINTS[lane_key], "scenarios": len(lane["scenarios"]),
                    "authority_transition": [26, 27], "fixture_cleanup": True})

    def test_serialized_receipt_round_trips_canonically(self):
        for lane_key in seq15.LANES:
            with self.subTest(lane=lane_key):
                raw = receipt_bytes(complete_receipt(lane_key))
                self.assertEqual(receipt_bytes(load_receipt(raw)), raw)
                self.verify(lane_key, load_receipt(raw))

    def test_refuses_another_lanes_receipt(self):
        for lane_key in seq15.LANES:
            for other in seq15.LANES:
                if other != lane_key:
                    with self.subTest(lane=lane_key, other=other), self.assertRaises(REFUSED):
                        self.verify(lane_key, complete_receipt(other))
        with self.assertRaises(KeyError):
            self.verify("label_registry", complete_receipt("helper_options"))

    def test_refuses_every_other_core_slot_or_missing_binding_arguments(self):
        for lane_key in seq15.LANES:
            value = complete_receipt(lane_key)
            for other in OTHER_CORES + list(NOT_SLOTS):
                with self.subTest(lane=lane_key, caller_core=other), self.assertRaises(REFUSED):
                    self.verify(lane_key, copy.deepcopy(value), core_version=other)
            for other in OTHER_CORES:
                # A receipt from an unextended slot is refused even when the caller names that slot.
                slot = next(s for s, v in SLOT_VERSIONS.items() if v == other)
                with self.subTest(lane=lane_key, slot=slot), self.assertRaises(REFUSED):
                    self.verify(lane_key, complete_receipt(lane_key, other), core_version=other, support_slot=slot)
            for wrong in ["current", "rollback", *NOT_STRINGS]:
                with self.subTest(lane=lane_key, caller_slot=wrong), self.assertRaises(REFUSED):
                    self.verify(lane_key, copy.deepcopy(value), support_slot=wrong)
            with self.assertRaises(TypeError):
                seq15.verify_result(lane_key, copy.deepcopy(value))

    def test_refuses_wrong_image_fingerprint_source_authority_or_cleanup(self):
        other = alarmo.slot_binding(OTHER_CORES[0])
        zero = "sha256:" + "0" * 64
        mutations = [
            lambda v: v.update(result="FAIL"),
            lambda v: v.update(core=OTHER_CORES[0]),
            lambda v: v.update(support_slot="current"),
            lambda v: v.update(core_image=other["image"]),
            lambda v: v.update(image_index_digest=other["image_index_digest"]),
            lambda v: v.update(image_receipt_sha256="1" * 63),
            lambda v: v["added_capabilities"][0].update(contract_fingerprint=zero),
            lambda v: v["added_capabilities"][0].update(adapter_id="compiled-core-other-v1"),
            lambda v: v["added_capabilities"][0].update(
                profile_version=v["added_capabilities"][0]["profile_version"] + 1),
            lambda v: v["added_capabilities"].pop(),
            lambda v: v["added_capabilities"].append(dict(v["added_capabilities"][0])),
            lambda v: v["engineering_source"].update(runtime_tree="0" * 40),
            lambda v: v["engineering_source"].update(commit="0" * 39),
            lambda v: v.update(authority_transition=[25, 26]),
            lambda v: v.update(authority_transition=[26, 26]),
            lambda v: v.update(authority_transition=[26, 29]),
            lambda v: v.update(authority_instances="shared_registry"),
            lambda v: v.update(production_authority=True),
            lambda v: v.update(fixture_cleanup=False),
            *[(lambda name: lambda v: v["fixture_absence"].update({name: False}))(n) for n in seq15.FIXTURE_ABSENCE],
            lambda v: v["fixture_absence"].pop("script"),
            lambda v: v["fixture_absence"].update(unverified=True),
            lambda v: v.update(provider_identity="supervisor"),
            lambda v: v["batch_health"].update(withheld_count=1),
            lambda v: v["batch_health"].update(compatible_count=30),
            lambda v: v.update(predecessor_capabilities_sha256="2" * 64),
            lambda v: v["predecessor_admission"].update(route_acquired=True),
            lambda v: v["predecessor_admission"].update(route_available=True),
            lambda v: v["predecessor_admission"].update(service_reason_code="validation_failed"),
            lambda v: v["predecessor_admission"].update(service_reason_code=None),
            lambda v: v["predecessor_admission"].update(ha_requests=1),
            lambda v: v["predecessor_admission"].update(ha_writes=1),
            lambda v: v["predecessor_admission"].update(control_capability_acquired=False),
            lambda v: v["predecessor_admission"].update(extra=True),
            lambda v: v["engineering_command_types"].append("config/entity_registry/update"),
            lambda v: v["engineering_command_types"].reverse(),
            lambda v: v["engineering_command_types"].append(v["engineering_command_types"][-1]),
            lambda v: v["engineering_command_types"].pop(0),
            lambda v: v["scenarios"].update(extra={"result": "PASS"}),
        ]
        for lane_key in seq15.LANES:
            for index, mutate in enumerate(mutations):
                value = complete_receipt(lane_key)
                mutate(value)
                with self.subTest(lane=lane_key, mutation=index), self.assertRaises(REFUSED):
                    self.verify(lane_key, value)

    def test_optional_reviewed_reads_are_accepted_but_never_required(self):
        value = complete_receipt("helper_options")
        value["engineering_command_types"] = sorted(seq15.LANES["helper_options"]["reviewed_commands"])
        self.verify("helper_options", value)
        for command in sorted(seq15.LANES["helper_options"]["required_commands"]):
            value = complete_receipt("helper_options")
            value["engineering_command_types"].remove(command)
            with self.subTest(command=command), self.assertRaises(REFUSED):
                self.verify("helper_options", value)

    def test_refuses_any_never_sent_command_even_when_otherwise_valid(self):
        for lane_key, lane in seq15.LANES.items():
            commands = set(lane["never_sent"])
            if "call_service" in commands:
                # The bare entry names every service call, in the observed "call_service:d.s" form.
                commands |= {"call_service:homeassistant.restart", "call_service:x.y"}
            for command in sorted(commands):
                value = complete_receipt(lane_key)
                value["engineering_command_types"] = sorted({*value["engineering_command_types"], command})
                with self.subTest(lane=lane_key, command=command), self.assertRaises(REFUSED):
                    self.verify(lane_key, value)

    def test_never_sent_prefix_check_is_independent_of_the_reviewed_set(self):
        lane = {**seq15.LANES["helper_options"]}
        lane["reviewed_commands"] = lane["reviewed_commands"] | {"call_service:x.y"}
        value = complete_receipt("helper_options")
        value["engineering_command_types"] = sorted({*value["engineering_command_types"], "call_service:x.y"})
        with patch.dict(seq15.LANES, {"helper_options": lane}), self.assertRaises(AssertionError):
            self.verify("helper_options", value)

    def test_refuses_missing_extra_or_weakened_helper_scenarios(self):
        mutations = {
            **self.missing("helper_options"),
            "extra": lambda s: s.update(utility_meter={"result": "PASS"}),
            **{name + "_" + label: (lambda name, change: lambda s: change(s[name]))(name, change)
               for name in seq15.HELPER_TYPES for label, change in {
                   "unverified": lambda v: v.update(task_state="manual_review_required"),
                   "redispatched": lambda v: v.update(dispatch_counts=[2]),
                   "readback_mismatch": lambda v: v["readback"].update(precision=9),
                   "no_change": lambda v: v.update(expected=v["baseline"], readback=v["baseline"]),
                   "wrong_write": lambda v: v.update(write_types=[seq15.SELECT_OPTION]),
                   "rollback_refused": lambda v: v["rollback"].update(status="rollback_refused"),
                   "rollback_unverified": lambda v: v["rollback"].update(task_state="failed_post_dispatch"),
                   "rollback_not_restored": lambda v: v["rollback"].update(
                       expected={"other": 1}, readback={"other": 1}),
                   "rollback_defaults_kept": lambda v: v["rollback"].update(defaults_not_restored=["precision"]),
                   "rollback_defaults_unreported": lambda v: v["rollback"].update(defaults_not_restored=None),
                   "rollback_wrong_write": lambda v: v["rollback"].update(write_types=[seq15.OPTIONS_ABORT]),
                   "scenario_failed": lambda v: v.update(result="FAIL"),
               }.items()},
            "suggested_not_disclosed": lambda s: s["suggested_value"].update(disclosed_baseline=None),
            "suggested_not_carried": lambda s: s["suggested_value"].update(raw_baseline=None),
            "field_not_editable_planned": lambda s: s["field_not_editable"].update(plan_created=True),
            "field_not_editable_read_ha": lambda s: s["field_not_editable"].update(ha_commands=1),
            "field_not_editable_wrong_error": lambda s: s["field_not_editable"].update(error_code="validation_failed"),
            "field_not_editable_wrong_reason": lambda s: s["field_not_editable"].update(
                reason_code="helper_options_validation_would_fail"),
            "validation_wrote": lambda s: s["validation_would_fail"].update(ha_writes=1),
            "validation_wrong_reason": lambda s: s["validation_would_fail"].update(reason_code=None),
            "stale_dispatched": lambda s: s["stale_state"].update(dispatch_counts=[1]),
            "stale_succeeded": lambda s: s["stale_state"].update(task_state="succeeded_verified"),
            "stale_wrote": lambda s: s["stale_state"].update(ha_writes=1),
            "stale_other_diagnostic": lambda s: s["stale_state"].update(diagnostic_codes=["dispatch_unknown"]),
            "stale_overwrote": lambda s: s["stale_state"].update(drift_retained=False),
            "concurrent_window_unobserved": lambda s: s["concurrent_flow"].update(od7_overwrite_observed=False),
            "concurrent_rollback_accepted": lambda s: s["concurrent_flow"].update(rollback_reason=None),
            "concurrent_undisclosed": lambda s: s["concurrent_flow"].update(disclosed_window=None),
            "concurrent_unverified": lambda s: s["concurrent_flow"]["readback"].update(time_constant=7),
        }
        self.assert_scenarios_refuse("helper_options", mutations)

    def test_refuses_missing_extra_or_weakened_input_select_scenarios(self):
        mutations = {
            **self.missing("input_select_option"),
            "extra": lambda s: s.update(set_options={"result": "PASS"}),
            "flip_unverified": lambda s: s["flip"].update(task_state="manual_review_required"),
            "flip_readback_mismatch": lambda s: s["flip"].update(readback="Home"),
            "flip_redispatched": lambda s: s["flip"].update(dispatch_counts=[2]),
            "flip_wrong_write": lambda s: s["flip"].update(write_types=["call_service:input_select.select_next"]),
            "rollback_not_prior": lambda s: s["rollback"].update(prior="Night"),
            "rollback_refused": lambda s: s["rollback"].update(status="rollback_refused"),
            "rollback_unverified": lambda s: s["rollback"].update(readback="Away"),
            "already_desired_planned": lambda s: s["already_desired"].update(plan_created=True),
            "already_desired_wrote": lambda s: s["already_desired"].update(ha_writes=1),
            "already_desired_status": lambda s: s["already_desired"].update(status="plan_created"),
            "already_desired_extra": lambda s: s["already_desired"].update(verified=True),
            "option_not_available_planned": lambda s: s["option_not_available"].update(plan_created=True),
            "option_not_available_wrong_reason": lambda s: s["option_not_available"].update(
                reason_code="stale_target_state"),
            "options_changed_dispatched": lambda s: s["options_changed"].update(dispatch_counts=[1]),
            "options_changed_overwrote": lambda s: s["options_changed"].update(drift_retained=False),
            "options_changed_not_stale": lambda s: s["options_changed"].update(diagnostic_codes=[]),
            "scenario_failed": lambda s: s["flip"].update(result="FAIL"),
        }
        self.assert_scenarios_refuse("input_select_option", mutations)

    def test_refuses_missing_extra_or_weakened_script_trace_scenarios(self):
        mutations = {
            **self.missing("script_traces"),
            "extra": lambda s: s.update(breakpoint={"result": "PASS"}),
            "list_empty": lambda s: s["list"].update(traces=0),
            "list_missing_run": lambda s: s["list"].update(run_listed=False),
            "list_failed_closed": lambda s: s["list"].update(sanitization_failed_closed=True),
            "get_other_run": lambda s: s["get"].update(run_matches=False),
            "get_secret_leaked": lambda s: s["get"].update(secret_absent=False),
            "get_unredacted": lambda s: s["get"].update(redacted=False),
            "get_unbounded": lambda s: s["get"].update(long_value_bounded=False),
            "not_found_wrong_reason": lambda s: s["not_found"].update(reason="invalid_arguments"),
            "not_found_wrong_error": lambda s: s["not_found"].update(error_code="internal_error"),
            "not_found_no_read": lambda s: s["not_found"].update(ha_commands=0),
            "not_found_retried": lambda s: s["not_found"].update(ha_commands=2),
            "invalid_reached_ha": lambda s: s["invalid_arguments"].update(ha_commands=1),
            "invalid_wrong_reason": lambda s: s["invalid_arguments"].update(reason="trace_not_found"),
            "scenario_failed": lambda s: s["get"].update(result="FAIL"),
        }
        self.assert_scenarios_refuse("script_traces", mutations)

    def test_refuses_missing_extra_or_weakened_dashboard_scenarios(self):
        edits = {name + "_" + label: (lambda name, change: lambda s: change(s[name]))(name, change)
                 for name in (*seq15.DASHBOARD_EDITS, "rollback") for label, change in {
                     "unverified": lambda v: v.update(task_state="manual_review_required"),
                     "redispatched": lambda v: v.update(dispatch_counts=[2]),
                     "readback_mismatch": lambda v: v["readback"].update(title="Seq15 Other"),
                     "no_change": lambda v: v.update(expected=v["baseline"], readback=v["baseline"]),
                     "wrong_write": lambda v: v.update(write_types=["lovelace/config/save"]),
                     "two_updates": lambda v: v["sent_fields"].append(v["requested_fields"]),
                     "extra_field_sent": lambda v: v["sent_fields"][0].append("url_path"),
                     "field_unsent": lambda v: v["sent_fields"][0].pop(),
                     "requested_unchanged_field": lambda v: v["requested_fields"].append("zz_mode"),
                     "other_dashboard": lambda v: v.update(dashboard_ids_exact=False),
                     "scenario_failed": lambda v: v.update(result="FAIL"),
                 }.items()}
        mutations = {
            **self.missing("dashboard_metadata"), **edits,
            "extra": lambda s: s.update(views={"result": "PASS"}),
            "title_trimmed": lambda s: [s["title"][key].update(title="Seq15 Map")
                                        for key in ("expected", "readback")],
            "icon_clear_key_retained": lambda s: s["icon_clear"].update(icon_key_absent=False),
            "icon_clear_empty_string": lambda s: [s["icon_clear"][key].update(icon="")
                                                  for key in ("expected", "readback")],
            "all_fields_partial": lambda s: s["all_fields"]["sent_fields"][0].remove("icon"),
            "rollback_not_prior": lambda s: s["rollback"].update(prior={**s["rollback"]["prior"], "title": "x"}),
            "rollback_refused": lambda s: s["rollback"].update(status="rollback_refused"),
            "rollback_partial": lambda s: s["rollback"].update(requested_fields=["title"]),
            "no_change_planned": lambda s: s["no_change"].update(plan_created=True),
            "no_change_wrote": lambda s: s["no_change"].update(ha_writes=1),
            "no_change_wrong_reason": lambda s: s["no_change"].update(reason_code="stale_target_state"),
            "default_read_ha": lambda s: s["default_dashboard"].update(ha_commands=1),
            "default_planned": lambda s: s["default_dashboard"].update(plan_created=True),
            "default_wrong_error": lambda s: s["default_dashboard"].update(error_code="validation_failed"),
            "default_wrong_reason": lambda s: s["default_dashboard"].update(reason_code=seq15.DASHBOARD_NO_CHANGE),
            "unknown_id_created": lambda s: s["unknown_id"].update(inventory_unchanged=False),
            "unknown_id_wrong_error": lambda s: s["unknown_id"].update(error_code="invalid_format"),
            "immutable_accepted": lambda s: s["immutable_fields"]["errors"].update(mode=None),
            "immutable_changed": lambda s: s["immutable_fields"].update(row_unchanged=False),
            "content_changed": lambda s: s["content_unchanged"].update(raw_config_unchanged=False),
            "content_fingerprint_differs": lambda s: s["content_unchanged"].update(fingerprints_match=False),
            "content_never_read": lambda s: s["content_unchanged"].update(fingerprint_reads=0),
            "content_reads_untyped": lambda s: s["content_unchanged"].update(fingerprint_reads=True),
            **{name + "_" + label: (lambda name, change: lambda s: change(s[name]))(name, change)
               for name in ("stale_state", "content_drift") for label, change in {
                   "dispatched": lambda v: v.update(dispatch_counts=[1]),
                   "succeeded": lambda v: v.update(task_state="succeeded_verified"),
                   "wrote": lambda v: v.update(ha_writes=1),
                   "other_diagnostic": lambda v: v.update(diagnostic_codes=["dispatch_unknown"]),
                   "overwrote": lambda v: v.update(drift_retained=False),
               }.items()},
        }
        self.assert_scenarios_refuse("dashboard_metadata", mutations)

    def test_scene_receipt_requires_exact_readback_preservation_and_no_activation(self):
        mutations = {
            **self.missing("scene_configuration"),
            **{name + "_" + key: (lambda name, key: lambda s: s[name].update({key: False}))(name, key)
               for name in (*seq15.scene_contract.EDITS, "rollback")
               for key in ("registry_identity_unchanged", "member_state_unchanged", "full_entry_preserved")},
            "type_mismatch": lambda s: s["members"].update(expected={"state": 1}, readback={"state": True}),
            "loaded_claim": lambda s: s["name"].update(loaded_state="verified"),
            "wrong_scope": lambda s: s["icon"].update(verification_scope="loaded"),
            "redispatch": lambda s: s["name"].update(dispatch_counts=[2]),
            "activation": lambda s: s["name"].update(write_types=["call_service:scene.turn_on"]),
            "missing_preserved_key": lambda s: s["icon_clear"]["readback"].pop("id"),
            "stale_wrote": lambda s: s["stale_state"].update(ha_writes=1),
            "mismatch_passed": lambda s: s["stored_mismatch"].update(task_state="succeeded_verified"),
            "mismatch_verified": lambda s: s["stored_mismatch"].update(diagnostic_codes=["exact_readback_verified"]),
            "mismatch_unexplained": lambda s: s["stored_mismatch"].update(diagnostic_codes=[]),
            "mismatch_retried": lambda s: s["stored_mismatch"].update(dispatch_counts=[2]),
            "mismatch_overwritten": lambda s: s["stored_mismatch"].update(mismatch_retained=False),
            "mismatch_wrong_write": lambda s: s["stored_mismatch"].update(write_types=[seq15.SELECT_OPTION]),
            "id_not_assigned": lambda s: s["id_assignment"].update(assigned_id_valid=False),
            "other_entry_changed": lambda s: s["id_assignment"].update(unrequested_keys_preserved=False),
            **{name + "_accepted": (lambda name: lambda s: s[name].update(plan_created=True))(name)
               for name in ("no_change", "not_found", "unknown_member", "target_mismatch")},
        }
        self.assert_scenarios_refuse("scene_configuration", mutations)

    def test_every_lane_binds_each_exact_release_and_signed_prefix(self):
        for core in sorted(seq15.TARGET_CORES):
            binding = alarmo.slot_binding(core)
            registry = json.loads(seq15.SIGNED_REGISTRY.read_text())
            (predecessor,) = [e for e in registry["envelopes"] if e["sequence"] == 14]
            (entry,) = [e for e in predecessor["entries"] if e["version"] == core]
            self.assertEqual(seq15.predecessor_references(core), entry["capabilities"])
            self.assertEqual(len(seq15.predecessor_references(core)), 26)
            (current,) = [e for e in registry["envelopes"][-1]["entries"] if e["version"] == core]
            self.assertEqual(current["capabilities"][:26], entry["capabilities"])
            self.assertEqual(len(current["capabilities"]), 31 if core == "2026.10.3" else 26)
            for lane in seq15.LANES:
                value = complete_receipt(lane, core)
                with self.subTest(core=core, lane=lane):
                    self.verify(lane, value, core, binding["slot"])
                    value["image_index_digest"] = "sha256:" + "0" * 64
                    with self.assertRaises(REFUSED):
                        self.verify(lane, value, core, binding["slot"])

    def missing(self, lane_key):
        return {"missing_" + name: (lambda name: lambda s: s.pop(name))(name)
                for name in seq15.LANES[lane_key]["scenarios"]}

    def assert_scenarios_refuse(self, lane_key, mutations):
        self.verify(lane_key, complete_receipt(lane_key))
        for name, mutate in mutations.items():
            value = complete_receipt(lane_key)
            mutate(value["scenarios"])
            with self.subTest(lane=lane_key, mutation=name), self.assertRaises(REFUSED):
                self.verify(lane_key, value)


class FixturePeer:
    """Synthetic loopback stand-in: acknowledges every delete, optionally retaining objects."""

    ENTRIES = ["01JSEQ15SOURCE000000000000", "01JSEQ15FILTER000000000000"]
    SELECT = "seq15_select_0a1b2c3d"
    SCRIPT = "seq15_0a1b2c3d"
    DASHBOARD = "seq15_ab12cd34"

    def __init__(self, retain=()):
        self.retain = set(retain)
        self.owned = {"config_entries": list(self.ENTRIES), "input_select": self.SELECT, "script": self.SCRIPT,
                      "dashboard": self.DASHBOARD, "scenes": [self.SCRIPT]}
        self.state = {"config_entries": set(self.ENTRIES),
                      "entities": {"sensor." + e.lower(): e for e in self.ENTRIES},
                      "input_select": {self.SELECT}, "script": {self.SCRIPT}, "dashboard": {self.DASHBOARD}, "scene": {self.SCRIPT}}
        self.commands = []
        self.deleted_entries = []

    async def delete_config_entry(self, entry_id):
        self.commands.append("DELETE config_entry")
        self.deleted_entries.append(entry_id)
        if "config_entries" not in self.retain:
            self.state["config_entries"].discard(entry_id)
        if "entities" not in self.retain:
            self.state["entities"] = {k: v for k, v in self.state["entities"].items() if v != entry_id}

    async def ok(self, payload):
        kind = payload["type"]
        self.commands.append(kind)
        if kind == "input_select/delete":
            if "input_select" not in self.retain:
                self.state["input_select"].discard(payload["input_select_id"])
            return None
        if kind == "config_entries/get":
            return [{"entry_id": e} for e in sorted(self.state["config_entries"]) + ["01JUNRELATED0000000000000"]]
        if kind == seq15.REGISTRY_LIST:
            return [{"entity_id": k, "config_entry_id": v} for k, v in self.state["entities"].items()] + [
                {"entity_id": "sensor.unrelated", "config_entry_id": "01JUNRELATED0000000000000"}]
        if kind == "input_select/list":
            return [{"id": item} for item in sorted(self.state["input_select"]) + ["unrelated"]]
        if kind == "lovelace/dashboards/delete":
            assert payload == {"type": kind, "dashboard_id": self.DASHBOARD}
            if "dashboard" not in self.retain:
                self.state["dashboard"].discard(self.DASHBOARD)
            return None
        if kind == seq15.DASHBOARD_LIST:
            return [{"id": item, "url_path": item.replace("_", "-")}
                    for item in sorted(self.state["dashboard"]) + ["map"]]
        raise AssertionError("Unexpected synthetic command")

    async def rest(self, method, path, body=None):
        kind = path.split("/")[2]
        assert method == "DELETE" and kind in {"scene", "script"}
        assert path == "/config/" + kind + "/config/" + self.SCRIPT
        self.commands.append("DELETE " + kind)
        if kind not in self.retain:
            self.state[kind].discard(self.SCRIPT)

    async def status(self, method, path, body=None):
        kind = path.split("/")[2]
        assert method == "GET" and kind in {"scene", "script"}
        assert path == "/config/" + kind + "/config/" + self.SCRIPT
        self.commands.append("GET " + kind)
        return (200, {"alias": "x"}) if self.SCRIPT in self.state[kind] else (404, None)


class FixtureCleanupTests(unittest.IsolatedAsyncioTestCase):
    COMPLETE = dict.fromkeys(seq15.FIXTURE_ABSENCE, True)
    READS = {"config_entries": "config_entries/get", "entities": seq15.REGISTRY_LIST,
             "input_select": "input_select/list", "script": "GET script", "dashboard": seq15.DASHBOARD_LIST, "scene": "GET scene"}

    async def test_complete_cleanup_reads_back_every_owned_type(self):
        peer = FixturePeer()
        absence = await seq15.cleanup_fixtures(peer, peer.owned, attempts=2, delay=0)
        self.assertEqual(absence, self.COMPLETE)
        self.assertEqual(set(peer.commands) - {"DELETE config_entry", "input_select/delete", "DELETE script", "DELETE scene",
                                               "lovelace/dashboards/delete"},
                         set(self.READS.values()))
        self.assertTrue(all(not items for items in peer.state.values()))
        # Dependents first: filter and statistics entries go before their template source.
        self.assertEqual(peer.deleted_entries, list(reversed(FixturePeer.ENTRIES)))

    async def test_acknowledged_but_retained_fixture_is_not_complete(self):
        for retained in seq15.FIXTURE_ABSENCE:
            peer = FixturePeer(retain=[retained])
            with self.subTest(retained=retained):
                absence = await seq15.cleanup_fixtures(peer, peer.owned, attempts=2, delay=0)
                self.assertEqual(absence, {**self.COMPLETE, retained: False})
                self.assertEqual(peer.commands.count(self.READS[retained]), 2)

    async def test_lane_that_owns_only_some_types_reads_back_only_those(self):
        peer = FixturePeer()
        owned = {"script": FixturePeer.SCRIPT}
        absence = await seq15.cleanup_fixtures(peer, owned, attempts=1, delay=0)
        self.assertEqual(absence, self.COMPLETE)
        self.assertNotIn("DELETE config_entry", peer.commands)
        self.assertNotIn("input_select/delete", peer.commands)
        self.assertNotIn("input_select/list", peer.commands)
        self.assertNotIn("lovelace/dashboards/delete", peer.commands)
        self.assertNotIn(seq15.DASHBOARD_LIST, peer.commands)

    async def test_nothing_owned_claims_nothing(self):
        peer = FixturePeer()
        self.assertIsNone(await seq15.cleanup_fixtures(peer, {}, attempts=1, delay=0))
        self.assertEqual(peer.commands, [])

    def test_lane_reports_cleanup_only_from_complete_absence(self):
        source = inspect.getsource(seq15._run_disposable)
        self.assertEqual(source.count("fixture_absence = await cleanup_fixtures(raw, owned)"), 1)
        self.assertEqual(source.count("fixture_cleanup = fixture_absence == dict.fromkeys(FIXTURE_ABSENCE, True)"), 1)
        self.assertEqual(source.count("fixture_cleanup = True"), 0)
        self.assertEqual(source.count("load_receipt(receipt_bytes(result))"), 1)

    def test_governed_paths_never_delete_and_fixtures_are_owned_before_waiting(self):
        self.assertFalse({c for c in seq15.WRITE_REQUESTS if c.startswith("DELETE ") or c.endswith("/delete")})
        for function in (seq15._run_disposable, *seq15.SCENARIOS.values()):
            with self.subTest(function=function.__name__):
                self.assertNotIn("engineering_ws.command(", inspect.getsource(function))
        helper = inspect.getsource(seq15._helper_options_scenarios)
        self.assertLess(helper.index('owned["config_entries"].append(entry_id)'),
                        helper.index("await _entity_of(raw, entry_id)"))
        select = inspect.getsource(seq15._input_select_scenarios)
        self.assertLess(select.index('owned["input_select"] = created["id"]'), select.index("for _ in range(100):"))
        script = inspect.getsource(seq15._script_trace_scenarios)
        self.assertLess(script.index('owned["script"] = key'), script.index('await raw.rest("POST"'))
        dashboard = inspect.getsource(seq15._dashboard_metadata_scenarios)
        self.assertLess(dashboard.index('owned["dashboard"] = created["id"]'),
                        dashboard.index('"lovelace/config/save"'))
        # Fixture setup is harness-only: Engineering's planner never reaches create, save or delete.
        self.assertEqual(dashboard.count('raw.ok({"type": "lovelace/dashboards/create"'), 1)


class DashboardContentReadTests(unittest.IsolatedAsyncioTestCase):
    """The harness stand-in for the admitted content read, through the production reader."""

    class Socket:
        def __init__(self, value):
            self.value, self.sent = value, []

        async def command(self, payload):
            self.sent.append(dict(payload))
            return copy.deepcopy(self.value)

    async def test_reads_the_forced_config_and_binds_engineering_config_hash(self):
        from ha_mcp_engineering.governance.runtime import _dashboard_content_fingerprint_reader
        from ha_mcp_engineering.providers.upstream_dashboard import _engineering_config_hash
        config, seen = {"title": "Seq15 ", "views": [{"cards": []}]}, []
        socket = self.Socket(config)
        read = _dashboard_content_fingerprint_reader(seq15.DashboardContentRead(socket, seen), 60_000)
        value = await read("seq15-ab12cd34")
        self.assertEqual(socket.sent, [{"type": "lovelace/config", "url_path": "seq15-ab12cd34", "force": True}])
        self.assertEqual(value, _engineering_config_hash(config))
        self.assertEqual(seen, [value])
        changed = await _dashboard_content_fingerprint_reader(seq15.DashboardContentRead(
            self.Socket({**config, "title": "Seq15"}), seen), 60_000)("seq15-ab12cd34")
        self.assertNotEqual(changed, value)

    async def test_a_non_configuration_result_fails_closed(self):
        from ha_mcp_engineering.governance.runtime import _dashboard_content_fingerprint_reader
        for value in (None, [], "config"):
            seen = []
            read = _dashboard_content_fingerprint_reader(seq15.DashboardContentRead(self.Socket(value), seen), 1)
            with self.subTest(value=value), self.assertRaises(ValueError):
                await read("seq15-ab12cd34")
            self.assertEqual(seen, [])


class LaneRawRouteTests(unittest.IsolatedAsyncioTestCase):
    """The lane's raw REST routes are closed and loopback-only."""

    def configured(self, host="127.0.0.1"):
        return SimpleNamespace(websocket_url="ws://" + host + ":8123/api/websocket",
                               api_url="http://" + host + ":8123/api", ha_token="synthetic")

    def test_only_a_loopback_core_is_accepted(self):
        self.assertEqual(seq15.LaneRaw(self.configured()).api_url, "http://127.0.0.1:8123/api")
        for host in ("homeassistant.local", "192.168.1.10", "example.com"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                seq15.LaneRaw(self.configured(host))

    async def test_reviewed_routes_match_the_closed_set(self):
        for path in ("/config/config_entries/flow", "/config/config_entries/flow/0123abcd",
                     "/config/config_entries/entry/01JSEQ15ENTRY", "/config/config_entries/options/flow",
                     "/config/config_entries/options/flow/0123456789abcdef0123456789abcdef",
                     "/config/script/config/seq15_0a1b2c3d", "/states/script.seq15_0a1b2c3d",
                     "/states/input_select.seq15_select_0a1b2c3d"):
            with self.subTest(path=path):
                self.assertIsNotNone(seq15.RAW_ROUTE.fullmatch(path))

    async def test_routes_outside_the_closed_set_are_refused_before_sending(self):
        raw = seq15.LaneRaw(self.configured())
        for path in ("/config/config_entries/entry", "/config/config_entries/entry/abc/reload",
                     "/config/config_entries/options/flow/abc", "/services/homeassistant/restart",
                     "/config/config_entries/flow/../entry/abc", "/config/config_entries/flow/" + "a" * 65,
                     "/config/script/config/other", "/config/automation/config/seq15_0a1b2c3d",
                     "/config/script/config/seq15_0a1b2c3d/../x", "/states/light.kitchen",
                     "/states/script.other", "/states/input_select.seq15_x/../../services"):
            for method in ("GET", "POST", "DELETE"):
                with self.subTest(path=path, method=method), patch("aiohttp.ClientSession") as session, \
                        self.assertRaisesRegex(ValueError, "Not a seq-15 lane route"):
                    await raw.rest(method, path)
                session.assert_not_called()

    async def test_raw_refusal_diagnostic_names_only_the_route_family(self):
        raw = seq15.LaneRaw(self.configured())
        for path, family in (("/config/config_entries/entry/01JSEQ15ENTRY", "DELETE config_entries"),
                             ("/config/script/config/seq15_0a1b2c3d", "DELETE script"),
                             ("/states/script.seq15_0a1b2c3d", "DELETE states")):
            with self.subTest(path=path), patch.object(raw, "status", return_value=(500, None)), \
                    self.assertRaises(RawRefusal) as found:
                await raw.rest("DELETE", path)
            self.assertEqual(found.exception.diagnostic, {"command": family, "error_code": "500"})
            self.assertNotIn("seq15_0a1b2c3d", json.dumps(found.exception.diagnostic))
            self.assertNotIn("01JSEQ15ENTRY", json.dumps(found.exception.diagnostic))


class DormancyTests(unittest.IsolatedAsyncioTestCase):
    async def test_lanes_refuse_unless_exactly_enabled(self):
        for value in (None, "", "dormant", "Enabled", "enabled ", "1", "true"):
            environ = {k: v for k, v in os.environ.items() if k != seq15.ENABLE_FLAG}
            if value is not None:
                environ[seq15.ENABLE_FLAG] = value
            for lane_key in seq15.LANES:
                with self.subTest(value=value, lane=lane_key), patch.dict(os.environ, environ, clear=True), \
                        patch.object(seq15, "_run_disposable") as inner, \
                        self.assertRaisesRegex(ValueError, "defined but not wired") as found:
                    await seq15.run_disposable(lane_key, None, expected_image="", core_version=CORE)
                inner.assert_not_called()
                self.assertEqual(found.exception.contract_missing_key, "seq15_" + lane_key)
                self.assertEqual(found.exception.contract_diagnostic["seq15_stage"], "binding")

    async def test_failure_retains_locations_without_exception_or_argument_content(self):
        private = "synthetic-do-not-retain"
        with patch.dict(os.environ, {seq15.ENABLE_FLAG: seq15.ENABLED}), \
                patch.object(seq15, "_run_disposable", side_effect=AssertionError(private)):
            with self.assertRaises(AssertionError) as found:
                await seq15.run_disposable("helper_options", private, expected_image=private, core_version=private)
        diagnostic = found.exception.contract_diagnostic
        self.assertEqual(set(diagnostic), {"seq15_source_lines", "seq15_stage"})
        self.assertTrue(all(type(line["line"]) is int and line["file"] in {"seq15_contract_acceptance.py", "seq15_scene_contract.py"}
                            for line in diagnostic["seq15_source_lines"]))
        self.assertNotIn(private, json.dumps(diagnostic))
        with patch.dict(os.environ, {seq15.ENABLE_FLAG: seq15.ENABLED}), self.assertRaises(KeyError) as found:
            await seq15.run_disposable(private, None, expected_image=private, core_version=private)
        self.assertEqual(found.exception.contract_missing_key, "seq15_unknown")

    async def test_raw_refusal_keeps_only_its_closed_diagnostic(self):
        refusal = RawRefusal("DELETE config_entries", "500")
        with patch.dict(os.environ, {seq15.ENABLE_FLAG: seq15.ENABLED}), \
                patch.object(seq15, "_run_disposable", side_effect=refusal), self.assertRaises(RawRefusal) as found:
            await seq15.run_disposable("helper_options", None, expected_image="", core_version=CORE)
        self.assertEqual(found.exception.contract_diagnostic["raw_refusal"],
                         {"command": "DELETE config_entries", "error_code": "500"})

    def test_harness_hook_runs_only_on_the_exact_flag_and_target_slot(self):
        source = inspect.getsource(real_ha_contract_tests)
        flag = 'os.environ.get("REAL_HA_SEQ15_LANES") == "enabled"'
        self.assertEqual(source.count(flag), 1)
        self.assertEqual(source.count("import seq15_contract_acceptance"), 1)
        self.assertEqual(source.count("seq15.run_disposable("), 1)
        self.assertEqual(source.count("retained.update(seq15_results)"), 1)
        self.assertEqual(source.count("if EXPECTED_HA_VERSION in seq15.TARGET_CORES else ()"), 1)
        hook = source.index(flag)
        self.assertLess(hook, source.index("import seq15_contract_acceptance"))
        self.assertLess(source.index("import seq15_contract_acceptance"), source.index("seq15.run_disposable("))
        # The seq-15 hook follows the seq-13 hook and its results are retained after seq-13's.
        self.assertLess(source.index('os.environ.get("REAL_HA_SEQ13_LANES") == "enabled"'), hook)
        self.assertLess(source.index("retained.update(seq13_results)"),
                        source.index("retained.update(seq15_results)"))


class LaneGuardTests(unittest.IsolatedAsyncioTestCase):
    HIDDEN = ("HA_CONTRACT_LANE", "REAL_HA_ALARMO_IMAGE_RESULT", "REAL_HA_HELPER_OPTIONS_RESULT",
              "REAL_HA_INPUT_SELECT_OPTION_RESULT", "REAL_HA_SCRIPT_TRACES_RESULT",
              "REAL_HA_DASHBOARD_METADATA_RESULT")

    async def test_lane_refuses_unextended_slot_wrong_image_or_remote_host_before_any_client(self):
        binding = alarmo.slot_binding(CORE)
        local = SimpleNamespace(ha_url="http://127.0.0.1:8123")
        cases = [
            (local, binding["image"], CORE, alarmo.slot_binding(OTHER_CORES[0])["lane"]),
            (local, binding["image"], CORE, None),
            (local, alarmo.slot_binding(OTHER_CORES[0])["image"], CORE, binding["lane"]),
            (local, binding["image"].split("@")[0], CORE, binding["lane"]),
            (SimpleNamespace(ha_url="http://homeassistant.local:8123"), binding["image"], CORE, binding["lane"]),
        ]
        # Releases outside the two exact targets remain refused.
        for other in OTHER_CORES:
            own = alarmo.slot_binding(other)
            cases.append((local, own["image"], other, own["lane"]))
        for lane_key, lane in seq15.LANES.items():
            for configured, image, version, lane_name in cases:
                environ = {k: v for k, v in os.environ.items() if k not in self.HIDDEN}
                if lane_name is not None:
                    environ["HA_CONTRACT_LANE"] = lane_name
                with self.subTest(lane=lane_key, image=image, version=version, url=configured.ha_url), \
                        patch.dict(os.environ, environ, clear=True), \
                        patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                        patch("ha_mcp_engineering.clients.HomeAssistantRestClient") as rest, \
                        self.assertRaisesRegex(ValueError, "Exact owned disposable seq-15 lane required"):
                    await seq15._run_disposable(lane, configured, expected_image=image, core_version=version,
                                                stage=["x"])
                client.assert_not_called()
                rest.assert_not_called()
            for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
                environ = {k: v for k, v in os.environ.items() if k not in self.HIDDEN}
                environ["HA_CONTRACT_LANE"] = binding["lane"]
                with self.subTest(lane=lane_key, unknown=unknown), patch.dict(os.environ, environ, clear=True), \
                        self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                    await seq15._run_disposable(lane, local, expected_image=binding["image"], core_version=unknown,
                                                stage=["x"])

    async def test_lane_refuses_a_wrong_slot_image_receipt_before_any_client(self):
        binding = alarmo.slot_binding(CORE)
        other = alarmo.slot_binding(OTHER_CORES[0])
        receipts = [
            {"result": "PASS", "index": other["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
            {"result": "PASS", "index": binding["image_index_digest"], "core_version": OTHER_CORES[0],
             "support_slot": CORE_SLOT},
            {"result": "PASS", "index": binding["image_index_digest"], "core_version": CORE,
             "support_slot": "current"},
            {"result": "FAIL", "index": binding["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
        ]
        for lane_key, lane in seq15.LANES.items():
            for image in receipts:
                with tempfile.TemporaryDirectory() as directory:
                    image_path, output = Path(directory) / "image.json", Path(directory) / "seq15.json"
                    image_path.write_text(json.dumps(image))
                    environ = {"HA_CONTRACT_LANE": binding["lane"], "REAL_HA_ALARMO_IMAGE_RESULT": str(image_path),
                               lane["output_env"]: str(output)}
                    with self.subTest(lane=lane_key, image=image), patch.dict(os.environ, environ), \
                            patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                            self.assertRaises(AssertionError):
                        await seq15._run_disposable(lane, SimpleNamespace(ha_url="http://127.0.0.1:8123"),
                                                    expected_image=binding["image"], core_version=CORE, stage=["x"])
                    client.assert_not_called()
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
