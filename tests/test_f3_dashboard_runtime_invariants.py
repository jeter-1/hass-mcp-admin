"""Guard the one bounded governed dashboard-update runtime route."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    CONFIGURATION_PLAN_CONTRACT_VERSION,
    OPERATIONAL_PLAN_CONTRACT_VERSION,
)
from ha_mcp_engineering.governance.task_models import TASK_SCHEMA_VERSION  # noqa: E402
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402

PROMOTION_SPEC = importlib.util.spec_from_file_location(
    "f3_dashboard_release_authority",
    ROOT / "scripts" / "promote_next_release.py",
)
PROMOTION_MODULE = importlib.util.module_from_spec(PROMOTION_SPEC)
assert PROMOTION_SPEC.loader is not None
PROMOTION_SPEC.loader.exec_module(PROMOTION_MODULE)


class DashboardRuntimeInvariantTests(unittest.TestCase):
    def test_engineering_runtime_imports_dashboard_only_at_closed_boundaries(self):
        runtime_root = BETA / "ha_mcp_engineering"
        contacts = []
        for path in runtime_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "f3_dashboard" in source:
                contacts.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            set(contacts),
            {
                "hass_mcp_engineering_beta/ha_mcp_engineering/f3_runtime/runtime.py",
                "hass_mcp_engineering_beta/ha_mcp_engineering/governance/runtime.py",
                "hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py",
            },
        )

    def test_dashboard_package_has_no_direct_network_or_arbitrary_dispatch(self):
        prohibited_imports = {"aiohttp", "httpx", "requests", "subprocess", "socket"}
        imported = set()
        function_names = set()
        for path in (
            BETA / "ha_mcp_engineering" / "f3_dashboard"
        ).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_names.add(node.name)
        self.assertTrue(prohibited_imports.isdisjoint(imported))
        self.assertNotIn("register_tool", function_names)
        self.assertNotIn("create_dashboard", function_names)
        self.assertNotIn("delete_dashboard", function_names)

    def test_public_surface_has_one_proposal_tool_and_no_direct_write_tool(self):
        names = set(registered_tools(get_registered_server()))
        self.assertEqual(len(names), 50)
        self.assertIn("create_dashboard_update_plan", names)
        self.assertNotIn("update_storage_dashboard", names)
        self.assertNotIn("update_dashboard", names)
        self.assertIn("update_dashboard", {value.value for value in ChangeOperation})

    def test_task_and_plan_schema_versions_are_unchanged(self):
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        self.assertEqual(CONFIGURATION_PLAN_CONTRACT_VERSION, 2)
        self.assertEqual(OPERATIONAL_PLAN_CONTRACT_VERSION, 3)

    def test_versions_and_secure_dependency_pins_are_unchanged(self):
        beta_config = (BETA / "config.yaml").read_text(encoding="utf-8")
        stable_config = (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
            encoding="utf-8"
        )
        requirements = (BETA / "requirements.txt").read_text(encoding="utf-8")
        advertised_version = PROMOTION_MODULE.advertised_version(ROOT)
        self.assertIn(f'version: "{advertised_version}"', beta_config)
        self.assertIn('version: "1.1.2"', stable_config)
        self.assertIn("aiohttp==3.14.3", requirements)
        self.assertIn("cryptography==50.0.0", requirements)

    def test_no_fallback_or_live_home_assistant_fixture_is_present(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(
                (BETA / "ha_mcp_engineering" / "f3_dashboard").glob("*.py")
            )
        )
        self.assertNotIn("SUPERVISOR_TOKEN", source)
        self.assertNotIn("direct_ha", source)
        self.assertNotIn("fallback_provider", source)


if __name__ == "__main__":
    unittest.main()
