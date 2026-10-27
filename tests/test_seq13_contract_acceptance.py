"""Offline controls for the HAMCP-165 / HAMCP-069B seq-13 lane receipts, not Core execution.

The lanes are defined but dormant: they run only inside the disposable Real HA
job when ``REAL_HA_SEQ13_LANES`` is ``enabled``, which no committed workflow
sets. Receipts here are synthetic.
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
import seq13_contract_acceptance as seq13  # noqa: E402
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
import real_ha_contract_tests  # noqa: E402
from entity_metadata_contract_acceptance import load_receipt, receipt_bytes  # noqa: E402
from test_alarmo_interval_observer import CORE, NOT_SLOTS, NOT_STRINGS, OTHER_CORE, SLOT_VERSIONS  # noqa: E402

CORE_SLOT = next(slot for slot, version in SLOT_VERSIONS.items() if version == CORE)
REFUSED = (AssertionError, ValueError, KeyError, TypeError)
LABEL_FINGERPRINT = "sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c"
AVAILABILITY_FINGERPRINT = "sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2"
EXPOSURE_FINGERPRINT = "sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd"


def governed(*types, expected, readback=None):
    return {"result": "PASS", "task_state": "succeeded_verified", "dispatch_counts": [1] * len(types),
            "ha_writes": len(types), "write_types": list(types), "expected": expected,
            "readback": copy.deepcopy(expected if readback is None else readback)}


def refused(reason_code):
    return {"result": "PASS", "error_code": "validation_failed", "reason_code": reason_code,
            "plan_created": False, "ha_writes": 0, "ha_commands": 2}


STALE = {"result": "PASS", "task_state": "failed_pre_dispatch", "dispatch_counts": [0], "ha_writes": 0,
         "write_types": [], "drift_retained": True}


def label_scenarios():
    canary = {"label_id": seq13.CANARY_ID, **seq13.CANARY}
    edited = {"label_id": seq13.CANARY_ID, **seq13.EDITED}
    assign = {"label_id": seq13.ASSIGN_ID, "name": seq13.ASSIGN["name"], "color": None, "icon": None,
              "description": None, "entity_labels": [seq13.ASSIGN_ID]}
    return {
        "create": {**governed(seq13.LABEL_CREATE, expected=canary),
                   "predicted_label_id": seq13.CANARY_ID, "core_label_id": seq13.CANARY_ID},
        "edit": {**governed(seq13.LABEL_UPDATE, expected=edited),
                 "sent_fields": sorted(["label_id", *seq13.EDITED])},
        "create_then_assign": {**governed(seq13.LABEL_CREATE, seq13.ENTITY_UPDATE, expected=assign),
                               "predicted_label_id": seq13.ASSIGN_ID, "core_label_id": seq13.ASSIGN_ID},
        "duplicate_name": refused("label_already_exists"),
        "rename_in_use": refused("label_name_in_use"),
        "invalid_values": {"result": "PASS", "plan_created": False, "ha_writes": 0, "ha_commands": 0,
                           "reason_codes": {"color": "invalid_label_color", "icon": "invalid_label_icon"}},
        "stale_state": dict(STALE),
    }


def availability_scenarios():
    return {
        "disable": governed(seq13.ENTITY_UPDATE, expected={"disabled_by": "user"}),
        "already_disabled": refused("entity_already_disabled"),
        "enable": governed(seq13.ENTITY_UPDATE, expected={"disabled_by": None}),
        "enable_config_entry": {**governed(seq13.ENTITY_UPDATE, expected={"disabled_by": None}),
                                "config_entry_reads": sorted(seq13.CONFIG_ENTRY_READS),
                                "config_entry": {"status": "read", "domain": "tod", "supports_unload": True,
                                                 "disabled_by": None},
                                "other_entities_in_entry": 0, "reload": "config_entry_reload_after_30s"},
        "unexpose": governed(seq13.EXPOSE, expected={"should_expose": False}),
        "expose": governed(seq13.EXPOSE, expected={"should_expose": True}),
        "not_in_registry": refused("entity_not_in_registry"),
        "stale_state": dict(STALE),
    }


SCENARIOS = {"label_registry": label_scenarios, "availability_exposure": availability_scenarios}


def synthetic_receipt(lane_key, core_version):
    """The receipt as the pre-review1 lane wrote it: no config-entry enable read was exercised."""
    lane = seq13.LANES[lane_key]
    binding = alarmo.slot_binding(core_version)
    return {
        "result": "PASS", "lane": lane_key, "core": core_version, "support_slot": binding["slot"],
        "core_image": binding["image"], "image_index_digest": binding["image_index_digest"],
        "image_receipt_sha256": "1" * 64, "engineering_source": seq13.engineering_source(),
        "added_capabilities": seq13.source_profiles(lane), "predecessor_capabilities_sha256": "2" * 64,
        "authority_transition": list(lane["authority_transition"]),
        "authority_instances": "separate_ephemeral_registries", "production_authority": False,
        "provider_identity": "synthetic_disposable_lane",
        "predecessor_admission": {"route_acquired": False, "service_reason_code": "pending_signed_registry_admission",
                                  "ha_requests": 0, "ha_writes": 0, "control_capability_acquired": True},
        "engineering_command_types": sorted(lane["required_commands"] - seq13.CONFIG_ENTRY_READS),
        "scenarios": SCENARIOS[lane_key](), "fixture_cleanup": True,
        "fixture_absence": dict.fromkeys(seq13.FIXTURE_ABSENCE, True)}


def complete_receipt(lane_key, core_version):
    """A complete receipt: the availability lane also ran both config-entry enable reads (M4)."""
    value = synthetic_receipt(lane_key, core_version)
    value["engineering_command_types"] = sorted(seq13.LANES[lane_key]["required_commands"])
    return value


class LaneDefinitionTests(unittest.TestCase):
    def test_lanes_bind_the_dormant_seq13_profiles_from_source(self):
        self.assertEqual(sorted(seq13.LANES), ["availability_exposure", "label_registry"])
        fingerprints = {key: [r["contract_fingerprint"] for r in seq13.source_profiles(lane)]
                        for key, lane in seq13.LANES.items()}
        self.assertEqual(fingerprints, {"label_registry": [LABEL_FINGERPRINT],
                                        "availability_exposure": [AVAILABILITY_FINGERPRINT, EXPOSURE_FINGERPRINT]})
        self.assertEqual(seq13.LANES["label_registry"]["authority_transition"], [22, 23])
        self.assertEqual(seq13.LANES["availability_exposure"]["authority_transition"], [22, 24])
        self.assertEqual(seq13.LANES["label_registry"]["tool"], "create_label_plan")
        self.assertEqual(seq13.LANES["availability_exposure"]["tool"], "create_entity_availability_exposure_plan")

    def test_predecessor_is_the_shipped_set_without_any_seq13_profile(self):
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
        refs = seq13.shipped_references()
        ids = {r["capability_id"] for r in refs}
        self.assertEqual(len(refs), 22)
        self.assertFalse(ids & seq13.SEQ13_DORMANT)
        self.assertIn(seq13.METADATA, ids)
        self.assertEqual(ids | seq13.SEQ13_DORMANT | seq13.NOVEMBER_DORMANT,
                         {p.capability_id for p in CORE_RUNTIME_CAPABILITY_PROFILES})
        self.assertFalse(ids & seq13.NOVEMBER_DORMANT)

    def test_no_lane_reviews_a_deletion_or_never_sent_command(self):
        for key, lane in seq13.LANES.items():
            with self.subTest(lane=key):
                self.assertFalse(lane["reviewed_commands"] & seq13.NEVER_SENT)
                self.assertLessEqual(lane["required_commands"], lane["reviewed_commands"])
                self.assertFalse({c for c in lane["reviewed_commands"] if c.endswith("/delete")})
        self.assertIn("config/label_registry/delete", seq13.NEVER_SENT)


class ReceiptTests(unittest.TestCase):
    def verify(self, lane_key, value, core_version=CORE, support_slot=CORE_SLOT):
        return seq13.verify_result(lane_key, value, core_version=core_version, support_slot=support_slot)

    def test_receipt_follows_every_window_slot_for_both_lanes(self):
        for lane_key, lane in seq13.LANES.items():
            for slot, version in SLOT_VERSIONS.items():
                with self.subTest(lane=lane_key, slot=slot):
                    summary = seq13.verify_result(lane_key, complete_receipt(lane_key, version),
                                                  core_version=version, support_slot=slot)
                    self.assertEqual(summary, {
                        "result": "PASS", "lane": lane_key, "core": version, "support_slot": slot,
                        "contract_fingerprints": [r["contract_fingerprint"] for r in seq13.source_profiles(lane)],
                        "scenarios": len(lane["scenarios"]),
                        "authority_transition": lane["authority_transition"], "fixture_cleanup": True})

    def test_serialized_receipt_round_trips_canonically(self):
        for lane_key in seq13.LANES:
            with self.subTest(lane=lane_key):
                raw = receipt_bytes(complete_receipt(lane_key, CORE))
                self.assertEqual(receipt_bytes(load_receipt(raw)), raw)
                self.verify(lane_key, load_receipt(raw))

    def test_refuses_another_lanes_receipt(self):
        self.verify("label_registry", complete_receipt("label_registry", CORE))
        with self.assertRaises(REFUSED):
            self.verify("label_registry", complete_receipt("availability_exposure", CORE))
        with self.assertRaises(REFUSED):
            self.verify("availability_exposure", complete_receipt("label_registry", CORE))
        with self.assertRaises(KeyError):
            self.verify("entity_metadata", complete_receipt("label_registry", CORE))

    def test_refuses_wrong_core_slot_or_missing_binding_arguments(self):
        for lane_key in seq13.LANES:
            value = complete_receipt(lane_key, CORE)
            for other in [v for v in SLOT_VERSIONS.values() if v != CORE] + list(NOT_SLOTS):
                with self.subTest(lane=lane_key, caller_core=other), self.assertRaises(REFUSED):
                    self.verify(lane_key, copy.deepcopy(value), core_version=other)
            for wrong in [s for s in ("current", "candidate", "rollback") if s != CORE_SLOT] + list(NOT_STRINGS):
                with self.subTest(lane=lane_key, caller_slot=wrong), self.assertRaises(REFUSED):
                    self.verify(lane_key, copy.deepcopy(value), support_slot=wrong)
            with self.assertRaises(TypeError):
                seq13.verify_result(lane_key, copy.deepcopy(value))

    def test_refuses_wrong_image_fingerprint_source_authority_or_cleanup(self):
        other = alarmo.slot_binding(OTHER_CORE)
        zero = "sha256:" + "0" * 64
        mutations = [
            lambda v: v.update(result="FAIL"),
            lambda v: v.update(core=OTHER_CORE),
            lambda v: v.update(core_image=other["image"]),
            lambda v: v.update(image_index_digest=other["image_index_digest"]),
            lambda v: v.update(image_receipt_sha256="1" * 63),
            lambda v: v["added_capabilities"][0].update(contract_fingerprint=zero),
            lambda v: v["added_capabilities"][0].update(adapter_id="compiled-core-other-v1"),
            lambda v: v["added_capabilities"][0].update(profile_version=v["added_capabilities"][0]["profile_version"] + 1),
            lambda v: v["added_capabilities"].pop(),
            lambda v: v["added_capabilities"].reverse() if len(v["added_capabilities"]) > 1
            else v["added_capabilities"].append(dict(v["added_capabilities"][0])),
            lambda v: v["engineering_source"].update(runtime_tree="0" * 40),
            lambda v: v["engineering_source"].update(commit="0" * 39),
            lambda v: v.update(authority_transition=[21, 22]),
            lambda v: v.update(authority_transition=[22, 22]),
            lambda v: v.update(authority_instances="shared_registry"),
            lambda v: v.update(production_authority=True),
            lambda v: v.update(fixture_cleanup=False),
            lambda v: v["fixture_absence"].update(labels=False),
            lambda v: v["fixture_absence"].update(helper_entity=False),
            lambda v: v["fixture_absence"].pop("helper_config"),
            lambda v: v["fixture_absence"].update(unverified=True),
            lambda v: v.update(provider_identity="supervisor"),
            lambda v: v.update(predecessor_capabilities_sha256=""),
            lambda v: v["predecessor_admission"].update(route_acquired=True),
            lambda v: v["predecessor_admission"].update(service_reason_code="validation_failed"),
            lambda v: v["predecessor_admission"].update(service_reason_code=None),
            lambda v: v["predecessor_admission"].update(ha_requests=1),
            lambda v: v["predecessor_admission"].update(ha_writes=1),
            lambda v: v["predecessor_admission"].update(control_capability_acquired=False),
            lambda v: v["engineering_command_types"].append("call_service"),
            lambda v: v["engineering_command_types"].reverse(),
            lambda v: v["engineering_command_types"].append(v["engineering_command_types"][-1]),
            lambda v: v["engineering_command_types"].pop(0),
        ]
        for lane_key in seq13.LANES:
            for index, mutate in enumerate(mutations):
                value = complete_receipt(lane_key, CORE)
                mutate(value)
                with self.subTest(lane=lane_key, mutation=index), self.assertRaises(REFUSED):
                    self.verify(lane_key, value)

    def test_refuses_any_never_sent_command_even_when_otherwise_valid(self):
        for lane_key in seq13.LANES:
            for command in sorted(seq13.NEVER_SENT):
                value = complete_receipt(lane_key, CORE)
                value["engineering_command_types"] = sorted({*value["engineering_command_types"], command})
                with self.subTest(lane=lane_key, command=command), self.assertRaises(REFUSED):
                    self.verify(lane_key, value)

    def test_refuses_missing_extra_or_weakened_label_scenarios(self):
        def drop(name):
            return lambda s: s.pop(name)
        mutations = {
            **{"missing_" + name: drop(name) for name in seq13.LANES["label_registry"]["scenarios"]},
            "extra": lambda s: s.update(delete={"result": "PASS"}),
            "create_unverified": lambda s: s["create"].update(task_state="manual_review_required"),
            "create_readback_mismatch": lambda s: s["create"]["readback"].update(color="red"),
            "create_redispatched": lambda s: s["create"].update(dispatch_counts=[2]),
            "create_id_mispredicted": lambda s: s["create"].update(predicted_label_id="hamcp165_other"),
            "create_core_id_differs": lambda s: s["create"].update(core_label_id="hamcp165_canary_2"),
            "edit_wrong_write": lambda s: s["edit"].update(write_types=[seq13.LABEL_CREATE]),
            "edit_unchanged_fields_sent": lambda s: s["edit"].update(
                sent_fields=sorted(["label_id", "name", "color", "icon", "description", "created_at"])),
            "edit_missing_field": lambda s: s["edit"].update(sent_fields=["label_id", "name"]),
            "assign_not_ordered": lambda s: s["create_then_assign"].update(
                write_types=[seq13.ENTITY_UPDATE, seq13.LABEL_CREATE]),
            "assign_partial": lambda s: s["create_then_assign"].update(dispatch_counts=[1, 0], ha_writes=1),
            "assign_not_applied": lambda s: s["create_then_assign"]["readback"].update(entity_labels=[]),
            "duplicate_accepted": lambda s: s["duplicate_name"].update(plan_created=True),
            "duplicate_wrote": lambda s: s["duplicate_name"].update(ha_writes=1),
            "duplicate_wrong_reason": lambda s: s["duplicate_name"].update(reason_code="label_name_in_use"),
            "rename_wrong_reason": lambda s: s["rename_in_use"].update(reason_code="label_already_exists"),
            "invalid_read_ha": lambda s: s["invalid_values"].update(ha_commands=1),
            "invalid_color_accepted": lambda s: s["invalid_values"]["reason_codes"].update(color=None),
            "invalid_icon_wrong": lambda s: s["invalid_values"]["reason_codes"].update(icon="invalid_label_color"),
            "stale_dispatched": lambda s: s["stale_state"].update(dispatch_counts=[1]),
            "stale_succeeded": lambda s: s["stale_state"].update(task_state="succeeded_verified"),
            "stale_overwrote": lambda s: s["stale_state"].update(drift_retained=False),
            "stale_wrote": lambda s: s["stale_state"].update(ha_writes=1),
            "scenario_failed": lambda s: s["edit"].update(result="FAIL"),
        }
        self.assert_scenarios_refuse("label_registry", mutations)

    def test_refuses_missing_extra_or_weakened_availability_scenarios(self):
        def drop(name):
            return lambda s: s.pop(name)
        mutations = {
            **{"missing_" + name: drop(name) for name in seq13.LANES["availability_exposure"]["scenarios"]},
            "extra": lambda s: s.update(set_default={"result": "PASS"}),
            "disable_unverified": lambda s: s["disable"].update(task_state="failed_post_dispatch"),
            "disable_readback_mismatch": lambda s: s["disable"]["readback"].update(disabled_by=None),
            "disable_by_integration": lambda s: s["disable"]["expected"].update(disabled_by="integration"),
            "enable_wrong_write": lambda s: s["enable"].update(write_types=[seq13.EXPOSE]),
            "expose_wrong_write": lambda s: s["expose"].update(write_types=[seq13.ENTITY_UPDATE]),
            "unexpose_mismatch": lambda s: s["unexpose"]["readback"].update(should_expose=True),
            "already_disabled_wrote": lambda s: s["already_disabled"].update(ha_writes=1),
            "already_disabled_wrong_reason": lambda s: s["already_disabled"].update(
                reason_code="entity_not_in_registry"),
            "not_in_registry_planned": lambda s: s["not_in_registry"].update(plan_created=True),
            "stale_dispatched": lambda s: s["stale_state"].update(dispatch_counts=[1]),
            "stale_overwrote": lambda s: s["stale_state"].update(drift_retained=False),
            # HAMCP-165 review1 M4: the config-entry enable reads and their results are bound.
            **{"config_entry_read_missing_" + c: (lambda c: lambda s: s["enable_config_entry"].update(
                config_entry_reads=sorted(seq13.CONFIG_ENTRY_READS - {c})))(c) for c in seq13.CONFIG_ENTRY_READS},
            "config_entry_unread": lambda s: s["enable_config_entry"]["config_entry"].update(status="unavailable"),
            "config_entry_other_domain": lambda s: s["enable_config_entry"]["config_entry"].update(domain="input_boolean"),
            "config_entry_no_unload": lambda s: s["enable_config_entry"]["config_entry"].update(supports_unload=False),
            "config_entry_disabled": lambda s: s["enable_config_entry"]["config_entry"].update(disabled_by="user"),
            "config_entry_count_unavailable": lambda s: s["enable_config_entry"].update(
                other_entities_in_entry="unavailable"),
            "config_entry_reload_unknown": lambda s: s["enable_config_entry"].update(reload="restart_required"),
            "config_entry_enable_unverified": lambda s: s["enable_config_entry"].update(
                task_state="manual_review_required"),
            "config_entry_readback_mismatch": lambda s: s["enable_config_entry"]["readback"].update(disabled_by="user"),
        }
        self.assert_scenarios_refuse("availability_exposure", mutations)

    def test_availability_receipt_requires_both_config_entry_reads(self):
        # HAMCP-165 review1 M4: the lane fails unless both enable reads ran.
        self.verify("availability_exposure", complete_receipt("availability_exposure", CORE))
        for command in sorted(seq13.CONFIG_ENTRY_READS):
            value = complete_receipt("availability_exposure", CORE)
            value["engineering_command_types"].remove(command)
            with self.subTest(command=command), self.assertRaises(REFUSED):
                self.verify("availability_exposure", value)
        with self.assertRaises(REFUSED):
            self.verify("availability_exposure", synthetic_receipt("availability_exposure", CORE))
        # The label lane never needs them.
        self.assertFalse(seq13.LANES["label_registry"]["required_commands"] & seq13.CONFIG_ENTRY_READS)

    def assert_scenarios_refuse(self, lane_key, mutations):
        for name, mutate in mutations.items():
            value = complete_receipt(lane_key, CORE)
            mutate(value["scenarios"])
            with self.subTest(lane=lane_key, mutation=name), self.assertRaises(REFUSED):
                self.verify(lane_key, value)


class FixturePeer:
    """Synthetic loopback stand-in: acknowledges every delete, optionally retaining objects."""

    HELPER = "seq13_lane_0a1b2c3d"
    ENTRY = "01JSEQ13LANEENTRY0000000000"
    LISTS = {"input_boolean/list": ("helper_config", "id"), seq13.LABEL_LIST: ("labels", "label_id"),
             "config_entries/get": ("config_entry", "entry_id")}

    def __init__(self, retain=()):
        self.retain = set(retain)
        self.owned = {"helper": self.HELPER, "labels": [seq13.CANARY_ID, seq13.ASSIGN_ID], "config_entry": self.ENTRY}
        self.state = {"helper_config": {self.HELPER}, "helper_entity": {"input_boolean." + self.HELPER},
                      "labels": set(self.owned["labels"]), "config_entry": {self.ENTRY}}
        self.commands = []

    async def delete_config_entry(self, entry_id):
        self.commands.append("DELETE config_entry")
        if "config_entry" not in self.retain:
            self.state["config_entry"].discard(entry_id)

    async def ok(self, payload):
        kind = payload["type"]
        self.commands.append(kind)
        if kind == "config/label_registry/delete":
            if "labels" not in self.retain:
                self.state["labels"].discard(payload["label_id"])
            return None
        if kind == "input_boolean/delete":
            for name, item in (("helper_config", payload["input_boolean_id"]),
                               ("helper_entity", "input_boolean." + payload["input_boolean_id"])):
                if name not in self.retain:
                    self.state[name].discard(item)
            return None
        if kind in self.LISTS:
            name, field = self.LISTS[kind]
            return [{field: item} for item in sorted(self.state[name]) + ["synthetic_unrelated"]]
        raise AssertionError("Unexpected synthetic command")

    async def call(self, payload):
        assert payload["type"] == seq13.ENTITY_GET
        self.commands.append(seq13.ENTITY_GET)
        if payload["entity_id"] in self.state["helper_entity"]:
            return {"type": "result", "success": True, "result": {"entity_id": payload["entity_id"]}}
        return {"type": "result", "success": False, "error": {"code": "not_found"}}


class FixtureCleanupTests(unittest.IsolatedAsyncioTestCase):
    COMPLETE = dict.fromkeys(seq13.FIXTURE_ABSENCE, True)
    READS = {"helper_config": "input_boolean/list", "labels": seq13.LABEL_LIST, "helper_entity": seq13.ENTITY_GET,
             "config_entry": "config_entries/get"}

    async def test_complete_cleanup_reads_back_every_owned_type(self):
        peer = FixturePeer()
        absence = await seq13.cleanup_fixtures(peer, peer.owned, attempts=2, delay=0)
        self.assertEqual(absence, self.COMPLETE)
        self.assertEqual(set(peer.commands) - {"input_boolean/delete", "config/label_registry/delete",
                                               "DELETE config_entry"},
                         set(self.READS.values()))
        self.assertTrue(all(not items for items in peer.state.values()))

    async def test_acknowledged_but_retained_fixture_is_not_complete(self):
        for retained in seq13.FIXTURE_ABSENCE:
            peer = FixturePeer(retain=[retained])
            with self.subTest(retained=retained):
                absence = await seq13.cleanup_fixtures(peer, peer.owned, attempts=2, delay=0)
                self.assertEqual(absence, {**self.COMPLETE, retained: False})
                self.assertEqual(peer.commands.count(self.READS[retained]), 2)

    async def test_nothing_owned_claims_nothing(self):
        peer = FixturePeer()
        self.assertIsNone(await seq13.cleanup_fixtures(peer, {}, attempts=1, delay=0))
        self.assertEqual(peer.commands, [])

    def test_lane_reports_cleanup_only_from_complete_absence(self):
        source = inspect.getsource(seq13._run_disposable)
        self.assertEqual(source.count("fixture_absence = await cleanup_fixtures(raw, owned)"), 1)
        self.assertEqual(source.count("fixture_cleanup = fixture_absence == dict.fromkeys(FIXTURE_ABSENCE, True)"), 1)
        self.assertEqual(source.count("fixture_cleanup = True"), 0)
        self.assertEqual(source.count("load_receipt(receipt_bytes(result))"), 1)

    def test_governed_paths_never_delete(self):
        # Only the lane's raw cleanup client deletes; the observed Engineering client never may.
        self.assertFalse({c for c in seq13.WRITE_COMMANDS if c.endswith("/delete")})
        source = inspect.getsource(seq13._run_disposable)
        self.assertNotIn("engineering_ws.command({", source)


class LaneRawRouteTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-165 review1 M4: the lane's config-entry routes are closed and loopback-only."""

    def configured(self, host="127.0.0.1"):
        return SimpleNamespace(websocket_url="ws://" + host + ":8123/api/websocket",
                               api_url="http://" + host + ":8123/api", ha_token="synthetic")

    def test_only_a_loopback_core_is_accepted(self):
        self.assertEqual(seq13.LaneRaw(self.configured()).api_url, "http://127.0.0.1:8123/api")
        with self.assertRaises(ValueError):
            seq13.LaneRaw(self.configured("homeassistant.local"))

    async def test_routes_outside_the_closed_set_are_refused_before_sending(self):
        raw = seq13.LaneRaw(self.configured())
        for path in ("/config/config_entries/entry", "/config/config_entries/entry/abc/reload",
                     "/config/config_entries/options/flow", "/services/homeassistant/restart",
                     "/config/config_entries/flow/../entry/abc", "/config/config_entries/flow/" + "a" * 65):
            with self.subTest(path=path), patch("aiohttp.ClientSession") as session, \
                    self.assertRaisesRegex(ValueError, "Not a seq-13 config-entry route"):
                await raw.rest("DELETE", path)
            session.assert_not_called()

    def test_governed_scenario_targets_the_owned_entry_before_waiting(self):
        source = inspect.getsource(seq13._availability_scenarios)
        self.assertLess(source.index('owned["config_entry"] = await raw.create_config_entry('),
                        source.index("for _ in range(100):"))
        self.assertIn('"config_entry_reads": sorted(CONFIG_ENTRY_READS & set(observed["commands"]))', source)
        self.assertEqual(inspect.getsource(seq13._run_disposable).count("raw = LaneRaw(configured)"), 1)


