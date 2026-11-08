"""Public installed-only Marketplace inventory read (HAMCP-290).

The input boundary never reflects a refused value. The read is dormant until
the owner-signed registry admits ``core.marketplace_read``.
"""

from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..marketplace_inventory import MARKETPLACE_INVENTORY, MarketplaceError, validate_arguments
from ..tool_framework import run_structured
from .compatibility import SETTINGS

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_PARTIAL = ("Inventory text is partial: Engineering withheld credential-like or unverifiable text "
            "or shortened long strings; see redaction.")


async def ha_get_hacs_info(
    query: Annotated[str, Field(max_length=200)] = "",
    category: Literal["integration", "plugin", "template", "theme"] | None = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
    offset: Annotated[int, Field(ge=0, le=20000)] = 0,
) -> str:
    """List and search INSTALLED Marketplace (HACS) repositories. Read-only.

    Returns a page of installed repositories (id, full_name, name, category,
    domain, installed and available version, pending_upgrade, custom,
    description), sorted by full_name, with ``total_matches``, ``has_more``
    and ``next_offset`` for paging and an ``inventory_fingerprint`` that
    changes when the installed inventory changes. ``query`` matches
    full_name, name, domain or description case-insensitively; ``category``
    narrows to one repository category. ``backend`` reports which store
    answered: ``core_marketplace`` or, only when Home Assistant does not know
    the Marketplace command at all, ``hacs``. Never installs, updates,
    removes, downloads or reconfigures anything, and never lists
    uninstalled catalog repositories.
    """

    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback_occurred": False}
    warnings: list[str] = []

    async def action():
        try:
            request = validate_arguments({"query": query, "category": category, "limit": limit, "offset": offset})
            value = await MARKETPLACE_INVENTORY.require().inventory(request)
        except MarketplaceError as exc:
            metadata["fallback_occurred"] = exc.fallback_occurred
            raise
        metadata["fallback_occurred"] = value["fallback_occurred"]
        metadata["backend"] = value["backend"]
        metadata["completeness"] = value["completeness"]
        if value["completeness"] != "complete":
            warnings.append(_PARTIAL)
        return value

    return await run_structured(
        "ha_get_hacs_info", "Returned a sanitized page of installed Marketplace repositories.",
        action, warnings=warnings, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )


class MarketplaceInventoryTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except MarketplaceError:
            # The refused value is never reflected.
            async def invalid():
                raise MarketplaceError("invalid_arguments")
            rendered = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = MarketplaceInventoryTool.from_function(ha_get_hacs_info, annotations=_READ_ANNOTATIONS)
    tool.parameters["additionalProperties"] = False
    return tool
