"""Bounded, privacy-conservative security metadata read (HAMCP-134).

``get_security_metadata`` is an Engineering-native read of Home Assistant
Core 2026.11.0 security and effective-permission posture, designed in
``docs/HAMCP_134_SECURITY_METADATA_FEASIBILITY.md`` (#392).  The runtime-only
profile ``core.security_metadata_read`` is admitted only by an owner-signed
registry; until then this service refuses ``authority_unavailable`` with zero
Home Assistant commands.

Closed vocabulary: constant payloads only, never a caller argument.

* ``auth/current_user`` once per call, whatever the sections (identity and
  the MFA module catalog only);
* ``config/auth/list`` only when ``users`` is requested;
* ``http/config`` (``http_boundary``), ``get_config`` (``urls``);
* ``homeassistant/expose_new_entities/get`` once per Core ``KNOWN_ASSISTANTS``
  value, ``assist_pipeline/pipeline/list`` and ``conversation/agent/list``
  without ``language`` or ``country`` (``assist``);
* ``get_panels`` (``panels``).

At most ten commands, sent sequentially on one connection, each at most once.
There is no optional read (no ``homeassistant/expose_entity/list``, no
``assist_satellite/get_configuration``) and no Supervisor request.  Raw
values are used only inside the positive projection below and discarded:
user ids become per-response ordinal pseudonyms, names and usernames are
dropped, TLS paths become booleans, trusted proxies become a count and three
class booleans.  A kept field the source does not return is
``"not_provided"``; nothing is inferred.  Core error text never leaves the
reader: only the Core error ``code`` maps to a fixed reason.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import math
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import aiohttp

from .errors import ErrorCode, GovernanceError
from .ha_core_readmission.routes import SECURITY_METADATA_PROFILE
from .observability import METRICS
from .request_context import current_caller_id, current_telemetry
from .sanitization import SanitizationResult, redaction_summary, sanitize_untrusted_data

PROFILE = SECURITY_METADATA_PROFILE
CORE_REQUIREMENTS = (PROFILE,)
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PROVIDER = "direct_ha_api"
PROVIDER_CONTRACT = "compiled-core-security-metadata-read-v1"
SCHEMA_VERSION = "security-metadata-v1"

CURRENT_USER = "auth/current_user"
AUTH_LIST = "config/auth/list"
HTTP_CONFIG = "http/config"
GET_CONFIG = "get_config"
EXPOSE_NEW_GET = "homeassistant/expose_new_entities/get"
PIPELINE_LIST = "assist_pipeline/pipeline/list"
AGENT_LIST = "conversation/agent/list"
GET_PANELS = "get_panels"
COMMANDS = frozenset({
    CURRENT_USER, AUTH_LIST, HTTP_CONFIG, GET_CONFIG, EXPOSE_NEW_GET,
    PIPELINE_LIST, AGENT_LIST, GET_PANELS,
})

# Core 2026.11.0 components/homeassistant/exposed_entities.py:25.
KNOWN_ASSISTANTS = ("cloud.alexa", "cloud.google_assistant", "conversation")


def _payload(**fields: str) -> Mapping[str, str]:
    return MappingProxyType(dict(fields))


CURRENT_USER_PAYLOAD = _payload(type=CURRENT_USER)
AUTH_LIST_PAYLOAD = _payload(type=AUTH_LIST)
HTTP_CONFIG_PAYLOAD = _payload(type=HTTP_CONFIG)
GET_CONFIG_PAYLOAD = _payload(type=GET_CONFIG)
EXPOSE_NEW_PAYLOADS = tuple(_payload(type=EXPOSE_NEW_GET, assistant=assistant)
                            for assistant in KNOWN_ASSISTANTS)
PIPELINE_LIST_PAYLOAD = _payload(type=PIPELINE_LIST)
AGENT_LIST_PAYLOAD = _payload(type=AGENT_LIST)
GET_PANELS_PAYLOAD = _payload(type=GET_PANELS)
PAYLOADS = (CURRENT_USER_PAYLOAD, AUTH_LIST_PAYLOAD, HTTP_CONFIG_PAYLOAD, GET_CONFIG_PAYLOAD,
            *EXPOSE_NEW_PAYLOADS, PIPELINE_LIST_PAYLOAD, AGENT_LIST_PAYLOAD, GET_PANELS_PAYLOAD)

SECTIONS = ("users", "mfa", "http_boundary", "urls", "assist", "panels")
SECTION_PAYLOADS: Mapping[str, tuple[Mapping[str, str], ...]] = MappingProxyType({
    "users": (AUTH_LIST_PAYLOAD,),
    "mfa": (),  # auth/current_user is always sent
    "http_boundary": (HTTP_CONFIG_PAYLOAD,),
    "urls": (GET_CONFIG_PAYLOAD,),
    "assist": (*EXPOSE_NEW_PAYLOADS, PIPELINE_LIST_PAYLOAD, AGENT_LIST_PAYLOAD),
    "panels": (GET_PANELS_PAYLOAD,),
})
SECTION_COMMANDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "users": (AUTH_LIST,),
    "mfa": (CURRENT_USER,),
    "http_boundary": (HTTP_CONFIG,),
    "urls": (GET_CONFIG,),
    "assist": (EXPOSE_NEW_GET, PIPELINE_LIST, AGENT_LIST),
    "panels": (GET_PANELS,),
})

# Design section 6.  None of these strings is ever built into a payload; the
# module sends only the constant payloads above.
NEVER_SENT_COMMANDS = (
    "auth/long_lived_access_token",
    "auth/sign_path",
    "auth/refresh_tokens",
    "auth/delete_refresh_token",
    "auth/delete_all_refresh_tokens",
    "auth/refresh_token_set_expiry",
    "auth/setup_mfa",
    "auth/depose_mfa",
    "config/auth/create",
    "config/auth/update",
    "config/auth/delete",
    "http/config/configure",
    "http/config/promote",
    "cloud/status",
    "homeassistant/expose_entity",
    "homeassistant/expose_new_entities/set",
    "homeassistant/expose_entity/list",
    "assist_satellite/get_configuration",
    "assist_pipeline/pipeline/create",
    "assist_pipeline/pipeline/update",
    "assist_pipeline/pipeline/delete",
    "assist_pipeline/pipeline/set_preferred",
    "frontend/update_panel",
    "config_entries/options/flow",
    "person/list",
)
NEVER_SENT_PREFIXES = ("supervisor/", "hassio/")

# Acquisition bounds (design sections 7.3 and 8).
FRAME_BYTES = 2_097_152
AUTH_BYTES = 65_536
MAX_COMMANDS = 10
MAX_FRAMES = 2 + MAX_COMMANDS  # auth_required, auth_ok, one result per command
MAX_SECONDS = 30.0
MAX_USERS = 1_000  # approval_identity.MAX_USERS
MAX_LIST_ITEMS = 500
OUTPUT_BYTES = 40_000

# Projection bounds (providers/upstream_platform_reads.py).
STRING_CAP = 200
TITLE_CAP = 120
EMAIL_MASK = "[email]"
_EMAIL_LIKE = re.compile(r"[^\s@]+@[^\s@]+")

NOT_PROVIDED = "not_provided"
UNKNOWN = "unknown"
CONFIGURED_LABEL = "configured_not_necessarily_active"

# Core 2026.11.0 homeassistant/auth/const.py:9-11.
GROUP_ADMIN = "system-admin"
GROUP_USER = "system-users"
GROUP_READ_ONLY = "system-read-only"

# Core 2026.11.0 components/http/config.py:91-92, 117-123.
HTTP_ERRORS = ("apply_failed", "not_promoted")
ACTIVE_CONFIG_TYPES = ("stable", "pending", "default", "default_legacy_port")
WILDCARD_BINDS = ("0.0.0.0", "::")
NABU_CASA_SUFFIX = ".ui.nabu.casa"
DEFAULT_PORTS = MappingProxyType({"http": 80, "https": 443})

# Core error code -> fixed section reason.  Message text is never read.
CORE_ERROR_REASONS = MappingProxyType({
    "unauthorized": "not_admin",
    "unknown_command": "command_unavailable",
})
SECTION_REASONS = frozenset({
    "not_requested", "not_admin", "command_unavailable", "core_error", "unexpected_shape",
    "too_many_users", "list_exceeds_bound", "component_failed", "output_cap",
    "withheld_for_local_only_user", "connection_identity_not_supervisor",
    "active_slot_not_returned", "active_config_type_unrecognized", "active_slot_missing",
    "active_slot_inconsistent",
})

IDENTITY_SUPERVISOR = "supervisor_system_user"
IDENTITY_OTHER = "other_connection_user"
IDENTITY_UNKNOWN = "unknown"
IDENTITY_BASES = frozenset({
    "supervisor_topology_and_core_corroboration",
    "supervisor_corroboration_failed",
    "alternative_connection_configuration",
    "current_user_failed",
    "token_source_unknown",
})

# Design section 4.8: every X area, reported so absence is never silent.  No
# session-default constant is reported (Josh decision 2026-11-08, item 10).
OMISSIONS = MappingProxyType({
    "per_user_mfa_enrolment": "not_provided_by_api",
    "per_user_session_token_policy_and_long_lived_token_existence": "not_provided_by_api",
    "complete_effective_exposure": "not_provided_by_api",
    "conversation_agent_llm_api_and_tool_scope": "not_provided_by_api",
    "satellite_user_authority": "not_provided_by_api",
    "device_tablet_kiosk_acting_user": "not_provided_by_api",
    "complete_add_on_inventory_and_other_add_on_options": "not_provided_by_api",
})
PERMISSION_LIMITS = MappingProxyType({
    "mfa.connection_user_only": (
        "MFA enabled flags describe only the connection user; per-user enrolment is not readable."),
    "panels.listing_not_enforcement": (
        "panel_listing_require_admin is the frontend listing value, not proof of access enforcement."),
    "http.default_legacy_port_not_returned": (
        "When default_legacy_port is active, Core does not return that slot; active is unknown."),
})
RELATED_TOOLS = MappingProxyType({
    "ha_get_system_health": "Nabu Casa cloud remote state",
    "ha_get_entity_exposure": "per-entity assistant exposure",
    "ha_get_integration": "companion app (mobile_app) installations",
})

# Lowest priority first: whole items are removed from the end of these lists.
TRUNCATION_ORDER = (("panels", ("panels",)), ("assist", ("agents",)),
                    ("assist", ("pipelines",)), ("users", ("users",)))
ARGUMENT_KEYS = frozenset({"sections"})


class SecurityMetadataError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    CODES = MappingProxyType({
        "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "invalid_arguments": ErrorCode.INVALID_REQUEST,
        "unauthorized": ErrorCode.AUTHORIZATION_FAILURE,
        "response_too_large": ErrorCode.HA_API_ERROR,
        "malformed_response": ErrorCode.HA_API_ERROR,
        "identity_mismatch": ErrorCode.HA_UNAVAILABLE,
        "timeout": ErrorCode.HA_TIMEOUT,
        "transport_unavailable": ErrorCode.HA_UNAVAILABLE,
        "provider_error": ErrorCode.HA_API_ERROR,
    })
    REASONS = frozenset(CODES)

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            reason = "provider_error"
        details: dict[str, Any] = {"reason": reason, "fallback_occurred": False}
        if reason == "authority_unavailable":
            details["core_capability"] = PROFILE
        super().__init__(self.CODES[reason], details=details)
        self.reason = reason


@dataclass(frozen=True)
class SecurityMetadataRequest:
    sections: tuple[str, ...] = SECTIONS


def validate_arguments(arguments: Any) -> SecurityMetadataRequest:
    """Exact keys, closed values; a refused value is never reflected."""

    if type(arguments) is not dict or not set(arguments) <= ARGUMENT_KEYS:
        raise SecurityMetadataError("invalid_arguments")
    sections = arguments.get("sections")
    if sections is None:
        return SecurityMetadataRequest()
    if (type(sections) is not list or not sections or len(sections) > len(SECTIONS)
            or any(type(item) is not str or item not in SECTIONS for item in sections)
            or len(set(sections)) != len(sections)):
        raise SecurityMetadataError("invalid_arguments")
    # Canonical order: the read set never depends on argument order.
    return SecurityMetadataRequest(tuple(name for name in SECTIONS if name in sections))


def read_plan(request: SecurityMetadataRequest) -> tuple[Mapping[str, str], ...]:
    """The exact payloads for ``request``: current_user first, then by section."""

    plan = [CURRENT_USER_PAYLOAD]
    for name in SECTIONS:
        if name in request.sections:
            plan.extend(SECTION_PAYLOADS[name])
    return tuple(plan)


def admitted_payload(payload: Any) -> bool:
    """Only the module's own constant payload objects are ever sendable."""

    return (any(payload is constant for constant in PAYLOADS)
            and payload["type"] in COMMANDS
            and payload["type"] not in NEVER_SENT_COMMANDS
            and not payload["type"].startswith(NEVER_SENT_PREFIXES))


