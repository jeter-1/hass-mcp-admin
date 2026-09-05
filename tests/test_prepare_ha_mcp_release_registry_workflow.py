from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = (
    ROOT
    / ".github"
    / "workflows"
    / "prepare-ha-mcp-release-registry-update.yml"
)
SCRIPT_PATH = ROOT / "scripts" / "prepare_ha_mcp_release_registry_update.py"
FIXTURE_PATH = ROOT / "scripts" / "fake_ha_read_gateway_contract_server.py"


class PrepareHaMcpReleaseRegistryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.fixture = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_workflow_is_manual_protected_main_only(self) -> None:
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        self.assertEqual(
            set(triggers["workflow_dispatch"]["inputs"]),
            {"upstream_version"},
        )
        job = self.workflow["jobs"]["prepare"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(
            job["environment"], "ha-mcp-release-registry-signing"
        )
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            job["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        self.assertEqual(job["timeout-minutes"], 75)
        self.assertNotIn("packages", json.dumps(job["permissions"]))

    def test_actions_are_immutable_and_locations_are_fixed(self) -> None:
        uses = [
            step["uses"]
            for step in self.workflow["jobs"]["prepare"]["steps"]
            if "uses" in step
        ]
        self.assertTrue(uses)
        self.assertTrue(
            all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in uses)
        )
        self.assertIn(
            "https://github.com/homeassistant-ai/ha-mcp.git", self.source
        )
        self.assertIn("ghcr.io/homeassistant-ai/ha-mcp", self.source)
        self.assertIn("refs/tags/v${VERSION}", self.source)
        self.assertIn("source_tree", self.source)
        self.assertIn("architecture_image_digests", self.source)

    def test_private_key_is_scoped_only_to_signing_step(self) -> None:
        job = self.workflow["jobs"]["prepare"]
        self.assertNotIn("HA_MCP_RELEASE_REGISTRY_SIGNING_KEY", job["env"])
        steps = job["steps"]
        signing = next(
            step
            for step in steps
            if step.get("name")
            == "Sign and verify data-only release-registry update"
        )
        self.assertEqual(
            set(signing["env"]), {"HA_MCP_RELEASE_REGISTRY_SIGNING_KEY"}
        )
        for step in steps:
            if step is signing:
                continue
            self.assertNotIn(
                "HA_MCP_RELEASE_REGISTRY_SIGNING_KEY", step.get("env", {})
            )

    def test_runtime_review_is_exact_repeatable_and_read_only(self) -> None:
        self.assertIn("@${{ steps.image.outputs.index_digest }}", self.source)
        self.assertIn("--read-only", self.source)
        self.assertIn("--tmpfs /tmp", self.source)
        self.assertEqual(
            self.source.count("review_upstream_read_release.py capture"), 2
        )
        self.assertIn("cmp .compat/capture-1.json", self.source)
        self.assertIn('stats["http_mutations"]', self.source)
        self.assertIn('stats["websocket_mutations"]', self.source)
        self.assertIn('stats["operational_backup_creates"]', self.source)
        self.assertIn('stats["approval_notification_calls"]', self.source)

    def test_workflow_creates_only_bounded_data_pr(self) -> None:
        self.assertIn("gh pr create --draft", self.source)
        self.assertIn("git push origin", self.source)
        self.assertNotIn("docker push", self.source)
        self.assertNotIn("build-push-action", self.source)
        self.assertNotIn("gh release", self.source)
        self.assertNotIn("git tag", self.source)
        self.assertNotIn("gh pr merge", self.source)
        self.assertNotIn("workflow_run", self.source)
        for path in (
            "upstream-trust/ha-mcp-release-registry.json",
            "docs/evidence/ha-mcp-release-registry/ha-mcp-${VERSION}.json",
            "docs/generated/HA_MCP_RELEASE_REGISTRY_INDEX.md",
        ):
            self.assertIn(path, self.source)
        self.assertIn("Dashboard authority: separate", self.source)
        self.assertIn("Engineering image publication: false", self.source)
        self.assertIn("Deployment or restart: false", self.source)

    def test_signer_accepts_no_authority_or_location_arguments(self) -> None:
        arguments = set(
            re.findall(r'add_argument\("--([a-z0-9-]+)"', self.script)
        )
        self.assertEqual(
            arguments, {"version", "operation", "revocation-reason"}
        )
        for prohibited in (
            "--repository",
            "--registry-url",
            "--output",
            "--profile",
            "--adapter",
            "--private-key",
        ):
            self.assertNotIn(prohibited, self.script)
        self.assertIn(
            'os.environ.get("HA_MCP_RELEASE_REGISTRY_SIGNING_KEY"',
            self.script,
        )
        self.assertIsNone(re.search(r"(?m)^\s*print\(", self.script))

    def test_disposable_fixture_accepts_bounded_future_stable_versions(self) -> None:
        self.assertIn("type=_stable_version", self.fixture)
        self.assertIn("STABLE_VERSION.fullmatch", self.fixture)
        self.assertNotIn('choices=(\n            "7.14.1"', self.fixture)

    def test_pull_request_ci_has_no_signing_environment(self) -> None:
        ci = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(ci["permissions"], {"contents": "read"})
        serialized = json.dumps(ci)
        self.assertNotIn("HA_MCP_RELEASE_REGISTRY_SIGNING_KEY", serialized)
        self.assertNotIn("ha-mcp-release-registry-signing", serialized)


if __name__ == "__main__":
    unittest.main()
