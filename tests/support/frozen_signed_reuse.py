"""Frozen ha-mcp 8.2.0 release for the dormant signed-reuse tests (HAMCP-437).

Dormant signed-reuse mechanism coverage until HAMCP-156/424 redesign (I2a-D
ruling 2). The shipped compiled registry admits only 9.2.0, whose blueprint
adapters block policy reuse, so the positive reuse path in
``HaMcpAuthoritySelector`` is reachable only through this fixture. The files in
``tests/fixtures/ha_mcp_8_2_0_signed_reuse`` are byte copies taken at
``0f1adcb5``; ``provenance.json`` records their source paths and digests. The
fixture is injected through ``release_registry=`` and never replaces the shipped
registry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta")]

from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    ReviewedUpstreamReleaseRegistry,
    load_reviewed_upstream_release_registry,
)

FIXTURE = ROOT / "tests/fixtures/ha_mcp_8_2_0_signed_reuse"
REGISTRY = FIXTURE / "upstream_release_registry.json"
CAPTURE = FIXTURE / "ha-mcp-8.2.0.json"
VERSION = "8.2.0"


def registry() -> ReviewedUpstreamReleaseRegistry:
    """The frozen one-row 8.2.0 registry, loaded the RegistryFixture way."""

    return load_reviewed_upstream_release_registry(REGISTRY)


def capture(release) -> dict:
    """The frozen 8.2.0 catalog capture, checked against the row's digest."""

    raw = CAPTURE.read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != release.capture_sha256:
        raise AssertionError("frozen 8.2.0 capture digest mismatch")
    return json.loads(raw.decode("utf-8"))
