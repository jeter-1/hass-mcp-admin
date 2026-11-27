"""Native Home Assistant Core history read for history slices (HAMCP-511).

One fixed ``GET /api/history/period/<start>`` request per slice window:
one validated entity (``filter_entity_id``), an absolute UTC ``end_time``
and the flags ``minimal_response``, ``no_attributes`` and
``significant_changes_only=1``: the query the native ``get_history`` tool
sends for a minimal read, and the recorder options the delegated
``ha_get_history`` route asked for over WebSocket. Engineering builds every
argument; no caller-selected argument, no other route and no fallback.

The read runs only under the tool's own Core lease: the request telemetry
must carry the tool's ``core.basic_rest_read`` authorizer (installed by
``routing.py``), which is checked before and after every Core request, and
the transport checks it again before dispatch. The request is one attempt
(``SINGLE_READ``: no retry, no redirect, body bounded to 1 MiB). A failed
read is this tool's disclosed partial result, never the call's provider
error, so the telemetry error code is restored.

Pinned Core 2026.11.3 representation (``components/history/__init__.py``
sha256 1f6fd903..., ``recorder/history/__init__.py`` sha256 f4a3d79d...,
``recorder/models/state.py`` sha256 795d04b9...,
``recorder/history/const.py`` sha256 87b24fc5...): the body is ``[]`` or a
list holding one non-empty list of rows for the entity. Row 0, and every
row of a ``NEED_ATTRIBUTE_DOMAINS`` entity, is a full state
``{entity_id, state, attributes, last_changed, last_updated}`` with empty
attributes (``no_attributes``) and ``last_changed`` equal to
``last_updated`` (``significant_changes_only``); every later row of other
domains is ``{state, last_changed}`` with the row's update time. The state
prevailing before the start is reported AT the requested start. With
``significant_changes_only`` the recorder also selects the attribute-only
updates of its ``SIGNIFICANT_DOMAINS``; a minimal response folds them into
the previous equal state except for ``NEED_ATTRIBUTE_DOMAINS``, where they
read as repeated states.

Every returned state is sanitized here, before the service truncates or
digests it, as the gateway's response sanitizer did on the delegated route.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..clients import HomeAssistantRestClient
from ..clients.paths import rest_path
from ..clients.single_read import SINGLE_READ
from ..configuration import parse_upstream_dashboard_endpoint
from ..errors import HomeAssistantApiError, HomeAssistantTimeoutError, HomeAssistantUnavailableError
from ..request_context import current_telemetry
from ..sanitization import sanitize_untrusted_data
from . import contracts as c


VERSION = c.NATIVE_VERSION
CONTRACT = ("GET /api/history/period/{start}?end_time&filter_entity_id&minimal_response&no_attributes"
            "&significant_changes_only=1|core-2026.11.3")
PROVENANCE = ("Home Assistant Core REST GET /api/history/period; one entity per request; minimal_response, "
              "no_attributes, significant_changes_only=1; one attempt; 1 MiB body bound; no fallback")
# recorder/history/const.py (Core 2026.11.3, sha256 87b24fc5...): these
# domains keep full rows and equal consecutive states in minimal responses.
NEED_ATTRIBUTE_DOMAINS = frozenset({"climate", "humidifier", "input_datetime", "thermostat", "water_heater"})
# The gateway's response sanitizer string bound on the delegated route.
MAX_SANITIZED_STRING = 2_000

_REQUEST_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00\Z")
_ROW_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?\+00:00\Z")
_FULL_ROW = frozenset({"entity_id", "state", "attributes", "last_changed", "last_updated"})
_MINIMAL_ROW = frozenset({"state", "last_changed"})


class _Invalid(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _Invalid
    return value


def _constant(_: str) -> Any:
    raise _Invalid


def _row_time(value: Any) -> int:
    if type(value) is not str or _ROW_TIME.fullmatch(value) is None:
        raise _Invalid
    ts_us = c.parse_upstream_us(value)
    if ts_us is None:
        raise _Invalid
    return ts_us


def parse_period(text: Any, entity_id: str, start_us: int, end_us: int) -> list[tuple[int, str]] | None:
    """The pinned single-entity rows as ``(timestamp_us, state)``, or ``None``."""

    if type(text) is not str:
        return None
    try:
        body = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        if type(body) is not list or len(body) > 1:
            raise _Invalid
        if not body:
            return []
        rows = body[0]
        if type(rows) is not list or not rows:
            raise _Invalid
        full_rows = entity_id.split(".", 1)[0] in NEED_ATTRIBUTE_DOMAINS
        parsed: list[tuple[int, str]] = []
        for index, row in enumerate(rows):
            if type(row) is not dict:
                raise _Invalid
            if index == 0 or full_rows:
                if (set(row) != _FULL_ROW or row["entity_id"] != entity_id or type(row["attributes"]) is not dict
                        or row["attributes"] or row["last_changed"] != row["last_updated"]):
                    raise _Invalid
                ts_us = _row_time(row["last_updated"])
            else:
                if set(row) != _MINIMAL_ROW:
                    raise _Invalid
                ts_us = _row_time(row["last_changed"])
            state = row["state"]
            if type(state) is not str or not start_us <= ts_us < end_us or (parsed and ts_us < parsed[-1][0]):
                raise _Invalid
            parsed.append((ts_us, state))
    except (ValueError, TypeError, RecursionError):
        return None
    return parsed


class NativeHistoryReader:
    """``await reader.read(...)`` is ``(states, total, partial)`` or a failure reason.

    One reader serves one page (the factory makes a new one per call): it
    caches each window's rows, so the page's verification and scan reads of
    one slice cost one Core request, and counts its Core requests.
    """

    version = VERSION
    contract = CONTRACT
    provenance = PROVENANCE

    def __init__(self, client: Any, *, known_secrets: tuple[str, ...] = ()):
        self.client = client
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.core_reads = 0
        self._windows: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
        self._safe: dict[str, tuple[str, bool]] = {}

    def ready(self) -> bool:
        """The tool's Core authority is installed (no Core call; HAMCP-510)."""

        telemetry = current_telemetry()
        return self.client is not None and telemetry is not None and telemetry.core_dispatch_authorizer is not None

    def current(self) -> bool:
        """The tool's Core lease still authorizes a Core read."""

        telemetry = current_telemetry()
        return self.ready() and telemetry.authorize_core_dispatch() is True

    async def read(self, entity_id: str, start: str, end: str, offset: int, limit: int):
        if (
            not c.valid_entity_id(entity_id)
            or type(start) is not str or _REQUEST_TIME.fullmatch(start) is None
            or type(end) is not str or _REQUEST_TIME.fullmatch(end) is None
            or type(offset) is not int or not 0 <= offset <= 1_000_000
            or type(limit) is not int or not 1 <= limit <= 1_000_000
        ):
            return "representation_invalid"
        key = (entity_id, start, end)
        rows = self._windows.get(key)
        if rows is None:
            rows = await self._fetch(entity_id, start, end)
            if type(rows) is str:
                return rows
            self._windows[key] = rows
        states, partial = [], False
        for ts_us, state in rows[offset:offset + limit]:
            safe = self._sanitize(state)
            if safe is None:
                return "sanitization_failed"
            states.append((ts_us, safe[0]))
            partial = partial or safe[1]
        return states, len(rows), partial

    async def _fetch(self, entity_id: str, start: str, end: str):
        start_us, end_us = c.parse_upstream_us(start), c.parse_upstream_us(end)
        if start_us is None or end_us is None or not start_us < end_us:
            return "representation_invalid"
        if not self.current():
            return "provider_authority_changed"
        telemetry = current_telemetry()
        prior_error = telemetry.error_code
        path = rest_path("/history/period/{}", start, kinds=("timestamp",), query=[
            ("end_time", end), ("filter_entity_id", entity_id), ("minimal_response", None),
            ("no_attributes", None), ("significant_changes_only", "1"),
        ])
        self.core_reads += 1
        token = SINGLE_READ.set(True)
        try:
            text = await self.client.request("GET", path, raw=True)
        except HomeAssistantTimeoutError:
            return "core_read_timeout"
        except HomeAssistantApiError:
            return "core_read_failed"
        except HomeAssistantUnavailableError:
            return "provider_authority_changed" if not self.current() else "core_read_unavailable"
        except Exception:
            return "core_read_unavailable"
        finally:
            SINGLE_READ.reset(token)
            # A failed slice read is this tool's disclosed partial result,
            # not the call's provider error.
            telemetry.error_code = prior_error
        if not self.current():
            return "provider_authority_changed"
        rows = parse_period(text, entity_id, start_us, end_us)
        return "representation_invalid" if rows is None else rows

    def _sanitize(self, state: str) -> tuple[str, bool] | None:
        cached = self._safe.get(state)
        if cached is None:
            result = sanitize_untrusted_data(state, known_secrets=self.known_secrets,
                                             max_string=MAX_SANITIZED_STRING)
            if result.failed_closed or type(result.value) is not str:
                return None
            cached = (result.value, bool(result.redacted_field_count or result.truncated_field_count))
            self._safe[state] = cached
        return cached


def reader_factory(settings: Any) -> Callable[[], NativeHistoryReader]:
    """A new reader per call over one shared REST client; no I/O here.

    The redaction set is the one the gateway's response sanitizer used on
    the delegated route: the add-on access secret, the Core token and the
    upstream endpoint's secret values.
    """

    endpoint = parse_upstream_dashboard_endpoint(settings.upstream_dashboard_mcp_url)
    secrets = tuple(dict.fromkeys(
        value for value in (settings.access_secret, settings.ha_token,
                            *(endpoint.secret_values if endpoint else ()))
        if value
    ))
    client = HomeAssistantRestClient(settings)
    return lambda: NativeHistoryReader(client, known_secrets=secrets)
