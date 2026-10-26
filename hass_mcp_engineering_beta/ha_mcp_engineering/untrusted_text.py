"""Credential screening for Home Assistant text that analyses return or store.

HAMCP-226 and HAMCP-228: every entity name, alias, excerpt and identifier
that the integrity, dependency and helper scans name, and that an automation
baseline stores or compares, passes the shared configuration-definitions
screen (``credentials.screen``) on its complete original value, before any
bound or sanitizer cuts it. A screened value is replaced whole by the
screen's marker; nothing is derived from it (owner decision, Josh,
2026-10-22).

Two rules, by what the text is:

- **Free text** (a name, an alias, an excerpt, a summary) is read with the
  labelled forms (``labelled=True``): a credential keyword or label, a
  mosquitto ``-P`` option, a bearer value or a private-key header withholds
  it, in any letter case, percent-encoding, confusable or whitespace split.
- **An identifier** (a configuration ID, an entity ID, a source ID, a
  configuration path) made only of ``PLAIN_IDENTIFIER`` characters is read
  with the default screen, so an ordinary ID such as ``token_refresh`` stays
  visible; any other character (whitespace, ``:``, ``=``, ``%``, non-ASCII)
  makes it free text, so ``api_key=...`` is withheld.

The caller decides what a withheld value costs: a withheld identifier drops
its record (the result is partial), a withheld text field keeps its record
with the marker in place of the text.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .configuration_definitions import credentials

PLAIN_IDENTIFIER = re.compile(r"[A-Za-z0-9_./\[\]-]+")
MARKERS = frozenset({credentials.WITHHELD_MARKER, credentials.UNVERIFIABLE_MARKER})


def screen_text(value: str, known_secrets: tuple[str, ...] = ()) -> str | None:
    """The marker that replaces free text ``value``, or ``None`` when shown."""

    return credentials.screen(value, _secrets(known_secrets), labelled=True)


def screen_identifier(value: str, known_secrets: tuple[str, ...] = ()) -> str | None:
    """The marker that replaces identifier ``value``, or ``None`` when shown."""

    labelled = PLAIN_IDENTIFIER.fullmatch(value) is None
    return credentials.screen(value, _secrets(known_secrets), labelled=labelled)


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
    for keys, screen, withheld in (
        (identifier_keys, screen_identifier, withheld_identifiers),
        (text_keys, screen_text, withheld_text),
    ):
        for key in keys:
            if key not in output:
                continue
            value, hit = _screen_value(output[key], screen, known_secrets)
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


def _screen_value(value: Any, screen, known_secrets) -> tuple[Any, bool]:
    if isinstance(value, str):
        marker = screen(value, known_secrets)
        return (marker, True) if marker is not None else (value, False)
    if isinstance(value, (list, tuple)):
        items = []
        hit = False
        for item in value:
            screened, item_hit = _screen_value(item, screen, known_secrets)
            items.append(screened)
            hit = hit or item_hit
        return (type(value)(items) if isinstance(value, tuple) else items), hit
    return value, False


def _secrets(known_secrets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(secret for secret in known_secrets if isinstance(secret, str) and secret)
