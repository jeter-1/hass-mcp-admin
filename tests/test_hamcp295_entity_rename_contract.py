"""HAMCP-295 entity registry rename boundary (design section 3.1).

Request contract, closed payload, zero dispatch on violation, no fallback,
V9 preflight cases (stale pre-image refusal), readback and lock keys.  The
gateway is exercised against fake clients only; nothing here reaches Home
Assistant.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.errors import AuthorizationError, HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance import entity_rename as er  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    EntityMetadataProviderError,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationNotDispatchedError,
)
from ha_mcp_engineering.request_context import observe_provider_send  # noqa: E402


OLD = "light.kitchen"
NEW = "light.cook"


def entry(entity_id: str = OLD, **changes: Any) -> dict[str, Any]:
    value = {
        "entity_id": entity_id,
        "id": "registry-id-kitchen",
        "unique_id": "unique-kitchen",
        "platform": "synthetic",
        "device_id": None,
        "config_entry_id": "entry-1",
        "disabled_by": None,
        "entity_category": None,
        "has_entity_name": False,
        "original_name": "Kitchen",
        "name": None,
        "icon": None,
        "aliases": [],
        "area_id": None,
        "hidden_by": None,
        "labels": [],
        "categories": {},
        "modified_at": 1.0,
    }
    value.update(changes)
    return value


def binding() -> dict[str, Any]:
    return er.planning_binding(OLD, NEW, entry(), new_registered=False, new_in_states=False, impact_hash="i" * 64)


class RequestContractTests(unittest.TestCase):
    def test_valid_request_is_normalized(self):
        self.assertEqual(
            er.validate_rename_request({"entity_id": OLD, "new_entity_id": NEW, "title": "Rename"}),
            {"entity_id": OLD, "new_entity_id": NEW, "title": "Rename"},
        )

    def test_refusals(self):
        cases = (
            ({"entity_id": OLD}, er.REFUSAL_MISSING_FIELD),
            ({"entity_id": OLD, "new_entity_id": NEW, "rewrite": True}, er.REFUSAL_UNKNOWN_FIELD),
            ({"entity_id": OLD, "new_entity_id": NEW, "execution_mode": "x"}, er.REFUSAL_UNKNOWN_FIELD),
            ({"entity_id": OLD, "new_entity_id": NEW, "name": "Kitchen"}, er.REFUSAL_UNKNOWN_FIELD),
            ({"entity_id": "light.*", "new_entity_id": NEW}, er.REFUSAL_INVALID_ENTITY_ID),
            ({"entity_id": "Light.Kitchen", "new_entity_id": NEW}, er.REFUSAL_INVALID_ENTITY_ID),
            ({"entity_id": OLD, "new_entity_id": "light.Cook"}, er.REFUSAL_INVALID_NEW_ENTITY_ID),
            ({"entity_id": OLD, "new_entity_id": OLD}, er.REFUSAL_SAME_ID),
            ({"entity_id": OLD, "new_entity_id": "switch.cook"}, er.REFUSAL_CROSS_DOMAIN),
            ({"entity_id": OLD, "new_entity_id": NEW, "title": " "}, er.REFUSAL_INVALID_TEXT),
            ({"entity_id": OLD, "new_entity_id": NEW, "description": "x" * 2001}, er.REFUSAL_INVALID_TEXT),
            ("not-an-object", er.REFUSAL_MISSING_FIELD),
        )
        for arguments, code in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(er.EntityRenameRequestError) as caught:
                    er.validate_rename_request(arguments)
                self.assertEqual(caught.exception.code, code)

    def test_lock_keys(self):
        self.assertEqual(
            er.rename_lock_keys(OLD, NEW),
            {
                "exclusive": ("entity_registry:light.cook", "entity_registry:light.kitchen", "reload:entity_registry_rename"),
                "shared": ("core:home_assistant",),
            },
        )

    def test_provider_evidence_has_no_fallback(self):
        evidence = er.entity_rename_provider_evidence()
        self.assertEqual(evidence["fallback"], "none")
        self.assertFalse(evidence["fallback_occurred"])
        self.assertFalse(evidence["argument_constraints"]["fallback_allowed"])
        self.assertFalse(evidence["argument_constraints"]["arbitrary_payload_allowed"])
        self.assertEqual(evidence["argument_constraints"]["fields"], ["new_entity_id"])


class PlanningAndPreflightTests(unittest.TestCase):
    def test_binding_excludes_unbound_fields_and_binds_identity(self):
        value = binding()
        self.assertNotIn("modified_at", value["entry"])
        self.assertNotIn("entity_id", value["entry"])
        self.assertEqual(value["identity"]["unique_id"], "unique-kitchen")
        self.assertEqual(
            er.binding_fingerprint(value),
            er.binding_fingerprint(
                er.planning_binding(OLD, NEW, entry(modified_at=9.0, next_name_part="x"),
                                    new_registered=False, new_in_states=False, impact_hash="i" * 64)
            ),
        )
        self.assertNotEqual(er.forward_result_fingerprint(value), er.preimage_fingerprint(value))

    def test_planning_refuses_occupied_destination_and_malformed_source(self):
        for registered, in_states in ((True, False), (False, True)):
            with self.assertRaises(er.EntityRenameRequestError) as caught:
                er.planning_binding(OLD, NEW, entry(), new_registered=registered, new_in_states=in_states, impact_hash="i")
            self.assertEqual(caught.exception.code, er.REFUSAL_DESTINATION_OCCUPIED)
        with self.assertRaises(er.EntityRenameRequestError) as caught:
            er.planning_binding(OLD, NEW, entry(entity_id="light.other"), new_registered=False, new_in_states=False, impact_hash="i")
        self.assertEqual(caught.exception.code, er.REFUSAL_SOURCE_NOT_REGISTERED)

    def test_preflight_dispatch_case(self):
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=entry(modified_at=5.0), new_entry=None, new_in_states=False),
            er.PREFLIGHT_DISPATCH,
        )

    def test_stale_preimage_is_refused_as_source_changed(self):
        for changed in (
            {"name": "Renamed"},
            {"unique_id": "other"},
            {"platform": "other"},
            {"disabled_by": "user"},
            {"area_id": "kitchen"},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    er.classify_preflight(binding(), old_entry=entry(**changed), new_entry=None, new_in_states=False),
                    er.REFUSAL_SOURCE_CHANGED,
                )

    def test_destination_taken_after_planning_is_refused(self):
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=entry(), new_entry=None, new_in_states=True),
            er.REFUSAL_DESTINATION_OCCUPIED,
        )
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=entry(), new_entry=entry(NEW, id="other"), new_in_states=False),
            er.REFUSAL_DESTINATION_OCCUPIED,
        )

    def test_already_present_requires_the_exact_planned_result(self):
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=None, new_entry=entry(NEW), new_in_states=True),
            er.PREFLIGHT_ALREADY_PRESENT,
        )
        # Planned identity at <new> but another bound field changed.
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=None, new_entry=entry(NEW, icon="mdi:x"), new_in_states=True),
            er.REFUSAL_SOURCE_CHANGED,
        )
        # A different entry at <new>.
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=None, new_entry=entry(NEW, id="other", unique_id="u2"), new_in_states=True),
            er.REFUSAL_DESTINATION_OCCUPIED,
        )
        self.assertEqual(
            er.classify_preflight(binding(), old_entry=None, new_entry=None, new_in_states=False),
            er.REFUSAL_SOURCE_CHANGED,
        )

    def test_readback(self):
        self.assertEqual(er.verify_readback(binding(), old_entry=None, new_entry=entry(NEW, modified_at=7.0)), (True, ()))
        ok, reasons = er.verify_readback(binding(), old_entry=entry(), new_entry=entry(NEW, name="x"))
        self.assertFalse(ok)
        self.assertEqual(set(reasons), {"entry_fields_changed", "old_entity_id_still_registered"})
        self.assertEqual(er.verify_readback(binding(), old_entry=None, new_entry=None), (False, ("new_entity_id_not_registered",)))
        self.assertTrue(er.verify_not_applied(binding(), old_entry=entry(), new_entry=None))
        self.assertFalse(er.verify_not_applied(binding(), old_entry=entry(name="x"), new_entry=None))
        self.assertFalse(er.verify_not_applied(binding(), old_entry=entry(), new_entry=entry(NEW)))

    def test_compensation_is_the_exact_inverse(self):
        value = binding()
        inverse = er.compensation_binding(value)
        self.assertEqual((inverse["old_entity_id"], inverse["new_entity_id"]), (NEW, OLD))
        self.assertEqual(er.forward_result_fingerprint(inverse), er.preimage_fingerprint(value))
        self.assertEqual(
            er.compensation_command(value),
            {"type": "config/entity_registry/update", "entity_id": NEW, "new_entity_id": OLD},
        )

    def test_state_machine_is_observation_only(self):
        self.assertEqual(er.state_observation(False, True)["verification"], "observation_not_verified")


class FakeWebsocket:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.result = result
        self.error = error

    async def command(self, payload: dict[str, Any]) -> Any:
        self.sent.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


class FakeRest:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        self.requests.append((args, kwargs))
        raise AssertionError("rename never uses REST for a write or a fallback")


def _run(coroutine):
    return asyncio.run(coroutine)


class GatewayTests(unittest.TestCase):
    def test_exact_command_is_sent_once(self):
        websocket = FakeWebsocket({"entity_entry": entry(NEW)})
        sends: list[int] = []
        gateway = er.EntityRegistryRenameGateway(FakeRest(), websocket)
        with observe_provider_send(lambda: sends.append(1)):
            result = _run(gateway.rename(er.rename_command(OLD, NEW)))
        self.assertEqual(result["entity_entry"]["entity_id"], NEW)
        self.assertEqual(websocket.sent, [{"type": "config/entity_registry/update", "entity_id": OLD, "new_entity_id": NEW}])
        self.assertEqual(sends, [1])

    def test_closed_payload_violation_dispatches_nothing(self):
        violations = (
            {"type": "config/entity_registry/update", "entity_id": OLD, "new_entity_id": NEW, "name": "x"},
            {"type": "config/entity_registry/update", "entity_id": OLD, "new_entity_id": NEW, "disabled_by": "user"},
            {"type": "config/entity_registry/update", "entity_id": OLD},
            {"type": "config/entity_registry/remove", "entity_id": OLD, "new_entity_id": NEW},
            {"type": "config/entity_registry/update", "entity_id": OLD, "new_entity_id": "switch.cook"},
            {"type": "config/entity_registry/update", "entity_id": OLD, "new_entity_id": OLD},
            {"type": "config/entity_registry/update", "entity_id": "light.*", "new_entity_id": NEW},
            ["not", "a", "mapping"],
        )
        for command in violations:
            with self.subTest(command=command):
                websocket = FakeWebsocket({"entity_entry": entry(NEW)})
                rest = FakeRest()
                sends: list[int] = []
                gateway = er.EntityRegistryRenameGateway(rest, websocket)
                with observe_provider_send(lambda: sends.append(1)):
                    with self.assertRaises(ConfigurationMutationNotDispatchedError) as caught:
                        _run(gateway.rename(command))
                self.assertTrue(caught.exception.call_not_sent)
                self.assertEqual(caught.exception.details["reason"], er.REFUSAL_CLOSED_PAYLOAD)
                self.assertEqual((websocket.sent, rest.requests, sends), ([], [], []))

    def test_unauthorized_is_not_dispatched_and_has_no_fallback(self):
        websocket = FakeWebsocket(error=AuthorizationError(call_not_sent=True))
        rest = FakeRest()
        gateway = er.EntityRegistryRenameGateway(rest, websocket)
        with self.assertRaises(ConfigurationMutationNotDispatchedError) as caught:
            _run(gateway.rename(er.rename_command(OLD, NEW)))
        self.assertEqual(caught.exception.details["reason"], er.ENTITY_RENAME_UNAUTHORIZED_REASON)
        self.assertEqual(len(websocket.sent), 1)
        self.assertEqual(rest.requests, [])

    def test_answered_error_stays_uncertain_and_is_never_retried(self):
        websocket = FakeWebsocket(
            error=HomeAssistantApiError(details={"provider_response_received": True, "status": 400})
        )
        rest = FakeRest()
        gateway = er.EntityRegistryRenameGateway(rest, websocket)
        with self.assertRaises(er.EntityRenameUpdateErrorResult) as caught:
            _run(gateway.rename(er.rename_command(OLD, NEW)))
        self.assertNotIsInstance(caught.exception, ConfigurationMutationNotDispatchedError)
        self.assertEqual(caught.exception.details["reason"], er.ENTITY_RENAME_ERROR_RESULT_REASON)
        self.assertEqual(len(websocket.sent), 1)
        self.assertEqual(rest.requests, [])

    def test_unanswered_transport_error_propagates_without_fallback(self):
        websocket = FakeWebsocket(error=HomeAssistantApiError(details={}))
        rest = FakeRest()
        gateway = er.EntityRegistryRenameGateway(rest, websocket)
        with self.assertRaises(HomeAssistantApiError):
            _run(gateway.rename(er.rename_command(OLD, NEW)))
        self.assertEqual((len(websocket.sent), rest.requests), (1, []))

    def test_malformed_or_wrong_result_is_refused(self):
        for result in (None, {}, {"entity_entry": entry(OLD)}):
            with self.subTest(result=result):
                gateway = er.EntityRegistryRenameGateway(FakeRest(), FakeWebsocket(result))
                with self.assertRaises(EntityMetadataProviderError):
                    _run(gateway.rename(er.rename_command(OLD, NEW)))

    def test_read_entry_not_found_and_malformed(self):
        missing = FakeWebsocket(error=HomeAssistantApiError(details={"status": 404}))
        self.assertIsNone(_run(er.EntityRegistryRenameGateway(FakeRest(), missing).read_entry(OLD)))
        self.assertEqual(missing.sent, [{"type": "config/entity_registry/get", "entity_id": OLD}])
        malformed = FakeWebsocket({"entity_id": "light.other", "id": "x"})
        with self.assertRaises(EntityMetadataProviderError):
            _run(er.EntityRegistryRenameGateway(FakeRest(), malformed).read_entry(OLD))


if __name__ == "__main__":
    unittest.main()
