"""Offline HAMCP-069B entity availability and assistant-exposure fixtures.

A synthetic Home Assistant entity registry, exposure store, config-entry
store and "expose new entities" settings behind the real
``ConfigurationResourceGateway`` and ``EntityAvailabilityExposureGateway``,
so end-to-end tests exercise the shipped planner, policy, F3 runtime and
closed commands without any live dependency.  Every value is synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.script_sources import (  # noqa: E402
    ScriptDiagnostics,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.governance.entity_availability_exposure import (  # noqa: E402
    NEVER_SENT_COMMANDS,
)
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    AVAILABILITY_PROFILE,
    EXPOSURE_PROFILE,
    f3_requirements,
)
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
from ha_mcp_engineering.request_context import begin_request  # noqa: E402

from tests.hamcp069a_entity_fixtures import (  # noqa: E402
    EntityMetadataServiceTestCase,
    HomeAssistantApiError,
    SyntheticRegistryRest,
    SyntheticRegistryWebSocket,
    UPDATE,
    _provider_identity,
    registry_entry,
)


EXPOSE = "homeassistant/expose_entity"
CONFIG_ENTRY_GET = "config_entries/get_single"
REGISTRY_LIST = "config/entity_registry/list"
REGISTRY_GET = "config/entity_registry/get"
EXPOSE_NEW_GET = "homeassistant/expose_new_entities/get"
WRITE_COMMANDS = frozenset({UPDATE, EXPOSE})


def exposure_entry(entity_id: str, **changes: Any) -> dict[str, Any]:
    """A registry ``extended_dict`` entry, including ``options``."""

    return registry_entry(entity_id, **{"options": {}, **changes})


def finding(
    target_entity_id: str,
    source_id: str,
    *,
    relation: str = "trigger_entity",
    config_path: str = "triggers[0].entity_id",
) -> Any:
    return SimpleNamespace(
        target_entity_id=target_entity_id,
        source_type="automation",
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        relation=relation,
        config_path=config_path,
    )


def automation_profile(source_id: str, consequence: str) -> Any:
    return SimpleNamespace(
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        physical_consequence=consequence,
        complete=True,
        processing_limit_exceeded=False,
    )


def coverage_item(source_type: str, completeness: str = "complete", **changes: Any) -> Any:
    """A dependency coverage item whose ledger contract matches ``completeness``."""

    return SourceCoverageItem(
        **{
            "source_type": source_type,
            "provider": "synthetic",
            "provider_capability": "read",
            "completeness": completeness,
            "obligation_ledger_completeness": completeness,
            **changes,
        }
    )


def script_diagnostics(
    completeness: str = "partial", *, findings: tuple = (), **changes: Any
) -> Any:
    """Script evidence as a working reader reports it (structurally partial)."""

    return ScriptDiagnostics(
        tuple(findings),
        (),
        coverage_item("script", completeness, **changes),
        "synthetic-script-fingerprint",
    )


def exposure_snapshot(
    *, findings: tuple = (), profiles: tuple = (), read_failures: tuple = ()
) -> Any:
    """A synthetic dependency snapshot with complete disable impact evidence.

    Each field the HAMCP-069B evidence-completeness predicate reads is set to
    its complete value; tests degrade one field at a time.
    """

    return SimpleNamespace(
        findings=tuple(findings),
        automation_action_profiles=tuple(profiles),
        automation_read_failures=tuple(read_failures),
        coverage=(coverage_item("automation"), coverage_item("blueprint")),
        obligations=(),
        obligation_overflow_count=0,
        obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        dynamic_reference_overflow_count=0,
        home_assistant_version=supported_home_assistant_versions()[0],
        home_assistant_version_status="observed",
        semantic_evidence=None,
        script_diagnostics=script_diagnostics(),
    )


class SyntheticExposureWebSocket(SyntheticRegistryWebSocket):
    """The 069B closed vocabulary, answered as Home Assistant does.

    A command outside the reviewed vocabulary, and every command the
    gateway must never send, fails the test rather than being answered.
    """

    def __init__(self) -> None:
        super().__init__()
        self.config_entries: dict[str, dict[str, Any]] = {}
        self.expose_new_settings: dict[str, bool] = {
            "cloud.alexa": False,
            "cloud.google_assistant": False,
            "conversation": False,
        }
        # When set, a registry update persists Alexa's computed default (the
        # one tolerated side effect, design §3.4).
        self.alexa_default_on_update: bool | None = None
        # When set, a write is answered but the stored value is not changed.
        self.ignore_writes = False

    def writes(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if c.get("type") in WRITE_COMMANDS]

    def command_types(self) -> set[str]:
        return {str(c.get("type")) for c in self.commands}

    def add_config_entry(
        self, entry_id: str, *, domain: str = "synthetic", supports_unload: bool = True
    ) -> None:
        self.config_entries[entry_id] = {
            "entry_id": entry_id,
            "domain": domain,
            "title": f"Synthetic {entry_id}",
            "source": "user",
            "state": "loaded",
            "supports_options": False,
            "supports_remove_device": False,
            "supports_unload": supports_unload,
            "supports_reconfigure": False,
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "disabled_by": None,
            "reason": None,
        }

    async def command(self, message: dict[str, Any]) -> Any:
        kind = message.get("type")
        if kind in NEVER_SENT_COMMANDS:
            raise AssertionError(f"never-sent command was sent: {kind}")
        if kind == EXPOSE:
            message = deepcopy(message)
            self.commands.append(message)
            if kind in self.failures:
                raise self.failures[kind]
            if not self.ignore_writes:
                for entity_id in message["entity_ids"]:
                    entry = self.entries[entity_id]
                    options = entry.setdefault("options", {})
                    for assistant in message["assistants"]:
                        settings = dict(options.get(assistant) or {})
                        settings["should_expose"] = message["should_expose"]
                        options[assistant] = settings
                    entry["modified_at"] = float(entry["modified_at"]) + 1
            # ``ws_expose_entity`` answers with an empty result.
            return None
        if kind == UPDATE and set(message) == {"type", "entity_id", "disabled_by"}:
            message = deepcopy(message)
            self.commands.append(message)
            if kind in self.failures:
                raise self.failures[kind]
            entry = self.entries[message["entity_id"]]
            if not self.ignore_writes:
                entry["disabled_by"] = message["disabled_by"]
                if self.alexa_default_on_update is not None:
                    options = entry.setdefault("options", {})
                    options.setdefault(
                        "cloud.alexa", {"should_expose": self.alexa_default_on_update}
                    )
                entry["modified_at"] = float(entry["modified_at"]) + 1
            result: dict[str, Any] = {"entity_entry": deepcopy(entry)}
            if message["disabled_by"] is None:
                result["reload_delay"] = 30
            return result
        if kind == CONFIG_ENTRY_GET:
            self.commands.append(deepcopy(message))
            if kind in self.failures:
                raise self.failures[kind]
            value = self.config_entries.get(message["entry_id"])
            if value is None:
                raise HomeAssistantApiError(
                    details={"status": 404, "provider_response_received": True}
                )
            return {"config_entry": deepcopy(value)}
        if kind == REGISTRY_LIST:
            self.commands.append(deepcopy(message))
            if kind in self.failures:
                raise self.failures[kind]
            return [deepcopy(entry) for entry in self.entries.values()]
        if kind == EXPOSE_NEW_GET:
            self.commands.append(deepcopy(message))
            if kind in self.failures:
                raise self.failures[kind]
            return {"expose_new": self.expose_new_settings[message["assistant"]]}
        return await super().command(message)


PROFILES_069B = frozenset({AVAILABILITY_PROFILE, EXPOSURE_PROFILE})


class AdmittingCore:
    """Core admission double: admitted 069B profiles, exact-target leases.

    ``route_status``, ``acquire`` (planning) and ``acquire_f3`` (apply) admit
    only the profiles in ``admitted``; a 069B lease is issued only for an
    exact target, and every issued authority is revalidated against the
    current admitted set, so ``withdraw`` retires leases already held.  The
    real ``CoreRuntime`` with a TEST-signed registry is exercised separately
    (``test_hamcp069b_review1_corrections``).
    """

    def __init__(self, admitted: Any = PROFILES_069B) -> None:
        self.admitted = set(admitted)
        self.planning_leases: list[tuple[tuple[str, ...], Any]] = []
        self.f3_leases: list[tuple[str, ...]] = []
        self.revalidations = 0

    def withdraw(self, *profiles: str) -> None:
        self.admitted -= set(profiles or PROFILES_069B)

    def _admits(self, requirements: Any) -> bool:
        return set(requirements) & PROFILES_069B <= self.admitted

    async def reconcile_once(self, reason: str) -> None:
        return None

    def route_status(self, capability_ids: tuple[str, ...], **_: Any) -> dict[str, Any]:
        return {"available": bool(capability_ids) and self._admits(capability_ids)}

    def acquire(
        self, capability_ids: tuple[str, ...], *, target: Any = None, **_: Any
    ) -> Any:
        self.planning_leases.append((tuple(capability_ids), deepcopy(target)))
        if not self._admits(capability_ids) or (
            set(capability_ids) & PROFILES_069B and target is None
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


class AvailabilityExposureServiceTestCase(EntityMetadataServiceTestCase):
    """Real service, real F3 runtime, real gateways, synthetic registry."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = SyntheticExposureWebSocket()
        self.rest = SyntheticRegistryRest(self.ws)
        self.resource_gateway = ConfigurationResourceGateway(self.rest, self.ws)
        self.snapshot_reads: list[bool] = []
        self.snapshot = exposure_snapshot()

        async def snapshot_reader(*, fenced: bool) -> Any:
            self.snapshot_reads.append(fenced)
            return self.snapshot

        self.snapshot_reader = snapshot_reader
        self.resource_gateway.entity_metadata_consumer_evidence = (
            runtime_consumer_evidence(snapshot_reader)
        )
        self.resource_gateway.availability_exposure_snapshot_reader = snapshot_reader
        self.clock = [datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)]
        self.repository = ChangePlanRepository(self.root / "plans")
        self.audit_path = self.root / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            self.resource_gateway,
            AuditLogger(str(self.audit_path), "hamcp069b-synthetic-audit"),
            now=lambda: self.clock[0],
            entity_metadata_gateway=self.resource_gateway.entity_metadata,
            entity_metadata_snapshot_reader=snapshot_reader,
            entity_availability_exposure_gateway=self.resource_gateway.availability_exposure,
            entity_availability_exposure_snapshot_reader=snapshot_reader,
        )
        self.core = AdmittingCore()
        await self.restart_runtime()
        self.telemetry, self.context = begin_request("hamcp069b-request")
        self.telemetry.caller_id = "hamcp069b-mcp-caller"

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
        self.ws.entries[entity_id] = exposure_entry(entity_id, **changes)
        return self.ws.entries[entity_id]

    async def create(self, **arguments: Any) -> dict[str, Any]:
        return await self.service.create_entity_availability_exposure_plan(arguments)

    async def approved(self, **arguments: Any) -> dict[str, Any]:
        created = await self.create(**arguments)
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
            for event in record.events
            for code in event["diagnostic_codes"]
        }

    def assert_never_sent(self) -> None:
        self.assertFalse(set(NEVER_SENT_COMMANDS) & self.ws.command_types())


__all__ = [
    "AdmittingCore",
    "PROFILES_069B",
    "AvailabilityExposureServiceTestCase",
    "CONFIG_ENTRY_GET",
    "EXPOSE",
    "EXPOSE_NEW_GET",
    "REGISTRY_GET",
    "REGISTRY_LIST",
    "SyntheticExposureWebSocket",
    "UPDATE",
    "automation_profile",
    "coverage_item",
    "exposure_entry",
    "exposure_snapshot",
    "finding",
    "script_diagnostics",
]
