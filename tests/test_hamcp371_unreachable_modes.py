"""HAMCP-371 S10 (plan row 2f, 21:36 record item 16 F1): unreachable modes.

ha-mcp 9.2.0 adds a helpers ``describe`` mode and integration
``include_schema`` and ``include_subentry_schema`` flags. Engineering keeps
them unreachable on 9.2.0:

* the 9.2 adapter pins ``describe`` off and the platform integration read
  builds a fixed wire shape, so neither flag is ever sent; a public call that
  names one is refused before any upstream call;
* a describe-shaped or schema-shaped result fed back through the 9.2.0
  helpers projection has ``read_only``, ``secret`` and ``later_forms``
  withheld and counted (the integration read keeps its shipped rebuild, which
  never returns them);
* an oversized helpers result fails with ``response_too_large``, not a
  truncated success.

Committed real-Core receipts and synthetic values only; no Home Assistant.
"""
from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_platform_reads as platform_reads  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry  # noqa: E402
import test_hamcp371_real_core_replay as replay_case  # noqa: E402

HELPERS = "ha_config_list_helpers"
INTEGRATION = "ha_get_integration"
DESCRIBE_KEYS = ("read_only", "secret", "later_forms")
SCHEMA_FLAGS = ("include_schema", "include_subentry_schema")
REGISTRY = load_reviewed_upstream_release_registry()


def _entry(name):
    return REGISTRY.by_version["9.2.0"].policy.by_name[name]


def _receipt(call_id):
    for call, rec in replay_case.public_calls("A/9.2.0"):
        if call["id"] == call_id:
            return call, rec["exchange"]["response"]["result"]
    raise AssertionError(call_id)


def _all_keys(value):
    return replay_case.all_keys(value, set())


class DispatchNeverSendsTheModes(unittest.TestCase):

    def test_helpers_describe_is_pinned_off(self):
        entry = _entry(HELPERS)
        self.assertTrue(reads_9_2.is_adapter(entry))
        call, _result = _receipt("r09-helpers")
        for arguments in ({}, call["public_arguments"], {"helper_type": "input_number"}):
            with self.subTest(arguments=arguments):
                built = reads_9_2.read_arguments(entry, arguments)
                self.assertIs(built["describe"], False)
                self.assertFalse(set(built) & set(DESCRIBE_KEYS))

    def test_helpers_describe_cannot_be_requested(self):
        entry = _entry(HELPERS)
        for value in (True, False):
            with self.subTest(describe=value), self.assertRaises(ValueError):
                reads_9_2.read_arguments(entry, {"describe": value})

    def test_integration_schema_flags_are_never_sent(self):
        entry = _entry(INTEGRATION)
        self.assertTrue(platform_reads.is_platform_adapter(entry))
        call, _result = _receipt("r26-integrations")
        for arguments in ({}, call["public_arguments"], {"domain": "synthetic_domain"}):
            with self.subTest(arguments=arguments):
                built = platform_reads.read_arguments(entry, arguments)
                self.assertFalse(set(built) & set(SCHEMA_FLAGS))
                self.assertIs(built["include_options"], False)

    def test_integration_schema_flags_cannot_be_requested(self):
        entry = _entry(INTEGRATION)
        for flag in SCHEMA_FLAGS:
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                platform_reads.read_arguments(entry, {flag: True})

    def test_gateway_refuses_before_any_upstream_call(self):
        _call, helpers = _receipt("r09-helpers")
        _call, integration = _receipt("r26-integrations")
        cases = [(HELPERS, {"describe": True}, helpers)]
        cases += [(INTEGRATION, {flag: True}, integration) for flag in SCHEMA_FLAGS]
        for (tool, arguments, _result), (out, sent) in zip(cases, replay_case.replay("9.2.0", cases)):
            with self.subTest(tool=tool, arguments=arguments):
                self.assertEqual(sent, [])
                self.assertIs(out["success"], False)
                self.assertEqual(out["details"]["failure_category"], "argument_validation")


class DescribeShapedResultsAreWithheld(unittest.TestCase):

    def _describe_shaped(self, result):
        content = deepcopy(result["structuredContent"])
        for row in content["helpers"]:
            row.update({"read_only": True, "secret": "synthetic-not-a-secret-371",
                        "later_forms": [{"step": "synthetic"}]})
        content["read_only"] = False
        return replay_case._with(result, content)

    def test_projection_withholds_and_counts(self):
        call, result = _receipt("r09-helpers")
        shaped = self._describe_shaped(result)["structuredContent"]
        pair = reads_9_2._projection(HELPERS)
        projected = pair[0](deepcopy(shaped), call["public_arguments"])
        self.assertFalse(_all_keys(projected) & set(DESCRIBE_KEYS))
        # The contract walk also counts names inside a withheld value: the three
        # describe keys plus ``step`` inside ``later_forms``.
        self.assertEqual(projected["fields_withheld"], 4)
        self.assertEqual(projected["fields_dropped_by_policy"], 0)
        self.assertEqual(projected["upstream_warnings_replaced"], 0)
        baseline = pair[0](deepcopy(result["structuredContent"]), call["public_arguments"])
        self.assertEqual(baseline["fields_withheld"], 0)

    def test_gateway_returns_the_read_without_the_describe_keys(self):
        call, result = _receipt("r09-helpers")
        [(out, sent)] = replay_case.replay(
            "9.2.0", [(HELPERS, call["public_arguments"], self._describe_shaped(result))])
        self.assertEqual(sent, [(HELPERS, call["wire_arguments"])])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertFalse(_all_keys(out) & set(DESCRIBE_KEYS))
        self.assertNotIn("synthetic-not-a-secret-371", json.dumps(out))
        self.assertEqual(out["data"]["fields_withheld"], 4)

    def test_integration_rebuild_never_returns_the_schema_keys(self):
        call, result = _receipt("r26-integrations")
        content = deepcopy(result["structuredContent"])
        for row in content["entries"]:
            row.update({"read_only": True, "secret": "synthetic-not-a-secret-371",
                        "later_forms": [], "schema": {"type": "object"}})
        [(out, sent)] = replay_case.replay(
            "9.2.0", [(INTEGRATION, call["public_arguments"], replay_case._with(result, content))])
        self.assertEqual(sent, [(INTEGRATION, call["wire_arguments"])])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertFalse(_all_keys(out) & (set(DESCRIBE_KEYS) | {"schema"}))
        self.assertNotIn("synthetic-not-a-secret-371", json.dumps(out))


class OversizedResultFailsClosed(unittest.TestCase):

    def test_oversized_helpers_list_is_response_too_large(self):
        call, result = _receipt("r09-helpers")
        content = deepcopy(result["structuredContent"])
        template = content["helpers"][0]
        # 20 rows of 4000 characters exceed the row's response bound while each
        # string stays below the sanitizer's string cap (test_hamcp319_parity_config).
        content["helpers"] = [{**template, "id": f"h{i}", "name": "x" * 4000} for i in range(20)]
        content.update({"count": 20, "total_count": 20, "limit": 20, "has_more": False})
        content.pop("next_offset", None)
        arguments = {**call["public_arguments"], "limit": 20}
        [(out, sent)] = replay_case.replay("9.2.0", [(HELPERS, arguments, replay_case._with(result, content))])
        self.assertEqual(len(sent), 1)
        self.assertIs(out["success"], False)
        self.assertEqual(out["details"]["failure_category"], "response_too_large")
        self.assertNotIn("x" * 4000, json.dumps(out))
        self.assertLess(len(json.dumps(out)), 20000)


if __name__ == "__main__":
    unittest.main()
