"""Test-only synthetic backup admission for the held backup provider (HAMCP-437).

I2a-D ruling 1, group B. The shipped compiled registry holds only 9.2.0, where
backup is ``held`` (HAMCP-184/371; HAMCP-381 Phase 2 replaces the dispatch
shape). The version-agnostic provider properties (drift categories, unknown
version, local pre-dispatch failure, health split, at-most-once after intent,
dispatch-scoped admission) run on an explicitly SYNTHETIC version carrying the
OLD ``ha_manage_backup`` tool contract (input schema fingerprint ``7a2b78cb``).

The row, policy and capture are the frozen 8.2.0 fixture
(``tests/fixtures/ha_mcp_8_2_0_signed_reuse``, byte copies at ``0f1adcb5``);
only the version, entry id and dispositions are replaced, so the admission is
never derived from 9.2.0. Shipped (held) dispatch shape until HAMCP-381 Phase 2
replaces it. The synthetic registry is injected by patching the provider
module's ``load_reviewed_upstream_release_registry`` (the test_hamcp184
pattern) and never replaces the shipped registry.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from support import frozen_signed_reuse
from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
from ha_mcp_engineering.compatibility_family import PROVIDER_SURFACES
from ha_mcp_engineering.providers import operational_backup

VERSION = "0.0.0-test-backup"
ENTRY_ID = "ha-mcp-v0.0.0-test-backup"


def release():
    """The frozen 8.2.0 row as the synthetic version, backup admitted."""

    frozen = frozen_signed_reuse.registry().by_version[frozen_signed_reuse.VERSION]
    return replace(
        frozen,
        version=VERSION,
        entry_id=ENTRY_ID,
        provider_dispositions=tuple(
            (surface, "admitted") for surface in sorted(PROVIDER_SURFACES)
        ),
    )


def registry():
    """A one-row registry holding only the synthetic admission."""

    frozen = frozen_signed_reuse.registry()
    return replace(frozen, default_version=VERSION, releases=(release(),))


def catalog() -> McpReadCatalog:
    """The frozen 8.2.0 catalog capture, served as the synthetic version."""

    frozen = frozen_signed_reuse.registry().by_version[frozen_signed_reuse.VERSION]
    capture = frozen_signed_reuse.capture(frozen)
    return McpReadCatalog(
        protocol_version="2025-03-26",
        server_name="ha-mcp",
        server_version=VERSION,
        tools=tuple(capture["tools"]),
        connection_latency_ms=1.0,
    )


def patch_backup_registry(value=None):
    """Patch the backup provider's registry loader with the synthetic one."""

    return patch.object(
        operational_backup,
        "load_reviewed_upstream_release_registry",
        return_value=registry() if value is None else value,
    )
