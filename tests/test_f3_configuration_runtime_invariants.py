"""Negative reachability and schema invariants for runtime-inert F3-C1."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.governance.models import ChangeOperation
from ha_mcp_engineering.governance.task_models import (
    ExecutionTaskState,
    TASK_SCHEMA_VERSION,
)
from ha_mcp_engineering.tools import get_registered_server, registered_tools


BETA = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
INTEGRATION_OWNED = (
    BETA / "application.py",
    BETA / "governance" / "service.py",
    BETA / "capabilities.py",
    BETA / "tools" / "registry.py",
    BETA / "providers" / "routing.py",
)


class RuntimeInertInvariantTests(unittest.TestCase):
    def test_current_startup_does_not_import_f3_configuration(self):
        command = (
            "import json,sys; "
            "import ha_mcp_engineering.application; "
            "print(json.dumps(sorted(name for name in sys.modules "
            "if 'f3_configuration' in name)))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            env={
                "PYTHONPATH": str(ROOT / "hass_mcp_engineering_beta"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.stdout.strip(), "[]")

    def test_current_routes_and_registration_have_zero_adapter_references(self):
        for path in INTEGRATION_OWNED:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("f3_configuration", source)
                self.assertNotIn("ConfigurationOperationAdapter", source)

    def test_new_package_defines_no_public_mcp_tools(self):
        package = BETA / "f3_configuration"
        decorators: list[str] = []
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decorators.extend(ast.unparse(item) for item in node.decorator_list)
        self.assertFalse(any("tool" in item for item in decorators), decorators)
        self.assertEqual(len(registered_tools(get_registered_server())), 48)

    def test_task_and_plan_vocabularies_are_unchanged(self):
        self.assertEqual(TASK_SCHEMA_VERSION, 1)
        self.assertEqual(
            {state.value for state in ExecutionTaskState},
            {
                "created",
                "preflight",
                "dispatching",
                "observing",
                "verifying",
                "succeeded_verified",
                "failed_pre_dispatch",
                "failed_post_dispatch",
                "manual_review_required",
                "cancelled_pre_dispatch",
                "waiting_for_lock",
                "compensating",
                "partial_application",
                "compensated",
                "superseded",
            },
        )
        self.assertEqual(
            {operation.value for operation in ChangeOperation},
            {
                "create_automation",
                "update_automation",
                "configuration_plan",
                "create_full_backup",
                "controlled_reload",
                "restart_addon",
                "restart_home_assistant",
            },
        )


if __name__ == "__main__":
    unittest.main()
