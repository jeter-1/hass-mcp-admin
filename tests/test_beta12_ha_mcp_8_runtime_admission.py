from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
EVIDENCE = ROOT / "docs/evidence/upstream-read-compatibility"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    OPERATIONAL_CATALOG_FINGERPRINT_MODEL,
    SUPPORTED_PROTOCOLS,
    UpstreamReadGateway,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    MAX_RUNTIME_POLICY_RULE_COUNT,
    RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
    runtime_contract_fingerprint,
    schema_fingerprint,
)
from tests.test_readonly_upstream_gateway import (  # noqa: E402
    FakeTransport,
    settings,
)


RECONSTRUCTION = (
    EVIDENCE / "ha-mcp-8.0.0-live-addon-reconstruction.json"
)
REVIEWED_CAPTURE = EVIDENCE / "ha-mcp-8.0.0.json"
EXACT_ARTIFACT_INSPECTION = (
    EVIDENCE / "ha-mcp-8.0.0-exact-artifact-inspection.json"
)
FIELD_DIFF_REPORT = EVIDENCE / "ha-mcp-8.0.0-live-addon-field-diff.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reconstruct_live_addon_tools() -> list[dict]:
    fixture = load_json(RECONSTRUCTION)
    tools = deepcopy(load_json(REVIEWED_CAPTURE)["tools"])
    replacement = fixture["transform"]["replacement"]
    for tool in tools:
        tool["_meta"]["ha_mcp"]["policy"] = deepcopy(replacement)
    return tools


def server() -> FastMCP:
    value = FastMCP("beta12-runtime-admission")
    for index in range(48):

        async def native() -> str:
            return "native"

        value.tool(name=f"native_beta12_test_{index}")(native)
    return value


