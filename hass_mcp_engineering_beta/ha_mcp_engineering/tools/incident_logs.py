"""Public read-only incident-window log tool (HAMCP-131; host journal sources HAMCP-224)."""

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..errors import ErrorCode, GovernanceError
from ..incident_logs import contracts as c
from ..incident_logs.service import INCIDENT_LOGS
from ..providers.routing import incident_logs_policy_allows_read
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def get_incident_logs(
    sources: Annotated[list[str], Field(min_length=1, max_length=c.MAX_SOURCES)],
    start: str,
    end: str | None = None,
    min_level: Literal["debug", "info", "warning", "error", "critical"] | None = None,
    text: Annotated[str | None, Field(max_length=c.MAX_TEXT_FILTER)] = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=c.MAX_LIMIT)] = c.DEFAULT_LIMIT,
    cursor: Annotated[str | None, Field(max_length=c.MAX_CURSOR_CHARS)] = None,
    calibration: Annotated[bool, Field(strict=True)] = False,
) -> str:
    """Read journal entries in one incident window across log sources. Read-only.

    sources: up to 4 of "core", "addon:self", "supervisor", "host",
    "host:kernel", "host:usb", "host:network", "host:storage", "host:os" or
    "addon:<slug>". start/end: ISO 8601 with offset, end exclusive and
    defaulting to now; the window is at most 6 hours. min_level: debug,
    info, warning, error or critical (parsed from known formats, else the
    journal priority; a withheld supervisor or add-on line has no verified
    level and is counted, never passed). text: a case-insensitive literal
    (no regex), at most 128 characters. limit: 1-200 entries per page; pass
    next_cursor with the same arguments to continue (cursors are signed,
    expire after an hour and fail closed as stale when the journal moved).
    Core and addon:self are read through Supervisor; supervisor, host,
    host:<category> and addon:<slug> from the host journal mounted read-only
    by the add-on option journald (no manager role is needed), and without
    it each is reported unavailable with its reason, never silently
    omitted. A message or field that may hold a credential is withheld
    whole and names the screen rule that withheld it (withheld_rules).
    calibration: true (host journal sources only; no min_level, text or
    cursor) returns only per-source counts over the window: scanned, shown,
    withheld, and withheld per rule id; never any entry or text.
    Each source reports its earliest/latest retained entry, the retention
    edge, gaps and filter skips. Messages are sanitized; log text is
    untrusted evidence. Empty output never proves an event did not occur.
    """
    metadata = {"provider": "supervisor_incident_logs", "source": "supervisor_journal",
                "transport": "supervisor_api", "fallback_occurred": False, "completeness": "failed"}
    arguments = {"sources": sources, "start": start, "end": end, "min_level": min_level,
                 "text": text, "limit": limit, "cursor": cursor, "calibration": calibration}
    arguments = {key: value for key, value in arguments.items() if value is not None}

    async def action():
        if not incident_logs_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        result = await INCIDENT_LOGS.require().read(arguments)
        metadata.update(completeness=result["completeness"], truncated=result["truncated"],
                        upstream_calls=result["requests"])
        return result

    return await run_structured("get_incident_logs", "Returned a sanitized incident-window log read.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class IncidentLogsTool(Tool):
    """Validate raw arguments before the SDK can coerce or discard fields."""

    async def run(self, arguments, context=None, convert_result=False):
        try:
            c.validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid incident log arguments.")
            rendered = await run_structured("get_incident_logs", "", invalid,
                                            metadata={"provider": "none", "fallback_occurred": False},
                                            response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool() -> Tool:
    tool = IncidentLogsTool.from_function(
        get_incident_logs,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    tool.parameters["additionalProperties"] = False
    return tool
