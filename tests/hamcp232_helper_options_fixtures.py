"""Offline HAMCP-232 helper-options fixtures.

A synthetic Home Assistant options-flow server, config-entry store and entity
registry behind the real ``HelperOptionsResourceGateway``.  The REST server
answers ``POST /config/config_entries/options/flow`` (open),
``DELETE /config/config_entries/options/flow/{flow_id}`` (abort) and
``POST /config/config_entries/options/flow/{flow_id}`` (the one step submit)
as Core 2026.10.3's ``SchemaOptionsFlowHandler`` does: the form carries every
stored value as its ``suggested_value``; a submit replaces the step's fields,
removes an omitted optional key, injects the schema default of an omitted
top-level field, stores the options (moving the entry's ``modified_at``) and
reloads the entry.  Every call is refused outside a Core-authority window,
exactly as the real client refuses it, and every request outside the reviewed
routes fails the test.  Every value is synthetic.
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
from ha_mcp_engineering.governance import helper_options as contract  # noqa: E402

from tests.core_authority_fakes import require_core_authority  # noqa: E402
from tests.hamcp069b_fixtures import PROFILES_069B, exposure_entry  # noqa: E402
from tests.hamcp155_fixtures import (  # noqa: E402
    PROFILES_155,
    AdmittingDeleteCore,
    ConfigurationDeleteServiceTestCase,
)


OPEN_ROUTE = "/config/config_entries/options/flow"
STEP_PREFIX = "/config/config_entries/options/flow/"
PROFILES_232 = frozenset({contract.PROFILE})


def flow_form(flow_id: str, domain: str, step_id: str, visible: dict[str, Any],
              *, errors: dict[str, str] | None = None) -> dict[str, Any]:
    """The serialized form Core's flow view returns for one options step."""

    top: list[dict[str, Any]] = []
    section: list[dict[str, Any]] = []
    for spec in contract.step_fields(domain, step_id):
        field: dict[str, Any] = {"name": spec.leaf}
        field["required" if spec.required else "optional"] = True
        if spec.name in visible:
            field["description"] = {"suggested_value": deepcopy(visible[spec.name])}
        (section if spec.section else top).append(field)
    if section:
        top.append(
            {
                "name": contract.SECTION,
                "optional": True,
                "type": "expandable",
                "schema": section,
                "expanded": False,
            }
        )
    return {
        "type": "form",
        "flow_id": flow_id,
        "handler": "synthetic",
        "step_id": step_id,
        "data_schema": top,
        "errors": dict(errors or {}),
        "description_placeholders": None,
        "last_step": None,
        "preview": None,
    }