class Beta12ReproductionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def gateway_for(
        self, tools: list[dict], *, version: str = "8.0.0"
    ) -> tuple[UpstreamReadGateway, FastMCP]:
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=FakeTransport(tools, version=version),
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        target = server()
        await gateway.initialize(target)
        return gateway, target

    async def test_live_addon_fingerprint_reproduces_and_admits_24_reads(self):
        fixture = load_json(RECONSTRUCTION)
        tools = reconstruct_live_addon_tools()
        expected = fixture["expected_results"]
        self.assertEqual(len(tools), 78)
        self.assertEqual(
            catalog_fingerprint(tools),
            expected["observed_live_catalog_fingerprint"],
        )

        gateway, target = await self.gateway_for(tools)
        health = gateway.health_snapshot()
        self.assertEqual(health["dynamically_exposed_count"], 24)
        self.assertEqual(health["exact_matched_automatic_read_count"], 24)
        self.assertEqual(health["runtime_contract_mismatch_count"], 0)
        self.assertEqual(health["quarantined_automatic_read_count"], 0)
        self.assertEqual(health["quarantine_reason_counts"], {})
        self.assertEqual(health["held_read_count"], 2)
        self.assertEqual(
            health["held_tools"], ["ha_get_operation_status", "ha_search"]
        )
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["upstream_advertised_tool_count"], 78)
        self.assertEqual(health["reviewed_automatic_read_count"], 24)
        self.assertEqual(health["missing_automatic_read_count"], 0)
        self.assertEqual(health["unreviewed_tool_count"], 0)
        self.assertEqual(len(registered_tools(target)), 72)
        self.assertNotIn("ha_search", registered_tools(target))
        self.assertNotIn("ha_get_operation_status", registered_tools(target))
        self.assertEqual(
            health["observed_catalog_fingerprint"],
            expected["observed_live_catalog_fingerprint"],
        )
        self.assertEqual(
            health["reviewed_catalog_fingerprint"],
            expected["reviewed_stock_catalog_fingerprint"],
        )
        self.assertEqual(
            health["operational_catalog_fingerprint_model"],
            OPERATIONAL_CATALOG_FINGERPRINT_MODEL,
        )
        self.assertEqual(
            health["catalog_diff_field_counts"],
            expected["raw_changed_field_counts"],
        )
        self.assertEqual(
            health["reviewed_strict_full_contract_fingerprint"],
            expected["reviewed_strict_full_contract_fingerprint"],
        )
        self.assertEqual(
            health["observed_strict_full_contract_fingerprint"],
            schema_fingerprint({"tools": tools}),
        )

        self.assertEqual(health["quarantined_tools"], [])
        self.assertEqual(
            health["runtime_contract_fingerprint_model"],
            RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
        )

    async def test_7_14_2_regression_accounting_remains_exact(self):
        tools = load_json(EVIDENCE / "ha-mcp-7.14.2.json")["tools"]
        gateway, target = await self.gateway_for(tools, version="7.14.2")
        health = gateway.health_snapshot()
        self.assertEqual(health["upstream_advertised_tool_count"], 78)
        self.assertEqual(health["reviewed_automatic_read_count"], 26)
        self.assertEqual(health["exact_matched_automatic_read_count"], 26)
        self.assertEqual(health["dynamically_exposed_count"], 26)
        self.assertEqual(len(registered_tools(target)), 74)
        self.assertEqual(
            health["observed_catalog_fingerprint"],
            "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c",
        )
        self.assertEqual(health["runtime_contract_mismatch_count"], 0)
        self.assertEqual(health["quarantined_automatic_read_count"], 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_unknown_8_x_versions_do_not_inherit_trust(self):
        tools = reconstruct_live_addon_tools()
        for version in ("8.0.1", "8.1.2", "8.2.0"):
            with self.subTest(version=version):
                gateway, target = await self.gateway_for(
                    tools, version=version
                )
                health = gateway.health_snapshot()
                self.assertEqual(health["version_status"], "rejected_unreviewed")
                self.assertEqual(health["dynamically_exposed_count"], 0)
                self.assertEqual(health["fallback_count"], 0)
                self.assertEqual(len(registered_tools(target)), 48)
        self.assertEqual(SUPPORTED_PROTOCOLS, frozenset({"2025-03-26"}))

    def test_release_consumers_cannot_hash_raw_tool_descriptors(self):
        consumers = (
            BETA
            / "ha_mcp_engineering"
            / "providers"
            / "upstream_read_gateway.py",
            BETA
            / "ha_mcp_engineering"
            / "providers"
            / "operational_backup.py",
            BETA
            / "ha_mcp_engineering"
            / "providers"
            / "operational_lifecycle.py",
        )
        for path in consumers:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                raw_descriptor_calls = []
                model_selected_calls = []
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(
                        node.func, ast.Name
                    ):
                        continue
                    if (
                        node.func.id == "schema_fingerprint"
                        and node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in {"tool", "observed_tool"}
                    ):
                        raw_descriptor_calls.append(node.lineno)
                    if node.func.id == "runtime_contract_fingerprint":
                        model_selected_calls.append(
                            (
                                node.lineno,
                                any(
                                    keyword.arg == "model"
                                    for keyword in node.keywords
                                ),
                            )
                        )
                self.assertEqual(raw_descriptor_calls, [])
                self.assertTrue(model_selected_calls)
                self.assertTrue(
                    all(selected for _line, selected in model_selected_calls)
                )

    async def test_one_runtime_only_change_has_bounded_exact_diagnostics(self):
        tools = deepcopy(load_json(REVIEWED_CAPTURE)["tools"])
        changed = next(tool for tool in tools if tool["name"] == "ha_get_state")
        changed["_meta"]["ha_mcp"]["pinned"] = True

        gateway, _target = await self.gateway_for(tools)
        health = gateway.health_snapshot()
        self.assertEqual(health["runtime_contract_mismatch_count"], 1)
        self.assertEqual(health["quarantined_automatic_read_count"], 1)
        self.assertEqual(health["dynamically_exposed_count"], 23)
        item = health["quarantined_tools"][0]
        self.assertEqual(item["upstream_name"], "ha_get_state")
        self.assertEqual(
            item["expected_contract_fingerprint"],
            item["observed_contract_fingerprint"],
        )
        self.assertNotEqual(
            item["expected_runtime_contract_fingerprint"],
            item["observed_runtime_contract_fingerprint"],
        )
        self.assertEqual(
            item["runtime_contract_diff_fields"],
            ["/_meta/ha_mcp/pinned"],
        )
        self.assertEqual(
            item["runtime_contract_fingerprint_model"],
            RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2,
        )
        self.assertNotIn("True", item["runtime_contract_diff_summary"])
        self.assertLessEqual(len(item["runtime_contract_diff_summary"]), 512)

    async def test_invalid_dynamic_policy_shapes_remain_fail_closed(self):
        invalid_values = (
            None,
            {},
            {
                "deployment": "embedded",
                "enabled": True,
                "live": True,
                "rules": 0,
            },
            {
                "deployment": "addon",
                "enabled": 1,
                "live": True,
                "rules": 0,
            },
            {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": MAX_RUNTIME_POLICY_RULE_COUNT + 1,
            },
            {
                "deployment": "addon",
                "enabled": True,
                "live": True,
                "rules": 0,
                "unreviewed": False,
            },
        )
        for invalid in invalid_values:
            with self.subTest(policy=invalid):
                tools = deepcopy(load_json(REVIEWED_CAPTURE)["tools"])
                changed = next(
                    tool for tool in tools if tool["name"] == "ha_get_state"
                )
                changed["_meta"]["ha_mcp"]["policy"] = invalid
                gateway, _target = await self.gateway_for(tools)
                health = gateway.health_snapshot()
                self.assertEqual(health["runtime_contract_mismatch_count"], 1)
                self.assertEqual(
                    health["quarantined_automatic_read_count"], 1
                )
                self.assertEqual(health["dynamically_exposed_count"], 23)
                self.assertEqual(
                    health["quarantined_tools"][0]["upstream_name"],
                    "ha_get_state",
                )

    def test_only_valid_policy_runtime_values_are_normalized(self):
        reviewed = deepcopy(load_json(REVIEWED_CAPTURE)["tools"][0])
        addon = deepcopy(reviewed)
        addon["_meta"]["ha_mcp"]["policy"] = {
            "deployment": "addon",
            "enabled": True,
            "live": True,
            "rules": MAX_RUNTIME_POLICY_RULE_COUNT,
        }
        self.assertEqual(
            runtime_contract_fingerprint(
                reviewed, model=RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
            ),
            runtime_contract_fingerprint(
                addon, model=RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
            ),
        )
        invalid = deepcopy(addon)
        invalid["_meta"]["ha_mcp"]["policy"]["rules"] = True
        self.assertNotEqual(
            runtime_contract_fingerprint(
                reviewed, model=RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
            ),
            runtime_contract_fingerprint(
                invalid, model=RUNTIME_CONTRACT_FINGERPRINT_MODEL_V2
            ),
        )

    def test_reproduction_script_writes_machine_and_human_readable_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_json = Path(temporary) / "report.json"
            output_markdown = Path(temporary) / "report.md"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/reproduce_beta12_runtime_admission.py"),
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            report = load_json(output_json)
            sources = {
                item["source"]: item for item in report["sources"]
            }
            self.assertEqual(
                sources["live_addon_reconstruction"][
                    "operational_catalog_fingerprint"
                ],
                "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768",
            )
            for tool_name in (
                "ha_get_state",
                "ha_config_get_automation",
                "ha_get_history",
                "ha_list_services",
            ):
                self.assertEqual(
                    [
                        item["path"]
                        for item in report["field_diffs"][tool_name][
                            "live_addon_reconstruction"
                        ]
                    ],
                    [
                        "/_meta/ha_mcp/policy/deployment",
                        "/_meta/ha_mcp/policy/enabled",
                        "/_meta/ha_mcp/policy/live",
                    ],
                )
                reviewed = sources["reviewed_fixture"]["tools"][tool_name]
                addon = sources["live_addon_reconstruction"]["tools"][
                    tool_name
                ]
                self.assertNotEqual(
                    reviewed["raw_runtime_contract_fingerprint"],
                    addon["raw_runtime_contract_fingerprint"],
                )
                self.assertEqual(
                    reviewed["admission_runtime_contract_fingerprint"],
                    addon["admission_runtime_contract_fingerprint"],
                )
                self.assertTrue(addon["accepted"])
            self.assertIn(
                "legacy raw full-descriptor runtime comparator differs",
                output_markdown.read_text(encoding="utf-8"),
            )

    def test_exact_artifact_evidence_is_bound_to_reproduced_fingerprints(self):
        inspection = load_json(EXACT_ARTIFACT_INSPECTION)
        artifacts = inspection["artifacts"]
        self.assertEqual(
            artifacts["standalone_linux_amd64"][
                "operational_catalog_fingerprint"
            ],
            "0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316",
        )
        self.assertEqual(
            artifacts["addon_linux_amd64"][
                "operational_catalog_fingerprint"
            ],
            "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768",
        )
        self.assertEqual(
            artifacts["addon_linux_arm64"][
                "source_payload_difference_count"
            ],
            0,
        )
        self.assertFalse(inspection["production_accessed"])

        report = load_json(FIELD_DIFF_REPORT)
        sources = {item["source"]: item for item in report["sources"]}
        self.assertEqual(
            sources["exact_standalone_image"][
                "strict_ordered_catalog_fingerprint"
            ],
            "ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a",
        )
        self.assertEqual(
            sources["exact_addon_image"][
                "strict_ordered_catalog_fingerprint"
            ],
            "f061e48a5d049a2fe84f8b46451a8c2928e0eb5fc68181cf0cbbe71ae5025727",
        )
        for tool_name in (
            "ha_get_state",
            "ha_config_get_automation",
            "ha_get_history",
            "ha_list_services",
        ):
            exact_changes = report["field_diffs"][tool_name][
                "exact_addon_image"
            ]
            self.assertEqual(
                [item["path"] for item in exact_changes],
                [
                    "/_meta/ha_mcp/policy/deployment",
                    "/_meta/ha_mcp/policy/enabled",
                    "/_meta/ha_mcp/policy/live",
                ],
            )
            self.assertTrue(
                sources["exact_addon_image"]["tools"][tool_name]["accepted"]
            )
            self.assertIsNone(
                sources["exact_addon_image"]["tools"][tool_name]["reason"]
            )
