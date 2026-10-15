"""HAMCP-183 (OD-15): an entity-registry error result is not a non-mutation proof.

Home Assistant Core can assign the new registry entry and then answer the
``config/entity_registry/update`` command with ``unknown_error`` (the HAMCP-181
revision-3 probe, reproduced on Core 2026.10.0 and 2026.10.1).  After the
update has been sent, only the ``unauthorized``/``forbidden`` result (P3) stays
a confirmed non-mutation.  Every other error result is recorded as an
uncertain dispatch, and the existing F3 entity-registry readback decides:

* readback equals the planned final state -> ``succeeded_verified`` (applied),
  with ``provider_error_after_send`` kept on the dispatch event;
* readback equals the exact prior state -> ``verification_mismatch``
  (``failed_post_dispatch``): verified not applied;
* any other complete readback -> ``manual_review_required``;
* readback unavailable -> ``observing`` until a later readback settles it
  (``manual_review_required`` on evidence exhaustion, unchanged).

The end-to-end tests drive the shipped service, F3 runtime, configuration
adapter, metadata gateway and the real Home Assistant WebSocket client against
a disposable loopback peer that answers with Core's result-message shape.
Every value is synthetic; no live dependency is used.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable
import unittest
from unittest.mock import patch

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3.executor import InternalRecoveryContext  # noqa: E402
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ERROR_RESULT_AFTER_SEND,
    ERROR_RESULT_READBACK_UNEXPLAINED,
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    ENTITY_METADATA_ERROR_RESULT_REASON,
    ENTITY_METADATA_UNAUTHORIZED_REASON,
    EntityMetadataUpdateErrorResult,
    EntityRegistryMetadataGateway,
)
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationNotDispatchedError,
)

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    UPDATE,
    EntityMetadataServiceTestCase,
)


TARGET = "sensor.porch"
REGISTRY_GET = "config/entity_registry/get"
ERROR_DIAGNOSTIC = ERROR_RESULT_AFTER_SEND
# Codes that would claim the error result proved non-mutation.
NON_MUTATION_CLAIMS = {"provider_rejected_mutation", "provider_confirmed_no_mutation"}
# Error codes Core's WebSocket dispatcher can return for the update command.
CORE_ERROR_CODES = ("unknown_error", "invalid_format", "home_assistant_error", "not_found")

Fault = Callable[[dict[str, Any]], Awaitable[str | None]]


class CoreShapedRegistryPeer:
    """Loopback WebSocket peer answering registry commands as Core does.

    State lives in the synthetic registry.  ``update_fault`` may change it and
    then name a Core error code, which the peer sends as an error result
    instead of the success result.  ``readback_fault`` does the same for
    registry reads made after an update was sent.
    """

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.update_fault: Fault | None = None
        self.readback_fault: str | None = None
        self.runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/websocket", self._handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        await ws.receive_json()
        await ws.send_json({"type": "auth_ok"})
        command = await ws.receive_json()
        message_id = command.pop("id")
        await ws.send_json({"id": message_id, "type": "result", **await self._answer(command)})
        await ws.close()
        return ws

    @staticmethod
    def _error(code: str) -> dict[str, Any]:
        return {"success": False, "error": {"code": code, "message": "synthetic"}}

    async def _answer(self, command: dict[str, Any]) -> dict[str, Any]:
        kind = command.get("type")
        if kind == UPDATE and self.update_fault is not None:
            code = await self.update_fault(command)
            if code is not None:
                return self._error(code)
        if kind == REGISTRY_GET and self.readback_fault and self.backend.updates():
            self.backend.commands.append(deepcopy(command))
            return self._error(self.readback_fault)
        try:
            result = await self.backend.command(command)
        except HomeAssistantApiError:
            return self._error("not_found")
        return {"success": True, "result": result}


class LoopbackRegistryTestCase(EntityMetadataServiceTestCase):
    """Real service and F3 runtime; registry commands cross a real client."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.peer = CoreShapedRegistryPeer(self.ws)
        await self.peer.start()
        settings = Settings(
            ha_url=self.peer.url,
            ha_token="synthetic-no-real-token",
            access_secret="synthetic-no-real-secret",
            port=8100,
            audit_path="unused-synthetic",
            rate_limit_per_minute=120,
            rate_limit_burst=20,
            destructive_services=frozenset(),
            ha_timeout_seconds=5.0,
        )
        self.resource_gateway.entity_metadata.websocket_client = (
            HomeAssistantWebSocketClient(settings)
        )
        self.ws.labels = [
            {"label_id": "night", "name": "Night"},
            {"label_id": "outdoor", "name": "Outdoor"},
        ]
        self.add_entity(TARGET, labels=["night"], icon="mdi:old")

    async def asyncTearDown(self) -> None:
        await self.peer.stop()
        await super().asyncTearDown()

    # -- faults -----------------------------------------------------------

    def assign_then_fail(self, code: str = "unknown_error") -> None:
        """Codex probe: Core assigns the entry, then save scheduling raises."""

        async def fault(command: dict[str, Any]) -> str:
            await self.ws.command(command)
            return code

        self.peer.update_fault = fault

    def fail_without_change(self, code: str) -> None:
        async def fault(command: dict[str, Any]) -> str:
            self.ws.commands.append(deepcopy(command))
            return code

        self.peer.update_fault = fault

    def third_state_then_fail(self) -> None:
        """Neither the plan nor the pre-state is in the registry afterwards."""

        async def fault(command: dict[str, Any]) -> str:
            self.ws.commands.append(deepcopy(command))
            self.ws.set_external(TARGET, icon="mdi:someone-else")
            return "unknown_error"

        self.peer.update_fault = fault

    # -- helpers ----------------------------------------------------------

    async def approved(self) -> dict:
        created = await self.create(entity_id=TARGET, name="Porch sensor", icon="mdi:new")
        await self.approve(created)
        return created

    def record(self, task_id: str) -> Any:
        (item,) = self.runtime.children.declarations_for_task(task_id)
        return self.runtime.children.get(item["child_id"])

    def event_codes(self, task_id: str, event_type: str) -> set[str]:
        return {
            code
            for event in self.record(task_id).events
            if event["event_type"] == event_type
            for code in event["diagnostic_codes"]
        }

    def all_codes(self, task_id: str) -> set[str]:
        return {
            code
            for event in self.record(task_id).events
            for code in event["diagnostic_codes"]
        }

    def assert_uncertain_dispatch_recorded(self, task_id: str) -> None:
        """The error result was recorded as sent and uncertain, never proven."""

        record = self.record(task_id)
        self.assertEqual(record.dispatch_count, 1)
        self.assertIs(record.provider_response_received, True)
        dispatch_codes = self.event_codes(task_id, "dispatch_result_recorded")
        self.assertEqual(
            dispatch_codes, {ERROR_DIAGNOSTIC, "provider_response_indeterminate"}
        )
        self.assertFalse(self.all_codes(task_id) & NON_MUTATION_CLAIMS)
        self.assertNotIn("provider_permission_denied", self.all_codes(task_id))
        self.assertNotEqual(record.normalized_outcome, "dispatch_failed_confirmed")

    async def assert_replay_refused(self, created: dict, task_id: str) -> None:
        updates = len(self.ws.updates())
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(created)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertEqual(raised.exception.details["task_id"], task_id)
        self.assertEqual(len(self.ws.updates()), updates)

    async def assert_rollback_refused(self, created: dict) -> None:
        updates = len(self.ws.updates())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE)
        self.assertEqual(raised.exception.details["reason"], "forward_not_verified")
        self.assertEqual(len(self.ws.updates()), updates)


