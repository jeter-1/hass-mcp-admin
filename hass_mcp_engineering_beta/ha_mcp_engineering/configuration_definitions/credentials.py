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

**A fixed policy redaction is not missing evidence** (owner decision, Josh,
2026-10-22). The scan also finds every ``{"redacted_by_policy": <kind>}``
value and treats it as withheld unless ``policy_redaction`` matches it to an
entry of ``POLICY_REDACTIONS``. That allowlist has one entry: a SQL
definition's own ``fields.db_url`` with kind ``database_url``. Every other
withholding still makes the report partial.

**Nothing is derived from withheld text** (owner decision, Josh,
2026-10-22). ``withheld_at_exit`` says whether the exit point would withhold,
redact or truncate a string (the checks above, then the strict sanitizer
with the known secrets). The projection derives no entity ID, unique_id,
dependency, definition ID or legacy name match from an input it withholds
or that this check matches: a slug or a generated ID could otherwise carry
the text past the exit point. The case is listed in the definition's
``withheld``, so the report is partial.

**Whitespace inside a credential is screened too** (correction batch 4).
Tab, line feed and carriage return stay displayable, so a multi-line
template is shown. Every string is screened in two forms: the canonical,
confusable-folded text below, and the same text with every whitespace and
line-control character removed (``squash``: space, tab, line feed, carriage
return, form feed, vertical tab and all Unicode whitespace). Either form
matching withholds the original value whole; ``pass<tab>word=`` and
a line feed inside a URI's user-info, literal or percent-encoded, are withheld. In the
squashed form the password field is matched without a word boundary. Text
with a colon and, later, an ``@`` and no slash between (``x: y @ z``) reads
as a user and password before a host there, so it is withheld (fail closed).

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

**One screen for every caller** (HAMCP-224 H1/H2). ``screen`` is the single
string decision (invisible text first, then credential text) that
``withhold`` applies here and the host journal applies to every returned
journal field. Log text names a credential by a label more often than by a
URI, so a caller may opt in to the **labelled forms** (``labelled=True``):
a credential label (``password``, ``passphrase``, ``pass``, ``pwd``,
``pw``, ``psk``, ``secret``, ``token``, ``api_key``, ``access_key``,
``private_key``, ``network_key``, ``encryption_key``, ``credential(s)``,
``authorization``, with any ``word_`` prefix such as ``mqtt_pass``)
followed by ``=`` or ``:``; a ``Bearer`` token; a PEM private-key header.
They are read in the same canonical, confusable-folded and squashed forms
as the checks above, so ``api%5fkey=``, ``api_<tab>key=`` and a Cyrillic
``е`` in ``key`` are withheld. The configuration-definitions report does
not opt in: its behavior is unchanged.

**The label withholds, not the value's shape** (HAMCP-224 review 2). In
labelled mode a strong keyword alone withholds the text, in the normal or
the squashed form (Unicode dashes folded to ``-``): ``bearer``,
``password``, ``passwd``, ``pwd``, ``--pw``, ``authorization``, ``api
key``/``apikey``/``api_key``, ``secret``, ``token``, ``private key``. So
does the mosquitto password option ``-P`` (``-p`` is the port), decided by
``password_option``: a bounded tokenizer on the decoded, confusable-folded
text in which quotes and backslashes are transparent and whitespace and
``,;:=[](){}<>|&`` separate tokens. A token starting with ``-P`` (``-P x``,
``-Px``, ``-P'x'``, ``"-P" x``, ``-P= x``, an argv list) is the option, and
so is a lone ``-`` followed by the token ``P``, or by a token starting
with ``P`` after anything but plain spaces (``-<tab>P x``); a text over
``MAX_OPTION_SCAN`` characters fails closed. The value shapes above remain
an additional layer. The cost is disclosed, never a leak: ordinary text
that names a keyword (``token refreshed``) or a real ``-P`` option of
another tool (``iptables -P``) is withheld too.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote_to_bytes

from ..providers.upstream_yaml_reader import MAX_DECODE_ROUNDS, _userinfo, canonical, canonical_forms
from ..sanitization import sanitize_untrusted_data
from . import contracts as c

WITHHELD_MARKER = "[withheld:credential_text]"
OMISSION = "credential_text_withheld"
UNVERIFIABLE_MARKER = "[withheld:unverifiable_text]"
UNVERIFIABLE_OMISSION = "unverifiable_text_withheld"
WITHHELD_ITEM_PREFIX = "withheld:"
MAX_WITHHELD_ITEMS = 20
WITHHELD_ITEMS_NOT_LISTED = "withheld_items_not_listed"

