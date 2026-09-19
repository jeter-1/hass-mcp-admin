"""Authenticated ordinary light/switch control; no general service exposure."""
from typing import Annotated, Literal
from pydantic import Field

from .compatibility import SETTINGS
from ..power.contracts import PowerRequest, digest
from ..power.service import POWER_OPERATIONS
from ..tool_framework import run_structured


async def control_power(
    entity_id: Annotated[str, Field(pattern=r"^(light|switch)\.[a-z0-9_]{1,120}$")],
    action: Literal["turn_on", "turn_off"],
    operation_id: Annotated[str, Field(pattern=r"^[0-9]{10}-[a-f0-9]{32}$")],
) -> str:
    """Turn one exact light or switch ON/OFF through Engineering's internal ha-mcp.

    Use the user's ordinary connector authorization for this exact action; no
    plan or panel approval is needed. Resolve ambiguous targets before calling.
    Generate operation_id ONCE as current UTC epoch seconds + '-' + uuid4().hex;
    manage it automatically without asking the user to supply it. Retain it for
    reconciliation. New IDs expire after five minutes. The same ID and arguments
    only reconcile; changed arguments refuse. Read get_execution_task after a
    timeout or partial response. Never create a fresh ID to retry uncertain work.

    Only light/switch turn_on/turn_off are supported. No toggle, brightness,
    color, extra data, bulk, area/device targets or other domains. Existing HA
    defaults, group membership and automation reactions may apply; consumer and
    consequence coverage is incomplete, and a switch may power a critical load.
    Exact owner-authorized actions remain supported despite those consequences.
    Verification establishes HA-reported ON/OFF, not physical feedback or other
    attribute preservation. Inspect the task outcome, not outer response success.
    Restoration is a separate owner-requested action after reconciliation.
    """
    return await run_structured(
        "control_power", "Returned the authoritative ordinary power operation receipt.",
        lambda: POWER_OPERATIONS.require().control(PowerRequest(
            entity_id=entity_id, action=action, operation_id=operation_id).checked()),
        metadata={"provider": "upstream_typed_power", "fallback": "none",
                  "task_id": digest({"power_operation_id": operation_id})[:32],
                  "operation_id": operation_id},
        response_limit=SETTINGS.response_size_limit,
    )
