"""Public read-only recorder settings with a non-reflecting input boundary."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..recorder_settings.contracts import MAX_ENTITY_IDS, validate_arguments
from ..recorder_settings.service import RECORDER_SETTINGS
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def get_recorder_settings(
    entity_ids: Annotated[list[str], Field(max_length=MAX_ENTITY_IDS)] = [],  # noqa: B006
) -> str:
    """Report the effective recorder settings and whether entities are recorded. Read-only.

    Reports each recorder setting as configured in configuration.yaml (via
    the reviewed YAML reader), as Core's pinned default, or as unknown with a
    reason (an !include, packages present or unreadable, or an unreadable
    file). Reports the include/exclude filter, which Core filter case applies
    and what it ignores; the recorder's runtime status (recorder/info); and,
    for up to 50 ``entity_ids``, Core's own answer (recorder/entity_options/
    get) beside the filter computed from the file, flagging filter_drift when
    they disagree. The database URL and any credential are never returned.
    Any unreadable source makes the result partial with reasons. Never
    changes settings, purges, disables or enables recording.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api", "upstream_read_gateway"],
                "fallback_occurred": False}

    async def action():
        result = await RECORDER_SETTINGS.require().report(tuple(entity_ids))
        metadata["completeness"] = result["completeness"]
        metadata["upstream_calls"] = result.pop("upstream_calls")
        return result

    return await run_structured("get_recorder_settings", "Returned the effective recorder settings.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class RecorderSettingsTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid recorder settings arguments.")
            rendered = await run_structured("get_recorder_settings", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = RecorderSettingsTool.from_function(get_recorder_settings,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