# The only policy redactions that leave a report complete, as (integration,
# definition field, kind): the owner decision of 2026-10-22, db_url only.
POLICY_REDACTIONS = frozenset({(c.POLICY_REDACTED_INTEGRATION, "db_url", c.DB_URL_POLICY_KIND)})

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

# Rule ids (HAMCP-234): the fixed, non-secret name of the screening rule
# that withheld a value, reported beside the marker as data. An id is a
# constant of this module (a keyword id is the group name in ``_KEYWORD``),
# never derived from the text, its length or the matched substring. A rule
# met in the squashed form only carries the ``.squashed`` suffix (a colon
# there would read as a ``key: value`` to the strict sanitizer).
RULE_INVISIBLE = "invisible_or_control"
RULE_CANONICAL_REFUSED = "canonical_refused"
RULE_KNOWN_SECRET = "known_secret"
RULE_CREDENTIAL_URL = "credential_url"
RULE_CONNECTION_URI = "connection_uri"
RULE_USER_PASS_HOST = "user_pass_host"
RULE_PASSWORD_FIELD = "password_field"
RULE_MARKER_COLLISION = "marker_collision"
RULE_LABELLED_KEYWORD = "labelled_keyword:"
RULE_PASSWORD_OPTION = "password_option"
RULE_PASSWORD_OPTION_SCAN_LIMIT = "password_option_scan_limit"
RULE_LABELLED_FIELD = "value_shape:labelled_field"
RULE_BEARER_TOKEN = "value_shape:bearer_token"
RULE_PEM_HEADER = "pem_header"
SQUASHED_FORM = ".squashed"
_KEYWORD_NAMES = ("bearer", "password", "passwd", "pwd", "pw_option", "authorization", "api_key",
                  "secret", "token", "private_key")
_FORM_RULES = (RULE_KNOWN_SECRET, RULE_CREDENTIAL_URL, RULE_CONNECTION_URI, RULE_USER_PASS_HOST,
               RULE_PASSWORD_FIELD, RULE_MARKER_COLLISION, RULE_LABELLED_FIELD, RULE_BEARER_TOKEN,
               RULE_PEM_HEADER) + tuple(RULE_LABELLED_KEYWORD + name for name in _KEYWORD_NAMES)
RULE_IDS = frozenset(
    (RULE_INVISIBLE, RULE_CANONICAL_REFUSED, RULE_PASSWORD_OPTION, RULE_PASSWORD_OPTION_SCAN_LIMIT)
    + _FORM_RULES + tuple(rule + SQUASHED_FORM for rule in _FORM_RULES))

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
# The squashed form has no word boundaries left (``my pass word=`` squashes
# to ``mypassword=``), so its password field is matched anywhere.
_SQUASHED_PASSWORD_FIELD = re.compile(r"(?:password|passwd|pwd)=")
_MARKER_FOLDED = WITHHELD_MARKER.casefold()

# The labelled forms (``labelled=True``, HAMCP-224), read after casefold and
# the confusable fold. A label is a whole word, optionally after ``word_``
# or ``word-`` prefixes; in the squashed form it is matched anywhere, like
# the squashed password field.
_LABEL = (r"(?:password|passwd|passphrase|pass|pwd|pw|psk|secret|token|api[_-]?key|apikey|"
          r"access[_-]?key|private[_-]?key|network[_-]?key|encryption[_-]?key|credentials?|"
          r"authorization)")
_LABELLED_FIELD = re.compile(r"(?<![a-z0-9])(?:[a-z0-9]+[_-])*" + _LABEL + r"[\"']?\s*[:=]")
_SQUASHED_LABELLED_FIELD = re.compile(_LABEL + r"[\"']?[:=]")
_TOKEN_CHARACTERS = r"[a-z0-9._~+/=-]"
# One threshold in both forms (review 2): ``Be<tab>arer`` and ``Bearer``
# with the same token decide the same.
_BEARER = re.compile(r"\bbearer\s+" + _TOKEN_CHARACTERS + r"{8,}")
_SQUASHED_BEARER = re.compile(r"bearer" + _TOKEN_CHARACTERS + r"{8,}")
_PEM_PRIVATE = re.compile(r"begin[a-z0-9 ]*private ?key")
# The strong credential keywords (HAMCP-224 review 2, owner direction): in
# the labelled forms a keyword alone withholds the value, whatever follows
# it. Matched anywhere, with no word boundary, in both forms; the squashed
# form has no whitespace left, so ``api key`` reads as ``apikey`` there.
#
# Each alternative is a named group, so the rule id (``labelled_keyword:<group
# name>``, HAMCP-234) names the keyword from this fixed pattern and never
# carries the text it matched; the names change nothing in what matches.
_KEYWORD = re.compile(r"(?P<bearer>bearer)|(?P<password>password)|(?P<passwd>passwd)|(?P<pwd>pwd)|"
                      r"(?P<pw_option>--pw)|(?P<authorization>authorization)|(?P<api_key>api[\s_-]*key)|"
                      r"(?P<secret>secret)|(?P<token>token)|(?P<private_key>private[\s_-]*key)")
