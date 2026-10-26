"""HAMCP-181 PR2 review 1 (M1): ``dispatched_at`` is the exact send time.

OD-6(ii): the published send time is the instant the provider client issues
the mutating request, captured at the send boundary inside the gateway, never
at gateway entry and never the intent commit time.  Each case inserts a delay
inside the real gateway, between its entry and its client send, and records
the synthetic clock when the synthetic transport receives the write:

* the reviewer's regression: a configuration update and a configuration
  delete, one child each;
* a multi-child configuration plan whose children are sent at different
  times: each child stores its own send time and the parent states its basis
  (``earliest_child_send_time``);
* a multi-child WebSocket registry plan (069B availability), the same way.

The time stays ``null`` until the send, the intent commit time is recorded
separately, and every surface reads the one stored value.  No case is sent
twice.  Every provider, registry and clock is synthetic and offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.governance.configuration_delete import (  # noqa: E402
    ConfigurationDeleteGateway,
)
from ha_mcp_engineering.governance.entity_availability_exposure import (  # noqa: E402
    EntityAvailabilityExposureGateway,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)

from tests.hamcp069b_fixtures import (  # noqa: E402
    WRITE_COMMANDS,
    AvailabilityExposureServiceTestCase,
)
from tests.hamcp155_fixtures import CHECK_CONFIG, automation_config  # noqa: E402
from tests.test_hamcp181_terminal_outcome_t2 import FACTS, T2Harness, unwrap  # noqa: E402

# The reviewer's probe: 11 seconds between gateway entry and the client send.
ENTRY_TO_SEND = timedelta(seconds=11)
SCOPED_FACTS = FACTS + ("dispatched_at_scope", "dispatch_intent_recorded_at")


def surfaces(service, created, result):
    plan = unwrap(service.get_plan(created["plan_id"]))
    [listed] = [
        item for item in service.list_plans(None, 10)["plans"]
        if item["plan_id"] == created["plan_id"]
    ]
    [row] = [
        item for item in service.list_execution_tasks()["tasks"]
        if item["task_id"] == result["task_id"]
    ]
    view = service.get_execution_task(result["task_id"])
    return {
        "apply": result,
        "apply.execution_task": result["execution_task"],
        "apply.plan.execution_task": result["plan"]["execution_task"],
        "get_change_plan.execution_task": plan["execution_task"],
        "list_change_plans.execution_task": listed["execution_task"],
        "list_execution_tasks": row,
        "get_execution_task": view,
    }


def per_child_send_times(view):
    """The stored per-child send times, keyed by child ordinal."""

    summary = view["verification_summary"]
    stored = summary[df.SEND_TIME_SUMMARY_KEY]
    by_child = {entry["child_execution_id"]: entry["ordinal"] for entry in summary["children"]}
    return {by_child[child_id]: value for child_id, value in stored.items()}


class SendTimeCase:
    """Shared assertions over a task whose sends were observed."""

    def assert_in_flight_unsent(self, task_ids):
        # Before the client send, no surface carries a send time.
        for item in self.service.list_execution_tasks()["tasks"]:
            if item["task_id"] in task_ids or not task_ids:
                self.assertIsNone(item["dispatched_at"], item)

    def assert_one_value_everywhere(self, created, result, expected):
        views = surfaces(self.service, created, result)
        reference = {key: views["get_execution_task"].get(key, "<absent>")
                     for key in SCOPED_FACTS}
        self.assertEqual(reference["dispatched_at"], expected)
        self.assertEqual(reference["dispatched_at_basis"], df.DISPATCHED_AT_PROVIDER_CALL_START)
        self.assertEqual(reference["dispatched_at_scope"], df.DISPATCHED_AT_SCOPE_PARENT)
        self.assertIsNotNone(reference["dispatch_intent_recorded_at"])
        self.assertLess(reference["dispatch_intent_recorded_at"], expected)
        for label, surface in views.items():
            with self.subTest(surface=label):
                for key in SCOPED_FACTS:
                    if key in surface:
                        self.assertEqual(surface[key], reference[key], (label, key))
                self.assertEqual(surface["dispatched_at"], expected, label)
        return views["get_execution_task"]


class ConfigurationSendBoundaryTests(SendTimeCase, T2Harness):
    """The reviewer's regression through the real configuration gateways."""

    def observe_rest(self, methods):
        self.sent_at: list[str] = []
        original = self.config_rest.request

        async def request(method, path, **kwargs):
            if method in methods and (method, str(path)) != CHECK_CONFIG:
                self.sent_at.append(self.clock[0].isoformat())
            return await original(method, path, **kwargs)

        return mock.patch.object(self.config_rest, "request", request)

    def delay_inside(self, owner, name, delays):
        original = getattr(owner, name)
        harness = self
        calls = iter(delays)

        async def delayed(gateway, *args, **kwargs):
            # Inside the gateway, before its client sends: still unsent.
            harness.assert_in_flight_unsent(())
            harness.entries.append(harness.clock[0].isoformat())
            harness.clock[0] = harness.clock[0] + next(calls)
            return await original(gateway, *args, **kwargs)

        self.entries: list[str] = []
        return mock.patch.object(owner, name, delayed)

    def intent_entry(self):
        return datetime.fromisoformat(self.entries[0])

    async def run_delayed(self, family):
        created = await self.planned(family)
        if family == "delete":
            delay = self.delay_inside(ConfigurationDeleteGateway, "delete", [ENTRY_TO_SEND])
            observed = self.observe_rest({"DELETE"})
        else:
            delay = self.delay_inside(
                ConfigurationResourceGateway, "_write_rest_configuration", [ENTRY_TO_SEND]
            )
            observed = self.observe_rest({"POST"})
        with delay, observed:
            result = await self.apply(created)
        return created, result

    async def test_update_publishes_the_send_time_not_gateway_entry(self):
        created, result = await self.run_delayed("update")
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.sent_at), 1)
        self.assertEqual(len(self.provider_writes()), 1)
        view = self.assert_one_value_everywhere(created, result, self.sent_at[0])
        self.assertNotEqual(view["dispatched_at"], self.entries[0])
        self.assertEqual(self.sent_at[0], (
            self.intent_entry() + ENTRY_TO_SEND).isoformat())
        self.assertEqual(per_child_send_times(view), {0: self.sent_at[0]})

    async def test_delete_publishes_the_send_time_not_gateway_entry(self):
        created, result = await self.run_delayed("delete")
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.sent_at), 1)
        self.assertEqual(len(self.config_rest.deletes()), 1)
        view = self.assert_one_value_everywhere(created, result, self.sent_at[0])
        self.assertNotEqual(view["dispatched_at"], self.entries[0])

    async def test_restart_keeps_the_stored_send_time(self):
        created, result = await self.run_delayed("update")
        await self.restart_runtime()
        self.assert_one_value_everywhere(created, result, self.sent_at[0])
        self.assertEqual(len(self.provider_writes()), 1)

    async def test_multi_child_sends_store_each_time_and_the_earliest(self):
        targets = ("hall_light", "porch_light", "stair_light")
        for target in targets[:1] + targets[2:]:
            self.add("automation", target)
        created = await self.service.create_configuration_plan(
            title="HAMCP-181 multi-child send times",
            description="Synthetic multi-child send-boundary fixture",
            operations=[
                {
                    "operation_id": f"update_{target}",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": target,
                    "depends_on": [],
                    "proposed_config": automation_config(target, target="light.hall"),
                }
                for target in targets
            ],
        )
        await self.approve(created)
        delays = [timedelta(seconds=3), timedelta(seconds=17), timedelta(seconds=5)]
        with self.delay_inside(
            ConfigurationResourceGateway, "_write_rest_configuration", delays
        ), self.observe_rest({"POST"}):
            result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(len(self.sent_at), 3)
        self.assertEqual(len(self.provider_writes()), 3)
        # Each child was entered strictly before its own send.
        for entered, sent in zip(self.entries, self.sent_at):
            self.assertLess(entered, sent)
        view = self.assert_one_value_everywhere(created, result, min(self.sent_at))
        self.assertEqual(per_child_send_times(view), dict(enumerate(self.sent_at)))
        self.assertEqual(view["child_dispatch_phases"],
                         {df.PHASE_SENT_RESPONSE_RECEIVED: 3})


