"""HAMCP-265 guard: the read-coverage matrix stays in step with current source.

The matrix must change whenever the tool catalog, the reader purposes, the
compiled Core profiles or the provider registry change, and every item of
those inventories must be mapped to a data source (or explicitly marked as
reading none). Citations must still resolve to their anchors.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import read_coverage_inventory as inventory  # noqa: E402

from ha_mcp_engineering import capabilities  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles  # noqa: E402
from ha_mcp_engineering.providers import routing  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway  # noqa: E402


GAP_ID = re.compile(r"^RCG-\d{3}$")
GAP_TOKEN = re.compile(r"\bRCG-\d{3}\b")
DELEGATED_ADAPTER_TOOLS = frozenset({
    "ha_config_get_yaml",
    "ha_config_get_dashboard",
    "ha_get_app",
    "ha_get_blueprint",
    "ha_get_integration",
    "ha_get_system_health",
})


def _matrix_tools(matrix):
    tools = set()
    for source in matrix["sources"]:
        for kind in ("read_paths", "write_paths"):
            tools.update(item["tool"] for item in source[kind])
    tools.update(item["tool"] for item in matrix["non_source_tools"])
    return tools


def _delegated_read_names():
    names = set(DELEGATED_ADAPTER_TOOLS)
    for path in inventory.RUNTIME.glob("upstream_tool_policy*.json"):
        policy = json.loads(path.read_text(encoding="utf-8"))
        names.update(
            tool["exposed_name"]
            for tool in policy["tools"]
            if tool["classification"] in {"automatic_read", "held_for_canary"}
        )
    return names


class ReadCoverageMatrixGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = inventory.load_matrix()
        cls.live = inventory.compute_inventory()

    def test_inventory_fingerprints_match_the_matrix(self):
        for section in inventory.SECTIONS:
            with self.subTest(section=section):
                recorded = self.matrix["inventory"][section]
                live = self.live[section]
                self.assertEqual(
                    recorded["fingerprint"],
                    inventory.section_fingerprint(live),
                    inventory.describe_drift(section, recorded["items"], live),
                )
                self.assertEqual(recorded["fingerprint"], inventory.section_fingerprint(recorded["items"]))

    def test_every_catalog_tool_is_mapped(self):
        mapped = _matrix_tools(self.matrix)
        catalog = {e["tool"] for e in capabilities.CAPABILITIES + capabilities.BETA_NATIVE_CAPABILITIES}
        self.assertEqual(sorted(catalog - mapped), [], "catalog tools missing from the matrix")

    def test_every_delegated_read_is_mapped(self):
        missing = sorted(_delegated_read_names() - _matrix_tools(self.matrix))
        self.assertEqual(missing, [], "reviewed ha-mcp read tools missing from the matrix")

    def test_matrix_names_no_unknown_tool(self):
        known = (
            {e["tool"] for e in capabilities.CAPABILITIES + capabilities.BETA_NATIVE_CAPABILITIES}
            | _delegated_read_names()
        )
        for tool in _matrix_tools(self.matrix):
            if tool.startswith("internal:"):
                continue
            self.assertIn(tool, known, f"matrix names a tool that is not in the catalog: {tool}")

    def test_every_core_profile_is_indexed(self):
        index = self.matrix["core_profile_index"]
        live = {p.capability_id for p in profiles.CORE_RUNTIME_CAPABILITY_PROFILES}
        self.assertEqual(sorted(index), sorted(live))
        probe = {p.capability_id for p in profiles.CORE_CAPABILITY_PROFILES}
        source_ids = {s["id"] for s in self.matrix["sources"]}
        for capability_id, entry in index.items():
            with self.subTest(profile=capability_id):
                self.assertEqual(entry["probe_profile"], capability_id in probe)
                self.assertTrue(entry["used_by"])
                self.assertLessEqual(set(entry["used_by"]), source_ids | {"engineering_internal"})

    def test_every_reader_purpose_is_indexed(self):
        index = self.matrix["reader_purpose_index"]
        live = {key for key in self.live["reader_purposes"]}
        self.assertEqual(sorted(index), sorted(live))
        bindings = json.dumps(self.matrix["sources"])
        for key in index:
            with self.subTest(reader=key):
                self.assertIn(key, bindings, "reader purpose is not cited by any read path")

    def test_entries_are_well_formed(self):
        gap_ids = []
        for source in self.matrix["sources"]:
            with self.subTest(source=source["id"]):
                self.assertIn(source["completeness"], inventory.COMPLETENESS)
                self.assertTrue(source["completeness_reason"])
                for path in source["read_paths"] + source["write_paths"]:
                    self.assertTrue(path["refs"], f"{source['id']}: {path['tool']} has no citation")
                for path in source["read_paths"]:
                    self.assertIn(path["completeness"], inventory.COMPLETENESS)
                for gap in source["gaps"]:
                    self.assertRegex(gap["id"], GAP_ID)
                    self.assertIn(gap["requirement_type"], inventory.REQUIREMENT_TYPES)
                    self.assertTrue(gap["requirement"])
                    gap_ids.append(gap["id"])
        self.assertEqual(len(gap_ids), len(set(gap_ids)), "duplicate gap id")
        source_ids = [s["id"] for s in self.matrix["sources"]]
        self.assertEqual(len(source_ids), len(set(source_ids)), "duplicate source id")

    def test_citations_resolve_to_their_anchors(self):
        for owner, ref in inventory.iter_refs(self.matrix):
            with self.subTest(owner=owner, path=ref["path"], anchor=ref["anchor"]):
                target = inventory.REPO / ref["path"]
                self.assertTrue(target.is_file(), f"cited file is missing: {ref['path']}")
                lines = target.read_text(encoding="utf-8").splitlines()
                self.assertTrue(1 <= ref["line"] <= len(lines))
                self.assertTrue(
                    any(ref["anchor"] in line for line in lines),
                    f"anchor no longer in {ref['path']}: {ref['anchor']!r}",
                )

    def test_generated_markdown_matches_the_matrix(self):
        text = inventory.MATRIX_MD.read_text(encoding="utf-8")
        self.assertEqual(
            inventory.generated_block(text),
            inventory.render_markdown_block(self.matrix),
            "docs/READ_COVERAGE_MATRIX.md is stale; run "
            "'python tests/read_coverage_inventory.py --write'",
        )

    def test_readiness_map_cites_only_known_gaps(self):
        known = {g["id"] for s in self.matrix["sources"] for g in s["gaps"]}
        cited = set(GAP_TOKEN.findall(inventory.READINESS_MD.read_text(encoding="utf-8")))
        self.assertTrue(cited)
        self.assertEqual(sorted(cited - known), [])


class ReadCoverageDriftDetectionTests(unittest.TestCase):
    """Negative coverage: each inventory change is detected and named."""

    def _drift(self, section):
        recorded = inventory.load_matrix()["inventory"][section]
        live = inventory.compute_inventory()[section]
        self.assertNotEqual(recorded["fingerprint"], inventory.section_fingerprint(live))
        return inventory.describe_drift(section, recorded["items"], live)

    def test_added_catalog_tool_is_detected(self):
        fake = {"tool": "synthetic_reader_tool", "category": "evidence", "status": "beta_native", "risk": "read"}
        with patch.object(capabilities, "BETA_NATIVE_CAPABILITIES", capabilities.BETA_NATIVE_CAPABILITIES + (fake,)):
            message = self._drift("tool_catalog")
        self.assertIn("added: native:synthetic_reader_tool", message)

    def test_changed_catalog_risk_is_detected(self):
        first = dict(capabilities.CAPABILITIES[0], risk="behavioral_write")
        with patch.object(capabilities, "CAPABILITIES", (first,) + capabilities.CAPABILITIES[1:]):
            message = self._drift("tool_catalog")
        self.assertIn("changed: native:" + first["tool"], message)

    def test_added_reader_purpose_is_detected(self):
        purposes = dict(gateway.YAML_READER_PURPOSES)
        purposes["synthetic_purpose"] = (frozenset({"template"}), "synthetic_yaml_read")
        with patch.object(gateway, "YAML_READER_PURPOSES", purposes):
            message = self._drift("reader_purposes")
        self.assertIn("added: yaml_reader:synthetic_purpose", message)

    def test_widened_reader_paths_are_detected(self):
        purposes = dict(gateway.YAML_READER_PURPOSES)
        paths, event = purposes["recorder_settings"]
        purposes["recorder_settings"] = (frozenset(paths | {"template"}), event)
        with patch.object(gateway, "YAML_READER_PURPOSES", purposes):
            message = self._drift("reader_purposes")
        self.assertIn("changed: yaml_reader:recorder_settings", message)

    def test_removed_core_profile_is_detected(self):
        removed = profiles.CORE_RUNTIME_CAPABILITY_PROFILES[-1]
        with patch.object(profiles, "CORE_RUNTIME_CAPABILITY_PROFILES", profiles.CORE_RUNTIME_CAPABILITY_PROFILES[:-1]):
            message = self._drift("core_profiles")
        self.assertIn("removed: " + removed.capability_id, message)

    def test_changed_direct_read_policy_is_detected(self):
        policies = dict(routing.DIRECT_HA_READ_POLICIES)
        policies["get_entity"] = dict(policies["get_entity"], access="write")
        with patch.object(routing, "DIRECT_HA_READ_POLICIES", policies):
            message = self._drift("provider_registry")
        self.assertIn("changed: direct_read_policy:get_entity", message)

    def test_unchanged_source_reports_no_drift(self):
        self.assertEqual(inventory.check(), 0)


if __name__ == "__main__":
    unittest.main()
