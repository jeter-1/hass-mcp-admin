"""Authenticated ordinary device-configuration control (HAMCP-297).

Registered in tools/registry.py. No general service exposure, no plan and no
approval panel.
"""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictStr

from .compatibility import SETTINGS
from ..device_configuration.contracts import ENTITY_PATTERN, TASK_ID_PATTERN, DeviceConfigurationRequest
from ..device_configuration.service import DEVICE_CONFIGURATION_OPERATIONS
from ..tool_framework import run_structured


async def control_device_configuration(
    entity_id: Annotated[str, Field(pattern=ENTITY_PATTERN)],
    action: Literal["select_option", "turn_on", "turn_off", "set_value", "undo"],
    operation_id: Annotated[str, Field(pattern=r"^[0-9]{10}-[a-f0-9]{32}$")],
    value: StrictStr | None = None,
    undo_task_id: Annotated[str, Field(pattern=TASK_ID_PATTERN)] | None = None,
    acknowledge_safety_critical: StrictBool = False,
) -> str:
    """Change one exact device CONFIGURATION entity, like a switch: no plan or approval.

    Admitted targets only (anything else refuses): Matter power-on behavior
    selects (select, translation key startup_on_off), Matter child-lock switches
    and Matter sensitivity-level numbers, each a configuration-category entity of
    an enabled device. select select_option value=<live option, exact string>;
    switch turn_on/turn_off; number set_value value=<decimal string within live
    min/max on step, e.g. "2">. Values are never rounded or clamped.
    Generate operation_id ONCE as current UTC epoch seconds + '-' + uuid4().hex.
    The same ID and arguments only reconcile; changed arguments refuse. New IDs
    expire after five minutes. Read get_execution_task after a timeout. Never
    create a fresh ID to retry uncertain work.

    After succeeded_verified, the result's undo field names the task to undo:
    action=undo undo_task_id=<that task_id> (new operation_id) restores the prior
    value only while the changed value is still exactly as verified; otherwise
    make an ordinary new change. Statuses: succeeded_verified, no_change,
    changed_unverified, verification_failed, confirmation_required, refused,
    provider_unavailable, in_progress. Tell the user the result's disclosures
    (persistence, power-outage behavior, physical access, sensing). If status is
    confirmation_required, nothing was sent: tell the user the reason and ask;
    call again with acknowledge_safety_critical=true ONLY after the user confirms
    in chat. Never set it pre-emptively.
    """
    metadata = {"provider": "direct_home_assistant_device_configuration", "fallback": "none",
                "operation_id": operation_id}

    async def run():
        service = DEVICE_CONFIGURATION_OPERATIONS.require()
        raw = {"entity_id": entity_id, "action": action, "operation_id": operation_id,
               "acknowledge_safety_critical": acknowledge_safety_critical}
        if value is not None:
            raw["value"] = value
        if undo_task_id is not None:
            raw["undo_task_id"] = undo_task_id
        request = DeviceConfigurationRequest.parse(raw)
        try:
            result = await service.control(request)
        except Exception:
            # Guidance follows durable state: no pointer before a saved task.
            metadata.update(service.failure_guidance(request))
            raise
        if result.get("status") != "confirmation_required":
            metadata["task_id"] = request.task_id
        return result

    return await run_structured(
        "control_device_configuration",
        "Returned the authoritative ordinary device-configuration operation result.",
        run, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )
