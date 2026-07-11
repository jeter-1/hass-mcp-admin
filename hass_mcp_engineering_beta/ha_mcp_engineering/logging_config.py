"""Structured application logging with deterministic redaction."""

from __future__ import annotations

import json
import logging
from typing import Any

from .request_context import current_request_id

SENSITIVE_KEYS = {
    "access_secret",
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "password",
    "api_key",
    "credential",
}


def redact_data(value: Any, *, secret: str = "", max_string: int = 2048) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if str(key).lower() in SENSITIVE_KEYS
                else redact_data(item, secret=secret, max_string=max_string)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(item, secret=secret, max_string=max_string) for item in value]
    if isinstance(value, str):
        safe = value.replace(secret, "<access_secret>") if secret else value
        return safe[:max_string] + ("...<truncated>" if len(safe) > max_string else "")
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname.lower(),
            "request_id": getattr(record, "request_id", None) or current_request_id(),
            "subsystem": getattr(record, "subsystem", record.name),
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "context": getattr(record, "safe_context", {}),
        }
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str, sort_keys=True)


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
