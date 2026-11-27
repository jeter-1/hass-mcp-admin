"""HAMCP-319 section 7 ledger row 16 (``ha_get_history``), re-pinned to ha-mcp 9.2.0. Offline.

Section 6.1 statistics projection ``ha-mcp-9.1.0-history-statistics-v1`` and
section 1.2 rule 3 state rows, through the real upstream read gateway with a
fake transport (the history-slices parser row is retired, HAMCP-511). Every call is an
8.5-shaped public call. HAMCP-437: each scenario is driven against the 9.2.0
gateway, whose ``upstream_history_9_2`` runs the unedited 9.1 projection on
the 9.1 shapes (equal in every real-Core 9.2.0 receipt,
``docs/evidence/hamcp371/s6``) and adds three counts. The shipped 8.5 public
result each 9.2.0 result must reach is the committed 8.5.0 side of the same
scenario, which the 8.5 gateway passed through unchanged. The fixtures are
class S (``tests/fixtures/ha_mcp_9_2_0/history/provenance.json``, cited at
ha-mcp 9.2.0 ``51e3aac7``; the 8.5.0 sides keep their 8.5.0 citations). Synthetic
data and credentials only; no live Home Assistant, no network.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402


from ha_mcp_engineering.providers import upstream_history_9_1 as history  # noqa: E402
from ha_mcp_engineering.providers import upstream_history_9_2 as history_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from test_ha_mcp_production_readmission import _GatewayTransport, _settings  # noqa: E402
from support.ha_mcp_captures import capture  # noqa: E402  (HAMCP-437)

FIXTURES = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_2_0/history/responses.json").read_text("utf-8")
)
PROVENANCE = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_2_0/history/provenance.json").read_text("utf-8")
)
SCENARIOS = FIXTURES["scenarios"]
TOOL = "ha_get_history"
PINS = {"summary": False, "include_schema": False, "core_options": None}
CANARY = "SYNTHETIC-CANARY-319-HIST"
HISTORY_SCENARIOS = (
    "history_minimal", "history_attributes", "history_fields_typo", "history_timezone_failed",
)
STATISTICS_SCENARIOS = (
    "statistics_default_types", "statistics_requested_units_unknown_and_empty",
    "statistics_unitless_timezone_failed",
)
DROPPED_91_KEYS = {"end", "unit_source", "unit_reason", "statistics_metadata", "last_reset"}
# HAMCP-437: the release the gateway cases drive, and its three count fields.
CURRENT = "9.2.0"
COUNTS = ("fields_withheld", "fields_dropped_by_policy", "upstream_warnings_replaced")
# A peer URL whose parts occur in no result (the default host ``upstream``
# would also redact ``upstream_warnings_replaced``).
PEER_URL = "http://synthetic-host-319:9583/synthetic-history-secret-319/mcp"


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
    side = "ha_mcp_9_2_0" if version in ("9.1.0", CURRENT) else "ha_mcp_8_5_0"
    return deepcopy(entry["arguments"]), deepcopy(entry[side])


def shipped_85(name):
    """The shipped 8.5 public result of a scenario (the 8.5 gateway passed it through)."""

    return scenario(name, "8.5.0")[1]


def projected(result):
    """The gateway ``data`` without the three 9.2.0 count fields."""

    return {k: v for k, v in result["data"].items() if k not in COUNTS}


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
            replace(_settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                    upstream_dashboard_mcp_url=PEER_URL),
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
        if version not in ("9.1.0", CURRENT):
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
    """Rule 3 state rows: 9.2.0 (9.1-shaped) rows reach the shipped 8.5 public result."""

    def assert_counts(self, result, *, withheld=0, replaced=0):
        self.assertEqual(set(result["data"]), {"data", "metadata", *COUNTS})
        self.assertEqual(result["data"]["fields_withheld"], withheld)
        self.assertEqual(result["data"]["upstream_warnings_replaced"], replaced)

    async def test_92_parity_row_16_history_states(self):
        for name in HISTORY_SCENARIOS:
            with self.subTest(name=name):
                r92 = await self.run_scenario(name, CURRENT)
                payload85 = shipped_85(name)
                self.assertEqual(projected(r92), payload85)
                self.assert_counts(r92)
                self.assertEqual(r92["metadata"]["completeness"], "complete", r92)
                self.assertEqual(r92["warnings"], [])
                # Field order is the 8.5 order, not 9.1's.
                for e92, e85 in zip(body(r92).get("entities", []), payload85["data"].get("entities", [])):
                    self.assertEqual(list(e92), list(e85))
                    for s92, s85 in zip(e92["states"], e85["states"]):
                        self.assertEqual(list(s92), list(s85))
                self.assertNotIn("summary", keys_anywhere(body(r92)))
                self.assertNotIn("summary", json.dumps(body(r92).get("query_params", {})))

    async def test_92_parity_row_16_timezone_failure_restores_8_5_metadata(self):
        r92 = await self.run_scenario("history_timezone_failed", CURRENT)
        payload85 = shipped_85("history_timezone_failed")
        self.assertEqual(body(r92)["entities"][0]["states"], payload85["data"]["entities"][0]["states"])
        self.assertEqual(meta(r92), payload85["metadata"])
        self.assertEqual(meta(r92)["note"], "Could not fetch Home Assistant timezone — timestamps are in UTC.")
        self.assertNotIn("Could not fetch Home Assistant timezone; timezone context", json.dumps(r92))

    async def test_92_parity_row_16_absent_attributes_disclosed_not_fabricated(self):
        r92 = await self.run_scenario("history_attributes_absent", CURRENT)
        data85 = shipped_85("history_attributes_absent")["data"]
        rows92 = body(r92)["entities"][0]["states"]
        rows85 = data85["entities"][0]["states"]
        self.assertEqual(rows85[1]["attributes"], {})  # 8.5 fabricated an empty mapping
        self.assertNotIn("attributes", rows92[1])  # 9.1 rows on 9.2.0: absent stays absent, never {}
        self.assertEqual(rows92[0], rows85[0])
        self.assertEqual({k: v for k, v in rows85[1].items() if k != "attributes"}, rows92[1])
        marker = ("sensor.synthetic_temperature: attributes unavailable for 1 of 2 returned states; "
                  "they were not reported, and they are not shown as empty.")
        self.assertEqual(body(r92)["warnings"], [marker])
        self.assertNotEqual(r92["metadata"]["completeness"], "complete")
        self.assertIn(history.ATTRIBUTES_UNAVAILABLE_WARNING, r92["warnings"])
        rest92 = {k: v for k, v in body(r92).items() if k not in {"entities", "warnings"}}
        rest85 = {k: v for k, v in data85.items() if k != "entities"}
        self.assertEqual(rest92, rest85)


class Row16StatisticsParityTests(_Base):
    """Section 6.1 ``ha-mcp-9.1.0-history-statistics-v1``, run by the 9.2.0 history row."""

    def expected_from_85(self, name, data85):
        """The 8.5 result with the section 6.1 unit difference applied."""

        expected = deepcopy(data85)
        payload = scenario(name, CURRENT)[1]
        for entity, upstream in zip(expected["entities"], payload["data"]["entities"]):
            entity["unit_of_measurement"] = upstream["unit_of_measurement"]
        return expected

    async def test_92_parity_row_16_statistics_projection(self):
        for name in STATISTICS_SCENARIOS:
            with self.subTest(name=name):
                r92 = await self.run_scenario(name, CURRENT)
                payload85 = shipped_85(name)
                data92 = deepcopy(body(r92))
                data85 = deepcopy(payload85["data"])
                data92.pop("warnings", None)
                data85.pop("warnings", None)
                self.assertEqual(data92, self.expected_from_85(name, data85))
                self.assertEqual(meta(r92), payload85["metadata"])
                self.assertEqual(r92["data"]["fields_withheld"], 0)
                # 9.1 entity/row additions are dropped (time_range keeps 8.5's own end).
                self.assertFalse(DROPPED_91_KEYS & keys_anywhere(body(r92)["entities"]), body(r92))
                for e92, e85 in zip(body(r92)["entities"], payload85["data"]["entities"]):
                    self.assertEqual(list(e92), list(e85))
                    for s92, s85 in zip(e92["statistics"], e85["statistics"]):
                        self.assertEqual(list(s92), list(s85))

    async def test_92_parity_row_16_statistics_types_order_and_units(self):
        r92 = await self.run_scenario("statistics_default_types", CURRENT)
        data = body(r92)
        self.assertEqual(data["statistic_types"], list(history.DEFAULT_STATISTIC_TYPES))
        self.assertEqual(data["query_params"]["statistic_types"], None)
        entity = data["entities"][0]
        self.assertEqual(entity["unit_of_measurement"], "°C")
        for row in entity["statistics"]:
            # Defaults in 8.5 order, taken only when present; 9.1 ``end`` dropped.
            self.assertEqual(list(row), ["start", "mean", "min", "max"])
            self.assertEqual(type(row["start"]), float)
        self.assertEqual(r92["metadata"]["completeness"], "complete")
        self.assertNotIn("warnings", data)

    async def test_92_parity_row_16_statistics_requested_missing_types_and_unknown_units(self):
        r92 = await self.run_scenario("statistics_requested_units_unknown_and_empty", CURRENT)
        data = body(r92)
        self.assertEqual(data["statistic_types"], ["sum", "change"])
        energy, missing = data["entities"]
        for row in energy["statistics"]:
            self.assertEqual(list(row)[0], "start")
            self.assertLessEqual(set(row), {"start", "sum", "change"})
        self.assertIsNone(energy["unit_of_measurement"])
        self.assertEqual(missing["statistics"], [])
        empty = ("No statistics found for: sensor.synthetic_missing. These entities may not have "
                 "state_class attribute or may not have recorded data yet.")
        self.assertEqual(shipped_85("statistics_requested_units_unknown_and_empty")["data"]["warnings"], [empty])
        self.assertEqual(data["warnings"], [
            "sensor.synthetic_energy: statistics output unit is unknown (statistics_metadata_missing); "
            "do not infer it from current state attributes.",
            "sensor.synthetic_missing: statistics output unit is unknown (statistics_metadata_missing); "
            "do not infer it from current state attributes.",
            empty,
        ])
        # Unknown units are an unavailable disclosure; the reset warning is withheld.
        self.assertNotEqual(r92["metadata"]["completeness"], "complete")
        self.assertIn(history.UNITS_UNKNOWN_WARNING, r92["warnings"])
        self.assertNotIn("last_reset", json.dumps(r92))
        self.assertEqual(r92["data"]["upstream_warnings_replaced"], 0)

    async def test_92_parity_row_16_statistics_offset_and_pagination(self):
        arguments, payload = scenario("statistics_default_types", CURRENT)
        arguments.update(offset=1, limit=1)
        entity = payload["data"]["entities"][0]
        entity["statistics"] = entity["statistics"][1:2]
        entity.update(total_count=len(scenario("statistics_default_types", CURRENT)[1]["data"]["entities"][0]["statistics"]),
                      offset=1, limit=1, count=1)
        entity["has_more"] = entity["total_count"] > 2
        entity["next_offset"] = 2 if entity["has_more"] else None
        payload["data"]["query_params"].update(offset=1, limit=1)
        result, transport = await self.call(CURRENT, arguments, ok(payload))
        self.assertIs(result["success"], True, result)
        out = body(result)["entities"][0]
        self.assertEqual((out["offset"], out["limit"], out["count"], out["total_count"]),
                         (1, 1, 1, entity["total_count"]))
        self.assertEqual(body(result)["query_params"], {"statistic_types": None, "limit": 1, "offset": 1})
        self.assertEqual(transport.sent[0][1]["offset"], 1)

    async def test_92_parity_row_16_unitless_timezone_failure(self):
        r92 = await self.run_scenario("statistics_unitless_timezone_failed", CURRENT)
        self.assertIsNone(body(r92)["entities"][0]["unit_of_measurement"])
        self.assertEqual(body(r92)["statistic_types"], ["state", "mean"])
        self.assertEqual(r92["metadata"]["completeness"], "complete")
        self.assertEqual(projected(r92), shipped_85("statistics_unitless_timezone_failed"))


class Row16RefusalTests(_Base):
    """Pre-dispatch refusals (zero peer calls) and fail-closed shapes."""

    async def test_92_parity_row_16_invalid_period_refused_before_dispatch(self):
        arguments = deepcopy(FIXTURES["refusals_8_5_0"]["statistics_invalid_period"]["arguments"])
        result, transport = await self.call(CURRENT, arguments, ok({"data": {CANARY: True}}))
        self.refused(result, transport, dispatched=False, category="argument_validation")
        self.assertEqual(transport.sent, [])

    async def test_92_parity_row_16_unsupported_statistic_types_refused_before_dispatch(self):
        base = scenario("statistics_default_types", CURRENT)[0]
        for value in ("", "bogus", "mean,bogus", ["mean", "last_reset"], [], "[]", "[\"bogus\"]"):
            with self.subTest(value=value):
                result, transport = await self.call(
                    CURRENT, {**base, "statistic_types": value}, ok({"data": {CANARY: True}}),
                )
                self.refused(result, transport, dispatched=False, category="argument_validation")
                self.assertEqual(transport.sent, [])

    async def test_92_parity_row_16_history_source_pins_without_statistics_checks(self):
        arguments, payload = scenario("history_minimal", CURRENT)
        result, transport = await self.call(CURRENT, {**arguments, "period": "fortnight"}, ok(payload))
        self.assertIs(result["success"], True, result)
        self.assertEqual(transport.sent[0][1], {**arguments, "period": "fortnight", **PINS})

    async def assert_invalid_response(self, name, mutate):
        arguments, payload = scenario(name, CURRENT)
        mutate(payload)
        result, transport = await self.call(CURRENT, arguments, ok(payload))
        self.refused(result, transport, dispatched=True, category="invalid_response")
        return result

    async def assert_withheld(self, name, mutate, absent):
        """9.2.0 (S9 item 7): an unreviewed key is withheld and counted, not a whole-read refusal."""

        arguments, payload = scenario(name, CURRENT)
        mutate(payload)
        result, transport = await self.call(CURRENT, arguments, ok(payload))
        self.assertIs(result.get("success"), True, result)
        self.assertGreaterEqual(result["data"]["fields_withheld"], 1, result)
        self.assertFalse(absent & keys_anywhere(result["data"]), result)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(len([s for s in transport.sent if s[0] == TOOL]), 1)
        return result

    async def test_92_parity_row_16_unrecognized_history_shape_fails_closed(self):
        def entity(payload):
            return payload["data"]["entities"][0]

        cases = {
            "summary_entity": lambda p: entity(p).update(summary={"state_changes": 2}),
            "naive_timestamp": lambda p: entity(p)["states"][0].update(last_updated="2026-10-19T05:30:00"),
            "attributes_not_mapping": lambda p: entity(p)["states"][0].update(attributes=[CANARY]),
            "attributes_null": lambda p: entity(p)["states"][1].update(attributes=None),
            "empty_warnings": lambda p: p.update(warnings=[]),
            "summary_true": lambda p: p["data"]["query_params"].update(summary=True),
            "summary_missing": lambda p: p["data"]["query_params"].pop("summary"),
            "metadata_note_changed": lambda p: p["metadata"].update(note=CANARY),
            "metadata_missing": lambda p: p.pop("metadata"),
            "wrong_entity": lambda p: entity(p).update(entity_id="sensor.synthetic_other"),
            "count_mismatch": lambda p: entity(p).update(count=7),
            "success_false": lambda p: p["data"].update(success=False),
            "statistics_source": lambda p: p["data"].update(source="statistics"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                await self.assert_invalid_response("history_attributes_absent", mutate)

    async def test_92_unreviewed_history_keys_withheld_and_counted(self):
        def entity(payload):
            return payload["data"]["entities"][0]

        # These four refused the whole 9.1 read; 9.2.0 withholds the key.
        cases = {
            "compressed_row_key": (lambda p: entity(p)["states"][0].update(lu=1.0), {"lu"}),
            "unknown_row_key": (lambda p: entity(p)["states"][0].update(last_reported=CANARY),
                                {"last_reported"}),
            "extra_top_level": (lambda p: p.update(schema={CANARY: 1}), {"schema"}),
            "extra_data_key": (lambda p: p["data"].update(summary_mode=CANARY), {"summary_mode"}),
        }
        for label, (mutate, absent) in cases.items():
            with self.subTest(case=label):
                await self.assert_withheld("history_attributes_absent", mutate, absent)

    async def test_92_unreviewed_history_warning_replaced_and_unverified(self):
        arguments, payload = scenario("history_attributes_absent", CURRENT)
        payload["warnings"] = [CANARY]
        result, transport = await self.call(CURRENT, arguments, ok(payload))
        self.assertIs(result.get("success"), True, result)
        self.assertEqual(result["data"]["upstream_warnings_replaced"], 1)
        self.assertNotEqual(result["metadata"]["completeness"], "complete")
        self.assertIn(history.UNVERIFIED_WARNING, result["warnings"])
        self.assertNotIn(CANARY, json.dumps(result))

    async def test_92_parity_row_16_unrecognized_statistics_shape_fails_closed(self):
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
            "entity_missing": lambda p: p["data"].update(entities=[]),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                await self.assert_invalid_response("statistics_default_types", mutate)

    async def test_92_core_options_echo_withheld_and_counted(self):
        # 9.1 refused the echoed core_options; 9.2.0 withholds and counts it.
        await self.assert_withheld(
            "statistics_default_types",
            lambda p: p["data"]["query_params"].update(core_options={"units": {}}), {"core_options"})

    async def test_92_parity_row_16_unknown_unit_requires_matching_warning(self):
        def drop_unit_warning(payload):
            payload["warnings"] = [w for w in payload["warnings"] if "sensor.synthetic_energy: statistics" not in w]

        await self.assert_invalid_response("statistics_requested_units_unknown_and_empty", drop_unit_warning)

    async def test_92_parity_row_16_typo_must_be_reported(self):
        await self.assert_invalid_response("history_fields_typo", lambda p: p.pop("warnings"))

    def test_91_parity_row_16_projection_registered_and_dispatch_pins(self):
        self.assertIs(upstream_reads_9_1._projector_tables()[TOOL][0], history.project)
        self.assertEqual(history.PROJECTION_ID, "ha-mcp-9.1.0-history-statistics-v1")
        # The 9.2.0 row runs its own time check and key filter, then the 9.1 projection.
        self.assertIs(history_9_2.PROJECTIONS[TOOL][0], history_9_2.project)
        self.assertEqual(history_9_2.dispatch_arguments({"entity_ids": "sensor.a"}),
                         {"entity_ids": "sensor.a", **PINS})
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

    def test_92_parity_row_16_fixture_provenance_covers_every_fixture(self):
        named = set(PROVENANCE["fixtures"])
        expected = {f"scenarios/{name}/{side}" for name in SCENARIOS for side in ("ha_mcp_9_2_0", "ha_mcp_8_5_0")}
        expected.add("refusals_8_5_0/statistics_invalid_period")
        self.assertEqual(named, expected)
        self.assertEqual({entry["class"] for entry in PROVENANCE["fixtures"].values()}, {"S"})


# HAMCP-511: HistorySlicesRowTests is retired. get_history_slices no longer
# maps through this row: it reads Core's /api/history/period natively
# (``history_slices/native.py``), so the gateway's slice reader, the
# per-release slice parsers and their envelopes are gone. Row 16 above still
# covers the delegated ``ha_get_history`` tool; the native read is covered by
# ``test_hamcp131_history_slices`` and ``test_hamcp511_native_history_slices``.


if __name__ == "__main__":
    unittest.main()
