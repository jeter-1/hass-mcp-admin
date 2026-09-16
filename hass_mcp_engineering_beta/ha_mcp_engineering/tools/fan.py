"""Typed ordinary fan actions; generic service forwarding remains unavailable."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from ..fan.contracts import FanRequest, digest
from ..fan.service import FAN_OPERATIONS
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def control_fan(
    entity_id: Annotated[str, Field(pattern=r"^fan\.[a-z0-9_]{1,120}$")],
    action: Literal["turn_on", "turn_off", "set_percentage"],
    operation_id: Annotated[str, Field(pattern=r"^[0-9]{10}-[a-f0-9]{32}$")],
    percentage: Annotated[StrictInt | None, Field(ge=0, le=100)] = None,
) -> str:
    """Control one exact fan under ordinary authenticated connector authority.

    The assistant generates operation_id ONCE as current UTC Unix seconds, a
    hyphen, and 32 lowercase UUID hex digits. Retain it automatically: never ask
    the owner to manage IDs. Reuse this exact ID/arguments after uncertainty;
    get_execution_task retrieves/reconciles its task. Never issue a replacement
    ID to retry an uncertain mutation. New IDs expire after five minutes.

    turn_on optionally includes percentage in ONE call; set_percentage requires
    an integer 0..100; zero requests OFF. turn_off forbids percentage. No generic
    services, bulk targets, configuration plans or panel approvals are involved.
    Fan motion/speed changes and automation reactions are possible; downstream
    consumer coverage is incomplete. Verification establishes HA-reported state
    and exact percentage, not independent physical feedback. Restoration is a
    separate owner-requested operation after reconciliation, never an automatic
    retry. Inspect the task outcome, not just the outer response success flag.
    """
    return await run_structured(
        "control_fan", "Returned the authoritative ordinary fan operation receipt.",
        lambda: FAN_OPERATIONS.require().control(FanRequest(
            entity_id=entity_id, action=action, operation_id=operation_id,
            percentage=percentage,
        ).checked()),
        metadata={"provider": "upstream_typed_fan", "fallback": "none",
                  "task_id": digest({"fan_operation_id": operation_id})[:32],
                  "operation_id": operation_id},
        response_limit=SETTINGS.response_size_limit,
    )
