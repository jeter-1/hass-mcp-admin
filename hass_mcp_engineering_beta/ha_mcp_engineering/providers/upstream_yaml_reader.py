"""HAMCP-162 YAML configuration reader (``ha_config_get_yaml``).

One public tool reads one mapping-key path of ``configuration.yaml`` through
the reviewed upstream ``ha_config_get_yaml``. Everything else is refused
before dispatch with zero upstream calls. Engineering builds the upstream
arguments itself (the ``file`` value is a constant), validates the response
identity, bounds the parsed value, and redacts it with the full key path
before the shared sanitizer runs (design layers L1-L5). Every other
client-visible field passes the same final sanitizer pass (H1).

H4: :func:`screen` is the single entry point every string the reader can
emit passes, in every output position (:data:`OUTPUT_POSITIONS`): values,
list items, keys at every level, reference names, key paths, limitations,
the redaction summary and every gateway response field. It applies the
complete rule set (:data:`SCANNER_RULES`) with no position-specific skip.

The upstream tool is an *optional reviewed tool*: it is admitted only for
exact versions listed in :data:`OPTIONAL_DISPOSITIONS` whose complete runtime
descriptor matches the D1-captured binding. It never joins the stock policy
or the stock catalog accounting, so its absence or drift never quarantines a
stock read.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Any
from urllib.parse import unquote_to_bytes

from ..sanitization import (
    ENV_VAR_REFERENCE_NAME,
    MARKER_COLLISION,
    MARKER_COLLISION_CATEGORY,
    SECRET_REFERENCE_NAME,
    EngineeringMarker,
    ReferenceMarker,
    ReferenceMarkerRegistry,
    _AUTH_FLOW_KEYS,
    _COOKIE_KEYS,
    _MATTER_CODE_KEYS,
    _MATTER_PAYLOAD_KEYS,
    _PASSWORD_KEYS,
    _TOKEN_KEYS,
    _WEBHOOK_KEYS,
    key_category,
    marker_like_text,
    normalize_key,
    sanitize_untrusted_data,
)

TOOL_NAME = "ha_config_get_yaml"
TITLE = "Read YAML Config Fragment"
SIBLING_TOOLS = ("ha_list_files", "ha_read_file")
ALLOWED_FILE = "configuration.yaml"
CORE_REQUIREMENTS = ("core.basic_rest_read", "core.basic_websocket_read")

MAX_DEPTH = 24
MAX_NODES = 4_000
MAX_INCLUDE_LIMITATIONS = 16
MAX_FILE_ARGUMENT_CHARS = 4_096
MAX_DECODE_ROUNDS = 4
YAML_PATH_PATTERN = (
    r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}(\.[A-Za-z0-9_][A-Za-z0-9_-]{0,63}){0,7}$"
)
_YAML_PATH = re.compile(YAML_PATH_PATTERN)
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")

PUBLIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["yaml_path"],
    "properties": {
        "yaml_path": {
            "type": "string",
            "pattern": YAML_PATH_PATTERN,
            "description": (
                "Dotted mapping-key path in configuration.yaml, e.g. 'recorder' "
                "or 'homeassistant.customize'. List indices are not supported. "
                "Paths naming a credential key (password, token, api_key, pin, "
                "code, ...) are refused; read the parent instead."
            ),
        },
        "file": {
            "type": "string",
            "enum": [ALLOWED_FILE],
            "default": ALLOWED_FILE,
            "description": "Only configuration.yaml is readable in this release.",
        },
    },
}

DESCRIPTION = (
    "Read one mapping-key path of configuration.yaml as parsed data "
    "(read-only).\n"
    "- Only configuration.yaml is readable; every other file, including "
    "secrets.yaml and packages/, is refused before any upstream call.\n"
    "- Values are ha-mcp's YAML 1.2 parse of the file on disk, not Home "
    "Assistant's loaded configuration, and may differ from Home Assistant's "
    "YAML 1.1 interpretation (for example on/off/yes/no and numbers with "
    "leading zeros).\n"
    "- Non-string mapping keys are converted to strings upstream; keys that "
    "convert to the same text collapse to one.\n"
    "- !secret and !env_var become {\"secret_ref\": name} and "
    "{\"env_var_ref\": name} markers; values and !env_var defaults are never "
    "returned. !include tags are returned as text and never followed.\n"
    "- Credential-shaped keys, connection strings and credential text are "
    "redacted whole (best effort; this over-redacts, for example most URLs "
    "and any text mentioning a token, auth or session). Selecting a "
    "credential-shaped path is refused.\n"
    "- Engineering makes no write or service call. The one accepted "
    "dependency: the first call may make the ha-mcp component call POST "
    "ha_mcp_tools/get_caller_token and persist the admin caller token."
)

# Response-level disclosures carried by every successful result.
K7_LIMITATION = (
    "yaml_1_2_parse:values are ha-mcp's YAML 1.2 parse and may differ from "
    "Home Assistant's YAML 1.1 interpretation"
)
K5_LIMITATION = (
    "keys_converted:non-string mapping keys are converted to strings "
    "upstream and keys with the same text collapse to one"
)
D12_LIMITATION = (
    "redaction_best_effort:a credential under an innocent key with no "
    "credential word in its text is not recognized"
)
DISCLOSURE_LIMITATIONS = (K7_LIMITATION, K5_LIMITATION, D12_LIMITATION)
K7_WARNING = (
    "Values are ha-mcp's YAML 1.2 parse of configuration.yaml and may differ "
    "from Home Assistant's YAML 1.1 interpretation."
)
ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


# ---------------------------------------------------------------------------
# Refusals and fixed failure codes.

ARGUMENTS_INVALID = "yaml_read_arguments_invalid"
FILE_NOT_ALLOWED = "yaml_file_not_allowed"
PATH_INVALID = "yaml_path_invalid"
PATH_SENSITIVE = "yaml_path_sensitive"
FILE_NOT_ALLOWED_UPSTREAM = "yaml_file_not_allowed_upstream"
FILE_NOT_FOUND = "yaml_file_not_found"
PARSE_ERROR = "yaml_parse_error"
VALIDATION_OR_UNAVAILABLE = "yaml_upstream_validation_or_component_unavailable"
READ_FAILED = "yaml_read_failed"
IDENTITY_INVALID = "yaml_read_response_identity_invalid"

# code -> (gateway failure category, fixed public message)
FAILURES: dict[str, tuple[str, str]] = {
    ARGUMENTS_INVALID: (
        "invalid_request",
        "Arguments must be yaml_path and optionally file, both strings.",
    ),
    FILE_NOT_ALLOWED: ("invalid_request", "Only configuration.yaml is readable."),
    PATH_INVALID: (
        "invalid_request",
        "yaml_path must be a dotted mapping-key path of at most eight segments.",
    ),
    PATH_SENSITIVE: (
        "invalid_request",
        "yaml_path names a credential-shaped key; read the parent instead.",
    ),
    FILE_NOT_ALLOWED_UPSTREAM: (
        "invalid_request",
        "The upstream component refused the file path.",
    ),
    FILE_NOT_FOUND: ("resource_not_found", "configuration.yaml was not found."),
    PARSE_ERROR: (
        "upstream_error",
        "configuration.yaml could not be parsed as YAML.",
    ),
    VALIDATION_OR_UNAVAILABLE: (
        "upstream_error",
        "upstream validation failed or component unavailable",
    ),
    READ_FAILED: ("upstream_error", "upstream read failed"),
    IDENTITY_INVALID: (
        "invalid_response",
        "The upstream response did not match the reviewed shape.",
    ),
}
PRE_DISPATCH_CODES = frozenset(
    {ARGUMENTS_INVALID, FILE_NOT_ALLOWED, PATH_INVALID, PATH_SENSITIVE}
)


class YamlReadRefusal(Exception):
    """A fixed-code reader refusal. Never carries upstream text or a value."""

    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        if code not in FAILURES:
            raise ValueError("yaml_refusal_code_unknown")
        super().__init__(code)
        self.code = code
        self.category, self.message = FAILURES[code]
        self.details = dict(details or {})


# ---------------------------------------------------------------------------
# L1-L3: argument validation before admission and dispatch.

# L2 deny floor: segment names, basenames and basename patterns.
_L2_SEGMENTS = frozenset(
    {
        ".storage", ".cloud", ".git", "ssl", "www", "custom_components",
        "blueprints", "backups", "deps", "tts", "packages", ".ha_mcp_tools",
    }
)
_L2_BASENAMES = frozenset({"secrets.yaml", "secrets.yml", "approval_pin.json"})
_L2_BASENAME_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r".*secret.*", r".*credential.*", r".*token.*", r".*\.pem", r".*\.key",
        r".*\.crt", r".*\.csr", r".*\.der", r".*\.p12", r".*\.pfx", r".*\.jks",
        r".*\.db", r".*\.db-.*", r".*\.sqlite.*", r".*\.json", r".*\.log.*",
        r"id_rsa.*", r"id_ed25519.*",
    )
)


def l1_allows(file_value: Any) -> bool:
    """L1: absent, or a string byte-equal to ``configuration.yaml``."""

    return type(file_value) is str and file_value == ALLOWED_FILE


def l2_denies(file_value: str) -> bool:
    """L2 deny floor: case-insensitive on every ``/``-separated segment."""

    segments = file_value.lower().split("/")
    if any(segment in _L2_SEGMENTS or segment.startswith(".") for segment in segments):
        return True
    basename = segments[-1]
    return basename in _L2_BASENAMES or any(
        pattern.fullmatch(basename) for pattern in _L2_BASENAME_PATTERNS
    )


# The single L1-admitted value is never denied by L2.
assert not l2_denies(ALLOWED_FILE)


def validate_arguments(arguments: Any) -> str:
    """Return the accepted ``yaml_path`` or raise a pre-dispatch refusal."""

    if type(arguments) is not dict or not set(arguments) <= {"yaml_path", "file"}:
        raise YamlReadRefusal(ARGUMENTS_INVALID)
    if any(type(value) is not str for value in arguments.values()):
        raise YamlReadRefusal(ARGUMENTS_INVALID)
    if "yaml_path" not in arguments:
        raise YamlReadRefusal(ARGUMENTS_INVALID)
    if "file" in arguments:
        file_value = arguments["file"]
        if not l1_allows(file_value) or l2_denies(file_value):
            raise YamlReadRefusal(FILE_NOT_ALLOWED)
    yaml_path = arguments["yaml_path"]
    if not _YAML_PATH.fullmatch(yaml_path):
        raise YamlReadRefusal(PATH_INVALID)
    if any(is_credential_key(segment.lower()) for segment in yaml_path.split(".")):
        raise YamlReadRefusal(PATH_SENSITIVE)
    return yaml_path


def refusal_audit_details(arguments: Any) -> dict[str, int]:
    """Value-free audit detail for a refused argument: lengths only."""

    if type(arguments) is not dict:
        return {}
    return {
        f"{name}_length": len(value)
        for name, value in sorted(arguments.items())
        if name in {"yaml_path", "file"} and type(value) is str
    }


def upstream_arguments(yaml_path: str) -> dict[str, Any]:
    """The only argument shape Engineering ever sends upstream."""

    return {
        "yaml_path": yaml_path,
        "file": ALLOWED_FILE,
        "include_content": False,
        "include_parsed": True,
    }


# ---------------------------------------------------------------------------
# Rule A: canonicalization.

_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_STRAY_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class _EncodingRefused(ValueError):
    pass


def canonical(raw: str) -> tuple[str, int]:
    """Rule A then NFKC and casefold. Returns (canonical text, rounds)."""

    text, rounds, _decoded = canonical_forms(raw)
    return text, rounds


def canonical_forms(raw: str) -> tuple[str, int, str]:
    """Return (canonical text, rounds, decoded NFKC text before casefold)."""

    value = raw
    rounds = MAX_DECODE_ROUNDS
    for count in range(MAX_DECODE_ROUNDS):
        if not _ESCAPE.search(value):
            rounds = count
            break
        if _STRAY_PERCENT.search(value):
            raise _EncodingRefused("ambiguous")
        try:
            decoded = unquote_to_bytes(value).decode("utf-8", "strict")
        except (UnicodeError, ValueError):
            raise _EncodingRefused("invalid") from None
        if Counter(_CONTROL.findall(decoded)) - Counter(_CONTROL.findall(value)):
            raise _EncodingRefused("control")
        value = decoded
    try:
        decoded = unicodedata.normalize("NFKC", value)
        text = decoded.casefold()
    except (TypeError, ValueError):
        raise _EncodingRefused("invalid") from None
    # One final check: covers a value still escaped after the last round and
    # an escape introduced by NFKC (fullwidth percent).
    if _ESCAPE.search(text):
        raise _EncodingRefused("non_converging")
    return text, rounds, decoded


# ---------------------------------------------------------------------------
# is_credential_key, rule B, rule C' and K1.

CREDENTIAL_WORDS = (
    "pwd", "passw", "secret", "token", "apikey", "api_key", "api-key", "auth",
    "credential", "private", "bearer", "cookie", "session",
)
_COMPACT_WORDS = tuple(sorted({re.sub("[^a-z0-9]", "", word) for word in CREDENTIAL_WORDS}))
_DELIMITED_TOKEN = re.compile(r"\b(pass|key)\s*[=:]")
_CONNECTION_WORDS = ("url", "uri", "dsn", "connect", "connstr", "database")
_IDENTIFIER = re.compile(r"[a-z0-9_.-]+")
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]{0,31})://")
_SQLITE = re.compile(r"sqlite:///[a-z0-9_./-]+")
_SQLITE_FORBIDDEN = re.compile("[\x00-\x1f\x7f  ]")
_SANITIZER_KEY_SETS = frozenset().union(
    _TOKEN_KEYS, _PASSWORD_KEYS, _COOKIE_KEYS, _WEBHOOK_KEYS, _AUTH_FLOW_KEYS,
    _MATTER_CODE_KEYS, _MATTER_PAYLOAD_KEYS,
)
_YAML_EXACT_KEYS = frozenset(
    "api_password apikey app_key app_secret auth auth_token bearer_token "
    "consumer_key consumer_secret access_key secret_key private_key "
    "encryption_key psk pin code passcode key pwd pass".split()
)
_CONTAINS = (
    "password", "passwd", "passphrase", "secret", "token", "credential",
    "api_key", "apikey", "private_key",
)
_SUFFIXES = ("_pin", "_code", "_psk", "_key", "_pass", "_pwd")


def _compact(text: str) -> str:
    return re.sub("[^a-z0-9]", "", text)


def _word_in_compact_key(canonical_key: str) -> bool:
    compact = _compact(canonical_key)
    return any(word in compact for word in _COMPACT_WORDS)


def is_credential_key(canonical_key: str) -> bool:
    """The one shared predicate of L3 and L4 (input is a canonical key)."""

    normalized = re.sub("[^a-z0-9]+", "_", canonical_key).strip("_")
    return (
        normalized in _SANITIZER_KEY_SETS
        or normalized in _YAML_EXACT_KEYS
        or any(part in normalized for part in _CONTAINS)
        or normalized.endswith(_SUFFIXES)
        or _word_in_compact_key(canonical_key)
    )


def is_connection_key(canonical_key: str) -> bool:
    compact = _compact(canonical_key)
    return any(word in compact for word in _CONNECTION_WORDS)


def _userinfo(text: str) -> bool:
    index = text.find("://")
    return index >= 0 and "@" in text[index + 3 :]


def credential_text(canonical_text: str) -> bool:
    """C'1 userinfo, C'2 credential word (substring), C'3 delimited token."""

    return (
        _userinfo(canonical_text)
        or any(word in canonical_text for word in CREDENTIAL_WORDS)
        or _DELIMITED_TOKEN.search(canonical_text) is not None
    )


