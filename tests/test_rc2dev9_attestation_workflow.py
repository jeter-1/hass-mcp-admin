import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
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
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"evidence", "sign", "publish"})
        for job in jobs.values():
            self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
            self.assertNotIn("packages", json.dumps(job["permissions"]))
        self.assertEqual(jobs["sign"]["environment"], "upstream-attestation-signing")
        self.assertNotIn("environment", jobs["evidence"])
        self.assertNotIn("environment", jobs["publish"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(jobs["evidence"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["sign"]["permissions"], {"contents": "read"})
        self.assertEqual(
            jobs["publish"]["permissions"],
            {"contents": "write", "pull-requests": "write"},
        )
        self.assertEqual(jobs["sign"]["needs"], "evidence")
        self.assertEqual(jobs["publish"]["needs"], "sign")
        self.assertNotIn("packages", json.dumps(self.workflow["permissions"]))

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
        job_env = self.workflow["jobs"]["sign"]["env"]
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", job_env)
        for name in (
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY",
            "UPSTREAM_TRUST_REGISTRY_KEY_ID",
        ):
            self.assertEqual(self.source.count(f"secrets.{name}"), 1)
            for other in ("evidence", "publish"):
                self.assertNotIn(name, json.dumps(self.workflow["jobs"][other]))
        steps = self.workflow["jobs"]["sign"]["steps"]
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

    def _step(self, job, name):
        return next(
            step
            for step in self.workflow["jobs"][job]["steps"]
            if step.get("name") == name or step.get("id") == name
        )

    def _run(self, cwd, script, extra):
        return subprocess.run(
            ["bash", "-c", script],
            cwd=cwd,
            env={**os.environ, "VERSION": "8.5.0", "GITHUB_RUN_ID": "12345", **extra},
            capture_output=True,
            text=True,
        )

    def _signed_update(self, root, changes):
        signer = root / "signer"
        signer.mkdir()
        git = lambda *args, cwd=signer: subprocess.run(  # noqa: E731
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        )
        git("init")
        git("config", "user.name", "fixture")
        git("config", "user.email", "fixture@example.invalid")
        for relative in (
            "upstream-trust/upstream-dashboard-registry.json",
            "upstream-trust/upstream-dashboard-registry.sig.json",
            "docs/generated/UPSTREAM_TRUST_REGISTRY_INDEX.md",
        ):
            path = signer / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("baseline\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "baseline")
        git("clone", "--quiet", str(signer), str(root / "publisher"), cwd=root)
        git("config", "user.name", "fixture", cwd=root / "publisher")
        git("config", "user.email", "fixture@example.invalid", cwd=root / "publisher")
        for relative, value in changes.items():
            path = signer / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        (root / "runner").mkdir()
        output = root / "output"
        result = self._run(
            signer,
            self._step("sign", "stage")["run"],
            {"RUNNER_TEMP": str(root / "runner"), "GITHUB_OUTPUT": str(output)},
        )
        manifest = None
        if output.exists():
            manifest = dict(
                line.split("=", 1) for line in output.read_text().splitlines()
            ).get("manifest_sha256")
        return result, manifest

    def _publish(self, root, manifest):
        apply = self._step("publish", "Verify and apply exact signed data-only update")["run"]
        branch = self._step("publish", "branch")["run"]
        marker = "git -c credential.helper="
        self.assertEqual(branch.count(marker), 1)
        return self._run(
            root / "publisher",
            apply + "\n" + branch.split(marker, 1)[0],
            {"RUNNER_TEMP": str(root / "runner"), "MANIFEST_SHA256": manifest},
        )

    def test_signed_update_is_hash_bound_from_signer_to_publication(self):
        changes = {
            "upstream-trust/upstream-dashboard-registry.json": "updated\n",
            "upstream-trust/upstream-dashboard-registry.sig.json": "signature\n",
            "docs/evidence/upstream-compatibility/ha-mcp-8.5.0.json": "{}\n",
            "docs/generated/UPSTREAM_TRUST_REGISTRY_INDEX.md": "index\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, manifest = self._signed_update(root, changes)
            self.assertEqual(result.returncode, 0, result.stderr)
            published = self._publish(root, manifest)
            self.assertEqual(published.returncode, 0, published.stderr)
            committed = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", "HEAD"],
                cwd=root / "publisher",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            self.assertEqual(sorted(committed), sorted(changes))
            self.assertFalse((root / "runner" / "signed").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, _manifest = self._signed_update(
                root, {**changes, "scripts/unexpected.py": "x\n"}
            )
            self.assertNotEqual(result.returncode, 0)

        for name in ("tamper", "inject", "manifest"):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result, manifest = self._signed_update(root, changes)
                self.assertEqual(result.returncode, 0, result.stderr)
                files = root / "runner" / "signed" / "files"
                if name == "tamper":
                    (files / "upstream-trust/upstream-dashboard-registry.json").write_text("x\n")
                elif name == "inject":
                    (files / "scripts").mkdir()
                    (files / "scripts/injected.py").write_text("x\n")
                else:
                    manifest = "0" * 64
                published = self._publish(root, manifest)
                self.assertNotEqual(published.returncode, 0)
                log = subprocess.run(
                    ["git", "log", "--format=%s"],
                    cwd=root / "publisher",
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.split("\n")
                self.assertEqual(list(filter(None, log)), ["baseline"])

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
