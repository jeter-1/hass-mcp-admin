import ast
import builtins
from dataclasses import replace
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from update_recovery_foundation import (  # noqa: E402
    BackupLocationStatus,
    BackupStatus,
    CompatibilityStatus,
    DEFAULT_UPDATE_RECOVERY_POLICY,
    EvidenceReference,
    ExpectedDisruption,
    IssueSeverity,
    PostUpdateVerificationProfile,
    PreflightVerdict,
    TargetType,
    UpdatePreflightEvidence,
    UpdateRiskIssue,
    evaluate_update_preflight,
)
from update_recovery_foundation import preflight as preflight_module  # noqa: E402


PROFILE_BY_TARGET = {
    TargetType.HOME_ASSISTANT_CORE: PostUpdateVerificationProfile.HOME_ASSISTANT_CORE,
    TargetType.SUPERVISOR: PostUpdateVerificationProfile.SUPERVISOR,
    TargetType.HOME_ASSISTANT_OS: PostUpdateVerificationProfile.HOME_ASSISTANT_OS,
    TargetType.ADDON_APP: PostUpdateVerificationProfile.ADDON_APP,
    TargetType.HACS_INTEGRATION: PostUpdateVerificationProfile.HACS,
    TargetType.HACS_FRONTEND_COMPONENT: PostUpdateVerificationProfile.HACS,
    TargetType.ENGINEERING_MCP_SERVER: PostUpdateVerificationProfile.ENGINEERING_MCP_SERVER,
    TargetType.UPSTREAM_HA_MCP: PostUpdateVerificationProfile.UPSTREAM_HA_MCP,
    TargetType.FIRMWARE_UPDATE_ENTITY: PostUpdateVerificationProfile.FIRMWARE,
}


def reference(evidence_id, *, authoritative=True):
    return EvidenceReference(
        evidence_id=evidence_id,
        source="synthetic_test_fixture",
        summary=f"Synthetic evidence {evidence_id}.",
        authoritative=authoritative,
    )


def complete_evidence(
    target_type=TargetType.HOME_ASSISTANT_CORE,
    **changes,
):
    values = {
        "target_type": target_type,
        "target_id": "synthetic-target",
        "installed_version": "2026.7.0",
        "candidate_version": "2026.7.1",
        "candidate_version_evidence": reference("candidate-version"),
        "compatibility_status": CompatibilityStatus.COMPATIBLE,
        "compatibility_evidence": (reference("compatibility"),),
        "current_repairs": (),
        "current_errors": (),
        "backup_status": BackupStatus.CURRENT,
        "backup_age_hours": 2,
        "backup_location_status": BackupLocationStatus.VERIFIED,
        "free_storage": 2_048,
        "required_storage": 512,
        "power_stability_known": True,
        "power_stable": True,
        "rollback_available": False,
        "restore_available": True,
        "expected_disruption": ExpectedDisruption.RESTART,
        "post_update_verification_profile": PROFILE_BY_TARGET[target_type],
    }
    values.update(changes)
    return UpdatePreflightEvidence(**values)


def codes(findings):
    return {item.code for item in findings}


