import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from awesomeversion import AwesomeVersion
import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_PATH = ROOT / ".github" / "workflows" / "publish-rc-image.yml"
TAG_GUARD_PATH = ROOT / "scripts" / "assert_registry_tags_absent.sh"
PROMOTION_PATH = ROOT / "scripts" / "promote_next_release.py"
IMAGE = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
# RC2dev12 remains the immutable failed full-host-reboot candidate. The
# correction uses the staged-release mechanism without changing advertised
# RC2dev12 runtime metadata in this feature pull request.
NEXT_VERSION = "2.0.0-rc2-dev13"
PROMOTION_FIXTURE_CURRENT_VERSION = "2.0.0-rc2-dev12"
CURRENT_REPOSITORY_VERSION = "2.2.0-beta.20"
PLATFORMS = ("linux/amd64", "linux/arm64", "linux/arm/v7")
BUILD_ARGUMENTS = (
    "BUILD_VERSION",
    "HAMCP_BUILD_SHA",
    "HAMCP_BUILD_TIME",
    "HAMCP_BUILD_DIRTY",
)

PROMOTION_SPEC = importlib.util.spec_from_file_location(
    "promote_next_release",
    PROMOTION_PATH,
)
PROMOTION_MODULE = importlib.util.module_from_spec(PROMOTION_SPEC)
assert PROMOTION_SPEC.loader is not None
PROMOTION_SPEC.loader.exec_module(PROMOTION_MODULE)


def load_workflow(path):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def workflow_events(workflow):
    return workflow.get("on", workflow.get(True))


def action_steps(job, action_prefix):
    return [
        step
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(action_prefix)
    ]


def run_steps(job):
    return [str(step["run"]) for step in job.get("steps", []) if "run" in step]


def assignment_lines(value):
    result = {}
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        if line:
            key, separator, item = line.partition("=")
            if not separator:
                raise AssertionError(f"expected KEY=VALUE workflow input: {line!r}")
            result[key] = item
    return result


class AutomatedPromotionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = load_workflow(CI_PATH)
        cls.workflow = load_workflow(PUBLISH_PATH)
        cls.jobs = cls.workflow["jobs"]
        cls.promote = cls.jobs["promote"]
        cls.steps = cls.promote["steps"]
        cls.text = PUBLISH_PATH.read_text(encoding="utf-8")

    def test_only_main_push_can_start_automatic_promotion(self):
        events = workflow_events(self.workflow)
        self.assertEqual(events, {"push": {"branches": ["main"]}})
        self.assertEqual(self.workflow["permissions"], {})
        self.assertNotIn("push:\n    tags:", self.text)
        self.assertNotIn("workflow_dispatch", self.text)
        self.assertEqual(
            self.workflow["concurrency"],
            {
                "group": "hass-mcp-engineering-release-promotion",
                "cancel-in-progress": False,
            },
        )

    def test_feature_pr_or_promoted_source_is_version_consistent(self):
        versions = PROMOTION_MODULE.authoritative_versions(ROOT)
        self.assertEqual(len(set(versions.values())), 1)
        configured_version = next(iter(versions.values()))
        declaration = ROOT / ".release" / "next-version"
        if declaration.exists():
            current, candidate = PROMOTION_MODULE.validate_candidate(ROOT)
            self.assertEqual(current, configured_version)
            self.assertEqual(candidate, NEXT_VERSION)
            self.assertEqual(
                declaration.read_text(encoding="utf-8").strip(),
                NEXT_VERSION,
            )
        else:
            self.assertEqual(configured_version, CURRENT_REPOSITORY_VERSION)

    def test_staged_or_advertised_version_is_ordered(self):
        versions = PROMOTION_MODULE.authoritative_versions(ROOT)
        configured_version = next(iter(versions.values()))
        declaration = ROOT / ".release" / "next-version"
        if declaration.exists():
            effective_version = declaration.read_text(encoding="utf-8").strip()
            self.assertGreater(
                AwesomeVersion(effective_version),
                AwesomeVersion(configured_version),
            )
        else:
            effective_version = configured_version
            self.assertEqual(configured_version, CURRENT_REPOSITORY_VERSION)
        if declaration.exists():
            self.assertLess(
                AwesomeVersion(effective_version),
                AwesomeVersion("2.0.0-rc.3"),
            )
        else:
            maintenance_version = AwesomeVersion(effective_version)
            self.assertEqual(
                maintenance_version,
                AwesomeVersion(CURRENT_REPOSITORY_VERSION),
            )
            self.assertGreater(maintenance_version, AwesomeVersion("2.0.0"))

    def test_complete_validation_precedes_release_detection_and_promotion(self):
        self.assertEqual(
            self.jobs["validate"]["uses"],
            "./.github/workflows/ci.yml",
        )
        self.assertEqual(self.jobs["detect-release"]["needs"], "validate")
        self.assertEqual(
            set(self.promote["needs"]),
            {"validate", "detect-release"},
        )
        self.assertIn("workflow_call", workflow_events(self.ci))

    def test_dependency_audit_is_a_required_release_validation_gate(self):
        validate_steps = self.ci["jobs"]["validate"]["steps"]
        audit = next(
            step
            for step in validate_steps
            if step.get("name") == "Audit Engineering dependencies"
        )
        self.assertEqual(
            audit["run"],
            "python -m pip_audit --strict --progress-spinner off "
            "--requirement hass_mcp_engineering_beta/requirements.txt",
        )
        install = next(
            step
            for step in validate_steps
            if step.get("name") == "Install dependencies"
        )
        self.assertIn("hass_mcp_engineering_beta/requirements.txt", install["run"])
        self.assertNotIn("hass_mcp_admin/requirements-dev.txt", install["run"])
        release_install = next(
            step
            for step in self.steps
            if step.get("name") == "Install release validation dependencies"
        )
        self.assertNotIn(
            "hass_mcp_admin/requirements-dev.txt",
            release_install["run"],
        )

    def test_preversioned_release_transition_is_detected_and_validated(self):
        detect = str(self.jobs["detect-release"]["steps"][-1]["run"])
        prepare = str(next(
            step["run"]
            for step in self.steps
            if step.get("name") == "Prepare local immutable release commit"
        ))
        self.assertIn("github.event.before", detect)
        self.assertIn("release_mode=preversioned", detect)
        self.assertIn('RELEASE_MODE" == "preversioned', prepare)
        self.assertIn('version="$current_version"', prepare)
        self.assertIn(
            'python scripts/promote_next_release.py --validate-authority "$version"',
            prepare,
        )
        self.assertIn('--deployed-version "$deployed_version"', prepare)
        self.assertIn('--base-ref "$validation_base"', prepare)
        self.assertIn('git status --porcelain', prepare)

    def run_release_detector(
        self,
        *,
        subject,
        current_version=CURRENT_REPOSITORY_VERSION,
        previous_version="2.0.0-rc2-dev16",
        staged_version=None,
    ):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute the release detector")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "hass_mcp_engineering_beta" / "config.yaml"
            config.parent.mkdir(parents=True)

            def git(*arguments):
                return subprocess.run(
                    ["git", *arguments],
                    cwd=root,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()

            git("init")
            git("config", "user.name", "Detector Fixture")
            git("config", "user.email", "detector-fixture@example.invalid")
            config.write_text(
                f'version: "{previous_version}"\n',
                encoding="utf-8",
            )
            git("add", "hass_mcp_engineering_beta/config.yaml")
            git("commit", "-m", "Establish previous Engineering version")
            before = git("rev-parse", "HEAD")

            config.write_text(
                f'version: "{current_version}"\n',
                encoding="utf-8",
            )
            if staged_version is not None:
                declaration = root / ".release" / "next-version"
                declaration.parent.mkdir()
                declaration.write_text(f"{staged_version}\n", encoding="utf-8")
                git("add", ".release/next-version")
            git("add", "hass_mcp_engineering_beta/config.yaml")
            git("commit", "-m", subject)

            output = root / "github-output"
            summary = root / "github-summary"
            environment = os.environ.copy()
            environment["GITHUB_OUTPUT"] = str(output)
            environment["GITHUB_STEP_SUMMARY"] = str(summary)
            detector = str(self.jobs["detect-release"]["steps"][-1]["run"])
            detector = detector.replace("${{ github.event.before }}", before)
            result = subprocess.run(
                [bash, "-c", detector],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            values = assignment_lines(output.read_text(encoding="utf-8"))
            self.assertEqual(git("tag", "--list"), "")
            return values, (
                summary.read_text(encoding="utf-8")
                if summary.exists()
                else ""
            )

    def test_release_detector_trusts_only_exact_current_bot_subject(self):
        exact_bot_subject = (
            f"Promote HA MCP Engineering Server {CURRENT_REPOSITORY_VERSION}"
        )
        cases = (
            (exact_bot_subject, False, "none"),
            ("Promote HA MCP Engineering Server 2.0.1 GA", True, "preversioned"),
            (
                "Promote HA MCP Engineering Server 2.0.1 GA (#65)",
                True,
                "preversioned",
            ),
            (f"{exact_bot_subject} extra", True, "preversioned"),
            (
                "Promote HA MCP Engineering Server 2.0.0-rc2-dev16",
                True,
                "preversioned",
            ),
            ("Promote HA MCP Engineering Server 2.0.2", True, "preversioned"),
            ("Promote Engineering server to 2.0.1", True, "preversioned"),
            ("Promote Engineering server to 2.0.1 (#65)", True, "preversioned"),
            (
                "Merge pull request #65 from jeter-1/release/2.0.1",
                True,
                "preversioned",
            ),
            ("promote HA MCP Engineering Server 2.0.1", True, "preversioned"),
            ("Promote  HA MCP Engineering Server 2.0.1", True, "preversioned"),
        )
        for subject, should_promote, release_mode in cases:
            with self.subTest(subject=subject):
                values, summary = self.run_release_detector(subject=subject)
                self.assertEqual(
                    values,
                    {
                        "should_promote": str(should_promote).lower(),
                        "release_mode": release_mode,
                    },
                )
                if should_promote:
                    self.assertNotIn("already promoted", summary)
                else:
                    self.assertIn("already promoted", summary)

    def test_exact_automated_subject_prevents_recursive_rc_publication(self):
        version = "2.0.0-rc2-dev16"
        values, summary = self.run_release_detector(
            subject=f"Promote HA MCP Engineering Server {version}",
            current_version=version,
            previous_version="2.0.0-rc2-dev15",
        )
        self.assertEqual(
            values,
            {"should_promote": "false", "release_mode": "none"},
        )
        self.assertIn("already promoted", summary)

    def test_staged_and_preversioned_rc_detection_remain_unchanged(self):
        staged_values, _ = self.run_release_detector(
            subject="Prepare the next reviewed RC correction",
            current_version="2.0.0-rc2-dev15",
            previous_version="2.0.0-rc2-dev14",
            staged_version="2.0.0-rc2-dev16",
        )
        self.assertEqual(
            staged_values,
            {"should_promote": "true", "release_mode": "staged"},
        )

        preversioned_values, _ = self.run_release_detector(
            subject="Promote reviewed RC correction",
            current_version="2.0.0-rc2-dev16",
            previous_version="2.0.0-rc2-dev15",
        )
        self.assertEqual(
            preversioned_values,
            {"should_promote": "true", "release_mode": "preversioned"},
        )

    def test_detector_subject_matches_automated_release_commit_exactly(self):
        detect = str(self.jobs["detect-release"]["steps"][-1]["run"])
        prepare = str(next(
            step["run"]
            for step in self.steps
            if step.get("name") == "Prepare local immutable release commit"
        ))
        self.assertIn(
            'automated_release_subject="Promote HA MCP Engineering Server ${current_version}"',
            detect,
        )
        self.assertIn(
            '"$commit_subject" == "$automated_release_subject"',
            detect,
        )
        self.assertNotIn(
            '== "Promote HA MCP Engineering Server "*',
            detect,
        )
        self.assertNotIn("docker", detect)
        self.assertNotIn("git tag", detect)
        self.assertNotIn("git push", detect)
        self.assertIn(
            'git commit -m "Promote HA MCP Engineering Server ${version}"',
            prepare,
        )

    def test_only_main_promotion_job_can_write_contents_or_packages(self):
        writers = {
            name: job.get("permissions", {})
            for name, job in self.jobs.items()
            if "write" in (job.get("permissions") or {}).values()
        }
        self.assertEqual(
            writers,
            {"promote": {"contents": "write", "packages": "write"}},
        )
        self.assertEqual(
            self.jobs["detect-release"]["permissions"],
            {"contents": "read"},
        )

    def test_pull_request_ci_cannot_authenticate_or_push(self):
        events = workflow_events(self.ci)
        self.assertIn("pull_request", events)
        self.assertNotIn("packages", self.ci.get("permissions", {}))
        actions = [
            str(step.get("uses", ""))
            for job in self.ci["jobs"].values()
            for step in job.get("steps", [])
        ]
        self.assertFalse(any(value.startswith("docker/login-action") for value in actions))
        scripts = "\n".join(
            script
            for job in self.ci["jobs"].values()
            for script in run_steps(job)
        )
        self.assertNotIn("docker login", scripts)
        self.assertNotIn("--push", scripts)
        self.assertNotIn("git push", scripts)

    def test_local_release_commit_is_validated_before_registry_login(self):
        names = [step.get("name", "") for step in self.steps]
        prepare_index = names.index("Prepare local immutable release commit")
        login_index = names.index("Log in to GHCR")
        build_index = names.index("Build and publish local release commit")
        self.assertLess(prepare_index, login_index)
        self.assertLess(login_index, build_index)
        prepare = str(self.steps[prepare_index]["run"])
        for value in (
            "git ls-remote origin refs/heads/main",
            "python scripts/promote_next_release.py",
            "git ls-remote --exit-code --tags",
            "scripts/assert_registry_tags_absent.sh",
            "python scripts/promote_next_release.py --apply",
            "git commit -m",
            "python scripts/validate_addon_metadata.py",
            "python -m unittest discover -s tests -v",
            "git diff --check",
        ):
            self.assertIn(value, prepare)
        self.assertIn('date -u +\'%Y-%m-%dT%H:%M:%SZ\'', prepare)
        self.assertNotIn("git push", prepare)

    def test_one_build_publishes_exact_multiarch_and_provenance_tags(self):
        builds = action_steps(self.promote, "docker/build-push-action")
        self.assertEqual(len(builds), 1)
        values = builds[0]["with"]
        self.assertIs(values["push"], True)
        self.assertEqual(values["provenance"], "mode=max")
        self.assertIs(values["sbom"], True)
        self.assertEqual(
            tuple(item.strip() for item in values["platforms"].split(",")),
            PLATFORMS,
        )
        arguments = assignment_lines(values["build-args"])
        self.assertEqual(set(arguments), set(BUILD_ARGUMENTS))
        self.assertEqual(
            arguments,
            {
                "BUILD_VERSION": "${{ steps.prepare.outputs.version }}",
                "HAMCP_BUILD_SHA": "${{ steps.prepare.outputs.release_sha }}",
                "HAMCP_BUILD_TIME": "${{ steps.prepare.outputs.build_time }}",
                "HAMCP_BUILD_DIRTY": "false",
            },
        )
        tags = tuple(
            line.strip() for line in values["tags"].splitlines() if line.strip()
        )
        self.assertEqual(
            tags,
            (
                f"{IMAGE}:${{{{ steps.prepare.outputs.version }}}}",
                f"{IMAGE}:sha-${{{{ steps.prepare.outputs.release_sha }}}}",
            ),
        )

    def test_anonymous_verification_precedes_release_finalization(self):
        names = [step.get("name", "") for step in self.steps]
        verify_index = names.index(
            "Verify immutable tags, architectures, and provenance anonymously"
        )
        push_index = names.index("Finalize release commit and annotated tag")
        self.assertLess(verify_index, push_index)
        verify = str(self.steps[verify_index]["run"])
        for value in (
            'anonymous_config="$RUNNER_TEMP/anonymous-docker"',
            'DOCKER_CONFIG="$anonymous_config"',
            'imagetools inspect --raw',
            '("linux", "amd64", None)',
            '("linux", "arm64", None)',
            '("linux", "arm", "v7")',
            "version_digest",
            "sha_digest",
            "org.opencontainers.image.revision",
            "org.opencontainers.image.created",
            "org.opencontainers.image.version",
        ):
            self.assertIn(value, verify)
        push = str(self.steps[push_index]["run"])
        self.assertIn("git push --atomic origin", push)
        self.assertIn("refs/heads/main", push)
        self.assertIn("refs/tags/", push)
        self.assertIn('git push origin "refs/tags/', push)
        self.assertNotIn("--force", push)
        self.assertIn('"$remote_main_sha" != "$SOURCE_MAIN_SHA"', push)
        self.assertIn('git config user.name "github-actions[bot]"', push)
        self.assertIn('git config user.email "41898282+github-actions[bot]@users.noreply.github.com"', push)
        self.assertIn('git rev-parse "${RELEASE_TAG}^{commit}"', push)
        for value in ("Version:", "Source SHA:", "Image digest:", "Build timestamp:"):
            self.assertIn(value, push)

    def test_failures_produce_reconciliation_without_silent_reuse(self):
        failure = next(
            step
            for step in self.steps
            if step.get("name") == "Write promotion and reconciliation summary"
        )
        self.assertEqual(failure["if"], "always()")
        script = str(failure["run"])
        self.assertIn("requires reconciliation", script)
        self.assertIn("Do not rebuild or overwrite", script)
        self.assertIn("did not force-push", script)
        for field in (
            "image_published", "image_verified", "manifest_digest",
            "tag_created", "tag_verified", "release_complete",
        ):
            self.assertIn(field, script)

    def test_promotion_exposes_truthful_phase_outputs(self):
        outputs = self.promote["outputs"]
        self.assertEqual(
            set(outputs),
            {
                "version", "release_sha", "digest", "image_published",
                "image_verified", "tag_created", "tag_verified", "release_complete",
            },
        )
        verify = next(
            step for step in self.steps
            if step.get("name") == "Verify immutable tags, architectures, and provenance anonymously"
        )
        self.assertEqual(verify["id"], "verify")
        self.assertIn("attestation-manifest", str(verify["run"]))
        self.assertIn("sbom_status=present", str(verify["run"]))

    def test_declared_architectures_match_ci_and_publication(self):
        config = yaml.safe_load(
            (ROOT / "hass_mcp_engineering_beta" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        mapping = {
            "amd64": "linux/amd64",
            "aarch64": "linux/arm64",
            "armv7": "linux/arm/v7",
        }
        self.assertEqual(
            tuple(mapping[arch] for arch in config["arch"]),
            PLATFORMS,
        )

    def test_dockerfile_consumes_all_provenance_arguments(self):
        dockerfile = (
            ROOT / "hass_mcp_engineering_beta" / "Dockerfile"
        ).read_text(encoding="utf-8")
        for name in BUILD_ARGUMENTS:
            self.assertIn(f"ARG {name}=unknown", dockerfile)
            self.assertIn(f"{name}=${{{name}}}", dockerfile)
        for label in ("version", "revision", "created", "source"):
            self.assertIn(f"org.opencontainers.image.{label}", dockerfile)


class PromotionScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = PROMOTION_MODULE

    def make_repo(
        self,
        root,
        current=PROMOTION_FIXTURE_CURRENT_VERSION,
        candidate=NEXT_VERSION,
    ):
        files = {
            ".release/next-version": candidate + "\n",
            "hass_mcp_engineering_beta/config.yaml": f'version: "{current}"\n',
            "hass_mcp_engineering_beta/ha_mcp_engineering/version.py": (
                f'SERVER_VERSION = "{current}"\n'
            ),
            "scripts/validate_addon_metadata.py": f'BETA_VERSION = "{current}"\n',
            "docs/RC2DEV13_RELEASE_NOTES.md": (
                f"# {candidate} release notes\n\nVersion: `{candidate}`\n"
            ),
            "docs/RC2DEV13_ACCEPTANCE.md": (
                f"# {candidate} acceptance\n\nVersion: `{candidate}`\n"
            ),
        }
        for relative, content in files.items():
            path = Path(root) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_apply_updates_authoritative_versions_and_consumes_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_repo(directory)
            current, candidate = self.module.apply_candidate(Path(directory))
            self.assertEqual(
                (current, candidate),
                (PROMOTION_FIXTURE_CURRENT_VERSION, NEXT_VERSION),
            )
            self.assertFalse((Path(directory) / ".release/next-version").exists())
            self.assertEqual(
                set(self.module.authoritative_versions(Path(directory)).values()),
                {NEXT_VERSION},
            )

    def test_candidate_must_be_newer_and_below_final_rc3(self):
        for candidate in (
            PROMOTION_FIXTURE_CURRENT_VERSION,
            "2.0.0-rc.3",
            "not-a-version",
        ):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as directory:
                self.make_repo(directory, candidate=candidate)
                with self.assertRaises(self.module.PromotionError):
                    self.module.validate_candidate(Path(directory))

    def test_authoritative_version_disagreement_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_repo(directory)
            path = Path(directory) / "hass_mcp_engineering_beta/config.yaml"
            path.write_text('version: "2.0.0-rc.2"\n', encoding="utf-8")
            with self.assertRaises(self.module.PromotionError):
                self.module.validate_candidate(Path(directory))

    def test_missing_or_non_authoritative_staged_documents_fail_closed(self):
        for relative, replacement in (
            ("docs/RC2DEV13_ACCEPTANCE.md", None),
            (
                "docs/RC2DEV13_ACCEPTANCE.md",
                "# 2.0.0-rc2-dev13 acceptance\n\nHistorical only; cannot authorize.\n",
            ),
            ("docs/RC2DEV13_RELEASE_NOTES.md", None),
        ):
            with self.subTest(relative=relative, replacement=replacement), tempfile.TemporaryDirectory() as directory:
                self.make_repo(directory)
                path = Path(directory) / relative
                if replacement is None:
                    path.unlink()
                else:
                    path.write_text(replacement, encoding="utf-8")
                with self.assertRaises(self.module.PromotionError):
                    self.module.validate_candidate(Path(directory))

    def test_preversioned_document_authority_is_validated_without_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_repo(directory)
            (Path(directory) / ".release" / "next-version").unlink()
            resolution = self.module.validate_document_authority(
                Path(directory), NEXT_VERSION
            )
            self.assertEqual(resolution["resolution_status"], "exact")
            (Path(directory) / "docs" / "RC2DEV13_ACCEPTANCE.md").unlink()
            with self.assertRaises(self.module.PromotionError):
                self.module.validate_document_authority(
                    Path(directory), NEXT_VERSION
                )

    def test_repository_ga_document_authority_is_exact(self):
        resolution = self.module.validate_document_authority(ROOT, "2.0.1")
        self.assertEqual(resolution["resolution_status"], "exact")
        self.assertEqual(
            resolution["active_release_notes"],
            "docs/V2_0_1_RELEASE_NOTES.md",
        )
        self.assertEqual(
            resolution["active_acceptance_document"],
            "docs/V2_0_1_ACCEPTANCE.md",
        )


class RegistryTagGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if cls.bash is None and os.name == "nt":
            for candidate in (
                Path("C:/Program Files/Git/bin/bash.exe"),
                Path("C:/Program Files/Git/usr/bin/bash.exe"),
            ):
                if candidate.is_file():
                    cls.bash = str(candidate)
                    break
        if cls.bash is None:
            raise unittest.SkipTest("bash is required to validate the release tag guard")

    def run_guard(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            fake_docker = Path(directory) / "docker"
            fake_docker.write_text(
                """#!/usr/bin/env bash
case "$MOCK_INSPECT_MODE" in
  absent_manifest)
    echo 'ERROR: manifest unknown' >&2
    exit 1
    ;;
  absent_not_found)
    echo 'ERROR: ghcr.io/jeter-1/hass-mcp-engineering-beta:test: not found' >&2
    exit 1
    ;;
  exists)
    echo 'Name: ghcr.io/jeter-1/hass-mcp-engineering-beta:test'
    exit 0
    ;;
  network)
    echo 'ERROR: failed to dial registry: connection reset by peer' >&2
    exit 1
    ;;
  auth)
    echo 'ERROR: denied: permission_denied' >&2
    exit 1
    ;;
  *)
    echo 'unexpected mock mode' >&2
    exit 9
    ;;
esac
""",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            environment = os.environ.copy()
            environment["DOCKER_CLI"] = str(fake_docker).replace("\\", "/")
            environment["MOCK_INSPECT_MODE"] = mode
            return subprocess.run(
                [self.bash, str(TAG_GUARD_PATH), f"{IMAGE}:test"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

    def test_explicit_absence_allows_publication(self):
        for mode in ("absent_manifest", "absent_not_found"):
            with self.subTest(mode=mode):
                result = self.run_guard(mode)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_or_ambiguous_tags_fail_closed(self):
        for mode in ("exists", "network", "auth", "unknown"):
            with self.subTest(mode=mode):
                result = self.run_guard(mode)
                self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
