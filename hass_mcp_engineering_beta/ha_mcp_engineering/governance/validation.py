"""Automation schema and persistence-safety validation."""

from __future__ import annotations

import re
from typing import Any


AUTOMATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SENSITIVE_KEYS = {
    "access_secret",
    "authorization",
    "cookie",
    "password",
    "token",
    "api_key",
    "webhook_id",
}


def validate_automation(
    automation_id: str, proposed: Any
) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not AUTOMATION_ID.fullmatch(automation_id or ""):
        errors.append("automation_id must contain only letters, numbers, underscores, or hyphens")
    if not isinstance(proposed, dict):
        errors.append("proposed_config must be an object")
        return False, errors, warnings
    if not any(key in proposed for key in ("trigger", "triggers", "use_blueprint")):
        errors.append("automation config requires trigger/triggers or use_blueprint")
    if not any(key in proposed for key in ("action", "actions", "use_blueprint")):
        errors.append("automation config requires action/actions or use_blueprint")
    if "mode" in proposed and proposed["mode"] not in {"single", "restart", "queued", "parallel"}:
        errors.append("automation mode must be single, restart, queued, or parallel")
    if any(key.lower() in SENSITIVE_KEYS for key in _keys(proposed)):
        errors.append("secret-bearing or webhook fields cannot be persisted in a change plan")
    if any(
        isinstance(value, str)
        and ("/mcp/" in value.lower() or ("/mcp" in value.lower() and value.startswith(("http://", "https://"))))
        for value in _values(proposed)
    ):
        errors.append("authenticated MCP URLs cannot be persisted in a change plan")
    if "id" in proposed and str(proposed["id"]) != automation_id:
        warnings.append("proposed config id differs from target; target automation_id remains authoritative")
    return not errors, errors, warnings


def sanitize_context(
    context: dict[str, Any] | None, sensitive_values: tuple[str, ...] = ()
) -> dict[str, Any]:
    if not context:
        return {}
    safe: dict[str, Any] = {}
    for key, value in context.items():
        if str(key).lower() in SENSITIVE_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str) and any(
                secret and secret in value for secret in sensitive_values
            ):
                continue
            safe[str(key)[:64]] = value[:256] if isinstance(value, str) else value
    return safe


def _keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _values(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
