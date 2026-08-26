"""Non-authoritative capability-scoped automatic-readmission reference model.

This test-support package is an executable compatibility specification. It is
not imported by production startup, routing, providers, tool registration,
health publication, or admission code and cannot grant runtime authority.
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
from .vector_runner import (
    ContractAdapter,
    ReferenceContractAdapter,
    VECTOR_SCHEMA_VERSION,
    run_contract_suite,
    run_contract_vector,
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
    "ContractAdapter",
    "DecisionGeneration",
    "DispatchCommit",
    "HARNESS_SCHEMA_VERSION",
    "ObservedCapability",
    "OfflineUpdateHarness",
    "ReconciliationAttempt",
    "ReconciliationResult",
    "ReferenceContractAdapter",
    "RegistryRefreshResult",
    "RouteLease",
    "UpstreamSurface",
    "VECTOR_SCHEMA_VERSION",
    "canonical_json",
    "classify_registry_refresh",
    "evidence_fingerprint",
    "run_contract_suite",
    "run_contract_vector",
]
