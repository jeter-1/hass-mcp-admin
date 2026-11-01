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
from .script_calls import _refused, extract_script_calls, MAX_CALLS
from .bounded_sources import MAX_SCRIPT_BOUNDED_OBLIGATIONS
from .models import (
    DependencyFinding, DependencyObligation, DynamicReference, SourceCoverageItem,
    dynamic_reference_fingerprint,
)

MAX_SCRIPT_SOURCES = 1_000
MAX_SCRIPT_FINDINGS = 10_000
MAX_SCRIPT_DYNAMIC_REFERENCES = 1_000
SCRIPT_SCAN_SECONDS = 60.0
_ENTITY = re.compile(r"script\.[a-z0-9_]{1,121}\Z", re.ASCII)
_KEY = re.compile(r"[a-z0-9_]{1,128}\Z", re.ASCII)
_GAPS = (
    "State/registry discovery does not enumerate every stored or package-defined script.",
    "Script call paths cover literal supported invocations only; dynamic calls and undiscovered sources remain unknown.",
    "Complete script enumeration beyond the state and registry inventory is deferred (HAMCP-264).",
)
# HAMCP-212: scripts are read from Home Assistant's loaded configuration over
# the provider's shared WebSocket batch; the delegated stored read remains only
# for the reported reasons below.
LOADED_SCRIPT_SOURCE = "loaded_websocket"
STORED_SCRIPT_SOURCE = "stored_delegated"
LOADED_SCRIPT_PROVIDER = "direct_ha_api"
STORED_SCRIPT_PROVIDER = "upstream_read_gateway"
SCRIPT_LOADED_FALLBACK_REASONS = frozenset({
    "blueprint_instance",
    "blueprint_reference_nested",
    "loaded_entity_unavailable",
    "loaded_config_unreadable",
    "pending_write_reload",
    "loaded_source_unavailable",
    "loaded_source_unsupported",
})
# The stored read is the analysis source for these regardless of reload
# timing: the loaded form is substituted, ambiguous or absent.
_STABLE_LOADED_REASONS = frozenset({
    "blueprint_instance",
    "blueprint_reference_nested",
    "loaded_entity_unavailable",
})
# The running configuration could not be read, so a successful stored read
# does not prove what is loaded (LOADED_CONFIG_UNAVAILABLE).
_UNVERIFIED_LOADED_REASONS = frozenset({
    "loaded_config_unreadable",
    "loaded_source_unavailable",
})
LOADED_CONFIG_UNAVAILABLE = "loaded_config_unavailable"
PENDING_WRITE_UNRECONCILED = "pending_write_unreconciled"
_FAILURES = frozenset({
    "resource_not_found", "authentication_failed", "authorization_failed", "timeout",
    "connection_failed", "invalid_response", "schema_mismatch", "sanitization_failed",
    "response_too_large", "prohibited_delegation", "provider_authority_changed",
})


@dataclass(frozen=True)
class ScriptSelection:
    """Independently mapped script identities, before any configuration read."""

    candidates: tuple[tuple[str, str], ...]
    errors: tuple[tuple[str, int], ...]
    inventory_bounded: bool


