"""HAMCP-184 F-05: provider dispositions fail closed and are always explicit.

Before HAMCP-184 ``ReviewedUpstreamRelease.provider_disposition`` defaulted an
absent surface to ``admitted`` and the compiled registry let five legacy
entries (7.14.1-8.2.0) omit ``provider_dispositions``. Admission is now never
inferred from omission: the loader refuses a reviewed entry without explicit
dispositions for every provider surface, and the accessor returns ``held`` for
an absent surface or an unknown value. Each legacy entry now declares its
dispositions explicitly. Owner decision (independent review R1): 8.2.0 backup
and lifecycle are ``held`` because no capability-specific review record exists
for them; every other effective disposition is unchanged, and the production
release (8.5.0) and rollback release (8.4.3) are pinned exactly.

HAMCP-437 (I2a-D): the compiled registry holds only the 9.2.0 row. The
EFFECTIVE_* tables stay as the historical HAMCP-184 record; the compiled checks
run on 9.2.0, the admitted-backup control runs on the test-only synthetic
admission (I2a-D ruling 1, group B), and the per-row legacy, 8.2.0, 8.4.3 and
8.5.0 pins retire with those rows.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))

from ha_mcp_engineering.clients.upstream_read import McpReadCatalog  # noqa: E402
from ha_mcp_engineering.compatibility_family import (  # noqa: E402
    PROVIDER_DISPOSITIONS,
    PROVIDER_SURFACES,
)
from ha_mcp_engineering.providers import operational_lifecycle  # noqa: E402
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    ADDON_READ_TOOL,
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    RELEASE_REGISTRY_PATH,
    UpstreamToolPolicyError,
    load_reviewed_upstream_release_registry,
)
from support import synthetic_backup_admission  # noqa: E402
from tests.test_upstream_release_registry import RegistryFixture  # noqa: E402

# Legacy entry without capability-specific backup/lifecycle review evidence
# (owner decision, independent review R1).
LEGACY_OPERATIONAL_HELD = "8.2.0"
# HAMCP-437: the only compiled row.
SHIPPED = ("9.2.0",)
ALL_ADMITTED = {
    "read_gateway": "admitted",
    "dashboard": "admitted",
    "backup": "admitted",
    "lifecycle": "admitted",
}
OPERATIONAL_HELD = {
    "read_gateway": "admitted",
    "dashboard": "admitted",
    "backup": "held",
    "lifecycle": "held",
}
# Effective dispositions captured from base b249738 (pre-HAMCP-184) for every
# compiled entry.
EFFECTIVE_BEFORE = {
    "7.14.1": ALL_ADMITTED,
    "7.14.2": ALL_ADMITTED,
    "8.0.0": ALL_ADMITTED,
    "8.1.0": ALL_ADMITTED,
    "8.1.1": ALL_ADMITTED,
    "8.2.0": ALL_ADMITTED,
    "8.4.1": OPERATIONAL_HELD,
    "8.4.3": OPERATIONAL_HELD,
    "8.5.0": OPERATIONAL_HELD,
    "8.6.0": OPERATIONAL_HELD,
}
# Entries reviewed after HAMCP-184, each pinned by its own task. HAMCP-319
# admits 9.1.0 at 8.5.0 parity: backup and lifecycle stay held, exactly as on
# 8.5.0 and 8.6.0. HAMCP-351 admits 9.1.2 at 9.1.0 parity. HAMCP-371: the S8
# 9.2.0 registry row's provider_dispositions are read_gateway and dashboard
# admitted, backup and lifecycle held. HAMCP-401 Phase 1 admits 9.2.0
# lifecycle; 9.2.0 backup stays held until HAMCP-381.
REVIEWED_AFTER_HAMCP184 = {
    "9.1.0": OPERATIONAL_HELD,
    "9.1.2": OPERATIONAL_HELD,
    "9.2.0": {**OPERATIONAL_HELD, "lifecycle": "admitted"},
}
# HAMCP-184 effective dispositions: identical to EFFECTIVE_BEFORE except that
# 8.2.0 backup and lifecycle become held, plus the later reviewed entries.
EFFECTIVE_AFTER = {
    **EFFECTIVE_BEFORE,
    LEGACY_OPERATIONAL_HELD: OPERATIONAL_HELD,
    **REVIEWED_AFTER_HAMCP184,
}


def effective(release) -> dict[str, str]:
    return {surface: release.provider_disposition(surface) for surface in PROVIDER_SURFACES}


class CompiledRegistryDispositionTests(unittest.TestCase):
    def test_every_compiled_entry_declares_every_surface(self):
        raw = json.loads(RELEASE_REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            [entry["version"] for entry in raw["releases"]], list(SHIPPED)
        )
        for entry in raw["releases"]:
            with self.subTest(version=entry["version"]):
                dispositions = entry["provider_dispositions"]
                self.assertEqual(set(dispositions), PROVIDER_SURFACES)
                self.assertLessEqual(set(dispositions.values()), PROVIDER_DISPOSITIONS)

    def test_effective_dispositions_match_reviewed_table(self):
        registry = load_reviewed_upstream_release_registry()
        for release in registry.releases:
            with self.subTest(version=release.version):
                self.assertEqual(dict(release.provider_dispositions), EFFECTIVE_AFTER[release.version])
                self.assertEqual(effective(release), EFFECTIVE_AFTER[release.version])

    def test_only_8_2_0_backup_and_lifecycle_change_from_pre_hamcp184(self):
        changed = {
            (version, surface): (EFFECTIVE_BEFORE[version][surface], EFFECTIVE_AFTER[version][surface])
            for version in EFFECTIVE_BEFORE
            for surface in PROVIDER_SURFACES
            if EFFECTIVE_BEFORE[version][surface] != EFFECTIVE_AFTER[version][surface]
        }
        self.assertEqual(
            changed,
            {
                ("8.2.0", "backup"): ("admitted", "held"),
                ("8.2.0", "lifecycle"): ("admitted", "held"),
            },
        )

    def test_production_9_2_0_exact_and_backup_held(self):
        # HAMCP-184/371: 9.2.0 backup stays held (no reviewed backup dispatch
        # shape for the 9.1+ tool contract until HAMCP-381 Phase 2);
        # HAMCP-401 Phase 1 admits 9.2.0 lifecycle.
        release = load_reviewed_upstream_release_registry().by_version["9.2.0"]
        self.assertFalse(release.revoked)
        self.assertEqual(
            effective(release),
            {"read_gateway": "admitted", "dashboard": "admitted", "backup": "held", "lifecycle": "admitted"},
        )
        self.assertEqual(release.provider_disposition("backup"), "held")


class AccessorFailClosedTests(unittest.TestCase):
    def setUp(self):
        # HAMCP-437: the admitted 9.2.0 row with every surface set admitted, so
        # each absent or forged surface is observed turning from admitted to held.
        release = load_reviewed_upstream_release_registry().by_version["9.2.0"]
        self.release = replace(
            release,
            provider_dispositions=tuple((surface, "admitted") for surface in sorted(PROVIDER_SURFACES)),
        )
        for surface in PROVIDER_SURFACES:
            self.assertEqual(self.release.provider_disposition(surface), "admitted")

    def test_absent_dispositions_are_held_for_every_surface(self):
        stripped = replace(self.release, provider_dispositions=())
        for surface in sorted(PROVIDER_SURFACES):
            with self.subTest(surface=surface):
                self.assertEqual(stripped.provider_disposition(surface), "held")

    def test_one_absent_surface_is_held_others_unchanged(self):
        for missing in sorted(PROVIDER_SURFACES):
            partial = replace(
                self.release,
                provider_dispositions=tuple(
                    item for item in self.release.provider_dispositions if item[0] != missing
                ),
            )
            with self.subTest(missing=missing):
                self.assertEqual(partial.provider_disposition(missing), "held")
                for surface in PROVIDER_SURFACES - {missing}:
                    self.assertEqual(partial.provider_disposition(surface), "admitted")

    def test_unknown_surface_and_unknown_value_are_held(self):
        self.assertEqual(self.release.provider_disposition("unreviewed_surface"), "held")
        for value in ("Admitted", "admitted ", "", None, "allow", "true"):
            forged = replace(
                self.release,
                provider_dispositions=tuple(
                    (surface, value) for surface in sorted(PROVIDER_SURFACES)
                ),
            )
            with self.subTest(value=value):
                for surface in PROVIDER_SURFACES:
                    self.assertEqual(forged.provider_disposition(surface), "held")


class LoaderRefusalTests(unittest.TestCase):
    def _refuses(self, mutate, code):
        fixture = RegistryFixture()
        self.addCleanup(fixture.close)
        value = fixture.value()
        mutate(value)
        fixture.write(value)
        with self.assertRaisesRegex(UpstreamToolPolicyError, code):
            load_reviewed_upstream_release_registry(fixture.path)

    def test_fixture_baseline_loads(self):
        fixture = RegistryFixture()
        self.addCleanup(fixture.close)
        fixture.write(fixture.value())
        load_reviewed_upstream_release_registry(fixture.path)

    def test_absent_dispositions_refused_for_every_entry(self):
        fixture = RegistryFixture()
        self.addCleanup(fixture.close)
        versions = [entry["version"] for entry in fixture.value()["releases"]]
        self.assertIn("9.2.0", versions)
        for index, version in enumerate(versions):
            with self.subTest(version=version):
                self._refuses(
                    lambda value, index=index: value["releases"][index].pop("provider_dispositions"),
                    "release_registry_entry_fields_invalid",
                )

    def test_null_missing_surface_extra_surface_and_unknown_value_refused(self):
        mutations = {
            "null": lambda entry: entry.update(provider_dispositions=None),
            "missing_surface": lambda entry: entry["provider_dispositions"].pop("backup"),
            "extra_surface": lambda entry: entry["provider_dispositions"].update(power="admitted"),
            "unknown_value": lambda entry: entry["provider_dispositions"].update(lifecycle="allowed"),
            "not_object": lambda entry: entry.update(provider_dispositions=["admitted"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                self._refuses(
                    lambda value, mutate=mutate: mutate(value["releases"][0]),
                    "release_registry_(provider_dispositions_invalid|family_admission_incomplete)",
                )


class ProviderGateRefusalTests(unittest.TestCase):
    """Negative reachability: a provider refuses a release without dispositions."""

    def test_backup_provider_refuses_absent_disposition_before_dispatch(self):
        # HAMCP-437: the shipped 9.2.0 backup is held, so the admitted row is
        # the test-only synthetic admission (I2a-D ruling 1, group B).
        registry = synthetic_backup_admission.registry()
        stripped = replace(
            registry,
            releases=tuple(
                replace(item, provider_dispositions=()) for item in registry.releases
            ),
        )
        catalog = _catalog(synthetic_backup_admission.VERSION)
        provider = ReviewedOperationalBackupProvider()
        with synthetic_backup_admission.patch_backup_registry(stripped):
            with self.assertRaises(OperationalBackupProviderError) as raised:
                provider._validate_catalog(catalog)
        self.assertEqual(raised.exception.category, "provider_held")
        self.assertFalse(raised.exception.dispatched)
        # The unmodified explicit entry passes the disposition gate and fails
        # later only on the (empty) synthetic tool catalog.
        with synthetic_backup_admission.patch_backup_registry():
            with self.assertRaises(OperationalBackupProviderError) as later:
                ReviewedOperationalBackupProvider()._validate_catalog(catalog)
        self.assertNotEqual(later.exception.category, "upstream_version_mismatch")


class LifecycleGateRefusalTests(unittest.TestCase):
    """HAMCP-437: the lifecycle gate on the admitted 9.2.0 row.

    9.2.0 lifecycle is admitted (HAMCP-401 Phase 1). Stripping its
    dispositions makes the provider refuse before dispatch; the unmodified
    row passes the gate and fails later only on the empty synthetic catalog.
    """

    def test_lifecycle_provider_refuses_absent_disposition_before_dispatch(self):
        registry = load_reviewed_upstream_release_registry()
        self.assertEqual(registry.by_version["9.2.0"].provider_disposition("lifecycle"), "admitted")
        stripped = replace(
            registry,
            releases=tuple(
                replace(item, provider_dispositions=()) if item.version == "9.2.0" else item
                for item in registry.releases
            ),
        )
        for operation in ("controlled_reload", "restart_addon", "restart_home_assistant"):
            required = ReviewedOperationalLifecycleProvider._required_tools(operation)
            with self.subTest(operation=operation):
                self.assertTrue(required)
                with patch.object(
                    operational_lifecycle, "load_reviewed_upstream_release_registry", return_value=stripped
                ):
                    with self.assertRaises(OperationalLifecycleProviderError) as raised:
                        ReviewedOperationalLifecycleProvider()._validate_catalog(
                            _catalog("9.2.0"), required, operation=operation
                        )
                self.assertEqual(raised.exception.category, "provider_held")
                self.assertFalse(raised.exception.dispatched)
                with self.assertRaises(OperationalLifecycleProviderError) as later:
                    ReviewedOperationalLifecycleProvider()._validate_catalog(
                        _catalog("9.2.0"), required, operation=operation
                    )
                self.assertNotIn(later.exception.category, {"provider_held", "upstream_version_mismatch"})
                self.assertFalse(later.exception.dispatched)


def _catalog(version: str) -> McpReadCatalog:
    return McpReadCatalog(
        protocol_version="2025-03-26",
        server_name="ha-mcp",
        server_version=version,
        tools=(),
        connection_latency_ms=1.0,
    )


class HeldOperationalRefusalTests(unittest.TestCase):
    """A held backup/lifecycle disposition refuses before dispatch.

    HAMCP-400: a held disposition of a reviewed release is ``provider_held``;
    ``upstream_version_mismatch`` is kept for an unknown version.

    HAMCP-437: the shipped 9.2.0 backup is held. The admitted-backup control is
    the test-only synthetic admission (I2a-D ruling 1, group B): its disposition
    gate passes and validation fails later only on the empty synthetic tool
    catalog. No shipped row holds lifecycle, so the lifecycle case holds it on
    a copy of the 9.2.0 row (a hold, never an admission).
    """

    def test_backup_provider_refuses_held_9_2_0_before_dispatch(self):
        with self.assertRaises(OperationalBackupProviderError) as raised:
            ReviewedOperationalBackupProvider()._validate_catalog(_catalog("9.2.0"))
        self.assertEqual(raised.exception.category, "provider_held")
        self.assertFalse(raised.exception.dispatched)
        with synthetic_backup_admission.patch_backup_registry():
            with self.assertRaises(OperationalBackupProviderError) as control:
                ReviewedOperationalBackupProvider()._validate_catalog(
                    _catalog(synthetic_backup_admission.VERSION)
                )
        self.assertNotIn(control.exception.category, {"provider_held", "upstream_version_mismatch"})
        self.assertFalse(control.exception.dispatched)

    def test_lifecycle_provider_refuses_held_lifecycle_before_dispatch(self):
        registry = load_reviewed_upstream_release_registry()
        held = replace(
            registry,
            releases=tuple(
                replace(
                    item,
                    provider_dispositions=tuple(
                        (surface, "held" if surface == "lifecycle" else value)
                        for surface, value in item.provider_dispositions
                    ),
                )
                for item in registry.releases
            ),
        )
        self.assertEqual(held.by_version["9.2.0"].provider_disposition("lifecycle"), "held")
        for operation in ("controlled_reload", "restart_addon", "restart_home_assistant"):
            required = ReviewedOperationalLifecycleProvider._required_tools(operation)
            with self.subTest(operation=operation):
                self.assertTrue(required)
                with patch.object(
                    operational_lifecycle, "load_reviewed_upstream_release_registry", return_value=held
                ):
                    with self.assertRaises(OperationalLifecycleProviderError) as raised:
                        ReviewedOperationalLifecycleProvider()._validate_catalog(
                            _catalog("9.2.0"), required, operation=operation
                        )
                self.assertEqual(raised.exception.category, "provider_held")
                self.assertFalse(raised.exception.dispatched)
        with self.assertRaises(OperationalLifecycleProviderError) as control:
            ReviewedOperationalLifecycleProvider()._validate_catalog(
                _catalog("9.2.0"), (ADDON_READ_TOOL,), operation="restart_addon"
            )
        self.assertNotEqual(control.exception.category, "upstream_version_mismatch")
        self.assertFalse(control.exception.dispatched)


if __name__ == "__main__":
    unittest.main()
