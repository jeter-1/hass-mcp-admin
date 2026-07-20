import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "prepare-upstream-compatibility-attestation.yml"
)
SCRIPT_PATH = ROOT / "scripts" / "prepare_upstream_compatibility_attestation.py"
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
        job = self.workflow["jobs"]["attest"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(job["environment"], "upstream-attestation-signing")
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            job["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        self.assertNotIn("packages", json.dumps(self.workflow["permissions"]))
        self.assertNotIn("packages", json.dumps(job["permissions"]))

    def test_fixed_upstream_locations_and_stable_version_only(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(
            set(triggers["workflow_dispatch"]["inputs"]),
            {"upstream_version"},
        )
        self.assertIn("https://github.com/homeassistant-ai/ha-mcp.git", self.source)
        self.assertIn("ghcr.io/homeassistant-ai/ha-mcp", self.source)
        self.assertIn(
            '[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$ ]]',
            self.source,
        )
        self.assertIn("refs/tags/v${VERSION}", self.source)

    def test_private_key_is_scoped_only_to_signing_step(self):
        job_env = self.workflow["jobs"]["attest"]["env"]
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", job_env)
        steps = self.workflow["jobs"]["attest"]["steps"]
        signing = next(step for step in steps if step.get("name") == "Sign and verify data-only registry update")
        self.assertIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", signing["env"])
        for step in steps:
            if step is signing:
                continue
            self.assertNotIn(
                "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
                step.get("env", {}),
            )

    def test_workflow_creates_only_a_data_pr_and_never_publishes(self):
        self.assertIn("gh pr create --draft", self.source)
        self.assertNotIn("docker push", self.source)
        self.assertNotIn("build-push-action", self.source)
        self.assertNotIn("gh release create", self.source)
        self.assertNotIn("git tag", self.source)
        self.assertIn("Image push: false", self.source)
        self.assertIn("Engineering release: false", self.source)
        allowed = {
            "upstream-trust/upstream-dashboard-registry.json",
            "upstream-trust/upstream-dashboard-registry.sig.json",
            "docs/generated/UPSTREAM_TRUST_REGISTRY_INDEX.md",
        }
        for path in allowed:
            self.assertIn(path, self.source)

    def test_signing_script_accepts_no_location_or_key_arguments(self):
        arguments = set(re.findall(r'add_argument\("--([a-z0-9-]+)"', self.script))
        self.assertEqual(arguments, {"version"})
        self.assertIn('os.environ.get("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"', self.script)
        self.assertNotIn("print(", self.script)

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