@dataclass
class LoadedScriptSource:
    """HAMCP-212: loaded-script results from the provider's one batch.

    ``outcomes`` maps an entity to its ``script/config`` and ``search/related``
    results. ``available`` lists the entities with a loaded, available state.
    ``pending`` and ``overflow`` are the provider's script-write journal at
    build start; ``verified`` receives the keys this build proved reloaded.
    """

    outcomes: dict[str, tuple[tuple[bool, Any], tuple[bool, Any]]]
    available: frozenset[str]
    failure: str | None = None
    pending: dict[str, int] = field(default_factory=dict)
    overflow: bool = False
    verified: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ScriptDiagnostics:
    findings: tuple[DependencyFinding, ...]
    dynamic_references: tuple[DynamicReference, ...]
    coverage: SourceCoverageItem
    fingerprint: str
    authority_current: Callable[[], bool] | None = None
    profile: dict[str, Any] = field(default_factory=dict, compare=False)

    call_findings: tuple[DependencyFinding, ...] = ()
    call_gaps: tuple[str, ...] = ()
    # HAMCP-212: the provider each analyzed script's configuration came from.
    source_providers: tuple[tuple[str, str], ...] = ()
    # HAMCP-226: coverage-failure terminals of analysed scripts, so the
    # over-bound scripts can be named (``bounded_sources``).
    bounded_obligations: tuple[DependencyObligation, ...] = ()

    def provider_for(self, script_id: str) -> str:
        return dict(self.source_providers).get(script_id, STORED_SCRIPT_PROVIDER)

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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _comparable_stored(stored: dict, loaded: dict) -> dict:
    """The delegated read adds the registry ``category``; Core's raw config has none."""
    if "category" in stored and "category" not in loaded:
        return {key: value for key, value in stored.items() if key != "category"}
    return stored


def create_script_reader(reader_factory: Callable | None):
    try:
        return reader_factory() if reader_factory is not None else None
    except Exception:
        return None


def loaded_script_route(entity: str, loaded: LoadedScriptSource | None) -> tuple[dict | None, str | None]:
    """Return the loaded config, or the reported reason to read stored."""

    if loaded is None:
        return None, "loaded_source_unsupported"
    if entity not in loaded.available:
        return None, "loaded_entity_unavailable"
    if loaded.failure is not None:
        return None, loaded.failure
    outcome = loaded.outcomes.get(entity)
    if outcome is None:
        return None, "loaded_config_unreadable"
    (config_ok, config_result), (search_ok, search_result) = outcome
    if (
        not config_ok or not isinstance(config_result, dict)
        or not isinstance(config_result.get("config"), dict)
        or not search_ok or not isinstance(search_result, dict)
    ):
        return None, "loaded_config_unreadable"
    blueprints = search_result.get("script_blueprint", [])
    nested = search_result.get("script", [])
    if not isinstance(blueprints, list) or not isinstance(nested, list):
        return None, "loaded_config_unreadable"
    config = config_result["config"]
    if "use_blueprint" in config:
        return None, "blueprint_instance"
    if blueprints:
        # Core's script search recurses into referenced scripts, so with a
        # nested script the blueprint may be the nested script's own.
        return None, "blueprint_reference_nested" if nested else "blueprint_instance"
    return config, None


