"""Safe, bounded provider request and result contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..facilitation import DetailLevel, EvidenceReference


class ProviderCapability(str, Enum):
    CURRENT_ENTITY_STATE = "current_entity_state"
    BROAD_ENTITY_SEARCH = "broad_entity_search"
    AREA_LOOKUP = "area_lookup"
    SERVICE_DISCOVERY = "service_discovery"
    ORDINARY_SERVICE_EXECUTION = "ordinary_service_execution"
    TEMPLATE_RENDER = "template_render"
    HISTORY_READ = "history_read"
    LOGBOOK_READ = "logbook_read"
    ERROR_LOG_READ = "error_log_read"
    AUTOMATION_LIST = "automation_list"
    DEVICE_REGISTRY_READ = "device_registry_read"
    ENTITY_REGISTRY_READ = "entity_registry_read"
    BLUEPRINT_LIST = "blueprint_list"
    LEGACY_AUTOMATION_WRITE = "legacy_automation_write"
    AUTOMATION_CONFIG = "automation_config"
    AUTOMATION_TRACE = "automation_trace"
    BLUEPRINT_SOURCE = "blueprint_source"
    CONFIG_VALIDATION = "config_validation"
    GOVERNED_APPLY = "governed_apply"
    EXACT_VERIFICATION = "exact_verification"
    GOVERNED_ROLLBACK = "governed_rollback"
    GOVERNANCE_PERSISTENCE = "governance_persistence"
    RISK_ASSESSMENT = "risk_assessment"
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    RELIABILITY_ANALYSIS = "reliability_analysis"
    IMPACT_ANALYSIS = "impact_analysis"
    AUDIT = "audit"
    HANDOFF_GENERATION = "handoff_generation"
    UNGOVERNED_PHYSICAL_ACTION = "ungoverned_physical_action"
    SECRET_BEARING_DIAGNOSTICS = "secret_bearing_diagnostics"
    UNSUPPORTED_EXPERIMENTAL = "unsupported_experimental"


class ProviderCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ProviderFailureCategory(str, Enum):
    UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "provider_timeout"
    UPSTREAM_ERROR = "provider_upstream_error"
    INVALID_RESPONSE = "provider_invalid_response"
    UNSUPPORTED = "provider_unsupported"
    PROHIBITED = "provider_prohibited"


@dataclass(frozen=True)
class ProviderCoverage:
    requested_sources: int
    completed_sources: int
    missing_sources: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.completed_sources >= self.requested_sources and not self.missing_sources


@dataclass(frozen=True)
class EvidenceRequest:
    capability: ProviderCapability
    query: dict[str, Any] = field(default_factory=dict)
    max_evidence: int = 25
    detail_level: DetailLevel = DetailLevel.STANDARD
    allow_direct_fallback: bool = False


@dataclass(frozen=True)
class ProviderError:
    category: ProviderFailureCategory
    message: str
    retryable: bool = False


@dataclass
class ProviderResult:
    provider_id: str
    capability: ProviderCapability
    completeness: ProviderCompleteness
    evidence: list[EvidenceReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timing_ms: float = 0.0
    failure: ProviderError | None = None
    fallback_occurred: bool = False
    coverage: ProviderCoverage = field(default_factory=lambda: ProviderCoverage(1, 0))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    @property
    def succeeded(self) -> bool:
        return self.completeness in {ProviderCompleteness.COMPLETE, ProviderCompleteness.PARTIAL}

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability": self.capability.value,
            "completeness": self.completeness.value,
            "evidence_count": self.evidence_count,
            "evidence": [item.as_dict() for item in self.evidence[:100]],
            "warnings": self.warnings[:20],
            "timing_ms": round(max(0.0, self.timing_ms), 3),
            "failure_category": self.failure.category.value if self.failure else None,
            "retryable": self.failure.retryable if self.failure else False,
            "fallback_occurred": self.fallback_occurred,
            "coverage": {**asdict(self.coverage), "complete": self.coverage.complete},
            "metadata": _safe_metadata(self.metadata),
        }


def _safe_metadata(value: Any, *, depth: int = 0) -> Any:
    """Bound diagnostic metadata and remove secret/path-bearing fields."""

    if depth > 3:
        return "<bounded>"
    if isinstance(value, dict):
        safe = {}
        for key, item in list(value.items())[:25]:
            normalized = str(key).lower()
            if any(term in normalized for term in ("secret", "token", "authorization", "authenticated_url", "path")):
                safe[str(key)] = "<redacted>"
            else:
                safe[str(key)] = _safe_metadata(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, depth=depth + 1) for item in list(value)[:25]]
    if isinstance(value, str):
        if len(value) > 256:
            return value[:256] + "...<bounded>"
        if "/mcp" in value or "bearer " in value.lower():
            return "<redacted>"
    return value