class SyntheticOptionsFlowRest:
    """The three options-flow routes and ``/states``, answered as Core does."""

    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        # entry_id -> {"step_id": str, "visible": flat dict}
        self.options: dict[str, dict[str, Any]] = {}
        # flow_id -> entry_id: the in-memory flows Core holds.
        self.flows: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, Any]] = []
        self.states: dict[str, str] = {}
        self._flow_counter = 0
        # Called with (entry_id, flow_id, ordinal) after an open is answered:
        # ``ordinal`` counts every open from 1 (drift injection).
        self.after_open: Any = None
        # When set, an abort raises this instead of discarding the flow.
        self.abort_failure: Exception | None = None
        # When set, a submit raises this (before or after applying, per
        # ``submit_failure_applies``) instead of answering.
        self.submit_failure: Exception | None = None
        self.submit_failure_applies = False
        # When set, a submit answers this response and stores nothing.
        self.submit_response: Any = None
        # The entry state after the reload that follows a stored submit.
        self.state_after_submit = "loaded"
        # When set, the open answers this response (the flow is still held).
        self.open_response: Any = None
        # When set, the form shows this step id instead of the stored one.
        self.form_step_override: str | None = None

    # -- store -------------------------------------------------------------

    def add_entry(
        self,
        entry_id: str,
        domain: str,
        step_id: str,
        visible: dict[str, Any],
        entity_ids: tuple[str, ...],
        *,
        title: str | None = None,
        modified_at: float = 1_700_000_000.0,
    ) -> None:
        self.options[entry_id] = {"step_id": step_id, "visible": deepcopy(visible)}
        self.websocket.config_entries[entry_id] = {
            "entry_id": entry_id,
            "domain": domain,
            "title": title or f"Synthetic {domain} {entry_id}",
            "source": "user",
            "state": "loaded",
            "supports_options": True,
            "supports_remove_device": False,
            "supports_unload": True,
            "supports_reconfigure": False,
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "disabled_by": None,
            "reason": None,
            "modified_at": modified_at,
        }
        for entity_id in entity_ids:
            self.websocket.entries[entity_id] = exposure_entry(
                entity_id, platform=domain, config_entry_id=entry_id
            )
            self.states[entity_id] = "1.0"

    def visible(self, entry_id: str) -> dict[str, Any]:
        return deepcopy(self.options[entry_id]["visible"])

    def modified_at(self, entry_id: str) -> float:
        return self.websocket.config_entries[entry_id]["modified_at"]

    def set_external(self, entry_id: str, **changes: Any) -> None:
        """A change made outside Engineering (another client's options save)."""

        self.options[entry_id]["visible"].update(deepcopy(changes))
        self.websocket.config_entries[entry_id]["modified_at"] += 1.0

    def touch(self, entry_id: str) -> None:
        """A data-only change: Core moves ``modified_at`` on any entry update."""

        self.websocket.config_entries[entry_id]["modified_at"] += 1.0

    # -- request log -------------------------------------------------------

    def opens(self) -> list[tuple[str, str]]:
        return [item for item in self.requests if item == ("POST", OPEN_ROUTE)]

    def aborts(self) -> list[tuple[str, str]]:
        return [item for item in self.requests if item[0] == "DELETE"]

    def submits(self) -> list[tuple[str, str]]:
        return [
            item
            for item in self.requests
            if item[0] == "POST" and item[1].startswith(STEP_PREFIX)
        ]

    def submitted_bodies(self) -> list[Any]:
        return [
            body
            for method, path, body in self.bodies
            if method == "POST" and path.startswith(STEP_PREFIX)
        ]

    # -- the reviewed routes -------------------------------------------------

    def _new_flow(self, entry_id: str) -> str:
        self._flow_counter += 1
        flow_id = f"{self._flow_counter:032x}"
        self.flows[flow_id] = entry_id
        return flow_id

    def _apply(self, entry_id: str, submit: dict[str, Any]) -> None:
        stored = self.options[entry_id]
        domain = self.websocket.config_entries[entry_id]["domain"]
        flat: dict[str, Any] = {}
        for name, value in submit.items():
            if name == contract.SECTION and isinstance(value, dict):
                for leaf, item in value.items():
                    flat[f"{contract.SECTION}.{leaf}"] = item
            else:
                flat[name] = value
        visible = dict(stored["visible"])
        for spec in contract.step_fields(domain, stored["step_id"]):
            if spec.name in flat:
                visible[spec.name] = deepcopy(flat[spec.name])
            elif spec.has_default and spec.section is None:
                # vol.Optional(default=...) fills the omitted key.
                visible[spec.name] = contract.core_default(spec)
            elif not spec.required:
                # ``_update_and_remove_omitted_optional_keys``.
                visible.pop(spec.name, None)
        stored["visible"] = visible
        entry = self.websocket.config_entries[entry_id]
        entry["modified_at"] += 1.0
        entry["state"] = self.state_after_submit

    async def request(
        self,
        method: str,
        path: Any,
        body: Any = None,
        raw: bool = False,
        expected_statuses: frozenset[int] = frozenset(),
        **_kwargs: Any,
    ) -> Any:
        path = str(path)
        require_core_authority(self, method, path)
        self.requests.append((method, path))
        self.bodies.append((method, path, deepcopy(body)))
        if method == "GET" and path.startswith("/states/"):
            entity_id = path.removeprefix("/states/")
            if entity_id in self.states:
                return {"entity_id": entity_id, "state": self.states[entity_id]}
            return ExpectedHttpStatus(404)
        if method == "POST" and path == OPEN_ROUTE:
            if set(body or {}) != {"handler"}:
                raise AssertionError(f"unreviewed options-flow open body: {body}")
            entry_id = body["handler"]
            entry = self.websocket.config_entries.get(entry_id)
            if entry is None or not entry["supports_options"]:
                raise HomeAssistantApiError(
                    details={"status": 404, "provider_response_received": True}
                )
            flow_id = self._new_flow(entry_id)
            if self.open_response is not None:
                response = deepcopy(self.open_response)
                if isinstance(response, dict) and "flow_id" not in response:
                    response["flow_id"] = flow_id
            else:
                stored = self.options[entry_id]
                response = flow_form(
                    flow_id,
                    entry["domain"],
                    self.form_step_override or stored["step_id"],
                    stored["visible"],
                )
            if self.after_open is not None:
                self.after_open(entry_id, flow_id, len(self.opens()))
            return response
        if path.startswith(STEP_PREFIX):
            flow_id = path.removeprefix(STEP_PREFIX)
            if method == "DELETE":
                if body is not None:
                    raise AssertionError("an options-flow abort carries no body")
                if self.abort_failure is not None:
                    raise self.abort_failure
                if self.flows.pop(flow_id, None) is None:
                    raise HomeAssistantApiError(
                        details={"status": 404, "provider_response_received": True}
                    )
                return {"message": "Flow aborted"}
            if method == "POST":
                if expected_statuses != frozenset({400, 404}):
                    raise AssertionError("the submit must expect 400/404 answers")
                entry_id = self.flows.get(flow_id)
                if entry_id is None:
                    return ExpectedHttpStatus(404)
                if self.submit_failure is not None:
                    if self.submit_failure_applies:
                        self.flows.pop(flow_id, None)
                        self._apply(entry_id, body)
                    raise self.submit_failure
                if self.submit_response is not None:
                    response = deepcopy(self.submit_response)
                    if isinstance(response, dict) and response.get("type") != "form":
                        self.flows.pop(flow_id, None)
                    return response
                self.flows.pop(flow_id, None)
                self._apply(entry_id, body)
                return {
                    "type": "create_entry",
                    "flow_id": flow_id,
                    "handler": entry_id,
                    "title": "",
                    "description": None,
                    "description_placeholders": None,
                    "result": True,
                }
        raise AssertionError(f"unreviewed REST request: {method} {path}")


