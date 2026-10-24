"""Offline HAMCP-155 governed-deletion fixtures.

A synthetic Home Assistant automation/script configuration store, entity
registry and state machine behind the real ``ConfigurationResourceGateway``
and ``ConfigurationDeleteGateway``.  DELETE behaves as Core 2026.10.1's
``EditKeyBasedConfigView.delete`` does: a missing key is HTTP 400 with no
write; otherwise the key is removed and the post-write hook removes the
registry entry (and so the state) without a reload.  Every value is
synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.governance.configuration_delete import (  # noqa: E402
    CAPABILITY_IDENTITIES,
    PROFILE as DELETE_PROFILE,
)

from tests.hamcp069a_entity_fixtures import registry_entry  # noqa: E402
from tests.hamcp069b_fixtures import (  # noqa: E402
    AdmittingCore,
    AvailabilityExposureServiceTestCase,
    PROFILES_069B,
    SyntheticExposureWebSocket,
)


CONFIG_PREFIX = {
    "automation": "/config/automation/config/",
    "script": "/config/script/config/",
}
PROFILES_155 = frozenset({DELETE_PROFILE})
CHECK_CONFIG = ("POST", "/config/core/check_config")
REVIEWER = "home_assistant_admin_ingress:hamcp155-reviewer"


def automation_config(
    target_id: str, *, service: str = "light.turn_on", target: str = "light.porch"
) -> dict[str, Any]:
    return {
        "id": target_id,
        "alias": target_id.replace("_", " ").title(),
        "description": "",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}
        ],
        "conditions": [],
        "actions": [{"action": service, "target": {"entity_id": target}}],
        "mode": "single",
    }


def script_config(
    target_id: str, *, service: str = "light.turn_on", target: str = "light.porch"
) -> dict[str, Any]:
    return {
        "alias": target_id.replace("_", " ").title(),
        "sequence": [{"action": service, "target": {"entity_id": target}}],
        "mode": "single",
    }


def registry_id(target_id: str) -> str:
    return "reg" + "".join(ch for ch in target_id if ch.isalnum())


class SyntheticConfigurationRest:
    """REST config GET/POST/DELETE and states, answered as Core does."""

    def __init__(self, websocket: SyntheticExposureWebSocket) -> None:
        self.websocket = websocket
        self.configs: dict[str, dict[str, dict[str, Any]]] = {
            "automation": {},
            "script": {},
        }
        self.requests: list[tuple[str, str]] = []
        self.check_config_calls = 0
        # The state machine, independent of the registry.
        self.states: set[str] = set()
        # Entity ids freed by a delete, as Core's DeletedRegistryEntry keeps.
        self.deleted_entries: dict[tuple[str, str], dict[str, Any]] = {}
        # When False, the post-write hook never removes the registry entry.
        self.delete_hook_removes_entry = True
        # When set, DELETE raises this instead of answering.
        self.delete_failure: Exception | None = None
        # When True, DELETE answers ok but nothing is removed.
        self.ignore_deletes = False
        # Called just before DELETE is answered (drift injection).
        self.before_delete: Any = None
        # Called with (resource_type, target_id) after a DELETE has removed
        # the configuration (and, per the hook flag, the entry and state).
        self.after_delete: Any = None
        # Called with (resource_type, target_id, ordinal) after each config
        # GET is answered: ``ordinal`` counts that target's GETs from 1.
        self.after_config_get: Any = None
        self.config_gets: dict[tuple[str, str], int] = {}

    def deletes(self) -> list[tuple[str, str]]:
        return [item for item in self.requests if item[0] == "DELETE"]

    def writes(self) -> list[tuple[str, str]]:
        return [
            item
            for item in self.requests
            if item[0] in {"POST", "DELETE"} and item != CHECK_CONFIG
        ]

    def _split(self, path: str) -> tuple[str, str] | None:
        for resource_type, prefix in CONFIG_PREFIX.items():
            if path.startswith(prefix):
                return resource_type, path.removeprefix(prefix)
        return None

    def add(self, resource_type: str, target_id: str, config: dict[str, Any]) -> str:
        self.configs[resource_type][target_id] = deepcopy(config)
        entity_id = f"{resource_type}.{target_id}"
        self.websocket.entries[entity_id] = registry_entry(
            entity_id,
            platform=resource_type,
            unique_id=target_id,
            id=registry_id(target_id),
        )
        self.states.add(entity_id)
        return entity_id

    async def request(self, method: str, path: Any, **kwargs: Any) -> Any:
        path = str(path)
        self.requests.append((method, path))
        if method == "GET" and path.startswith("/states/"):
            entity_id = path.removeprefix("/states/")
            if entity_id in self.states:
                return {"entity_id": entity_id, "state": "on", "attributes": {}}
            return ExpectedHttpStatus(404)
        if (method, path) == CHECK_CONFIG:
            self.check_config_calls += 1
            return {"result": "valid", "errors": None}
        split = self._split(path)
        if split is None:
            raise AssertionError(f"unreviewed REST request: {method} {path}")
        resource_type, target_id = split
        store = self.configs[resource_type]
        entity_id = f"{resource_type}.{target_id}"
        if method == "GET":
            key = (resource_type, target_id)
            self.config_gets[key] = self.config_gets.get(key, 0) + 1
            value = (
                deepcopy(store[target_id])
                if target_id in store
                else ExpectedHttpStatus(404)
            )
            if self.after_config_get is not None:
                self.after_config_get(resource_type, target_id, self.config_gets[key])
            return value
        if method == "POST":
            body = deepcopy(kwargs["body"])
            # ``EditIdBasedConfigView._write_value`` stores the key as "id".
            store[target_id] = (
                {"id": target_id, **body} if resource_type == "automation" else body
            )
            if not any(
                entry.get("platform") == resource_type
                and entry.get("unique_id") == target_id
                for entry in self.websocket.entries.values()
            ):
                restored = self.deleted_entries.pop(
                    (resource_type, target_id), None
                )
                self.websocket.entries[entity_id] = restored or registry_entry(
                    entity_id,
                    platform=resource_type,
                    unique_id=target_id,
                    id=registry_id(target_id),
                )
            self.states.add(entity_id)
            return {"result": "ok"}
        if method == "DELETE":
            if kwargs.get("body") is not None:
                raise AssertionError("a configuration DELETE carries no body")
            if self.before_delete is not None:
                self.before_delete()
            if self.delete_failure is not None:
                raise self.delete_failure
            if target_id not in store:
                raise HomeAssistantApiError(
                    details={"status": 400, "provider_response_received": True}
                )
            if self.ignore_deletes:
                return {"result": "ok"}
            del store[target_id]
            if self.delete_hook_removes_entry:
                for key, entry in list(self.websocket.entries.items()):
                    if (
                        entry.get("platform") == resource_type
                        and entry.get("unique_id") == target_id
                    ):
                        self.deleted_entries[(resource_type, target_id)] = (
                            self.websocket.entries.pop(key)
                        )
                        self.states.discard(key)
            if self.after_delete is not None:
                self.after_delete(resource_type, target_id)
            return {"result": "ok"}
        raise AssertionError(f"unreviewed REST request: {method} {path}")


class AdmittingDeleteCore(AdmittingCore):
    """``AdmittingCore`` that also admits (or withholds) the 155 profile."""

    def __init__(self, admitted: Any = PROFILES_069B | PROFILES_155) -> None:
        super().__init__(admitted)

    def withdraw(self, *profiles: str) -> None:
        self.admitted -= set(profiles or PROFILES_155)

    def _admits(self, requirements: Any) -> bool:
        gated = PROFILES_069B | PROFILES_155
        return set(requirements) & gated <= self.admitted


class ConfigurationDeleteServiceTestCase(AvailabilityExposureServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic Core store."""

    core_admits_delete = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.config_rest = SyntheticConfigurationRest(self.ws)
        # The gateway reads the REST client through the shared resource
        # gateway, so swapping it rebinds every governed read and write.
        self.resource_gateway.rest_client = self.config_rest
        self.resource_gateway.entity_metadata.rest_client = self.config_rest
        self.service.configuration_delete_gateway = (
            self.resource_gateway.configuration_delete
        )
        self.core = AdmittingDeleteCore(
            PROFILES_069B | PROFILES_155
            if self.core_admits_delete
            else PROFILES_069B
        )
        await self.restart_runtime()

    async def restart_runtime(self) -> None:
        await super().restart_runtime()
        # The absence poll is bounded by attempts; tests never really sleep.
        self.poll_sleeps: list[float] = []

        async def no_sleep(seconds: float) -> None:
            self.poll_sleeps.append(seconds)

        for identity in CAPABILITY_IDENTITIES.values():
            self.runtime.registry.adapter(identity)._sleep = no_sleep

    async def approve(self, created: dict[str, Any]) -> dict[str, Any]:
        """Exact-hash external approval, including the HAMCP-213 form."""

        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertIn("challenge_id", pending, pending)
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=REVIEWER,
        )
        for _ in range(2):
            if granted.get("status") != "approval_pending":
                return granted
            # Elevated plans: the second step, with the HAMCP-213
            # safety-critical acknowledgement when the review asks for it.
            review, csrf = await self.service.issue_external_csrf(
                created["plan_id"], granted["challenge_id"]
            )
            safety = review.get("safety_critical_acknowledgement")
            acknowledgement = (
                {
                    "acknowledged_objects": tuple(
                        row["object_ref"] for row in safety["objects"]
                    ),
                    "acknowledged_projection_hash": safety["projection_hash"],
                }
                if safety
                else {}
            )
            granted = await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=granted["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=granted["approval_kind"],
                approval_action=granted["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=REVIEWER,
                **acknowledgement,
            )
        if granted.get("status") != "approval_pending":
            return granted
        raise AssertionError("approval did not settle")

    async def approved(self, resource_type: str, target_id: str) -> dict[str, Any]:
        created = await self.create_one(resource_type, target_id)
        await self.approve(created)
        return created

    def add(self, resource_type: str, target_id: str, config: dict[str, Any] | None = None) -> str:
        if config is None:
            config = (
                automation_config(target_id)
                if resource_type == "automation"
                else script_config(target_id)
            )
        return self.config_rest.add(resource_type, target_id, config)

    async def create(self, **arguments: Any) -> dict[str, Any]:
        arguments.setdefault("reason", "Synthetic cleanup")
        return await self.service.create_configuration_delete_plan(arguments)

    async def create_one(self, resource_type: str, target_id: str) -> dict[str, Any]:
        return await self.create(
            deletions=[{"resource_type": resource_type, "target_id": target_id}]
        )

    def child_codes(self, task_id: str) -> set[str]:
        # An unattempted child (stop on first failure) has no record.
        return {
            code
            for record in self.child_records(task_id)
            if record is not None
            for event in record.events
            for code in event["diagnostic_codes"]
        }

    def reads(self) -> int:
        return len(self.config_rest.requests) + len(self.ws.commands)


__all__ = [
    "AdmittingDeleteCore",
    "ConfigurationDeleteServiceTestCase",
    "DELETE_PROFILE",
    "PROFILES_155",
    "SyntheticConfigurationRest",
    "automation_config",
    "registry_id",
    "script_config",
]