def key_text_unsafe(canonical_key: str) -> bool:
    """K1: a key whose own text would carry a credential."""

    if _userinfo(canonical_key):
        return True
    return (
        _word_in_compact_key(canonical_key)
        or _DELIMITED_TOKEN.search(canonical_key) is not None
    ) and not _IDENTIFIER.fullmatch(canonical_key)


def sqlite_visible(raw: str, canonical_text: str, rounds: int) -> bool:
    """R11: the one exact safe shape visible under a connection key."""

    if not _SQLITE.fullmatch(canonical_text) or _SQLITE_FORBIDDEN.search(canonical_text):
        return False
    if ".." in canonical_text.split("/"):
        return False
    return rounds == 0 and canonical_text == raw


CREDENTIAL_MARKER = "[REDACTED:credential_key]"
ENCODING_MARKER = "[REDACTED:encoding]"
CONNECTION_STRING_MARKER = "[REDACTED:connection_string]"
CONNECTION_URI_SUFFIX = "://[REDACTED:connection_uri]"
REFERENCE_MALFORMED_MARKER = "[REDACTED:reference_malformed]"
COLLISION_MARKER = "[REDACTED:reference_marker_collision]"
INCLUDE_MALFORMED_MARKER = "[REDACTED:include_malformed]"
DEPTH_MARKER = "[TRUNCATED:max_depth]"


def redaction_form(
    canonical_text: str | None, known_secrets: tuple[str, ...] = ()
) -> tuple[str, str]:
    """C' redaction form: at most the canonical scheme survives.

    The scheme is upstream text, so it is kept only when it carries no
    credential word and passes the scanners (H3 sweep).
    """

    match = _SCHEME.match(canonical_text) if isinstance(canonical_text, str) else None
    if match and not credential_text(match[1]) and _scheme_clean(match[1], known_secrets):
        return match[1] + CONNECTION_URI_SUFFIX, "connection_uri"
    return CONNECTION_STRING_MARKER, "connection_string"


def _scheme_clean(scheme: str, known_secrets: tuple[str, ...]) -> bool:
    # H4: the kept scheme is emitted text, so it passes the uniform screen.
    return screen(scheme, known_secrets, position="value").kept


