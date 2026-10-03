"""One read-only native capture surface with frozen, caller-bound export pages."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..audit_baseline.capture_contracts import validate_arguments, CaptureError
from ..audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
from ..request_context import current_telemetry
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def capture_automation_baseline(
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 25,
    cursor: Annotated[str, Field(max_length=102)] = "",
) -> str:
    """Capture exact configuration hashes for administrator-visible loaded automations.

    Includes switched-off loaded automations; this is not all stored YAML or
    complete registry inventory. Requires reviewed native metadata admission and
    a verified Hass.io Core registry-lineage anchor, not a physical installation
    UUID. Captures are non-atomic, bounded to 1000 records and 45 seconds of
    collection with four configuration reads at a time. No writes/retries/fallback.
    Export baseline_header plus all ordered records; validate artifact_sha256.
    Continuations reuse one ten-minute memory snapshot with zero HA reads.
    Compare distinct ordered exports using the existing offline comparator.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "fallback_occurred": False, "upstream_calls": 0}
    async def action():
        validate_arguments({"limit": limit, "cursor": cursor})
        result = await AUTOMATION_BASELINE_CAPTURE.require().capture(limit=limit, cursor=cursor)
        header = result["baseline_header"]
        partial = (header["inventory"]["completeness"] != "complete"
                   or header["installation"]["status"] != "established"
                   or result["diagnostics"].get("configuration_incomplete_count", 0) > 0
                   or header["consistency"]["inventory_drift"] != "none_observed_at_capture_fences")
        metadata["completeness"] = "partial" if partial else "complete"
        telemetry = current_telemetry()
        if telemetry:
            telemetry.completeness = metadata["completeness"]
            telemetry.result_status = "partial" if partial else "success"
            telemetry.audit_context.update(scope=header["inventory"]["scope"],
                capture_id=result["snapshot"]["capture_id"], continuation=bool(cursor),
                returned_records=len(result["records"]), fallback="none")
        return result
    return await run_structured("capture_automation_baseline", "Returned frozen loaded-automation baseline evidence.",
        action, metadata=metadata, response_limit=min(60_000, SETTINGS.response_size_limit))


class BaselineTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except CaptureError:
            async def invalid():
                raise CaptureError("invalid_arguments")
            result = await run_structured("capture_automation_baseline", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False}, response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(result) if convert_result else result
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = BaselineTool.from_function(capture_automation_baseline,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