class WebSocketSendBoundaryTests(SendTimeCase, AvailabilityExposureServiceTestCase):
    """A multi-child registry plan: the WebSocket send boundary."""

    async def test_multi_child_websocket_sends(self):
        for entity_id in ("light.a", "light.b"):
            self.add_entity(entity_id)
        created = await self.approved(operation="disable", entity_ids=["light.a", "light.b"])
        self.sent_at: list[str] = []
        self.entries: list[str] = []
        original_answer = self.ws._answer
        original_update = EntityAvailabilityExposureGateway.update
        delays = iter([timedelta(seconds=9), timedelta(seconds=4)])
        harness = self

        async def answer(message):
            if message.get("type") in WRITE_COMMANDS:
                harness.sent_at.append(harness.clock[0].isoformat())
            return await original_answer(message)

        async def update(gateway, *args, **kwargs):
            harness.assert_in_flight_unsent(())
            harness.entries.append(harness.clock[0].isoformat())
            harness.clock[0] = harness.clock[0] + next(delays)
            return await original_update(gateway, *args, **kwargs)

        with mock.patch.object(self.ws, "_answer", answer), mock.patch.object(
            EntityAvailabilityExposureGateway, "update", update
        ):
            result = await self.apply(created)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(len(self.sent_at), 2)
        self.assertEqual(len(self.ws.writes()), 2)
        for entered, sent in zip(self.entries, self.sent_at):
            self.assertLess(entered, sent)
        view = self.assert_one_value_everywhere(created, result, min(self.sent_at))
        self.assertEqual(per_child_send_times(view), dict(enumerate(self.sent_at)))


if __name__ == "__main__":
    unittest.main()
