from __future__ import annotations

import ast
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
    runtime_contract_fingerprint,
    validate_reviewed_release_catalog,
)
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
    validate_dashboard_read_arguments,
)
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadCatalog,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    OperationalBackupProviderError,
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY_V3,
    decide_admission,
    load_attestations,
    normalize_runtime_contract,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_catalog_admission_comparison_lines(source: str) -> tuple[int, ...]:
    """Find raw release-catalog comparisons while allowing evidence reads."""

    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        operands = (node.left, *node.comparators)
        if any(
            isinstance(item, ast.Attribute)
            and item.attr == "catalog_fingerprint"
            for operand in operands
            for item in ast.walk(operand)
        ):
            lines.append(node.lineno)
    return tuple(sorted(lines))


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


def dashboard_tool(tools: list[dict]) -> dict:
    return next(
        item for item in tools if item["name"] == "ha_config_get_dashboard"
    )


def dashboard_decision(tool: dict):
    return decide_admission(
        server_name="ha-mcp",
        server_version="8.0.0",
        protocol_version="2025-03-26",
        tool=tool,
        attestations=tuple((item, "builtin") for item in load_attestations()),
    )


def dashboard_settings() -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-supervisor-token",
        access_secret="synthetic-access-secret-value",
        port=8100,
        audit_path="audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        response_size_limit=60_000,
        upstream_dashboard_mcp_url=(
            "http://ha-mcp:9583/synthetic-dashboard-secret/mcp"
        ),
    )


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


