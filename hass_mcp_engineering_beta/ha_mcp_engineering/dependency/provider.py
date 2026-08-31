"""Direct Home Assistant evidence provider for dependency index construction."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
from pathlib import Path
import re
import time
from typing import Any

from ..facilitation import EvidenceReference
from ..observability import METRICS
from ..logging_config import redact_data
from ..providers import (
    EngineeringEvidenceProvider,
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderResult,
)
from .extraction import (
    EXPAND_LOOKUP_MODEL,
    MAX_EXPAND_MEMBERS_PER_ENTITY,
    MAX_EXPAND_SNAPSHOT_ENTITIES,
    ExpandEntitySnapshotEvidence,
    ExpandSnapshotEvidence,
    LABEL_LOOKUP_MODEL,
    discharge_resolved_blueprint_source_obligation,
    extract_document_with_obligations,
    make_coverage_failure_obligation,
    project_obligations,
    resolve_literal_label_obligations,
    resolve_blueprint_roles,
    valid_entity_id,
)
from .models import (
    AutomationReadFailure,
    AutomationActionRiskProfile,
    DependencyScanResult,
    LABEL_SELECTOR_AUTHORITY_MODEL,
    LABEL_SELECTOR_DIAGNOSTIC_MODEL,
    LabelSelectorAuthorityEvidence,
    OBLIGATION_LEDGER_MODEL,
    SOURCE_TYPES,
    SourceCoverageItem,
)


MAX_AUTOMATION_SOURCES = 1_000


MAX_LABEL_REGISTRY_ENTRIES = 1_000
MAX_LABEL_MEMBERSHIP = 128
MAX_LITERAL_LABEL_SELECTORS = 256
MAX_ENTITY_LABELS = 64
MAX_ENTITY_REGISTRY_CANONICAL_RECORD_BYTES = 65_536
MAX_ENTITY_REGISTRY_CANONICAL_RECORD_NODES = 4_096
MAX_ENTITY_REGISTRY_CANONICAL_RECORD_DEPTH = 64
MAX_ENTITY_REGISTRY_CANONICAL_TOTAL_BYTES = 16_777_216
MAX_BLUEPRINT_RESOLUTION_NODES = 10_000
MAX_BLUEPRINT_RESOLUTION_DEPTH = 64
MAX_BLUEPRINT_SOURCE_BYTES = 1_048_576
MAX_EXPAND_SOURCE_DOMAIN_LENGTH = 64
CANONICAL_EXPAND_SOURCE_DOMAIN = re.compile(
    r"(?=.*[a-z])[a-z0-9]+(?:_[a-z0-9]+)*", re.ASCII
)


@dataclass(frozen=True)
class LabelMembershipEvidence:
    """Bounded selector-local label lookup and membership evidence.

    Iteration preserves the historical five-value internal test contract.
    New production code consumes ``selector_complete`` explicitly so one
    malformed but provably unrelated entity-registry record cannot poison
    every literal label selector.
    """

    memberships: dict[str, tuple[str, ...]]
    fingerprints: dict[str, str]
    truncated: tuple[str, ...]
    lookup_resolutions: dict[str, tuple[str, str | None]]
    selector_complete: dict[str, bool]
    selector_authority: dict[str, LabelSelectorAuthorityEvidence]
    complete: bool

    def _legacy_values(self) -> tuple[Any, ...]:
        return (
            self.memberships,
            self.fingerprints,
            self.truncated,
            self.lookup_resolutions,
            self.complete,
        )

    def __iter__(self):
        return iter(self._legacy_values())

    def __len__(self) -> int:
        return 5

    def __getitem__(self, index):
        return self._legacy_values()[index]


def _valid_expand_source_domain(value: Any) -> bool:
    """Return whether registry source authority is already canonical."""

    return bool(
        isinstance(value, str)
        and 0 < len(value) <= MAX_EXPAND_SOURCE_DOMAIN_LENGTH
        and CANONICAL_EXPAND_SOURCE_DOMAIN.fullmatch(value)
    )


def _bounded_provider_identity(
    value: Any,
    *,
    secret: str,
    limit: int = 160,
) -> str | None:
    if value is None:
        return None
    raw = str(value)
    safe = redact_data(raw, secret=secret, max_string=limit)
    if isinstance(safe, str) and safe == raw and len(raw) <= limit:
        return safe
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DependencySourceProvider(EngineeringEvidenceProvider):
    provider_id = "dependency_source_provider"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    @abstractmethod
    async def scan(self) -> DependencyScanResult:
        raise NotImplementedError


class DirectHaDependencyProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset(
        {
            ProviderCapability.DEPENDENCY_ANALYSIS,
            ProviderCapability.AUTOMATION_CONFIG,
            ProviderCapability.BLUEPRINT_SOURCE,
            ProviderCapability.ENTITY_REGISTRY_READ,
            ProviderCapability.CURRENT_ENTITY_STATE,
        }
    )

    def __init__(self, rest_client, websocket_client, *, secret: str = "", concurrency: int = 8, timeout: float = 60.0):
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.secret = secret
        self.concurrency = max(1, min(concurrency, 10))
        self.timeout = max(1.0, min(timeout, 120.0))

    @property
    def available(self) -> bool:
        return True

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        scan = await self.scan()
        completeness = ProviderCompleteness.COMPLETE
        if any(item.completeness == "partial" for item in scan.coverage):
            completeness = ProviderCompleteness.PARTIAL
        if all(item.completeness in {"unavailable", "unsupported"} for item in scan.coverage):
            completeness = ProviderCompleteness.UNAVAILABLE
        references = [
            EvidenceReference(item.evidence_id, self.provider_id, item.relation, item.evidence_summary)
            for item in scan.findings[: max(1, min(request.max_evidence, 100))]
        ]
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=completeness,
            evidence=references,
            warnings=[warning for item in scan.coverage for warning in item.warnings][:20],
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(scan.coverage), sum(item.completeness == "complete" for item in scan.coverage)),
        )

    async def scan(self) -> DependencyScanResult:
        # Import after the dependency package is initialized; governance uses
        # this shared index at runtime and a module-level import would create a
        # package initialization cycle.
        from ..governance.risk import (
            automation_action_consequence_profile,
        )

        scan_started = time.perf_counter()
        findings = []
        dynamic = []
        obligations = []
        automation_action_profiles: list[
            AutomationActionRiskProfile
        ] = []
        automation_read_failures: list[AutomationReadFailure] = []
        metadata: dict[str, dict[str, Any]] = {}
        label_memberships: dict[str, tuple[str, ...]] = {}
        label_membership_fingerprints: dict[str, str] = {}
        label_membership_complete: dict[str, bool] = {}
        label_selector_authority: dict[
            str, LabelSelectorAuthorityEvidence
        ] = {}
        label_lookup_resolutions: dict[
            str, tuple[str, str | None]
        ] = {}
        label_membership_truncated: tuple[str, ...] = ()
        label_registry_complete = False
        coverage: list[SourceCoverageItem] = []
        request_counts: Counter[str] = Counter()
        request_time_ms: dict[str, float] = defaultdict(float)
        queue_wait_samples_ms: list[float] = []
        active_requests = 0
        maximum_concurrency = 0
        semaphore = asyncio.Semaphore(self.concurrency)

        async def request(operation: str, factory, *, queued: bool = False):
            nonlocal active_requests, maximum_concurrency
            queued_at = time.perf_counter()
            if queued:
                await semaphore.acquire()
                queue_wait_samples_ms.append((time.perf_counter() - queued_at) * 1000)
            started = time.perf_counter()
            request_counts[operation] += 1
            active_requests += 1
            maximum_concurrency = max(maximum_concurrency, active_requests)
            try:
                return await asyncio.wait_for(factory(), self.timeout)
            finally:
                active_requests -= 1
                request_time_ms[operation] += (time.perf_counter() - started) * 1000
                if queued:
                    semaphore.release()

        # B39-136-R3b: the reviewed template semantics are only valid for a
        # supported Home Assistant release, so the running version is read as
        # part of this scan.  Binding it to the scan means the R2 source-read
        # fence already governs its freshness: a fenced post-lock refresh
        # cannot be satisfied by a scan that observed an older version.
        home_assistant_version: str | None = None
        home_assistant_version_status = "unavailable"
        try:
            config = await request(
                "home_assistant_config",
                lambda: self.rest_client.request("GET", "/config"),
            )
            if not isinstance(config, dict):
                home_assistant_version_status = "unreadable"
            else:
                observed = config.get("version")
                if isinstance(observed, str) and observed.strip():
                    home_assistant_version = observed.strip()[:64]
                    home_assistant_version_status = "observed"
                else:
                    home_assistant_version_status = "unreadable"
        except Exception:
            # Connectivity failure, timeout, or transport error.  Fails closed
            # downstream; it is never treated as an admitted version.
            home_assistant_version_status = "unavailable"

        state_started = time.perf_counter()
        try:
            states = await request("states_inventory", lambda: self.rest_client.request("GET", "/states"))
            if not isinstance(states, list):
                raise TypeError("state response is not a list")
            METRICS.record_provider_result(self.provider_id, "complete", dispatched=True)
        except Exception:
            METRICS.record_provider_result(self.provider_id, "failed", dispatched=True)
            raise

        registry = []
        registry_warning = []
        entity_registry_complete = False
        try:
            registry = await request(
                "entity_registry_inventory",
                lambda: self.websocket_client.command({"type": "config/entity_registry/list"}),
            )
            if not isinstance(registry, list):
                registry = []
                registry_warning.append("Entity registry returned an invalid response.")
            else:
                entity_registry_complete = True
            METRICS.record_provider_result(
                self.provider_id,
                "complete" if not registry_warning else "partial",
                dispatched=True,
            )
        except Exception:
            registry_warning.append("Entity registry could not be read; target metadata is partial.")
            METRICS.record_provider_result(self.provider_id, "failed", dispatched=True)

        semantic_registry = _deduplicate_identical_entity_registry_records(
            registry
        )

        expand_snapshot_evidence = _build_expand_snapshot_evidence(
            states=states,
            entity_registry=semantic_registry,
            entity_registry_complete=entity_registry_complete,
        )

        for state in states:
            entity_id = str(state.get("entity_id", "")).lower()
            if not entity_id:
                continue
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            metadata[entity_id] = {
                "entity_id": entity_id,
                "entity_exists": True,
                "registry_entry_exists": False,
                "domain": entity_id.split(".", 1)[0],
                "friendly_name": redact_data(attrs.get("friendly_name"), secret=self.secret, max_string=160),
                "state": redact_data(state.get("state"), secret=self.secret, max_string=128),
            }
        for entry in semantic_registry:
            if not isinstance(entry, dict):
                continue
            entity_id = str(entry.get("entity_id", "")).lower()
            if not entity_id:
                continue
            item = metadata.setdefault(
                entity_id,
                {"entity_id": entity_id, "entity_exists": False, "domain": entity_id.split(".", 1)[0]},
            )
            item.update(
                {
                    "registry_entry_exists": True,
                    "platform": entry.get("platform"),
                    "device_id": entry.get("device_id"),
                    "area_id": entry.get("area_id"),
                    "disabled": bool(entry.get("disabled_by")),
                    "hidden": bool(entry.get("hidden_by")),
                }
            )

        automations = sorted(
            (
                state
                for state in states
                if str(state.get("entity_id", "")).startswith(
                    "automation."
                )
            ),
            key=lambda state: str(state.get("entity_id", "")),
        )
        automation_inventory_overflow = automations[
            MAX_AUTOMATION_SOURCES:
        ]
        automations = automations[:MAX_AUTOMATION_SOURCES]
        automation_inventory_overflow_count = len(
            automation_inventory_overflow
        )
        if automation_inventory_overflow_count:
            overflow_identities = sorted(
                str(state.get("entity_id", ""))[:128]
                for state in automation_inventory_overflow
            )
            overflow_fingerprint = hashlib.sha256(
                json.dumps(
                    overflow_identities,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            obligations.append(
                make_coverage_failure_obligation(
                    source_type="automation",
                    source_id="dependency_provider",
                    source_entity_id=None,
                    config_path="$",
                    relation="other_structured_reference",
                    reason_code="automation_inventory_limit_exceeded",
                    configuration_fingerprint=overflow_fingerprint,
                    limit_exceeded=True,
                )
            )
        async def fetch_automation(state):
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            internal_id = attrs.get("id")
            if not internal_id:
                return state, None, "automation_id_missing"
            try:
                config = await request(
                    "automation_config",
                    lambda: self.rest_client.request("GET", f"/config/automation/config/{internal_id}"),
                    queued=True,
                )
                if not isinstance(config, dict):
                    return state, None, "automation_config_invalid"
                return state, config, None
            except Exception:
                return state, None, "automation_config_unreadable"

        auto_started = time.perf_counter()
        results = await asyncio.gather(*(fetch_automation(state) for state in automations))
        failed = 0
        blueprint_failures = 0
        parse_started = time.perf_counter()
        for state, config, failure in results:
            attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            internal_id = _bounded_provider_identity(
                attrs.get("id") or state.get("entity_id"),
                secret=self.secret,
            )
            source_entity_id = _bounded_provider_identity(
                state.get("entity_id"),
                secret=self.secret,
            )
            if failure or config is None:
                failed += 1
                failure_reason = str(
                    failure or "automation_config_unreadable"
                )
                automation_read_failures.append(
                    AutomationReadFailure(
                        source_id=internal_id,
                        source_entity_id=source_entity_id,
                        reason_code=failure_reason,
                    )
                )
                obligations.append(
                    make_coverage_failure_obligation(
                        source_type="automation",
                        source_id=internal_id,
                        source_entity_id=source_entity_id,
                        config_path="$",
                        relation="other_structured_reference",
                        reason_code=failure_reason,
                    )
                )
                continue
            blueprint = config.get("use_blueprint")
            parsed_blueprint = None
            blueprint_read_failure = None
            if isinstance(blueprint, dict):
                path = blueprint.get("path")
                parsed_blueprint, blueprint_read_failure = (
                    _read_blueprint_with_status(path)
                    if isinstance(path, str)
                    else (None, "blueprint_source_unavailable")
                )
            extracted, unresolved, extracted_obligations = (
                extract_document_with_obligations(
                    source_type="automation",
                    source_id=internal_id,
                    source_entity_id=source_entity_id,
                    source_name=attrs.get("friendly_name"),
                    source_state=state.get("state"),
                    config=config,
                    secret=self.secret,
                )
            )
            action_config = config
            if isinstance(blueprint, dict):
                if parsed_blueprint is None:
                    blueprint_failures += 1
                    obligations.append(
                        make_coverage_failure_obligation(
                            source_type="blueprint",
                            source_id=internal_id,
                            source_entity_id=source_entity_id,
                            config_path="$.use_blueprint.path",
                            relation="blueprint_resolved_role",
                            reason_code=(
                                blueprint_read_failure
                                or "blueprint_source_unavailable"
                            ),
                            configuration_fingerprint=(
                                extracted_obligations[0].configuration_fingerprint
                                if extracted_obligations
                                else None
                            ),
                        )
                    )
                    action_config = {
                        "action": [
                            {"service": "{{ unresolved_blueprint_action }}"}
                        ]
                    }
                else:
                    resolved_blueprint, blueprint_resolution_complete = (
                        _resolve_blueprint_inputs(parsed_blueprint, config)
                    )
                    if not blueprint_resolution_complete:
                        blueprint_failures += 1
                        obligations.append(
                            make_coverage_failure_obligation(
                                source_type="blueprint",
                                source_id=internal_id,
                                source_entity_id=source_entity_id,
                                config_path="$.use_blueprint.input",
                                relation="blueprint_resolved_role",
                                reason_code=(
                                    "blueprint_input_resolution_limit_exceeded"
                                ),
                                configuration_fingerprint=(
                                    extracted_obligations[0].configuration_fingerprint
                                    if extracted_obligations
                                    else None
                                ),
                                limit_exceeded=True,
                            )
                        )
                    findings.extend(
                        resolve_blueprint_roles(
                            extracted,
                            parsed_blueprint,
                            source_id=internal_id,
                        )
                    )
                    if blueprint_resolution_complete:
                        (
                            extracted_obligations,
                            blueprint_findings,
                            blueprint_dynamic,
                            blueprint_obligations,
                            discharged_dynamic_ids,
                        ) = discharge_resolved_blueprint_source_obligation(
                            automation_config=config,
                            resolved_blueprint_config=resolved_blueprint,
                            raw_obligations=extracted_obligations,
                            source_id=internal_id,
                            source_entity_id=source_entity_id,
                            source_name=attrs.get("friendly_name"),
                            source_state=state.get("state"),
                            secret=self.secret,
                        )
                        unresolved = [
                            item
                            for item in unresolved
                            if item.evidence_id
                            not in discharged_dynamic_ids
                        ]
                    else:
                        (
                            blueprint_findings,
                            blueprint_dynamic,
                            blueprint_obligations,
                        ) = extract_document_with_obligations(
                            source_type="blueprint",
                            source_id=internal_id,
                            source_entity_id=source_entity_id,
                            source_name=attrs.get("friendly_name"),
                            source_state=state.get("state"),
                            config=resolved_blueprint,
                            secret=self.secret,
                        )
                    findings.extend(blueprint_findings)
                    dynamic.extend(blueprint_dynamic)
                    obligations.extend(blueprint_obligations)
                    action_config = resolved_blueprint
            findings.extend(extracted)
            dynamic.extend(unresolved)
            obligations.extend(extracted_obligations)
            consequence = automation_action_consequence_profile(
                action_config
            )
            automation_action_profiles.append(
                AutomationActionRiskProfile(
                    source_id=internal_id,
                    source_entity_id=source_entity_id,
                    risk_level=str(consequence["risk_level"]),
                    physical_consequence=str(
                        consequence["physical_consequence"]
                    ),
                    complete=bool(consequence["complete"]),
                    truncated=bool(consequence["truncated"]),
                    action_domains=tuple(
                        str(item)
                        for item in consequence["action_domains"]
                    ),
                    services=tuple(
                        str(item) for item in consequence["services"]
                    ),
                    reason_codes=tuple(
                        str(item)
                        for item in consequence["reason_codes"]
                    ),
                    effect_projection_model=str(
                        consequence["effect_projection_model"]
                    ),
                    effect_targets=tuple(
                        str(item)
                        for item in consequence["effect_targets"]
                    ),
                    effect_data=tuple(
                        str(item)
                        for item in consequence["effect_data"]
                    ),
                    effect_structure_fingerprint=str(
                        consequence["effect_structure_fingerprint"]
                    ),
                    effect_projection_fingerprint=str(
                        consequence["effect_projection_fingerprint"]
                    ),
                    effect_projection_clipped=bool(
                        consequence["effect_projection_clipped"]
                    ),
                    evidence_fingerprint=str(
                        consequence["evidence_fingerprint"]
                    ),
                    analysis_complete=bool(
                        consequence["analysis_complete"]
                    ),
                    semantic_complete=bool(
                        consequence["semantic_complete"]
                    ),
                    presentation_truncated=bool(
                        consequence["presentation_truncated"]
                    ),
                    processing_limit_exceeded=bool(
                        consequence["processing_limit_exceeded"]
                    ),
                    processing_limit_reason=(
                        str(consequence["processing_limit_reason"])
                        if consequence["processing_limit_reason"] is not None
                        else None
                    ),
                    processing_observed_action_step_count=int(
                        consequence[
                            "processing_observed_action_step_count"
                        ]
                    ),
                    processing_action_step_limit=int(
                        consequence["processing_action_step_limit"]
                    ),
                    processing_action_depth_limit=int(
                        consequence["processing_action_depth_limit"]
                    ),
                    processing_observed_effect_node_count=int(
                        consequence[
                            "processing_observed_effect_node_count"
                        ]
                    ),
                    processing_effect_node_limit=int(
                        consequence["processing_effect_node_limit"]
                    ),
                    processing_effect_depth_limit=int(
                        consequence["processing_effect_depth_limit"]
                    ),
                    processing_overflow_fingerprint=(
                        str(consequence["processing_overflow_fingerprint"])
                        if consequence["processing_overflow_fingerprint"]
                        is not None
                        else None
                    ),
                    action_domain_count=int(
                        consequence["action_domain_count"]
                    ),
                    action_domains_fingerprint=str(
                        consequence["action_domains_fingerprint"]
                    ),
                    service_count=int(consequence["service_count"]),
                    services_fingerprint=str(
                        consequence["services_fingerprint"]
                    ),
                    reason_code_count=int(
                        consequence["reason_code_count"]
                    ),
                    reason_codes_fingerprint=str(
                        consequence["reason_codes_fingerprint"]
                    ),
                    effect_target_count=int(
                        consequence["effect_target_count"]
                    ),
                    effect_targets_fingerprint=str(
                        consequence["effect_targets_fingerprint"]
                    ),
                    effect_data_count=int(
                        consequence["effect_data_count"]
                    ),
                    effect_data_fingerprint=str(
                        consequence["effect_data_fingerprint"]
                    ),
                )
            )

        literal_label_selectors = sorted(
            {
                selector
                for item in obligations
                if "entity_set_producer:label_entities"
                in item.context_provenance
                for selector in item.literal_selectors
            },
            key=lambda item: item.encode("utf-8"),
        )
        label_warning: list[str] = []
        if literal_label_selectors:
            selector_inventory_complete = True
            if len(literal_label_selectors) > MAX_LITERAL_LABEL_SELECTORS:
                selector_inventory_complete = False
                literal_label_selectors = literal_label_selectors[
                    :MAX_LITERAL_LABEL_SELECTORS
                ]
                label_warning.append(
                    "Literal label selectors exceeded the bounded dependency payload."
                )
            label_registry: list[Any] = []
            label_inventory_available = False
            label_inventory_raw_bound_exceeded = False
            try:
                label_registry = await request(
                    "label_registry_inventory",
                    lambda: self.websocket_client.command(
                        {"type": "config/label_registry/list"}
                    ),
                )
                if not isinstance(label_registry, list):
                    label_registry = []
                    label_warning.append(
                        "Label registry returned an invalid response."
                    )
                elif len(label_registry) > MAX_LABEL_REGISTRY_ENTRIES:
                    label_inventory_raw_bound_exceeded = True
                    label_registry = sorted(
                        label_registry,
                        key=lambda item: json.dumps(
                            item, sort_keys=True, default=str
                        ),
                    )[:MAX_LABEL_REGISTRY_ENTRIES]
                    label_warning.append(
                        "Label registry exceeded the bounded dependency payload."
                    )
                else:
                    label_inventory_available = True
            except Exception:
                label_warning.append(
                    "Label registry could not be read for dynamic dependency resolution."
                )
            label_evidence = _build_label_membership_evidence(
                literal_label_selectors,
                entity_registry=registry,
                label_registry=label_registry,
                entity_inventory_available=entity_registry_complete,
                label_inventory_available=label_inventory_available,
                selector_inventory_complete=selector_inventory_complete,
                label_inventory_raw_bound_exceeded=(
                    label_inventory_raw_bound_exceeded
                ),
            )
            (
                label_memberships,
                label_membership_fingerprints,
                label_membership_truncated,
                label_lookup_resolutions,
                membership_complete,
            ) = label_evidence
            label_membership_complete = dict(
                label_evidence.selector_complete
            )
            label_selector_authority = dict(
                label_evidence.selector_authority
            )
            label_registry_complete = bool(
                membership_complete
                and not registry_warning
                and not label_warning
            )
            registry_warning.extend(label_warning)

        obligations = resolve_literal_label_obligations(
            obligations,
            label_memberships=label_memberships,
            label_membership_fingerprints=(
                label_membership_fingerprints
            ),
            label_membership_truncated=label_membership_truncated,
            label_lookup_resolutions=label_lookup_resolutions,
            label_registry_complete=label_registry_complete,
            label_membership_complete=label_membership_complete,
            expand_snapshot_evidence=expand_snapshot_evidence,
        )
        # Compatibility findings are a projection of the same post-registry
        # ledger consumed by helper risk and persisted in the index.  Retain
        # structured/blueprint-role findings, replace pre-resolution template
        # projections, and never leave a stale opaque label projection behind.
        ledger_findings, ledger_dynamic = project_obligations(
            obligations,
            secret=self.secret,
        )
        findings = [
            item
            for item in findings
            if item.match_type != "template_ast_exact"
        ]
        findings.extend(ledger_findings)
        dynamic = ledger_dynamic

        automation_coverage_failure_sources = {
            item.source_id
            for item in obligations
            if item.source_type == "automation"
            and item.outcome == "coverage_failure"
        }
        automation_failed_items = max(
            failed + automation_inventory_overflow_count,
            len(automation_coverage_failure_sources),
        )
        automation_status = (
            "complete"
            if automation_failed_items == 0
            else "partial"
            if results
            else "unavailable"
        )
        automation_warnings: list[str] = []
        if failed:
            automation_warnings.append(
                f"{failed} automation configuration(s) could not be read."
            )
        if automation_inventory_overflow_count:
            automation_warnings.append(
                f"{automation_inventory_overflow_count} automation source(s) exceeded the bounded provider inventory."
            )
        bounded_failures = max(
            0,
            len(automation_coverage_failure_sources)
            - failed
            - (1 if automation_inventory_overflow_count else 0),
        )
        if bounded_failures:
            automation_warnings.append(
                f"{bounded_failures} automation configuration(s) exceeded dependency-analysis coverage bounds."
            )
        coverage.append(
            SourceCoverageItem(
                "automation", self.provider_id, ProviderCapability.AUTOMATION_CONFIG.value,
                automation_status, sum(item.source_type == "automation" for item in findings), automation_failed_items,
                automation_warnings,
                (time.perf_counter() - auto_started) * 1000,
                obligation_ledger_completeness=automation_status,
                obligation_ledger_failed_item_count=(
                    automation_failed_items
                ),
            )
        )
        blueprint_coverage_failure_sources = {
            item.source_id
            for item in obligations
            if item.source_type == "blueprint"
            and item.outcome == "coverage_failure"
        }
        blueprint_failed_items = max(
            blueprint_failures,
            len(blueprint_coverage_failure_sources),
        )
        blueprint_status = (
            "complete" if blueprint_failed_items == 0 else "partial"
        )
        blueprint_warnings: list[str] = []
        if blueprint_failures:
            blueprint_warnings.append(
                f"{blueprint_failures} blueprint source(s) could not be resolved; input findings were retained."
            )
        bounded_blueprint_failures = max(
            0, blueprint_failed_items - blueprint_failures
        )
        if bounded_blueprint_failures:
            blueprint_warnings.append(
                f"{bounded_blueprint_failures} blueprint source(s) exceeded dependency-analysis coverage bounds."
            )
        coverage.append(
            SourceCoverageItem(
                "blueprint", self.provider_id, ProviderCapability.BLUEPRINT_SOURCE.value,
                blueprint_status, sum(item.relation.startswith("blueprint") for item in findings), blueprint_failed_items,
                blueprint_warnings,
                (time.perf_counter() - auto_started) * 1000,
                obligation_ledger_completeness=blueprint_status,
                obligation_ledger_failed_item_count=(
                    blueprint_failed_items
                ),
            )
        )
        for source_type in SOURCE_TYPES:
            if source_type in {"automation", "blueprint"}:
                continue
            coverage.append(
                SourceCoverageItem(
                    source_type, "none", f"{source_type}_configuration", "unavailable", 0, 0,
                    [f"Reliable {source_type} configuration access is not available in this beta."], 0.0,
                )
            )
        coverage.append(
            SourceCoverageItem(
                "entity_metadata", self.provider_id, ProviderCapability.CURRENT_ENTITY_STATE.value,
                "partial" if registry_warning else "complete", len(metadata), 1 if registry_warning else 0,
                registry_warning, (time.perf_counter() - state_started) * 1000, False,
                "transitional_direct exact administrative read",
            )
        )
        parsing_ms = (time.perf_counter() - parse_started) * 1000
        return DependencyScanResult(
            findings,
            dynamic,
            metadata,
            coverage,
            profile={
                "request_count": sum(request_counts.values()),
                "request_count_by_operation": dict(sorted(request_counts.items())),
                "time_by_operation_ms": {
                    key: round(value, 3) for key, value in sorted(request_time_ms.items())
                },
                "automation_count": len(automations),
                "dependency_obligation_count": len(obligations),
                "dependency_coverage_failure_count": sum(
                    item.outcome == "coverage_failure"
                    for item in obligations
                ),
                "inventory_calls_duplicated": False,
                "state_inventory_reused": True,
                "entity_registry_snapshot_reused": True,
                "configured_max_concurrency": self.concurrency,
                "observed_max_concurrency": maximum_concurrency,
                # Cumulative wait is per-request effort, not elapsed wall time.
                "queue_wait_ms": round(sum(queue_wait_samples_ms), 3),
                "cumulative_queue_wait_ms": round(sum(queue_wait_samples_ms), 3),
                "maximum_single_request_queue_wait_ms": round(
                    max(queue_wait_samples_ms, default=0.0), 3
                ),
                "average_request_queue_wait_ms": round(
                    sum(queue_wait_samples_ms) / len(queue_wait_samples_ms)
                    if queue_wait_samples_ms else 0.0,
                    3,
                ),
                "network_attempt_time_ms": round(sum(request_time_ms.values()), 3),
                "parsing_indexing_time_ms": round(parsing_ms, 3),
                "scan_wall_time_ms": round((time.perf_counter() - scan_started) * 1000, 3),
                "build_wall_clock_ms": round((time.perf_counter() - scan_started) * 1000, 3),
            },
            home_assistant_version=home_assistant_version,
            home_assistant_version_status=home_assistant_version_status,
            automation_action_profiles=automation_action_profiles,
            automation_read_failures=automation_read_failures,
            label_memberships=label_memberships,
            label_membership_fingerprints=(
                label_membership_fingerprints
            ),
            label_membership_complete=label_membership_complete,
            label_membership_truncated=(
                label_membership_truncated
            ),
            label_registry_complete=label_registry_complete,
            label_selector_authority=label_selector_authority,
            obligations=obligations,
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        )


def _read_blueprint_with_status(
    path: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not path or not path.endswith((".yaml", ".yml")):
        return None, "blueprint_source_unavailable"
    try:
        import yaml
    except ImportError:
        return None, "blueprint_parser_unavailable"
    for base in ("/homeassistant/blueprints", "/config/blueprints"):
        root = Path(base, "automation").resolve()
        candidate = Path(root, path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        try:
            class BlueprintLoader(yaml.SafeLoader):
                pass
            BlueprintLoader.add_constructor(
                "!input", lambda loader, node: {"__blueprint_input__": loader.construct_scalar(node)}
            )
            with candidate.open("rb") as handle:
                payload = handle.read(MAX_BLUEPRINT_SOURCE_BYTES + 1)
            if len(payload) > MAX_BLUEPRINT_SOURCE_BYTES:
                return None, "blueprint_source_limit_exceeded"
            value = yaml.load(
                payload.decode("utf-8"), Loader=BlueprintLoader
            )
            return (
                (value, None)
                if isinstance(value, dict)
                else (None, "blueprint_source_invalid")
            )
        except Exception:
            return None, "blueprint_source_unavailable"
    return None, "blueprint_source_unavailable"


def _read_blueprint(path: str | None) -> dict[str, Any] | None:
    """Compatibility wrapper for other read-only Engineering providers."""

    value, _reason = _read_blueprint_with_status(path)
    return value


def _resolve_blueprint_inputs(
    blueprint_config: dict[str, Any],
    automation_config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Resolve bounded ``!input`` markers without executing templates."""

    use_blueprint = automation_config.get("use_blueprint")
    supplied = (
        use_blueprint.get("input", {})
        if isinstance(use_blueprint, dict)
        else {}
    )
    supplied = supplied if isinstance(supplied, dict) else {}
    blueprint_metadata = blueprint_config.get("blueprint")
    input_definitions = (
        blueprint_metadata.get("input", {})
        if isinstance(blueprint_metadata, dict)
        else {}
    )
    input_definitions = (
        input_definitions if isinstance(input_definitions, dict) else {}
    )
    work_units = 0
    complete = True

    def resolve(value: Any, depth: int) -> Any:
        nonlocal work_units, complete
        work_units += 1
        if (
            work_units > MAX_BLUEPRINT_RESOLUTION_NODES
            or depth > MAX_BLUEPRINT_RESOLUTION_DEPTH
        ):
            complete = False
            return {"__blueprint_input__": "resolution_limit"}
        if isinstance(value, dict):
            if set(value) == {"__blueprint_input__"}:
                name = value.get("__blueprint_input__")
                if isinstance(name, str) and name in supplied:
                    return resolve(supplied[name], depth + 1)
                definition = input_definitions.get(name)
                if isinstance(definition, dict) and "default" in definition:
                    return resolve(definition["default"], depth + 1)
                return {"__blueprint_input__": str(name)[:128]}
            return {
                str(key): resolve(item, depth + 1)
                for key, item in value.items()
                if complete
            }
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value if complete]
        if isinstance(value, tuple):
            return [resolve(item, depth + 1) for item in value if complete]
        return value

    resolved = resolve(blueprint_config, 0)
    return (
        resolved if isinstance(resolved, dict) else {},
        bool(complete and isinstance(resolved, dict)),
    )


