import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_contracts import canonical_json  # noqa: E402
from scripts.manage_upstream_trust_registry import (  # noqa: E402
    RegistryOperationError,
    RegistrySetWriteError,
    _canonical_file,
    check_origin_main_freshness,
    lifecycle_evidence_path,
    mutate_registry,
    prepare_mutation_candidate,
    public_material,
    sign_candidate_directory,
    validate_main_freshness_values,
    verify_committed_registry,
    write_unsigned_candidate,
)
from scripts.prepare_upstream_registry_publication import (  # noqa: E402
    construct_verified_publication_commit,
)
from scripts.verify_upstream_registry_signing_wheels import (  # noqa: E402
    EXPECTED_WHEELS,
    LOCK_PACKAGES,
    WheelhouseVerificationError,
    verify_wheelhouse,
)
from tests.test_rc2dev11_registry_operations import RegistryFixture  # noqa: E402


WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "prepare-upstream-compatibility-attestation.yml"
)
LOCK_PATH = ROOT / "scripts" / "requirements-upstream-registry-signing.lock"
FIXED_NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)
VERIFY_NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def managed_snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for parent in (directory / "upstream-trust", directory / "docs")
        if parent.exists()
        for path in parent.rglob("*")
        if path.is_file()
    }


def public_environment(fixture: RegistryFixture) -> dict[str, str]:
    return {
        "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": fixture.environment[
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"
        ],
        "UPSTREAM_TRUST_REGISTRY_KEY_ID": fixture.environment[
            "UPSTREAM_TRUST_REGISTRY_KEY_ID"
        ],
    }


def resign_evidence(path: Path, fixture: RegistryFixture, document: dict) -> None:
    document["signature"]["signature"] = base64.b64encode(
        fixture.private.sign(canonical_json(document["payload"]))
    ).decode("ascii")
    path.write_bytes(_canonical_file(document))


class WorkflowSecurityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.jobs = cls.workflow["jobs"]

    def test_inspection_has_read_only_non_signing_boundary(self):
        job = self.jobs["inspection"]
        serialized = json.dumps(job)
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", serialized)
        self.assertNotIn("git push", serialized)
        self.assertNotIn("gh pr create", serialized)
        checkout = next(step for step in job["steps"] if "actions/checkout@" in step.get("uses", ""))
        self.assertFalse(checkout["with"]["persist-credentials"])

    def test_signing_has_no_write_authority_and_private_seed_only_once(self):
        job = self.jobs["signing"]
        self.assertEqual(job["environment"], "upstream-attestation-signing")
        self.assertEqual(job["permissions"], {"contents": "read"})
        serialized = json.dumps(job)
        self.assertNotIn("git push", serialized)
        self.assertNotIn("gh pr create", serialized)
        self.assertNotIn("docker push", serialized)
        self.assertNotIn("gh release", serialized)
        self.assertNotIn("git tag", serialized)
        self.assertEqual(serialized.count("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"), 2)
        signing_steps = [
            step
            for step in job["steps"]
            if "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY" in json.dumps(step)
        ]
        self.assertEqual(len(signing_steps), 1)
        install = next(
            step
            for step in job["steps"]
            if step.get("name") == "Verify and install the reviewed signing closure offline"
        )["run"]
        self.assertIn("--require-hashes", install)
        self.assertIn("--no-index", install)
        self.assertNotIn("https://", install)
        checkout = next(step for step in job["steps"] if "actions/checkout@" in step.get("uses", ""))
        self.assertFalse(checkout["with"]["persist-credentials"])

    def test_publication_is_only_writer_and_has_no_private_seed(self):
        writers = []
        for name, job in self.jobs.items():
            permissions = job.get("permissions", {})
            if "write" in permissions.values():
                writers.append(name)
        self.assertEqual(writers, ["publication"])
        publication = json.dumps(self.jobs["publication"])
        self.assertNotIn("UPSTREAM_TRUST_REGISTRY_SIGNING_KEY", publication)
        self.assertIn("prepare_upstream_registry_publication.py", publication)
        self.assertIn("prepare_upstream_registry_publication.py", publication)
        self.assertIn("gh pr create --draft", publication)
        for forbidden in ("gh release", "git tag", "docker push", "build-push-action"):
            self.assertNotIn(forbidden, publication)

    def test_workflow_contract_is_main_only_concurrent_and_data_only(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        self.assertEqual(
            self.workflow["concurrency"],
            {"group": "upstream-compatibility-attestation", "cancel-in-progress": False},
        )
        for job in self.jobs.values():
            self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("len(changed_paths) != 4", (
            ROOT / "scripts" / "prepare_upstream_registry_publication.py"
        ).read_text(encoding="utf-8"))
        ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
        self.assertNotIn("upstream-attestation-signing", json.dumps(ci))

    def test_complete_hash_lock_includes_every_transitive_distribution(self):
        lock = LOCK_PATH.read_text(encoding="utf-8")
        self.assertEqual(set(LOCK_PACKAGES), {"cryptography", "cffi", "pycparser"})
        for package, version in LOCK_PACKAGES.items():
            self.assertIn(f"{package}=={version}", lock)
        self.assertEqual(lock.count("--hash=sha256:"), 3)


class WheelhouseIntegrityTests(unittest.TestCase):
    def _fixture(self, directory: Path):
        contents = {"a.whl": b"a", "b.whl": b"b", "c.whl": b"c"}
        expected = {}
        for name, content in contents.items():
            (directory / name).write_bytes(content)
            expected[name] = hashlib.sha256(content).hexdigest()
        lock = directory / "lock.txt"
        lines = []
        for (package, version), digest in zip(LOCK_PACKAGES.items(), expected.values()):
            lines.append(f"{package}=={version} --hash=sha256:{digest}")
        lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return expected, lock

    def test_missing_or_altered_wheel_fails_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            expected, generated_lock = self._fixture(wheelhouse)
            lock = root / "lock.txt"
            generated_lock.replace(lock)
            with patch.dict(EXPECTED_WHEELS, expected, clear=True):
                self.assertTrue(
                    verify_wheelhouse(wheelhouse, lock)["all_hashes_verified"]
                )
                (wheelhouse / "a.whl").unlink()
                with self.assertRaises(WheelhouseVerificationError):
                    verify_wheelhouse(wheelhouse, lock)
                (wheelhouse / "a.whl").write_bytes(b"changed")
                with self.assertRaises(WheelhouseVerificationError):
                    verify_wheelhouse(wheelhouse, lock)


class LifecycleEvidenceChainTests(unittest.TestCase):
    def _verify(self, fixture: RegistryFixture):
        return verify_committed_registry(
            paths=fixture.paths,
            environment=public_environment(fixture),
            now=VERIFY_NOW,
        )

    def _build(self, directory: str, operations: list[tuple[str, str | None, datetime]]):
        fixture = RegistryFixture(directory)
        sequence = 0
        for operation, version, now in operations:
            if operation in {"bootstrap", "add"}:
                assert version is not None
                fixture.evidence(version)
            fixture.mutate(
                operation,
                sequence,
                version,
                now=now,
                operator_reason=f"review-{sequence + 1}",
            )
            sequence += 1
        return fixture

    def test_valid_complete_chains_cover_every_operation_and_recovery(self):
        cases = (
            [("bootstrap", "7.14.2", FIXED_NOW)],
            [("bootstrap", "7.14.2", FIXED_NOW), ("add", "7.14.3", FIXED_NOW)],
            [("bootstrap", "7.14.2", FIXED_NOW), ("revoke", "7.14.2", FIXED_NOW)],
            [
                ("bootstrap", "7.14.2", FIXED_NOW),
                ("revoke", "7.14.2", FIXED_NOW),
                ("restore", "7.14.2", FIXED_NOW),
            ],
            [
                ("bootstrap", "7.14.2", FIXED_NOW),
                ("renew", None, FIXED_NOW + timedelta(days=1)),
                ("renew", None, FIXED_NOW + timedelta(days=2)),
            ],
        )
        for operations in cases:
            with self.subTest(operations=[item[0] for item in operations]):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._build(directory, list(operations))
                    result = self._verify(fixture)
                    self.assertEqual(result["sequence"], len(operations))
                    self.assertEqual(result["lifecycle_evidence_record_count"], len(operations))

    def test_every_security_field_is_cryptographically_bound(self):
        mutations = {
            "operation": lambda p: p.__setitem__("operation", "renew"),
            "entry_id": lambda p: p["affected_entry"].__setitem__("entry_id", "changed"),
            "upstream_version": lambda p: p["affected_entry"].__setitem__("upstream_version", "9.9.9"),
            "contract_family": lambda p: p["affected_entry"].__setitem__("contract_family", "changed"),
            "operator_reason": lambda p: p.__setitem__("operator_reason", "changed"),
            "old_revoked": lambda p: p.__setitem__("old_revoked", False),
            "new_revoked": lambda p: p.__setitem__("new_revoked", True),
            "workflow_base_sha": lambda p: p.__setitem__("workflow_base_sha", "f" * 40),
            "current_registry_digest": lambda p: p.__setitem__("current_registry_digest", "sha256:" + "f" * 64),
            "prior_registry_digest": lambda p: p.__setitem__("prior_registry_digest", "sha256:" + "e" * 64),
            "prior_evidence_digest": lambda p: p.__setitem__("prior_lifecycle_evidence_digest", "sha256:" + "d" * 64),
            "data_only": lambda p: p.__setitem__("data_only", False),
            "allowed_path_added": lambda p: p["allowed_output_paths"].append("unexpected"),
            "allowed_path_removed": lambda p: p["allowed_output_paths"].pop(),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._build(
                        directory, [("bootstrap", "7.14.2", FIXED_NOW)]
                    )
                    path = lifecycle_evidence_path(fixture.paths, 1)
                    document = json.loads(path.read_text())
                    mutation(document["payload"])
                    path.write_bytes(_canonical_file(document))
                    with self.assertRaises(RegistryOperationError):
                        self._verify(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._build(directory, [("bootstrap", "7.14.2", FIXED_NOW)])
            path = lifecycle_evidence_path(fixture.paths, 1)
            document = json.loads(path.read_text())
            document["signature"]["signature"] = base64.b64encode(b"0" * 64).decode()
            path.write_bytes(_canonical_file(document))
            with self.assertRaises(RegistryOperationError):
                self._verify(fixture)

    def test_historical_removal_replacement_reordering_skip_duplicate_and_extra_reject(self):
        scenarios = ("remove", "replace", "reorder", "skip", "duplicate", "extra", "pair")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = self._build(
                        directory,
                        [
                            ("bootstrap", "7.14.2", FIXED_NOW),
                            ("add", "7.14.3", FIXED_NOW),
                            ("revoke", "7.14.3", FIXED_NOW),
                        ],
                    )
                    one = lifecycle_evidence_path(fixture.paths, 1)
                    two = lifecycle_evidence_path(fixture.paths, 2)
                    three = lifecycle_evidence_path(fixture.paths, 3)
                    if scenario == "remove":
                        two.unlink()
                    elif scenario == "replace":
                        two.write_bytes(one.read_bytes())
                    elif scenario == "reorder":
                        first, second = one.read_bytes(), two.read_bytes()
                        one.write_bytes(second)
                        two.write_bytes(first)
                    elif scenario == "skip":
                        two.rename(fixture.paths.evidence_directory / "registry-sequence-000004.json")
                    elif scenario == "duplicate":
                        document = json.loads(two.read_text())
                        document["payload"]["new_sequence"] = 1
                        resign_evidence(two, fixture, document)
                    elif scenario == "extra":
                        (fixture.paths.evidence_directory / "registry-sequence-999999.json").write_bytes(
                            three.read_bytes()
                        )
                    else:
                        first = json.loads(one.read_text())
                        second = json.loads(two.read_text())
                        first["payload"]["current_registry"] = second["payload"]["current_registry"]
                        first["payload"]["current_registry_digest"] = second["payload"][
                            "current_registry_digest"
                        ]
                        resign_evidence(one, fixture, first)
                    with self.assertRaises(RegistryOperationError):
                        self._verify(fixture)

    def test_resigned_semantically_invalid_transition_still_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._build(directory, [("bootstrap", "7.14.2", FIXED_NOW)])
            path = lifecycle_evidence_path(fixture.paths, 1)
            document = json.loads(path.read_text())
            document["payload"]["operation"] = "renew"
            document["payload"]["release_evidence"] = None
            document["payload"]["release_evidence_digest"] = None
            resign_evidence(path, fixture, document)
            with self.assertRaisesRegex(RegistryOperationError, "semantics"):
                self._verify(fixture)


class MainFreshnessTests(unittest.TestCase):
    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _remote_fixture(self, directory: str) -> tuple[Path, str]:
        root = Path(directory)
        remote = root / "remote.git"
        repository = root / "repository"
        self._git(root, "init", "--bare", str(remote))
        repository.mkdir()
        self._git(repository, "init", "-b", "main")
        self._git(repository, "config", "user.name", "test")
        self._git(repository, "config", "user.email", "test@example.invalid")
        (repository / "README.md").write_text("base\n", encoding="utf-8")
        self._git(repository, "add", "README.md")
        self._git(repository, "commit", "-m", "base")
        self._git(repository, "remote", "add", "origin", str(remote))
        self._git(repository, "push", "-u", "origin", "main")
        return repository, self._git(repository, "rev-parse", "HEAD")

    def test_signing_and_publication_freshness_categories(self):
        base = "a" * 40
        validate_main_freshness_values(
            resolved_main_sha=base,
            workflow_base_sha=base,
            current_sequence=4,
            expected_current_sequence=4,
            phase="signing",
        )
        cases = (
            ({"resolved_main_sha": "b" * 40, "phase": "signing"}, "workflow_base_moved"),
            ({"current_sequence": 5, "phase": "signing"}, "workflow_sequence_stale"),
            ({"resolved_main_sha": "b" * 40, "phase": "publication"}, "publication_base_moved"),
            ({"phase": "publication", "signed_base_sha": "c" * 40}, "signed_base_mismatch"),
        )
        for overrides, category in cases:
            values = {
                "resolved_main_sha": base,
                "workflow_base_sha": base,
                "current_sequence": 4,
                "expected_current_sequence": 4,
                "phase": "signing",
            }
            values.update(overrides)
            with self.subTest(category=category):
                with self.assertRaises(RegistryOperationError) as context:
                    validate_main_freshness_values(**values)
                self.assertEqual(context.exception.category, category)

    def test_main_move_rejects_even_when_registry_text_is_unchanged(self):
        with self.assertRaises(RegistryOperationError) as context:
            validate_main_freshness_values(
                resolved_main_sha="b" * 40,
                workflow_base_sha="a" * 40,
                current_sequence=7,
                expected_current_sequence=7,
                phase="publication",
                signed_base_sha="a" * 40,
            )
        self.assertEqual(context.exception.category, "publication_base_moved")
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("check_upstream_registry_main_freshness.py"), 3)
        self.assertNotIn("git rebase", workflow)
        jobs = yaml.safe_load(workflow)["jobs"]
        protected_boundaries = json.dumps(
            {name: jobs[name] for name in ("signing", "publication")}
        )
        self.assertNotIn("git push --force", protected_boundaries)
        self.assertNotIn("git rebase", protected_boundaries)

    def test_actual_origin_main_check_accepts_exact_dispatch_base(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, base = self._remote_fixture(directory)
            before = self._git(repository, "status", "--porcelain=v1")
            result = check_origin_main_freshness(
                repository=repository,
                workflow_base_sha=base,
                expected_current_sequence=0,
                phase="signing",
                environment={},
            )
            self.assertTrue(result["fresh"])
            self.assertEqual(result["origin_main_sha"], base)
            self.assertEqual(before, self._git(repository, "status", "--porcelain=v1"))

    def test_actual_origin_main_move_aborts_without_regeneration_or_ref_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, base = self._remote_fixture(directory)
            (repository / "README.md").write_text("base\nunrelated main move\n", encoding="utf-8")
            self._git(repository, "add", "README.md")
            self._git(repository, "commit", "-m", "advance main without registry change")
            self._git(repository, "push", "origin", "main")
            refs_before = self._git(repository, "ls-remote", "--heads", "origin")
            marker = repository / "signature-was-generated"
            for phase, category in (
                ("signing", "workflow_base_moved"),
                ("publication", "publication_base_moved"),
            ):
                with self.subTest(phase=phase):
                    with self.assertRaises(RegistryOperationError) as context:
                        check_origin_main_freshness(
                            repository=repository,
                            workflow_base_sha=base,
                            expected_current_sequence=0,
                            phase=phase,
                            environment={},
                            signed_base_sha=base if phase == "publication" else None,
                        )
                    self.assertEqual(context.exception.category, category)
            self.assertFalse(marker.exists())
            self.assertEqual(refs_before, self._git(repository, "ls-remote", "--heads", "origin"))

    def test_actual_sequence_stale_aborts_before_signature_output(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, base = self._remote_fixture(directory)
            registry = repository / "upstream-trust" / "upstream-dashboard-registry.json"
            signature = repository / "upstream-trust" / "upstream-dashboard-registry.sig.json"
            registry.parent.mkdir(parents=True)
            registry.write_text("{}\n", encoding="utf-8")
            signature.write_text("{}\n", encoding="utf-8")
            self._git(repository, "add", "upstream-trust")
            self._git(repository, "commit", "-m", "introduce registry")
            self._git(repository, "push", "origin", "main")
            moved = self._git(repository, "rev-parse", "HEAD")
            with self.assertRaises(RegistryOperationError) as context:
                check_origin_main_freshness(
                    repository=repository,
                    workflow_base_sha=moved,
                    expected_current_sequence=0,
                    phase="signing",
                    environment={},
                )
            self.assertEqual(context.exception.category, "workflow_sequence_stale")
            self.assertFalse((repository / "signature-was-generated").exists())


class OutputReplacementRollbackTests(unittest.TestCase):
    def _renew_with_failure(self, failure_after: int):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        fixture = RegistryFixture(directory.name)
        fixture.evidence("7.14.2")
        fixture.mutate("bootstrap", 0, "7.14.2")
        before = managed_snapshot(root)
        counter = {"value": 0}

        def replace_then_fail(source: Path, target: Path):
            os.replace(source, target)
            counter["value"] += 1
            if counter["value"] == failure_after:
                raise OSError("synthetic replacement failure")

        with self.assertRaises(RegistrySetWriteError) as context:
            fixture.mutate(
                "renew",
                1,
                replace_func=replace_then_fail,
                now=FIXED_NOW + timedelta(days=1),
            )
        return fixture, before, managed_snapshot(root), context.exception

    def test_failure_after_every_replacement_restores_original_set(self):
        for number in range(1, 5):
            with self.subTest(replacement=number):
                fixture, before, after, error = self._renew_with_failure(number)
                self.assertEqual(before, after)
                self.assertEqual(error.category, "replacement_failed")
                self.assertEqual(error.rollback_status, "rollback_succeeded")
                self.assertNotIn(
                    fixture.environment["UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"], str(error)
                )

    def test_bootstrap_failure_removes_originally_missing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            counter = {"value": 0}

            def fail_after_second(source: Path, target: Path):
                os.replace(source, target)
                counter["value"] += 1
                if counter["value"] == 2:
                    raise OSError("synthetic")

            with self.assertRaises(RegistrySetWriteError):
                fixture.mutate("bootstrap", 0, "7.14.2", replace_func=fail_after_second)
            self.assertEqual(managed_snapshot(root), {})

    def test_staging_post_write_rollback_and_restoration_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            before = managed_snapshot(Path(directory))

            def staging_failure(**_kwargs):
                raise ValueError("synthetic")

            with self.assertRaises(RegistrySetWriteError) as context:
                fixture.mutate("renew", 1, verify_func=staging_failure)
            self.assertEqual(context.exception.category, "staging_failed")
            self.assertEqual(before, managed_snapshot(Path(directory)))

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            calls = {"value": 0}

            def fail_final(**kwargs):
                calls["value"] += 1
                if calls["value"] == 2:
                    raise ValueError("synthetic")
                return verify_committed_registry(**kwargs)

            with self.assertRaises(RegistrySetWriteError) as context:
                fixture.mutate("renew", 1, verify_func=fail_final)
            self.assertEqual(context.exception.category, "post_write_verification_failed")
            self.assertEqual(context.exception.rollback_status, "rollback_succeeded")

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")

            def replace_failure(source: Path, target: Path):
                os.replace(source, target)
                raise OSError("synthetic")

            def rollback_failure(_source: Path, _target: Path):
                raise OSError("synthetic rollback")

            with self.assertRaises(RegistrySetWriteError) as context:
                fixture.mutate(
                    "renew",
                    1,
                    replace_func=replace_failure,
                    rollback_replace_func=rollback_failure,
                )
            self.assertEqual(context.exception.category, "rollback_failed")

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            verify_calls = {"value": 0}

            def fail_after_replace(source: Path, target: Path):
                os.replace(source, target)
                raise OSError("synthetic")

            def fail_restored_verify(**kwargs):
                verify_calls["value"] += 1
                if verify_calls["value"] == 2:
                    raise ValueError("synthetic restored-state failure")
                return verify_committed_registry(**kwargs)

            with self.assertRaises(RegistrySetWriteError) as context:
                fixture.mutate(
                    "renew",
                    1,
                    replace_func=fail_after_replace,
                    verify_func=fail_restored_verify,
                )
            self.assertEqual(context.exception.category, "restored_state_verification_failed")

    def test_dry_run_never_writes_and_success_finishes_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            before = managed_snapshot(Path(directory))
            result = fixture.mutate("bootstrap", 0, "7.14.2", dry_run=True)
            self.assertFalse(result["written"])
            self.assertEqual(before, managed_snapshot(Path(directory)))
            fixture.mutate("bootstrap", 0, "7.14.2")
            self.assertTrue(self._public_verify(fixture)["lifecycle_evidence_chain_valid"])

    @staticmethod
    def _public_verify(fixture: RegistryFixture):
        return verify_committed_registry(
            paths=fixture.paths,
            environment=public_environment(fixture),
            now=VERIFY_NOW,
        )


class PublicationWorktreeTests(unittest.TestCase):
    def _git(self, repository: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _artifacts(self, root: Path):
        fixture = RegistryFixture(str(root))
        fixture.evidence("7.14.2")
        base_sha = self._git(root, "rev-parse", "HEAD")
        candidate = prepare_mutation_candidate(
            operation="bootstrap",
            upstream_version="7.14.2",
            expected_current_sequence=0,
            paths=fixture.paths,
            environment=fixture.environment,
            now=FIXED_NOW,
            workflow_base_sha=base_sha,
            dispatch_sha=base_sha,
        )
        artifacts = root.parent / "artifacts"
        write_unsigned_candidate(candidate, artifacts / "unsigned")
        sign_candidate_directory(
            artifacts / "unsigned",
            artifacts / "signed",
            paths=fixture.paths,
            environment=fixture.environment,
            now=FIXED_NOW,
        )
        return fixture, artifacts / "signed"

    def _repository(self, root: Path):
        root.mkdir()
        self._git(root, "init", "-b", "main")
        self._git(root, "config", "user.name", "test")
        self._git(root, "config", "user.email", "test@example.invalid")
        (root / "README.md").write_text("baseline\n")
        self._git(root, "add", "README.md")
        self._git(root, "commit", "-m", "baseline")
        self._git(root, "update-ref", "refs/remotes/origin/main", "HEAD")

    def test_clean_worktree_commit_contains_and_reverifies_complete_set(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repository = base / "repository"
            self._repository(repository)
            fixture, signed = self._artifacts(repository)
            worktree = base / "worktree"
            result = construct_verified_publication_commit(
                repository=repository,
                signed_artifacts=signed,
                worktree=worktree,
                branch="data/upstream-registry-bootstrap-1",
                environment=public_environment(fixture),
                now=VERIFY_NOW,
            )
            self.assertTrue(result["complete_set_verified"])
            self.assertEqual(len(result["changed_paths"]), 4)
            changed = self._git(
                worktree, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
            ).splitlines()
            self.assertEqual(sorted(changed), sorted(result["changed_paths"]))
            self._git(repository, "worktree", "remove", "--force", str(worktree))

    def test_unverified_unrelated_missing_and_partial_artifacts_are_refused(self):
        for scenario in (
            "unverified",
            "unrelated",
            "missing",
            "partial",
            "manifest_base",
            "summary_binding",
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    repository = base / "repository"
                    self._repository(repository)
                    fixture, signed = self._artifacts(repository)
                    manifest_path = signed / "publication-manifest.json"
                    manifest = json.loads(manifest_path.read_text())
                    if scenario == "unverified":
                        manifest["verified"] = False
                        manifest_path.write_bytes(_canonical_file(manifest))
                    elif scenario == "unrelated":
                        manifest["changed_paths"].append("README.md")
                        manifest_path.write_bytes(_canonical_file(manifest))
                    elif scenario == "missing":
                        (signed / "tree" / manifest["changed_paths"][0]).unlink()
                    elif scenario == "partial":
                        evidence = next(
                            item for item in manifest["changed_paths"] if "registry-sequence" in item
                        )
                        (signed / "tree" / evidence).unlink()
                    elif scenario == "manifest_base":
                        manifest["workflow_base_sha"] = "f" * 40
                        manifest_path.write_bytes(_canonical_file(manifest))
                    else:
                        summary_path = signed / "operation-summary.json"
                        summary = json.loads(summary_path.read_text())
                        summary["workflow_base_sha"] = "f" * 40
                        summary_path.write_bytes(_canonical_file(summary))
                    with self.assertRaises(ValueError):
                        construct_verified_publication_commit(
                            repository=repository,
                            signed_artifacts=signed,
                            worktree=base / "worktree",
                            branch="data/upstream-registry-bootstrap-2",
                            environment=public_environment(fixture),
                            now=VERIFY_NOW,
                        )


if __name__ == "__main__":
    unittest.main()