# Look-alikes of the hyphen-minus that NFKC leaves unchanged (a fixed table,
# like ``_CONFUSABLE_POINTS``), folded in the labelled forms only so the
# default screen is unchanged: ``\u2013P`` and ``\u2212\u2212pw`` read as ``-P`` and ``--pw``.
_DASHES = str.maketrans(dict.fromkeys(
    map(chr, (0x2010, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x2043, 0x02D7, 0x2796, 0x2E3A, 0x2E3B,
              0x058A, 0x05BE, 0x1806, 0x2E17, 0x1428)), "-"))
# Case matters (mosquitto ``-p`` is the port), so ``-P`` is read on the
# decoded NFKC text before casefold, with its uppercase look-alikes mapped.
_UPPER_P = str.maketrans({chr(0x0420): "P", chr(0x03A1): "P", chr(0x13E2): "P", chr(0x146D): "P"})
# The ``-P`` tokenizer (``password_option``): quotes and backslashes join
# the text around them, as a shell does (``-P'x'`` is ``-Px``, ``"-P"`` is
# ``-P``); whitespace and the argv-list and assignment punctuation separate
# tokens (``["pub", "-P", "x"]``, ``-P= x``). The scan is one pass, bounded
# by ``MAX_OPTION_SCAN`` characters; longer text is withheld (fail closed).
_OPTION_QUOTES = frozenset("'\"`\\")
_OPTION_SEPARATORS = frozenset(",;:=[](){}<>|&")
MAX_OPTION_SCAN = 65536

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


def credential_text(value: str, known_secrets: tuple[str, ...] = (), *, labelled: bool = False) -> bool:
    """Whether ``value`` must not reach the caller.

    Two forms of the canonical, confusable-folded text are checked: the text
    itself and its ``squash`` (correction batch 4). Whitespace inside a
    credential (``pass<tab>word=``, a line feed inside a URI's user-info, literal or
    percent-encoded) hides it from the first form only; either form
    matching withholds the original value whole. ``labelled`` adds the
    labelled forms (HAMCP-224), read in the same forms.
    """

    return credential_rule(value, known_secrets, labelled=labelled) is not None


def credential_rule(value: str, known_secrets: tuple[str, ...] = (), *, labelled: bool = False) -> str | None:
    """The id of the first rule that withholds ``value`` (HAMCP-234), or ``None``.

    The one ordered chain ``credential_text`` decides by, so the id never
    changes what is withheld. The id is a fixed name from ``RULE_IDS``.
    """

    return next(_credential_rules(value, known_secrets, labelled), None)


def credential_rules(value: str, known_secrets: tuple[str, ...] = (), *, labelled: bool = False) -> tuple[str, ...]:
    """Every rule of the chain that matches ``value``, in chain order, each once.

    For the calibration counts (HAMCP-234): which rules a withheld entry
    would still meet if its deciding rule were relaxed. The first is
    ``credential_rule``. A canonicalization refusal ends the chain.
    """

    return tuple(dict.fromkeys(_credential_rules(value, known_secrets, labelled, every=True)))


def _credential_rules(value: str, known_secrets: tuple[str, ...], labelled: bool, *, every: bool = False):
    if any(secret and secret in value for secret in known_secrets):
        yield RULE_KNOWN_SECRET
    try:
        text, _rounds = canonical(value)
    except Exception:
        # Text the canonicalization refuses cannot be screened (fail closed).
        yield RULE_CANONICAL_REFUSED
        return
    text = fold_confusables(text)
    secrets = tuple(fold_confusables(secret.casefold()) for secret in known_secrets if secret)
    squashed = squash(text)
    yield from _credential_form_rules(text, secrets, _PASSWORD_FIELD, "")
    yield from _credential_form_rules(squashed, tuple(squash(secret) for secret in secrets),
                                      _SQUASHED_PASSWORD_FIELD, SQUASHED_FORM)
    if labelled:
        yield from _labelled_rules(value, text, squashed, every)


