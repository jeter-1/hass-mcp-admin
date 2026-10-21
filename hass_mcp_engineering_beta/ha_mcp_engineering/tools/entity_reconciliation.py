"""Public read-only unregistered-entity report with a non-reflecting input boundary."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..entity_reconciliation.contracts import DEFAULT_LIMIT, MAX_DOMAINS, MAX_LIMIT, validate_arguments
from ..entity_reconciliation.service import ENTITY_RECONCILIATION
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def get_unregistered_entities(
    domains: Annotated[list[str] | None, Field(min_length=1, max_length=MAX_DOMAINS)] = None,
    limit: Annotated[int, Field(strict=True, ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> str:
    """List entities that have a state but no entity registry entry. Read-only.

    Compares current states with the entity registry, optionally only for
    ``domains``, and lists up to ``limit`` entities without a registry entry
    (typically YAML-defined without a unique_id). For each it reports the
    integration platform that created it (entity/source), or that the state
    was written directly when no platform did, and the config entry when
    search/related resolves exactly one. The defining file is never asserted:
    Core does not record it. Any unreadable source makes the result partial
    with reasons. Never registers, renames or removes an entity.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "upstream_calls": 0, "fallback_occurred": False}

    async def action():
        result = await ENTITY_RECONCILIATION.require().report(domains, limit)
        metadata["completeness"] = result["completeness"]
        return result

    return await run_structured("get_unregistered_entities", "Returned entities without a registry entry.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class EntityReconciliationTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid unregistered entity arguments.")
            rendered = await run_structured("get_unregistered_entities", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = EntityReconciliationTool.from_function(get_unregistered_entities,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