class UpdateRecoveryPreflightTests(unittest.TestCase):
    def test_policy_covers_every_required_future_target_class(self):
        self.assertEqual(
            {item.target_type for item in DEFAULT_UPDATE_RECOVERY_POLICY.target_policies},
            set(TargetType),
        )
        self.assertEqual(
            {item.value for item in TargetType},
            {
                "home_assistant_core",
                "supervisor",
                "home_assistant_os",
                "addon_app",
                "hacs_integration",
                "hacs_frontend_component",
                "engineering_mcp_server",
                "upstream_ha_mcp",
                "firmware_update_entity",
            },
        )

    def test_complete_compatible_evidence_reaches_planning_readiness(self):
        result = evaluate_update_preflight(complete_evidence())
        self.assertEqual(
            result.verdict,
            PreflightVerdict.READY_FOR_GOVERNED_PLANNING,
        )
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.unknowns, ())
        self.assertIn("rollback_unavailable", codes(result.warnings))

    def test_missing_required_backup_blocks(self):
        result = evaluate_update_preflight(
            complete_evidence(
                backup_status=BackupStatus.MISSING,
                backup_age_hours=None,
                backup_location_status=BackupLocationStatus.UNKNOWN,
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn("required_backup_missing", codes(result.blockers))

    def test_candidate_version_requires_authoritative_evidence(self):
        result = evaluate_update_preflight(
            complete_evidence(
                candidate_version_evidence=reference(
                    "candidate-observation",
                    authoritative=False,
                )
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn(
            "candidate_version_not_authoritative",
            codes(result.blockers),
        )

    def test_stale_core_backup_blocks_under_explicit_policy(self):
        result = evaluate_update_preflight(
            complete_evidence(backup_age_hours=25)
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn("required_backup_stale", codes(result.blockers))

    def test_explicit_stale_backup_status_blocks_even_with_recent_age(self):
        result = evaluate_update_preflight(
            complete_evidence(
                backup_status=BackupStatus.STALE,
                backup_age_hours=2,
            )
        )
        stale = next(
            item
            for item in result.blockers
            if item.code == "required_backup_stale"
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertEqual(stale.summary, "The backup is marked stale.")

    def test_stale_hacs_backup_requires_manual_review_under_explicit_policy(self):
        result = evaluate_update_preflight(
            complete_evidence(
                TargetType.HACS_INTEGRATION,
                backup_age_hours=73,
            )
        )
        self.assertEqual(
            result.verdict,
            PreflightVerdict.MANUAL_REVIEW_REQUIRED,
        )
        self.assertIn(
            "stale_backup_requires_manual_review",
            codes(result.warnings),
        )
        self.assertEqual(result.blockers, ())

    def test_insufficient_storage_blocks(self):
        result = evaluate_update_preflight(
            complete_evidence(free_storage=511, required_storage=512)
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn("insufficient_storage", codes(result.blockers))

    def test_incompatibility_blocks(self):
        result = evaluate_update_preflight(
            complete_evidence(
                compatibility_status=CompatibilityStatus.INCOMPATIBLE
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn("target_incompatible", codes(result.blockers))

    def test_unknown_compatibility_requires_manual_review(self):
        result = evaluate_update_preflight(
            complete_evidence(
                compatibility_status=CompatibilityStatus.UNKNOWN
            )
        )
        self.assertEqual(
            result.verdict,
            PreflightVerdict.MANUAL_REVIEW_REQUIRED,
        )
        self.assertIn("compatibility_unknown", codes(result.unknowns))

    def test_unavailable_compatibility_evidence_blocks(self):
        result = evaluate_update_preflight(
            complete_evidence(
                compatibility_status=CompatibilityStatus.UNAVAILABLE,
                compatibility_evidence=(),
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn(
            "compatibility_evidence_unavailable",
            codes(result.blockers),
        )

    def test_compatible_status_without_authoritative_evidence_blocks(self):
        result = evaluate_update_preflight(
            complete_evidence(
                compatibility_evidence=(
                    reference("compatibility-observation", authoritative=False),
                )
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn(
            "authoritative_compatibility_evidence_missing",
            codes(result.blockers),
        )

    def test_existing_critical_repairs_and_errors_are_not_ignored(self):
        result = evaluate_update_preflight(
            complete_evidence(
                current_repairs=(
                    UpdateRiskIssue(
                        "repair-critical",
                        IssueSeverity.CRITICAL,
                        "Synthetic critical repair remains open.",
                    ),
                ),
                current_errors=(
                    UpdateRiskIssue(
                        "error-critical",
                        IssueSeverity.CRITICAL,
                        "Synthetic critical error remains open.",
                    ),
                ),
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertEqual(
            {
                "critical_repair_unresolved",
                "critical_error_unresolved",
            },
            codes(result.blockers)
            & {
                "critical_repair_unresolved",
                "critical_error_unresolved",
            },
        )

    def test_unavailable_rollback_and_restore_are_surfaced(self):
        result = evaluate_update_preflight(
            complete_evidence(
                rollback_available=False,
                restore_available=False,
            )
        )
        self.assertEqual(result.verdict, PreflightVerdict.BLOCKED)
        self.assertIn("viable_recovery_path_missing", codes(result.blockers))
        self.assertIn("rollback_unavailable", codes(result.warnings))
        self.assertIn("restore_unavailable", codes(result.warnings))

    def test_unsupported_target_class_is_explicit(self):
        value = replace(complete_evidence(), target_type="future_unknown_target")
        result = evaluate_update_preflight(value)
        self.assertEqual(result.verdict, PreflightVerdict.UNSUPPORTED)
        self.assertEqual(codes(result.blockers), {"unsupported_target_class"})

    def test_evaluator_has_no_provider_dependency_or_provider_call_path(self):
        tree = ast.parse(inspect.getsource(preflight_module))
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(
            any("provider" in module for module in imported_modules)
        )
        self.assertEqual(
            tuple(inspect.signature(evaluate_update_preflight).parameters),
            ("evidence", "policy"),
        )

        original_import = builtins.__import__

        def reject_provider_import(name, *args, **kwargs):
            if "provider" in name:
                raise AssertionError(f"provider import attempted: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_provider_import):
            result = evaluate_update_preflight(complete_evidence())
        self.assertEqual(
            result.verdict,
            PreflightVerdict.READY_FOR_GOVERNED_PLANNING,
        )

    def test_results_are_deterministic_across_input_order(self):
        issues = (
            UpdateRiskIssue(
                "repair-z",
                IssueSeverity.HIGH,
                "Synthetic high repair.",
            ),
            UpdateRiskIssue(
                "repair-a",
                IssueSeverity.CRITICAL,
                "Synthetic critical repair.",
            ),
        )
        compatibility = (
            reference("compatibility-z"),
            reference("compatibility-a"),
        )
        first = evaluate_update_preflight(
            complete_evidence(
                current_repairs=issues,
                compatibility_evidence=compatibility,
            )
        )
        second = evaluate_update_preflight(
            complete_evidence(
                current_repairs=tuple(reversed(issues)),
                compatibility_evidence=tuple(reversed(compatibility)),
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(
            len(first.assessment_fingerprint),
            64,
        )

    def test_invalid_numeric_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "free_storage"):
            complete_evidence(free_storage=-1)


if __name__ == "__main__":
    unittest.main()
