"""Central recursive sanitization for untrusted diagnostic data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
import unicodedata
from typing import Any
from urllib.parse import unquote


REDACTION_MARKER = re.compile(r"\[REDACTED:[a-z0-9_]+\]")
REPEATED_REDACTION_MARKER = re.compile(
    r"(\[REDACTED:([a-z0-9_]+)\])(?:\s*\1)+"
)
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


# HAMCP-162 H3: redaction-marker look-alikes in untrusted text. Under
# ``identity_markers_only`` text never decides that something is already
# redacted; text that a client could read as an Engineering marker is
# replaced whole by this marker instead.
MARKER_COLLISION_CATEGORY = "redaction_marker_collision"
MARKER_COLLISION = "[REDACTED:redaction_marker_collision]"
MAX_MARKER_DECODE_ROUNDS = 4
_MARKER_LIKE = re.compile(r"[\[(<{][\W_]*(?:redacted|truncated)")
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
        if _MARKER_LIKE.search(_marker_fold(value)):
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
    """The markers one YAML read minted, held by object identity (H2, H3).

    The opt-in exemption trusts a value only when it is the very object this
    registry minted: never a type alone, a mapping shape or a marker-shaped
    string. Upstream data, a truncation shell or a copy cannot be
    registered. Holding each marker keeps its ``id`` from being reused while
    the registry lives.
    """

    __slots__ = ("_minted", "_emitted")

    def __init__(self) -> None:
        self._minted: dict[int, ReferenceMarker] = {}
        self._emitted: dict[int, EngineeringMarker] = {}

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

    @property
    def redaction_applied(self) -> bool:
        return self.redacted_field_count > 0


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


class _Sanitizer:
    def __init__(
        self,
        *,
        known_secrets: tuple[str, ...],
        max_string: int | None,
        preserve_reference_markers: ReferenceMarkerRegistry | None = None,
        identity_markers_only: bool = False,
    ) -> None:
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.max_string = max_string
        if preserve_reference_markers is not None and (
            type(preserve_reference_markers) is not ReferenceMarkerRegistry
        ):
            raise TypeError("preserve_reference_markers takes a ReferenceMarkerRegistry")
        self.minted_markers = preserve_reference_markers
        if type(identity_markers_only) is not bool:
            raise TypeError("identity_markers_only takes a bool")
        self.identity_markers_only = identity_markers_only
        self.redacted_field_count = 0
        self.categories: set[str] = set()
        self.truncated_field_count = 0
        self.failed_closed = False

    @staticmethod
    def _normalize_key(key: Any) -> str:
        return normalize_key(key)

    @staticmethod
    def _marker(category: str) -> str:
        return f"[REDACTED:{category}]"

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
        return value[: self.max_string] + "...<truncated>"

    def _replace(
        self,
        text: str,
        pattern: re.Pattern[str],
        category: str,
        replacement,
        categories: set[str],
    ) -> str:
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

    def _sanitize_segment(self, text: str) -> tuple[str, set[str]]:
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
        # Earlier rules introduce markers. Resplit before scanning serialized
        # assignments so a later rule cannot interpret or partially rewrite a
        # marker that was just emitted.
        parts = REDACTION_MARKER.split(safe)
        markers = REDACTION_MARKER.findall(safe)
        rebuilt: list[str] = []
        for index, part in enumerate(parts):
            for pattern, category in _SERIALIZED_PATTERNS:
                part = self._replace(
                    part,
                    pattern,
                    category,
                    lambda match, category=category: match.group(1)
                    + self._marker(category),
                    categories,
                )
            rebuilt.append(part)
            if index < len(markers):
                rebuilt.append(markers[index])
        safe = "".join(rebuilt)
        return safe, categories

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

    def _strict_string(self, value: str) -> str:
        """H3: scan the whole untrusted string; trust no marker by its text.

        Text that could read as an Engineering marker is replaced whole.
        Every other string runs through every rule; a marker a rule emits is
        an ``_Emitted`` piece, which later rules skip by type, never by text.
        """

        if marker_like_text(value):
            self._record_field({MARKER_COLLISION_CATEGORY})
            return MARKER_COLLISION
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
        self._record_field(categories)
        # One marker per run of identical emitted markers separated only by
        # whitespace, as the default pass does; decided by type, not text.
        output: list[str] = []
        last: _Emitted | None = None
        gap: list[str] = []
        for piece in pieces:
            if type(piece) is _Emitted:
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
        return self._truncate("".join(output))

    def _sanitize_string(self, value: str) -> str:
        if self.identity_markers_only:
            return self._strict_string(value)
        # Existing markers are immutable delimiters. Only the text between them
        # is scanned, so repeat sanitation is deterministic and idempotent.
        parts = REDACTION_MARKER.split(value)
        markers = REDACTION_MARKER.findall(value)
        categories: set[str] = set()
        output: list[str] = []
        for index, part in enumerate(parts):
            safe, detected = self._sanitize_segment(part)
            output.append(safe)
            categories.update(detected)
            if index < len(markers):
                output.append(markers[index])
        self._record_field(categories)
        # Overlapping key-aware and free-text detections may identify the same
        # original value. Preserve one stable marker without weakening either
        # detection pass or changing idempotence.
        deduplicated = REPEATED_REDACTION_MARKER.sub(r"\1", "".join(output))
        return self._truncate(deduplicated)

    def _preserved(self, value: Any) -> bool:
        return self.minted_markers is not None and self.minted_markers.minted(value)

    def _reference_marker(self, value: ReferenceMarker) -> Any:
        # Registry identity only; an unregistered marker, a subclass or a
        # look-alike mapping is ordinary data.
        if not self._preserved(value):
            self._record_field({"reference_marker"})
            return REFERENCE_MARKER_DENIED
        if value.valid():
            return value
        self._record_field({"reference_malformed"})
        return REFERENCE_MALFORMED

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
        return MARKER_COLLISION

    def _key_text(self, raw_key: Any, key_category: str | None) -> str:
        # The default pass keeps a sensitive key's own text; under H3 every
        # key string passes the scan.
        if key_category and not self.identity_markers_only:
            return str(raw_key)
        return self._sanitize_string(str(raw_key))

    def sanitize(self, value: Any, path: tuple[str, ...] = ()) -> Any:
        if type(value) is ReferenceMarker:
            return self._reference_marker(value)
        if isinstance(value, ReferenceMarker):
            # A subclass is never preserved and never rendered through repr.
            self._record_field({"reference_marker"})
            return REFERENCE_MARKER_DENIED
        if isinstance(value, EngineeringMarker):
            return self._engineering_marker(value)
        if isinstance(value, Mapping):
            safe: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = self._normalize_key(raw_key)
                try:
                    key_category = self._category_for_key(key, path)
                    if self._preserved(item):
                        safe[self._key_text(raw_key, key_category)] = self._reference_marker(item)
                        continue
                    if key_category:
                        safe[self._key_text(raw_key, key_category)] = self._marker(key_category)
                        self._record_field({key_category})
                    else:
                        safe_key = self._sanitize_string(str(raw_key))
                        safe[safe_key] = self.sanitize(item, (*path, key))
                except Exception:
                    self.failed_closed = True
                    safe[str(raw_key)] = SANITIZATION_FAILURE_MARKER
                    self._record_field({"sanitization_failure"})
            return safe
        if isinstance(value, list):
            return [self._sanitize_item(item, (*path, "item")) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_item(item, (*path, "item")) for item in value)
        if isinstance(value, str):
            try:
                return self._sanitize_string(value)
            except Exception:
                self.failed_closed = True
                self._record_field({"sanitization_failure"})
                return SANITIZATION_FAILURE_MARKER
        if value is None or isinstance(value, (bool, int, float)):
            return value
        try:
            return self._sanitize_string(repr(value))
        except Exception:
            self.failed_closed = True
            self._record_field({"sanitization_failure"})
            return SANITIZATION_FAILURE_MARKER

    def _sanitize_item(self, item: Any, path: tuple[str, ...]) -> Any:
        try:
            return self.sanitize(item, path)
        except Exception:
            self.failed_closed = True
            self._record_field({"sanitization_failure"})
            return SANITIZATION_FAILURE_MARKER


def sanitize_untrusted_data(
    value: Any,
    *,
    known_secrets: tuple[str, ...] = (),
    max_string: int | None = None,
    preserve_reference_markers: ReferenceMarkerRegistry | None = None,
    identity_markers_only: bool = False,
) -> SanitizationResult:
    """Recursively sanitize untrusted data, failing closed at each field.

    ``preserve_reference_markers`` (default off) takes the registry of one
    YAML read. It keeps a marker that registry minted, by object identity,
    whose name still matches the minting grammar, even under a sensitive
    key. Only the HAMCP-162 YAML reader passes it.

    ``identity_markers_only`` (default off, HAMCP-162 H3) stops treating
    marker-shaped input text as already redacted: every string, key text
    included, passes every rule, and text that could read as a redaction
    marker is replaced whole by ``MARKER_COLLISION``. The registry above
    keeps the reader's own markers by identity. The default pass is
    unchanged for every other caller.
    """

    sanitizer = _Sanitizer(
        known_secrets=known_secrets,
        max_string=max_string,
        preserve_reference_markers=preserve_reference_markers,
        identity_markers_only=identity_markers_only,
    )
    try:
        safe = sanitizer.sanitize(value)
    except Exception:
        sanitizer.failed_closed = True
        sanitizer._record_field({"sanitization_failure"})
        safe = SANITIZATION_FAILURE_MARKER
    categories = tuple(sorted(sanitizer.categories)[:MAX_REDACTION_CATEGORIES])
    return SanitizationResult(
        value=safe,
        redacted_field_count=sanitizer.redacted_field_count,
        redaction_categories=categories,
        truncated_field_count=sanitizer.truncated_field_count,
        failed_closed=sanitizer.failed_closed,
    )
