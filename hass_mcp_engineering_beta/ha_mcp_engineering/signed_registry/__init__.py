"""Inert signed compatibility-registry format and verification primitives.

Nothing in the Engineering runtime imports this package during startup or
admission. It is a data-only foundation for a separately reviewed integration.
"""

from .canonical import canonical_json, sha256_digest
from .models import (
    DashboardAttestationContract,
    ProviderArgumentConstraint,
    RegistryEnvelope,
    RegistryErrorCode,
    RegistryValidationError,
    ReleaseRevocation,
    ReviewedReleaseEntry,
    ToolContract,
)
from .verification import (
    MAX_CLOCK_SKEW,
    AcceptedRegistryState,
    RegistryValidationResult,
    TrustAnchorStore,
    ValidationIssue,
    ValidationStatus,
    parse_verified_registry_envelope,
    validate_registry_envelope,
)

__all__ = [
    "MAX_CLOCK_SKEW",
    "AcceptedRegistryState",
    "DashboardAttestationContract",
    "ProviderArgumentConstraint",
    "RegistryEnvelope",
    "RegistryErrorCode",
    "RegistryValidationError",
    "RegistryValidationResult",
    "ReleaseRevocation",
    "ReviewedReleaseEntry",
    "ToolContract",
    "TrustAnchorStore",
    "ValidationIssue",
    "ValidationStatus",
    "canonical_json",
    "parse_verified_registry_envelope",
    "sha256_digest",
    "validate_registry_envelope",
]
