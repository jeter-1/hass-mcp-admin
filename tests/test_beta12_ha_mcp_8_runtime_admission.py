from __future__ import annotations

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
    UpstreamReadGateway,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1,
    catalog_fingerprint,
    load_reviewed_upstream_release_registry,
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

    async def native() -> str:
        return "native"

    value.tool(name="native_beta12_test")(native)
    return value


class Beta12ReproductionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def gateway_for(
        self, tools: list[dict]
    ) -> tuple[UpstreamReadGateway, FastMCP]:
        gateway = UpstreamReadGateway()
        gateway.configure(
            settings(),
            transport=FakeTransport(tools, version="8.0.0"),
            release_registry=load_reviewed_upstream_release_registry(),
            admission_validator=lambda _catalog: None,
        )
        target = server()
        await gateway.initialize(target)
        return gateway, target

    async def test_live_addon_fingerprint_reproduces_all_24_mismatches(self):
        fixture = load_json(RECONSTRUCTION)
        tools = reconstruct_live_addon_tools()
        expected = fixture["expected_results"]
        self.assertEqual(len(tools), 78)
        self.assertEqual(
            catalog_fingerprint(tools),
            expected["observed_live_catalog_fingerprint"],
        )

        gateway, _target = await self.gateway_for(tools)
        health = gateway.health_snapshot()
        self.assertEqual(health["dynamically_exposed_count"], 0)
        self.assertEqual(health["runtime_contract_mismatch_count"], 24)
        self.assertEqual(health["quarantined_automatic_read_count"], 24)
        self.assertEqual(
            health["quarantine_reason_counts"],
            {"runtime_contract_mismatch": 24},
        )
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

        expected_diff = [
            "/_meta/ha_mcp/policy/deployment",
            "/_meta/ha_mcp/policy/enabled",
            "/_meta/ha_mcp/policy/live",
        ]
        self.assertEqual(len(health["quarantined_tools"]), 24)
        for item in health["quarantined_tools"]:
            with self.subTest(tool=item["upstream_name"]):
                self.assertEqual(
                    item["expected_contract_fingerprint"],
                    item["observed_contract_fingerprint"],
                )
                self.assertNotEqual(
                    item["expected_runtime_contract_fingerprint"],
                    item["observed_runtime_contract_fingerprint"],
                )
                self.assertEqual(
                    item["runtime_contract_fingerprint_model"],
                    RUNTIME_CONTRACT_FINGERPRINT_MODEL_V1,
                )
                self.assertEqual(
                    item["runtime_contract_diff_fields"], expected_diff
                )
                self.assertLessEqual(
                    len(item["runtime_contract_diff_summary"]), 512
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
        self.assertNotIn("True", item["runtime_contract_diff_summary"])
        self.assertLessEqual(len(item["runtime_contract_diff_summary"]), 512)

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
            self.assertIn(
                "legacy raw full-descriptor runtime comparator differs",
                output_markdown.read_text(encoding="utf-8"),
            )
