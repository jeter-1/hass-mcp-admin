"""Home Assistant Core capability-scoped readmission primitives.

This package is intentionally not initialized by the application yet.  Final
route publication belongs to the shared coordinator after the concurrent
ha-mcp readmission branch lands.
"""

from .coordinator import (
    CoreGenerationAllocator,
    CoreReadmissionCoordinator,
    CoreReconciliationAttempt,
    LocalCoreGenerationAllocator,
)
from .models import (
    CORE_IDENTITY,
    CORE_PROTOCOL,
    SURFACE,
    CoreAuthoritySelection,
    CoreAuthoritySource,
    CoreAuthorityStatus,
    CoreCapabilityClass,
    CoreCapabilityDecision,
    CoreCapabilityEvidence,
    CoreCapabilityProfile,
    CoreDecisionGeneration,
    CoreDispatchCommit,
    CoreDisposition,
    CoreObservation,
    CoreReadmissionError,
    CoreReconciliationResult,
    CoreRouteLease,
    canonical_json,
    fingerprint,
)
from .observation import (
    CoreObservationCollector,
    CoreSnapshotSource,
    stable_observation,
)
from .profiles import (
    CORE_CAPABILITY_PROFILES,
    SUPPORTED_CORE_RELEASES,
    compiled_exact_authority,
)


__all__ = [
    "CORE_CAPABILITY_PROFILES",
    "CORE_IDENTITY",
    "CORE_PROTOCOL",
    "SUPPORTED_CORE_RELEASES",
    "SURFACE",
    "CoreAuthoritySelection",
    "CoreAuthoritySource",
    "CoreAuthorityStatus",
    "CoreCapabilityClass",
    "CoreCapabilityDecision",
    "CoreCapabilityEvidence",
    "CoreCapabilityProfile",
    "CoreDecisionGeneration",
    "CoreDispatchCommit",
    "CoreDisposition",
    "CoreGenerationAllocator",
    "CoreObservation",
    "CoreObservationCollector",
    "CoreReadmissionCoordinator",
    "CoreReadmissionError",
    "CoreReconciliationAttempt",
    "CoreReconciliationResult",
    "CoreRouteLease",
    "CoreSnapshotSource",
    "LocalCoreGenerationAllocator",
    "canonical_json",
    "compiled_exact_authority",
    "fingerprint",
    "stable_observation",
]
