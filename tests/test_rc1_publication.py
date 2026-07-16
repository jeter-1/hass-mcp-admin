import os
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_PATH = ROOT / ".github" / "workflows" / "publish-rc-image.yml"
TAG_GUARD_PATH = ROOT / "scripts" / "assert_registry_tags_absent.sh"
IMAGE = "ghcr.io/jeter-1/hass-mcp-engineering-beta"
VERSION = "2.0.0-rc.2.rc3a.1"
TAG = f"v{VERSION}"
PLATFORMS = ("linux/amd64", "linux/arm64", "linux/arm/v7")
BUILD_ARGUMENTS = ("BUILD_VERSION", "HAMCP_BUILD_SHA", "HAMCP_BUILD_TIME")


def load_workflow(path):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def workflow_events(workflow):
    # PyYAML 1.1 treats the unquoted key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def needs(job):
    value = job.get("needs", [])
    if isinstance(value, str):
        return {value}
    return set(value)


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
        if not line:
            continue
        key, separator, item = line.partition("=")
        if not separator:
            raise AssertionError(f"expected KEY=VALUE workflow input, got {line!r}")
        result[key] = item
    return result


class RC3ADevelopmentPublicationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = load_workflow(CI_PATH)
        cls.publish_workflow = load_workflow(PUBLISH_PATH)
        cls.jobs = cls.publish_workflow["jobs"]

    def test_only_the_exact_rc_tag_can_start_publication(self):
        events = workflow_events(self.publish_workflow)
        self.assertIsInstance(events, dict)
        self.assertEqual(set(events), {"push"})
        self.assertEqual(events["push"], {"tags": [TAG]})
        self.assertEqual(self.publish_workflow.get("permissions"), {})
        self.assertEqual(self.publish_workflow["env"]["IMAGE_REPOSITORY"], IMAGE)
        self.assertEqual(self.publish_workflow["env"]["EXPECTED_VERSION"], VERSION)

    def test_rc1_and_rc2_version_tags_are_never_publication_targets(self):
        workflow_text = PUBLISH_PATH.read_text(encoding="utf-8")
        self.assertNotIn("v2.0.0-rc.1", workflow_text)
        self.assertNotIn(":2.0.0-rc.1", workflow_text)
        self.assertNotIn('tags:\n      - "v2.0.0-rc.2"', workflow_text)
        self.assertNotIn(":2.0.0-rc.2\n", workflow_text)

    def test_publication_requires_the_complete_reusable_validation_gate(self):
        self.assertEqual(
            self.jobs["validate"]["uses"],
            "./.github/workflows/ci.yml",
        )
        self.assertEqual(needs(self.jobs["release-metadata"]), {"validate"})
        self.assertEqual(
            needs(self.jobs["publish"]),
            {"validate", "release-metadata"},
        )
        self.assertEqual(
            needs(self.jobs["verify-anonymous-pull"]),
            {"release-metadata", "publish"},
        )
        self.assertIn("workflow_call", workflow_events(self.ci))

    def test_only_the_publish_job_can_write_packages(self):
        package_writers = {
            name
            for name, job in self.jobs.items()
            if (job.get("permissions") or {}).get("packages") == "write"
        }
        self.assertEqual(package_writers, {"publish"})
        self.assertEqual(
            self.jobs["publish"]["permissions"],
            {"contents": "read", "packages": "write"},
        )
        self.assertEqual(self.jobs["verify-anonymous-pull"]["permissions"], {})

    def test_pull_request_ci_builds_every_architecture_without_login_or_push(self):
        events = workflow_events(self.ci)
        self.assertIn("pull_request", events)
        self.assertNotIn("packages", self.ci.get("permissions", {}))

        ci_jobs = self.ci["jobs"].values()
        ci_actions = [
            str(step.get("uses", ""))
            for job in ci_jobs
            for step in job.get("steps", [])
        ]
        self.assertFalse(
            any(action.startswith("docker/login-action") for action in ci_actions)
        )
        ci_scripts = "\n".join(
            script for job in self.ci["jobs"].values() for script in run_steps(job)
        )
        self.assertNotRegex(ci_scripts, r"(?m)^\s*docker\s+(?:buildx\s+)?push\b")
        self.assertNotRegex(ci_scripts, r"(?m)^\s*docker\s+buildx\s+build\b[^\n]*--push\b")
        self.assertNotIn("--push", ci_scripts)
        for job in self.ci["jobs"].values():
            for step in action_steps(job, "docker/build-push-action"):
                self.assertIs(step.get("with", {}).get("push"), False)

        validation_builds = [
            script
            for job in self.ci["jobs"].values()
            for script in run_steps(job)
            if "docker buildx build" in script
        ]
        self.assertEqual(len(validation_builds), 1)
        build = validation_builds[0]
        self.assertIn(
            '--platform "linux/amd64,linux/arm64,linux/arm/v7"',
            build,
        )
        self.assertIn("--output=type=cacheonly", build)
        self.assertNotIn("--push", build)
        for argument in BUILD_ARGUMENTS:
            self.assertRegex(
                build,
                rf'--build-arg\s+["\']{re.escape(argument)}=',
            )

    def test_versions_are_checked_and_provenance_is_resolved_before_login(self):
        metadata_job = self.jobs["release-metadata"]
        self.assertFalse(action_steps(metadata_job, "docker/login-action"))
        release_steps = [
            step for step in metadata_job["steps"] if step.get("id") == "release"
        ]
        self.assertEqual(len(release_steps), 1)
        script = str(release_steps[0]["run"])

        for value in (
            "GITHUB_REF_TYPE",
            "GITHUB_REF_NAME",
            "hass_mcp_engineering_beta/config.yaml",
            "hass_mcp_engineering_beta/ha_mcp_engineering/version.py",
            'config_version" != "$EXPECTED_VERSION',
            'server_version" != "$EXPECTED_VERSION',
            'git merge-base --is-ancestor "$build_sha"',
        ):
            self.assertIn(value, script)
        self.assertEqual(script.count("git rev-parse HEAD"), 1)
        self.assertEqual(script.count("date -u"), 1)
        self.assertIn("date -u +'%Y-%m-%dT%H:%M:%SZ'", script)
        self.assertIn("^[0-9a-f]{40}$", script)
        self.assertIn("T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", script)
        self.assertNotIn("github.sha", script.lower())
        for output in ("build_sha", "build_time", "version"):
            self.assertIn(f'echo "{output}=', script)
            self.assertIn(
                f"steps.release.outputs.{output}",
                str(metadata_job["outputs"][output]),
            )

        publish_job = self.jobs["publish"]
        login_steps = action_steps(publish_job, "docker/login-action")
        self.assertEqual(len(login_steps), 1)
        self.assertIn("release-metadata", needs(publish_job))
        checkout_checks = [
            script for script in run_steps(publish_job) if "actual_sha=" in script
        ]
        self.assertEqual(len(checkout_checks), 1)
        self.assertIn("git rev-parse HEAD", checkout_checks[0])
        self.assertIn('"$actual_sha" != "$EXPECTED_SHA"', checkout_checks[0])

    def test_existing_immutable_tags_are_refused_before_the_build(self):
        steps = self.jobs["publish"]["steps"]
        build_index = next(
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("docker/build-push-action")
        )
        refusal_steps = [
            (index, step)
            for index, step in enumerate(steps)
            if "Refuse to overwrite immutable image tags" in step.get("name", "")
        ]
        self.assertEqual(len(refusal_steps), 1)
        refusal_index, refusal = refusal_steps[0]
        self.assertLess(refusal_index, build_index)
        script = str(refusal["run"])
        self.assertIn('${IMAGE_REPOSITORY}:${VERSION}', script)
        self.assertIn('${IMAGE_REPOSITORY}:sha-${BUILD_SHA}', script)
        self.assertIn(
            'bash scripts/assert_registry_tags_absent.sh "$version_image" "$sha_image"',
            script,
        )

    def test_privileged_workflow_checkout_is_pinned_without_persisted_credentials(self):
        checkout_sha = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
        for job_name in ("release-metadata", "publish"):
            checkouts = action_steps(self.jobs[job_name], "actions/checkout@")
            self.assertEqual(len(checkouts), 1)
            self.assertEqual(checkouts[0]["uses"].split()[0], checkout_sha)
            self.assertIs(checkouts[0]["with"]["persist-credentials"], False)

    def test_one_build_publishes_all_platforms_with_exact_provenance_inputs(self):
        publish_job = self.jobs["publish"]
        build_steps = action_steps(publish_job, "docker/build-push-action")
        self.assertEqual(len(build_steps), 1)
        build_inputs = build_steps[0]["with"]
        self.assertIs(build_inputs["push"], True)
        self.assertEqual(
            tuple(item.strip() for item in build_inputs["platforms"].split(",")),
            PLATFORMS,
        )

        arguments = assignment_lines(build_inputs["build-args"])
        self.assertEqual(set(arguments), set(BUILD_ARGUMENTS))
        self.assertEqual(
            arguments,
            {
                "BUILD_VERSION": "${{ needs.release-metadata.outputs.version }}",
                "HAMCP_BUILD_SHA": "${{ needs.release-metadata.outputs.build_sha }}",
                "HAMCP_BUILD_TIME": "${{ needs.release-metadata.outputs.build_time }}",
            },
        )

        tags = tuple(
            line.strip() for line in str(build_inputs["tags"]).splitlines() if line.strip()
        )
        self.assertEqual(
            tags,
            (
                f"{IMAGE}:${{{{ needs.release-metadata.outputs.version }}}}",
                f"{IMAGE}:sha-${{{{ needs.release-metadata.outputs.build_sha }}}}",
            ),
        )
        labels = assignment_lines(build_inputs["labels"])
        self.assertEqual(
            labels["org.opencontainers.image.revision"],
            "${{ needs.release-metadata.outputs.build_sha }}",
        )
        self.assertEqual(
            labels["org.opencontainers.image.created"],
            "${{ needs.release-metadata.outputs.build_time }}",
        )
        self.assertEqual(
            labels["org.opencontainers.image.version"],
            "${{ needs.release-metadata.outputs.version }}",
        )

    def test_declared_addon_architectures_match_ci_and_publish_platforms(self):
        config = yaml.safe_load(
            (ROOT / "hass_mcp_engineering_beta" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )
        platform_for_arch = {
            "amd64": "linux/amd64",
            "aarch64": "linux/arm64",
            "armv7": "linux/arm/v7",
        }
        self.assertEqual(tuple(config["arch"]), tuple(platform_for_arch))
        self.assertEqual(
            tuple(platform_for_arch[arch] for arch in config["arch"]),
            PLATFORMS,
        )

    def test_dockerfile_consumes_every_published_build_argument(self):
        dockerfile = (
            ROOT / "hass_mcp_engineering_beta" / "Dockerfile"
        ).read_text(encoding="utf-8")
        args = dict(re.findall(r"(?m)^ARG\s+([A-Z0-9_]+)=([^\s]+)$", dockerfile))
        self.assertEqual({name: args.get(name) for name in BUILD_ARGUMENTS}, {
            name: "unknown" for name in BUILD_ARGUMENTS
        })
        for name in BUILD_ARGUMENTS:
            self.assertRegex(
                dockerfile,
                rf"(?m)^(?:ENV\s+|\s+){name}=\$\{{{name}\}}(?:\s*\\)?$",
            )
        self.assertIn('org.opencontainers.image.version="${BUILD_VERSION}"', dockerfile)
        self.assertIn('org.opencontainers.image.revision="${HAMCP_BUILD_SHA}"', dockerfile)
        self.assertIn('org.opencontainers.image.created="${HAMCP_BUILD_TIME}"', dockerfile)

    def test_armv7_source_dependencies_build_outside_the_runtime_image(self):
        dockerfile = (
            ROOT / "hass_mcp_engineering_beta" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim AS dependency-builder", dockerfile)
        self.assertRegex(
            dockerfile,
            r"apt-get install --no-install-recommends -y build-essential",
        )
        self.assertIn(
            "pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt",
            dockerfile,
        )
        self.assertIn("COPY --from=dependency-builder /wheels /wheels", dockerfile)
        runtime_stage = dockerfile.rsplit("FROM python:3.12-slim", 1)[1]
        self.assertNotIn("apt-get", runtime_stage)
        self.assertNotIn("build-essential", runtime_stage)
        self.assertIn(
            "pip install --no-cache-dir --no-index --find-links=/wheels",
            runtime_stage,
        )

    def test_anonymous_verification_is_a_separate_credential_free_job(self):
        job = self.jobs["verify-anonymous-pull"]
        self.assertFalse(action_steps(job, "docker/login-action"))
        script = "\n".join(run_steps(job))
        for value in (
            'docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"',
            "grep -q 'ghcr\\.io'",
            'imagetools inspect --raw "$version_image"',
            'imagetools inspect "$sha_image"',
            'docker pull --platform linux/amd64 "$version_image"',
            "GHCR package is not public",
            '("linux", "amd64", None)',
            '("linux", "arm64", None)',
            '("linux", "arm", "v7")',
        ):
            self.assertIn(value, script)
        self.assertIn("EXPECTED_DIGEST", script)
        self.assertIn("version_digest", script)
        self.assertIn("sha_digest", script)


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
                [
                    self.bash,
                    str(TAG_GUARD_PATH),
                    f"{IMAGE}:test",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

    def test_explicit_manifest_absence_allows_publication_to_continue(self):
        for mode in ("absent_manifest", "absent_not_found"):
            with self.subTest(mode=mode):
                result = self.run_guard(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Confirmed registry tag is absent", result.stdout)

    def test_existing_tag_fails_closed(self):
        result = self.run_guard("exists")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing to overwrite immutable tag", result.stdout)

    def test_network_auth_and_unknown_failures_cannot_be_treated_as_absence(self):
        for mode in ("network", "auth", "unknown"):
            with self.subTest(mode=mode):
                result = self.run_guard(mode)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Unable to prove registry tag is absent", result.stdout)


if __name__ == "__main__":
    unittest.main()