class CodexProbeEndToEndTests(LoopbackRegistryTestCase):
    """The registry changed and Core still answered ``unknown_error``."""

    async def test_change_then_unknown_error_is_verified_applied_never_unchanged(self):
        created = await self.approved()
        self.assign_then_fail("unknown_error")
        result = await self.apply(created)

        # The registry did change; Engineering reports it, never "nothing changed".
        self.assertEqual(self.ws.entries[TARGET]["name"], "Porch sensor")
        self.assertEqual(self.ws.entries[TARGET]["icon"], "mdi:new")
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertIs(result["provider_dispatch_occurred"], True)
        record = self.record(result["task_id"])
        self.assertEqual(record.normalized_outcome, "succeeded_verified")
        self.assert_uncertain_dispatch_recorded(result["task_id"])
        # The authoritative readback ran after the error result.
        self.assertIn("exact_readback_verified", self.all_codes(result["task_id"]))
        update_index = max(
            i for i, c in enumerate(self.ws.commands) if c.get("type") == UPDATE
        )
        self.assertTrue(
            any(c.get("type") == REGISTRY_GET for c in self.ws.commands[update_index + 1:])
        )
        self.assertEqual(self.repository.get(created["plan_id"]).status, PlanStatus.APPLIED)
        # The received error stays visible in the audit next to the verified result.
        reasons = {
            event["event"]: (event.get("evidence_references") or {}).get("reason_code")
            for event in self.audit_events()
            if event["event"] in {"f3_dispatch_result_recorded", "f3_verification_recorded"}
        }
        self.assertEqual(reasons["f3_dispatch_result_recorded"], ERROR_DIAGNOSTIC)
        self.assertEqual(reasons["f3_verification_recorded"], "exact_readback_verified")
        # Replay never redispatches.
        replay = await self.apply(created)
        self.assertIs(replay["task_reused"], True)
        self.assertEqual(len(self.ws.updates()), 1)

    async def test_rollback_is_offered_exactly_as_for_a_normal_success(self):
        created = await self.approved()
        self.assign_then_fail("unknown_error")
        await self.apply(created)
        self.peer.update_fault = None

        rollback = await self.service.rollback_change(created["plan_id"], created["plan_hash"])
        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertIs(rollback["approval_required"], True)
        self.assertIs(rollback["provider_dispatch_occurred"], False)
        self.assertEqual(rollback["restored_fields"], ["icon", "name"])
        self.assertEqual(len(self.ws.updates()), 1)

        reverse = {"plan_id": rollback["rollback_plan_id"], "plan_hash": rollback["plan_hash"]}
        await self.approve(reverse)
        applied = await self.apply(reverse)
        self.assertEqual(applied["status"], "applied")
        entry = self.ws.entries[TARGET]
        self.assertEqual((entry["name"], entry["icon"]), (None, "mdi:old"))

    async def test_every_non_p3_core_error_code_is_uncertain_then_readback(self):
        for code in CORE_ERROR_CODES:
            with self.subTest(code=code):
                entity = f"sensor.probe_{code}"
                self.add_entity(entity)
                created = await self.create(entity_id=entity, name="Probe")
                await self.approve(created)
                self.assign_then_fail(code)
                result = await self.apply(created)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assert_uncertain_dispatch_recorded(result["task_id"])