def squash(text: str) -> str:
    """``text`` with every whitespace and line-control character removed.

    Space, tab, line feed, carriage return, form feed, vertical tab and every
    other character ``str.isspace`` accepts (the Unicode whitespace, the
    file, group, record and unit separators, U+0085 included).
    """

    return "".join(character for character in text if not character.isspace())


def _credential_form(text: str, secrets: tuple[str, ...], password_field: re.Pattern) -> bool:
    return next(_credential_form_rules(text, secrets, password_field, ""), None) is not None


def _credential_form_rules(text: str, secrets: tuple[str, ...], password_field: re.Pattern, form: str):
    if _userinfo(text):
        yield RULE_CREDENTIAL_URL + form
    if _CONNECTION_SCHEME.search(text) is not None:
        yield RULE_CONNECTION_URI + form
    if _USER_PASS_HOST.search(text) is not None:
        yield RULE_USER_PASS_HOST + form
    if password_field.search(text) is not None:
        yield RULE_PASSWORD_FIELD + form
    if _MARKER_FOLDED in text:
        yield RULE_MARKER_COLLISION + form
    if any(secret and secret in text for secret in secrets):
        yield RULE_KNOWN_SECRET + form


def password_option(decoded: str) -> bool:
    """Whether ``decoded`` holds a mosquitto ``-P`` option, in any quoting or splitting.

    ``decoded`` is the case-kept NFKC text with the ``-P`` look-alikes
    mapped. A token starting with ``-P`` is the option (``-P``, ``-Px``,
    ``-P'x'``, ``"-P"``, ``-P=x``; ``--P`` and ``-p`` are not). A lone
    ``-`` token followed by a ``P`` token is the option split apart: the
    ``P`` token is ``P`` exactly (``- P x``, ``"-" "P"``, ``- P=x``) or,
    when the text between them is not only spaces (``-<tab>P``,
    ``-<line feed>Px``), any token starting with ``P``. A lone ``-``
    followed by a word after spaces (``Kitchen - Pool``) is not the option.
    """

    if len(decoded) > MAX_OPTION_SCAN:
        return True
    tokens: list[tuple[str, str]] = []  # (token, the separating text before it)
    token: list[str] = []
    gap = ""
    for character in decoded + " ":
        if character in _OPTION_QUOTES:
            continue
        if character.isspace() or character in _OPTION_SEPARATORS:
            if token:
                tokens.append(("".join(token), gap))
                token, gap = [], ""
            gap += character
            continue
        token.append(character)
    for index, (word, _gap) in enumerate(tokens):
        if word.startswith("-P"):
            return True
        if word != "-" or index + 1 == len(tokens):
            continue
        following, between = tokens[index + 1]
        if following == "P" or (following.startswith("P") and set(between) - {" "}):
            return True
    return False


def _labelled_form(value: str, text: str, squashed: str) -> bool:
    return next(_labelled_rules(value, text, squashed, False), None) is not None


def _labelled_rules(value: str, text: str, squashed: str, every: bool):
    try:
        _text, _rounds, decoded = canonical_forms(value)
    except Exception:
        yield RULE_CANONICAL_REFUSED
        return
    text, squashed = text.translate(_DASHES), squashed.translate(_DASHES)
    decoded = fold_confusables(decoded).translate(_UPPER_P).translate(_DASHES)
    # The label itself (review 2): a strong keyword, in either form. The id
    # is the keyword's group name in ``_KEYWORD``, never the matched text.
    for form_text, form in ((text, ""), (squashed, SQUASHED_FORM)):
        matches = _KEYWORD.finditer(form_text) if every else filter(None, (_KEYWORD.search(form_text),))
        for match in matches:
            yield RULE_LABELLED_KEYWORD + match.lastgroup + form
    if len(decoded) > MAX_OPTION_SCAN:
        yield RULE_PASSWORD_OPTION_SCAN_LIMIT
    elif password_option(decoded):
        yield RULE_PASSWORD_OPTION
    # The value shapes, an additional layer.
    yield from _value_shape_rules(text, squashed)


def _value_shape(text: str, squashed: str) -> bool:
    """The labelled value shapes (review 1), kept under the label rule.

    A layer of its own, so it is tested apart from the keywords it overlaps.
    """

    return next(_value_shape_rules(text, squashed), None) is not None


