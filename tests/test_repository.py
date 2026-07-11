from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_addon_config_is_valid_yaml_and_version_matches(self):
        config = yaml.safe_load((ROOT / "hass_mcp_admin" / "config.yaml").read_text())
        self.assertEqual(config["name"], "HA MCP Engineering Server")
        self.assertEqual(config["version"], "1.1.2")
        self.assertTrue(config["homeassistant_api"])

    def test_required_project_documents_exist(self):
        for filename in ("README.md", "ARCHITECTURE.md", "TOOL_AUDIT.md", "SOURCE_INFRASTRUCTURE_AUDIT.md"):
            self.assertTrue((ROOT / filename).is_file(), filename)


if __name__ == "__main__":
    unittest.main()


class ToolCatalogParityTests(unittest.TestCase):
    def test_uvicorn_access_log_is_disabled(self):
        server_source = (ROOT / "hass_mcp_admin" / "server.py").read_text()
        self.assertIn("access_log=False", server_source)

    def test_registered_tools_match_capability_catalog(self):
        import ast
        import importlib.util

        server_tree = ast.parse((ROOT / "hass_mcp_admin" / "server.py").read_text())
        registered = set()
        for node in server_tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    registered.add(node.name)

        module_path = ROOT / "hass_mcp_admin" / "metadata.py"
        spec = importlib.util.spec_from_file_location("metadata_parity", module_path)
        metadata = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(metadata)
        catalog = {item["tool"] for item in metadata.CAPABILITIES}
        self.assertEqual(registered, catalog)