class ErrorWithoutChangeTests(LoopbackRegistryTestCase):
    async def test_error_without_change_is_verified_not_applied(self):
        for code in ("unknown_error", "invalid_format", "not_found"):
            with self.subTest(code=code):
                entity = f"sensor.unchanged_{code}"
                self.add_entity(entity, icon="mdi:old")
                created = await self.create(entity_id=entity, name="Probe")
                await self.approve(created)
                self.fail_without_change(code)
                updates = len(self.ws.updates())
                result = await self.apply(created)

                self.assertEqual(len(self.ws.updates()), updates + 1)
                self.assertIsNone(self.ws.entries[entity]["name"])
                self.assertEqual(result["task_state"], "failed_post_dispatch")
                self.assertIs(result["provider_dispatch_occurred"], True)
                record = self.record(result["task_id"])
                # Readback found the pre-state: the plan is verified not applied.
                self.assertEqual(record.normalized_outcome, "verification_mismatch")
                self.assertTrue(record.terminal)
                self.assertIn("exact_readback_not_verified", self.all_codes(result["task_id"]))
                self.assert_uncertain_dispatch_recorded(result["task_id"])
                await self.assert_replay_refused(created, result["task_id"])
                await self.assert_rollback_refused(created)

    async def test_third_state_after_error_requires_manual_review(self):
        created = await self.approved()
        self.third_state_then_fail()
        result = await self.apply(created)

        self.assertEqual(self.ws.entries[TARGET]["icon"], "mdi:someone-else")
        self.assertEqual(result["task_state"], "manual_review_required")
        record = self.record(result["task_id"])
        self.assertEqual(record.normalized_outcome, "manual_review_required")
        self.assertTrue(record.terminal)
        self.assertEqual(
            record.evidence["manual_review_reason_code"], ERROR_RESULT_READBACK_UNEXPLAINED
        )
        self.assert_uncertain_dispatch_recorded(result["task_id"])
        await self.assert_replay_refused(created, result["task_id"])
        await self.assert_rollback_refused(created)


