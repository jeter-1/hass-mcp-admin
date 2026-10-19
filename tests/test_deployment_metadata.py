import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
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
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev6", "2.0.0-rc2-dev5"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev6"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev7", "2.0.0-rc2-dev6"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev7"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev8", "2.0.0-rc2-dev7"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev8"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev9", "2.0.0-rc2-dev8"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev9"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev10", "2.0.0-rc2-dev9"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev10"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev12", "2.0.0-rc2-dev10"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev12"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev13", "2.0.0-rc2-dev12"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev13"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev14", "2.0.0-rc2-dev13"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev14"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev15", "2.0.0-rc2-dev14"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc2-dev16", "2.0.0-rc2-dev15"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0-rc.3", "2.0.0-rc2-dev16"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.0", "2.0.0-rc.3"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.1-rc1-dev1", "2.0.0"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.1-rc1-dev2", "2.0.1-rc1-dev1"
            )
        )
        self.assertTrue(
            VALIDATOR.is_newer_version(
                "2.0.1", "2.0.1-rc1-dev2"
            )
        )

    def test_version_comparison_uses_awesomeversion_25_8_0(self):
        requirements = (ROOT / "tests" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("awesomeversion==25.8.0", requirements)
        self.assertIn("pip-audit==2.10.1", requirements)
        self.assertEqual(
            VALIDATOR.version_key("2.0.0-rc2-dev1").strategy.name,
            "SEMVER",
        )

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.version_key("beta-latest")


class AddonMetadataValidationTests(unittest.TestCase):
    def setUp(self):
        self.production = VALIDATOR.read_yaml(ROOT / "hass_mcp_admin" / "retired-addon-config.yaml")
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

    def test_inbound_policy_option_schemas_are_required_and_exact(self):
        for name in ("mcp_allowed_hosts", "mcp_allowed_origins"):
            self.assertEqual(self.beta["schema"][name], ["str"])
            for section in ("options", "schema"):
                beta = copy.deepcopy(self.beta)
                beta[section].pop(name)
                with self.subTest(name=name, missing=section), self.assertRaises(VALIDATOR.MetadataValidationError):
                    self.validate(beta=beta)
            beta = copy.deepcopy(self.beta)
            beta["schema"][name] = "str"
            with self.subTest(name=name, malformed=True), self.assertRaises(VALIDATOR.MetadataValidationError):
                self.validate(beta=beta)

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

    def test_approval_notification_service_defaults_to_disabled(self):
        self.assertEqual(
            self.beta["options"]["approval_notification_service"], ""
        )
        self.assertEqual(
            self.beta["schema"]["approval_notification_service"], "str"
        )
        beta = copy.deepcopy(self.beta)
        beta["options"]["approval_notification_service"] = (
            "notify.mobile_app_synthetic_phone"
        )
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
        for path in ("hass_mcp_admin/server.py", "hass_mcp_admin/Dockerfile"):
            with self.subTest(path=path), self.assertRaisesRegex(
                VALIDATOR.MetadataValidationError, "Production add-on files were modified"
            ):
                VALIDATOR.validate_repository(
                    ROOT,
                    base_ref="origin/main",
                    deployed_version="2.0.0-beta.2",
                    paths={path},
                )

    def test_retired_stable_v1_is_not_discoverable(self):
        self.assertEqual(VALIDATOR.discoverable_retired_configs(ROOT), [])
        self.assertTrue((ROOT / VALIDATOR.RETIRED_CONFIG_PATH).is_file())

    def test_reintroduced_stable_v1_config_is_rejected(self):
        for name in ("config.yaml", "config.yml", "config.json", "nested/config.yaml"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                config = Path(temp, "hass_mcp_admin", name)
                config.parent.mkdir(parents=True)
                config.write_text("slug: hass_mcp_admin\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    VALIDATOR.MetadataValidationError, "must not be discoverable"
                ):
                    VALIDATOR.validate_repository(Path(temp), base_ref="HEAD", paths=set())

    def test_retirement_transition_is_one_time_and_exact(self):
        allowed = VALIDATOR.is_stable_retirement_transition
        full = set(VALIDATOR.RETIREMENT_TRANSITION_PATHS)
        renamed = {"hass_mcp_admin/retired-addon-config.yaml", "hass_mcp_admin/README.md"}
        for paths in (full, renamed):
            self.assertTrue(allowed(
                paths, base_discoverable=True, head_discoverable=False, content_preserved=True
            ))
        refused = (
            (full | {"hass_mcp_admin/server.py"}, True, False, True),
            ({"hass_mcp_admin/README.md"}, False, False, True),
            (full, True, True, True),
            (set(), True, False, True),
            (full, True, False, False),
        )
        for paths, base, head, content in refused:
            with self.subTest(paths=sorted(paths), base=base, head=head, content=content):
                self.assertFalse(allowed(
                    paths, base_discoverable=base, head_discoverable=head, content_preserved=content
                ))

    def test_retirement_transition_requires_a_discoverable_base(self):
        paths = set(VALIDATOR.RETIREMENT_TRANSITION_PATHS)
        with patch.object(VALIDATOR, "ref_has_path", return_value=False) as base, \
                patch.object(VALIDATOR, "retirement_content_preserved", return_value=True), \
                self.assertRaisesRegex(
                    VALIDATOR.MetadataValidationError, "Production add-on files were modified"
                ):
            VALIDATOR.validate_repository(ROOT, base_ref="HEAD", paths=paths)
        base.assert_called_once_with(ROOT, "HEAD", "hass_mcp_admin/config.yaml")

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
        self.assertEqual(current, VALIDATOR.BETA_VERSION)
        self.assertTrue(
            VALIDATOR.is_newer_version(
                report.staged_release_version,
                current,
            )
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
        next_major = int(str(self.beta["version"]).split(".", 1)[0]) + 1
        with self.assertRaises(VALIDATOR.MetadataValidationError):
            VALIDATOR.validate_repository(
                ROOT,
                base_ref="origin/main",
                deployed_version=f"{next_major}.0.0",
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


class StableRetirementTransitionContentTests(unittest.TestCase):
    """HAMCP-203 M1: the one-time retirement allows a byte-exact rename only.

    Each case builds a disposable Git repository whose base holds the original
    stable ``config.yaml`` and runs the real validator CLI against that base.
    """

    RETIRED = "hass_mcp_admin/retired-addon-config.yaml"
    ORIGINAL = "hass_mcp_admin/config.yaml"
    ENGINEERING = (
        "hass_mcp_engineering_beta/config.yaml",
        "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
        "hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py",
    )
    REFUSED = "Production add-on files were modified"

    def setUp(self):
        retired = (ROOT / self.RETIRED).read_bytes()
        self.assertTrue(retired.startswith(VALIDATOR.RETIREMENT_HEADER))
        self.original = retired[len(VALIDATOR.RETIREMENT_HEADER):]
        self.assertIn(b"  - homeassistant_config:ro\n", self.original)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.git("init", "--quiet")
        self.git("config", "core.autocrlf", "false")
        stable = self.git("ls-files", "hass_mcp_admin", cwd=ROOT).splitlines()
        for path in stable + list(self.ENGINEERING):
            if path in (self.RETIRED, "hass_mcp_admin/README.md"):
                continue
            self.write(path, (ROOT / path).read_bytes())
        self.write(self.ORIGINAL, self.original)
        self.base = self.commit("base: shipped stable v1")

    def git(self, *args, cwd=None):
        return subprocess.run(
            [
                "git", "-c", "user.name=HAMCP-203 test", "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false", *args,
            ],
            cwd=cwd or self.repo, check=True, capture_output=True, text=True,
        ).stdout

    def write(self, path, data):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def retire(self, config=None, readme=True):
        self.git("mv", self.ORIGINAL, self.RETIRED)
        self.write(self.RETIRED, VALIDATOR.RETIREMENT_HEADER + self.original if config is None else config)
        if readme:
            self.write("hass_mcp_admin/README.md", (ROOT / "hass_mcp_admin/README.md").read_bytes())
        return self.commit("retire stable v1")

    def run_cli(self, base):
        return subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/validate_addon_metadata.py"),
                "--repo-root", str(self.repo), "--base-ref", base,
            ],
            capture_output=True, text=True, check=False,
        )

    def assert_allowed(self, base):
        result = self.run_cli(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stable v1 retirement transition: allowed", result.stdout)

    def assert_refused(self, base):
        result = self.run_cli(base)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(self.REFUSED, result.stderr)
        self.assertNotIn("transition: allowed", result.stdout)

    def test_exact_rename_with_header_and_new_readme_is_allowed(self):
        self.retire()
        self.assert_allowed(self.base)

    def test_exact_rename_without_readme_is_allowed(self):
        self.retire(readme=False)
        self.assert_allowed(self.base)

    def test_codex_reproduction_mount_ro_to_rw_is_refused(self):
        self.retire(VALIDATOR.RETIREMENT_HEADER + self.original.replace(
            b"homeassistant_config:ro", b"homeassistant_config:rw"
        ))
        self.assert_refused(self.base)

    def test_any_other_config_change_is_refused(self):
        header = VALIDATOR.RETIREMENT_HEADER
        mutations = {
            "added_key": self.original + b"host_network: true\n",
            "removed_arch": self.original.replace(b"  - armv7\n", b""),
            "init_flag": self.original.replace(b"init: false", b"init: true"),
            "option_value": self.original.replace(b"rate_limit_burst: 25", b"rate_limit_burst: 26"),
            "destructive_list": self.original.replace(b"    - lock.unlock\n", b""),
            "port_description": self.original.replace(b"point your Cloudflare", b"point any"),
            "trailing_whitespace": self.original + b"\n",
            "crlf_committed": self.original.replace(b"\n", b"\r\n"),
        }
        for name, content in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(content, self.original)
                self.git("reset", "--quiet", "--hard", self.base)
                self.retire(header + content)
                self.assert_refused(self.base)

    def test_header_alterations_are_refused(self):
        header = VALIDATOR.RETIREMENT_HEADER
        variants = {
            "no_header": self.original,
            "header_comments_out_mount": header + self.original.replace(
                b"  - homeassistant_config:ro\n", b"#  - homeassistant_config:ro\n"
            ),
            "header_absorbs_mount": header.rstrip(b"\n") + b"\n# map:\n#   - homeassistant_config:ro\n\n"
            + self.original.replace(b"map:\n  - homeassistant_config:ro\n", b""),
            "header_reworded": header.replace(b"Do not", b"Feel free to") + self.original,
            "header_appends_key": header + b"host_network: true\n" + self.original,
            "header_twice": header + header + self.original,
            "header_after_content": self.original + header,
        }
        for name, content in variants.items():
            with self.subTest(name=name):
                self.git("reset", "--quiet", "--hard", self.base)
                self.retire(content)
                self.assert_refused(self.base)

    def test_any_other_stable_path_is_refused(self):
        for name, path, data in (
            ("runtime_edit", "hass_mcp_admin/server.py", None),
            ("new_file", "hass_mcp_admin/NOTES.md", b"retired\n"),
        ):
            with self.subTest(name=name):
                self.git("reset", "--quiet", "--hard", self.base)
                self.git("clean", "--quiet", "-fdx")
                self.git("mv", self.ORIGINAL, self.RETIRED)
                self.write(self.RETIRED, VALIDATOR.RETIREMENT_HEADER + self.original)
                current = (self.repo / path).read_bytes() if data is None else b""
                self.write(path, current + (data or b"# unauthorized\n"))
                self.commit("retire plus extra")
                self.assert_refused(self.base)

    def test_uncommitted_or_staged_config_changes_are_refused(self):
        self.retire()
        exact = (self.repo / self.RETIRED).read_bytes()
        rw = exact.replace(b"homeassistant_config:ro", b"homeassistant_config:rw")
        self.write(self.RETIRED, rw)
        self.assert_refused(self.base)
        self.git("add", self.RETIRED)
        self.write(self.RETIRED, exact)
        self.assert_refused(self.base)

    def test_checkout_line_ending_translation_is_not_a_change(self):
        self.retire()
        self.git("config", "core.autocrlf", "true")
        (self.repo / self.RETIRED).unlink()
        self.git("checkout", "--", self.RETIRED)
        self.assertIn(b"\r\n", (self.repo / self.RETIRED).read_bytes())
        self.assert_allowed(self.base)

    def test_existing_retired_paths_in_base_are_refused(self):
        self.write(self.RETIRED, VALIDATOR.RETIREMENT_HEADER + self.original)
        base = self.commit("base already holds the retired name")
        self.git("rm", "--quiet", self.ORIGINAL)
        self.commit("drop the discoverable config")
        self.assert_refused(base)

    def test_post_merge_edits_are_refused_and_unchanged_tree_passes(self):
        merged = self.retire()
        result = self.run_cli(merged)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("transition: allowed", result.stdout)
        self.write(self.RETIRED, (self.repo / self.RETIRED).read_bytes().replace(
            b"homeassistant_config:ro", b"homeassistant_config:rw"
        ))
        self.commit("post-merge edit")
        self.assert_refused(merged)


class UnreleasedRcIntegrityTests(unittest.TestCase):
    def setUp(self):
        version_patch = patch.object(
            VALIDATOR,
            "BETA_VERSION",
            "2.0.0-rc2-dev16",
        )
        version_patch.start()
        self.addCleanup(version_patch.stop)

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

    def test_engineering_discovery_excludes_retired_stable_behavior(self):
        historical = (
            ROOT / "tests" / "historical_stable_v1_gateway.py",
            ROOT / "tests" / "historical_stable_v1_metadata.py",
        )
        for path in historical:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertFalse(path.name.startswith("test"))
        # Engineering discovery now runs as shards proven complete by the
        # aggregate; retired stable-v1 dependencies and image build stay out.
        self.assertIn("Run Engineering unit test shard", self.workflow)
        self.assertIn(
            "Prove every discovered Engineering unit test ran exactly once and passed",
            self.workflow,
        )
        self.assertIn("python scripts/unit_test_shards.py aggregate", self.workflow)
        self.assertNotIn("hass_mcp_admin/requirements", self.workflow)
        self.assertNotIn("Build retired stable-v1 image packaging", self.workflow)
        self.assertNotIn("hass-mcp-admin:test", self.workflow)

    def test_current_assurance_docs_record_dns_and_stable_boundaries(self):
        release = (ROOT / "docs" / "V2_0_1_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "V2_0_1_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        security = (ROOT / "docs" / "SECURITY.md").read_text(
            encoding="utf-8"
        )
        combined = "\n".join((release, acceptance, security))

        self.assertIn("`0.0.0.0`", combined)
        self.assertIn("issue #62", combined)
        self.assertIn("mitigated, deferred configuration risk", combined)
        self.assertNotIn("DNS-rebinding protection remains enabled", combined)
        self.assertNotIn(
            "CVE-2025-53366, CVE-2025-53365, CVE-2025-66416",
            combined,
        )
        self.assertIn("operationally retired", combined)
        self.assertIn(
            "sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130",
            combined,
        )

    def test_2_0_1_ga_docs_preserve_release_and_operational_contracts(self):
        release = (ROOT / "docs" / "V2_0_1_RELEASE_NOTES.md").read_text(
            encoding="utf-8"
        )
        acceptance = (ROOT / "docs" / "V2_0_1_ACCEPTANCE.md").read_text(
            encoding="utf-8"
        )
        operator = (
            ROOT / "docs" / "UPSTREAM_COMPATIBILITY_OPERATOR_GUIDE.md"
        ).read_text(encoding="utf-8")
        combined = "\n".join((release, acceptance, operator))

        for expected in (
            "Version: `2.0.1`",
            "156 records total",
            "41 Engineering tools plus 26 delegated reads",
            "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c",
            "HA MCP Engineering Server 2.0.1",
            "ha-mcp 7.14.2",
            "Home Assistant 2026.7.3",
            "up to approximately 15 minutes",
            "Restarting the Engineering add-on forces immediate rediscovery",
            "provider_error` / `upstream_error` / `retryable=true",
            "does not independently observe the running upstream artifact",
            "No fallback is used during the transition",
            "7.14.1 -> 7.14.2 -> 7.14.1 -> 7.14.2",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)

        self.assertIn("exclusive compatibility restriction", release)
        self.assertIn("2.1.0 features\nare not included", release)

    def test_exact_image_contract_makes_no_host_rejection_claim(self):
        source = (
            ROOT / "scripts" / "exact_image_read_gateway_acceptance.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("DNS-rebinding", source)
        self.assertNotIn("untrusted-host", source)


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
        self.assertIn('"Run complete Engineering test suite"', self.script)
        self.assertGreaterEqual(self.script.count("-SuppressOutput"), 3)


if __name__ == "__main__":
    unittest.main()
