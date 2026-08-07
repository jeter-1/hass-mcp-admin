"""Engineering-native evidence-only canaries for reviewed held reads."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from ..providers.upstream_read_gateway import UPSTREAM_READ_GATEWAY


async def run_held_read_canary(
    upstream_tool_name: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
    ],
    expected_compatibility_entry_id: Annotated[
        str,
        Field(min_length=1, max_length=160),
    ],
    arguments: dict[str, Any] | None = None,
) -> str:
    """Run one held pure-read against the active exact upstream release.

    The named upstream tool must remain classified ``held_for_canary`` in the
    active reviewed compatibility entry. The expected entry ID prevents a
    caller from unknowingly testing a different release. This operation emits
    evidence only: it never registers, admits, promotes, or changes the held
    tool, and it has no fallback.
    """

    return await UPSTREAM_READ_GATEWAY.run_held_read_canary(
        upstream_tool_name=upstream_tool_name,
        expected_compatibility_entry_id=expected_compatibility_entry_id,
        arguments=arguments,
    )


CANARY_TOOLS = (run_held_read_canary,)