def _build_expand_snapshot_evidence(
    *,
    states: list[Any],
    entity_registry: list[Any],
    entity_registry_complete: bool,
) -> ExpandSnapshotEvidence:
    """Project bounded Home Assistant group/zone expansion authority.

    ``StateExtension.expand`` uses both state attributes and the live entity
    source integration.  The entity registry's immutable ``platform`` field is
    the admitted source evidence available to this provider scan.  Absence or
    malformed evidence is retained as ``unknown`` and cannot prove exclusion.
    """

    state_inventory_complete = True
    source_inventory_complete = bool(entity_registry_complete)
    state_records: dict[str, dict[str, Any]] = {}
    duplicate_states: set[str] = set()
    if len(states) > MAX_EXPAND_SNAPSHOT_ENTITIES:
        state_inventory_complete = False
        bounded_states: list[Any] = []
    else:
        bounded_states = states
    for raw in sorted(
        bounded_states,
        key=lambda value: (
            str(value.get("entity_id", ""))
            if isinstance(value, dict)
            else ""
        ),
    ):
        if not isinstance(raw, dict):
            state_inventory_complete = False
            continue
        entity_id = raw.get("entity_id")
        attributes = raw.get("attributes")
        if (
            not isinstance(entity_id, str)
            or not valid_entity_id(entity_id)
            or not isinstance(attributes, dict)
        ):
            state_inventory_complete = False
            continue
        if entity_id in state_records:
            duplicate_states.add(entity_id)
            state_inventory_complete = False
            continue
        state_records[entity_id] = attributes

    source_domains: dict[str, str] = {}
    invalid_sources: set[str] = set()
    seen_source_ids: set[str] = set()
    if len(entity_registry) > MAX_EXPAND_SNAPSHOT_ENTITIES:
        source_inventory_complete = False
        bounded_registry: list[Any] = []
    else:
        bounded_registry = entity_registry
    for raw in sorted(
        bounded_registry,
        key=lambda value: (
            str(value.get("entity_id", ""))
            if isinstance(value, dict)
            else ""
        ),
    ):
        if not isinstance(raw, dict):
            source_inventory_complete = False
            continue
        entity_id = raw.get("entity_id")
        source_domain = raw.get("platform")
        if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
            source_inventory_complete = False
            continue
        if entity_id in seen_source_ids:
            invalid_sources.add(entity_id)
            source_domains.pop(entity_id, None)
            source_inventory_complete = False
            continue
        seen_source_ids.add(entity_id)
        if not _valid_expand_source_domain(source_domain):
            invalid_sources.add(entity_id)
            source_domains.pop(entity_id, None)
            source_inventory_complete = False
            continue
        if entity_id in invalid_sources:
            source_inventory_complete = False
            continue
        source_domains[entity_id] = source_domain

    projected: dict[str, ExpandEntitySnapshotEvidence] = {}
    for entity_id, attributes in state_records.items():
        domain = entity_id.split(".", 1)[0]
        source_domain = source_domains.get(entity_id)
        if entity_id in duplicate_states:
            expandable_kind = "unknown"
            member_attribute = None
            failure_reason = "expand_state_identity_conflict"
        elif domain == "group":
            expandable_kind = "group"
            member_attribute = "entity_id"
            failure_reason = None
        elif domain == "zone":
            expandable_kind = "zone"
            member_attribute = "persons"
            failure_reason = None
        elif entity_id in invalid_sources:
            expandable_kind = "unknown"
            member_attribute = None
            failure_reason = "expand_entity_source_malformed"
        elif source_domain == "group":
            expandable_kind = "group"
            member_attribute = "entity_id"
            failure_reason = None
        elif source_domain is None:
            expandable_kind = "unknown"
            member_attribute = None
            failure_reason = (
                "expand_entity_source_unavailable"
                if source_inventory_complete
                else "expand_source_inventory_unavailable"
            )
        else:
            expandable_kind = "leaf"
            member_attribute = None
            failure_reason = None

        members: tuple[str, ...] = ()
        membership_complete = expandable_kind in {"leaf", "unknown"}
        membership_count = 0
        membership_material: list[str] = []
        if member_attribute is not None:
            raw_members = attributes.get(member_attribute)
            if raw_members is None:
                raw_members = []
            if not isinstance(raw_members, (list, tuple)):
                membership_complete = False
                failure_reason = "expand_membership_malformed"
                membership_material = [
                    "invalid_type:" + type(raw_members).__name__
                ]
            else:
                bounded_raw_members = raw_members[
                    : MAX_EXPAND_MEMBERS_PER_ENTITY + 1
                ]
                valid_members = {
                    value
                    for value in bounded_raw_members
                    if isinstance(value, str) and valid_entity_id(value)
                }
                membership_material = sorted(valid_members)
                membership_count = len(membership_material)
                if any(
                    not isinstance(value, str)
                    or not valid_entity_id(value)
                    for value in bounded_raw_members
                ):
                    membership_complete = False
                    failure_reason = "expand_membership_malformed"
                elif len(raw_members) > MAX_EXPAND_MEMBERS_PER_ENTITY:
                    membership_complete = False
                    failure_reason = "expand_membership_limit_exceeded"
                else:
                    membership_complete = True
                members = tuple(
                    membership_material[:MAX_EXPAND_MEMBERS_PER_ENTITY]
                )
        membership_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "model": EXPAND_LOOKUP_MODEL,
                    "entity_id": entity_id,
                    "source_domain": source_domain,
                    "expandable_kind": expandable_kind,
                    "member_attribute": member_attribute,
                    "members": membership_material,
                    "membership_count": membership_count,
                    "membership_complete": membership_complete,
                    "failure_reason": failure_reason,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        projected[entity_id] = ExpandEntitySnapshotEvidence(
            entity_id=entity_id,
            source_domain=source_domain,
            expandable_kind=expandable_kind,
            member_entity_ids=members,
            membership_complete=membership_complete,
            membership_count=membership_count,
            membership_fingerprint=membership_fingerprint,
            failure_reason=failure_reason,
        )
    return ExpandSnapshotEvidence(
        entities=projected,
        state_inventory_complete=state_inventory_complete,
        source_inventory_complete=source_inventory_complete,
    )


