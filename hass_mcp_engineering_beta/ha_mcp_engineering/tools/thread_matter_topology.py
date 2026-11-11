"""Public Thread dataset and Matter topology read (HAMCP-231).

The input boundary never reflects a refused value and accepts no
``refresh``: the Matter topology command is always sent with the literal
``refresh: false``. The read is dormant until the owner-signed registry
admits ``core.thread_matter_topology_read``.
"""

from typing import Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from ..thread_matter_topology import THREAD_MATTER_TOPOLOGY, TopologyError, validate_arguments
from ..tool_framework import run_structured
from .compatibility import SETTINGS

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_PARTIAL = ("Topology text is partial: Engineering withheld identifier-bearing or credential-like "
            "text or shortened long strings; see redaction and withheld_identifier_field_count.")
_TRUNCATED = "A section was truncated at its output cap; see each section's total and truncated."
_UNAVAILABLE = "A requested section is unavailable; its status names the reason. Nothing fell back."


async def get_thread_matter_topology(
    sections: list[Literal["datasets", "topology"]] | None = None,
) -> str:
    """Read Thread datasets and the Matter network topology. Read-only.

    ``datasets`` lists the stored Thread datasets (dataset_id, network name,
    extended PAN ID, PAN ID, channel, preferred, source, created) with
    credential material never read. ``topology`` returns the Matter Server's
    cached network graph (nodes with kind, network type, role, availability,
    RLOC16, extended PAN ID, Matter node_id and ha_device_id; connections
    with link strength, LQI, RSSI and path cost) and never asks for a fresh
    scan. LAN identifiers (MAC/extended addresses, BSSIDs, SSIDs, host
    names, upstream node ids) are replaced with per-response references
    (g1, x1, b1) that are not stable across calls. Each section reports its
    own status; a missing integration or old Matter Server is a typed
    status, not a failure of the other section. Never commissions, removes,
    pings, interviews, changes datasets or credentials, or subscribes.
    """

    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback_occurred": False}
    warnings: list[str] = []

    async def action():
        request = validate_arguments({} if sections is None else {"sections": sections})
        value = await THREAD_MATTER_TOPOLOGY.require().topology(request)
        metadata["completeness"] = value["completeness"]
        if "section_unavailable" in value["partial_reasons"]:
            warnings.append(_UNAVAILABLE)
        if "truncated" in value["partial_reasons"]:
            warnings.append(_TRUNCATED)
        if "sanitized_text" in value["partial_reasons"]:
            warnings.append(_PARTIAL)
        return value

    return await run_structured(
        "get_thread_matter_topology", "Returned Thread datasets and Matter topology with opaque identifiers.",
        action, warnings=warnings, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )


class ThreadMatterTopologyTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except TopologyError:
            # The refused value is never reflected.
            async def invalid():
                raise TopologyError("invalid_arguments")
            rendered = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = ThreadMatterTopologyTool.from_function(get_thread_matter_topology, annotations=_READ_ANNOTATIONS)
    tool.parameters["additionalProperties"] = False
    return tool
