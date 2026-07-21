import ast
import base64
import copy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prepare_upstream_registry_inspection import (  # noqa: E402
    prepare_inspection_artifact,
)
from scripts.upstream_registry_signing_core import (  # noqa: E402
    CONTRACT_FAMILY,
    INSPECTION_ARTIFACT_NAME,
    REGISTRY_PATH,
    SIGNED_ARTIFACT_NAME,
    WHEELHOUSE_ARTIFACT_NAME,
    SigningCoreError,
    TrustedInputs,
    allowed_output_paths,
    canonical_file,
    canonical_json,
    normalize_runtime_contract,
    prepare_signing,
    reviewed_security_projection,
    sha256_digest,
    sign_prepared,
    validate_inspection_artifact,
    verify_and_assemble_artifacts,
    verify_signed_artifact_directory,
)


WORKFLOW = ROOT / ".github/workflows/prepare-upstream-compatibility-attestation.yml"
CONTRACT = (
    ROOT
    / "hass_mcp_engineering_beta"
    / "ha_mcp_engineering"
    / "providers"
    / "contracts"
    / "ha_mcp_7_14_dashboard_read_v2.json"
)


def run_git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class ProtectedSignerFixture:
    def __init__(self, root: Path):
        self.root = root
        self.repository = root / "repository"
        self.inspection = root / "registry-inspection"
        self.prepared = root / "prepared-signing"
        self.signatures = root / "signature-fragments"
        self.signed = root / "signed-registry"
        self.evidence = root / "raw"
        self.repository.mkdir()
        run_git(self.repository, "init", "-b", "main")
        run_git(self.repository, "config", "user.name", "Test")
        run_git(self.repository, "config", "user.email", "test@example.invalid")
        (self.repository / "README.md").write_text("test\n", encoding="utf-8")
        run_git(self.repository, "add", "README.md")
        run_git(self.repository, "commit", "-m", "base")
        self.base_sha = run_git(self.repository, "rev-parse", "HEAD")
        run_git(
            self.repository,
            "update-ref",
            "refs/remotes/origin/main",
            self.base_sha,
        )
        self.private = Ed25519PrivateKey.generate()
        self.private_text = base64.b64encode(self.private.private_bytes_raw()).decode()
        self.public_text = base64.b64encode(
            self.private.public_key().public_bytes_raw()
        ).decode()
        self.key_id = "test-only-registry-key-v1"
        self.public_environment = {
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": self.public_text,
            "UPSTREAM_TRUST_REGISTRY_KEY_ID": self.key_id,
        }
        self.signing_environment = {
            **self.public_environment,
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY": self.private_text,
        }
        self.trusted = TrustedInputs(
            operation="bootstrap",
            upstream_version="7.14.2",
            expected_current_sequence=0,
            expiry_days=90,
            operator_reason="review-123",
            workflow_base_sha=self.base_sha,
            dispatch_sha=self.base_sha,
            contract_family=CONTRACT_FAMILY,
            output_paths=tuple(allowed_output_paths(1)),
            key_id=self.key_id,
        )
        self._write_evidence()

    def _write_evidence(self) -> None:
        self.evidence.mkdir()
        tool = json.loads(CONTRACT.read_text(encoding="utf-8"))
        fingerprints = normalize_runtime_contract(tool, "2025-03-26")
        descriptor = hashlib.sha256(canonical_json(tool)).hexdigest()
        runtime = {
            "server_name": "ha-mcp",
            "server_version": "7.14.2",
            "protocol_version": "2025-03-26",
            "required_tool": tool,
            "contract_fingerprints": fingerprints,
            "informational_fingerprints": {
                "raw_input_schema": hashlib.sha256(
                    canonical_json(tool["inputSchema"])
                ).hexdigest(),
                "reviewed_security_descriptor": hashlib.sha256(
                    canonical_json(reviewed_security_projection(tool))
                ).hexdigest(),
                "fixture_runtime_descriptor": descriptor,
                "published_runtime_descriptor": descriptor,
            },
            "catalog_fingerprint": "5" * 64,
            "write_dispatches": 0,
            "negative_reachability": {
                "rejected_before_dispatch": [
                    "ha_set_entity",
                    "ha_set_device",
                    "ha_call_service",
                    "ha_bulk_control",
                    "ha_config_set_dashboard",
                    "ha_config_delete_dashboard",
                ],
                "include_screenshot_true_rejected": True,
                "generic_forwarder_present": False,
            },
        }
        release = {
            "version": "7.14.2",
            "source_tag": "v7.14.2",
            "source_commit": "a" * 40,
            "image_index_digest": "sha256:" + "b" * 64,
            "image_revision": "c" * 40,
            "image_created": "2026-07-20T00:00:00Z",
            "image_source": "https://github.com/homeassistant-ai/ha-mcp",
            "dirty_label": "false",
            "platform_digests": {
                "linux/amd64": "sha256:" + "d" * 64,
                "linux/arm64": "sha256:" + "e" * 64,
                "linux/arm/v7": "sha256:" + "f" * 64,
            },
            "slsa_provenance": "present_per_platform",
            "sbom": "present",
            "official_repository": "homeassistant-ai/ha-mcp",
            "official_image": "ghcr.io/homeassistant-ai/ha-mcp",
        }
        (self.evidence / "runtime.json").write_bytes(canonical_file(runtime))
        (self.evidence / "release.json").write_bytes(canonical_file(release))
        prepare_inspection_artifact(
            output_directory=self.inspection,
            operation=self.trusted.operation,
            upstream_version=self.trusted.upstream_version,
            expected_current_sequence=self.trusted.expected_current_sequence,
            expiry_days=self.trusted.expiry_days,
            operator_reason=self.trusted.operator_reason,
            workflow_base_sha=self.trusted.workflow_base_sha,
            dispatch_sha=self.trusted.dispatch_sha,
            contract_family=self.trusted.contract_family,
            runtime_evidence=self.evidence / "runtime.json",
            release_evidence=self.evidence / "release.json",
        )

    def complete(self):
        prepare_signing(
            inspection_directory=self.inspection,
            prepared_directory=self.prepared,
            repository=self.repository,
            trusted=self.trusted,
            environment=self.public_environment,
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        sign_prepared(
            prepared_directory=self.prepared,
            signature_directory=self.signatures,
            trusted=self.trusted,
            environment=self.signing_environment,
        )
        return verify_and_assemble_artifacts(
            prepared_directory=self.prepared,
            signature_directory=self.signatures,
            output_directory=self.signed,
            repository=self.repository,
            trusted=self.trusted,
            environment=self.public_environment,
        )


class FindingOneWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)

    def test_artifacts_are_separate_and_download_to_exact_roots(self):
        inspection = self.workflow["jobs"]["inspection"]
        uploads = [
            step for step in inspection["steps"] if "actions/upload-artifact@" in step.get("uses", "")
        ]
        self.assertEqual(
            {step["with"]["name"] for step in uploads},
            {INSPECTION_ARTIFACT_NAME, WHEELHOUSE_ARTIFACT_NAME},
        )
        self.assertEqual(
            {step["with"]["path"] for step in uploads},
            {
                "${{ runner.temp }}/registry-inspection/",
                "${{ runner.temp }}/signing-wheelhouse/",
            },
        )
        signing = json.dumps(self.workflow["jobs"]["signing"])
        self.assertIn("$RUNNER_TEMP/registry-inspection", signing)
        self.assertIn("$RUNNER_TEMP/signing-wheelhouse", signing)
        self.assertNotIn("registry-artifacts/signing-wheelhouse", signing)
        publication = json.dumps(self.workflow["jobs"]["publication"])
        self.assertIn(SIGNED_ARTIFACT_NAME, publication)
        self.assertNotIn(WHEELHOUSE_ARTIFACT_NAME, publication)

    def test_seed_scope_and_three_phase_signing_are_explicit(self):
        signing = self.workflow["jobs"]["signing"]
        serialized = json.dumps(signing)
        self.assertEqual(signing["permissions"], {"contents": "read"})
        seed_steps = [
            step
            for step in signing["steps"]
            if "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY" in json.dumps(step)
        ]
        self.assertEqual(len(seed_steps), 1)
        self.assertEqual(
            seed_steps[0]["name"],
            "Sign only prevalidated canonical bytes with minimum secret scope",
        )
        for phase in ("prepare-signing", "--phase sign", "verify-artifacts"):
            self.assertIn(phase, serialized)
        self.assertNotIn("manage_upstream_trust_registry.py", serialized)
        self.assertNotIn("pip install -r hass_mcp_engineering_beta", serialized)
        self.assertIn("--no-index", serialized)
        self.assertIn("--require-hashes", serialized)
        ci_source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("run_upstream_registry_signing_clean_environment.py", ci_source)

    def test_inspection_has_no_seed_or_write_authority(self):
        inspection = self.workflow["jobs"]["inspection"]
        serialized = json.dumps(inspection)
        self.assertEqual(inspection["permissions"], {"contents": "read"})
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", serialized)
        self.assertNotIn("git push", serialized)
        self.assertNotIn("gh pr create", serialized)
        checkout = next(
            step for step in inspection["steps"] if "actions/checkout@" in step.get("uses", "")
        )
        self.assertFalse(checkout["with"]["persist-credentials"])

    def test_publication_is_only_writer_and_has_no_seed_or_wheelhouse(self):
        jobs = self.workflow["jobs"]
        writers = [
            name
            for name, job in jobs.items()
            if "write" in job.get("permissions", {}).values()
        ]
        self.assertEqual(writers, ["publication"])
        publication = json.dumps(jobs["publication"])
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", publication)
        self.assertNotIn(WHEELHOUSE_ARTIFACT_NAME, publication)
        self.assertIn("prepare_upstream_registry_publication.py", publication)


