"""Offline HAMCP-069A-1 entity registry fixtures.

A synthetic Home Assistant entity registry behind the real
``ConfigurationResourceGateway`` and ``EntityRegistryMetadataGateway``, so
end-to-end tests exercise the shipped planner, policy, F3 runtime and closed
update command without any live dependency.  Every value is synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata_disclosure import (  # noqa: E402
    runtime_consumer_evidence,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402


UPDATE = "config/entity_registry/update"
CHECK_CONFIG = ("POST", "/config/core/check_config")


def registry_entry(entity_id: str, **changes: Any) -> dict[str, Any]:
    domain, object_id = entity_id.split(".", 1)
    value = {
        "entity_id": entity_id,
        "id": f"synthetic-registry-id-{object_id}",
        "platform": "synthetic",
        "device_id": None,
        "config_entry_id": None,
        "disabled_by": None,
        "entity_category": None,
        "has_entity_name": False,
        "original_name": object_id.replace("_", " ").title(),
        "name": None,
        "icon": None,
        "aliases": [],
        "area_id": None,
        "hidden_by": None,
        "labels": [],
        "categories": {},
        "modified_at": 1.0,
        "original_device_class": None,
        "device_class": None,
    }
    del domain
    value.update(changes)
    return value


class SyntheticRegistryWebSocket:
    """The reviewed registry commands, answered as Home Assistant does."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}
        self.labels: list[dict[str, Any]] = []
        self.areas: list[dict[str, Any]] = []
        self.categories: dict[str, list[dict[str, Any]]] = {}
        self.commands: list[dict[str, Any]] = []
        # command type -> exception raised instead of answering
        self.failures: dict[str, Exception] = {}

    def updates(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if c.get("type") == UPDATE]

    def set_external(self, entity_id: str, **changes: Any) -> None:
        """Simulate a change made outside Engineering."""

        entry = self.entries[entity_id]
        entry.update(deepcopy(changes))
        entry["modified_at"] = float(entry["modified_at"]) + 1

    async def command(self, message: dict[str, Any]) -> Any:
        message = deepcopy(message)
        self.commands.append(message)
        kind = message.get("type")
        if kind in self.failures:
            raise self.failures[kind]
        if kind == "config/entity_registry/get":
            entry = self.entries.get(message["entity_id"])
            if entry is None:
                raise HomeAssistantApiError(
                    details={"status": 404, "provider_response_received": True}
                )
            return deepcopy(entry)
        if kind == UPDATE:
            entry = self.entries[message["entity_id"]]
            for field, value in message.items():
                if field in {"type", "entity_id"}:
                    continue
                if field == "categories":
                    merged = dict(entry.get("categories") or {})
                    for scope, category in value.items():
                        if category is None:
                            merged.pop(scope, None)
                        else:
                            merged[scope] = category
                    entry["categories"] = merged
                elif field == "hidden_by":
                    entry["hidden_by"] = value
                else:
                    entry[field] = deepcopy(value)
            entry["modified_at"] = float(entry["modified_at"]) + 1
            return {"entity_entry": deepcopy(entry)}
        if kind == "config/label_registry/list":
            return deepcopy(self.labels)
        if kind == "config/area_registry/list":
            return deepcopy(self.areas)
        if kind == "config/category_registry/list":
            return deepcopy(self.categories.get(message["scope"], []))
        raise AssertionError(f"unreviewed websocket command: {kind}")


class SyntheticRegistryRest:
    def __init__(self, websocket: SyntheticRegistryWebSocket) -> None:
        self.websocket = websocket
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.requests.append((method, path))
        if method == "GET" and path.startswith("/states/"):
            entity_id = path.removeprefix("/states/")
            if entity_id in self.websocket.entries:
                return {"entity_id": entity_id, "state": "on", "attributes": {}}
            return ExpectedHttpStatus(404)
        raise AssertionError(f"unreviewed REST request: {method} {path}")


async def _provider_identity() -> dict[str, Any]:
    return {"slug": "core_ha_mcp", "evidence_hash": "a" * 64}


class EntityMetadataServiceTestCase(unittest.IsolatedAsyncioTestCase):
    """Real service, real F3 runtime, real gateways, synthetic registry."""

    snapshot: Any = None

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = SyntheticRegistryWebSocket()
        self.rest = SyntheticRegistryRest(self.ws)
        self.resource_gateway = ConfigurationResourceGateway(self.rest, self.ws)
        self.snapshot_reads: list[bool] = []

        async def snapshot_reader(*, fenced: bool) -> Any:
            self.snapshot_reads.append(fenced)
            return self.snapshot

        self.snapshot_reader = snapshot_reader
        self.resource_gateway.entity_metadata_consumer_evidence = (
            runtime_consumer_evidence(snapshot_reader)
        )
        self.clock = [datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)]
        self.repository = ChangePlanRepository(self.root / "plans")
        self.audit_path = self.root / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            self.resource_gateway,
            AuditLogger(str(self.audit_path), "hamcp069a-synthetic-audit"),
            now=lambda: self.clock[0],
            entity_metadata_gateway=self.resource_gateway.entity_metadata,
            entity_metadata_snapshot_reader=snapshot_reader,
        )
        await self.restart_runtime()
        self.telemetry, self.context = begin_request("hamcp069a-request")
        self.telemetry.caller_id = "hamcp069a-mcp-caller"

    async def restart_runtime(self) -> None:
        """A fresh F3 runtime over the same durable storage (process restart)."""

        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")
        self.assertEqual(self.runtime.readiness_state()["faults"], [])
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temp.cleanup()

    def add_entity(self, entity_id: str, **changes: Any) -> dict[str, Any]:
        self.ws.entries[entity_id] = registry_entry(entity_id, **changes)
        return self.ws.entries[entity_id]

    async def create(self, **arguments: Any) -> dict[str, Any]:
        return await self.service.create_entity_metadata_plan(arguments)

    async def approve(self, created: dict[str, Any]) -> dict[str, Any]:
        granted = self.service.approve(created["plan_id"], created["plan_hash"])
        self.assertIn("challenge_id", granted, granted)
        for _ in range(3):
            _, csrf = await self.service.issue_external_csrf(
                created["plan_id"], granted["challenge_id"]
            )
            granted = await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=granted["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind=granted["approval_kind"],
                approval_action=granted["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal="home_assistant_admin_ingress:hamcp069a-reviewer",
            )
            if granted.get("status") != "approval_pending":
                return granted
        raise AssertionError("approval did not settle")

    async def apply(self, created: dict[str, Any]) -> dict[str, Any]:
        return await self.service.apply(created["plan_id"], created["plan_hash"])

    def audit_events(self) -> list[dict[str, Any]]:
        import json

        if not self.audit_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


__all__ = [
    "AuthorizationError",
    "CHECK_CONFIG",
    "EntityMetadataServiceTestCase",
    "HomeAssistantApiError",
    "SyntheticRegistryRest",
    "SyntheticRegistryWebSocket",
    "UPDATE",
    "registry_entry",
]
