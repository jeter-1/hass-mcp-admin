"""Public fixed Alarmo read surface with non-reflecting input/error boundaries."""

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from ..integration_inspection.contracts import validate_arguments
from ..integration_inspection.models import InspectionError
from ..integration_inspection.runtime import INTEGRATION_INSPECTION
from ..tool_framework import run_structured
from ..observability import METRICS
from .compatibility import SETTINGS


async def get_integration_inspection(
    alarm_entity_id: str,
    integration: Literal["alarmo"] = "alarmo",
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 25,
    cursor: Annotated[str, Field(max_length=2048)] = "",
) -> str:
    """Inspect configured Alarmo membership/modes using fixed native read callbacks.

    Requires an exact alarm_control_panel entity, reviewed Alarmo source profile
    and separately admitted Core metadata authority. No options flows, actions,
    secrets, diagnostics, retries or fallback. Configuration is not proof of
    active protection or effective sensor timing. Collection is integration-wide,
    bounded to nine commands/30 seconds, projected to this area or master.
    Partial coverage and non-atomic capture remain explicit. A cursor serves the
    same sanitized five-minute snapshot with zero new HA reads.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "upstream_calls": 0, "fallback_occurred": False}

    async def action():
        try:
            validate_arguments(dict(alarm_entity_id=alarm_entity_id, integration=integration, limit=limit, cursor=cursor))
            result = await INTEGRATION_INSPECTION.require().inspect(
                alarm_entity_id=alarm_entity_id, integration=integration, limit=limit, cursor=cursor)
        except ValidationError:
            METRICS.inspection_refusal_count += 1
            raise InspectionError("malformed_response") from None
        except (InspectionError, ValueError):
            METRICS.inspection_refusal_count += 1
            raise
        METRICS.inspection_read_count += 1
        METRICS.inspection_partial_count += int(result["assessment"] != "complete")
        METRICS.inspection_omitted_count += result["truncation"]["omitted_count"] or 0
        metadata["completeness"] = result["assessment"]
        return result

    return await run_structured("get_integration_inspection", "Returned configured Alarmo evidence.", action,
                                metadata=metadata, response_limit=min(60_000, SETTINGS.response_size_limit))


class IntegrationInspectionTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid integration inspection arguments.")
            rendered = await run_structured("get_integration_inspection", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False}, response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = IntegrationInspectionTool.from_function(get_integration_inspection,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
