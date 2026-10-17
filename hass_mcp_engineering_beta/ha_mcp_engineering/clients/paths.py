"""Fixed-route Home Assistant REST path construction (HAMCP-200).

Every Engineering REST request names one fixed route template. Caller,
provider and Home Assistant-derived values enter that template only through
``path_segment``: each value is refused when any decoded form could change
the route, and is otherwise percent-encoded as exactly one path segment.
``request_url`` then rebuilds the URL the way the HTTP client would
(including dot-segment normalization) and refuses it unless the normalized
route is exactly the template's route. Refusals happen before any request
or credential exists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote, unquote

from yarl import URL

from ..entity_ids import ENTITY_ID_PATTERN, MAX_ENTITY_ID_LENGTH
from ..errors import InvalidRequestError


INVALID_IDENTIFIER_MESSAGE = "The identifier is invalid."
MAX_SEGMENT_LENGTH = 512
MAX_DECODE_ROUNDS = 4

# Literal route segments and query keys are fixed source text, never data.
_ROUTE_SEGMENT = re.compile(r"[a-z0-9_]+\Z")
_QUERY_KEY = re.compile(r"[a-z_]+\Z")
_STATIC_ROUTE = re.compile(r"(?:/[a-z0-9_]+)+\Z")
# Separators, dots, query/fragment delimiters and control characters.
_FORBIDDEN = re.compile(r"[./\\?#\x00-\x1f\x7f-\x9f]")
_FORBIDDEN_QUERY_VALUE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Typed segments whose grammar contains interior dots but can never form a
# dot segment ("." or "..") or carry a separator.
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
SEGMENT_KINDS = frozenset({"identifier", "entity_id", "timestamp"})


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


class RestPath(str):
    """An encoded REST path bound to the fixed route template it came from."""

    template: str
    values: tuple[str, ...]
    kinds: tuple[str, ...]
    query: tuple[tuple[str, str | None], ...]

    def __new__(
        cls,
        encoded: str,
        template: str,
        values: tuple[str, ...],
        kinds: tuple[str, ...],
        query: tuple[tuple[str, str | None], ...],
    ) -> "RestPath":
        instance = super().__new__(cls, encoded)
        instance.template = template
        instance.values = values
        instance.kinds = kinds
        instance.query = query
        return instance

    def expected_parts(self) -> tuple[str, ...]:
        """Decoded route segments the template plus values must produce."""

        values = iter(self.values)
        parts = []
        for literal in self.template.split("/")[1:]:
            parts.append(next(values) if literal == "{}" else literal)
        return tuple(parts)


def _template_literals(template: Any) -> list[str]:
    if type(template) is not str or not template.startswith("/") or "?" in template:
        raise ValueError("REST route template must be a fixed absolute route")
    literals = template.split("/")[1:]
    for literal in literals:
        if literal != "{}" and not _ROUTE_SEGMENT.fullmatch(literal):
            raise ValueError("REST route template contains an unsafe literal")
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
) -> RestPath:
    """Build one REST path from a fixed template and guarded segment values.

    ``template`` uses ``{}`` for each value; literal segments must be fixed
    lowercase route text. ``kinds`` optionally names a typed grammar per value.
    """

    literals = _template_literals(template)
    if literals.count("{}") != len(values):
        raise ValueError("REST route template and value count differ")
    if kinds is None:
        kinds = ("identifier",) * len(values)
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
    pairs = _query_pairs(query, field)
    if pairs:
        encoded += "?" + _encoded_query(pairs)
    return RestPath(encoded, template, tuple(values), tuple(kinds), pairs)


def require_route(path: Any) -> RestPath:
    """Accept a template-built path or a fixed literal route; refuse others."""

    if isinstance(path, RestPath):
        return path
    if type(path) is str and _STATIC_ROUTE.fullmatch(path):
        return RestPath(path, path, (), (), ())
    raise UnsafeRequestPathError("unbound_request_path")


def request_url(api_url: str, path: Any) -> URL:
    """Return the exact URL for ``path`` after re-verifying its fixed route.

    The URL is parsed the way the HTTP client parses a string (requoting and
    dot-segment normalization). The request is refused unless that
    normalized URL keeps the base origin, the base path plus exactly the
    template's segments, the template's query, and no fragment.
    """

    route = require_route(path)
    _template_literals(route.template)
    if len(route.kinds) != len(route.values):
        raise UnsafeRequestPathError("route_mismatch")
    # Central guard: re-check every bound value, however the path was built.
    for value, kind in zip(route.values, route.kinds):
        path_segment(value, kind=kind)
    base = URL(api_url)
    url = URL(api_url.rstrip("/") + str(route))
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
    "RestPath",
    "UnsafeRequestPathError",
    "path_segment",
    "request_url",
    "require_route",
    "rest_path",
]
