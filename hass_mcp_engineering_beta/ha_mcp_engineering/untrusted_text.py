"""Credential screening for Home Assistant text that analyses return or store.

HAMCP-226 and HAMCP-228: every entity name, alias, excerpt and identifier
that the integrity, dependency and helper scans name, and that an automation
baseline stores or compares, passes the shared configuration-definitions
screen (``credentials.screen``) on its complete original value, before any
bound or sanitizer cuts it. A screened value is replaced whole by the
screen's marker; nothing is derived from it (owner decision, Josh,
2026-10-22).

Home Assistant's own identifiers and names (automation and script IDs and
aliases, entity IDs and names, configuration paths, excerpts) are read with
the screen's **default** mode, the HAMCP-132 shape-based checks: a
credential URL, a known secret, a PEM private key, an invisible or format
character, in the percent-decoded, NFKC, confusable-folded and squashed
forms. A PEM private-key header is checked too (``private_key_header``): the
owner's list names it, and the default mode reads it only in labelled mode,
so it is added here alone, without the labelled keywords. The journal's labelled keyword mode (``labelled=True``, HAMCP-224) is
for third-party log text only, so an ordinary name such as ``Token
refresh`` is shown (owner direction, Josh, 2026-10-26).

The caller decides what a withheld value costs: a withheld identifier drops
its record (the result is partial), a withheld text field keeps its record
with the marker in place of the text.
"""

from __future__ import annotations

from typing import Any, Iterable

from .configuration_definitions import credentials

MARKERS = frozenset({credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER})


def screen(value: str, known_secrets: tuple[str, ...] = ()) -> str | None:
    """The marker that replaces Home Assistant text ``value``, or ``None`` when shown."""

    marker = credentials.screen(value, _secrets(known_secrets))
    if marker is None and credentials.private_key_header(value):
        return credentials.WITHHELD_MARKER
    return marker


def screen_fields(
    record: dict[str, Any],
    *,
    identifier_keys: Iterable[str] = (),
    text_keys: Iterable[str] = (),
    known_secrets: tuple[str, ...] = (),
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """``(record, withheld identifier keys, withheld text keys)``.

    Each named key holding a string (or a list of strings) is screened; a
    withheld value is replaced by its marker in the returned copy. The
    record itself is not changed.
    """

    output = dict(record)
    withheld_identifiers: list[str] = []
    withheld_text: list[str] = []
    for keys, withheld in (
        (identifier_keys, withheld_identifiers),
        (text_keys, withheld_text),
    ):
        for key in keys:
            if key not in output:
                continue
            value, hit = _screen_value(output[key], known_secrets)
            if hit:
                output[key] = value
                withheld.append(key)
    return output, tuple(withheld_identifiers), tuple(withheld_text)


def screen_tree(value: Any, known_secrets: tuple[str, ...] = ()) -> tuple[Any, int]:
    """``(copy, withheld count)``: every string leaf of ``value`` screened.

    Mapping keys are left as they are; callers use it only on results whose
    keys are fixed field names. A withheld leaf is replaced by its marker
    and the record holding it is kept, so a count or relation is not lost.
    A leaf that already is a marker counts as withheld.
    """

    if isinstance(value, str):
        if value in MARKERS:
            # Withheld upstream (extraction screens the original first).
            return value, 1
        marker = screen(value, known_secrets)
        return (value, 0) if marker is None else (marker, 1)
    if isinstance(value, dict):
        output, count = {}, 0
        for key, item in value.items():
            output[key], hits = screen_tree(item, known_secrets)
            count += hits
        return output, count
    if isinstance(value, (list, tuple)):
        items, count = [], 0
        for item in value:
            screened, hits = screen_tree(item, known_secrets)
            items.append(screened)
            count += hits
        return (tuple(items) if isinstance(value, tuple) else items), count
    return value, 0


class EnvelopeKeyWithheld(ValueError):
    """A response mapping key failed the screen; the response is refused whole."""


def screen_envelope(value: Any, known_secrets: tuple[str, ...] = ()) -> tuple[Any, int]:
    """``(copy, newly withheld count)``: the final-envelope backstop.

    HAMCP-226/228 review 1 (H1): each tool of these scans screens its whole
    response once more just before it is returned, so a field added later
    cannot bypass the screens at the sources. Every string leaf is screened;
    a leaf that already is a marker is kept and not counted again. Mapping
    keys are screened too: they are fixed field names, so a withheld key
    means the response shape itself carries Home Assistant text, and
    ``EnvelopeKeyWithheld`` refuses the response instead of renaming it.
    """

    if isinstance(value, str):
        if value in MARKERS:
            return value, 0
        marker = screen(value, known_secrets)
        return (value, 0) if marker is None else (marker, 1)
    if isinstance(value, dict):
        output, count = {}, 0
        for key, item in value.items():
            if isinstance(key, str) and key not in MARKERS and screen(key, known_secrets) is not None:
                raise EnvelopeKeyWithheld("a response key failed the credential screen")
            output[key], hits = screen_envelope(item, known_secrets)
            count += hits
        return output, count
    if isinstance(value, (list, tuple)):
        items, count = [], 0
        for item in value:
            screened, hits = screen_envelope(item, known_secrets)
            items.append(screened)
            count += hits
        return (tuple(items) if isinstance(value, tuple) else items), count
    return value, 0


def envelope_warning(count: int) -> str:
    """The fixed warning for fields only the final-envelope screen withheld."""

    return f"{count} response field(s) were withheld whole by the final credential screen."


def withheld(value: Any) -> bool:
    """Whether ``value`` is (or holds) a screen marker."""

    if isinstance(value, str):
        return value in MARKERS
    if isinstance(value, (list, tuple)):
        return any(withheld(item) for item in value)
    return False


def _screen_value(value: Any, known_secrets) -> tuple[Any, bool]:
    if isinstance(value, str):
        marker = screen(value, known_secrets)
        return (marker, True) if marker is not None else (value, False)
    if isinstance(value, (list, tuple)):
        items = []
        hit = False
        for item in value:
            screened, item_hit = _screen_value(item, known_secrets)
            items.append(screened)
            hit = hit or item_hit
        return (type(value)(items) if isinstance(value, tuple) else items), hit
    return value, False


def _secrets(known_secrets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(secret for secret in known_secrets if isinstance(secret, str) and secret)