class MinimalImportBoundaryTests(unittest.TestCase):
    def test_protected_import_graph_is_stdlib_plus_cryptography_and_core(self):
        permitted = {
            "__future__",
            "argparse",
            "base64",
            "binascii",
            "copy",
            "dataclasses",
            "datetime",
            "hashlib",
            "json",
            "os",
            "pathlib",
            "re",
            "shutil",
            "subprocess",
            "sys",
            "typing",
            "cryptography",
            "scripts",
        }
        for relative in (
            "scripts/upstream_registry_signing_core.py",
            "scripts/protected_sign_upstream_registry.py",
        ):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots.add(node.module.split(".")[0])
            self.assertFalse(roots - permitted, f"{relative}: {roots - permitted}")
        command = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "import scripts.protected_sign_upstream_registry; "
            "forbidden={'aiohttp','mcp','ha_mcp_engineering.application'}; "
            "assert not any(name in sys.modules for name in forbidden)"
        )
        environment = dict(os.environ)
        environment["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", command],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_seed_bearing_function_has_no_git_network_or_reconstruction_path(self):
        source = inspect.getsource(sign_prepared)
        for forbidden in (
            "subprocess",
            "_git",
            "prepare_signing",
            "reconstruct",
            "urlopen",
            "requests",
            "aiohttp",
        ):
            self.assertNotIn(forbidden, source)


