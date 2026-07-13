"""Structured, bounded, secret-safe beta audit records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

from .logging_config import get_logger, log_event, redact_data
from .version import SERVER_VERSION

AUDIT_MAX_BYTES = 5 * 1024 * 1024


@dataclass
class AuditRecord:
    request_id: str
    tool_name: str | None
    capability_classification: str | None
    operation_category: str | None
    access: str | None
    authenticated: bool
    caller_id: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    result_status: str = "unknown"
    error_code: str | None = None
    duration_ms: float | None = None
    ha_endpoint_categories: list[str] = field(default_factory=list)
    resource_ids: dict[str, str] = field(default_factory=dict)
    analysis_summary: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    server_version: str = SERVER_VERSION
    event: str = "tool_call"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLogger:
    def __init__(
        self,
        path: str,
        access_secret: str,
        *,
        enabled: bool = True,
        max_payload_chars: int = 8192,
    ):
        self.path = path
        self.access_secret = access_secret
        self.enabled = enabled
        self.max_payload_chars = max_payload_chars
        self.write_failures = 0
        self.last_error: str | None = None
        self.logger = get_logger("audit")

    def sanitize(self, entry: dict[str, Any]) -> dict[str, Any]:
        safe = redact_data(entry, secret=self.access_secret, max_string=1024)
        encoded = json.dumps(safe, default=str, sort_keys=True)
        if len(encoded) <= self.max_payload_chars:
            return safe
        return {
            "event": safe.get("event", "audit_record"),
            "request_id": safe.get("request_id"),
            "server_version": safe.get("server_version", SERVER_VERSION),
            "result_status": safe.get("result_status", "unknown"),
            "payload_truncated": True,
            "original_size": len(encoded),
        }

    def write(self, entry: AuditRecord | dict[str, Any]) -> bool:
        if not self.enabled:
            return True
        raw = entry.as_dict() if isinstance(entry, AuditRecord) else dict(entry)
        safe = self.sanitize(raw)
        safe.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        safe.setdefault("server_version", SERVER_VERSION)
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > AUDIT_MAX_BYTES:
                os.replace(self.path, self.path + ".1")
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, default=str, sort_keys=True) + "\n")
            self.last_error = None
            return True
        except OSError as exc:
            self.write_failures += 1
            self.last_error = type(exc).__name__
            log_event(
                self.logger,
                logging.ERROR,
                "audit_write_failed",
                "Audit output could not be written.",
                context={"error_type": type(exc).__name__},
            )
            return False

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "target_configured": bool(self.path),
            "write_failures": self.write_failures,
            "last_error_category": self.last_error,
            "redaction_enabled": True,
            "max_payload_chars": self.max_payload_chars,
        }
