import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "hass_mcp_admin" / "metadata.py"
spec = importlib.util.spec_from_file_location("metadata", MODULE_PATH)
metadata = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(metadata)


class MetadataTests(unittest.TestCase):
    def test_server_metadata_has_stable_identity(self):
        result = metadata.build_server_metadata(
            ha_url="http://supervisor/core",
            runtime_mode="home_assistant_addon",
            ha_connection={"checked": False, "status": "not_checked"},
        )
        self.assertEqual(result["server"]["id"], "hass-mcp-engineering")
        self.assertEqual(result["server"]["version"], "1.1.2")
        self.assertEqual(result["tool_count"], 25)

    def test_capability_catalog_contains_every_tool_once(self):
        result = metadata.build_capability_catalog()
        names = [item["tool"] for item in result["tools"]]
        self.assertEqual(len(names), 25)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("server_info", names)
        self.assertIn("list_capabilities", names)

    def test_capability_filters(self):
        native = metadata.build_capability_catalog(status="native")
        self.assertTrue(native["tools"])
        self.assertTrue(all(item["status"] == "native" for item in native["tools"]))

        foundation = metadata.build_capability_catalog(category="foundation")
        self.assertEqual(
            {item["tool"] for item in foundation["tools"]},
            {"server_info", "list_capabilities"},
        )


if __name__ == "__main__":
    unittest.main()
