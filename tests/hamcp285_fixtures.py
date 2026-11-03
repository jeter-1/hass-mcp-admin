"""HAMCP-285 input_boolean harness: real governance, real F3, real reader.

The governance service and F3 runtime are constructed exactly as the
HAMCP-089 (beta37) input_boolean acceptance harness constructs them, with the
same synthetic clock, entity, audit secret and storage layout, so the exact
beta.59 records in ``tests/fixtures/hamcp285_beta59_input_boolean_v13`` load
into it unchanged.  The dependency reader is the shipped runtime reader
``helper_dependency.read_runtime_helper_dependency_risk`` looked up at call
time, over a real ``DependencyIndex`` built from installation-shaped synthetic
automation configurations.  The only substitution is the process-wide
``DEPENDENCY_ANALYSIS.require()`` lookup, which returns that index.  Because
the reader is the module-level runtime function, the same tests run against
the parent commit, where it is the v13 reader.

Nothing here reaches a network, a live Home Assistant, or the ha-mcp
provider: every request outside the reviewed dependency reads fails the test.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.dependency import DEPENDENCY_ANALYSIS  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_dependency as helper_module,
)
from ha_mcp_engineering.governance.helper_state import (  # noqa: E402
    HelperStateGatewayError,
    helper_state_provider_evidence,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)


ENTITY = "input_boolean.beta37_exact_action"
DIRECT_PROVIDER_SLUG = "direct_home_assistant_state"
RESPONSE_LIMIT = 60_000
NOISE_AUTOMATIONS = 60
LABEL_AUTOMATIONS = 5
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]
AUDIT_SECRET = "synthetic-beta37-secret"


# --------------------------------------------------------------------------
# Installation-shaped synthetic Home Assistant
# --------------------------------------------------------------------------


def installation_configs() -> dict[str, dict[str, Any]]:
    """Template-heavy automations with broad triggers; none names the helper."""

    configs: dict[str, dict[str, Any]] = {}
    for n in range(NOISE_AUTOMATIONS):
        configs[f"room_{n:02d}"] = {
            "alias": f"Synthetic room {n}",
            "triggers": [
                {"trigger": "state", "entity_id": f"sensor.room_{n}"},
                {"trigger": "event", "event_type": "call_service",
                 "event_data": {"domain": "light", "service": "turn_on"}},
            ],
            "conditions": [{
                "condition": "template",
                "value_template": (
                    "{{ states('sensor.room_%d') | float(0) > 3 "
                    "and now().hour > 6 }}" % n
                ),
            }],
            "actions": [{"action": "light.turn_on",
                         "target": {"entity_id": f"light.room_{n}"}}],
        }
    for n in range(LABEL_AUTOMATIONS):
        configs[f"kitchen_{n}"] = {
            "alias": f"Synthetic kitchen {n}",
            "triggers": [{"trigger": "time_pattern", "minutes": "/5"}],
            "conditions": [{
                "condition": "template",
                "value_template": (
                    "{{ label_entities('kitchen') | select('is_state', 'on') "
                    "| list | count > 0 }}"
                ),
            }],
            "actions": [{"action": "lock.lock",
                         "target": {"entity_id": f"lock.door_{n}"}}],
        }
    return configs


def installation_registry() -> list[Any]:
    return [
        {"entity_id": "switch.kitchen_kettle", "labels": ["kitchen"]},
        # One unrelated malformed record: labels must be a list.
        {"entity_id": "sensor.unrelated_bad", "labels": "kitchen"},
    ]


def consumer(alias: str, *, triggers=None, conditions=None, actions=None):
    return {
        "alias": alias,
        "triggers": triggers or [{"trigger": "time_pattern", "minutes": "/5"}],
        "conditions": conditions or [],
        "actions": actions or [{"action": "notify.notify",
                                "data": {"message": "synthetic"}}],
    }


def template(value: str) -> list[dict[str, Any]]:
    return [{"condition": "template", "value_template": value}]


def over_budget_consumers(count: int = 60) -> dict[str, dict[str, Any]]:
    """Consumers that each literally reference the target."""

    return {
        f"watch_{n:03d}": consumer(
            f"Watch {n}",
            triggers=[{"trigger": "state", "entity_id": ENTITY}],
        )
        for n in range(count)
    }


class InstallationRest:
    def __init__(self, configs: dict[str, dict[str, Any]],
                 extra_states: list[dict[str, Any]]) -> None:
        self.configs = configs
        self.extra_states = extra_states
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, **_: Any) -> Any:
        self.requests.append((method, path))
        if method != "GET":
            raise AssertionError(f"unreviewed dependency request: {method} {path}")
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            states = [
                {"entity_id": ENTITY, "state": "off", "attributes": {}},
                {"entity_id": "sensor.which", "state": "beta37_exact_action",
                 "attributes": {}},
                *deepcopy(self.extra_states),
            ]
            states += [
                {"entity_id": f"automation.{key}", "state": "on",
                 "attributes": {"id": key, "friendly_name": value["alias"]}}
                for key, value in self.configs.items()
            ]
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return deepcopy(self.configs[path.removeprefix(prefix)])
        raise AssertionError(f"unreviewed dependency request: {path}")


class InstallationWebSocket:
    def __init__(self, registry: list[Any], labels: list[dict[str, Any]]) -> None:
        self.registry = registry
        self.labels = labels

    async def command(self, payload: dict[str, Any]) -> Any:
        if payload == {"type": "config/entity_registry/list"}:
            return deepcopy(self.registry)
        if payload == {"type": "config/label_registry/list"}:
            return deepcopy(self.labels)
        raise AssertionError(f"unreviewed dependency command: {payload}")


# --------------------------------------------------------------------------
# HAMCP-089 (beta37) service doubles, copied so this module does not depend
# on another test module's internals
# --------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


class UnusedLegacyGateway:
    async def get(self, *_args):
        raise AssertionError("legacy configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("legacy configuration gateway must not be used")

    async def validate(self):
        raise AssertionError("legacy configuration gateway must not be used")


class AbsentStateRest:
    async def request(self, *_args, **_kwargs):
        return ExpectedHttpStatus(404)


class UnusedConfigurationGateway(ConfigurationResourceGateway):
    def __init__(self) -> None:
        self.rest_client = AbsentStateRest()
        self.websocket_client = object()

    async def get(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def read(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def validate_all(self):
        raise AssertionError("configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("configuration gateway must not be used")


class FakeHelperStateGateway:
    """The exact input_boolean state provider double (one entity)."""

    def __init__(self) -> None:
        self.entity_id = ENTITY
        self.state = "off"
        self.last_changed = "2026-08-13T12:00:00+00:00"
        self.dispatch_count = 0
        self.dispatched: list[tuple[str, str]] = []
        self.provider_evidence: dict[str, Any] | None = None

    def set_observed_state(self, state: str, clock: Clock) -> None:
        self.state = state
        clock.advance()
        self.last_changed = clock().isoformat()

    async def planning_evidence(self, entity_id: str):
        if entity_id != self.entity_id:
            raise HelperStateGatewayError("entity_not_found")
        return {
            "provider": (
                deepcopy(self.provider_evidence)
                if self.provider_evidence is not None
                else helper_state_provider_evidence()
            ),
            "baseline": await self.read_state(entity_id),
        }

    async def read_state(self, entity_id: str):
        if entity_id != self.entity_id:
            raise HelperStateGatewayError("entity_not_found")
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "last_changed": self.last_changed,
        }

    async def set_state(self, entity_id: str, desired_state: str, *, before_dispatch):
        if entity_id != self.entity_id or desired_state not in {"on", "off"}:
            raise AssertionError("unreviewed helper-state arguments reached dispatch")
        await before_dispatch()
        self.dispatch_count += 1
        self.dispatched.append((entity_id, desired_state))
        self.state = desired_state
        self.last_changed = f"2026-08-13T12:00:{4 + self.dispatch_count:02d}+00:00"
        return SimpleNamespace(provider_response_received=True)


async def forbidden_upstream_identity():
    raise AssertionError("direct helper state must not depend on ha-mcp identity")


# --------------------------------------------------------------------------
# Service test case
# --------------------------------------------------------------------------


class InputBooleanServiceTestCase(unittest.IsolatedAsyncioTestCase):
    """The beta37 service harness with the real runtime dependency reader."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.helper = FakeHelperStateGateway()
        self.index: Any = None
        self.reads: list[bool] = []
        self.service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            UnusedLegacyGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), AUDIT_SECRET),
            now=self.clock,
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.read_dependency,
        )
        self.telemetry, self.context = begin_request("hamcp285-input-boolean")
        self.telemetry.caller_id = "mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=forbidden_upstream_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def asyncTearDown(self) -> None:
        end_request(self.context)
        self.temp.cleanup()

    async def read_dependency(self, entity_id: str, *, refresh: bool = True,
                              fenced: bool = False) -> dict[str, Any]:
        """The shipped runtime reader over this test's index."""

        self.reads.append(fenced)
        if self.index is None:
            raise AssertionError("install() must run before a dependency read")
        with mock.patch.object(
            DEPENDENCY_ANALYSIS, "require",
            return_value=SimpleNamespace(index=self.index),
        ):
            return await helper_module.read_runtime_helper_dependency_risk(
                entity_id, refresh=refresh, fenced=fenced
            )

    def install(self, *, extra=None, registry=None, labels=None,
                extra_states=(), base_registry=True, noise=True) -> None:
        configs = installation_configs() if noise else {}
        configs.update(extra or {})
        self.rest = InstallationRest(configs, list(extra_states))
        self.ws = InstallationWebSocket(
            (installation_registry() if base_registry else [])
            + list(registry or []),
            [{"label_id": "kitchen", "name": "Kitchen"}, *(labels or [])],
        )
        self.index = DependencyIndex(
            DirectHaDependencyProvider(self.rest, self.ws, concurrency=4)
        )

    async def create(self, desired_state: str = "on") -> dict[str, Any]:
        return await self.service.create_helper_state_plan(
            entity_id=ENTITY, desired_state=desired_state
        )

    async def planned(self, desired_state: str = "on", **install: Any) -> dict[str, Any]:
        self.install(**install)
        created = await self.create(desired_state)
        self.assertEqual(created["plan"]["status"], "awaiting_approval", created)
        return created

    async def grant(self, plan: dict[str, Any]) -> dict[str, Any]:
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:hamcp285",
        )
        return plan

    async def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        return await self.service.apply(plan["plan_id"], plan["plan_hash"])

    def child(self, task_id: str) -> Any:
        return self.runtime.children.get(
            self.runtime.children.declarations_for_task(task_id)[0]["child_id"]
        )

    def child_codes(self, task_id: str) -> set[str]:
        return {
            code
            for event in self.child(task_id).events
            for code in event["diagnostic_codes"]
        }

    def stored_binding(self, plan: dict[str, Any]) -> dict[str, Any]:
        stored = self.service._load(plan["plan_id"])
        return stored.operational.baseline["dependency_risk"]

    def lock_modes(self, plan: dict[str, Any], *, readback: bool = False,
                   baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        stored = self.service._load(plan["plan_id"])
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=ENTITY),
            authoritative_provider_slug=stored.operational.provider,
            baseline=deepcopy(
                baseline if baseline is not None else stored.operational.baseline
            ),
        )
        calculator = OperationalLockSetCalculator()
        requests = (
            calculator.calculate_for_readback(operation)
            if readback else calculator.calculate(operation)
        )
        return {item.key: item.mode for item in requests}

    @staticmethod
    def chars(value: Any) -> int:
        return len(json.dumps(value, indent=2, ensure_ascii=True, default=str))


__all__ = [
    "AUDIT_SECRET",
    "ENTITY",
    "InputBooleanServiceTestCase",
    "NOISE_AUTOMATIONS",
    "RESPONSE_LIMIT",
    "consumer",
    "over_budget_consumers",
    "template",
]
