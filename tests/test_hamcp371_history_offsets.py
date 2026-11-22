"""HAMCP-371 S9: ha_get_history on exact ha-mcp 9.2.0 (21:36 record, items 5-12).

9.2.0 resolves an ISO start_time or end_time without an offset in Home
Assistant local time, where 9.1 forced UTC (tools_history.py at 51e3aac7).
These tests pin the 9.2.0 history module (upstream_history_9_2) and its
gateway path:

* accepted times (full ISO with an explicit offset or Z, including the local
  -06:00 case, and the exact upstream relative forms) are forwarded unchanged;
* offset-free and date-only times refuse before dispatch with the existing
  invalid_request and the fixed failure_category history_time_offset_required
  (R1, item 5); unparseable times keep the generic invalid_request
  (argument_validation, 22:59 record item 7);
* HAMCP-437: only 9.2.0 is admitted (D1); the 9.1.2 pass-through case is
  retired;
* the history-slices route keeps working on 9.2.0;
* unknown keys are withheld and counted, not a whole-response refusal (item
  7); policy drops are counted apart (item 10); an unreviewed upstream warning
  is replaced by a fixed code and counted (item 12);
* on the shipped 9.1 synthetic scenarios the 9.2.0 projection equals the 9.1
  projection plus the three Engineering count fields.

Offline and deterministic; loopback transports and synthetic values only.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.history_slices import contracts as slice_contracts  # noqa: E402
from ha_mcp_engineering.history_slices.service import HistorySliceService  # noqa: E402
from ha_mcp_engineering.providers import upstream_history_9_1 as history_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_history_9_2 as history_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
import test_hamcp319_history as h319  # noqa: E402
import test_hamcp371_real_core_replay as real  # noqa: E402

TOOL = "ha_get_history"
CATEGORY = "history_time_offset_required"
MESSAGE = (
    "start_time and end_time need a full date and time with an explicit UTC offset, "
    "for example 2026-11-10T08:00:00-06:00 or 2026-11-10T14:00:00Z."
)
ENTITY = "sensor.synthetic_temperature"
CONTRACT_ROW = real.READS[TOOL]
ADDED = ("fields_withheld", "fields_dropped_by_policy", "upstream_warnings_replaced")
INJECTED = "hamcp371_s9_injected_key"
CANARY = "hamcp371 s9 unlisted upstream warning canary"

ACCEPTED = (
    "2026-11-10T08:00:00-06:00",          # the "local" case: explicit local offset
    "2026-11-10T08:00:00+02:00",
    "2026-11-10T14:00:00Z",
    "2026-11-10T14:00:00+00:00",
    "2026-11-10T14:00:00.123456+00:00",   # the history-slices form
    "2026-11-10T14:00-06:00",
    "30min", "24h", "7d", "2w", "1m", " 24H ",
)
OFFSET_REQUIRED = (
    "2026-11-10T08:00:00",                # offset-free
    "2026-11-10",                         # date-only
    "2026-11-10T08:00:00+0000",           # offset without a colon
    "2026-11-10 08:00:00+00:00",          # space separator
    "20261110T080000Z",                   # basic format
    "2026-11-10T08:00:00.1234567+00:00",  # beyond microseconds is not ISO here
)
UNPARSEABLE = ("yesterday-ish", "", "now", "2026-13-40T99:00:00Z", "24 hours", "99999999999999999999w")


def _call(arguments, version="9.2.0", result=None):
    """One public ha_get_history call through the real gateway -> (output, sent)."""
    payload = result if result is not None else {"isError": False, "structuredContent": {}}
    [(out, sent)] = real.replay(version, [(TOOL, arguments, payload)])
    return out, sent


def _structured(content):
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(content)}],
            "structuredContent": content}


class TimeClassification(unittest.TestCase):
    def test_accepted_times_forward_unchanged(self):
        for value in ACCEPTED:
            for key in ("start_time", "end_time"):
                with self.subTest(value=value, key=key):
                    arguments = {"entity_ids": ENTITY, key: value}
                    out = history_9_2.dispatch_arguments(dict(arguments))
                    self.assertEqual(out, history_9_1.dispatch_arguments(dict(arguments)))
                    self.assertEqual(out[key], value)

    def test_omitted_times_are_accepted(self):
        self.assertEqual(history_9_2.dispatch_arguments({"entity_ids": ENTITY}),
                         history_9_1.dispatch_arguments({"entity_ids": ENTITY}))

    def test_offset_free_and_date_only_times_need_an_offset(self):
        for value in OFFSET_REQUIRED:
            for key in ("start_time", "end_time"):
                with self.subTest(value=value, key=key):
                    with self.assertRaises(history_9_2.HistoryTimeOffsetRequired) as caught:
                        history_9_2.dispatch_arguments({"entity_ids": ENTITY, key: value})
                    self.assertEqual(str(caught.exception), CATEGORY)

    def test_unparseable_times_keep_the_generic_refusal(self):
        for value in (*UNPARSEABLE, 1792368000, ["24h"]):
            for key in ("start_time", "end_time"):
                with self.subTest(value=value, key=key):
                    with self.assertRaises(ValueError) as caught:
                        history_9_2.dispatch_arguments({"entity_ids": ENTITY, key: value})
                    self.assertNotIsInstance(caught.exception, history_9_2.HistoryTimeOffsetRequired)

    def test_an_unparseable_bound_wins_over_an_offset_free_one(self):
        for start, end in (("2026-11-10T08:00:00", "yesterday-ish"),
                           ("yesterday-ish", "2026-11-10T08:00:00")):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError) as caught:
                    history_9_2.dispatch_arguments({"entity_ids": ENTITY, "start_time": start,
                                                    "end_time": end})
                self.assertNotIsInstance(caught.exception, history_9_2.HistoryTimeOffsetRequired)

    def test_9_1_refusals_still_apply_after_the_time_check(self):
        with self.assertRaises(ValueError):
            history_9_2.dispatch_arguments({"entity_ids": ENTITY, "source": "statistics",
                                            "period": "fortnight", "start_time": "24h"})


class GatewayTimeRefusal(unittest.TestCase):
    def test_offset_free_time_refuses_before_dispatch_with_the_fixed_category(self):
        for value in ("2026-11-10T08:00:00", "2026-11-10"):
            for key in ("start_time", "end_time"):
                with self.subTest(value=value, key=key):
                    out, sent = _call({"entity_ids": ENTITY, "start_time": "24h", key: value})
                    self.assertIs(out["success"], False)
                    self.assertEqual(sent, [])
                    self.assertEqual(out["error_code"], "invalid_request")
                    self.assertEqual(out["details"]["failure_category"], CATEGORY)
                    self.assertEqual(out["message"], MESSAGE)
                    self.assertIs(out["retryable"], False)
                    self.assertIs(out["metadata"]["upstream_dispatch_occurred"], False)
                    # The fixed message (asserted above) carries its own example
                    # date; nothing else may echo the refused value.
                    rest = {k: v for k, v in out.items() if k != "message"}
                    self.assertNotIn(value, json.dumps(rest))

    def test_unparseable_time_keeps_argument_validation(self):
        for value in ("yesterday-ish", "now"):
            with self.subTest(value=value):
                out, sent = _call({"entity_ids": ENTITY, "start_time": value})
                self.assertEqual(sent, [])
                self.assertEqual(out["error_code"], "invalid_request")
                self.assertEqual(out["details"]["failure_category"], "argument_validation")
                self.assertNotEqual(out["message"], MESSAGE)

    def test_accepted_times_reach_the_wire_unchanged(self):
        receipt = next(r for _s, c, r in real._cases_920(("A/9.2.0",)) if c["id"] == "r16-history")
        for value in ("2026-11-10T08:00:00-06:00", "2026-11-10T14:00:00Z", "24h"):
            with self.subTest(value=value):
                _out, sent = _call({"entity_ids": ENTITY, "start_time": value}, result=receipt)
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0][0], TOOL)
                self.assertEqual(sent[0][1]["start_time"], value)

    def test_the_category_is_registered_only_for_the_9_2_module(self):
        self.assertIn(CATEGORY, gateway_module._FAILURE_CATEGORIES)
        self.assertEqual(gateway_module._public_failure(CATEGORY), ("invalid_request", False))
        self.assertEqual(gateway_module._safe_failure_message(CATEGORY, TOOL), MESSAGE)
        self.assertNotEqual(gateway_module._safe_failure_message("argument_validation", TOOL), MESSAGE)
        self.assertFalse(hasattr(history_9_1, "HistoryTimeOffsetRequired"))


class HistorySlices920(h319._Base):
    async def test_920_history_slices_positive(self):
        self.assertIn("9.2.0", slice_contracts.REVIEWED_VERSIONS)
        recorder = h319.Recorder()
        recorder.add(h319.at("00:00:00") - 5, "heat")
        recorder.add(h319.at("01:00:00"), "idle")
        recorder.add(h319.at("07:00:00"), "heat")
        gateway, transport = await self.gateway("9.2.0")
        transport.responder = lambda tool, arguments: h319.ok(h319.upstream_9_1(recorder, arguments))
        audit = Mock()
        reader = gateway.history_reader(audit=audit)
        self.assertIsNotNone(reader)
        self.assertEqual(reader.version, "9.2.0")
        service = HistorySliceService(lambda: reader, known_secrets=(h319.HA_TOKEN,),
                                      clock=lambda: h319.NOW_MS)
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            page = await service.read({"entity_id": h319.SLICE_ENTITY, "start": h319.START,
                                       "end": h319.END, "limit": 1})
            rest = await service.read({"entity_id": h319.SLICE_ENTITY, "start": h319.START,
                                       "end": h319.END, "cursor": page["next_cursor"]})
        finally:
            end_request(token)
        got = [(slice_contracts.parse_upstream_us(item["at"]), item["state"])
               for p in (page, rest) for item in p["states"]]
        self.assertEqual(got, [(h319.at("01:00:00"), "idle"), (h319.at("07:00:00"), "heat")])
        self.assertEqual(page["provider"]["upstream_version"], "9.2.0")
        sent = [arguments for name, arguments in transport.sent if name == TOOL]
        self.assertEqual(len(sent), 3)
        for arguments in sent:
            self.assertEqual({k: arguments[k] for k in h319.PINS}, h319.PINS)
            self.assertTrue(gateway_module._HISTORY_TIME.fullmatch(arguments["start_time"]))
            self.assertTrue(gateway_module._HISTORY_TIME.fullmatch(arguments["end_time"]))


class ReviewedKeySet(unittest.TestCase):
    def test_module_sets_equal_the_output_key_contract(self):
        self.assertEqual(CONTRACT_ROW["projection"], "closed")
        self.assertEqual(history_9_2.ADMITTED, frozenset(CONTRACT_ROW["admitted"]))
        self.assertEqual(history_9_2.POLICY_DROPS, frozenset(CONTRACT_ROW["policy_drops"]))
        self.assertEqual(history_9_2.OPEN, frozenset(CONTRACT_ROW["open_containers"]))
        self.assertEqual(history_9_2.COUNT_CAP, real.CONTRACT["rules"]["count_cap"])
        self.assertEqual(history_9_2.REPLACED_WARNING, real.REPLACED)
        # The history read adds the three counts; engineering_notes (S10) is zone and services only.
        self.assertEqual(set(ADDED), set(real.ADDED) - {"engineering_notes"})
        self.assertEqual(CONTRACT_ROW["reviewed_warnings"], [])

    def test_9_1_scenarios_project_identically_plus_the_counts(self):
        for name in (*h319.HISTORY_SCENARIOS, *h319.STATISTICS_SCENARIOS):
            with self.subTest(scenario=name):
                arguments, payload = h319.scenario(name, "9.1.0")
                expected = history_9_1.project(deepcopy(payload), arguments)
                got = history_9_2.project(deepcopy(payload), arguments)
                counts = {key: got.pop(key) for key in ADDED}
                self.assertEqual(got, expected)
                self.assertEqual(counts["fields_withheld"], 0)
                self.assertEqual(counts["upstream_warnings_replaced"], 0)
                self.assertEqual(history_9_2.completeness(got), history_9_1.completeness(expected))

    def test_policy_drops_are_counted_apart_from_unreviewed_keys(self):
        arguments, payload = h319.scenario("statistics_requested_units_unknown_and_empty", "9.1.0")
        got = history_9_2.project(deepcopy(payload), arguments)
        drops = real.generator.names(payload, TOOL) & history_9_2.POLICY_DROPS
        self.assertTrue(drops)
        self.assertEqual(got["fields_dropped_by_policy"], len(drops))
        self.assertEqual(got["fields_withheld"], 0)

    def test_unknown_keys_are_withheld_and_counted_once_per_name(self):
        arguments, payload = h319.scenario("history_attributes", "9.1.0")
        base = history_9_2.project(deepcopy(payload), arguments)
        injected = deepcopy(payload)
        injected[INJECTED] = "top"
        injected["data"][INJECTED] = "data"
        injected["data"]["entities"][0][INJECTED] = "entity"
        injected["data"]["entities"][0]["states"][0][INJECTED] = "row"
        injected["data"]["entities"][0]["states"][0]["second_" + INJECTED] = "row"
        injected["metadata"][INJECTED] = "metadata"
        got = history_9_2.project(injected, arguments)
        self.assertNotIn(INJECTED, json.dumps(got))
        self.assertEqual(got["fields_withheld"], 2)
        got.pop("fields_withheld")
        base.pop("fields_withheld")
        self.assertEqual(got, base)

    def test_keys_inside_open_containers_pass_uncounted(self):
        arguments, payload = h319.scenario("history_attributes", "9.1.0")
        injected = deepcopy(payload)
        row = next(r for r in injected["data"]["entities"][0]["states"] if "attributes" in r)
        row["attributes"][INJECTED] = "open"
        got = history_9_2.project(injected, arguments)
        self.assertIn(INJECTED, json.dumps(got))
        self.assertEqual(got["fields_withheld"], 0)

    def test_the_withheld_count_is_capped(self):
        arguments, payload = h319.scenario("history_minimal", "9.1.0")
        injected = deepcopy(payload)
        injected["data"].update({f"{INJECTED}_{i}": i for i in range(1200)})
        got = history_9_2.project(injected, arguments)
        self.assertEqual(got["fields_withheld"], history_9_2.COUNT_CAP)

    def test_a_structurally_novel_admitted_key_still_fails_closed(self):
        arguments, payload = h319.scenario("history_minimal", "9.1.0")
        injected = deepcopy(payload)
        injected["data"]["entities"][0]["period"]["state"] = "unexpected"
        with self.assertRaises(ValueError):
            history_9_2.project(injected, arguments)

    def test_non_dict_payload_fails_closed(self):
        for payload in (None, [], "text"):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    history_9_2.project(payload, {"entity_ids": ENTITY})


class UpstreamWarnings(unittest.TestCase):
    def test_an_unreviewed_warning_is_replaced_and_counted(self):
        for extra in ([CANARY], [CANARY, CANARY + " two"], [7]):
            with self.subTest(extra=extra):
                arguments, payload = h319.scenario("history_minimal", "9.1.0")
                injected = deepcopy(payload)
                injected["warnings"] = list(extra)
                got = history_9_2.project(injected, arguments)
                self.assertNotIn(CANARY, json.dumps(got))
                self.assertEqual(got["data"]["warnings"], [history_9_2.REPLACED_WARNING])
                self.assertEqual(got["upstream_warnings_replaced"], len(extra))
                self.assertEqual(history_9_2.completeness(got),
                                 (True, [history_9_1.UNVERIFIED_WARNING]))

    def test_reviewed_warnings_pass_beside_a_replaced_one(self):
        arguments, payload = h319.scenario("history_timezone_failed", "9.1.0")
        expected = history_9_1.project(deepcopy(payload), arguments)
        injected = deepcopy(payload)
        injected["warnings"] = list(injected["warnings"]) + [CANARY]
        got = history_9_2.project(injected, arguments)
        self.assertEqual(got["metadata"], expected["metadata"])
        self.assertEqual(got["data"]["warnings"],
                         expected["data"].get("warnings", []) + [history_9_2.REPLACED_WARNING])
        self.assertEqual(got["upstream_warnings_replaced"], 1)

    def test_a_repeated_single_use_warning_is_replaced(self):
        arguments, payload = h319.scenario("history_timezone_failed", "9.1.0")
        injected = deepcopy(payload)
        injected["warnings"] = list(injected["warnings"]) * 2
        got = history_9_2.project(injected, arguments)
        self.assertEqual(got["upstream_warnings_replaced"], 1)

    def test_statistics_warnings_keep_the_9_1_templates(self):
        arguments, payload = h319.scenario("statistics_requested_units_unknown_and_empty", "9.1.0")
        expected = history_9_1.project(deepcopy(payload), arguments)
        got = history_9_2.project(deepcopy(payload), arguments)
        self.assertEqual(got["upstream_warnings_replaced"], 0)
        self.assertEqual(got["data"].get("warnings"), expected["data"].get("warnings"))

    def test_an_empty_or_malformed_warning_list_still_fails_closed(self):
        for value in ([], "text", {"a": 1}):
            with self.subTest(value=value):
                arguments, payload = h319.scenario("history_minimal", "9.1.0")
                injected = deepcopy(payload)
                injected["warnings"] = value
                with self.assertRaises(ValueError):
                    history_9_2.project(injected, arguments)


class EchoedBounds(unittest.TestCase):
    def test_an_offset_free_echoed_bound_fails_closed(self):
        arguments, payload = h319.scenario("history_minimal", "9.1.0")
        for path in (("data", "period"), ("data", "entities", 0, "period")):
            with self.subTest(path=path):
                injected = deepcopy(payload)
                target = injected
                for step in path:
                    target = target[step]
                target["start"] = "2026-10-19T00:00:00"
                with self.assertRaises(ValueError):
                    history_9_2.project(injected, arguments)

    def test_an_offset_free_statistics_time_range_fails_closed(self):
        arguments, payload = h319.scenario("statistics_default_types", "9.1.0")
        injected = deepcopy(payload)
        injected["data"]["time_range"]["end"] = "2026-10-19T12:00:00"
        with self.assertRaises(ValueError):
            history_9_2.project(injected, arguments)

    def test_real_920_receipts_project_with_local_offsets(self):
        cases = [(c, r) for _s, c, r in real._cases_920(("A/9.2.0", "B/9.2.0"))
                 if c["public_tool"] == TOOL and c["id"] != "v16-history-offset-free"]
        self.assertTrue(cases)
        outputs = real.replay("9.2.0", [(TOOL, c["public_arguments"], r) for c, r in cases])
        for (call, _r), (out, sent) in zip(cases, outputs):
            with self.subTest(call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(sent, [(call["upstream_tool"], call["wire_arguments"])])
                self.assertEqual(out["data"]["fields_withheld"], 0)
                self.assertEqual(out["data"]["upstream_warnings_replaced"], 0)


if __name__ == "__main__":
    unittest.main()
