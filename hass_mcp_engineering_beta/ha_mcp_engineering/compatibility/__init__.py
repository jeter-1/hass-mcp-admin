"""Inert capability-scoped automatic-readmission foundation.

Nothing in this package is imported by production startup, routing, providers,
tool registration, health publication, or admission code in this release.
"""

from .coordinator import CapabilityAdmissionCoordinator, ReconciliationAttempt
from .harness import HARNESS_SCHEMA_VERSION, OfflineUpdateHarness
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
    "HARNESS_SCHEMA_VERSION",
    "ObservedCapability",
    "OfflineUpdateHarness",
    "ReconciliationAttempt",
    "ReconciliationResult",
    "RegistryRefreshResult",
    "RouteLease",
    "UpstreamSurface",
    "canonical_json",
    "classify_registry_refresh",
    "evidence_fingerprint",
]
