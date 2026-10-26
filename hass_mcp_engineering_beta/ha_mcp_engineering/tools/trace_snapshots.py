"""HAMCP-225 automation trace snapshot tools.

Each response leaves through the service's one strict sanitized exit; the
tool adds only fixed warnings and metadata. Raw arguments are validated by
the closed contract before any coercion.
"""

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..trace_snapshots import contracts as c
from ..trace_snapshots.runtime import TRACE_SNAPSHOTS
from ..tool_framework import run_structured
from .compatibility import SETTINGS


_PARTIAL_CAPTURE = ("Capture is partial: check each run's outcome and completeness, the selection "
                    "counts and the source listing.")
_PARTIAL_READ = ("Snapshot evidence is partial: values were redacted, truncated or withheld at capture "
                 "or on read, or a unit exceeded the page bound.")
_PARTIAL_LIST = "Some listed values were withheld by the strict read pass."


def _metadata(data_providers):
    return {"provider": "engineering", "data_providers": data_providers,
            "fallback_occurred": False, "upstream_calls": 0}


async def _respond(operation, summary, call, data_providers, partial_warning):
    metadata = _metadata(data_providers)
    warnings = []

    async def action():
        value, partial = await call(TRACE_SNAPSHOTS.require())
        metadata["completeness"] = "partial" if partial else "complete"
        if partial:
            warnings.append(partial_warning)
        return value

    return await run_structured(operation, summary, action, warnings=warnings, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


async def capture_automation_trace(
    automation_id: Annotated[str, Field(min_length=1, max_length=256)],
    run_id: Annotated[str, Field(max_length=128)] = "",
    include_condition_failures: Annotated[bool, Field(strict=True)] = False,
    note: Annotated[str, Field(max_length=c.NOTE_CHARS)] = "",
) -> str:
    """Store sanitized copies of an automation's retained Core traces before Core rotates them.

    Core keeps about five runs per automation. With run_id, exactly that run is
    captured; otherwise up to 5 retained runs, skipping not_triggered and
    (unless include_condition_failures) failed_conditions. A running run is not
    captured. Each run reports captured, already_captured, trace_not_retained,
    run_in_progress, over_size_bound, store_full or read_failed. Reads only
    trace/list and trace/get for the automation domain; writes only the
    Engineering-local store (200 snapshots, 256 KiB each, 90 days; refused when
    full). Values are sanitized before storage; a resolved !secret in a field
    that is neither credential-named nor credential-shaped is not detectable.
    """
    return await _respond(
        "capture_automation_trace", "Captured automation trace snapshots.",
        lambda service: service.capture(automation_id=automation_id, run_id=run_id,
                                        include_condition_failures=include_condition_failures, note=note),
        ["direct_ha_api"], _PARTIAL_CAPTURE)


async def list_trace_snapshots(
    automation_id: Annotated[str, Field(max_length=256)] = "",
    limit: Annotated[int, Field(strict=True, ge=1, le=c.LIST_LIMIT)] = 20,
    cursor: Annotated[str, Field(max_length=114)] = "",
) -> str:
    """List stored automation trace snapshots, newest first; no Home Assistant call.

    Shows store usage and each snapshot's origin (manual or trace_watch),
    completeness and expiry. The cursor is bound to this caller and ends at a
    server restart.
    """
    return await _respond(
        "list_trace_snapshots", "Listed stored automation trace snapshots.",
        lambda service: service.list(automation_id=automation_id, limit=limit, cursor=cursor),
        [], _PARTIAL_LIST)


async def get_trace_snapshot(
    snapshot_id: Annotated[str, Field(min_length=32, max_length=32)],
    cursor: Annotated[str, Field(max_length=102)] = "",
) -> str:
    """Read one stored automation trace snapshot in pages; no Home Assistant call.

    The first page carries the run summary, config, blueprint inputs and
    context; later pages carry steps in Core's path order. Every page is
    re-sanitized on read: a string partly redacted at capture is withheld
    whole. The cursor is bound to this caller and ends at a server restart.
    """
    return await _respond(
        "get_trace_snapshot", "Returned one stored automation trace snapshot page.",
        lambda service: service.get(snapshot_id=snapshot_id, cursor=cursor),
        [], _PARTIAL_READ)


async def set_automation_trace_watch(
    action: Literal["add", "remove", "list"],
    automation_id: Annotated[str, Field(max_length=256)] = "",
    mode: Literal["failures", "executed"] = "failures",
    duration_hours: Annotated[int, Field(strict=True, ge=1, le=c.WATCH_MAX_HOURS)] = 24,
) -> str:
    """Add, remove or list watched automations whose new runs are captured automatically.

    Read-only toward Home Assistant, so no change plan or approval; every add
    and remove is audited with the caller and every response returns the
    current watch list. At most 10 watches, each expiring within 168 hours;
    add on a watched automation replaces its mode and expiry. Every 30 s
    Engineering lists each watched automation's traces and captures new
    stopped runs matching the mode (failures: error, aborted, cancelled,
    failed_single, failed_max_runs, disallowed_recursion_detected or an error
    field; executed: every run except failed_conditions and not_triggered).
    Each watch keeps at most 20 snapshots and evicts only its own oldest;
    manual snapshots are never evicted. Polling pauses while Core authority is
    unavailable; missed runs are counted, never backfilled.
    """
    return await _respond(
        "set_automation_trace_watch", "Returned the automation trace watch list.",
        lambda service: service.watch(action=action, automation_id=automation_id, mode=mode,
                                      duration_hours=duration_hours),
        [], _PARTIAL_LIST)


_VALIDATORS = {
    "capture_automation_trace": c.validate_capture,
    "list_trace_snapshots": c.validate_list,
    "get_trace_snapshot": c.validate_get,
    "set_automation_trace_watch": c.validate_watch,
}


def validate_raw_arguments(tool_name, arguments):
    """Closed raw-argument check shared by the transport and the tool."""

    _VALIDATORS[tool_name](arguments)


class TraceSnapshotTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_raw_arguments(self.name, arguments)
        except c.TraceSnapshotError:
            async def invalid():
                raise c.TraceSnapshotError("invalid_arguments")
            result = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(result) if convert_result else result
        return await super().run(arguments, context, convert_result)


def _tool(function, *, read_only):
    tool = TraceSnapshotTool.from_function(function, annotations=ToolAnnotations(
        readOnlyHint=read_only, destructiveHint=False, idempotentHint=read_only, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool


def registered_tools():
    return {
        "capture_automation_trace": _tool(capture_automation_trace, read_only=False),
        "list_trace_snapshots": _tool(list_trace_snapshots, read_only=True),
        "get_trace_snapshot": _tool(get_trace_snapshot, read_only=True),
        "set_automation_trace_watch": _tool(set_automation_trace_watch, read_only=False),
    }
