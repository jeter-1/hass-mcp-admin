"""Exact, code-owned Home Assistant input_select option boundary (HAMCP-145).

The runtime-only profile ``core.typed_input_select_option_operation`` is
admitted only by an owner-signed registry.  Until then every entry point
refuses ``pending_signed_registry_admission`` before any Home Assistant
request.

The gateway has a closed vocabulary: one exact-state REST read and one
``input_select.select_option`` service call for one exact entity.  It never
sends ``set_options``, ``reload`` or a relative move (``select_first``,
``select_last``, ``select_next``, ``select_previous``), refuses any other
shape before I/O, and calls ``mark_provider_send()`` at the exact send
boundary.  An error result after the send is never proof of failure; the
exact-state readback decides.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Awaitable, Callable

from ..clients.paths import rest_path
from ..errors import (
    AuthorizationError,
    EntityNotFoundError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..request_context import mark_provider_send

PROFILE = "core.typed_input_select_option_operation"
PENDING_ADMISSION_REASON = "pending_signed_registry_admission"
PROVIDER = "direct_home_assistant_state"
PROVIDER_CONTRACT = "direct-ha-exact-input-select-option-v1"
PROVIDER_OPERATION = "set_exact_input_select_option"
PROVIDER_SLUG = "home-assistant-core"
TARGET_TYPE = "input_select"
DOMAIN = "input_select"
SERVICE = "select_option"
NEVER_SENT_SERVICES = (
    "reload",
    "select_first",
    "select_last",
    "select_next",
    "select_previous",
    "set_options",
)
ENTITY_ID_PATTERN = re.compile(r"input_select\.[a-z0-9_]{1,114}\Z")
MAX_OPTIONS = 256
MAX_OPTION_LENGTH = 255
BASELINE_MODEL = "input_select_option_baseline_v1"


class InputSelectGatewayError(RuntimeError):
    """Bounded error that never includes Home Assistant response content."""

    def __init__(
        self,
        category: str,
        *,
        dispatched: bool = False,
        call_not_sent: bool = False,
    ) -> None:
        super().__init__("The exact input_select provider rejected the request.")
        self.category = category
        self.dispatched = dispatched
        self.call_not_sent = call_not_sent is True and not dispatched


@dataclass(frozen=True)
class InputSelectDispatchResult:
    provider_response_received: bool


def validate_entity_id(entity_id: Any) -> str:
    if not isinstance(entity_id, str) or not ENTITY_ID_PATTERN.fullmatch(entity_id):
        raise ValueError("input_select entity_id is invalid")
    return entity_id


def validate_option(option: Any) -> str:
    if (
        not isinstance(option, str)
        or not 1 <= len(option) <= MAX_OPTION_LENGTH
        or re.search(r"[\x00-\x1f\x7f-\x9f]", option)
    ):
        raise ValueError("input_select option is invalid")
    return option


def baseline_fingerprint(state: str, options: tuple[str, ...]) -> str:
    """Bind the current option and the exact ordered options list."""

    encoded = json.dumps(
        {"model": BASELINE_MODEL, "options": list(options), "state": state},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": PROVIDER,
        "provider_contract_model": PROVIDER_CONTRACT,
        "provider_operation": PROVIDER_OPERATION,
        "transport": "home_assistant_websocket_call_service",
        "readback_transport": "home_assistant_rest_exact_state",
        "argument_constraints": {
            "domain": DOMAIN,
            "services": [SERVICE],
            "service_data": ["option"],
            "target": "exact_entity_id",
            "never_sent": list(NEVER_SENT_SERVICES),
            "arbitrary_service_allowed": False,
            "arbitrary_service_data_allowed": False,
            "fallback_allowed": False,
        },
        "fallback": "none",
        "fallback_occurred": False,
    }


def select_option_payload(entity_id: str, option: str) -> dict[str, Any]:
    """The only command this gateway can send."""

    return {
        "type": "call_service",
        "domain": DOMAIN,
        "service": SERVICE,
        "service_data": {"option": validate_option(option)},
        "target": {"entity_id": validate_entity_id(entity_id)},
    }


class InputSelectGateway:
    """Permit only exact ``input_select.select_option`` and exact readback."""

    def __init__(self, rest_client: Any, websocket_client: Any) -> None:
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "provider_contract": PROVIDER_CONTRACT,
            "configured": True,
            "operational_status": "configured_unprobed",
            "health": "unknown",
            "fallback": "none",
            "fallback_count": 0,
            "fallback_policy": "none",
            "last_failure_category": None,
        }

    async def read_state(self, entity_id: str) -> dict[str, Any]:
        """Read one exact entity's current option and ordered options list."""

        entity_id = validate_entity_id(entity_id)
        try:
            value = await self.rest_client.request(
                "GET", rest_path("/states/{}", entity_id, kinds=("entity_id",))
            )
        except EntityNotFoundError:
            raise InputSelectGatewayError("entity_not_found") from None
        except AuthorizationError:
            raise InputSelectGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise InputSelectGatewayError("provider_timeout") from None
        except HomeAssistantUnavailableError:
            raise InputSelectGatewayError("provider_unavailable") from None
        except HomeAssistantApiError as exc:
            status = exc.details.get("status")
            raise InputSelectGatewayError(
                "permission_failure" if status in {401, 403} else "provider_error"
            ) from None
        if not isinstance(value, dict) or value.get("entity_id") != entity_id:
            raise InputSelectGatewayError("invalid_provider_response")
        state = value.get("state")
        attributes = value.get("attributes")
        last_changed = value.get("last_changed")
        if not isinstance(state, str) or not isinstance(last_changed, str):
            raise InputSelectGatewayError("invalid_provider_response")
        options = attributes.get("options") if isinstance(attributes, dict) else None
        if (
            not isinstance(options, list)
            or not 1 <= len(options) <= MAX_OPTIONS
            or not all(isinstance(item, str) for item in options)
            or len(set(options)) != len(options)
        ):
            # ``unavailable``/``unknown`` entities carry no usable options.
            raise InputSelectGatewayError(
                "entity_unavailable"
                if state in {"unavailable", "unknown"}
                else "invalid_provider_response"
            )
        return {
            "entity_id": entity_id,
            "state": state,
            "options": tuple(options),
            "last_changed": last_changed,
        }

    async def select_option(
        self,
        entity_id: str,
        option: str,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
    ) -> InputSelectDispatchResult:
        payload = select_option_payload(entity_id, option)
        try:
            await before_dispatch()
        except Exception as exc:
            # The callback raised, so the command was never sent.
            raise InputSelectGatewayError(
                "before_dispatch_failed", call_not_sent=True
            ) from exc
        mark_provider_send()  # the exact send boundary
        try:
            await self.websocket_client.command(payload)
        except (AuthorizationError, HomeAssistantApiError) as exc:
            # Core raises HomeAssistantError for an option removed in between;
            # that is never proof the state is unchanged: readback decides.
            details = exc.details if isinstance(exc.details, dict) else {}
            confirmed = details.get("provider_response_received") is True
            raise InputSelectGatewayError(
                "provider_rejected" if confirmed else "dispatch_indeterminate",
                dispatched=True,
            ) from None
        except (HomeAssistantTimeoutError, HomeAssistantUnavailableError):
            raise InputSelectGatewayError(
                "dispatch_indeterminate", dispatched=True
            ) from None
        return InputSelectDispatchResult(provider_response_received=True)


__all__ = [
    "BASELINE_MODEL",
    "DOMAIN",
    "ENTITY_ID_PATTERN",
    "InputSelectDispatchResult",
    "InputSelectGateway",
    "InputSelectGatewayError",
    "NEVER_SENT_SERVICES",
    "PENDING_ADMISSION_REASON",
    "PROFILE",
    "PROVIDER",
    "PROVIDER_CONTRACT",
    "PROVIDER_OPERATION",
    "SERVICE",
    "TARGET_TYPE",
    "baseline_fingerprint",
    "provider_evidence",
    "select_option_payload",
    "validate_entity_id",
    "validate_option",
]