class ReadbackUnavailableTests(LoopbackRegistryTestCase):
    async def test_readback_unavailable_stays_uncertain_and_never_redispatches(self):
        created = await self.approved()
        self.assign_then_fail("unknown_error")
        self.peer.readback_fault = "unknown_error"
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "observing")
        self.assertIs(result["provider_dispatch_occurred"], True)
        record = self.record(result["task_id"])
        self.assertEqual(record.normalized_outcome, "observing")
        self.assertFalse(record.terminal)
        self.assertIn("readback_unavailable", self.all_codes(result["task_id"]))
        self.assert_uncertain_dispatch_recorded(result["task_id"])
        self.assertEqual(
            self.repository.get(created["plan_id"]).status, PlanStatus.VERIFICATION_REQUIRED
        )
        await self.assert_rollback_refused(created)

        replay = await self.apply(created)
        self.assertIs(replay["task_reused"], True)
        self.assertIs(replay["redispatch_performed"], False)
        self.assertEqual(len(self.ws.updates()), 1)

        # The evidence window then closes without a readback: manual review,
        # never a confirmed non-mutation and never a second update.
        self.clock[0] += timedelta(minutes=5)
        expired = await self.apply(created)
        self.assertEqual(expired["task_state"], "manual_review_required")
        self.assertEqual(self.record(result["task_id"]).normalized_outcome, "manual_review_required")
        self.assertFalse(self.all_codes(result["task_id"]) & NON_MUTATION_CLAIMS)
        self.assertEqual(len(self.ws.updates()), 1)


