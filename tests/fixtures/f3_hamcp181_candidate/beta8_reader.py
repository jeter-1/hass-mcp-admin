"""HAMCP-181 §4.3 item 3: drive the exact beta.8 reader over candidate records.

Run only by ``tests/test_hamcp181_beta8_downgrade.py``, in a separate
interpreter started with ``-I`` so the beta.8 ``ha_mcp_engineering`` package
never meets the current one::

    python -I beta8_reader.py BETA8_SOURCE FIXTURE_DIRECTORY WORK_DIRECTORY

``BETA8_SOURCE`` is a ``git archive`` extraction of commit 7370ad2 (tag
v2.4.1-beta.8). Each shape's store is copied into ``WORK_DIRECTORY`` and
opened by beta.8 code with synthetic fakes only. Every gateway and transport
method call is counted. The driver prints one JSON object of observations and
asserts nothing itself; the test module asserts.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any
from unittest.mock import patch


GOVERNED_KINDS = {
    "a_helper_already_present": "helper",
    "b_configuration_callback_not_sent": "configuration",
    "b_hvac_helper_appeared_not_sent": "configuration",
    "b_dashboard_setter_not_sent": "dashboard",
    "r_proven_configuration_4xx": "configuration",
    "r_unproven_operational_rejection": "operational",
    "r_unproven_entity_registry_error": "entity_metadata",
    "c_response_lost_verified": "configuration",
    "sent_verified_configuration": "configuration",
    "v9_entity_metadata_already_present": "entity_metadata",
    "mixed_three_child_parent": "configuration",
}
TYPED_KINDS = {"fan_sent_verified": "fan", "power_sent_verified": "power"}
TIME_KEYS = {"updated_at", "claim_expires_at", "completed_at", "occurred_at", "committed_at"}


def instrument(obj: Any, label: str, log: list) -> Any:
    """Count every method call on ``obj`` without changing its type checks."""

    base = type(obj)

    def __getattribute__(self, name):
        value = super(counting, self).__getattribute__(name)
        if name.startswith("__") or not inspect.ismethod(value) or value.__self__ is not self:
            return value
        if inspect.iscoroutinefunction(value):
            async def wrapped(*args, **kwargs):
                log.append(f"{label}.{name}")
                return await value(*args, **kwargs)
        else:
            def wrapped(*args, **kwargs):
                log.append(f"{label}.{name}")
                return value(*args, **kwargs)
        return wrapped

    counting = type("Counting" + base.__name__, (base,), {"__getattribute__": __getattribute__})
    obj.__class__ = counting
    return obj


def counted_identity(reader, label: str, log: list):
    async def identity():
        log.append(label)
        return await reader()
    return identity


def latest_time(store: Path) -> datetime:
    found = []

    def scan(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in TIME_KEYS and isinstance(item, str):
                    try:
                        found.append(datetime.fromisoformat(item))
                    except ValueError:
                        pass
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    for path in store.rglob("*.json"):
        scan(json.loads(path.read_text(encoding="utf-8")))
    return max(found) + timedelta(minutes=1)


def file_hashes(store: Path) -> dict[str, str]:
    return {
        path.relative_to(store).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(store.rglob("*"))
        if path.is_file() and path.suffix not in {".lock", ".jsonl"}
    }


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": sorted(key for key in set(before) & set(after) if before[key] != after[key]),
    }


def quarantined(store: Path) -> list[str]:
    return sorted(
        path.relative_to(store).as_posix()
        for path in store.rglob("*")
        if path.is_file() and ("quarantine" in path.relative_to(store).parts
                               or path.name.endswith(".corrupt"))
    )


def failure(exc: BaseException) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None)
    return {
        "type": type(exc).__name__,
        "code": getattr(code, "value", code),
        "details": details if isinstance(details, dict) else None,
        "category": getattr(exc, "category", None),
        "traceback": traceback.format_exception(exc)[-6:],
    }


async def attempt(coroutine_or_callable):
    try:
        value = coroutine_or_callable()
        if inspect.isawaitable(value):
            value = await value
        return {"ok": True, "value": value}
    except Exception as exc:  # observed, asserted by the parent test
        return {"ok": False, "error": failure(exc)}


class Stack:
    """One beta.8 process: governance service, F3 runtime and counted fakes."""

    def __init__(self, kind: str, store: Path, audit: Path, now: datetime):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository

        self.kind = kind
        self.calls: list[str] = []
        self.exit = ExitStack()
        self.transport = lambda: {}
        clock = lambda: now  # noqa: E731
        f3i = importlib.import_module("tests.test_f3_runtime_integration")
        identity = counted_identity(f3i._provider_identity, "provider_identity_reader", self.calls)
        service_kwargs: dict[str, Any] = {}
        runtime_kwargs: dict[str, Any] = {"backup_gateway": None, "lifecycle_gateway": None}
        if kind == "configuration":
            dev14 = importlib.import_module("tests.test_dev14_configuration_plans")
            fake = instrument(dev14.FakeConfigurationGateway(), "configuration_fake", self.calls)
            legacy = fake
            runtime_kwargs["configuration_gateway"] = instrument(
                f3i._ExactFakeConfigurationGateway(fake), "configuration_gateway", self.calls)
            self.transport = lambda: {"configuration_fake_calls": list(map(list, fake.calls))}
        elif kind == "operational":
            lifecycle_module = importlib.import_module("tests.test_2_1a_beta2_operational_lifecycle")
            lifecycle = instrument(f3i._F3LifecycleGateway(), "lifecycle_gateway", self.calls)
            lifecycle.now = clock
            legacy = instrument(lifecycle_module.LegacyGateway(), "legacy_gateway", self.calls)
            service_kwargs["lifecycle_gateway"] = lifecycle
            runtime_kwargs["lifecycle_gateway"] = lifecycle
            runtime_kwargs["configuration_gateway"] = instrument(
                f3i._UnusedExactConfigurationGateway(), "configuration_gateway", self.calls)
            self.transport = lambda: {
                "lifecycle_dispatch_count": lifecycle.dispatch_count,
                "lifecycle_planning_count": lifecycle.planning_count,
                "lifecycle_verification_count": lifecycle.verification_count,
            }
        elif kind == "helper":
            b37 = importlib.import_module("tests.test_beta37_exact_helper_state")
            helper = instrument(b37.FakeHelperStateGateway(), "helper_state_gateway", self.calls)
            dependency = instrument(b37.FakeDependencyRiskReader(helper.entity_id),
                                    "helper_dependency_risk_reader", self.calls)
            legacy = instrument(b37.UnusedLegacyGateway(), "legacy_gateway", self.calls)
            service_kwargs.update(helper_state_gateway=helper,
                                  helper_dependency_risk_reader=dependency)
            runtime_kwargs["helper_state_gateway"] = helper
            runtime_kwargs["configuration_gateway"] = instrument(
                b37.UnusedConfigurationGateway(), "configuration_gateway", self.calls)
            identity = counted_identity(b37.forbidden_upstream_identity,
                                        "provider_identity_reader", self.calls)
            self.transport = lambda: {"helper_dispatch_count": helper.dispatch_count}
        elif kind == "dashboard":
            h177 = importlib.import_module("tests.test_hamcp177_dashboard_write_admission")
            peer = h177.DashboardPeer(h177.catalog(h177.EXTRAS))
            provider = h177.UpstreamDashboardProvider()
            provider._transport = h177.McpDashboardTransport(
                "", timeout_seconds=3, client_version="synthetic")
            self.exit.enter_context(
                patch.object(h177.mcp_client, "streamablehttp_client", peer.connect))
            gateway = instrument(h177.DashboardExecutionGateway(provider, response_limit=60_000),
                                 "dashboard_gateway", self.calls)
            legacy = instrument(h177._UnusedConfigurationGateway(), "legacy_gateway", self.calls)
            identity = counted_identity(h177._unrelated_lifecycle_provider_identity,
                                        "provider_identity_reader", self.calls)
            service_kwargs.update(dashboard_gateway=gateway, provider_identity_reader=identity)
            runtime_kwargs["dashboard_gateway"] = gateway
            runtime_kwargs["configuration_gateway"] = instrument(
                h177._UnusedConfigurationGateway(), "configuration_gateway", self.calls)
            self.transport = lambda: {"dashboard_peer_events": list(peer.events),
                                      "dashboard_peer_calls": peer.names()}
        elif kind == "entity_metadata":
            fixtures = importlib.import_module("tests.hamcp069a_entity_fixtures")
            from ha_mcp_engineering.governance.entity_metadata_disclosure import (
                runtime_consumer_evidence,
            )
            from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway

            ws = fixtures.SyntheticRegistryWebSocket()
            rest = fixtures.SyntheticRegistryRest(ws)
            resource = ConfigurationResourceGateway(rest, ws)
            snapshot = fixtures.dependency_snapshot()

            async def snapshot_reader(*, fenced):
                self.calls.append("entity_metadata_snapshot_reader")
                return snapshot

            resource.entity_metadata_consumer_evidence = runtime_consumer_evidence(snapshot_reader)
            instrument(ws, "registry_websocket", self.calls)
            instrument(rest, "registry_rest", self.calls)
            legacy = instrument(resource, "configuration_gateway", self.calls)
            metadata = instrument(resource.entity_metadata, "entity_metadata_gateway", self.calls)
            service_kwargs.update(entity_metadata_gateway=metadata,
                                  entity_metadata_snapshot_reader=snapshot_reader)
            runtime_kwargs["configuration_gateway"] = resource
            self.transport = lambda: {"registry_websocket_commands": [c.get("type") for c in ws.commands],
                                      "registry_rest_requests": [list(r) for r in rest.requests]}
        else:  # pragma: no cover - driver table error
            raise AssertionError(kind)
        self.service = ChangeGovernanceService(
            ChangePlanRepository(store), legacy,
            AuditLogger(str(audit), "synthetic-hamcp181-beta8-downgrade"),
            now=clock, **service_kwargs)
        self.runtime = F3RuntimeIntegration(
            service=self.service, storage_root=str(store),
            provider_identity_reader=identity, retention_days=90, **runtime_kwargs)
        self.service.f3_runtime = self.runtime

    async def recovery_pass(self, trigger: str) -> None:
        # beta.8 application._run_f3_recovery_pass(trigger, strict=True), less
        # the fan/power services that this governed process does not own.
        await self.runtime.recover_once(trigger)
        await self.service.reconcile_execution_tasks(trigger=trigger)
        await self.service.reconcile_operational_plans(trigger=trigger)

    def snapshot(self, store: Path) -> dict[str, Any]:
        readiness = self.runtime.readiness_state()
        health = self.runtime.health()
        summary = self.service.health_summary()
        return {
            "readiness": readiness,
            "runtime_status": health.get("status"),
            "runtime_execution_ready": health.get("execution_ready"),
            "runtime_nonterminal_execution_count": health.get("nonterminal_execution_count"),
            "runtime_corrupt_record_count": health.get("corrupt_record_count"),
            "plan_corruption_count": self.service.repository.health()["corruption_count"],
            "task_corruption_count": self.service.task_repository.health()["corruption_count"],
            "storage_corruption_count": summary.get("storage_corruption_count"),
            "quarantined": quarantined(store),
            "provider_calls": list(self.calls),
            "transport": self.transport(),
        }


def plan_decode(store: Path) -> dict[str, Any]:
    """beta.8's own decode of each stored plan, before anything else runs.

    ``ChangePlanRepository._load`` quarantines a plan on ValueError,
    TypeError or KeyError from ``ChangePlan.from_dict``; this records that
    exception, so a quarantine is explained by beta.8 code rather than
    inferred.
    """

    from ha_mcp_engineering.governance.models import ChangePlan

    result: dict[str, Any] = {}
    paths = sorted(store.glob("*.json")) + sorted(store.glob("operational-administration-v3/*.json"))
    for path in paths:
        try:
            ChangePlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
            result[path.relative_to(store).as_posix()] = None
        except Exception as exc:  # observed, asserted by the parent test
            result[path.relative_to(store).as_posix()] = {"type": type(exc).__name__,
                                                          "message": str(exc)[:300]}
    return result


def surface(value: Any) -> Any:
    keep = ("status", "state", "task_id", "plan_id", "terminal_outcome", "task_state",
            "task_reused", "redispatch_performed", "provider_dispatch_occurred", "outcome_code")
    if isinstance(value, dict):
        return {key: value[key] for key in keep if key in value}
    return value


async def governed(shape: str, source: Path, work: Path) -> dict[str, Any]:
    from ha_mcp_engineering.request_context import begin_request, end_request

    kind = GOVERNED_KINDS[shape]
    store = work / shape / "plans"
    shutil.copytree(source / shape / "store", store)
    (task_path,) = (store / "execution-tasks-v1").glob("*.json")
    stored = json.loads(task_path.read_text(encoding="utf-8"))
    now = latest_time(store)
    original = file_hashes(store)
    out: dict[str, Any] = {"kind": kind, "task_id": stored["task_id"],
                           "plan_id": stored["plan_id"], "stored_state": stored["state"],
                           "stored_terminal_outcome": stored["terminal_outcome"],
                           "plan_decode": plan_decode(store)}
    telemetry, context = begin_request("synthetic-hamcp181-beta8-downgrade")
    telemetry.caller_id = "synthetic-beta8-downgrade-reader"
    try:
        built = await attempt(lambda: Stack(kind, store, work / shape / "audit.jsonl", now))
        if not built["ok"]:
            out["startup_error"] = built["error"]
            return out
        stack = built["value"]
        with stack.exit:
            started = await attempt(lambda: stack.recovery_pass("startup"))
            out["startup_error"] = None if started["ok"] else started["error"]
            out["startup"] = stack.snapshot(store)
            out["startup_files"] = diff(original, file_hashes(store))
            service, task_id, plan_id = stack.service, stored["task_id"], stored["plan_id"]
            reads = {
                "get_change_plan": await attempt(lambda: service.get_plan(plan_id)),
                "list_change_plans": await attempt(lambda: service.list_plans(limit=100)),
                "get_execution_task": await attempt(lambda: service.get_execution_task(task_id)),
                "list_execution_tasks": await attempt(lambda: service.list_execution_tasks()),
            }
            out["reads"] = {}
            for name, result in reads.items():
                if not result["ok"]:
                    out["reads"][name] = {"error": result["error"]}
                    continue
                value = result["value"]
                if name == "list_change_plans":
                    items = value.get("plans", [])
                    out["reads"][name] = {"listed": [surface(i) for i in items
                                                     if i.get("plan_id") == plan_id]}
                elif name == "list_execution_tasks":
                    items = value.get("tasks", [])
                    out["reads"][name] = {"listed": [surface(i) for i in items
                                                     if i.get("task_id") == task_id]}
                elif name == "get_change_plan":
                    plan = value.get("plan", value)
                    out["reads"][name] = {"plan_id": plan.get("plan_id"),
                                          "status": plan.get("status")}
                else:
                    out["reads"][name] = surface(value)
            out["after_reads_calls"] = list(stack.calls)
            duplicate = await attempt(lambda: service.apply(plan_id, stored["plan_hash"]))
            out["duplicate_apply"] = (
                {"ok": True, "result": surface(duplicate["value"])}
                if duplicate["ok"] else {"ok": False, "error": duplicate["error"]})
            out["after_duplicate"] = stack.snapshot(store)
        restarted = await attempt(lambda: Stack(kind, store, work / shape / "audit.jsonl", now))
        if not restarted["ok"]:
            out["restart_error"] = restarted["error"]
            return out
        stack = restarted["value"]
        with stack.exit:
            first = await attempt(lambda: stack.recovery_pass("startup"))
            second = await attempt(lambda: stack.recovery_pass("periodic"))
            out["restart_error"] = next((r["error"] for r in (first, second) if not r["ok"]), None)
            out["restart"] = stack.snapshot(store)
        out["final_files"] = diff(original, file_hashes(store))
        return out
    finally:
        end_request(context)


async def typed(shape: str, source: Path, work: Path, provenance: dict) -> dict[str, Any]:
    from ha_mcp_engineering.request_context import begin_request, end_request

    kind = TYPED_KINDS[shape]
    store = work / shape / "store"
    shutil.copytree(source / shape / "store", store)
    recorded = provenance["shapes"][shape]
    task_id = recorded["task_id"]
    now = latest_time(store)
    original = file_hashes(store)
    out: dict[str, Any] = {"kind": kind, "task_id": task_id}
    if kind == "fan":
        module = importlib.import_module("tests.test_typed_fan")
        from ha_mcp_engineering.fan.authority import FanCoreAuthority as Authority
        from ha_mcp_engineering.fan.contracts import FanRequest as Request, digest
        from ha_mcp_engineering.fan.service import FAN_OPERATIONS as operations, FanService as Service
        from ha_mcp_engineering.providers.upstream_fan import FanProvider as Provider
        transport_type, binding = module.Transport, "ordinary_fan_binding"

        def make_provider(transport):
            return Provider(transport, lambda: transport.authority)
    else:
        module = importlib.import_module("tests.test_typed_power")
        from ha_mcp_engineering.power.contracts import PowerRequest as Request, digest
        from ha_mcp_engineering.power.service import (
            POWER_OPERATIONS as operations, PowerCoreAuthority as Authority,
            PowerService as Service,
        )
        from ha_mcp_engineering.providers.upstream_power import PowerProvider as Provider
        transport_type, binding = (lambda: module.PowerTransport(module.PowerTests.version),
                                   "ordinary_power_binding")

        def make_provider(transport):
            return Provider(transport, lambda *args: transport.authority)
    from ha_mcp_engineering.tools.governance import get_execution_task

    def build():
        calls: list[str] = []
        core = instrument(module.Core(), "core_authority", calls)
        transport = instrument(transport_type(), "transport", calls)
        service = Service(str(store), make_provider(transport), Authority(core), now=lambda: now)
        return service, transport, calls

    def observe(service, transport, calls):
        return {"health": service.health(), "quarantined": quarantined(store),
                "transport_calls": [list(map(str, c[:1])) for c in transport.calls],
                "transport_writes": transport.writes, "method_calls": list(calls)}

    telemetry, context = begin_request("synthetic-hamcp181-beta8-downgrade")
    try:
        service, transport, calls = build()
        started = await attempt(service.recover_once)
        out["startup_error"] = None if started["ok"] else started["error"]
        out["startup"] = observe(service, transport, calls)
        out["startup_files"] = diff(original, file_hashes(store))
        out["load"] = await attempt(lambda: service.load(task_id) is not None)
        receipt = await attempt(lambda: service.receipt(task_id))
        out["receipt"] = surface(receipt["value"]) if receipt["ok"] else {"error": receipt["error"]}
        with patch.object(operations, "service", service):
            read = await attempt(lambda: get_execution_task(task_id))
        out["get_execution_task"] = (
            json.loads(read["value"]) if read["ok"] else {"error": read["error"]})
        if isinstance(out["get_execution_task"].get("data"), dict):
            out["get_execution_task"] = {"success": out["get_execution_task"].get("success"),
                                         "data": surface(out["get_execution_task"]["data"])}
        request = Request(**recorded["request"])
        setattr(telemetry, binding, digest(request.model_dump()))
        duplicate = await attempt(lambda: service.control(request))
        setattr(telemetry, binding, None)
        out["duplicate_control"] = (
            {"ok": True, "result": surface(duplicate["value"])}
            if duplicate["ok"] else {"ok": False, "error": duplicate["error"]})
        out["after_duplicate"] = observe(service, transport, calls)
        await service.close()
        service, transport, calls = build()
        first = await attempt(service.recover_once)
        with patch.object(operations, "service", service):
            reread = await attempt(lambda: get_execution_task(task_id))
        out["restart_error"] = next((r["error"] for r in (first, reread) if not r["ok"]), None)
        out["restart"] = observe(service, transport, calls)
        await service.close()
        out["final_files"] = diff(original, file_hashes(store))
        return out
    finally:
        end_request(context)


async def main_async(beta8: Path, fixtures: Path, work: Path) -> dict[str, Any]:
    provenance = json.loads((fixtures / "provenance.json").read_text(encoding="utf-8"))
    import ha_mcp_engineering

    package = Path(ha_mcp_engineering.__file__).resolve()
    results: dict[str, Any] = {
        "reader_package": str(package),
        "reader_is_beta8": package.is_relative_to(beta8.resolve()),
        "shapes": {},
    }
    for shape in sorted(provenance["shapes"]):
        if shape in GOVERNED_KINDS:
            results["shapes"][shape] = await governed(shape, fixtures, work)
        elif shape in TYPED_KINDS:
            results["shapes"][shape] = await typed(shape, fixtures, work, provenance)
        else:
            results["shapes"][shape] = {"unmapped": True}
    return results


def sdk_gate(results: dict[str, Any]) -> None:
    """Admit the installed MCP SDK when it differs from beta.8's exact pin.

    beta.8 refuses to import its tool layer unless the installed ``mcp``
    distribution is exactly its pin (1.28.1). The storage reader does not use
    the SDK, so only that one version gate is replaced, and only when the
    versions differ; the observation records which SDK ran. Run with an
    interpreter that has beta.8's exact requirements to avoid the shim.
    """

    from importlib.metadata import version

    from ha_mcp_engineering import mcp_sdk_compatibility as compatibility

    installed = version("mcp")
    results["mcp_sdk"] = {"installed": installed,
                          "beta8_pin": compatibility.PINNED_MCP_SDK_VERSION,
                          "version_gate_shimmed": installed != compatibility.PINNED_MCP_SDK_VERSION}
    if results["mcp_sdk"]["version_gate_shimmed"]:
        compatibility._require_pinned_sdk_version = lambda: None


def main() -> None:
    beta8, fixtures, work = (Path(item) for item in sys.argv[1:4])
    sys.path[:0] = [str(beta8 / "hass_mcp_engineering_beta"), str(beta8 / "tests"), str(beta8)]
    gate: dict[str, Any] = {}
    sdk_gate(gate)
    results = asyncio.run(main_async(beta8, fixtures, work))
    results.update(gate)
    print(json.dumps(results, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
