"""HAMCP-181 PR2 (design section 5.3, row b): proven no-call producers.

A task is stored as ``failed_after_intent_not_dispatched`` (phase
``intent_recorded_not_sent``) only when P1/P3-grade evidence shows that no
send followed the durable intent.  Each producer is checked at its site (zero
transport sends, the not-sent attribute) and through the shipped chain (the
proof code on ``dispatch_result_recorded``, the stored phase).  The negative
rows keep their earlier classification and never gain the proof code:

* automation/script REST 4xx (P1) and the WebSocket ``unauthorized`` result
  (P3) stay sent and rejected without mutation (r-proven);
* an entity-registry error result stays sent with the mutation unproven;
* a lost response or ``HomeAssistantUnavailableError`` stays uncertain (c);
* a backup or add-on lifecycle callback failure stays uncertain (exclusion).

Every transport is a synthetic fake or a disposable loopback peer.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from tests import test_hamcp177_dashboard_write_admission as dashboard  # noqa: E402
from tests.core_authority_fakes import require_core_authority  # noqa: E402
# TestCase bases are imported through their modules so the loader does not
# collect (and rerun) the base classes' own tests from this module.
from tests import test_beta37_exact_helper_state as helper_state_tests  # noqa: E402
from tests import test_f3_runtime_integration as f3_integration  # noqa: E402
from tests.test_beta37_exact_helper_state import FakeHelperStateGateway  # noqa: E402
from tests.test_2_1a_operational_backup import exact_catalog  # noqa: E402
from tests.test_dev14_resource_adapters import (  # noqa: E402
    AUTOMATION,
    INPUT_BOOLEAN,
    FakeRestClient,
    FakeWebSocketClient,
)
from tests.test_hamcp181_fan_power_dispatched_at import FanKind, PowerKind  # noqa: E402
from tests.test_hamcp183_entity_registry_error_truth import (  # noqa: E402
    TARGET as REGISTRY_TARGET,
    CoreShapedRegistryPeer,
    LoopbackRegistryTestCase,
    _Client as RegistryClient,
)
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.f3.executor import (  # noqa: E402
    STAGE_AUTHORITY_RETIRED_BEFORE_SEND,
    STAGE_BEFORE_DISPATCH_FAILED,
    SharedOperationExecutor,
)
from ha_mcp_engineering.f3_runtime import dispatch_facts as df  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    EntityMetadataUpdateErrorResult,
    EntityRegistryMetadataGateway,
)
from ha_mcp_engineering.governance.operational import (  # noqa: E402
    BackupAdministrationGateway,
    OperationalGatewayError,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HelperStateGateway,
    HelperStateGatewayError,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ENTITY_METADATA_RESOURCE_TYPE,
    ConfigurationMutationNotDispatchedError,
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)


INTENT_STAGE = "after_durable_intent_before_provider_invocation"
NOT_SENT = df.PHASE_INTENT_RECORDED_NOT_SENT
HELPER_ID = "input_boolean.hvac_override"
AUTOMATION_PATH = "/config/automation/config/hvac_guard"


def _not_sent_attribute(exc: BaseException | None) -> bool:
    return getattr(exc, "call_not_sent", False) is True


def dispatch_codes(events: list[dict[str, Any]]) -> set[str]:
    return {
        code
        for event in events
        if event["event_type"] == df.DISPATCH_RESULT_EVENT
        for code in event["diagnostic_codes"]
    }


def all_codes(events: list[dict[str, Any]]) -> set[str]:
    return {code for event in events for code in event["diagnostic_codes"]}


@contextmanager
def at_intent(action):
    """Run ``action`` at the executor's post-intent, pre-invocation stage."""

    original = SharedOperationExecutor._inject

    def inject(executor, stage):
        if stage == INTENT_STAGE:
            action()
        return original(executor, stage)

    with patch.object(SharedOperationExecutor, "_inject", inject):
        yield


def _raise_callback_failure():
    raise RuntimeError("synthetic dispatch callback failure")


class _ProofAssertions:
    """Shared expectations for the (b) row and for the rows that never get it."""

    def assert_proven_not_sent(self, view, events, *, stage=None, proof=df.PROVIDER_CALL_NOT_SENT):
        self.assertEqual(view["dispatch_phase"], NOT_SENT, view)
        self.assertIsNone(view["dispatched_at"])
        self.assertEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        self.assertIs(view["provider_dispatch_occurred"], False)
        codes = dispatch_codes(events)
        # The proof code is on the dispatch result event, not elsewhere only.
        self.assertIn(proof, codes)
        if stage is not None:
            self.assertIn(stage, codes)
        self.assertFalse(codes & df._B_CONFLICTING_CODES, codes)

    def assert_no_proof(self, view, events):
        self.assertNotEqual(view["dispatch_phase"], NOT_SENT, view)
        self.assertNotEqual(view.get("terminal_outcome"), df.NOT_SENT_OUTCOME)
        self.assertNotEqual(view["dispatched_at_basis"], df.DISPATCHED_AT_CALL_NOT_SENT)
        codes = all_codes(events)
        self.assertNotIn(df.PROVIDER_CALL_NOT_SENT, codes)
        self.assertNotIn(df.SETTER_CALL_NOT_SENT, codes)


