"""Public read-only automation run counts from the native logbook (HAMCP-476)."""

import json
from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..automation_runs import contracts as c
from ..automation_runs.service import count_runs, size_error
from ..errors import ErrorCode, GovernanceError
from ..providers.routing import automation_run_counts_policy_allows_read
from ..tool_framework import run_structured
from .compatibility import REST_CLIENT, SETTINGS


async def get_automation_run_counts(
    automation_ids: Annotated[list[str] | None, Field(min_length=1, max_length=c.MAX_IDS)] = None,
    all_automations: bool | None = None,
    hours: float | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """Count how many times automations ran in a time window. Read-only.

    A run is one automation_triggered logbook row: the conditions passed and
    the action started. Failed-condition triggers and runs the automation's
    mode dropped are not counted. Pass automation_ids (1-10 automation.*
    IDs, counted in request order; an ID with no runs, or no such
    automation, counts 0) or all_automations=true (every automation in the
    current states, at most 200, ranked by runs descending, ties by ID).
    Window: hours back from end_time (default 24, maximum 168), or
    start_time to end_time (ISO 8601 with offset, start in the past, a
    future end clamped to now); bounds are exclusive. Each batch of 10 is
    one logbook GET over the whole window, split in halves when a body
    exceeds a logbook limit. Budgets: 8 GETs and 60 seconds for
    automation_ids, 32 GETs and 120 seconds for all_automations. A spent
    budget or an unsplittable window returns partial with partial_reasons
    and the uncovered automations and intervals; their counts are lower
    bounds. A set whose reply cannot fit the response limit is refused
    before any logbook read. Any other failure is an error, never partial.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "fallback_occurred": False, "completeness": "failed"}
    arguments = {"automation_ids": automation_ids, "all_automations": all_automations,
                 "hours": hours, "start_time": start_time, "end_time": end_time}
    arguments = {key: value for key, value in arguments.items() if value is not None}

    limit = min(c.MAX_RESPONSE_CHARS, SETTINGS.response_size_limit)
    counted = {}

    async def action():
        if not automation_run_counts_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        result = await count_runs(REST_CLIENT, c.validate_arguments(arguments), response_limit=limit)
        metadata["completeness"] = "complete" if result["complete"] else "partial"
        counted["automation_count"] = result["automation_count"]
        return result

    rendered = await run_structured("get_automation_run_counts", "Returned automation run counts.",
                                    action, metadata=metadata, response_limit=limit)
    value = json.loads(rendered)
    if value.get("success") is not True or _whole(value):
        return rendered

    # The size bound cut a success: never return a paged or cut count list.
    async def overflow():
        raise size_error(counted["automation_count"], limit)

    metadata["completeness"] = "failed"
    return await run_structured("get_automation_run_counts", "", overflow,
                                metadata=metadata, response_limit=limit)


def _whole(value):
    """True when a rendered success carries every counted automation, uncut."""

    data = value.get("data")
    return ("response_completeness" not in value and isinstance(data, dict)
            and isinstance(data.get("counts"), list) and "uncovered" in data
            and len(data["counts"]) == data.get("automation_count"))


class AutomationRunCountsTool(Tool):
    """Validate raw arguments before the SDK can coerce or discard fields."""

    async def run(self, arguments, context=None, convert_result=False):
        try:
            c.validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError(c.INVALID_MESSAGE)
            rendered = await run_structured("get_automation_run_counts", "", invalid,
                                            metadata={"provider": "none", "fallback_occurred": False},
                                            response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool() -> Tool:
    tool = AutomationRunCountsTool.from_function(
        get_automation_run_counts,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    tool.parameters["additionalProperties"] = False
    return tool