class ProtectedSignerBehaviorTests(unittest.TestCase):
    def test_real_three_phase_bootstrap_reconstructs_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProtectedSignerFixture(Path(directory))
            result = fixture.complete()
            self.assertTrue(result["verified"])
            verified = verify_signed_artifact_directory(
                fixture.signed,
                environment=fixture.public_environment,
            )
            self.assertEqual(verified["sequence"], 1)
            registry = json.loads(
                (fixture.signed / "tree" / REGISTRY_PATH).read_text(encoding="utf-8")
            )
            self.assertEqual(registry["entries"][0]["upstream_version"], "7.14.2")
            self.assertEqual(registry["entries"][0]["contract_family"], CONTRACT_FAMILY)

    def test_inspection_preview_is_not_an_authoritative_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProtectedSignerFixture(Path(directory))
            (fixture.inspection / "unsigned-candidate.json").write_text("{}\n")
            with self.assertRaisesRegex(SigningCoreError, "artifact_file_set_mismatch"):
                validate_inspection_artifact(fixture.inspection, fixture.trusted)

    def test_missing_extra_or_nested_inspection_files_fail(self):
        for mutation in ("missing", "extra", "directory"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                fixture = ProtectedSignerFixture(Path(directory))
                if mutation == "missing":
                    (fixture.inspection / "runtime-evidence.json").unlink()
                elif mutation == "extra":
                    (fixture.inspection / "extra.json").write_text("{}\n")
                else:
                    (fixture.inspection / "nested").mkdir()
                with self.assertRaises(SigningCoreError):
                    validate_inspection_artifact(fixture.inspection, fixture.trusted)

    def test_every_trusted_input_binding_rejects_mismatch(self):
        changes = {
            "operation": "add",
            "upstream_version": "7.14.3",
            "expiry_days": 91,
            "operator_reason": "different",
            "workflow_base_sha": "f" * 40,
            "dispatch_sha": "e" * 40,
            "contract_family": "uncompiled-family",
            "output_paths": tuple(allowed_output_paths(1)[:-1]),
            "key_id": "different-key",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProtectedSignerFixture(Path(directory))
            for field, replacement in changes.items():
                with self.subTest(field=field):
                    values = dict(fixture.trusted.__dict__)
                    values[field] = replacement
                    with self.assertRaises(SigningCoreError):
                        prepare_signing(
                            inspection_directory=fixture.inspection,
                            prepared_directory=fixture.prepared,
                            repository=fixture.repository,
                            trusted=TrustedInputs(**values),
                            environment=fixture.public_environment,
                        )

    def test_prepared_bytes_cannot_change_before_seed_step(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProtectedSignerFixture(Path(directory))
            prepare_signing(
                inspection_directory=fixture.inspection,
                prepared_directory=fixture.prepared,
                repository=fixture.repository,
                trusted=fixture.trusted,
                environment=fixture.public_environment,
            )
            path = fixture.prepared / "canonical-registry.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["expires_at"] = "2026-07-21T00:00:00Z"
            path.write_bytes(canonical_json(value))
            with self.assertRaisesRegex(SigningCoreError, "prepared_trusted_input_mismatch"):
                sign_prepared(
                    prepared_directory=fixture.prepared,
                    signature_directory=fixture.signatures,
                    trusted=fixture.trusted,
                    environment=fixture.signing_environment,
                )

    def test_raw_evidence_is_recomputed_not_copied_from_a_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ProtectedSignerFixture(Path(directory))
            runtime_path = fixture.inspection / "runtime-evidence.json"
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            runtime["contract_fingerprints"]["runtime"] = "0" * 64
            runtime_path.write_bytes(canonical_file(runtime))
            manifest_path = fixture.inspection / "inspection-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["evidence_digests"]["runtime-evidence.json"] = sha256_digest(
                runtime_path.read_bytes()
            )
            manifest_path.write_bytes(canonical_file(manifest))
            with self.assertRaisesRegex(SigningCoreError, "runtime_contract_evidence_mismatch"):
                prepare_signing(
                    inspection_directory=fixture.inspection,
                    prepared_directory=fixture.prepared,
                    repository=fixture.repository,
                    trusted=fixture.trusted,
                    environment=fixture.public_environment,
                )


if __name__ == "__main__":
    unittest.main()