def select_script_candidates(
    states: list, registry: list, *, registry_complete: bool, secret: str,
) -> ScriptSelection:
    """Map script entities to configuration keys from the read inventory."""

    errors: Counter[str] = Counter()
    # Keep the lexically first bounded union without an unbounded identity set.
    selected: set[str] = set()
    inventory_bounded = False
    for rows in (states, registry):
        for row in rows:
            entity = row.get("entity_id") if isinstance(row, dict) else None
            if not isinstance(entity, str) or not entity.startswith("script."):
                if rows is registry and isinstance(row, dict) and row.get("platform") == "script":
                    errors["invalid_script_identity"] += 1
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
        entity = row.get("entity_id") if isinstance(row, dict) else None
        if not isinstance(entity, str) or entity not in selected:
            continue
        key = row.get("unique_id")
        valid = (
            registry_complete and row.get("platform") == "script"
            and isinstance(key, str) and _KEY.fullmatch(key)
            and not _refused(sanitize_untrusted_data([entity, key], known_secrets=(secret,)))
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

    return ScriptSelection(tuple(candidates), tuple(sorted(errors.items())), inventory_bounded)


async def collect_script_diagnostics(
    states: list, registry: list, *, registry_complete: bool,
    reader_factory: Callable | None, secret: str, concurrency: int,
    automation_documents: tuple = (),
    selection: ScriptSelection | None = None,
    loaded: LoadedScriptSource | None = None,
) -> ScriptDiagnostics:
    """Reuse already read inventory; dispatch only independently mapped identities.

    HAMCP-212: with ``loaded``, each script's loaded configuration is analyzed
    and the delegated stored read runs only for a reported fallback reason.
    Without it no loaded source exists and every script is read stored.
    """
    started = time.monotonic()
    selection = selection or select_script_candidates(
        states, registry, registry_complete=registry_complete, secret=secret,
    )
    errors: Counter[str] = Counter(dict(selection.errors))
    inventory_bounded = selection.inventory_bounded
    candidates = list(selection.candidates)
    mapped = dict(candidates)
    calls: list[DependencyFinding] = []
    call_errors: Counter[str] = Counter()

    def collect_calls(source_type, source_id, source_entity_id, config):
        extracted_calls, gaps = extract_script_calls(
            config, source_type=source_type, source_id=source_id,
            source_entity_id=source_entity_id, identities=mapped, secret=secret,
        )
        call_errors.update(gaps)
        if len(calls) + len(extracted_calls) > MAX_CALLS:
            call_errors["script_call_edge_limit_exceeded"] += 1
        calls[:] = heapq.nsmallest(MAX_CALLS, chain(calls, extracted_calls), key=lambda item: item.evidence_id)

    # Reuse the bounded automation configuration reads. These edges never enter
    # the shared helper/impact snapshot or its obligation budgets.
    for source_id, source_entity_id, config in automation_documents:
        collect_calls("automation", source_id, source_entity_id, config)

    reader = create_script_reader(reader_factory)
    findings: list[DependencyFinding] = []
    dynamic: list[DynamicReference] = []
    documents: list[tuple[str, str]] = []
    document_providers: dict[str, str] = {}
    completed: set[str] = set()
    # One coverage-failure terminal per distinct (source, reason) pair: the
    # named-source total is exact (review 1, M3). Pairs per scan are bounded
    # by MAX_SCRIPT_SOURCES scripts times each script's distinct reasons.
    bounded: dict[tuple[str, str, str, str], DependencyObligation] = {}
    bounded_overflow = [False]
    successes = 0
    read_count = 0
    read_time_ms = 0.0
    active_reads = 0
    maximum_reads = 0
    evidence_bounded = False
    source_counts: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    unverified_loaded = 0
    pending = dict(loaded.pending) if loaded is not None else {}
    journal_overflow = loaded.overflow if loaded is not None else False
    # Journaled keys whose written configuration this build analyzed.
    accounted: set[str] = set()
    verified: set[str] = loaded.verified if loaded is not None else set()

    async def read_stored(entity: str, key: str) -> tuple[dict | None, str | None]:
        nonlocal read_count, read_time_ms, active_reads, maximum_reads
        read_started = time.monotonic()
        read_count += 1
        active_reads += 1
        maximum_reads = max(maximum_reads, active_reads)
        try:
            response = await reader.read(entity)
        finally:
            active_reads -= 1
            read_time_ms += (time.monotonic() - read_started) * 1000
        if not isinstance(response, dict) or response.get("success") is not True:
            category = response.get("details", {}).get("failure_category") if isinstance(response, dict) and isinstance(response.get("details"), dict) else None
            return None, category if category in _FAILURES else "script_provider_error"
        metadata = response.get("metadata")
        data = response.get("data")
        if (not isinstance(metadata, dict) or metadata.get("provider") != "upstream_read_gateway"
                or metadata.get("completeness") != "complete"
                or metadata.get("fallback_occurred") is not False
                or not isinstance(data, dict) or data.get("success") is not True
                or data.get("script_id") != key or not isinstance(data.get("config"), dict)):
            return None, "script_response_incomplete_or_identity_mismatch"
        return data["config"], None

    def analyze(entity: str, key: str, config: dict, provider: str) -> None:
        nonlocal successes, evidence_bounded
        # Blueprint input evidence is useful; its unexpanded body is
        # never silently represented as a complete empty sequence.
        if not isinstance(config.get("sequence"), list) and not isinstance(config.get("use_blueprint"), dict):
            errors["script_configuration_invalid"] += 1
            return
        extracted, unresolved, obligations = extract_document_with_obligations(
            source_type="script", source_id=key, source_entity_id=entity,
            config=config, secret=secret,
        )
        collect_calls("script", key, entity, config)
        documents.append((key, _digest(config)))
        document_providers[key] = provider
        successes += 1
        if any(item.outcome == "coverage_failure" for item in obligations):
            errors["script_extraction_coverage_failure"] += 1
            pairs: dict[tuple[str, str, str, str], DependencyObligation] = {}
            for item in obligations:
                if item.outcome != "coverage_failure":
                    continue
                pair = (item.source_type, item.source_id, item.source_entity_id or "", item.reason_code)
                if pair not in pairs or item.evidence_id < pairs[pair].evidence_id:
                    pairs[pair] = item
            if len(pairs) > MAX_SCRIPT_BOUNDED_OBLIGATIONS:
                # A guard for one document, beyond any reason set it can yield.
                bounded_overflow[0] = True
                pairs = dict(heapq.nsmallest(MAX_SCRIPT_BOUNDED_OBLIGATIONS, pairs.items()))
            for pair, item in pairs.items():
                if pair not in bounded or item.evidence_id < bounded[pair].evidence_id:
                    bounded[pair] = item
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

    if reader is None:
        errors["script_provider_unavailable"] += 1
    else:
        queue = iter(candidates)

        async def process(entity: str, key: str) -> None:
            nonlocal unverified_loaded
            config, reason = loaded_script_route(entity, loaded)
            tracked = key in pending
            if reason is None and not (tracked or journal_overflow):
                source_counts[LOADED_SCRIPT_SOURCE] += 1
                completed.add(entity)
                analyze(entity, key, config, LOADED_SCRIPT_PROVIDER)
                return
            stored, failure = await read_stored(entity, key)
            completed.add(entity)
            if reason is None:
                # A governed write is pending: the reload may not have run yet.
                if failure is None and _canonical(_comparable_stored(stored, config)) == _canonical(config):
                    verified.add(key)
                    source_counts[LOADED_SCRIPT_SOURCE] += 1
                    analyze(entity, key, config, LOADED_SCRIPT_PROVIDER)
                    return
                if failure is not None and not tracked:
                    # Only the journal overflow routed this script here (a
                    # package script has no stored form). The loaded config
                    # stands; the overflow keeps the build unreconciled.
                    source_counts[LOADED_SCRIPT_SOURCE] += 1
                    analyze(entity, key, config, LOADED_SCRIPT_PROVIDER)
                    return
                reason = "pending_write_reload"
            elif failure is None and reason in _STABLE_LOADED_REASONS and loaded is not None:
                verified.add(key)
            elif failure is None and reason in _UNVERIFIED_LOADED_REASONS:
                unverified_loaded += 1
                errors[LOADED_CONFIG_UNAVAILABLE] += 1
            fallback_reasons[reason] += 1
            source_counts[STORED_SCRIPT_SOURCE] += 1
            if failure is not None:
                errors[failure] += 1
                return
            if tracked:
                accounted.add(key)
            analyze(entity, key, stored, STORED_SCRIPT_PROVIDER)

        async def worker() -> None:
            for entity, key in queue:
                if time.monotonic() - started >= SCRIPT_SCAN_SECONDS:
                    return
                if not reader.current():
                    return
                try:
                    await process(entity, key)
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
        # A journaled write whose script is not a mapped candidate was either
        # deleted (stored not found) or is not loaded yet.
        candidate_keys = {key for _entity, key in candidates}
        for key in sorted(set(pending) - candidate_keys):
            entity = f"script.{key}"
            if (not _ENTITY.fullmatch(entity) or not reader.current()
                    or time.monotonic() - started >= SCRIPT_SCAN_SECONDS):
                continue
            try:
                _stored, failure = await read_stored(entity, key)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            if failure == "resource_not_found":
                verified.add(key)
    unreconciled = len(set(pending) - verified - accounted)
    if unreconciled or journal_overflow:
        errors[PENDING_WRITE_UNRECONCILED] += unreconciled + int(journal_overflow)
    if not registry_complete:
        errors["script_registry_incomplete"] += 1
    if evidence_bounded or bounded_overflow[0]:
        errors["script_diagnostic_evidence_limit_exceeded"] += 1
    findings.sort(key=lambda item: item.evidence_id)
    calls.sort(key=lambda item: item.evidence_id)
    dynamic.sort(key=dynamic_reference_fingerprint)
    used_providers = sorted(set(document_providers.values()))
    if used_providers:
        coverage_provider = "+".join(used_providers)
    else:
        coverage_provider = STORED_SCRIPT_PROVIDER if reader else "none"
    if source_counts[LOADED_SCRIPT_SOURCE] and source_counts[STORED_SCRIPT_SOURCE]:
        config_source = "mixed"
    elif source_counts[STORED_SCRIPT_SOURCE]:
        config_source = STORED_SCRIPT_SOURCE
    elif source_counts[LOADED_SCRIPT_SOURCE]:
        config_source = LOADED_SCRIPT_SOURCE
    else:
        config_source = "none"
    source_policy = (
        f"script_config_source={config_source}; "
        f"loaded={source_counts[LOADED_SCRIPT_SOURCE]}; "
        f"stored={source_counts[STORED_SCRIPT_SOURCE]}; "
        "stored fallback reasons: "
        + (", ".join(f"{key}={count}" for key, count in sorted(fallback_reasons.items())) or "none")
    )
    coverage = SourceCoverageItem(
        "script", coverage_provider, "script_configuration",
        "partial" if successes or (reader and not candidates and not errors) else "unavailable",
        evidence_count=len(findings), failed_item_count=sum(errors.values()),
        warnings=[*_GAPS, *(f"{key}: {count}" for key, count in sorted(errors.items())),
                  *(["Inventory was bounded; the exact omitted script count is unknown."] if inventory_bounded else []),
                  "Failure counts include source-level gaps and per-document failures; they are not unique script counts."],
        duration_ms=(time.monotonic() - started) * 1000,
        policy=(f"{reader.provenance}; {source_policy}" if reader
                else "admitted script reader unavailable; no fallback"),
        obligation_ledger_completeness="partial" if successes else "unavailable",
        obligation_ledger_failed_item_count=sum(errors.values()),
    )
    result = ScriptDiagnostics(
        tuple(findings), tuple(dynamic), coverage,
        _digest([sorted(documents), sorted(errors.items()), coverage.policy,
                 [item.evidence_id for item in findings],
                 [dynamic_reference_fingerprint(item) for item in dynamic],
                 [(item.evidence_id, item.source_entity_id) for item in calls],
                 sorted(mapped.items()), sorted(call_errors.items()),
                 sorted(document_providers.items())]),
        reader.current if reader else None,
        {"read_attempts": read_count, "successful_documents": successes,
         "read_time_ms": round(read_time_ms, 3), "maximum_concurrent_reads": maximum_reads,
         "script_config_source": config_source,
         "script_config_source_counts": {
             LOADED_SCRIPT_SOURCE: source_counts[LOADED_SCRIPT_SOURCE],
             STORED_SCRIPT_SOURCE: source_counts[STORED_SCRIPT_SOURCE],
         },
         "stored_config_fallback_reasons": dict(sorted(fallback_reasons.items())),
         LOADED_CONFIG_UNAVAILABLE: unverified_loaded,
         "pending_write_journal": {
             "pending_at_build": len(pending),
             "overflow_at_build": journal_overflow,
             "unreconciled": unreconciled,
             "verified_cleared": len(verified & set(pending)),
         }},
        tuple(calls), tuple(f"{key}: {count}" for key, count in sorted(call_errors.items())),
        tuple(sorted(document_providers.items())),
        tuple(item for _, item in sorted(bounded.items())),
    )
    return result.visible()
