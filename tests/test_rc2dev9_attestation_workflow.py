import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "prepare-upstream-compatibility-attestation.yml"
)
SCRIPT_PATH = ROOT / "scripts" / "manage_upstream_trust_registry.py"
DEFERRED_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "RC2DEV9_DEFERRED_REGISTRY_WRITE_CONTRACTS.json"
)


class AttestationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_workflow_is_manual_main_only_and_protected(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"inspection", "signing", "publication"})
        self.assertEqual(self.workflow["permissions"], {})
        for job in jobs.values():
            self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(jobs["signing"]["environment"], "upstream-attestation-signing")
        self.assertEqual(
            jobs["publication"]["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        self.assertEqual(jobs["inspection"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["signing"]["permissions"], {"contents": "read"})
        self.assertNotIn("packages", json.dumps(self.workflow))
        self.assertEqual(
            self.workflow["concurrency"],
            {"group": "upstream-compatibility-attestation", "cancel-in-progress": False},
        )

    def test_fixed_upstream_locations_and_stable_version_only(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(
            set(triggers["workflow_dispatch"]["inputs"]),
            {
                "operation",
                "upstream_version",
                "expected_current_sequence",
                "expiry_days",
                "operator_reason",
            },
        )
        operation = triggers["workflow_dispatch"]["inputs"]["operation"]
        self.assertEqual(
            operation["options"],
            ["bootstrap", "add", "revoke", "restore", "renew", "verify"],
        )
        self.assertIn("https://github.com/homeassistant-ai/ha-mcp.git", self.source)
        self.assertIn("ghcr.io/homeassistant-ai/ha-mcp", self.source)
        self.assertIn(
            '[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]',
            self.source,
        )
        self.assertIn("refs/tags/v${VERSION}", self.source)

    def test_private_key_is_scoped_only_to_signing_step(self):
        jobs = self.workflow["jobs"]
        steps = jobs["signing"]["steps"]
        signing = next(
            step
            for step in steps
            if step.get("name")
            == "Sign only prevalidated canonical bytes with minimum secret scope"
        )
        self.assertEqual(
            set(signing["env"]),
            {
                "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
                "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY",
                "UPSTREAM_TRUST_REGISTRY_KEY_ID",
            },
        )
        public_verify = next(
            step
            for step in steps
            if step.get("name") == "Reverify signed set with the public key only"
        )
        self.assertEqual(
            set(public_verify["env"]),
            {"UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY", "UPSTREAM_TRUST_REGISTRY_KEY_ID"},
        )
        for step in steps:
            if step is signing:
                continue
            self.assertNotIn(
                "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
                step.get("env", {}),
            )
        self.assertNotIn(
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
            json.dumps(jobs["inspection"]),
        )
        self.assertNotIn(
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
            json.dumps(jobs["publication"]),
        )

    def test_workflow_creates_only_a_data_pr_and_never_publishes(self):
        self.assertIn("gh pr create --draft", self.source)
        self.assertNotIn("docker push", self.source)
        self.assertNotIn("build-push-action", self.source)
        self.assertNotIn("gh release create", self.source)
        self.assertNotIn("git tag", self.source)
        self.assertIn("Tag, release, image, package, or deployment command: false", self.source)
        self.assertIn("if: inputs.operation != 'verify'", self.source)
        self.assertIn("prepare_upstream_registry_publication.py", self.source)
        allowed = {
            "upstream-trust/upstream-dashboard-registry.json",
            "upstream-trust/upstream-dashboard-registry.sig.json",
        }
        implementation = (
            self.script
            + (ROOT / "scripts" / "prepare_upstream_registry_publication.py").read_text(
                encoding="utf-8"
            )
        )
        for path in allowed:
            self.assertIn(path, implementation)
        self.assertIn('ROOT / "docs" / "generated"', implementation)
        self.assertIn('"UPSTREAM_TRUST_REGISTRY_INDEX.md"', implementation)

    def test_lifecycle_script_has_fixed_runtime_locations_and_no_registry_url(self):
        arguments = set(re.findall(r'add_argument\("--([a-z0-9-]+)"', self.script))
        self.assertTrue(
            {
                "operation",
                "upstream-version",
                "expected-current-sequence",
                "expiry-days",
                "operator-reason",
                "workflow-base-sha",
                "dispatch-sha",
                "artifact-directory",
                "dry-run",
            }.issubset(arguments)
        )
        self.assertIn('"UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"', self.script)
        self.assertNotIn("--registry-url", self.script)
        self.assertNotIn("--registry-output", self.script)
        self.assertIn("replace_verified_output_set", self.script)
        self.assertIn("stale expected_current_sequence", self.script)

    def test_signing_is_offline_hash_locked_and_publication_only_job_writes(self):
        jobs = self.workflow["jobs"]
        signing_source = json.dumps(jobs["signing"])
        self.assertIn("--require-hashes", signing_source)
        self.assertIn("--no-index", signing_source)
        self.assertNotIn("pip install -r hass_mcp_engineering_beta", signing_source)
        self.assertIn("$SIGNING_LOCK", signing_source)
        self.assertIn("requirements-upstream-registry-signing.lock", self.source)
        writers = [
            name
            for name, job in jobs.items()
            if job.get("permissions", {}).get("contents") == "write"
            or job.get("permissions", {}).get("pull-requests") == "write"
        ]
        self.assertEqual(writers, ["publication"])
        for name in ("inspection", "signing"):
            serialized = json.dumps(jobs[name])
            self.assertNotIn("git push", serialized)
            self.assertNotIn("gh pr create", serialized)
        self.assertIn("gh pr create --draft", json.dumps(jobs["publication"]))

    def test_all_lifecycle_operations_are_data_only_and_draft(self):
        for operation in ("bootstrap", "add", "revoke", "restore", "renew", "verify"):
            self.assertIn(operation, self.source)
        self.assertIn("gh pr create --draft", self.source)
        self.assertIn("This pull request is data-only", self.source)
        for forbidden in (
            "docker push",
            "docker/login-action",
            "build-push-action",
            "gh release create",
            "git tag",
            "kubectl",
        ):
            self.assertNotIn(forbidden, self.source)

    def test_pull_request_ci_cannot_publish_or_access_signing_environment(self):
        ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        self.assertEqual(ci["permissions"], {"contents": "read"})
        serialized = json.dumps(ci)
        self.assertNotIn("packages", serialized)
        self.assertNotIn("docker/login-action", serialized)
        self.assertNotIn("upstream-attestation-signing", serialized)


class DeferredWriteEvidenceTests(unittest.TestCase):
    def test_deferred_registry_operations_are_evidence_only(self):
        evidence = json.loads(DEFERRED_EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(evidence["evidence_only"])
        self.assertFalse(evidence["runtime_admission"])
        self.assertEqual(
            {item["name"] for item in evidence["tools"]},
            {"ha_set_entity", "ha_set_device"},
        )
        self.assertEqual(evidence["decision"]["dev9_public_tools_added"], 0)
        self.assertEqual(evidence["decision"]["dev9_upstream_allowlist_changes"], 0)
        self.assertEqual(evidence["decision"]["dev9_governance_schema_changes"], 0)
        self.assertTrue(
            all(item["annotations"]["destructiveHint"] for item in evidence["tools"])
        )


if __name__ == "__main__":
    unittest.main()
