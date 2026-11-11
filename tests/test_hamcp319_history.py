"""HAMCP-319 section 7 ledger row 16 (``ha_get_history``) for ha-mcp 9.1.0. Offline.

Section 6.1 statistics projection ``ha-mcp-9.1.0-history-statistics-v1`` and
section 1.2 rule 3 state rows, through the real upstream read gateway with a
fake transport, plus the history-slices 9.1 parser row. Every call is an
8.5-shaped public call; each scenario is driven against the 8.5.0 and the
9.1.0 gateway. The fixtures are class S
(``tests/fixtures/ha_mcp_9_1_0/history/provenance.json``). Synthetic data and
credentials only; no live Home Assistant, no network.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from history_slices_sim import FakeReader, Recorder, history_result, us  # noqa: E402

from ha_mcp_engineering.history_slices import contracts as c  # noqa: E402
from ha_mcp_engineering.history_slices import service as service_module  # noqa: E402
from ha_mcp_engineering.history_slices.service import HistorySliceService  # noqa: E402
from ha_mcp_engineering.providers import upstream_history_9_1 as history  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from test_upstream_8_5_compatibility import capture  # noqa: E402

FIXTURES = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_1_0/history/responses.json").read_text("utf-8")
)
PROVENANCE = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_1_0/history/provenance.json").read_text("utf-8")
)
SCENARIOS = FIXTURES["scenarios"]
TOOL = "ha_get_history"
PINS = {"summary": False, "include_schema": False, "core_options": None}
CANARY = "SYNTHETIC-CANARY-319-HIST"
HA_TOKEN = "synthetic-ha-" + "credential-319"
HISTORY_SCENARIOS = (
    "history_minimal", "history_attributes", "history_fields_typo", "history_timezone_failed",
)
STATISTICS_SCENARIOS = (
    "statistics_default_types", "statistics_requested_units_unknown_and_empty",
    "statistics_unitless_timezone_failed",
)
DROPPED_91_KEYS = {"end", "unit_source", "unit_reason", "statistics_metadata", "last_reset"}
CHICAGO = timezone(timedelta(hours=-5))
NOW_MS = us("2026-10-19T13:30:00") // 1000
SLICE_ENTITY = "climate.mudroom_thermostat"
START, END = "2026-10-19T00:00:00Z", "2026-10-19T12:00:00Z"


def at(text: str) -> int:
    return us("2026-10-19T" + text)


def ok(payload):
    return {
        "isError": False,
        "structuredContent": deepcopy(payload),
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


def error(payload):
    return {"isError": True, "content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def scenario(name, version):
    entry = SCENARIOS[name]
    return deepcopy(entry["arguments"]), deepcopy(entry["ha_mcp_9_1_0" if version == "9.1.0" else "ha_mcp_8_5_0"])


def keys_anywhere(value) -> set:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= keys_anywhere(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= keys_anywhere(item)
        return found
    return set()


def body(result):
    """The ha-mcp ``data`` object inside the gateway success envelope."""

    return result["data"]["data"]


def meta(result):
    """The ha-mcp timezone ``metadata`` inside the gateway success envelope."""

    return result["data"]["metadata"]


def localized(text: str) -> str:
    return datetime.fromisoformat(text).astimezone(CHICAGO).isoformat()


def upstream_9_1(recorder: Recorder, arguments: dict) -> dict:
    """The 9.1 history result for the gateway's fixed slice query (class S shapes).

    Built from the shared recorder simulation: 9.1 ``format_history_response``
    (``history_response.py:49-74``) localizes ``lu``/``lc`` to the Home
    Assistant time zone (offset-aware); entity pagination follows
    ``build_pagination_metadata`` (``response_helpers.py:271-292``); the
    query params carry ``summary`` (``tools_history.py:505-519``) and the
    metadata is the 9.1 history form (``history_response.py:16-46``).
    """

    legacy = recorder.upstream(arguments["entity_ids"], arguments["start_time"],
                               arguments["end_time"], arguments["offset"], arguments["limit"])
    inner = legacy["data"]["data"]
    entity = inner["entities"][0]
    offset, limit = arguments["offset"], arguments["limit"]
    period = {"start": arguments["start_time"], "end": arguments["end_time"]}
    rows = [{"state": row["state"], "last_changed": localized(row["last_changed"]),
             "last_updated": localized(row["last_updated"])} for row in entity["states"]]
    return {
        "data": {
            "success": True, "source": "history",
            "entities": [{
                "entity_id": entity["entity_id"], "period": dict(period), "states": rows,
                "total_count": entity["total_count"], "offset": offset, "limit": limit,
                "count": len(rows), "has_more": entity["has_more"],
                "next_offset": offset + limit if entity["has_more"] else None,
            }],
            "period": dict(period),
            "query_params": {
                "minimal_response": True, "significant_changes_only": True,
                "limit": limit, "offset": offset, "order": "asc", "summary": False,
            },
        },
        "metadata": {
            "home_assistant_timezone": "America/Chicago",
            "timestamp_format": "ISO 8601 (America/Chicago)",
            "note": history._history_note_91("America/Chicago"),
        },
    }


def upstream_8_5(recorder: Recorder, arguments: dict) -> dict:
    """The shipped 8.5 result (identical to 8.6, see ``history_slices_sim``)."""

    return recorder.upstream(arguments["entity_ids"], arguments["start_time"], arguments["end_time"],
                             arguments["offset"], arguments["limit"])["data"]


class Transport(_GatewayTransport):
    def __init__(self, tools, version):
        super().__init__(tools, version=version)
        self.sent = []
        self.response = None
        self.responder = None

    async def execute_read(self, tool, arguments, **kwargs):
        exchange = await super().execute_read(tool, arguments, **kwargs)
        self.sent.append((tool, deepcopy(arguments)))
        response = self.responder(tool, arguments) if self.responder else self.response
        return replace(exchange, call_result=deepcopy(response))


class _Base(unittest.IsolatedAsyncioTestCase):
    async def gateway(self, version):
        transport = Transport(capture(version)["tools"], version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False),
            transport=transport,
        )
        await gateway.initialize(FastMCP(f"synthetic-hamcp319-hist-{version}"))
        return gateway, transport

    async def call(self, version, arguments, response=None):
        gateway, transport = await self.gateway(version)
        transport.response = response
        _, token = begin_request("synthetic-hamcp319-hist")
        try:
            result = json.loads(await gateway._registered_tool_registry.snapshot()[TOOL].run(arguments))
        finally:
            end_request(token)
        return result, transport

    async def run_scenario(self, name, version):
        arguments, payload = scenario(name, version)
        result, transport = await self.call(version, arguments, ok(payload))
        self.assertIs(result.get("success"), True, result)
        self.assertEqual(result["metadata"]["upstream_version"], version)
        self.assertEqual(transport.sent, [(TOOL, self.wire(version, arguments))])
        return result

    @staticmethod
    def wire(version, arguments):
        if version != "9.1.0":
            return arguments
        out = {**arguments, **PINS}
        if out.get("source") == "statistics" and "statistic_types" in out:
            out["statistic_types"] = history.parse_statistic_types(out["statistic_types"])
        return out

    def refused(self, result, transport, *, dispatched, category):
        self.assertIs(result.get("success"), False, result)
        self.assertEqual(result["metadata"]["upstream_dispatch_occurred"], dispatched, result)
        self.assertEqual(result["details"]["failure_category"], category, result)
        self.assertEqual(len([s for s in transport.sent if s[0] == TOOL]), 1 if dispatched else 0)
        self.assertNotIn(CANARY, json.dumps(result))


class Row16HistoryParityTests(_Base):
    """Rule 3 state rows: 9.1 rows reach the shipped 8.5 public result."""

    async def test_91_parity_row_16_history_states(self):
        for name in HISTORY_SCENARIOS:
            with self.subTest(name=name):
                r85 = await self.run_scenario(name, "8.5.0")
                r91 = await self.run_scenario(name, "9.1.0")
                self.assertEqual(r91["data"], r85["data"])
                self.assertEqual(set(r91["data"]), {"data", "metadata"})
                self.assertEqual(r91["metadata"]["completeness"], "complete", r91)
                self.assertEqual(r91["warnings"], r85["warnings"])
                # Field order is the 8.5 order, not 9.1's.
                for e91, e85 in zip(body(r91).get("entities", []), body(r85).get("entities", [])):
                    self.assertEqual(list(e91), list(e85))
                    for s91, s85 in zip(e91["states"], e85["states"]):
                        self.assertEqual(list(s91), list(s85))
                self.assertNotIn("summary", keys_anywhere(body(r91)))
                self.assertNotIn("summary", json.dumps(body(r91).get("query_params", {})))

    async def test_85_parity_row_16_history_states(self):
        for name in HISTORY_SCENARIOS:
            with self.subTest(name=name):
                arguments, payload = scenario(name, "8.5.0")
                result = await self.run_scenario(name, "8.5.0")
                # 8.5 is unchanged: the shipped result is passed through.
                self.assertEqual(result["data"], payload)
                self.assertEqual(result["metadata"]["completeness"], "complete")
                self.assertNotIn("summary", arguments)

    async def test_91_parity_row_16_timezone_failure_restores_8_5_metadata(self):
        r91 = await self.run_scenario("history_timezone_failed", "9.1.0")
        _, payload85 = scenario("history_timezone_failed", "8.5.0")
        self.assertEqual(body(r91)["entities"][0]["states"], payload85["data"]["entities"][0]["states"])
        self.assertEqual(meta(r91), payload85["metadata"])
        self.assertEqual(meta(r91)["note"], "Could not fetch Home Assistant timezone — timestamps are in UTC.")
        self.assertNotIn("Could not fetch Home Assistant timezone; timezone context", json.dumps(r91))

    async def test_91_parity_row_16_absent_attributes_disclosed_not_fabricated(self):
        r85 = await self.run_scenario("history_attributes_absent", "8.5.0")
        r91 = await self.run_scenario("history_attributes_absent", "9.1.0")
        rows91 = body(r91)["entities"][0]["states"]
        rows85 = body(r85)["entities"][0]["states"]
        self.assertEqual(rows85[1]["attributes"], {})  # 8.5 fabricated an empty mapping
        self.assertNotIn("attributes", rows91[1])  # 9.1: absent stays absent, never {}
        self.assertEqual(rows91[0], rows85[0])
        self.assertEqual({k: v for k, v in rows85[1].items() if k != "attributes"}, rows91[1])
        marker = ("sensor.synthetic_temperature: attributes unavailable for 1 of 2 returned states; "
                  "they were not reported, and they are not shown as empty.")
        self.assertEqual(body(r91)["warnings"], [marker])
        self.assertNotEqual(r91["metadata"]["completeness"], "complete")
        self.assertIn(history.ATTRIBUTES_UNAVAILABLE_WARNING, r91["warnings"])
        rest91 = {k: v for k, v in body(r91).items() if k not in {"entities", "warnings"}}
        rest85 = {k: v for k, v in body(r85).items() if k != "entities"}
        self.assertEqual(rest91, rest85)

    async def test_85_parity_row_16_absent_attributes_baseline(self):
        r85 = await self.run_scenario("history_attributes_absent", "8.5.0")
        self.assertEqual(r85["metadata"]["completeness"], "complete")
        self.assertNotIn("warnings", body(r85))


class Row16StatisticsParityTests(_Base):
    """Section 6.1 ``ha-mcp-9.1.0-history-statistics-v1``."""

    def expected_from_85(self, name, data85):
        """The 8.5 result with the section 6.1 unit difference applied."""

        expected = deepcopy(data85)
        payload91 = scenario(name, "9.1.0")[1]
        for entity, upstream in zip(expected["entities"], payload91["data"]["entities"]):
            entity["unit_of_measurement"] = upstream["unit_of_measurement"]
        return expected

    async def test_91_parity_row_16_statistics_projection(self):
        for name in STATISTICS_SCENARIOS:
            with self.subTest(name=name):
                r85 = await self.run_scenario(name, "8.5.0")
                r91 = await self.run_scenario(name, "9.1.0")
                data91 = deepcopy(body(r91))
                data85 = deepcopy(body(r85))
                data91.pop("warnings", None)
                data85.pop("warnings", None)
                self.assertEqual(data91, self.expected_from_85(name, data85))
                self.assertEqual(meta(r91), meta(r85))
                # 9.1 entity/row additions are dropped (time_range keeps 8.5's own end).
                self.assertFalse(DROPPED_91_KEYS & keys_anywhere(body(r91)["entities"]), body(r91))
                for e91, e85 in zip(body(r91)["entities"], body(r85)["entities"]):
                    self.assertEqual(list(e91), list(e85))
                    for s91, s85 in zip(e91["statistics"], e85["statistics"]):
                        self.assertEqual(list(s91), list(s85))

    async def test_91_parity_row_16_statistics_types_order_and_units(self):
        r91 = await self.run_scenario("statistics_default_types", "9.1.0")
        data = body(r91)
        self.assertEqual(data["statistic_types"], list(history.DEFAULT_STATISTIC_TYPES))
        self.assertEqual(data["query_params"]["statistic_types"], None)
        entity = data["entities"][0]
        self.assertEqual(entity["unit_of_measurement"], "°C")
        for row in entity["statistics"]:
            # Defaults in 8.5 order, taken only when present; 9.1 ``end`` dropped.
            self.assertEqual(list(row), ["start", "mean", "min", "max"])
            self.assertEqual(type(row["start"]), float)
        self.assertEqual(r91["metadata"]["completeness"], "complete")
        self.assertNotIn("warnings", data)

    async def test_91_parity_row_16_statistics_requested_missing_types_and_unknown_units(self):
        r85 = await self.run_scenario("statistics_requested_units_unknown_and_empty", "8.5.0")
        r91 = await self.run_scenario("statistics_requested_units_unknown_and_empty", "9.1.0")
        data = body(r91)
        self.assertEqual(data["statistic_types"], ["sum", "change"])
        energy, missing = data["entities"]
        for row in energy["statistics"]:
            self.assertEqual(list(row)[0], "start")
            self.assertLessEqual(set(row), {"start", "sum", "change"})
        self.assertIsNone(energy["unit_of_measurement"])
        self.assertEqual(missing["statistics"], [])
        empty = ("No statistics found for: sensor.synthetic_missing. These entities may not have "
                 "state_class attribute or may not have recorded data yet.")
        self.assertEqual(body(r85)["warnings"], [empty])
        self.assertEqual(data["warnings"], [
            "sensor.synthetic_energy: statistics output unit is unknown (statistics_metadata_missing); "
            "do not infer it from current state attributes.",
            "sensor.synthetic_missing: statistics output unit is unknown (statistics_metadata_missing); "
            "do not infer it from current state attributes.",
            empty,
        ])
        # Unknown units are an unavailable disclosure; the reset warning is withheld.
        self.assertNotEqual(r91["metadata"]["completeness"], "complete")
        self.assertIn(history.UNITS_UNKNOWN_WARNING, r91["warnings"])
        self.assertNotIn("last_reset", json.dumps(r91))

    async def test_91_parity_row_16_statistics_offset_and_pagination(self):
        arguments, payload = scenario("statistics_default_types", "9.1.0")
        arguments.update(offset=1, limit=1)
        entity = payload["data"]["entities"][0]
        entity["statistics"] = entity["statistics"][1:2]
        entity.update(total_count=len(scenario("statistics_default_types", "9.1.0")[1]["data"]["entities"][0]["statistics"]),
                      offset=1, limit=1, count=1)
        entity["has_more"] = entity["total_count"] > 2
        entity["next_offset"] = 2 if entity["has_more"] else None
        payload["data"]["query_params"].update(offset=1, limit=1)
        result, transport = await self.call("9.1.0", arguments, ok(payload))
        self.assertIs(result["success"], True, result)
        out = body(result)["entities"][0]
        self.assertEqual((out["offset"], out["limit"], out["count"], out["total_count"]),
                         (1, 1, 1, entity["total_count"]))
        self.assertEqual(body(result)["query_params"], {"statistic_types": None, "limit": 1, "offset": 1})
        self.assertEqual(transport.sent[0][1]["offset"], 1)

    async def test_85_parity_row_16_statistics_projection(self):
        for name in STATISTICS_SCENARIOS:
            with self.subTest(name=name):
                _, payload = scenario(name, "8.5.0")
                result = await self.run_scenario(name, "8.5.0")
                self.assertEqual(result["data"], payload)
                for entity in body(result)["entities"]:
                    self.assertIsNone(entity["unit_of_measurement"])

    async def test_91_parity_row_16_unitless_timezone_failure(self):
        r85 = await self.run_scenario("statistics_unitless_timezone_failed", "8.5.0")
        r91 = await self.run_scenario("statistics_unitless_timezone_failed", "9.1.0")
        self.assertIsNone(body(r91)["entities"][0]["unit_of_measurement"])
        self.assertEqual(body(r91)["statistic_types"], ["state", "mean"])
        self.assertEqual(r91["metadata"]["completeness"], "complete")
        self.assertEqual(r91["data"], r85["data"])


class Row16RefusalTests(_Base):
    """Pre-dispatch refusals (zero peer calls) and fail-closed shapes."""

    async def test_91_parity_row_16_invalid_period_refused_before_dispatch(self):
        arguments = deepcopy(FIXTURES["refusals_8_5_0"]["statistics_invalid_period"]["arguments"])
        result, transport = await self.call("9.1.0", arguments, ok({"data": {CANARY: True}}))
        self.refused(result, transport, dispatched=False, category="argument_validation")
        self.assertEqual(transport.sent, [])

    async def test_91_parity_row_16_unsupported_statistic_types_refused_before_dispatch(self):
        base = scenario("statistics_default_types", "9.1.0")[0]
        for value in ("", "bogus", "mean,bogus", ["mean", "last_reset"], [], "[]", "[\"bogus\"]"):
            with self.subTest(value=value):
                result, transport = await self.call(
                    "9.1.0", {**base, "statistic_types": value}, ok({"data": {CANARY: True}}),
                )
                self.refused(result, transport, dispatched=False, category="argument_validation")
                self.assertEqual(transport.sent, [])

    async def test_91_parity_row_16_history_source_pins_without_statistics_checks(self):
        arguments, payload = scenario("history_minimal", "9.1.0")
        result, transport = await self.call("9.1.0", {**arguments, "period": "fortnight"}, ok(payload))
        self.assertIs(result["success"], True, result)
        self.assertEqual(transport.sent[0][1], {**arguments, "period": "fortnight", **PINS})

    async def test_85_parity_row_16_invalid_period_refused_upstream(self):
        entry = deepcopy(FIXTURES["refusals_8_5_0"]["statistics_invalid_period"])
        result, transport = await self.call("8.5.0", entry["arguments"], error(entry["error"]))
        self.assertIs(result.get("success"), False, result)
        # 8.5 refused in its own client-side check, after Engineering dispatched.
        self.assertIs(result["metadata"]["upstream_dispatch_occurred"], True, result)
        self.assertEqual(transport.sent, [(TOOL, entry["arguments"])])

    async def assert_invalid_response(self, name, mutate):
        arguments, payload = scenario(name, "9.1.0")
        mutate(payload)
        result, transport = await self.call("9.1.0", arguments, ok(payload))
        self.refused(result, transport, dispatched=True, category="invalid_response")
        return result

    async def test_91_parity_row_16_unrecognized_history_shape_fails_closed(self):
        def entity(payload):
            return payload["data"]["entities"][0]

        cases = {
            "summary_entity": lambda p: entity(p).update(summary={"state_changes": 2}),
            "compressed_row_key": lambda p: entity(p)["states"][0].update(lu=1.0),
            "unknown_row_key": lambda p: entity(p)["states"][0].update(last_reported=CANARY),
            "naive_timestamp": lambda p: entity(p)["states"][0].update(last_updated="2026-10-19T05:30:00"),
            "attributes_not_mapping": lambda p: entity(p)["states"][0].update(attributes=[CANARY]),
            "attributes_null": lambda p: entity(p)["states"][1].update(attributes=None),
            "unknown_warning": lambda p: p.update(warnings=[CANARY]),
            "empty_warnings": lambda p: p.update(warnings=[]),
            "summary_true": lambda p: p["data"]["query_params"].update(summary=True),
            "summary_missing": lambda p: p["data"]["query_params"].pop("summary"),
            "metadata_note_changed": lambda p: p["metadata"].update(note=CANARY),
            "metadata_missing": lambda p: p.pop("metadata"),
            "extra_top_level": lambda p: p.update(schema={CANARY: 1}),
            "extra_data_key": lambda p: p["data"].update(summary_mode=CANARY),
            "wrong_entity": lambda p: entity(p).update(entity_id="sensor.synthetic_other"),
            "count_mismatch": lambda p: entity(p).update(count=7),
            "success_false": lambda p: p["data"].update(success=False),
            "statistics_source": lambda p: p["data"].update(source="statistics"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                await self.assert_invalid_response("history_attributes_absent", mutate)

    async def test_91_parity_row_16_unrecognized_statistics_shape_fails_closed(self):
        def entity(payload):
            return payload["data"]["entities"][0]

        cases = {
            "core_converter_unit": lambda p: entity(p).update(unit_source="core_converter"),
            "unit_source_unknown_value": lambda p: entity(p).update(unit_source=CANARY),
            "unknown_with_unit": lambda p: entity(p).update(unit_source="unknown"),
            "unit_reason_with_unit": lambda p: entity(p).update(unit_reason="statistics_are_unitless"),
            "row_string_value": lambda p: entity(p)["statistics"][0].update(mean=CANARY),
            "row_without_start": lambda p: entity(p)["statistics"][0].pop("start"),
            "period_mismatch": lambda p: entity(p).update(period="day"),
            "metadata_history_form": lambda p: p["metadata"].update(timestamp_format="ISO 8601 (UTC)"),
            "unit_warning_without_unknown_unit": lambda p: p.update(warnings=[
                "sensor.synthetic_temperature: statistics output unit is unknown "
                "(statistics_metadata_missing); do not infer it from current state attributes."]),
            "empty_warning_for_nonempty": lambda p: p.update(warnings=[
                "No statistics found for: sensor.synthetic_temperature. These entities may not have "
                "state_class attribute or may not have recorded data yet."]),
            "query_types_mismatch": lambda p: p["data"]["query_params"].update(statistic_types=["mean"]),
            "core_options_echo": lambda p: p["data"]["query_params"].update(core_options={"units": {}}),
            "entity_missing": lambda p: p["data"].update(entities=[]),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                await self.assert_invalid_response("statistics_default_types", mutate)

    async def test_91_parity_row_16_unknown_unit_requires_matching_warning(self):
        def drop_unit_warning(payload):
            payload["warnings"] = [w for w in payload["warnings"] if "sensor.synthetic_energy: statistics" not in w]

        await self.assert_invalid_response("statistics_requested_units_unknown_and_empty", drop_unit_warning)

    async def test_91_parity_row_16_typo_must_be_reported(self):
        await self.assert_invalid_response("history_fields_typo", lambda p: p.pop("warnings"))

    def test_91_parity_row_16_projection_registered_and_dispatch_pins(self):
        self.assertIs(upstream_reads_9_1._projector_tables()[TOOL][0], history.project)
        self.assertEqual(history.PROJECTION_ID, "ha-mcp-9.1.0-history-statistics-v1")
        self.assertEqual(history.dispatch_arguments({"entity_ids": "sensor.a"}),
                         {"entity_ids": "sensor.a", **PINS})
        # A caller value never overrides a pin.
        self.assertEqual(history.dispatch_arguments({"entity_ids": "sensor.a", "summary": True})["summary"], False)
        with self.assertRaises(ValueError):
            history.dispatch_arguments({"entity_ids": "sensor.a", "source": "statistics", "period": "decade"})

    def test_91_parity_row_16_completeness_reads_only_fixed_markers(self):
        unit = ("sensor.synthetic_energy: statistics output unit is unknown (statistics_metadata_missing); "
                "do not infer it from current state attributes.")
        self.assertEqual(history.completeness({"data": {"success": True}}), (False, []))
        self.assertEqual(history.completeness({"data": {"warnings": [unit, unit]}}),
                         (True, [history.UNITS_UNKNOWN_WARNING]))
        for payload in ({"data": {"warnings": [CANARY]}}, {"data": {"warnings": "x"}}, {"data": []}, None,
                        {"data": {"warnings": [{"text": CANARY}]}}):
            with self.subTest(payload=payload):
                self.assertEqual(history.completeness(payload), (True, [history.UNVERIFIED_WARNING]))

    def test_91_parity_row_16_fixture_provenance_covers_every_fixture(self):
        named = set(PROVENANCE["fixtures"])
        expected = {f"scenarios/{name}/{side}" for name in SCENARIOS for side in ("ha_mcp_9_1_0", "ha_mcp_8_5_0")}
        expected.add("refusals_8_5_0/statistics_invalid_period")
        self.assertEqual(named, expected)
        self.assertEqual({entry["class"] for entry in PROVENANCE["fixtures"].values()}, {"S"})


class HistorySlicesRowTests(_Base):
    """Rule 3: the history-slices 9.1 parser row beside the retained 8.5/8.6 rows."""

    async def slices_case(self, version):
        recorder = Recorder()
        recorder.add(at("00:00:00") - 5, "heat")
        recorder.add(at("01:00:00"), "idle")
        recorder.add(at("07:00:00"), "heat")
        gateway, transport = await self.gateway(version)
        build = upstream_9_1 if version == "9.1.0" else upstream_8_5
        transport.responder = lambda tool, arguments: ok(build(recorder, arguments))
        audit = Mock()
        reader = gateway.history_reader(audit=audit)
        self.assertIsNotNone(reader)
        self.assertEqual(reader.version, version)
        service = HistorySliceService(lambda: reader, known_secrets=(HA_TOKEN,), clock=lambda: NOW_MS)
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            page = await service.read({"entity_id": SLICE_ENTITY, "start": START, "end": END, "limit": 1})
            rest = await service.read({"entity_id": SLICE_ENTITY, "start": START, "end": END,
                                       "cursor": page["next_cursor"]})
        finally:
            end_request(token)
        got = [(c.parse_upstream_us(item["at"]), item["state"]) for p in (page, rest) for item in p["states"]]
        self.assertEqual(got, [(at("01:00:00"), "idle"), (at("07:00:00"), "heat")])
        self.assertEqual(page["provider"]["upstream_version"], version)
        self.assertIs(rest["has_more"], False)
        sent = [arguments for name, arguments in transport.sent if name == TOOL]
        self.assertEqual(len(sent), 3)
        fixed = {"entity_ids", "start_time", "end_time", "limit", "offset", "order", "source",
                 "minimal_response", "significant_changes_only"}
        for arguments in sent:
            if version == "9.1.0":
                self.assertEqual(set(arguments), fixed | set(PINS))
                self.assertEqual({k: arguments[k] for k in PINS}, PINS)
            else:
                self.assertEqual(set(arguments), fixed)
            self.assertEqual((arguments["entity_ids"], arguments["order"], arguments["source"],
                              arguments["minimal_response"]), (SLICE_ENTITY, "asc", "history", True))
        events = [call.args[0] for call in audit.write.call_args_list]
        self.assertEqual({e["upstream_version"] for e in events}, {version})
        self.assertFalse(any(e["fallback_occurred"] for e in events))
        return page, rest

    async def test_91_history_slices_positive(self):
        await self.slices_case("9.1.0")

    async def test_85_history_slices_positive(self):
        await self.slices_case("8.5.0")

    async def test_91_history_slices_unrecognized_shape_fails_closed(self):
        recorder = Recorder()
        recorder.add(at("01:00:00"), "idle")
        gateway, transport = await self.gateway("9.1.0")

        def responder(tool, arguments):
            payload = upstream_9_1(recorder, arguments)
            payload["data"]["entities"][0]["states"][0]["lu"] = CANARY
            return ok(payload)

        transport.responder = responder
        reader = gateway.history_reader(audit=Mock())
        service = HistorySliceService(lambda: reader, known_secrets=(HA_TOKEN,), clock=lambda: NOW_MS)
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        telemetry.core_dispatch_authorizer = lambda: True
        try:
            page = await service.read({"entity_id": SLICE_ENTITY, "start": START, "end": END})
        finally:
            end_request(token)
        # The compiled row 16 projection refuses the unrecognized row; the
        # slice reports the omission and claims no coverage.
        self.assertNotIn(CANARY, json.dumps(page))
        self.assertEqual(page["states"], [])
        self.assertEqual(page["omissions"], ["upstream_invalid_response"])
        self.assertIsNone(page["coverage"]["complete_until"])
        self.assertEqual(page["upstream_calls"], 1)

    def test_history_slices_parser_rows_match_reviewed_versions(self):
        self.assertEqual(set(service_module._PARSERS), set(c.REVIEWED_VERSIONS))
        self.assertIn("9.1.0", c.REVIEWED_VERSIONS)
        self.assertIs(service_module._PARSERS["8.5.0"], service_module._parse)
        self.assertIs(service_module._PARSERS["9.1.0"], service_module._parse_9_1)

    @staticmethod
    def envelope(states, *, version="9.1.0", completeness="complete"):
        result = history_result("sensor.synthetic_temperature", [], total=len(states), offset=0, limit=10)
        body(result)["entities"][0].update(states=states, count=len(states))
        result["metadata"].update(upstream_version=version, completeness=completeness)
        return result

    def test_91_history_slices_parser_expanded_rows(self):
        rows = [
            {"state": "21.5", "last_changed": "2026-10-19T05:30:00-05:00",
             "last_updated": "2026-10-19T05:30:00-05:00", "attributes": {"friendly_name": "Synthetic"}},
            {"state": "20.0", "last_changed": "2026-10-18T19:00:00-05:00",
             "last_updated": "2026-10-19T01:00:00-05:00"},
        ]
        parsed = service_module._parse_9_1(self.envelope(rows), "sensor.synthetic_temperature", 0)
        self.assertEqual(parsed, ([(us("2026-10-19T10:30:00"), "21.5"), (us("2026-10-19T06:00:00"), "20.0")],
                                  2, False))
        # Without attributes on every row; offset-aware localized times.
        bare = [{k: v for k, v in row.items() if k != "attributes"} for row in rows]
        self.assertEqual(service_module._parse_9_1(self.envelope(bare), "sensor.synthetic_temperature", 0)[0],
                         parsed[0])

    def test_91_history_slices_parser_refusals(self):
        good = {"state": "on", "last_changed": "2026-10-19T05:30:00-05:00",
                "last_updated": "2026-10-19T05:30:00-05:00"}
        entity = "sensor.synthetic_temperature"
        cases = {
            "compressed_key": {**good, "lu": 1.0},
            "unknown_key": {**good, "last_reported": good["last_updated"]},
            "attributes_empty_list": {**good, "attributes": []},
            "attributes_null": {**good, "attributes": None},
            "naive_last_changed": {**good, "last_changed": "2026-10-19T05:30:00"},
            "naive_last_updated": {**good, "last_updated": "2026-10-19T05:30:00"},
            "missing_last_changed": {k: v for k, v in good.items() if k != "last_changed"},
        }
        for label, row in cases.items():
            with self.subTest(case=label):
                self.assertEqual(service_module._parse_9_1(self.envelope([row]), entity, 0),
                                 "representation_invalid")
        # An 8.5/8.6 session never maps through the 9.1 row.
        for version in ("8.5.0", "8.6.0", None):
            with self.subTest(version=version):
                raw = self.envelope([good], version=version)
                self.assertEqual(service_module._parse_9_1(raw, entity, 0), "representation_invalid")
        self.assertEqual(service_module._parse_9_1([], entity, 0), "representation_invalid")
        failure = {"success": False, "details": {"failure_category": "transport_timeout"}}
        self.assertEqual(service_module._parse_9_1(failure, entity, 0), "upstream_transport_timeout")

    async def test_history_slices_version_without_parser_refused_with_zero_calls(self):
        for version, parsers in (("9.9.9", service_module._PARSERS),
                                 ("9.1.0", {k: v for k, v in service_module._PARSERS.items() if k != "9.1.0"})):
            with self.subTest(version=version):
                recorder = Recorder()
                recorder.add(at("01:00:00"), "idle")
                reader = FakeReader(recorder, version=version)
                service = HistorySliceService(lambda: reader, known_secrets=(HA_TOKEN,), clock=lambda: NOW_MS)
                telemetry, token = begin_request()
                telemetry.caller_id = "synthetic_authenticated_caller"
                try:
                    with patch.object(service_module, "_PARSERS", parsers):
                        with self.assertRaises(c.HistorySlicesError) as raised:
                            await service.read({"entity_id": SLICE_ENTITY, "start": START, "end": END})
                finally:
                    end_request(token)
                self.assertEqual(raised.exception.reason, "history_route_unavailable")
                self.assertEqual(recorder.calls, [])


if __name__ == "__main__":
    unittest.main()
