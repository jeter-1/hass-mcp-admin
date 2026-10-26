"""Fixed-route Home Assistant REST path construction (HAMCP-200).

Every Engineering REST request names one fixed route template from the
closed ``ROUTE_TEMPLATES`` registry. Caller, provider and Home
Assistant-derived values enter that template only through ``path_segment``:
each value is refused when any decoded form could change the route, and is
otherwise percent-encoded as exactly one path segment.

``rest_path`` is the only way to obtain a ``Route``. A ``Route`` cannot be
constructed, subclassed, copied into a new object or unpickled elsewhere,
and the transports accept nothing else: a plain string (however it was
built) or a forged object is refused before any session, credential or
request exists. ``request_url`` then rebuilds the URL the way the HTTP
client would (including dot-segment normalization) and refuses it unless
the normalized route is exactly the template's route.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, final
from urllib.parse import quote, unquote
import weakref

from yarl import URL

from ..entity_ids import ENTITY_ID_PATTERN, MAX_ENTITY_ID_LENGTH
from ..errors import InvalidRequestError


INVALID_IDENTIFIER_MESSAGE = "The identifier is invalid."
MAX_SEGMENT_LENGTH = 512
MAX_DECODE_ROUNDS = 4

# Literal route segments and query keys are fixed source text, never data.
_ROUTE_SEGMENT = re.compile(r"[a-z0-9_]+\Z")
_QUERY_KEY = re.compile(r"[a-z_]+\Z")
# Separators, dots, query/fragment delimiters and control characters.
_FORBIDDEN = re.compile(r"[./\\?#\x00-\x1f\x7f-\x9f]")
_FORBIDDEN_QUERY_VALUE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Typed segments whose grammar contains interior dots but can never form a
# dot segment ("." or "..") or carry a separator.
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
SEGMENT_KINDS = frozenset({"identifier", "entity_id", "timestamp"})

# Every route an Engineering transport may request. A template is reviewed
# source text here, so a computed string can never become a route.
ROUTE_TEMPLATES = frozenset({
    "/addons/self/info",
    "/addons/self/logs",
    "/config",
    "/config/automation/config/{}",
    "/config/config_entries/options/flow",
    "/config/config_entries/options/flow/{}",
    "/config/core/check_config",
    "/config/script/config/{}",
    "/core/logs",
    "/history/period/{}",
    "/logbook/{}",
    "/services",
    "/services/notify/{}",
    "/states",
    "/states/{}",
    "/template",
})
# HAMCP-232: a route listed here accepts only its reviewed methods; the
# transport refuses any other method before a credential or session exists.
# The options-flow routes are reached only by the helper-options gateway.
ROUTE_METHODS: Mapping[str, frozenset[str]] = MappingProxyType({
    "/config/config_entries/options/flow": frozenset({"POST"}),
    "/config/config_entries/options/flow/{}": frozenset({"DELETE", "POST"}),
})
# Disposable-Core CI harness routes are not runtime routes: they live only
# under ``scripts/`` with their own sender, outside the add-on image.


class UnsafeRequestPathError(InvalidRequestError):
    """A request path could change the fixed route; nothing was sent."""

    def __init__(self, reason: str, *, field: str | None = None):
        details: dict[str, Any] = {"reason": reason}
        if field:
            details["field"] = field
        super().__init__(INVALID_IDENTIFIER_MESSAGE, details=details)


def _decoded_forms(value: str) -> list[str]:
    forms = [value]
    for _ in range(MAX_DECODE_ROUNDS):
        decoded = unquote(forms[-1], errors="strict")
        if decoded == forms[-1]:
            return forms
        forms.append(decoded)
    # Still decoding after the bound: treat as an encoding attack.
    raise UnsafeRequestPathError("excessive_encoding")


def path_segment(value: Any, *, kind: str = "identifier", field: str | None = None) -> str:
    """Return ``value`` encoded as exactly one safe path segment, or refuse."""

    if kind not in SEGMENT_KINDS:
        raise ValueError("unknown path segment kind")
    if type(value) is not str or not value or len(value) > MAX_SEGMENT_LENGTH:
        raise UnsafeRequestPathError("invalid_identifier", field=field)
    try:
        forms = _decoded_forms(value)
    except UnicodeDecodeError:
        raise UnsafeRequestPathError("invalid_identifier", field=field) from None
    except UnsafeRequestPathError:
        raise UnsafeRequestPathError("invalid_identifier", field=field) from None
    if kind == "entity_id":
        if len(value) > MAX_ENTITY_ID_LENGTH or not ENTITY_ID_PATTERN.fullmatch(value):
            raise UnsafeRequestPathError("invalid_identifier", field=field)
    elif kind == "timestamp":
        if not _TIMESTAMP.fullmatch(value):
            raise UnsafeRequestPathError("invalid_identifier", field=field)
    for index, form in enumerate(forms):
        if form in {".", ".."}:
            raise UnsafeRequestPathError("invalid_identifier", field=field)
        # A typed grammar above proved its interior dots cannot form a dot
        # segment; every other dot, and every dot in a decoded form, refuses.
        checked = form.replace(".", "") if index == 0 and kind != "identifier" else form
        if _FORBIDDEN.search(checked):
            raise UnsafeRequestPathError("invalid_identifier", field=field)
    return quote(value, safe="")


_ISSUED: "weakref.WeakValueDictionary[int, Route]" = weakref.WeakValueDictionary()


@final
class Route(str):
    """An encoded REST path bound to the registered template it came from.

    Only ``rest_path`` creates one. It stays a ``str`` so logs and test
    doubles read it as a path, but every ``str`` operation returns a plain
    ``str``, which the transports refuse.
    """

    __slots__ = ("_template", "_values", "_kinds", "_query", "__weakref__")

    def __new__(cls, *args: Any, **kwargs: Any) -> "Route":
        raise TypeError("a Route is built only by rest_path()")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("Route cannot be subclassed")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Route is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Route is immutable")

    def __copy__(self) -> "Route":
        return self

    def __deepcopy__(self, memo: Any) -> "Route":
        return self

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise TypeError("Route cannot be serialized")

    @property
    def template(self) -> str:
        return self._template

    @property
    def values(self) -> tuple[str, ...]:
        return self._values

    @property
    def kinds(self) -> tuple[str, ...]:
        return self._kinds

    @property
    def query(self) -> tuple[tuple[str, str | None], ...]:
        return self._query

    def expected_parts(self) -> tuple[str, ...]:
        """Decoded route segments the template plus values must produce."""

        values = iter(self.values)
        parts = []
        for literal in self.template.split("/")[1:]:
            parts.append(next(values) if literal == "{}" else literal)
        return tuple(parts)


def _issue(
    encoded: str,
    template: str,
    values: tuple[str, ...],
    kinds: tuple[str, ...],
    query: tuple[tuple[str, str | None], ...],
) -> Route:
    route = str.__new__(Route, encoded)
    for name, value in (
        ("_template", template), ("_values", values),
        ("_kinds", kinds), ("_query", query),
    ):
        object.__setattr__(route, name, value)
    _ISSUED[id(route)] = route
    return route


def is_route(path: Any) -> bool:
    """True only for a ``Route`` that ``rest_path`` itself issued."""

    return type(path) is Route and _ISSUED.get(id(path)) is path


def _template_literals(template: Any) -> list[str]:
    if type(template) is not str or not template.startswith("/") or "?" in template:
        raise ValueError("REST route template must be a fixed absolute route")
    literals = template.split("/")[1:]
    for literal in literals:
        if literal != "{}" and not _ROUTE_SEGMENT.fullmatch(literal):
            raise ValueError("REST route template contains an unsafe literal")
    if template not in ROUTE_TEMPLATES:
        raise UnsafeRequestPathError("unregistered_route")
    return literals


def _query_pairs(
    query: Mapping[str, Any] | Iterable[tuple[str, Any]] | None,
    field: str | None,
) -> tuple[tuple[str, str | None], ...]:
    if query is None:
        return ()
    items = query.items() if isinstance(query, Mapping) else query
    pairs = []
    for key, value in items:
        if type(key) is not str or not _QUERY_KEY.fullmatch(key):
            raise ValueError("REST query key must be fixed source text")
        if value is not None and (
            type(value) is not str or len(value) > MAX_SEGMENT_LENGTH
            or _FORBIDDEN_QUERY_VALUE.search(value)
        ):
            raise UnsafeRequestPathError("invalid_identifier", field=field)
        pairs.append((key, value))
    return tuple(pairs)


def _encoded_query(pairs: tuple[tuple[str, str | None], ...]) -> str:
    return "&".join(
        key if value is None else f"{key}={quote(value, safe='')}"
        for key, value in pairs
    )


def rest_path(
    template: str,
    *values: Any,
    kinds: tuple[str, ...] | None = None,
    query: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    field: str | None = None,
) -> Route:
    """Build one REST route from a registered template and guarded values.

    ``template`` must be in ``ROUTE_TEMPLATES`` and uses ``{}`` for each
    value. ``kinds`` optionally names a typed grammar per value.
    """

    if kinds is None:
        kinds = ("identifier",) * len(values)
    pairs = _query_pairs(query, field)
    encoded = _encode(template, values, tuple(kinds), pairs, field)
    return _issue(encoded, template, tuple(values), tuple(kinds), pairs)


def _encode(
    template: str,
    values: tuple[Any, ...],
    kinds: tuple[str, ...],
    pairs: tuple[tuple[str, str | None], ...],
    field: str | None = None,
) -> str:
    literals = _template_literals(template)
    if literals.count("{}") != len(values):
        raise ValueError("REST route template and value count differ")
    if len(kinds) != len(values):
        raise ValueError("REST route kinds and value count differ")
    encoded_values = iter(
        path_segment(value, kind=kind, field=field)
        for value, kind in zip(values, kinds)
    )
    encoded = "/" + "/".join(
        next(encoded_values) if literal == "{}" else literal
        for literal in literals
    )
    if pairs:
        encoded += "?" + _encoded_query(pairs)
    return encoded


def require_route(path: Any, method: Any = None) -> Route:
    """Return ``path`` if ``rest_path`` issued it; refuse anything else.

    A method-bound route (``ROUTE_METHODS``) is refused for any other method,
    including when no method is named.
    """

    if not is_route(path):
        raise UnsafeRequestPathError("unbound_request_path")
    allowed = ROUTE_METHODS.get(path.template)
    if allowed is not None and method not in allowed:
        raise UnsafeRequestPathError("route_method_not_allowed")
    return path


def request_url(api_url: str, path: Any, method: Any = None) -> URL:
    """Return the exact URL for ``path`` after re-verifying its fixed route.

    Only an issued ``Route`` is accepted. Its template, every bound value
    and its encoding are re-checked, and the URL is parsed the way the HTTP
    client parses a string (requoting and dot-segment normalization). The
    request is refused unless that normalized URL keeps the base origin,
    the base path plus exactly the template's segments, the template's
    query, and no fragment. A method-bound route needs its reviewed method.
    """

    route = require_route(path, method)
    # Central guard: re-check the template, every bound value and the
    # encoding, however the route object came to hold them.
    if str.__str__(route) != _encode(route.template, route.values, route.kinds, route.query):
        raise UnsafeRequestPathError("route_mismatch")
    base = URL(api_url)
    url = URL(api_url.rstrip("/") + str.__str__(route))
    base_parts = tuple(part for part in base.parts if part not in ("", "/"))
    expected = base_parts + route.expected_parts()
    actual = tuple(part for part in url.parts if part != "/")
    expected_query = [
        (key, "" if value is None else value) for key, value in route.query
    ]
    if (
        url.scheme != base.scheme
        or url.raw_host != base.raw_host
        or url.port != base.port
        or url.user is not None
        or url.password is not None
        or url.raw_fragment
        or actual != expected
        or list(url.query.items()) != expected_query
    ):
        raise UnsafeRequestPathError("route_mismatch")
    return url


__all__ = [
    "INVALID_IDENTIFIER_MESSAGE",
    "ROUTE_METHODS",
    "ROUTE_TEMPLATES",
    "Route",
    "UnsafeRequestPathError",
    "is_route",
    "path_segment",
    "request_url",
    "require_route",
    "rest_path",
]
