"""Central recursive sanitization for untrusted diagnostic data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import threading
import unicodedata
from typing import Any, Iterable
from urllib.parse import unquote


# The marker grammar Engineering emits. HAMCP-191: no pass trusts input text
# of this shape; it is only the form of the markers the sanitizer writes.
REDACTION_MARKER = re.compile(r"\[REDACTED:[a-z0-9_]+\]")
# HAMCP-191 D11: only the stored-plan re-scan still reads these by text.
_STORED_PLAN_REPEATED_MARKER = re.compile(
    r"(\[REDACTED:([a-z0-9_]+)\])(?:\s*\1)+"
)
TRUNCATION_SUFFIX = "...<truncated>"
MAX_REDACTION_CATEGORIES = 16
SANITIZATION_FAILURE_MARKER = "[REDACTED:sanitization_failure]"

_KEY_NORMALIZER = re.compile(r"[^a-z0-9]+")
_TOKEN_KEYS = frozenset(
    {
        "authorization",
        "access_secret",
        "access_token",
        "refresh_token",
        "long_lived_access_token",
        "oauth_token",
        "id_token",
        "session_token",
        "api_key",
        "api_secret",
        "client_secret",
        "secret",
        "token",
        "credential",
        "credentials",
        "authorization_code",
        "auth_code",
        "upstream_dashboard_mcp_url",
    }
)
_PASSWORD_KEYS = frozenset({"password", "passwd", "passphrase"})
_COOKIE_KEYS = frozenset({"cookie", "set_cookie", "auth_cookie", "session_cookie"})
_WEBHOOK_KEYS = frozenset({"webhook_id", "webhook_secret", "webhook_token"})
_AUTH_FLOW_KEYS = frozenset(
    {
        "login_flow_id",
        "login_flow",
        "auth_flow_id",
        "authentication_flow_id",
        "auth_session_id",
        "authentication_session_id",
        "login_session_id",
        "session_id",
        "auth_callback_id",
        "authentication_callback_id",
    }
)
_MATTER_CODE_KEYS = frozenset(
    {
        "setup_code",
        "matter_setup_code",
        "manual_pairing_code",
        "matter_manual_pairing_code",
        "pairing_code",
    }
)
_MATTER_PAYLOAD_KEYS = frozenset(
    {
        "setup_payload",
        "matter_setup_payload",
        "qr_setup_payload",
        "matter_qr_payload",
    }
)

_AUTH_HEADER = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;\]}]+)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]+)")
_COOKIE_HEADER = re.compile(r"(?i)(\b(?:set-cookie|cookie)\s*:\s*)([^\r\n]+)")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?![A-Za-z0-9_-])")
_URL_USERINFO = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_URL_ENCODED_USERINFO = re.compile(r"(?i)(https?://)([^/@\s]+%3A[^/@\s]+)@")
_URL_CREDENTIAL_PARAMETER = re.compile(
    r"(?i)([?&#;](?:access(?:%5[fF]|_)token|refresh(?:%5[fF]|_)token|token|api(?:%5[fF]|_)(?:key|secret)|client(?:%5[fF]|_)secret|password|credential|authorization(?:%5[fF]|_)code|signature|sig|x-amz-signature|x-goog-signature)(?:=|%3[dD]))([^&#;\s]+)"
)
_WEBHOOK_PATH = re.compile(r"(?i)(/api/webhook/)([^/?#\s'\"\]}]+)")
_WEBHOOK_PROSE = re.compile(
    r"(?i)(\b(?:local\s+)?webhook(?:\s+(?:id|identifier))?\s+)([A-Za-z0-9_-]{20,})"
)
_LOGIN_FLOW_PATH = re.compile(r"(?i)(/auth/login_flow/)([^/?#\s'\"\]}]+)")
_MATTER_PAYLOAD = re.compile(r"(?i)\bMT:[A-Z0-9+./_=-]+")


def _serialized_assignment_pattern(keys: frozenset[str]) -> re.Pattern[str]:
    joined = "|".join(sorted((re.escape(key) for key in keys), key=len, reverse=True))
    return re.compile(
        rf"(?i)((?:['\"])?(?:{joined})(?:['\"])?\s*[:=]\s*)(?:['\"]([^'\"]*)['\"]|([^\s,;}}\]]+))"
    )


_SERIALIZED_PATTERNS = (
    (_serialized_assignment_pattern(_MATTER_CODE_KEYS), "matter_setup_code"),
    (_serialized_assignment_pattern(_MATTER_PAYLOAD_KEYS), "matter_setup_payload"),
    (_serialized_assignment_pattern(_AUTH_FLOW_KEYS), "auth_flow"),
    (_serialized_assignment_pattern(frozenset({"webhook_id"})), "webhook_identifier"),
    (_serialized_assignment_pattern(frozenset({"webhook_secret", "webhook_token"})), "webhook_secret"),
    (_serialized_assignment_pattern(_TOKEN_KEYS), "token"),
    (_serialized_assignment_pattern(_PASSWORD_KEYS), "password"),
    (_serialized_assignment_pattern(_COOKIE_KEYS), "auth_cookie"),
)
_MATTER_CODE_LABEL = re.compile(
    r"(?i)(\b(?:matter\s+)?(?:setup|manual pairing|pairing)\s+code\s*[:=]?\s*)([0-9][0-9 -]{5,}[0-9])"
)

# Free-text rules in their fixed order: (pattern, category, form). The form
# says what replaces a match: "prefix" keeps group 1, "whole" keeps nothing,
# "userinfo" keeps group 1 and the "@". Serialized assignments run after them.
_SEGMENT_RULES = (
    (_AUTH_HEADER, "token", "prefix"),
    (_BEARER, "token", "prefix"),
    (_COOKIE_HEADER, "auth_cookie", "prefix"),
    (_JWT, "token", "whole"),
    (_URL_USERINFO, "url_credentials", "userinfo"),
    (_URL_ENCODED_USERINFO, "url_credentials", "userinfo"),
    (_URL_CREDENTIAL_PARAMETER, "url_credentials", "prefix"),
    (_WEBHOOK_PATH, "webhook_identifier", "prefix"),
    (_WEBHOOK_PROSE, "webhook_identifier", "prefix"),
    (_LOGIN_FLOW_PATH, "auth_flow", "prefix"),
    (_MATTER_PAYLOAD, "matter_setup_payload", "whole"),
    (_MATTER_CODE_LABEL, "matter_setup_code", "prefix"),
)


# HAMCP-162 H3 / HAMCP-191 D1, D2: redaction-marker look-alikes in untrusted
# text. Text never decides that something is already redacted; text that a
# client could read as an Engineering marker is replaced whole by this marker.
MARKER_COLLISION_CATEGORY = "redaction_marker_collision"
MARKER_COLLISION = "[REDACTED:redaction_marker_collision]"
MAX_MARKER_DECODE_ROUNDS = 4
# An opener followed by ``[\W_]*`` and a keyword, found in linear time: only
# the start of each maximal non-word run is tried (the lookbehind), the run is
# taken whole (possessive), and the run must hold an opener. The plain
# ``[\[(<{][\W_]*(?:redacted|truncated)`` backtracks quadratically on long
# opener runs, which untrusted text and 64 KiB templates can supply.
_MARKER_RUN = re.compile(r"(?<![\W_])[\W_]++(?=redacted|truncated)")
_MARKER_OPENERS = frozenset("[(<{")


def _marker_run_present(folded: str) -> bool:
    return any(
        not _MARKER_OPENERS.isdisjoint(match.group())
        for match in _MARKER_RUN.finditer(folded)
    )


_MARKER_FOLD = str.maketrans(
    {
        # Opening and closing bracket and colon look-alikes NFKC keeps.
        **dict.fromkeys("\u2045\u27e6\u27e8\u27ea\u27ec\u27ee\u2772\u2989\u298b\u298d"
                        "\u298f\u2991\u2993\u2995\u2997\u3008\u300a\u3010\u3014\u3016"
                        "\u3018\u301a\u2039\u00ab\u2329", "["),
        **dict.fromkeys("\u2046\u27e7\u27e9\u27eb\u27ed\u27ef\u2773\u298a\u298c\u298e"
                        "\u2990\u2992\u2994\u2996\u2998\u3009\u300b\u3011\u3015\u3017"
                        "\u3019\u301b\u203a\u00bb\u232a", "]"),
        **dict.fromkeys("\u02d0\u02f8\ua789\u2236\u0589\u205a\u05c3\u16ec\ufe13", ":"),
        # Letter look-alikes of r, e, d, a, c, t, u and n (after casefold).
        **dict.fromkeys("\u0430\u03b1\u0251\u13aa\u1d00", "a"),
        **dict.fromkeys("\u0441\u03f2\u03f9\u1d04\u13df\u217d", "c"),
        **dict.fromkeys("\u0501\u13a0\u217e\u0257", "d"),
        **dict.fromkeys("\u0435\u03b5\u04bd\u13ac\u1d07\u0454", "e"),
        **dict.fromkeys("\u0578\u057c\u043f\u0274", "n"),
        **dict.fromkeys("\u0433\u1d26\u13a1\u0280", "r"),
        **dict.fromkeys("\u0442\u03c4\u13a2\u1d1b", "t"),
        **dict.fromkeys("\u03c5\u057d\u1d1c\u0446", "u"),
    }
)


def _marker_fold(text: str) -> str:
    # NFKC folds compatibility forms; NFD then splits the precomposed letters
    # NFKC builds so their combining marks can be dropped. The look-alike
    # table applies before normalization and again before and after casefold
    # (NFKC and casefold map some look-alikes, such as U+03F9, off the table).
    if text.isascii():
        # HAMCP-191: the same result for ASCII, where the table, NFKC, NFD and
        # the format/combining filter are all identities and casefold is lower.
        return text.lower()
    folded = text.translate(_MARKER_FOLD)
    folded = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", folded))
    folded = "".join(
        char for char in folded if unicodedata.category(char) not in ("Cf", "Mn", "Me")
    )
    return folded.translate(_MARKER_FOLD).casefold().translate(_MARKER_FOLD)


def marker_like_text(text: str) -> bool:
    """H3: whether ``text`` could read as an Engineering redaction marker.

    Checked on the raw text and up to ``MAX_MARKER_DECODE_ROUNDS``
    percent-decoded forms, each after NFKC, NFD, removal of format and
    combining characters, casefold and a look-alike fold. A bracket-like opener
    followed by ``redacted`` or ``truncated`` matches. Text still changing
    after the bound is treated as marker-like (fail closed).
    """

    value = text
    for _round in range(MAX_MARKER_DECODE_ROUNDS + 1):
        if _marker_run_present(_marker_fold(value)):
            return True
        decoded = unquote(value, errors="replace")
        if decoded == value:
            return False
        value = decoded
    return True


# HAMCP-162 R8: reference-name grammars shared by the YAML reader's minting
# rule and the opt-in revalidation below.
SECRET_REFERENCE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
ENV_VAR_REFERENCE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
REFERENCE_MARKER_DENIED = "[REDACTED:reference_marker]"
REFERENCE_MALFORMED = "[REDACTED:reference_malformed]"


@dataclass(frozen=True)
class ReferenceMarker:
    """Engineering-internal `!secret`/`!env_var` reference (name only).

    Only the YAML reader mints it. Upstream JSON can never construct it, and
    no sanitizer path renders it through ``repr``.
    """

    kind: str
    name: str
    default_redacted: bool = False

    def valid(self) -> bool:
        if type(self.name) is not str or type(self.default_redacted) is not bool:
            return False
        if self.kind == "secret":
            return not self.default_redacted and SECRET_REFERENCE_NAME.fullmatch(self.name) is not None
        if self.kind == "env_var":
            return ENV_VAR_REFERENCE_NAME.fullmatch(self.name) is not None
        return False


_ENGINEERING_MARKER_TEXT = re.compile(
    r"(?:[a-z][a-z0-9+.-]{0,31}://)?\[(?:REDACTED|TRUNCATED):[a-z0-9_]+\]"
)


@dataclass(frozen=True)
class EngineeringMarker:
    """A redaction or truncation marker the YAML reader emitted (H3).

    The text is a source-defined marker, optionally after a scheme the
    reader vetted. L5 keeps it and rendering emits its text only when it is
    the very object the same read's registry minted; marker-shaped text is
    never trusted.
    """

    text: str

    def valid(self) -> bool:
        return type(self.text) is str and _ENGINEERING_MARKER_TEXT.fullmatch(self.text) is not None


class ReferenceMarkerRegistry:
    """The markers one request produced, held by object identity (H2, H3).

    The opt-in exemption trusts a value only when it is the very object this
    registry minted: never a type alone, a mapping shape or a marker-shaped
    string. Upstream data, a truncation shell or a copy cannot be
    registered. Holding each marker keeps its ``id`` from being reused while
    the registry lives.

    HAMCP-191 D4 adds the request's identity record of output strings: the
    exact ``str`` objects a strict pass given this registry produced that
    carry an Engineering marker or the truncation suffix. A later pass given
    the same registry keeps such an object as it is; equal text from anywhere
    else, a copy or a slice is a different object and is scanned. One
    registry serves one request (one tool invocation or one audit record)
    or one cached dependency snapshot; it is passed explicitly and never
    stored globally.

    Each output records the known secrets the pass that produced it
    replaced. A later pass with a secret that pass did not have keeps the
    object only when that secret does not occur in it; otherwise the string
    is scanned like any other, so identity never skips a secret check.
    """

    __slots__ = ("_minted", "_emitted", "_outputs", "_inherits")

    def __init__(self, *, inherits: "ReferenceMarkerRegistry | None" = None) -> None:
        self._minted: dict[int, ReferenceMarker] = {}
        self._emitted: dict[int, EngineeringMarker] = {}
        self._outputs: dict[int, tuple[str, frozenset[str]]] = {}
        # A long-lived record (a cached dependency snapshot's) consulted
        # read-only, so one request's outputs never grow it.
        self._inherits = inherits

    def register_output(
        self, value: str, known_secrets: Iterable[str] = ()
    ) -> str:
        if type(value) is not str:
            raise TypeError("register_output takes an exact str")
        self._outputs[id(value)] = (
            value,
            frozenset(secret for secret in known_secrets if secret),
        )
        return value

    def output(self, value: Any, known_secrets: Iterable[str] = ()) -> bool:
        if type(value) is not str:
            return False
        entry = self._outputs.get(id(value))
        if entry is None or entry[0] is not value:
            return self._inherits is not None and self._inherits.output(
                value, known_secrets
            )
        return not any(
            secret and secret not in entry[1] and secret in value
            for secret in known_secrets
        )

    def mint(self, *, kind: str, name: str, default_redacted: bool = False) -> ReferenceMarker:
        marker = ReferenceMarker(kind=kind, name=name, default_redacted=default_redacted)
        self._minted[id(marker)] = marker
        return marker

    def minted(self, value: Any) -> bool:
        return type(value) is ReferenceMarker and self._minted.get(id(value)) is value

    def mint_marker(self, text: str) -> EngineeringMarker:
        marker = EngineeringMarker(text=text)
        self._emitted[id(marker)] = marker
        return marker

    def emitted(self, value: Any) -> bool:
        return type(value) is EngineeringMarker and self._emitted.get(id(value)) is value


@dataclass(frozen=True)
class SanitizationResult:
    value: Any
    redacted_field_count: int
    redaction_categories: tuple[str, ...]
    truncated_field_count: int = 0
    failed_closed: bool = False
    marker_collision_count: int = 0

    @property
    def redaction_applied(self) -> bool:
        return self.redacted_field_count > 0


# HAMCP-191 Finding 11: redaction makes a response partial, never complete,
# and the evidence of it is bounded counts and category names, never values.
MAX_REDACTION_COUNT = 1_000_000


class RedactionTally:
    """Bounded redaction evidence accumulated over one response's passes."""

    __slots__ = ("redacted_field_count", "categories", "marker_collision_count")

    def __init__(self) -> None:
        self.redacted_field_count = 0
        self.categories: set[str] = set()
        self.marker_collision_count = 0

    def add(self, result: SanitizationResult) -> SanitizationResult:
        self.redacted_field_count += result.redacted_field_count
        self.categories.update(result.redaction_categories)
        self.marker_collision_count += result.marker_collision_count
        return result

    @property
    def applied(self) -> bool:
        return self.redacted_field_count > 0

    def summary(self) -> dict[str, Any]:
        return {
            "redacted_field_count": min(
                self.redacted_field_count, MAX_REDACTION_COUNT
            ),
            "categories": sorted(self.categories)[:MAX_REDACTION_CATEGORIES],
            "category_count": len(self.categories),
            "marker_collision_count": min(
                self.marker_collision_count, MAX_REDACTION_COUNT
            ),
        }


