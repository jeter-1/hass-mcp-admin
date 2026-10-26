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
forms. The journal's labelled keyword mode (``labelled=True``, HAMCP-224) is
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

    return credentials.screen(value, _secrets(known_secrets))


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
