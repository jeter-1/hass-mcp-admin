"""HAMCP-371 D5-B: the 9.2.0 blueprint read refuses unreviewed keys under ``data``.

``upstream_reads_9_2_blueprint.validate`` is reached from one exact-9.2.0 gate
in ``upstream_blueprint.validate_result``, which the gateway now calls with the
policy entry. These tests cover:

* the module: the reviewed list and get key sets, refusal of any other key
  under ``data``, ``config`` and ``yaml`` left open, and no payload mutation;
* the real gateway, with the stock registry capture of each version and the
  committed ``r12-blueprints`` receipts (profile A without the component,
  profile B with it) plus a synthetic get-mode answer per profile: on 9.2.0 a
  key injected under ``data`` is refused as ``invalid_response`` in list and
  get mode; on 9.1.2 and 8.5.0 the same payload passes as shipped;
* the top-level case: a key at the payload top level, outside ``data``, is
  not covered by D5-B and passes unchanged on every version, 9.2.0 included
  (HAMCP-402 owns it).

Get mode has no 9.2.0 receipt: its answers here are synthetic, shaped from the
source review recorded in the module docstring. Synthetic values only; no
endpoint is contacted.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_blueprint  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2_blueprint as bp_9_2  # noqa: E402
import test_hamcp371_real_core_replay as replay  # noqa: E402

INJECTED = "hamcp371_injected_key"
LIST_ARGS = {"domain": "automation"}
PATH = "homeassistant/motion_light.yaml"
GET_ARGS = {"domain": "automation", "path": PATH}
VERSIONS = ("9.2.0", "9.1.2", "8.5.0")


def _get_answer(component: bool) -> dict:
    """A synthetic get-mode answer with every reviewed key the profile writes."""

    data = {
        "path": PATH, "domain": "automation", "name": "Motion-activated Light",
        "metadata": {"name": "Motion-activated Light", "description": "hamcp371 synthetic",
                     "source_url": None, "author": None, "domain": "automation",
                     "homeassistant": None},
        "inputs": {"motion_entity": {"name": "Motion Sensor", "selector": {"entity": {}}}},
        "used_by": [],
    }
    if component:
        data.update(config={"hamcp371_user_key": {"deep": 1}}, yaml="hamcp371_user: 1\n",
                    yaml_source="component")
    return {"success": True, "data": data}


def _receipts():
    """{profile: (receipt result, list-mode structuredContent)} for 9.2.0."""

    out = {}
    for profile in ("A", "B"):
        calls = {c["id"]: rec for c, rec in replay.public_calls(f"{profile}/9.2.0")}
        result = calls["r12-blueprints"]["exchange"]["response"]["result"]
        out[profile] = (result, result["structuredContent"])
    return out


def _under(content: dict, value="x") -> dict:
    copy = deepcopy(content)
    copy["data"][INJECTED] = value
    return copy


def _top(content: dict) -> dict:
    copy = deepcopy(content)
    copy[INJECTED] = "x"
    return copy


class Module(unittest.TestCase):

    def test_releases_and_key_sets(self):
        self.assertEqual(bp_9_2.RELEASES, frozenset({"9.2.0"}))
        self.assertEqual(bp_9_2.LIST_KEYS, frozenset({"domain", "count", "blueprints"}))
        self.assertEqual(bp_9_2.GET_KEYS, frozenset({
            "path", "domain", "name", "metadata", "inputs", "used_by",
            "config", "yaml", "yaml_source"}))

    def test_list_keys_equal_every_9_2_0_receipt(self):
        for profile, (_result, content) in _receipts().items():
            with self.subTest(profile=profile):
                self.assertEqual(set(content["data"]), bp_9_2.LIST_KEYS)
                bp_9_2.validate(content, "list")

    def test_reviewed_sets_pass_and_nothing_is_mutated(self):
        for component in (True, False):
            answer = _get_answer(component)
            before = deepcopy(answer)
            bp_9_2.validate(answer, "get")
            self.assertEqual(answer, before)
        content = _receipts()["B"][1]
        before = deepcopy(content)
        bp_9_2.validate(content, "list")
        self.assertEqual(content, before)

    def test_any_other_key_under_data_is_refused(self):
        cases = [("list", _receipts()["A"][1]), ("get", _get_answer(True)),
                 ("get", _get_answer(False))]
        # A get-only key in list mode and a list-only key in get mode count too.
        extra = {"list": ("path", "metadata", INJECTED), "get": ("count", "blueprints", INJECTED)}
        for action, content in cases:
            for key in extra[action]:
                with self.subTest(action=action, key=key):
                    payload = deepcopy(content)
                    payload["data"][key] = 1
                    before = deepcopy(payload)
                    with self.assertRaisesRegex(ValueError, "blueprint_read_response_key_unreviewed"):
                        bp_9_2.validate(payload, action)
                    self.assertEqual(payload, before)

    def test_config_and_yaml_stay_open(self):
        answer = _get_answer(True)
        answer["data"]["config"] = {INJECTED: {"any": ["user", {"content": 1}]}}
        answer["data"]["yaml"] = f"{INJECTED}: open\n"
        bp_9_2.validate(answer, "get")


class SharedGate(unittest.TestCase):
    """``validate_result`` reaches the module on 9.2.0 bindings only."""

    def entry(self, version):
        adapter = next(a for a, b in upstream_blueprint.ADAPTER_BINDINGS.items() if b[0] == version)
        return type("Entry", (), {"argument_restrictions": (adapter,)})()

    def test_every_other_binding_never_calls_the_module(self):
        payload = _under(_receipts()["A"][1])
        for adapter, binding in upstream_blueprint.ADAPTER_BINDINGS.items():
            if binding[0] in bp_9_2.RELEASES:
                continue
            with self.subTest(version=binding[0]), patch.object(
                    bp_9_2, "validate", side_effect=AssertionError("9.2.0 only")) as spy:
                upstream_blueprint.validate_result(payload, LIST_ARGS, self.entry(binding[0]))
                spy.assert_not_called()

    def test_9_2_0_calls_the_module_after_the_shared_checks(self):
        with patch.object(bp_9_2, "validate", side_effect=AssertionError("unreached")) as spy:
            with self.assertRaisesRegex(ValueError, "blueprint_read_response_identity_invalid"):
                upstream_blueprint.validate_result(
                    {"success": True, "data": {"domain": "script"}}, LIST_ARGS, self.entry("9.2.0"))
            spy.assert_not_called()
        with self.assertRaisesRegex(ValueError, "blueprint_read_response_key_unreviewed"):
            upstream_blueprint.validate_result(
                _under(_receipts()["A"][1]), LIST_ARGS, self.entry("9.2.0"))


class GatewayReplay(unittest.TestCase):
    """The real gateway on each version's stock registry capture."""

    @classmethod
    def setUpClass(cls):
        cls.receipts = _receipts()
        cls.results = {}
        for version in VERSIONS:
            cases, keys = [], []
            for profile, (result, content) in cls.receipts.items():
                get = _get_answer(profile == "B")
                for name, args, payload in (
                        ("list", LIST_ARGS, content), ("list_under", LIST_ARGS, _under(content)),
                        ("list_top", LIST_ARGS, _top(content)),
                        ("get", GET_ARGS, get), ("get_under", GET_ARGS, _under(get)),
                        ("get_top", GET_ARGS, _top(get))):
                    cases.append(("ha_get_blueprint", args, replay._with(result, payload)))
                    keys.append((profile, name))
            outputs = replay.replay(version, cases)
            cls.results[version] = dict(zip(keys, outputs))

    def out(self, version, profile, name):
        return self.results[version][(profile, name)]

    def test_the_reviewed_answers_pass_on_every_version(self):
        for version in VERSIONS:
            for profile in ("A", "B"):
                for name, action in (("list", "list"), ("get", "get")):
                    with self.subTest(version=version, profile=profile, mode=name):
                        out, sent = self.out(version, profile, name)
                        self.assertIs(out["success"], True, out.get("details"))
                        self.assertEqual(sent[0][1]["action"], action)

    def test_a_key_under_data_is_refused_on_9_2_0(self):
        for profile in ("A", "B"):
            for name in ("list_under", "get_under"):
                with self.subTest(profile=profile, mode=name):
                    out, sent = self.out("9.2.0", profile, name)
                    self.assertIs(out["success"], False)
                    self.assertEqual(out["details"]["failure_category"], "invalid_response")
                    self.assertEqual(len(sent), 1)
                    self.assertNotIn(INJECTED, json.dumps(out))

    def test_the_same_key_passes_as_before_on_9_1_2_and_8_5_0(self):
        for version in ("9.1.2", "8.5.0"):
            for profile in ("A", "B"):
                for name in ("list_under", "get_under"):
                    with self.subTest(version=version, profile=profile, mode=name):
                        out, _sent = self.out(version, profile, name)
                        self.assertIs(out["success"], True, out.get("details"))
                        self.assertIn(INJECTED, json.dumps(out))

    def test_the_top_level_case_is_unchanged_on_every_version(self):
        # Outside ``data``; D5-B does not cover it (HAMCP-402). Same result on
        # 9.2.0 as on the shipped versions.
        for profile in ("A", "B"):
            for name in ("list_top", "get_top"):
                shipped = self.out("9.1.2", profile, name)[0]
                for version in VERSIONS:
                    with self.subTest(version=version, profile=profile, mode=name):
                        out, _sent = self.out(version, profile, name)
                        self.assertIs(out["success"], True, out.get("details"))
                        self.assertIn(INJECTED, out["data"])
                        self.assertEqual(sorted(out["data"]), sorted(shipped["data"]))


if __name__ == "__main__":
    unittest.main()
