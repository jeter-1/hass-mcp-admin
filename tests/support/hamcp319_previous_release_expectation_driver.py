"""Derive the HAMCP-319 rollback-image expectation from an extracted source tree.

Run with ``python -I <this file> <hass_mcp_engineering_beta directory>`` on a
``git archive`` of a published release's source (2.4.2-beta.6 at af5ec837,
2.4.2-beta.7 at b614be43). Prints one JSON object computed only from that
tree's own code: its self-reported version, its static tool registration, its
reviewed 8.5.0 read routes, and the delegated reads it retires, if any.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    sys.path.insert(0, sys.argv[1])
    from ha_mcp_engineering.version import SERVER_VERSION
    from ha_mcp_engineering.tools import (
        ENGINEERING_STATIC_TOOL_COUNT,
        get_registered_server,
        registered_tools,
    )
    from ha_mcp_engineering.ha_core_readmission import routes
    from ha_mcp_engineering.upstream_tool_policy import (
        load_reviewed_upstream_release_registry,
    )

    policy = load_reviewed_upstream_release_registry().by_version["8.5.0"].policy
    print(
        json.dumps(
            {
                "version": SERVER_VERSION,
                "static_count_constant": ENGINEERING_STATIC_TOOL_COUNT,
                "static_names": sorted(
                    tool.name for tool in registered_tools(get_registered_server()).values()
                ),
                "delegated_reads_by_upstream_version": {
                    "8.5.0": sorted(
                        entry.exposed_name for entry in policy.tools if entry.is_read_route
                    ),
                },
                "retired_delegated_reads": sorted(
                    getattr(routes, "RETIRED_DELEGATED_READS", frozenset())
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