class RecoveryReadbackTests(LoopbackRegistryTestCase):
    """Recovery keeps the error-result mapping from the persisted dispatch codes.

    ``recover`` is given only durable evidence (``dispatch_diagnostic_codes``),
    so this drives the shipped adapter's ``recover`` and ``verify`` with the
    prepared operation the real executor used.
    """

    async def captured_operation(self) -> tuple[Any, Any]:
        captured: dict[str, Any] = {}
        original = ConfigurationOperationAdapter.observe

        async def spy(adapter: Any, operation: Any, dispatch: Any) -> Any:
            captured["adapter"], captured["operation"] = adapter, operation
            return await original(adapter, operation, dispatch)

        created = await self.approved()
        self.assign_then_fail("unknown_error")
        self.peer.readback_fault = "unknown_error"
        with patch.object(ConfigurationOperationAdapter, "observe", spy):
            result = await self.apply(created)
        self.assertEqual(result["task_state"], "observing")
        self.peer.readback_fault = None
        self.peer.update_fault = None
        return captured["adapter"], captured["operation"]

    @staticmethod
    def recovery_context(codes: tuple[str, ...]) -> InternalRecoveryContext:
        return InternalRecoveryContext(
            dispatch_intent_recorded=True,
            provider_invocation_may_have_occurred=True,
            provider_response_received=True,
            prior_observation_attempts=1,
            prior_verification_attempts=0,
            post_dispatch_deadline=None,
            dispatch_diagnostic_codes=codes,
        )

    async def recovered(self, codes: tuple[str, ...]) -> Any:
        adapter, operation = self.captured_operation_value
        observation = await adapter.recover(operation, context=self.recovery_context(codes))
        return await adapter.verify(operation, observation)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.captured_operation_value = await self.captured_operation()
        self.applied = (self.ws.entries[TARGET]["name"], self.ws.entries[TARGET]["icon"])
        self.dispatch_codes = (ERROR_RESULT_AFTER_SEND, "provider_response_indeterminate")

    async def test_recovered_planned_state_is_verified(self):
        self.assertEqual(self.applied, ("Porch sensor", "mdi:new"))
        verification = await self.recovered(self.dispatch_codes)
        self.assertEqual(verification.outcome.value, "succeeded_verified")

    async def test_recovered_prior_state_is_verified_not_applied(self):
        self.ws.set_external(TARGET, name=None, icon="mdi:old")
        self.ws.entries[TARGET]["modified_at"] = 1.0
        verification = await self.recovered(self.dispatch_codes)
        self.assertEqual(verification.outcome.value, "verification_mismatch")
        self.assertIs(verification.verified, False)

    async def test_recovered_third_state_requires_manual_review(self):
        self.ws.set_external(TARGET, icon="mdi:someone-else")
        verification = await self.recovered(self.dispatch_codes)
        self.assertEqual(verification.outcome.value, "manual_review_required")
        self.assertEqual(
            verification.manual_review_reason_code, ERROR_RESULT_READBACK_UNEXPLAINED
        )

    async def test_lost_response_third_state_mapping_is_unchanged(self):
        # Without the error-result code (a lost response), a third state keeps
        # the existing verification-mismatch outcome.
        self.ws.set_external(TARGET, icon="mdi:someone-else")
        verification = await self.recovered(("provider_response_indeterminate",))
        self.assertEqual(verification.outcome.value, "verification_mismatch")


class P3StaysConfirmedTests(LoopbackRegistryTestCase):
    async def test_unauthorized_result_stays_confirmed_non_mutation(self):
        for code in ("unauthorized", "forbidden"):
            with self.subTest(code=code):
                entity = f"sensor.denied_{code}"
                self.add_entity(entity)
                created = await self.create(entity_id=entity, name="Probe")
                await self.approve(created)
                self.fail_without_change(code)
                updates = len(self.ws.updates())
                commands = len(self.ws.commands)
                result = await self.apply(created)

                self.assertEqual(result["task_state"], "failed_post_dispatch")
                record = self.record(result["task_id"])
                self.assertEqual(record.normalized_outcome, "dispatch_failed_confirmed")
                self.assertIs(record.provider_response_received, True)
                codes = self.event_codes(result["task_id"], "dispatch_result_recorded")
                self.assertEqual(
                    codes, {"provider_rejected_mutation", "provider_permission_denied"}
                )
                self.assertNotIn(ERROR_DIAGNOSTIC, self.all_codes(result["task_id"]))
                # No readback after a P3 refusal: the update was the last command.
                self.assertEqual(len(self.ws.updates()), updates + 1)
                self.assertEqual(self.ws.commands[-1]["type"], UPDATE)
                self.assertGreater(len(self.ws.commands), commands)
                await self.assert_replay_refused(created, result["task_id"])
                await self.assert_rollback_refused(created)