def _value_shape_rules(text: str, squashed: str):
    if _LABELLED_FIELD.search(text) is not None:
        yield RULE_LABELLED_FIELD
    if _SQUASHED_LABELLED_FIELD.search(squashed) is not None:
        yield RULE_LABELLED_FIELD + SQUASHED_FORM
    if _BEARER.search(text) is not None:
        yield RULE_BEARER_TOKEN
    if _SQUASHED_BEARER.search(squashed) is not None:
        yield RULE_BEARER_TOKEN + SQUASHED_FORM
    if _PEM_PRIVATE.search(text) is not None:
        yield RULE_PEM_HEADER
    if _PEM_PRIVATE.search(squashed) is not None:
        yield RULE_PEM_HEADER + SQUASHED_FORM


def screen(value: str, known_secrets: tuple[str, ...] = (), *, labelled: bool = False) -> str | None:
    """The marker that replaces ``value`` whole, or ``None`` when it is shown.

    Invisible text first (``UNVERIFIABLE_MARKER``), then credential text
    (``WITHHELD_MARKER``), always on the complete original ``value``.
    """

    return screen_rule(value, known_secrets, labelled=labelled)[0]


def screen_rule(value: str, known_secrets: tuple[str, ...] = (), *,
                labelled: bool = False) -> tuple[str | None, str | None]:
    """(``screen``'s marker, the id of the rule that decided it), or (``None``, ``None``).

    The same decision as ``screen``: the id is data about it (HAMCP-234),
    a fixed name from ``RULE_IDS`` that never carries any part of ``value``.
    """

    if unverifiable_text(value):
        return UNVERIFIABLE_MARKER, RULE_INVISIBLE
    rule = credential_rule(value, known_secrets, labelled=labelled)
    if rule is not None:
        return WITHHELD_MARKER, rule
    return None, None


def screen_rules(value: str, known_secrets: tuple[str, ...] = (), *, labelled: bool = False) -> tuple[str, ...]:
    """Every rule that matches ``value``: ``invisible_or_control`` first, then the chain.

    Empty exactly when ``screen`` shows ``value``; the first id is
    ``screen_rule``'s. For the calibration counts only.
    """

    rules = credential_rules(value, known_secrets, labelled=labelled)
    return ((RULE_INVISIBLE,) + rules) if unverifiable_text(value) else rules


def withheld_at_exit(value: str, known_secrets: tuple[str, ...] = ()) -> bool:
    """Whether the exit point would withhold, redact or truncate ``value``.

    The same checks ``ConfigurationDefinitionsService._finish`` applies to a
    string: invisible text, credential text with the known secrets, then the
    strict sanitizer (a token, a JWT, a known secret, a marker collision, or
    text over ``MAX_TEXT``). A value derived from such text (a slug, a
    generated ID, a name match) is never produced (owner decision, Josh,
    2026-10-22): the transformation could carry the text past these checks.
    """

    if unverifiable_text(value) or credential_text(value, known_secrets):
        return True
    result = sanitize_untrusted_data(value, known_secrets=known_secrets, max_string=c.MAX_TEXT)
    return bool(result.failed_closed or result.redacted_field_count or result.truncated_field_count
                or result.value != value)


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
            marker = screen(item, known_secrets)
            count += marker == WITHHELD_MARKER
            return item if marker is None else marker
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
        return (item.get("withheld") not in (None, "", [], ())
                or c.POLICY_REDACTION_KEY in item)
    return False


def policy_redaction(envelope: dict, path: tuple, item: Any) -> bool:
    """Whether ``item`` at ``path`` is an allowlisted fixed policy redaction.

    Only ``{"redacted_by_policy": <kind>}`` exactly, at
    ``definitions[<n>].fields.<field>`` of a definition whose integration,
    field and kind are one entry of ``POLICY_REDACTIONS``. Anything else is
    not exempt, so it counts as withheld.
    """

    if type(item) is not dict or len(item) != 1 or type(item.get(c.POLICY_REDACTION_KEY)) is not str:
        return False
    if (len(path) != 4 or path[0] != "definitions" or type(path[1]) is not int
            or path[2] != "fields" or type(path[3]) is not str):
        return False
    definitions = envelope.get("definitions")
    if type(definitions) is not list or not 0 <= path[1] < len(definitions):
        return False
    definition = definitions[path[1]]
    integration = definition.get("integration") if type(definition) is dict else None
    return (integration, path[3], item[c.POLICY_REDACTION_KEY]) in POLICY_REDACTIONS


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
        if _withheld_form(item) and not policy_redaction(envelope, path, item):
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