def redaction_summary(result: SanitizationResult) -> dict[str, Any] | None:
    """The bounded Finding 11 evidence for one pass, or None if nothing was redacted."""

    tally = RedactionTally()
    tally.add(result)
    return tally.summary() if tally.applied else None


# HAMCP-191 D3: process health counters for marker collisions. They count
# events, never decide anything, and hold no data.
_COUNTER_LOCK = threading.Lock()
_COUNTERS = {"marker_collisions": 0, "marker_collision_refusals": 0}


def record_marker_collision_refusal(result: SanitizationResult) -> bool:
    """Count a refusal a gate made because of a marker collision (D3).

    Returns whether ``result`` carried a collision; a gate calls it on the
    result it refuses, so the count is how often look-alike text blocked a
    governed path.
    """

    collided = result.marker_collision_count > 0
    if collided:
        with _COUNTER_LOCK:
            _COUNTERS["marker_collision_refusals"] += 1
    return collided


def marker_collision_health() -> dict[str, int]:
    """The D3 counters since process start, for the health payload."""

    with _COUNTER_LOCK:
        return dict(_COUNTERS)


def normalize_key(key: Any) -> str:
    """The key normalization of the key-scoped rules."""

    return _KEY_NORMALIZER.sub("_", str(key).strip().lower()).strip("_")