class DormancyTests(unittest.IsolatedAsyncioTestCase):
    async def test_lanes_refuse_unless_exactly_enabled(self):
        for value in (None, "", "dormant", "Enabled", "enabled ", "1", "true"):
            environ = {k: v for k, v in os.environ.items() if k != seq13.ENABLE_FLAG}
            if value is not None:
                environ[seq13.ENABLE_FLAG] = value
            for lane_key in seq13.LANES:
                with self.subTest(value=value, lane=lane_key), patch.dict(os.environ, environ, clear=True), \
                        patch.object(seq13, "_run_disposable") as inner, \
                        self.assertRaisesRegex(ValueError, "defined but not wired") as found:
                    await seq13.run_disposable(lane_key, None, expected_image="", core_version=CORE)
                inner.assert_not_called()
                self.assertEqual(found.exception.contract_missing_key, "seq13_" + lane_key)
                self.assertEqual(found.exception.contract_diagnostic["seq13_stage"], "binding")

    async def test_failure_retains_locations_without_exception_or_argument_content(self):
        private = "synthetic-do-not-retain"
        with patch.dict(os.environ, {seq13.ENABLE_FLAG: seq13.ENABLED}), \
                patch.object(seq13, "_run_disposable", side_effect=AssertionError(private)):
            with self.assertRaises(AssertionError) as found:
                await seq13.run_disposable("label_registry", private, expected_image=private, core_version=private)
        diagnostic = found.exception.contract_diagnostic
        self.assertEqual(set(diagnostic), {"seq13_source_lines", "seq13_stage"})
        self.assertTrue(all(type(line) is int for line in diagnostic["seq13_source_lines"]))
        self.assertNotIn(private, json.dumps(diagnostic))
        with patch.dict(os.environ, {seq13.ENABLE_FLAG: seq13.ENABLED}), self.assertRaises(KeyError) as found:
            await seq13.run_disposable(private, None, expected_image=private, core_version=private)
        self.assertEqual(found.exception.contract_missing_key, "seq13_unknown")

    def test_harness_hook_runs_only_on_the_exact_flag(self):
        source = inspect.getsource(real_ha_contract_tests)
        self.assertEqual(source.count('os.environ.get("REAL_HA_SEQ13_LANES") == "enabled"'), 1)
        self.assertEqual(source.count("import seq13_contract_acceptance"), 1)
        self.assertEqual(source.count("seq13.run_disposable("), 1)
        self.assertEqual(source.count("retained.update(seq13_results)"), 1)
        hook = source.index('os.environ.get("REAL_HA_SEQ13_LANES") == "enabled"')
        self.assertLess(source.index("import seq13_contract_acceptance"), source.index("seq13.run_disposable("))
        self.assertLess(hook, source.index("import seq13_contract_acceptance"))
        # The hook is only reachable after the entity metadata lane has passed.
        self.assertLess(source.index("entity_metadata_result"), hook)


class LaneGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_lane_refuses_wrong_lane_image_or_remote_host_before_any_client(self):
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
        hidden = ("HA_CONTRACT_LANE", "REAL_HA_ALARMO_IMAGE_RESULT", "REAL_HA_LABEL_REGISTRY_RESULT",
                  "REAL_HA_AVAILABILITY_EXPOSURE_RESULT")
        for lane_key, lane in seq13.LANES.items():
            for configured, image, version, lane_name in cases:
                environ = {k: v for k, v in os.environ.items() if k not in hidden}
                if lane_name is not None:
                    environ["HA_CONTRACT_LANE"] = lane_name
                with self.subTest(lane=lane_key, image=image, version=version, url=configured.ha_url), \
                        patch.dict(os.environ, environ, clear=True), \
                        patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                        self.assertRaisesRegex(ValueError, "Exact owned disposable seq-13 lane required"):
                    await seq13._run_disposable(lane, configured, expected_image=image, core_version=version,
                                                stage=["x"])
                client.assert_not_called()
            for unknown in [*NOT_SLOTS, *NOT_STRINGS]:
                environ = {k: v for k, v in os.environ.items() if k not in hidden}
                environ["HA_CONTRACT_LANE"] = binding["lane"]
                with self.subTest(lane=lane_key, unknown=unknown), patch.dict(os.environ, environ, clear=True), \
                        self.assertRaisesRegex(ValueError, "exact support-window Core slot"):
                    await seq13._run_disposable(lane, local, expected_image=binding["image"], core_version=unknown,
                                                stage=["x"])

    async def test_lane_refuses_a_wrong_slot_image_receipt_before_any_client(self):
        binding = alarmo.slot_binding(CORE)
        other = alarmo.slot_binding(OTHER_CORE)
        receipts = [
            {"result": "PASS", "index": other["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
            {"result": "PASS", "index": binding["image_index_digest"], "core_version": OTHER_CORE,
             "support_slot": CORE_SLOT},
            {"result": "FAIL", "index": binding["image_index_digest"], "core_version": CORE, "support_slot": CORE_SLOT},
        ]
        for lane_key, lane in seq13.LANES.items():
            for image in receipts:
                with tempfile.TemporaryDirectory() as directory:
                    image_path, output = Path(directory) / "image.json", Path(directory) / "seq13.json"
                    image_path.write_text(json.dumps(image))
                    environ = {"HA_CONTRACT_LANE": binding["lane"], "REAL_HA_ALARMO_IMAGE_RESULT": str(image_path),
                               lane["output_env"]: str(output)}
                    with self.subTest(lane=lane_key, image=image), patch.dict(os.environ, environ), \
                            patch("ha_mcp_engineering.clients.HomeAssistantWebSocketClient") as client, \
                            self.assertRaises(AssertionError):
                        await seq13._run_disposable(lane, SimpleNamespace(ha_url="http://127.0.0.1:8123"),
                                                    expected_image=binding["image"], core_version=CORE, stage=["x"])
                    client.assert_not_called()
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
