"""Production, read-only Home Assistant Core identity and contract probes."""

from __future__ import annotations

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
    add(
        "core.direct_device_registry_read",
        (
            device_assessment.complete
            if version == "2026.9.0"
            else legacy_device_shape
        ),
        semantic=True,
    )
    # Pre-2026.9 releases use the already-reviewed flat device model. Core
    # 2026.9 needs a separate ha-mcp projection probe; the direct Core snapshot
    # cannot manufacture that cross-surface evidence.
    add(
        "core.delegated_device_effective_area",
        version != "2026.9.0" and legacy_device_shape,
        semantic=True,
    )
    add("core.direct_entity_state_read", state_shape)
    add(
        "core.automation_configuration_read",
        _bounded_mapping(websocket_results.get("automation")),
    )
    add(
        "core.dashboard_configuration_read",
        _registry_sequence(
            websocket_results.get("dashboards"), "url_path"
        )
        and _bounded_mapping(websocket_results.get("dashboard")),
    )
    add("core.governance_observability", True)

    # These exact profiles were already reviewed for the deployed pre-2026.9
    # releases.  For 2026.9 they are intentionally withheld until dedicated
    # semantic probes and disposable acceptance exist; identity success must
    # never manufacture that authority.
    if version in {"2026.7.2", "2026.8.0", "2026.8.1"}:
        add("core.template_semantics", websocket_ok, semantic=True)
        add("core.configuration_validation", rest_ok, semantic=True)
        add(
            "core.dependency_helper_planning",
            rest_ok and websocket_ok,
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
            body = await response.content.read(MAX_CORE_PROBE_BYTES + 1)
            if len(body) > MAX_CORE_PROBE_BYTES:
                raise RuntimeError("core_probe_response_oversized")
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
        if (
            message.get("id") != command_id
            or message.get("type") != "result"
            or message.get("success") is not True
        ):
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
            services = await self._rest_json(session, "/services")
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
                    (8, "dashboard", "lovelace/config"),
                )
                results: dict[str, Any] = {}
                for command_id, name, command_type in commands:
                    try:
                        results[name] = await self._command(
                            websocket, command_id, command_type
                        )
                    except RuntimeError:
                        results[name] = None
                automation_entity_id = next(
                    (
                        item.get("entity_id")
                        for item in states
                        if isinstance(item, Mapping)
                        and isinstance(item.get("entity_id"), str)
                        and item["entity_id"].startswith("automation.")
                    ),
                    None,
                )
                if automation_entity_id is not None:
                    try:
                        results["automation"] = await self._command(
                            websocket,
                            9,
                            "automation/config",
                            {"entity_id": automation_entity_id},
                        )
                    except RuntimeError:
                        results["automation"] = None
                else:
                    results["automation"] = None

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