# -- site level: configuration resource gateway --------------------------------


class _EnforcingRest(FakeRestClient):
    """Every request is refused outside a Core-authority window (HAMCP-155)."""

    async def request(self, method, path, **kwargs):
        require_core_authority(self, method, str(path))
        return await super().request(method, path, **kwargs)


class _EnforcingWebSocket(FakeWebSocketClient):
    async def command(self, payload):
        require_core_authority(self, "WS", payload["type"])
        return await super().command(payload)


def _real_gateway(rest_cls=FakeRestClient, ws_cls=FakeWebSocketClient):
    rest, websocket = rest_cls(), ws_cls()
    websocket.responses["input_boolean/list"] = []
    rest.responses[("GET", f"/states/{HELPER_ID}")] = ExpectedHttpStatus(404)
    return rest, websocket, ConfigurationResourceGateway(rest, websocket)


def _sends(rest, websocket) -> list[Any]:
    """Every mutating transport call the fakes saw."""

    return [call for call in rest.calls if call["method"] != "GET"] + [
        call for call in websocket.calls if not call["type"].endswith("/list")
    ]


class ConfigurationGatewaySiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rest, self.websocket, self.gateway = _real_gateway()

    async def raised(self, call) -> BaseException:
        try:
            await call
        except BaseException as exc:  # noqa: BLE001 - the classification is under test
            return exc
        self.fail("expected the gateway to refuse")

    async def test_helper_create_preflight_refusals_declare_not_sent(self):
        cases = {
            "collection_read_failed": (
                lambda: self.websocket.responses.__setitem__(
                    "input_boolean/list", HomeAssistantUnavailableError()),
                "helper_create_preflight_unavailable"),
            "already_exists": (
                lambda: self.websocket.responses.__setitem__(
                    "input_boolean/list", [{"id": "hvac_override", **INPUT_BOOLEAN}]),
                "target_already_exists"),
            "state_read_failed": (
                lambda: self.rest.responses.__setitem__(
                    ("GET", f"/states/{HELPER_ID}"), HomeAssistantTimeoutError()),
                "helper_create_preflight_unavailable"),
            "entity_id_reserved": (
                lambda: self.rest.responses.__setitem__(
                    ("GET", f"/states/{HELPER_ID}"), {"entity_id": HELPER_ID, "state": "off"}),
                "target_entity_id_reserved"),
        }
        for name, (arrange, reason) in cases.items():
            with self.subTest(case=name):
                self.rest, self.websocket, self.gateway = _real_gateway()
                arrange()
                exc = await self.raised(self.gateway.write(
                    "create", "input_boolean", HELPER_ID, copy.deepcopy(INPUT_BOOLEAN)))
                self.assertIsInstance(exc, ConfigurationMutationNotDispatchedError)
                self.assertEqual(exc.details["reason"], reason)
                self.assertTrue(_not_sent_attribute(exc))
                self.assertEqual(_sends(self.rest, self.websocket), [])
                self.assertNotIn({"type": "input_boolean/create", **INPUT_BOOLEAN},
                                 self.websocket.calls)

    async def test_entity_metadata_closed_payload_declares_not_sent(self):
        exc = await self.raised(self.gateway.update(
            ENTITY_METADATA_RESOURCE_TYPE, "sensor.porch", {"options_domain": "x"}))
        self.assertIsInstance(exc, ConfigurationMutationNotDispatchedError)
        self.assertEqual(exc.details["reason"], "closed_payload_violation")
        self.assertTrue(_not_sent_attribute(exc))
        self.assertEqual(self.websocket.calls, [])
        self.assertEqual(self.rest.calls, [])

    async def test_invalid_identifier_declares_not_sent(self):
        exc = await self.raised(self.gateway._write_rest_configuration(
            "automation", "../../hassio/addons", copy.deepcopy(AUTOMATION)))
        self.assertIsInstance(exc, ConfigurationMutationNotDispatchedError)
        self.assertEqual(exc.details["reason"], "invalid_identifier")
        self.assertTrue(_not_sent_attribute(exc))
        self.assertEqual(self.rest.calls, [])

    async def test_p1_rejection_is_answered_not_declared_not_sent(self):
        self.rest.responses[("POST", AUTOMATION_PATH)] = HomeAssistantApiError(
            details={"status": 400, "provider_response_received": True})
        exc = await self.raised(self.gateway.update(
            "automation", "hvac_guard", copy.deepcopy(AUTOMATION)))
        self.assertIsInstance(exc, ConfigurationMutationNotDispatchedError)
        self.assertEqual(exc.details["reason"], "configuration_write_rejected")
        self.assertIs(exc.details["provider_response_received"], True)
        # The request was sent and answered: proven no mutation, never not sent.
        self.assertFalse(_not_sent_attribute(exc))
        self.assertEqual([call["method"] for call in self.rest.calls], ["POST"])

    async def test_lost_or_unavailable_write_is_never_declared_not_sent(self):
        for error in (HomeAssistantApiError(details={"status": 502}),
                      HomeAssistantTimeoutError(),
                      HomeAssistantUnavailableError()):
            with self.subTest(error=type(error).__name__):
                self.rest, self.websocket, self.gateway = _real_gateway()
                self.rest.responses[("POST", AUTOMATION_PATH)] = error
                exc = await self.raised(self.gateway.update(
                    "automation", "hvac_guard", copy.deepcopy(AUTOMATION)))
                self.assertIs(exc, error)
                self.assertFalse(_not_sent_attribute(exc))