def _strict_canonical_json_bytes(value: Any) -> bytes:
    """Return complete canonical JSON bytes without coercion or repair."""

    encoded_size = 0
    node_count = 0

    def charge(size: int) -> None:
        nonlocal encoded_size
        encoded_size += size
        if encoded_size > MAX_ENTITY_REGISTRY_CANONICAL_RECORD_BYTES:
            raise ValueError("canonical JSON record exceeds the byte bound")

    def count_node() -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_ENTITY_REGISTRY_CANONICAL_RECORD_NODES:
            raise ValueError("canonical JSON record exceeds the node bound")

    def charge_string(value: str) -> None:
        charge(2)
        for character in value:
            codepoint = ord(character)
            if codepoint in {0x22, 0x5C} or codepoint in {
                0x08,
                0x09,
                0x0A,
                0x0C,
                0x0D,
            }:
                charge(2)
            elif codepoint < 0x20:
                charge(6)
            elif codepoint <= 0x7F:
                charge(1)
            elif codepoint <= 0x7FF:
                charge(2)
            elif 0xD800 <= codepoint <= 0xDFFF:
                raise TypeError("canonical JSON strings must be valid Unicode")
            elif codepoint <= 0xFFFF:
                charge(3)
            else:
                charge(4)

    def validate(item: Any, *, depth: int = 0) -> None:
        if depth > MAX_ENTITY_REGISTRY_CANONICAL_RECORD_DEPTH:
            raise ValueError("canonical JSON nesting exceeds the bound")
        count_node()
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise TypeError("canonical JSON mapping keys must be strings")
            charge(2)
            for index, (key, nested) in enumerate(item.items()):
                if index:
                    charge(1)
                count_node()
                charge_string(key)
                charge(1)
                validate(nested, depth=depth + 1)
            return
        if isinstance(item, list):
            charge(2)
            for index, nested in enumerate(item):
                if index:
                    charge(1)
                validate(nested, depth=depth + 1)
            return
        if item is None:
            charge(4)
            return
        if type(item) is str:
            charge_string(item)
            return
        if type(item) is bool:
            charge(4 if item else 5)
            return
        if type(item) is int:
            charge(len(str(item)))
            return
        if type(item) is float and math.isfinite(item):
            charge(
                len(
                    json.dumps(
                        item,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                )
            )
            return
        raise TypeError("unsupported canonical JSON value")

    validate(value)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_ENTITY_REGISTRY_CANONICAL_RECORD_BYTES:
        raise ValueError("canonical JSON record exceeds the byte bound")
    return encoded


def _bounded_registry_canonical_bytes(
    value: Any,
    *,
    retained_bytes: int,
) -> tuple[bytes | None, int, bool]:
    """Canonicalize one record without exceeding aggregate retention."""

    try:
        canonical = _strict_canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError):
        return None, retained_bytes, False
    if len(canonical) > (
        MAX_ENTITY_REGISTRY_CANONICAL_TOTAL_BYTES - retained_bytes
    ):
        return None, retained_bytes, True
    return canonical, retained_bytes + len(canonical), False