class PreSendErrorsUnchangedTests(LoopbackRegistryTestCase):
    async def test_stale_reference_is_still_refused_before_dispatch(self):
        created = await self.create(entity_id=TARGET, add_labels=["outdoor"], name="Probe")
        await self.approve(created)
        self.ws.labels = [
            {"label_id": "night", "name": "Night"},
            {"label_id": "outdoor", "name": "Outside"},
        ]
        self.assign_then_fail("unknown_error")
        result = await self.apply(created)

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(self.record(result["task_id"]).normalized_outcome, "preflight_rejected")
        self.assertEqual(self.ws.updates(), [])

    async def test_area_name_rule_is_still_refused_at_planning(self):
        self.ws.areas = [{"area_id": "porch", "name": "Porch"}]
        self.add_entity("sensor.bound", device_id="dev1", original_name=None)
        self.assign_then_fail("unknown_error")
        with self.assertRaises(GovernanceError) as raised:
            await self.create(entity_id="sensor.bound", area_id="porch")
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(
            raised.exception.details["reason_code"], "entity_metadata_area_requires_own_name"
        )
        self.assertIs(raised.exception.details["provider_dispatch_occurred"], False)
        self.assertEqual(self.ws.updates(), [])


class _Client:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error
        self.sent: list[dict[str, Any]] = []

    async def command(self, payload: dict[str, Any]) -> Any:
        self.sent.append(payload)
        if self.error is not None:
            raise self.error
        return {"entity_entry": {"entity_id": TARGET}}


class GatewayClassificationTests(unittest.IsolatedAsyncioTestCase):
    """The metadata gateway's classification of each client failure."""

    COMMAND = {"type": UPDATE, "entity_id": TARGET, "name": "Porch sensor"}

    async def update(self, error: BaseException | None) -> tuple[_Client, BaseException | None]:
        client = _Client(error)
        try:
            await EntityRegistryMetadataGateway(None, client).update(TARGET, dict(self.COMMAND))
        except BaseException as exc:  # noqa: BLE001 - the classification is under test
            return client, exc
        return client, None

    async def test_error_result_is_mutation_unknown_not_not_dispatched(self):
        for status in (None, 404):
            with self.subTest(status=status):
                details = {"provider_response_received": True}
                if status is not None:
                    details["status"] = status
                client, raised = await self.update(HomeAssistantApiError(details=details))
                self.assertEqual(len(client.sent), 1)
                self.assertIsInstance(raised, EntityMetadataUpdateErrorResult)
                self.assertNotIsInstance(raised, ConfigurationMutationNotDispatchedError)
                self.assertIsNot(getattr(raised, "mutation_dispatched", None), False)
                self.assertEqual(raised.details["reason"], ENTITY_METADATA_ERROR_RESULT_REASON)
                self.assertIs(raised.details["provider_response_received"], True)

    async def test_p3_result_stays_confirmed_not_dispatched(self):
        client, raised = await self.update(
            AuthorizationError(details={"provider_response_received": True})
        )
        self.assertIsInstance(raised, ConfigurationMutationNotDispatchedError)
        self.assertNotIsInstance(raised, EntityMetadataUpdateErrorResult)
        self.assertEqual(raised.details["reason"], ENTITY_METADATA_UNAUTHORIZED_REASON)
        self.assertIs(raised.details["provider_response_received"], True)

    async def test_pre_command_failures_are_unchanged(self):
        # Authentication refused before the command is sent (b2-A).
        _, raised = await self.update(AuthorizationError(details={}))
        self.assertIsInstance(raised, ConfigurationMutationNotDispatchedError)
        self.assertIs(raised.details["provider_response_received"], False)
        # No response at all stays a raw transport error (lost response).
        error = HomeAssistantApiError(details={})
        _, raised = await self.update(error)
        self.assertIs(raised, error)
        timeout = HomeAssistantTimeoutError()
        _, raised = await self.update(timeout)
        self.assertIs(raised, timeout)

    async def test_closed_payload_violation_is_refused_before_send(self):
        client = _Client(None)
        with self.assertRaises(ConfigurationMutationNotDispatchedError) as raised:
            await EntityRegistryMetadataGateway(None, client).update(
                TARGET, {"type": UPDATE, "entity_id": TARGET, "options_domain": "x"}
            )
        self.assertEqual(raised.exception.details["reason"], "closed_payload_violation")
        self.assertEqual(client.sent, [])


if __name__ == "__main__":
    unittest.main()
