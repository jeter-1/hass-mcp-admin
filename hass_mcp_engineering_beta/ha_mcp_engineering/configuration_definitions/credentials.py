"""Credential text withheld from every string of the final report (HAMCP-132 H1).

Two rules, by where the text comes from.

**Core free text** (a config entry title, and any other text copied from a UI
config entry or another non-YAML source) is displayed only through a
positive allowlist (``display_text``, correction batch 2): the complete
original value is shown verbatim only when it is printable ASCII with no
control character, within its length bound without truncation, and carries
no ``@``, no ``://``, no password-like ``key=value`` or ``key: value``, no
known secret in any letter case and no credential text. Credential text is
replaced with ``WITHHELD_MARKER``; anything else the allowlist does not clear
with ``UNVERIFIABLE_MARKER`` (an ordinary non-ASCII title included, by
design). The check always reads the full original value; nothing is
truncated, normalized or otherwise transformed before it is cleared.

**Every string of the final envelope** (keys and values, YAML-projected
definitions included) is screened at the single exit point
(``ConfigurationDefinitionsService._finish``) before the strict sanitizer
runs, and each string that reads as credential text is replaced with
``WITHHELD_MARKER``:

- a URI with userinfo (a user name, with or without a password, before the host);
- a database or broker connection URI by scheme (``db_url``-shaped, a sqlite
  path included), whether or not it carries a password;
- a user name and password before a host, without a scheme;
- a connection-string password field (``Password=``, ``pwd=``);
- a known secret (the access secret or the Home Assistant token);
- text the reader's canonicalization refuses (it cannot be screened);
- text containing the marker itself, so the marker is never ambiguous.

**Invisible text is never screened** (correction batch 3, R2-H1). A string or
key that contains an invisible, format or control character
(``unverifiable_text``) cannot be screened reliably: a zero-width character
inside ``password=`` hides the credential word from every pattern. Such a
value is withheld whole as ``UNVERIFIABLE_MARKER`` (a key: its entry is
dropped), never screened and shown. The characters are the Unicode categories
Cc (except tab, line feed and carriage return), Cf, Cs, Co, Cn, Zl and Zp,
plus every Default_Ignorable_Code_Point (zero-width characters, bidi
controls, variation selectors, tag characters, the Hangul fillers). The check
reads the original text and each round of a bounded percent-decode
(``MAX_DECODE_ROUNDS``). It runs before credential screening, in the YAML
projection and again over every key and string at the exit point. An emoji
written with a variation selector or a zero-width joiner is therefore
withheld too, by design.

**Any withholding makes the report partial** (correction batch 3, R2-M1).
At the exit point, one scan of the final envelope (``withheld_items``) finds
every withheld form (a ``WITHHELD_MARKER`` or ``UNVERIFIABLE_MARKER``
string, a ``{"withheld": <kind>}`` value, a non-empty ``withheld`` path list,
a dropped key) and names each affected item (``withheld:definitions[3]``) in
a bounded omission list; any such omission makes the report partial.

The checks read the reader's canonical form (percent-decoded, NFKC,
casefold) with a bounded confusable fold on top (``fold_confusables``):
the known look-alikes of ``/``, ``:`` and ``@`` that NFKC leaves unchanged
(the division slash or the ratio sign, for example) and the Cyrillic and
Greek letters that look like Latin ones. The fold is a fixed table, not
general confusable coverage; free text outside the YAML path does not rely
on it, because the allowlist refuses every non-ASCII character. A
dictionary entry whose key is credential text is dropped. Any withholding
adds the ``credential_text_withheld`` or ``unverifiable_text_withheld``
omission, so the report is partial.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote_to_bytes

from ..providers.upstream_yaml_reader import MAX_DECODE_ROUNDS, _userinfo, canonical

WITHHELD_MARKER = "[withheld:credential_text]"
OMISSION = "credential_text_withheld"
UNVERIFIABLE_MARKER = "[withheld:unverifiable_text]"
UNVERIFIABLE_OMISSION = "unverifiable_text_withheld"
WITHHELD_ITEM_PREFIX = "withheld:"
MAX_WITHHELD_ITEMS = 20
WITHHELD_ITEMS_NOT_LISTED = "withheld_items_not_listed"

# Invisible, format and control characters (R2-H1): these Unicode categories
# (tab, line feed and carriage return excepted) and every
# Default_Ignorable_Code_Point range of Unicode's DerivedCoreProperties.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_ALLOWED_CONTROLS = frozenset("\t\n\r")
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_ASCII_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")

# Look-alikes NFKC and casefold leave unchanged, mapped to the character they
# imitate (Unicode confusables). Read after casefold, so only lowercase
# letters are listed. The known look-alikes of ``@`` (fullwidth U+FF20, small
# U+FE6B) are already mapped by NFKC. The slash and colon are named by code
# point so no literal here reads as a REST path to the fixed-read audit.
_SLASH, _COLON = chr(0x2F), chr(0x3A)
_CONFUSABLE_POINTS = {
    _SLASH: (0x2215, 0x2044, 0x29F8, 0x2571, 0x27CB, 0x1735, 0x2F03, 0x3033, 0x30CE, 0x31D3, 0x4E3F),
    _COLON: (0x2236, 0x02D0, 0x02F8, 0xA789, 0x0589, 0x05C3, 0x0703, 0x0704, 0x16EC, 0x1803, 0x1809,
          0x205A, 0x0903, 0x0A83, 0xA4FD, 0x2802, 0x2E2C),
    "a": (0x0430, 0x03B1, 0x0251, 0x237A),
    "b": (0x0432, 0x03B2, 0x0184, 0x042C),
    "c": (0x0441, 0x03F2, 0x1D04, 0x217D),
    "d": (0x0501, 0x13E7, 0x146F),
    "e": (0x0435, 0x03B5, 0x0451, 0x0454, 0x04BD, 0x1971),
    "g": (0x0261, 0x0581, 0x01E5),
    "h": (0x04BB, 0x043D, 0x0570, 0x13C2),
    "i": (0x0456, 0x03B9, 0x0131, 0x0269, 0x2373),
    "j": (0x0458, 0x03F3),
    "k": (0x043A, 0x03BA, 0x049F),
    "l": (0x04CF, 0x0627, 0x05C0, 0x07CA, 0x2223),
    "m": (0x043C,),
    "n": (0x0578, 0x057C, 0x03B7, 0x043F),
    "o": (0x043E, 0x03BF, 0x03C3, 0x0585, 0x05E1, 0x0D20, 0x101D, 0x10FF, 0x1D0F, 0x2134),
    "p": (0x0440, 0x03C1, 0x03F1, 0x2374),
    "q": (0x051B, 0x0563, 0x0566),
    "r": (0x0433, 0x1D26, 0x2C85),
    "s": (0x0455, 0x01BD, 0xA731),
    "t": (0x0442, 0x03C4, 0x1D1B),
    "u": (0x03C5, 0x057D, 0x028B, 0x1D1C),
    "v": (0x03BD, 0x0475, 0x05D8, 0x1D20, 0x2228),
    "w": (0x0461, 0x051D, 0x03C9, 0x0561, 0x1D21),
    "x": (0x0445, 0x03C7, 0x04B3, 0x166E, 0x2179),
    "y": (0x0443, 0x04AF, 0x03B3, 0x0263, 0x10E7),
    "z": (0x1D22, 0x01B6),
}
_CONFUSABLES = str.maketrans({chr(point): target for target, points in _CONFUSABLE_POINTS.items()
                              for point in points})

# Connection URI schemes whose value is a ``db_url``-shaped connection target,
# with an optional SQLAlchemy driver suffix (``postgresql+psycopg2://``).
_CONNECTION_SCHEME = re.compile(
    r"(?:sqlite|postgres|postgresql|pgsql|psql|mysql|mariadb|mssql|sqlserver|oracle|"
    r"mongodb|redis|rediss|cockroachdb|db2|firebird|sybase|snowflake|clickhouse|duckdb|"
    r"influxdb|amqp|amqps|mqtt|mqtts)(?:\+[a-z0-9_]+)*://"
)
# A user and password before a host: no whitespace, no further colon in the user part.
_USER_PASS_HOST = re.compile(r"[^\s:/@]+:[^\s/@]+@[^\s/@]+")
_PASSWORD_FIELD = re.compile(r"\b(?:password|passwd|pwd)\s*=")
_MARKER_FOLDED = WITHHELD_MARKER.casefold()

# The allowlist for Core free text: printable ASCII only, and no
# password-like ``key=value`` or ``key: value`` (any letters after the
# credential word: ``dbpassword=``, ``api_key: ``, ``Token =``).
_PRINTABLE_ASCII = re.compile(r"[\x20-\x7e]*")
_KEY_VALUE = re.compile(
    r"(?:pass|pwd|\bpw|secret|token|api[ _-]?key|auth|credential|bearer|private[ _-]?key)"
    r"[a-z0-9_-]*\s*[:=]",
    re.IGNORECASE,
)


def fold_confusables(text: str) -> str:
    """``text`` with the fixed confusable table applied (after casefold)."""

    return text.translate(_CONFUSABLES)


def _invisible_character(character: str) -> bool:
    if character in _ALLOWED_CONTROLS:
        return False
    if unicodedata.category(character) in _INVISIBLE_CATEGORIES:
        return True
    point = ord(character)
    return any(low <= point <= high for low, high in _DEFAULT_IGNORABLE)


def _invisible(text: str) -> bool:
    if text.isascii():
        return _ASCII_CONTROL.search(text) is not None
    return any(_invisible_character(character) for character in text)


def unverifiable_text(value: str) -> bool:
    """Whether ``value`` carries an invisible character, so it cannot be screened.

    The original text is read, then each round of a bounded percent-decode
    (``%E2%80%8B`` is a zero-width space). Text whose decode fails is left to
    ``credential_text``, which refuses it (fail closed).
    """

    text = value
    for _round in range(MAX_DECODE_ROUNDS):
        if _invisible(text):
            return True
        if not _ESCAPE.search(text):
            return False
        try:
            text = unquote_to_bytes(text).decode("utf-8", "strict")
        except (UnicodeError, ValueError):
            return False
    return _invisible(text)


def credential_text(value: str, known_secrets: tuple[str, ...] = ()) -> bool:
    """Whether ``value`` must not reach the caller."""

    if any(secret and secret in value for secret in known_secrets):
        return True
    try:
        text, _rounds = canonical(value)
    except Exception:
        # Text the canonicalization refuses cannot be screened (fail closed).
        return True
    text = fold_confusables(text)
    return (
        _userinfo(text)
        or _CONNECTION_SCHEME.search(text) is not None
        or _USER_PASS_HOST.search(text) is not None
        or _PASSWORD_FIELD.search(text) is not None
        or _MARKER_FOLDED in text
        or any(secret and fold_confusables(secret.casefold()) in text for secret in known_secrets)
    )


def display_text(value: str, max_length: int, known_secrets: tuple[str, ...] = ()) -> str:
    """Core free text as shown: ``value`` itself only when the allowlist clears it.

    The complete original ``value`` is checked; a value over ``max_length``
    is withheld whole, never truncated into something that would pass.
    Credential text, a known secret in any letter case included
    (``credential_text`` compares casefolded), is withheld first.
    """

    if credential_text(value, known_secrets):
        return WITHHELD_MARKER
    if (len(value) > max_length or not _PRINTABLE_ASCII.fullmatch(value)
            or "@" in value or "://" in value or _KEY_VALUE.search(value) is not None):
        return UNVERIFIABLE_MARKER
    return value


def contains(value: Any, marker: str) -> bool:
    """Whether ``marker`` is any string (key or value) of ``value``."""

    if type(value) is str:
        return value == marker
    if type(value) is dict:
        return any(key == marker or contains(child, marker) for key, child in value.items())
    if type(value) in (list, tuple):
        return any(contains(child, marker) for child in value)
    return False


def withhold(value: Any, known_secrets: tuple[str, ...] = (),
             dropped: list[tuple[tuple, str]] | None = None) -> tuple[Any, int]:
    """(copy of ``value`` with credential and invisible text withheld, number
    of credential texts withheld).

    Invisible text (``unverifiable_text``) is checked first and becomes
    ``UNVERIFIABLE_MARKER``; credential text becomes ``WITHHELD_MARKER``. A
    key of either kind drops its entry, and (the entry's path, the marker it
    would have had) is appended to ``dropped``: no marker is left behind, so
    ``withheld_items`` and the exit point read it from there.
    """

    count = 0

    def walk(item: Any, path: tuple) -> Any:
        nonlocal count
        if type(item) is str:
            if unverifiable_text(item):
                return UNVERIFIABLE_MARKER
            if credential_text(item, known_secrets):
                count += 1
                return WITHHELD_MARKER
            return item
        if type(item) is dict:
            kept = {}
            for key, child in item.items():
                if type(key) is str and unverifiable_text(key):
                    if dropped is not None:
                        dropped.append((path + ("<key>",), UNVERIFIABLE_MARKER))
                    continue
                if type(key) is str and credential_text(key, known_secrets):
                    count += 1
                    if dropped is not None:
                        dropped.append((path + ("<key>",), WITHHELD_MARKER))
                    continue
                kept[key] = walk(child, path + (key,))
            return kept
        if type(item) in (list, tuple):
            return [walk(child, path + (index,)) for index, child in enumerate(item)]
        return item

    return walk(value, ()), count


def _withheld_form(item: Any) -> bool:
    if type(item) is str:
        return item in (WITHHELD_MARKER, UNVERIFIABLE_MARKER)
    if type(item) is dict:
        return item.get("withheld") not in (None, "", [], ())
    return False


def _item_name(path: tuple) -> str:
    if not path:
        return "envelope"
    if len(path) > 1 and type(path[1]) is int:
        return f"{path[0]}[{path[1]}]"
    return str(path[0])


def withheld_items(envelope: dict, dropped: list[tuple[tuple, str]] = (),
                   skip: tuple[str, ...] = ("omissions",)) -> list[str]:
    """The bounded omissions naming each item of ``envelope`` with anything withheld.

    One scan of the final envelope finds every withheld form: a
    ``WITHHELD_MARKER`` or ``UNVERIFIABLE_MARKER`` string, a
    ``{"withheld": <kind>}`` value, a non-empty ``withheld`` path list and
    each entry ``withhold`` dropped. An item is a row of a top-level list
    (``definitions[3]``) or a top-level key. At most ``MAX_WITHHELD_ITEMS``
    are named; the rest are counted (``withheld_items_not_listed:<n>``).
    """

    names: list[str] = []

    def note(path: tuple) -> None:
        name = _item_name(path)
        if name not in names:
            names.append(name)

    def scan(item: Any, path: tuple) -> None:
        if _withheld_form(item):
            note(path)
        if type(item) is dict:
            for key, child in item.items():
                scan(child, path + (key,))
        elif type(item) in (list, tuple):
            for index, child in enumerate(item):
                scan(child, path + (index,))

    for key, child in envelope.items():
        if key not in skip:
            scan(child, (key,))
    for path, _marker in dropped:
        if path[0] not in skip:
            note(path)
    listed = [WITHHELD_ITEM_PREFIX + name for name in names[:MAX_WITHHELD_ITEMS]]
    if len(names) > MAX_WITHHELD_ITEMS:
        listed.append(f"{WITHHELD_ITEMS_NOT_LISTED}:{len(names) - MAX_WITHHELD_ITEMS}")
    return listed