def _deduplicate_identical_entity_registry_records(
    entity_registry: list[Any],
) -> list[Any]:
    """Collapse only bounded, fully canonical-identical entity records.

    The raw response bound is authoritative and is evaluated before this
    helper is called.  Invalid, unsupported, or conflicting records are kept
    byte-for-byte in their original order so downstream completeness checks
    continue to fail closed.
    """

    if len(entity_registry) > MAX_EXPAND_SNAPSHOT_ENTITIES:
        return entity_registry

    groups: dict[str, list[tuple[int, bytes | None]]] = defaultdict(list)
    canonical_retained_bytes = 0
    for index, item in enumerate(entity_registry):
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
            continue
        canonical, canonical_retained_bytes, _bound_exceeded = (
            _bounded_registry_canonical_bytes(
                item,
                retained_bytes=canonical_retained_bytes,
            )
        )
        groups[entity_id].append((index, canonical))

    duplicate_indexes: set[int] = set()
    for records in groups.values():
        if len(records) < 2:
            continue
        canonical_values = {canonical for _, canonical in records}
        if None not in canonical_values and len(canonical_values) == 1:
            duplicate_indexes.update(index for index, _ in records[1:])

    if not duplicate_indexes:
        return entity_registry
    return [
        item
        for index, item in enumerate(entity_registry)
        if index not in duplicate_indexes
    ]


