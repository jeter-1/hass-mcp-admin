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
        """Return only fixed categories and bounded numeric observations."""

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


class ProviderAuthorityUnavailableError(RawEvidenceError):
    """Exact dashboard write authority was not admitted for the observed catalog."""


class PatchValidationError(DashboardFoundationError):
    code = "invalid_dashboard_patch"


class PatchCompilationError(DashboardFoundationError):
    code = "dashboard_patch_compilation_failed"


_JSON_BOUNDS = frozenset({"nodes", "depth"})


def json_bound_error(message: str, *, bound: str, limit: int) -> PatchValidationError:
    """Return the shared codec's node or depth refusal with an attributable bound.

    The exception is a plain ``PatchValidationError`` whose class, code,
    message and ``diagnostic_details`` are exactly the legacy ones (class
    names are persisted as failure categories elsewhere).  No constraint is
    set because the shared codec does not know which input it is walking;
    the patch compiler reads ``json_bound`` to attribute the exceeded limit
    to the value, patch or result it was validating, without matching text.
    """

    if bound not in _JSON_BOUNDS:
        raise ValueError("Dashboard JSON bound is not reviewed")
    error = PatchValidationError(message)
    error.json_bound = (bound, DashboardFoundationError._count(limit, "limit"))
    return error


def json_bound_of(exc: BaseException) -> tuple[str, int] | None:
    """Return ``(bound, limit)`` for a codec node or depth refusal, else ``None``."""

    if not isinstance(exc, PatchValidationError):
        return None
    bound = getattr(exc, "json_bound", None)
    if (
        isinstance(bound, tuple)
        and len(bound) == 2
        and bound[0] in _JSON_BOUNDS
        and isinstance(bound[1], int)
        and not isinstance(bound[1], bool)
        and bound[1] >= 0
    ):
        return bound
    return None


# HAMCP-143: every reviewed dashboard compiler limit, paired with the stage
# that enforces it.  Only a patch validation or compilation failure carrying
# one of these exact pairs and a numeric limit may expose its bounded
# diagnostic details publicly; every other failure keeps ``{"reason": code}``.
COMPILER_LIMIT_DIAGNOSTICS = frozenset(
    {
        ("operation_count", "validation"),
        ("pointer_length", "validation"),
        ("pointer_depth", "validation"),
        ("value_json_nodes", "validation"),
        ("value_json_depth", "validation"),
        ("operation_value_bytes", "validation"),
        ("patch_json_nodes", "validation"),
        ("patch_json_depth", "validation"),
        ("patch_bytes", "validation"),
        ("semantic_leaf_changes", "compilation"),
        ("traversal_nodes", "compilation"),
        ("traversal_depth", "compilation"),
        ("result_json_nodes", "compilation"),
        ("result_json_depth", "compilation"),
        ("result_config_bytes", "compilation"),
        ("config_growth_bytes", "compilation"),
    }
)


def is_compiler_limit_refusal(exc: BaseException) -> bool:
    """Return whether ``exc`` is a reviewed compiler-limit refusal."""

    return (
        isinstance(exc, (PatchValidationError, PatchCompilationError))
        and (exc.constraint, exc.stage) in COMPILER_LIMIT_DIAGNOSTICS
        and exc.limit is not None
    )


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