class DashboardCatalogTransport:
    def __init__(self, tool: dict) -> None:
        self.handshake = McpDashboardHandshake(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version="8.0.0",
            tools=(tool,),
            connection_latency_ms=1.0,
        )
        self.dispatch_count = 0

    async def discover(self) -> McpDashboardHandshake:
        return self.handshake


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
        self.assertEqual(
            addon.normalized_catalog_fingerprint,
            "3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53",
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
                "unknown_minor",
                tools,
                {"upstream_version": "8.1.0"},
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

    def test_security_schema_output_and_policy_drift_fail_closed(self):
        baseline = addon_8_tools()
        target_index = next(
            index
            for index, tool in enumerate(baseline)
            if tool["name"] == "ha_get_state"
        )
        candidates: dict[str, list[dict]] = {}

        additional = deepcopy(baseline)
        unreviewed = deepcopy(additional[0])
        unreviewed["name"] = "ha_unreviewed_beta14"
        additional.append(unreviewed)
        candidates["additional_unreviewed_tool"] = additional

        pinned = deepcopy(baseline)
        pinned[target_index]["_meta"]["ha_mcp"]["pinned"] = not pinned[
            target_index
        ]["_meta"]["ha_mcp"]["pinned"]
        candidates["pinned"] = pinned

        exposed = deepcopy(baseline)
        exposed[target_index]["_meta"]["ha_mcp"][
            "llm_api_exposed"
        ] = not exposed[target_index]["_meta"]["ha_mcp"][
            "llm_api_exposed"
        ]
        candidates["llm_api_exposed"] = exposed

        tags = deepcopy(baseline)
        tags[target_index]["_meta"]["fastmcp"]["tags"].append(
            "unreviewed"
        )
        candidates["fastmcp_tags"] = tags

        annotations = deepcopy(baseline)
        annotations[target_index]["annotations"][
            "readOnlyHint"
        ] = not annotations[target_index]["annotations"]["readOnlyHint"]
        candidates["annotations"] = annotations

        input_schema = deepcopy(baseline)
        input_schema[target_index]["inputSchema"] = {
            "type": "object",
            "additionalProperties": True,
        }
        candidates["input_schema"] = input_schema

        output_contract = deepcopy(baseline)
        output_contract[target_index]["outputSchema"] = {
            "type": "object",
            "properties": {"unreviewed": {"type": "string"}},
        }
        candidates["output_contract"] = output_contract

        policy_cases: dict[str, object] = {
            "policy_not_object": "addon",
            "policy_missing_key": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
            },
            "policy_extra_key": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": 0,
                "unreviewed": False,
            },
            "policy_wrong_boolean": {
                "deployment": "addon",
                "enabled": 1,
                "live": True,
                "rules": 0,
            },
            "policy_wrong_rules_type": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": False,
            },
            "policy_rules_above_bound": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": 10_001,
            },
            "policy_unsupported_deployment": {
                "deployment": "future",
                "enabled": True,
                "live": True,
                "rules": 0,
            },
        }
        for name, policy in policy_cases.items():
            candidate = deepcopy(baseline)
            candidate[target_index]["_meta"]["ha_mcp"][
                "policy"
            ] = policy
            candidates[name] = candidate

        missing_policy = deepcopy(baseline)
        missing_policy[target_index]["_meta"]["ha_mcp"].pop("policy")
        candidates["policy_missing"] = missing_policy

        for name, tools in candidates.items():
            with self.subTest(name=name):
                result = validate("8.0.0", tools)
                self.assertFalse(result.valid)
                self.assertEqual(
                    result.validation_status,
                    "rejected_catalog_mismatch",
                )
                self.assertGreater(
                    result.invalid_descriptor_count
                    + result.additional_tool_count
                    + sum(dict(result.component_mismatch_counts).values()),
                    0,
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
    async def test_backup_accepts_each_exact_reviewed_deployment(self):
        deployments = (
            ("7.14.2", reviewed_tools("7.14.2")),
            ("8.0.0-standalone", reviewed_tools("8.0.0")),
            ("8.0.0-addon", addon_8_tools()),
        )
        for label, tools in deployments:
            version = label.split("-")[0]
            with self.subTest(deployment=label):
                transport = CatalogTransport(version, tools)
                provider = ReviewedOperationalBackupProvider()
                provider._transport = transport
                provider._state.configured = True

                evidence = await provider.probe()

                self.assertEqual(evidence.server_version, version)
                self.assertEqual(provider.health_snapshot()["dispatch_count"], 0)
                self.assertEqual(provider.health_snapshot()["fallback_count"], 0)

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

    async def test_lifecycle_accepts_7_14_2_and_8_0_0_standalone_catalogs(self):
        for version in ("7.14.2", "8.0.0"):
            with self.subTest(version=version):
                transport = CatalogTransport(
                    version, reviewed_tools(version)
                )
                provider = ReviewedOperationalLifecycleProvider()
                provider._transport = transport
                provider._state.configured = True

                evidence = await provider.probe("restart_addon")

                self.assertEqual(evidence.server_version, version)
                health = provider.health_snapshot()
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


class DashboardV3RuntimeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def test_standalone_and_addon_use_identical_normalized_dashboard_contracts(
        self,
    ):
        standalone_tool = dashboard_tool(reviewed_tools("8.0.0"))
        addon_tool = dashboard_tool(addon_8_tools())
        standalone = normalize_runtime_contract(
            standalone_tool,
            protocol_version="2025-03-26",
            contract_family=CONTRACT_FAMILY_V3,
        )
        addon = normalize_runtime_contract(
            addon_tool,
            protocol_version="2025-03-26",
            contract_family=CONTRACT_FAMILY_V3,
        )

        self.assertEqual(
            standalone.security_fingerprint,
            addon.security_fingerprint,
        )
        self.assertEqual(
            standalone.runtime_fingerprint,
            addon.runtime_fingerprint,
        )
        self.assertEqual(
            addon.security_fingerprint,
            "f1f03110ee84abc017287ebfdc12706dd2368668414ba082efd593e89b583c95",
        )
        self.assertEqual(
            addon.runtime_fingerprint,
            "806f6d6b0b54cd49162684834e650f8ca7c8f2735b36e8772263b1bbe00a5569",
        )
        self.assertTrue(dashboard_decision(standalone_tool).accepted)
        self.assertTrue(dashboard_decision(addon_tool).accepted)

    async def test_addon_dashboard_admission_reports_exact_release_model(self):
        tool = dashboard_tool(addon_8_tools())
        transport = DashboardCatalogTransport(tool)
        provider = UpstreamDashboardProvider()
        provider.configure(dashboard_settings(), transport=transport)

        await provider.refresh_capabilities()

        health = provider.health_snapshot()
        exact_release_fingerprint = (
            "fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e"
        )
        self.assertEqual(health["admission_status"], "admitted_builtin_attestation")
        self.assertEqual(health["contract_family"], CONTRACT_FAMILY_V3)
        self.assertEqual(
            health["release_runtime_contract_fingerprint_model"],
            "ha-mcp-operational-tool-descriptor-v2",
        )
        self.assertEqual(
            health["expected_release_runtime_contract_fingerprint"],
            exact_release_fingerprint,
        )
        self.assertEqual(
            health["observed_release_runtime_contract_fingerprint"],
            exact_release_fingerprint,
        )
        self.assertTrue(health["release_runtime_contract_match"])
        self.assertEqual(health["runtime_contract_diff_fields"], [])
        self.assertTrue(health["runtime_policy_state_normalized"])
        self.assertEqual(transport.dispatch_count, 0)

    def test_all_four_valid_policy_values_are_normalized(self):
        baseline = dashboard_tool(reviewed_tools("8.0.0"))
        expected = runtime_contract_fingerprint(
            baseline,
            model="ha-mcp-operational-tool-descriptor-v2",
        )
        variants = (
            {"deployment": "addon"},
            {"enabled": True},
            {"live": True},
            {"rules": 10_000},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                tool = deepcopy(baseline)
                tool["_meta"]["ha_mcp"]["policy"].update(changes)
                self.assertEqual(
                    runtime_contract_fingerprint(
                        tool,
                        model="ha-mcp-operational-tool-descriptor-v2",
                    ),
                    expected,
                )
                self.assertTrue(dashboard_decision(tool).accepted)

    def test_malformed_policy_shapes_fail_closed(self):
        baseline = dashboard_tool(addon_8_tools())
        cases: dict[str, object] = {
            "missing_policy": None,
            "not_object": "addon",
            "missing_key": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
            },
            "extra_key": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": 0,
                "future": False,
            },
            "wrong_boolean_type": {
                "deployment": "addon",
                "enabled": 1,
                "live": True,
                "rules": 0,
            },
            "wrong_rules_type": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": True,
            },
            "rules_above_bound": {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": 10_001,
            },
            "unsupported_deployment": {
                "deployment": "production",
                "enabled": True,
                "live": True,
                "rules": 0,
            },
        }
        for name, policy in cases.items():
            with self.subTest(name=name):
                tool = deepcopy(baseline)
                if name == "missing_policy":
                    tool["_meta"]["ha_mcp"].pop("policy")
                else:
                    tool["_meta"]["ha_mcp"]["policy"] = policy
                decision = dashboard_decision(tool)
                self.assertFalse(decision.accepted)
                self.assertEqual(
                    decision.failure_category,
                    "upstream_runtime_contract_mismatch",
                )

    def test_security_and_execution_relevant_drift_remains_rejected(self):
        baseline = dashboard_tool(addon_8_tools())
        cases: dict[str, dict] = {}
        pinned = deepcopy(baseline)
        pinned["_meta"]["ha_mcp"]["pinned"] = True
        cases["pinned"] = pinned
        exposed = deepcopy(baseline)
        exposed["_meta"]["ha_mcp"]["llm_api_exposed"] = False
        cases["llm_api_exposed"] = exposed
        tags = deepcopy(baseline)
        tags["_meta"]["fastmcp"]["tags"].append("future")
        cases["tags"] = tags
        annotations = deepcopy(baseline)
        annotations["annotations"]["readOnlyHint"] = False
        cases["annotations"] = annotations
        schema = deepcopy(baseline)
        schema["inputSchema"]["properties"]["list_only"]["type"] = "string"
        cases["input_schema"] = schema
        output = deepcopy(baseline)
        output["outputSchema"] = {"type": "object"}
        cases["output_contract"] = output
        name = deepcopy(baseline)
        name["name"] = "ha_config_get_dashboard_future"
        cases["name"] = name
        description = deepcopy(baseline)
        description["description"] += " drift"
        cases["description"] = description

        for label, tool in cases.items():
            with self.subTest(label=label):
                self.assertFalse(dashboard_decision(tool).accepted)

    def test_dashboard_argument_surface_remains_exact(self):
        validate_dashboard_read_arguments(
            {"list_only": True, "include_screenshot": False}
        )
        validate_dashboard_read_arguments(
            {
                "url_path": "operator-dashboard",
                "list_only": False,
                "force_reload": False,
                "include_screenshot": False,
            }
        )
        rejected = (
            {"list_only": True, "include_screenshot": True},
            {"list_only": True, "preferences": {}},
            {"list_only": True, "mode": "search"},
        )
        for arguments in rejected:
            with self.subTest(arguments=arguments):
                with self.assertRaises(DashboardTransportError):
                    validate_dashboard_read_arguments(arguments)


