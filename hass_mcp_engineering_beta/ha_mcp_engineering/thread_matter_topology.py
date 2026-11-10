"""Bounded Thread dataset and Matter topology read (HAMCP-231, A1 + A2).

``get_thread_matter_topology`` is an Engineering-native read under the
runtime-only profile ``core.thread_matter_topology_read``.  Until an
owner-signed registry admits it the gateway refuses
``pending_signed_registry_admission`` before the application, and this
service independently refuses ``authority_unavailable`` with zero Home
Assistant commands.

Closed vocabulary: exactly two constant payloads, never an argument
(docs/HAMCP_231_THREAD_MATTER_TOPOLOGY_INVESTIGATION.md section 5.1).

* A1 ``{"type": "thread/list_datasets"}`` (Core 2026.11.0 ``74cf5a49``
  ``components/thread/websocket_api.py:152-182``).
* A2 ``{"type": "matter/network_topology", "refresh": false}``
  (``components/matter/api.py:384-404``).  ``refresh`` is the literal
  ``False``; no input reaches it.

Both are sent on one authenticated connection, A1 first, only for the
requested sections.  Each section has its own typed status (section 5.1.5); a
failed section never hides the other, and nothing falls back.

Identifier policy ``opaque_per_response_v1`` (section 5.1.2): every LAN
identifier class is dropped or rewritten to a per-response counter reference
(``g<k>`` graph ids, ``x<k>`` extended addresses with no graph node,
``b<k>`` border agent ids) before any value reaches output, audit, logs or
error text.  Kept network-level values are ``ext_pan_id``/``extended_pan_id``,
``pan_id``, ``channel``, the Thread network name, ``rloc16``, ``node_id`` and
``ha_device_id``.  A key matching ``tlv``, ``network_key``, ``pskc`` or
``pskd`` anywhere in a section refuses the whole section.  Upstream error
text is never read.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping

import aiohttp

from .errors import ErrorCode, GovernanceError
from .ha_core_readmission.routes import THREAD_MATTER_TOPOLOGY_PROFILE
from .observability import METRICS
from .request_context import current_caller_id, current_telemetry
from .sanitization import SanitizationResult, redaction_summary, sanitize_untrusted_data

PROFILE = THREAD_MATTER_TOPOLOGY_PROFILE
CORE_REQUIREMENTS = (PROFILE,)
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PROVIDER = "direct_home_assistant_thread_matter"
PROVIDER_CONTRACT = "compiled-core-thread-matter-topology-read-v1"
IDENTIFIER_POLICY = "opaque_per_response_v1"

DATASETS = "datasets"
TOPOLOGY = "topology"
SECTIONS = (DATASETS, TOPOLOGY)
LIST_DATASETS_COMMAND = "thread/list_datasets"
NETWORK_TOPOLOGY_COMMAND = "matter/network_topology"
DATASETS_PAYLOAD: Mapping[str, Any] = MappingProxyType({"type": LIST_DATASETS_COMMAND})
TOPOLOGY_PAYLOAD: Mapping[str, Any] = MappingProxyType({"type": NETWORK_TOPOLOGY_COMMAND, "refresh": False})
PAYLOADS: Mapping[str, Mapping[str, Any]] = MappingProxyType({DATASETS: DATASETS_PAYLOAD, TOPOLOGY: TOPOLOGY_PAYLOAD})
COMMANDS = frozenset({LIST_DATASETS_COMMAND, NETWORK_TOPOLOGY_COMMAND})
UPSTREAM_ACTIVITY: Mapping[str, tuple[str, ...]] = MappingProxyType({
    DATASETS: ("cached_graph",),
    TOPOLOGY: ("cached_graph", "may_start_br_discovery"),
})

# Every other ``thread/*``, ``otbr/*`` and ``matter/*`` command Core 2026.11.0
# (74cf5a49) registers (components/thread/websocket_api.py,
# components/otbr/websocket_api.py, components/matter/api.py).  None of these
# strings is ever built: the module sends only the two constant payloads.
NEVER_SENT_COMMANDS = (
    "matter/commission",
    "matter/commission_on_network",
    "matter/interview_node",
    "matter/node_diagnostics",
    "matter/open_commissioning_window",
    "matter/ping_node",
    "matter/remove_matter_fabric",
    "matter/set_thread",
    "matter/set_wifi_credentials",
    "matter/subscribe_network_topology",
    "otbr/create_network",
    "otbr/info",
    "otbr/set_channel",
    "otbr/set_network",
    "thread/add_dataset_tlv",
    "thread/delete_dataset",
    "thread/discover_routers",
    "thread/get_dataset_tlv",
    "thread/set_preferred_border_agent",
    "thread/set_preferred_dataset",
)

# Acquisition bounds, checked before parsing.
FRAME_BYTES = 8_388_608
AUTH_BYTES = 65_536
MAX_COMMANDS = 2
MAX_FRAMES = 2 + MAX_COMMANDS  # auth_required, auth_ok, one result per command
MAX_SECONDS = 30.0
MAX_UPSTREAM_DATASETS = 256
MAX_UPSTREAM_NODES = 4_096
MAX_UPSTREAM_CONNECTIONS = 16_384
MAX_DEPTH = 16

# Output caps (section 5.1, HAMCP-129 style truncation with totals).
MAX_DATASETS = 16
MAX_NODES = 200
MAX_CONNECTIONS = 800
NAME_CHARS = 64
LABEL_CHARS = 100

ARGUMENT_KEYS = frozenset({"sections"})
SECRET_KEY_MARKERS = ("tlv", "networkkey", "pskc", "pskd")
NON_HA_KINDS = frozenset({"border_router", "thread_unknown", "wifi_ap"})
IDENTITY_VALUES = ("ha_device", "not_an_ha_device", "ha_registry_match_missing", "unknown")
# Upstream graph ids embed the identifier after these prefixes
# (matterjs-server network topology: ``br_<XA>``, ``unknown_<EXTADDR>``,
# ``ap_<BSSID>``).
GRAPH_ID_PREFIXES = ("br_", "unknown_", "ap_", "matter_", "node_")
MIN_IDENTIFIER_SCREEN = 4

SECTION_REASONS = frozenset({
    "thread_not_loaded",
    "matter_not_loaded",
    "matter_server_schema_below_13",
    "core_authority_refused",
    "malformed_upstream_response",
    "upstream_response_too_large",
    "upstream_command_failed",
    "transport_unavailable",
    "timeout",
})

_VOCABULARY = re.compile(r"[a-z][a-z0-9_]{0,31}\Z", re.ASCII)
_HEX16 = re.compile(r"[0-9a-f]{16}\Z", re.ASCII)
_HEX4 = re.compile(r"[0-9a-f]{1,4}\Z", re.ASCII)
_DATASET_ID = re.compile(r"[0-9A-Za-z]{26}\Z", re.ASCII)
_HA_DEVICE_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_ISO_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})\Z",
                       re.ASCII)
_SECRET_HEX = re.compile(r"[0-9A-Fa-f]{32,}", re.ASCII)
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]", re.ASCII)
MAX_EPOCH_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z
MAX_NODE_ID = 2**64 - 1


class TopologyError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    CODES = MappingProxyType({
        "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "invalid_arguments": ErrorCode.INVALID_REQUEST,
        "core_authority_refused": ErrorCode.AUTHORIZATION_FAILURE,
        "identity_mismatch": ErrorCode.HA_UNAVAILABLE,
        "timeout": ErrorCode.HA_TIMEOUT,
        "transport_unavailable": ErrorCode.HA_UNAVAILABLE,
        "malformed_upstream_response": ErrorCode.HA_API_ERROR,
        "upstream_response_too_large": ErrorCode.HA_API_ERROR,
        "provider_error": ErrorCode.HA_API_ERROR,
    })
    REASONS = frozenset(CODES)

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "provider_error"
        details: dict[str, Any] = {"reason": reason}
        if reason == "authority_unavailable":
            details["core_capability"] = PROFILE
        super().__init__(self.CODES[reason], details=details)
        self.reason = reason


class SectionRefused(Exception):
    """A section-local typed status; never carries upstream text."""

    def __init__(self, reason: str):
        if reason not in SECTION_REASONS:
            reason = "malformed_upstream_response"
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TopologyRequest:
    sections: tuple[str, ...] = SECTIONS


def validate_arguments(arguments: Any) -> TopologyRequest:
    """Exact keys, closed values; a refused value is never reflected.

    There is no ``refresh`` and no ``device_id`` input: any key other than
    ``sections`` is refused.
    """

    if type(arguments) is not dict or not set(arguments) <= ARGUMENT_KEYS:
        raise TopologyError("invalid_arguments")
    sections = arguments.get("sections")
    if sections is None:
        return TopologyRequest()
    if (type(sections) not in (list, tuple) or not sections or len(sections) > len(SECTIONS)
            or any(type(item) is not str or item not in SECTIONS for item in sections)
            or len(set(sections)) != len(sections)):
        raise TopologyError("invalid_arguments")
    return TopologyRequest(tuple(section for section in SECTIONS if section in sections))


def classify_reply(message: Any, expected_id: int, section: str) -> Any:
    """Return the result payload, or raise ``SectionRefused`` with a typed reason.

    An envelope that is not this command's result is a stream fault and
    raises ``TopologyError``.  Upstream error text is never read.
    """

    if (type(message) is not dict or message.get("type") != "result"
            or type(message.get("id")) is not int or message["id"] != expected_id
            or type(message.get("success")) is not bool):
        raise TopologyError("malformed_upstream_response")
    if message["success"] is True:
        if "error" in message or type(message.get("result")) is not dict:
            raise SectionRefused("malformed_upstream_response")
        return message["result"]
    if "result" in message:
        raise SectionRefused("malformed_upstream_response")
    error = message.get("error")
    code = error.get("code") if type(error) is dict else None
    if type(code) is not str:
        raise SectionRefused("malformed_upstream_response")
    if code == "unknown_command":
        # thread registers in async_setup; matter only in async_setup_entry.
        raise SectionRefused("thread_not_loaded" if section == DATASETS else "matter_not_loaded")
    if code == "not_supported" and section == TOPOLOGY:
        raise SectionRefused("matter_server_schema_below_13")
    if code in ("unauthorized", "forbidden"):
        raise SectionRefused("core_authority_refused")
    raise SectionRefused("upstream_command_failed")


@dataclass
class SectionOutcome:
    section: str
    dispatched: bool = False
    result: Any = None
    reason: str | None = None


class TopologyReader:
    """One closed, bounded WebSocket session; no retry, redirect or subscription."""

    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def _record(started: float, timed_out: bool) -> None:
        finished = time.perf_counter()
        duration = round((finished - started) * 1000, 3)
        METRICS.record_ha(duration, timeout=timed_out)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.ha_duration_ms += duration
            telemetry.finish_ha_attempt(finished)
            telemetry.timeout_occurred = telemetry.timeout_occurred or timed_out
            telemetry.endpoint_categories.add("thread_matter_topology")

    async def read(
        self, *, sections: tuple[str, ...], core_version: str, authorize: Callable[[], None],
        on_dispatch: Callable[[str], None] = lambda _command: None,
        on_sent: Callable[[str], None] = lambda _command: None,
    ) -> dict[str, SectionOutcome]:
        """Return one outcome per requested section, or raise ``TopologyError``.

        A stream fault before any section reply raises.  After at least one
        reply, the remaining sections take the fault's reason instead, except
        authority loss, which always fails the whole read.
        """

        if (type(sections) is not tuple or not sections
                or any(section not in SECTIONS for section in sections)
                or len(set(sections)) != len(sections)):
            raise TopologyError("invalid_arguments")
        deadline = min(MAX_SECONDS, float(getattr(self.settings, "ha_timeout_seconds", MAX_SECONDS)))
        if not math.isfinite(deadline) or deadline <= 0:
            raise TopologyError("timeout")
        outcomes = {section: SectionOutcome(section) for section in sections}
        replied: list[str] = []
        frames = 0

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise TopologyError("malformed_upstream_response")
                result[key] = value
            return result

        def constant(_value):
            raise TopologyError("malformed_upstream_response")

        def float_value(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise TopologyError("malformed_upstream_response")
            return parsed

        async def no_retry(request, handler):
            try:
                return await handler(request)
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
                raise TopologyError("transport_unavailable") from None

        async def reject_redirect(*_args):
            raise TopologyError("transport_unavailable")

        authorize()
        started = time.perf_counter()
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.begin_ha_attempt(started)
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        timed_out = False
        reason: str | None = None
        try:
            async with asyncio.timeout(deadline):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=deadline), trust_env=False,
                    auto_decompress=False, middlewares=(no_retry,), trace_configs=[trace],
                ) as session:
                    async with session.ws_connect(
                        # aiohttp rejects at >= max_msg_size; the byte ceiling
                        # stays inclusive and is checked again before decoding.
                        self.settings.websocket_url, max_msg_size=FRAME_BYTES + 1,
                        autoping=False, compress=0,
                        timeout=aiohttp.ClientWSTimeout(ws_receive=deadline, ws_close=min(1, deadline)),
                    ) as websocket:
                        async def receive(*, auth=False):
                            nonlocal frames
                            frames += 1
                            if frames > MAX_FRAMES:
                                raise TopologyError("malformed_upstream_response")
                            frame = await websocket.receive()
                            if (frame.type == aiohttp.WSMsgType.ERROR
                                    and isinstance(frame.data, aiohttp.WebSocketError)
                                    and frame.data.code == aiohttp.WSCloseCode.MESSAGE_TOO_BIG):
                                raise TopologyError("upstream_response_too_large")
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                raise TopologyError("transport_unavailable"
                                                    if frame.type in (aiohttp.WSMsgType.CLOSE,
                                                                      aiohttp.WSMsgType.CLOSING,
                                                                      aiohttp.WSMsgType.CLOSED)
                                                    else "malformed_upstream_response")
                            if len(frame.data.encode("utf-8")) > (AUTH_BYTES if auth else FRAME_BYTES):
                                raise TopologyError("upstream_response_too_large")
                            try:
                                data = json.loads(frame.data, object_pairs_hook=pairs,
                                                  parse_constant=constant, parse_float=float_value)
                            except (ValueError, TypeError, RecursionError):
                                raise TopologyError("malformed_upstream_response") from None
                            if type(data) is not dict:
                                raise TopologyError("malformed_upstream_response")
                            return data

                        greeting = await receive(auth=True)
                        if greeting.get("type") != "auth_required":
                            raise TopologyError("malformed_upstream_response")
                        await websocket.send_json({"type": "auth", "access_token": self.settings.ha_token})
                        auth = await receive(auth=True)
                        if auth.get("type") != "auth_ok":
                            raise TopologyError("core_authority_refused")
                        if auth.get("ha_version") != core_version:
                            raise TopologyError("identity_mismatch")

                        for command_id, section in enumerate(sections, start=1):
                            payload = PAYLOADS[section]
                            authorize()
                            on_dispatch(payload["type"])
                            # With compress=0 aiohttp writes the frame before
                            # its first await; a closing transport raises.
                            await websocket.send_json({"id": command_id, **payload})
                            on_sent(payload["type"])
                            outcomes[section].dispatched = True
                            message = await receive()
                            try:
                                outcomes[section].result = classify_reply(message, command_id, section)
                            except SectionRefused as refused:
                                outcomes[section].reason = refused.reason
                            replied.append(section)
                        authorize()
                        return outcomes
        except TopologyError as exc:
            reason = exc.reason
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            reason = "timeout"
        except aiohttp.WSServerHandshakeError as exc:
            reason = "core_authority_refused" if exc.status in (401, 403) else "transport_unavailable"
        except (aiohttp.ClientError, OSError, ValueError, TypeError, RecursionError):
            reason = "transport_unavailable"
        finally:
            self._record(started, timed_out)
        if not replied or reason not in SECTION_REASONS:
            # Nothing usable arrived, or authority or identity was lost.
            raise TopologyError(reason or "provider_error")
        for section in sections:
            if section not in replied:
                outcomes[section].result = None
                outcomes[section].reason = reason
        return outcomes


def normalize_identifier(value: str) -> str:
    """Uppercase hex/alphanumeric with separators removed (section 5.1.2 rule 2)."""

    return _NON_ALNUM.sub("", value).upper()


def secret_key_present(value: Any, depth: int = 0) -> bool:
    """True when any key at any depth looks like Thread credential material."""

    if depth > MAX_DEPTH:
        raise SectionRefused("malformed_upstream_response")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise SectionRefused("malformed_upstream_response")
            normalized = _NON_ALNUM.sub("", key).lower()
            if any(marker in normalized for marker in SECRET_KEY_MARKERS):
                return True
            if secret_key_present(item, depth + 1):
                return True
    elif type(value) is list:
        return any(secret_key_present(item, depth + 1) for item in value)
    return False


class AliasTable:
    """One per response: counters, never hashes or derived bytes."""

    def __init__(self) -> None:
        self.graph_refs: dict[str, str] = {}  # normalized extended address -> g<k>
        self.extended: dict[str, str] = {}  # normalized value -> x<k>
        self.agents: dict[str, str] = {}  # normalized value -> b<k>
        self.identifiers: set[str] = set()  # every normalized LAN identifier seen

    def observe(self, value: Any) -> None:
        if type(value) is str:
            normalized = normalize_identifier(value)
            if len(normalized) >= MIN_IDENTIFIER_SCREEN:
                self.identifiers.add(normalized)

    def bind_graph(self, value: Any, reference: str) -> None:
        if type(value) is str:
            normalized = normalize_identifier(value)
            if normalized:
                self.graph_refs.setdefault(normalized, reference)

    def extended_ref(self, value: str) -> str:
        normalized = normalize_identifier(value)
        if normalized in self.graph_refs:
            return self.graph_refs[normalized]
        if normalized not in self.extended:
            self.extended[normalized] = f"x{len(self.extended) + 1}"
        return self.extended[normalized]

    def agent_ref(self, value: str) -> str:
        normalized = normalize_identifier(value)
        if normalized not in self.agents:
            self.agents[normalized] = f"b{len(self.agents) + 1}"
        return self.agents[normalized]

    def contains_identifier(self, text: str) -> bool:
        normalized = normalize_identifier(text)
        return any(identifier in normalized for identifier in self.identifiers)


@dataclass
class _Screen:
    redacted: int = 0
    truncated: int = 0
    collisions: int = 0
    withheld: int = 0
    failed_closed: bool = False
    categories: frozenset[str] = frozenset()
    fields: list[tuple[dict, str, int]] = field(default_factory=list)

    def text(self, container: dict, key: str, value: Any, bound: int) -> None:
        """Queue one kept free-text field for screening; ``None`` stays ``None``."""

        if value is not None and type(value) is not str:
            raise SectionRefused("malformed_upstream_response")
        container[key] = value
        if value is not None:
            self.fields.append((container, key, bound))

    def apply(self, aliases: AliasTable, known_secrets: tuple[str, ...]) -> None:
        """Identifier screen, then secret-shaped hex, then the shared sanitizer.

        Truncation is last.  A withheld field becomes ``None``.
        """

        by_bound: dict[int, list[tuple[dict, str]]] = {}
        for container, key, bound in self.fields:
            value = container[key]
            if aliases.contains_identifier(value) or _SECRET_HEX.search(value):
                container[key] = None
                self.withheld += 1
                if key == "network_name":
                    container["network_name_withheld"] = True
                continue
            by_bound.setdefault(bound, []).append((container, key))
        for bound, refs in sorted(by_bound.items()):
            result = sanitize_untrusted_data([container[key] for container, key in refs],
                                             known_secrets=known_secrets, max_string=bound)
            self.redacted += result.redacted_field_count
            self.truncated += result.truncated_field_count
            self.collisions += result.marker_collision_count
            self.failed_closed = self.failed_closed or result.failed_closed
            self.categories = self.categories | frozenset(result.redaction_categories)
            values = result.value
            if type(values) is not list or len(values) != len(refs):
                raise TopologyError("provider_error")
            for (container, key), value in zip(refs, values):
                if value is not None and type(value) is not str:
                    raise TopologyError("provider_error")
                container[key] = value
        self.fields.clear()

    def summary(self) -> SanitizationResult:
        return SanitizationResult(
            value=None, redacted_field_count=self.redacted,
            redaction_categories=tuple(sorted(self.categories)),
            truncated_field_count=self.truncated, failed_closed=self.failed_closed,
            marker_collision_count=self.collisions,
        )


def _optional_bool(record: dict, key: str) -> bool | None:
    value = record.get(key)
    if value is None or type(value) is bool:
        return value
    raise SectionRefused("malformed_upstream_response")


def _optional_int(record: dict, key: str, low: int, high: int) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise SectionRefused("malformed_upstream_response")
    return value


def _vocabulary(record: dict, key: str, *, required: bool) -> str | None:
    value = record.get(key)
    if value is None and not required:
        return None
    if type(value) is not str or not _VOCABULARY.fullmatch(value):
        raise SectionRefused("malformed_upstream_response")
    return value


def _optional_str(record: dict, key: str) -> str | None:
    value = record.get(key)
    if value is None or type(value) is str:
        return value
    raise SectionRefused("malformed_upstream_response")


def _hex(value: Any, pattern: re.Pattern, *, allow_none_text: bool = False) -> str | None:
    """Network-level hex identifiers, normalized lowercase without separators."""

    if value is None:
        return None
    if type(value) is not str:
        raise SectionRefused("malformed_upstream_response")
    if allow_none_text and value == "None":
        # Core 2026.11.0 ``dataset_store.py:149-151`` stringifies a missing PANID.
        return None
    normalized = value.lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    normalized = normalized.replace(":", "").replace("-", "").replace(" ", "")
    if not pattern.fullmatch(normalized):
        raise SectionRefused("malformed_upstream_response")
    return normalized


def _upstream_list(result: dict, key: str, limit: int) -> list:
    value = result.get(key)
    if type(value) is not list:
        raise SectionRefused("malformed_upstream_response")
    if len(value) > limit:
        raise SectionRefused("upstream_response_too_large")
    return value


def _epoch_ms_iso(value: Any) -> str:
    if type(value) is not int or not 0 <= value <= MAX_EPOCH_MS:
        raise SectionRefused("malformed_upstream_response")
    moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _observe_graph_identifiers(node: dict, aliases: AliasTable) -> None:
    upstream_id = node.get("id")
    aliases.observe(upstream_id)
    if type(upstream_id) is str:
        for prefix in GRAPH_ID_PREFIXES:
            if upstream_id.startswith(prefix):
                aliases.observe(upstream_id[len(prefix):])
    for key in ("ext_address", "bssid", "ssid", "host_name"):
        aliases.observe(node.get(key))


def project_topology(result: Any, aliases: AliasTable, screen: _Screen) -> dict[str, Any]:
    """A2 projection under the positive allowlist (section 5.1 and 5.1.2)."""

    if type(result) is not dict:
        raise SectionRefused("malformed_upstream_response")
    if secret_key_present(result):
        raise SectionRefused("malformed_upstream_response")
    graph_assembled_at = _epoch_ms_iso(result.get("collected_at"))
    nodes = _upstream_list(result, "nodes", MAX_UPSTREAM_NODES)
    connections = _upstream_list(result, "connections", MAX_UPSTREAM_CONNECTIONS)

    # Rule 1: every upstream node gets g<k> in upstream order, before truncation.
    graph: dict[str, str] = {}
    for index, node in enumerate(nodes, start=1):
        if type(node) is not dict:
            raise SectionRefused("malformed_upstream_response")
        upstream_id = node.get("id")
        if type(upstream_id) is not str or not upstream_id or upstream_id in graph:
            raise SectionRefused("malformed_upstream_response")
        reference = f"g{index}"
        graph[upstream_id] = reference
        _observe_graph_identifiers(node, aliases)
        aliases.bind_graph(node.get("ext_address"), reference)
        for prefix in ("br_", "unknown_"):
            if upstream_id.startswith(prefix):
                aliases.bind_graph(upstream_id[len(prefix):], reference)
    for connection in connections:
        if type(connection) is not dict:
            raise SectionRefused("malformed_upstream_response")
        aliases.observe(connection.get("source"))
        aliases.observe(connection.get("target"))

    emitted_nodes = []
    emitted_refs: set[str] = set()
    for node in nodes[:MAX_NODES]:
        kind = _vocabulary(node, "kind", required=True)
        network_type = _vocabulary(node, "network_type", required=True)
        ha_device_id = node.get("ha_device_id")
        if ha_device_id is not None and (type(ha_device_id) is not str or not _HA_DEVICE_ID.fullmatch(ha_device_id)):
            raise SectionRefused("malformed_upstream_response")
        if ha_device_id is not None:
            identity = "ha_device"
        elif kind in NON_HA_KINDS:
            identity = "not_an_ha_device"
        elif kind == "matter":
            identity = "ha_registry_match_missing"
        else:
            identity = "unknown"
        reference = graph[node["id"]]
        projected: dict[str, Any] = {
            "id": reference,
            "kind": kind,
            "network_type": network_type,
            "role": _vocabulary(node, "role", required=False),
            "available": _optional_bool(node, "available"),
            "is_bridge": _optional_bool(node, "is_bridge"),
            "rloc16": _optional_int(node, "rloc16", 0, 0xFFFF),
            "ext_pan_id": _hex(node.get("ext_pan_id"), _HEX16),
            "node_id": _optional_int(node, "node_id", 0, MAX_NODE_ID),
            "ha_device_id": ha_device_id,
            "identity": identity,
            "border_router_mdns_last_seen": _optional_int(node, "last_seen", 0, MAX_EPOCH_MS),
        }
        # Rule 4: the network name is kept only for Thread, never for Wi-Fi APs.
        if network_type == "thread" and kind != "wifi_ap":
            projected["network_name_withheld"] = False
            screen.text(projected, "network_name", _optional_str(node, "network_name"), NAME_CHARS)
        else:
            projected["network_name"] = None
            projected["network_name_withheld"] = True
        screen.text(projected, "vendor_name", _optional_str(node, "vendor_name"), LABEL_CHARS)
        screen.text(projected, "model_name", _optional_str(node, "model_name"), LABEL_CHARS)
        emitted_nodes.append(projected)
        emitted_refs.add(reference)

    resolved = []
    dropped = 0
    for connection in connections:
        source = connection.get("source")
        target = connection.get("target")
        if type(source) is not str or type(target) is not str:
            raise SectionRefused("malformed_upstream_response")
        source_ref = graph.get(source)
        target_ref = graph.get(target)
        if source_ref not in emitted_refs or target_ref not in emitted_refs:
            # Never emitted with a raw endpoint.
            dropped += 1
            continue
        resolved.append({
            "source": source_ref,
            "target": target_ref,
            "network": _vocabulary(connection, "network", required=True),
            "strength": _vocabulary(connection, "strength", required=True),
            "source_to_target": _direction(connection.get("source_to_target")),
            "target_to_source": _direction(connection.get("target_to_source")),
            "via_route_table": _optional_bool(connection, "via_route_table"),
            "path_cost": _optional_int(connection, "path_cost", 0, 0xFFFF),
        })
    nodes_truncated = len(nodes) > MAX_NODES
    connections_truncated = len(resolved) > MAX_CONNECTIONS
    return {
        "status": "ok",
        "nodes": emitted_nodes,
        "connections": resolved[:MAX_CONNECTIONS],
        "total": {"nodes": len(nodes), "connections": len(connections)},
        "truncated": nodes_truncated or connections_truncated,
        "nodes_truncated": nodes_truncated,
        "connections_truncated": connections_truncated,
        "connections_dropped_unresolved": dropped,
        "graph_assembled_at": graph_assembled_at,
        "observation_age": "unknown",
        "coverage": {"border_routers": "neighbours_of_commissioned_nodes_only"},
        "connection_semantics": "reported_links_not_end_to_end_paths",
        "upstream_activity": list(UPSTREAM_ACTIVITY[TOPOLOGY]),
    }


def _direction(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise SectionRefused("malformed_upstream_response")
    return {
        "strength": _vocabulary(value, "strength", required=True),
        "lqi": _optional_int(value, "lqi", 0, 255),
        "rssi": _optional_int(value, "rssi", -128, 127),
    }


def project_datasets(result: Any, aliases: AliasTable, screen: _Screen) -> dict[str, Any]:
    """A1 projection: non-secret dataset identities only (section 5.1)."""

    if type(result) is not dict:
        raise SectionRefused("malformed_upstream_response")
    if secret_key_present(result):
        raise SectionRefused("malformed_upstream_response")
    datasets = _upstream_list(result, "datasets", MAX_UPSTREAM_DATASETS)
    for record in datasets:
        if type(record) is not dict:
            raise SectionRefused("malformed_upstream_response")
        aliases.observe(record.get("preferred_border_agent_id"))
        aliases.observe(record.get("preferred_extended_address"))
    items = []
    seen: set[str] = set()
    for record in datasets[:MAX_DATASETS]:
        dataset_id = record.get("dataset_id")
        if type(dataset_id) is not str or not _DATASET_ID.fullmatch(dataset_id) or dataset_id in seen:
            raise SectionRefused("malformed_upstream_response")
        seen.add(dataset_id)
        created = record.get("created")
        if type(created) is not str or not _ISO_TIME.fullmatch(created):
            raise SectionRefused("malformed_upstream_response")
        preferred = record.get("preferred")
        if type(preferred) is not bool:
            raise SectionRefused("malformed_upstream_response")
        agent = _optional_str(record, "preferred_border_agent_id")
        extended = _optional_str(record, "preferred_extended_address")
        item: dict[str, Any] = {
            "dataset_id": dataset_id,
            "extended_pan_id": _hex(record.get("extended_pan_id"), _HEX16),
            "pan_id": _hex(record.get("pan_id"), _HEX4, allow_none_text=True),
            "channel": _optional_int(record, "channel", 0, 0xFFFF),
            "preferred": preferred,
            "created": created,
            "preferred_border_agent_ref": aliases.agent_ref(agent) if agent else None,
            "preferred_extended_address_ref": aliases.extended_ref(extended) if extended else None,
        }
        screen.text(item, "network_name", _optional_str(record, "network_name"), NAME_CHARS)
        screen.text(item, "source", _optional_str(record, "source"), NAME_CHARS)
        items.append(item)
    return {
        "status": "ok",
        "items": items,
        "total": len(datasets),
        "truncated": len(datasets) > MAX_DATASETS,
        "upstream_activity": list(UPSTREAM_ACTIVITY[DATASETS]),
    }


def failed_section(section: str, reason: str, *, dispatched: bool) -> dict[str, Any]:
    if reason not in SECTION_REASONS:
        reason = "malformed_upstream_response"
    return {
        "status": reason,
        # The read's activity class is disclosed only when it was sent.
        "upstream_activity": list(UPSTREAM_ACTIVITY[section]) if dispatched else [],
    }


def build_result(outcomes: Mapping[str, SectionOutcome], known_secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    """Project every section; topology first so its graph ids bind A1 aliases."""

    aliases = AliasTable()
    screen = _Screen()
    projected: dict[str, dict[str, Any]] = {}
    for section, projector in ((TOPOLOGY, project_topology), (DATASETS, project_datasets)):
        outcome = outcomes.get(section)
        if outcome is None:
            continue
        if outcome.reason is not None:
            projected[section] = failed_section(section, outcome.reason, dispatched=outcome.dispatched)
            continue
        staged = _Screen()
        try:
            value = projector(outcome.result, aliases, staged)
        except SectionRefused as refused:
            projected[section] = failed_section(section, refused.reason, dispatched=outcome.dispatched)
            continue
        screen.fields.extend(staged.fields)
        projected[section] = value
    # Every identifier from every section is known before any text is screened.
    screen.apply(aliases, known_secrets)
    sections = [section for section in SECTIONS if section in projected]
    unavailable = any(projected[section]["status"] != "ok" for section in sections)
    truncated = any(projected[section].get("truncated") is True for section in sections)
    dropped = projected.get(TOPOLOGY, {}).get("connections_dropped_unresolved", 0) > 0
    text_partial = (screen.redacted > 0 or screen.truncated > 0 or screen.failed_closed
                    or screen.collisions > 0 or screen.withheld > 0)
    partial_reasons = [reason for reason, present in (
        ("section_unavailable", unavailable),
        ("truncated", truncated),
        ("connections_dropped_unresolved", dropped),
        ("sanitized_text", text_partial),
    ) if present]
    result: dict[str, Any] = {
        "identifier_policy": IDENTIFIER_POLICY,
        "sections": sections,
        **{section: projected[section] for section in sections},
        "completeness": "partial" if partial_reasons else "complete",
        "partial_reasons": partial_reasons,
        "sanitization_failed_closed": screen.failed_closed,
        "redaction": redaction_summary(screen.summary()),
        "withheld_identifier_field_count": screen.withheld,
        "truncated_field_count": screen.truncated,
    }
    return result


class ThreadMatterTopologyService:
    def __init__(self, reader, core_runtime, *, known_secrets: tuple[str, ...] = ()):
        self.reader = reader
        self.core_runtime = core_runtime
        self.known_secrets = tuple(secret for secret in known_secrets if secret)

    def authority(self) -> tuple[Any, str]:
        """Require the admitted profile, an observed Core and dispatch authority."""

        status = self.core_runtime.route_status(CORE_REQUIREMENTS)
        observation = getattr(self.core_runtime, "current_observation", None)
        version = getattr(observation, "version", None)
        telemetry = current_telemetry()
        if (status.get("available") is not True or type(version) is not str or not version
                or telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise TopologyError("authority_unavailable")
        return status.get("generation"), version

    async def topology(self, request: TopologyRequest) -> dict[str, Any]:
        if type(request) is not TopologyRequest:
            raise TopologyError("invalid_arguments")
        if current_caller_id() == "anonymous":
            raise TopologyError("authority_unavailable")
        authority = self.authority()
        commands: list[str] = []  # admitted for dispatch
        sent: list[str] = []  # written to the transport

        def authorize() -> None:
            # The admission generation and Core identity hold across the read.
            if self.authority() != authority:
                raise TopologyError("authority_unavailable")

        def on_dispatch(command: str) -> None:
            if command not in COMMANDS or command in commands or len(commands) >= MAX_COMMANDS:
                raise TopologyError("provider_error")  # unreachable by construction
            commands.append(command)

        def on_sent(command: str) -> None:
            if len(sent) >= len(commands) or commands[len(sent)] != command:
                raise TopologyError("provider_error")  # unreachable by construction
            sent.append(command)

        failure = None
        result: dict[str, Any] = {}
        try:
            outcomes = await self.reader.read(
                sections=request.sections, core_version=authority[1], authorize=authorize,
                on_dispatch=on_dispatch, on_sent=on_sent,
            )
            if type(outcomes) is not dict or set(outcomes) != set(request.sections):
                raise TopologyError("provider_error")
            result = build_result(outcomes, self.known_secrets)
        except asyncio.CancelledError:
            raise
        except TopologyError as exc:
            failure = exc
        except Exception:
            # Exception text and provider values never enter the result.
            failure = TopologyError("provider_error")
        finally:
            if commands:
                METRICS.record_provider_result(
                    "direct_ha_api",
                    "failed" if failure else result.get("completeness", "failed"),
                    dispatched=True,
                )
        if failure is None:
            try:
                if self.authority() != authority:
                    failure = TopologyError("authority_unavailable")
            except TopologyError as exc:
                failure = exc
            except Exception:
                failure = TopologyError("provider_error")
        if failure is not None:
            raise TopologyError(failure.reason) from None
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.completeness = result["completeness"]
            telemetry.result_status = "partial" if result["completeness"] == "partial" else "success"
        result["commands_sent"] = len(sent)
        return result


class ThreadMatterTopologyRuntime:
    def __init__(self) -> None:
        self.service: ThreadMatterTopologyService | None = None

    def configure(self, reader, core_runtime, settings) -> None:
        self.service = ThreadMatterTopologyService(
            reader, core_runtime, known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> ThreadMatterTopologyService:
        if self.service is None:
            raise TopologyError("authority_unavailable")
        return self.service


THREAD_MATTER_TOPOLOGY = ThreadMatterTopologyRuntime()


__all__ = [
    "AliasTable",
    "COMMANDS",
    "CORE_REQUIREMENTS",
    "DATASETS",
    "DATASETS_PAYLOAD",
    "IDENTIFIER_POLICY",
    "LIST_DATASETS_COMMAND",
    "MAX_COMMANDS",
    "MAX_CONNECTIONS",
    "MAX_DATASETS",
    "MAX_NODES",
    "NETWORK_TOPOLOGY_COMMAND",
    "NEVER_SENT_COMMANDS",
    "PAYLOADS",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "SECTIONS",
    "SECTION_REASONS",
    "SectionOutcome",
    "SectionRefused",
    "THREAD_MATTER_TOPOLOGY",
    "TOPOLOGY",
    "TOPOLOGY_PAYLOAD",
    "ThreadMatterTopologyRuntime",
    "ThreadMatterTopologyService",
    "TopologyError",
    "TopologyReader",
    "TopologyRequest",
    "UPSTREAM_ACTIVITY",
    "build_result",
    "classify_reply",
    "normalize_identifier",
    "project_datasets",
    "project_topology",
    "secret_key_present",
    "validate_arguments",
]