def _selector_identity_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_label_membership_evidence(
    selectors: list[str],
    *,
    entity_registry: list[Any],
    label_registry: list[Any],
    entity_inventory_available: bool = True,
    label_inventory_available: bool = True,
    selector_inventory_complete: bool = True,
    label_inventory_raw_bound_exceeded: bool = False,
) -> LabelMembershipEvidence:
    """Resolve literal labels with admitted Home Assistant lookup semantics.

    Home Assistant 2026.7.2, 2026.8.0, and 2026.8.1 all use an exact label-ID
    lookup first, then a normalized-name index where normalization is exactly
    ``name.casefold().replace(" ", "")``.  The provider has already bound the
    running release to that reviewed semantic registry before this helper is
    called.
    """

    distinct_selectors = sorted(
        {
            item
            for item in selectors
            if isinstance(item, str) and item
        },
        key=lambda item: item.encode("utf-8"),
    )
    selector_bound_exceeded = bool(
        len(distinct_selectors) > MAX_LITERAL_LABEL_SELECTORS
        or not selector_inventory_complete
    )
    ordered_selectors = tuple(
        distinct_selectors[:MAX_LITERAL_LABEL_SELECTORS]
    )
    label_inventory_bounded = bool(
        label_inventory_available
        and not label_inventory_raw_bound_exceeded
        and len(label_registry) <= MAX_LABEL_REGISTRY_ENTRIES
    )
    retained_label_registry = label_registry[:MAX_LABEL_REGISTRY_ENTRIES]
    parsed_labels: list[tuple[Any, Any, bool]] = []
    label_id_counts: Counter[str] = Counter()
    normalized_name_counts: Counter[str] = Counter()
    label_inventory_canonical = label_inventory_bounded
    for item in retained_label_registry:
        if not isinstance(item, dict):
            parsed_labels.append((None, None, False))
            label_inventory_canonical = False
            continue
        label_id = item.get("label_id")
        name = item.get("name")
        valid = bool(
            isinstance(label_id, str)
            and 0 < len(label_id) <= 255
            and isinstance(name, str)
            and 0 < len(name) <= 255
        )
        parsed_labels.append((label_id, name, valid))
        if not valid:
            label_inventory_canonical = False
            continue
        normalized_name = name.casefold().replace(" ", "")
        label_id_counts[label_id] += 1
        normalized_name_counts[normalized_name] += 1
    if any(count != 1 for count in label_id_counts.values()) or any(
        count != 1 for count in normalized_name_counts.values()
    ):
        label_inventory_canonical = False

    label_lookup_resolutions: dict[str, tuple[str, str | None]] = {}
    label_ids_by_selector: dict[str, str | None] = {}
    label_selector_complete: dict[str, bool] = {}
    failure_reasons: dict[str, set[str]] = {
        selector: set() for selector in ordered_selectors
    }
    for selector in ordered_selectors:
        exact_matches = [
            (label_id, name, valid)
            for label_id, name, valid in parsed_labels
            if label_id == selector
        ]
        if exact_matches:
            label_ids_by_selector[selector] = selector
            label_lookup_resolutions[selector] = ("label_id", selector)
            label_selector_complete[selector] = bool(
                label_inventory_bounded
                and len(exact_matches) == 1
                and exact_matches[0][2]
                and label_id_counts[selector] == 1
            )
            if label_inventory_raw_bound_exceeded:
                failure_reasons[selector].add(
                    "label_registry_raw_bound_exceeded"
                )
            elif not label_inventory_available:
                failure_reasons[selector].add(
                    "label_inventory_unavailable"
                )
            elif len(label_registry) > MAX_LABEL_REGISTRY_ENTRIES:
                failure_reasons[selector].add(
                    "label_registry_raw_bound_exceeded"
                )
            elif not exact_matches[0][2]:
                failure_reasons[selector].add(
                    "label_identity_malformed"
                )
            elif len(exact_matches) != 1 or label_id_counts[selector] != 1:
                failure_reasons[selector].add(
                    "label_identity_conflicting"
                )
            continue

        normalized_selector = selector.casefold().replace(" ", "")
        name_matches = [
            (label_id, name, valid)
            for label_id, name, valid in parsed_labels
            if isinstance(name, str)
            and name.casefold().replace(" ", "") == normalized_selector
        ]
        if name_matches:
            resolved_label_id = (
                name_matches[0][0]
                if len(name_matches) == 1
                and isinstance(name_matches[0][0], str)
                else None
            )
            label_ids_by_selector[selector] = resolved_label_id
            label_lookup_resolutions[selector] = (
                "normalized_name",
                resolved_label_id,
            )
            label_selector_complete[selector] = bool(
                label_inventory_canonical
                and len(name_matches) == 1
                and name_matches[0][2]
                and resolved_label_id is not None
            )
            if label_inventory_raw_bound_exceeded:
                failure_reasons[selector].add(
                    "label_registry_raw_bound_exceeded"
                )
            elif not label_inventory_available:
                failure_reasons[selector].add(
                    "label_inventory_unavailable"
                )
            elif len(label_registry) > MAX_LABEL_REGISTRY_ENTRIES:
                failure_reasons[selector].add(
                    "label_registry_raw_bound_exceeded"
                )
            elif not label_selector_complete[selector]:
                failure_reasons[selector].add(
                    "label_identity_conflicting_or_malformed"
                )
            continue

        label_ids_by_selector[selector] = None
        label_lookup_resolutions[selector] = ("missing", None)
        # Missing-label exclusion depends on the complete registry.  Exact-ID
        # selection above is the only lookup whose precedence can isolate it
        # from a malformed unrelated label record.
        label_selector_complete[selector] = label_inventory_canonical
        if label_inventory_raw_bound_exceeded:
            failure_reasons[selector].add(
                "label_registry_raw_bound_exceeded"
            )
        elif not label_inventory_available:
            failure_reasons[selector].add("label_inventory_unavailable")
        elif len(label_registry) > MAX_LABEL_REGISTRY_ENTRIES:
            failure_reasons[selector].add(
                "label_registry_raw_bound_exceeded"
            )
        elif not label_inventory_canonical:
            failure_reasons[selector].add(
                "label_identity_conflicting_or_malformed"
            )

    if selector_bound_exceeded:
        for selector in ordered_selectors:
            failure_reasons[selector].add(
                "literal_label_selector_bound_exceeded"
            )

    memberships: dict[str, set[str]] = {
        selector: set() for selector in ordered_selectors
    }
    raw_entity_record_count = len(entity_registry)
    raw_bound_exceeded = bool(
        raw_entity_record_count > MAX_EXPAND_SNAPSHOT_ENTITIES
    )
    entity_selector_complete: dict[str, bool] = {
        selector: bool(entity_inventory_available and not raw_bound_exceeded)
        for selector in ordered_selectors
    }
    identical_duplicates: Counter[str] = Counter()
    conflicting_duplicates: Counter[str] = Counter()
    malformed_relevant: Counter[str] = Counter()
    if not entity_inventory_available:
        for selector in ordered_selectors:
            failure_reasons[selector].add(
                "entity_inventory_unavailable"
            )
    if raw_bound_exceeded:
        for selector in ordered_selectors:
            failure_reasons[selector].add(
                "entity_registry_raw_bound_exceeded"
            )

        def canonical_registry_key(value: Any) -> tuple[Any, ...]:
            """Order an oversized raw response without semantic repair."""

            if not isinstance(value, dict):
                return (1, b"", ())
            raw_entity_id = value.get("entity_id")
            raw_labels = value.get("labels", [])
            if (
                not isinstance(raw_entity_id, str)
                or not valid_entity_id(raw_entity_id)
                or not isinstance(raw_labels, list)
                or len(raw_labels) > MAX_ENTITY_LABELS
                or any(
                    not isinstance(label, str)
                    or not label
                    or len(label) > 255
                    for label in raw_labels
                )
            ):
                return (1, b"", ())
            labels = tuple(
                sorted(
                    {label.encode("utf-8") for label in raw_labels}
                )
            )
            try:
                canonical_digest = hashlib.sha256(
                    _strict_canonical_json_bytes(value)
                ).digest()
            except (TypeError, ValueError, OverflowError):
                canonical_digest = b""
            return (
                0,
                raw_entity_id.encode("utf-8"),
                labels,
                canonical_digest,
            )

        # ``nsmallest`` traverses the supplied snapshot while retaining only
        # the admitted prefix.  Equivalent oversized registries therefore
        # bind the same evidence without allocating another unbounded list.
        retained_entity_registry = heapq.nsmallest(
            MAX_EXPAND_SNAPSHOT_ENTITIES,
            entity_registry,
            key=canonical_registry_key,
        )
    else:
        retained_entity_registry = entity_registry

    canonical_unique_records: set[bytes] = set()
    canonical_groups: dict[
        str, list[tuple[bytes | None, frozenset[str]]]
    ] = defaultdict(list)
    canonical_retained_bytes = 0
    canonical_byte_bound_exceeded = False
    for item in retained_entity_registry:
        if not isinstance(item, dict):
            for selector in ordered_selectors:
                entity_selector_complete[selector] = False
                malformed_relevant[selector] += 1
                failure_reasons[selector].add(
                    "entity_registry_malformed_relevant_record"
                )
            continue
        canonical, canonical_retained_bytes, bound_exceeded = (
            _bounded_registry_canonical_bytes(
                item,
                retained_bytes=canonical_retained_bytes,
            )
        )
        canonical_byte_bound_exceeded = bool(
            canonical_byte_bound_exceeded or bound_exceeded
        )
        if canonical is not None:
            canonical_unique_records.add(canonical)
        raw_labels = item.get("labels", [])
        if (
            not isinstance(raw_labels, list)
            or len(raw_labels) > MAX_ENTITY_LABELS
            or any(
                not isinstance(label, str)
                or not label
                or len(label) > 255
                for label in raw_labels
            )
        ):
            # An unreadable label collection could contain any admitted label.
            for selector in ordered_selectors:
                entity_selector_complete[selector] = False
                malformed_relevant[selector] += 1
                failure_reasons[selector].add(
                    "entity_registry_malformed_relevant_record"
                )
            continue
        labels = set(raw_labels)
        raw_entity_id = item.get("entity_id")
        entity_id = (
            raw_entity_id
            if isinstance(raw_entity_id, str)
            and valid_entity_id(raw_entity_id)
            else None
        )
        relevant_selectors = [
            selector
            for selector, label_id in label_ids_by_selector.items()
            if label_id is not None and label_id in labels
        ]
        if entity_id is None:
            for selector in relevant_selectors:
                entity_selector_complete[selector] = False
                malformed_relevant[selector] += 1
                failure_reasons[selector].add(
                    "entity_registry_malformed_relevant_record"
                )
            continue
        canonical_groups[entity_id].append((canonical, frozenset(labels)))

    if canonical_byte_bound_exceeded:
        for selector in ordered_selectors:
            entity_selector_complete[selector] = False
            failure_reasons[selector].add(
                "entity_registry_canonical_byte_bound_exceeded"
            )

    for entity_id, records in canonical_groups.items():
        variants = {canonical for canonical, _labels in records}
        labels_union = set().union(
            *(labels for _canonical, labels in records)
        )
        relevant_selectors = [
            selector
            for selector, label_id in label_ids_by_selector.items()
            if label_id is not None and label_id in labels_union
        ]
        for selector in relevant_selectors:
            memberships[selector].add(entity_id)
        if None in variants:
            malformed_count = sum(
                canonical is None for canonical, _labels in records
            )
            for selector in relevant_selectors:
                entity_selector_complete[selector] = False
                malformed_relevant[selector] += malformed_count
                failure_reasons[selector].add(
                    "entity_registry_malformed_relevant_record"
                )
            continue
        if len(variants) == 1:
            collapsed = max(0, len(records) - 1)
            for selector in relevant_selectors:
                identical_duplicates[selector] += collapsed
            continue
        conflict_count = max(1, len(records) - 1)
        for selector in relevant_selectors:
            entity_selector_complete[selector] = False
            conflicting_duplicates[selector] += conflict_count
            failure_reasons[selector].add(
                "entity_registry_conflicting_duplicate"
            )

    retained: dict[str, tuple[str, ...]] = {}
    fingerprints: dict[str, str] = {}
    selector_authority: dict[str, LabelSelectorAuthorityEvidence] = {}
    selector_complete: dict[str, bool] = {}
    truncated: list[str] = []
    for selector in ordered_selectors:
        ordered = sorted(
            memberships[selector], key=lambda item: item.encode("utf-8")
        )
        if len(ordered) > MAX_LABEL_MEMBERSHIP:
            entity_selector_complete[selector] = False
            failure_reasons[selector].add(
                "label_membership_bound_exceeded"
            )
        selector_complete[selector] = bool(
            label_selector_complete[selector]
            and entity_selector_complete[selector]
            and not selector_bound_exceeded
        )
        lookup_material = {
            "model": LABEL_LOOKUP_MODEL,
            "selector": selector,
            "lookup_mode": label_lookup_resolutions[selector][0],
            "resolved_label_id": label_lookup_resolutions[selector][1],
            "entity_ids": ordered,
            "complete": selector_complete[selector],
        }
        authority_material = {
            **lookup_material,
            "model": LABEL_SELECTOR_AUTHORITY_MODEL,
            "failure_reason_codes": sorted(failure_reasons[selector]),
            "entity_inventory_available": bool(entity_inventory_available),
            "entity_inventory_complete": entity_selector_complete[selector],
            "label_inventory_available": bool(label_inventory_available),
            "label_inventory_complete": label_selector_complete[selector],
            "raw_bound_exceeded": raw_bound_exceeded,
        }
        encoded = json.dumps(
            lookup_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        fingerprints[selector] = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
        if len(ordered) > MAX_LABEL_MEMBERSHIP:
            truncated.append(selector)
            retained[selector] = tuple(ordered[:MAX_LABEL_MEMBERSHIP])
        else:
            retained[selector] = tuple(ordered)
        authority_fingerprint = hashlib.sha256(
            json.dumps(
                authority_material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        anomaly_material = {
            "model": LABEL_SELECTOR_DIAGNOSTIC_MODEL,
            "selector_fingerprint": _selector_identity_fingerprint(
                selector
            ),
            "raw_entity_record_count": raw_entity_record_count,
            "canonical_unique_record_count": len(
                canonical_unique_records
            ),
            "identical_duplicates_collapsed": identical_duplicates[
                selector
            ],
            "conflicting_duplicate_count": conflicting_duplicates[
                selector
            ],
            "malformed_relevant_record_count": malformed_relevant[
                selector
            ],
            "raw_bound_exceeded": raw_bound_exceeded,
            "canonical_byte_bound_exceeded": (
                canonical_byte_bound_exceeded
            ),
        }
        anomaly_fingerprint = hashlib.sha256(
            json.dumps(
                anomaly_material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        selector_authority[selector] = LabelSelectorAuthorityEvidence(
            selector_fingerprint=(
                _selector_identity_fingerprint(selector) or ""
            ),
            lookup_mode=label_lookup_resolutions[selector][0],
            resolved_label_fingerprint=_selector_identity_fingerprint(
                label_lookup_resolutions[selector][1]
            ),
            complete=selector_complete[selector],
            failure_reason_codes=tuple(
                sorted(failure_reasons[selector])
            ),
            membership_count=len(ordered),
            membership_fingerprint=fingerprints[selector],
            candidate_count=len(ordered),
            candidate_complete=selector_complete[selector],
            entity_inventory_available=bool(entity_inventory_available),
            entity_inventory_complete=entity_selector_complete[selector],
            label_inventory_available=bool(label_inventory_available),
            label_inventory_complete=label_selector_complete[selector],
            raw_entity_record_count=raw_entity_record_count,
            canonical_unique_record_count=len(canonical_unique_records),
            identical_duplicates_collapsed=identical_duplicates[selector],
            conflicting_duplicate_count=conflicting_duplicates[selector],
            malformed_relevant_record_count=malformed_relevant[selector],
            raw_bound_exceeded=raw_bound_exceeded,
            authority_fingerprint=authority_fingerprint,
            anomaly_fingerprint=anomaly_fingerprint,
        )
    complete = bool(
        ordered_selectors
        and all(selector_complete.values())
        and not truncated
    )
    return LabelMembershipEvidence(
        memberships=retained,
        fingerprints=fingerprints,
        truncated=tuple(sorted(truncated)),
        lookup_resolutions=label_lookup_resolutions,
        selector_complete=selector_complete,
        selector_authority=selector_authority,
        complete=complete,
    )