def key_category(key: str, path: tuple[str, ...]) -> str | None:
    """The key-scoped rules: the category a normalized key's value gets.

    ``path`` holds the normalized keys above ``key``. The sanitizer and the
    HAMCP-162 reader's uniform screen share this one definition.
    """

    if key in _MATTER_CODE_KEYS or (
        any("matter" in part for part in path) and key in {"code", "manual_code"}
    ):
        return "matter_setup_code"
    if key in _MATTER_PAYLOAD_KEYS or (
        any("matter" in part for part in path) and key in {"payload", "qr_payload"}
    ):
        return "matter_setup_payload"
    if key in _AUTH_FLOW_KEYS:
        return "auth_flow"
    if key == "webhook_id":
        return "webhook_identifier"
    if key in _WEBHOOK_KEYS:
        return "webhook_secret"
    if key in _TOKEN_KEYS:
        return "token"
    if key in _PASSWORD_KEYS:
        return "password"
    if key in _COOKIE_KEYS:
        return "auth_cookie"
    return None


class _Emitted:
    """A marker one strict sanitizer pass produced, held as a piece (H3)."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


SEGMENT_MEMO_ENTRY_LIMIT = 65_536
SEGMENT_MEMO_TEXT_LIMIT = 4_096


class SegmentMemo:
    """Memo of one pass's pure string scan for one job (HAMCP-171 D5).

    The scan is a pure function of the string text and the known-secret
    tuple. A memo is bound to one secret tuple and to one pass (the strict
    pass, or the D11 stored-plan re-scan) and is owned by one caller job,
    which discards it at the end. It stores the scanned text, its categories and whether it carries an
    emitted marker, never a verdict, and it is bounded. The identity record
    is consulted before the memo, so a memo hit never trusts text.
    """

    __slots__ = ("known_secrets", "hits", "misses", "_results", "_pass")

    def __init__(self, known_secrets: tuple[str, ...]) -> None:
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.hits = 0
        self.misses = 0
        self._results: dict[str, tuple[Any, ...]] = {}
        # The pass the memo serves, bound on first use: the strict scan and
        # the D11 stored-plan segment scan never share a memo.
        self._pass: str | None = None

    def __len__(self) -> int:
        return len(self._results)


class _Sanitizer:
    _PASS = "strict"

    def __init__(
        self,
        *,
        known_secrets: tuple[str, ...],
        max_string: int | None,
        preserve_reference_markers: ReferenceMarkerRegistry | None = None,
        registry: ReferenceMarkerRegistry | None = None,
        segment_memo: SegmentMemo | None = None,
    ) -> None:
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.max_string = max_string
        if preserve_reference_markers is not None and (
            type(preserve_reference_markers) is not ReferenceMarkerRegistry
        ):
            raise TypeError("preserve_reference_markers takes a ReferenceMarkerRegistry")
        self.minted_markers = preserve_reference_markers
        if registry is not None and type(registry) is not ReferenceMarkerRegistry:
            raise TypeError("registry takes a ReferenceMarkerRegistry")
        self.registry = registry
        if segment_memo is not None and (
            type(segment_memo) is not SegmentMemo
            or segment_memo.known_secrets != self.known_secrets
        ):
            # A memo is valid only for its own secrets.
            raise TypeError("segment_memo does not match this sanitizer")
        if segment_memo is not None:
            if segment_memo._pass is None:
                segment_memo._pass = self._PASS
            elif segment_memo._pass != self._PASS:
                raise TypeError("segment_memo serves another sanitizer pass")
        self.segment_memo = segment_memo
        self.redacted_field_count = 0
        self.categories: set[str] = set()
        self.truncated_field_count = 0
        self.failed_closed = False
        self.marker_collision_count = 0

    @staticmethod
    def _normalize_key(key: Any) -> str:
        return normalize_key(key)

    @staticmethod
    def _marker(category: str) -> str:
        return f"[REDACTED:{category}]"

    def _own(self, text: str) -> str:
        # D4: an Engineering-produced string carrying a marker joins this
        # request's identity record, so a later pass keeps it by identity.
        if self.registry is not None:
            self.registry.register_output(text, self.known_secrets)
        return text

    def _category_for_key(self, key: str, path: tuple[str, ...]) -> str | None:
        return key_category(key, path)

    def _record_field(self, categories: set[str]) -> None:
        if categories:
            self.redacted_field_count += 1
            self.categories.update(categories)

    def _truncate(self, value: str) -> str:
        if self.max_string is None or len(value) <= self.max_string:
            return value
        self.truncated_field_count += 1
        return self._own(value[: self.max_string] + TRUNCATION_SUFFIX)

    def _replacement(self, match: re.Match[str], category: str, form: str) -> list[Any]:
        marker = _Emitted(self._marker(category))
        if form == "whole":
            return [marker]
        if form == "userinfo":
            return [match.group(1), marker, "@"]
        return [match.group(1), marker]

    @staticmethod
    def _apply(
        pieces: list[Any], pattern: re.Pattern[str], build, on_match
    ) -> list[Any]:
        output: list[Any] = []
        for piece in pieces:
            if type(piece) is not str:
                output.append(piece)
                continue
            position = 0
            for match in pattern.finditer(piece):
                on_match()
                output.append(piece[position : match.start()])
                output.extend(build(match))
                position = match.end()
            output.append(piece[position:])
        return output

    def _scan(self, value: str) -> tuple[str, frozenset[str], bool]:
        """H3/HAMCP-191: every rule over the whole string, then the collision.

        Known-secret replacement and every free-text and serialized rule run
        first and record their categories; a marker a rule emits is an
        ``_Emitted`` piece, which later rules skip by type, never by text.
        Only then is the original string judged: text that could read as an
        Engineering marker makes the whole string the collision marker, with
        the collision recorded in addition to every category found.
        """

        categories: set[str] = set()
        pieces: list[Any] = [value]
        for secret in self.known_secrets:
            pieces = self._apply(
                pieces,
                re.compile(re.escape(secret)),
                lambda _match: [_Emitted(self._marker("token"))],
                lambda: categories.add("token"),
            )
        rules = [*_SEGMENT_RULES, *((pattern, category, "prefix") for pattern, category in _SERIALIZED_PATTERNS)]
        for pattern, category, form in rules:
            pieces = self._apply(
                pieces,
                pattern,
                lambda match, category=category, form=form: self._replacement(
                    match, category, form
                ),
                lambda category=category: categories.add(category),
            )
        if marker_like_text(value):
            categories.add(MARKER_COLLISION_CATEGORY)
            return MARKER_COLLISION, frozenset(categories), True
        # One marker per run of identical emitted markers separated only by
        # whitespace; decided by type, not text.
        output: list[str] = []
        last: _Emitted | None = None
        gap: list[str] = []
        emitted = False
        for piece in pieces:
            if type(piece) is _Emitted:
                emitted = True
                if last is not None and last.text == piece.text and not "".join(gap).strip():
                    gap = []
                    continue
                output.extend(gap)
                gap = []
                output.append(piece.text)
                last = piece
            elif piece:
                if last is not None and not piece.strip():
                    gap.append(piece)
                else:
                    output.extend(gap)
                    gap = []
                    output.append(piece)
                    last = None
        output.extend(gap)
        return "".join(output), frozenset(categories), emitted

    def _scan_memo(self, value: str) -> tuple[str, frozenset[str], bool]:
        memo = self.segment_memo
        if memo is None:
            return self._scan(value)
        cached = memo._results.get(value)
        if cached is not None:
            memo.hits += 1
            return cached
        result = self._scan(value)
        memo.misses += 1
        if (
            len(value) <= SEGMENT_MEMO_TEXT_LIMIT
            and len(memo._results) < SEGMENT_MEMO_ENTRY_LIMIT
        ):
            memo._results[value] = result
        return result

    def _sanitize_string(self, value: str) -> str:
        """The only string path (HAMCP-191 D1): trust no marker by its text.

        A string this request's identity record holds is Engineering's own
        output: it is bounded again but never rescanned or recounted. Every
        other string, equal text included, runs ``_scan``.
        """

        if self.registry is not None and self.registry.output(value, self.known_secrets):
            return self._truncate(value)
        text, categories, emitted = self._scan_memo(value)
        self._record_field(set(categories))
        if MARKER_COLLISION_CATEGORY in categories:
            self.marker_collision_count += 1
            return self._own(MARKER_COLLISION)
        if emitted:
            text = self._own(text)
        return self._truncate(text)

    def _preserved(self, value: Any) -> bool:
        return self.minted_markers is not None and self.minted_markers.minted(value)

    def _reference_marker(self, value: ReferenceMarker) -> Any:
        # Registry identity only; an unregistered marker, a subclass or a
        # look-alike mapping is ordinary data.
        if not self._preserved(value):
            self._record_field({"reference_marker"})
            return self._own(REFERENCE_MARKER_DENIED)
        if value.valid():
            return value
        self._record_field({"reference_malformed"})
        return self._own(REFERENCE_MALFORMED)

    def _engineering_marker(self, value: Any) -> Any:
        # H3: registry identity only; an unregistered marker or a subclass is
        # withheld, never rendered through repr.
        if (
            type(value) is EngineeringMarker
            and self.minted_markers is not None
            and self.minted_markers.emitted(value)
            and value.valid()
        ):
            return value
        self._record_field({MARKER_COLLISION_CATEGORY})
        self.marker_collision_count += 1
        return self._own(MARKER_COLLISION)

    def _key_text(self, raw_key: Any) -> str:
        # H3/HAMCP-191: every key string passes the scan, a sensitive key's
        # own text included.
        return self._sanitize_string(str(raw_key))

    def _failure(self) -> str:
        self.failed_closed = True
        self._record_field({"sanitization_failure"})
        return self._own(SANITIZATION_FAILURE_MARKER)

    def sanitize(self, value: Any, path: tuple[str, ...] = ()) -> Any:
        if type(value) is ReferenceMarker:
            return self._reference_marker(value)
        if isinstance(value, ReferenceMarker):
            # A subclass is never preserved and never rendered through repr.
            self._record_field({"reference_marker"})
            return self._own(REFERENCE_MARKER_DENIED)
        if isinstance(value, EngineeringMarker):
            return self._engineering_marker(value)
        if isinstance(value, Mapping):
            safe: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = self._normalize_key(raw_key)
                safe_key: str | None = None
                try:
                    key_category = self._category_for_key(key, path)
                    safe_key = self._key_text(raw_key)
                    if self._preserved(item):
                        safe[safe_key] = self._reference_marker(item)
                        continue
                    if key_category:
                        safe[safe_key] = self._own(self._marker(key_category))
                        self._record_field({key_category})
                    else:
                        safe[safe_key] = self.sanitize(item, (*path, key))
                except Exception:
                    # HAMCP-191: the key is written only as its scanned text;
                    # a key that never finished its scan is withheld.
                    if safe_key is None:
                        safe_key = self._own(SANITIZATION_FAILURE_MARKER)
                    safe[safe_key] = self._failure()
            return safe
        if isinstance(value, list):
            return [self._sanitize_item(item, (*path, "item")) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_item(item, (*path, "item")) for item in value)
        if isinstance(value, str):
            try:
                return self._sanitize_string(value)
            except Exception:
                return self._failure()
        if value is None or isinstance(value, (bool, int, float)):
            return value
        try:
            return self._sanitize_string(repr(value))
        except Exception:
            return self._failure()

    def _sanitize_item(self, item: Any, path: tuple[str, ...]) -> Any:
        try:
            return self.sanitize(item, path)
        except Exception:
            return self._failure()


class _StoredPlanSanitizer(_Sanitizer):
    """HAMCP-191 D11: the re-scan of a stored change plan, and nothing else.

    A stored plan has been through ``ChangePlan.to_dict()`` and the plan
    database, so no string in it is the object Engineering produced and the
    identity record cannot hold it. Until HAMCP-207 persists that identity,
    this pass keeps beta.19's marker tolerance for stored plans only: text
    between canonical ``[REDACTED:<category>]`` markers is scanned by every
    rule, the markers themselves are kept and not counted, and look-alike
    text is not judged. Every other part of the strict sanitizer (key text,
    reference markers, failure handling, truncation) is unchanged. Only the
    allow-listed stored-plan call sites may use it (``rescan_stored_plan``).
    """

    _PASS = "stored_plan"

    def _replace(self, text: str, pattern: re.Pattern[str], category: str, replacement, categories: set[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            categories.add(category)
            return replacement(match)

        parts = REDACTION_MARKER.split(text)
        markers = REDACTION_MARKER.findall(text)
        output: list[str] = []
        for index, part in enumerate(parts):
            output.append(pattern.sub(replace, part))
            if index < len(markers):
                output.append(markers[index])
        return "".join(output)

    def _segment_scan(self, text: str) -> tuple[str, frozenset[str]]:
        categories: set[str] = set()
        safe = text
        for secret in self.known_secrets:
            if secret in safe:
                safe = safe.replace(secret, self._marker("token"))
                categories.add("token")
        for pattern, category, form in _SEGMENT_RULES:
            safe = self._replace(
                safe,
                pattern,
                category,
                lambda match, category=category, form=form: "".join(
                    piece if type(piece) is str else piece.text
                    for piece in self._replacement(match, category, form)
                ),
                categories,
            )
        # Resplit so a serialized rule never rewrites a marker just emitted.
        parts = REDACTION_MARKER.split(safe)
        markers = REDACTION_MARKER.findall(safe)
        rebuilt: list[str] = []
        for index, part in enumerate(parts):
            for pattern, category in _SERIALIZED_PATTERNS:
                part = self._replace(
                    part,
                    pattern,
                    category,
                    lambda match, category=category: match.group(1) + self._marker(category),
                    categories,
                )
            rebuilt.append(part)
            if index < len(markers):
                rebuilt.append(markers[index])
        return "".join(rebuilt), frozenset(categories)

    def _segment(self, text: str) -> tuple[str, frozenset[str]]:
        memo = self.segment_memo
        if memo is None:
            return self._segment_scan(text)
        cached = memo._results.get(text)
        if cached is not None:
            memo.hits += 1
            return cached
        result = self._segment_scan(text)
        memo.misses += 1
        if len(text) <= SEGMENT_MEMO_TEXT_LIMIT and len(memo._results) < SEGMENT_MEMO_ENTRY_LIMIT:
            memo._results[text] = result
        return result

    def _sanitize_string(self, value: str) -> str:
        parts = REDACTION_MARKER.split(value)
        markers = REDACTION_MARKER.findall(value)
        categories: set[str] = set()
        output: list[str] = []
        for index, part in enumerate(parts):
            safe, detected = self._segment(part)
            output.append(safe)
            categories.update(detected)
            if index < len(markers):
                output.append(markers[index])
        self._record_field(categories)
        deduplicated = _STORED_PLAN_REPEATED_MARKER.sub(r"\1", "".join(output))
        return self._truncate(deduplicated)


def _result(sanitizer: _Sanitizer, value: Any) -> SanitizationResult:
    try:
        safe = sanitizer.sanitize(value)
    except Exception:
        safe = sanitizer._failure()
    if sanitizer.marker_collision_count:
        with _COUNTER_LOCK:
            _COUNTERS["marker_collisions"] += sanitizer.marker_collision_count
    categories = tuple(sorted(sanitizer.categories)[:MAX_REDACTION_CATEGORIES])
    return SanitizationResult(
        value=safe,
        redacted_field_count=sanitizer.redacted_field_count,
        redaction_categories=categories,
        truncated_field_count=sanitizer.truncated_field_count,
        failed_closed=sanitizer.failed_closed,
        marker_collision_count=sanitizer.marker_collision_count,
    )


def rescan_stored_plan(
    value: Any,
    *,
    known_secrets: tuple[str, ...] = (),
    max_string: int | None = None,
    segment_memo: SegmentMemo | None = None,
) -> SanitizationResult:
    """Re-scan a stored change plan with beta.19 marker tolerance (D11).

    The one reviewed exception to the strict-only pass. It exists because a
    stored plan carries Engineering's own markers (truncated review
    projections, redacted approval notes, bounded validation evidence) as
    rebuilt strings that no identity record can hold. New plans are
    sanitized strictly when they are created; this pass only re-reads what
    Engineering stored. HAMCP-207 removes it. A code-derived test pins the
    exact call sites allowed to use it.
    """

    return _result(
        _StoredPlanSanitizer(
            known_secrets=known_secrets,
            max_string=max_string,
            segment_memo=segment_memo,
        ),
        value,
    )


def sanitize_untrusted_data(
    value: Any,
    *,
    known_secrets: tuple[str, ...] = (),
    max_string: int | None = None,
    preserve_reference_markers: ReferenceMarkerRegistry | None = None,
    identity_markers_only: bool = True,
    segment_memo: SegmentMemo | None = None,
    registry: ReferenceMarkerRegistry | None = None,
) -> SanitizationResult:
    """Recursively sanitize untrusted data, failing closed at each field.

    HAMCP-191 D1: the strict pass is the only pass, for every caller. No
    input text counts as already redacted. Every string, key text included,
    runs every rule; text that could read as a redaction or truncation marker
    is then replaced whole by ``MARKER_COLLISION`` (D2), with the collision
    counted in addition to every category the rules found.
    ``identity_markers_only`` remains for HAMCP-162 call sites and accepts
    only ``True``.

    ``registry`` (D4) is the request's identity record. Strings this pass
    produces that carry a marker or the truncation suffix are registered in
    it, and a string it already holds is kept as Engineering's own output,
    by object identity only. Without it, Engineering's own earlier output is
    scanned like any other text.

    ``preserve_reference_markers`` (default off) takes the registry of one
    YAML read. It keeps a marker that registry minted, by object identity,
    whose name still matches the minting grammar, even under a sensitive
    key. Only the HAMCP-162 YAML reader passes it.

    ``segment_memo`` (default off, HAMCP-171 D5) reuses the pure string scan
    for identical text within one caller job. The result is identical with
    or without it.
    """

    if type(identity_markers_only) is not bool:
        raise TypeError("identity_markers_only takes a bool")
    if not identity_markers_only:
        # D1: the marker-trusting default mode is retired.
        raise TypeError("identity_markers_only=False is no longer supported")
    return _result(
        _Sanitizer(
            known_secrets=known_secrets,
            max_string=max_string,
            preserve_reference_markers=preserve_reference_markers,
            registry=registry,
            segment_memo=segment_memo,
        ),
        value,
    )