class SourceAndExactAddonRuntimeGuardTests(unittest.TestCase):
    def test_special_providers_use_shared_catalog_admission_not_raw_equality(self):
        for relative in (
            "hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py",
            "hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py",
        ):
            with self.subTest(path=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("validate_reviewed_release_catalog(", source)
                self.assertEqual(
                    raw_catalog_admission_comparison_lines(source),
                    (),
                )

    def test_raw_catalog_guard_allows_evidence_but_rejects_renamed_equality(self):
        diagnostic_only = """
reviewed_raw = selected_release.catalog_fingerprint
report(reviewed_raw)
"""
        renamed_gate = """
if actual_raw == selected_release.catalog_fingerprint:
    admit()
"""
        self.assertEqual(
            raw_catalog_admission_comparison_lines(diagnostic_only),
            (),
        )
        self.assertEqual(
            raw_catalog_admission_comparison_lines(renamed_gate),
            (2,),
        )

    def test_dashboard_and_exception_group_guards_remain_model_aware(self):
        dashboard = (
            ROOT
            / "hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_contracts.py"
        ).read_text(encoding="utf-8")
        client = (
            ROOT
            / "hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "runtime_policy_state_fingerprint_projection(policy)",
            dashboard,
        )
        self.assertNotIn('"deployment": "standalone"', dashboard)
        self.assertIn("_classified_transport_error", client)
        self.assertIn("grouped_categories", client)

    def test_distinct_immutable_addon_runtime_job_is_complete(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        acceptance = (
            ROOT / "scripts/exact_addon_runtime_acceptance.py"
        ).read_text(encoding="utf-8")
        fixture = (
            ROOT / "scripts/fake_ha_read_gateway_contract_server.py"
        ).read_text(encoding="utf-8")
        self.assertIn("exact-addon-runtime-acceptance:", workflow)
        self.assertIn(
            "Exact ha-mcp 8.0.0 add-on runtime acceptance",
            workflow,
        )
        self.assertIn(
            "sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd",
            workflow,
        )
        self.assertIn(
            "sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404",
            workflow,
        )
        self.assertIn(
            "sha256:150ee09078919a47db19639deaa8c27ec064390054e27b4e618f82eea9cf7f50",
            workflow,
        )
        self.assertIn(
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@82c7e631bb3cdc910f68e0081d67478d79c6982d",
            workflow,
        )
        self.assertIn(
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            workflow,
        )
        self.assertIn(
            "scripts/exact_addon_runtime_acceptance.py",
            workflow,
        )
        for operation in (
            "create_backup_plan",
            "create_reload_plan",
            "create_addon_restart_plan",
            "create_home_assistant_restart_plan",
        ):
            self.assertIn(operation, acceptance)
        self.assertNotIn("apply_change_plan", acceptance)
        self.assertIn("EXPECTED_RAW_CATALOG_FINGERPRINT", acceptance)
        self.assertIn("backup_dispatch_count == 0", acceptance)
        self.assertIn("sum(lifecycle_dispatch_counts.values()) == 0", acceptance)
        self.assertIn('"lovelace/config"', fixture)


if __name__ == "__main__":
    unittest.main()
