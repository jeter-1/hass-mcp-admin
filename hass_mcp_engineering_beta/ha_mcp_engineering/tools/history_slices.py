"""Public read-only continuable entity history tool (HAMCP-131)."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..errors import ErrorCode, GovernanceError
from ..history_slices import contracts as c
from ..history_slices.service import HISTORY_SLICES
from ..providers.routing import history_slices_policy_allows_read
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def get_history_slices(
    entity_id: Annotated[str, Field(max_length=255)],
    start: str,
    end: str | None = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=c.MAX_LIMIT)] = c.DEFAULT_LIMIT,
    cursor: Annotated[str | None, Field(max_length=c.MAX_CURSOR_CHARS)] = None,
) -> str:
    """Read one entity's state history in pages that continue safely. Read-only.

    start/end: ISO 8601 with offset, end exclusive and defaulting to now;
    the window is at most 7 days and is read in fixed 6-hour slices through
    the admitted, sanitized ha_get_history route (ascending, minimal). limit:
    1-100 states per page; pass next_cursor with the same arguments to
    continue (cursors are signed and expire after an hour). Each page
    re-checks the last state it read and the state at each slice start, so a
    purge, a late write or a changed provider fails closed as a stale cursor
    instead of skipping or repeating states. Coverage reports each slice's
    offsets and the bound through which the window is complete;
    state_at_window_start appears on the first page. Values are untrusted
    evidence. Empty output never proves a state change did not occur.
    """
    metadata = {"provider": "engineering", "data_providers": ["upstream_read_gateway"],
                "source": "ha_get_history", "fallback_occurred": False, "completeness": "failed"}
    arguments = {"entity_id": entity_id, "start": start, "end": end, "limit": limit, "cursor": cursor}
    arguments = {key: value for key, value in arguments.items() if value is not None}

    async def action():
        if not history_slices_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        result = await HISTORY_SLICES.require().read(arguments)
        metadata.update(completeness=result["completeness"], truncated=result["truncated"],
                        upstream_calls=result["upstream_calls"])
        return result

    return await run_structured("get_history_slices", "Returned a sanitized continuable history page.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class HistorySlicesTool(Tool):
    """Validate raw arguments before the SDK can coerce or discard fields."""

    async def run(self, arguments, context=None, convert_result=False):
        try:
            c.validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid history slice arguments.")
            rendered = await run_structured("get_history_slices", "", invalid,
                                            metadata={"provider": "none", "fallback_occurred": False},
                                            response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool() -> Tool:
    tool = HistorySlicesTool.from_function(
        get_history_slices,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    tool.parameters["additionalProperties"] = False
    return tool
