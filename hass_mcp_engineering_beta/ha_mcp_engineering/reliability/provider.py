"""Bounded direct evidence collection behind the reliability provider interface."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from typing import Any

from ..dependency.extraction import extract_document, resolve_blueprint_roles
from ..dependency.provider import _read_blueprint
from ..errors import AutomationNotFoundError, EntityNotFoundError
from ..observability import METRICS
from ..providers import (
    EngineeringEvidenceProvider,
    EvidenceRequest,
    ProviderCapability,
    ProviderCompleteness,
    ProviderCoverage,
    ProviderError,
    ProviderFailureCategory,
    ProviderResult,
)
from ..sanitization import sanitize_untrusted_data
from .models import ReliabilityEvidenceBundle, ReliabilitySourceCoverage


MAX_REFERENCED_ENTITIES = 100
MAX_SYSTEM_LOG_MATCHES = 20
MAX_TRACE_ERROR_CHARS = 500


class DirectHaReliabilityProvider(EngineeringEvidenceProvider):
    """Engineering orchestrator for explicitly approved read-only HA sources."""

    provider_id = "engineering"
    capabilities = frozenset({ProviderCapability.RELIABILITY_ANALYSIS})

    def __init__(
        self,
        rest_client,
        websocket_client,
        *,
        secret: str = "",
        ha_token: str = "",
        concurrency: int = 5,
    ):
        self.rest_client = rest_client
        self.websocket_client = websocket_client
        self.secret = secret
        self.ha_token = ha_token
        self.concurrency = max(1, min(int(concurrency), 10))

    @property
    def available(self) -> bool:
        return True

    async def fetch(self, request: EvidenceRequest) -> ProviderResult:
        started = time.perf_counter()
        query = request.query
        try:
            bundle = await self.collect(
                automation_id=str(query["automation_id"]),
                lookback_hours=int(query["lookback_hours"]),
                trace_limit=int(query["trace_limit"]),
            )
        except AutomationNotFoundError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.TIMEOUT, "Reliability evidence collection timed out.", True),
                coverage=ProviderCoverage(1, 0, ("reliability_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            return ProviderResult(
                provider_id=self.provider_id,
                capability=request.capability,
                completeness=ProviderCompleteness.FAILED,
                failure=ProviderError(ProviderFailureCategory.UPSTREAM_ERROR, "Reliability evidence collection failed.", True),
                coverage=ProviderCoverage(1, 0, ("reliability_evidence",)),
                timing_ms=(time.perf_counter() - started) * 1000,
            )

        partial = bundle.partial
        completeness = ProviderCompleteness.PARTIAL if partial else ProviderCompleteness.COMPLETE
        completed = sum(item.completeness in {"complete", "not_requested"} for item in bundle.coverage)
        missing = tuple(item.source_type for item in bundle.coverage if item.completeness not in {"complete", "not_requested"})
        warnings = [warning for item in bundle.coverage for warning in item.warnings][:20]
        return ProviderResult(
            provider_id=self.provider_id,
            capability=request.capability,
            completeness=completeness,
            warnings=warnings,
            timing_ms=(time.perf_counter() - started) * 1000,
            coverage=ProviderCoverage(len(bundle.coverage), completed, missing),
            metadata={"source_count": len(bundle.coverage), "direct_fallback": False},
            data=bundle,
        )

    async def collect(self, *, automation_id: str, lookback_hours: int, trace_limit: int) -> ReliabilityEvidenceBundle:
        coverage: list[ReliabilitySourceCoverage] = []

        config_started = time.perf_counter()
        try:
            configuration = await self.rest_client.request(
                "GET", f"/config/automation/config/{automation_id}"
            )
            if not isinstance(configuration, dict):
                raise TypeError("automation configuration response is invalid")
            self._coverage(coverage, "automation_config", ProviderCapability.AUTOMATION_CONFIG, "complete", 1, 0, config_started)
        except AutomationNotFoundError:
            self._coverage(coverage, "automation_config", ProviderCapability.AUTOMATION_CONFIG, "failed", 0, 1, config_started)
            raise
        except Exception:
            self._coverage(
                coverage,
                "automation_config",
                ProviderCapability.AUTOMATION_CONFIG,
                "failed",
                0,
                1,
                config_started,
                warnings=["Automation configuration evidence was unavailable."],
            )
            raise

        state_started = time.perf_counter()
        automation = {"id": automation_id, "entity_id": None, "friendly_name": configuration.get("alias"), "state": None, "last_triggered": None, "disabled": False}
        registry_values: list[dict[str, Any]] = []
        registry_failed = False
        try:
            raw_registry = await self.websocket_client.command(
                {"type": "config/entity_registry/list"}
            )
            if not isinstance(raw_registry, list) or any(
                not isinstance(item, dict) for item in raw_registry
            ):
                raise TypeError("entity registry response is invalid")
            registry_values = raw_registry
            registry_match = next(
                (
                    item
                    for item in registry_values
                    if str(item.get("entity_id", "")).startswith("automation.")
                    and str(item.get("unique_id") or item.get("id") or "") == automation_id
                ),
                None,
            )
            matched = None
            if registry_match:
                automation["entity_id"] = registry_match.get("entity_id")
                automation["disabled"] = bool(registry_match.get("disabled_by"))
                try:
                    matched = await self.rest_client.request(
                        "GET", f"/states/{registry_match.get('entity_id')}"
                    )
                except EntityNotFoundError:
                    matched = None
            if isinstance(matched, dict):
                attrs = matched.get("attributes") if isinstance(matched.get("attributes"), dict) else {}
                automation.update(
                    {
                        "entity_id": matched.get("entity_id"),
                        "friendly_name": attrs.get("friendly_name") or configuration.get("alias"),
                        "state": matched.get("state"),
                        "last_triggered": attrs.get("last_triggered"),
                    }
                )
            resolved = bool(registry_match and (matched or automation["disabled"]))
            self._coverage(
                coverage, "automation_state", ProviderCapability.AUTOMATION_LIST,
                "complete" if resolved else "partial", 1 if resolved else 0, 0 if resolved else 1, state_started,
                warnings=[] if resolved else ["Automation state could not be matched to the internal ID; configuration analysis continued."],
            )
        except Exception:
            registry_failed = True
            self._coverage(
                coverage, "automation_state", ProviderCapability.AUTOMATION_LIST, "unavailable", 0, 1, state_started,
                warnings=["Automation state was unavailable; configuration analysis continued."],
            )

        safe_config = sanitize_untrusted_data(
            configuration, known_secrets=(self.secret, self.ha_token), max_string=2_000
        )
        safe_identity = sanitize_untrusted_data(
            automation, known_secrets=(self.secret, self.ha_token), max_string=256
        )
        if isinstance(safe_identity.value, dict):
            automation = safe_identity.value
        configuration_fingerprint = hashlib.sha256(
            json.dumps(safe_config.value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

        extracted, dynamic = extract_document(
            source_type="automation",
            source_id=automation_id,
            source_entity_id=automation.get("entity_id"),
            source_name=automation.get("friendly_name"),
            source_state=automation.get("state"),
            config=configuration,
            secret=self.secret,
        )

        blueprint_path = None
        blueprint = None
        blueprint_value = configuration.get("use_blueprint")
        if isinstance(blueprint_value, dict):
            blueprint_path = blueprint_value.get("path") if isinstance(blueprint_value.get("path"), str) else None
        blueprint_started = time.perf_counter()
        if blueprint_path:
            blueprint = _read_blueprint(blueprint_path)
            if blueprint is not None:
                extracted.extend(resolve_blueprint_roles(extracted, blueprint, source_id=automation_id))
            self._coverage(
                coverage, "blueprint_source", ProviderCapability.BLUEPRINT_SOURCE,
                "complete" if blueprint is not None else "partial", 1 if blueprint is not None else 0,
                0 if blueprint is not None else 1, blueprint_started,
                warnings=[] if blueprint is not None else ["Blueprint source could not be read; analysis is partial."],
            )
        else:
            self._coverage(
                coverage, "blueprint_source", ProviderCapability.BLUEPRINT_SOURCE, "not_requested", 0, 0, blueprint_started,
            )

        reference_records, reference_coverage = await self._collect_references(
            extracted, registry_values, registry_failed
        )
        coverage.extend(reference_coverage)

        traces, trace_coverage = await self._collect_traces(automation_id, lookback_hours, trace_limit)
        coverage.append(trace_coverage)

        logs, log_coverage = await self._collect_system_log(
            automation, automation_id, reference_records, traces
        )
        coverage.append(log_coverage)
        self._coverage(
            coverage, "logbook_history", ProviderCapability.LOGBOOK_READ, "not_requested", 0, 0, time.perf_counter(),
            warnings=["Logbook/history was not required by a deterministic Beta 12 rule."],
        )

        return ReliabilityEvidenceBundle(
            automation_id=automation_id,
            automation=automation,
            configuration=configuration,
            configuration_fingerprint=configuration_fingerprint,
            blueprint=blueprint,
            blueprint_path=blueprint_path,
            references=reference_records,
            dynamic_references=[
                {"evidence_id": item.evidence_id, "config_path": item.config_path, "warning": item.warning}
                for item in dynamic[:100]
            ],
            traces=traces,
            system_log_entries=logs,
            coverage=coverage,
        )

    async def _collect_references(self, extracted, registry_values, registry_failed):
        started = time.perf_counter()
        by_entity: dict[str, list[Any]] = {}
        for item in extracted:
            by_entity.setdefault(item.target_entity_id, []).append(item)
        truncated = len(by_entity) > MAX_REFERENCED_ENTITIES
        selected = sorted(by_entity)[:MAX_REFERENCED_ENTITIES]
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch(entity_id):
            try:
                async with semaphore:
                    state = await self.rest_client.request("GET", f"/states/{entity_id}")
                value = str(state.get("state", "")).lower() if isinstance(state, dict) else ""
                status = "unavailable" if value == "unavailable" else "unknown" if value == "unknown" else "available"
                return entity_id, status
            except EntityNotFoundError:
                return entity_id, "missing"
            except Exception:
                return entity_id, "lookup_failed"

        states = dict(await asyncio.gather(*(fetch(entity_id) for entity_id in selected)))
        failed = sum(value == "lookup_failed" for value in states.values())

        registry_started = time.perf_counter()
        registry = {
            str(item.get("entity_id", "")).lower(): item
            for item in registry_values
            if isinstance(item, dict) and str(item.get("entity_id", "")).lower() in selected
        }

        records = []
        for entity_id in selected:
            references = by_entity[entity_id]
            registry_item = registry.get(entity_id, {})
            for reference in references:
                records.append(
                    {
                        "entity_id": entity_id,
                        "status": states.get(entity_id, "lookup_failed"),
                        "registry_disabled": bool(registry_item.get("disabled_by")),
                        "registry_hidden": bool(registry_item.get("hidden_by")),
                        "config_path": reference.config_path,
                        "relation": reference.relation,
                    }
                )

        state_completeness = "partial" if failed or truncated else "complete"
        state_coverage = ReliabilitySourceCoverage(
            "entity_state", "direct_ha_api", ProviderCapability.CURRENT_ENTITY_STATE.value,
            state_completeness, len(selected) - failed, failed,
            (time.perf_counter() - started) * 1000, truncated,
            ([f"Referenced entities were capped at {MAX_REFERENCED_ENTITIES}."] if truncated else [])
            + ([f"{failed} referenced entity lookup(s) failed independently."] if failed else []),
        )
        self._record_source(state_coverage)
        registry_coverage = ReliabilitySourceCoverage(
            "entity_registry", "direct_ha_api", ProviderCapability.ENTITY_REGISTRY_READ.value,
            "unavailable" if registry_failed else "complete", len(registry), 1 if registry_failed else 0,
            (time.perf_counter() - registry_started) * 1000, False,
            ["Entity registry evidence was unavailable; disabled status could not be assessed."] if registry_failed else [],
        )
        self._record_source(registry_coverage)
        return records, [state_coverage, registry_coverage]

    async def _collect_traces(self, automation_id, lookback_hours, trace_limit):
        started = time.perf_counter()
        try:
            listing = await self.websocket_client.command(
                {"type": "trace/list", "domain": "automation", "item_id": automation_id}
            )
            if not isinstance(listing, list):
                raise TypeError("trace list response is invalid")
            cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            eligible = [item for item in listing if isinstance(item, dict) and _within_lookback(item.get("timestamp"), cutoff)]
            selected = eligible[:trace_limit]
            truncated = len(eligible) > trace_limit
            semaphore = asyncio.Semaphore(self.concurrency)

            async def fetch(item):
                run_id = item.get("run_id")
                if not run_id:
                    return _normalize_trace(item, None), True
                try:
                    async with semaphore:
                        detail = await self.websocket_client.command(
                            {"type": "trace/get", "domain": "automation", "item_id": automation_id, "run_id": run_id}
                        )
                    return _normalize_trace(item, detail), False
                except Exception:
                    return _normalize_trace(item, None), True

            values = await asyncio.gather(*(fetch(item) for item in selected))
            traces = [item for item, _failed in values]
            failed = sum(failed for _item, failed in values)
            completeness = "partial" if failed or truncated else "complete"
            coverage = ReliabilitySourceCoverage(
                "automation_traces", "direct_ha_api", ProviderCapability.AUTOMATION_TRACE.value,
                completeness, len(traces), failed, (time.perf_counter() - started) * 1000, truncated,
                (["Trace evidence reached the requested trace_limit."] if truncated else [])
                + ([f"{failed} trace detail request(s) failed; summary evidence was retained."] if failed else []),
            )
            self._record_source(coverage)
            return traces, coverage
        except Exception:
            coverage = ReliabilitySourceCoverage(
                "automation_traces", "direct_ha_api", ProviderCapability.AUTOMATION_TRACE.value,
                "unavailable", 0, 1, (time.perf_counter() - started) * 1000, False,
                ["Recent automation traces were unavailable."],
            )
            self._record_source(coverage)
            return [], coverage

    async def _collect_system_log(self, automation, automation_id, references, traces):
        started = time.perf_counter()
        try:
            result = await self.websocket_client.command({"type": "system_log/list"})
            if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise TypeError("system log response is invalid")
            sanitation = sanitize_untrusted_data(
                result, known_secrets=(self.secret, self.ha_token), max_string=2_048
            )
            safe = sanitation.value
            identifiers = {
                str(automation_id).lower(),
                str(automation.get("entity_id") or "").lower(),
            }
            name = str(automation.get("friendly_name") or "").strip().lower()
            if len(name) >= 4:
                identifiers.add(name)
            # A dependency mention alone is not enough to correlate an error
            # to this automation. Include referenced entities only when the
            # same identifier appears in an explicit trace error.
            failed_trace_text = json.dumps(
                [item.get("error") for item in traces if item.get("error")],
                default=str,
            ).lower()
            identifiers.update(
                str(item.get("entity_id", "")).lower()
                for item in references
                if str(item.get("entity_id", "")).lower() in failed_trace_text
            )
            identifiers.discard("")
            matched = []
            for item in safe:
                encoded = json.dumps(item, sort_keys=True, default=str).lower()
                if not any(identifier in encoded for identifier in identifiers):
                    continue
                messages = item.get("message") or []
                if isinstance(messages, str):
                    messages = [messages]
                summary = " ".join(str(value) for value in list(messages)[:2])[:300]
                identity = str(item.get("hash") or item.get("id") or hashlib.sha256(encoded.encode()).hexdigest()[:16])
                matched.append(
                    {
                        "identity": identity[:128],
                        "timestamp": item.get("timestamp") or item.get("first_occurred") or item.get("last_occurred"),
                        "summary": summary or "Sanitized correlated System Log entry.",
                    }
                )
            truncated = len(matched) > MAX_SYSTEM_LOG_MATCHES
            bounded = matched[:MAX_SYSTEM_LOG_MATCHES]
            completeness = "partial" if truncated or sanitation.failed_closed else "complete"
            warnings = []
            if truncated:
                warnings.append(f"Correlated System Log evidence was capped at {MAX_SYSTEM_LOG_MATCHES} entries.")
            if sanitation.failed_closed:
                warnings.append("One or more System Log fields failed closed during sanitization.")
            coverage = ReliabilitySourceCoverage(
                "system_log", "direct_ha_api", ProviderCapability.ERROR_LOG_READ.value,
                completeness, len(bounded), 1 if sanitation.failed_closed else 0,
                (time.perf_counter() - started) * 1000, truncated, warnings,
            )
            self._record_source(coverage)
            return bounded, coverage
        except Exception:
            coverage = ReliabilitySourceCoverage(
                "system_log", "direct_ha_api", ProviderCapability.ERROR_LOG_READ.value,
                "unavailable", 0, 1, (time.perf_counter() - started) * 1000, False,
                ["Sanitized System Log evidence was unavailable."],
            )
            self._record_source(coverage)
            return [], coverage

    def _coverage(self, values, source_type, capability, completeness, examined, failed, started, *, warnings=None):
        item = ReliabilitySourceCoverage(
            source_type, "direct_ha_api", capability.value, completeness, examined, failed,
            (time.perf_counter() - started) * 1000, False, warnings or [],
        )
        values.append(item)
        if completeness != "not_requested":
            self._record_source(item)

    @staticmethod
    def _record_source(item: ReliabilitySourceCoverage) -> None:
        normalized = "partial" if item.completeness == "partial" else "complete" if item.completeness == "complete" else "failed"
        METRICS.record_provider_result(item.provider, normalized)


def _within_lookback(value: Any, cutoff: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= cutoff
    except (TypeError, ValueError):
        return True


def _normalize_trace(summary: dict[str, Any], detail: Any) -> dict[str, Any]:
    errors: list[tuple[str, str]] = []
    condition_stops: list[str] = []

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            error = value.get("error")
            if error not in (None, "", False):
                errors.append((path, str(error)[:MAX_TRACE_ERROR_CHARS]))
            result = value.get("result")
            if isinstance(result, dict) and result.get("result") is False and "condition" in path.lower():
                condition_stops.append(path[:160])
            for key, item in list(value.items())[:100]:
                if key in {"config", "variables", "context"}:
                    continue
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value[:100]):
                walk(item, f"{path}[{index}]")

    walk(detail)
    summary_error = summary.get("error")
    if summary_error not in (None, "", False):
        errors.insert(0, (str(summary.get("last_step") or "$"), str(summary_error)[:MAX_TRACE_ERROR_CHARS]))
    error_step, error = errors[0] if errors else (None, None)
    last_step = str(summary.get("last_step") or error_step or "")[:160] or None
    return {
        "run_id": str(summary.get("run_id") or "")[:128] or None,
        "timestamp": summary.get("timestamp"),
        "state": str(summary.get("state") or "")[:64] or None,
        "script_execution": str(summary.get("script_execution") or "")[:128] or None,
        "last_step": last_step,
        "failure_step": error_step,
        "error": error,
        "action_error": bool(error_step and any(term in error_step.lower() for term in ("action", "sequence", "service"))),
        "condition_stop_step": condition_stops[0] if condition_stops else (
            last_step if last_step and "condition" in last_step.lower() and not error else None
        ),
    }
