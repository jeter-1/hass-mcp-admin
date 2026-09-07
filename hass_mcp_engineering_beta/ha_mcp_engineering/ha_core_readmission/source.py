"""Production, read-only Home Assistant Core identity and contract probes."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import aiohttp

from ..configuration import Settings
from .device_registry import assess_device_registry
from .models import CORE_IDENTITY
from .profiles import CORE_CAPABILITY_PROFILES


MAX_CORE_PROBE_BYTES = 4_000_000
MAX_CORE_PROBE_ITEMS = 20_000
CORE_2026_9_VERSIONS = frozenset({"2026.9.0", "2026.9.1"})
AUTOMATION_CONTRACT_PROBE_ENTITY_ID = (
    "automation.ha_mcp_engineering_contract_probe_0000000000000000"
)
TRACE_CONTRACT_PROBE_ITEM_ID = "ha_mcp_engineering_contract_probe_0000000000000000"
TRACE_CONTRACT_PROBE_RUN_ID = "00000000000000000000000000000000"
_EXPECTED_PROBE_ERROR_FIELD = "_ha_mcp_engineering_expected_probe_error"
_AUTOMATION_NOT_FOUND_ERRORS = (("not_found", "Entity not found"),)
_TRACE_NOT_FOUND_ERRORS = (
    ("not_found", "The trace could not be found"),
)
_DASHBOARD_NOT_FOUND_ERRORS = (
    ("config_not_found", "Unknown config specified: None"),
    ("config_not_found", "No config found."),
)


def _profile(capability_id: str):
    return next(
        item
        for item in CORE_CAPABILITY_PROFILES
        if item.capability_id == capability_id
    )


def _bounded_sequence(value: Any) -> bool:
    return (
        not isinstance(value, (str, bytes))
        and isinstance(value, Sequence)
        and len(value) <= MAX_CORE_PROBE_ITEMS
    )


def _bounded_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and len(value) <= MAX_CORE_PROBE_ITEMS


def _registry_sequence(value: Any, identity_field: str) -> bool:
    if not _bounded_sequence(value):
        return False
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return False
        identity = item.get(identity_field)
        if (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 256
            or identity in seen
        ):
            return False
        seen.add(identity)
    return True


def capability_evidence_for_probes(
    *,
    version: str,
    rest_config: Any,
    states: Any,
    services: Any,
    websocket_config: Any,
    websocket_results: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project transient raw probes into bounded binary-owned check evidence."""

    state_shape = _registry_sequence(states, "entity_id") and all(
        isinstance(item.get("state"), str)
        and isinstance(item.get("attributes"), Mapping)
        for item in states
    )
    service_shape = _registry_sequence(services, "domain") and all(
        isinstance(item.get("services"), Mapping) for item in services
    )
    rest_ok = _bounded_mapping(rest_config) and rest_config.get("version") == version
    websocket_ok = (
        _bounded_mapping(websocket_config)
        and websocket_config.get("version") == version
    )
    evidence: list[dict[str, Any]] = []

    def add(capability_id: str, passed: bool, *, semantic: bool = False) -> None:
        if not passed:
            return
        profile = _profile(capability_id)
        evidence.append(
            {
                "capability_id": capability_id,
                "passed_checks": list(profile.required_checks),
                "semantic_fingerprint": (
                    profile.contract_fingerprint if semantic else None
                ),
            }
        )

    add("core.basic_rest_read", rest_ok and state_shape)
    add("core.basic_websocket_read", websocket_ok)
    add("core.state_service_discovery", state_shape and service_shape)
    add(
        "core.non_device_registry_read",
        _registry_sequence(websocket_results.get("areas"), "area_id")
        and _registry_sequence(websocket_results.get("floors"), "floor_id")
        and _registry_sequence(websocket_results.get("labels"), "label_id")
        and _registry_sequence(
            websocket_results.get("entities"), "entity_id"
        ),
    )
    device_records = websocket_results.get("devices")
    device_assessment = assess_device_registry(device_records)
    legacy_device_shape = _bounded_sequence(device_records) and all(
        isinstance(item, Mapping) for item in device_records
    )
    device_contract_complete = (
        device_assessment.complete
        if version in CORE_2026_9_VERSIONS
        else legacy_device_shape
    )
    add(
        "core.direct_device_registry_read",
        device_contract_complete,
        semantic=True,
    )
    # This is Core-side device evidence only.  For Core 2026.9, the delegated
    # gateway independently requires a binary-owned ha-mcp adapter that
    # implements child-device and effective-area semantics.
    add(
        "core.delegated_device_effective_area",
        device_contract_complete,
        semantic=True,
    )
    add("core.direct_entity_state_read", state_shape)
    add(
        "core.automation_configuration_read",
        _bounded_mapping(websocket_results.get("automation")),
    )
    trace_list = websocket_results.get("trace_list")
    trace_get = websocket_results.get("trace_get")
    add(
        "core.automation_trace_read",
        _bounded_sequence(trace_list)
        and all(_bounded_mapping(item) for item in trace_list)
        and trace_get
        == {_EXPECTED_PROBE_ERROR_FIELD: "not_found"},
        semantic=True,
    )
    add(
        "core.dashboard_configuration_read",
        _registry_sequence(
            websocket_results.get("dashboards"), "url_path"
        )
        and _bounded_mapping(websocket_results.get("dashboard")),
    )
    add("core.governance_observability", True)

    # These exact profiles are released only for the compiled source versions
    # whose semantic and disposable-runtime contracts were reviewed.  The
    # coordinator still requires the exact binary-owned semantic fingerprint;
    # an unknown version or a successful identity probe cannot select it.
    if version in {
        "2026.7.2",
        "2026.8.0",
        "2026.8.1",
        *CORE_2026_9_VERSIONS,
    }:
        add("core.template_semantics", websocket_ok, semantic=True)
        add("core.configuration_validation", rest_ok, semantic=True)
        add(
            "core.dependency_helper_planning",
            rest_ok and websocket_ok and device_contract_complete,
            semantic=True,
        )
        add("core.typed_helper_operation", state_shape, semantic=True)
        add("core.f3_mutation_verification", rest_ok and websocket_ok, semantic=True)
        add(
            "core.governed_configuration_operation",
            rest_ok and websocket_ok,
            semantic=True,
        )
    return evidence


