"""Bounded signed compatibility-registry verification primitives.

The ha-mcp readmission path imports these data-only models and validators during
startup and admission. Signed data may select only already compiled profiles
and adapters; it cannot supply executable behavior or new reachability.
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
