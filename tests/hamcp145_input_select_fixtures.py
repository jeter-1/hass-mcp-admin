"""Offline HAMCP-145 input_select option fixtures.

A synthetic Home Assistant behind the real ``InputSelectGateway``: an exact
``GET /states/{entity_id}`` REST read and one WebSocket ``call_service`` for
``input_select.select_option``, answered as Core 2026.10.3 answers them (an
option the entity does not offer raises a service error after the call).
Both clients refuse every call outside a Core-authority window, exactly as the
real clients refuse it, and every request outside the reviewed vocabulary
fails the test.  The dependency reader wraps the reviewed v13 binding shape in
the real input_select wrapper.  Every value is synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    EntityNotFoundError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    input_select_option as contract,
)
from ha_mcp_engineering.governance.input_select_dependency import (  # noqa: E402
    _wrap,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    f3_requirements,
)

from tests.core_authority_fakes import require_core_authority  # noqa: E402
from tests.hamcp069b_fixtures import (  # noqa: E402
    PROFILES_069B,
    AdmittingCore,
    AvailabilityExposureServiceTestCase,
    _provider_identity,
)
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    FakeDependencyRiskReader,
)


ENTITY = "input_select.house_mode"
OPTIONS = ["Home", "Away", "Night"]
PROFILES_145 = frozenset({contract.PROFILE})
STATE_ROUTE = f"/states/{ENTITY}"


class SyntheticInputSelectRest:
    """Exact-state reads only; refused outside a Core-authority window."""

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self.authority_refusals: list[tuple[str, str]] = []
        # Hooks run on the Nth successful read (1-based), before it answers.
        self.on_read: dict[int, Callable[[], None]] = {}
        self.clock = 0

    def add(self, entity_id: str, state: str, options: list[str]) -> None:
        self.states[entity_id] = {
            "entity_id": entity_id,
            "state": state,
            "attributes": {"options": list(options), "friendly_name": "Mode"},
            "last_changed": self._stamp(),
            "last_updated": self._stamp(),
        }

    def _stamp(self) -> str:
        self.clock += 1
        return f"2026-10-22T12:00:{self.clock:02d}+00:00"

    def set_external(self, entity_id: str, *, state: str | None = None,
                     options: list[str] | None = None) -> None:
        """A change made by something other than this plan."""

        current = self.states[entity_id]
        if state is not None and state != current["state"]:
            current["state"] = state
            current["last_changed"] = self._stamp()
        if options is not None:
            current["attributes"]["options"] = list(options)
            current["last_updated"] = self._stamp()

    def reads(self) -> int:
        return len(self.requests)

    async def request(self, method: str, path: str, **_: Any) -> Any:
        require_core_authority(self, method, path)
        if method != "GET" or not path.startswith("/states/input_select."):
            raise AssertionError(f"unreviewed REST request: {method} {path}")
        self.requests.append((method, path))
        hook = self.on_read.pop(len(self.requests), None)
        if hook is not None:
            hook()
        value = self.states.get(path.removeprefix("/states/"))
        if value is None:
            raise EntityNotFoundError(details={"status": 404})
        return deepcopy(value)


class SyntheticInputSelectWebSocket:
    """Only ``input_select.select_option`` for one exact entity is accepted."""

    def __init__(self, rest: SyntheticInputSelectRest) -> None:
        self.rest = rest
        self.commands: list[dict[str, Any]] = []
        self.authority_refusals: list[tuple[str, str]] = []
        # success | lost_applied | lost_not_applied | rejected | ignored
        self.mode = "success"

    async def command(self, payload: dict[str, Any]) -> Any:
        require_core_authority(self, "WS", str(payload.get("type")))
        if (
            set(payload) != {"type", "domain", "service", "service_data", "target"}
            or payload["type"] != "call_service"
            or payload["domain"] != "input_select"
            or payload["service"] != "select_option"
            or set(payload["service_data"]) != {"option"}
            or set(payload["target"]) != {"entity_id"}
        ):
            raise AssertionError(f"unreviewed WebSocket command: {payload}")
        self.commands.append(deepcopy(payload))
        entity_id = payload["target"]["entity_id"]
        option = payload["service_data"]["option"]
        state = self.rest.states.get(entity_id)
        if state is None or option not in state["attributes"]["options"]:
            # Core raises a HomeAssistantError from async_select_option.
            raise HomeAssistantApiError(
                details={"provider_response_received": True}
            )
        if self.mode == "rejected":
            raise HomeAssistantApiError(
                details={"provider_response_received": True}
            )
        if self.mode not in {"lost_not_applied", "ignored"}:
            self.rest.set_external(entity_id, state=option)
        if self.mode in {"lost_applied", "lost_not_applied"}:
            raise HomeAssistantTimeoutError()
        return None


class InputSelectDependencyReader(FakeDependencyRiskReader):
    """The reviewed v13 binding shape, wrapped by the real input_select wrapper."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(entity_id)
        self.records: list[dict[str, Any]] = []
        self.reprojected = 0
        self.calls: list[bool] = []

    async def __call__(self, entity_id: str, *, refresh: bool = True,
                       fenced: bool = False) -> dict[str, Any]:
        self.calls.append(fenced)
        evidence = await super().__call__(entity_id, refresh=refresh, fenced=fenced)
        evidence["binding"] = _wrap(
            evidence["binding"],
            records=deepcopy(self.records),
            truncated=False,
            reprojected=self.reprojected,
        )
        return evidence


