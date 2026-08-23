"""Direct Home Assistant evidence provider for dependency index construction."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable

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
    discharge_resolved_blueprint_source_obligation,
    extract_document_with_obligations,
    make_coverage_failure_obligation,
    resolve_blueprint_roles,
    valid_entity_id,
)
from .models import (
    AutomationReadFailure,
    AutomationActionRiskProfile,
    DependencyScanResult,
    OBLIGATION_LEDGER_MODEL,
    SOURCE_TYPES,
    SourceCoverageItem,
)


MAX_AUTOMATION_SOURCES = 1_000


MAX_LABEL_REGISTRY_ENTRIES = 1_000
MAX_LABEL_MEMBERSHIP = 128
MAX_LITERAL_LABEL_SELECTORS = 256
MAX_ENTITY_LABELS = 64
MAX_BLUEPRINT_PARSE_NODES = 32_768
MAX_BLUEPRINT_RESOLUTION_NODES = 16_384
MAX_BLUEPRINT_RESOLUTION_DEPTH = 64
MAX_BLUEPRINT_SOURCE_BYTES = 1_048_576
MAX_BLUEPRINT_ANALYSIS_SECONDS = 60.0


@dataclass(frozen=True)
class _BlueprintSourceEvidence:
    config: dict[str, Any] | None
    reason_code: str | None
    source_path: str | None
    content_sha256: str | None
    content_bytes: int


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

    def __init__(
        self,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        concurrency: int = 8,
        timeout: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.secret = secret
        self.concurrency = max(1, min(concurrency, 10))
        self.timeout = max(1.0, min(timeout, 120.0))
        self.monotonic = monotonic

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
        try:
            registry = await request(
                "entity_registry_inventory",
                lambda: self.websocket_client.command({"type": "config/entity_registry/list"}),
            )
            if not isinstance(registry, list):
                registry = []
                registry_warning.append("Entity registry returned an invalid response.")
            METRICS.record_provider_result(
                self.provider_id,
                "complete" if not registry_warning else "partial",
                dispatched=True,
            )
        except Exception:
            registry_warning.append("Entity registry could not be read; target metadata is partial.")
            METRICS.record_provider_result(self.provider_id, "failed", dispatched=True)

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
        for entry in registry:
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
        blueprint_source_cache: dict[str, _BlueprintSourceEvidence] = {}
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
            blueprint_source_content_sha256 = None
            blueprint_analysis_deadline = None
            if isinstance(blueprint, dict):
                path = blueprint.get("path")
                blueprint_analysis_deadline = (
                    self.monotonic() + MAX_BLUEPRINT_ANALYSIS_SECONDS
                )
                if isinstance(path, str):
                    source_evidence = blueprint_source_cache.get(path)
                    if source_evidence is None:
                        source_evidence = _read_blueprint_source_with_status(
                            path,
                            analysis_deadline_monotonic=(
                                blueprint_analysis_deadline
                            ),
                            monotonic=self.monotonic,
                        )
                        blueprint_source_cache[path] = source_evidence
                    parsed_blueprint = source_evidence.config
                    blueprint_read_failure = source_evidence.reason_code
                    blueprint_source_content_sha256 = (
                        source_evidence.content_sha256
                    )
                else:
                    blueprint_read_failure = "blueprint_source_unavailable"
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
                    (
                        resolved_blueprint,
                        blueprint_resolution_complete,
                        blueprint_resolution_failure,
                    ) = _resolve_blueprint_inputs_with_status(
                        parsed_blueprint,
                        config,
                        analysis_deadline_monotonic=(
                            blueprint_analysis_deadline
                        ),
                        monotonic=self.monotonic,
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
                                    blueprint_resolution_failure
                                    or "blueprint_input_resolution_limit_exceeded"
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
                            blueprint_source_content_sha256=(
                                blueprint_source_content_sha256 or ""
                            ),
                            source_entity_id=source_entity_id,
                            source_name=attrs.get("friendly_name"),
                            source_state=state.get("state"),
                            secret=self.secret,
                            analysis_deadline_monotonic=(
                                blueprint_analysis_deadline
                            ),
                            monotonic=self.monotonic,
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
                            analysis_deadline_monotonic=(
                                blueprint_analysis_deadline
                            ),
                            monotonic=self.monotonic,
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
                )
            )

        literal_label_selectors = sorted(
            {
                selector
                for item in dynamic
                for selector in item.literal_label_selectors
            },
            key=lambda item: item.encode("utf-8"),
        )
        label_warning: list[str] = []
        if literal_label_selectors:
            if len(literal_label_selectors) > MAX_LITERAL_LABEL_SELECTORS:
                literal_label_selectors = literal_label_selectors[
                    :MAX_LITERAL_LABEL_SELECTORS
                ]
                label_warning.append(
                    "Literal label selectors exceeded the bounded dependency payload."
                )
            label_registry: list[Any] = []
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
                    label_registry = sorted(
                        label_registry,
                        key=lambda item: json.dumps(
                            item, sort_keys=True, default=str
                        ),
                    )[:MAX_LABEL_REGISTRY_ENTRIES]
                    label_warning.append(
                        "Label registry exceeded the bounded dependency payload."
                    )
            except Exception:
                label_warning.append(
                    "Label registry could not be read for dynamic dependency resolution."
                )
            (
                label_memberships,
                label_membership_fingerprints,
                label_membership_truncated,
                membership_complete,
            ) = _build_label_membership_evidence(
                literal_label_selectors,
                entity_registry=registry,
                label_registry=label_registry,
            )
            label_registry_complete = bool(
                membership_complete
                and not registry_warning
                and not label_warning
            )
            registry_warning.extend(label_warning)

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
            label_membership_truncated=(
                label_membership_truncated
            ),
            label_registry_complete=label_registry_complete,
            obligations=obligations,
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        )


class _BlueprintSourceLimitExceeded(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _read_blueprint_source_with_status(
    path: str | None,
    *,
    roots: tuple[Path, ...] | None = None,
    analysis_deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> _BlueprintSourceEvidence:
    if not path or not path.endswith((".yaml", ".yml")):
        return _BlueprintSourceEvidence(
            None, "blueprint_source_unavailable", None, None, 0
        )
    try:
        import yaml
    except ImportError:
        return _BlueprintSourceEvidence(
            None, "blueprint_parser_unavailable", None, None, 0
        )
    source_roots = roots or (
        Path("/homeassistant/blueprints/automation"),
        Path("/config/blueprints/automation"),
    )
    for configured_root in source_roots:
        root = configured_root.resolve()
        candidate = Path(root, path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        try:
            if (
                analysis_deadline_monotonic is not None
                and monotonic() >= analysis_deadline_monotonic
            ):
                raise _BlueprintSourceLimitExceeded(
                    "blueprint_source_analysis_time_limit_exceeded"
                )

            class BlueprintLoader(yaml.SafeLoader):
                def __init__(self, stream):
                    super().__init__(stream)
                    self._blueprint_node_count = 0
                    self._blueprint_node_depth = 0

                def compose_node(self, parent, index):
                    if (
                        analysis_deadline_monotonic is not None
                        and monotonic() >= analysis_deadline_monotonic
                    ):
                        raise _BlueprintSourceLimitExceeded(
                            "blueprint_source_analysis_time_limit_exceeded"
                        )
                    self._blueprint_node_count += 1
                    self._blueprint_node_depth += 1
                    if (
                        self._blueprint_node_count
                        > MAX_BLUEPRINT_PARSE_NODES
                        or self._blueprint_node_depth
                        > MAX_BLUEPRINT_RESOLUTION_DEPTH
                    ):
                        raise _BlueprintSourceLimitExceeded(
                            "blueprint_source_structure_limit_exceeded"
                        )
                    try:
                        return super().compose_node(parent, index)
                    finally:
                        self._blueprint_node_depth -= 1

            BlueprintLoader.add_constructor(
                "!input", lambda loader, node: {"__blueprint_input__": loader.construct_scalar(node)}
            )
            with candidate.open("rb") as handle:
                payload = handle.read(MAX_BLUEPRINT_SOURCE_BYTES + 1)
            if len(payload) > MAX_BLUEPRINT_SOURCE_BYTES:
                return _BlueprintSourceEvidence(
                    None,
                    "blueprint_source_limit_exceeded",
                    path,
                    None,
                    len(payload),
                )
            content_sha256 = hashlib.sha256(payload).hexdigest()
            value = yaml.load(
                payload.decode("utf-8"), Loader=BlueprintLoader
            )
            return _BlueprintSourceEvidence(
                value if isinstance(value, dict) else None,
                None if isinstance(value, dict) else "blueprint_source_invalid",
                path,
                content_sha256,
                len(payload),
            )
        except _BlueprintSourceLimitExceeded as exc:
            return _BlueprintSourceEvidence(
                None, exc.reason_code, path, None, 0
            )
        except Exception:
            return _BlueprintSourceEvidence(
                None, "blueprint_source_unavailable", path, None, 0
            )
    return _BlueprintSourceEvidence(
        None, "blueprint_source_unavailable", None, None, 0
    )


def _read_blueprint_with_status(
    path: str | None,
    *,
    roots: tuple[Path, ...] | None = None,
    analysis_deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any] | None, str | None]:
    evidence = _read_blueprint_source_with_status(
        path,
        roots=roots,
        analysis_deadline_monotonic=analysis_deadline_monotonic,
        monotonic=monotonic,
    )
    return evidence.config, evidence.reason_code


def _read_blueprint(path: str | None) -> dict[str, Any] | None:
    """Compatibility wrapper for other read-only Engineering providers."""

    value, _reason = _read_blueprint_with_status(path)
    return value


def _resolve_blueprint_inputs_with_status(
    blueprint_config: dict[str, Any],
    automation_config: dict[str, Any],
    *,
    analysis_deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], bool, str | None]:
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
    failure_reason: str | None = None

    def resolve(value: Any, depth: int) -> Any:
        nonlocal work_units, complete, failure_reason
        work_units += 1
        if analysis_deadline_monotonic is not None and (
            monotonic() >= analysis_deadline_monotonic
        ):
            complete = False
            failure_reason = (
                failure_reason
                or "blueprint_input_resolution_time_limit_exceeded"
            )
            return {"__blueprint_input__": "resolution_limit"}
        if work_units > MAX_BLUEPRINT_RESOLUTION_NODES or (
            depth > MAX_BLUEPRINT_RESOLUTION_DEPTH
        ):
            complete = False
            failure_reason = (
                failure_reason
                or "blueprint_input_resolution_limit_exceeded"
            )
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
        failure_reason,
    )


def _resolve_blueprint_inputs(
    blueprint_config: dict[str, Any],
    automation_config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Compatibility wrapper retaining the historical two-value contract."""

    resolved, complete, _reason = _resolve_blueprint_inputs_with_status(
        blueprint_config, automation_config
    )
    return resolved, complete


