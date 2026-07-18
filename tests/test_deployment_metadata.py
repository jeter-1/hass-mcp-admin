import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


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
            VALIDATOR.is_newer_version("2.0.0-beta.7", "2.0.0-beta.6")
        )
        self.assertFalse(
            VALIDATOR.is_newer_version("2.0.0-beta.2", "2.0.0-beta.2")
        )

    def test_release_is_newer_than_prerelease(self):
        self.assertTrue(VALIDATOR.is_newer_version("2.0.0", "2.0.0-beta.14"))

    def test_rc_is_newer_than_beta(self):
        self.assertTrue(VALIDATOR.is_newer_version("2.0.0-rc.2", "2.0.0-rc.1"))

    def test_rc3a_development_version_orders_between_rc2_and_final_rc3(self):
        self.assertTrue(
            VALIDATOR.is_newer_version("2.0.0-rc2-dev1", "2.0.0-rc.2")
        )
        self.assertTrue(
            VALIDATOR.is_newer_version("2.0.0-rc.3", "2.0.0-rc2-dev1")
        )
        self.assertFalse(
            VALIDATOR.is_newer_version("2.0.0-rc.2.rc3a.1", "2.0.0-rc.2")
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev2", "2.0.0-rc2-dev1"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev2"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev3", "2.0.0-rc2-dev2"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev3"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev4", "2.0.0-rc2-dev3"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev4"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev5", "2.0.0-rc2-dev4"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev5"
            )
        )

    def test_version_comparison_uses_awesomeversion_25_8_0(self):
        requirements = (ROOT / "tests" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("awesomeversion==25.8.0", requirements)
        self.assertEqual(
            VALIDATOR.version_key("2.0.0-rc2-dev1").strategy.name,
            "SEMVER",
        )

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

    def test_rc_uses_exact_generic_registry_image_and_version(self):
        self.assertEqual(self.beta["version"], VALIDATOR.BETA_VERSION)
        self.assertEqual(self.beta["image"], VALIDATOR.BETA_IMAGE)
        self.assertEqual(
            f'{self.beta["image"]}:{self.beta["version"]}',
            f"{VALIDATOR.BETA_IMAGE}:{VALIDATOR.BETA_VERSION}",
        )

    def test_registry_image_is_required_and_cannot_be_arch_templated(self):
        for image in (
            None,
            "ghcr.io/jeter-1/hass-mcp-engineering-beta-{arch}",
            "ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2",
            "ghcr.io/jeter-1/hass-mcp-admin",
        ):
            with self.subTest(image=image):
                beta = copy.deepcopy(self.beta)
                if image is None:
                    beta.pop("image", None)
                else:
                    beta["image"] = image
                with self.assertRaises(VALIDATOR.MetadataValidationError):
                    self.validate(beta=beta)

    def test_rc_version_is_pinned_by_metadata_validation(self):
        beta = copy.deepcopy(self.beta)
        beta["version"] = "2.0.0-rc.3"
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            self.validate(beta=beta)

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

    def test_external_approval_ingress_must_be_admin_only_and_not_host_mapped(self):
        self.assertTrue(self.beta["ingress"])
        self.assertTrue(self.beta["panel_admin"])
        self.assertEqual(self.beta["ingress_port"], 8110)
        self.assertNotIn("8110/tcp", self.beta["ports"])
        for field, value in (("ingress", False), ("panel_admin", False), ("ingress_port", 8100)):
            with self.subTest(field=field):
                beta = copy.deepcopy(self.beta)
                beta[field] = value
                with self.assertRaises(VALIDATOR.MetadataValidationError):
                    self.validate(beta=beta)

    def test_unnecessary_auth_api_is_rejected(self):
        beta = copy.deepcopy(self.beta)
        beta["auth_api"] = True
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

    def test_production_has_no_registry_image(self):
        self.assertNotIn("image", self.production)
        production = copy.deepcopy(self.production)
        production["image"] = "ghcr.io/jeter-1/hass-mcp-admin"
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

    def test_staged_release_allows_feature_pr_to_keep_advertised_version(self):
        declaration = ROOT / VALIDATOR.NEXT_VERSION_PATH
        if not declaration.exists():
            self.skipTest("staged declaration is consumed on the promoted release commit")
        current = str(self.beta["version"])
        report = VALIDATOR.validate_repository(
            ROOT,
            base_ref="origin/main",
            deployed_version=current,
            paths={"hass_mcp_engineering_beta/config.yaml"},
        )
        self.assertEqual(report.beta_version, VALIDATOR.BETA_VERSION)
        self.assertEqual(
            report.staged_release_version,
            declaration.read_text(encoding="utf-8").strip(),
        )

    @patch.object(VALIDATOR, "staged_release_version", return_value=None)
    def test_unreleased_same_version_requires_explicit_integrity_check(self, _staged):
        current = str(self.beta["version"])
        integrity_check = Mock()
        report = VALIDATOR.validate_repository(
            ROOT,
            base_ref="origin/main",
            deployed_version=current,
            paths={"hass_mcp_engineering_beta/ha_mcp_engineering/providers/routing.py"},
            unreleased_integrity_check=integrity_check,
        )
        integrity_check.assert_called_once_with(ROOT, current)
        self.assertTrue(report.same_version_correction)

    @patch.object(VALIDATOR, "staged_release_version", return_value=None)
    def test_unreleased_same_version_check_failure_is_fail_closed(self, _staged):
        current = str(self.beta["version"])
        integrity_check = Mock(
            side_effect=VALIDATOR.MetadataValidationError("release already exists")
        )
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.validate_repository(
                ROOT,
                base_ref="origin/main",
                deployed_version=current,
                paths={"hass_mcp_engineering_beta/config.yaml"},
                unreleased_integrity_check=integrity_check,
            )

    @patch.object(VALIDATOR, "staged_release_version", return_value=None)
    def test_unreleased_check_cannot_allow_an_older_version(self, _staged):
        integrity_check = Mock()
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.validate_repository(
                ROOT,
                base_ref="origin/main",
                deployed_version="2.0.0-rc.3",
                paths={"hass_mcp_engineering_beta/config.yaml"},
                unreleased_integrity_check=integrity_check,
            )
        integrity_check.assert_not_called()

    def test_beta_release_passes_with_expected_version(self):
        report = VALIDATOR.validate_repository(
            ROOT,
            base_ref="origin/main",
            expected_version=VALIDATOR.BETA_VERSION,
            deployed_version="2.0.0-beta.8",
            paths={"hass_mcp_engineering_beta/config.yaml"},
        )
        self.assertEqual(report.production_version, "1.1.2")
        self.assertEqual(report.beta_version, VALIDATOR.BETA_VERSION)


class UnreleasedRcIntegrityTests(unittest.TestCase):
    @staticmethod
    def result(returncode, stdout="", stderr=""):
        return VALIDATOR.subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch.object(VALIDATOR.subprocess, "run")
    def test_exact_tag_and_image_absence_are_both_required(self, run):
        run.side_effect = [
            self.result(2),
            self.result(
                1,
                stderr=(
                    "ERROR: ghcr.io/jeter-1/hass-mcp-engineering-beta:"
                    f"{VALIDATOR.BETA_VERSION}: not found"
                ),
            ),
        ]
        VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--tags",
                "origin",
                f"refs/tags/v{VALIDATOR.BETA_VERSION}",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                f"{VALIDATOR.BETA_IMAGE}:{VALIDATOR.BETA_VERSION}",
            ],
        )
        docker_env = run.call_args_list[1].kwargs["env"]
        self.assertIn("hamcp-rc-integrity-", docker_env["DOCKER_CONFIG"])

    @patch.object(VALIDATOR.subprocess, "run")
    def test_existing_release_tag_fails_before_registry_inspection(self, run):
        run.return_value = self.result(0, stdout="tag exists")
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)
        self.assertEqual(run.call_count, 1)

    @patch.dict(
        VALIDATOR.os.environ,
        {"HAMCP_GHCR_READ_TOKEN": "test-token", "GITHUB_ACTOR": "test-actor"},
        clear=False,
    )
    @patch.object(VALIDATOR.subprocess, "run")
    def test_ci_authenticates_read_only_registry_check_without_token_in_args(self, run):
        run.side_effect = [
            self.result(2),
            self.result(0, stdout="Login Succeeded"),
            self.result(1, stderr="manifest unknown"),
        ]
        VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)
        self.assertEqual(run.call_count, 3)
        login = run.call_args_list[1]
        self.assertEqual(
            login.args[0],
            [
                "docker",
                "login",
                "ghcr.io",
                "--username",
                "test-actor",
                "--password-stdin",
            ],
        )
        self.assertEqual(login.kwargs["input"], "test-token")
        self.assertNotIn("test-token", login.args[0])

    @patch.dict(
        VALIDATOR.os.environ,
        {"HAMCP_GHCR_READ_TOKEN": "test-token", "GITHUB_ACTOR": ""},
        clear=False,
    )
    @patch.object(VALIDATOR.subprocess, "run")
    def test_incomplete_registry_auth_configuration_fails_closed(self, run):
        run.return_value = self.result(2)
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)
        self.assertEqual(run.call_count, 1)

    @patch.dict(
        VALIDATOR.os.environ,
        {"HAMCP_GHCR_READ_TOKEN": "", "GITHUB_ACTOR": "test-actor"},
        clear=False,
    )
    @patch.object(VALIDATOR.subprocess, "run")
    def test_global_github_actor_without_scoped_token_uses_anonymous_probe(self, run):
        run.side_effect = [
            self.result(2),
            self.result(1, stderr="manifest unknown"),
        ]
        VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)
        self.assertEqual(run.call_count, 2)

    @patch.object(VALIDATOR.subprocess, "run")
    def test_existing_version_image_is_rejected(self, run):
        run.side_effect = [self.result(2), self.result(0, stdout="manifest")]
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)

    @patch.object(VALIDATOR.subprocess, "run")
    def test_ambiguous_tag_or_registry_failure_is_rejected(self, run):
        for side_effect in (
            [self.result(128, stderr="network unavailable")],
            [self.result(2), self.result(1, stderr="unauthorized")],
            [self.result(2), self.result(1, stderr="ERROR: credential: not found")],
        ):
            with self.subTest(side_effect=side_effect):
                run.reset_mock(side_effect=True)
                run.side_effect = side_effect
                with self.assertRaises(VALIDATOR.MetadataValidationError):
                    VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)

    @patch.object(VALIDATOR.subprocess, "run")
    def test_remote_check_timeout_is_rejected(self, run):
        run.side_effect = VALIDATOR.subprocess.TimeoutExpired(
            cmd=["git", "ls-remote"],
            timeout=VALIDATOR.EXTERNAL_CHECK_TIMEOUT_SECONDS,
        )
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.assert_unreleased_rc(ROOT, VALIDATOR.BETA_VERSION)


class CIWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

    def test_version_bump_metadata_gate_precedes_docker_setup(self):
        buildx = self.workflow.index("Set up Docker Buildx")
        metadata = self.workflow.index("Validate deployment metadata")
        self.assertLess(metadata, buildx)
        self.assertNotIn("--allow-unreleased-same-version", self.workflow)

    def test_pr_ci_has_no_package_permission_or_registry_token(self):
        self.assertNotIn("packages:", self.workflow)
        self.assertNotIn("HAMCP_GHCR_READ_TOKEN", self.workflow)

    def test_ci_still_has_no_registry_login_or_push(self):
        self.assertNotIn("docker/login-action", self.workflow)
        self.assertNotIn("push: true", self.workflow)


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
            "PythonExecutable",
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
