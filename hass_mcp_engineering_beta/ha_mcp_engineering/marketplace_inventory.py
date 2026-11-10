"""Bounded, installed-only Marketplace inventory read (HAMCP-290).

``get_marketplace_inventory`` is an Engineering-native read of the Core 2026.11
Marketplace store.  The runtime-only profile ``core.marketplace_read`` is
admitted only by an owner-signed registry; until then the gateway refuses
``pending_signed_registry_admission`` before the application, and this
service independently refuses ``authority_unavailable`` with zero Home
Assistant commands.

Closed vocabulary: exactly two constant payloads, never an argument.

* ``{"type": "marketplace/repositories/list"}``
  (Core 2026.11.0 ``components/marketplace/websocket/repositories.py:62-89``).
* ``{"type": "hacs/repositories/list"}``, on the same authenticated
  connection, only when the Marketplace reply is a well-formed failure whose
  ``error.code`` is exactly ``unknown_command``
  (``components/websocket_api/connection.py:308-314``).  Message text is
  ignored.  Any other failure, including ``not_loaded``, fails closed.

Acquisition is bounded before parsing (frame bytes, frame count, record
count); an oversized, malformed or partially valid store fails closed and no
partial page is ever returned.  Only installed repositories are projected, to
a fixed field set that passes through the shared sanitizer, truncation last.
Query matching runs over sanitized text only, so a search cannot probe text
the sanitizer withheld.  ``backend`` is decided by which command produced the
accepted result, never by upstream self-description.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
import unicodedata

import aiohttp

from .errors import ErrorCode, GovernanceError
from .ha_core_readmission.routes import MARKETPLACE_PROFILE
from .observability import METRICS
from .request_context import current_caller_id, current_telemetry
from .sanitization import SanitizationResult, redaction_summary, sanitize_untrusted_data

PROFILE = MARKETPLACE_PROFILE
CORE_REQUIREMENTS = (PROFILE,)
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PROVIDER = "direct_home_assistant_marketplace"
PROVIDER_CONTRACT = "compiled-core-marketplace-read-v1"
FALLBACK = "hacs_repositories_list_on_exact_unknown_command"
FALLBACK_ERROR_CODE = "unknown_command"

MARKETPLACE_LIST_COMMAND = "marketplace/repositories/list"
HACS_LIST_COMMAND = "hacs/repositories/list"
MARKETPLACE_PAYLOAD: Mapping[str, str] = MappingProxyType({"type": MARKETPLACE_LIST_COMMAND})
HACS_PAYLOAD: Mapping[str, str] = MappingProxyType({"type": HACS_LIST_COMMAND})
COMMANDS = frozenset({MARKETPLACE_LIST_COMMAND, HACS_LIST_COMMAND})
BACKEND_MARKETPLACE = "core_marketplace"
BACKEND_HACS = "hacs"

# Every other command Core 2026.11.0 (74cf5a49) registers for the Marketplace
# (components/marketplace/websocket/{__init__,critical,repositories,
# repository}.py) and every other command the pinned HACS 2.0.5
# custom_components/hacs/websocket/repositories.py registers, plus
# ``hacs/info`` (HAMCP-319 Phase 1 receipt).  None of these strings is ever
# built: the module sends only the two constant payloads above.
NEVER_SENT_COMMANDS = (
    "marketplace/info",
    "marketplace/subscribe",
    "marketplace/github/connect",
    "marketplace/warning/accept",
    "marketplace/critical/list",
    "marketplace/critical/acknowledge",
    "marketplace/repositories/add",
    "marketplace/repositories/clear_new",
    "marketplace/repositories/detect",
    "marketplace/repositories/remove",
    "marketplace/repositories/removed",
    "marketplace/repository/beta",
    "marketplace/repository/ignore",
    "marketplace/repository/info",
    "marketplace/repository/install",
    "marketplace/repository/refresh",
    "marketplace/repository/releases",
    "marketplace/repository/uninstall",
    "marketplace/repository/version",
    "hacs/info",
    "hacs/repositories/add",
    "hacs/repositories/clear_new",
    "hacs/repositories/remove",
    "hacs/repositories/removed",
)

# Acquisition bounds (design §4).  Core 2026.11.0 returned 4,264,900 bytes for
# 6,194 repositories in the HAMCP-319 Phase 1 lane.
FRAME_BYTES = 8_388_608
AUTH_BYTES = 65_536
MAX_FRAMES = 4  # auth_required, auth_ok, at most two command results
MAX_RECORDS = 20_000
MAX_COMMANDS = 2
MAX_SECONDS = 30.0

# Public input bounds.
CATEGORIES = ("integration", "plugin", "template", "theme")
MAX_QUERY = 200
MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_OFFSET = MAX_RECORDS
ARGUMENT_KEYS = frozenset({"query", "category", "limit", "offset"})

# Record projection.
ID_PATTERN = re.compile(r"[0-9]{1,20}\Z", re.ASCII)
FULL_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z", re.ASCII)
CATEGORY_PATTERN = re.compile(r"[a-z_]{1,32}\Z", re.ASCII)
DOMAIN_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)
NAME_CHARS = 200
VERSION_CHARS = 64
DESCRIPTION_CHARS = 300
PROJECTED_FIELDS = (
    "id", "full_name", "name", "category", "domain", "installed_version",
    "available_version", "pending_upgrade", "custom", "description",
)


class MarketplaceError(GovernanceError):
    """Only a closed reason reaches error and audit serialization."""

    CODES = MappingProxyType({
        "authority_unavailable": ErrorCode.PROVIDER_UNAVAILABLE,
        "invalid_arguments": ErrorCode.INVALID_REQUEST,
        "marketplace_not_loaded": ErrorCode.HA_UNAVAILABLE,
        "marketplace_unauthorized": ErrorCode.AUTHORIZATION_FAILURE,
        "marketplace_unavailable": ErrorCode.HA_UNAVAILABLE,
        "response_too_large": ErrorCode.HA_API_ERROR,
        "malformed_response": ErrorCode.HA_API_ERROR,
        "identity_mismatch": ErrorCode.HA_UNAVAILABLE,
        "timeout": ErrorCode.HA_TIMEOUT,
        "transport_unavailable": ErrorCode.HA_UNAVAILABLE,
        "provider_error": ErrorCode.HA_API_ERROR,
    })
    REASONS = frozenset(CODES)

    def __init__(self, reason: str, *, fallback_occurred: bool = False):
        if reason not in self.REASONS:
            reason = "provider_error"
        details: dict[str, Any] = {"reason": reason, "fallback_occurred": fallback_occurred is True}
        if reason == "authority_unavailable":
            details["core_capability"] = PROFILE
        super().__init__(self.CODES[reason], details=details)
        self.reason = reason
        self.fallback_occurred = fallback_occurred is True


@dataclass(frozen=True)
class InventoryQuery:
    query: str = ""
    category: str | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0


def _bounded_int(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise MarketplaceError("invalid_arguments")
    return value


def _query_text(value: Any) -> str:
    if type(value) is not str or len(value) > MAX_QUERY:
        raise MarketplaceError("invalid_arguments")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn") for char in value):
        raise MarketplaceError("invalid_arguments")
    return value.strip()


def validate_arguments(arguments: Any) -> InventoryQuery:
    """Exact keys, closed values; a refused value is never reflected."""

    if type(arguments) is not dict or not set(arguments) <= ARGUMENT_KEYS:
        raise MarketplaceError("invalid_arguments")
    category = arguments.get("category")
    if category is not None and (type(category) is not str or category not in CATEGORIES):
        raise MarketplaceError("invalid_arguments")
    return InventoryQuery(
        query=_query_text(arguments.get("query", "")),
        category=category,
        limit=_bounded_int(arguments.get("limit", DEFAULT_LIMIT), 1, MAX_LIMIT),
        offset=_bounded_int(arguments.get("offset", 0), 0, MAX_OFFSET),
    )


def classify_reply(message: Any, expected_id: int) -> list | None:
    """Return the store list, ``None`` for the exact fallback code, or refuse.

    ``None`` means exactly: a well-formed failure envelope for this command
    whose ``error.code`` is ``unknown_command``.  Nothing else selects the
    fallback.
    """

    if (type(message) is not dict or message.get("type") != "result"
            or type(message.get("id")) is not int or message["id"] != expected_id
            or type(message.get("success")) is not bool):
        raise MarketplaceError("malformed_response")
    if message["success"] is True:
        if "error" in message or "result" not in message or type(message["result"]) is not list:
            raise MarketplaceError("malformed_response")
        if len(message["result"]) > MAX_RECORDS:
            raise MarketplaceError("response_too_large")
        return message["result"]
    if "result" in message:
        raise MarketplaceError("malformed_response")
    error = message.get("error")
    code = error.get("code") if type(error) is dict else None
    if type(code) is not str:
        raise MarketplaceError("malformed_response")
    if code == FALLBACK_ERROR_CODE:
        return None
    if code == "not_loaded":
        raise MarketplaceError("marketplace_not_loaded")
    if code in ("unauthorized", "forbidden"):
        raise MarketplaceError("marketplace_unauthorized")
    raise MarketplaceError("provider_error")


class MarketplaceReader:
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
            telemetry.endpoint_categories.add("marketplace_inventory")

    async def read(
        self, *, core_version: str, authorize: Callable[[], None],
        on_dispatch: Callable[[str], None] = lambda _command: None,
        on_sent: Callable[[str], None] = lambda _command: None,
    ) -> tuple[str, list]:
        """Return ``(backend, store)`` or raise ``MarketplaceError``.

        ``on_dispatch`` admits a command before it is written; ``on_sent``
        records it only after the frame has been written to the transport.
        """

        deadline = min(MAX_SECONDS, float(getattr(self.settings, "ha_timeout_seconds", MAX_SECONDS)))
        if not math.isfinite(deadline) or deadline <= 0:
            raise MarketplaceError("timeout")
        frames = 0
        fallback = False

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise MarketplaceError("malformed_response")
                result[key] = value
            return result

        def constant(_value):
            raise MarketplaceError("malformed_response")

        def float_value(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise MarketplaceError("malformed_response")
            return parsed

        async def no_retry(request, handler):
            try:
                return await handler(request)
            except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
                raise MarketplaceError("transport_unavailable") from None

        async def reject_redirect(*_args):
            raise MarketplaceError("transport_unavailable")

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
                                raise MarketplaceError("malformed_response")
                            frame = await websocket.receive()
                            if (frame.type == aiohttp.WSMsgType.ERROR
                                    and isinstance(frame.data, aiohttp.WebSocketError)
                                    and frame.data.code == aiohttp.WSCloseCode.MESSAGE_TOO_BIG):
                                raise MarketplaceError("response_too_large")
                            if frame.type != aiohttp.WSMsgType.TEXT:
                                raise MarketplaceError("malformed_response")
                            if len(frame.data.encode("utf-8")) > (AUTH_BYTES if auth else FRAME_BYTES):
                                raise MarketplaceError("response_too_large")
                            try:
                                data = json.loads(frame.data, object_pairs_hook=pairs,
                                                  parse_constant=constant, parse_float=float_value)
                            except (ValueError, TypeError, RecursionError):
                                raise MarketplaceError("malformed_response") from None
                            if type(data) is not dict:
                                raise MarketplaceError("malformed_response")
                            return data

                        greeting = await receive(auth=True)
                        if greeting.get("type") != "auth_required":
                            raise MarketplaceError("malformed_response")
                        await websocket.send_json({"type": "auth", "access_token": self.settings.ha_token})
                        auth = await receive(auth=True)
                        if auth.get("type") != "auth_ok":
                            raise MarketplaceError("marketplace_unauthorized")
                        if auth.get("ha_version") != core_version:
                            raise MarketplaceError("identity_mismatch")

                        async def send(command_id: int, payload: Mapping[str, str]):
                            nonlocal fallback
                            authorize()
                            on_dispatch(payload["type"])
                            # With compress=0 aiohttp writes the frame before its
                            # first await; a closing transport raises instead.
                            await websocket.send_json({"id": command_id, **payload})
                            on_sent(payload["type"])
                            fallback = fallback or payload is HACS_PAYLOAD
                            return classify_reply(await receive(), command_id)

                        store = await send(1, MARKETPLACE_PAYLOAD)
                        backend = BACKEND_MARKETPLACE
                        if store is None:
                            backend = BACKEND_HACS
                            store = await send(2, HACS_PAYLOAD)
                            if store is None:
                                raise MarketplaceError("marketplace_unavailable", fallback_occurred=True)
                        authorize()
                        return backend, store
        except MarketplaceError as exc:
            if fallback and not exc.fallback_occurred:
                raise MarketplaceError(exc.reason, fallback_occurred=True) from None
            raise
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            raise MarketplaceError("timeout", fallback_occurred=fallback) from None
        except aiohttp.WSServerHandshakeError as exc:
            reason = "marketplace_unauthorized" if exc.status in (401, 403) else "transport_unavailable"
            raise MarketplaceError(reason, fallback_occurred=fallback) from None
        except (aiohttp.ClientError, OSError, ValueError, TypeError, RecursionError):
            raise MarketplaceError("transport_unavailable", fallback_occurred=fallback) from None
        finally:
            self._record(started, timed_out)


def _strict_bool(record: dict, key: str) -> bool:
    value = record.get(key)
    if type(value) is not bool:
        raise MarketplaceError("malformed_response")
    return value


def _optional_text(record: dict, key: str) -> str | None:
    value = record.get(key)
    if value is None or type(value) is str:
        return value
    raise MarketplaceError("malformed_response")


def _identity(record: dict) -> tuple[str, str, str, str | None]:
    raw_id = record.get("id")
    if type(raw_id) is int and raw_id >= 0:
        raw_id = str(raw_id)
    if type(raw_id) is not str or not ID_PATTERN.fullmatch(raw_id):
        raise MarketplaceError("malformed_response")
    full_name = record.get("full_name")
    if (type(full_name) is not str or not FULL_NAME_PATTERN.fullmatch(full_name)
            or any(part in {".", ".."} for part in full_name.split("/"))):
        raise MarketplaceError("malformed_response")
    category = record.get("category")
    if type(category) is not str or not CATEGORY_PATTERN.fullmatch(category):
        raise MarketplaceError("malformed_response")
    domain = record.get("domain")
    if domain == "":
        domain = None
    if domain is not None and (type(domain) is not str or not DOMAIN_PATTERN.fullmatch(domain)):
        raise MarketplaceError("malformed_response")
    return raw_id, full_name, category, domain


def project_installed(store: Any) -> list[dict[str, Any]]:
    """Validate the whole store envelope, project installed records only.

    Every record must be a mapping with a strict boolean ``installed``; every
    installed record must satisfy the full projection.  Any violation fails
    the whole read.  Repository ids are unique among installed records.
    """

    if type(store) is not list:
        raise MarketplaceError("malformed_response")
    if len(store) > MAX_RECORDS:
        raise MarketplaceError("response_too_large")
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in store:
        if type(record) is not dict:
            raise MarketplaceError("malformed_response")
        if not _strict_bool(record, "installed"):
            continue
        repository_id, full_name, category, domain = _identity(record)
        if repository_id in seen:
            raise MarketplaceError("malformed_response")
        seen.add(repository_id)
        projected.append({
            "id": repository_id,
            "full_name": full_name,
            "name": _optional_text(record, "name"),
            "category": category,
            "domain": domain,
            "installed_version": _optional_text(record, "installed_version"),
            "available_version": _optional_text(record, "available_version"),
            "pending_upgrade": _strict_bool(record, "pending_upgrade"),
            "custom": _strict_bool(record, "custom"),
            "description": _optional_text(record, "description"),
        })
    return projected


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


def sanitize_records(records: list[dict[str, Any]], known_secrets: tuple[str, ...]) -> tuple[list[dict[str, Any]], _Screen]:
    """Shared sanitizer over every projected string, truncation last per field."""

    screen = _Screen()
    bounds = (("id", None), ("full_name", None), ("category", None), ("domain", None),
              ("name", NAME_CHARS), ("installed_version", VERSION_CHARS),
              ("available_version", VERSION_CHARS), ("description", DESCRIPTION_CHARS))
    columns: dict[str, list] = {}
    for key, bound in bounds:
        values = screen.add(sanitize_untrusted_data(
            [record[key] for record in records], known_secrets=known_secrets, max_string=bound,
        ))
        if type(values) is not list or len(values) != len(records):
            raise MarketplaceError("malformed_response")
        columns[key] = values
    sanitized = []
    for index, record in enumerate(records):
        item = {key: columns[key][index] if key in columns else record[key] for key in PROJECTED_FIELDS}
        for key in PROJECTED_FIELDS:
            value = item[key]
            if key in ("pending_upgrade", "custom"):
                if type(value) is not bool:
                    raise MarketplaceError("malformed_response")
            elif value is not None and type(value) is not str:
                raise MarketplaceError("malformed_response")
        sanitized.append(item)
    return sanitized, screen


def _sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (record["full_name"] or "").casefold(), record["id"] or ""


def _matches(record: dict[str, Any], request: InventoryQuery) -> bool:
    if request.category is not None and record["category"] != request.category:
        return False
    if not request.query:
        return True
    needle = request.query.casefold()
    return any(
        isinstance(record[key], str) and needle in record[key].casefold()
        for key in ("full_name", "name", "domain", "description")
    )


def inventory_fingerprint(backend: str, records: list[dict[str, Any]]) -> str:
    material = json.dumps({"backend": backend, "records": records}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def build_page(backend: str, store: Any, request: InventoryQuery,
               known_secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    if backend not in (BACKEND_MARKETPLACE, BACKEND_HACS):
        raise MarketplaceError("provider_error")
    records, screen = sanitize_records(project_installed(store), known_secrets)
    records.sort(key=_sort_key)
    matches = [record for record in records if _matches(record, request)]
    page = matches[request.offset:request.offset + request.limit]
    end = request.offset + len(page)
    has_more = end < len(matches)
    # Shortened text is withheld text: truncation alone makes the page partial.
    text_partial = (screen.redacted > 0 or screen.truncated > 0 or screen.failed_closed
                    or screen.collisions > 0)
    # A page that leaves matches unreturned is not the complete inventory.
    partial_reasons = [reason for reason, present in (("sanitized_text", text_partial),
                                                      ("more_pages", has_more)) if present]
    summary = screen.result()
    return {
        "backend": backend,
        "query": request.query,
        "category": request.category,
        "installed_total": len(records),
        "total_matches": len(matches),
        "offset": request.offset,
        "limit": request.limit,
        "count": len(page),
        "has_more": has_more,
        "next_offset": end if has_more else None,
        "results": page,
        "inventory_fingerprint": inventory_fingerprint(backend, records),
        "completeness": "partial" if partial_reasons else "complete",
        "partial_reasons": partial_reasons,
        "sanitization_failed_closed": screen.failed_closed,
        "redaction": redaction_summary(summary),
        "truncated_field_count": screen.truncated,
    }


class MarketplaceInventoryService:
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
            raise MarketplaceError("authority_unavailable")
        return status.get("generation"), version

    async def inventory(self, request: InventoryQuery) -> dict[str, Any]:
        if type(request) is not InventoryQuery:
            raise MarketplaceError("invalid_arguments")
        if current_caller_id() == "anonymous":
            raise MarketplaceError("authority_unavailable")
        authority = self.authority()
        commands: list[str] = []  # admitted for dispatch
        sent: list[str] = []  # written to the transport

        def authorize() -> None:
            # The admission generation and Core identity hold across the read.
            if self.authority() != authority:
                raise MarketplaceError("authority_unavailable")

        def on_dispatch(command: str) -> None:
            if command not in COMMANDS or len(commands) >= MAX_COMMANDS:
                raise MarketplaceError("provider_error")  # unreachable by construction
            commands.append(command)

        def on_sent(command: str) -> None:
            # Only an admitted command, in admission order, can be sent.
            if len(sent) >= len(commands) or commands[len(sent)] != command:
                raise MarketplaceError("provider_error")  # unreachable by construction
            sent.append(command)

        failure = None
        try:
            backend, store = await self.reader.read(
                core_version=authority[1], authorize=authorize, on_dispatch=on_dispatch,
                on_sent=on_sent,
            )
            page = build_page(backend, store, request, self.known_secrets)
        except asyncio.CancelledError:
            raise
        except MarketplaceError as exc:
            failure = exc
        except Exception:
            # Exception text and provider values never enter the result.
            failure = MarketplaceError("provider_error")
        finally:
            if commands:
                METRICS.record_provider_result(
                    "direct_ha_api", "failed" if failure else "complete", dispatched=True,
                )
        if failure is None:
            try:
                if self.authority() != authority:
                    failure = MarketplaceError("authority_unavailable")
            except MarketplaceError as exc:
                failure = exc
            except Exception:
                failure = MarketplaceError("provider_error")
        if failure is not None:
            # Attribution follows the sent ledger wherever the failure arose:
            # reader, projection, sanitization or the final authority check.
            # A HACS command admitted but rejected before its frame was
            # written was never sent.
            raise MarketplaceError(failure.reason,
                                   fallback_occurred=HACS_LIST_COMMAND in sent) from None
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.completeness = page["completeness"]
            telemetry.result_status = "partial" if page["completeness"] == "partial" else "success"
        page["fallback_occurred"] = backend == BACKEND_HACS
        page["commands_sent"] = len(commands)
        return page


class MarketplaceInventoryRuntime:
    def __init__(self) -> None:
        self.service: MarketplaceInventoryService | None = None

    def configure(self, reader, core_runtime, settings) -> None:
        self.service = MarketplaceInventoryService(
            reader, core_runtime, known_secrets=(settings.access_secret, settings.ha_token),
        )

    def require(self) -> MarketplaceInventoryService:
        if self.service is None:
            raise MarketplaceError("authority_unavailable")
        return self.service


MARKETPLACE_INVENTORY = MarketplaceInventoryRuntime()


__all__ = [
    "ARGUMENT_KEYS",
    "BACKEND_HACS",
    "BACKEND_MARKETPLACE",
    "CATEGORIES",
    "COMMANDS",
    "CORE_REQUIREMENTS",
    "FALLBACK",
    "FALLBACK_ERROR_CODE",
    "FRAME_BYTES",
    "HACS_PAYLOAD",
    "InventoryQuery",
    "MARKETPLACE_INVENTORY",
    "MARKETPLACE_PAYLOAD",
    "MAX_COMMANDS",
    "MAX_RECORDS",
    "MarketplaceError",
    "MarketplaceInventoryRuntime",
    "MarketplaceInventoryService",
    "MarketplaceReader",
    "NEVER_SENT_COMMANDS",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "build_page",
    "classify_reply",
    "inventory_fingerprint",
    "project_installed",
    "sanitize_records",
    "validate_arguments",
]