class EntityMetadataGatewaySiteTests(unittest.IsolatedAsyncioTestCase):
    COMMAND = {"type": "config/entity_registry/update", "entity_id": REGISTRY_TARGET,
               "name": "Porch sensor"}

    async def update(self, error):
        client = RegistryClient(error)
        try:
            await EntityRegistryMetadataGateway(None, client).update(
                REGISTRY_TARGET, dict(self.COMMAND))
        except BaseException as exc:  # noqa: BLE001 - the classification is under test
            return client, exc
        return client, None

    async def test_closed_payload_declares_not_sent_without_a_send(self):
        client = RegistryClient(None)
        with self.assertRaises(ConfigurationMutationNotDispatchedError) as raised:
            await EntityRegistryMetadataGateway(None, client).update(
                REGISTRY_TARGET, {"type": self.COMMAND["type"], "entity_id": REGISTRY_TARGET,
                                  "options_domain": "x"})
        self.assertTrue(_not_sent_attribute(raised.exception))
        self.assertEqual(client.sent, [])

    async def test_only_the_clients_pre_command_refusal_is_carried(self):
        cases = {
            # b2-A: the client refused before writing the command frame.
            "pre_command": (AuthorizationError(details={}, call_not_sent=True), True),
            # P3: the command was sent and answered unauthorized.
            "answered": (AuthorizationError(details={"provider_response_received": True}), False),
            # An answered refusal never carries the attribute, even if set.
            "answered_with_attribute": (
                AuthorizationError(details={"provider_response_received": True},
                                   call_not_sent=True), False),
            # No declaration from the client: no proof.
            "undeclared": (AuthorizationError(details={}), False),
        }
        for name, (error, expected) in cases.items():
            with self.subTest(case=name):
                _, raised = await self.update(error)
                self.assertIsInstance(raised, ConfigurationMutationNotDispatchedError)
                self.assertIs(_not_sent_attribute(raised), expected)

    async def test_error_result_and_transport_failures_carry_no_proof(self):
        _, raised = await self.update(
            HomeAssistantApiError(details={"provider_response_received": True}))
        self.assertIsInstance(raised, EntityMetadataUpdateErrorResult)
        self.assertFalse(_not_sent_attribute(raised))
        for error in (HomeAssistantUnavailableError(), HomeAssistantTimeoutError(),
                      HomeAssistantApiError(details={})):
            with self.subTest(error=type(error).__name__):
                _, raised = await self.update(error)
                self.assertIs(raised, error)
                self.assertFalse(_not_sent_attribute(raised))


class HelperStateGatewaySiteTests(unittest.IsolatedAsyncioTestCase):
    class _WebSocket:
        def __init__(self, error=None):
            self.payloads, self.error = [], error

        async def command(self, payload):
            self.payloads.append(copy.deepcopy(payload))
            if self.error is not None:
                raise self.error

    async def test_callback_failure_declares_not_sent_before_the_command(self):
        websocket = self._WebSocket()

        async def before_dispatch():
            raise RuntimeError("synthetic intent failure")
        with self.assertRaises(HelperStateGatewayError) as raised:
            await HelperStateGateway(None, websocket).set_state(
                "input_boolean.exact_target", "on", before_dispatch=before_dispatch)
        self.assertEqual(raised.exception.category, "before_dispatch_failed")
        self.assertTrue(_not_sent_attribute(raised.exception))
        self.assertEqual(websocket.payloads, [])

    async def test_sent_command_failures_carry_no_proof(self):
        async def before_dispatch():
            return None
        for error in (AuthorizationError(details={"provider_response_received": True}),
                      HomeAssistantTimeoutError(), HomeAssistantUnavailableError()):
            with self.subTest(error=type(error).__name__):
                websocket = self._WebSocket(error)
                with self.assertRaises(HelperStateGatewayError) as raised:
                    await HelperStateGateway(None, websocket).set_state(
                        "input_boolean.exact_target", "on", before_dispatch=before_dispatch)
                self.assertTrue(raised.exception.dispatched)
                self.assertFalse(_not_sent_attribute(raised.exception))
                self.assertEqual(len(websocket.payloads), 1)
        # The attribute cannot coexist with a dispatched error.
        self.assertFalse(HelperStateGatewayError(
            "x", dispatched=True, call_not_sent=True).call_not_sent)

    async def test_unclassified_failure_after_the_command_carries_no_proof(self):
        # The callback succeeded and the command was handed to the transport:
        # an unclassified failure there is never the callback's not-sent row.
        async def before_dispatch():
            return None
        for error in (RuntimeError("synthetic failure after the command"),
                      OSError("synthetic connection reset after the command")):
            with self.subTest(error=type(error).__name__):
                websocket = self._WebSocket(error)
                with self.assertRaises(Exception) as raised:
                    await HelperStateGateway(None, websocket).set_state(
                        "input_boolean.exact_target", "on", before_dispatch=before_dispatch)
                self.assertFalse(_not_sent_attribute(raised.exception))
                self.assertNotEqual(getattr(raised.exception, "category", None),
                                    "before_dispatch_failed")
                self.assertEqual(len(websocket.payloads), 1)


