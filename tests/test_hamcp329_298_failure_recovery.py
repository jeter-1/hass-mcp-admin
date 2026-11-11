"""HAMCP-329/298 failure preservation and recovery for created objects.

The contract is ``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md``
§3.2, §3.5 and the §6 rows on rejected, uncertain and partial execution.  A
send is made at most once: a rejected send reports no mutation, an uncertain
send is settled by reading only, a failed step stops the plan, and a later
process observes without sending again.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any
import unittest
from unittest import mock

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    created_object_delete,
    created_object_rollback as rollback_model,
)

from tests.hamcp329_298_fixtures import (  # noqa: E402
    CreatedObjectServiceTestCase,
    EVENING,
    HELPER_PROFILE,
    LAMP,
    SCENE_PREFIX,
    SCENE_PROFILE,
    SimulatedProcessLoss,
    SyntheticCreatedObjectWebSocket,
    boolean_config,
    helper_operation,
    number_config,
    registry_id,
    slug,
)


HELPERS = ("input_boolean", "input_number")
SCENE_DELETE = ("DELETE", f"{SCENE_PREFIX}{EVENING}")


def lost_response() -> HomeAssistantApiError:
    """The transport failed with no provider response: the send is uncertain."""

    return HomeAssistantApiError(details={"provider_response_received": False})


def helper_config(domain: str, name: str | None = None) -> dict[str, Any]:
    if domain == "input_boolean":
        return boolean_config(name or "HVAC override")
    return number_config(name or "HVAC target")


def helper_delete(domain: str, object_id: str) -> dict[str, Any]:
    return {"type": f"{domain}/delete", f"{domain}_id": object_id}


class FailureTestCase(CreatedObjectServiceTestCase):
    async def helper_rollback(self, domain: str) -> tuple[str, dict, dict]:
        config = helper_config(domain)
        created = await self.created_helper(domain, config)
        _rollback, plan = await self.rollback_plan(created)
        return slug(config["name"]), created, plan

    async def scene_rollback(self) -> tuple[dict, dict]:
        created = await self.created_scene()
        _rollback, plan = await self.rollback_plan(created)
        return created, plan

    def assert_child(self, plan: dict, outcome: str, *codes: str) -> None:
        self.assertEqual(
            self.children(plan["plan_id"]), [("delete_1", "terminal", outcome, 1)]
        )
        self.assertLessEqual(set(codes), self.codes(plan["plan_id"]))

    async def assert_no_recreate(self, plan: dict) -> None:
        """A delete that was not verified absent is never offered a recreate."""

        mark = self.mark()
        error = await self.refused(plan["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE, error.details)
        self.assertEqual(
            error.details["reason"], "no_verified_absent_deletion", error.details
        )
        self.assertEqual(self.writes_since(mark), [])


class RejectedDeleteTests(FailureTestCase):
    """§3.2: Core answering "not there" is a confirmed non-mutation."""

    async def test_a_scene_delete_answered_400_is_a_confirmed_non_mutation(self):
        _created, plan = await self.scene_rollback()
        # Removed by a user after preflight, just before the DELETE is answered.
        self.config_rest.before_delete = lambda: self.remove_scene(EVENING)
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertEqual(result["dispatch_phase"], "sent_rejected_no_mutation")
        self.assertIs(result["redispatch_performed"], False)
        self.assert_child(
            plan,
            "dispatch_failed_confirmed",
            "configuration_delete_rejected",
            "provider_rejected_mutation",
        )
        self.assertEqual(self.writes_since(mark), [SCENE_DELETE])
        await self.assert_no_recreate(plan)

    async def test_a_helper_delete_answered_not_found_is_a_confirmed_non_mutation(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                self.ws.before_helper_delete = self.remove_helper
                mark = self.mark()
                result = await self.apply(plan)
                self.ws.before_helper_delete = None
                self.assertEqual(result["task_state"], "failed_post_dispatch", result)
                self.assertEqual(result["dispatch_phase"], "sent_rejected_no_mutation")
                self.assertIs(result["redispatch_performed"], False)
                self.assert_child(
                    plan,
                    "dispatch_failed_confirmed",
                    "configuration_delete_rejected",
                    "provider_rejected_mutation",
                )
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )
                await self.assert_no_recreate(plan)


class DuplicateFramePeer:
    """Loopback Core peer with the ``ws_delete_item`` not-found quirk.

    ``helpers/collection.py`` sends ``ERR_NOT_FOUND`` and then falls through to
    ``send_result`` for the same message id.  State lives in the synthetic
    store, so a found item is deleted and answered once, as Core does.
    """

    def __init__(self, backend: SyntheticCreatedObjectWebSocket) -> None:
        self.backend = backend
        self.frames: list[list[dict[str, Any]]] = []
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
        try:
            result = await self.backend.command(command)
        except HomeAssistantApiError:
            sent = [
                {
                    "id": message_id,
                    "type": "result",
                    "success": False,
                    "error": {"code": "not_found", "message": "synthetic"},
                },
                {"id": message_id, "type": "result", "success": True, "result": None},
            ]
        else:
            sent = [
                {"id": message_id, "type": "result", "success": True, "result": result}
            ]
        self.frames.append(sent)
        for frame in sent:
            await ws.send_json(frame)
        await ws.close()
        return ws


def loopback_client(url: str) -> HomeAssistantWebSocketClient:
    return HomeAssistantWebSocketClient(
        Settings(
            ha_url=url,
            ha_token="synthetic-no-real-token",
            access_secret="synthetic-no-real-secret",
            port=8100,
            audit_path="unused-synthetic",
            rate_limit_per_minute=120,
            rate_limit_burst=20,
            destructive_services=frozenset(),
            ha_timeout_seconds=5.0,
        )
    )


class DuplicateResultFrameClientTests(unittest.IsolatedAsyncioTestCase):
    """The shipped WebSocket client against the two-frame not-found answer."""

    async def asyncSetUp(self) -> None:
        self.backend = SyntheticCreatedObjectWebSocket()
        self.peer = DuplicateFramePeer(self.backend)
        await self.peer.start()
        self.addAsyncCleanup(self.peer.stop)
        self.client = loopback_client(self.peer.url)

    async def test_the_first_result_frame_is_the_reply(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                command = created_object_delete.helper_delete_command(
                    domain, f"{domain}.never_created"
                )
                with self.assertRaises(HomeAssistantApiError) as raised:
                    await self.client.command(dict(command))
                details = raised.exception.details
                self.assertEqual(details["status"], 404, details)
                self.assertIs(details["provider_response_received"], True)
                error, stray = self.peer.frames[-1]
                self.assertIs(error["success"], False)
                self.assertIs(stray["success"], True)
                self.assertEqual(error["id"], stray["id"])

    async def test_the_stray_frame_is_never_the_reply_to_a_later_command(self):
        self.backend.add_helper("input_boolean", boolean_config("Kept"))
        with self.assertRaises(HomeAssistantApiError):
            await self.client.command(
                dict(
                    created_object_delete.helper_delete_command(
                        "input_boolean", "input_boolean.never_created"
                    )
                )
            )
        # The next command is answered by its own single frame.
        listed = await self.client.command({"type": "input_boolean/list"})
        self.assertEqual([item["id"] for item in listed], ["kept"])
        self.assertIsNone(
            await self.client.command(
                dict(
                    created_object_delete.helper_delete_command(
                        "input_boolean", "input_boolean.kept"
                    )
                )
            )
        )
        self.assertEqual(self.backend.helpers["input_boolean"], {})
        self.assertEqual([len(frames) for frames in self.peer.frames], [2, 1, 1])


class _DeleteThroughLoopback:
    """Every helper delete crosses the real client; the rest stays synthetic."""

    def __init__(self, fake: Any, client: HomeAssistantWebSocketClient) -> None:
        self._fake = fake
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fake, name)

    async def command(self, payload: dict) -> Any:
        if str(payload.get("type")).endswith("/delete"):
            return await self._client.command(payload)
        return await self._fake.command(payload)


class DuplicateResultFrameRollbackTests(FailureTestCase):
    async def test_not_found_then_a_stray_result_is_a_rejected_delete(self):
        peer = DuplicateFramePeer(self.ws)
        await peer.start()
        self.addAsyncCleanup(peer.stop)
        router = _DeleteThroughLoopback(self.ws, loopback_client(peer.url))
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                self.ws.before_helper_delete = self.remove_helper
                mark = self.mark()
                with mock.patch.object(
                    created_object_delete.CreatedObjectDeleteGateway,
                    "websocket_client",
                    new_callable=mock.PropertyMock,
                    return_value=router,
                ):
                    result = await self.apply(plan)
                self.ws.before_helper_delete = None
                self.assertEqual(len(peer.frames[-1]), 2)
                self.assertEqual(result["task_state"], "failed_post_dispatch", result)
                self.assertEqual(result["dispatch_phase"], "sent_rejected_no_mutation")
                self.assert_child(
                    plan, "dispatch_failed_confirmed", "provider_rejected_mutation"
                )
                self.assertNotIn("exact_readback_verified", self.codes(plan["plan_id"]))
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )


class UncertainDeleteTests(FailureTestCase):
    """§6: an uncertain send is settled by readback and never sent again."""

    async def test_an_uncertain_scene_delete_that_did_not_apply(self):
        created, plan = await self.scene_rollback()
        self.config_rest.delete_failure = lost_response()
        mark = self.mark()
        result = await self.apply(plan)
        self.config_rest.delete_failure = None
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertEqual(result["dispatch_phase"], "intent_recorded_send_uncertain")
        self.assertIs(result["redispatch_performed"], False)
        self.assert_child(
            plan,
            "verification_mismatch",
            "provider_response_indeterminate",
            "config_still_present",
            "prior_state_observed",
        )
        self.assertEqual(self.writes_since(mark), [SCENE_DELETE])
        self.assertIn(EVENING, self.config_rest.scenes)
        # Asking again returns the same failed plan; it is never sent again.
        mark = self.mark()
        again = await self.service.rollback_change(created["plan_id"])
        self.assertEqual(again["rollback_plan_id"], plan["plan_id"])
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assertEqual(raised.exception.code, ErrorCode.DUPLICATE_APPLY_ATTEMPT)
        self.assertEqual(self.writes_since(mark), [])
        await self.assert_no_recreate(plan)

    async def test_an_uncertain_scene_delete_that_applied(self):
        _created, plan = await self.scene_rollback()

        def applied_then_lost() -> None:
            self.remove_scene(EVENING)
            raise lost_response()

        self.config_rest.before_delete = applied_then_lost
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(result["dispatch_phase"], "intent_recorded_send_uncertain")
        self.assert_child(
            plan,
            "succeeded_verified",
            "provider_response_indeterminate",
            "exact_readback_verified",
        )
        self.assertEqual(self.writes_since(mark), [SCENE_DELETE])

    async def test_an_uncertain_helper_delete_that_did_not_apply(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                self.ws.failures[f"{domain}/delete"] = lost_response()
                mark = self.mark()
                result = await self.apply(plan)
                del self.ws.failures[f"{domain}/delete"]
                self.assertEqual(result["task_state"], "failed_post_dispatch", result)
                self.assertEqual(
                    result["dispatch_phase"], "intent_recorded_send_uncertain"
                )
                self.assert_child(
                    plan,
                    "verification_mismatch",
                    "provider_response_indeterminate",
                    "config_still_present",
                )
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )
                self.assertIn(object_id, self.ws.helpers[domain])
                await self.assert_no_recreate(plan)

    async def test_an_uncertain_helper_delete_that_applied(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)

                def applied_then_lost(kind: str, item: str) -> None:
                    self.remove_helper(kind, item)
                    raise lost_response()

                self.ws.before_helper_delete = applied_then_lost
                mark = self.mark()
                result = await self.apply(plan)
                self.ws.before_helper_delete = None
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assert_child(
                    plan,
                    "succeeded_verified",
                    "provider_response_indeterminate",
                    "exact_readback_verified",
                )
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )


class DeleteReadbackTests(FailureTestCase):
    """§3.2: only stored, registry and state absence together are success."""

    async def test_a_scene_delete_core_ignored_is_a_mismatch(self):
        _created, plan = await self.scene_rollback()
        self.config_rest.ignore_deletes = True
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assert_child(plan, "verification_mismatch", "config_still_present")
        self.assertEqual(len(self.config_rest.scene_deletes()), 1)
        await self.assert_no_recreate(plan)

    async def test_a_helper_delete_core_ignored_is_a_mismatch(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                self.ws.ignore_helper_deletes = True
                mark = self.mark()
                result = await self.apply(plan)
                self.ws.ignore_helper_deletes = False
                self.assertEqual(result["task_state"], "failed_post_dispatch", result)
                self.assert_child(plan, "verification_mismatch", "config_still_present")
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )
                await self.assert_no_recreate(plan)

    async def test_a_scene_whose_entity_stays_registered_is_manual_review(self):
        _created, plan = await self.scene_rollback()
        self.config_rest.delete_hook_removes_entry = False
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "manual_review_required", result)
        self.assert_child(
            plan, "manual_review_required", "config_removed_runtime_unverified"
        )
        self.assertNotIn("exact_readback_verified", self.codes(plan["plan_id"]))
        self.assertEqual(len(self.config_rest.scene_deletes()), 1)
        await self.assert_no_recreate(plan)

    async def test_a_helper_whose_entity_stays_registered_is_manual_review(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                self.ws.after_helper_delete = self.ws.register_helper
                mark = self.mark()
                result = await self.apply(plan)
                self.ws.after_helper_delete = None
                self.assertEqual(
                    result["task_state"], "manual_review_required", result
                )
                self.assert_child(
                    plan, "manual_review_required", "config_removed_runtime_unverified"
                )
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )
                await self.assert_no_recreate(plan)


class SceneCreateFailureTests(FailureTestCase):
    """§3.1: a scene create is sent once and is success only when read back."""

    async def assert_not_created(self, outcome: str, code: str) -> dict:
        plan = await self.scene_plan()
        await self.approve(plan)
        mark = self.mark()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertIs(result["redispatch_performed"], False)
        self.assertEqual(
            self.children(plan["plan_id"]), [("scene_create_1", "terminal", outcome, 1)]
        )
        self.assertIn(code, self.codes(plan["plan_id"]))
        self.assertNotIn("exact_readback_verified", self.codes(plan["plan_id"]))
        self.assertEqual(len(self.writes_since(mark)), 1, "the POST is sent once")
        self.assertEqual(self.identity_values(plan["plan_id"]), [None])
        # Not success, so never rollback-eligible, and nothing is read for it.
        mark = self.mark()
        await self.refused_rollback(
            plan, {"scene_create_1": rollback_model.NOT_VERIFIED}
        )
        self.assertEqual(self.reads_since(mark), [])
        self.assertEqual(self.config_rest.scene_deletes(), [])
        return result

    async def test_a_rejected_post_creates_nothing(self):
        self.config_rest.scene_post_failure = HomeAssistantApiError(
            details={"status": 400, "provider_response_received": True}
        )
        await self.assert_not_created(
            "dispatch_failed_confirmed", "provider_rejected_mutation"
        )
        self.assertNotIn(EVENING, self.config_rest.scenes)

    async def test_a_lost_post_that_did_not_apply_is_a_mismatch(self):
        self.config_rest.scene_post_failure = lost_response()
        result = await self.assert_not_created(
            "verification_mismatch", "scene_create_not_applied"
        )
        self.assertEqual(result["dispatch_phase"], "intent_recorded_send_uncertain")
        self.assertIn("provider_response_indeterminate", self.codes(result["plan"]["plan_id"]))

    async def test_a_post_core_ignored_is_a_mismatch(self):
        self.config_rest.ignore_scene_posts = True
        await self.assert_not_created(
            "verification_mismatch", "scene_create_not_applied"
        )

    async def test_a_stored_body_that_differs_is_a_mismatch(self):
        self.config_rest.scene_drift = {"icon": "mdi:drift"}
        await self.assert_not_created(
            "verification_mismatch", "scene_create_stored_configuration_differs"
        )
        # The differing scene is left for the user; nothing deletes it.
        self.assertIn(EVENING, self.config_rest.scenes)

    # -- §3.3 readback: exactly one ``scene.*`` entry, platform homeassistant --
    def settles(self, change: Any) -> None:
        """Run ``change(entity id, entry)`` once the POST has registered it."""

        def settle(config_id: str) -> None:
            entity_id = self.config_rest.scene_entity(config_id)
            change(entity_id, self.ws.entries[entity_id])

        self.config_rest.after_scene_post = settle

    def second_entry(self, entity_id: str, entry: dict, **changes: Any) -> None:
        other = {
            **deepcopy(entry),
            "entity_id": f"{entity_id}_2",
            "id": registry_id("synthetic_second_scene_entry"),
            **changes,
        }
        self.ws.entries[other["entity_id"]] = other

    async def assert_registry_identity_refused(self) -> None:
        result = await self.assert_not_created(
            "verification_mismatch", "scene_create_registry_entry_ambiguous"
        )
        codes = self.codes(result["plan"]["plan_id"])
        # A wrong entry's metadata is never reported as a created scene's.
        self.assertEqual(
            {code for code in codes if code.startswith("scene_create_observed_")},
            set(),
        )
        # The stored scene is left for the user; nothing deletes it.
        self.assertIn(EVENING, self.config_rest.scenes)

    async def test_a_second_registry_entry_for_the_config_id_is_a_mismatch(self):
        self.settles(self.second_entry)
        await self.assert_registry_identity_refused()

    async def test_a_second_entry_beside_a_restored_disabled_one_is_a_mismatch(self):
        def change(entity_id: str, entry: dict) -> None:
            entry["disabled_by"] = "user"
            self.config_rest.states.discard(entity_id)
            self.second_entry(entity_id, entry, disabled_by=None)

        self.settles(change)
        await self.assert_registry_identity_refused()

    async def test_a_second_entry_on_another_platform_is_a_mismatch(self):
        self.settles(
            lambda entity_id, entry: self.second_entry(
                entity_id, entry, platform="synthetic_platform"
            )
        )
        await self.assert_registry_identity_refused()

    async def test_one_entry_on_another_platform_is_a_mismatch(self):
        self.settles(
            lambda _entity_id, entry: entry.update(platform="synthetic_platform")
        )
        await self.assert_registry_identity_refused()

    async def test_a_disabled_entry_on_another_platform_is_still_a_mismatch(self):
        def change(entity_id: str, entry: dict) -> None:
            entry.update(platform="synthetic_platform", disabled_by="user")
            self.config_rest.states.discard(entity_id)

        self.settles(change)
        await self.assert_registry_identity_refused()

    async def test_a_lost_post_that_applied_is_verified_by_readback(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        real = self.config_rest.request

        async def stored_then_lost(method: str, path: Any, **kwargs: Any) -> Any:
            answer = await real(method, path, **kwargs)
            if method == "POST" and str(path).startswith(SCENE_PREFIX):
                raise lost_response()
            return answer

        with mock.patch.object(self.config_rest, "request", stored_then_lost):
            result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(result["dispatch_phase"], "intent_recorded_send_uncertain")
        self.assertIs(result["redispatch_performed"], False)
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        (record,) = self.identity_values(plan["plan_id"])
        self.assertEqual(record["outcome"], "captured")

    async def test_registration_is_awaited_by_a_bounded_poll_not_assumed(self):
        # The entity registers only on the third config read after the POST.
        self.config_rest.scene_registers_after_gets = 3
        plan = await self.scene_plan()
        await self.approve(plan)
        self.poll_sleeps.clear()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.poll_sleeps, [0.5, 0.5])
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        (record,) = self.identity_values(plan["plan_id"])
        self.assertEqual(record["outcome"], "captured")


class PartialRollbackTests(FailureTestCase):
    """§3.4.4: strict reverse order, stop at the first failure, no automatic undo."""

    async def consumer_plan(self, domain: str = "input_boolean") -> dict:
        """Create a helper, then update an existing automation that uses it."""

        self.add("automation", "consumer")
        proposed = deepcopy(self.config_rest.configs["automation"]["consumer"])
        proposed["description"] = "uses the helper"
        return await self.applied(
            await self.configuration_plan(
                helper_operation(domain, helper_config(domain)),
                {
                    "operation_id": "update_consumer",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "consumer",
                    "depends_on": ["create_helper"],
                    "proposed_config": proposed,
                },
            )
        )

    async def test_a_stale_prerequisite_restore_excludes_the_helper_delete(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                created = await self.consumer_plan(domain)
                # A user edited the consumer afterwards: it cannot be restored,
                # so the helper it may still use must not be deleted.
                self.config_rest.configs["automation"]["consumer"]["description"] = (
                    "changed by the user"
                )
                error = await self.refused_rollback(
                    created,
                    {
                        "update_consumer": rollback_model.RESTORE_TARGET_CHANGED,
                        "create_helper": rollback_model.PREREQUISITE_EXCLUDED,
                    },
                )
                excluded = {
                    item["operation_id"]: item
                    for item in error.details["operations_excluded"]
                }
                self.assertEqual(
                    excluded["create_helper"]["prerequisite"], "update_consumer"
                )
                self.assertIn(slug(helper_config(domain)["name"]), self.ws.helpers[domain])
                self.assertEqual(self.ws.helper_commands("delete"), [])
                del self.config_rest.configs["automation"]["consumer"]
                self.ws.entries.pop("automation.consumer", None)

    async def test_a_failed_restore_leaves_the_helper_delete_unattempted(self):
        created = await self.consumer_plan()
        rollback, plan = await self.rollback_plan(created)
        self.assertEqual(rollback["operations_to_delete"], ["create_helper"])
        (restore_id,) = rollback["operations_to_restore"]
        self.assertEqual(
            [operation.operation_id for operation in self.operations(plan["plan_id"])],
            [restore_id, "delete_1"],
        )
        real = self.config_rest.request

        async def failing_restore(method: str, path: Any, **kwargs: Any) -> Any:
            if method == "POST" and "automation" in str(path):
                raise HomeAssistantApiError(
                    details={"status": 500, "provider_response_received": True}
                )
            return await real(method, path, **kwargs)

        with mock.patch.object(self.config_rest, "request", failing_restore):
            result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        children = self.children(plan["plan_id"])
        self.assertEqual(children[0][:2], (restore_id, "terminal"))
        self.assertNotEqual(children[0][2], "succeeded_verified")
        self.assertEqual(children[1], ("delete_1", "not_started", None, 0))
        self.assertEqual(self.ws.helper_commands("delete"), [])
        self.assertIn("hvac_override", self.ws.helpers["input_boolean"])

    async def test_a_failed_first_delete_leaves_the_second_unattempted(self):
        created = await self.applied(
            await self.configuration_plan(
                helper_operation(
                    "input_boolean", boolean_config(), operation_id="create_a"
                ),
                helper_operation(
                    "input_number",
                    number_config(),
                    operation_id="create_b",
                    depends_on=["create_a"],
                ),
            )
        )
        rollback, plan = await self.rollback_plan(created)
        self.assertEqual(rollback["operations_to_delete"], ["create_b", "create_a"])
        self.ws.before_helper_delete = self.remove_helper
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "failed_post_dispatch", result)
        self.assertEqual(
            self.children(plan["plan_id"]),
            [
                ("delete_1", "terminal", "dispatch_failed_confirmed", 1),
                ("delete_2", "not_started", None, 0),
            ],
        )
        self.assertEqual(
            self.ws.helper_commands("delete"),
            [helper_delete("input_number", "hvac_target")],
        )
        self.assertIn("hvac_override", self.ws.helpers["input_boolean"])


class CrashRecoveryTests(FailureTestCase):
    """§6: a later process observes; it never sends the write a second time."""

    @staticmethod
    def crash(*_args: Any) -> None:
        raise SimulatedProcessLoss()

    def assert_observed_only(self, plan: dict) -> None:
        self.assertLessEqual(
            {"observation_only", "redispatch_prohibited"}, self.codes(plan["plan_id"])
        )

    async def test_a_scene_delete_that_applied_before_the_crash(self):
        _created, plan = await self.scene_rollback()
        self.config_rest.after_delete = self.crash
        await self.crash_during(plan)
        self.config_rest.after_delete = None
        await self.restart_after()
        self.assertEqual(self.task_state(plan["plan_id"]), "succeeded_verified")
        self.assert_child(plan, "succeeded_verified", "exact_readback_verified")
        self.assert_observed_only(plan)
        self.assertEqual(self.config_rest.scene_deletes(), [SCENE_DELETE])

    async def test_a_scene_delete_that_did_not_apply_before_the_crash(self):
        _created, plan = await self.scene_rollback()
        self.config_rest.before_delete = self.crash
        await self.crash_during(plan)
        self.config_rest.before_delete = None
        await self.restart_after()
        self.assertEqual(self.task_state(plan["plan_id"]), "failed_post_dispatch")
        self.assert_child(plan, "verification_mismatch", "config_still_present")
        self.assert_observed_only(plan)
        await self.runtime.recover_once("periodic")
        self.assertEqual(self.config_rest.scene_deletes(), [SCENE_DELETE])
        self.assertIn(EVENING, self.config_rest.scenes)
        await self.assert_no_recreate(plan)

    async def test_a_helper_delete_that_applied_before_the_crash(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                mark = self.mark()
                self.ws.after_helper_delete = self.crash
                await self.crash_during(plan)
                self.ws.after_helper_delete = None
                await self.restart_after()
                self.assertEqual(
                    self.task_state(plan["plan_id"]), "succeeded_verified"
                )
                self.assert_child(plan, "succeeded_verified", "exact_readback_verified")
                self.assert_observed_only(plan)
                await self.runtime.recover_once("periodic")
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )

    async def test_a_helper_delete_that_did_not_apply_before_the_crash(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, _created, plan = await self.helper_rollback(domain)
                mark = self.mark()
                self.ws.before_helper_delete = self.crash
                await self.crash_during(plan)
                self.ws.before_helper_delete = None
                await self.restart_after()
                self.assertEqual(
                    self.task_state(plan["plan_id"]), "failed_post_dispatch"
                )
                self.assert_child(plan, "verification_mismatch", "config_still_present")
                self.assert_observed_only(plan)
                await self.runtime.recover_once("periodic")
                self.assertEqual(
                    self.writes_since(mark), [helper_delete(domain, object_id)]
                )
                self.assertIn(object_id, self.ws.helpers[domain])

    async def test_a_scene_create_that_applied_before_the_crash(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        real = self.config_rest.request

        async def stored_then_crash(method: str, path: Any, **kwargs: Any) -> Any:
            answer = await real(method, path, **kwargs)
            if method == "POST" and str(path).startswith(SCENE_PREFIX):
                raise SimulatedProcessLoss()
            return answer

        with mock.patch.object(self.config_rest, "request", stored_then_crash):
            await self.crash_during(plan)
        await self.restart_after()
        self.assertEqual(self.task_state(plan["plan_id"]), "succeeded_verified")
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("scene_create_1", "terminal", "succeeded_verified", 1)],
        )
        self.assert_observed_only(plan)
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        # The identity is captured by the recovering process, and only then
        # is the create rollback-eligible.
        (record,) = self.identity_values(plan["plan_id"])
        self.assertEqual(record["outcome"], "captured")
        rollback, _plan = await self.rollback_plan(plan, approve=False)
        self.assertEqual(rollback["operations_to_delete"], ["scene_create_1"])

    async def test_a_scene_create_that_did_not_apply_before_the_crash(self):
        plan = await self.scene_plan()
        await self.approve(plan)
        self.config_rest.before_scene_post = self.crash
        await self.crash_during(plan)
        self.config_rest.before_scene_post = None
        await self.restart_after()
        self.assertEqual(self.task_state(plan["plan_id"]), "failed_post_dispatch")
        self.assertEqual(
            self.children(plan["plan_id"]),
            [("scene_create_1", "terminal", "verification_mismatch", 1)],
        )
        self.assert_observed_only(plan)
        await self.runtime.recover_once("periodic")
        self.assertEqual(len(self.config_rest.scene_posts), 1)
        self.assertNotIn(EVENING, self.config_rest.scenes)
        await self.refused_rollback(
            plan, {"scene_create_1": rollback_model.NOT_VERIFIED}
        )


class RecreateRefusalTests(FailureTestCase):
    """A recreate is a governed create of its own, with the same refusals."""

    async def deleted_scene(self) -> dict:
        _created, plan = await self.scene_rollback()
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return plan

    async def deleted_helper(self, domain: str) -> tuple[str, dict]:
        object_id, _created, plan = await self.helper_rollback(domain)
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        return object_id, plan

    async def assert_unadmitted(self, plan: dict, profile: str) -> None:
        self.core.withdraw(profile)
        mark = self.mark()
        try:
            error = await self.refused(plan["plan_id"])
        finally:
            self.core.admit(profile)
        self.assertEqual(error.code, ErrorCode.PROVIDER_UNAVAILABLE, error.details)
        self.assertEqual(
            error.details["reason"], rollback_model.PENDING_ADMISSION, error.details
        )
        self.assertEqual(error.details["core_capability"], profile)
        self.assertEqual(self.reads_since(mark), [])

    async def test_a_scene_recreate_while_unadmitted_reads_nothing(self):
        await self.assert_unadmitted(await self.deleted_scene(), SCENE_PROFILE)

    async def test_a_helper_recreate_stays_under_the_existing_create_profile(self):
        # Design §5: helper creates, and so the helper recreate, stay under the
        # already-signed configuration profile.  The dormant delete profile
        # gates the delete only, and a recreate sends no delete.
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, plan = await self.deleted_helper(domain)
                self.core.withdraw(HELPER_PROFILE)
                try:
                    mark = self.mark()
                    _rollback, recreate = await self.rollback_plan(plan)
                    result = await self.apply(recreate)
                finally:
                    self.core.admit(HELPER_PROFILE)
                self.assertEqual(result["task_state"], "succeeded_verified", result)
                self.assertEqual(
                    [write["type"] for write in self.writes_since(mark)],
                    [f"{domain}/create"],
                )
                self.assertIn(object_id, self.ws.helpers[domain])

    async def test_a_scene_recreate_never_overwrites_a_scene_made_since(self):
        plan = await self.deleted_scene()
        self.config_rest.add_scene(
            EVENING, {"name": "A user's own", "entities": {LAMP: {"state": "on"}}}
        )
        mark = self.mark()
        error = await self.refused(plan["plan_id"])
        self.assertEqual(error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE, error.details)
        self.assertEqual(error.details["reason"], "no_verified_absent_deletion")
        self.assertEqual(
            error.details["current_state"], "scene_configuration_target_exists"
        )
        self.assertEqual(self.writes_since(mark), [])
        self.assertEqual(self.config_rest.scenes[EVENING]["name"], "A user's own")

    async def test_a_helper_recreate_never_overwrites_a_helper_made_since(self):
        for domain in HELPERS:
            with self.subTest(domain=domain):
                object_id, plan = await self.deleted_helper(domain)
                config = dict(helper_config(domain), icon="mdi:account")
                self.ws.add_helper(domain, config)
                mark = self.mark()
                error = await self.refused(plan["plan_id"])
                self.assertEqual(
                    error.code, ErrorCode.ROLLBACK_NOT_AVAILABLE, error.details
                )
                self.assertEqual(
                    error.details["reason"], "no_verified_absent_deletion"
                )
                self.assertEqual(self.writes_since(mark), [])
                self.assertEqual(
                    self.ws.helpers[domain][object_id]["icon"], "mdi:account"
                )


if __name__ == "__main__":
    unittest.main()
