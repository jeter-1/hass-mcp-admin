"""Beta-native entity dependency analysis MCP tool."""

import time
from typing import Annotated, Literal

from pydantic import Field

from ..dependency import DEPENDENCY_ANALYSIS
from ..reliability import RELIABILITY_ANALYSIS
from ..errors import map_exception
from ..models import FailureResponse, SuccessResponse
from ..observability import METRICS
from ..request_context import current_request_id, current_telemetry
from ..tool_framework import timing_since
from .compatibility import SETTINGS


async def entity_dependency_analysis(
    entity_id: str,
    detail_level: Literal["summary", "standard", "evidence"] = "summary",
    include_indirect: bool = False,
    max_depth: Annotated[int, Field(ge=1, le=3)] = 2,
    source_types: list[Literal["automation", "blueprint", "script", "scene", "group", "template", "dashboard"]] = [],
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    cursor: str = "",
    refresh_index: bool = False,
) -> str:
    """Find bounded static configuration references to one Home Assistant entity.

    The entity may be valid but currently missing. detail_level is summary,
    standard, or evidence. Optional indirect traversal follows only explicit
    group/template edges to max_depth 1-3. source_types may contain automation,
    blueprint, script, scene, group, template, or dashboard. Pagination cursors
    are opaque and tied to one dependency-index generation.
    """
    started = time.perf_counter()
    telemetry = current_telemetry()
    try:
        output = await DEPENDENCY_ANALYSIS.require().analyze(
            entity_id=entity_id,
            detail_level=detail_level,
            include_indirect=include_indirect,
            max_depth=max_depth,
            source_types=source_types,
            limit=limit,
            cursor=cursor,
            refresh_index=refresh_index,
        )
        if telemetry:
            telemetry.result_status = "partial" if output.partial else "success"
            telemetry.completeness = "partial" if output.partial else "complete"
        return SuccessResponse(
            operation="entity_dependency_analysis",
            summary=(
                "Found bounded Home Assistant configuration dependencies."
                if output.data["overview"]["direct_reference_count"]
                else "No dependencies were detected within the reported source coverage."
            ),
            data=output.data,
            warnings=output.warnings,
            metadata=output.metadata,
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)
    except Exception as exc:
        code, message, retryable, details = map_exception(exc)
        if telemetry:
            telemetry.error_code = code.value
            telemetry.result_status = "failure"
        return FailureResponse(
            operation="entity_dependency_analysis",
            error=type(exc).__name__,
            error_code=code.value,
            message=message,
            details=details,
            retryable=retryable,
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)


async def automation_reliability_analysis(
    automation_id: str,
    lookback_hours: Annotated[int, Field(ge=1, le=720)] = 168,
    trace_limit: Annotated[int, Field(ge=1, le=50)] = 10,
    detail_level: Literal["summary", "standard", "evidence"] = "standard",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str = "",
) -> str:
    """Analyze one automation using bounded configuration and runtime evidence.

    automation_id is the internal Home Assistant automation ID returned by
    list_automations, not an automation entity_id. The operation is read-only:
    it cannot trigger an automation, call a service, or modify configuration.
    Results are evidence-backed, paginated, and explicit about partial coverage.
    """
    started = time.perf_counter()
    telemetry = current_telemetry()
    try:
        output = await RELIABILITY_ANALYSIS.require().analyze(
            automation_id=automation_id,
            lookback_hours=lookback_hours,
            trace_limit=trace_limit,
            detail_level=detail_level,
            limit=limit,
            cursor=cursor,
        )
        if telemetry:
            telemetry.result_status = "partial" if output.partial else "success"
            telemetry.completeness = "partial" if output.partial else "complete"
        return SuccessResponse(
            operation="automation_reliability_analysis",
            summary=(
                "Completed a bounded single-automation reliability analysis with partial evidence."
                if output.partial
                else "Completed a bounded single-automation reliability analysis."
            ),
            data=output.data,
            warnings=output.warnings,
            metadata=output.metadata,
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)
    except Exception as exc:
        code, message, retryable, details = map_exception(exc)
        if telemetry:
            telemetry.error_code = code.value
            telemetry.result_status = "failure"
            telemetry.completeness = "failed"
        return FailureResponse(
            operation="automation_reliability_analysis",
            error=type(exc).__name__,
            error_code=code.value,
            message=message,
            details=details,
            retryable=retryable,
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)


ANALYSIS_TOOLS = (entity_dependency_analysis, automation_reliability_analysis)