class AdmittingHelperOptionsCore(AdmittingDeleteCore):
    """Admits (or withholds) the runtime-only options profile as well."""

    def __init__(self, admitted: Any = PROFILES_069B | PROFILES_155 | PROFILES_232) -> None:
        super().__init__(admitted)

    def withdraw(self, *profiles: str) -> None:
        self.admitted -= set(profiles or PROFILES_232)

    def _admits(self, requirements: Any) -> bool:
        gated = PROFILES_069B | PROFILES_155 | PROFILES_232
        return set(requirements) & gated <= self.admitted


FILTER_LOWPASS = {
    "entity_id": "sensor.raw_power",
    "filter": "lowpass",
    "window_size": 1,
    "time_constant": 10,
    "precision": 2,
}
STATISTICS_INIT = {
    "entity_id": "sensor.raw_temperature",
    "state_characteristic": "mean",
    "sampling_size": 20.0,
    "max_age": {"hours": 0, "minutes": 10, "seconds": 0},
    "keep_last_sample": False,
    "percentile": 50.0,
    "precision": 2.0,
}
TEMPLATE_SENSOR = {
    "state": "{{ states('sensor.raw_power') | float(0) * 2 }}",
    "unit_of_measurement": "W",
}
TEMPLATE_BINARY_SENSOR = {
    "state": "{{ is_state('binary_sensor.door', 'on') }}",
}