class AiohttpCoreSnapshotSource:
    """Capture bounded REST and authenticated WebSocket observations only."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._observer_session_id = uuid.uuid4().hex

    def mark_connection_changed(self) -> None:
        """Retire the observer-session binding after a known connection event."""

        self._observer_session_id = uuid.uuid4().hex

    async def wait_for_connection_change(self, expected_version: str) -> None:
        """Wait for the authenticated Core lifecycle socket to move or close."""

        if (
            not isinstance(expected_version, str)
            or not expected_version
            or len(expected_version) > 64
        ):
            return
        request_timeout = self._settings.ha_timeout_seconds
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=request_timeout,
        )
        websocket_timeout = aiohttp.ClientWSTimeout(
            ws_receive=None,
            ws_close=request_timeout,
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    self._settings.websocket_url,
                    timeout=websocket_timeout,
                    max_msg_size=MAX_CORE_PROBE_BYTES,
                ) as websocket:
                    required = await asyncio.wait_for(
                        self._receive_mapping(websocket),
                        timeout=request_timeout,
                    )
                    if required.get("type") != "auth_required":
                        return
                    await websocket.send_json(
                        {
                            "type": "auth",
                            "access_token": self._settings.ha_token,
                        }
                    )
                    auth = await asyncio.wait_for(
                        self._receive_mapping(websocket),
                        timeout=request_timeout,
                    )
                    if (
                        auth.get("type") != "auth_ok"
                        or auth.get("ha_version") != expected_version
                    ):
                        return
                    # This connection subscribes to nothing and issues no HA
                    # command. Any application frame, close, or transport
                    # failure means the verified lifecycle binding moved.
                    await websocket.receive()
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _rest_json(
        self,
        session: aiohttp.ClientSession,
        path: str,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._settings.ha_token}",
            "Content-Type": "application/json",
        }
        async with session.get(
            f"{self._settings.api_url}{path}", headers=headers
        ) as response:
            if response.status != 200:
                raise RuntimeError("core_probe_http_failure")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await response.content.read(
                    min(65_536, MAX_CORE_PROBE_BYTES + 1 - total)
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_CORE_PROBE_BYTES:
                    raise RuntimeError("core_probe_response_oversized")
            body = b"".join(chunks)
            value = json.loads(body)
            if not isinstance(value, (Mapping, Sequence)) or isinstance(
                value, (str, bytes)
            ):
                raise RuntimeError("core_probe_response_invalid")
            return value

    @staticmethod
    async def _receive_mapping(websocket: Any) -> Mapping[str, Any]:
        message = await websocket.receive_json()
        if not isinstance(message, Mapping):
            raise RuntimeError("core_probe_websocket_invalid")
        return message

    async def _command(
        self,
        websocket: Any,
        command_id: int,
        command_type: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        expected_errors: tuple[tuple[str, str], ...] = (),
    ) -> Any:
        await websocket.send_json(
            {
                "id": command_id,
                "type": command_type,
                **dict(arguments or {}),
            }
        )
        # This fresh, unsubscribed probe session has exactly one request in
        # flight.  An unrelated or duplicate frame is therefore malformed
        # evidence, not something to skip in an unbounded receive loop.
        message = await self._receive_mapping(websocket)
        if message.get("id") != command_id or message.get("type") != "result":
            raise RuntimeError("core_probe_command_failed")
        if message.get("success") is False:
            error = message.get("error")
            if (
                expected_errors
                and set(message) == {"id", "type", "success", "error"}
                and _bounded_mapping(error)
                and set(error) == {"code", "message"}
                and (error.get("code"), error.get("message")) in expected_errors
            ):
                json.dumps(error, allow_nan=False)
                return {_EXPECTED_PROBE_ERROR_FIELD: error["code"]}
            raise RuntimeError("core_probe_command_failed")
        if message.get("success") is not True:
            raise RuntimeError("core_probe_command_failed")
        result = message.get("result")
        json.dumps(result, allow_nan=False)
        return result

    async def capture_core_snapshot(self) -> Mapping[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self._settings.ha_timeout_seconds)
        websocket_timeout = aiohttp.ClientWSTimeout(
            ws_receive=self._settings.ha_timeout_seconds,
            ws_close=self._settings.ha_timeout_seconds,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            rest_config = await self._rest_json(session, "/config")
            states = await self._rest_json(session, "/states")
            try:
                services = await self._rest_json(session, "/services")
            except (RuntimeError, ValueError):
                # Service discovery is an independent read surface. A failed,
                # malformed, or oversized service inventory withholds only its
                # capability and cannot erase already validated REST evidence.
                services = None
            async with session.ws_connect(
                self._settings.websocket_url,
                timeout=websocket_timeout,
                max_msg_size=MAX_CORE_PROBE_BYTES,
            ) as websocket:
                required = await self._receive_mapping(websocket)
                if required.get("type") != "auth_required":
                    raise RuntimeError("core_probe_auth_protocol_invalid")
                await websocket.send_json(
                    {
                        "type": "auth",
                        "access_token": self._settings.ha_token,
                    }
                )
                auth = await self._receive_mapping(websocket)
                if auth.get("type") != "auth_ok":
                    raise RuntimeError("core_probe_authentication_failed")
                websocket_config = await self._command(websocket, 1, "get_config")
                commands = (
                    (2, "areas", "config/area_registry/list"),
                    (3, "floors", "config/floor_registry/list"),
                    (4, "labels", "config/label_registry/list"),
                    (5, "entities", "config/entity_registry/list"),
                    (6, "devices", "config/device_registry/list"),
                    (7, "dashboards", "lovelace/dashboards/list"),
                )
                results: dict[str, Any] = {}
                for command_id, name, command_type in commands:
                    try:
                        results[name] = await self._command(
                            websocket, command_id, command_type
                        )
                    except RuntimeError:
                        results[name] = None
                try:
                    # Core 2026.7.2 through 2026.9.1 use these exact
                    # config_not_found envelopes when the default Lovelace
                    # dashboard exists without stored configuration or no
                    # default dashboard is configured.  That is affirmative
                    # endpoint-contract evidence, not a malformed response.
                    results["dashboard"] = await self._command(
                        websocket,
                        8,
                        "lovelace/config",
                        expected_errors=_DASHBOARD_NOT_FOUND_ERRORS,
                    )
                except RuntimeError:
                    results["dashboard"] = None
                automation_entity_id = next(
                    (
                        item.get("entity_id")
                        for item in states
                        if isinstance(item, Mapping)
                        and isinstance(item.get("entity_id"), str)
                        and item["entity_id"].startswith("automation.")
                    ),
                    AUTOMATION_CONTRACT_PROBE_ENTITY_ID,
                )
                try:
                    # Absence of a sample automation must not be confused
                    # with absence of the automation/config contract.  The
                    # fixed missing entity produces the exact source-reviewed
                    # not_found envelope on every supported Core release.
                    results["automation"] = await self._command(
                        websocket,
                        9,
                        "automation/config",
                        {"entity_id": automation_entity_id},
                        expected_errors=_AUTOMATION_NOT_FOUND_ERRORS,
                    )
                except RuntimeError:
                    results["automation"] = None
                try:
                    results["trace_list"] = await self._command(
                        websocket,
                        10,
                        "trace/list",
                        {
                            "domain": "automation",
                            "item_id": TRACE_CONTRACT_PROBE_ITEM_ID,
                        },
                    )
                except (RuntimeError, ValueError):
                    results["trace_list"] = None
                try:
                    # Exact Core 2026.7.2 through 2026.9.1 return this bounded
                    # not_found envelope for a missing trace. It proves the
                    # independently reviewed trace/get command contract without
                    # requiring a real automation run or causing a mutation.
                    results["trace_get"] = await self._command(
                        websocket,
                        11,
                        "trace/get",
                        {
                            "domain": "automation",
                            "item_id": TRACE_CONTRACT_PROBE_ITEM_ID,
                            "run_id": TRACE_CONTRACT_PROBE_RUN_ID,
                        },
                        expected_errors=_TRACE_NOT_FOUND_ERRORS,
                    )
                except (RuntimeError, ValueError):
                    results["trace_get"] = None

        version = rest_config.get("version") if isinstance(rest_config, Mapping) else None
        if not isinstance(version, str) or not isinstance(websocket_config, Mapping):
            raise RuntimeError("core_probe_version_invalid")
        auth_version = auth.get("ha_version")
        if not isinstance(auth_version, str):
            raise RuntimeError("core_probe_version_invalid")
        evidence = capability_evidence_for_probes(
            version=version,
            rest_config=rest_config,
            states=states,
            services=services,
            websocket_config=websocket_config,
            websocket_results=results,
        )
        return {
            "identity": CORE_IDENTITY,
            "connected": True,
            "authenticated": True,
            "session_id": self._observer_session_id,
            "rest_config": {"version": version},
            "websocket_auth_ok": {
                "type": "auth_ok",
                "ha_version": auth_version,
            },
            "websocket_get_config": {"version": websocket_config.get("version")},
            "capability_evidence": evidence,
        }


__all__ = [
    "AiohttpCoreSnapshotSource",
    "MAX_CORE_PROBE_BYTES",
    "capability_evidence_for_probes",
]