@dataclass(frozen=True)
class Reply:
    """One command outcome: a result, or a fixed reason from the Core error code."""

    command: str
    result: Any = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None


def classify_reply(message: Any, expected_id: int, command: str) -> Reply:
    if (type(message) is not dict or message.get("type") != "result"
            or type(message.get("id")) is not int or message["id"] != expected_id
            or type(message.get("success")) is not bool):
        raise SecurityMetadataError("malformed_response")
    if message["success"] is True:
        if "error" in message or "result" not in message:
            raise SecurityMetadataError("malformed_response")
        return Reply(command, result=message["result"])
    if "result" in message:
        raise SecurityMetadataError("malformed_response")
    error = message.get("error")
    code = error.get("code") if type(error) is dict else None
    if type(code) is not str:
        raise SecurityMetadataError("malformed_response")
    return Reply(command, reason=CORE_ERROR_REASONS.get(code, "core_error"))


class SecurityMetadataReader:
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
            telemetry.endpoint_categories.add("security_metadata")

    async def read(
        self, *, core_version: str, plan: tuple[Mapping[str, str], ...],
        authorize: Callable[[], None],
        on_dispatch: Callable[[str], None] = lambda _command: None,
        on_sent: Callable[[str], None] = lambda _command: None,
    ) -> list[Reply]:
        """Return one ``Reply`` per planned payload or raise ``SecurityMetadataError``."""

        if (type(plan) is not tuple or not plan or len(plan) > MAX_COMMANDS
                or plan[0] is not CURRENT_USER_PAYLOAD
                or not all(admitted_payload(payload) for payload in plan)
                or len({id(payload) for payload in plan}) != len(plan)):
            raise SecurityMetadataError("provider_error")
        deadline = min(MAX_SECONDS, float(getattr(self.settings, "ha_timeout_seconds", MAX_SECONDS)))
        if not math.isfinite(deadline) or deadline <= 0:
            raise SecurityMetadataError("timeout")
        frames = 0

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise SecurityMetadataError("malformed_response")
                result[key] = value
            return result

        def constant(_value):
            raise SecurityMetadataError("malformed_response")

        def float_value(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise SecurityMetadataError("malformed_response")
            return parsed

        async def no_retry(request, handler):
            try:
                return await handler(request)
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
                raise SecurityMetadataError("transport_unavailable") from None

        async def reject_redirect(*_args):
            raise SecurityMetadataError("transport_unavailable")

        authorize()
        started = time.perf_counter()
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.begin_ha_attempt(started)
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        timed_out = False
        try:
            async with asyncio.timeout(deadline):
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=deadline), trust_env=False,
                    auto_decompress=False, middlewares=(no_retry,), trace_configs=[trace],
                ) as session:
                    async with session.ws_connect(
                        self.settings.websocket_url, max_msg_size=FRAME_BYTES + 1,
                        autoping=False, compress=0,
                        timeout=aiohttp.ClientWSTimeout(ws_receive=deadline, ws_close=min(1, deadline)),
                    ) as websocket:
                        async def receive(*, auth=False):
                            nonlocal frames
                            frames += 1
                            if frames > MAX_FRAMES:
                                raise SecurityMetadataError("malformed_response")
                            frame = await websocket.receive()
                            if (frame.type == aiohttp.WSMsgType.ERROR
                                    and isinstance(frame.data, aiohttp.WebSocketError)
                                    and frame.data.code == aiohttp.WSCloseCode.MESSAGE_TOO_BIG):
                                raise SecurityMetadataError("response_too_large")
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                raise SecurityMetadataError("malformed_response")
                            if len(frame.data.encode("utf-8")) > (AUTH_BYTES if auth else FRAME_BYTES):
                                raise SecurityMetadataError("response_too_large")
                            try:
                                data = json.loads(frame.data, object_pairs_hook=pairs,
                                                  parse_constant=constant, parse_float=float_value)
                            except (ValueError, TypeError, RecursionError):
                                raise SecurityMetadataError("malformed_response") from None
                            if type(data) is not dict:
                                raise SecurityMetadataError("malformed_response")
                            return data

                        greeting = await receive(auth=True)
                        if greeting.get("type") != "auth_required":
                            raise SecurityMetadataError("malformed_response")
                        await websocket.send_json({"type": "auth", "access_token": self.settings.ha_token})
                        auth = await receive(auth=True)
                        if auth.get("type") != "auth_ok":
                            raise SecurityMetadataError("unauthorized")
                        if auth.get("ha_version") != core_version:
                            raise SecurityMetadataError("identity_mismatch")

                        replies: list[Reply] = []
                        for command_id, payload in enumerate(plan, start=1):
                            authorize()
                            on_dispatch(payload["type"])
                            # With compress=0 aiohttp writes the frame before its
                            # first await; a closing transport raises instead.
                            await websocket.send_json({"id": command_id, **payload})
                            on_sent(payload["type"])
                            replies.append(classify_reply(await receive(), command_id, payload["type"]))
                        authorize()
                        return replies
        except SecurityMetadataError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            raise SecurityMetadataError("timeout") from None
        except aiohttp.WSServerHandshakeError as exc:
            reason = "unauthorized" if exc.status in (401, 403) else "transport_unavailable"
            raise SecurityMetadataError(reason) from None
        except (aiohttp.ClientError, OSError, ValueError, TypeError, RecursionError):
            raise SecurityMetadataError("transport_unavailable") from None
        finally:
            self._record(started, timed_out)


