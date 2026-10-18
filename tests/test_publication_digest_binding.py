"""Promote publishes only the digest built and verified in this exact attempt (HAMCP-204).

The token-holding promote job installs nothing and trusts no unbound job
output: ``scripts/publication_digest_binding.py`` binds the build job's digest
to the digest the read-only verify-image job resolved and verified, and every
contributing job to the current run attempt.  These tests cover acceptance in
both source modes, every refusal category, the CLI boundary, a killed mutant
without the digest comparison, and the workflow wiring that makes the bound
digest the only one promote can publish.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publication_digest_binding.py"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-rc-image.yml"
RELEASE = "a" * 40
BUILT = "sha256:" + "1" * 64
RECOVERED = "sha256:" + "2" * 64
OTHER = "sha256:" + "3" * 64
NOTES = "docs/ENGINEERING_SYNTHETIC_RELEASE_NOTES.md"
BUILD_TIME = "2026-10-18T00:00:00Z"
BOUND = "${{ steps.bind.outputs.digest }}"


def _load_module(source: str | None = None):
    spec = importlib.util.spec_from_file_location("publication_digest_binding_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if source is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(source, str(SCRIPT), "exec"), module.__dict__)
    return module


binding = _load_module()


def build_env(**overrides) -> dict[str, str]:
    env = {
        "SOURCE_MODE": "build",
        "CURRENT_RUN_ATTEMPT": "1",
        "CLAIM_RUN_ATTEMPT": "1",
        "APPROVED_RUN_ATTEMPT": "1",
        "BUILD_RUN_ATTEMPT": "1",
        "DETECTED_RELEASE_SHA": RELEASE,
        "PREPARED_RELEASE_SHA": RELEASE,
        "SOURCE_MAIN_SHA": RELEASE,
        "PREPARED_VERSION": "2.0.0-beta.99",
        "DETECTED_EXPECTED_VERSION": "2.0.0-beta.99",
        "PREPARED_RELEASE_TAG": "v2.0.0-beta.99",
        "BUILD_TIME": BUILD_TIME,
        "BUILD_PLATFORMS": "linux/amd64,linux/arm64,linux/arm/v7",
        "RELEASE_NOTES_PATH": NOTES,
        "BUILT_DIGEST": BUILT,
        "RECOVERY_DIGEST": "",
        "DETECTED_RECOVERY_BUILD_TIME": "",
        "APPROVED_DIGEST": BUILT,
        "VERIFIED_MANIFEST_DIGEST": BUILT,
        "SOURCE_IMAGE_VERIFIED": "true",
    }
    env.update(overrides)
    return env


def resume_env(**overrides) -> dict[str, str]:
    env = build_env(
        SOURCE_MODE="resume_digest",
        BUILD_RUN_ATTEMPT="",
        BUILT_DIGEST="",
        RECOVERY_DIGEST=RECOVERED,
        DETECTED_RECOVERY_BUILD_TIME=BUILD_TIME,
        APPROVED_DIGEST=RECOVERED,
        VERIFIED_MANIFEST_DIGEST=RECOVERED,
    )
    env.update(overrides)
    return env


class BindingFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        notes = self.root / NOTES
        notes.parent.mkdir(parents=True)
        notes.write_text("synthetic release notes\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def bind(self, env, module=binding, head=RELEASE):
        return module.bind(env, self.root, head=lambda root: head)

    def assertRefused(self, env, category, module=binding, head=RELEASE) -> None:
        with self.assertRaises(module.Refusal) as caught:
            self.bind(env, module=module, head=head)
        self.assertEqual(str(caught.exception), category)


class DigestBindingTests(BindingFixture):
    def test_fresh_build_binds_the_built_and_verified_digest(self) -> None:
        self.assertEqual(
            self.bind(build_env()),
            {"digest": BUILT, "source_image_published": "true", "source_image_reused": "false"},
        )
        two = build_env(BUILD_PLATFORMS="linux/amd64,linux/arm64")
        self.assertEqual(self.bind(two)["digest"], BUILT)

    def test_digest_recovery_binds_the_owner_supplied_digest(self) -> None:
        self.assertEqual(
            self.bind(resume_env()),
            {"digest": RECOVERED, "source_image_published": "false", "source_image_reused": "true"},
        )

    def test_mismatched_digest_is_refused(self) -> None:
        cases = {
            # A different artifact than the one built cannot be promoted.
            "approved_digest_mismatch": (
                build_env(APPROVED_DIGEST=OTHER),
                build_env(BUILT_DIGEST=OTHER, VERIFIED_MANIFEST_DIGEST=OTHER),
                resume_env(APPROVED_DIGEST=BUILT),
                build_env(APPROVED_DIGEST=""),
            ),
            "verified_manifest_digest_mismatch": (
                build_env(VERIFIED_MANIFEST_DIGEST=OTHER),
                resume_env(VERIFIED_MANIFEST_DIGEST=OTHER),
                build_env(VERIFIED_MANIFEST_DIGEST=""),
            ),
            "source_image_unverified": (
                build_env(SOURCE_IMAGE_VERIFIED="false"),
                build_env(SOURCE_IMAGE_VERIFIED=""),
                resume_env(SOURCE_IMAGE_VERIFIED="True"),
            ),
        }
        for category, envs in cases.items():
            for env in envs:
                with self.subTest(category=category, env=env):
                    self.assertRefused(env, category)

    def test_jobs_outside_this_attempt_are_refused(self) -> None:
        cases = (
            (build_env(CURRENT_RUN_ATTEMPT="2"), "claim_attempt_mismatch"),
            (build_env(CURRENT_RUN_ATTEMPT="0"), "run_attempt_invalid"),
            (build_env(CURRENT_RUN_ATTEMPT=""), "run_attempt_invalid"),
            (build_env(CLAIM_RUN_ATTEMPT=""), "claim_attempt_mismatch"),
            (build_env(APPROVED_RUN_ATTEMPT="2"), "verification_attempt_mismatch"),
            (build_env(BUILD_RUN_ATTEMPT="2"), "build_attempt_mismatch"),
            (build_env(BUILD_RUN_ATTEMPT=""), "build_attempt_mismatch"),
            (resume_env(CLAIM_RUN_ATTEMPT="2"), "claim_attempt_mismatch"),
        )
        for env, category in cases:
            with self.subTest(category=category):
                self.assertRefused(env, category)

    def test_release_identity_mismatch_is_refused(self) -> None:
        cases = (
            (build_env(DETECTED_RELEASE_SHA="A" * 40), "release_sha_invalid"),
            (build_env(PREPARED_RELEASE_SHA="b" * 40), "prepared_release_mismatch"),
            (build_env(SOURCE_MAIN_SHA="b" * 40), "source_main_mismatch"),
            (build_env(PREPARED_VERSION="2.0.0 beta"), "version_invalid"),
            (build_env(DETECTED_EXPECTED_VERSION="2.0.0-beta.98"), "version_mismatch"),
            (build_env(PREPARED_RELEASE_TAG="2.0.0-beta.99"), "release_tag_mismatch"),
            (build_env(BUILD_TIME="2026-10-18 00:00:00"), "build_time_invalid"),
            (build_env(SOURCE_MODE="tag"), "source_mode_unsupported"),
            (build_env(SOURCE_MODE=""), "source_mode_unsupported"),
        )
        for env, category in cases:
            with self.subTest(category=category):
                self.assertRefused(env, category)
        self.assertRefused(build_env(), "checkout_release_mismatch", head="b" * 40)

    def test_platforms_must_be_a_unique_allowlisted_set_with_both_required(self) -> None:
        for platforms in (
            "linux/amd64",
            "linux/amd64,linux/amd64,linux/arm64",
            "linux/amd64,linux/arm64,linux/386",
            "",
            "linux/amd64, linux/arm64",
        ):
            with self.subTest(platforms=platforms):
                self.assertRefused(build_env(BUILD_PLATFORMS=platforms), "platforms_invalid")

    def test_release_notes_must_be_an_existing_regular_repository_file(self) -> None:
        self.assertRefused(build_env(RELEASE_NOTES_PATH="README.md"), "release_notes_invalid")
        self.assertRefused(
            build_env(RELEASE_NOTES_PATH="docs/../docs/ENGINEERING_SYNTHETIC_RELEASE_NOTES.md"),
            "release_notes_unavailable",
        )
        self.assertRefused(
            build_env(RELEASE_NOTES_PATH="docs/MISSING_RELEASE_NOTES.md"),
            "release_notes_unavailable",
        )
        link = self.root / "docs" / "LINKED_RELEASE_NOTES.md"
        link.symlink_to(self.root / NOTES)
        self.assertRefused(
            build_env(RELEASE_NOTES_PATH="docs/LINKED_RELEASE_NOTES.md"),
            "release_notes_unavailable",
        )

    def test_source_modes_cannot_be_mixed(self) -> None:
        cases = (
            (build_env(BUILT_DIGEST=""), "built_digest_invalid"),
            (build_env(BUILT_DIGEST="sha256:" + "1" * 63), "built_digest_invalid"),
            (build_env(RECOVERY_DIGEST=RECOVERED), "built_digest_invalid"),
            (build_env(DETECTED_RECOVERY_BUILD_TIME=BUILD_TIME), "fresh_build_carries_recovery"),
            (resume_env(BUILT_DIGEST=BUILT), "recovery_attempted_build"),
            (resume_env(BUILD_RUN_ATTEMPT="1"), "recovery_attempted_build"),
            (resume_env(RECOVERY_DIGEST="sha256:" + "G" * 64), "recovery_digest_invalid"),
            (resume_env(RECOVERY_DIGEST=""), "recovery_digest_invalid"),
            (
                resume_env(DETECTED_RECOVERY_BUILD_TIME="2026-10-17T00:00:00Z"),
                "recovery_build_time_mismatch",
            ),
        )
        for env, category in cases:
            with self.subTest(category=category):
                self.assertRefused(env, category)

    def test_mutant_without_the_digest_comparison_is_killed(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        comparison = (
            '    require(env.get("APPROVED_DIGEST") == digest, "approved_digest_mismatch")\n'
            '    require(env.get("VERIFIED_MANIFEST_DIGEST") == digest, '
            '"verified_manifest_digest_mismatch")\n'
        )
        self.assertEqual(source.count(comparison), 1)
        mutant = _load_module(source.replace(comparison, ""))
        tampered = build_env(APPROVED_DIGEST=OTHER, VERIFIED_MANIFEST_DIGEST=OTHER)
        # The mutant promotes the build digest although verification approved
        # another artifact; the real script refuses the same inputs.
        self.assertEqual(self.bind(tampered, module=mutant)["digest"], BUILT)
        self.assertRefused(tampered, "approved_digest_mismatch")


class DigestBindingCliTests(BindingFixture):
    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()

    def _repository(self) -> str:
        self._git("init", "-q")
        self._git("add", NOTES)
        self._git(
            "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "synthetic",
        )
        return self._git("rev-parse", "HEAD")

    def _run(self, env: dict[str, str]) -> tuple[subprocess.CompletedProcess, str]:
        output = self.root.parent / (self.root.name + "-github-output")
        output.write_text("", encoding="utf-8")
        self.addCleanup(output.unlink, missing_ok=True)
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--repo-root", str(self.root),
             "--github-output", str(output)],
            env={"PATH": "/usr/bin:/bin", **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        return result, output.read_text(encoding="utf-8")

    def test_cli_writes_only_the_bound_outputs_against_the_real_checkout(self) -> None:
        head = self._repository()
        identity = {"DETECTED_RELEASE_SHA": head, "PREPARED_RELEASE_SHA": head, "SOURCE_MAIN_SHA": head}
        result, written = self._run(build_env(**identity))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            written,
            f"digest={BUILT}\nsource_image_published=true\nsource_image_reused=false\n",
        )

    def test_cli_refusal_writes_nothing_and_echoes_only_the_category(self) -> None:
        head = self._repository()
        identity = {"DETECTED_RELEASE_SHA": head, "PREPARED_RELEASE_SHA": head, "SOURCE_MAIN_SHA": head}
        result, written = self._run(build_env(APPROVED_DIGEST=OTHER, **identity))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(written, "")
        self.assertEqual(
            result.stderr, "Publication digest binding refused: approved_digest_mismatch\n"
        )
        self.assertNotIn(OTHER, result.stdout + result.stderr)
        # Outside a git checkout the identity is unavailable and binding refuses.
        result, written = self._run(build_env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(written, "")


def _step(job: dict, step_id: str) -> dict:
    matches = [step for step in job["steps"] if step.get("id") == step_id]
    if len(matches) != 1:
        raise AssertionError(f"expected one step {step_id!r}")
    return matches[0]


def digest_binding_violations(workflow: dict) -> set[str]:
    """Return how the workflow lets promote publish an unbound digest."""
    violations: set[str] = set()
    jobs = workflow["jobs"]
    promote = jobs["promote"]
    if not {"claim", "build", "verify-image"} <= set(promote.get("needs", [])):
        violations.add("PROMOTE_NOT_AFTER_BUILD_AND_VERIFICATION")
    ids = [step.get("id") for step in promote["steps"]]
    if ids.count("bind") != 1:
        return violations | {"BIND_STEP_MISSING"}
    bind = _step(promote, "bind")
    if "publication_digest_binding.py" not in bind.get("run", ""):
        violations.add("BIND_STEP_DOES_NOT_RUN_THE_BINDING_CHECK")
    if "if" in bind or bind.get("continue-on-error"):
        violations.add("BIND_STEP_IS_CONDITIONAL")
    expected = {
        "BUILT_DIGEST": "${{ needs.build.outputs.digest }}",
        "BUILD_RUN_ATTEMPT": "${{ needs.build.outputs.run_attempt }}",
        "APPROVED_DIGEST": "${{ needs.verify-image.outputs.digest }}",
        "VERIFIED_MANIFEST_DIGEST": "${{ needs.verify-image.outputs.manifest_digest }}",
        "APPROVED_RUN_ATTEMPT": "${{ needs.verify-image.outputs.run_attempt }}",
        "SOURCE_IMAGE_VERIFIED": "${{ needs.verify-image.outputs.source_image_verified }}",
        "CLAIM_RUN_ATTEMPT": "${{ needs.claim.outputs.run_attempt }}",
        "CURRENT_RUN_ATTEMPT": "${{ github.run_attempt }}",
        "RECOVERY_DIGEST": "${{ needs.detect-release.outputs.recovery_source_digest }}",
        "SOURCE_MODE": "${{ needs.detect-release.outputs.source_mode }}",
    }
    env = bind.get("env", {})
    if any(env.get(name) != value for name, value in expected.items()):
        violations.add("BIND_INPUTS_NOT_FROM_THEIR_JOBS")
    publishing = [i for i, step in enumerate(promote["steps"]) if step.get("id") in {
        "publish_tags", "verify", "finalize", "release"}]
    if not publishing or min(publishing) < ids.index("bind"):
        violations.add("PUBLICATION_BEFORE_BINDING")
    # Every digest promote uses after binding is the bound digest.
    for step in promote["steps"]:
        if step is bind:
            continue
        text = json.dumps(step)
        for reference in re.findall(r"\$\{\{[^}]*digest[^}]*\}\}", text):
            if reference != BOUND:
                violations.add("PROMOTE_USES_UNBOUND_DIGEST")
    if promote.get("outputs", {}).get("digest") != BOUND:
        violations.add("PROMOTE_USES_UNBOUND_DIGEST")
    for name in ("publish_tags", "verify", "finalize", "release"):
        if name in ids and BOUND not in json.dumps(_step(promote, name)):
            violations.add("PUBLICATION_STEP_NOT_BOUND")
    # verify-image verifies exactly what build produced (or the recovery digest).
    verify_image = jobs["verify-image"]
    source = json.dumps(_step(verify_image, "source"))
    if "needs.build.outputs.digest" not in source:
        violations.add("VERIFICATION_NOT_OF_BUILT_DIGEST")
    if verify_image.get("outputs", {}).get("digest") != "${{ steps.source.outputs.digest }}":
        violations.add("VERIFICATION_NOT_OF_BUILT_DIGEST")
    if "build" not in verify_image.get("needs", []):
        violations.add("VERIFICATION_NOT_OF_BUILT_DIGEST")
    return violations


class DigestHandoffWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def _mutated(self, mutate) -> set[str]:
        workflow = copy.deepcopy(self.workflow)
        mutate(workflow["jobs"])
        return digest_binding_violations(workflow)

    def test_promote_publishes_only_the_bound_digest(self) -> None:
        self.assertEqual(digest_binding_violations(self.workflow), set())
        promote = self.workflow["jobs"]["promote"]
        ids = [step.get("id") for step in promote["steps"]]
        # The binding runs before the authority recheck and every publication.
        self.assertEqual(ids.index("bind"), 3)
        self.assertEqual(
            [step_id for step_id in ids if step_id],
            ["bind", "publish_tags", "verify", "finalize", "release"],
        )
        self.assertIn(
            'python3 -I -S "$RUNNER_TEMP/publication-policy/publication_digest_binding.py"',
            _step(promote, "bind")["run"],
        )
        materialize = promote["steps"][2]["run"]
        self.assertIn("publication_digest_binding.py", materialize)
        # The raw build digest reaches promote only through the binding inputs.
        outside = [
            step for step in promote["steps"]
            if "needs.build.outputs.digest" in json.dumps(step)
        ]
        self.assertEqual(outside, [_step(promote, "bind")])

    def test_promote_without_the_digest_check_fails(self) -> None:
        def drop_bind(jobs):
            jobs["promote"]["steps"] = [
                step for step in jobs["promote"]["steps"] if step.get("id") != "bind"
            ]

        def unbound_publication(jobs):
            step = _step(jobs["promote"], "publish_tags")
            step["env"]["SOURCE_DIGEST"] = "${{ needs.build.outputs.digest }}"

        def unbound_release(jobs):
            step = _step(jobs["promote"], "release")
            step["env"]["IMAGE_DIGEST"] = "${{ needs.verify-image.outputs.digest }}"

        def self_approval(jobs):
            _step(jobs["promote"], "bind")["env"]["APPROVED_DIGEST"] = (
                "${{ needs.build.outputs.digest }}"
            )

        def conditional_bind(jobs):
            _step(jobs["promote"], "bind")["continue-on-error"] = True

        def bind_after_publication(jobs):
            steps = jobs["promote"]["steps"]
            bind = _step(jobs["promote"], "bind")
            steps.remove(bind)
            steps.append(bind)

        def verify_other_digest(jobs):
            step = _step(jobs["verify-image"], "source")
            step["env"]["BUILT_DIGEST"] = "${{ needs.detect-release.outputs.recovery_source_digest }}"

        def promote_before_verification(jobs):
            jobs["promote"]["needs"].remove("verify-image")

        cases = {
            "BIND_STEP_MISSING": drop_bind,
            "PROMOTE_USES_UNBOUND_DIGEST": unbound_publication,
            "PUBLICATION_STEP_NOT_BOUND": unbound_release,
            "BIND_INPUTS_NOT_FROM_THEIR_JOBS": self_approval,
            "BIND_STEP_IS_CONDITIONAL": conditional_bind,
            "PUBLICATION_BEFORE_BINDING": bind_after_publication,
            "VERIFICATION_NOT_OF_BUILT_DIGEST": verify_other_digest,
            "PROMOTE_NOT_AFTER_BUILD_AND_VERIFICATION": promote_before_verification,
        }
        for expected, mutate in cases.items():
            with self.subTest(mutation=mutate.__name__):
                self.assertIn(expected, self._mutated(mutate))

    def test_build_is_bound_to_this_attempts_claim_before_registry_login(self) -> None:
        build = self.workflow["jobs"]["build"]
        self.assertIn("claim", build["needs"])
        ids = [step.get("id") for step in build["steps"]]
        self.assertLess(ids.index("claim_binding"), ids.index("build"))
        names = [step.get("name", "") for step in build["steps"]]
        self.assertLess(ids.index("claim_binding"), names.index("Log in to GHCR"))
        claim_binding = _step(build, "claim_binding")
        self.assertEqual(
            claim_binding["env"]["CLAIM_RUN_ATTEMPT"], "${{ needs.claim.outputs.run_attempt }}"
        )
        self._assert_claim_binding_executes(claim_binding)
        self.assertEqual(
            build["outputs"],
            {
                "digest": "${{ steps.build.outputs.digest }}",
                "push_outcome": "${{ steps.build.outcome }}",
                "run_attempt": "${{ steps.claim_binding.outputs.run_attempt }}",
            },
        )
        # Untagged push by digest only: final tags are promote's job.
        options = _step(build, "build")["with"]
        self.assertIn("push-by-digest=true", options["outputs"])
        self.assertNotIn("tags", options)
        self.assertNotIn("push", options)

    def _assert_claim_binding_executes(self, step: dict) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.run(
                    ["git", "-C", directory, *args], check=True, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                ).stdout.strip()

            git("init", "-q")
            git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "synthetic")
            head = git("rev-parse", "HEAD")
            output = Path(directory, "github-output")

            def run(**overrides):
                output.write_text("", encoding="utf-8")
                env = {
                    "PATH": "/usr/bin:/bin",
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_RUN_ATTEMPT": "1",
                    "CLAIM_RUN_ATTEMPT": "1",
                    "RELEASE_SHA": head,
                    "VERSION": "2.0.0-beta.99",
                    "BUILD_TIME": BUILD_TIME,
                    "BUILD_PLATFORMS": "linux/amd64,linux/arm64,linux/arm/v7",
                    **overrides,
                }
                result = subprocess.run(
                    ["bash", "-c", step["run"]], cwd=directory, env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                )
                return result.returncode, output.read_text(encoding="utf-8")

            self.assertEqual(run(), (0, "run_attempt=1\n"))
            for overrides in (
                {"CLAIM_RUN_ATTEMPT": ""},
                {"CLAIM_RUN_ATTEMPT": "1", "GITHUB_RUN_ATTEMPT": "2"},
                {"GITHUB_RUN_ATTEMPT": "0", "CLAIM_RUN_ATTEMPT": "0"},
                {"RELEASE_SHA": "b" * 40},
                {"VERSION": "2.0.0 beta"},
                {"BUILD_TIME": "yesterday"},
                {"BUILD_PLATFORMS": "linux/arm64,linux/amd64"},
                {"BUILD_PLATFORMS": "linux/amd64"},
            ):
                with self.subTest(overrides=overrides):
                    self.assertEqual(run(**overrides), (1, ""))


if __name__ == "__main__":
    unittest.main()