# ---------------------------------------------------------------------------
# Reference markers, includes and the other value scanners.

_SECRET_REF = re.compile(r"!secret (" + SECRET_REFERENCE_NAME.pattern + r")")
_ENV_REF = re.compile(r"!env_var (" + ENV_VAR_REFERENCE_NAME.pattern + r")")
_ENV_REF_DEFAULT = re.compile(
    r"!env_var (" + ENV_VAR_REFERENCE_NAME.pattern + r") .+", re.DOTALL
)
_REFERENCE_LIKE = re.compile(r"\s*(?:!secret|!env_var)", re.IGNORECASE)
_INCLUDE = re.compile(
    r"!include(?:_dir_list|_dir_named|_dir_merge_list|_dir_merge_named)? "
    r"([A-Za-z0-9_./-]{1,256})"
)
_INCLUDE_LIKE = re.compile(r"\s*!include", re.IGNORECASE)
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE|ENCRYPTED PRIVATE KEY|"
    r"OPENSSH PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----",
    re.IGNORECASE,
)
_OPAQUE_BLOB = re.compile(r"[A-Za-z0-9+/=]{200,}")
_COLLISION_KEY_SETS = (
    frozenset({"secret_ref"}),
    frozenset({"env_var_ref"}),
    frozenset({"env_var_ref", "default_redacted"}),
)


def scanner_category(text: str, known_secrets: tuple[str, ...]) -> str | None:
    """Existing sanitizer patterns plus PEM and opaque-blob detection.

    The sanitizer runs with ``identity_markers_only`` (H3): marker-shaped
    text is never skipped as already redacted, and is itself a finding.
    """

    result = sanitize_untrusted_data(
        text, known_secrets=known_secrets, identity_markers_only=True
    )
    if result.failed_closed:
        return "sanitization_failure"
    if result.redaction_applied:
        return sorted(result.redaction_categories)[0]
    if _PEM.search(text):
        return "pem"
    if _OPAQUE_BLOB.search(text):
        return "opaque_blob"
    return None


# ---------------------------------------------------------------------------
# H4: the uniform pipeline.

# Every position a reader response can carry a string in. ``screen`` refuses
# any other position, so a new output field cannot bypass it silently.
OUTPUT_POSITIONS = frozenset(
    {
        # The value tree (L4).
        "value", "list_item", "key", "reference_name",
        # Key paths shown as text.
        "yaml_path", "include_path",
        # The reader envelope.
        "envelope", "limitation", "redaction",
        # The gateway response fields.
        "operation", "summary", "warning", "metadata", "timing", "request_id",
        "error", "error_code", "message", "details", "retryable", "status",
    }
)
_PATH_POSITIONS = frozenset({"yaml_path", "include_path"})
_KEY_POSITIONS = frozenset({"key", "reference_name"})
_VALUE_POSITIONS = frozenset({"value", "list_item"})

# A sealed body's top-level field -> the position of every string under it.
# Keys at every level are screened in position ``key``.
FIELD_POSITIONS: dict[str, str] = {
    "file": "envelope",
    "source": "envelope",
    "found": "envelope",
    "value": "envelope",
    "completeness": "envelope",
    "data": "envelope",
    "yaml_path": "yaml_path",
    "limitations": "limitation",
    "redaction": "redaction",
    "operation": "operation",
    "summary": "summary",
    "warnings": "warning",
    "metadata": "metadata",
    "timing": "timing",
    "request_id": "request_id",
    "error": "error",
    "error_code": "error_code",
    "message": "message",
    "details": "details",
    "retryable": "retryable",
    "success": "status",
}
assert set(FIELD_POSITIONS.values()) <= OUTPUT_POSITIONS

# The rules ``screen`` applies, in order, to every string in every position.
# "sanitizer" is the shared sanitizer's whole rule set (its segment and
# serialized rules and the configured known secrets).
SCANNER_RULES = (
    "credential_key_context",
    "key_scoped",
    "canonicalize",
    "marker_lookalike",
    "credential_text_userinfo",
    "credential_text_word",
    "credential_text_delimited",
    "key_text",
    "connection_context",
    "sanitizer",
    "known_secret_casefold",
    "pem",
    "opaque_blob",
)

# Every category a redaction summary can name.
REDACTION_CATEGORIES = frozenset(
    {
        # The reader's own.
        "credential_key", "encoding", "connection_string", "connection_uri",
        "reference_malformed", "reference_marker_collision", "include_malformed",
        "env_var_default", "pem", "opaque_blob", "sanitization_failure",
        MARKER_COLLISION_CATEGORY,
        # The shared sanitizer's.
        "token", "password", "auth_cookie", "url_credentials",
        "webhook_identifier", "webhook_secret", "auth_flow",
        "matter_setup_payload", "matter_setup_code", "reference_marker",
    }
)
KNOWN_SECRET_MARKER = "[REDACTED:token]"


@dataclass(frozen=True)
class Screened:
    """One ``screen`` decision.

    ``text`` is the input when ``kept``, otherwise its replacement.
    ``projectable`` (keys only): the only finding is a configured known
    secret, so the key may be shown with each occurrence replaced (H1).
    """

    kept: bool
    text: str
    category: str | None = None
    projectable: bool = False


def screen(
    text: str,
    known_secrets: tuple[str, ...],
    *,
    position: str,
    path: tuple[str, ...] = (),
    vocabulary: frozenset[str] = frozenset(),
) -> Screened:
    """H4: the single entry point for every string the reader can emit.

    ``position`` is one of :data:`OUTPUT_POSITIONS`. ``path`` holds the
    canonical keys above the string (for a reference name, the key whose
    value it was). Only a byte-exact match of a source-defined constant in
    ``vocabulary`` is kept without the rules; every other string gets every
    rule of :data:`SCANNER_RULES` on its raw, decoded and canonical forms.
    There is no fast path for already-canonical text. A finding replaces
    the string whole (a key path segment becomes ``*``).
    """

    if position not in OUTPUT_POSITIONS:
        raise ValueError("yaml_screen_position_unknown")
    if type(text) is not str:
        raise TypeError("yaml_screen_text_not_str")
    if text in vocabulary:
        return Screened(True, text)
    finding = _finding(text, known_secrets, position, tuple(path))
    if finding is None:
        return Screened(True, text)
    replacement, category = finding
    if position in _PATH_POSITIONS:
        replacement = PATH_MASK
    return Screened(
        False,
        replacement,
        category,
        position == "key" and _projectable(text, known_secrets, tuple(path)),
    )


def _finding(
    text: str, known_secrets: tuple[str, ...], position: str, path: tuple[str, ...]
) -> tuple[str, str] | None:
    """The first rule of :data:`SCANNER_RULES` that ``text`` triggers."""

    # A reference name may sit under a credential key; the key above it is
    # the context.
    context = path[:-1] if position == "reference_name" else path
    # credential_key_context
    if any(is_credential_key(key) for key in context):
        return CREDENTIAL_MARKER, "credential_key"
    # key_scoped (rule B, shared with the sanitizer)
    normalized = tuple(normalize_key(key) for key in context)
    for index, key in enumerate(normalized):
        category = key_category(key, normalized[:index])
        if category is not None:
            return f"[REDACTED:{category}]", category
    # canonicalize (rule A)
    try:
        canonical_text, rounds, decoded = canonical_forms(text)
    except _EncodingRefused:
        return ENCODING_MARKER, "encoding"
    # marker_lookalike
    if marker_like_text(text):
        return MARKER_COLLISION, MARKER_COLLISION_CATEGORY
    # credential_text_* and key_text
    if position in _PATH_POSITIONS:
        if (
            is_credential_key(canonical_text)
            or key_text_unsafe(canonical_text)
            or credential_text(canonical_text)
        ):
            return CREDENTIAL_MARKER, "credential_key"
    elif position in _KEY_POSITIONS:
        if key_text_unsafe(canonical_text):
            return CONNECTION_STRING_MARKER, "connection_string"
    elif credential_text(canonical_text):
        return redaction_form(canonical_text, known_secrets)
    # connection_context; R11 exempts the exact SQLite value shape from it
    # only, so the scanners below still run on that shape.
    if any(is_connection_key(key) for key in context) and not (
        position in _VALUE_POSITIONS and sqlite_visible(text, canonical_text, rounds)
    ):
        if position in _KEY_POSITIONS:
            return CONNECTION_STRING_MARKER, "connection_string"
        return redaction_form(canonical_text, known_secrets)
    # sanitizer, pem and opaque_blob, on every form
    for form in dict.fromkeys((text, decoded, canonical_text)):
        category = scanner_category(form, known_secrets)
        if category is not None:
            return f"[REDACTED:{category}]", category
    # known_secret_casefold
    if any(secret.casefold() in canonical_text for secret in known_secrets if secret):
        return KNOWN_SECRET_MARKER, "token"
    return None


