"""Authenticated ordinary helper value control; no general service exposure."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from .compatibility import SETTINGS
from ..helper_value.contracts import ENTITY_PATTERN, HelperRequest
from ..helper_value.service import HELPER_VALUE_OPERATIONS
from ..tool_framework import run_structured


async def control_helper(
    entity_id: Annotated[str, Field(pattern=ENTITY_PATTERN)],
    action: Literal["turn_on", "turn_off", "select_option", "set_value", "set_datetime", "press",
                    "increment", "decrement", "reset", "start", "pause", "cancel", "finish"],
    operation_id: Annotated[str, Field(pattern=r"^[0-9]{10}-[a-f0-9]{32}$")],
    value: StrictStr | StrictInt | StrictFloat | None = None,
    acknowledge_safety_critical: StrictBool = False,
) -> str:
    """Change one exact helper's value through Engineering's internal ha-mcp.

    Helper values are ordinary control, like a switch: no plan or panel approval.
    Use this instead of create_helper_state_plan / create_input_select_option_plan
    for value changes. Helper CONFIGURATION still uses configuration plans.
    Generate operation_id ONCE as current UTC epoch seconds + '-' + uuid4().hex;
    manage it automatically. The same ID and arguments only reconcile; changed
    arguments refuse. New IDs expire after five minutes. Read get_execution_task
    after a timeout. Never create a fresh ID to retry uncertain work.

    Allowed (anything else refuses): input_boolean turn_on/turn_off;
    input_select select_option value=<live option>; input_number set_value
    value=<number within live min/max on step>; input_text set_value
    value=<text within live length and pattern>; input_datetime set_datetime
    value='YYYY-MM-DD', 'HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS' per has_date/has_time;
    input_button press; counter increment/decrement/reset, set_value value=<int>;
    timer start (optional value='H:MM:SS'), pause, cancel, finish.

    An already-current value returns status no_change without dispatch. Statuses:
    succeeded_verified, changed_unverified, verification_failed, no_change,
    confirmation_required, refused, provider_unavailable. If status is
    confirmation_required, nothing was sent: tell the user the listed
    safety-critical automations and ask; call again with
    acknowledge_safety_critical=true ONLY after the user confirms in chat. Never
    set it pre-emptively. The result lists known automation consumers and
    template readers as information; coverage may be incomplete.
    """
    metadata = {"provider": "upstream_typed_helper_value", "fallback": "none", "operation_id": operation_id}

    async def run():
        service = HELPER_VALUE_OPERATIONS.require()
        request = HelperRequest(entity_id=entity_id, action=action, operation_id=operation_id, value=value,
                                acknowledge_safety_critical=acknowledge_safety_critical).checked()
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
        "control_helper", "Returned the authoritative ordinary helper value operation result.",
        run, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )
