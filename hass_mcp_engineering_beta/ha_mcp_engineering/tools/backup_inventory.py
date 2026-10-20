"""Public read-only backup inventory with a non-reflecting input boundary."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..backup_inventory.contracts import DEFAULT_LIMIT, MAX_LIMIT, validate_arguments
from ..backup_inventory.service import BACKUP_INVENTORY
from ..tool_framework import run_structured
from .compatibility import SETTINGS


async def get_backup_inventory(
    limit: Annotated[int, Field(strict=True, ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> str:
    """Report Home Assistant backups and whether they look healthy. Read-only.

    Reads Core's backup integration (backup/config/info, backup/info) over the
    admin WebSocket: every backup in every readable storage location (local,
    network storage, Home Assistant Cloud, other backup agents), each with
    date, size, full/partial content scope, protection flag, Home Assistant
    version, included add-on names, folders and database flag, and which
    locations hold a copy; plus a summary, the automatic-backup schedule,
    retention and last attempted/completed times, recent failures and health
    signals stated as facts with thresholds. Lists the newest ``limit``
    backups. Any unreadable source, location or field makes the result
    partial with reasons. Never creates, restores, deletes, downloads or
    changes settings, and never returns the encryption key.
    """
    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"],
                "upstream_calls": 0, "fallback_occurred": False}

    async def action():
        result = await BACKUP_INVENTORY.require().inventory(limit)
        metadata["completeness"] = result["completeness"]
        return result

    return await run_structured("get_backup_inventory", "Returned the Home Assistant backup inventory.",
                                action, metadata=metadata,
                                response_limit=min(60_000, SETTINGS.response_size_limit))


class BackupInventoryTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except ValueError:
            async def invalid():
                raise ValueError("Invalid backup inventory arguments.")
            rendered = await run_structured("get_backup_inventory", "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = BackupInventoryTool.from_function(get_backup_inventory,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    tool.parameters["additionalProperties"] = False
    return tool
