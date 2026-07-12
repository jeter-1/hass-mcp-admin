import copy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_addon_metadata", ROOT / "scripts" / "validate_addon_metadata.py"
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class VersionComparisonTests(unittest.TestCase):
    def test_beta_prerelease_increments_are_ordered(self):
        self.assertTrue(
            VALIDATOR.is_newer_version("2.0.0-beta.3", "2.0.0-beta.2")
        )
        self.assertFalse(
            VALIDATOR.is_newer_version("2.0.0-beta.2", "2.0.0-beta.2")
        )

    def test_release_is_newer_than_prerelease(self):
        self.assertTrue(VALIDATOR.is_newer_version("2.0.0", "2.0.0-beta.9"))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.version_key("beta-latest")


class AddonMetadataValidationTests(unittest.TestCase):
    def setUp(self):
        self.production = VALIDATOR.read_yaml(ROOT / "hass_mcp_admin" / "config.yaml")
        self.beta = VALIDATOR.read_yaml(
            ROOT / "hass_mcp_engineering_beta" / "config.yaml"
        )

    def validate(self, production=None, beta=None, minimum=24):
        VALIDATOR.validate_config_pair(
            production or self.production,
            beta or self.beta,
            minimum_secret_length=minimum,
        )

    def test_repository_metadata_is_valid(self):
        self.validate()

    def test_slug_collision_is_rejected(self):
        beta = copy.deepcopy(self.beta)
        beta["slug"] = self.production["slug"]
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

    def test_port_collision_is_rejected(self):
        beta = copy.deepcopy(self.beta)
        beta["ports"] = {"8099/tcp": 8099}
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

    def test_access_secret_must_be_required(self):
        beta = copy.deepcopy(self.beta)
        beta["schema"]["access_secret"] = "str?"
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

    def test_access_secret_cannot_be_stored_in_metadata(self):
        beta = copy.deepcopy(self.beta)
        beta["options"]["access_secret"] = "not-a-real-secret-value-placeholder"
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

    def test_access_secret_minimum_cannot_change(self):
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(minimum=23)

    def test_beta_options_require_schema_entries(self):
        beta = copy.deepcopy(self.beta)
        beta["options"]["unvalidated_option"] = True
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

    def test_production_version_is_fixed(self):
        production = copy.deepcopy(self.production)
        production["version"] = "1.1.3"
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(production=production)

    def test_production_changes_are_rejected(self):
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.validate_repository(
                ROOT,
                base_ref="origin/main",
                deployed_version="2.0.0-beta.2",
                paths={"hass_mcp_admin/config.yaml"},
            )

    def test_beta_version_must_advance(self):
        current = str(self.beta["version"])
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.validate_repository(
                ROOT,
                base_ref="origin/main",
                deployed_version=current,
                paths={"hass_mcp_engineering_beta/config.yaml"},
            )

    def test_beta_release_passes_with_expected_version(self):
        report = VALIDATOR.validate_repository(
            ROOT,
            base_ref="origin/main",
            expected_version="2.0.0-beta.3",
            deployed_version="2.0.0-beta.2",
            paths={"hass_mcp_engineering_beta/config.yaml"},
        )
        self.assertEqual(report.production_version, "1.1.2")
        self.assertEqual(report.beta_version, "2.0.0-beta.3")


class DeploymentScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "scripts" / "deploy-beta.ps1").read_text(encoding="utf-8")

    def test_required_parameters_are_exposed(self):
        for parameter in (
            "SkipTests",
            "FullTests",
            "SkipDockerBuild",
            "HealthHost",
            "HealthTimeoutSeconds",
            "ExpectedVersion",
            "DryRun",
        ):
            self.assertIn(f"${parameter}", self.script)

    def test_script_targets_beta_port_and_image_only(self):
        self.assertIn("$BetaPort = 8100", self.script)
        self.assertIn("./hass_mcp_engineering_beta", self.script)
        self.assertNotIn("docker build -t hass-mcp-admin", self.script)

    def test_script_has_no_secret_or_webhook_parameter(self):
        self.assertNotIn("[string]$AccessSecret", self.script)
        self.assertNotIn("[string]$Webhook", self.script)

    def test_test_output_is_suppressed_to_protect_authenticated_paths(self):
        self.assertIn('"Run complete test suite"', self.script)
        self.assertGreaterEqual(self.script.count("-SuppressOutput"), 3)


if __name__ == "__main__":
    unittest.main()
