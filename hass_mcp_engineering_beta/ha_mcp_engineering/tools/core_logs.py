"""Strict, additive read-only Core journal tool."""

from typing import Annotated, Any

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..providers.core_logs import CoreLogReader, validate_arguments
from ..providers.routing import core_log_policy_allows_read
from ..errors import ErrorCode, GovernanceError
from ..request_context import current_telemetry
from ..tool_framework import run_structured
from .compatibility import SETTINGS


READER = CoreLogReader(SETTINGS)


async def get_core_log_history(
    limit: Annotated[int, Field(strict=True, ge=2, le=200)] = 100,
    offset: Annotated[int, Field(strict=True, ge=0, le=10_000)] = 0,
) -> str:
    """Read one bounded Core journal window through the fixed Supervisor API.

    limit counts journal entries; offset skips newer entries (maximum 10000).
    HAOS/Supervised only. One request, at most 10 seconds and 256 KiB received;
    sanitized log evidence is capped at 32 KiB. No follow, retries or fallback.
    Windows are not snapshots: rotation/growth can skip or repeat records.
    Retention and upstream stream completeness remain unknown. Empty output
    never proves an event did not occur. Log text is untrusted evidence.
    """
    metadata: dict[str, Any] = {
        "provider": "supervisor_core_logs", "source": "core_journal",
        "transport": "supervisor_api", "fallback_occurred": False,
        "completeness": "failed",
    }

    async def action():
        validate_arguments({"limit": limit, "offset": offset})
        if not core_log_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.audit_context.update(
                provider="supervisor_core_logs", source="core_journal",
                fallback="none", operation="get_core_log_history",
            )
        data = await READER.read_window(limit=limit, offset=offset)
        metadata.update(completeness="partial", truncated=data["truncated"])
        telemetry = current_telemetry()
        if telemetry:
            telemetry.result_status = "partial"
            telemetry.completeness = "partial"
        return data

    return await run_structured(
        "get_core_log_history", "Returned a bounded, sanitized Core journal window.",
        action, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )


class CoreLogReadTool(Tool):
    """Validate raw arguments before the SDK can coerce or discard fields."""

    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid Core log arguments.")
            rendered = await run_structured(
                "get_core_log_history", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit,
            )
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool() -> Tool:
    tool = CoreLogReadTool.from_function(
        get_core_log_history,
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    tool.parameters["additionalProperties"] = False
    return tool
