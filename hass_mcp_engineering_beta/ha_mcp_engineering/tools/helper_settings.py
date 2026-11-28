"""Public read-only storage helper settings (HAMCP-519).

The input boundary validates the raw arguments before SDK coercion and never
reflects a refused value. The report reuses native search's Core read
session factory (``tools.search.SEARCH``), configured at application start,
so it reads only the fixed ``search.core_contracts`` reads under this
request's Core lease. Nothing falls back to a delegated read.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import paging
from ..errors import ErrorCode, GovernanceError
from ..helper_settings import contracts, service
from ..incident_logs.cursor import CursorCodec
from ..providers.routing import helper_settings_policy_allows_read
from ..request_context import current_telemetry
from ..tool_framework import run_structured
from .compatibility import SETTINGS, ListCursor
from .search import SEARCH

CURSORS = CursorCodec(contracts.NAME)
SUMMARY = "Returned storage helper settings."
DATA_PROVIDERS = ("direct_ha_api",)

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_PARTIAL = ("Helper settings are partial: see partial_reasons, unavailable_domains and each "
            "helper's invalid_fields and entity_id_status.")
_REDACTED = ("Engineering withheld credential-like text or shortened long strings; see "
             "redaction.")


def _response_limit() -> int:
    return min(60_000, SETTINGS.response_size_limit)


async def get_helper_settings(
    domains: Annotated[
        list[Literal[contracts.DOMAINS]], Field(min_length=1, max_length=len(contracts.DOMAINS)),
    ] | None = None,
    limit: Annotated[int, Field(ge=1, le=contracts.MAX_LIMIT)] = contracts.DEFAULT_LIMIT,
    cursor: ListCursor = "",
) -> str:
    """Read the stored settings of UI-created input helpers. Read-only.

    Lists input_boolean, input_number, input_select, input_text,
    input_datetime and input_button helpers from Home Assistant's storage
    collections (``domains`` selects some; default all), each with its
    entity_id from the entity registry, name, icon, initial value and
    settings: number min, max, step, mode, unit and device class; select
    options (first 100, with options_total); text min, max, mode, pattern
    and unit; datetime has_date and has_time. An input_text initial value is
    reported as presence and length only, never its text. Helpers defined in
    YAML are not in the storage collections and are not listed. Pages in
    helper_key order: pass next_cursor back as cursor, with the same
    domains, while has_more is true; a changed helper set refuses the cursor.
    Any unreadable source or unexpected field makes the result partial with
    reasons. Never creates, changes, reloads or deletes a helper.
    """

    metadata = {"provider": "engineering", "data_providers": list(DATA_PROVIDERS),
                "fallback": "none", "fallback_occurred": False, "completeness": "failed"}
    warnings: list[str] = []

    async def action():
        if not helper_settings_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        arguments = {"limit": limit, "cursor": cursor}
        if domains is not None:
            arguments["domains"] = domains
        request = contracts.validate_arguments(arguments)
        if SEARCH.session_factory is None:
            raise contracts.HelperSettingsError("authority_unavailable")
        reply = await service.report(
            request, session_factory=SEARCH.session_factory, known_secrets=SEARCH.known_secrets,
            codec=CURSORS, budget=_response_limit() - paging.ENVELOPE_RESERVE_CHARS,
        )
        metadata["completeness"] = reply["completeness"]
        if reply["partial_reasons"]:
            warnings.append(_PARTIAL)
        if reply["redaction"]["redacted_field_count"] or reply["redaction"]["truncated_field_count"]:
            warnings.append(_REDACTED)
        telemetry = current_telemetry()
        if telemetry:
            telemetry.completeness = reply["completeness"]
            telemetry.result_status = "partial" if reply["partial_reasons"] else "success"
        return reply

    return await run_structured(contracts.NAME, SUMMARY, action, warnings=warnings,
                                metadata=metadata, response_limit=_response_limit())


class HelperSettingsTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            contracts.validate_arguments(arguments)
        except contracts.HelperSettingsError:
            # The refused value is never reflected.
            async def invalid():
                raise contracts.HelperSettingsError("invalid_arguments")
            rendered = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=_response_limit())
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = HelperSettingsTool.from_function(get_helper_settings, annotations=_READ_ANNOTATIONS)
    tool.parameters["additionalProperties"] = False
    return tool
