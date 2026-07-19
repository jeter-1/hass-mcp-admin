import importlib.util
import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rc2dev8-exact-image-bake.yml"
SCRIPT = ROOT / "scripts" / "rc2dev8_exact_image_bake.py"


def load_script():
    spec = importlib.util.spec_from_file_location("rc2dev8_exact_image_bake", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ExactImageWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)

    def test_workflow_is_manual_only_and_read_only(self):
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("pull_request:", self.source)
        self.assertNotIn("\n  push:", self.source)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("packages: write", self.source)
        self.assertNotIn("docker/login-action", self.source)
        self.assertNotIn("push: true", self.source)
        self.assertNotIn("docker build ", self.source)
        self.assertNotIn("docker buildx build", self.source)

    def test_workflow_pins_exact_images_and_platform_manifests(self):
        self.assertIn(
            "sha256:e1c2edf06f03e12ca42e1c90f43aa5c9e5b226b17acb69d302c1f483ff789a4a",
            self.source,
        )
        self.assertIn(
            "sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b",
            self.source,
        )
        for digest in (
            "sha256:34e2a4923fc51753ba47024536b7c36e474df14b4c0b9f975ea72598e706f0e3",
            "sha256:f50b589c460ae24e90f322297b00edc0ba12295cb08dce5243a774aea89bcb5f",
            "sha256:e7d0f1c0d0433b9a6d4ee16cfdb648b38026399837ccdcfa20626f2e5946c775",
        ):
            self.assertIn(digest, self.source)
        self.assertIn("c146c4378a221a34d66ee465772ecac09aca4899", self.source)
        self.assertIn("2.0.0-rc2-dev8", self.source)
        self.assertIn("2026-07-19T13:14:16Z", self.source)

    def test_workflow_always_sanitizes_uploads_and_cleans_up(self):
        self.assertGreaterEqual(self.source.count("if: always()"), 3)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", self.source)
        self.assertIn("retention-days: 7", self.source)
        self.assertIn("docker rm -f", self.source)
        self.assertIn("docker network rm", self.source)
        self.assertIn(
            'sudo rm -rf "$RUNNER_TEMP"/rc2dev8-exact-image-*',
            self.source,
        )
        self.assertIn("RC2DEV8_DISPOSABLE_EXACT_IMAGE", self.source)
        self.assertIn("RC2DEV8_ATTESTATION_COUNT", self.source)


class ExactImageHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script()
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_harness_has_no_external_target_arguments(self):
        parser_args = self.module.parse_args([])
        self.assertFalse(parser_args.acknowledge_disposable_exact_image)
        self.assertFalse(hasattr(parser_args, "ha_url"))
        self.assertFalse(hasattr(parser_args, "mcp_url"))
        self.assertNotIn("--ha-url", self.source)
        self.assertNotIn("--mcp-url", self.source)

    def test_harness_pins_expected_runtime_and_ttls(self):
        self.assertEqual(self.module.ENGINEERING_VERSION, "2.0.0-rc2-dev8")
        self.assertEqual(
            self.module.ENGINEERING_REVISION,
            "c146c4378a221a34d66ee465772ecac09aca4899",
        )
        self.assertEqual(self.module.SOFT_TTL_SECONDS, 5.0)
        self.assertEqual(self.module.HARD_TTL_SECONDS, 30.0)
        self.assertIn("@sha256:", self.module.ENGINEERING_IMAGE)
        self.assertIn("2026.7.2@sha256:", self.module.HOME_ASSISTANT_IMAGE)
        self.assertIn("RC2DEV8_PLATFORM_CONFIG_DIGEST", self.source)

    def test_container_owned_bind_mounts_cannot_override_pass_result(self):
        self.assertIn("ignore_cleanup_errors=True", self.source)

    def test_sanitized_evidence_rejects_credential_fields_and_values(self):
        with self.assertRaises(self.module.BakeFailure):
            self.module.assert_sanitized({"access_token": "synthetic"}, ())
        with self.assertRaises(self.module.BakeFailure):
            self.module.assert_sanitized({"value": "prefix-secret-suffix"}, ("secret",))
        self.module.assert_sanitized(
            {"result": "PASS", "image": "ghcr.io/example/image@sha256:abc"},
            ("not-present",),
        )

    def test_fixture_options_are_secret_free_and_disable_prewarm(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.module.write_engineering_options(path)
            options = json.loads((path / "options.json").read_text(encoding="utf-8"))
        self.assertNotIn("access_secret", options)
        self.assertFalse(options["prewarm_enabled"])
        self.assertEqual(options["dependency_index_soft_ttl_seconds"], 5.0)
        self.assertEqual(options["dependency_index_hard_ttl_seconds"], 30.0)

    def test_home_assistant_fixture_is_synthetic_and_bounded(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.module.write_fixture_configuration(path)
            configuration = (path / "configuration.yaml").read_text(encoding="utf-8")
            automations = (path / "automations.yaml").read_text(encoding="utf-8")
        self.assertIn("input_boolean:\n", configuration)
        self.assertIn(self.module.TARGET_ENTITY, automations)
        self.assertIn("rc2dev8_exact_image_fixture", automations)
        self.assertNotIn("http://", configuration + automations)


if __name__ == "__main__":
    unittest.main()