# --- projection ---------------------------------------------------------------


class _Drift(Exception):
    """A section-local projection failure with a fixed reason; never carries data."""

    def __init__(self, reason: str = "unexpected_shape"):
        super().__init__(reason)
        self.reason = reason if reason in SECTION_REASONS else "unexpected_shape"


class _Text(str):
    """A Core-originated string, sanitized by the final pass; never a constant."""

    capped = False


def _text(value: str, cap: int = STRING_CAP) -> _Text:
    # E-mail masking runs before capping, so a cut cannot leave a partial address.
    masked = _EMAIL_LIKE.sub(EMAIL_MASK, value)
    text = _Text(masked[:cap])
    text.capped = len(masked) > cap
    return text


def _bool(record: Mapping, key: str) -> bool | str:
    if key not in record:
        return NOT_PROVIDED
    if type(record[key]) is not bool:
        raise _Drift()
    return record[key]


def _int(record: Mapping, key: str) -> int | str:
    if key not in record:
        return NOT_PROVIDED
    if type(record[key]) is not int:
        raise _Drift()
    return record[key]


def _string(record: Mapping, key: str, *, nullable: bool = False, cap: int = STRING_CAP):
    if key not in record:
        return NOT_PROVIDED
    value = record[key]
    if value is None and nullable:
        return None
    if type(value) is not str:
        raise _Drift()
    return _text(value, cap)


