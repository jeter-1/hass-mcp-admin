from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def _parse_architecture_index(self, value: object) -> dict[str, str]:
        steps = self.workflow["jobs"]["prepare"]["steps"]
        run = next(step for step in steps if step.get("id") == "image")["run"]
        marker = 'python - "$raw" "$RUNNER_TEMP/platforms.json" <<\'PY\'\n'
        code = run.split(marker, 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "index.json"
            output = Path(directory) / "platforms.json"
            source.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(sys, "argv", ["-", str(source), str(output)]):
                exec(compile(code, "<workflow-platform-parser>", "exec"), {})
            return json.loads(output.read_text(encoding="utf-8"))

    def test_workflow_is_manual_protected_main_only(self) -> None:
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        self.assertEqual(
            set(triggers["workflow_dispatch"]["inputs"]),
            {"operation", "revocation_reason", "upstream_version"},
        )
        operation = triggers["workflow_dispatch"]["inputs"]["operation"]
        self.assertEqual(operation["type"], "choice")
        self.assertEqual(operation["options"], ["add", "revoke"])
        self.assertEqual(operation["default"], "add")
        reason = triggers["workflow_dispatch"]["inputs"]["revocation_reason"]
        self.assertEqual(reason["type"], "string")
        self.assertFalse(reason["required"])
        self.assertEqual(reason["default"], "")
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
            set(signing["env"]),
            {
                "HA_MCP_RELEASE_REGISTRY_REVOCATION_REASON",
                "HA_MCP_RELEASE_REGISTRY_SIGNING_KEY",
                "OPERATION",
            },
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

    def test_mutable_tag_is_resolved_once_then_never_reused(self) -> None:
        steps = self.workflow["jobs"]["prepare"]["steps"]
        image = next(step for step in steps if step.get("id") == "image")
        run = image["run"]
        self.assertEqual(
            len(re.findall(r'imagetools inspect(?: --raw)? "\$tagged"', run)),
            1,
        )
        self.assertIn('imagetools inspect "$tagged"', run)
        self.assertNotIn('imagetools inspect --raw "$tagged"', run)
        self.assertIn('immutable="${UPSTREAM_IMAGE_REPOSITORY}@${index_digest}"', run)
        self.assertIn('imagetools inspect "$immutable"', run)
        self.assertIn('imagetools inspect --raw "$immutable"', run)
        self.assertIn('docker pull --platform linux/amd64 "$immutable"', run)
        self.assertIn('test "$reported_digest" = "$index_digest"', run)

        capture = next(
            step
            for step in steps
            if step.get("name") == "Capture exact runtime catalog twice"
        )["run"]
        self.assertIn(
            "immutable=\"${UPSTREAM_IMAGE_REPOSITORY}"
            "@${{ steps.image.outputs.index_digest }}\"",
            capture,
        )
        self.assertIn('"$immutable" ha-mcp-web', capture)
        self.assertNotIn('${UPSTREAM_IMAGE_REPOSITORY}:${VERSION}', capture)

    def test_architecture_manifest_extraction_fails_closed(self) -> None:
        amd64 = {
            "platform": {"os": "linux", "architecture": "amd64"},
            "digest": "sha256:" + "1" * 64,
        }
        arm64 = {
            "platform": {"os": "linux", "architecture": "arm64"},
            "digest": "sha256:" + "2" * 64,
        }
        self.assertEqual(
            self._parse_architecture_index({"manifests": [amd64, arm64]}),
            {
                "linux/amd64": "sha256:" + "1" * 64,
                "linux/arm64": "sha256:" + "2" * 64,
            },
        )
        cases = {
            "missing": {"manifests": [amd64]},
            "duplicate": {"manifests": [amd64, dict(amd64), arm64]},
            "malformed": {
                "manifests": [
                    {**amd64, "digest": "sha256:not-a-digest"},
                    arm64,
                ]
            },
        }
        for name, value in cases.items():
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self._parse_architecture_index(value)

    def test_revocation_is_denial_only_and_skips_upstream_observation(self) -> None:
        steps = self.workflow["jobs"]["prepare"]["steps"]
        add_only = {
            "Resolve exact official source tag",
            "Set up Docker Buildx",
            "Resolve exact official image",
            "Start disposable read-only Home Assistant fixture",
            "Capture exact runtime catalog twice",
            "Generate bounded immutable release evidence",
            "Clean up disposable runtime",
        }
        for step in steps:
            if step.get("name") in add_only:
                condition = step.get("if")
                if step.get("name") == "Clean up disposable runtime":
                    self.assertEqual(
                        condition, "${{ always() && inputs.operation == 'add' }}"
                    )
                else:
                    self.assertEqual(condition, "inputs.operation == 'add'")

        signing = next(
            step
            for step in steps
            if step.get("name")
            == "Sign and verify data-only release-registry update"
        )
        self.assertIn("HA_MCP_RELEASE_REGISTRY_REVOCATION_REASON", signing["env"])
        self.assertIn('--operation "$OPERATION"', signing["run"])
        self.assertNotIn("--revocation-reason", signing["run"])
        validation = next(
            step
            for step in steps
            if step.get("name") == "Validate protected-main request"
        )["run"]
        self.assertIn("reason.encode", validation)
        self.assertIn("reason.strip", validation)
        self.assertIn("unicodedata.category", validation)
        self.assertIn('character in {"\\u2028", "\\u2029"}', validation)

        branch = next(step for step in steps if step.get("id") == "branch")["run"]
        self.assertIn('if [[ "$OPERATION" == "add" ]]', branch)
        self.assertIn('expected_count=3', branch)
        self.assertIn('expected_count=2', branch)
        self.assertIn('data/ha-mcp-release-${OPERATION}-${VERSION}', branch)
        self.assertIn("Revoke ha-mcp ${VERSION} compatible-read authority", branch)
        self.assertIn('test "$staged" = "$expected"', branch)
        self.assertIn('test "$changed" = "$expected"', branch)
        summary = next(
            step
            for step in steps
            if step.get("name") == "Write bounded preparation summary"
        )["run"]
        self.assertNotIn("REVOCATION_REASON", summary)

    def test_add_removes_ephemeral_inputs_before_exact_file_gate(self) -> None:
        steps = self.workflow["jobs"]["prepare"]["steps"]
        cleanup = next(
            step
            for step in steps
            if step.get("name") == "Remove bounded add preparation inputs"
        )
        branch = next(step for step in steps if step.get("id") == "branch")
        cleanup_index = steps.index(cleanup)
        branch_index = steps.index(branch)
        self.assertLess(cleanup_index, branch_index)
        self.assertEqual(cleanup["if"], "inputs.operation == 'add'")
        self.assertIn(
            "test -f .compat/ha-mcp-runtime-capture.json", cleanup["run"]
        )
        self.assertIn(
            "test -f .compat/ha-mcp-release-evidence.json", cleanup["run"]
        )
        self.assertIn("rmdir -- .compat", cleanup["run"])

        final_cleanup = next(
            step
            for step in steps
            if step.get("name") == "Clean up disposable runtime"
        )
        self.assertEqual(
            final_cleanup["if"],
            "${{ always() && inputs.operation == 'add' }}",
        )
        self.assertIn("rm -rf .compat", final_cleanup["run"])

        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)

            def run(*arguments: str, script: str | None = None) -> None:
                command = (
                    ["git", *arguments]
                    if script is None
                    else ["bash", "-c", script]
                )
                subprocess.run(
                    command,
                    cwd=repository,
                    env={
                        **os.environ,
                        "GITHUB_RUN_ID": "12345",
                        "OPERATION": "add",
                        "VERSION": "8.4.4",
                    },
                    check=True,
                    capture_output=True,
                    text=True,
                )

            run("init")
            run("config", "user.name", "fixture")
            run("config", "user.email", "fixture@example.invalid")
            baseline = {
                "upstream-trust/ha-mcp-release-registry.json": "{}\n",
                "docs/generated/HA_MCP_RELEASE_REGISTRY_INDEX.md": "baseline\n",
            }
            for relative, value in baseline.items():
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="utf-8")
            run("add", ".")
            run("commit", "-m", "baseline")

            generated = {
                "upstream-trust/ha-mcp-release-registry.json": '{"updated":true}\n',
                "docs/generated/HA_MCP_RELEASE_REGISTRY_INDEX.md": "updated\n",
                "docs/evidence/ha-mcp-release-registry/ha-mcp-8.4.4.json": "{}\n",
                ".compat/ha-mcp-runtime-capture.json": "{}\n",
                ".compat/ha-mcp-release-evidence.json": "{}\n",
            }
            for relative, value in generated.items():
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="utf-8")

            enforce_before_push = branch["run"].split(
                'git push origin "HEAD:refs/heads/${branch}"', 1
            )[0]
            run(script=cleanup["run"] + "\n" + enforce_before_push)

            committed = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", "HEAD"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(
                sorted(filter(None, committed)),
                [
                    "docs/evidence/ha-mcp-release-registry/ha-mcp-8.4.4.json",
                    "docs/generated/HA_MCP_RELEASE_REGISTRY_INDEX.md",
                    "upstream-trust/ha-mcp-release-registry.json",
                ],
            )
            self.assertFalse((repository / ".compat").exists())

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
