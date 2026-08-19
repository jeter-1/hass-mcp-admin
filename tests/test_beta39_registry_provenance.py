"""B39-136-R3a: registry provenance is verified against independent evidence.

The corrected generator must never accept a Home Assistant or Jinja
path/blob attribution because the declaration says so.  Home Assistant
attributions are checked against immutable captured evidence, and Jinja
attributions are recomputed as git blob SHA-1 values from the installed
pinned package.  A wrong path, a wrong blob, or a path that does not exist
at a supported tag must fail generation rather than pass silently.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest

import jinja2

from ha_mcp_engineering.dependency.semantic_registry import (
    SEMANTIC_REGISTRY_FILE,
    semantic_registry,
)


GENERATOR = ROOT / "scripts" / "generate_template_semantic_registry.py"
DECLARATION = ROOT / "scripts" / "template_semantic_registry_source.json"
EVIDENCE = (
    ROOT / "docs" / "evidence" / "home-assistant-template-source-blobs.json"
)


def _git_blob_sha1(data: bytes) -> str:
    header = ("blob %d" % len(data)).encode("ascii") + bytes(1)
    return hashlib.sha1(header + data).hexdigest()


class RegistryProvenanceEvidenceTests(unittest.TestCase):
    """The captured evidence is a real, self-consistent witness."""

    def test_captured_evidence_covers_every_declared_path_and_version(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
        declared_paths = set(
            declaration["source_provenance"]["home_assistant_paths"]
        )
        captured = {item["tag"]: item for item in evidence["versions"]}
        for version in declaration["home_assistant"]["supported_versions"]:
            witness = captured[version["tag"]]
            self.assertEqual(version["commit"], witness["commit"])
            self.assertEqual(declared_paths, set(witness["paths"]))
            self.assertEqual(
                declared_paths, set(version["source_blobs"])
            )
            for path, blob in version["source_blobs"].items():
                self.assertEqual(witness["paths"][path]["blob"], blob)

    def test_evidence_records_the_nonexistent_path_that_was_attributed(self):
        # The reviewed head attributed semantics to a Home Assistant module
        # that exists at no supported tag.  Keeping that explicit is what
        # makes the "referenced source existence" check auditable.
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        absent = {
            path
            for item in evidence["versions"]
            for path in item["absent_paths"]
        }
        self.assertIn(
            "homeassistant/helpers/template/extensions/states.py", absent
        )
        declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
        for version in declaration["home_assistant"]["supported_versions"]:
            self.assertNotIn(
                "homeassistant/helpers/template/extensions/states.py",
                version["source_blobs"],
            )

    def test_no_two_paths_share_one_blob_at_one_tag(self):
        declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
        for version in declaration["home_assistant"]["supported_versions"]:
            blobs = version["source_blobs"]
            self.assertEqual(
                len(set(blobs.values())),
                len(blobs),
                f"duplicated blob attribution at {version['tag']}",
            )

    def test_jinja_blobs_recompute_from_the_installed_pinned_package(self):
        declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
        package = Path(jinja2.__file__).resolve().parent
        blobs = declaration["jinja"]["source_blobs"]
        self.assertTrue(blobs)
        for path, blob in blobs.items():
            module = package / path.split("/")[-1]
            self.assertTrue(module.is_file(), path)
            self.assertEqual(_git_blob_sha1(module.read_bytes()), blob, path)


class RegistryProvenanceFalsificationTests(unittest.TestCase):
    """A wrong attribution must fail generation, not pass silently."""

    def _generate(self, mutate) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "scripts").mkdir()
            (workspace / "docs" / "evidence").mkdir(parents=True)
            shutil.copy2(GENERATOR, workspace / "scripts" / GENERATOR.name)
            shutil.copy2(
                EVIDENCE, workspace / "docs" / "evidence" / EVIDENCE.name
            )
            declaration = json.loads(
                DECLARATION.read_text(encoding="utf-8")
            )
            mutate(declaration)
            (workspace / "scripts" / DECLARATION.name).write_bytes(
                (
                    json.dumps(
                        declaration,
                        ensure_ascii=True,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            return subprocess.run(
                [
                    sys.executable,
                    str(workspace / "scripts" / GENERATOR.name),
                    "--output",
                    str(workspace / "generated.json"),
                ],
                cwd=str(workspace),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_unmutated_declaration_still_generates_the_shipped_registry(self):
        completed = self._generate(lambda value: None)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_wrong_home_assistant_blob_fails_generation(self):
        def mutate(value):
            version = value["home_assistant"]["supported_versions"][0]
            version["source_blobs"]["homeassistant/core.py"] = "0" * 40

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("contradicts captured evidence", completed.stderr)

    def test_copied_blob_attribution_across_paths_fails_generation(self):
        # This is the exact defect shape found at the reviewed head: one
        # module's blob copied onto an unrelated module's path.
        def mutate(value):
            version = value["home_assistant"]["supported_versions"][0]
            version["source_blobs"][
                "homeassistant/components/script/__init__.py"
            ] = version["source_blobs"]["homeassistant/helpers/script.py"]

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("contradicts captured evidence", completed.stderr)

    def test_nonexistent_home_assistant_path_fails_generation(self):
        def mutate(value):
            path = "homeassistant/helpers/template/extensions/states.py"
            value["source_provenance"]["home_assistant_paths"].append(path)
            for version in value["home_assistant"]["supported_versions"]:
                version["source_blobs"][path] = "1" * 40

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("does not exist at Home Assistant tag", completed.stderr)

    def test_wrong_home_assistant_commit_fails_generation(self):
        def mutate(value):
            value["home_assistant"]["supported_versions"][0]["commit"] = (
                "a" * 40
            )

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("contradicts captured evidence", completed.stderr)

    def test_wrong_jinja_blob_fails_generation(self):
        def mutate(value):
            value["jinja"]["source_blobs"]["src/jinja2/filters.py"] = "b" * 40

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("contradicts the installed package", completed.stderr)

    def test_declared_filter_and_test_names_are_rejected(self):
        def mutate(value):
            value["semantics"]["filters"] = {"states": "state_entity_access"}

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("must not be declared", completed.stderr)

    def test_unclassified_standard_jinja_function_fails_generation(self):
        def mutate(value):
            value["semantics"]["jinja_filter_functions"].pop("do_default")

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("does not classify", completed.stderr)

    def test_home_assistant_override_of_a_standard_name_fails_generation(self):
        def mutate(value):
            value["semantics"]["home_assistant_filters"]["round"] = (
                "dependency_neutral"
            )

        completed = self._generate(mutate)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("redeclares standard", completed.stderr)


class RegistryRuntimeProvenanceTests(unittest.TestCase):
    def test_runtime_validation_recomputes_jinja_blobs(self):
        # The shipped registry is loaded and validated against the imported
        # parser, so a registry that describes a different Jinja build cannot
        # be used to reason about this one.
        value = semantic_registry()
        self.assertEqual("3.1.6", value["jinja"]["version"])
        self.assertTrue(SEMANTIC_REGISTRY_FILE.is_file())
        raw = SEMANTIC_REGISTRY_FILE.read_bytes()
        self.assertNotIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()