def _required_id(record: Mapping, key: str) -> _Text:
    value = record.get(key)
    if type(value) is not str or not value:
        raise _Drift()
    return _text(value)


def _bounded_list(value: Any, bound: int = MAX_LIST_ITEMS) -> list:
    if type(value) is not list:
        raise _Drift()
    if len(value) > bound:
        raise _Drift("list_exceeds_bound")
    return value


def _strings(value: Any) -> list[str]:
    items = _bounded_list(value)
    if any(type(item) is not str for item in items):
        raise _Drift()
    return items


def project_current_user(result: Any) -> dict[str, Any]:
    """Validate ``auth/current_user``; the id stays in-process for corroboration."""

    if type(result) is not dict:
        raise _Drift()
    user_id = result.get("id")
    is_admin = result.get("is_admin")
    credentials = result.get("credentials")
    if type(user_id) is not str or not user_id or type(is_admin) is not bool:
        raise _Drift()
    _bounded_list(credentials)
    modules = []
    for module in _bounded_list(result.get("mfa_modules")):
        if type(module) is not dict or type(module.get("enabled")) is not bool:
            raise _Drift()
        modules.append({"id": _required_id(module, "id"),
                        "name": _string(module, "name", cap=TITLE_CAP),
                        "enabled": module["enabled"]})
    return {"id": user_id, "is_admin": is_admin, "credentials_empty": not credentials,
            "mfa_modules": modules}


