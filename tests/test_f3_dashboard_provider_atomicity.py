"""Exact-release provider admission and mandatory atomicity-gate tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from f3_dashboard.atomicity import (  # noqa: E402
    assess_atomicity,
    require_executable_atomicity,
    simulate_non_atomic_interleaving,
)
from f3_dashboard.errors import AtomicityGateError, ProviderAdmissionError  # noqa: E402
from f3_dashboard.models import AtomicityStatus  # noqa: E402
from f3_dashboard.patch import compile_dashboard_patch  # noqa: E402
from f3_dashboard.provider import (  # noqa: E402
    COMMON_INPUT_SCHEMA_FINGERPRINT,
    EXACT_CONTRACTS,
    PROHIBITED_ARGUMENT_NAMES,
    admit_provider_contract,
    build_provider_projection,
    project_provider_response,
    require_executable_projection,
)
from f3_dashboard_support import load_dashboard  # noqa: E402


class DashboardProviderAdmissionTests(unittest.TestCase):
    def test_repo_captures_independently_verify_the_common_schema_fingerprint(self):
        fingerprints = []
        for version in ("7.14.2", "8.0.0"):
            path = (
                ROOT
                / "docs"
                / "evidence"
                / "upstream-read-compatibility"
                / f"ha-mcp-{version}.json"
            )
            capture = json.loads(path.read_text(encoding="utf-8"))
            tool = next(
                item for item in capture["tools"] if item["name"] == "ha_config_set_dashboard"
            )
            encoded = json.dumps(
                tool["inputSchema"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            fingerprints.append(hashlib.sha256(encoded).hexdigest())
        self.assertEqual(
            fingerprints,
            [COMMON_INPUT_SCHEMA_FINGERPRINT, COMMON_INPUT_SCHEMA_FINGERPRINT],
        )

    def test_exact_7142_and_800_contracts_are_planning_admitted_only(self):
        for version, evidence in EXACT_CONTRACTS.items():
            with self.subTest(version=version):
                admission = admit_provider_contract(evidence)
                self.assertTrue(admission.admitted_for_planning)
                self.assertFalse(admission.executable)
                self.assertEqual(admission.exact_release, version)

    def test_unknown_release_protocol_schema_security_output_and_runtime_fail_closed(self):
        exact = EXACT_CONTRACTS["8.0.0"]
        mutations = (
            replace(exact, upstream_version="8.0.1"),
            replace(exact, protocol_version="2026-01-01"),
            replace(exact, input_schema_fingerprint="0" * 64),
            replace(exact, annotation_fingerprint="0" * 64),
            replace(exact, output_contract_fingerprint="0" * 64),
            replace(exact, runtime_contract_fingerprint="0" * 64),
            replace(exact, policy_classification="read_only"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ProviderAdmissionError):
                admit_provider_contract(mutation)

    def test_projection_binds_result_without_a_mutating_argument_realization(self):
        config = load_dashboard()
        compilation = compile_dashboard_patch(
            config,
            [
                {
                    "operation_id": "rename",
                    "operation": "replace",
                    "path": "/title",
                    "value": "Projected",
                }
            ],
        )
        admission = admit_provider_contract(EXACT_CONTRACTS["8.0.0"])
        projection = build_provider_projection(
            admission=admission,
            compilation=compilation,
            url_path="synthetic-dashboard",
            current_config_hash=compilation.resulting_upstream_config_hash,
            atomicity=assess_atomicity("8.0.0"),
        )
        self.assertEqual(projection.target_url_path, "synthetic-dashboard")
        self.assertEqual(
            projection.resulting_configuration_sha256, compilation.resulting_sha256
        )
        self.assertEqual(
            projection.resulting_upstream_config_hash,
            compilation.resulting_upstream_config_hash,
        )
        self.assertFalse(projection.executable)
        self.assertIn(
            "no_reviewed_mutating_argument_realization", projection.blocked_reason
        )
        self.assertIn("BestPracticeKey", projection.potential_ephemeral_argument_names)
        self.assertEqual(projection.prohibited_argument_names, PROHIBITED_ARGUMENT_NAMES)
        self.assertEqual(
            set(PROHIBITED_ARGUMENT_NAMES),
            {
                "config",
                "python_transform",
                "title",
                "icon",
                "require_admin",
                "show_in_sidebar",
                "view_path",
                "return_screenshot",
                "resources",
                "preferences",
            },
        )
        with self.assertRaises(AtomicityGateError):
            require_executable_projection(projection)

    def test_arbitrary_arguments_creation_and_screenshot_routes_are_not_projected(self):
        source = Path(ROOT / "f3_dashboard" / "provider.py").read_text(encoding="utf-8")
        self.assertNotIn("def dispatch", source)
        self.assertNotIn("def create_dashboard", source)
        self.assertNotIn("def delete_dashboard", source)
        self.assertNotIn("**kwargs", source)
        self.assertNotIn('"python_transform": compilation', source)
        self.assertNotIn('"config": compilation', source)
        self.assertNotIn("return_screenshot\": True", source)

    def test_provider_response_is_bounded_evidence_not_verification(self):
        payload = {
            "success": True,
            "action": "python_transform",
            "url_path": "synthetic-dashboard",
            "write_committed": True,
            "post_write_verified": True,
            "config_hash": "a" * 16,
            "python_expression": "sensitive generated expression",
            "warnings": ["raw upstream content"],
        }
        evidence = project_provider_response(
            payload, expected_url_path="synthetic-dashboard"
        )
        self.assertTrue(evidence.success_claimed)
        self.assertIn("provider_response_is_not_verification", evidence.diagnostic_codes)
        self.assertFalse(hasattr(evidence, "python_expression"))
        for malformed in (
            None,
            {},
            {**payload, "url_path": "other-dashboard"},
            {**payload, "config_hash": "malformed"},
            {**payload, "post_write_verified": "yes"},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ProviderAdmissionError):
                project_provider_response(malformed, expected_url_path="synthetic-dashboard")


class DashboardAtomicityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = {"title": "approved preread"}
        self.approved = {"title": "approved result"}
        self.external = {"title": "external writer result"}

    def test_exact_reviewed_releases_have_no_selected_atomicity_mechanism(self):
        for version in ("7.14.2", "8.0.0"):
            with self.subTest(version=version):
                decision = assess_atomicity(version)
                self.assertEqual(decision.status, AtomicityStatus.BLOCKED)
                self.assertIsNone(decision.mechanism)
                self.assertIn(
                    "upstream_hash_check_and_save_are_separate_awaited_calls",
                    decision.reason_codes,
                )
                with self.assertRaises(AtomicityGateError):
                    require_executable_atomicity(decision)

    def test_unchanged_dashboard_models_save_without_fixture_mutation(self):
        result = simulate_non_atomic_interleaving(
            approved_preread=self.before,
            approved_result=self.approved,
            external_result=self.external,
            phase="unchanged",
        )
        self.assertTrue(result.modeled_setter_saved)
        self.assertEqual(result.setter_invocation_count, 0)
        self.assertEqual(result.fixture_mutation_count, 0)
        self.assertFalse(result.external_write_overwritten)
        self.assertEqual(result.final_configuration, self.approved)

    def test_external_writer_before_preflight_or_hash_check_is_rejected(self):
        for phase in ("before_preflight", "after_preflight_before_hash_check"):
            with self.subTest(phase=phase):
                result = simulate_non_atomic_interleaving(
                    approved_preread=self.before,
                    approved_result=self.approved,
                    external_result=self.external,
                    phase=phase,
                )
                self.assertTrue(result.conflict_rejected_before_save)
                self.assertFalse(result.modeled_setter_saved)
                self.assertEqual(result.setter_invocation_count, 0)
                self.assertEqual(result.fixture_mutation_count, 0)

    def test_external_writer_in_hash_check_save_gap_is_overwritten_undetectably(self):
        fixture_snapshots = tuple(
            deepcopy(item) for item in (self.before, self.approved, self.external)
        )
        result = simulate_non_atomic_interleaving(
            approved_preread=self.before,
            approved_result=self.approved,
            external_result=self.external,
            phase="during_hash_check_save_gap",
        )
        self.assertTrue(result.modeled_setter_saved)
        self.assertEqual(result.setter_invocation_count, 0)
        self.assertEqual(result.fixture_mutation_count, 0)
        self.assertTrue(result.external_write_overwritten)
        self.assertFalse(result.readback_detects_overwrite)
        self.assertEqual(result.final_configuration, self.approved)
        self.assertEqual(
            (self.before, self.approved, self.external), fixture_snapshots
        )

    def test_external_writer_immediately_after_save_yields_a_mismatch_not_lost_update_proof(self):
        result = simulate_non_atomic_interleaving(
            approved_preread=self.before,
            approved_result=self.approved,
            external_result=self.external,
            phase="immediately_after_save",
        )
        self.assertTrue(result.modeled_setter_saved)
        self.assertEqual(result.setter_invocation_count, 0)
        self.assertEqual(result.fixture_mutation_count, 0)
        self.assertFalse(result.external_write_overwritten)
        self.assertTrue(result.readback_detects_overwrite)
        self.assertEqual(result.final_configuration, self.external)

    def test_ui_equivalent_writer_and_competing_non_engineering_client_are_same_hard_blocker(self):
        for writer in ("home-assistant-ui", "other-integration", "other-client"):
            with self.subTest(writer=writer):
                external = {"writer": writer, "title": "external"}
                result = simulate_non_atomic_interleaving(
                    approved_preread=self.before,
                    approved_result=self.approved,
                    external_result=external,
                    phase="during_hash_check_save_gap",
                )
                self.assertTrue(result.external_write_overwritten)
                self.assertFalse(result.conflict_rejected_before_save)
                self.assertEqual(result.setter_invocation_count, 0)
                self.assertEqual(result.fixture_mutation_count, 0)

    def test_strict_bps_receipts_are_not_modeled_as_cas_or_writer_exclusion(self):
        decision = assess_atomicity("8.0.0")
        self.assertNotIn("strict_bps", decision.reason_codes)
        self.assertEqual(decision.status, AtomicityStatus.BLOCKED)
        self.assertIsNone(decision.mechanism)


if __name__ == "__main__":
    unittest.main()
