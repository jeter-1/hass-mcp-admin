"""Structured application logging with deterministic redaction."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG as _UVICORN_LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from .request_context import current_request_id
from .sanitization import (
    RedactionTally,
    ReferenceMarkerRegistry,
    sanitize_untrusted_data,
)


def redact_untrusted_text(
    value: str,
    *,
    secrets: tuple[str, ...] = (),
    max_string: int = 2048,
) -> str:
    """Compatibility wrapper around the centralized fail-closed sanitizer."""

    result = sanitize_untrusted_data(
        value, known_secrets=secrets, max_string=max_string
    )
    return str(result.value)


def redact_data(
    value: Any,
    *,
    secret: str = "",
    secrets: tuple[str, ...] = (),
    max_string: int = 2048,
    registry: ReferenceMarkerRegistry | None = None,
) -> Any:
    result = sanitize_untrusted_data(
        value,
        known_secrets=tuple(item for item in (secret, *secrets) if item),
        max_string=max_string,
        registry=registry,
    )
    return result.value


def utc_timestamp(created: float) -> str:
    """ISO-8601 UTC with milliseconds, e.g. ``2026-11-09T21:00:36.590Z``."""

    return (
        datetime.fromtimestamp(created, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class _UtcTimestampMixin:
    """HAMCP-350 (c): one timestamp format on every line, whatever its source."""

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:
        return utc_timestamp(record.created)


class TimestampedTextFormatter(_UtcTimestampMixin, logging.Formatter):
    """Root-logger lines (the MCP SDK and other libraries), text kept as is."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")


class TimestampedUvicornFormatter(_UtcTimestampMixin, DefaultFormatter):
    pass


class TimestampedUvicornAccessFormatter(_UtcTimestampMixin, AccessFormatter):
    pass


def uvicorn_log_config() -> dict[str, Any]:
    """Uvicorn's own logging configuration with a leading UTC timestamp."""

    config = deepcopy(_UVICORN_LOGGING_CONFIG)
    config["formatters"]["default"].update(
        {
            "()": TimestampedUvicornFormatter,
            "fmt": "%(asctime)s %(levelprefix)s %(message)s",
        }
    )
    config["formatters"]["access"].update(
        {
            "()": TimestampedUvicornAccessFormatter,
            "fmt": (
                '%(asctime)s %(levelprefix)s %(client_addr)s - '
                '"%(request_line)s" %(status_code)s'
            ),
        }
    )
    return config


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        untrusted_payload = {
            "level": record.levelname.lower(),
            "request_id": getattr(record, "request_id", None) or current_request_id(),
            "subsystem": getattr(record, "subsystem", record.name),
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "context": getattr(record, "safe_context", {}),
        }
        if record.exc_info:
            untrusted_payload["exception_type"] = record.exc_info[0].__name__
        # HAMCP-191 D4: the context was sanitized by log_event in this same
        # record; its identity record keeps Engineering's markers there.
        sanitized = sanitize_untrusted_data(
            untrusted_payload,
            max_string=2048,
            registry=getattr(record, "sanitizer_registry", None),
        )
        payload = sanitized.value
        if isinstance(payload, dict):
            # HAMCP-191 L1: report log_event's first pass too. Its markers are
            # identity hits here and are not counted twice.
            tally = RedactionTally()
            first = getattr(record, "sanitizer_tally", None)
            if isinstance(first, RedactionTally):
                tally.redacted_field_count = first.redacted_field_count
                tally.categories.update(first.categories)
            tally.add(sanitized)
            payload["redaction_applied"] = (
                tally.applied or sanitized.redaction_applied
            )
            payload["redacted_field_count"] = tally.redacted_field_count
            payload["redaction_categories"] = sorted(tally.categories)
            # HAMCP-350 (c): added after sanitization so it is never redacted.
            payload["timestamp"] = utc_timestamp(record.created)
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("ha_mcp_engineering")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    # HAMCP-350 (c): the pinned MCP SDK's ``basicConfig`` gives the root
    # logger a plain handler when FastMCP is constructed at import, bound to
    # the stderr of that moment. Replace it with one timestamped handler on
    # the current (stream-guarded) stderr; the root level is left unchanged.
    process_root = logging.getLogger()
    root_handler = logging.StreamHandler()
    root_handler.setFormatter(TimestampedTextFormatter())
    for existing in tuple(process_root.handlers):
        process_root.removeHandler(existing)
    process_root.addHandler(root_handler)


def get_logger(subsystem: str) -> logging.Logger:
    return logging.getLogger(f"ha_mcp_engineering.{subsystem}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
    secret: str = "",
    exc_info: bool = False,
) -> None:
    registry = ReferenceMarkerRegistry()
    first = sanitize_untrusted_data(
        context or {},
        known_secrets=(secret,) if secret else (),
        max_string=2048,
        registry=registry,
    )
    # HAMCP-191 L1: the formatter reports this pass's counts as well.
    tally = RedactionTally()
    tally.add(first)
    logger.log(
        level,
        message,
        extra={
            "event": event,
            "subsystem": logger.name.rsplit(".", 1)[-1],
            "request_id": current_request_id(),
            "safe_context": first.value,
            "sanitizer_registry": registry,
            "sanitizer_tally": tally,
        },
        exc_info=exc_info,
    )
