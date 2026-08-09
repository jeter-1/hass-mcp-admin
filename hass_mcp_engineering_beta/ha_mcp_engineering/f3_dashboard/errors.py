"""Typed, bounded failures for governed dashboard planning and execution."""

from __future__ import annotations


class DashboardFoundationError(ValueError):
    """Base class for deterministic F3-B validation failures."""

    code = "dashboard_foundation_error"

    def __init__(self, message: str = "Dashboard operation was rejected") -> None:
        super().__init__(message)


class RawEvidenceError(DashboardFoundationError):
    code = "invalid_raw_dashboard_evidence"


class PatchValidationError(DashboardFoundationError):
    code = "invalid_dashboard_patch"


class PatchCompilationError(DashboardFoundationError):
    code = "dashboard_patch_compilation_failed"


class SemanticDiffError(DashboardFoundationError):
    code = "dashboard_semantic_diff_failed"


class RiskAnalysisError(DashboardFoundationError):
    code = "dashboard_risk_analysis_failed"


class ProviderAdmissionError(DashboardFoundationError):
    code = "dashboard_write_provider_not_admitted"


class KnownUpstreamCompatibilityError(ProviderAdmissionError):
    code = "dashboard_write_existing_hyphenless_path_incompatible"


class AtomicityGateError(DashboardFoundationError):
    code = "dashboard_write_atomicity_unproven"


class ArtifactStorageError(DashboardFoundationError):
    code = "dashboard_artifact_storage_error"


class PlanningError(DashboardFoundationError):
    code = "dashboard_update_planning_failed"


class VerificationError(DashboardFoundationError):
    code = "dashboard_update_verification_failed"