def context_key(key: str) -> str:
    """A key's canonical form as path context (casefold when refused)."""

    try:
        return canonical(key)[0]
    except _EncodingRefused:
        return key.casefold()


def _projectable(text: str, known_secrets: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """H1: a key whose only finding is a configured known secret.

    The key text with every occurrence removed must pass the whole screen;
    L5 then shows the key with each occurrence replaced by its marker.
    """

    present = sorted(
        {secret for secret in known_secrets if secret and secret in text},
        key=len,
        reverse=True,
    )
    if not present:
        return False
    residue = text
    for secret in present:
        residue = residue.replace(secret, " ")
    return _finding(residue, known_secrets, "key", path) is None


def mint_reference(
    value: str,
    known_secrets: tuple[str, ...],
    registry: ReferenceMarkerRegistry,
    path: tuple[str, ...] = (),
) -> ReferenceMarker | None:
    """Mint a marker from a whole scalar string into ``registry``, or return None.

    This is the only place a marker is created. L5 and rendering trust a
    marker only by its identity in the registry of the same read (H2).
    """

    for pattern, default_redacted in ((_SECRET_REF, False), (_ENV_REF, False)):
        match = pattern.fullmatch(value)
        if match:
            kind = "secret" if pattern is _SECRET_REF else "env_var"
            name = match[1]
            break
    else:
        match = _ENV_REF_DEFAULT.fullmatch(value)
        if not match:
            return None
        kind, name, default_redacted = "env_var", match[1], True
    if not screen(name, known_secrets, position="reference_name", path=path).kept:
        return None
    return registry.mint(kind=kind, name=name, default_redacted=default_redacted)


def reference_like(value: str) -> bool:
    return _REFERENCE_LIKE.match(value) is not None


# ---------------------------------------------------------------------------
# H1: client-visible key paths (include limitations and the requested path).

MAX_PATH_SEGMENTS = 16
PATH_MASK = "*"


def path_segment_safe(
    segment: str, known_secrets: tuple[str, ...], position: str = "yaml_path"
) -> bool:
    """A key-path segment may be shown only if it is bounded and ``screen``
    keeps it (in a path position the segment's own text is also checked as
    a credential key, K1 key text and C' credential text)."""

    if type(segment) is not str or not _SEGMENT.fullmatch(segment):
        return False
    return screen(segment, known_secrets, position=position).kept


def project_path(
    segments: list[str], known_secrets: tuple[str, ...], position: str = "yaml_path"
) -> str:
    """Safe display projection of a key path for client-visible text.

    The first unsafe segment, or a segment past the bound, becomes ``*`` and
    ends the path, so a path never carries key text that the value tree
    would redact.
    """

    shown: list[str] = []
    for segment in segments:
        if len(shown) >= MAX_PATH_SEGMENTS or not path_segment_safe(
            segment, known_secrets, position
        ):
            shown.append(PATH_MASK)
            break
        shown.append(segment)
    return ".".join(shown)


# ---------------------------------------------------------------------------
# L4: path-context redaction over absolute canonical key paths.


@dataclass
class _Accounting:
    redacted: int = 0
    categories: set[str] | None = None
    includes: list[str] | None = None
    depth_truncated: int = 0

    def __post_init__(self) -> None:
        self.categories = set()
        self.includes = []

    def record(self, category: str) -> None:
        self.redacted += 1
        self.categories.add(category)


class _L4:
    def __init__(
        self, known_secrets: tuple[str, ...], registry: ReferenceMarkerRegistry
    ) -> None:
        self.known_secrets = known_secrets
        self.registry = registry
        self.account = _Accounting()

    def redact(
        self,
        value: Any,
        display: list[str],
        cpath: list[str],
        depth: int,
        position: str = "value",
    ) -> Any:
        first_credential = next(
            (index for index, key in enumerate(cpath) if is_credential_key(key)), None
        )
        if first_credential is not None:
            if first_credential == len(cpath) - 1 and type(value) is str:
                marker = mint_reference(
                    value, self.known_secrets, self.registry, tuple(cpath)
                )
                if marker is not None:
                    return self._minted(marker)
            self.account.record("credential_key")
            return self._mark(CREDENTIAL_MARKER)
        if type(value) is dict and frozenset(value) in _COLLISION_KEY_SETS:
            self.account.record("reference_marker_collision")
            return self._mark(COLLISION_MARKER)
        connection = any(is_connection_key(key) for key in cpath)
        if type(value) is dict:
            if connection:
                self.account.record("connection_string")
                return self._mark(CONNECTION_STRING_MARKER)
            canonical_keys = []
            for key in value:
                # H4: every key, canonical or not, gets the whole screen. A
                # finding withholds the whole mapping, except a key whose
                # only finding is a known secret: L5 projects it (H1).
                screened = screen(
                    str(key), self.known_secrets, position="key", path=tuple(cpath)
                )
                if not screened.kept and not screened.projectable:
                    self.account.record(screened.category)
                    return self._mark(screened.text)
                canonical_keys.append(context_key(str(key)))
            if value and depth >= MAX_DEPTH:
                self.account.depth_truncated += 1
                return self._mark(DEPTH_MARKER)
            return {
                key: self.redact(child, display + [str(key)], cpath + [canonical_key], depth + 1)
                for (key, child), canonical_key in zip(value.items(), canonical_keys)
            }
        if type(value) is list:
            if connection:
                self.account.record("connection_string")
                return self._mark(CONNECTION_STRING_MARKER)
            if value and depth >= MAX_DEPTH:
                self.account.depth_truncated += 1
                return self._mark(DEPTH_MARKER)
            # List elements are transparent: they inherit the holding key's path.
            return [
                self.redact(child, display, cpath, depth + 1, "list_item")
                for child in value
            ]
        if type(value) is str:
            return self._string(value, display, cpath, position)
        if connection:
            self.account.record("connection_string")
            return self._mark(CONNECTION_STRING_MARKER)
        if value is None or type(value) in (bool, int, float):
            return value
        self.account.record("encoding")
        return self._mark(ENCODING_MARKER)

    def _mark(self, text: str) -> EngineeringMarker:
        # H3: L5 and rendering trust this marker by registry identity only.
        return self.registry.mint_marker(text)

    def _minted(self, marker: ReferenceMarker) -> ReferenceMarker:
        if marker.default_redacted:
            # The default is discarded here; only the fact is recorded.
            self.account.record("env_var_default")
        return marker

    def _string(
        self, value: str, display: list[str], cpath: list[str], position: str
    ) -> Any:
        marker = mint_reference(value, self.known_secrets, self.registry, tuple(cpath))
        if marker is not None:
            return self._minted(marker)
        if reference_like(value):
            self.account.record("reference_malformed")
            return self._mark(REFERENCE_MALFORMED_MARKER)
        if _INCLUDE.fullmatch(value):
            self._include(display)
        elif _INCLUDE_LIKE.match(value):
            self.account.record("include_malformed")
            return self._mark(INCLUDE_MALFORMED_MARKER)
        # H4: the uniform screen (rule A, marker look-alikes, C', connection
        # context with the R11 SQLite exemption from rule B only, and every
        # scanner on every form).
        screened = screen(value, self.known_secrets, position=position, path=tuple(cpath))
        if not screened.kept:
            self.account.record(screened.category)
            return self._mark(screened.text)
        # Visible values are emitted raw, never in canonical form.
        return value

    def _include(self, display: list[str]) -> None:
        # H1: the path is upstream key text, so it gets the key pipeline.
        self.account.includes.append(
            project_path(display, self.known_secrets, "include_path")
        )


def _count_nodes(value: Any) -> int:
    total = 0
    stack = [value]
    while stack:
        node = stack.pop()
        total += 1
        if type(node) is dict:
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
    return total


def truncate_breadth_first(value: Any, cap: int = MAX_NODES) -> tuple[Any, int]:
    """Keep the first ``cap`` nodes in breadth-first order.

    Returns the kept tree and the number of omitted nodes. Children of one
    container are enqueued together, so omitted children are always a
    suffix of their container. It runs on the decoded upstream tree, before
    any redaction or minting (H2): its output is plain JSON data, so L4
    judges the truncated shapes and nothing it emits can be a marker.
    """

    total = _count_nodes(value)
    if total <= cap:
        return value, 0

    def shell(node: Any) -> Any:
        if type(node) is dict:
            return {}
        if type(node) is list:
            return []
        return node

    root = shell(value)
    kept = 1
    queue: deque[tuple[Any, Any]] = deque([(value, root)])
    while queue and kept < cap:
        source, target = queue.popleft()
        if type(source) is dict:
            for key, child in source.items():
                if kept >= cap:
                    break
                target[key] = shell(child)
                kept += 1
                queue.append((child, target[key]))
        elif type(source) is list:
            for child in source:
                if kept >= cap:
                    break
                target.append(shell(child))
                kept += 1
                queue.append((child, target[-1]))
    return root, total - kept


def _relabeled_count(before: Any, after: Any, registry: ReferenceMarkerRegistry) -> int:
    """Fields L4 already redacted that L5 replaced again under its own key rule.

    L5 keeps the tree shape, so a parallel walk finds them; each such field
    was counted once by L4 and must not be counted again. An L4 redaction is
    recognized by registry identity, never by marker text (H3).
    """

    if type(before) is dict and type(after) is dict:
        return sum(
            _relabeled_count(child, after[key], registry)
            for key, child in before.items()
            if key in after
        )
    if type(before) is list and type(after) is list:
        return sum(_relabeled_count(a, b, registry) for a, b in zip(before, after))
    return int(registry.emitted(before) and after is not before)


def render_markers(
    value: Any, registry: ReferenceMarkerRegistry, account: _Accounting
) -> Any:
    """Serialize minted markers after L5; nothing sanitizes after this.

    Only a marker this read's registry minted renders as a reference (H2)
    or as its redaction text (H3). Any other marker, and any plain mapping
    that has acquired a rendered marker's key set, is withheld: no
    transform can forge a reference or a redaction.
    """

    if type(value) is EngineeringMarker or isinstance(value, EngineeringMarker):
        if not registry.emitted(value) or not value.valid():
            account.record(MARKER_COLLISION_CATEGORY)
            return MARKER_COLLISION
        return value.text

    if type(value) is ReferenceMarker or isinstance(value, ReferenceMarker):
        if not registry.minted(value) or not value.valid():
            account.record("reference_marker_collision")
            return COLLISION_MARKER
        if value.kind == "secret":
            return {"secret_ref": value.name}
        rendered: dict[str, Any] = {"env_var_ref": value.name}
        if value.default_redacted:
            rendered["default_redacted"] = True
        return rendered
    if type(value) is dict:
        if frozenset(value) in _COLLISION_KEY_SETS:
            account.record("reference_marker_collision")
            return COLLISION_MARKER
        return {key: render_markers(child, registry, account) for key, child in value.items()}
    if type(value) is list:
        return [render_markers(child, registry, account) for child in value]
    return value


# ---------------------------------------------------------------------------
# Response decoding, identity validation and error classification (K3).


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValueError("non_finite_number")


def _strict_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicates,
        parse_constant=_reject_constant,
    )


