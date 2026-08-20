"""Inert F3-B dashboard-write planning and verification foundation.

This package is deliberately outside the Engineering add-on package.  No
runtime module imports it, it registers no MCP tool, and its provider module
contains no transport or mutating dispatch method.
"""

from .artifact_store import DashboardArtifactStore
from .approval_projection import (
    APPROVAL_PROJECTION_MODEL,
    build_dashboard_approval_projection,
    validate_dashboard_approval_projection,
)
from .atomicity import assess_atomicity, simulate_non_atomic_interleaving
from .constants import (
    ARTIFACT_SCHEMA,
    PATCH_MODEL,
    PROPOSAL_MODEL,
    RAW_EVIDENCE_MODEL,
    RISK_MODEL,
    SEMANTIC_DIFF_MODEL,
    VERIFICATION_MODEL,
)
from .patch import canonicalize_patch, compile_dashboard_patch, parse_pointer
from .planning import (
    create_dashboard_update_plan,
    create_dashboard_update_plan_projection,
)
from .provider import EXACT_CONTRACTS, admit_provider_contract
from .raw_evidence import build_raw_dashboard_evidence
from .risk import analyze_dashboard_risk
from .semantic_diff import build_semantic_diff
from .verification import assess_dashboard_preflight, verify_dashboard_observation

__all__ = [
    "ARTIFACT_SCHEMA",
    "APPROVAL_PROJECTION_MODEL",
    "DashboardArtifactStore",
    "EXACT_CONTRACTS",
    "PATCH_MODEL",
    "PROPOSAL_MODEL",
    "RAW_EVIDENCE_MODEL",
    "RISK_MODEL",
    "SEMANTIC_DIFF_MODEL",
    "VERIFICATION_MODEL",
    "admit_provider_contract",
    "analyze_dashboard_risk",
    "assess_atomicity",
    "assess_dashboard_preflight",
    "build_raw_dashboard_evidence",
    "build_dashboard_approval_projection",
    "build_semantic_diff",
    "canonicalize_patch",
    "compile_dashboard_patch",
    "create_dashboard_update_plan",
    "create_dashboard_update_plan_projection",
    "parse_pointer",
    "simulate_non_atomic_interleaving",
    "verify_dashboard_observation",
    "validate_dashboard_approval_projection",
]
