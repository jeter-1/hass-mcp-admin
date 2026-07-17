"""Structured application logging with deterministic redaction."""

from __future__ import annotations

import json
import logging
from typing import Any

from .request_context import current_request_id
from .sanitization import sanitize_untrusted_data


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
) -> Any:
    result = sanitize_untrusted_data(
        value,
        known_secrets=tuple(item for item in (secret, *secrets) if item),
        max_string=max_string,
    )
    return result.value


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
        sanitized = sanitize_untrusted_data(untrusted_payload, max_string=2048)
        payload = sanitized.value
        if isinstance(payload, dict):
            payload["redaction_applied"] = sanitized.redaction_applied
            payload["redacted_field_count"] = sanitized.redacted_field_count
            payload["redaction_categories"] = list(sanitized.redaction_categories)
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("ha_mcp_engineering")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


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
    logger.log(
        level,
        message,
        extra={
            "event": event,
            "subsystem": logger.name.rsplit(".", 1)[-1],
            "request_id": current_request_id(),
            "safe_context": redact_data(context or {}, secret=secret),
        },
        exc_info=exc_info,
    )
