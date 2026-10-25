"""Offline HAMCP-165 label registry fixtures.

A synthetic Home Assistant label registry (plus the 069A entity registry)
behind the real ``ConfigurationResourceGateway`` and ``LabelRegistryGateway``,
so end-to-end tests exercise the shipped planner, policy, F3 runtime and the
closed label commands without any live dependency.  Every value is synthetic.

The label registry answers as Core 2026.10 ``label_registry`` does: a
normalized-name duplicate is an error result, a create derives its id with an
independent ``slugify`` plus Core's ``_2`` suffixing, and an update of an
unknown id is an error result.  ``config/label_registry/delete`` fails the
test.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unicodedata
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata_disclosure import (  # noqa: E402
    runtime_consumer_evidence,
)
from ha_mcp_engineering.governance.label_registry import (  # noqa: E402
    NEVER_SENT_COMMANDS,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    LABEL_PROFILE,
    f3_requirements,
)
from ha_mcp_engineering.request_context import begin_request  # noqa: E402

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
    HomeAssistantApiError,
    SyntheticRegistryRest,
    SyntheticRegistryWebSocket,
    UPDATE,
    _provider_identity,
    dependency_snapshot,
    registry_entry,
)


LABEL_LIST = "config/label_registry/list"
LABEL_CREATE = "config/label_registry/create"
LABEL_UPDATE = "config/label_registry/update"
LABEL_DELETE = "config/label_registry/delete"
LABEL_WRITES = frozenset({LABEL_CREATE, LABEL_UPDATE})
WRITE_COMMANDS = frozenset({LABEL_CREATE, LABEL_UPDATE, UPDATE})


def core_slugify(text: str) -> str:
    """An independent model of Core ``slugify`` (python-slugify, ``_``).

    Deliberately not the planner's derivation: ASCII-fold, lowercase, turn
    every run of characters outside ``[a-z0-9]`` into one ``_`` and trim.
    """

    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")
    return slug or "unknown"


def core_normalize(name: str) -> str:
    return name.casefold().replace(" ", "")


def label_entry(label_id: str, name: str, **changes: Any) -> dict[str, Any]:
    """A Core ``_entry_dict`` label entry."""

    value = {
        "label_id": label_id,
        "name": name,
        "color": None,
        "icon": None,
        "description": None,
        "created_at": 1.0,
        "modified_at": 1.0,
    }
    value.update(changes)
    return value


class SyntheticLabelWebSocket(SyntheticRegistryWebSocket):
    """The 165 closed vocabulary over a Core-faithful label registry."""

    def __init__(self) -> None:
        super().__init__()
        self._clock = 10.0
        # When set, a label write is answered but the registry is unchanged.
        self.ignore_writes = False
        # When set, a create stores the label under this id instead (as Core
        # would after a namespace race); the response carries that id.
        self.generated_id_override: str | None = None
        # When set, a label write is applied but these fields are stored
        # differently from what was sent (a drifted readback).
        self.drift: dict[str, Any] = {}

    def writes(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if c.get("type") in WRITE_COMMANDS]

    def label_writes(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if c.get("type") in LABEL_WRITES]

    def command_types(self) -> set[str]:
        return {str(c.get("type")) for c in self.commands}

    def label(self, label_id: str) -> dict[str, Any] | None:
        for entry in self.labels:
            if entry["label_id"] == label_id:
                return entry
        return None

    def add_label(self, label_id: str, name: str, **changes: Any) -> dict[str, Any]:
        entry = label_entry(label_id, name, **changes)
        self.labels.append(entry)
        return entry

    def set_external_label(self, label_id: str, **changes: Any) -> None:
        """Simulate a label edit made outside Engineering."""

        entry = self.label(label_id)
        entry.update(deepcopy(changes))
        entry["modified_at"] = float(entry["modified_at"]) + 1

    def _tick(self) -> float:
        self._clock += 1
        return self._clock

    @staticmethod
    def _invalid(message: str) -> HomeAssistantApiError:
        return HomeAssistantApiError(
            message,
            details={"provider_response_received": True, "code": "invalid_info"},
        )

    def _holder(self, name: str, *, other_than: str | None = None) -> Any:
        folded = core_normalize(name)
        for entry in self.labels:
            if entry["label_id"] != other_than and core_normalize(entry["name"]) == folded:
                return entry
        return None

    def _create(self, message: dict[str, Any]) -> dict[str, Any]:
        name = message["name"]
        if self._holder(name) is not None:
            raise self._invalid(f"The name {name} is already in use")
        taken = {entry["label_id"] for entry in self.labels}
        suggestion = core_slugify(name)
        label_id, tries = suggestion, 1
        while label_id in taken:
            tries += 1
            label_id = f"{suggestion}_{tries}"
        if self.generated_id_override is not None:
            label_id = self.generated_id_override
        now = self._tick()
        entry = label_entry(
            label_id,
            name,
            color=message.get("color"),
            icon=message.get("icon"),
            description=message.get("description"),
            created_at=now,
            modified_at=now,
        )
        response = deepcopy(entry)
        if not self.ignore_writes:
            entry.update(deepcopy(self.drift))
            self.labels.append(entry)
        return response

    def _update(self, message: dict[str, Any]) -> dict[str, Any]:
        entry = self.label(message["label_id"])
        if entry is None:
            raise HomeAssistantApiError(
                "unknown label",
                details={"provider_response_received": True, "code": "unknown_error"},
            )
        name = message.get("name", entry["name"])
        if name != entry["name"] and self._holder(name, other_than=entry["label_id"]):
            raise self._invalid(f"The name {name} is already in use")
        changed = {
            field: message[field]
            for field in ("name", "color", "icon", "description")
            if field in message and message[field] != entry[field]
        }
        if changed and not self.ignore_writes:
            entry.update(changed)
            entry.update(deepcopy(self.drift))
            entry["modified_at"] = self._tick()
        return deepcopy(entry) if not self.ignore_writes else {
            **deepcopy(entry),
            **changed,
        }

    async def _answer(self, message: dict[str, Any]) -> Any:
        kind = message.get("type")
        if kind in NEVER_SENT_COMMANDS or kind == LABEL_DELETE:
            raise AssertionError(f"never-sent command was sent: {kind}")
        if kind in LABEL_WRITES:
            message = deepcopy(message)
            self.commands.append(message)
            if kind in self.failures:
                raise self.failures[kind]
            return self._create(message) if kind == LABEL_CREATE else self._update(message)
        return await super()._answer(message)


def bypass_entry_admission(case: Any) -> None:
    """Skip the first-apply entry refusal so a test reaches the dispatch gate.

    The entry check (``F3RuntimeIntegration._refuse_unadmitted_entry``) is the
    first barrier; the dispatch-time ``_UnsignedProfileAdmissionAdapter`` is
    the independent second one.  Tests of the second barrier bypass the first.
    """

    patcher = patch.object(
        F3RuntimeIntegration, "_refuse_unadmitted_entry", lambda self, plan: None
    )
    patcher.start()
    case.addCleanup(patcher.stop)


class LabelAdmittingCore:
    """Core admission double: the label profile admitted, exact-target leases.

    ``route_status``, ``acquire`` (planning) and ``acquire_f3`` (apply) admit
    the label profile only while it is in ``admitted``; a label planning lease
    is issued only for an exact target, and every issued authority is
    revalidated against the current admitted set, so ``withdraw`` retires
    leases already held.  Every other profile (the signed 069A profile, the
    dependency helper) is admitted.  The real ``CoreRuntime`` with a
    TEST-signed registry is exercised separately.
    """

    GATED = frozenset({LABEL_PROFILE})

    def __init__(self, admitted: Any = GATED) -> None:
        self.admitted = set(admitted)
        self.planning_leases: list[tuple[tuple[str, ...], Any]] = []
        self.f3_leases: list[tuple[str, ...]] = []
        self.route_queries: list[tuple[str, ...]] = []
        self.revalidations = 0

    def withdraw(self) -> None:
        self.admitted.clear()

    def _admits(self, requirements: Any) -> bool:
        return set(requirements) & self.GATED <= self.admitted

    async def reconcile_once(self, reason: str) -> None:
        return None

    def route_status(self, capability_ids: tuple[str, ...], **_: Any) -> dict[str, Any]:
        self.route_queries.append(tuple(capability_ids))
        return {"available": bool(capability_ids) and self._admits(capability_ids)}

    def acquire(
        self, capability_ids: tuple[str, ...], *, target: Any = None, **_: Any
    ) -> Any:
        self.planning_leases.append((tuple(capability_ids), deepcopy(target)))
        if not self._admits(capability_ids) or (
            set(capability_ids) & self.GATED and target is None
        ):
            return None
        return SimpleNamespace(requirements=tuple(capability_ids))

    def acquire_f3(self, prepared: Any, plan_created_at: Any = None) -> Any:
        requirements = f3_requirements(prepared)
        self.f3_leases.append(requirements)
        if not self._admits(requirements):
            return None
        return SimpleNamespace(requirements=requirements)

    def consume(self, authority: Any) -> Any:
        return ("commit",)

    def revalidate(self, authority: Any, commits: Any) -> bool:
        self.revalidations += 1
        return self._admits(authority.requirements)

    def release(self, authority: Any) -> bool:
        return True

    def finish(self, commits: Any) -> bool:
        return True


class LabelServiceTestCase(EntityMetadataServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic registries."""

    core_admits = True

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = SyntheticLabelWebSocket()
        self.rest = SyntheticRegistryRest(self.ws)
        self.resource_gateway = ConfigurationResourceGateway(self.rest, self.ws)
        self.snapshot_reads: list[bool] = []
        self.snapshot = dependency_snapshot()

        async def snapshot_reader(*, fenced: bool) -> Any:
            self.snapshot_reads.append(fenced)
            return self.snapshot

        self.snapshot_reader = snapshot_reader
        self.resource_gateway.entity_metadata_consumer_evidence = (
            runtime_consumer_evidence(snapshot_reader)
        )
        self.clock = [datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)]
        self.repository = ChangePlanRepository(self.root / "plans")
        self.audit_path = self.root / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            self.resource_gateway,
            AuditLogger(str(self.audit_path), "hamcp165-synthetic-audit"),
            now=lambda: self.clock[0],
            entity_metadata_gateway=self.resource_gateway.entity_metadata,
            entity_metadata_snapshot_reader=snapshot_reader,
            label_registry_gateway=self.resource_gateway.label_registry,
        )
        self.core = LabelAdmittingCore(
            LabelAdmittingCore.GATED if self.core_admits else ()
        )
        await self.restart_runtime()
        self.telemetry, self.context = begin_request("hamcp165-request")
        self.telemetry.caller_id = "hamcp165-mcp-caller"

    async def restart_runtime(self) -> None:
        """A fresh F3 runtime over the same storage and the same Core."""

        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
            core_runtime=self.core,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")
        self.assertEqual(self.runtime.readiness_state()["faults"], [])
        self.assertTrue(self.runtime.readiness_state()["execution_ready"])

    def add_entity(self, entity_id: str, **changes: Any) -> dict[str, Any]:
        self.ws.entries[entity_id] = registry_entry(entity_id, **changes)
        return self.ws.entries[entity_id]

    async def create(self, *operations: dict[str, Any], **arguments: Any) -> dict[str, Any]:
        return await self.service.create_label_plan(
            {"operations": list(operations), **arguments}
        )

    async def approved(self, *operations: dict[str, Any], **arguments: Any) -> dict[str, Any]:
        created = await self.create(*operations, **arguments)
        await self.approve(created)
        return created

    def child_records(self, task_id: str) -> list:
        return [
            self.runtime.children.get(item["child_id"])
            for item in self.runtime.children.declarations_for_task(task_id)
        ]

    def child_codes(self, task_id: str) -> set[str]:
        return {
            code
            for record in self.child_records(task_id)
            if record is not None
            for event in record.events
            for code in event["diagnostic_codes"]
        }

    def assert_never_sent(self) -> None:
        self.assertNotIn(LABEL_DELETE, self.ws.command_types())


__all__ = [
    "LABEL_CREATE",
    "LABEL_DELETE",
    "LABEL_LIST",
    "LABEL_UPDATE",
    "LabelAdmittingCore",
    "LabelServiceTestCase",
    "SyntheticLabelWebSocket",
    "UPDATE",
    "core_normalize",
    "core_slugify",
    "label_entry",
]
