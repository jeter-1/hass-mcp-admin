"""Offline tests of the disposable-Core read capture lane (HAMCP-371).

No container, network, registry, image or Home Assistant is used.  Every
value is synthetic.
"""

from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_read_replay_capture_local as lane  # noqa: E402


def export_stream(members):
    """A synthetic ``docker export`` stream: path -> bytes, or None for a directory."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for path, body in members.items():
            info = tarfile.TarInfo(path)
            if body is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
    raw.seek(0)
    return raw


SITE = "usr/local/lib/python3.14/site-packages/"


class RequirementTests(unittest.TestCase):
    def test_names_compare_normalized(self):
        self.assertEqual(lane.normalized("ruamel.yaml"), "ruamel-yaml")
        self.assertEqual(lane.normalized("Voluptuous_OpenAPI"), "voluptuous-openapi")

    def test_minimum_and_exact_requirements(self):
        self.assertTrue(lane.satisfied("mcp>=1.24.0", "2.3.0"))
        self.assertTrue(lane.satisfied("mcp>=1.24.0", "1.24"))
        self.assertFalse(lane.satisfied("mcp>=1.24.0", "1.23.9"))
        self.assertFalse(lane.satisfied("mcp>=1.24.0", None))
        self.assertTrue(lane.satisfied("ha-mcp==9.2.0", "9.2.0"))
        self.assertFalse(lane.satisfied("ha-mcp==9.2.0", "9.2.1"))

    def test_an_unreviewed_requirement_form_is_refused(self):
        for text in ("mcp", "mcp<2", "mcp>=1.24.0,<2", "mcp @ https://synthetic.invalid/x"):
            with self.subTest(text=text), self.assertRaises(lane.Refusal) as raised:
                lane.parse_requirement(text)
            self.assertEqual(str(raised.exception), "component_requirement_form_not_reviewed")

    def test_a_core_without_two_requirements_makes_profile_b_infeasible(self):
        report = lane.requirement_report("9.2.0", {"mcp": "2.3.0"})
        self.assertEqual(report["missing"],
                         ["ruamel.yaml>=0.18.0", "voluptuous-openapi>=0.4.1"])
        self.assertIs(report["profile_b_feasible_without_a_route"], False)
        sources = {row["requirement"]: row["source"] for row in report["requirements"]}
        self.assertEqual(sources["ha-mcp==9.2.0"], "pinned ha-mcp image")
        self.assertEqual(sources["mcp>=1.24.0"], "core image")
        with self.assertRaises(lane.Refusal) as raised:
            lane.require_profile_b_feasible(report)
        self.assertEqual(str(raised.exception), "profile_b_core_requirement_missing")

    def test_a_core_with_every_other_requirement_is_feasible(self):
        report = lane.requirement_report(
            "9.1.2", {"mcp": "2.3.0", "ruamel-yaml": "0.19.1", "voluptuous-openapi": "0.4.1"})
        self.assertEqual(report["missing"], [])
        lane.require_profile_b_feasible(report)
        self.assertEqual(report["requirements"][-1]["requirement"], "ha-mcp==9.1.2")

    def test_the_package_pin_is_never_taken_from_core(self):
        # A Core that happened to carry the package does not change its source.
        report = lane.requirement_report("9.2.0", {"ha-mcp": "9.2.0"})
        self.assertEqual(report["requirements"][-1]["source"], "pinned ha-mcp image")


class ExportScanTests(unittest.TestCase):
    def test_names_versions_and_the_interpreter_are_read(self):
        found = lane.scan_export(export_stream({
            SITE: None,
            SITE + "mcp-2.3.0.dist-info/METADATA": b"synthetic",
            SITE + "ruamel_yaml-0.19.1.dist-info/METADATA": b"synthetic",
            SITE + "ruamel_yaml-0.19.1.dist-info/RECORD": b"synthetic",
            SITE + "not_a_distribution/METADATA": b"synthetic",
            "app/src/ha_mcp/__init__.py": b"12345",
            "app/src/ha_mcp/tools/x.py": b"123",
            "app/src/other/x.py": b"1",
        }))
        self.assertEqual(found["python"], ["3.14"])
        self.assertEqual(found["distributions"], {"mcp": "2.3.0", "ruamel-yaml": "0.19.1"})
        self.assertEqual(found["package_source"],
                         {"path": "/app/src/ha_mcp/", "files": 2, "bytes": 8})
        self.assertEqual(found["component_manifests_in_image"], [])

    def test_a_component_tree_inside_an_image_is_reported(self):
        found = lane.scan_export(export_stream({
            "config/custom_components/ha_mcp_tools/manifest.json": b"{}"}))
        self.assertEqual(found["component_manifests_in_image"],
                         ["/config/custom_components/ha_mcp_tools/manifest.json"])

    def test_nothing_is_extracted(self):
        # A member that points outside the stream is read as a name, never written.
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            link = tarfile.TarInfo(SITE + "evil-1.0.dist-info/METADATA")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/synthetic"
            archive.addfile(link)
        raw.seek(0)
        self.assertEqual(lane.scan_export(raw)["distributions"], {})


class BindingTests(unittest.TestCase):
    def test_the_core_image_is_the_reviewed_digest(self):
        bound = lane.core_binding()
        self.assertEqual(bound["version"], "2026.11.0")
        self.assertRegex(bound["image"],
                         r"^ghcr\.io/home-assistant/home-assistant@sha256:[0-9a-f]{64}$")

    def test_component_requirements_pin_the_version_under_capture(self):
        for version in lane.VERSIONS:
            self.assertEqual(lane.COMPONENT_REQUIREMENTS[version][-1], "ha-mcp==" + version)


if __name__ == "__main__":
    unittest.main()