# -- site level: the real WebSocket client against a loopback peer ---------------


class _ScriptedPeer:
    """Loopback peer whose authentication and answer are scripted per test."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.frames: list[dict[str, Any]] = []
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/websocket", self._handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def commands(self) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame.get("type") != "auth"]

    async def _handle(self, request: web.Request):
        if self.mode == "handshake_401":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        self.frames.append(await ws.receive_json())
        if self.mode == "auth_invalid":
            await ws.send_json({"type": "auth_invalid", "message": "synthetic"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        command = await ws.receive_json()
        self.frames.append(command)
        if self.mode == "drop_after_command":
            await ws.close()
            return ws
        code = {"unauthorized_result": "unauthorized", "error_result": "unknown_error"}[self.mode]
        await ws.send_json({"id": command["id"], "type": "result", "success": False,
                            "error": {"code": code, "message": "synthetic"}})
        await ws.close()
        return ws


def _loopback_settings(url: str) -> Settings:
    return Settings(
        ha_url=url, ha_token="synthetic-no-real-token",
        access_secret="synthetic-no-real-secret", port=8100,
        audit_path="unused-synthetic", rate_limit_per_minute=120, rate_limit_burst=20,
        destructive_services=frozenset(), ha_timeout_seconds=2.0)


class WebSocketClientProofTests(unittest.IsolatedAsyncioTestCase):
    COMMAND = {"type": "config/entity_registry/update", "entity_id": REGISTRY_TARGET,
               "name": "Porch sensor"}

    async def run_mode(self, mode):
        peer = _ScriptedPeer(mode)
        await peer.start()
        self.addAsyncCleanup(peer.stop)
        client = HomeAssistantWebSocketClient(_loopback_settings(peer.url))
        try:
            await client.command(dict(self.COMMAND))
        except BaseException as exc:  # noqa: BLE001 - the classification is under test
            return peer, exc
        self.fail(f"{mode}: expected the client to raise")

    async def test_authentication_refusals_declare_not_sent_with_no_command_frame(self):
        for mode in ("auth_invalid", "handshake_401"):
            with self.subTest(mode=mode):
                peer, exc = await self.run_mode(mode)
                self.assertIsInstance(exc, AuthorizationError)
                self.assertTrue(_not_sent_attribute(exc))
                self.assertNotEqual(exc.details.get("provider_response_received"), True)
                self.assertEqual(peer.commands(), [])

    async def test_connection_failure_after_the_command_carries_no_proof(self):
        # The command frame is written; the read that follows it fails at the
        # connection level.  That is unavailable/uncertain, never not sent.
        original = aiohttp.ClientWebSocketResponse.receive_json
        for error_type in (aiohttp.ClientConnectionError, ConnectionResetError):
            with self.subTest(error=error_type.__name__):
                reads = 0

                async def receive_json(websocket, *args, **kwargs):
                    nonlocal reads
                    reads += 1
                    # auth_required and auth_ok precede the command frame.
                    if reads <= 2:
                        return await original(websocket, *args, **kwargs)
                    raise error_type("synthetic connection failure after the command")

                with patch.object(aiohttp.ClientWebSocketResponse, "receive_json",
                                  receive_json):
                    peer, exc = await self.run_mode("drop_after_command")
                self.assertEqual(reads, 3)
                self.assertIsInstance(exc, HomeAssistantUnavailableError)
                self.assertFalse(_not_sent_attribute(exc))
                self.assertEqual([frame["type"] for frame in peer.commands()],
                                 [self.COMMAND["type"]])

    async def test_answered_or_lost_commands_carry_no_proof(self):
        # A dropped connection surfaces whatever the transport raised; it must
        # only never be declared not sent.
        expected = {"unauthorized_result": AuthorizationError,
                    "error_result": HomeAssistantApiError,
                    "drop_after_command": Exception}
        for mode, error_type in expected.items():
            with self.subTest(mode=mode):
                peer, exc = await self.run_mode(mode)
                self.assertIsInstance(exc, error_type)
                self.assertFalse(_not_sent_attribute(exc))
                self.assertEqual([frame["type"] for frame in peer.commands()],
                                 [self.COMMAND["type"]])


# -- chain: configuration adapter through the real resource gateway --------------


class ConfigurationChainTests(_ProofAssertions, f3_integration.F3ConfigurationActivationTests):
    """The F3 configuration path with the shipped gateway's own refusal sites."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.rest, self.websocket, self.real = _real_gateway(_EnforcingRest, _EnforcingWebSocket)
        self.websocket.responses["input_number/list"] = []
        self.rest.responses[("GET", "/states/input_number.hvac_target")] = ExpectedHttpStatus(404)
        fake_write = self.gateway.write

        async def write(action, resource_type, resource_id, approved_config):
            self.gateway.calls.append(("write", action, resource_type, resource_id))
            if (resource_type, resource_id) in self.routed:
                return await self.real.write(action, resource_type, resource_id, approved_config)
            return await fake_write(action, resource_type, resource_id, approved_config)

        self.routed: set[tuple[str, str]] = set()
        self.enterContext(patch.object(self.gateway, "write", write))

    async def applied(self, created):
        await self.approve(created)
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        view = self.service.get_execution_task(result["task_id"])
        records = [self.runtime.children.get(item["child_id"])
                   for item in self.runtime.children.declarations_for_task(result["task_id"])]
        # Children after the stopping operation were declared but never claimed.
        events = [event for record in records if record is not None for event in record.events]
        return result, view, events

    async def test_helper_appearing_after_intent_is_proven_not_sent(self):
        self.routed.add(("input_number", "input_number.hvac_target"))
        self.websocket.responses["input_number/list"] = [{"id": "hvac_target", "name": "x"}]
        result, view, events = await self.applied(await self.create_hvac_plan())
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(view["terminal_outcome"], df.NOT_SENT_OUTCOME)
        self.assert_proven_not_sent(view, events)
        self.assertEqual(_sends(self.rest, self.websocket), [])
        # Stop on first failure: no later operation was written.
        self.assertEqual([call[2] for call in self.gateway.calls if call[0] == "write"],
                         ["input_number"])

    def send_times(self, task_id):
        task = self.service.task_repository.get(task_id)
        return task.verification_summary.get(df.SEND_TIME_SUMMARY_KEY)

    async def test_helper_appearing_after_intent_stores_no_send_time(self):
        # OD-6(ii): the gateway observed a provider-call start for this child
        # (its boundary ran before the preflight refused), but a proven
        # no-call stores null, after apply and after a restart.
        self.routed.add(("input_number", "input_number.hvac_target"))
        self.websocket.responses["input_number/list"] = [{"id": "hvac_target", "name": "x"}]
        created = await self.create_hvac_plan()
        times = self.runtime._provider_call_started_at = _RecordingSendTimes()
        result, view, _events = await self.applied(created)
        self.assertEqual(view["dispatch_phase"], NOT_SENT)
        child_id = self.runtime.children.declarations_for_task(result["task_id"])[0]["child_id"]
        self.assertIsInstance(times.captured.get(child_id), str)
        self.assertEqual(self.send_times(result["task_id"]), {child_id: None})
        self.assertIsNone(view["verification_summary"][df.SEND_TIME_SUMMARY_KEY][child_id])
        self.assertIsNone(view["dispatched_at"])
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=f3_integration._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=f3_integration._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("restart")
        self.assertEqual(self.send_times(result["task_id"]), {child_id: None})
        after = self.service.get_execution_task(result["task_id"])
        self.assertIsNone(after["verification_summary"][df.SEND_TIME_SUMMARY_KEY][child_id])
        self.assertEqual(after["dispatch_phase"], NOT_SENT)
        self.assertEqual(_sends(self.rest, self.websocket), [])

    async def test_p1_rejection_stays_sent_rejected_without_proof(self):
        self.routed.add(("automation", "apply_hvac_comfort"))
        self.rest.responses[("POST", "/config/automation/config/apply_hvac_comfort")] = (
            HomeAssistantApiError(details={"status": 400, "provider_response_received": True}))
        _, view, events = await self.applied(await self.create_automation_plan())
        self.assertEqual(view["dispatch_phase"], df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assert_no_proof(view, events)

    async def test_lost_response_and_unavailable_stay_uncertain(self):
        for error in (HomeAssistantTimeoutError(), HomeAssistantUnavailableError()):
            with self.subTest(error=type(error).__name__):
                self.routed = {("automation", "apply_hvac_comfort")}
                self.rest.responses[("POST", "/config/automation/config/apply_hvac_comfort")] = error
                _, view, events = await self.applied(await self.create_automation_plan())
                self.assertEqual(view["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
                self.assertIs(view["provider_dispatch_occurred"], True)
                self.assert_no_proof(view, events)

    async def test_callback_failure_after_intent_is_proven_not_sent(self):
        # b2-L: the configuration adapter's own callback raised after intent.
        with at_intent(_raise_callback_failure):
            _, view, events = await self.applied(await self.create_automation_plan())
        self.assert_proven_not_sent(view, events, stage=STAGE_BEFORE_DISPATCH_FAILED)
        self.assertEqual([call for call in self.gateway.calls if call[0] == "write"], [])


# -- chain: entity metadata (b2-A) end to end over the loopback peer -------------


class _RetiringRegistryPeer(CoreShapedRegistryPeer):
    """Core-shaped peer that starts refusing authentication on request."""

    def __init__(self, backend: Any) -> None:
        super().__init__(backend)
        self.refuse_auth = False
        self.frames_after_refusal: list[dict[str, Any]] = []

    async def _handle(self, request: web.Request):
        if not self.refuse_auth:
            return await super()._handle(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        await ws.receive_json()
        await ws.send_json({"type": "auth_invalid", "message": "synthetic"})
        await ws.close()
        return ws


class EntityMetadataChainTests(_ProofAssertions, LoopbackRegistryTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.peer.stop()
        self.peer = _RetiringRegistryPeer(self.ws)
        await self.peer.start()
        self.resource_gateway.entity_metadata.websocket_client = HomeAssistantWebSocketClient(
            _loopback_settings(self.peer.url))

    def view_and_events(self, task_id):
        return self.service.get_execution_task(task_id), self.record(task_id).events

    async def test_authentication_refused_after_intent_is_proven_not_sent(self):
        created = await self.approved()
        updates = len(self.ws.updates())
        with at_intent(lambda: setattr(self.peer, "refuse_auth", True)):
            result = await self.apply(created)
        view, events = self.view_and_events(result["task_id"])
        self.assertEqual(result["task_state"], "failed_post_dispatch")
        self.assertEqual(view["terminal_outcome"], df.NOT_SENT_OUTCOME)
        self.assert_proven_not_sent(view, events)
        self.assertEqual(len(self.ws.updates()), updates)
        await self.assert_replay_refused(created, result["task_id"])

    async def test_authentication_refused_after_intent_stores_no_send_time(self):
        created = await self.approved()
        times = self.runtime._provider_call_started_at = _RecordingSendTimes()
        with at_intent(lambda: setattr(self.peer, "refuse_auth", True)):
            result = await self.apply(created)
        view = self.service.get_execution_task(result["task_id"])
        self.assertEqual(view["dispatch_phase"], NOT_SENT)
        (declaration,) = self.runtime.children.declarations_for_task(result["task_id"])
        child_id = declaration["child_id"]
        # The provider boundary was entered before the client's refusal.
        self.assertIsInstance(times.captured.get(child_id), str)

        def stored():
            task = self.service.task_repository.get(result["task_id"])
            return task.verification_summary.get(df.SEND_TIME_SUMMARY_KEY)

        self.assertEqual(stored(), {child_id: None})
        await self.restart_runtime()
        await self.runtime.recover_once("restart")
        self.assertEqual(stored(), {child_id: None})
        after = self.service.get_execution_task(result["task_id"])
        self.assertIsNone(after["verification_summary"][df.SEND_TIME_SUMMARY_KEY][child_id])
        self.assertIsNone(after["dispatched_at"])

    async def test_unauthorized_result_stays_r_proven_without_proof(self):
        created = await self.approved()
        self.fail_without_change("unauthorized")
        result = await self.apply(created)
        view, events = self.view_and_events(result["task_id"])
        self.assertEqual(view["dispatch_phase"], df.PHASE_SENT_REJECTED_NO_MUTATION)
        self.assert_no_proof(view, events)

    async def test_registry_error_result_stays_unproven_without_proof(self):
        created = await self.approved()
        self.fail_without_change("unknown_error")
        result = await self.apply(created)
        view, events = self.view_and_events(result["task_id"])
        self.assert_no_proof(view, events)
        codes = all_codes(events)
        self.assertNotIn(df.PROVIDER_REJECTED_MUTATION, codes)
        self.assertNotIn(df.PROVIDER_PERMISSION_DENIED, codes)


# -- chain: b5 callback failures (dashboard, fan, power, helper state) -----------


class DashboardCallbackFailureTests(_ProofAssertions, dashboard.Harness):
    async def test_callback_failure_after_intent_is_proven_not_sent(self):
        peer = dashboard.DashboardPeer(dashboard.catalog(dashboard.EXTRAS))
        _p, service, runtime, created = await self.planned(peer)
        with at_intent(_raise_callback_failure):
            result = await service.apply(created["plan_id"], created["plan_hash"])
        child = self.child(runtime, result["task_id"])
        view = service.get_execution_task(result["task_id"])
        self.assert_proven_not_sent(view, child["events"], stage=STAGE_BEFORE_DISPATCH_FAILED)
        self.assertEqual(peer.names().count(dashboard.SETTER), 0)
        self.assertEqual(peer.configuration["title"], "Before")


class _FanPowerProofCases(_ProofAssertions):
    kind_type = None

    async def run_at_intent(self, action):
        self.kind = self.kind_type(self)
        await self.kind.build()
        request = self.kind.request(self)
        self.kind.bind(self, request)
        with at_intent(action):
            result = await self.service.control(request)
        return result, self.service.executions.get(result["task_id"]).events

    async def test_b5_callback_failure_is_proven_not_sent(self):
        result, events = await self.run_at_intent(_raise_callback_failure)
        self.assert_proven_not_sent(result, events)
        self.assertEqual(self.transport.writes, 0)

    async def test_b8_authority_retired_before_send_is_proven_not_sent(self):
        def retire():
            self.transport.authority = "retired"
        result, events = await self.run_at_intent(retire)
        self.assert_proven_not_sent(result, events)
        self.assertEqual(self.transport.writes, 0)

    async def test_authority_retired_after_the_call_is_never_proven_not_sent(self):
        # The authority check after the transport returned runs strictly after
        # tools/call: a refusal there is never the b8 not-sent row.
        self.kind = self.kind_type(self)
        await self.kind.build()
        original = self.transport.execute_read

        async def execute_read(tool, args, **kwargs):
            writes = self.transport.writes
            value = await original(tool, args, **kwargs)
            if self.transport.writes > writes:
                self.transport.authority = "retired"
            return value

        self.transport.execute_read = execute_read
        request = self.kind.request(self)
        self.kind.bind(self, request)
        result = await self.service.control(request)
        events = self.service.executions.get(result["task_id"]).events
        self.assertEqual(self.transport.writes, 1)
        self.assertIs(result["provider_dispatch_occurred"], True)
        self.assertIn(result["dispatch_phase"], {df.PHASE_SEND_UNCERTAIN,
                                                 df.PHASE_SENT_RESPONSE_RECEIVED})
        self.assert_no_proof(result, events)

    async def test_lost_response_stays_uncertain_without_proof(self):
        self.kind = self.kind_type(self)
        await self.kind.build(mode="timeout")
        request = self.kind.request(self)
        self.kind.bind(self, request)
        result = await self.service.control(request)
        events = self.service.executions.get(result["task_id"]).events
        self.assertEqual(result["dispatch_phase"], df.PHASE_SEND_UNCERTAIN)
        self.assert_no_proof(result, events)


class FanProofTests(_FanPowerProofCases, unittest.IsolatedAsyncioTestCase):
    kind_type = FanKind


class PowerProofTests(_FanPowerProofCases, unittest.IsolatedAsyncioTestCase):
    kind_type = PowerKind


class _RealSendingHelper(FakeHelperStateGateway):
    """The fake's state store, with the shipped gateway's ``set_state``."""

    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, Any]] = []
        helper = self

        class WebSocket:
            async def command(self, payload):
                helper.payloads.append(copy.deepcopy(payload))
                helper.dispatch_count += 1
                helper.state = "on" if payload["service"] == "turn_on" else "off"
                helper.last_changed = "2026-08-13T12:00:05+00:00"

        self.real = HelperStateGateway(None, WebSocket())

    async def set_state(self, entity_id, desired_state, *, before_dispatch):
        return await self.real.set_state(entity_id, desired_state, before_dispatch=before_dispatch)


class HelperStateCallbackFailureTests(_ProofAssertions, helper_state_tests.ExactHelperStateRuntimeTests):
    async def asyncSetUp(self):
        with patch("tests.test_beta37_exact_helper_state.FakeHelperStateGateway",
                   _RealSendingHelper):
            await super().asyncSetUp()

    async def test_callback_failure_after_intent_is_proven_not_sent(self):
        self.assertIsInstance(self.helper, _RealSendingHelper)
        plan = await self.create_and_grant()
        with at_intent(_raise_callback_failure):
            result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        view = self.service.get_execution_task(result["task_id"])
        child = self.runtime.children.get(
            self.runtime.children.declarations_for_task(result["task_id"])[0]["child_id"])
        self.assert_proven_not_sent(view, child.events)
        self.assertEqual(self.helper.payloads, [])
        self.assertEqual(self.helper.state, "off")

    async def test_success_through_the_shipped_gateway_still_sends_once(self):
        plan = await self.create_and_grant()
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(len(self.helper.payloads), 1)


# Only the classes' own tests run here; the inherited suites run in their modules.
for _cls in (ConfigurationChainTests, EntityMetadataChainTests,
             DashboardCallbackFailureTests, HelperStateCallbackFailureTests):
    for _base in _cls.__mro__[1:]:
        for _name in vars(_base):
            if _name.startswith("test_") and _name not in vars(_cls):
                setattr(_cls, _name, None)


# -- exclusions: backup and add-on lifecycle stay uncertain ----------------------


class _UnwrappedBackupTransport:
    """Reviewed catalog; a callback failure reaches the provider unwrapped."""

    def __init__(self) -> None:
        self.catalog = exact_catalog("8.0.0")
        self.sends = 0

    async def execute_read(self, tool, arguments, *, catalog_validator, before_dispatch,
                           **kwargs):
        catalog_validator(self.catalog)
        await before_dispatch()
        self.sends += 1
        raise AssertionError("tools/call must remain unreachable")


class _RecordingSendTimes(dict):
    """The runtime's send-time map, remembering every captured start.

    The runtime drops its in-process entries once the terminal summary is
    stored, so a test proving a captured time was withheld records it here.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: dict[str, str] = {}

    def setdefault(self, key, default=None):
        self.captured.setdefault(key, default)
        return super().setdefault(key, default)


class _ExclusionCases(_ProofAssertions):
    def boundary_labels(self):
        rows = [json.loads(line) for line in
                (Path(self.temp.name) / "audit.jsonl").read_text().splitlines()]
        return [row["event"] for row in rows
                if row.get("event", "").startswith("f3_provider_")
                and "readback" not in row["event"]]

    async def assert_refused_label(self, created):
        plan = await self._grant(created)
        with at_intent(_raise_callback_failure):
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        labels = self.boundary_labels()
        # A pre-send refusal on these gateways is labelled refused, never
        # not sent: the exclusion holds on the audit surface too.
        self.assertEqual(labels, ["f3_provider_invocation_refused"])
        self.assertNotIn("f3_" + df.PROVIDER_CALL_NOT_SENT, labels)

    async def assert_excluded(self, created, sends):
        plan = await self._grant(created)
        with at_intent(_raise_callback_failure):
            result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        view = self.service.get_execution_task(result["task_id"])
        record = self.runtime.children.get(
            self.runtime.children.declarations_for_task(result["task_id"])[0]["child_id"])
        self.assert_no_proof(view, record.events)
        self.assertEqual(sends(), 0)
        # Never redispatched on a second apply.
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(sends(), 0)


class BackupExclusionTests(_ExclusionCases, f3_integration.F3BackupActivationTests):
    async def test_backup_callback_failure_is_never_proven_not_sent(self):
        await self.assert_excluded(
            await self.service.create_backup_plan(backup_name="F3 disposable"),
            lambda: self.gateway.dispatch_count)

    async def test_backup_pre_send_refusal_is_labelled_refused(self):
        await self.assert_refused_label(
            await self.service.create_backup_plan(backup_name="F3 disposable"))
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_real_provider_not_dispatched_error_after_intent_is_never_proven_not_sent(self):
        # The shipped provider and gateway turn the failed callback into
        # OperationalGatewayError(dispatched=False) after the intent committed.
        provider = ReviewedOperationalBackupProvider()
        transport = _UnwrappedBackupTransport()
        provider._transport = transport
        provider._state.configured = True
        real = BackupAdministrationGateway(provider, None)
        raised: list[BaseException] = []

        async def create_full_backup(name, *, before_dispatch):
            try:
                return await real.create_full_backup(name, before_dispatch=before_dispatch)
            except BaseException as exc:
                raised.append(exc)
                raise

        self.enterContext(patch.object(self.gateway, "create_full_backup", create_full_backup))
        await self.assert_excluded(
            await self.service.create_backup_plan(backup_name="F3 disposable"),
            lambda: transport.sends)
        self.assertIsInstance(raised[0], OperationalGatewayError)
        self.assertIs(raised[0].dispatched, False)
        self.assertEqual(provider.health_snapshot()["dispatch_count"], 0)


class LifecycleExclusionTests(_ExclusionCases, f3_integration.F3OperationalActivationTests):
    async def test_addon_restart_callback_failure_is_never_proven_not_sent(self):
        await self.assert_excluded(
            await self.service.create_addon_restart_plan(addon_slug="local_test_addon"),
            lambda: self.lifecycle.dispatch_count)

    async def test_lifecycle_pre_send_refusal_is_labelled_refused(self):
        await self.assert_refused_label(
            await self.service.create_addon_restart_plan(addon_slug="local_test_addon"))
        self.assertEqual(self.lifecycle.dispatch_count, 0)


for _cls in (BackupExclusionTests, LifecycleExclusionTests):
    for _base in _cls.__mro__[1:]:
        for _name in vars(_base):
            if _name.startswith("test_") and _name not in vars(_cls):
                setattr(_cls, _name, None)
# A class still bound to a module-level name would be collected twice.
del _cls, _base, _name


if __name__ == "__main__":
    unittest.main()
