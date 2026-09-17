"""Closed ordinary fan contract; no general service arguments or approval plans."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from ..errors import InvalidRequestError

CORE_VERSION = "2026.9.2"
CORE_SOURCE = "33c3e0cca60e73a8c4970ee677d75b8bc6464cdf"
CORE_FAN_SOURCE_SHA256 = "cfe36916625e40c89b395d091de87ad4230f78b854a4b126e99e2aa287731a19"
FAN_CONTRACT = "core-2026.9.2-ha-mcp-8.4.3-single-fan-v1"
PROVIDER = "upstream_typed_fan"
CORE_REQUIREMENTS = (
    "core.direct_entity_state_read", "core.state_service_discovery",
    "core.f3_mutation_verification",
)
OPERATION_PATTERN = r"^[0-9]{10}-[a-f0-9]{32}$"
ENTITY_PATTERN = r"^fan\.[a-z0-9_]{1,120}$"


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class FanRefusal(InvalidRequestError, ValueError):
    """Only fixed local categories, never upstream text, leave this boundary."""

    def __init__(self, category):
        import re
        if not isinstance(category, str) or not re.fullmatch(r"[a-z_]{1,64}", category):
            category = "fan_internal_refusal"
        super().__init__(category, details={"category": category, "fallback": "none"})


class FanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_id: str = Field(pattern=ENTITY_PATTERN)
    action: Literal["turn_on", "turn_off", "set_percentage"]
    operation_id: str = Field(pattern=OPERATION_PATTERN)
    percentage: StrictInt | None = Field(default=None, ge=0, le=100)

    def checked(self) -> FanRequest:
        if self.action == "set_percentage" and self.percentage is None:
            raise FanRefusal("percentage_required")
        if self.action == "turn_off" and self.percentage is not None:
            raise FanRefusal("percentage_not_allowed")
        return self

    @property
    def task_id(self) -> str:
        return digest({"fan_operation_id": self.operation_id})[:32]

    def arguments(self) -> dict:
        self.checked()
        return {
            "domain": "fan", "service": self.action, "entity_id": self.entity_id,
            "data": {} if self.percentage is None else {"percentage": self.percentage},
            "wait": False, "return_response": False, "verbose": False,
        }

    def is_fresh(self, now) -> bool:
        age = now.timestamp() - int(self.operation_id.split("-", 1)[0])
        return -30 <= age <= 300


def checked_state(raw: object, entity_id: str) -> dict:
    if not isinstance(raw, dict) or raw.get("entity_id") != entity_id:
        raise FanRefusal("target_identity_unproven")
    attrs = raw.get("attributes")
    if raw.get("state") not in {"on", "off"} or not isinstance(attrs, dict):
        raise FanRefusal("target_state_unavailable")
    features = attrs.get("supported_features")
    percentage = attrs.get("percentage")
    if type(features) is not int or not 0 <= features <= 65535:
        raise FanRefusal("fan_features_unproven")
    if percentage is not None and (type(percentage) not in {int, float}
                                   or not 0 <= percentage <= 100):
        raise FanRefusal("fan_percentage_unproven")
    # Context and timestamps detect changes away and back; they stay private.
    stamp = raw.get("last_updated")
    if not isinstance(stamp, str) or not 1 <= len(stamp) <= 64:
        raise FanRefusal("state_revision_unproven")
    return {"entity_id": entity_id, "state": raw["state"],
            "percentage": percentage, "supported_features": features,
            "last_updated": stamp}


def desired(request: FanRequest, state: dict) -> bool:
    off = request.action == "turn_off" or request.percentage == 0
    return state["state"] == ("off" if off else "on") and (
        request.percentage is None or state["percentage"] == request.percentage
    )


def check_features(request: FanRequest, state: dict) -> None:
    # Exact Core 2026.9.2 FanEntityFeature: SET_SPEED=1, TURN_OFF=16, TURN_ON=32.
    required = 16 if request.action == "turn_off" else 32 if request.action == "turn_on" else 1
    if request.percentage is not None:
        required |= 1
    if state["supported_features"] & required != required:
        raise FanRefusal("fan_action_unsupported")