def trigger_record(automation_id: str, *, to: list[str] | None = None,
                   from_: list[str] | None = None, complete: bool = True,
                   path: str = "triggers[0]") -> dict[str, Any]:
    return {
        "automation_id": automation_id,
        "configuration_path": path,
        "to": to,
        "from": from_,
        "not_to": [],
        "not_from": [],
        "complete": complete,
        "reason_codes": [] if complete else ["trigger_value_template"],
    }


class AdmittingInputSelectCore(AdmittingCore):
    """Admits (or withholds) the runtime-only input_select profile as well."""

    def __init__(self, admitted: Any = PROFILES_069B | PROFILES_145) -> None:
        super().__init__(admitted)

    def withdraw(self, *profiles: str) -> None:
        self.admitted -= set(profiles or PROFILES_145)

    def _admits(self, requirements: Any) -> bool:
        gated = PROFILES_069B | PROFILES_145
        return set(requirements) & gated <= self.admitted

    def acquire(self, capability_ids: tuple[str, ...], *, target: Any = None,
                **_: Any) -> Any:
        self.planning_leases.append((tuple(capability_ids), deepcopy(target)))
        if not self._admits(capability_ids) or (
            set(capability_ids) & PROFILES_145 and target is None
        ):
            return None
        return SimpleNamespace(requirements=tuple(capability_ids))


class InputSelectServiceTestCase(AvailabilityExposureServiceTestCase):
    """Real service, real F3 runtime, real input_select gateway, synthetic Core."""

    core_admits_input_select = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.select_rest = SyntheticInputSelectRest()
        self.select_ws = SyntheticInputSelectWebSocket(self.select_rest)
        self.select_rest.add(ENTITY, "Home", OPTIONS)
        self.select_gateway = contract.InputSelectGateway(
            self.select_rest, self.select_ws
        )
        self.dependency = InputSelectDependencyReader(ENTITY)
        self.service.input_select_gateway = self.select_gateway
        self.service.input_select_dependency_risk_reader = self.dependency
        self.core = AdmittingInputSelectCore(
            PROFILES_069B
            | (PROFILES_145 if self.core_admits_input_select else frozenset())
        )
        await self.restart_runtime()

    async def restart_runtime(self) -> None:
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=self.resource_gateway,
            backup_gateway=None,
            lifecycle_gateway=None,
            input_select_gateway=getattr(self, "select_gateway", None),
            provider_identity_reader=_provider_identity,
            retention_days=90,
            core_runtime=self.core,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")
        self.assertEqual(self.runtime.readiness_state()["faults"], [])

    async def create_select(self, option: str = "Away", **arguments: Any) -> dict[str, Any]:
        arguments.setdefault("reason", "Synthetic mode change")
        return await self.service.create_input_select_option_plan(
            {"entity_id": ENTITY, "option": option, **arguments}
        )

    async def approved_select(self, option: str = "Away") -> dict[str, Any]:
        created = await self.create_select(option)
        await self.approve(created["plan"])
        return created["plan"]

    async def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        return await self.service.apply(plan["plan_id"], plan["plan_hash"])

    def current(self) -> str:
        return self.select_rest.states[ENTITY]["state"]

    def sent(self) -> list[dict[str, Any]]:
        return self.select_ws.commands

    def ha_calls(self) -> int:
        return self.select_rest.reads() + len(self.select_ws.commands)


__all__ = [
    "AdmittingInputSelectCore",
    "ENTITY",
    "InputSelectDependencyReader",
    "InputSelectServiceTestCase",
    "OPTIONS",
    "PROFILES_145",
    "STATE_ROUTE",
    "SyntheticInputSelectRest",
    "SyntheticInputSelectWebSocket",
    "f3_requirements",
    "trigger_record",
]
