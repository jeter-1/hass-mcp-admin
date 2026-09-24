"""Bounded diagnostic script evidence, isolated from helper execution evidence."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field, replace
import hashlib
import heapq
from itertools import chain
import json
import re
import time
from typing import Any, Callable

from ..sanitization import sanitize_untrusted_data
from .extraction import extract_document_with_obligations
from .models import (
    DependencyFinding, DynamicReference, SourceCoverageItem,
    dynamic_reference_fingerprint,
)

MAX_SCRIPT_SOURCES = 1_000
MAX_SCRIPT_FINDINGS = 10_000
MAX_SCRIPT_DYNAMIC_REFERENCES = 1_000
SCRIPT_SCAN_SECONDS = 60.0
_ENTITY = re.compile(r"script\.[a-z0-9_]{1,128}\Z", re.ASCII)
_KEY = re.compile(r"[a-z0-9_]{1,128}\Z", re.ASCII)
_GAPS = (
    "State/registry discovery does not enumerate every stored or package-defined script.",
    "Direct script-service call graphs and transitive effects are outside diagnostic coverage.",
)
_FAILURES = frozenset({
    "resource_not_found", "authentication_failed", "authorization_failed", "timeout",
    "connection_failed", "invalid_response", "schema_mismatch", "sanitization_failed",
    "response_too_large", "prohibited_delegation", "provider_authority_changed",
})


@dataclass(frozen=True)
class ScriptDiagnostics:
    findings: tuple[DependencyFinding, ...]
    dynamic_references: tuple[DynamicReference, ...]
    coverage: SourceCoverageItem
    fingerprint: str
    authority_current: Callable[[], bool] | None = None
    profile: dict[str, Any] = field(default_factory=dict, compare=False)

    def visible(self) -> ScriptDiagnostics:
        """A retired route cannot lend its cached findings current authority."""
        if self.authority_current is None:
            return self
        try:
            if self.authority_current():
                return self
        except Exception:
            pass
        return ScriptDiagnostics(
            (), (), replace(
                self.coverage, completeness="unavailable", evidence_count=0,
                warnings=[*_GAPS, "Script provider authority changed; cached script evidence is withheld."],
                obligation_ledger_completeness="unavailable",
            ), _digest([self.fingerprint, "provider_authority_changed"]), profile=self.profile,
        )


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def collect_script_diagnostics(
    states: list, registry: list, *, registry_complete: bool,
    reader_factory: Callable | None, secret: str, concurrency: int,
) -> ScriptDiagnostics:
    """Reuse already read inventory; dispatch only independently mapped identities."""
    started = time.monotonic()
    errors: Counter[str] = Counter()
    # Keep the lexically first bounded union without an unbounded identity set.
    selected: set[str] = set()
    inventory_bounded = False
    for rows in (states, registry):
        for row in rows:
            entity = row.get("entity_id") if isinstance(row, dict) else None
            if not isinstance(entity, str) or not entity.startswith("script."):
                continue
            if not _ENTITY.fullmatch(entity):
                errors["invalid_script_identity"] += 1
                continue
            if entity in selected:
                continue
            if len(selected) < MAX_SCRIPT_SOURCES:
                selected.add(entity)
            else:
                inventory_bounded = True
                last = max(selected)
                if entity < last:
                    selected.remove(last)
                    selected.add(entity)
    if inventory_bounded:
        errors["script_inventory_limit_exceeded"] += 1
    identities: dict[str, str | None] = {}
    for row in registry:
        if not isinstance(row, dict) or row.get("entity_id") not in selected:
            continue
        entity = row["entity_id"]
        key = row.get("unique_id")
        valid = (
            registry_complete and row.get("platform") == "script"
            and isinstance(key, str) and _KEY.fullmatch(key)
            and not sanitize_untrusted_data([entity, key], known_secrets=(secret,)).redaction_applied
        )
        if not valid or (entity in identities and identities[entity] != key):
            identities[entity] = None
        elif entity not in identities:
            identities[entity] = key
    # Check selected keys against the entire observed registry, including rows
    # outside the candidate bound. Keep at most one claimant per selected key.
    claimants = {key: entity for entity, key in identities.items() if key is not None}
    ambiguous_keys = set()
    for row in registry:
        if not isinstance(row, dict) or row.get("platform") != "script":
            continue
        key = row.get("unique_id")
        if isinstance(key, str) and key in claimants and row.get("entity_id") != claimants[key]:
            ambiguous_keys.add(key)
    candidates = []
    for entity in sorted(selected):
        key = identities.get(entity)
        if key is None or key in ambiguous_keys:
            errors["script_identity_unavailable_or_ambiguous"] += 1
        else:
            candidates.append((entity, key))

    try:
        reader = reader_factory() if reader_factory is not None else None
    except Exception:
        reader = None
    findings: list[DependencyFinding] = []
    dynamic: list[DynamicReference] = []
    documents: list[tuple[str, str]] = []
    completed: set[str] = set()
    successes = 0
    read_count = 0
    read_time_ms = 0.0
    active_reads = 0
    maximum_reads = 0
    evidence_bounded = False
    if reader is None:
        errors["script_provider_unavailable"] += 1
    else:
        queue = iter(candidates)

        async def worker() -> None:
            nonlocal successes, evidence_bounded, read_count, read_time_ms, active_reads, maximum_reads
            for entity, key in queue:
                if time.monotonic() - started >= SCRIPT_SCAN_SECONDS:
                    return
                if not reader.current():
                    return
                try:
                    read_started = time.monotonic()
                    read_count += 1
                    active_reads += 1
                    maximum_reads = max(maximum_reads, active_reads)
                    try:
                        response = await reader.read(entity)
                    finally:
                        active_reads -= 1
                        read_time_ms += (time.monotonic() - read_started) * 1000
                    completed.add(entity)
                    if not isinstance(response, dict) or response.get("success") is not True:
                        category = response.get("details", {}).get("failure_category") if isinstance(response, dict) and isinstance(response.get("details"), dict) else None
                        errors[category if category in _FAILURES else "script_provider_error"] += 1
                        continue
                    metadata = response.get("metadata")
                    data = response.get("data")
                    if (not isinstance(metadata, dict) or metadata.get("provider") != "upstream_read_gateway"
                            or metadata.get("completeness") != "complete"
                            or metadata.get("fallback_occurred") is not False
                            or not isinstance(data, dict) or data.get("success") is not True
                            or data.get("script_id") != key or not isinstance(data.get("config"), dict)):
                        errors["script_response_incomplete_or_identity_mismatch"] += 1
                        continue
                    config = data["config"]
                    # Blueprint input evidence is useful; its unexpanded body is
                    # never silently represented as a complete empty sequence.
                    if not isinstance(config.get("sequence"), list) and not isinstance(config.get("use_blueprint"), dict):
                        errors["script_configuration_invalid"] += 1
                        continue
                    extracted, unresolved, obligations = extract_document_with_obligations(
                        source_type="script", source_id=key, source_entity_id=entity,
                        config=config, secret=secret,
                    )
                    documents.append((key, _digest(config)))
                    successes += 1
                    if any(item.outcome == "coverage_failure" for item in obligations):
                        errors["script_extraction_coverage_failure"] += 1
                    if "use_blueprint" in config:
                        errors["script_blueprint_body_unresolved"] += 1
                    evidence_bounded |= len(findings) + len(extracted) > MAX_SCRIPT_FINDINGS
                    evidence_bounded |= len(dynamic) + len(unresolved) > MAX_SCRIPT_DYNAMIC_REFERENCES
                    findings[:] = heapq.nsmallest(
                        MAX_SCRIPT_FINDINGS, chain(findings, extracted), key=lambda item: item.evidence_id,
                    )
                    dynamic[:] = heapq.nsmallest(
                        MAX_SCRIPT_DYNAMIC_REFERENCES, chain(dynamic, unresolved), key=dynamic_reference_fingerprint,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    completed.add(entity)
                    errors["script_provider_or_parse_failure"] += 1

        workers = [asyncio.create_task(worker()) for _ in range(min(len(candidates), max(1, min(8, concurrency))))]
        try:
            async with asyncio.timeout(max(0.001, SCRIPT_SCAN_SECONDS - (time.monotonic() - started))):
                await asyncio.gather(*workers)
        except TimeoutError:
            errors["script_scan_timeout"] += 1
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        if len(completed) < len(candidates):
            errors["script_candidates_not_read"] += len(candidates) - len(completed)
    if not registry_complete:
        errors["script_registry_incomplete"] += 1
    if evidence_bounded:
        errors["script_diagnostic_evidence_limit_exceeded"] += 1
    findings.sort(key=lambda item: item.evidence_id)
    dynamic.sort(key=dynamic_reference_fingerprint)
    coverage = SourceCoverageItem(
        "script", "upstream_read_gateway" if reader else "none", "script_configuration",
        "partial" if successes or (reader and not candidates and not errors) else "unavailable",
        evidence_count=len(findings), failed_item_count=sum(errors.values()),
        warnings=[*_GAPS, *(f"{key}: {count}" for key, count in sorted(errors.items())),
                  *(["Inventory was bounded; the exact omitted script count is unknown."] if inventory_bounded else []),
                  "Failure counts include source-level gaps and per-document failures; they are not unique script counts."],
        duration_ms=(time.monotonic() - started) * 1000,
        policy=reader.provenance if reader else "admitted script reader unavailable; no fallback",
        obligation_ledger_completeness="partial" if successes else "unavailable",
        obligation_ledger_failed_item_count=sum(errors.values()),
    )
    result = ScriptDiagnostics(
        tuple(findings), tuple(dynamic), coverage,
        _digest([sorted(documents), sorted(errors.items()), coverage.policy,
                 [item.evidence_id for item in findings],
                 [dynamic_reference_fingerprint(item) for item in dynamic]]),
        reader.current if reader else None,
        {"read_attempts": read_count, "successful_documents": successes,
         "read_time_ms": round(read_time_ms, 3), "maximum_concurrent_reads": maximum_reads},
    )
    return result.visible()
