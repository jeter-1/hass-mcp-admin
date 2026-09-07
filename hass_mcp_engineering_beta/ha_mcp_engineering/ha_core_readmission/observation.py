"""Bounded Core REST/WebSocket observation collection.

The source protocol is deliberately narrow so the later shared coordinator can
wire the existing authenticated clients without making this package a second
runtime coordinator.  Raw Home Assistant responses are validated and discarded;
only bounded version, probe, and fingerprint evidence survives.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    CORE_IDENTITY,
    CORE_PROTOCOL,
    MAX_CAPABILITIES,
    MAX_IDENTIFIER_CHARS,
    MAX_REQUIRED_CHECKS,
    CoreCapabilityEvidence,
    CoreObservation,
    CoreReadmissionError,
    fingerprint,
)


class CoreSnapshotSource(Protocol):
    """Capture one authenticated REST/WS observation without mutating Core."""

    def capture_core_snapshot(self) -> Awaitable[Mapping[str, Any]]:
        """Return one bounded snapshot from the configured Core authority."""


@dataclass(frozen=True)
class _Snapshot:
    identity: str
    rest_version: str
    websocket_auth_version: str
    websocket_config_version: str
    session_fingerprint: str
    capabilities: tuple[CoreCapabilityEvidence, ...]
    connected: bool
    authenticated: bool

    @property
    def version(self) -> str:
        return self.rest_version


def _strict_mapping(
    value: Any,
    fields: set[str],
    code: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CoreReadmissionError(code)
    return value


def _text(value: Any, code: str, *, maximum: int = MAX_IDENTIFIER_CHARS) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CoreReadmissionError(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if type(value) is not bool:
        raise CoreReadmissionError(code)
    return value


def _sequence(value: Any, code: str, *, maximum: int) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CoreReadmissionError(code)
    if len(value) > maximum:
        raise CoreReadmissionError(code)
    return value


def _capability_evidence(value: Any) -> tuple[CoreCapabilityEvidence, ...]:
    items = _sequence(value, "core_capability_evidence_invalid", maximum=MAX_CAPABILITIES)
    output: list[CoreCapabilityEvidence] = []
    for item in items:
        raw = _strict_mapping(
            item,
            {
                "capability_id",
                "passed_checks",
                "semantic_fingerprint",
            },
            "core_capability_evidence_entry_invalid",
        )
        checks = tuple(
            _text(
                check,
                "core_capability_check_invalid",
                maximum=96,
            )
            for check in _sequence(
                raw["passed_checks"],
                "core_capability_checks_invalid",
                maximum=MAX_REQUIRED_CHECKS,
            )
        )
        output.append(
            CoreCapabilityEvidence(
                capability_id=_text(
                    raw["capability_id"],
                    "core_capability_id_invalid",
                ),
                passed_checks=checks,
                semantic_fingerprint=(
                    None
                    if raw["semantic_fingerprint"] is None
                    else _text(
                        raw["semantic_fingerprint"],
                        "core_semantic_fingerprint_invalid",
                        maximum=71,
                    )
                ),
            )
        )
    return tuple(output)


def _parse_snapshot(value: Mapping[str, Any]) -> _Snapshot:
    raw = _strict_mapping(
        value,
        {
            "identity",
            "connected",
            "authenticated",
            "session_id",
            "rest_config",
            "websocket_auth_ok",
            "websocket_get_config",
            "capability_evidence",
        },
        "core_snapshot_fields_invalid",
    )
    rest = _strict_mapping(
        raw["rest_config"],
        {"version"},
        "core_rest_config_invalid",
    )
    auth = _strict_mapping(
        raw["websocket_auth_ok"],
        {"type", "ha_version"},
        "core_websocket_auth_invalid",
    )
    config = _strict_mapping(
        raw["websocket_get_config"],
        {"version"},
        "core_websocket_config_invalid",
    )
    if auth["type"] != "auth_ok":
        raise CoreReadmissionError("core_websocket_auth_invalid")
    session_id = _text(raw["session_id"], "core_session_invalid")
    return _Snapshot(
        identity=_text(raw["identity"], "core_identity_invalid"),
        rest_version=_text(rest["version"], "core_rest_version_invalid", maximum=64),
        websocket_auth_version=_text(
            auth["ha_version"],
            "core_websocket_auth_version_invalid",
            maximum=64,
        ),
        websocket_config_version=_text(
            config["version"],
            "core_websocket_config_version_invalid",
            maximum=64,
        ),
        session_fingerprint=fingerprint(
            {
                "model": "ha-core-authenticated-session-v1",
                "session_id": session_id,
            }
        ),
        capabilities=_capability_evidence(raw["capability_evidence"]),
        connected=_boolean(raw["connected"], "core_connected_invalid"),
        authenticated=_boolean(
            raw["authenticated"],
            "core_authenticated_invalid",
        ),
    )


def _failure_observation(reason_code: str) -> CoreObservation:
    """Produce sanitized fail-closed evidence without retaining malformed data."""

    return CoreObservation(
        identity=CORE_IDENTITY,
        version=None,
        protocol=CORE_PROTOCOL,
        rest_version=None,
        websocket_auth_version=None,
        websocket_config_version=None,
        session_fingerprint=fingerprint(
            {"model": "ha-core-unavailable-session-v1", "reason": reason_code}
        ),
        capability_evidence=(),
        connected=False,
        authenticated=False,
        complete=False,
        stable=False,
        reason_code=reason_code,
    )


def _merge_capabilities(
    first: tuple[CoreCapabilityEvidence, ...],
    second: tuple[CoreCapabilityEvidence, ...],
) -> tuple[CoreCapabilityEvidence, ...]:
    first_by_id: dict[str, list[CoreCapabilityEvidence]] = {}
    second_by_id: dict[str, list[CoreCapabilityEvidence]] = {}
    for item in first:
        first_by_id.setdefault(item.capability_id, []).append(item)
    for item in second:
        second_by_id.setdefault(item.capability_id, []).append(item)
    result: list[CoreCapabilityEvidence] = []
    for capability_id in sorted(set(first_by_id) | set(second_by_id)):
        left = first_by_id.get(capability_id, [])
        right = second_by_id.get(capability_id, [])
        duplicate = len(left) != 1 or len(right) != 1
        candidate = left[0] if left else right[0]
        stable = not duplicate and left[0].to_mapping() == right[0].to_mapping()
        result.append(
            CoreCapabilityEvidence(
                capability_id=capability_id,
                passed_checks=candidate.passed_checks,
                stable=stable,
                semantic_fingerprint=candidate.semantic_fingerprint,
            )
        )
    return tuple(result)


def stable_observation(
    first_value: Mapping[str, Any],
    second_value: Mapping[str, Any],
) -> CoreObservation:
    """Validate two consecutive observations and retain no raw response data."""

    try:
        first = _parse_snapshot(first_value)
        second = _parse_snapshot(second_value)
    except CoreReadmissionError:
        return _failure_observation("malformed_core_evidence")

    versions = {
        first.rest_version,
        first.websocket_auth_version,
        first.websocket_config_version,
    }
    if len(versions) != 1:
        reason = "core_version_disagreement"
        stable = False
    elif first.identity != second.identity:
        reason = "core_identity_changed"
        stable = False
    elif first.session_fingerprint != second.session_fingerprint:
        reason = "core_session_changed"
        stable = False
    elif (
        first.rest_version != second.rest_version
        or first.websocket_auth_version != second.websocket_auth_version
        or first.websocket_config_version != second.websocket_config_version
    ):
        reason = "core_observation_unstable"
        stable = False
    elif not first.connected or not second.connected:
        reason = "transport_unavailable"
        stable = False
    elif not first.authenticated or not second.authenticated:
        reason = "authentication_failed"
        stable = False
    else:
        reason = "observation_complete"
        stable = True

    capabilities = _merge_capabilities(first.capabilities, second.capabilities)
    complete = bool(capabilities) and stable
    try:
        return CoreObservation(
            identity=first.identity,
            version=first.version,
            protocol=CORE_PROTOCOL,
            rest_version=first.rest_version,
            websocket_auth_version=first.websocket_auth_version,
            websocket_config_version=first.websocket_config_version,
            session_fingerprint=first.session_fingerprint,
            capability_evidence=capabilities,
            connected=first.connected and second.connected,
            authenticated=first.authenticated and second.authenticated,
            complete=complete,
            stable=stable,
            reason_code=reason,
        )
    except CoreReadmissionError:
        return _failure_observation("malformed_core_evidence")


class CoreObservationCollector:
    """Collect two bounded snapshots so one unstable response cannot admit."""

    def __init__(self, source: CoreSnapshotSource):
        self._source = source

    async def collect(self) -> CoreObservation:
        try:
            first = await self._source.capture_core_snapshot()
            second = await self._source.capture_core_snapshot()
        except Exception:
            # Provider exception text is deliberately not retained.
            return _failure_observation("core_observation_unavailable")
        return stable_observation(first, second)


__all__ = [
    "CoreObservationCollector",
    "CoreSnapshotSource",
    "stable_observation",
]
