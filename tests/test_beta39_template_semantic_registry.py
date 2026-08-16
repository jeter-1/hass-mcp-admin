from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ha_mcp_engineering.dependency.semantic_registry import (
    SEMANTIC_REGISTRY_CATEGORIES,
    SEMANTIC_REGISTRY_FILE,
    SEMANTIC_REGISTRY_MODEL,
    SUPPORTED_HOME_ASSISTANT_TEMPLATE_SOURCES,
    semantic_category,
    semantic_registry,
    semantic_registry_identity,
    semantic_registry_sha256,
)


ROOT = Path(__file__).resolve().parents[1]


class TemplateSemanticRegistryTests(unittest.TestCase):
    def test_exact_parser_and_supported_home_assistant_provenance(self):
        value = semantic_registry()
        self.assertEqual(SEMANTIC_REGISTRY_MODEL, value["model"])
        self.assertEqual("3.1.6", value["jinja"]["version"])
        observed = tuple(
            (
                item["tag"],
                item["commit"],
                item["exact_ci_image_digest"],
            )
            for item in value["home_assistant"]["supported_versions"]
        )
        self.assertEqual(SUPPORTED_HOME_ASSISTANT_TEMPLATE_SOURCES, observed)
        self.assertEqual(
            {
                "base": "jinja2.sandbox.ImmutableSandboxedEnvironment",
                "extensions": ["jinja2.ext.do", "jinja2.ext.loopcontrols"],
                "loader": None,
                "parse_only": True,
            },
            value["home_assistant"]["parser_environment"],
        )
        for source in (*value["home_assistant"]["supported_versions"], value["jinja"]):
            self.assertTrue(source["source_blobs"])
            self.assertTrue(
                all(
                    len(blob) == 40
                    and set(blob).issubset(set("0123456789abcdef"))
                    for blob in source["source_blobs"].values()
                )
            )

    def test_semantic_vocabulary_is_versioned_and_unknown_fails_closed(self):
        value = semantic_registry()
        for surface in ("globals", "filters", "tests", "attributes"):
            self.assertTrue(value["semantics"][surface])
            self.assertTrue(
                set(value["semantics"][surface].values()).issubset(
                    SEMANTIC_REGISTRY_CATEGORIES
                )
            )
        for name in (
            "states",
            "state_attr",
            "state_translated",
            "state_attr_translated",
            "is_state",
            "is_state_attr",
            "has_value",
            "expand",
            "closest",
            "distance",
        ):
            self.assertEqual("state_entity_access", semantic_category("globals", name))
        for name in (
            "area_entities",
            "device_entities",
            "floor_entities",
            "integration_entities",
            "label_entities",
        ):
            self.assertEqual("entity_set_producer", semantic_category("globals", name))
        self.assertEqual("dependency_neutral", semantic_category("globals", "now"))
        self.assertEqual("unknown", semantic_category("globals", "future_ha_callable"))
        self.assertEqual("unknown", semantic_category("future_surface", "states"))

    def test_registry_identity_binds_exact_checked_in_bytes(self):
        expected = hashlib.sha256(SEMANTIC_REGISTRY_FILE.read_bytes()).hexdigest()
        self.assertEqual(expected, semantic_registry_sha256())
        self.assertEqual(
            {
                "model": SEMANTIC_REGISTRY_MODEL,
                "sha256": expected,
                "jinja_version": "3.1.6",
            },
            semantic_registry_identity(),
        )

    def test_offline_registry_generation_is_byte_identical_twice(self):
        script = ROOT / "scripts" / "generate_template_semantic_registry.py"
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            for output in (first, second):
                completed = subprocess.run(
                    [sys.executable, str(script), "--output", str(output)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(SEMANTIC_REGISTRY_FILE.read_bytes(), first.read_bytes())

    def test_generation_uses_independent_reviewed_declaration_not_output(self):
        script = ROOT / "scripts" / "generate_template_semantic_registry.py"
        declaration = ROOT / "scripts" / "template_semantic_registry_source.json"
        self.assertNotEqual(declaration.resolve(), SEMANTIC_REGISTRY_FILE.resolve())
        self.assertEqual(
            "e0976d69feaac262dcc3090787a152f34265644dfe19ab7018c9c78d9d4be2bc",
            hashlib.sha256(declaration.read_bytes()).hexdigest(),
        )
        self.assertIn(
            "template_semantic_registry_source.json",
            script.read_text(encoding="utf-8"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "perturbed.json"
            output.write_text('{"tautological":true}\n', encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--output", str(output)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(SEMANTIC_REGISTRY_FILE.read_bytes(), output.read_bytes())


if __name__ == "__main__":
    unittest.main()
