"""Public security and effective-permission metadata read (HAMCP-134).

The input boundary never reflects a refused value. The read is dormant until
the owner-signed registry admits ``core.security_metadata_read``; registration
is pending wiring (``application.py`` and ``capabilities.py`` are outside this
change).
"""

from typing import Literal

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from ..security_metadata import SECURITY_METADATA, SecurityMetadataError, validate_arguments
from ..tool_framework import run_structured
from .compatibility import SETTINGS

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_PARTIAL = ("Security metadata is partial: a requested section was not fully observed, Engineering "
            "withheld credential-like text, or whole list items were removed to fit the output bound; "
            "see coverage, redaction and truncated_field_count.")

Section = Literal["users", "mfa", "http_boundary", "urls", "assist", "panels"]


async def get_security_metadata(sections: list[Section] | None = None) -> str:
    """Report Home Assistant security and effective-permission posture. Read-only.

    Sections (default all): ``users`` (pseudonymous per-user owner, admin,
    active, local_only, system_generated, built-in group membership and
    credential provider types; no names or ids), ``mfa`` (MFA module catalog;
    enrolment only for the Supervisor service user), ``http_boundary``
    (active and configured HTTP security settings as booleans and counts),
    ``urls`` (external and internal URL presence, scheme and Nabu Casa domain
    class; no URL text), ``assist`` (expose-new defaults, pipeline engines and
    conversation agents) and ``panels`` (sidebar panels and their listing
    admin flag). ``connection_identity`` says whose view this is. A field Home
    Assistant does not return is ``not_provided``; ``omissions`` lists areas
    Home Assistant exposes no read for. Never changes users, tokens, MFA, HTTP
    settings, exposure, pipelines or panels, and never calls the Supervisor.
    """

    metadata = {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback_occurred": False}
    warnings: list[str] = []

    async def action():
        arguments = {} if sections is None else {"sections": list(sections)}
        request = validate_arguments(arguments)
        value = await SECURITY_METADATA.require().metadata(request)
        metadata["completeness"] = value["completeness"]
        if value["completeness"] == "partial":
            warnings.append(_PARTIAL)
        return value

    return await run_structured(
        "get_security_metadata", "Returned sanitized security and effective-permission metadata.",
        action, warnings=warnings, metadata=metadata, response_limit=SETTINGS.response_size_limit,
    )


class SecurityMetadataTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            validate_arguments(arguments)
        except SecurityMetadataError:
            # The refused value is never reflected.
            async def invalid():
                raise SecurityMetadataError("invalid_arguments")
            rendered = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def registered_tool():
    tool = SecurityMetadataTool.from_function(get_security_metadata, annotations=_READ_ANNOTATIONS)
    tool.parameters["additionalProperties"] = False
    return tool
