"""Bounded internal evidence provider for handoff generation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

from ..capabilities import build_capability_catalog
from ..dependency.extraction import valid_entity_id
from ..incident.models import IncidentSourceCoverage
from ..observability import METRICS
from ..providers import (
    EngineeringEvidenceProvider, EvidenceRequest, ProviderCapability,
    ProviderCompleteness, ProviderCoverage, ProviderError,
    ProviderFailureCategory, ProviderResult,
)
from ..sanitization import sanitize_untrusted_data
from ..version import SERVER_ID, SERVER_NAME, SERVER_VERSION
from .models import HandoffEvidenceBundle, HandoffEvidenceReference, HandoffItem, stable_id

MAX_ITEMS = 500
MAX_EVIDENCE = 500
MAX_CONCURRENT_HA_REQUESTS = 5


class EngineeringHandoffProvider(EngineeringEvidenceProvider):
    provider_id = "engineering"
    capabilities = frozenset({ProviderCapability.HANDOFF_GENERATION})

    @property
    def available(self) -> bool:
        return True

    def active_index_identity(self) -> dict[str, object]:
        return self.index.active_identity()

    def __init__(self, *, governance, incident, dependency_index, rest_client, health, secret="", ha_token=""):
        self.governance = governance
        self.incident = incident
        self.index = dependency_index
        self.rest_client = rest_client
        self.health = health
        self.secret = secret
        self.ha_token = ha_token

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        if request.capability != ProviderCapability.HANDOFF_GENERATION:
            return ProviderResult(
                self.provider_id, request.capability, ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.UNSUPPORTED, "Unsupported provider capability."),
                coverage=ProviderCoverage(1, 0, (request.capability.value,)),
            )
        try:
            bundle = await self._collect(request.query)
        except (asyncio.TimeoutError, TimeoutError):
            return ProviderResult(
                self.provider_id, request.capability, ProviderCompleteness.FAILED,
                timing_ms=(time.perf_counter() - started) * 1000,
                failure=ProviderError(ProviderFailureCategory.TIMEOUT, "Handoff evidence collection timed out.", True),
                coverage=ProviderCoverage(1, 0, ("handoff_evidence",)),
            )
        except Exception:
            return ProviderResult(
                self.provider_id, request.capability, ProviderCompleteness.FAILED,
                timing_ms=(time.perf_counter() - started) * 1000,
                failure=ProviderError(ProviderFailureCategory.UPSTREAM_ERROR, "Handoff evidence collection failed."),
                coverage=ProviderCoverage(1, 0, ("handoff_evidence",)),
            )
        completeness = ProviderCompleteness.PARTIAL if bundle.source_partial else ProviderCompleteness.COMPLETE
        return ProviderResult(
            self.provider_id, request.capability, completeness,
            warnings=_coverage_warnings(bundle.coverage),
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(bundle.coverage), sum(item.assessment_complete for item in bundle.coverage)),
            data=bundle,
        )

    async def _collect(self, query: dict[str, Any]) -> HandoffEvidenceBundle:
        started = time.perf_counter()
        evidence: dict[str, HandoffEvidenceReference] = {}
        items: list[HandoffItem] = []
        coverage: list[IncidentSourceCoverage] = []
        payloads: dict[str, Any] = {}

        identity_ref = _reference(
            evidence, "server_identity", SERVER_ID,
            f"{SERVER_NAME} is running version {SERVER_VERSION}.",
        )
        items.append(_item(
            "current_state", "fact", "Engineering server identity",
            f"{SERVER_NAME} reports version {SERVER_VERSION}.", "current", "info",
            refs=(identity_ref,), key=SERVER_VERSION,
        ))
        coverage.append(_complete("server_identity", "engineering", "handoff_generation", 1, cached=True, upstream=False))

        catalog = build_capability_catalog()
        payloads["capability_catalog"] = {
            "registered_count": catalog["registered_count"],
            "canonical_count": catalog["count"],
            "planned_count": len(catalog["planned"]),
        }
        catalog_ref = _reference(
            evidence, "capability_catalog", "registered_tools",
            f"The catalog reports {catalog['registered_count']} registered tools, {catalog['count']} canonical tools, and {len(catalog['planned'])} planned capabilities.",
        )
        items.append(_item(
            "current_state", "fact", "Capability catalog",
            evidence[catalog_ref].summary, "current", "info", refs=(catalog_ref,),
            key=(catalog["registered_count"], len(catalog["planned"])),
        ))
        coverage.append(_complete("capability_catalog", "engineering", "handoff_generation", 1, cached=True, upstream=False))

        if query["include_runtime_health"]:
            try:
                health = self.health.snapshot({"status": "not_checked", "checked": False})
                payloads["server_health"] = {
                    "configuration_valid": bool(health.get("configuration", {}).get("valid")),
                    "governance": health.get("governance", {}),
                    "recent_error_counts": health.get("recent_error_counts", {}),
                    "retry_count": health.get("retry_count", 0),
                    "timeout_count": health.get("timeout_count", 0),
                }
                ref = _reference(evidence, "server_health", "runtime", "Runtime health and observability were captured without an active Home Assistant probe.")
                items.append(_item("current_state", "fact", "Runtime health snapshot", evidence[ref].summary, "current", "info", refs=(ref,), key="runtime"))
                coverage.append(_complete("server_health", "engineering", "audit", 1, cached=True, upstream=False))
            except Exception:
                coverage.append(_failed("server_health", "engineering", "audit", upstream=False))
        else:
            coverage.append(_not_requested("server_health", "audit"))

        if query["include_governance_context"]:
            plans, plan_coverage = self._governance_plans(query)
            coverage.append(plan_coverage)
            coverage.append(_complete("change_verification", "engineering", "exact_verification", len(plans), cached=True, upstream=False))
            payloads["governance_plans"] = plans
            for plan in plans:
                items.append(self._plan_item(plan, evidence))
            found_ids = {plan.plan_id for plan in plans}
            for plan_id in query["change_plan_ids"]:
                if plan_id in found_ids:
                    continue
                items.append(_item(
                    "known_limitations", "limitation", "Requested change plan was not found",
                    f"No persisted governance record was available for requested plan {plan_id}.",
                    "unknown", "medium", confidence="insufficient", plans=(plan_id,),
                    limitations=("requested_plan_not_found",), manual=True, key=("missing-plan", plan_id),
                ))
        else:
            coverage.append(_not_requested("governance_plans", "governance_persistence"))
            coverage.append(_not_requested("change_verification", "exact_verification"))

        focus_entities = query["focus_entity_ids"]
        if focus_entities:
            states, state_coverage = await self._states(focus_entities)
            coverage.append(state_coverage)
            payloads["current_states"] = states
            for entity_id in focus_entities:
                state = states.get(entity_id)
                if state is None:
                    continue
                summary = f"{entity_id} currently reports state {str(state.get('state') or 'unknown')[:128]}."
                ref = _reference(evidence, "current_state", entity_id, summary, timestamp=state.get("last_updated") or state.get("last_changed"))
                items.append(_item("current_state", "fact", f"Current state: {entity_id}", summary, "current", "medium" if state.get("state") == "unavailable" else "info", entities=(entity_id,), refs=(ref,), key=entity_id, timestamp=state.get("last_updated") or state.get("last_changed")))
        else:
            coverage.append(_not_requested("current_state", "current_entity_state"))

        automation_ids = query["automation_ids"]
        if automation_ids:
            configs, config_coverage = await self._automation_configs(automation_ids)
            coverage.append(config_coverage)
            payloads["automation_configs"] = configs
            for automation_id, config in configs.items():
                name = str(config.get("alias") or config.get("id") or automation_id)[:160]
                summary = f"Automation {automation_id} ({name}) configuration was read for this bounded snapshot."
                ref = _reference(evidence, "automation_config", automation_id, summary)
                items.append(_item("current_state", "fact", f"Automation: {name}", summary, "current", "info", automations=(automation_id,), refs=(ref,), key=automation_id))
        else:
            coverage.append(_not_requested("automation_config", "automation_config"))

        index_requested = bool(query["include_dependency_context"] or query["include_integrity_context"])
        index_snapshot = None
        rebuilt = False
        lookup_ms = 0.0
        if index_requested:
            try:
                index_snapshot, rebuilt, lookup_ms = await self.index.get(refresh=query["refresh_index"])
                requested_rows = [row for row in index_snapshot.coverage if row.completeness != "not_requested"]
                failed = sum(max(0, int(row.failed_item_count)) for row in requested_rows)
                unsupported = sorted(row.source_type for row in requested_rows if row.provider == "none" and row.completeness in {"unavailable", "unsupported", "not_supported"})
                completeness = "partial" if failed or unsupported else "complete"
                limitations = ["dependency_index_unsupported_source_types"] if unsupported else []
                coverage.append(IncidentSourceCoverage(
                    "dependency_index", "engineering", "dependency_analysis", completeness,
                    True, True, len(index_snapshot.findings), failed,
                    [str(w)[:240] for row in requested_rows for w in row.warnings][:10],
                    lookup_ms, not rebuilt, "item_read_failure" if failed else None,
                    True, limitations,
                ))
                scoped = [
                    finding for finding in index_snapshot.findings
                    if finding.target_entity_id in set(focus_entities)
                    or finding.source_id in set(automation_ids)
                ][:200]
                for finding in scoped:
                    summary = f"{finding.source_type} {finding.source_id} has an exact dependency on {finding.target_entity_id}."
                    ref = _reference(evidence, "dependency_index", finding.source_id, summary, coverage_status=completeness)
                    items.append(_item("confirmed_findings", "fact", "Static dependency relationship", summary, "current", "low", entities=(finding.target_entity_id,), automations=(finding.source_id,) if finding.source_type == "automation" else (), refs=(ref,), limitations=limitations, key=(finding.source_type, finding.source_id, finding.target_entity_id)))
                for dynamic in index_snapshot.dynamic_references:
                    if dynamic.source_id not in set(automation_ids):
                        continue
                    summary = f"{dynamic.source_type} {dynamic.source_id} contains a dynamic entity selection that cannot be resolved statically."
                    ref = _reference(evidence, "dependency_index", dynamic.source_id, summary, confidence="low", coverage_status=completeness)
                    items.append(_item("open_questions", "limitation", "Unresolved dynamic entity selection", summary, "open", "medium", confidence="low", automations=(dynamic.source_id,), refs=(ref,), limitations=("dynamic_reference_target_unresolved",), manual=True, key=(dynamic.source_id, dynamic.configuration_path)))
            except Exception:
                METRICS.record_provider_result("engineering", "failed", dispatched=True)
                coverage.append(_failed("dependency_index", "engineering", "dependency_analysis", upstream=True))
        else:
            coverage.append(_not_requested("dependency_index", "dependency_analysis"))

        if query["include_integrity_context"]:
            if index_snapshot is None:
                coverage.append(IncidentSourceCoverage(
                    "configuration_integrity", "engineering", "configuration_integrity_analysis",
                    "partial", True, False, 0, 0,
                    ["Scoped integrity context was unavailable because its dependency evidence was unavailable."],
                    0.0, False, None, False, ["dependency_index_context_unavailable"],
                ))
            else:
                coverage.append(IncidentSourceCoverage(
                    "configuration_integrity", "engineering", "configuration_integrity_analysis",
                    "partial", True, False, 0, 0,
                    ["Handoff integrity context is limited to scoped dependency evidence; no unbounded global audit is performed."],
                    0.0, True, None, False, ["bounded_handoff_integrity_scope"],
                ))
        else:
            coverage.append(_not_requested("configuration_integrity", "configuration_integrity_analysis"))

        if query["include_reliability_context"] and automation_ids:
            coverage.append(IncidentSourceCoverage("automation_reliability", "engineering", "reliability_analysis", "partial", True, False, 0, 0, ["Reliability context is summarized through incident evidence when requested."], 0.0, True, None, False, ["bounded_handoff_context"] ))
        else:
            coverage.append(_not_requested("automation_reliability", "reliability_analysis"))

        if query["include_incident_context"] and (focus_entities or automation_ids):
            incident_items, incident_evidence, incident_coverage, incident_payload = await self._incident_context(
                query, dependency_snapshot=index_snapshot
            )
            items.extend(incident_items)
            evidence.update(incident_evidence)
            coverage.extend(incident_coverage)
            payloads["incident"] = incident_payload
        else:
            coverage.append(_not_requested("incident_correlation", "incident_correlation"))
            for source, capability in (
                ("entity_registry", "entity_registry_read"),
                ("entity_history", "history_read"),
                ("logbook", "logbook_read"),
                ("automation_traces", "automation_trace"),
                ("system_log", "error_log_read"),
            ):
                coverage.append(_not_requested(source, capability))

        if query["include_recommendations"]:
            open_items = [item for item in items if item.status in {"open", "pending", "blocked", "failed", "unknown"}]
            if open_items:
                refs = tuple(dict.fromkeys(ref for item in open_items for ref in item.supporting_evidence_reference_ids))[:20]
                items.append(_item(
                    "recommended_next_steps", "recommendation", "Review unresolved evidence and open work",
                    "Review the cited bounded evidence before proposing any configuration or behavioral change.",
                    "open", "medium", confidence="medium", refs=refs, manual=True,
                    requires_authorization=False, authorization_type="none",
                    recommendation_category="read_only_investigation", key="review-open-work",
                ))

        items.append(_item(
            "authorization_boundaries", "limitation", "Documentation is not authorization",
            "This handoff is a read-only snapshot. It does not approve, apply, or authorize any runtime or configuration change.",
            "not_applicable", "info", confidence="confirmed", key="authorization-boundary",
        ))

        items = _dedupe_order(items)
        item_truncated = len(items) > MAX_ITEMS
        items = items[:MAX_ITEMS]
        if len(evidence) > MAX_EVIDENCE:
            allowed = {ref for item in items for ref in (*item.supporting_evidence_reference_ids, *item.contradicting_evidence_reference_ids)}
            evidence = {key: evidence[key] for key in sorted(allowed)[:MAX_EVIDENCE] if key in evidence}
        index_info = {
            "requested": index_requested,
            "generation": index_snapshot.generation if index_snapshot is not None else None,
            "fingerprint": index_snapshot.fingerprint if index_snapshot is not None else None,
            "built_at": index_snapshot.built_at if index_snapshot is not None else None,
            "cache_hit": bool(index_snapshot is not None and not rebuilt),
            "refreshed": bool(index_snapshot is not None and rebuilt and query["refresh_index"]),
            "lookup_duration_ms": round(lookup_ms, 3),
            "current_index_build_duration_ms": round(index_snapshot.build_duration_ms if index_snapshot is not None and rebuilt else 0.0, 3),
            "original_index_build_duration_ms": round(index_snapshot.build_duration_ms if index_snapshot is not None else 0.0, 3),
        }
        automation_entity_ids = []
        for automation_id in automation_ids:
            entity_id = str(
                payloads.get("automation_configs", {}).get(automation_id, {}).get("entity_id") or ""
            )
            if valid_entity_id(entity_id) and entity_id.startswith("automation.") and entity_id not in automation_entity_ids:
                automation_entity_ids.append(entity_id)
        incident_entity_id = str(payloads.get("incident", {}).get("automation_entity_id") or "")
        if (
            valid_entity_id(incident_entity_id)
            and incident_entity_id.startswith("automation.")
            and incident_entity_id not in automation_entity_ids
        ):
            automation_entity_ids.append(incident_entity_id)

        scope = {
            "focus_entity_ids": focus_entities,
            "automation_ids": automation_ids,
            "automation_entity_ids": automation_entity_ids,
            "change_plan_ids": query["change_plan_ids"],
            "lookback_hours": query["lookback_hours"],
            "contexts_requested": [name for name, enabled in (
                ("runtime_health", query["include_runtime_health"]),
                ("governance", query["include_governance_context"]),
                ("dependency", query["include_dependency_context"]),
                ("integrity", query["include_integrity_context"]),
                ("reliability", query["include_reliability_context"]),
                ("incident", query["include_incident_context"]),
            ) if enabled],
        }
        coverage = _normalize_coverage(coverage)
        return HandoffEvidenceBundle(scope, items, evidence, coverage, index_info, payloads, (time.perf_counter() - started) * 1000, item_truncated)

    def _governance_plans(self, query):
        try:
            service = self.governance.require()
            requested_ids = set(query["change_plan_ids"])
            plans = service.repository.list()
            if requested_ids:
                plans = [plan for plan in plans if plan.plan_id in requested_ids]
            else:
                plans = plans[:20]
            return plans, _complete("governance_plans", "engineering", "governance_persistence", len(plans), cached=False, upstream=False)
        except Exception:
            return [], _failed("governance_plans", "engineering", "governance_persistence", upstream=False)

    def _plan_item(self, plan, evidence):
        status = _value(getattr(plan, "status", "unknown"))
        verification_status = str(getattr(getattr(plan, "verification", None), "status", "not_run"))
        verified = status == "applied" and verification_status == "passed"
        historical = status in {
            "draft", "validation_failed", "expired", "superseded", "rolled_back",
            "rejected", "invalidated", "cancelled",
        }
        active_failure = status in {"failed", "verification_failed", "rollback_failed"}
        active_pending = status in {
            "awaiting_approval", "approved", "applying", "applied", "rollback_pending",
        } and not verified

        if verified:
            section, public_status, severity = "completed_work", "verified", "info"
            summary = f"Plan {plan.plan_id} was applied and verification passed."
        elif historical:
            section = "confirmed_findings"
            public_status = "rolled_back" if status == "rolled_back" else "not_applicable"
            severity = "info"
            summary = _historical_plan_summary(plan.plan_id, status)
        elif active_failure:
            section, public_status, severity = "risks", "failed", "high"
            summary = f"Plan {plan.plan_id} has an unresolved current {status.replace('_', ' ')} state."
        elif active_pending:
            section, public_status, severity = "outstanding_work", "pending", "medium"
            summary = f"Plan {plan.plan_id} is active with lifecycle state {status}; it is not verified completed work."
        else:
            section, public_status, severity = "confirmed_findings", "unknown", "low"
            summary = f"Plan {plan.plan_id} has unrecognized lifecycle state {status}; manual classification is required."

        requires_authorization = status in {"awaiting_approval", "rollback_pending"} or active_failure
        authorization_type = "governed_change_plan" if status in {"awaiting_approval", "rollback_pending"} else "manual_review" if active_failure else "none"
        ref = _reference(evidence, "governance_plans", plan.plan_id, summary, timestamp=plan.updated_at)
        return _item(section, "fact", f"Change plan: {plan.title[:120]}", summary, public_status, severity,
                     plans=(plan.plan_id,), refs=(ref,), manual=active_failure,
                     requires_authorization=requires_authorization,
                     authorization_type=authorization_type,
                     key=(plan.plan_id, status, verification_status), timestamp=plan.updated_at)

    async def _states(self, entity_ids):
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_HA_REQUESTS)
        async def one(entity_id):
            try:
                async with semaphore:
                    value = await self.rest_client.request("GET", f"/states/{entity_id}")
                safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=500)
                if safe.failed_closed or not isinstance(safe.value, dict):
                    raise TypeError("invalid state response")
                return entity_id, safe.value, False
            except Exception:
                return entity_id, None, True
        values = await asyncio.gather(*(one(entity_id) for entity_id in entity_ids))
        states = {entity_id: value for entity_id, value, failed in values if not failed and value is not None}
        failures = sum(failed for _, _, failed in values)
        completeness = "complete" if failures == 0 else "partial" if states else "failed"
        return states, IncidentSourceCoverage("current_state", "direct_ha_api", "current_entity_state", completeness, True, True, len(states), failures, [f"{failures} bounded entity state read(s) failed."] if failures else [], 0.0, False, "item_read_failure" if failures and states else "provider_upstream_error" if failures else None, True)

    async def _automation_configs(self, automation_ids):
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_HA_REQUESTS)
        async def one(automation_id):
            try:
                async with semaphore:
                    value = await self.rest_client.request("GET", f"/config/automation/config/{automation_id}")
                safe = sanitize_untrusted_data(value, known_secrets=(self.secret, self.ha_token), max_string=500)
                if safe.failed_closed or not isinstance(safe.value, dict):
                    raise TypeError("invalid automation response")
                return automation_id, safe.value, False
            except Exception:
                return automation_id, None, True
        values = await asyncio.gather(*(one(automation_id) for automation_id in automation_ids))
        configs = {key: dict(value) for key, value, failed in values if not failed and value is not None}
        failures = sum(failed for _, _, failed in values)

        state_inventory_failed = False
        state_by_internal_id = {}
        try:
            raw_states = await self.rest_client.request("GET", "/states")
            if not isinstance(raw_states, list):
                raise TypeError("invalid state inventory response")
            safe_states = sanitize_untrusted_data(
                raw_states, known_secrets=(self.secret, self.ha_token), max_string=500
            )
            if safe_states.failed_closed or not isinstance(safe_states.value, list):
                raise TypeError("invalid sanitized state inventory")
            for state in safe_states.value:
                if not isinstance(state, dict):
                    continue
                entity_id = str(state.get("entity_id") or "")
                attributes = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
                internal_id = str(attributes.get("id") or "")
                if internal_id and valid_entity_id(entity_id) and entity_id.startswith("automation."):
                    state_by_internal_id.setdefault(internal_id, entity_id)
        except Exception:
            state_inventory_failed = True

        unresolved = []
        for automation_id, config in configs.items():
            entity_id = str(config.get("entity_id") or state_by_internal_id.get(automation_id) or "")
            if valid_entity_id(entity_id) and entity_id.startswith("automation."):
                config["entity_id"] = entity_id
            else:
                config.pop("entity_id", None)
                unresolved.append(automation_id)

        actual_failures = failures + int(state_inventory_failed)
        completeness = "failed" if not configs and failures else "partial" if actual_failures or unresolved else "complete"
        warnings = []
        limitations = []
        if failures:
            warnings.append(f"{failures} bounded automation configuration read(s) failed.")
        if state_inventory_failed:
            warnings.append("Automation entity-ID resolution could not read the bounded state inventory.")
        elif unresolved:
            warnings.append(f"{len(unresolved)} automation entity ID(s) could not be resolved from current state evidence.")
            limitations.append("automation_entity_resolution_incomplete")
        failure_category = (
            "provider_upstream_error" if not configs and failures
            else "item_read_failure" if actual_failures
            else None
        )
        return configs, IncidentSourceCoverage(
            "automation_config", "direct_ha_api", "automation_config", completeness,
            True, True, len(configs), actual_failures, warnings, 0.0, False,
            failure_category, True, limitations,
        )

    async def _incident_context(self, query, *, dependency_snapshot=None):
        try:
            result = await self.incident.analyze(
                focus_entity_id=query["focus_entity_ids"][0] if query["focus_entity_ids"] else "",
                automation_id=query["automation_ids"][0] if query["automation_ids"] else "",
                related_entity_ids=query["focus_entity_ids"][1:20],
                lookback_hours=min(query["lookback_hours"], 168),
                correlation_window_minutes=10, trace_limit=10,
                include_dependency_context=query["include_dependency_context"],
                include_integrity_context=query["include_integrity_context"],
                include_reliability_context=query["include_reliability_context"],
                detail_level="standard", limit=100, cursor="", refresh_index=False,
                _dependency_snapshot=dependency_snapshot,
            )
            evidence = {}
            items = []
            for hypothesis in result.data.get("hypotheses", [])[:100]:
                refs = []
                for reference_id in hypothesis.get("supporting_evidence_reference_ids", [])[:20]:
                    ref = stable_id("handoff_ev", "incident", reference_id)
                    evidence[ref] = HandoffEvidenceReference(ref, "incident_correlation", hypothesis.get("hypothesis_id", "incident"), f"Incident evidence reference {reference_id} supports this bounded inference.", str(hypothesis.get("confidence") or "medium"), "partial")
                    refs.append(ref)
                contradictory = []
                for reference_id in hypothesis.get("contradicting_evidence_reference_ids", [])[:20]:
                    ref = stable_id("handoff_ev", "incident_contradiction", reference_id)
                    evidence[ref] = HandoffEvidenceReference(ref, "incident_correlation", hypothesis.get("hypothesis_id", "incident"), f"Incident evidence reference {reference_id} contradicts or limits this inference.", "medium", "partial")
                    contradictory.append(ref)
                items.append(_item(
                    "inferences", "inference", str(hypothesis.get("title") or "Incident hypothesis"),
                    str(hypothesis.get("explanation") or "Bounded incident inference.")[:800],
                    "open", str(hypothesis.get("severity") or "medium"),
                    confidence=str(hypothesis.get("confidence") or "medium"), refs=tuple(refs),
                    contradicting=tuple(contradictory), limitations=tuple(hypothesis.get("coverage_limitations", [])[:10]),
                    manual=True, key=hypothesis.get("hypothesis_id"),
                ))
            coverage = [IncidentSourceCoverage("incident_correlation", "engineering", "incident_correlation", "partial" if result.partial else "complete", True, query["handoff_type"] == "incident", len(items), 0, list(result.warnings)[:10], 0.0, True, None, False, ["incident_context_partial"] if result.partial else [])]
            wanted = {"entity_registry", "entity_history", "logbook", "automation_traces", "system_log"}
            for row in result.data.get("source_coverage_matrix", []):
                if not isinstance(row, dict) or row.get("source_type") not in wanted:
                    continue
                coverage.append(IncidentSourceCoverage(
                    str(row.get("source_type")), str(row.get("provider") or "engineering"),
                    str(row.get("provider_capability") or row.get("source_type")),
                    str(row.get("completeness") or "failed"), bool(row.get("requested")),
                    bool(row.get("required_for_assessment")), int(row.get("items_examined") or 0),
                    int(row.get("failed_items") or 0), list(row.get("warnings") or [])[:10],
                    float(row.get("duration_ms") or 0.0), bool(row.get("cached_provenance")),
                    row.get("failure_category"), bool(row.get("upstream_attempted")),
                    list(row.get("coverage_limitations") or [])[:10],
                ))
            present = {row.source_type for row in coverage}
            for source, capability in (("entity_registry", "entity_registry_read"), ("entity_history", "history_read"), ("logbook", "logbook_read"), ("automation_traces", "automation_trace"), ("system_log", "error_log_read")):
                if source not in present:
                    coverage.append(_not_requested(source, capability))
            focus = result.data.get("focus") if isinstance(result.data.get("focus"), dict) else {}
            return items, evidence, coverage, {
                "final_assessment": result.data.get("final_assessment"),
                "incident_id": result.data.get("incident_id"),
                "automation_entity_id": focus.get("automation_entity_id"),
            }
        except Exception:
            return [], {}, [_failed("incident_correlation", "engineering", "incident_correlation", upstream=True)], {}


def _reference(evidence, source_type, source_object, summary, *, confidence="confirmed", coverage_status="complete", timestamp=None):
    ref = stable_id("handoff_ev", source_type, source_object, summary, timestamp)
    evidence[ref] = HandoffEvidenceReference(ref, source_type, str(source_object)[:160], str(summary)[:500], confidence, coverage_status, timestamp)
    return ref


def _item(section, statement_type, title, summary, status, severity, *, confidence="confirmed", entities=(), automations=(), plans=(), refs=(), contradicting=(), limitations=(), manual=False, requires_authorization=False, authorization_type="none", recommendation_category=None, key=None, timestamp=None):
    return HandoffItem(
        stable_id("handoff_item", section, statement_type, key or title), section, statement_type,
        title, summary, status, severity, confidence, tuple(entities), tuple(automations), tuple(plans),
        tuple(refs), tuple(contradicting), tuple(limitations), manual, requires_authorization,
        authorization_type, recommendation_category, timestamp,
    )


def _complete(source, provider, capability, examined, *, cached, upstream):
    return IncidentSourceCoverage(source, provider, capability, "complete", True, True, examined, 0, [], 0.0, cached, None, upstream)


def _failed(source, provider, capability, *, upstream):
    return IncidentSourceCoverage(source, provider, capability, "failed", True, True, 0, 1, [f"{source.replace('_', ' ').title()} evidence was unavailable."], 0.0, False, "provider_upstream_error", upstream)


def _not_requested(source, capability):
    return IncidentSourceCoverage(source, "none", capability, "not_requested", False, False, 0, 0, [], 0.0, False, None, False)


def _coverage_warnings(coverage):
    return list(dict.fromkeys(str(warning)[:240] for item in coverage for warning in item.warnings if str(warning).strip()))[:20]


def _normalize_coverage(coverage):
    """Return one deterministic effective row for each logical evidence source."""
    normalized = {}
    order = []
    for row in coverage:
        key = str(row.source_type)
        if key not in normalized:
            normalized[key] = row
            order.append(key)
            continue
        normalized[key] = _merge_coverage_rows(normalized[key], row)
    return [normalized[key] for key in order]


def _merge_coverage_rows(left, right):
    same_shared_operation = left.provider_capability == right.provider_capability
    left_usable = left.completeness in {"complete", "partial"} and not left.actual_failure
    right_usable = right.completeness in {"complete", "partial"} and not right.actual_failure

    # A synthetic or repeated failure for the same shared source must not
    # override evidence already acquired from that exact logical operation.
    if same_shared_operation and left_usable != right_usable:
        usable = left if left_usable else right
        failed = right if left_usable else left
        if failed.completeness == "failed":
            return IncidentSourceCoverage(
                usable.source_type, usable.provider, usable.provider_capability,
                usable.completeness, usable.requested, usable.required_for_assessment,
                usable.items_examined, usable.failed_items,
                _bounded_unique(usable.warnings), max(usable.duration_ms, failed.duration_ms),
                usable.cached_provenance or failed.cached_provenance,
                usable.failure_category, usable.upstream_attempted or failed.upstream_attempted,
                _bounded_unique(usable.coverage_limitations, limit=10, length=128),
            )

    precedence = {"failed": 5, "partial": 4, "complete": 3, "not_supported": 2, "not_requested": 1}
    primary = left if precedence.get(left.completeness, 0) >= precedence.get(right.completeness, 0) else right
    secondary = right if primary is left else left
    requested = left.requested or right.requested
    actual_failure = left.actual_failure or right.actual_failure
    completeness = primary.completeness
    if actual_failure and (left_usable or right_usable):
        completeness = "partial"
    provider = primary.provider if primary.provider != "none" else secondary.provider
    capability = primary.provider_capability or secondary.provider_capability
    failure_category = (primary.failure_category or secondary.failure_category) if actual_failure else None
    failed_items = max(left.failed_items, right.failed_items) if same_shared_operation else left.failed_items + right.failed_items
    items_examined = max(left.items_examined, right.items_examined) if same_shared_operation else left.items_examined + right.items_examined
    return IncidentSourceCoverage(
        primary.source_type, provider, capability, completeness, requested,
        left.required_for_assessment or right.required_for_assessment,
        items_examined, failed_items,
        _bounded_unique([*left.warnings, *right.warnings]),
        max(left.duration_ms, right.duration_ms),
        left.cached_provenance or right.cached_provenance,
        failure_category, left.upstream_attempted or right.upstream_attempted,
        _bounded_unique([*left.coverage_limitations, *right.coverage_limitations], limit=10, length=128),
    )


def _bounded_unique(values, *, limit=10, length=240):
    return list(dict.fromkeys(str(value)[:length] for value in values if str(value).strip()))[:limit]


def _value(value):
    return str(getattr(value, "value", value))


def _historical_plan_summary(plan_id, status):
    if status == "expired":
        return f"Plan {plan_id} expired; its authorization and execution window ended and it is retained as history."
    if status == "superseded":
        return f"Plan {plan_id} was superseded and is retained as historical intent, not active pending work."
    if status == "rolled_back":
        return f"Plan {plan_id} was rolled back and is historical, not active completed work."
    if status == "validation_failed":
        return f"Plan {plan_id} recorded a terminal validation failure before application and is retained as history."
    return f"Plan {plan_id} has terminal historical lifecycle state {status}; it is not active pending work."


def _dedupe_order(items):
    section_priority = {name: index for index, name in enumerate(("current_state", "completed_work", "confirmed_findings", "risks", "inferences", "open_questions", "outstanding_work", "recommended_next_steps", "known_limitations", "authorization_boundaries"))}
    severity_priority = {"high": 0, "medium": 1, "low": 2, "info": 3}
    status_priority = {"blocked": 0, "failed": 1, "open": 2, "pending": 3, "unknown": 4, "current": 5, "verified": 6, "completed": 7, "rolled_back": 8, "not_applicable": 9}
    values = {item.item_id: item for item in items}
    return sorted(values.values(), key=lambda item: (section_priority.get(item.section, 99), severity_priority.get(item.severity, 99), status_priority.get(item.status, 99), item.timestamp or "", item.item_id))
