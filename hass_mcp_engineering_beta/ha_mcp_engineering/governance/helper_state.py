"""Exact, code-owned Home Assistant input-boolean state boundary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Awaitable, Callable

from ..errors import (
    AuthorizationError,
    EntityNotFoundError,
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)


HELPER_STATE_PROVIDER = "direct_home_assistant_state"
HELPER_STATE_PROVIDER_CONTRACT = "direct-ha-exact-input-boolean-v1"
HELPER_STATE_PROVIDER_OPERATION = "set_exact_input_boolean_state"
HELPER_STATE_PROVIDER_SLUG = "home-assistant-core"
INPUT_BOOLEAN_ENTITY_PATTERN = re.compile(
    r"^input_boolean\.[a-z0-9_]{1,114}$"
)


class HelperStateGatewayError(RuntimeError):
    """Bounded error that never includes Home Assistant response content."""

    def __init__(self, category: str, *, dispatched: bool = False) -> None:
        super().__init__("The exact helper-state provider rejected the request.")
        self.category = category
        self.dispatched = dispatched


@dataclass(frozen=True)
class HelperStateDispatchResult:
    provider_response_received: bool


def validate_input_boolean_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not INPUT_BOOLEAN_ENTITY_PATTERN.fullmatch(
        entity_id
    ):
        raise ValueError("input_boolean entity_id is invalid")
    return entity_id


def validate_desired_state(desired_state: str) -> str:
    if desired_state not in {"on", "off"}:
        raise ValueError("desired_state is invalid")
    return desired_state


def helper_state_provider_evidence() -> dict[str, Any]:
    """Return the immutable, code-owned direct-provider contract."""

    return {
        "provider": HELPER_STATE_PROVIDER,
        "provider_contract_model": HELPER_STATE_PROVIDER_CONTRACT,
        "provider_operation": HELPER_STATE_PROVIDER_OPERATION,
        "transport": "home_assistant_websocket_call_service",
        "readback_transport": "home_assistant_rest_exact_state",
        "argument_constraints": {
            "domain": "input_boolean",
            "services": ["turn_off", "turn_on"],
            "target": "exact_entity_id",
            "arbitrary_service_allowed": False,
            "arbitrary_service_data_allowed": False,
            "fallback_allowed": False,
        },
        "fallback": "none",
        "fallback_occurred": False,
    }


class HelperStateGateway:
    """Permit only exact ``input_boolean`` on/off and exact-state readback."""

    def __init__(self, rest_client: Any, websocket_client: Any) -> None:
        self.rest_client = rest_client
        self.websocket_client = websocket_client

    async def read_state(self, entity_id: str) -> dict[str, Any]:
        entity_id = validate_input_boolean_entity_id(entity_id)
        try:
            value = await self.rest_client.request("GET", f"/states/{entity_id}")
        except EntityNotFoundError:
            raise HelperStateGatewayError("entity_not_found") from None
        except AuthorizationError:
            raise HelperStateGatewayError("permission_failure") from None
        except HomeAssistantTimeoutError:
            raise HelperStateGatewayError("provider_timeout") from None
        except HomeAssistantUnavailableError:
            raise HelperStateGatewayError("provider_unavailable") from None
        except HomeAssistantApiError as exc:
            status = exc.details.get("status")
            raise HelperStateGatewayError(
                "permission_failure"
                if status in {401, 403}
                else "provider_error"
            ) from None
        if not isinstance(value, dict):
            raise HelperStateGatewayError("invalid_provider_response")
        returned_id = value.get("entity_id")
        state = value.get("state")
        last_changed = value.get("last_changed")
        if (
            returned_id != entity_id
            or state not in {"on", "off"}
            or not isinstance(last_changed, str)
            or not last_changed
        ):
            raise HelperStateGatewayError("invalid_provider_response")
        return {
            "entity_id": returned_id,
            "state": state,
            "last_changed": last_changed,
        }

    async def planning_evidence(self, entity_id: str) -> dict[str, Any]:
        return {
            "provider": helper_state_provider_evidence(),
            "baseline": await self.read_state(entity_id),
        }

    async def set_state(
        self,
        entity_id: str,
        desired_state: str,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
    ) -> HelperStateDispatchResult:
        entity_id = validate_input_boolean_entity_id(entity_id)
        desired_state = validate_desired_state(desired_state)
        payload = {
            "type": "call_service",
            "domain": "input_boolean",
            "service": "turn_on" if desired_state == "on" else "turn_off",
            "target": {"entity_id": entity_id},
        }
        await before_dispatch()
        try:
            await self.websocket_client.command(payload)
        except (AuthorizationError, HomeAssistantApiError) as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            confirmed = details.get("provider_response_received") is True
            raise HelperStateGatewayError(
                "provider_rejected" if confirmed else "dispatch_indeterminate",
                dispatched=True,
            ) from None
        except (HomeAssistantTimeoutError, HomeAssistantUnavailableError):
            raise HelperStateGatewayError(
                "dispatch_indeterminate", dispatched=True
            ) from None
        return HelperStateDispatchResult(provider_response_received=True)


__all__ = [
    "HELPER_STATE_PROVIDER",
    "HELPER_STATE_PROVIDER_CONTRACT",
    "HELPER_STATE_PROVIDER_OPERATION",
    "HELPER_STATE_PROVIDER_SLUG",
    "HelperStateDispatchResult",
    "HelperStateGateway",
    "HelperStateGatewayError",
    "helper_state_provider_evidence",
    "validate_desired_state",
    "validate_input_boolean_entity_id",
]