def _build_label_membership_evidence(
    selectors: list[str],
    *,
    entity_registry: list[Any],
    label_registry: list[Any],
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, str],
    tuple[str, ...],
    bool,
]:
    """Resolve exact literal label names/IDs without template execution."""

    complete = True
    label_ids_by_selector: dict[str, set[str]] = {
        selector: set() for selector in selectors
    }
    for item in label_registry:
        if not isinstance(item, dict):
            complete = False
            continue
        label_id = item.get("label_id")
        name = item.get("name")
        if not isinstance(label_id, str) or not label_id:
            complete = False
            continue
        for selector in selectors:
            if selector == label_id or selector == name:
                label_ids_by_selector[selector].add(label_id)

    memberships: dict[str, set[str]] = {
        selector: set() for selector in selectors
    }
    for item in entity_registry:
        if not isinstance(item, dict):
            complete = False
            continue
        entity_id = item.get("entity_id")
        labels = item.get("labels", [])
        if not isinstance(labels, list):
            complete = False
            continue
        if len(labels) > MAX_ENTITY_LABELS:
            complete = False
            continue
        if not isinstance(entity_id, str) or not valid_entity_id(entity_id):
            continue
        exact_labels = {
            label for label in labels if isinstance(label, str)
        }
        if any(not isinstance(label, str) for label in labels):
            complete = False
        for selector, label_ids in label_ids_by_selector.items():
            if exact_labels.intersection(label_ids):
                memberships[selector].add(entity_id)

    retained: dict[str, tuple[str, ...]] = {}
    fingerprints: dict[str, str] = {}
    truncated: list[str] = []
    for selector in selectors:
        ordered = sorted(
            memberships[selector], key=lambda item: item.encode("utf-8")
        )
        encoded = json.dumps(
            ordered, separators=(",", ":"), ensure_ascii=True
        )
        fingerprints[selector] = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
        if len(ordered) > MAX_LABEL_MEMBERSHIP:
            truncated.append(selector)
            retained[selector] = tuple(ordered[:MAX_LABEL_MEMBERSHIP])
        else:
            retained[selector] = tuple(ordered)
    return retained, fingerprints, tuple(sorted(truncated)), complete
