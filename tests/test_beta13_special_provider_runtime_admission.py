from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
    load_reviewed_upstream_release_registry,
    validate_reviewed_release_catalog,
)
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadCatalog,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reviewed_tools(version: str) -> list[dict]:
    return deepcopy(load_json(EVIDENCE / f"ha-mcp-{version}.json")["tools"])


def addon_8_tools() -> list[dict]:
    tools = reviewed_tools("8.0.0")
    reconstruction = load_json(
        EVIDENCE / "ha-mcp-8.0.0-live-addon-reconstruction.json"
    )
    policy = reconstruction["transform"]["replacement"]
    for tool in tools:
        tool["_meta"]["ha_mcp"]["policy"] = deepcopy(policy)
    return tools


def validate(version: str, tools: list[dict], **overrides):
    release = load_reviewed_upstream_release_registry().by_version[version]
    return validate_reviewed_release_catalog(
        overrides.pop("release", release),
        observed_server_name=overrides.pop("server_name", "ha-mcp"),
        observed_upstream_version=overrides.pop("upstream_version", version),
        observed_protocol_version=overrides.pop(
            "protocol_version", "2025-03-26"
        ),
        tools=tools,
    )


class CatalogTransport:
    def __init__(self, version: str, tools: list[dict]) -> None:
        self.catalog = McpReadCatalog(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version=version,
            tools=tuple(tools),
            connection_latency_ms=1.0,
        )
        self.dispatch_count = 0

    async def discover(self) -> McpReadCatalog:
        return self.catalog


class ReviewedCatalogValidatorTests(unittest.TestCase):
    def test_exact_reviewed_standalone_catalogs_are_fully_accounted(self):
        for version in ("7.14.2", "8.0.0"):
            with self.subTest(version=version):
                result = validate(version, reviewed_tools(version))
                self.assertTrue(result.valid)
                self.assertEqual(result.validation_status, "accepted_exact")
                self.assertEqual(result.expected_tool_count, 78)
                self.assertEqual(result.observed_tool_count, 78)
                self.assertEqual(result.reviewed_accounted_count, 78)
                self.assertEqual(result.missing_tool_count, 0)
                self.assertEqual(result.additional_tool_count, 0)
                self.assertEqual(result.duplicated_tool_count, 0)
                self.assertEqual(result.unreviewed_tool_count, 0)
                self.assertEqual(result.invalid_descriptor_count, 0)
                self.assertEqual(
                    result.expected_normalized_catalog_fingerprint,
                    result.normalized_catalog_fingerprint,
                )
                self.assertEqual(
                    result.aggregate_fingerprint_model,
                    REVIEWED_NORMALIZED_CATALOG_FINGERPRINT_MODEL_V1,
                )
                self.assertEqual(
                    set(dict(result.component_mismatch_counts).values()),
                    {0},
                )

    def test_exact_addon_catalog_uses_same_normalized_identity(self):
        standalone = validate("8.0.0", reviewed_tools("8.0.0"))
        addon = validate("8.0.0", addon_8_tools())

        self.assertTrue(addon.valid)
        self.assertEqual(addon.reviewed_accounted_count, 78)
        self.assertNotEqual(
            standalone.observed_raw_catalog_fingerprint,
            addon.observed_raw_catalog_fingerprint,
        )
        self.assertEqual(
            addon.observed_raw_catalog_fingerprint,
            "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768",
        )
        self.assertEqual(
            standalone.normalized_catalog_fingerprint,
            addon.normalized_catalog_fingerprint,
        )

    def test_identity_tool_set_and_contract_drift_fail_closed(self):
        tools = addon_8_tools()
        cases = (
            (
                "unknown_patch",
                tools,
                {"upstream_version": "8.0.1"},
                "upstream_version_mismatch",
            ),
            (
                "wrong_protocol",
                tools,
                {"protocol_version": "2025-11-25"},
                "unsupported_protocol_version",
            ),
            (
                "missing_tool",
                tools[:-1],
                {},
                "rejected_catalog_mismatch",
            ),
            (
                "duplicate_tool",
                [*tools, deepcopy(tools[0])],
                {},
                "rejected_catalog_mismatch",
            ),
        )
        changed = deepcopy(tools)
        changed[0]["description"] += " drift"
        cases += (
            (
                "description_drift",
                changed,
                {},
                "rejected_catalog_mismatch",
            ),
        )
        for name, candidate, overrides, status in cases:
            with self.subTest(name=name):
                result = validate("8.0.0", candidate, **overrides)
                self.assertFalse(result.valid)
                self.assertEqual(result.validation_status, status)
                if name in {"missing_tool", "duplicate_tool"}:
                    self.assertEqual(
                        result.normalized_catalog_fingerprint, None
                    )
                elif name == "description_drift":
                    self.assertNotEqual(
                        result.normalized_catalog_fingerprint,
                        result.expected_normalized_catalog_fingerprint,
                    )
                else:
                    self.assertEqual(
                        result.normalized_catalog_fingerprint,
                        result.expected_normalized_catalog_fingerprint,
                    )

    def test_reviewed_classification_and_runtime_model_are_fail_closed(self):
        registry = load_reviewed_upstream_release_registry()
        release = registry.by_version["8.0.0"]
        entries = tuple(
            replace(entry, classification="persistent_write")
            if entry.upstream_name == "ha_manage_backup"
            else entry
            for entry in release.policy.tools
        )
        changed_policy = replace(release.policy, tools=entries)
        changed_release = replace(release, policy=changed_policy)
        result = validate(
            "8.0.0", addon_8_tools(), release=changed_release
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.classification_mismatch_count, 1)
        self.assertEqual(
            result.classification_mismatches, ("ha_manage_backup",)
        )

        unsupported = replace(
            release,
            runtime_contract_fingerprint_model="unreviewed-model-v9",
        )
        result = validate("8.0.0", addon_8_tools(), release=unsupported)
        self.assertFalse(result.valid)
        self.assertEqual(
            result.validation_status,
            "unsupported_runtime_fingerprint_model",
        )
        self.assertEqual(result.normalized_catalog_fingerprint, None)


class SpecialProviderCatalogAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_accepts_exact_addon_catalog_without_dispatch(self):
        transport = CatalogTransport("8.0.0", addon_8_tools())
        provider = ReviewedOperationalBackupProvider()
        provider._transport = transport
        provider._state.configured = True

        evidence = await provider.probe()

        self.assertEqual(
            evidence.catalog_fingerprint,
            "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768",
        )
        self.assertEqual(
            evidence.normalized_catalog_fingerprint,
            provider.health_snapshot()["catalog_validation"][
                "expected_normalized_catalog_fingerprint"
            ],
        )
        health = provider.health_snapshot()
        self.assertEqual(
            health["selected_compatibility_entry_id"],
            "ha-mcp-v8.0.0-d65630f6",
        )
        self.assertEqual(
            health["catalog_validation"]["reviewed_accounted_count"], 78
        )
        self.assertEqual(
            health["catalog_validation"]["validation_status"],
            "accepted_exact",
        )
        self.assertEqual(health["dispatch_count"], 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_lifecycle_accepts_exact_addon_catalog_for_each_operation(
        self,
    ):
        for operation in (
            "controlled_reload",
            "restart_addon",
            "restart_home_assistant",
        ):
            with self.subTest(operation=operation):
                transport = CatalogTransport("8.0.0", addon_8_tools())
                provider = ReviewedOperationalLifecycleProvider()
                provider._transport = transport
                provider._state.configured = True

                evidence = await provider.probe(operation)

                self.assertEqual(
                    evidence.normalized_catalog_fingerprint,
                    provider.health_snapshot()["catalog_validation"][
                        "expected_normalized_catalog_fingerprint"
                    ],
                )
                health = provider.health_snapshot()
                self.assertEqual(
                    health["selected_compatibility_entry_id"],
                    "ha-mcp-v8.0.0-d65630f6",
                )
                self.assertEqual(
                    health["catalog_validation"]["observed_tool_count"],
                    78,
                )
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_catalog_drift_fails_before_special_provider_dispatch(self):
        tools = addon_8_tools()
        tools[0]["_meta"]["ha_mcp"]["pinned"] = not tools[0][
            "_meta"
        ]["ha_mcp"]["pinned"]

        backup_transport = CatalogTransport("8.0.0", tools)
        backup = ReviewedOperationalBackupProvider()
        backup._transport = backup_transport
        backup._state.configured = True
        with self.assertRaises(OperationalBackupProviderError) as caught:
            await backup.probe()
        self.assertEqual(caught.exception.category, "catalog_mismatch")
        backup_health = backup.health_snapshot()
        self.assertEqual(backup_health["dispatch_count"], 0)
        self.assertEqual(
            backup_health["catalog_validation"]["validation_status"],
            "rejected_catalog_mismatch",
        )
        self.assertEqual(
            backup_health["catalog_validation"]["mismatch_diagnostics"][
                0
            ]["runtime_contract_diff_fields"],
            ["/_meta/ha_mcp/pinned"],
        )

        lifecycle_transport = CatalogTransport("8.0.0", tools)
        lifecycle = ReviewedOperationalLifecycleProvider()
        lifecycle._transport = lifecycle_transport
        lifecycle._state.configured = True
        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await lifecycle.probe("restart_addon")
        self.assertEqual(caught.exception.category, "catalog_mismatch")
        lifecycle_health = lifecycle.health_snapshot()
        self.assertEqual(sum(lifecycle_health["dispatch_counts"].values()), 0)
        self.assertEqual(lifecycle_health["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
