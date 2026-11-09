"""One no-redirect client for every ha-mcp SDK session (HAMCP-319 §3.2-3.3).

The endpoint form only decides whether a session may be admitted as ha-mcp
9.1.0. It never selects a transport and never refuses a configuration value:
``configuration.parse_upstream_dashboard_endpoint`` stays the only
configuration-time refusal. Every Engineering SDK session to ha-mcp builds one
:class:`NoRedirectBinding` from the immutable endpoint snapshot and passes
:func:`ha_mcp_no_redirect_client_factory` to the SDK.

Nothing in this module serializes, logs or echoes the configured value, the
classifier result, a host, a path, a query, a status code or ``Location``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import threading
from typing import Any, Awaitable, TypeVar
from urllib.parse import unquote, urlsplit, urlunsplit
import re

import httpx
from mcp.shared._httpx_utils import (
    MCP_DEFAULT_SSE_READ_TIMEOUT,
    MCP_DEFAULT_TIMEOUT,
)

from .mcp import DashboardTransportError


STRICT_FULL = "strict_full"
LEGACY = "legacy"
STRICT_FULL_SELECTOR_RELEASES = frozenset({"9.1.0"})
SELECTOR_KEYWORDS = frozenset({"catalog", "full", "search", "readonly", "default"})
EXPECTED_SELECTOR_SUFFIX = "/catalog/full"

UPSTREAM_REDIRECT_REFUSED = "upstream_redirect_refused"
UPSTREAM_ENDPOINT_REJECTED = "upstream_endpoint_rejected"
UPSTREAM_CATALOG_MODE_UNADMITTED = "upstream_catalog_mode_unadmitted"
UPSTREAM_CATALOG_SELECTOR_INVALID = "upstream_catalog_selector_invalid"
ENDPOINT_REFUSAL_CODES = frozenset(
    {
        UPSTREAM_REDIRECT_REFUSED,
        UPSTREAM_ENDPOINT_REJECTED,
        UPSTREAM_CATALOG_MODE_UNADMITTED,
        UPSTREAM_CATALOG_SELECTOR_INVALID,
    }
)

# Fixed operator remediation (design §3.3). No interpolation, ever.
UPSTREAM_REDIRECT_OPERATOR_MESSAGE = (
    "The ha-mcp endpoint answered with an HTTP redirect. Engineering does not "
    "follow redirects on ha-mcp connections. Set the upstream_dashboard_mcp_url "
    "option to the exact ha-mcp endpoint URL that answers without a redirect, "
    "then restart Engineering."
)
UPSTREAM_CATALOG_MODE_OPERATOR_MESSAGE = (
    "ha-mcp 9.1.0 is admitted only through its full catalog endpoint. Set the "
    "upstream_dashboard_mcp_url option to the ha-mcp endpoint path ending in "
    "/catalog/full, then restart Engineering."
)
OPERATOR_MESSAGES = {
    UPSTREAM_REDIRECT_REFUSED: UPSTREAM_REDIRECT_OPERATOR_MESSAGE,
    UPSTREAM_CATALOG_MODE_UNADMITTED: UPSTREAM_CATALOG_MODE_OPERATOR_MESSAGE,
    UPSTREAM_CATALOG_SELECTOR_INVALID: UPSTREAM_CATALOG_MODE_OPERATOR_MESSAGE,
}

_ALLOWED_METHODS = frozenset({"POST", "GET", "DELETE"})
_BASE_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")

_T = TypeVar("_T")


class UpstreamEndpointRefused(DashboardTransportError):
    """Closed, non-retryable endpoint refusal. It never carries endpoint text."""

    def __init__(self, category: str, *, tool_call_attempted: bool = True):
        if category not in ENDPOINT_REFUSAL_CODES:
            category = UPSTREAM_ENDPOINT_REJECTED
        super().__init__(
            category,
            retryable=False,
            tool_call_attempted=tool_call_attempted,
        )


def endpoint_form_failing_rule(value: str) -> str | None:
    """Return the first failing rule ``S1``-``S4``, or ``None`` when strict.

    Pure and I/O-free (design §3.2 Step 1). Rules are checked in order.
    """

    if not isinstance(value, str) or not value:
        return "S1"
    # S1: printable ASCII without backslash.
    if any(not 0x21 <= ord(ch) <= 0x7E or ch == "\\" for ch in value):
        return "S1"
    # S2: neither the stdlib nor the transport's own parser rewrites it.
    try:
        parts = urlsplit(value)
        if urlunsplit(parts) != value:
            return "S2"
        if str(httpx.URL(value)) != value:
            return "S2"
    except (ValueError, TypeError, httpx.InvalidURL):
        return "S2"
    path = parts.path
    # S3: no percent-encoding anywhere in the path.
    if "%" in path:
        return "S3"
    # S4: 1*( "/" base-seg ) "/catalog/full".
    if not path.endswith(EXPECTED_SELECTOR_SUFFIX):
        return "S4"
    base = path[: -len(EXPECTED_SELECTOR_SUFFIX)]
    if not base.startswith("/"):
        return "S4"
    for segment in base[1:].split("/"):
        if not _BASE_SEGMENT.fullmatch(segment):
            return "S4"
        if segment in {".", ".."}:
            return "S4"
        if segment.lower() in SELECTOR_KEYWORDS:
            return "S4"
    return None


def classify_endpoint_form(value: str) -> str:
    """Return ``strict_full`` only when every rule S1-S4 holds (design §3.2).

    Pure and I/O-free. Runs only on a value Step 0 already accepted; it never
    refuses a value. The first failing rule makes the value ``legacy``.
    """

    return STRICT_FULL if endpoint_form_failing_rule(value) is None else LEGACY


def selector_refusal_code(value: str) -> str:
    """Return the closed refusal code for a non-strict value under 9.1.0."""

    try:
        path = urlsplit(value).path
    except (ValueError, TypeError):
        return UPSTREAM_CATALOG_MODE_UNADMITTED
    for segment in path.split("/"):
        try:
            decoded = unquote(segment, errors="strict")
        except (UnicodeDecodeError, ValueError):
            decoded = segment
        if decoded.lower() in SELECTOR_KEYWORDS:
            return UPSTREAM_CATALOG_SELECTOR_INVALID
        # An encoded separator may hide a keyword inside one raw segment.
        for piece in decoded.replace("\\", "/").split("/"):
            if piece.lower() in SELECTOR_KEYWORDS:
                return UPSTREAM_CATALOG_SELECTOR_INVALID
    return UPSTREAM_CATALOG_MODE_UNADMITTED


@dataclass(frozen=True)
class EndpointSnapshot:
    """Immutable endpoint value, form and configuration generation."""

    url: str = field(repr=False)
    form: str = field(repr=False)
    generation: int = 0
    published: bool = field(default=False, repr=False)

    def __repr__(self) -> str:
        return f"EndpointSnapshot(generation={self.generation})"


_GENERATION_LOCK = threading.Lock()
_CURRENT = {"digest": None, "generation": 0}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def publish_endpoint_snapshot(url: str) -> EndpointSnapshot:
    """Snapshot the configured value for the current configuration generation.

    All providers configured from the same settings share one generation. The
    generation advances only when the configured value changes. Only a digest
    is retained process-wide, never the value.
    """

    digest = _digest(url)
    with _GENERATION_LOCK:
        if _CURRENT["digest"] != digest:
            _CURRENT["digest"] = digest
            _CURRENT["generation"] = int(_CURRENT["generation"]) + 1
        generation = int(_CURRENT["generation"])
    return EndpointSnapshot(
        url=url,
        form=classify_endpoint_form(url),
        generation=generation,
        published=True,
    )


def endpoint_snapshot(value: "EndpointSnapshot | str") -> EndpointSnapshot:
    """Accept a published snapshot, or build an unpublished one from a str."""

    if isinstance(value, EndpointSnapshot):
        return value
    if not isinstance(value, str):
        raise TypeError("endpoint must be an EndpointSnapshot or str")
    return EndpointSnapshot(url=value, form=classify_endpoint_form(value))


def snapshot_is_current(snapshot: EndpointSnapshot) -> bool:
    """Unpublished snapshots (direct construction) carry no generation."""

    if not snapshot.published:
        return True
    with _GENERATION_LOCK:
        return snapshot.generation == _CURRENT["generation"]


class NoRedirectBinding:
    """Per-SDK-session binding: bound URL, snapshot generation, refusal latch."""

    __slots__ = (
        "snapshot",
        "bound_url",
        "generation",
        "factory_installed",
        "client_created",
        "latch_code",
        "latch_phase",
        "delete_used",
        "step_failure",
        "_event",
    )

    def __init__(self, snapshot: EndpointSnapshot):
        self.snapshot = snapshot
        try:
            self.bound_url: httpx.URL | None = httpx.URL(snapshot.url)
        except (httpx.InvalidURL, TypeError, ValueError):
            # A value Step 0 accepts but httpx cannot parse (for example a raw
            # tab) binds nothing: every request is refused before send, and the
            # SDK's own request construction fails exactly as it does today.
            self.bound_url = None
        self.generation = snapshot.generation
        # Set when the no-redirect factory for this binding is built and handed
        # to the SDK; ``client_created`` records the SDK actually calling it.
        self.factory_installed = False
        self.client_created = False
        self.latch_code: str | None = None
        self.latch_phase: str | None = None
        self.delete_used = False
        # The last session step's own exception, kept so a step whose
        # exception an SDK cancel scope absorbed still fails closed.
        self.step_failure: BaseException | None = None
        self._event: asyncio.Event | None = None

    def __repr__(self) -> str:
        return (
            "NoRedirectBinding("
            f"generation={self.generation}, latched={self.latched})"
        )

    @property
    def latched(self) -> bool:
        return self.latch_code is not None

    def _latch_event(self) -> asyncio.Event:
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    def refuse_redirect(self, phase: str = "operation") -> UpstreamEndpointRefused:
        """The only redirect-refusal recorder. First write wins."""

        if self.latch_code is None:
            self.latch_code = UPSTREAM_REDIRECT_REFUSED
            self.latch_phase = "cleanup" if phase == "cleanup" else "operation"
            try:
                self._latch_event().set()
            except RuntimeError:
                pass
        return UpstreamEndpointRefused(self.latch_code)

    def latched_error(self) -> UpstreamEndpointRefused:
        return UpstreamEndpointRefused(self.latch_code or UPSTREAM_ENDPOINT_REJECTED)

    async def request_hook(self, request: httpx.Request) -> None:
        if self.bound_url is None or request.url != self.bound_url:
            raise UpstreamEndpointRefused(UPSTREAM_ENDPOINT_REJECTED)
        method = request.method
        if method not in _ALLOWED_METHODS:
            raise UpstreamEndpointRefused(UPSTREAM_ENDPOINT_REJECTED)
        if self.latch_code is not None:
            if method != "DELETE" or self.delete_used:
                raise self.latched_error()
        if method == "DELETE":
            self.delete_used = True

    async def response_hook(self, response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            phase = "cleanup" if response.request.method == "DELETE" else "operation"
            raise self.refuse_redirect(phase)

    async def run_until_latched(self, awaitable: Awaitable[_T]) -> _T:
        """Race one session step against the latch; the deadline never classifies.

        A latch set by a background SDK task (for example a redirect on
        ``notifications/initialized``) cancels the step at once and raises the
        latched code. Cancellation of the caller's own task propagates.
        """

        if self.latch_code is not None:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise self.latched_error()
        event = self._latch_event()
        task = asyncio.ensure_future(awaitable)
        waiter = asyncio.ensure_future(event.wait())
        try:
            done, _pending = await asyncio.wait(
                (task, waiter), return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            task.cancel()
            waiter.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)
            self._record_step_failure(task)
            raise
        if task in done:
            # No await between the step's completion and its outcome: a
            # cancellation delivered here could otherwise replace it.
            waiter.cancel()
            self._record_step_failure(task)
            # HAMCP-319 (Codex F2): this is the step's linearization point. An
            # operation-phase refusal latched before it wins even when the
            # step succeeded and the latch is ready at the same time. Only a
            # cleanup-phase refusal leaves a successful step's outcome alone.
            if self.latch_code is not None and (
                self.latch_phase != "cleanup"
                or task.cancelled()
                or task.exception() is not None
            ):
                raise self.latched_error()
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise self.latched_error()

    def _record_step_failure(self, task: "asyncio.Future[Any]") -> None:
        if task.done() and not task.cancelled():
            failure = task.exception()
            if failure is not None:
                self.step_failure = failure

    def unfinished_error(self) -> BaseException:
        """Outcome of a session that ended with neither result nor exception.

        An SDK cancel scope can absorb a step's exception and exit normally.
        That is never success: the latch wins, then the step's own recorded
        exception, then a bounded connection failure.
        """

        if self.latch_code is not None:
            return self.latched_error()
        if self.step_failure is not None:
            return self.step_failure
        return DashboardTransportError("connection_failed", retryable=False)


def ha_mcp_no_redirect_client_factory(
    binding: NoRedirectBinding,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    trust_env: bool | None = None,
    refuse_auth: bool = False,
):
    """Return an SDK ``McpHttpClientFactory`` bound to one session binding.

    Settings mirror the SDK default factory (timeout, headers, auth) and add
    the two hooks, ``follow_redirects=False`` and ``max_redirects=0``. The
    analysis session passes its fence transport, ``trust_env=False`` and its
    ``auth`` refusal; nothing else differs between consumers.
    """

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        if refuse_auth and auth is not None:
            raise DashboardTransportError("prohibited_argument", retryable=False)
        if timeout is None:
            timeout = httpx.Timeout(
                MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT
            )
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": False,
            "max_redirects": 0,
            "event_hooks": {
                "request": [binding.request_hook],
                "response": [binding.response_hook],
            },
        }
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        if transport is not None:
            kwargs["transport"] = transport
        if trust_env is not None:
            kwargs["trust_env"] = trust_env
        client = httpx.AsyncClient(**kwargs)
        binding.client_created = True
        return client

    factory.ha_mcp_no_redirect_binding = binding  # type: ignore[attr-defined]
    binding.factory_installed = True
    return factory


def require_selector_agreement(
    release_version: str,
    snapshot: EndpointSnapshot,
    binding: NoRedirectBinding | None,
) -> None:
    """Shared admission check after initialize and before any tools/list.

    Every version: the session's client must carry the no-redirect binding of
    this snapshot generation. 9.1.0 only: the form must be ``strict_full``.
    Other registered versions are unchanged on either form. Generation
    currency of a retained worker is enforced before each reuse by its owner.
    """

    if (
        not isinstance(binding, NoRedirectBinding)
        or binding.snapshot is not snapshot
        or binding.generation != snapshot.generation
        or not binding.factory_installed
    ):
        raise UpstreamEndpointRefused(
            UPSTREAM_ENDPOINT_REJECTED, tool_call_attempted=False
        )
    if binding.latched:
        raise binding.latched_error()
    if release_version in STRICT_FULL_SELECTOR_RELEASES and snapshot.form != STRICT_FULL:
        raise UpstreamEndpointRefused(
            selector_refusal_code(snapshot.url), tool_call_attempted=False
        )


def latched_or_none(binding: NoRedirectBinding | None) -> UpstreamEndpointRefused | None:
    if isinstance(binding, NoRedirectBinding) and binding.latched:
        return binding.latched_error()
    return None


__all__ = [
    "EndpointSnapshot",
    "NoRedirectBinding",
    "OPERATOR_MESSAGES",
    "STRICT_FULL",
    "LEGACY",
    "UPSTREAM_CATALOG_MODE_UNADMITTED",
    "UPSTREAM_CATALOG_SELECTOR_INVALID",
    "UPSTREAM_ENDPOINT_REJECTED",
    "UPSTREAM_REDIRECT_OPERATOR_MESSAGE",
    "UPSTREAM_REDIRECT_REFUSED",
    "UpstreamEndpointRefused",
    "classify_endpoint_form",
    "endpoint_snapshot",
    "ha_mcp_no_redirect_client_factory",
    "latched_or_none",
    "publish_endpoint_snapshot",
    "require_selector_agreement",
    "selector_refusal_code",
    "snapshot_is_current",
]
