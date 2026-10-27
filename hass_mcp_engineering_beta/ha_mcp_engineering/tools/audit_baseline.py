"""Native baseline capture (stored, caller-bound export pages) and stored compare (HAMCP-228)."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from ..audit_baseline.capture_contracts import (validate_arguments, validate_compare_arguments,
                                                CaptureError)
from ..audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
from ..audit_baseline.storage_screen import withheld_counts
from ..request_context import current_telemetry
from ..tool_framework import run_structured
from ..untrusted_text import EnvelopeKeyWithheld, screen_envelope
from .compatibility import SETTINGS


def _final_screen(service, result):
    """``(result, newly withheld count)``: the final-envelope backstop (HAMCP-226/228 review 1, H1).

    Behind the projector's and the comparison's own screens, so a field added
    later cannot return Home Assistant text unscreened. A withheld mapping key
    refuses the response.
    """

    projector = getattr(getattr(service, "provider", None), "projector", None)
    known = tuple(dict.fromkeys(
        item for item in (*getattr(projector, "known_secrets", ()), getattr(SETTINGS, "access_secret", ""),
                          getattr(SETTINGS, "ha_token", "")) if isinstance(item, str) and item))
    try:
        return screen_envelope(result, known)
    except EnvelopeKeyWithheld:
        raise CaptureError("redacted_identity") from None


async def capture_automation_baseline(
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 25,
    cursor: Annotated[str, Field(max_length=102)] = "",
    resume_capture_id: Annotated[str, Field(max_length=32)] = "",
) -> str:
    """Capture exact configuration hashes for administrator-visible loaded automations.

    Includes switched-off loaded automations; this is not all stored YAML or
    complete registry inventory. Requires reviewed native metadata admission and
    a verified Hass.io Core registry-lineage anchor, not a physical installation
    UUID. Captures are non-atomic, bounded to 1000 records and 45 seconds of
    collection with four configuration reads at a time. No Home Assistant
    writes, retries or fallback.
    Export baseline_header plus all ordered records; validate artifact_sha256.
    Each finished capture is stored durably (12 baselines, 180 days; refused
    when full). Continuation cursors last ten minutes with zero HA reads.
    resume_capture_id re-reads the inventory and re-collects only that
    capture's timed-out records into a new baseline; it refuses when the
    inventory changed. Compare with compare_automation_baselines.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "fallback_occurred": False, "upstream_calls": 0}
    warnings = []
    async def action():
        validate_arguments({"limit": limit, "cursor": cursor, "resume_capture_id": resume_capture_id})
        service = AUTOMATION_BASELINE_CAPTURE.require()
        result = await service.capture(limit=limit, cursor=cursor, resume_capture_id=resume_capture_id)
        # The storage screen already withheld what it must (review 2, R1). An
        # exported page must match artifact_sha256 exactly, so this backstop
        # refuses a page instead of replacing a value.
        if _final_screen(service, result)[1]:
            raise CaptureError("redacted_identity")
        header = result["baseline_header"]
        withheld_records, withheld_fields = withheld_counts(result["diagnostics"])
        partial = (header["inventory"]["completeness"] != "complete"
                   or header["installation"]["status"] != "established"
                   or result["diagnostics"].get("configuration_incomplete_count", 0) > 0
                   or header["consistency"]["inventory_drift"] != "none_observed_at_capture_fences"
                   or withheld_records > 0 or withheld_fields > 0)
        if withheld_records or withheld_fields:
            warnings.append(f"The credential screen withheld {withheld_records} record(s) and "
                            f"{withheld_fields} field(s) before storage; see diagnostics.credential_screen.")
        metadata["completeness"] = "partial" if partial else "complete"
        telemetry = current_telemetry()
        if telemetry:
            telemetry.completeness = metadata["completeness"]
            telemetry.result_status = "partial" if partial else "success"
            telemetry.audit_context.update(scope=header["inventory"]["scope"],
                capture_id=result["snapshot"]["capture_id"], continuation=bool(cursor),
                resumed_from=result["snapshot"]["resumed_from"],
                returned_records=len(result["records"]), fallback="none")
        return result
    return await run_structured("capture_automation_baseline", "Returned frozen loaded-automation baseline evidence.",
        action, warnings=warnings, metadata=metadata, response_limit=min(60_000, SETTINGS.response_size_limit))


class RecordedFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    configuration_id: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")]
    fingerprint: Annotated[str, Field(min_length=23, max_length=71, pattern=r"^sha256:[0-9a-f]{16,64}$")]


async def compare_automation_baselines(
    earlier_capture_id: Annotated[str, Field(min_length=32, max_length=32)],
    later_capture_id: Annotated[str, Field(max_length=32)] = "",
    recorded_fingerprints: Annotated[list[RecordedFingerprint] | None, Field(max_length=1000)] = None,
    fingerprint_model: Annotated[str, Field(max_length=64)] = "",
    cursor: Annotated[str, Field(max_length=102)] = "",
) -> str:
    """Compare your stored automation baseline with a later capture or recorded fingerprints.

    No Home Assistant read: only your own stored captures (capture_id from
    capture_automation_baseline). Give later_capture_id, or
    recorded_fingerprints ({configuration_id, fingerprint: "sha256:" plus 16-64
    hex}) with fingerprint_model. UNCHANGED means a full digest match; a
    shorter matching prefix is PREFIX_MATCH (reason prefix_match:<n>), counted
    separately. Recorded fingerprints carry no inventory authority: an id on
    one side only is UNKNOWN, never ADDED/REMOVED. Rows needing attention come
    first. Counts cover every row; records are paged (up to 100): repeat the
    same arguments with pagination.next_cursor until it is null.
    """
    metadata = {"provider": "engineering", "data_providers": [],
                "fallback_occurred": False, "upstream_calls": 0}
    warnings = []
    rows = None if recorded_fingerprints is None else [row.model_dump() for row in recorded_fingerprints]
    async def action():
        service = AUTOMATION_BASELINE_CAPTURE.require()
        result = await service.compare(
            earlier_capture_id=earlier_capture_id, later_capture_id=later_capture_id,
            recorded_fingerprints=rows, fingerprint_model=fingerprint_model, cursor=cursor)
        result, hits = _final_screen(service, result)
        result["screened_withheld_count"] += hits
        partial = result["screened_withheld_count"] > 0
        metadata["completeness"] = "partial" if partial else "complete"
        if result["screened_withheld_count"]:
            warnings.append("Some values were withheld by the credential screen.")
        telemetry = current_telemetry()
        if telemetry:
            telemetry.completeness = metadata["completeness"]
            telemetry.result_status = "partial" if partial else "success"
            telemetry.audit_context.update(mode=result["mode"], counts=dict(result["counts"]),
                continuation=bool(cursor), returned_records=result["pagination"]["returned"],
                total_records=result["pagination"]["total_records"],
                screened_withheld_count=result["screened_withheld_count"], fallback="none")
        return result
    return await run_structured("compare_automation_baselines", "Compared stored automation baseline evidence.",
        action, warnings=warnings, metadata=metadata, response_limit=min(60_000, SETTINGS.response_size_limit))


_VALIDATORS = {"capture_automation_baseline": validate_arguments,
               "compare_automation_baselines": validate_compare_arguments}


def validate_raw_arguments(tool_name, arguments):
    """The closed contract on raw arguments, shared by the transport and the tool."""

    _VALIDATORS[tool_name](arguments)


class BaselineTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_raw_arguments(self.name, arguments)
        except CaptureError:
            async def invalid():
                raise CaptureError("invalid_arguments")
            result = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False}, response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(result) if convert_result else result
        return await super().run(arguments, context, convert_result)


def _tool(function, *, read_only):
    tool = BaselineTool.from_function(function, annotations=ToolAnnotations(
        readOnlyHint=read_only, destructiveHint=False, idempotentHint=read_only, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool


def registered_tool():
    # HAMCP-228: a capture now writes one Engineering-local stored baseline.
    return _tool(capture_automation_baseline, read_only=False)


def registered_compare_tool():
    return _tool(compare_automation_baselines, read_only=True)
