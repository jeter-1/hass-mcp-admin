"""F3-A isolation, metadata, and compatibility invariants."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
RUNTIME = BETA_DIR / "ha_mcp_engineering"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.capabilities import (  # noqa: E402
    CAPABILITIES,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.governance.models import ChangeOperation  # noqa: E402
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    TASK_SCHEMA_VERSION,
)
from ha_mcp_engineering.tools import registered_tools  # noqa: E402
from ha_mcp_engineering.tools.registry import (  # noqa: E402
    get_registered_server,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    validate_reviewed_release_evidence,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402

PROMOTION_SPEC = importlib.util.spec_from_file_location(
    "f3_adapter_release_authority",
    ROOT / "scripts" / "promote_next_release.py",
)
PROMOTION_MODULE = importlib.util.module_from_spec(PROMOTION_SPEC)
assert PROMOTION_SPEC.loader is not None
PROMOTION_SPEC.loader.exec_module(PROMOTION_MODULE)


class F3AdapterIsolationTests(unittest.TestCase):
    def test_only_reviewed_adapter_and_integration_packages_import_f3_core(self):
        forbidden_modules = {
            "ha_mcp_engineering.f3",
            "f3.executor",
            "f3.locks",
            "f3.persistence",
        }
        for path in sorted(RUNTIME.rglob("*.py")):
            if any(
                RUNTIME / package in path.parents
                for package in (
                    "f3",
                    "f3_configuration",
                    "f3_dashboard",
                    "f3_runtime",
                )
            ):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertFalse(
                    any(
                        value in forbidden_modules
                        or value.startswith("ha_mcp_engineering.f3.")
                        for value in imported
                    )
                )

    def test_source_tool_and_schema_contract_has_only_approved_dashboard_additions(self):
        local_tools = registered_tools(get_registered_server())
        self.assertEqual(len(CAPABILITIES), 25)
        self.assertEqual(len(local_tools) - len(CAPABILITIES), 26)
        self.assertEqual(len(local_tools), 51)
        self.assertEqual(len(PLANNED_CAPABILITIES), 0)
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        self.assertEqual(
            {item.value for item in ChangeOperation},
            {
                "create_automation",
                "update_automation",
                "configuration_plan",
                "update_dashboard",
                "create_full_backup",
                "controlled_reload",
                "restart_addon",
                "restart_home_assistant",
                "set_input_boolean_state",
            },
        )

    def test_versions_and_secure_dependency_pins_are_unchanged(self):
        beta_config = yaml.safe_load(
            (BETA_DIR / "config.yaml").read_text(encoding="utf-8")
        )
        stable_config = yaml.safe_load(
            (ROOT / "hass_mcp_admin" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        requirements = (
            BETA_DIR / "requirements.txt"
        ).read_text(encoding="utf-8").splitlines()
        advertised_version = PROMOTION_MODULE.advertised_version(ROOT)
        self.assertEqual(SERVER_VERSION, advertised_version)
        self.assertEqual(beta_config["version"], advertised_version)
        self.assertEqual(stable_config["version"], "1.1.2")
        self.assertIn("aiohttp==3.14.3", requirements)
        self.assertIn("cryptography==50.0.0", requirements)

    def test_exact_upstream_release_accounting_is_unchanged(self):
        registry = validate_reviewed_release_evidence(repository_root=ROOT)
        seven = registry.by_version["7.14.2"]
        eight = registry.by_version["8.0.0"]
        self.assertEqual(len(seven.tool_contracts), 78)
        self.assertEqual(
            seven.policy.classification_counts["automatic_read"], 26
        )
        self.assertEqual(50 + 26, 76)
        self.assertEqual(len(eight.tool_contracts), 78)
        self.assertEqual(
            eight.policy.classification_counts["automatic_read"], 24
        )
        self.assertEqual(
            eight.policy.classification_counts["held_for_canary"], 2
        )
        held = {
            item.upstream_name
            for item in eight.policy.tools
            if item.classification == "held_for_canary"
        }
        self.assertEqual(held, {"ha_search", "ha_get_operation_status"})
        self.assertEqual(50 + 24, 74)


if __name__ == "__main__":
    unittest.main()
