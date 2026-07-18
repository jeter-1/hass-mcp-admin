"""Central recursive sanitization for untrusted diagnostic data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


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


class _Sanitizer:
    def __init__(
        self,
        *,
        known_secrets: tuple[str, ...],
        max_string: int | None,
    ) -> None:
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.max_string = max_string
        self.redacted_field_count = 0
        self.categories: set[str] = set()
        self.truncated_field_count = 0
        self.failed_closed = False

    @staticmethod
    def _normalize_key(key: Any) -> str:
        return _KEY_NORMALIZER.sub("_", str(key).strip().lower()).strip("_")

    @staticmethod
    def _marker(category: str) -> str:
        return f"[REDACTED:{category}]"

    def _category_for_key(self, key: str, path: tuple[str, ...]) -> str | None:
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

        safe = self._replace(
            safe,
            _AUTH_HEADER,
            "token",
            lambda match: match.group(1) + self._marker("token"),
            categories,
        )
        safe = self._replace(
            safe,
            _BEARER,
            "token",
            lambda match: match.group(1) + self._marker("token"),
            categories,
        )
        safe = self._replace(
            safe,
            _COOKIE_HEADER,
            "auth_cookie",
            lambda match: match.group(1) + self._marker("auth_cookie"),
            categories,
        )
        safe = self._replace(
            safe,
            _JWT,
            "token",
            lambda _match: self._marker("token"),
            categories,
        )
        safe = self._replace(
            safe,
            _URL_USERINFO,
            "url_credentials",
            lambda match: match.group(1) + self._marker("url_credentials") + "@",
            categories,
        )
        safe = self._replace(
            safe,
            _URL_ENCODED_USERINFO,
            "url_credentials",
            lambda match: match.group(1) + self._marker("url_credentials") + "@",
            categories,
        )
        safe = self._replace(
            safe,
            _URL_CREDENTIAL_PARAMETER,
            "url_credentials",
            lambda match: match.group(1) + self._marker("url_credentials"),
            categories,
        )
        safe = self._replace(
            safe,
            _WEBHOOK_PATH,
            "webhook_identifier",
            lambda match: match.group(1) + self._marker("webhook_identifier"),
            categories,
        )
        safe = self._replace(
            safe,
            _WEBHOOK_PROSE,
            "webhook_identifier",
            lambda match: match.group(1) + self._marker("webhook_identifier"),
            categories,
        )
        safe = self._replace(
            safe,
            _LOGIN_FLOW_PATH,
            "auth_flow",
            lambda match: match.group(1) + self._marker("auth_flow"),
            categories,
        )
        safe = self._replace(
            safe,
            _MATTER_PAYLOAD,
            "matter_setup_payload",
            lambda _match: self._marker("matter_setup_payload"),
            categories,
        )
        safe = self._replace(
            safe,
            _MATTER_CODE_LABEL,
            "matter_setup_code",
            lambda match: match.group(1) + self._marker("matter_setup_code"),
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

    def _sanitize_string(self, value: str) -> str:
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

    def sanitize(self, value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, Mapping):
            safe: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = self._normalize_key(raw_key)
                try:
                    key_category = self._category_for_key(key, path)
                    if key_category:
                        safe[str(raw_key)] = self._marker(key_category)
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
) -> SanitizationResult:
    """Recursively sanitize untrusted data, failing closed at each field."""

    sanitizer = _Sanitizer(known_secrets=known_secrets, max_string=max_string)
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
