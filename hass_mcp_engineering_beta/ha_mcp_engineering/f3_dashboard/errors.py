"""Typed, bounded failures for governed dashboard planning and execution."""

from __future__ import annotations

import re


_DIAGNOSTIC_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DashboardFoundationError(ValueError):
    """Base class for deterministic F3-B validation failures."""

    code = "dashboard_foundation_error"

    def __init__(
        self,
        message: str = "Dashboard operation was rejected",
        *,
        reason: str | None = None,
        constraint: str | None = None,
        observed: int | None = None,
        limit: int | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = self._token(reason or self.code, "reason")
        self.constraint = self._optional_token(constraint, "constraint")
        self.stage = self._optional_token(stage, "stage")
        self.observed = self._count(observed, "observed")
        self.limit = self._count(limit, "limit")

    @staticmethod
    def _token(value: str, field: str) -> str:
        if not isinstance(value, str) or not _DIAGNOSTIC_TOKEN.fullmatch(value):
            raise ValueError(f"Dashboard diagnostic {field} is not canonical")
        return value

    @classmethod
    def _optional_token(cls, value: str | None, field: str) -> str | None:
        return None if value is None else cls._token(value, field)

    @staticmethod
    def _count(value: int | None, field: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Dashboard diagnostic {field} is not bounded")
        return value

    def diagnostic_details(self) -> dict[str, str | int]:
        """Return only stable categories and bounded numeric observations."""

        details: dict[str, str | int] = {
            "reason": self.reason,
            "dashboard_error_code": self.code,
        }
        if self.constraint is not None:
            details["constraint"] = self.constraint
        if self.observed is not None:
            details["observed"] = self.observed
        if self.limit is not None:
            details["limit"] = self.limit
        if self.stage is not None:
            details["stage"] = self.stage
        return details


class RawEvidenceError(DashboardFoundationError):
    code = "invalid_raw_dashboard_evidence"


class PatchValidationError(DashboardFoundationError):
    code = "invalid_dashboard_patch"


class PatchCompilationError(DashboardFoundationError):
    code = "dashboard_patch_compilation_failed"


class SemanticDiffError(DashboardFoundationError):
    code = "dashboard_semantic_diff_failed"


class ApprovalProjectionError(DashboardFoundationError):
    code = "dashboard_approval_projection_failed"


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
