"""Public read-only configuration definitions with a non-reflecting input boundary."""

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..configuration_definitions.contracts import INTEGRATIONS, MAX_ENTITY_IDS, validate_arguments
from ..configuration_definitions.service import CONFIGURATION_DEFINITIONS
from ..tool_framework import run_structured
from .compatibility import SETTINGS

Integration = Literal[INTEGRATIONS]


async def get_configuration_definitions(
    entity_ids: Annotated[list[str], Field(max_length=MAX_ENTITY_IDS)] = [],  # noqa: B006
    integrations: Annotated[list[Integration], Field(max_length=len(INTEGRATIONS))] = [],  # noqa: B006
) -> str:
    """Report how YAML-defined template, SQL and helper entities are defined, and why one is unavailable. Read-only.

    For up to 50 ``entity_ids`` and/or whole ``integrations`` (template, sql,
    filter, derivative, statistics, group, integration, min_max,
    history_stats, threshold, trend, utility_meter), returns each matching
    configuration.yaml definition (via the reviewed YAML reader) as an
    allowlisted projection, how it was matched to the entity (exact unique_id,
    a heuristic with confidence, ambiguous, or not found), and, per entity, a
    diagnosis of why it is unavailable (disabled, config entry or platform not
    loaded, dependency unavailable, transient, undetermined). UI-created
    entries are listed as not readable without a flow. The SQL db_url and any
    credential or !secret value are never returned; !include and packages are
    disclosed, never followed. Any unreadable source makes the result partial
    with reasons. Never changes, reloads or creates anything.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api", "upstream_read_gateway"],
                "fallback_occurred": False}

    async def action():
        result = await CONFIGURATION_DEFINITIONS.require().report(tuple(entity_ids), tuple(integrations))
        metadata["completeness"] = result["completeness"]
        metadata["upstream_calls"] = result.pop("upstream_calls")
        return result

    return await run_structured("get_configuration_definitions", "Returned the configuration definitions.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class ConfigurationDefinitionsTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid configuration definition arguments.")
            rendered = await run_structured("get_configuration_definitions", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = ConfigurationDefinitionsTool.from_function(get_configuration_definitions,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
