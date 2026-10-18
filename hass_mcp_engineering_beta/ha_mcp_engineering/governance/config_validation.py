"""Reusable bounded interpretation of Home Assistant configuration checks."""

from __future__ import annotations

from typing import Any, Iterable

from ..sanitization import ReferenceMarkerRegistry, sanitize_untrusted_data


def normalize_configuration_validation(
    result: Any,
    *,
    known_secrets: Iterable[str] = (),
    registry: ReferenceMarkerRegistry | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return a strict status and bounded evidence without changing HA output.

    ``registry`` (HAMCP-191 D4) is the caller's identity record, for a caller
    that bounds the returned evidence again in the same request.
    """

    is_object = isinstance(result, dict)
    result_present = is_object and "result" in result
    errors_present = is_object and "errors" in result
    raw_result = result.get("result") if is_object else result
    raw_errors = result.get("errors") if is_object else None
    safe_result = sanitize_untrusted_data(
        raw_result,
        known_secrets=known_secrets,
        max_string=2048,
        registry=registry,
    ).value
    safe_errors = sanitize_untrusted_data(
        raw_errors,
        known_secrets=known_secrets,
        max_string=2048,
        registry=registry,
    ).value

    if not is_object:
        reason = "malformed_response"
    elif not result_present:
        reason = "missing_result"
    elif raw_result != "valid":
        reason = "configuration_invalid"
    elif not errors_present:
        reason = "missing_errors"
    elif raw_errors is not None:
        reason = "configuration_errors_present"
    else:
        reason = "explicit_valid_result"

    details = {
        "response_type": type(result).__name__,
        "result_present": result_present,
        "result": safe_result,
        "errors_present": errors_present,
        "errors": safe_errors,
        "reason": reason,
    }
    return (
        ("valid" if reason == "explicit_valid_result" else "failed"),
        details,
    )