class HelperOptionsServiceTestCase(ConfigurationDeleteServiceTestCase):
    """Real service, real F3 runtime, real options gateway, synthetic Core."""

    core_admits_options = True

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.options_rest = SyntheticOptionsFlowRest(self.ws)
        # ``HelperOptionsResourceGateway`` captured the clients at
        # construction; bind the synthetic server there.
        self.resource_gateway.helper_options.rest_client = self.options_rest
        self.resource_gateway.helper_options.websocket_client = self.ws
        self.service.helper_options_gateway = self.resource_gateway.helper_options
        self.core = AdmittingHelperOptionsCore(
            PROFILES_069B
            | PROFILES_155
            | (PROFILES_232 if self.core_admits_options else frozenset())
        )
        await self.restart_runtime()

    async def restart_runtime(self) -> None:
        await super().restart_runtime()
        self.poll_sleeps = []

        async def no_sleep(seconds: float) -> None:
            self.poll_sleeps.append(seconds)

        if getattr(self, "options_rest", None) is not None:
            self.runtime.registry.adapter(contract.CAPABILITY_IDENTITY)._sleep = no_sleep

    def add_filter(self, entry_id: str = "filterentry01", **changes: Any) -> str:
        entity_id = "sensor.filtered_power"
        self.options_rest.add_entry(
            entry_id, "filter", "lowpass", {**FILTER_LOWPASS, **changes}, (entity_id,)
        )
        return entity_id

    def add_statistics(self, entry_id: str = "statsentry01", **changes: Any) -> str:
        entity_id = "sensor.mean_temperature"
        self.options_rest.add_entry(
            entry_id, "statistics", "init", {**STATISTICS_INIT, **changes}, (entity_id,)
        )
        return entity_id

    def add_template_sensor(
        self, entry_id: str = "templateentry01",
        entity_id: str = "sensor.double_power", **changes: Any
    ) -> str:
        self.options_rest.add_entry(
            entry_id, "template", "sensor", {**TEMPLATE_SENSOR, **changes}, (entity_id,)
        )
        return entity_id

    def add_template_binary_sensor(
        self, entry_id: str = "templateentry02", **changes: Any
    ) -> str:
        entity_id = "binary_sensor.door_open"
        self.options_rest.add_entry(
            entry_id, "template", "binary_sensor",
            {**TEMPLATE_BINARY_SENSOR, **changes}, (entity_id,),
        )
        return entity_id

    async def create_options(self, *changes: dict[str, Any], **arguments: Any) -> dict[str, Any]:
        arguments.setdefault("reason", "Synthetic tuning")
        return await self.service.create_helper_options_plan(
            {"changes": list(changes), **arguments}
        )

    async def approved_options(self, *changes: dict[str, Any]) -> dict[str, Any]:
        created = await self.create_options(*changes)
        await self.approve(created)
        return created

    def options_disclosure(self, created: dict[str, Any], index: int = 0) -> dict[str, Any]:
        operation = created["dry_run_results"]["operations"][index]
        return operation["dry_run_results"][contract.INTENT_KEY]

    def ha_calls(self) -> int:
        return len(self.options_rest.requests) + len(self.ws.commands)

    def assert_never_sent(self) -> None:
        """Only the reviewed routes and read commands ever reached Core."""

        for method, path in self.options_rest.requests:
            self.assertTrue(
                (method, path) == ("POST", OPEN_ROUTE)
                or (method in {"POST", "DELETE"} and path.startswith(STEP_PREFIX))
                or (method == "GET" and path.startswith("/states/")),
                (method, path),
            )
        self.assertLessEqual(
            {str(c.get("type")) for c in self.ws.commands}, set(contract.READ_COMMANDS)
        )
        self.assertEqual(self.config_rest.writes(), [])


__all__ = [
    "AdmittingHelperOptionsCore",
    "FILTER_LOWPASS",
    "HelperOptionsServiceTestCase",
    "OPEN_ROUTE",
    "PROFILES_232",
    "STATISTICS_INIT",
    "STEP_PREFIX",
    "SyntheticOptionsFlowRest",
    "TEMPLATE_BINARY_SENSOR",
    "TEMPLATE_SENSOR",
    "flow_form",
]