def _canonical_text(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _single_text(call_result: Any) -> str:
    content = call_result.get("content") if type(call_result) is dict else None
    if (
        type(content) is not list
        or len(content) != 1
        or type(content[0]) is not dict
        or content[0].get("type") != "text"
        or type(content[0].get("text")) is not str
    ):
        raise YamlReadRefusal(IDENTITY_INVALID)
    return content[0]["text"]


_SUCCESS_KEYS = frozenset(
    {"success", "yaml_path", "matches", "count", "files_searched",
     "files_unreadable", "warnings"}
)
_MATCH_KEYS = frozenset({"file", "yaml_path", "parsed"})


def decode_success(call_result: Any, yaml_path: str) -> tuple[bool, Any]:
    """Validate the exact success identity; return (found, parsed)."""

    text = _single_text(call_result)
    try:
        payload = _strict_json(text)
        structured = call_result.get("structuredContent")
        if structured is not None and _canonical_text(structured) != _canonical_text(payload):
            raise YamlReadRefusal(IDENTITY_INVALID)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise YamlReadRefusal(IDENTITY_INVALID) from None
    if type(payload) is not dict or not set(payload) <= _SUCCESS_KEYS:
        raise YamlReadRefusal(IDENTITY_INVALID)
    if not {"success", "yaml_path", "matches", "count", "files_searched",
            "files_unreadable"} <= set(payload):
        raise YamlReadRefusal(IDENTITY_INVALID)
    matches = payload["matches"]
    count = payload["count"]
    if (
        payload["success"] is not True
        or payload["yaml_path"] != yaml_path
        or type(payload["yaml_path"]) is not str
        or type(payload["files_searched"]) is not int
        or payload["files_searched"] != 1
        or type(payload["files_unreadable"]) is not int
        or payload["files_unreadable"] != 0
        or type(matches) is not list
        or type(count) is not int
        or count != len(matches)
        or count not in (0, 1)
    ):
        raise YamlReadRefusal(IDENTITY_INVALID)
    if "warnings" in payload and payload["warnings"] != []:
        raise YamlReadRefusal(IDENTITY_INVALID)
    if not matches:
        return False, None
    match = matches[0]
    if (
        type(match) is not dict
        or set(match) != _MATCH_KEYS
        or match["file"] != ALLOWED_FILE
        or match["yaml_path"] != yaml_path
    ):
        raise YamlReadRefusal(IDENTITY_INVALID)
    return True, match["parsed"]


_PARSE_POSITION = re.compile(r"at line (\d{1,7}), column (\d{1,7})")


def classify_error(call_result: Any) -> YamlReadRefusal:
    """K2/K3/K4: map an upstream error to a fixed code; no upstream text."""

    try:
        payload = _strict_json(_single_text(call_result))
    except (YamlReadRefusal, RecursionError, TypeError, UnicodeError, ValueError):
        return YamlReadRefusal(READ_FAILED)
    if type(payload) is not dict or payload.get("success") is not False:
        return YamlReadRefusal(READ_FAILED)
    error = payload.get("error")
    if type(error) is str:
        # Component string error.
        if error.startswith("Path not allowed"):
            return YamlReadRefusal(FILE_NOT_ALLOWED_UPSTREAM)
        if error.startswith("File does not exist"):
            return YamlReadRefusal(FILE_NOT_FOUND)
        return YamlReadRefusal(READ_FAILED)
    if type(error) is dict:
        # ha-mcp structured error object.
        code = error.get("code")
        if code == "CONFIG_INVALID":
            message = error.get("message")
            position = (
                _PARSE_POSITION.search(message) if type(message) is str else None
            )
            details = (
                {"line": int(position[1]), "column": int(position[2])}
                if position
                else {}
            )
            return YamlReadRefusal(PARSE_ERROR, details=details)
        if code == "VALIDATION_FAILED":
            return YamlReadRefusal(VALIDATION_OR_UNAVAILABLE)
    return YamlReadRefusal(READ_FAILED)


# ---------------------------------------------------------------------------
# The whole success projection: L4, caps, L5, rendering and the envelope.


# H1: the source-defined text of the envelope. ``seal`` keeps these strings
# only where its input is byte-equal to one of them.
ENVELOPE_VOCABULARY = frozenset(
    {
        "file",
        "yaml_path",
        "source",
        "found",
        "value",
        "completeness",
        "limitations",
        "redaction",
        "redacted_field_count",
        "categories",
        "line",
        "column",
        "failure_category",
        ALLOWED_FILE,
        "disk",
        "complete",
        "partial",
        "include_not_followed_truncated",
        K7_WARNING,
        *DISCLOSURE_LIMITATIONS,
        *(code for code in FAILURES),
        *(category for category, _message in FAILURES.values()),
        *(message for _category, message in FAILURES.values()),
    }
)


# H4: the constants ``seal`` keeps without the rules: the envelope text, the
# field names and the redaction categories a summary can name.
SEAL_VOCABULARY = ENVELOPE_VOCABULARY | REDACTION_CATEGORIES | frozenset(FIELD_POSITIONS)
SOURCE = "disk"


@dataclass(frozen=True)
class YamlReadResult:
    envelope: dict[str, Any]
    redacted_field_count: int
    redaction_categories: tuple[str, ...]
    partial: bool
    sanitization_failed: bool = False


def seal(
    value: dict[str, Any],
    known_secrets: tuple[str, ...],
    vocabulary: frozenset[str] = frozenset(),
) -> tuple[Any, int, set[str]]:
    """H1/H4: the final pass over a body of client-visible fields.

    ``value`` is a mapping of top-level fields named in
    :data:`FIELD_POSITIONS`; an unknown field fails the read. Every key and
    string in it passes :func:`screen` in its field's position, with its key
    path as context; only an exact source-defined constant of ``vocabulary``
    (``SEAL_VOCABULARY`` plus the gateway's own constants) is kept without
    the rules. Its text is the same for every configuration and upstream,
    so it cannot carry upstream, caller or configured text. The shared
    sanitizer also runs on the whole body and the walk is parallel to its
    output: a shape change fails the read, and a string it changed that the
    screen kept takes its value (defense in depth). A number or flag under
    a credential or connection key becomes a marker. Returns the sealed
    value and the count and categories of the changes.
    """

    if type(value) is not dict:
        raise YamlReadRefusal(READ_FAILED)
    vocabulary = SEAL_VOCABULARY | vocabulary
    # H3: no input text counts as already redacted; marker-shaped text is
    # replaced whole.
    result = sanitize_untrusted_data(
        value, known_secrets=known_secrets, identity_markers_only=True
    )
    if result.failed_closed:
        raise YamlReadRefusal(READ_FAILED)
    walk = _Seal(
        known_secrets,
        vocabulary,
        min(result.redaction_categories, default="sanitization_failure"),
    )
    sealed = walk.node(value, result.value, None, ())
    return sealed, walk.account.redacted, set(walk.account.categories)


class _Seal:
    def __init__(
        self, known_secrets: tuple[str, ...], vocabulary: frozenset[str], l5_category: str
    ) -> None:
        self.known_secrets = known_secrets
        self.vocabulary = vocabulary
        self.l5_category = l5_category
        self.account = _Accounting()

    def node(
        self, original: Any, cleaned: Any, position: str | None, path: tuple[str, ...]
    ) -> Any:
        if type(original) is str:
            if type(cleaned) is not str or position is None:
                raise YamlReadRefusal(READ_FAILED)
            screened = screen(
                original,
                self.known_secrets,
                position=position,
                path=path,
                vocabulary=self.vocabulary,
            )
            if not screened.kept:
                # Byte-identical replacement text is not a visible change.
                if screened.text != original:
                    self.account.record(screened.category)
                return screened.text
            if original not in self.vocabulary and cleaned != original:
                self.account.record(self.l5_category)
                return cleaned
            return original
        if type(original) is dict:
            if type(cleaned) is not dict or len(cleaned) != len(original):
                raise YamlReadRefusal(READ_FAILED)
            sealed: dict[str, Any] = {}
            for (key, child), (cleaned_key, cleaned_child) in zip(
                original.items(), cleaned.items()
            ):
                if type(key) is not str or type(cleaned_key) is not str:
                    raise YamlReadRefusal(READ_FAILED)
                child_position = position
                if position is None:
                    child_position = FIELD_POSITIONS.get(key)
                    if child_position is None:
                        raise YamlReadRefusal(READ_FAILED)
                shown = self.key(key, cleaned_key, path)
                if shown in sealed:
                    raise YamlReadRefusal(READ_FAILED)
                sealed[shown] = self.node(
                    child, cleaned_child, child_position, path + (context_key(key),)
                )
            return sealed
        if type(original) is list:
            if type(cleaned) is not list or len(cleaned) != len(original) or position is None:
                raise YamlReadRefusal(READ_FAILED)
            return [
                self.node(child, cleaned_child, position, path)
                for child, cleaned_child in zip(original, cleaned)
            ]
        if (original is None or type(original) in (bool, int, float)) and position is not None:
            if any(is_credential_key(key) or is_connection_key(key) for key in path):
                self.account.record("credential_key")
                return CREDENTIAL_MARKER
            if type(cleaned) is not type(original) or cleaned != original:
                self.account.record(self.l5_category)
                return cleaned
            return original
        raise YamlReadRefusal(READ_FAILED)

    def key(self, key: str, cleaned_key: str, path: tuple[str, ...]) -> str:
        screened = screen(
            key, self.known_secrets, position="key", path=path, vocabulary=self.vocabulary
        )
        if screened.kept:
            if key not in self.vocabulary and cleaned_key != key:
                self.account.record(self.l5_category)
                return cleaned_key
            return key
        if screened.text != key:
            self.account.record(screened.category)
        if screened.projectable and cleaned_key != key:
            # H1: L5 replaced each known-secret occurrence in the key.
            return cleaned_key
        return screened.text


def seal_response(
    body: dict[str, Any],
    known_secrets: tuple[str, ...],
    vocabulary: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """H1: seal every gateway response field except the reader's own value.

    ``data`` on success is the ``project_success`` envelope, already sealed
    field by field; it is reinserted unchanged so its rendered markers stay
    intact (R8). Every other field (summary, warnings, metadata, error,
    message, details, timing, request id) passes ``seal``. Key order is kept.
    """

    # The ``data`` key itself still passes; only its value is held back.
    rest = {key: None if key == "data" else child for key, child in body.items()}
    sealed, _count, _categories = seal(rest, known_secrets, vocabulary | {"data"})
    if list(sealed) != list(rest):
        raise YamlReadRefusal(READ_FAILED)
    return {key: body["data"] if key == "data" else sealed[key] for key in body}


def project_success(
    call_result: Any,
    yaml_path: str,
    *,
    known_secrets: tuple[str, ...] = (),
    max_string: int | None = None,
) -> YamlReadResult:
    found, parsed = decode_success(call_result, yaml_path)
    registry = ReferenceMarkerRegistry()
    limitations: list[str] = []
    redacted = 0
    categories: set[str] = set()
    value: Any = None
    partial = False
    sanitization_failed = False
    if found:
        # H2: truncation runs first, on the decoded upstream tree. Redaction
        # then judges exactly the shapes that can be returned, and work is
        # bounded by the node cap.
        kept, omitted = truncate_breadth_first(parsed)
        if omitted:
            partial = True
            limitations.append(f"value_truncated:{omitted} nodes omitted")
        prefix = yaml_path.split(".")
        walker = _L4(known_secrets, registry)
        redacted_tree = walker.redact(kept, list(prefix), [segment.lower() for segment in prefix], 1)
        account = walker.account
        if account.depth_truncated:
            partial = True
            limitations.append(f"value_depth_limited:{MAX_DEPTH}")
        includes = sorted(set(account.includes))
        if includes:
            partial = True
            limitations.extend(
                f"include_not_followed:{path}"
                for path in includes[:MAX_INCLUDE_LIMITATIONS]
            )
            if len(includes) > MAX_INCLUDE_LIMITATIONS:
                limitations.append("include_not_followed_truncated")
        sanitation = sanitize_untrusted_data(
            redacted_tree,
            known_secrets=known_secrets,
            max_string=max_string,
            preserve_reference_markers=registry,
            identity_markers_only=True,
        )
        sanitization_failed = sanitation.failed_closed
        if sanitation.truncated_field_count:
            partial = True
            limitations.append(
                f"strings_truncated:{sanitation.truncated_field_count}"
            )
        value = render_markers(sanitation.value, registry, account)
        redacted += account.redacted + sanitation.redacted_field_count - _relabeled_count(
            redacted_tree, sanitation.value, registry
        )
        categories |= account.categories | set(sanitation.redaction_categories)
    limitations.extend(DISCLOSURE_LIMITATIONS)
    # H1/H4: every envelope field passes the final pass. The value (already
    # screened by L4 and through L5 above) is held back as None so its
    # rendered markers stay intact; the requested path gets the key-path
    # projection.
    fields, sealed, sealed_categories = seal(
        {
            "file": ALLOWED_FILE,
            "yaml_path": project_path(yaml_path.split("."), known_secrets),
            "source": SOURCE,
            "found": found,
            "value": None,
            "completeness": "partial" if partial else "complete",
            "limitations": limitations,
        },
        known_secrets,
    )
    redacted += sealed
    categories |= sealed_categories
    summary, _count, _categories = seal(
        {"redaction": {"redacted_field_count": redacted, "categories": sorted(categories)}},
        known_secrets,
    )
    envelope = {
        "file": fields["file"],
        "yaml_path": fields["yaml_path"],
        "source": fields["source"],
        "found": fields["found"],
        "value": value,
        "completeness": fields["completeness"],
        "limitations": fields["limitations"],
        "redaction": summary["redaction"],
    }
    return YamlReadResult(
        envelope=envelope,
        redacted_field_count=redacted,
        redaction_categories=tuple(sorted(categories)),
        partial=partial,
        sanitization_failed=sanitization_failed,
    )


# ---------------------------------------------------------------------------
# Optional reviewed-tool admission (design §5.2, owner decisions D2 and D7).
#
# Bindings are compiled from the D1 capture (docs/evidence/hamcp162). They are
# never derived from a live observation: an observed descriptor can only
# match a binding, never create or widen one.

BINDING_MODEL = "hamcp162-optional-tool-binding-v1"
RUNTIME_MODEL = "ha-mcp-operational-tool-descriptor-v2"
ARGUMENT_CONSTRAINTS_ID = "hamcp162-yaml-fragment-read-arguments-v1"
INPUT_SCHEMA_FINGERPRINT = (
    "29c61932fef093a1c826aa150b9824724326fe38fb1d5e7fcbed5083d54f9b9a"
)
TOP_LEVEL_FIELDS = (
    "_meta", "annotations", "description", "inputSchema", "name",
    "outputSchema", "title",
)
_GET_YAML_RUNTIME = (
    "75d623c93573aee91b9d78723f71ac1de30c688efda78bb48033dd62a196d007"
)
_SAME = "f31334156d7e5209dfc90f4f42f19e829fa93f75741665019395b045576b09a5"
_TITLE_FP = "b5a58f2039cce78043c7e31875567abf2a0b4114ea7880071aaa09ab791cbce8"
# Per-component hashes for reason codes and diagnostics only. They never
# decide admission (R4).
_GET_YAML_DIAGNOSTICS = {
    "/_meta": "f535d29daa8306a1a10d58af032e7b650ca8ac8760558638c5515ed4af64bb55",
    "/_meta/fastmcp/tags": "dfe77884725873810db0f7b4def09c1f98b92f9d7fea790137b93b5745b00e1e",
    "/_meta/ha_mcp/llm_api_exposed": _SAME,
    "/_meta/ha_mcp/pinned": _SAME,
    "/_meta/ha_mcp/policy/deployment": "2e2c0ec055a41040c4442e9937319e1dce57868490940812a7cdeecaaebe9e9c",
    "/_meta/ha_mcp/policy/enabled": _SAME,
    "/_meta/ha_mcp/policy/live": _SAME,
    "/_meta/ha_mcp/policy/rules": "811b653edaf0d2809f19593484e4555c471f955271369b69cdeb3262696470e6",
    "/annotations": "3b57d136bd405ccf36a181b0c7aeecd323a5544ecff2e199c88e6d1aff54b950",
    "/annotations/title": _TITLE_FP,
    "/description": "2e4d072d031700da738a26ea8578aabfca6b8477ec607505238c950eb8893674",
    "/execution": "f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109",
    "/inputSchema": INPUT_SCHEMA_FINGERPRINT,
    "/outputSchema": "7751ca245b89acd7d145f5c7b68cb80458b8d38bfe36a965a332f56bc2525a8c",
    "/title": _TITLE_FP,
}
DIAGNOSTIC_POINTERS = tuple(sorted(_GET_YAML_DIAGNOSTICS))
# The four dynamic policy pointers are projected by the v2 model; a
# difference there is never named as the drift reason.
_POLICY_POINTERS = frozenset(
    pointer for pointer in DIAGNOSTIC_POINTERS
    if pointer.startswith("/_meta/ha_mcp/policy/")
)
_DIAGNOSTIC_REASONS = (
    ("/inputSchema", "optional_input_schema_changed"),
    ("/outputSchema", "optional_output_schema_changed"),
    ("/description", "optional_description_changed"),
    ("/annotations", "optional_annotations_changed"),
    ("/title", "optional_title_changed"),
    ("/execution", "optional_execution_changed"),
    ("/_meta", "optional_meta_changed"),
)


@dataclass(frozen=True)
class OptionalToolBinding:
    version: str
    tool_name: str
    classification: str
    adapter_token: str | None
    runtime_contract_fingerprint: str
    runtime_contract_fingerprint_model: str
    top_level_fields: tuple[str, ...]
    source_commit: str
    capture_resource: str
    capture_sha256: str
    binding_model: str = BINDING_MODEL


_CAPTURES = {
    "8.4.3": (
        "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
        "docs/evidence/hamcp162/captures/catalog-8.4.3-A.json",
        "d5d6a5393dae9ed420d906e6b7e63be5ade7ca3ceed56e64f7f63aec50210303",
    ),
    "8.5.0": (
        "311d6dc273fb4e9a5b8cde0de15f69472a64fe44",
        "docs/evidence/hamcp162/captures/catalog-8.5.0-A.json",
        "4321a9e0f609084d7e1fe9885a767907c5ad3bfea0712186b4a4585eeb3d31f1",
    ),
}
_SIBLING_RUNTIME = {
    ("8.4.3", "ha_list_files"): "71e51bafd62d0837b7f2056b7a8bd007aa20b0c8c42c169981a8af1796c0e083",
    ("8.5.0", "ha_list_files"): "71e51bafd62d0837b7f2056b7a8bd007aa20b0c8c42c169981a8af1796c0e083",
    ("8.4.3", "ha_read_file"): "97459dd33eca15079f42cce074485c821752fad85c0c6a3f545544e42adecae1",
    ("8.5.0", "ha_read_file"): "7627857f25d581e75c41bed2cad2ddf9b41839b69b8942c033c150c17cbf92a6",
}


def _binding(version: str, tool: str, runtime: str, token: str | None) -> OptionalToolBinding:
    commit, resource, digest = _CAPTURES[version]
    return OptionalToolBinding(
        version=version,
        tool_name=tool,
        classification=(
            "mixed_or_requires_wrapper" if tool == TOOL_NAME else "prohibited"
        ),
        adapter_token=token,
        runtime_contract_fingerprint=runtime,
        runtime_contract_fingerprint_model=RUNTIME_MODEL,
        top_level_fields=TOP_LEVEL_FIELDS,
        source_commit=commit,
        capture_resource=resource,
        capture_sha256=digest,
    )


OPTIONAL_TOOL_BINDINGS: dict[tuple[str, str], OptionalToolBinding] = {
    **{
        (version, TOOL_NAME): _binding(
            version, TOOL_NAME, _GET_YAML_RUNTIME,
            f"ha-mcp-{version}-yaml-fragment-read-v1",
        )
        for version in _CAPTURES
    },
    **{
        (version, tool): _binding(version, tool, runtime, None)
        for (version, tool), runtime in _SIBLING_RUNTIME.items()
    },
}
ADAPTER_TOKENS = {
    binding.adapter_token: binding
    for binding in OPTIONAL_TOOL_BINDINGS.values()
    if binding.adapter_token is not None
}
# One explicit disposition for every registry version (HAMCP-184 rule): no
# default exists, and a disposition is never inferred from provider
# dispositions. 8.6.0 stays held until its HOLD lifts (D7).
OPTIONAL_TOOL_DISPOSITIONS: dict[str, str] = {
    "7.14.1": "held",
    "7.14.2": "held",
    "8.0.0": "held",
    "8.1.0": "held",
    "8.1.1": "held",
    "8.2.0": "held",
    "8.4.1": "held",
    "8.4.3": "admitted",
    "8.5.0": "admitted",
    "8.6.0": "held",
}
OPTIONAL_NAMES = (TOOL_NAME,) + SIBLING_TOOLS


class OptionalBindingError(ValueError):
    """The compiled optional-tool tables disagree with the release registry."""


def validate_bindings_against_registry(registry: Any) -> None:
    """Load assertions: exact version coverage and matching models."""

    versions = {release.version for release in registry.releases}
    if set(OPTIONAL_TOOL_DISPOSITIONS) != versions:
        raise OptionalBindingError("optional_disposition_versions_mismatch")
    if any(
        value not in {"admitted", "held"}
        for value in OPTIONAL_TOOL_DISPOSITIONS.values()
    ):
        raise OptionalBindingError("optional_disposition_invalid")
    by_version = {release.version: release for release in registry.releases}
    for (version, tool), binding in OPTIONAL_TOOL_BINDINGS.items():
        release = by_version.get(version)
        if release is None or binding.version != version or binding.tool_name != tool:
            raise OptionalBindingError("optional_binding_version_unknown")
        if binding.runtime_contract_fingerprint_model != (
            release.runtime_contract_fingerprint_model
        ):
            raise OptionalBindingError("optional_binding_model_mismatch")
        if binding.binding_model != BINDING_MODEL:
            raise OptionalBindingError("optional_binding_model_invalid")
    for version, disposition in OPTIONAL_TOOL_DISPOSITIONS.items():
        if disposition == "admitted" and (version, TOOL_NAME) not in OPTIONAL_TOOL_BINDINGS:
            raise OptionalBindingError("optional_admitted_binding_absent")


def binding_contract_fingerprint(binding: OptionalToolBinding, disposition: str) -> str:
    from ..ha_mcp_readmission.models import evidence_fingerprint

    return evidence_fingerprint(
        _contract_material(
            binding,
            runtime=binding.runtime_contract_fingerprint,
            top_level_fields=binding.top_level_fields,
            disposition=disposition,
        )
    )


def descriptor_diagnostic_fingerprints(tool: Any) -> dict[str, str]:
    from ..upstream_tool_policy import schema_fingerprint

    values: dict[str, str] = {}
    for pointer in DIAGNOSTIC_POINTERS:
        current: Any = tool
        present = True
        for name in pointer.lstrip("/").split("/"):
            if not isinstance(current, dict) or name not in current:
                present = False
                current = None
                break
            current = current[name]
        try:
            values[pointer] = schema_fingerprint(
                {"present": present, "value": current if present else None}
            )
        except (TypeError, ValueError, UnicodeError, OverflowError):
            values[pointer] = "invalid"
    return values


def _observed_runtime(tool: dict[str, Any], model: str) -> str | None:
    from ..upstream_tool_policy import runtime_contract_fingerprint

    try:
        return runtime_contract_fingerprint(tool, model=model)
    except Exception:
        return None


@dataclass(frozen=True)
class OptionalMatch:
    status: str  # "matched" | "absent" | "drift"
    reason: str | None
    observed_runtime_contract_fingerprint: str | None


def match_descriptor(tools: Any, binding: OptionalToolBinding) -> OptionalMatch:
    """R4: exactly one occurrence, equal top-level keys, equal v2 fingerprint."""

    occurrences = [
        item for item in tuple(tools)
        if isinstance(item, dict) and item.get("name") == binding.tool_name
    ]
    if not occurrences:
        return OptionalMatch("absent", None, None)
    if len(occurrences) > 1:
        return OptionalMatch("drift", "optional_descriptor_duplicate", None)
    tool = occurrences[0]
    observed = _observed_runtime(tool, binding.runtime_contract_fingerprint_model)
    if tuple(sorted(str(key) for key in tool)) != binding.top_level_fields:
        return OptionalMatch("drift", "optional_descriptor_fields_changed", observed)
    if observed == binding.runtime_contract_fingerprint:
        return OptionalMatch("matched", None, observed)
    reason = "optional_descriptor_contract_changed"
    if binding.tool_name == TOOL_NAME:
        diagnostics = descriptor_diagnostic_fingerprints(tool)
        changed = {
            pointer for pointer in DIAGNOSTIC_POINTERS
            if pointer not in _POLICY_POINTERS
            and diagnostics.get(pointer) != _GET_YAML_DIAGNOSTICS[pointer]
        }
        for prefix, code in _DIAGNOSTIC_REASONS:
            if any(pointer == prefix or pointer.startswith(prefix + "/") for pointer in changed):
                reason = code
                break
    return OptionalMatch("drift", reason, observed)


@dataclass(frozen=True)
class OptionalAdmission:
    """The evaluated optional-tool state for one catalog and release.

    ``status`` is one of the ``server_info`` values: ``admitted``,
    ``optional_absent``, ``quarantined:<reason>``, ``held``,
    ``withheld:<reason>`` or ``prohibited_withheld`` (siblings).
    """

    version: str | None
    tools: tuple[tuple[str, str], ...]
    binding: OptionalToolBinding | None
    contract_fingerprint: str | None
    observed_tool: dict[str, Any] | None
    accounted_names: tuple[str, ...]

    @property
    def status(self) -> str:
        return dict(self.tools)[TOOL_NAME]

    @property
    def admitted(self) -> bool:
        return self.status == "admitted" and self.binding is not None


def withheld_reason(release: Any, *, signed_selection: bool) -> str | None:
    """Admission rules 1-4; ``None`` means the extension may be attached."""

    if release is None:
        return "optional_binding_absent"
    if signed_selection:
        return "optional_signed_selection"
    if release.provider_disposition("read_gateway") != "admitted":
        return "optional_disposition_held"
    disposition = OPTIONAL_TOOL_DISPOSITIONS.get(release.version)
    if disposition != "admitted":
        return "optional_disposition_held"
    binding = OPTIONAL_TOOL_BINDINGS.get((release.version, TOOL_NAME))
    if binding is None:
        return "optional_binding_absent"
    if binding.source_commit != release.source_commit:
        return "optional_source_commit_mismatch"
    return None


def evaluate_optional(
    tools: Any,
    release: Any,
    *,
    signed_selection: bool = False,
) -> OptionalAdmission:
    """Evaluate the optional reader and its prohibited siblings.

    The result never touches stock accounting. Only an exact R4 match under
    an admitted disposition and binding yields ``admitted``.
    """

    version = getattr(release, "version", None)
    observed_names = {
        item.get("name") for item in tuple(tools) if isinstance(item, dict)
    }
    states: dict[str, str] = {}
    accounted: list[str] = []
    for sibling in SIBLING_TOOLS:
        states[sibling] = (
            "prohibited_withheld" if sibling in observed_names else "optional_absent"
        )
        if (version, sibling) in OPTIONAL_TOOL_BINDINGS and sibling in observed_names:
            accounted.append(sibling)
    binding = OPTIONAL_TOOL_BINDINGS.get((version, TOOL_NAME))
    if binding is not None and TOOL_NAME in observed_names:
        accounted.append(TOOL_NAME)
    observed_tool = None
    contract = None
    if TOOL_NAME not in observed_names:
        states[TOOL_NAME] = "optional_absent"
    else:
        reason = withheld_reason(release, signed_selection=signed_selection)
        if reason == "optional_disposition_held":
            states[TOOL_NAME] = "held"
        elif reason is not None:
            states[TOOL_NAME] = f"withheld:{reason}"
        else:
            match = match_descriptor(tools, binding)
            if match.status == "matched":
                states[TOOL_NAME] = "admitted"
                observed_tool = next(
                    item for item in tuple(tools)
                    if isinstance(item, dict) and item.get("name") == TOOL_NAME
                )
                contract = binding_contract_fingerprint(binding, "admitted")
            else:
                states[TOOL_NAME] = f"quarantined:{match.reason}"
    return OptionalAdmission(
        version=version,
        tools=tuple(sorted(states.items())),
        binding=binding if states[TOOL_NAME] == "admitted" else None,
        contract_fingerprint=contract,
        observed_tool=observed_tool,
        accounted_names=tuple(sorted(accounted)),
    )


def sibling_drift(tools: Any, version: str | None) -> tuple[tuple[str, str], ...]:
    """Report drift in a prohibited sibling. It never changes admission."""

    drift = []
    for sibling in SIBLING_TOOLS:
        binding = OPTIONAL_TOOL_BINDINGS.get((version, sibling))
        if binding is None:
            continue
        match = match_descriptor(tools, binding)
        if match.status == "drift":
            drift.append((sibling, match.reason))
    return tuple(drift)


def is_yaml_adapter(entry: Any) -> bool:
    restrictions = getattr(entry, "argument_restrictions", ())
    binding = ADAPTER_TOKENS.get(restrictions[0]) if len(restrictions) == 1 else None
    return (
        binding is not None
        and entry.upstream_name == TOOL_NAME
        and entry.exposed_name == TOOL_NAME
        and entry.classification == "mixed_or_requires_wrapper"
        and entry.input_schema_fingerprint == INPUT_SCHEMA_FINGERPRINT
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    )


def synthesized_policy_entry(binding: OptionalToolBinding) -> Any:
    """The binary-owned route entry for an admitted binding.

    It is built at runtime and never added to a policy document, so stock
    policy digests, counts and order fingerprints are unchanged.
    """

    from ..upstream_tool_policy import ReviewedToolAnnotations, UpstreamToolPolicyEntry

    if binding.tool_name != TOOL_NAME or binding.adapter_token is None:
        raise OptionalBindingError("optional_binding_not_routable")
    return UpstreamToolPolicyEntry(
        upstream_name=TOOL_NAME,
        exposed_name=TOOL_NAME,
        description=DESCRIPTION,
        classification="mixed_or_requires_wrapper",
        input_schema_fingerprint=INPUT_SCHEMA_FINGERPRINT,
        reason=(
            "HAMCP-162 optional reviewed tool: configuration.yaml fragment "
            "read through a closed Engineering-built argument shape."
        ),
        collision_status="none",
        collision_policy="alias_upstream_on_collision",
        argument_restrictions=(binding.adapter_token,),
        response_limit_bytes=60_000,
        timeout_seconds=60.0,
        source_evidence=(binding.capture_resource, binding.capture_sha256),
        reviewed_annotations=ReviewedToolAnnotations(
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=False,
        ),
    )


def _contract_material(
    binding: OptionalToolBinding,
    *,
    runtime: str | None,
    top_level_fields: tuple[str, ...],
    disposition: str,
) -> dict[str, Any]:
    return {
        "model": binding.binding_model,
        "tool_name": binding.tool_name,
        "classification": binding.classification,
        "adapter_token": binding.adapter_token,
        "argument_constraints_id": ARGUMENT_CONSTRAINTS_ID,
        "runtime_contract_fingerprint": runtime,
        "runtime_contract_fingerprint_model": (
            binding.runtime_contract_fingerprint_model
        ),
        "top_level_fields": list(top_level_fields),
        "version": binding.version,
        "source_commit": binding.source_commit,
        "disposition": disposition,
    }


def observed_contract_fingerprint(
    tool: Any,
    binding: OptionalToolBinding,
) -> str:
    """The optional contract fingerprint computed from one observed descriptor.

    It equals ``binding_contract_fingerprint(binding, "admitted")`` exactly
    when the descriptor's top-level fields and v2 runtime fingerprint match.
    """

    from ..ha_mcp_readmission.models import evidence_fingerprint

    if not isinstance(tool, dict):
        return evidence_fingerprint({"invalid_optional_descriptor": True})
    return evidence_fingerprint(
        _contract_material(
            binding,
            runtime=_observed_runtime(
                tool, binding.runtime_contract_fingerprint_model
            ),
            top_level_fields=tuple(sorted(str(key) for key in tool)),
            disposition="admitted",
        )
    )


def extension_profile(release: Any, binding: OptionalToolBinding) -> Any:
    """The optional extension profile for a ``compiled_exact`` selection."""

    from ..ha_mcp_readmission.models import (
        CapabilityContract,
        CapabilityKind,
        CapabilityProfile,
        UpstreamSurface,
    )

    contract = binding_contract_fingerprint(binding, "admitted")
    return CapabilityProfile(
        profile_id=(
            f"ha-mcp-optional-yaml-{binding.version}-"
            f"{contract.removeprefix('sha256:')[:16]}"
        ),
        profile_version=1,
        surface=UpstreamSurface.HA_MCP,
        adapter_id=binding.adapter_token,
        expected_identity=release.server_name,
        supported_protocols=release.allowed_protocol_versions,
        capabilities=(
            CapabilityContract(
                capability_id=TOOL_NAME,
                kind=CapabilityKind.ORDINARY_READ,
                contract_fingerprint=contract,
                optional=True,
            ),
        ),
    )
