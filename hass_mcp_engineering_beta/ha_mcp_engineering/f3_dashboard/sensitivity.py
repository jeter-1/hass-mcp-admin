"""The one dashboard sensitivity rule (HAMCP-477).

The semantic-diff preview redacts exactly what this rule calls sensitive, and
governed dashboard plan creation refuses a changed value this rule calls
sensitive, so the preview and the write guard cannot disagree. The shared
sanitizer stays the detector for every other surface; this rule is wider and
applies only to the values a dashboard operation adds, replaces or removes.

A value is sensitive when it is a string or a number and either a key on its
path is a credential name, or it is a string that carries a credential shape:
a bearer or basic prefix, or a URL whose query names a credential parameter.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from .models import PatchEffect, PatchKind

# Substring fragments, matched against a key with every non-alphanumeric
# character removed, so ``apiKey``, ``api-key`` and ``API_KEY`` all match.
_KEY_FRAGMENTS = (
    "token",
    "password",
    "passwd",
    "passphrase",
    "secret",
    "authorization",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
)
# Whole names, matched against the same squashed key.
_KEY_NAMES = frozenset({"pin", "passcode", "otp", "totp", "xapikey"})
# ``code`` is a credential only directly inside a card of these types.
_CODE_CARD_TYPES = frozenset({"alarm-panel"})
# URL query parameter names that are credentials in addition to the key rule
# (which covers the shared sanitizer's token, password, api key, client
# secret and credential parameters).
_URL_PARAMETER_NAMES = frozenset({"key", "auth", "sig", "signature"})
_URL_PARAMETER_FRAGMENTS = ("signature",)

_SQUASH = re.compile(r"[^a-z0-9]+")
_URL = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|/)[^\s<>\"'()\[\]{}`]*")
_QUERY_PARAMETER = re.compile(r"[?&#;]([^=&#;?\s]+)(?:=|%3[dD])")
# A pointer segment a refusal may name: a list index, or a short name with no
# digits (key material almost always has some). Any other segment is ``*``.
_SHOWN_SEGMENT = re.compile(r"[0-9]{1,6}|[A-Za-z_-]{1,40}")

MAX_SENSITIVE_PATHS = 16


def _squash(text: str) -> str:
    return _SQUASH.sub("", text.lower())


def sensitive_key(key: Any, *, card_type: Any = None) -> bool:
    """Whether a mapping key, or a JSON pointer segment, names a credential."""

    squashed = _squash(str(key))
    if not squashed:
        return False
    if squashed in _KEY_NAMES:
        return True
    if squashed == "code" and card_type in _CODE_CARD_TYPES:
        return True
    return any(fragment in squashed for fragment in _KEY_FRAGMENTS)


def _sensitive_parameter(name: str) -> bool:
    decoded = re.sub(r"(?i)%5f", "_", re.sub(r"(?i)%2d", "-", name))
    squashed = _squash(decoded)
    return (
        sensitive_key(decoded)
        or squashed in _URL_PARAMETER_NAMES
        or any(fragment in squashed for fragment in _URL_PARAMETER_FRAGMENTS)
    )


def sensitive_string(value: str) -> bool:
    """Whether a string carries a credential shape whatever its key."""

    lowered = value.lower()
    if lowered.startswith("bearer ") or lowered.startswith("basic "):
        return True
    for url in _URL.finditer(value):
        for match in _QUERY_PARAMETER.finditer(url.group(0)):
            if _sensitive_parameter(match.group(1)):
                return True
    return False


def pointer_segments(path: str) -> tuple[str, ...]:
    """The decoded segments of an RFC 6901 JSON pointer."""

    if not path:
        return ()
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in path[1:].split("/")
    )


def _pointer(segments: tuple[str, ...]) -> str:
    return "".join(
        "/" + segment.replace("~", "~0").replace("/", "~1") for segment in segments
    )


def _shown_pointer(segments: tuple[str, ...]) -> str:
    return "".join(
        "/" + (segment if _SHOWN_SEGMENT.fullmatch(segment) else "*")
        for segment in segments
    )


def sensitive_leaf(
    segments: tuple[str, ...], value: Any, *, card_type: Any = None
) -> bool:
    """Whether one scalar at ``segments`` is sensitive.

    ``card_type`` is the ``type`` of the mapping that directly holds the
    value; it decides only the last segment's ``code`` rule.
    """

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    last = len(segments) - 1
    if any(
        sensitive_key(segment, card_type=card_type if index == last else None)
        for index, segment in enumerate(segments)
    ):
        return True
    return isinstance(value, str) and sensitive_string(value)


def container_type(configuration: Any, path: str) -> Any:
    """The ``type`` of the mapping that directly holds the node at ``path``.

    Returns ``None`` when the parent is missing or is not a mapping.
    """

    node = configuration
    for segment in pointer_segments(path)[:-1]:
        if isinstance(node, dict):
            node = node.get(segment)
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return None
    if isinstance(node, dict):
        return node.get("type")
    return None


def _walk(
    node: Any, segments: tuple[str, ...], holder_type: Any
) -> Iterator[tuple[str, ...]]:
    if isinstance(node, dict):
        node_type = node.get("type")
        for key, child in node.items():
            yield from _walk(child, (*segments, str(key)), node_type)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _walk(child, (*segments, str(index)), None)
    elif sensitive_leaf(segments, node, card_type=holder_type):
        yield segments


def sensitive_paths(
    value: Any,
    *,
    path: str,
    holder_type: Any = None,
    limit: int = MAX_SENSITIVE_PATHS,
) -> tuple[str, ...]:
    """JSON pointers of the sensitive scalars in the subtree at ``path``.

    ``holder_type`` is :func:`container_type` for ``path``. Pointers name
    structure only, never a value: a segment other than a short plain name or
    index is shown as ``*``. At most ``limit`` are returned.
    """

    found: list[str] = []
    for segments in _walk(value, pointer_segments(path), holder_type):
        found.append(_shown_pointer(segments))
        if len(found) >= limit:
            break
    return tuple(found)


def _suffix_start(effect: PatchEffect, side: str) -> int | None:
    """The list index a side's value starts at when it is a list suffix.

    An array add that displaces items, and an array remove, carry the
    affected suffix of the list (every shifted item) on both sides; the
    approval projection shows each of those items.
    """

    last = pointer_segments(effect.path)[-1:] or ("",)
    if not last[0].isdigit():
        return None
    value = effect.previous_value if side == "previous" else effect.proposed_value
    if not isinstance(value, list):
        return None
    if effect.operation is PatchKind.ADD and effect.previous_present:
        return int(last[0])
    if effect.operation is PatchKind.REMOVE:
        return int(last[0])
    return None


def effect_sensitive_paths(
    effect: PatchEffect,
    *,
    current: Any,
    resulting: Any,
    limit: int = MAX_SENSITIVE_PATHS,
) -> tuple[str, ...]:
    """Pointers of the sensitive values one effect adds, replaces or removes.

    Both sides are judged, each with the holder type from the current and
    from the resulting configuration (an earlier operation of the same plan
    can change a card's type).
    """

    found: list[str] = []
    holder_types = {container_type(current, effect.path), container_type(resulting, effect.path)}
    parent = pointer_segments(effect.path)[:-1]
    for side, present, value in (
        ("previous", effect.previous_present, effect.previous_value),
        ("proposed", effect.proposed_present, effect.proposed_value),
    ):
        if not present:
            continue
        start = _suffix_start(effect, side)
        if start is None:
            roots = [(effect.path, value, holder_types)]
        else:
            roots = [
                (_pointer(parent + (str(start + offset),)), item, {None})
                for offset, item in enumerate(value)
            ]
        for path, node, types in roots:
            for holder_type in types:
                for pointer in sensitive_paths(
                    node, path=path, holder_type=holder_type, limit=limit
                ):
                    if pointer not in found:
                        found.append(pointer)
                    if len(found) >= limit:
                        return tuple(found)
    return tuple(found)
