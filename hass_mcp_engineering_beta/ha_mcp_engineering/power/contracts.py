"""Closed light/switch contract. No generic services or optional action data."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..fan.contracts import CORE_VERSION, CORE_SOURCE, CORE_REQUIREMENTS, OPERATION_PATTERN, digest, FanRefusal

PROVIDER = "upstream_typed_power"
POWER_RELEASES = {
    "8.4.3": ("ha-mcp-v8.4.3-d5cea47a", "eac7a3aa7063432e9af17e7d7726040e909c7b8f",
              "core-2026.9.2-ha-mcp-8.4.3-single-power-v1"),
    "8.5.0": ("ha-mcp-v8.5.0-e1538bcd", "311d6dc273fb4e9a5b8cde0de15f69472a64fe44",
              "core-2026.9.2-ha-mcp-8.5.0-single-power-v1"),
}
POWER_CONTRACTS = tuple(item[2] for item in POWER_RELEASES.values())
ENTITY_PATTERN = r"^(light|switch)\.[a-z0-9_]{1,120}$"


class PowerRefusal(FanRefusal):
    """Reuse bounded local diagnostics, never upstream exception text."""

    def __init__(self, category):
        if isinstance(category, str) and category.startswith("fan_"):
            category = "power_" + category[4:]
        super().__init__(category)


class PowerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    entity_id: str = Field(pattern=ENTITY_PATTERN)
    action: Literal["turn_on", "turn_off"]
    operation_id: str = Field(pattern=OPERATION_PATTERN)

    def checked(self):
        return self

    @property
    def domain(self):
        return self.entity_id.split(".", 1)[0]

    @property
    def task_id(self):
        return digest({"power_operation_id": self.operation_id})[:32]

    def arguments(self):
        return {"domain": self.domain, "service": self.action,
                "entity_id": self.entity_id, "data": {}, "wait": False,
                "return_response": False, "verbose": False}

    def is_fresh(self, now):
        age = now.timestamp() - int(self.operation_id.split("-", 1)[0])
        return -30 <= age <= 300


def checked_state(raw, entity_id):
    if not isinstance(raw, dict) or raw.get("entity_id") != entity_id:
        raise PowerRefusal("power_target_identity_unproven")
    if raw.get("state") not in ("on", "off"):
        raise PowerRefusal("power_target_state_unavailable")
    stamp = raw.get("last_updated")
    if not isinstance(stamp, str) or not 1 <= len(stamp) <= 64:
        raise PowerRefusal("power_state_revision_unproven")
    return {"entity_id": entity_id, "state": raw["state"], "last_updated": stamp}


def desired(request, state):
    return state["state"] == ("on" if request.action == "turn_on" else "off")
