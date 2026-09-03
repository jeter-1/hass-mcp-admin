"""Production-owned capability-scoped automatic readmission primitives.

Runtime integration is intentionally limited to the read-only ha-mcp gateway.
The generic data model remains transport-free so the merged ADR-020 vectors can
exercise the exact production coordinator without importing test support.
"""

from .coordinator import (
    CapabilityAdmissionCoordinator,
    ReconciliationAttempt,
)
from .models import (
    AdmissionDisposition,
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityContract,
    CapabilityDecision,
    CapabilityKind,
    CapabilityProfile,
    CompatibilityModelError,
    CompatibilityObservation,
    DecisionGeneration,
    DispatchCommit,
    ObservedCapability,
    ReconciliationResult,
    RegistryRefreshResult,
    RouteLease,
    UpstreamSurface,
    canonical_json,
    classify_registry_refresh,
    evidence_fingerprint,
)

__all__ = [
    "AdmissionDisposition",
    "AuthorityBundle",
    "AuthorityDecision",
    "AuthoritySource",
    "AuthorityStatus",
    "CapabilityAdmissionCoordinator",
    "CapabilityContract",
    "CapabilityDecision",
    "CapabilityKind",
    "CapabilityProfile",
    "CompatibilityModelError",
    "CompatibilityObservation",
    "DecisionGeneration",
    "DispatchCommit",
    "ObservedCapability",
    "ReconciliationAttempt",
    "ReconciliationResult",
    "RegistryRefreshResult",
    "RouteLease",
    "UpstreamSurface",
    "canonical_json",
    "classify_registry_refresh",
    "evidence_fingerprint",
]
