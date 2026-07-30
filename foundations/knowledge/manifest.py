"""Fail-closed loading for one operator-selected local knowledge root."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from .models import (
    ContentClass,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeValidationError,
    LoadedKnowledgeSource,
    RedactionClass,
    RetrievedKnowledgeText,
    SOURCE_ID_PATTERN,
    SOURCE_TYPE_PATTERN,
    TrustClass,
)
from .versioning import IntegrationScope, VersionScope, VersionScopeError


MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_KNOWLEDGE_FILE_BYTES = 1024 * 1024
ALLOWED_TEXT_SUFFIXES = frozenset({".adoc", ".markdown", ".md", ".rst", ".txt"})

_SOURCE_KEYS = {
    "source_id",
    "source_type",
    "title",
    "publisher",
    "canonical_origin",
    "version_scope",
    "home_assistant_version_scope",
    "integration_scope",
    "retrieved_at",
    "valid_until",
    "trust_class",
    "content_class",
    "redaction_class",
    "license_or_usage_note",
    "content_sha256",
    "relative_path",
    "citation_prefix",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    re.ASCII,
)


def _error(code: str, message: str) -> KnowledgeValidationError:
    return KnowledgeValidationError(code, message)


def _strict_object_keys(
    value: dict[str, Any],
    *,
    expected: set[str],
    code: str,
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing keys {missing}")
    if unknown:
        details.append(f"unknown keys {unknown}")
    raise _error(code, "; ".join(details))


def _bounded_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    canonical_pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _error(
            f"invalid_{field_name}",
            f"{field_name} must be a non-empty string of at most {maximum} characters",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _error(
            f"invalid_{field_name}",
            f"{field_name} cannot contain control characters",
        )
    if canonical_pattern is not None and canonical_pattern.fullmatch(value) is None:
        raise _error(
            f"invalid_{field_name}",
            f"{field_name} is not canonical",
        )
    return value


def _parse_utc_timestamp(value: Any, *, field_name: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise _error(
            f"invalid_{field_name}",
            f"{field_name} must be an exact UTC timestamp ending in Z",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise _error(
            f"invalid_{field_name}",
            f"{field_name} is not a real UTC timestamp",
        ) from exc
    return value, parsed


def _canonical_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise _error("invalid_validation_time", "now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise _error(
            "invalid_relative_path",
            "relative_path must be a non-empty bounded string",
        )
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or "\\" in value
    ):
        raise _error("absolute_path", "relative_path must not be absolute")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _error(
            "path_traversal",
            "relative_path must contain canonical child segments",
        )
    suffix = PurePosixPath(value).suffix
    if suffix not in ALLOWED_TEXT_SUFFIXES:
        raise _error(
            "unsupported_text_format",
            "relative_path does not use an allowed text suffix",
        )
    return value


def _resolve_content_path(root: Path, relative_path: str) -> Path:
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error("missing_content", "source content is missing") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _error(
            "escaping_symlink",
            "source content resolves outside the allowed root",
        ) from exc
    if not resolved.is_file():
        raise _error("missing_content", "source content is not a regular file")
    return resolved


def _read_bounded(path: Path, *, maximum: int, too_large_code: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _error("missing_content", "source content cannot be inspected") from exc
    if size > maximum:
        raise _error(too_large_code, "file exceeds the configured size limit")
    try:
        with path.open("rb") as handle:
            content = handle.read(maximum + 1)
    except OSError as exc:
        raise _error("missing_content", "source content cannot be read") from exc
    if len(content) > maximum:
        raise _error(too_large_code, "file exceeds the configured size limit")
    return content


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _error("duplicate_json_key", "manifest contains a duplicate key")
        value[key] = item
    return value


def _load_manifest_json(root: Path) -> dict[str, Any]:
    manifest_candidate = root / MANIFEST_FILENAME
    try:
        manifest_path = manifest_candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error("missing_manifest", "local knowledge manifest is missing") from exc
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise _error(
            "escaping_manifest_symlink",
            "manifest resolves outside the allowed root",
        ) from exc
    raw = _read_bounded(
        manifest_path,
        maximum=MAX_MANIFEST_BYTES,
        too_large_code="manifest_too_large",
    )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("invalid_manifest_encoding", "manifest must be UTF-8") from exc
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_rejecting_object,
        )
    except json.JSONDecodeError as exc:
        raise _error("invalid_manifest_json", "manifest must be valid JSON") from exc
    if not isinstance(value, dict):
        raise _error("invalid_manifest", "manifest root must be an object")
    return value


def _enum_value(enum_type: type, value: Any, *, code: str, label: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise _error(code, f"unknown {label}") from exc


def _parse_source(
    value: Any,
    *,
    root: Path,
    now: datetime,
) -> LoadedKnowledgeSource:
    if not isinstance(value, dict):
        raise _error("invalid_source", "each source must be an object")
    _strict_object_keys(value, expected=_SOURCE_KEYS, code="invalid_source")

    source_id = _bounded_text(
        value["source_id"],
        field_name="source_id",
        maximum=128,
        canonical_pattern=SOURCE_ID_PATTERN,
    )
    source_type = _bounded_text(
        value["source_type"],
        field_name="source_type",
        maximum=64,
        canonical_pattern=SOURCE_TYPE_PATTERN,
    )
    title = _bounded_text(value["title"], field_name="title", maximum=256)
    publisher = _bounded_text(
        value["publisher"],
        field_name="publisher",
        maximum=256,
    )
    canonical_origin = _bounded_text(
        value["canonical_origin"],
        field_name="canonical_origin",
        maximum=2048,
    )
    license_note = _bounded_text(
        value["license_or_usage_note"],
        field_name="license_or_usage_note",
        maximum=1024,
    )
    content_sha256 = value["content_sha256"]
    if (
        not isinstance(content_sha256, str)
        or _SHA256_PATTERN.fullmatch(content_sha256) is None
    ):
        raise _error(
            "invalid_content_sha256",
            "content_sha256 must be 64 lowercase hexadecimal characters",
        )
    relative_path = _validate_relative_path(value["relative_path"])
    citation_prefix = value["citation_prefix"]
    if citation_prefix != f"knowledge:{source_id}":
        raise _error(
            "malformed_citation",
            "citation_prefix must exactly bind to the canonical source_id",
        )

    retrieved_at, retrieved_time = _parse_utc_timestamp(
        value["retrieved_at"],
        field_name="retrieved_at",
    )
    if retrieved_time > now:
        raise _error("future_retrieval", "retrieved_at cannot be in the future")
    raw_valid_until = value["valid_until"]
    if raw_valid_until is None:
        valid_until = None
    else:
        valid_until, valid_until_time = _parse_utc_timestamp(
            raw_valid_until,
            field_name="valid_until",
        )
        if valid_until_time <= retrieved_time:
            raise _error(
                "invalid_valid_until",
                "valid_until must be later than retrieved_at",
            )
        if valid_until_time <= now:
            raise _error("expired_content", "source content is expired")

    try:
        version_scope = VersionScope.from_dict(value["version_scope"])
        home_assistant_version_scope = VersionScope.from_dict(
            value["home_assistant_version_scope"]
        )
        integration_scope = IntegrationScope.from_dict(value["integration_scope"])
    except VersionScopeError as exc:
        raise _error(exc.code, exc.message) from exc

    trust_class = _enum_value(
        TrustClass,
        value["trust_class"],
        code="unknown_trust_class",
        label="trust class",
    )
    content_class = _enum_value(
        ContentClass,
        value["content_class"],
        code="unknown_content_class",
        label="content class",
    )
    redaction_class = _enum_value(
        RedactionClass,
        value["redaction_class"],
        code="unknown_redaction_class",
        label="redaction class",
    )

    content_path = _resolve_content_path(root, relative_path)
    content_bytes = _read_bounded(
        content_path,
        maximum=MAX_KNOWLEDGE_FILE_BYTES,
        too_large_code="content_too_large",
    )
    actual_hash = hashlib.sha256(content_bytes).hexdigest()
    if not hmac.compare_digest(actual_hash, content_sha256):
        raise _error(
            "content_hash_mismatch",
            "source content does not match its declared hash",
        )
    try:
        content_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("invalid_content_encoding", "source content must be UTF-8") from exc

    source = KnowledgeSource(
        source_id=source_id,
        source_type=source_type,
        title=title,
        publisher=publisher,
        canonical_origin=canonical_origin,
        version_scope=version_scope,
        home_assistant_version_scope=home_assistant_version_scope,
        integration_scope=integration_scope,
        retrieved_at=retrieved_at,
        valid_until=valid_until,
        trust_class=trust_class,
        content_class=content_class,
        redaction_class=redaction_class,
        license_or_usage_note=license_note,
        content_sha256=content_sha256,
        relative_path=relative_path,
        citation_prefix=citation_prefix,
    )
    return LoadedKnowledgeSource(
        source=source,
        retrieved_text=RetrievedKnowledgeText(
            source_id=source_id,
            text=content_text,
            content_sha256=content_sha256,
            relative_path=relative_path,
            citation_prefix=citation_prefix,
        ),
    )


def load_knowledge_manifest(
    allowed_root: str | Path,
    *,
    now: datetime | None = None,
) -> KnowledgeManifest:
    """Load exactly ``manifest.json`` below one trusted local root.

    This function performs no network access, runtime registration, routing,
    planning, recommendation, or instruction interpretation. The caller is
    responsible for choosing the allowed root from trusted configuration, never
    from retrieved source content or an unvalidated public argument.
    """

    root_candidate = Path(allowed_root)
    try:
        root = root_candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _error("missing_root", "allowed knowledge root is missing") from exc
    if not root.is_dir():
        raise _error("invalid_root", "allowed knowledge root must be a directory")
    validation_time = _canonical_now(now)
    manifest_value = _load_manifest_json(root)
    _strict_object_keys(
        manifest_value,
        expected={"schema_version", "sources"},
        code="invalid_manifest",
    )
    if type(manifest_value["schema_version"]) is not int or (
        manifest_value["schema_version"] != MANIFEST_SCHEMA_VERSION
    ):
        raise _error(
            "unsupported_manifest_schema",
            f"schema_version must be {MANIFEST_SCHEMA_VERSION}",
        )
    raw_sources = manifest_value["sources"]
    if not isinstance(raw_sources, list):
        raise _error("invalid_manifest", "sources must be a list")

    source_ids: set[str] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise _error("invalid_source", "each source must be an object")
        source_id = _bounded_text(
            raw_source.get("source_id"),
            field_name="source_id",
            maximum=128,
            canonical_pattern=SOURCE_ID_PATTERN,
        )
        if source_id in source_ids:
            raise _error(
                "duplicate_source_id",
                f"duplicate source_id {source_id!r}",
            )
        source_ids.add(source_id)

    loaded: list[LoadedKnowledgeSource] = []
    for raw_source in raw_sources:
        item = _parse_source(raw_source, root=root, now=validation_time)
        loaded.append(item)
    loaded.sort(key=lambda item: item.source.source_id)
    canonical_payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "sources": [item.source.canonical_dict() for item in loaded],
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return KnowledgeManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        sources=tuple(loaded),
        manifest_sha256=hashlib.sha256(encoded).hexdigest(),
    )
