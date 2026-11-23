"""Frozen ha-mcp 8.6.0 and 9.1.2 releases for foreign-release refusal tests (HAMCP-437).

The shipped compiled registry admits only 9.2.0. Tests that prove 9.2.0 does
not trust a real retired release (crossed read-adapter tokens, the ha_search
descriptor pair, a 9.2.0 policy loaded under 9.1.2 identity) need those
releases as they were reviewed. The files in
``tests/fixtures/ha_mcp_8_6_0_9_1_2_foreign`` are byte copies taken at
``0f1adcb5`` (ANSWER 20261122-0005-01: a frozen copy of a committed input is
not an invented fixture); ``provenance.json`` records their source paths and
digests. The fixture is loaded by path and never replaces the shipped registry.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta")]

from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    ReviewedUpstreamReleaseRegistry,
    load_reviewed_upstream_release_registry,
)

FIXTURE = ROOT / "tests/fixtures/ha_mcp_8_6_0_9_1_2_foreign"
REGISTRY = FIXTURE / "upstream_release_registry.json"
POLICY_860 = FIXTURE / "upstream_tool_policy_8_6_0.json"
POLICY_912 = FIXTURE / "upstream_tool_policy_9_1_2.json"
SOURCE_912 = "bab615dfc90cc510f5e19a9118707d0fae03a37c"


def registry() -> ReviewedUpstreamReleaseRegistry:
    """The frozen two-row 8.6.0 and 9.1.2 registry."""

    return load_reviewed_upstream_release_registry(REGISTRY)
