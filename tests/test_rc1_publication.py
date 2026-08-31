import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from awesomeversion import AwesomeVersion
import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_PATH = ROOT / ".github" / "workflows" / "publish-rc-image.yml"
TAG_GUARD_PATH = ROOT / "scripts" / "assert_registry_tags_absent.sh"
ANCESTOR_GUARD_PATH = ROOT / "scripts" / "assert_protected_release_ancestor.sh"
PROMOTION_PATH = ROOT / "scripts" / "promote_next_release.py"
CREATE_ONLY_PUBLISHER_PATH = (
    ROOT / "scripts" / "publish_registry_tags_create_only.py"
)
SOURCE_VERIFIER_PATH = ROOT / "scripts" / "verify_publication_source_image.py"
IMAGE = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
# RC2dev12 remains the immutable failed full-host-reboot candidate. The
# correction uses the staged-release mechanism without changing advertised
# RC2dev12 runtime metadata in this feature pull request.
NEXT_VERSION = "2.0.0-rc2-dev13"
PROMOTION_FIXTURE_CURRENT_VERSION = "2.0.0-rc2-dev12"
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
CURRENT_REPOSITORY_VERSION = PROMOTION_MODULE.advertised_version(ROOT)
CURRENT_STAGED_VERSION = (
    PROMOTION_MODULE.read_next_version(ROOT)
    if (ROOT / PROMOTION_MODULE.NEXT_VERSION_PATH).exists()
    else CURRENT_REPOSITORY_VERSION
)


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

    def test_only_main_push_or_guarded_manual_recovery_can_publish(self):
        events = workflow_events(self.workflow)
        self.assertEqual(
            set(events),
            {"push", "workflow_dispatch"},
        )
        self.assertEqual(events["push"], {"branches": ["main"]})
        dispatch = events["workflow_dispatch"]
        self.assertEqual(set(dispatch), {"inputs"})
        self.assertEqual(
            set(dispatch["inputs"]),
            {
                "release_sha",
                "expected_version",
                "recovery_run_id",
                "recovery_source_digest",
                "recovery_build_time",
            },
        )
        for name in ("release_sha", "expected_version"):
            value = dispatch["inputs"][name]
            self.assertIs(value["required"], True)
            self.assertEqual(value["type"], "string")
        for name in (
            "recovery_run_id",
            "recovery_source_digest",
            "recovery_build_time",
        ):
            value = dispatch["inputs"][name]
            self.assertIs(value["required"], False)
            self.assertEqual(value["type"], "string")
        self.assertEqual(self.workflow["permissions"], {})
        self.assertNotIn("push:\n    tags:", self.text)
        self.assertEqual(
            self.workflow["concurrency"],
            {
                "group": "hass-mcp-engineering-release-publication",
                "cancel-in-progress": False,
            },
        )

    def test_every_promotion_workflow_bash_step_parses(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to validate workflow scripts")
        for job_name, job in self.jobs.items():
            for step in job.get("steps", []):
                if "run" not in step or step.get("shell", "bash") != "bash":
                    continue
                with self.subTest(job=job_name, step=step.get("name", "unnamed")):
                    result = subprocess.run(
                        [bash, "-n", "-c", str(step["run"])],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_feature_pr_or_promoted_source_is_version_consistent(self):
        versions = PROMOTION_MODULE.authoritative_versions(ROOT)
        self.assertEqual(len(set(versions.values())), 1)
        configured_version = next(iter(versions.values()))
        declaration = ROOT / ".release" / "next-version"
        if declaration.exists():
            current, candidate = PROMOTION_MODULE.validate_candidate(ROOT)
            self.assertEqual(current, configured_version)
            self.assertEqual(candidate, CURRENT_STAGED_VERSION)
            self.assertEqual(
                declaration.read_text(encoding="utf-8").strip(),
                CURRENT_STAGED_VERSION,
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
            self.assertEqual(effective_version, CURRENT_STAGED_VERSION)
        else:
            maintenance_version = AwesomeVersion(effective_version)
            self.assertEqual(
                maintenance_version,
                AwesomeVersion(CURRENT_REPOSITORY_VERSION),
            )
            self.assertGreater(maintenance_version, AwesomeVersion("2.0.0"))

    def test_complete_validation_precedes_release_publication(self):
        self.assertEqual(
            self.jobs["validate"]["uses"],
            "./.github/workflows/ci.yml",
        )
        self.assertEqual(self.jobs["detect-release"]["needs"], "validate")
        self.assertEqual(
            set(self.promote["needs"]),
            {"validate", "detect-release"},
        )
        self.assertEqual(
            self.promote["if"],
            "needs.detect-release.outputs.release_action == 'publish'",
        )
        self.assertNotIn("prepare-promotion-pr", self.jobs)
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

    def test_pull_request_ci_requires_materialized_release_state(self):
        validate_steps = self.ci["jobs"]["validate"]["steps"]
        transition_validation = next(
            step
            for step in validate_steps
            if step.get("name") == "Validate exact materialized release transition"
        )
        candidate_validation = next(
            step
            for step in validate_steps
            if step.get("name") == "Validate staged promotion candidate"
        )
        self.assertEqual(
            candidate_validation["run"],
            "python scripts/validate_promotion_candidate.py --repo-root . "
            "--require-materialized",
        )
        transition_script = str(transition_validation["run"])
        self.assertIn(
            "git show origin/main:hass_mcp_engineering_beta/config.yaml",
            transition_script,
        )
        self.assertIn("--validate-transition", transition_script)
        self.assertLess(
            validate_steps.index(transition_validation),
            validate_steps.index(candidate_validation),
        )
        declaration = ROOT / PROMOTION_MODULE.NEXT_VERSION_PATH
        if not declaration.exists():
            self.assertEqual(
                PROMOTION_MODULE.advertised_version(ROOT),
                CURRENT_REPOSITORY_VERSION,
            )
            return
        result = subprocess.run(
            [
                sys.executable,
                "scripts/validate_promotion_candidate.py",
                "--repo-root",
                ".",
                "--require-materialized",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "release declaration is not final review state",
            result.stderr,
        )

    def test_pull_request_ci_rejects_skipped_and_cross_channel_materialization(self):
        validate_steps = self.ci["jobs"]["validate"]["steps"]
        transition_script = str(
            next(
                step["run"]
                for step in validate_steps
                if step.get("name")
                == "Validate exact materialized release transition"
            )
        )
        cases = (
            ("2.2.0-beta.50", True),
            ("2.2.0-beta.51", True),
            ("2.2.0-beta.52", False),
            ("2.2.0-rc2-dev51", False),
        )
        for candidate, succeeds in cases:
            with (
                self.subTest(candidate=candidate),
                tempfile.TemporaryDirectory() as directory,
            ):
                repo = Path(directory)
                config = repo / "hass_mcp_engineering_beta" / "config.yaml"
                script = repo / "scripts" / "promote_next_release.py"
                config.parent.mkdir(parents=True)
                script.parent.mkdir(parents=True)
                config.write_text('version: "2.2.0-beta.50"\n', encoding="utf-8")
                shutil.copy2(PROMOTION_PATH, script)
                subprocess.run(
                    ["git", "init", "-b", "main"],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "CI transition test"],
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "ci-transition@example.invalid"],
                    cwd=repo,
                    check=True,
                )
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "base"],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                base_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                subprocess.run(
                    ["git", "update-ref", "refs/remotes/origin/main", base_sha],
                    cwd=repo,
                    check=True,
                )
                config.write_text(f'version: "{candidate}"\n', encoding="utf-8")

                bin_dir = repo / "test-bin"
                bin_dir.mkdir()
                python_wrapper = bin_dir / "python"
                python_wrapper.write_text(
                    f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
                    encoding="utf-8",
                )
                python_wrapper.chmod(0o700)
                result = subprocess.run(
                    ["bash", "-c", transition_script],
                    cwd=repo,
                    env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_reviewed_release_transition_is_detected_and_validated(self):
        detect_step = self.jobs["detect-release"]["steps"][-1]
        detect = str(detect_step["run"])
        prepare = str(next(
            step["run"]
            for step in self.steps
            if step.get("name") == "Validate protected release commit"
        ))
        self.assertEqual(detect_step["env"]["EVENT_BEFORE"], "${{ github.event.before }}")
        self.assertIn("current_version", detect)
        self.assertIn("previous_version", detect)
        self.assertIn("release_action=publish", detect)
        self.assertIn('EVENT_ACTOR" != "jeter-1', detect)
        self.assertIn('EVENT_TRIGGERING_ACTOR" != "jeter-1', detect)
        self.assertIn('EVENT_REF" != "refs/heads/main', detect)
        self.assertIn("git merge-base --is-ancestor", detect)
        self.assertIn("git diff --quiet", detect)
        self.assertIn(".release/next-version", prepare)
        self.assertNotIn("staged_version", prepare)
        self.assertIn('version="$current_version"', prepare)
        self.assertIn(
            'python scripts/promote_next_release.py --validate-authority "$version"',
            prepare,
        )
        self.assertIn(
            'python scripts/promote_next_release.py --resolve-release-notes "$version"',
            prepare,
        )
        self.assertIn("gh api", prepare)
        self.assertIn("HTTP 404", prepare)
        self.assertIn('--deployed-version "$deployed_version"', prepare)
        self.assertIn('--base-ref "$validation_base"', prepare)
        self.assertIn(
            '--validate-transition "$deployed_version" "$version"',
            prepare,
        )
        self.assertIn("--require-materialized", prepare)
        self.assertIn("actual-release-paths", prepare)
        self.assertIn('git status --porcelain', prepare)
        self.assertNotIn("promote_next_release.py --apply", prepare)
        self.assertNotIn("git commit", prepare)

    def run_release_detector(
        self,
        *,
        subject,
        current_version=CURRENT_REPOSITORY_VERSION,
        previous_version="2.0.0-rc2-dev16",
        previous_staged_version=None,
        current_staged_version=None,
        event_name="push",
        event_actor="jeter-1",
        event_triggering_actor=None,
        event_ref="refs/heads/main",
        event_expected_version=None,
        recovery_run_id="",
        recovery_source_digest="",
        recovery_build_time="",
        manual_target="release",
        release_topology="linear",
        post_release_staged_version=None,
        runtime_drift=False,
        expect_success=True,
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

            git("init", "-b", "main")
            git("config", "user.name", "Detector Fixture")
            git("config", "user.email", "detector-fixture@example.invalid")
            config.write_text(
                f'version: "{previous_version}"\n',
                encoding="utf-8",
            )
            if previous_staged_version is not None:
                declaration = root / ".release" / "next-version"
                declaration.parent.mkdir()
                declaration.write_text(
                    f"{previous_staged_version}\n", encoding="utf-8"
                )
            git("add", "-A")
            git("commit", "-m", "Establish previous Engineering version")
            before = git("rev-parse", "HEAD")

            if release_topology == "feature_merge":
                git("switch", "-c", "reviewed-release")
            elif release_topology != "linear":
                raise AssertionError(
                    f"unsupported release topology: {release_topology}"
                )
            config.write_text(
                f'version: "{current_version}"\n',
                encoding="utf-8",
            )
            declaration = root / ".release" / "next-version"
            if current_staged_version is not None:
                declaration.parent.mkdir(exist_ok=True)
                declaration.write_text(
                    f"{current_staged_version}\n", encoding="utf-8"
                )
            elif declaration.exists():
                declaration.unlink()
            git("add", "-A")
            git("commit", "--allow-empty", "-m", subject)
            feature_release_sha = git("rev-parse", "HEAD")
            if release_topology == "feature_merge":
                git("switch", "main")
                git(
                    "merge",
                    "--no-ff",
                    "reviewed-release",
                    "-m",
                    "Merge reviewed release pull request",
                )
                release_sha = git("rev-parse", "HEAD")
            else:
                release_sha = feature_release_sha

            manual_release_sha = release_sha
            if manual_target == "nonancestor":
                git("switch", "-c", "unrelated-release", before)
                config.write_text(
                    f'version: "{current_version}"\n',
                    encoding="utf-8",
                )
                git("add", "-A")
                git("commit", "--allow-empty", "-m", "Unrelated release candidate")
                manual_release_sha = git("rev-parse", "HEAD")
                git("switch", "main")
            elif manual_target == "unavailable":
                manual_release_sha = "a" * 40
            elif manual_target == "malformed":
                manual_release_sha = "not-a-commit"
            elif manual_target == "feature_commit":
                if release_topology != "feature_merge":
                    raise AssertionError(
                        "feature_commit requires the feature_merge topology"
                    )
                manual_release_sha = feature_release_sha
            elif manual_target != "release":
                raise AssertionError(f"unsupported manual target: {manual_target}")

            if event_name == "workflow_dispatch":
                recovery = root / ".github" / "workflows" / "recovery.txt"
                recovery.parent.mkdir(parents=True, exist_ok=True)
                recovery.write_text("reviewed publication recovery\n", encoding="utf-8")
                if runtime_drift:
                    drift = root / "hass_mcp_engineering_beta" / "runtime-drift.txt"
                    drift.write_text("drift\n", encoding="utf-8")
                if post_release_staged_version is not None:
                    declaration = root / ".release" / "next-version"
                    declaration.parent.mkdir(parents=True, exist_ok=True)
                    declaration.write_text(
                        f"{post_release_staged_version}\n", encoding="utf-8"
                    )
                git("add", "-A")
                git("commit", "-m", "Add reviewed publication recovery")

            trigger_sha = git("rev-parse", "HEAD")
            git("remote", "add", "origin", str(root))

            output = root / "github-output"
            summary = root / "github-summary"
            environment = os.environ.copy()
            environment.update(
                {
                    "EVENT_ACTOR": event_actor,
                    "EVENT_BEFORE": before,
                    "EVENT_EXPECTED_VERSION": (
                        event_expected_version
                        if event_expected_version is not None
                        else current_version
                    ),
                    "EVENT_NAME": event_name,
                    "EVENT_REF": event_ref,
                    "EVENT_RELEASE_SHA": manual_release_sha,
                    "EVENT_RECOVERY_BUILD_TIME": recovery_build_time,
                    "EVENT_RECOVERY_RUN_ID": recovery_run_id,
                    "EVENT_RECOVERY_SOURCE_DIGEST": recovery_source_digest,
                    "EVENT_TRIGGERING_ACTOR": (
                        event_triggering_actor
                        if event_triggering_actor is not None
                        else event_actor
                    ),
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "TRIGGER_SHA": trigger_sha,
                }
            )
            detector = str(self.jobs["detect-release"]["steps"][-1]["run"])
            result = subprocess.run(
                [bash, "-c", detector],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            if expect_success:
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
            values = (
                assignment_lines(output.read_text(encoding="utf-8"))
                if output.exists()
                else {}
            )
            self.assertEqual(git("tag", "--list"), "")
            return (
                values,
                summary.read_text(encoding="utf-8") if summary.exists() else "",
                result,
                {
                    "before": before,
                    "release_sha": release_sha,
                    "feature_release_sha": feature_release_sha,
                    "requested_release_sha": manual_release_sha,
                    "trigger_sha": trigger_sha,
                },
            )

    def run_prepublication_recheck(
        self,
        *,
        step_name="Revalidate publication authority before registry access",
        move_main_after_trigger=False,
        trigger_contains_staged_declaration=False,
        event_triggering_actor="jeter-1",
        github_release_exists=False,
        github_release_probe_error=False,
        git_tag_probe_error=False,
        present_git_tag=False,
        present_registry_tags=(),
        registry_probe_error=False,
    ):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute the pre-publication guard")
        step = next(
            item
            for item in self.steps
            if item.get("name")
            == step_name
        )
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

            git("init", "-b", "main")
            git("config", "user.name", "Pre-publication Fixture")
            git("config", "user.email", "prepublication-fixture@example.invalid")
            config.write_text('version: "2.2.0-beta.52"\n', encoding="utf-8")
            git("add", "-A")
            git("commit", "-m", "Establish previous version")

            config.write_text('version: "2.2.0-beta.53"\n', encoding="utf-8")
            git("add", "-A")
            git("commit", "-m", "Merge reviewed Beta 53 release")
            release_sha = git("rev-parse", "HEAD")

            recovery = root / ".github" / "workflows" / "recovery.txt"
            recovery.parent.mkdir(parents=True)
            recovery.write_text("reviewed publication recovery\n", encoding="utf-8")
            if trigger_contains_staged_declaration:
                declaration = root / ".release" / "next-version"
                declaration.parent.mkdir()
                declaration.write_text("2.2.0-beta.54\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-m", "Add reviewed publication recovery")
            trigger_sha = git("rev-parse", "HEAD")

            if move_main_after_trigger:
                later = root / "docs" / "later-main-change.md"
                later.parent.mkdir(exist_ok=True)
                later.write_text("later protected-main change\n", encoding="utf-8")
                git("add", "-A")
                git("commit", "-m", "Advance protected main after preparation")
            current_main_sha = git("rev-parse", "HEAD")

            helper = root / "scripts" / ANCESTOR_GUARD_PATH.name
            helper.parent.mkdir()
            shutil.copy2(ANCESTOR_GUARD_PATH, helper)
            registry_helper = root / "scripts" / TAG_GUARD_PATH.name
            shutil.copy2(TAG_GUARD_PATH, registry_helper)
            registry_log = root / "registry-probes.txt"
            fake_docker = root / "fake-docker"
            fake_docker.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[:3] != ["buildx", "imagetools", "inspect"] or len(args) != 4:
    raise SystemExit(f"unexpected docker arguments: {args!r}")
image = args[3]
with Path(os.environ["MOCK_REGISTRY_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(image + "\\n")
if image in set(filter(None, os.environ.get("MOCK_PRESENT_TAGS", "").split(","))):
    print("synthetic existing manifest")
    raise SystemExit(0)
if os.environ.get("MOCK_REGISTRY_PROBE_ERROR") == "true":
    print("synthetic registry transport failure", file=sys.stderr)
    raise SystemExit(2)
print("manifest unknown", file=sys.stderr)
raise SystemExit(1)
""",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            release_tag = "v2.2.0-beta.53"
            if present_git_tag:
                git("tag", release_tag)
            initial_tags = git("tag", "--list")
            git("remote", "add", "origin", str(root))
            git("switch", "--detach", release_sha)

            fake_git = root / "git"
            fake_git.write_text(
                """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args[:3] == ["ls-remote", "--exit-code", "--tags"] and os.environ.get("MOCK_GIT_TAG_PROBE_ERROR") == "true":
    print("synthetic Git tag transport failure", file=sys.stderr)
    raise SystemExit(128)
real_git = os.environ["REAL_GIT"]
os.execv(real_git, [real_git, *args])
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            fake_gh = root / "gh"
            fake_gh.write_text(
                """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if not args or args[0] != "api":
    raise SystemExit(f"unexpected gh arguments: {args!r}")
if os.environ.get("MOCK_GITHUB_RELEASE_EXISTS") == "true":
    print('{"tag_name":"v2.2.0-beta.53"}')
    raise SystemExit(0)
if os.environ.get("MOCK_GITHUB_RELEASE_PROBE_ERROR") == "true":
    print("synthetic GitHub Release transport failure", file=sys.stderr)
    raise SystemExit(2)
print("gh: Not Found (HTTP 404)", file=sys.stderr)
raise SystemExit(1)
""",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            result = subprocess.run(
                [bash, "-c", str(step["run"])],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "DETECTED_EXPECTED_VERSION": "2.2.0-beta.53",
                    "DETECTED_RELEASE_MODE": "manual_recovery",
                    "DETECTED_RELEASE_SHA": release_sha,
                    "EVENT_ACTOR": "jeter-1",
                    "EVENT_REF": "refs/heads/main",
                    "EVENT_TRIGGERING_ACTOR": event_triggering_actor,
                    "GH_TOKEN": "synthetic-github-token",
                    "GITHUB_REPOSITORY": "jeter-1/hass-mcp-admin",
                    "IMAGE_REPOSITORY": IMAGE,
                    "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                    "PREPARED_RELEASE_SHA": release_sha,
                    "PREPARED_RELEASE_TAG": release_tag,
                    "PREPARED_VERSION": "2.2.0-beta.53",
                    "REAL_GIT": shutil.which("git") or "git",
                    "RUNNER_TEMP": str(root),
                    "TRIGGER_SHA": trigger_sha,
                    "DOCKER_CLI": str(fake_docker),
                    "MOCK_GITHUB_RELEASE_EXISTS": str(
                        github_release_exists
                    ).lower(),
                    "MOCK_GITHUB_RELEASE_PROBE_ERROR": str(
                        github_release_probe_error
                    ).lower(),
                    "MOCK_GIT_TAG_PROBE_ERROR": str(
                        git_tag_probe_error
                    ).lower(),
                    "MOCK_PRESENT_TAGS": ",".join(present_registry_tags),
                    "MOCK_REGISTRY_PROBE_ERROR": str(
                        registry_probe_error
                    ).lower(),
                    "MOCK_REGISTRY_LOG": str(registry_log),
                },
            )
            self.assertEqual(git("tag", "--list"), initial_tags)
            return result, {
                "release_sha": release_sha,
                "trigger_sha": trigger_sha,
                "current_main_sha": current_main_sha,
                "registry_probes": (
                    registry_log.read_text(encoding="utf-8").splitlines()
                    if registry_log.exists()
                    else []
                ),
            }

    def run_annotated_tag_finalization(
        self,
        *,
        manual_recovery=False,
        main_sha=None,
        trigger_sha=None,
        main_is_descendant=True,
        main_first_parent=True,
        engineering_drift=False,
        main_has_staged_declaration=False,
        main_version=CURRENT_REPOSITORY_VERSION,
    ):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute annotated-tag finalization")
        release_sha = "d" * 40
        release_tag = f"v{CURRENT_REPOSITORY_VERSION}"
        step = next(
            item
            for item in self.steps
            if item.get("name") == "Finalize release commit and annotated tag"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = root / "git"
            fake_git.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["rev-parse", "HEAD"]:
    print(os.environ["RELEASE_SHA"])
elif args == ["rev-parse", "refs/remotes/origin/main"]:
    print(os.environ["MOCK_MAIN_SHA"])
elif args[:2] == ["rev-parse", f"{os.environ['RELEASE_TAG']}^{{commit}}"]:
    print(os.environ["RELEASE_SHA"])
elif args[:2] == ["config", "user.name"] or args[:2] == ["config", "user.email"]:
    pass
elif args[:3] == ["config", "--get", "user.name"]:
    print("github-actions[bot]")
elif args[:3] == ["config", "--get", "user.email"]:
    print("41898282+github-actions[bot]@users.noreply.github.com")
elif args[:2] == ["tag", "-a"]:
    pass
elif args == ["fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main"]:
    pass
elif args[:2] == ["cat-file", "-e"]:
    if args[2].endswith(":.release/next-version"):
        raise SystemExit(
            0 if os.environ["MOCK_MAIN_HAS_STAGED_DECLARATION"] == "true" else 1
        )
elif args[:2] == ["merge-base", "--is-ancestor"]:
    raise SystemExit(0 if os.environ["MOCK_MAIN_IS_DESCENDANT"] == "true" else 1)
elif args[:2] == ["rev-list", "--first-parent"]:
    if os.environ["MOCK_MAIN_FIRST_PARENT"] == "true":
        print(os.environ["RELEASE_SHA"])
elif args[:2] == ["diff", "--quiet"]:
    raise SystemExit(1 if os.environ["MOCK_ENGINEERING_DRIFT"] == "true" else 0)
elif args[0] == "show" and args[1].endswith(":hass_mcp_engineering_beta/config.yaml"):
    print(f'version: "{os.environ["MOCK_MAIN_VERSION"]}"')
elif args[:3] == ["ls-remote", "--exit-code", "--tags"]:
    raise SystemExit(2)
elif args[:2] == ["ls-remote", "origin"] and args[2].endswith("^{}"):
    print(os.environ["RELEASE_SHA"], args[2])
elif args[:2] == ["push", "origin"]:
    with Path(os.environ["MOCK_GIT_WRITE_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write("push " + " ".join(args[2:]) + "\\n")
else:
    raise SystemExit(f"unexpected git arguments: {args!r}")
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            helper = root / "scripts" / ANCESTOR_GUARD_PATH.name
            helper.parent.mkdir()
            shutil.copy2(ANCESTOR_GUARD_PATH, helper)
            output = root / "github-output"
            write_log = root / "git-writes.txt"
            current_main_sha = main_sha or release_sha
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{root}{os.pathsep}{environment['PATH']}",
                    "GITHUB_OUTPUT": str(output),
                    "RUNNER_TEMP": str(root),
                    "DETECTED_EXPECTED_VERSION": CURRENT_REPOSITORY_VERSION,
                    "DETECTED_RELEASE_MODE": (
                        "manual_recovery" if manual_recovery else "protected_main_push"
                    ),
                    "DETECTED_RELEASE_SHA": release_sha,
                    "EVENT_ACTOR": "jeter-1",
                    "EVENT_REF": "refs/heads/main",
                    "EVENT_TRIGGERING_ACTOR": "jeter-1",
                    "TRIGGER_SHA": trigger_sha or current_main_sha,
                    "RELEASE_SHA": release_sha,
                    "RELEASE_TAG": release_tag,
                    "VERSION": CURRENT_REPOSITORY_VERSION,
                    "BUILD_TIME": "2026-08-06T00:00:00Z",
                    "IMAGE_DIGEST": f"sha256:{'e' * 64}",
                    "MOCK_MAIN_SHA": current_main_sha,
                    "MOCK_MAIN_IS_DESCENDANT": str(main_is_descendant).lower(),
                    "MOCK_MAIN_FIRST_PARENT": str(main_first_parent).lower(),
                    "MOCK_ENGINEERING_DRIFT": str(engineering_drift).lower(),
                    "MOCK_MAIN_HAS_STAGED_DECLARATION": str(
                        main_has_staged_declaration
                    ).lower(),
                    "MOCK_MAIN_VERSION": main_version,
                    "MOCK_GIT_WRITE_LOG": str(write_log),
                }
            )
            result = subprocess.run(
                [bash, "-c", str(step["run"])],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            return {
                "result": result,
                "outputs": (
                    assignment_lines(output.read_text(encoding="utf-8"))
                    if output.exists()
                    else {}
                ),
                "writes": (
                    write_log.read_text(encoding="utf-8").splitlines()
                    if write_log.exists()
                    else []
                ),
            }

    def run_github_release_finalization(
        self,
        *,
        existing_release=False,
        main_sha=None,
        main_is_descendant=True,
        tag_sha=None,
        manual_recovery=False,
        trigger_sha=None,
        main_first_parent=True,
        engineering_drift=False,
        main_has_staged_declaration=False,
        main_version=CURRENT_REPOSITORY_VERSION,
        event_actor="jeter-1",
        event_triggering_actor="jeter-1",
        event_ref="refs/heads/main",
    ):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute release finalization")
        release_sha = "d" * 40
        release_tag = f"v{CURRENT_REPOSITORY_VERSION}"
        step = next(
            item
            for item in self.steps
            if item.get("name") == "Create and verify GitHub Release"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "docs" / "V2_2_0_BETA22_RELEASE_NOTES.md"
            notes.parent.mkdir()
            notes.write_text(
                f"# {CURRENT_REPOSITORY_VERSION} release notes\n\n"
                "Synthetic offline release fixture.\n",
                encoding="utf-8",
            )
            fake_git = root / "git"
            fake_git.write_text(
                """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args == ["fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main"]:
    pass
elif args == ["rev-parse", "refs/remotes/origin/main"]:
    print(os.environ["MOCK_MAIN_SHA"])
elif args[:2] == ["cat-file", "-e"]:
    if args[2].endswith(":.release/next-version"):
        raise SystemExit(
            0 if os.environ["MOCK_MAIN_HAS_STAGED_DECLARATION"] == "true" else 1
        )
elif args[:2] == ["merge-base", "--is-ancestor"]:
    raise SystemExit(0 if os.environ["MOCK_MAIN_IS_DESCENDANT"] == "true" else 1)
elif args[:2] == ["rev-list", "--first-parent"]:
    if os.environ["MOCK_MAIN_FIRST_PARENT"] == "true":
        print(os.environ["RELEASE_SHA"])
elif args[:2] == ["diff", "--quiet"]:
    raise SystemExit(1 if os.environ["MOCK_ENGINEERING_DRIFT"] == "true" else 0)
elif args[0] == "show" and args[1].endswith(":hass_mcp_engineering_beta/config.yaml"):
    print(f'version: "{os.environ["MOCK_MAIN_VERSION"]}"')
elif args[:2] == ["ls-remote", "origin"] and args[2].startswith("refs/tags/"):
    print(os.environ["MOCK_TAG_SHA"], args[2])
else:
    raise SystemExit(f"unexpected git arguments: {args!r}")
""",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            helper = root / "scripts" / ANCESTOR_GUARD_PATH.name
            helper.parent.mkdir()
            shutil.copy2(ANCESTOR_GUARD_PATH, helper)
            (root / "python").symlink_to(sys.executable)
            fake_gh = root / "gh"
            fake_gh.write_text(
                """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state = Path(os.environ["MOCK_RELEASE_STATE"])
if args and args[0] == "api":
    if state.exists():
        print(state.read_text(encoding="utf-8"))
        raise SystemExit(0)
    if os.environ.get("MOCK_EXISTING_RELEASE") == "true":
        print(json.dumps({"tag_name": os.environ["RELEASE_TAG"]}))
        raise SystemExit(0)
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    raise SystemExit(1)
if args[:2] == ["release", "create"]:
    if "--verify-tag" not in args:
        raise SystemExit("missing --verify-tag")
    def option(name):
        return args[args.index(name) + 1]
    tag = args[2]
    title = option("--title")
    body = Path(option("--notes-file")).read_text(encoding="utf-8")
    url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/releases/tag/{tag}"
    payload = {
        "id": 220022,
        "tag_name": tag,
        "name": title,
        "html_url": url,
        "body": body,
        "draft": False,
        "prerelease": "--prerelease" in args,
        "published_at": "2026-08-06T00:00:00Z",
        "target_commitish": option("--target"),
    }
    state.write_text(json.dumps(payload), encoding="utf-8")
    print(url)
    raise SystemExit(0)
raise SystemExit(f"unexpected gh arguments: {args!r}")
""",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            output = root / "github-output"
            state = root / "release-state.json"
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{root}{os.pathsep}{environment['PATH']}",
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_REPOSITORY": "jeter-1/hass-mcp-admin",
                    "GH_TOKEN": "synthetic-release-token",
                    "RUNNER_TEMP": str(root),
                    "RELEASE_SHA": release_sha,
                    "RELEASE_TAG": release_tag,
                    "RELEASE_NOTES_PATH": str(notes),
                    "SOURCE_MAIN_SHA": release_sha,
                    "VERSION": CURRENT_REPOSITORY_VERSION,
                    "BUILD_TIME": "2026-08-06T00:00:00Z",
                    "IMAGE_DIGEST": f"sha256:{'e' * 64}",
                    "MOCK_RELEASE_STATE": str(state),
                    "MOCK_EXISTING_RELEASE": str(existing_release).lower(),
                    "MOCK_MAIN_SHA": main_sha or release_sha,
                    "MOCK_MAIN_IS_DESCENDANT": str(main_is_descendant).lower(),
                    "MOCK_MAIN_FIRST_PARENT": str(main_first_parent).lower(),
                    "MOCK_ENGINEERING_DRIFT": str(engineering_drift).lower(),
                    "MOCK_MAIN_HAS_STAGED_DECLARATION": str(
                        main_has_staged_declaration
                    ).lower(),
                    "MOCK_MAIN_VERSION": main_version,
                    "MOCK_TAG_SHA": tag_sha or release_sha,
                    "DETECTED_EXPECTED_VERSION": CURRENT_REPOSITORY_VERSION,
                    "DETECTED_RELEASE_MODE": (
                        "manual_recovery" if manual_recovery else "protected_main_push"
                    ),
                    "DETECTED_RELEASE_SHA": release_sha,
                    "EVENT_ACTOR": event_actor,
                    "EVENT_REF": event_ref,
                    "EVENT_TRIGGERING_ACTOR": event_triggering_actor,
                    "TRIGGER_SHA": trigger_sha or main_sha or release_sha,
                }
            )
            result = subprocess.run(
                [bash, "-c", str(step["run"])],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            return {
                "result": result,
                "outputs": (
                    assignment_lines(output.read_text(encoding="utf-8"))
                    if output.exists()
                    else {}
                ),
                "release": (
                    json.loads(state.read_text(encoding="utf-8"))
                    if state.exists()
                    else None
                ),
            }

    def test_release_detector_routes_only_final_reviewed_version_state(self):
        publish_values, publish_summary, _, identities = self.run_release_detector(
            subject="Merge reviewed release pull request",
            current_version="2.0.0-rc2-dev16",
            previous_version="2.0.0-rc2-dev15",
        )
        self.assertEqual(
            publish_values,
            {
                "release_action": "publish",
                "release_sha": identities["release_sha"],
                "validation_base": identities["before"],
                "expected_version": "2.0.0-rc2-dev16",
                "release_mode": "protected_main_push",
                "source_mode": "build",
                "recovery_run_id": "",
                "recovery_source_digest": "",
                "recovery_build_time": "",
            },
        )
        self.assertIn("eligible for protected_main_push publication", publish_summary)

        none_values, none_summary, _, _ = self.run_release_detector(
            subject="Ordinary source correction",
            current_version="2.0.0-rc2-dev16",
            previous_version="2.0.0-rc2-dev16",
        )
        self.assertEqual(none_values, {"release_action": "none"})
        self.assertIn("No reviewed Engineering version transition", none_summary)

    def test_release_detector_rejects_unmaterialized_or_legacy_markers(self):
        cases = (
            {
                "subject": "Merge unmaterialized declaration",
                "previous_version": "2.0.0-rc2-dev15",
                "current_version": "2.0.0-rc2-dev15",
                "current_staged_version": "2.0.0-rc2-dev16",
            },
            {
                "subject": "Consume a legacy staged declaration",
                "current_version": "2.0.0-rc2-dev16",
                "previous_version": "2.0.0-rc2-dev15",
                "previous_staged_version": "2.0.0-rc2-dev16",
            },
        )
        for values in cases:
            with self.subTest(subject=values["subject"]):
                output, _summary, result = self.run_release_detector(
                    expect_success=False,
                    **values,
                )[:3]
                self.assertEqual(output, {})
                self.assertIn("::error::", result.stdout)

    def test_manual_recovery_selects_exact_unchanged_release_commit(self):
        values, summary, _result, identities = self.run_release_detector(
            subject="Merge reviewed release pull request",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
        )
        self.assertNotEqual(identities["release_sha"], identities["trigger_sha"])
        self.assertEqual(
            values,
            {
                "release_action": "publish",
                "release_sha": identities["release_sha"],
                "validation_base": identities["before"],
                "expected_version": "2.2.0-beta.53",
                "release_mode": "manual_recovery",
                "source_mode": "build",
                "recovery_run_id": "",
                "recovery_source_digest": "",
                "recovery_build_time": "",
            },
        )
        self.assertIn("eligible for manual_recovery publication", summary)

    def test_manual_recovery_accepts_only_complete_bounded_digest_authority(self):
        digest = f"sha256:{'2' * 64}"
        values, summary, _result, identities = self.run_release_detector(
            subject="Merge reviewed release pull request",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
            recovery_run_id="33379623142",
            recovery_source_digest=digest,
            recovery_build_time="2026-08-31T10:10:06Z",
        )
        self.assertEqual(values["release_sha"], identities["release_sha"])
        self.assertEqual(values["source_mode"], "resume_digest")
        self.assertEqual(values["recovery_run_id"], "33379623142")
        self.assertEqual(values["recovery_source_digest"], digest)
        self.assertEqual(
            values["recovery_build_time"], "2026-08-31T10:10:06Z"
        )
        self.assertIn("using resume_digest", summary)

        invalid_cases = (
            {"recovery_run_id": "33379623142"},
            {
                "recovery_run_id": "0",
                "recovery_source_digest": digest,
                "recovery_build_time": "2026-08-31T10:10:06Z",
            },
            {
                "recovery_run_id": "33379623142",
                "recovery_source_digest": "sha256:not-a-digest",
                "recovery_build_time": "2026-08-31T10:10:06Z",
            },
            {
                "recovery_run_id": "33379623142",
                "recovery_source_digest": digest,
                "recovery_build_time": "2026-08-31 10:10:06",
            },
        )
        for delta in invalid_cases:
            with self.subTest(delta=delta):
                rejected, _summary, result, _identities = self.run_release_detector(
                    subject="Reject invalid digest recovery",
                    current_version="2.2.0-beta.53",
                    previous_version="2.2.0-beta.52",
                    event_name="workflow_dispatch",
                    expect_success=False,
                    **delta,
                )
                self.assertEqual(rejected, {})
                self.assertIn("::error::", result.stdout)

    def test_manual_recovery_requires_protected_main_first_parent_commit(self):
        values, _summary, _result, identities = self.run_release_detector(
            subject="Materialize reviewed release on feature branch",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
            release_topology="feature_merge",
        )
        self.assertEqual(values["release_sha"], identities["release_sha"])
        self.assertNotEqual(
            identities["release_sha"], identities["feature_release_sha"]
        )

        rejected, _summary, result, rejected_identities = self.run_release_detector(
            subject="Materialize reviewed release on feature branch",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
            release_topology="feature_merge",
            manual_target="feature_commit",
            expect_success=False,
        )
        self.assertEqual(rejected, {})
        self.assertEqual(
            rejected_identities["requested_release_sha"],
            rejected_identities["feature_release_sha"],
        )
        self.assertIn("not on protected main's first-parent history", result.stdout)

    def test_manual_recovery_rejects_declaration_added_after_release(self):
        values, _summary, result, _identities = self.run_release_detector(
            subject="Merge reviewed release pull request",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
            post_release_staged_version="2.2.0-beta.54",
            expect_success=False,
        )
        self.assertEqual(values, {})
        self.assertIn(
            "Current protected main contains an unmaterialized release declaration",
            result.stdout,
        )

    def test_manual_recovery_rejects_missing_authority_or_drift(self):
        cases = (
            {"event_actor": "someone-else"},
            {"event_triggering_actor": "someone-else"},
            {"event_ref": "refs/heads/feature"},
            {"manual_target": "malformed"},
            {"manual_target": "unavailable"},
            {"manual_target": "nonancestor"},
            {"event_expected_version": "2.2.0-beta.54"},
            {"event_expected_version": "not/version"},
            {"runtime_drift": True},
            {"previous_version": "2.2.0-beta.53"},
            {"current_staged_version": "2.2.0-beta.54"},
            {"post_release_staged_version": "2.2.0-beta.54"},
        )
        for delta in cases:
            with self.subTest(**delta):
                case = dict(delta)
                values, _summary, result, _identities = self.run_release_detector(
                    subject="Synthetic manual recovery case",
                    current_version="2.2.0-beta.53",
                    previous_version=case.pop(
                        "previous_version", "2.2.0-beta.52"
                    ),
                    current_staged_version=case.pop(
                        "current_staged_version", None
                    ),
                    post_release_staged_version=case.pop(
                        "post_release_staged_version", None
                    ),
                    event_name="workflow_dispatch",
                    expect_success=False,
                    **case,
                )
                self.assertEqual(values, {})
                self.assertIn("::error::", result.stdout)

    def test_promote_checks_out_only_the_detected_release_commit(self):
        checkout = action_steps(self.promote, "actions/checkout")
        self.assertEqual(len(checkout), 1)
        self.assertEqual(
            checkout[0]["with"]["ref"],
            "${{ needs.detect-release.outputs.release_sha }}",
        )
        prepare = next(
            step for step in self.steps
            if step.get("name") == "Validate protected release commit"
        )
        self.assertEqual(
            prepare["env"]["DETECTED_RELEASE_SHA"],
            "${{ needs.detect-release.outputs.release_sha }}",
        )
        script = str(prepare["run"])
        self.assertIn('source_main_sha" != "$DETECTED_RELEASE_SHA', script)
        self.assertIn('DETECTED_RELEASE_MODE" == "manual_recovery', script)
        self.assertIn("git diff --quiet", script)

    def test_manual_recovery_rechecks_request_and_rerun_actor(self):
        detector = self.jobs["detect-release"]["steps"][-1]
        prepare = next(
            step
            for step in self.steps
            if step.get("name") == "Validate protected release commit"
        )
        recheck = next(
            step
            for step in self.steps
            if step.get("name")
            == "Revalidate publication authority before registry access"
        )
        final_image_recheck = next(
            step
            for step in self.steps
            if step.get("name")
            == "Revalidate publication authority before final image tags"
        )
        finalize = next(
            step
            for step in self.steps
            if step.get("name") == "Finalize release commit and annotated tag"
        )
        release = next(
            step
            for step in self.steps
            if step.get("name") == "Create and verify GitHub Release"
        )
        for step in (
            detector,
            prepare,
            recheck,
            final_image_recheck,
            finalize,
            release,
        ):
            with self.subTest(step=step.get("name", "detect")):
                self.assertEqual(
                    step["env"]["EVENT_TRIGGERING_ACTOR"],
                    "${{ github.triggering_actor }}",
                )
                self.assertIn(
                    'EVENT_TRIGGERING_ACTOR" != "jeter-1',
                    str(step["run"]),
                )

        rejected, _summary, result, _identities = self.run_release_detector(
            subject="Synthetic non-owner rerun",
            current_version="2.2.0-beta.53",
            previous_version="2.2.0-beta.52",
            event_name="workflow_dispatch",
            event_actor="jeter-1",
            event_triggering_actor="repository-writer",
            expect_success=False,
        )
        self.assertEqual(rejected, {})
        self.assertIn("request or rerun", result.stdout)

    def test_github_release_finalization_succeeds_with_exact_identity(self):
        outcome = self.run_github_release_finalization()
        result = outcome["result"]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            outcome["outputs"],
            {
                "github_release_created": "true",
                "github_release_verified": "true",
                "release_complete": "true",
                "release_url": (
                    "https://github.com/jeter-1/hass-mcp-admin/releases/tag/"
                    f"v{CURRENT_REPOSITORY_VERSION}"
                ),
            },
        )
        release = outcome["release"]
        self.assertEqual(release["target_commitish"], "d" * 40)
        self.assertTrue(release["prerelease"])
        self.assertIn("Immutable publication identity", release["body"])
        self.assertIn(f"sha256:{'e' * 64}", release["body"])

    def test_github_release_finalization_fails_closed_on_drift(self):
        cases = (
            {"existing_release": True},
            {"main_sha": "a" * 40, "main_is_descendant": False},
            {"tag_sha": "b" * 40},
        )
        for values in cases:
            with self.subTest(**values):
                outcome = self.run_github_release_finalization(**values)
                self.assertNotEqual(outcome["result"].returncode, 0)
                self.assertEqual(outcome["outputs"], {})

    def test_manual_recovery_main_drift_after_image_tags_blocks_metadata_writes(self):
        trigger_sha = "c" * 40
        moved_main_sha = "a" * 40

        tag_outcome = self.run_annotated_tag_finalization(
            manual_recovery=True,
            trigger_sha=trigger_sha,
            main_sha=moved_main_sha,
        )
        self.assertNotEqual(tag_outcome["result"].returncode, 0)
        self.assertIn(
            "Protected main moved before the annotated-tag write",
            tag_outcome["result"].stdout,
        )
        self.assertEqual(tag_outcome["writes"], [])
        self.assertEqual(tag_outcome["outputs"], {})

        release_outcome = self.run_github_release_finalization(
            manual_recovery=True,
            trigger_sha=trigger_sha,
            main_sha=moved_main_sha,
        )
        self.assertNotEqual(release_outcome["result"].returncode, 0)
        self.assertIn(
            "Protected main moved before the GitHub Release write",
            release_outcome["result"].stdout,
        )
        self.assertIsNone(release_outcome["release"])
        self.assertEqual(release_outcome["outputs"], {})

        names = [step.get("name", "") for step in self.steps]
        image_verify_index = names.index("Verify immutable release tags anonymously")
        tag_index = names.index("Finalize release commit and annotated tag")
        release_index = names.index("Create and verify GitHub Release")
        self.assertLess(image_verify_index, tag_index)
        self.assertLess(tag_index, release_index)

    def test_manual_recovery_current_authority_allows_metadata_writes(self):
        current_main_sha = "a" * 40
        tag_outcome = self.run_annotated_tag_finalization(
            manual_recovery=True,
            trigger_sha=current_main_sha,
            main_sha=current_main_sha,
        )
        self.assertEqual(
            tag_outcome["result"].returncode,
            0,
            tag_outcome["result"].stderr,
        )
        self.assertEqual(len(tag_outcome["writes"]), 1)
        self.assertEqual(
            tag_outcome["outputs"],
            {"tag_created": "true", "tag_verified": "true"},
        )

        release_outcome = self.run_github_release_finalization(
            manual_recovery=True,
            trigger_sha=current_main_sha,
            main_sha=current_main_sha,
        )
        self.assertEqual(
            release_outcome["result"].returncode,
            0,
            release_outcome["result"].stderr,
        )
        self.assertIsNotNone(release_outcome["release"])
        self.assertEqual(release_outcome["outputs"]["release_complete"], "true")

    def test_publication_has_no_second_pull_request_or_branch_write(self):
        detect = str(self.jobs["detect-release"]["steps"][-1]["run"])
        scripts = "\n".join(
            script for job in self.jobs.values() for script in run_steps(job)
        )
        self.assertNotIn("release_action=prepare", detect)
        self.assertNotIn("docker", detect)
        self.assertNotIn("git tag", detect)
        self.assertNotIn("git push", detect)
        self.assertNotIn("gh pr create", scripts)
        self.assertNotIn("git switch -c", scripts)
        self.assertNotIn("HEAD:refs/heads/main", scripts)

    def test_only_publication_has_write_permissions(self):
        writers = {
            name: job.get("permissions", {})
            for name, job in self.jobs.items()
            if "write" in (job.get("permissions") or {}).values()
        }
        self.assertEqual(
            writers,
            {
                "promote": {
                    "actions": "read",
                    "contents": "write",
                    "packages": "write",
                },
            },
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
        self.assertNotIn("gh release create", scripts)

    def test_protected_release_commit_is_validated_before_registry_login(self):
        names = [step.get("name", "") for step in self.steps]
        prepare_index = names.index("Validate protected release commit")
        qemu_index = names.index("Set up QEMU")
        recheck_index = names.index(
            "Revalidate publication authority before registry access"
        )
        login_index = names.index("Log in to GHCR")
        build_index = names.index(
            "Build release commit without a temporary tag"
        )
        source_index = names.index("Resolve immutable source image")
        publish_index = names.index(
            "Create release image tags with registry-enforced preconditions"
        )
        source_verify_index = names.index(
            "Verify digest-addressed image architectures and provenance anonymously"
        )
        final_recheck_index = names.index(
            "Revalidate publication authority before final image tags"
        )
        self.assertLess(prepare_index, login_index)
        self.assertLess(qemu_index, recheck_index)
        self.assertEqual(recheck_index + 1, login_index)
        self.assertEqual(login_index + 1, build_index)
        self.assertEqual(build_index + 1, source_index)
        self.assertEqual(source_index + 1, source_verify_index)
        self.assertEqual(source_verify_index + 1, final_recheck_index)
        self.assertEqual(final_recheck_index + 1, publish_index)
        prepare = str(self.steps[prepare_index]["run"])
        for value in (
            "git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main",
            "actual-release-paths",
            "--require-materialized",
            "git ls-remote --exit-code --tags",
            "scripts/assert_protected_release_ancestor.sh",
            "scripts/assert_registry_tags_absent.sh",
            "python scripts/validate_addon_metadata.py",
            "python -m unittest discover -s tests -v",
            "git diff --check",
        ):
            self.assertIn(value, prepare)
        self.assertIn('date -u +\'%Y-%m-%dT%H:%M:%SZ\'', prepare)
        self.assertNotIn("git push", prepare)
        self.assertNotIn("promote_next_release.py --apply", prepare)
        self.assertNotIn("git commit", prepare)
        self.assertNotIn("expected-release-paths", prepare)

    def test_prepublication_recheck_blocks_main_movement_before_any_write(self):
        accepted, accepted_identities = self.run_prepublication_recheck()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(
            accepted_identities["trigger_sha"],
            accepted_identities["current_main_sha"],
        )
        self.assertEqual(
            accepted_identities["registry_probes"],
            [
                f"{IMAGE}:2.2.0-beta.53",
                f"{IMAGE}:sha-{accepted_identities['release_sha']}",
            ],
        )

        moved, moved_identities = self.run_prepublication_recheck(
            move_main_after_trigger=True
        )
        self.assertNotEqual(moved.returncode, 0)
        self.assertNotEqual(
            moved_identities["trigger_sha"], moved_identities["current_main_sha"]
        )
        self.assertIn("Protected main moved before registry access", moved.stdout)

        staged, staged_identities = self.run_prepublication_recheck(
            trigger_contains_staged_declaration=True
        )
        self.assertNotEqual(staged.returncode, 0)
        self.assertEqual(
            staged_identities["trigger_sha"], staged_identities["current_main_sha"]
        )
        self.assertIn(
            "Current protected main contains an unmaterialized release declaration",
            staged.stdout,
        )

        changed_rerun_actor, changed_actor_identities = (
            self.run_prepublication_recheck(
                event_triggering_actor="repository-writer"
            )
        )
        self.assertNotEqual(changed_rerun_actor.returncode, 0)
        self.assertEqual(changed_actor_identities["registry_probes"], [])
        self.assertIn(
            "authority changed before registry access",
            changed_rerun_actor.stdout,
        )

        existing_git_tag, existing_git_tag_identities = (
            self.run_prepublication_recheck(present_git_tag=True)
        )
        self.assertNotEqual(existing_git_tag.returncode, 0)
        self.assertEqual(existing_git_tag_identities["registry_probes"], [])
        self.assertIn(
            "immutable release tag v2.2.0-beta.53 appeared",
            existing_git_tag.stdout,
        )

        ambiguous_git_tag, ambiguous_git_tag_identities = (
            self.run_prepublication_recheck(git_tag_probe_error=True)
        )
        self.assertNotEqual(ambiguous_git_tag.returncode, 0)
        self.assertEqual(ambiguous_git_tag_identities["registry_probes"], [])
        self.assertIn(
            "Unable to prove that the immutable release tag is absent",
            ambiguous_git_tag.stdout,
        )

        existing_release, existing_release_identities = (
            self.run_prepublication_recheck(github_release_exists=True)
        )
        self.assertNotEqual(existing_release.returncode, 0)
        self.assertEqual(existing_release_identities["registry_probes"], [])
        self.assertIn(
            "GitHub Release for v2.2.0-beta.53 appeared",
            existing_release.stdout,
        )

        ambiguous_release, ambiguous_release_identities = (
            self.run_prepublication_recheck(github_release_probe_error=True)
        )
        self.assertNotEqual(ambiguous_release.returncode, 0)
        self.assertEqual(ambiguous_release_identities["registry_probes"], [])
        self.assertIn(
            "Unable to prove that the GitHub Release is absent",
            ambiguous_release.stdout,
        )

        occupied_version_tag = f"{IMAGE}:2.2.0-beta.53"
        occupied, occupied_identities = self.run_prepublication_recheck(
            present_registry_tags=(occupied_version_tag,)
        )
        self.assertNotEqual(occupied.returncode, 0)
        self.assertEqual(
            occupied_identities["registry_probes"], [occupied_version_tag]
        )
        self.assertIn(
            f"Refusing to overwrite immutable tag {occupied_version_tag}",
            occupied.stdout,
        )

        ambiguous, ambiguous_identities = self.run_prepublication_recheck(
            registry_probe_error=True
        )
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertEqual(
            ambiguous_identities["registry_probes"], [occupied_version_tag]
        )
        self.assertIn(
            "Unable to prove registry tag is absent",
            ambiguous.stdout,
        )

        recheck = next(
            step
            for step in self.steps
            if step.get("name")
            == "Revalidate publication authority before registry access"
        )
        script = str(recheck["run"])
        self.assertIn("git rev-list --first-parent", script)
        self.assertIn("git diff --quiet", script)
        self.assertIn(".release/next-version", script)
        self.assertIn("git ls-remote --exit-code --tags", script)
        self.assertIn("remote_tag_status", script)
        self.assertIn("gh api", script)
        self.assertIn("HTTP 404", script)
        self.assertIn("scripts/assert_registry_tags_absent.sh", script)
        self.assertIn('"${IMAGE_REPOSITORY}:${PREPARED_VERSION}"', script)
        self.assertIn(
            '"${IMAGE_REPOSITORY}:sha-${PREPARED_RELEASE_SHA}"', script
        )
        self.assertNotIn("docker", script)
        self.assertNotIn("gh release", script)
        self.assertNotIn("git push", script)

    def test_final_tag_recheck_blocks_main_movement_after_digest_build(self):
        accepted, _ = self.run_prepublication_recheck(
            step_name="Revalidate publication authority before final image tags"
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        moved, identities = self.run_prepublication_recheck(
            step_name="Revalidate publication authority before final image tags",
            move_main_after_trigger=True,
        )
        self.assertNotEqual(moved.returncode, 0)
        self.assertEqual(identities["registry_probes"], [])
        self.assertIn("Protected main moved before final image tags", moved.stdout)

        names = [step.get("name", "") for step in self.steps]
        final_recheck_index = names.index(
            "Revalidate publication authority before final image tags"
        )
        publish_index = names.index(
            "Create release image tags with registry-enforced preconditions"
        )
        self.assertEqual(final_recheck_index + 1, publish_index)
        final_recheck = str(self.steps[final_recheck_index]["run"])
        for value in (
            "git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main",
            "git rev-list --first-parent",
            "git diff --quiet",
            ".release/next-version",
            "git ls-remote --exit-code --tags",
            "gh api",
            "scripts/assert_registry_tags_absent.sh",
        ):
            self.assertIn(value, final_recheck)
        self.assertNotIn('python "$publisher"', final_recheck)

    def test_later_nonrelease_merge_preserves_release_ancestor(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute the release ancestry guard")
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Release ancestry test"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "release-ancestry@example.invalid"],
                cwd=repo,
                check=True,
            )
            marker = repo / "state.txt"
            marker.write_text("release A\n", encoding="utf-8")
            subprocess.run(["git", "add", "state.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "merge release PR A"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            release_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            marker.write_text("release A\nordinary PR B\n", encoding="utf-8")
            subprocess.run(["git", "add", "state.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "merge ordinary PR B"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            later_main_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()

            preserved = subprocess.run(
                [bash, str(ANCESTOR_GUARD_PATH), release_sha, later_main_sha],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(preserved.returncode, 0, preserved.stderr)
            reversed_history = subprocess.run(
                [bash, str(ANCESTOR_GUARD_PATH), later_main_sha, release_sha],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(reversed_history.returncode, 0)

        publication_scripts = "\n".join(run_steps(self.promote))
        self.assertGreaterEqual(
            publication_scripts.count("scripts/assert_protected_release_ancestor.sh"),
            4,
        )

    def test_one_build_pushes_exact_multiarch_by_digest_before_tagging(self):
        builds = action_steps(self.promote, "docker/build-push-action")
        self.assertEqual(len(builds), 1)
        self.assertEqual(self.promote["timeout-minutes"], 45)
        self.assertEqual(
            self.promote["permissions"],
            {"actions": "read", "contents": "write", "packages": "write"},
        )
        build_only = "needs.detect-release.outputs.source_mode == 'build'"
        self.assertEqual(builds[0]["if"], build_only)
        qemu = next(step for step in self.steps if step.get("name") == "Set up QEMU")
        login = next(step for step in self.steps if step.get("name") == "Log in to GHCR")
        self.assertEqual(qemu["if"], build_only)
        self.assertEqual(login["if"], build_only)
        values = builds[0]["with"]
        self.assertNotIn("push", values)
        self.assertNotIn("tags", values)
        self.assertEqual(
            values["outputs"],
            "type=image,name=ghcr.io/jeter-1/hass-mcp-engineering-beta,"
            "push-by-digest=true,name-canonical=true,push=true",
        )
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
        publisher = next(
            step
            for step in self.steps
            if step.get("name")
            == "Create release image tags with registry-enforced preconditions"
        )
        self.assertEqual(publisher["id"], "publish_tags")
        publish_script = str(publisher["run"])
        self.assertIn(
            'git show "${WORKFLOW_AUTHORITY_SHA}:scripts/'
            'publish_registry_tags_create_only.py"',
            publish_script,
        )
        self.assertIn('python "$publisher"', publish_script)
        self.assertIn("$RUNNER_TEMP/publish_registry_tags_create_only.py", publish_script)
        self.assertIn('--source-digest "$SOURCE_DIGEST"', publish_script)
        sha_target = '--target-tag "sha-${RELEASE_SHA}"'
        version_target = '--target-tag "$VERSION"'
        self.assertIn(sha_target, publish_script)
        self.assertIn(version_target, publish_script)
        self.assertLess(
            publish_script.index(sha_target), publish_script.index(version_target)
        )
        self.assertEqual(
            publisher["env"]["SOURCE_DIGEST"],
            "${{ steps.source.outputs.digest }}",
        )
        self.assertEqual(publisher["env"]["GHCR_TOKEN"], "${{ github.token }}")
        self.assertEqual(publisher["env"]["GHCR_USERNAME"], "${{ github.actor }}")
        self.assertEqual(
            publisher["env"]["WORKFLOW_AUTHORITY_SHA"], "${{ github.sha }}"
        )
        self.assertTrue(CREATE_ONLY_PUBLISHER_PATH.is_file())
        publisher_source = CREATE_ONLY_PUBLISHER_PATH.read_text(encoding="utf-8")
        self.assertIn('headers["If-None-Match"] = "*"', publisher_source)
        self.assertIn("never creates a temporary tag", publisher_source)
        self.assertIn("REGISTRY_CREATE_ONLY_UNSUPPORTED", publisher_source)
        self.assertIn("REGISTRY_CREATE_ONLY_CAPABILITY_AMBIGUOUS", publisher_source)
        self.assertTrue(SOURCE_VERIFIER_PATH.is_file())

    def test_digest_recovery_resolves_existing_source_without_a_build_path(self):
        source = next(
            step for step in self.steps if step.get("name") == "Resolve immutable source image"
        )
        self.assertEqual(source["id"], "source")
        self.assertEqual(
            source["env"]["RECOVERY_DIGEST"],
            "${{ needs.detect-release.outputs.recovery_source_digest }}",
        )
        script = str(source["run"])
        self.assertIn('SOURCE_MODE" == "resume_digest', script)
        self.assertIn('-n "$BUILT_DIGEST"', script)
        self.assertIn("source_image_reused=true", script)
        self.assertIn("source_image_published=false", script)
        self.assertNotIn("docker", script)

        workflow_source = PUBLISH_PATH.read_text(encoding="utf-8")
        self.assertNotIn("docker pull", workflow_source)
        self.assertNotIn("docker image inspect", workflow_source)
        for step in action_steps(self.promote, "docker/build-push-action"):
            self.assertEqual(
                step["if"], "needs.detect-release.outputs.source_mode == 'build'"
            )

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to execute the source resolver")
        digest = f"sha256:{'2' * 64}"
        cases = (
            ("build", digest, "", True, "true", "false"),
            ("resume_digest", "", digest, True, "false", "true"),
            ("resume_digest", digest, digest, False, None, None),
            ("build", "", digest, False, None, None),
            ("unsupported", "", digest, False, None, None),
        )
        for mode, built, recovery, succeeds, published, reused in cases:
            with self.subTest(mode=mode, built=bool(built), recovery=bool(recovery)):
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / "github-output"
                    environment = os.environ.copy()
                    environment.update(
                        {
                            "BUILT_DIGEST": built,
                            "GITHUB_OUTPUT": str(output),
                            "RECOVERY_DIGEST": recovery,
                            "SOURCE_MODE": mode,
                        }
                    )
                    result = subprocess.run(
                        [bash, "-c", script],
                        text=True,
                        capture_output=True,
                        check=False,
                        env=environment,
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    if succeeds:
                        outputs = assignment_lines(output.read_text(encoding="utf-8"))
                        self.assertEqual(outputs["digest"], digest)
                        self.assertEqual(outputs["source_image_published"], published)
                        self.assertEqual(outputs["source_image_reused"], reused)
                    else:
                        self.assertFalse(output.exists())

    def test_failure_after_build_cannot_leave_a_temporary_registry_tag(self):
        build = action_steps(self.promote, "docker/build-push-action")[0]
        values = build["with"]
        self.assertNotIn("tags", values)
        self.assertIn("push-by-digest=true", values["outputs"])
        self.assertIn("name-canonical=true", values["outputs"])
        self.assertEqual(self.promote["timeout-minutes"], 45)

        workflow_source = PUBLISH_PATH.read_text(encoding="utf-8")
        self.assertNotIn("publication-staging-", workflow_source)
        summary = next(
            step
            for step in self.steps
            if step.get("name") == "Write promotion and reconciliation summary"
        )
        self.assertIn(
            "temporary_registry_tag_created: false",
            str(summary["run"]),
        )

    def test_anonymous_verification_precedes_release_finalization(self):
        names = [step.get("name", "") for step in self.steps]
        source_verify_index = names.index(
            "Verify digest-addressed image architectures and provenance anonymously"
        )
        verify_index = names.index(
            "Verify immutable release tags anonymously"
        )
        publish_image_index = names.index(
            "Create release image tags with registry-enforced preconditions"
        )
        push_index = names.index("Finalize release commit and annotated tag")
        release_index = names.index("Create and verify GitHub Release")
        self.assertLess(source_verify_index, publish_image_index)
        self.assertLess(publish_image_index, verify_index)
        self.assertLess(verify_index, push_index)
        self.assertLess(push_index, release_index)
        source_verify = str(self.steps[source_verify_index]["run"])
        for value in (
            'anonymous_config="$RUNNER_TEMP/anonymous-docker"',
            'source_image="${IMAGE_REPOSITORY}@${EXPECTED_DIGEST}"',
            'DOCKER_CONFIG="$anonymous_config"',
            'imagetools inspect --raw',
            "{{json .Image}}",
            "{{json .Provenance}}",
            "{{json .SBOM}}",
            "extract-manifest",
            "verify-source",
            'git show "${WORKFLOW_AUTHORITY_SHA}:scripts/verify_publication_source_image.py"',
            '--image-json "linux/amd64=$amd64_json"',
            '--image-json "linux/arm64=$arm64_json"',
            '--image-json "linux/arm/v7=$armv7_json"',
        ):
            self.assertIn(value, source_verify)
        self.assertNotIn("docker pull", source_verify)
        self.assertNotIn("docker image inspect", source_verify)
        verify = str(self.steps[verify_index]["run"])
        self.assertIn("version_digest", verify)
        self.assertIn("sha_digest", verify)
        push = str(self.steps[push_index]["run"])
        self.assertIn('git push origin "refs/tags/', push)
        self.assertNotIn("HEAD:refs/heads/main", push)
        self.assertNotIn("git push --atomic", push)
        self.assertNotIn("--force", push)
        self.assertIn("scripts/assert_protected_release_ancestor.sh", push)
        self.assertNotIn('"$remote_main_sha" != "$SOURCE_MAIN_SHA"', push)
        self.assertIn('git config user.name "github-actions[bot]"', push)
        self.assertIn('git config user.email "41898282+github-actions[bot]@users.noreply.github.com"', push)
        self.assertIn('git rev-parse "${RELEASE_TAG}^{commit}"', push)
        for value in (
            "git rev-list --first-parent",
            "git diff --quiet",
            ".release/next-version",
            "Protected main moved before the annotated-tag write",
            "git ls-remote --exit-code --tags",
        ):
            self.assertIn(value, push)
        tag_push_index = push.index('git push origin "refs/tags/')
        self.assertNotEqual(
            push.rfind(
                "git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main",
                0,
                tag_push_index,
            ),
            -1,
        )
        self.assertLess(
            push.index("Protected main moved before the annotated-tag write"),
            tag_push_index,
        )
        self.assertGreater(push.index("tag_created=true"), tag_push_index)
        for value in ("Version:", "Source SHA:", "Image digest:", "Build timestamp:"):
            self.assertIn(value, push)

        release = str(self.steps[release_index]["run"])
        for value in (
            "gh release create",
            "--verify-tag",
            "--notes-file",
            "--target",
            "gh api",
            "HTTP 404",
            "Immutable publication identity",
            "OCI manifest digest",
            "SLSA provenance and SBOM attestations: verified",
            "scripts/assert_protected_release_ancestor.sh",
            '"$promoted_tag" != "$RELEASE_SHA"',
            "github_release_created=true",
            "github_release_verified=true",
            "release_complete=true",
            "git rev-list --first-parent",
            "git diff --quiet",
            ".release/next-version",
            "Protected main moved before the GitHub Release write",
        ):
            self.assertIn(value, release)
        release_create_index = release.index("gh release create")
        self.assertNotEqual(
            release.rfind(
                "git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main",
                0,
                release_create_index,
            ),
            -1,
        )
        self.assertLess(
            release.index("Protected main moved before the GitHub Release write"),
            release_create_index,
        )
        self.assertNotEqual(release.rfind("gh api", 0, release_create_index), -1)
        self.assertNotIn("--latest", release)

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
        self.assertIn("did not push, force-push, or overwrite main", script)
        for field in (
            "temporary_registry_tag_created",
            "source_image_published", "source_image_reused", "source_mode",
            "source_image_verified",
            "image_published", "image_publication_disposition",
            "known_release_tags_created", "image_verified", "manifest_digest",
            "tag_created", "tag_verified", "github_release_created",
            "github_release_verified", "github_release_url",
            "release_complete",
        ):
            self.assertIn(field, script)
        self.assertIn("Digest-addressed source image", script)
        self.assertIn("creating or correcting only the GitHub Release", script)

    def test_promotion_exposes_truthful_phase_outputs(self):
        outputs = self.promote["outputs"]
        self.assertEqual(
            set(outputs),
            {
                "version", "release_sha", "digest", "image_published",
                "image_publication_disposition",
                "image_verified", "tag_created", "tag_verified",
                "github_release_created", "github_release_verified",
                "release_complete",
            },
        )
        verify = next(
            step
            for step in self.steps
            if step.get("name")
            == "Verify digest-addressed image architectures and provenance anonymously"
        )
        self.assertEqual(verify["id"], "verify_source")
        self.assertIn("verify_publication_source_image.py", str(verify["run"]))
        verifier_source = SOURCE_VERIFIER_PATH.read_text(encoding="utf-8")
        self.assertIn("attestation-manifest", verifier_source)
        self.assertIn('"sbom_status=present"', verifier_source)
        self.assertEqual(
            outputs["image_published"],
            "${{ steps.publish_tags.outputs.publication_disposition == 'complete' }}",
        )
        self.assertEqual(
            outputs["image_publication_disposition"],
            "${{ steps.publish_tags.outputs.publication_disposition || 'none' }}",
        )

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

    def test_ready_validation_requires_the_release_to_be_materialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repo(root)
            scripts = root / "scripts"
            shutil.copy2(PROMOTION_PATH, scripts / "promote_next_release.py")
            shutil.copy2(ROOT / "scripts" / "codex-context.py", scripts / "codex-context.py")
            command = [
                sys.executable,
                str(ROOT / "scripts" / "validate_promotion_candidate.py"),
                "--repo-root",
                str(root),
                "--require-materialized",
            ]

            staged = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(staged.returncode, 0)
            self.assertIn("release declaration is not final review state", staged.stderr)

            self.module.apply_candidate(root)
            materialized = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                materialized.returncode,
                0,
                materialized.stdout + materialized.stderr,
            )
            self.assertIn("Validated exact advertised release", materialized.stdout)

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

    def test_materialized_transition_must_be_exact_next_in_same_channel(self):
        self.module.validate_sequenced_transition(
            "2.2.0-beta.50", "2.2.0-beta.51"
        )
        for candidate in (
            "2.2.0-beta.50",
            "2.2.0-beta.52",
            "2.2.0-rc2-dev51",
            "2.3.0-beta.51",
            "not-a-version",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(self.module.PromotionError):
                    self.module.validate_sequenced_transition(
                        "2.2.0-beta.50", candidate
                    )

    def test_materialized_transition_cli_rejects_skip_and_cross_channel(self):
        cases = (
            ("2.2.0-beta.50", "2.2.0-beta.51", True),
            ("2.2.0-beta.50", "2.2.0-beta.52", False),
            ("2.2.0-beta.50", "2.2.0-rc2-dev51", False),
        )
        for current, candidate, succeeds in cases:
            with self.subTest(current=current, candidate=candidate):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PROMOTION_PATH),
                        "--validate-transition",
                        current,
                        candidate,
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

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

    def test_repository_advertised_document_authority_is_exact(self):
        resolution = self.module.validate_document_authority(
            ROOT, CURRENT_REPOSITORY_VERSION
        )
        self.assertEqual(resolution["resolution_status"], "exact")
        for key in ("active_release_notes", "active_acceptance_document"):
            relative = resolution[key]
            self.assertNotEqual(relative, "unknown")
            self.assertTrue((ROOT / str(relative)).is_file())


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
