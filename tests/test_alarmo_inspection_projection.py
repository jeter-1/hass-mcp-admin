"""Truthful null/empty/partial facts, scope joins, and shared processing bounds."""

import copy
import unittest
from unittest.mock import patch

from test_integration_inspection_contract import TARGET, fixture, setup_service
from ha_mcp_engineering.integration_inspection import contracts as c
from ha_mcp_engineering.integration_inspection.models import InspectionError
from ha_mcp_engineering.integration_inspection.projection import Budget, Projector
from ha_mcp_engineering.request_context import end_request


class ProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def inspect(self, data):
        service, client, _, _, token = setup_service(data)
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id=TARGET, limit=50)
        rows, page = list(report["records"]), report
        while page["pagination"]["has_more"]:
            page = await service.inspect(alarm_entity_id=TARGET, limit=50, cursor=page["pagination"]["next_cursor"])
            rows.extend(page["records"])
        return {**report, "records": rows}

    async def test_null_zero_false_and_empty_are_observed(self):
        report = await self.inspect(fixture())
        modes = [r for r in report["records"] if r["kind"] == "mode"]
        night = next(r for r in modes if r["mode"] == "armed_night")
        away = next(r for r in modes if r["mode"] == "armed_away")
        vacation = next(r for r in modes if r["mode"] == "armed_vacation")
        self.assertEqual((night["entry_time_seconds"]["status"], night["entry_time_seconds"]["value"]), ("observed", None))
        self.assertEqual(away["entry_time_seconds"]["value"], 0)
        self.assertIs(vacation["enabled"]["value"], False)
        always = next(r for r in report["records"] if r.get("configured_entity_id") == "binary_sensor.synthetic_always")
        self.assertEqual(always["configured_modes"]["value"], [])
        self.assertEqual(always["configured_mode_eligibility"][0]["result"], "eligible")

    async def test_empty_membership_only_for_complete_sensor_source(self):
        raw = fixture()
        raw["sensors"], raw["sensor_groups"] = {}, {}
        report = await self.inspect(raw)
        self.assertEqual(report["membership"]["outcome"], "empty")
        self.assertEqual(report["membership"]["total_in_scope"], 0)
        raw["sensors"] = InspectionError("source_unavailable")
        report = await self.inspect(raw)
        self.assertEqual(report["membership"]["outcome"], "unavailable")
        self.assertIsNone(report["membership"]["total_in_scope"])
        self.assertTrue(any(r["kind"] == "mode" for r in report["records"]))

    async def test_missing_invalid_and_boolean_durations_do_not_default(self):
        raw = fixture()
        mode = raw["areas"]["0"]["modes"]["armed_away"]
        mode.pop("entry_time")
        mode["exit_time"] = True
        mode["trigger_time"] = 2147483648
        report = await self.inspect(raw)
        row = next(r for r in report["records"] if r["kind"] == "mode" and r["area"]["id"] == "0" and r["mode"] == "armed_away")
        self.assertEqual(report["assessment"], "partial")
        for key in ("entry_time_seconds", "exit_time_seconds", "trigger_time_seconds"):
            self.assertIsNone(row[key]["value"])
            self.assertNotEqual(row[key]["status"], "observed")

    async def test_unknown_conjunct_and_known_false_exclusion(self):
        raw = fixture()
        raw["sensors"]["binary_sensor.synthetic_door"].pop("enabled")
        report = await self.inspect(raw)
        sensor = next(r for r in report["records"] if r.get("configured_entity_id") == "binary_sensor.synthetic_door")
        outcomes = {v["mode"]: v["result"] for v in sensor["configured_mode_eligibility"]}
        self.assertEqual(outcomes["armed_away"], "indeterminate")
        self.assertEqual(outcomes["armed_vacation"], "excluded")

    async def test_dangling_sensor_is_retained_in_unresolved_tail(self):
        raw = fixture()
        raw["sensors"]["binary_sensor.synthetic_door"]["area"] = "unresolved_area"
        report = await self.inspect(raw)
        self.assertEqual(report["records"][-1]["configured_entity_id"], "binary_sensor.synthetic_door")
        self.assertEqual(report["membership"]["outcome"], "partial")
        self.assertIsNone(report["membership"]["total_in_scope"])
        self.assertIn("dangling_area", [g["reason"] for g in report["gaps"]])

    async def test_group_conflict_and_failed_group_evidence_are_not_ungrouped(self):
        raw = fixture()
        raw["sensor_groups"]["duplicate"] = {**raw["sensor_groups"]["synthetic_group"], "group_id": "duplicate"}
        report = await self.inspect(raw)
        self.assertIn("group_conflict", [g["reason"] for g in report["gaps"]])
        raw["sensor_groups"] = InspectionError("source_unavailable")
        report = await self.inspect(raw)
        for row in report["records"]:
            if row["kind"] == "sensor":
                self.assertEqual(row["reported_group_id"]["status"], "unavailable")

    async def test_partial_and_malformed_source_preserve_independent_useful_facts(self):
        report = await self.inspect(fixture("alarmo_partial.json"))
        self.assertEqual(report["assessment"], "partial")
        self.assertEqual(report["membership"]["configured_members_retained"], 4)
        self.assertTrue(any(r["kind"] == "mode" for r in report["records"]))

    def test_sensor_limit_exact_and_plus_one(self):
        example = fixture()["sensors"]["binary_sensor.synthetic_door"]
        for count in (512, 513):
            raw = {f"binary_sensor.synthetic_{i}": {**example, "entity_id": f"binary_sensor.synthetic_{i}"} for i in range(count)}
            p = Projector().project(c.ReadKind.SENSORS, raw, "sensors")
            self.assertEqual(p.retained, min(count, 512))
            self.assertEqual(p.truncated, count > 512)
            self.assertEqual(p.omitted, max(0, count - 512))

    def test_shared_budget_counts_malformed_members_and_rejected_branches(self):
        budget = Budget(nodes=c.MAX_NODES - 4)
        p = Projector(budget=budget)
        result = p.project(c.ReadKind.SENSORS, {f"binary_sensor.synthetic_{i}": [] for i in range(100)}, "sensors")
        self.assertEqual(budget.nodes, c.MAX_NODES)
        self.assertTrue(result.truncated)
        following = p.project(c.ReadKind.GENERAL, fixture()["general"], "general")
        self.assertFalse(following.available)
        self.assertEqual(budget.nodes, c.MAX_NODES)

    def test_excluded_opaque_depth_is_never_walked_and_malformed_allowed_shape_is_bounded(self):
        opaque = []
        for _ in range(100):
            opaque = [opaque] * 5
        raw = fixture()["general"]
        raw["mqtt"] = opaque
        budget = Budget()
        projected = Projector(budget=budget).project(c.ReadKind.GENERAL, raw, "general")
        self.assertLess(budget.nodes, 20)
        self.assertNotIn("mqtt", projected.value)
        raw["master"] = opaque
        projected = Projector(budget=budget).project(c.ReadKind.GENERAL, raw, "general")
        self.assertIn("invalid_field", [g.reason for g in projected.gaps])
        self.assertLess(budget.nodes, 40)

    def test_shared_edge_limit_and_unknown_modes_do_not_shorten_selectors(self):
        raw = {"synthetic_a": {"group_id": "synthetic_a", "timeout": 0, "event_count": 2,
                             "entities": [f"binary_sensor.synthetic_{i}" for i in range(2048)]},
               "synthetic_b": {"group_id": "synthetic_b", "timeout": 0, "event_count": 2,
                             "entities": ["binary_sensor.extra"]}}
        budget = Budget()
        p = Projector(budget=budget).project(c.ReadKind.SENSOR_GROUPS, raw, "sensor_groups")
        self.assertEqual(budget.edges, 2048)
        self.assertIsNone(p.value["synthetic_b"]["entities"])
        self.assertTrue(p.truncated)
        sensors = fixture()["sensors"]
        sensors["binary_sensor.synthetic_door"]["modes"] = ["armed_away", "unreviewed"]
        p = Projector().project(c.ReadKind.SENSORS, sensors, "sensors")
        self.assertIsNone(p.value["binary_sensor.synthetic_door"]["modes"])

    def test_area_group_registry_boundaries_and_depth_rejection_charge(self):
        data = fixture()
        cases = ((c.ReadKind.AREAS, c.MAX_AREAS, data["areas"]["0"], "area_id", "area_"),
                 (c.ReadKind.SENSOR_GROUPS, c.MAX_GROUPS, data["sensor_groups"]["synthetic_group"], "group_id", "group_"),
                 (c.ReadKind.ENTITY_REGISTRY, c.MAX_REGISTRY,
                  data["entity_registry"]["binary_sensor.synthetic_door"], "entity_id", "binary_sensor.row_"))
        for kind, maximum, example, identity, prefix in cases:
            for count in (maximum, maximum + 1):
                with self.subTest(kind=kind, count=count):
                    raw = {prefix + str(i): {**example, identity: prefix + str(i)} for i in range(count)}
                    result = Projector().project(kind, raw, kind.value)
                    self.assertEqual(result.retained, maximum)
                    self.assertEqual(result.omitted, count - maximum)
                    self.assertEqual(result.truncated, count > maximum)
        budget = Budget(nodes=c.MAX_NODES - 1)
        self.assertFalse(budget.charge(c.MAX_DEPTH + 1))
        self.assertEqual(budget.nodes, c.MAX_NODES)
        self.assertFalse(budget.charge(0))

    async def test_final_manifest_failure_preserves_facts_and_drift_refuses(self):
        for failure in (InspectionError("timeout"), "drift"):
            service, client, _, _, token = setup_service()
            self.addCleanup(end_request, token)
            async def final(kind, kwargs):
                if len(client.calls) == 9:
                    if failure == "drift":
                        client.values["manifest"]["version"] = "other"
                    else:
                        raise failure
            client.hook = final
            if failure == "drift":
                with self.assertRaises(InspectionError) as found:
                    await service.inspect(alarm_entity_id=TARGET)
                self.assertEqual(found.exception.reason, "identity_drift")
                self.assertEqual(service.snapshots, {})
            else:
                report = await service.inspect(alarm_entity_id=TARGET)
                self.assertEqual(report["freshness"]["status"], "unverified")
                self.assertEqual(report["assessment"], "partial")
                self.assertGreater(len(report["records"]), 0)
                self.assertIsNone(report["truncation"]["omitted_count"])

    async def test_missing_target_area_does_not_certify_empty_scope(self):
        raw = fixture()
        raw["areas"].pop("0")
        raw["sensors"], raw["sensor_groups"] = {}, {}
        service, _, _, _, token = setup_service(raw)
        self.addCleanup(end_request, token)
        report = await service.inspect(alarm_entity_id="alarm_control_panel.synthetic_0")
        self.assertEqual(report["membership"]["outcome"], "partial")
        self.assertIsNone(report["membership"]["total_in_scope"])
        self.assertIn("scope_incomplete", [g["reason"] for g in report["gaps"]])
