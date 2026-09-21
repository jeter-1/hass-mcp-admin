"""Offline automatic provenance and original-attempt recovery regressions.

Synthetic event shape: GitHub's completed workflow_run contract (no top-level
ref). Buildx ac30b249211430b85fb8f37b6e7154b5c47ba0b6/util/ghutil/ghutil.go copies
GITHUB_EVENT_PATH verbatim. Only Git/API/registry transport is substituted;
claim, event, OCI digest, SLSA, image label and SBOM validators remain real.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml
from tests import test_publication_handoff as h
from tests import test_publication_source_verifier as f


class WorkflowRunProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.handoff = h.HandoffTests()
        self.handoff.setUp()
        self.store = h.ClaimTests()
        self.store.setUp()
        self.env = {
            "GITHUB_REPOSITORY": f.REPOSITORY, "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": f.REPOSITORY + "/" + f.MODULE.WORKFLOW_PATH + "@refs/heads/main",
            "GITHUB_SHA": f.WORKFLOW_SHA, "GITHUB_WORKFLOW_SHA": f.WORKFLOW_SHA,
            "GITHUB_RUN_ID": f.RUN_ID, "GITHUB_RUN_ATTEMPT": str(f.RUN_ATTEMPT),
            "GITHUB_EVENT_NAME": "workflow_run",
        }
        patch = mock.patch.object(f, "RELEASE_SHA", h.MERGE)
        patch.start()
        self.addCleanup(patch.stop)
        self.binding = json.loads(self.handoff.verify()["source_handoff"])
        self.claim = h.claim.identity(self.env, f.RELEASE_SHA, f.VERSION, json.dumps(self.binding))
        self.workflow = yaml.safe_load((h.ROOT / ".github/workflows/publish-rc-image.yml").read_text())
        self.steps = self.workflow["jobs"]["promote"]["steps"]
        self.build_count = 0

    def step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def payload(self):
        return {
            "action": "completed", "repository": {"full_name": f.REPOSITORY},
            # Sender is not approval authority; the verified run actors are.
            "sender": {"login": "ghost"},
            "workflow": {"id": 8, "path": h.handoff.PRODUCER},
            "workflow_run": {**deepcopy(self.handoff.run), "head_branch": "reviewed-release"},
        }

    def evidence(self, root, event="workflow_run", mutate=None, tamper_blob=None):
        def statement_mutator(platform, predicate_type, statement):
            if predicate_type != f.MODULE.SLSA_PREDICATE_TYPE:
                return
            internal = statement["predicate"]["buildDefinition"]["internalParameters"]
            internal["github_event_name"] = event
            if event == "workflow_run":
                internal["github_event_payload"] = self.payload()
            elif event == "push":
                internal["github_event_payload"].pop("inputs")
            if mutate:
                mutate(statement["predicate"])
        manifest, transport, _, sbom = f.registry_attestation_fixture(
            statement_mutator=statement_mutator, tamper_blob=tamper_blob)
        manifest_path, digest, _, _, images = f.PublicationSourceVerifierTests().write_evidence(
            root, manifest=manifest, sbom=sbom)
        return manifest_path, digest, images, transport

    def verify(self, root, evidence, *, event="workflow_run", binding=None, overrides=None):
        manifest, digest, images, transport = evidence
        expected = {
            "expected-digest": digest, "expected-release-sha": f.RELEASE_SHA,
            "expected-version": f.VERSION, "expected-build-time": f.BUILD_TIME,
            "expected-run-id": f.RUN_ID, "expected-run-attempt": str(f.RUN_ATTEMPT),
            "expected-workflow-sha": f.WORKFLOW_SHA, "expected-event-name": event,
            "expected-handoff": (json.dumps(self.binding if binding is None else binding)
                                 if event == "workflow_run" else ""),
        }
        expected.update(overrides or {})
        output = root / "verified-output"
        output.unlink(missing_ok=True)
        args = ["verify-source", "--release-repo", str(root), "--manifest-json", str(manifest),
                "--image-repository", f.IMAGE_REPOSITORY, "--repository", f.REPOSITORY,
                "--owner", f.OWNER, "--github-output", str(output)]
        for key, value in expected.items():
            args += ["--" + key, value]
        for platform, path in images.items():
            args += ["--image-json", platform + "=" + str(path)]
        original = f.MODULE._attestation_statements
        error = io.StringIO()
        with mock.patch.object(f.MODULE, "release_blob", return_value=b"arch: [amd64, aarch64, armv7]\n"), \
                mock.patch.object(f.MODULE, "release_build_inputs", return_value=None), \
                mock.patch.object(f.MODULE, "_attestation_statements", side_effect=
                                  lambda repo, index: original(repo, index, transport=transport)), \
                mock.patch("socket.create_connection", side_effect=AssertionError("offline only")), \
                redirect_stderr(error):
            result = f.MODULE.main(args)
        return result, output.read_text() if output.exists() else "", error.getvalue()

    def claim_cli(self, root, operation, *, prior=None):
        output = root / (operation + "-claim-output")
        args = ["publication_claim.py", operation, "--release", f.RELEASE_SHA,
                "--version", f.VERSION, "--github-output", str(output)]
        env = self.env
        if operation == "create":
            args += ["--handoff", json.dumps(self.binding)]
        else:
            env = {**env, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "999"}
            args += ["--run-id", f.RUN_ID, "--attempt", prior["source_run_attempt"],
                     "--workflow-sha", prior["source_workflow_sha"],
                     "--event", prior["source_event_name"]]
        create, resume = h.claim.create, h.claim.resume
        with mock.patch.object(sys, "argv", args), mock.patch.dict(os.environ, env), \
                mock.patch.object(h.claim, "create", side_effect=lambda value: create(value, self.store.post, self.store.api)), \
                mock.patch.object(h.claim, "resume", side_effect=lambda *values: resume(*values, api=self.store.api)), \
                mock.patch.object(h.claim, "git", return_value="scripts/publication_claim.py"), \
                redirect_stdout(io.StringIO()):
            h.claim.main()
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        return json.loads(values["source_handoff"])

    def resolve(self, root, digest, mode):
        # Execute the workflow's actual source resolver. Recovery rejects any
        # build output, and the build action is gated by the same source mode.
        build = self.step("Build release commit without a temporary tag")
        self.assertEqual(build["if"], "needs.detect-release.outputs.source_mode == 'build'")
        if mode == "build":
            self.build_count += 1
        output = root / (mode + "-source-output")
        result = subprocess.run(["bash", "-c", self.step("Resolve immutable source image")["run"]],
                                env={**os.environ, "SOURCE_MODE": mode,
                                     "BUILT_DIGEST": digest if mode == "build" else "",
                                     "RECOVERY_DIGEST": digest if mode == "resume_digest" else "",
                                     "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_automatic_handoff_claim_and_complete_raw_provenance_succeed(self):
        self.assertNotIn("ref", self.payload())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_binding = self.claim_cli(root, "create")
            self.assertEqual(original_binding, self.binding)
            evidence = self.evidence(root)
            source = self.resolve(root, evidence[1], "build")
            binding = original_binding
            result, output, error = self.verify(root, evidence, binding=binding)
            self.assertEqual(result, 0, error)
            self.assertIn("source_image_verified=true", output)
            self.assertIn("manifest_digest=" + source["digest"], output)
            self.assertIn("sbom_status=present", output)
            self.assertEqual(source["source_image_published"], "true")
            self.assertEqual(self.build_count, 1)
            self.assertEqual(len(self.store.writes), 2)

    def test_original_automatic_digest_recovery_passes_without_second_build_or_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_binding = self.claim_cli(root, "create")
            self.assertEqual(original_binding, self.binding)
            evidence = self.evidence(root)
            self.resolve(root, evidence[1], "build")
            # The original publisher failed after producing this digest. A later
            # attempt exists, so only the explicitly selected original may resume.
            metadata = f.valid_run_metadata()
            metadata["event"] = "workflow_run"
            run_file, run_output = root / "run.json", root / "run-output"
            run_file.write_text(json.dumps(metadata))
            args = ["verify-recovery-run", "--run-json", str(run_file),
                    "--expected-run-id", f.RUN_ID, "--expected-run-attempt", str(f.RUN_ATTEMPT),
                    "--expected-repository", f.REPOSITORY, "--expected-owner", f.OWNER,
                    "--github-output", str(run_output)]
            self.assertEqual(f.MODULE.main(args), 0)
            prior = dict(line.split("=", 1) for line in run_output.read_text().splitlines())
            recovering = h.claim.identity({**self.env, "GITHUB_EVENT_NAME": "workflow_dispatch",
                                           "GITHUB_RUN_ID": "999", "GITHUB_RUN_ATTEMPT": "2"},
                                          f.RELEASE_SHA, f.VERSION)
            binding = self.claim_cli(root, "resume", prior=prior)
            self.assertEqual(binding, original_binding)
            source = self.resolve(root, evidence[1], "resume_digest")
            result, output, error = self.verify(root, evidence, binding=binding, overrides={
                "expected-run-attempt": prior["source_run_attempt"],
                "expected-workflow-sha": prior["source_workflow_sha"],
                "expected-event-name": prior["source_event_name"],
                "expected-digest": source["digest"],
            })
            self.assertEqual(result, 0, error)
            self.assertIn("source_image_verified=true", output)
            self.assertEqual(source["source_image_reused"], "true")
            self.assertEqual(source["source_image_published"], "false")
            self.assertEqual(self.build_count, 1)
            self.assertEqual(len(self.store.writes), 2)
            metadata["run_attempt"] = 2
            run_file.write_text(json.dumps(metadata))
            run_output.unlink()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(f.MODULE.main(args), 1)
            self.assertFalse(run_output.exists())
            with self.assertRaises(ValueError):
                h.claim.resume(recovering, int(f.RUN_ID), 2, f.WORKFLOW_SHA, "workflow_run", self.store.api)

    def test_owner_comment_handoff_uses_its_verified_base_identity(self):
        self.handoff.run.update(event="issue_comment", head_sha=h.BASE)
        self.handoff.job["head_sha"] = h.BASE
        self.handoff.receipt["source_event"] = "issue_comment"
        self.handoff.bind_artifact()
        self.binding = json.loads(self.handoff.verify()["source_handoff"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, output, error = self.verify(root, self.evidence(root))
            self.assertEqual(result, 0, error)
            self.assertIn("source_image_verified=true", output)

    def test_push_and_manual_dispatch_complete_provenance_controls(self):
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result, output, error = self.verify(root, self.evidence(root, event), event=event)
                self.assertEqual(result, 0, error)
                self.assertIn("source_image_verified=true", output)
                for ref in (None, "refs/heads/feature"):
                    def mutate(slsa):
                        slsa["buildDefinition"]["internalParameters"]["github_event_payload"]["ref"] = ref
                    self.assertEqual(self.verify(root, self.evidence(root, event, mutate), event=event)[0], 1)
                if event == "workflow_dispatch":
                    def wrong_input(slsa):
                        slsa["buildDefinition"]["internalParameters"]["github_event_payload"]["inputs"]["release_sha"] = h.HEAD
                    self.assertEqual(self.verify(root, self.evidence(root, event, wrong_input), event=event)[0], 1)

    def test_source_payload_identity_mismatches_refuse_complete_verification(self):
        cases = [
            (("action",), "requested"), (("repository", "full_name"), "foreign/repo"),
            (("workflow", "id"), 42), (("workflow", "path"), "other.yml"),
        ]
        for key, bad in (
            ("id", 8), ("run_attempt", 2), ("workflow_id", 42), ("path", "other.yml"),
            ("event", "push"), ("head_sha", h.BASE), ("status", "in_progress"),
            ("conclusion", "failure"), ("id", True), ("run_attempt", True),
            ("repository", {"full_name": "foreign/repo"}),
            ("head_repository", {"full_name": "foreign/repo"}),
            ("actor", {"login": "other"}), ("triggering_actor", {"login": "other"}),
        ):
            cases.append((("workflow_run", key), bad))
        for path, bad in cases:
            with self.subTest(path=path, bad=bad), tempfile.TemporaryDirectory() as tmp:
                def mutate(slsa):
                    item = slsa["buildDefinition"]["internalParameters"]["github_event_payload"]
                    for key in path[:-1]:
                        item = item[key]
                    item[path[-1]] = bad
                root = Path(tmp)
                result, output, _ = self.verify(root, self.evidence(root, mutate=mutate))
                self.assertEqual(result, 1)
                self.assertEqual(output, "")

    def test_publisher_identity_source_and_digest_checks_remain_required(self):
        for key, bad in (("github_ref", "refs/heads/feature"), ("github_workflow_ref", "other"),
                         ("github_workflow_sha", h.HEAD), ("github_run_id", "999"),
                         ("github_run_attempt", "2"), ("github_actor", "other"),
                         ("github_triggering_actor", "other"), ("github_repository", "foreign/repo")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                def mutate(slsa):
                    slsa["buildDefinition"]["internalParameters"][key] = bad
                root = Path(tmp)
                result, output, _ = self.verify(root, self.evidence(root, mutate=mutate))
                self.assertEqual((result, output), (1, ""))
        for change in ({"expected-digest": "sha256:" + "0" * 64},
                       {"expected-release-sha": h.HEAD},
                       {"expected-run-attempt": "2"}, {"expected-run-id": "999"},
                       {"expected-workflow-sha": h.HEAD}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.assertEqual(self.verify(root, self.evidence(root), overrides=change)[:2], (1, ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = self.evidence(root, tamper_blob=("linux/amd64", f.MODULE.SLSA_PREDICATE_TYPE))
            self.assertEqual(self.verify(root, evidence)[:2], (1, ""))

    def test_missing_or_malformed_independent_binding_cannot_borrow_image_assertions(self):
        for raw in ("", "{}", "null", "x" * 2049, '{"run_id":7,"run_id":7}'):
            with self.subTest(raw=raw[:30]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.assertEqual(self.verify(root, self.evidence(root), overrides={"expected-handoff": raw})[:2], (1, ""))
        for key, bad in (("run_id", 8), ("run_attempt", 2), ("workflow_id", 42),
                         ("event", "issue_comment"), ("head_sha", h.BASE), ("release_sha", h.HEAD)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.assertEqual(self.verify(root, self.evidence(root), binding={**self.binding, key: bad})[:2], (1, ""))

    def test_workflow_carries_verified_binding_through_claim_for_both_modes(self):
        detector = self.workflow["jobs"]["detect-release"]
        self.assertEqual(detector["outputs"]["source_handoff"], "${{ steps.handoff.outputs.source_handoff }}")
        attempt = self.step("Consume create-only publication attempt authority")
        self.assertEqual(attempt["env"]["SOURCE_HANDOFF"], "${{ needs.detect-release.outputs.source_handoff }}")
        self.assertIn('--handoff "$SOURCE_HANDOFF" --github-output "$GITHUB_OUTPUT"', attempt["run"])
        self.assertEqual(attempt["run"].count('--github-output "$GITHUB_OUTPUT"'), 2)
        verifier = self.step("Verify digest-addressed image architectures and raw attestations anonymously")
        self.assertEqual(verifier["env"]["EXPECTED_HANDOFF"], "${{ steps.attempt.outputs.source_handoff }}")
        self.assertIn('--expected-handoff "$EXPECTED_HANDOFF"', verifier["run"])


if __name__ == "__main__":
    unittest.main()
