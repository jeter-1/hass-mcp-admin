"""Beta-native entity dependency analysis MCP tool."""

import time
from typing import Annotated, Literal

from pydantic import Field

from ..dependency import DEPENDENCY_ANALYSIS
from ..impact import CHANGE_IMPACT_ANALYSIS
from ..integrity import CONFIGURATION_INTEGRITY_ANALYSIS
from ..reliability import RELIABILITY_ANALYSIS
from ..errors import ErrorCode, map_exception
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


async def change_impact_analysis(
    entity_id: str,
    operation: Literal["rename_entity", "remove_entity", "disable_entity"],
    replacement_entity_id: str = "",
    include_indirect: bool = True,
    max_depth: Annotated[int, Field(ge=1, le=3)] = 2,
    source_types: list[
        Literal[
            "automation",
            "blueprint",
            "script",
            "scene",
            "group",
            "template",
            "dashboard",
        ]
    ] = [],
    detail_level: Literal["summary", "standard", "evidence"] = "standard",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str = "",
    refresh_index: bool = False,
) -> str:
    """Analyze known effects of renaming, removing, or disabling one entity.

    This operation is read-only. Rename requires a distinct canonical
    replacement_entity_id; remove and disable reject one. It reuses the
    bounded dependency index, reports unsupported sources honestly, and never
    changes Home Assistant, calls a service, or creates a change plan.
    """

    started = time.perf_counter()
    telemetry = current_telemetry()
    try:
        output = await CHANGE_IMPACT_ANALYSIS.require().analyze(
            entity_id=entity_id,
            operation=operation,
            replacement_entity_id=replacement_entity_id,
            include_indirect=include_indirect,
            max_depth=max_depth,
            source_types=source_types,
            detail_level=detail_level,
            limit=limit,
            cursor=cursor,
            refresh_index=refresh_index,
        )
        if telemetry:
            telemetry.result_status = "partial" if output.partial else "success"
            telemetry.completeness = "partial" if output.partial else "complete"
        assessment = output.data.get("final_assessment", "review_required")
        return SuccessResponse(
            operation="change_impact_analysis",
            summary=(
                "Completed bounded single-entity change-impact analysis; blocking impacts were found."
                if assessment == "blocking_impacts_found"
                else "Completed bounded single-entity change-impact analysis; review is required."
                if assessment == "review_required"
                else "Completed bounded single-entity change-impact analysis with incomplete coverage."
                if assessment == "no_known_impacts_with_incomplete_coverage"
                else "Completed bounded single-entity change-impact analysis with complete reported coverage."
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
        local_validation = code in {
            ErrorCode.INVALID_REQUEST,
            ErrorCode.VALIDATION_FAILURE,
        }
        metadata = {
            "routing": {
                "lifecycle_status": "beta_native",
                "classification": "engineering_native",
                "provider": "engineering",
                "policy": "single_entity_change_impact_read",
                "access": "read",
                "fallback_occurred": False,
            },
            "source_coverage": [
                {
                    "source_type": "request_validation"
                    if local_validation
                    else "change_impact_evidence",
                    "provider": "engineering",
                    "completeness": "unavailable",
                    "failure_category": "request_validation"
                    if local_validation
                    else "provider_error",
                    "upstream_attempted": bool(
                        telemetry and telemetry.ha_request_count > 0
                    ),
                }
            ],
        }
        return FailureResponse(
            operation="change_impact_analysis",
            error=type(exc).__name__,
            error_code=code.value,
            message=message,
            details=details,
            retryable=retryable,
            metadata=metadata,
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)


async def configuration_integrity_analysis(
    source_types: list[
        Literal[
            "automation",
            "blueprint",
            "script",
            "scene",
            "group",
            "template",
            "dashboard",
        ]
    ] = [],
    finding_types: list[
        Literal[
            "missing_entity_reference",
            "disabled_entity_reference",
            "registry_only_entity_reference",
            "orphan_registry_candidate",
            "unresolved_dynamic_reference",
        ]
    ] = [],
    include_orphan_candidates: bool = True,
    detail_level: Literal["summary", "standard", "evidence"] = "standard",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str = "",
    refresh_index: bool = False,
) -> str:
    """Audit global Home Assistant configuration integrity using bounded evidence.

    Reports exact references to missing, disabled, or registry-only entities,
    conservative orphan-registry candidates, and unresolved dynamic references.
    This read-only operation never deletes, rewrites, or changes Home Assistant.
    """

    started = time.perf_counter()
    telemetry = current_telemetry()
    try:
        output = await CONFIGURATION_INTEGRITY_ANALYSIS.require().analyze(
            source_types=source_types,
            finding_types=finding_types,
            include_orphan_candidates=include_orphan_candidates,
            detail_level=detail_level,
            limit=limit,
            cursor=cursor,
            refresh_index=refresh_index,
        )
        if telemetry:
            telemetry.result_status = "partial" if output.partial else "success"
            telemetry.completeness = "partial" if output.partial else "complete"
        assessment = output.data.get("final_assessment")
        summary = (
            "Completed bounded configuration-integrity analysis; review is required."
            if assessment == "review_required"
            else "Completed bounded configuration-integrity analysis with incomplete coverage."
            if assessment == "assessment_incomplete"
            else "No confirmed integrity findings were detected within the reported coverage."
        )
        return SuccessResponse(
            operation="configuration_integrity_analysis",
            summary=summary,
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
        local_validation = code in {
            ErrorCode.INVALID_REQUEST,
            ErrorCode.VALIDATION_FAILURE,
            ErrorCode.INVALID_CURSOR,
            ErrorCode.STALE_CURSOR,
        }
        return FailureResponse(
            operation="configuration_integrity_analysis",
            error=type(exc).__name__,
            error_code=code.value,
            message=message,
            details=details,
            retryable=retryable,
            metadata={
                "routing": {
                    "lifecycle_status": "beta_native",
                    "classification": "engineering_native",
                    "provider": "engineering",
                    "policy": "global_configuration_integrity_read",
                    "access": "read",
                    "fallback_occurred": False,
                },
                "source_coverage": [
                    {
                        "source_type": "request_validation"
                        if local_validation
                        else "configuration_integrity_evidence",
                        "provider": "engineering",
                        "completeness": "unavailable",
                        "failure_category": "request_validation"
                        if local_validation
                        else "provider_error",
                        "upstream_attempted": bool(
                            telemetry and telemetry.ha_request_count > 0
                        ),
                    }
                ],
            },
            timing=timing_since(started),
            request_id=current_request_id(),
        ).to_json(SETTINGS.response_size_limit)


ANALYSIS_TOOLS = (
    entity_dependency_analysis,
    automation_reliability_analysis,
    change_impact_analysis,
    configuration_integrity_analysis,
)