def project_users(result: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    """Return the section and an in-process raw id -> system_generated map."""

    if type(result) is not list:
        raise _Drift()
    if len(result) > MAX_USERS:
        raise _Drift("too_many_users")
    users = []
    system_generated: dict[str, bool] = {}
    for ordinal, record in enumerate(result, start=1):
        if type(record) is not dict:
            raise _Drift()
        raw_id = record.get("id")
        if type(raw_id) is not str or not raw_id or raw_id in system_generated:
            raise _Drift()
        is_owner = _bool(record, "is_owner")
        is_active = _bool(record, "is_active")
        generated = _bool(record, "system_generated")
        system_generated[raw_id] = generated is True
        groups: dict[str, Any] | str = NOT_PROVIDED
        if "group_ids" in record:
            group_ids = set(_strings(record["group_ids"]))
            groups = {
                "admin": GROUP_ADMIN in group_ids,
                "user": GROUP_USER in group_ids,
                "read_only": GROUP_READ_ONLY in group_ids,
                "other_group_count": len(group_ids - {GROUP_ADMIN, GROUP_USER, GROUP_READ_ONLY}),
            }
        # Core auth/models.py:92-96, never guessed from a missing input.
        if is_owner is True:
            is_admin: bool | str = True
        elif is_owner is False and type(is_active) is bool and type(groups) is dict:
            is_admin = is_active and groups["admin"]
        else:
            is_admin = NOT_PROVIDED
        credential_types: list | str = NOT_PROVIDED
        if "credentials" in record:
            kinds = set()
            for credential in _bounded_list(record["credentials"]):
                if type(credential) is not dict or type(credential.get("type")) is not str:
                    raise _Drift()
                kinds.add(credential["type"])
            credential_types = [_text(kind) for kind in sorted(kinds)]
        users.append({
            "pseudonym": f"user-{ordinal}",
            "is_owner": is_owner,
            "is_admin": is_admin,
            "is_active": is_active,
            "local_only": _bool(record, "local_only"),
            "system_generated": generated,
            "groups": groups,
            "credential_types": credential_types,
        })
    return {"user_count": len(users), "users": users}, system_generated


def _network_classes(values: list[str]) -> dict[str, Any]:
    loopback = private = public = False
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise _Drift() from None
        if network.is_loopback:
            loopback = True
        elif network.is_private:
            private = True
        else:
            public = True
    return {"count": len(values), "includes_loopback": loopback,
            "includes_private": private, "includes_public": public}


def project_http_slot(slot: Mapping) -> dict[str, Any]:
    """Positive per-slot projection; paths, origins, CIDRs and error text dropped."""

    if type(slot) is not dict:
        raise _Drift()
    for key in ("ssl_certificate", "ssl_peer_certificate"):
        if key in slot and type(slot[key]) is not str:
            raise _Drift()
    if "server_host" in slot:
        hosts = _strings(slot["server_host"])
        bind: dict[str, Any] = {"bind": "explicit", "explicit_bind_count": len(hosts),
                                "explicit_bind_includes_wildcard": any(h in WILDCARD_BINDS for h in hosts)}
    else:
        bind = {"bind": "default"}
    if "error" not in slot:
        error = NOT_PROVIDED
    elif slot["error"] is None:
        error = "none"
    elif type(slot["error"]) is str:
        error = slot["error"] if slot["error"] in HTTP_ERRORS else "other"
    else:
        raise _Drift()
    return {
        "server_port": _int(slot, "server_port"),
        "server_host": bind,
        "tls_configured": "ssl_certificate" in slot,
        "client_cert_required": "ssl_peer_certificate" in slot,
        "ssl_profile": _string(slot, "ssl_profile"),
        "use_x_forwarded_for": _bool(slot, "use_x_forwarded_for"),
        "trusted_proxies": (_network_classes(_strings(slot["trusted_proxies"]))
                            if "trusted_proxies" in slot else NOT_PROVIDED),
        "cors_allowed_origins_count": (len(_strings(slot["cors_allowed_origins"]))
                                       if "cors_allowed_origins" in slot else NOT_PROVIDED),
        "ip_ban_enabled": _bool(slot, "ip_ban_enabled"),
        "login_attempts_threshold": _int(slot, "login_attempts_threshold"),
        "use_x_frame_options": _bool(slot, "use_x_frame_options"),
        "error": error,
    }


def project_http(result: Any) -> tuple[dict[str, Any], str | None]:
    """Return ``(section, partial_reason)``; ``active`` is never substituted."""

    if type(result) is not dict:
        raise _Drift()
    active_type = result.get("active_config_type")
    reason = None
    active: dict[str, Any] | str = UNKNOWN
    if type(active_type) is not str or active_type not in ACTIVE_CONFIG_TYPES:
        reason = "active_config_type_unrecognized"
        active_type = UNKNOWN
    elif active_type == "default_legacy_port":
        reason = "active_slot_not_returned"
    elif active_type not in result:
        reason = "active_slot_missing"
    else:
        selected = result[active_type]
        if type(selected) is not dict or (active_type == "pending" and selected.get("error") is not None):
            reason = "active_slot_inconsistent"
        else:
            active = project_http_slot(selected)
    configured: dict[str, Any] = {"label": CONFIGURED_LABEL}
    configured["stable"] = project_http_slot(result["stable"]) if "stable" in result else NOT_PROVIDED
    if "pending" not in result:
        configured["pending"] = NOT_PROVIDED
    elif result["pending"] is None:
        configured["pending"] = None
    else:
        configured["pending"] = project_http_slot(result["pending"])
    if "revert_at" not in result:
        revert_at: str | None = NOT_PROVIDED
        trial: bool | str = NOT_PROVIDED
    elif result["revert_at"] is None:
        revert_at, trial = None, False
    elif type(result["revert_at"]) is str:
        try:
            parsed = datetime.fromisoformat(result["revert_at"])
        except ValueError:
            raise _Drift() from None
        if parsed.tzinfo is None:
            raise _Drift()
        # Re-rendered from the parsed value; the upstream text is not echoed.
        revert_at, trial = parsed.astimezone(timezone.utc).isoformat(timespec="seconds"), True
    else:
        raise _Drift()
    section = {
        "active_config_type": active_type,
        "active": active,
        "configured": configured,
        "pending_trial_revert_at": revert_at,
        "pending_trial_active": trial,
    }
    if reason is not None:
        section["active_reason"] = reason
    return section, reason


def project_url(config: Mapping, key: str) -> dict[str, Any] | str:
    if key not in config:
        return NOT_PROVIDED
    value = config[key]
    if value is None:
        return {"present": False}
    if type(value) is not str:
        raise _Drift()
    if not value:
        return {"present": False}
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise _Drift() from None
    scheme = parts.scheme.lower()
    scheme = scheme if scheme in DEFAULT_PORTS else "other"
    host = (parts.hostname or "").rstrip(".").lower()
    return {
        "present": True,
        "scheme": scheme,
        "https": scheme == "https",
        "is_nabu_casa_domain": host.endswith(NABU_CASA_SUFFIX) and len(host) > len(NABU_CASA_SUFFIX),
        "uses_default_port": port is None or port == DEFAULT_PORTS.get(scheme),
    }


def project_urls(result: Any) -> tuple[dict[str, Any], str | None]:
    if type(result) is not dict:
        raise _Drift()
    section = {"external_url": project_url(result, "external_url"),
               "internal_url": project_url(result, "internal_url")}
    if "external_url" not in result:
        # get_config pops external_url for a local_only connection user.
        section["external_url"] = {"status": UNKNOWN, "reason": "withheld_for_local_only_user"}
        return section, "withheld_for_local_only_user"
    return section, None


def project_pipelines(result: Any) -> dict[str, Any]:
    if type(result) is not dict:
        raise _Drift()
    pipelines = []
    seen = set()
    for record in _bounded_list(result.get("pipelines")):
        if type(record) is not dict:
            raise _Drift()
        pipeline_id = _required_id(record, "id")
        if pipeline_id in seen:
            raise _Drift()
        seen.add(pipeline_id)
        pipelines.append({
            "id": pipeline_id,
            "name": _string(record, "name", cap=TITLE_CAP),
            "conversation_engine": _string(record, "conversation_engine"),
            "stt_engine": _string(record, "stt_engine", nullable=True),
            "tts_engine": _string(record, "tts_engine", nullable=True),
            "wake_word_entity": _string(record, "wake_word_entity", nullable=True),
            "language": _string(record, "language"),
            "prefer_local_intents": _bool(record, "prefer_local_intents"),
        })
    return {"pipelines": pipelines,
            "preferred_pipeline": _string(result, "preferred_pipeline", nullable=True)}


def project_agents(result: Any) -> list[dict[str, Any]]:
    if type(result) is not dict:
        raise _Drift()
    agents = []
    for record in _bounded_list(result.get("agents")):
        if type(record) is not dict:
            raise _Drift()
        agents.append({"id": _required_id(record, "id"),
                       "name": _string(record, "name", cap=TITLE_CAP)})
    return agents


def project_panels(result: Any) -> dict[str, Any]:
    if type(result) is not dict:
        raise _Drift()
    if len(result) > MAX_LIST_ITEMS:
        raise _Drift("list_exceeds_bound")
    panels = []
    for record in result.values():
        if type(record) is not dict:
            raise _Drift()
        url_path = _required_id(record, "url_path")
        component = _string(record, "component_name")
        addon: Any = NOT_PROVIDED
        if component == "app":
            config = record.get("config")
            if type(config) is dict and type(config.get("addon")) is str and config["addon"]:
                addon = _text(config["addon"])
        panel = {
            "url_path": url_path,
            "component_name": component,
            "panel_listing_require_admin": _bool(record, "require_admin"),
            "show_in_sidebar": _bool(record, "show_in_sidebar"),
            "title": _string(record, "title", nullable=True, cap=TITLE_CAP),
        }
        if component == "app":
            panel["addon"] = addon
        panels.append(panel)
    panels.sort(key=lambda panel: str(panel["url_path"]))
    return {"panel_count": len(panels), "panels": panels}


# --- identity -------------------------------------------------------------------


def connection_identity(topology: tuple[str, str], current_user: dict[str, Any] | None,
                        system_generated: dict[str, bool] | None,
                        users_requested: bool) -> dict[str, Any]:
    """Design section 2.5.  Raw ids are compared in-process only."""

    url_source, token_source = topology
    is_admin = current_user["is_admin"] if current_user is not None else NOT_PROVIDED
    supervisor = url_source == "default" and token_source == "supervisor_token"
    alternative = url_source == "ha_url_env" or token_source == "ha_token"
    if url_source not in ("default", "ha_url_env") or token_source not in ("supervisor_token", "ha_token"):
        return {"kind": IDENTITY_UNKNOWN, "is_admin": is_admin, "basis": "token_source_unknown"}
    if current_user is None:
        return {"kind": IDENTITY_UNKNOWN, "is_admin": NOT_PROVIDED, "basis": "current_user_failed"}
    if supervisor:
        corroborated = current_user["is_admin"] is True and current_user["credentials_empty"]
        if users_requested:
            corroborated = (corroborated and system_generated is not None
                            and system_generated.get(current_user["id"]) is True)
        if corroborated:
            return {"kind": IDENTITY_SUPERVISOR, "is_admin": True,
                    "basis": "supervisor_topology_and_core_corroboration"}
        return {"kind": IDENTITY_UNKNOWN, "is_admin": is_admin, "basis": "supervisor_corroboration_failed"}
    if alternative:
        return {"kind": IDENTITY_OTHER, "is_admin": is_admin, "basis": "alternative_connection_configuration"}
    return {"kind": IDENTITY_UNKNOWN, "is_admin": is_admin, "basis": "token_source_unknown"}


def project_mfa(current_user: dict[str, Any], identity: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    modules = [{"id": module["id"], "name": module["name"]} for module in current_user["mfa_modules"]]
    if identity["kind"] == IDENTITY_SUPERVISOR:
        for projected, module in zip(modules, current_user["mfa_modules"]):
            projected["service_user_enrolled"] = module["enabled"]
        return {"modules": modules, "enrolment_scope": IDENTITY_SUPERVISOR}, None
    return {"modules": modules, "connection_user_enrolment": "not_reported",
            "connection_user_enrolment_reason": "connection_identity_not_supervisor"}, \
        "connection_identity_not_supervisor"


# --- report ---------------------------------------------------------------------


def _coverage(status: str, name: str, reason: str | None) -> dict[str, Any]:
    commands = [] if reason == "not_requested" else list(SECTION_COMMANDS[name])
    return {"status": status, "commands": commands, "reason_code": reason}


@dataclass
class _Screen:
    redacted: int = 0
    truncated: int = 0
    collisions: int = 0
    failed_closed: bool = False
    categories: frozenset[str] = frozenset()

    def add(self, result: SanitizationResult) -> Any:
        self.redacted += result.redacted_field_count
        self.truncated += result.truncated_field_count
        self.collisions += result.marker_collision_count
        self.failed_closed = self.failed_closed or result.failed_closed
        self.categories = self.categories | frozenset(result.redaction_categories)
        return result.value

    def result(self) -> SanitizationResult:
        return SanitizationResult(
            value=None, redacted_field_count=self.redacted,
            redaction_categories=tuple(sorted(self.categories)),
            truncated_field_count=self.truncated, failed_closed=self.failed_closed,
            marker_collision_count=self.collisions,
        )


def _collect(value: Any, found: list) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect(item, found)
    elif isinstance(value, _Text):
        found.append(value)


def _replace(value: Any, mapping: dict[int, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, mapping) for item in value]
    if isinstance(value, _Text):
        return mapping[id(value)]
    return value


def sanitize_sections(sections: dict[str, Any], known_secrets: tuple[str, ...]) -> tuple[dict[str, Any], _Screen]:
    """Shared sanitizer over every Core-originated string value (keys are ours)."""

    screen = _Screen()
    found: list[_Text] = []
    _collect(sections, found)
    values = screen.add(sanitize_untrusted_data([str(item) for item in found], known_secrets=known_secrets))
    screen.truncated += sum(1 for item in found if item.capped)
    if type(values) is not list or len(values) != len(found) or any(type(v) is not str for v in values):
        raise SecurityMetadataError("provider_error")
    return _replace(sections, {id(item): value for item, value in zip(found, values)}), screen


def _size(report: dict[str, Any]) -> int:
    return len(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def apply_output_cap(report: dict[str, Any], cap: int = OUTPUT_BYTES) -> dict[str, Any]:
    """Remove whole list items, lowest priority first; no string is ever cut."""

    if _size(report) <= cap:
        return report
    for section, path in TRUNCATION_ORDER:
        container = report["sections"].get(section)
        if type(container) is not dict:
            continue
        items = container.get(path[0])
        if type(items) is not list:
            continue
        coverage = report["coverage"][section]
        while items and _size(report) > cap:
            items.pop()
            coverage["status"] = "partial"
            coverage["reason_code"] = "output_cap"
            coverage["removed_items"] = coverage.get("removed_items", 0) + 1
        if _size(report) <= cap:
            return report
    raise SecurityMetadataError("response_too_large")


def build_report(request: SecurityMetadataRequest, core_version: str, replies: list[Reply],
                 topology: tuple[str, str], known_secrets: tuple[str, ...] = (),
                 observed_at: str | None = None) -> dict[str, Any]:
    plan = read_plan(request)
    if len(replies) != len(plan) or any(r.command != p["type"] for r, p in zip(replies, plan)):
        raise SecurityMetadataError("provider_error")
    by_command: dict[str, list[Reply]] = {}
    for reply in replies:
        by_command.setdefault(reply.command, []).append(reply)

    def one(command: str) -> Reply:
        return by_command[command][0]

    sections: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    counts: dict[str, int] = {}

    def failed(name: str, reason: str) -> None:
        sections[name] = None
        coverage[name] = _coverage("failed", name, reason)

    current_reply = one(CURRENT_USER)
    current_user = None
    current_reason = current_reply.reason
    if current_reply.ok:
        try:
            current_user = project_current_user(current_reply.result)
        except _Drift as drift:
            current_reason = drift.reason

    system_generated = None
    if "users" in request.sections:
        reply = one(AUTH_LIST)
        if not reply.ok:
            failed("users", reply.reason)
        else:
            try:
                sections["users"], system_generated = project_users(reply.result)
                coverage["users"] = _coverage("observed", "users", None)
                counts["users"] = sections["users"]["user_count"]
            except _Drift as drift:
                failed("users", drift.reason)
    identity = connection_identity(topology, current_user, system_generated, "users" in request.sections)
    system_generated = None  # raw ids end here

    if "mfa" in request.sections:
        if current_user is None:
            failed("mfa", current_reason)
        else:
            sections["mfa"], reason = project_mfa(current_user, identity)
            coverage["mfa"] = _coverage("partial" if reason else "observed", "mfa", reason)
            counts["mfa_modules"] = len(sections["mfa"]["modules"])
    current_user = None

    for name, command, projector in (("http_boundary", HTTP_CONFIG, project_http),
                                     ("urls", GET_CONFIG, project_urls)):
        if name not in request.sections:
            continue
        reply = one(command)
        if not reply.ok:
            failed(name, reply.reason)
            continue
        try:
            sections[name], reason = projector(reply.result)
            coverage[name] = _coverage("partial" if reason else "observed", name, reason)
        except _Drift as drift:
            failed(name, drift.reason)

    if "assist" in request.sections:
        reasons: list[str] = []
        expose_new: dict[str, Any] = {}
        for assistant, reply in zip(KNOWN_ASSISTANTS, by_command[EXPOSE_NEW_GET]):
            value = None
            reason = reply.reason
            if reply.ok:
                result = reply.result
                if type(result) is dict and type(result.get("expose_new")) is bool:
                    value = result["expose_new"]
                else:
                    reason = "unexpected_shape"
            if reason is not None:
                reasons.append(reason)
                expose_new[assistant] = {"status": "failed", "reason_code": reason}
            else:
                expose_new[assistant] = value
        assist: dict[str, Any] = {"expose_new_entities": expose_new}
        for key, command, projector in (("pipelines", PIPELINE_LIST, project_pipelines),
                                        ("agents", AGENT_LIST, project_agents)):
            reply = one(command)
            reason = reply.reason
            if reply.ok:
                try:
                    projected = projector(reply.result)
                except _Drift as drift:
                    reason = drift.reason
            if reason is not None:
                reasons.append(reason)
                assist[key] = []
                assist[f"{key}_status"] = {"status": "failed", "reason_code": reason}
                if key == "pipelines":
                    assist["preferred_pipeline"] = NOT_PROVIDED
            elif key == "pipelines":
                assist["pipelines"] = projected["pipelines"]
                assist["preferred_pipeline"] = projected["preferred_pipeline"]
                counts["pipelines"] = len(projected["pipelines"])
            else:
                assist["agents"] = projected
                counts["agents"] = len(projected)
        if len(reasons) == 5:
            failed("assist", reasons[0] if len(set(reasons)) == 1 else "component_failed")
        else:
            sections["assist"] = assist
            if not reasons:
                coverage["assist"] = _coverage("observed", "assist", None)
            else:
                coverage["assist"] = _coverage(
                    "partial", "assist", reasons[0] if len(set(reasons)) == 1 else "component_failed")

    if "panels" in request.sections:
        reply = one(GET_PANELS)
        if not reply.ok:
            failed("panels", reply.reason)
        else:
            try:
                sections["panels"] = project_panels(reply.result)
                coverage["panels"] = _coverage("observed", "panels", None)
                counts["panels"] = sections["panels"]["panel_count"]
            except _Drift as drift:
                failed("panels", drift.reason)

    for name in SECTIONS:
        if name not in request.sections:
            sections[name] = None
            coverage[name] = _coverage("not_provided", name, "not_requested")

    sanitized, screen = sanitize_sections({name: sections[name] for name in SECTIONS}, known_secrets)
    summary = screen.result()
    text_partial = screen.redacted > 0 or screen.truncated > 0 or screen.failed_closed or screen.collisions > 0
    report = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "core_version": core_version,
        "requested_sections": list(request.sections),
        "connection_identity": identity,
        "sections": sanitized,
        "coverage": {name: coverage[name] for name in SECTIONS},
        "omissions": dict(OMISSIONS),
        "permission_limits": dict(PERMISSION_LIMITS),
        "related_tools": dict(RELATED_TOOLS),
        "sanitization_failed_closed": screen.failed_closed,
        "redaction": redaction_summary(summary),
        "truncated_field_count": screen.truncated,
        "item_counts": counts,
    }
    report = apply_output_cap(report)
    requested = [report["coverage"][name]["status"] for name in request.sections]
    report["completeness"] = ("complete" if all(status == "observed" for status in requested)
                              and not text_partial else "partial")
    return report


def audit_summary(request: SecurityMetadataRequest | None, sent: list[str],
                  report: dict[str, Any] | None, failure: str | None) -> dict[str, Any]:
    """Sections, statuses, reason codes, command names and counts only."""

    summary: dict[str, Any] = {
        "requested_sections": list(request.sections) if request else [],
        "commands_sent": list(sent),
        "command_count": len(sent),
        "result": "failed" if failure else report["completeness"],
    }
    if failure:
        summary["reason_code"] = failure
    if report is not None:
        summary["section_statuses"] = {name: cov["status"] for name, cov in report["coverage"].items()}
        summary["section_reason_codes"] = {name: cov["reason_code"] for name, cov in report["coverage"].items()
                                           if cov["reason_code"] is not None}
        summary["item_counts"] = dict(report["item_counts"])
    return summary


class SecurityMetadataService:
    def __init__(self, reader, core_runtime, *, topology: tuple[str, str] = ("unknown", "unknown"),
                 known_secrets: tuple[str, ...] = ()):
        self.reader = reader
        self.core_runtime = core_runtime
        self.topology = (str(topology[0]), str(topology[1]))
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
            raise SecurityMetadataError("authority_unavailable")
        return status.get("generation"), version

    async def metadata(self, request: SecurityMetadataRequest) -> dict[str, Any]:
        telemetry = current_telemetry()
        commands: list[str] = []  # admitted for dispatch
        sent: list[str] = []  # written to the transport
        failure: SecurityMetadataError | None = None
        report = None
        try:
            if type(request) is not SecurityMetadataRequest:
                raise SecurityMetadataError("invalid_arguments")
            if current_caller_id() == "anonymous":
                raise SecurityMetadataError("authority_unavailable")
            authority = self.authority()
            plan = read_plan(request)

            def authorize() -> None:
                if self.authority() != authority:
                    raise SecurityMetadataError("authority_unavailable")

            def on_dispatch(command: str) -> None:
                if (len(commands) >= len(plan) or plan[len(commands)]["type"] != command
                        or command not in COMMANDS):
                    raise SecurityMetadataError("provider_error")  # unreachable by construction
                commands.append(command)

            def on_sent(command: str) -> None:
                if len(sent) >= len(commands) or commands[len(sent)] != command:
                    raise SecurityMetadataError("provider_error")  # unreachable by construction
                sent.append(command)

            try:
                replies = await self.reader.read(
                    core_version=authority[1], plan=plan, authorize=authorize,
                    on_dispatch=on_dispatch, on_sent=on_sent,
                )
                if sent != [payload["type"] for payload in plan]:
                    raise SecurityMetadataError("provider_error")
                report = build_report(request, authority[1], replies, self.topology, self.known_secrets)
            finally:
                if commands:
                    METRICS.record_provider_result(
                        PROVIDER, "failed" if report is None else report["completeness"], dispatched=True,
                    )
            if self.authority() != authority:
                raise SecurityMetadataError("authority_unavailable")
        except asyncio.CancelledError:
            raise
        except SecurityMetadataError as exc:
            failure = exc
        except Exception:
            # Exception text and provider values never enter the result.
            failure = SecurityMetadataError("provider_error")
        if telemetry is not None:
            telemetry.audit_context["security_metadata_summary"] = audit_summary(
                request if type(request) is SecurityMetadataRequest else None, sent,
                None if failure else report, failure.reason if failure else None,
            )
        if failure is not None:
            raise SecurityMetadataError(failure.reason) from None
        if telemetry is not None:
            telemetry.completeness = report["completeness"]
            telemetry.result_status = "partial" if report["completeness"] == "partial" else "success"
        report["commands_sent"] = len(sent)
        return report


class SecurityMetadataRuntime:
    def __init__(self) -> None:
        self.service: SecurityMetadataService | None = None

    def configure(self, reader, core_runtime, settings) -> None:
        self.service = SecurityMetadataService(
            reader, core_runtime,
            topology=(getattr(settings, "ha_url_source", "unknown"),
                      getattr(settings, "ha_token_source", "unknown")),
            known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> SecurityMetadataService:
        if self.service is None:
            raise SecurityMetadataError("authority_unavailable")
        return self.service


SECURITY_METADATA = SecurityMetadataRuntime()


__all__ = [
    "AGENT_LIST",
    "AUTH_LIST",
    "COMMANDS",
    "CORE_REQUIREMENTS",
    "CURRENT_USER",
    "EXPOSE_NEW_GET",
    "FRAME_BYTES",
    "GET_CONFIG",
    "GET_PANELS",
    "HTTP_CONFIG",
    "KNOWN_ASSISTANTS",
    "MAX_COMMANDS",
    "MAX_USERS",
    "NEVER_SENT_COMMANDS",
    "NEVER_SENT_PREFIXES",
    "NOT_PROVIDED",
    "OMISSIONS",
    "OUTPUT_BYTES",
    "PAYLOADS",
    "PENDING_ADMISSION_REASON",
    "PIPELINE_LIST",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "Reply",
    "SCHEMA_VERSION",
    "SECTIONS",
    "SECURITY_METADATA",
    "SecurityMetadataError",
    "SecurityMetadataReader",
    "SecurityMetadataRequest",
    "SecurityMetadataRuntime",
    "SecurityMetadataService",
    "apply_output_cap",
    "audit_summary",
    "build_report",
    "classify_reply",
    "connection_identity",
    "read_plan",
    "validate_arguments",
]
