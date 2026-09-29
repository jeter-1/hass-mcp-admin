"""Offline preparation checks; these never substitute for real Core .4 execution."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]
import core_registry_contract_lane as authority
import ha_mcp_850_container_acceptance as lane

BRANCH = "refs/heads/codex/core-2026-9-4-contract-check"
WORKFLOW = "jeter-1/hass-mcp-admin/.github/workflows/core-2026-9-4-compatibility.yml@" + BRANCH


class Core4PreparationTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="jeter-1/hass-mcp-admin",
                        GITHUB_EVENT_NAME="push", GITHUB_REF=BRANCH,
                        GITHUB_WORKFLOW_REF=WORKFLOW, GITHUB_JOB="exact-addon-runtime-acceptance",
                        GITHUB_SHA="a" * 40, GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1")

    def test_exact_branch_and_workflow_admit_both_native_architectures(self):
        for arch in ("amd64", "arm64"):
            identity = lane.execution_guard(self.env, arch, "8.5.0", "2026.9.4")
            self.assertEqual(identity, f"h850c4-1234-1-{arch}")
            self.assertTrue(all(name.startswith(identity + "-") for name in lane.resource_names(identity)))

    def test_other_contexts_cannot_select_core4(self):
        changes = (("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "fork/repo"),
                   ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "workflow_run"),
                   ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_REF", "refs/heads/main"),
                   ("GITHUB_REF", "refs/pull/219/merge"), ("GITHUB_WORKFLOW_REF", WORKFLOW + "-other"),
                   ("GITHUB_JOB", "unrelated"), ("GITHUB_SHA", "bad"),
                   ("GITHUB_RUN_ID", "../other"), ("GITHUB_RUN_ATTEMPT", "1\n"))
        for field, value in changes:
            with self.subTest(field=field, value=value), self.assertRaises(lane.Refusal):
                lane.execution_guard({**self.env, field: value}, "amd64", "8.5.0", "2026.9.4")
        for field in self.env:
            env = self.env.copy()
            del env[field]
            with self.subTest(missing=field), self.assertRaises(lane.Refusal):
                lane.execution_guard(env, "amd64", "8.5.0", "2026.9.4")

    def test_pair_and_architecture_remain_closed(self):
        for arch, version, core in (("arm/v7", "8.5.0", "2026.9.4"),
                                    ("amd64", "8.4.3", "2026.9.4"),
                                    ("amd64", "8.5.0", "2026.9.5"),
                                    ("amd64", "latest", "2026.9.4")):
            with self.subTest(arch=arch, version=version, core=core), self.assertRaises(lane.Refusal):
                lane.execution_guard(self.env, arch, version, core)
        with self.assertRaises(ValueError):
            authority.lane_entry("2026.9.5")
        for identity in ("h850c5-1234-1-amd64", "../h850c4-1234-1-amd64", "h850c4-1234-1-amd64-extra"):
            with self.subTest(identity=identity), self.assertRaises(lane.Refusal):
                lane.resource_names(identity)

    def test_exact_core_identity_and_unchanged_upstream_selection(self):
        entry = authority.lane_entry("2026.9.4")
        self.assertEqual(entry["source_commit"], "9212531f40a0b7b23229a90d688dd79d9dfccff4")
        self.assertEqual(entry["source_tree"], "6c31a6d74d29920a223b779d395852f757f68853")
        self.assertEqual(entry["source_archive_sha256"], "c8d814b63c0b7043ad54980044f9824c9582450af9cac38ba376fadc37fa4056")
        self.assertEqual(entry["image_index_digest"], "sha256:3e6710a7ab2a61311d9d899b719f6c3657791c63e8f4942cec4ebc42401d6b76")
        self.assertEqual(entry["architecture_manifests"], {
            "linux/amd64": "sha256:e47c978e1b801466e7f62f612fd552bc3a228e077b31a3f1c22c05cf63d754da",
            "linux/arm64": "sha256:35e6df56a9ce632c9b15df869ac73a17af6cdd2cfb99830527ffac9cc5218ba2"})
        original = lane.PINS.read_bytes()
        previous = lane.candidate_pins("8.5.0", "2026.9.3")
        current = lane.candidate_pins("8.5.0", "2026.9.4")
        for key in set(previous) - {"core_version", "core_image"}:
            self.assertEqual(current[key], previous[key], key)
        self.assertEqual(current["core_image"], "ghcr.io/home-assistant/home-assistant:2026.9.4@" + entry["image_index_digest"])
        self.assertEqual(lane.PINS.read_bytes(), original)

    def test_old_pr_context_retains_old_core_admission_only(self):
        env = {**self.env, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/219/merge"}
        for core, suffix in (("2026.9.2", ""), ("2026.9.3", "c3")):
            self.assertEqual(lane.execution_guard(env, "amd64", "8.5.0", core), f"h850{suffix}-1234-1-amd64")
        with self.assertRaises(lane.Refusal):
            lane.execution_guard(env, "amd64", "8.5.0", "2026.9.4")

    def test_workflow_is_closed_read_permission_no_publication(self):
        text = (ROOT / ".github/workflows/core-2026-9-4-compatibility.yml").read_text()
        data = yaml.safe_load(text)
        self.assertEqual(data.get("on", data.get(True)), {"push": {"branches": [BRANCH.removeprefix("refs/heads/")]}})
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertEqual(set(data["jobs"]), {"real-ha-contract-tests", "exact-addon-runtime-acceptance"})
        for forbidden in ("secrets.", "pull_request_target", "workflow_dispatch:", "docker push", "gh release", "git push"):
            self.assertNotIn(forbidden, text)
        approved_actions = {
            "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"}
        for job in data["jobs"].values():
            self.assertEqual(job["if"], "github.repository == 'jeter-1/hass-mcp-admin'")
            self.assertLessEqual(job["timeout-minutes"], 50)
            self.assertTrue(any(step.get("if") == "always()" and "cleanup" in step.get("name", "").lower()
                                for step in job["steps"]))
            for step in job["steps"]:
                if "uses" in step:
                    self.assertIn(step["uses"], approved_actions)
                if step.get("uses", "").startswith("actions/checkout"):
                    self.assertFalse(step["with"]["persist-credentials"])
        typed = data["jobs"]["exact-addon-runtime-acceptance"]
        self.assertEqual({item["architecture"] for item in typed["strategy"]["matrix"]["include"]}, {"amd64", "arm64"})
        general = data["jobs"]["real-ha-contract-tests"]
        self.assertEqual(general["env"]["REAL_HA_EXPECTED_VERSION"], "2026.9.4")
        self.assertEqual(general["env"]["HA_CONTRACT_IMAGE"], lane.candidate_pins("8.5.0", "2026.9.4")["core_image"])
        for job in (general, typed):
            for step in job["steps"]:
                if "run" in step:
                    result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, step.get("name"))

    def test_runner_paths_are_resolved_only_after_job_dispatch(self):
        import re

        data = yaml.safe_load((ROOT / ".github/workflows/core-2026-9-4-compatibility.yml").read_text())
        # GitHub validates job env before assigning a runner. Valid YAML alone
        # does not establish that an expression's context exists at that scope.
        allowed = {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
        for name, job in data["jobs"].items():
            for key, value in job.get("env", {}).items():
                roots = re.findall(r"\$\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.", str(value))
                with self.subTest(job=name, variable=key):
                    self.assertLessEqual(set(roots), allowed)
        general = data["jobs"]["real-ha-contract-tests"]
        cleanup = next(step for step in general["steps"]
                       if step.get("name") == "Verify disposable Home Assistant cleanup")
        # always() cleanup must have its exact paths even if preparation never
        # reached the GITHUB_ENV writes (for example, an earlier pull failed).
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(cleanup["env"], {
            "REAL_HA_CONTRACT_DIR": "${{ runner.temp }}/beta25-real-ha-ha-2026-9-4",
            "REAL_HA_TOKEN_FILE": "${{ runner.temp }}/beta25-real-ha-ha-2026-9-4.token",
        })


class Core4SyntheticAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_runtime_requires_signature_then_admits_exact_nineteen(self):
        from core_registry_fixtures import ProjectedCoreSource
        from tests.test_ha_core_2026_9_integration import settings
        from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
        for version, expected in (("2026.9.2", 17), ("2026.9.3", 19), ("2026.9.4", 19)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                runtime = CoreRuntime()
                configure = runtime.configure
                def synthetic(configured, *, release_registry):
                    configure(configured, release_registry=release_registry,
                              source=ProjectedCoreSource(release_registry, version, typed_operations=expected == 19))
                image = "ghcr.io/home-assistant/home-assistant:" + version + "@" + authority.lane_entry(version)["image_index_digest"]
                with patch.object(runtime, "configure", side_effect=synthetic):
                    await authority.configure_with_test_authority(runtime, settings(), cache_path=Path(tmp) / "test.json",
                                                                 expected_image=image, core_version=version)
                health = runtime.health_snapshot()
                self.assertEqual(health["compatible_count"], expected)
                for key in ("issued_lease_count", "active_commit_count", "fallback_count"):
                    self.assertEqual(health[key], 0)

    async def test_wrong_image_fails_before_runtime_configuration(self):
        from unittest.mock import Mock
        runtime = Mock()
        with self.assertRaises(AssertionError):
            await authority.configure_with_test_authority(runtime, object(), cache_path=Path("unused"),
                                                         expected_image="unbound", core_version="2026.9.4")
        runtime.configure.assert_not_called()
