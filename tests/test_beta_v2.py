import asyncio
from collections import Counter
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
import yaml


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIR = ROOT / "hass_mcp_admin"
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(PRODUCTION_DIR))
sys.path.insert(0, str(BETA_DIR))

production_spec = importlib.util.spec_from_file_location(
    "v1_server", PRODUCTION_DIR / "server.py"
)
production_server = importlib.util.module_from_spec(production_spec)
assert production_spec.loader is not None
production_spec.loader.exec_module(production_server)

from ha_mcp_engineering.application import create_application  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    CAPABILITIES,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.tools import compatibility  # noqa: E402
from ha_mcp_engineering.tools.registry import get_registered_server  # noqa: E402


SECRET = "beta-regression-access-secret"
INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "beta-regression", "version": "1"},
    },
}


def beta_settings(audit_path: str) -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="test-token",
        access_secret=SECRET,
        port=8100,
        audit_path=audit_path,
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
    )


class AddonIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = yaml.safe_load((PRODUCTION_DIR / "config.yaml").read_text())
        cls.beta = yaml.safe_load((BETA_DIR / "config.yaml").read_text())

    def test_production_metadata_remains_v1_1_2(self):
        self.assertEqual(self.production["name"], "HA MCP Engineering Server")
        self.assertEqual(self.production["slug"], "hass_mcp_admin")
        self.assertEqual(self.production["version"], "1.1.2")
        self.assertEqual(self.production["ports"], {"8099/tcp": 8099})

    def test_beta_metadata_is_distinct_and_valid(self):
        self.assertEqual(self.beta["name"], "HA MCP Engineering Server Beta")
        self.assertEqual(self.beta["slug"], "hass_mcp_engineering_beta")
        self.assertEqual(self.beta["version"], "2.0.0-beta.1")
        self.assertEqual(self.beta["ports"], {"8100/tcp": 8100})
        self.assertNotEqual(self.beta["slug"], self.production["slug"])
        self.assertNotEqual(set(self.beta["ports"]), set(self.production["ports"]))
        self.assertEqual(
            self.beta["slug"].replace("_", "-"), "hass-mcp-engineering-beta"
        )

    def test_dependencies_are_pinned_and_match_production(self):
        production = (PRODUCTION_DIR / "requirements.txt").read_text().splitlines()
        beta = (BETA_DIR / "requirements.txt").read_text().splitlines()
        self.assertEqual(beta, production)
        self.assertTrue(all("==" in requirement for requirement in beta))

    def test_required_v2_boundaries_and_documentation_exist(self):
        package = BETA_DIR / "ha_mcp_engineering"
        for relative_path in (
            "application.py",
            "mcp_server.py",
            "tools/registry.py",
            "routing.py",
            "clients/rest.py",
            "clients/websocket.py",
            "configuration.py",
            "models/responses.py",
            "models/failures.py",
            "audit.py",
            "capabilities.py",
            "version.py",
        ):
            self.assertTrue((package / relative_path).is_file(), relative_path)
        self.assertTrue((BETA_DIR / "README.md").is_file())
        self.assertTrue((BETA_DIR / "OBSERVABILITY.md").is_file())
        self.assertTrue((ROOT / "V2_BETA_ARCHITECTURE.md").is_file())


class ToolParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production_tools = {
            tool.name: tool for tool in production_server.mcp._tool_manager.list_tools()
        }
        cls.beta_tools = {
            tool.name: tool for tool in get_registered_server()._tool_manager.list_tools()
        }

    def test_all_25_tools_are_registered(self):
        self.assertEqual(len(self.production_tools), 25)
        self.assertEqual(len(self.beta_tools), 26)
        self.assertEqual(set(self.production_tools), set(self.beta_tools) - {"get_server_health"})

    def test_tool_names_and_argument_schemas_match_v1_1_2(self):
        production_schemas = {
            name: tool.parameters for name, tool in self.production_tools.items()
        }
        beta_schemas = {
            name: self.beta_tools[name].parameters for name in self.production_tools
        }
        self.assertEqual(beta_schemas, production_schemas)

    def test_capability_catalog_and_classifications_match_v1_1_2(self):
        self.assertEqual(
            list(CAPABILITIES), production_server.build_capability_catalog()["tools"]
        )
        counts = Counter(item["status"] for item in CAPABILITIES)
        self.assertEqual(
            counts,
            {"native": 8, "transitional": 10, "delegated": 4, "deprecated": 3},
        )
        self.assertEqual(len(PLANNED_CAPABILITIES), 6)

    def test_server_info_reports_beta_identity(self):
        result = json.loads(asyncio.run(compatibility.server_info(check_ha=False)))
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["server"]["id"], "hass-mcp-engineering-beta")
        self.assertEqual(result["data"]["server"]["name"], "HA MCP Engineering Server Beta")
        self.assertEqual(result["data"]["server"]["version"], "2.0.0-beta.1")
        self.assertEqual(result["data"]["tool_count"], 25)

    def test_list_capabilities_reports_expected_catalog(self):
        result = json.loads(asyncio.run(compatibility.list_capabilities()))
        self.assertTrue(result["success"])
        catalog = result["data"]
        self.assertEqual(catalog["count"], 25)
        self.assertEqual(catalog["registered_count"], 26)
        self.assertEqual(len(catalog["planned"]), 6)
        self.assertEqual([item["tool"] for item in catalog["beta_native"]], ["get_server_health"])
        self.assertEqual(
            Counter(item["status"] for item in catalog["tools"]),
            {"native": 8, "transitional": 10, "delegated": 4, "deprecated": 3},
        )


class BetaApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        settings = beta_settings(str(Path(cls.tempdir.name) / "audit.jsonl"))
        cls.client_context = TestClient(
            create_application(settings), follow_redirects=False
        )
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.tempdir.cleanup()

    def initialize(self, path: str):
        return self.client.post(
            path,
            content=json.dumps(INITIALIZE_REQUEST),
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )

    def test_beta_application_starts_and_health_check_succeeds(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_authenticated_mcp_forms_initialize_without_redirects(self):
        for path in (f"/{SECRET}/mcp", f"/{SECRET}/mcp/"):
            with self.subTest(path=path):
                response = self.initialize(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("location", response.headers)
                self.assertIn("protocolVersion", response.text)

    def test_unauthenticated_root_paths_are_rejected(self):
        for path in ("/mcp", "/mcp/"):
            with self.subTest(path=path):
                self.assertEqual(self.initialize(path).status_code, 404)

    def test_secret_is_redacted_from_audit_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit = AuditLogger(str(path), SECRET)
            audit.write({"event": "auth_failure", "path": f"/{SECRET}x/mcp"})
            contents = path.read_text()
            self.assertNotIn(SECRET, contents)
            self.assertIn("<access_secret>", contents)

    def test_startup_log_does_not_contain_secret(self):
        settings = beta_settings(str(Path(self.tempdir.name) / "startup-audit.jsonl"))
        output = io.StringIO()
        with patch("ha_mcp_engineering.application.load_settings", return_value=settings), patch(
            "ha_mcp_engineering.application.uvicorn.run"
        ), redirect_stderr(output):
            from ha_mcp_engineering.application import main

            main()
        self.assertNotIn(SECRET, output.getvalue())
        self.assertIn('"event": "server_starting"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
