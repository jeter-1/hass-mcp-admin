"""HAMCP-227: diagnostic scene, group, dashboard and template reference coverage.

These references are kept outside the helper obligation ledger, the snapshot
findings and the snapshot fingerprint, like ``ScriptDiagnostics``. Governance
therefore cannot be downgraded by them, and a dashboard edit does not change an
approval-bound fingerprint. ``entity_dependency_analysis`` and
``change_impact_analysis`` merge them; ``configuration_integrity_analysis``,
``incident_correlation`` and ``handoff_generation`` consume them with their
completeness (HAMCP-267). None of these changes a governance binding.

- Scenes and groups: the loaded membership in the ``entity_id`` attribute of
  the states the build already read. No extra request is made, and the evidence
  is labeled as loaded membership, not stored configuration.
- Dashboards: the reviewed dashboard list and analysis read path plus the
  existing dashboard walker (``dashboard_analysis.dependency_reader``).
- Templates: deferred (HAMCP-264); reported as unavailable with the reason.

A source is ``complete`` only when every item was enumerated, read and parsed.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field, replace
import hashlib
import heapq
import json
import re
import time
from typing import Any, Callable

from ..dashboard_analysis.contracts import REASONS as ANALYSIS_REASONS
from ..untrusted_text import screen
from .extraction import _bounded, valid_entity_id
from .models import DependencyFinding, SourceCoverageItem, evidence_id

MAX_SCENE_SOURCES = 1_000
MAX_GROUP_SOURCES = 1_000
MAX_MEMBERS_PER_SOURCE = 256
MAX_MEMBERSHIP_FINDINGS = 10_000
MAX_DEPENDENCY_DASHBOARDS = 50
MAX_DASHBOARD_FINDINGS = 10_000
DASHBOARD_SCAN_SECONDS = 6.0
# HAMCP-277: one read may take at most this long (and never past the phase
# deadline), so a slow dashboard cannot hold a worker for the whole phase.
DASHBOARD_READ_SECONDS = 3.0
DASHBOARD_READ_CONCURRENCY = 4
MAX_DASHBOARD_OUTCOME_PATHS = 10
# Exact per-dashboard outcome, in report order.
DASHBOARD_OUTCOMES = (
    "read", "too_large", "timed_out", "failed",
    "not_attempted_budget", "not_attempted_authority", "not_attempted_failure",
    "not_attempted_limit",
)
MAX_GLOBAL_BOUNDED_TERMINALS = 1_000
DEFAULT_DASHBOARD_PATH = "lovelace"
GLOBAL_SOURCE_TYPES = ("scene", "group", "template", "dashboard")
MEMBERSHIP_PROVIDER = "direct_ha_api"
DASHBOARD_PROVIDER = "upstream_dashboard"
MEMBERSHIP_LABEL = "loaded membership (state attributes), not stored configuration"
MEMBERSHIP_POLICY = (
    f"{MEMBERSHIP_LABEL}; states and registry inventory reused; no extra request; "
    "diagnostic only, outside the governance-bound index; no fallback"
)
TEMPLATE_DEFERRED_WARNING = (
    "Template configuration enumeration is deferred (HAMCP-264): no audited read "
    "purpose exists for YAML templates, and UI template helpers are not read because "
    "their only configuration path is an options flow that can write."
)
TEMPLATE_COUNT_WARNING = (
    "{count} template-platform entity registry entr{suffix} observed; this is a lower "
    "bound, because YAML template entities without a unique_id are not in the registry."
)
_RULE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)


@dataclass(frozen=True)
class GlobalBoundedTerminal:
    """A source that hit a bound, in the shape ``bounded_source_entries`` reads."""

    source_type: str
    source_id: str
    source_entity_id: str | None
    reason_code: str
    outcome: str = "coverage_failure"


@dataclass(frozen=True)
class GlobalSourceDiagnostics:
    findings: tuple[DependencyFinding, ...]
    coverage: tuple[SourceCoverageItem, ...]
    fingerprint: str
    # Dashboard evidence depends on the dashboard provider's current authority;
    # loaded membership is read by the build itself.
    authority_current: Callable[[], bool] | None = None
    profile: dict[str, Any] = field(default_factory=dict, compare=False)
    source_providers: tuple[tuple[str, str], ...] = ()
    bounded_obligations: tuple[GlobalBoundedTerminal, ...] = ()

    def provider_for(self, source_type: str) -> str:
        return dict(self.source_providers).get(source_type, "none")

    def coverage_for(self, source_type: str) -> SourceCoverageItem | None:
        return next((item for item in self.coverage if item.source_type == source_type), None)

    def visible(self) -> GlobalSourceDiagnostics:
        """A retired dashboard route cannot lend cached findings current authority."""
        if self.authority_current is None:
            return self
        try:
            if self.authority_current():
                return self
        except Exception:
            pass
        return GlobalSourceDiagnostics(
            tuple(item for item in self.findings if item.source_type != "dashboard"),
            tuple(
                replace(
                    item, completeness="unavailable", evidence_count=0,
                    warnings=["Dashboard provider authority changed; cached dashboard evidence is withheld."],
                ) if item.source_type == "dashboard" else item
                for item in self.coverage
            ),
            _digest([self.fingerprint, "dashboard_provider_authority_changed"]),
            profile=self.profile,
            source_providers=self.source_providers,
            bounded_obligations=tuple(
                item for item in self.bounded_obligations if item.source_type != "dashboard"
            ),
        )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _shown_entity(value: Any, secret: str) -> str | None:
    """An exact entity identity that the credential screen shows unchanged."""
    if not isinstance(value, str) or not valid_entity_id(value):
        return None
    if screen(value, (secret,) if secret else ()) is not None:
        return None
    return value


def template_row(template_count: int | None) -> SourceCoverageItem:
    warnings = [TEMPLATE_DEFERRED_WARNING]
    if template_count is not None:
        warnings.append(TEMPLATE_COUNT_WARNING.format(
            count=template_count, suffix="y" if template_count == 1 else "ies",
        ))
    return SourceCoverageItem(
        "template", "none", "template_configuration", "unavailable", 0, 0, warnings, 0.0,
        policy="deferred (HAMCP-264); no template configuration read; no fallback",
    )


@dataclass
class _MembershipResult:
    findings: list[DependencyFinding]
    coverage: SourceCoverageItem
    terminals: list[GlobalBoundedTerminal]
    documents: list[tuple[str, str]]


def _membership(
    source_type: str, sources: list[tuple[str, dict | None, str | None]],
    *, errors: Counter, inventory_bounded: bool, registry_complete: bool,
    secret: str, started: float,
) -> _MembershipResult:
    relation = f"{source_type}_member"
    findings: list[DependencyFinding] = []
    terminals: list[GlobalBoundedTerminal] = []
    documents: list[tuple[str, str]] = []
    examined = 0
    findings_bounded = False
    for entity, attributes, state in sources:
        if attributes is None:
            errors[f"{source_type}_state_unavailable"] += 1
            continue
        members = attributes.get("entity_id")
        if not isinstance(members, list):
            errors[f"{source_type}_members_not_exposed"] += 1
            continue
        examined += 1
        name = attributes.get("friendly_name")
        source_name = _bounded(name, 160, secret) if isinstance(name, str) else None
        source_state = _bounded(state, 64, secret) if isinstance(state, str) else None
        retained = []
        if len(members) > MAX_MEMBERS_PER_SOURCE:
            errors[f"{source_type}_member_limit_exceeded"] += 1
            terminals.append(GlobalBoundedTerminal(
                source_type, entity, entity, f"{source_type}_member_limit_exceeded",
            ))
        for position, member in enumerate(members[:MAX_MEMBERS_PER_SOURCE]):
            safe = _shown_entity(member, secret)
            if safe is None:
                errors[f"{source_type}_member_invalid_or_withheld"] += 1
                continue
            retained.append(safe)
            if len(findings) >= MAX_MEMBERSHIP_FINDINGS:
                findings_bounded = True
                continue
            path = f"$.attributes.entity_id[{position}]"
            findings.append(DependencyFinding(
                evidence_id=evidence_id(source_type, entity, safe, relation, path),
                target_entity_id=safe,
                source_type=source_type,
                source_id=entity,
                source_entity_id=entity,
                source_name=source_name,
                relation=relation,
                config_path=path,
                match_type="loaded_membership",
                source_state=source_state,
                evidence_summary=(
                    f"Entity is a member of this {source_type} in its loaded state "
                    "attributes; stored configuration is not read."
                ),
            ))
        documents.append((entity, _digest(retained)))
    if findings_bounded:
        errors[f"{source_type}_finding_limit_exceeded"] += 1
        terminals.append(GlobalBoundedTerminal(
            source_type, "dependency_provider", None, f"{source_type}_finding_limit_exceeded",
        ))
    if inventory_bounded:
        errors[f"{source_type}_inventory_limit_exceeded"] += 1
        terminals.append(GlobalBoundedTerminal(
            source_type, "dependency_provider", None, f"{source_type}_inventory_limit_exceeded",
        ))
    if not registry_complete:
        errors["entity_registry_incomplete"] += 1
    completeness = "complete" if not errors else "partial"
    warnings = [
        f"{source_type.capitalize()} evidence is {MEMBERSHIP_LABEL}.",
        *(f"{key}: {count}" for key, count in sorted(errors.items())),
    ]
    if inventory_bounded:
        warnings.append(
            f"The {source_type} inventory was bounded; the exact omitted {source_type} count is unknown."
        )
    coverage = SourceCoverageItem(
        source_type, MEMBERSHIP_PROVIDER, f"{source_type}_loaded_membership", completeness,
        evidence_count=len(findings), failed_item_count=sum(errors.values()),
        warnings=warnings, duration_ms=(time.monotonic() - started) * 1000,
        policy=MEMBERSHIP_POLICY,
        obligation_ledger_completeness="unavailable",
        obligation_ledger_failed_item_count=0,
    )
    findings.sort(key=lambda item: item.evidence_id)
    return _MembershipResult(findings, coverage, terminals, documents)


def _bounded_selection(entities, limit: int) -> tuple[list[str], bool]:
    ordered = sorted(set(entities))
    return ordered[:limit], len(ordered) > limit


def collect_membership_sources(
    states: list, registry: list, *, registry_complete: bool, secret: str,
) -> tuple[list[_MembershipResult], int]:
    """Scene and group membership from the already read inventories.

    Returns the scene and group results and the template-platform registry
    count. No request is made.
    """
    started = time.monotonic()
    by_entity: dict[str, dict] = {}
    for row in states:
        entity = row.get("entity_id") if isinstance(row, dict) else None
        if isinstance(entity, str) and entity not in by_entity:
            by_entity[entity] = row
    scene_errors: Counter[str] = Counter()
    group_errors: Counter[str] = Counter()
    scene_candidates: set[str] = set()
    group_candidates: set[str] = set()
    template_count = 0

    def candidate(entity: Any, errors: Counter, prefix: str) -> str | None:
        safe = _shown_entity(entity, secret)
        if safe is None:
            errors[f"{prefix}_identity_invalid_or_withheld"] += 1
        return safe

    for entity in by_entity:
        if entity.startswith("scene."):
            if (safe := candidate(entity, scene_errors, "scene")) is not None:
                scene_candidates.add(safe)
        elif entity.startswith("group."):
            if (safe := candidate(entity, group_errors, "group")) is not None:
                group_candidates.add(safe)
    for row in registry:
        if not isinstance(row, dict):
            continue
        entity = row.get("entity_id")
        platform = row.get("platform")
        if platform == "template":
            template_count += 1
        if isinstance(entity, str) and entity.startswith("scene."):
            if (safe := candidate(entity, scene_errors, "scene")) is not None:
                scene_candidates.add(safe)
        elif platform == "group" or (isinstance(entity, str) and entity.startswith("group.")):
            if (safe := candidate(entity, group_errors, "group")) is not None:
                group_candidates.add(safe)

    results = []
    for source_type, candidates, errors, limit in (
        ("scene", scene_candidates, scene_errors, MAX_SCENE_SOURCES),
        ("group", group_candidates, group_errors, MAX_GROUP_SOURCES),
    ):
        selected, bounded = _bounded_selection(candidates, limit)
        sources = []
        for entity in selected:
            row = by_entity.get(entity)
            attributes = row.get("attributes") if isinstance(row, dict) else None
            sources.append((
                entity,
                attributes if isinstance(attributes, dict) else None,
                row.get("state") if isinstance(row, dict) else None,
            ))
        results.append(_membership(
            source_type, sources, errors=errors, inventory_bounded=bounded,
            registry_complete=registry_complete, secret=secret, started=started,
        ))
    return results, template_count


def _config_path(pointer: str, secret: str) -> str:
    segments = pointer.split("/")[1:] if pointer else []
    parts = ["$"]
    for segment in segments:
        if segment.isdigit():
            parts.append(f"[{segment}]")
        elif _SEGMENT.fullmatch(segment):
            parts.append(f".{segment}")
        else:
            parts.append(".<key>")
    return _bounded("".join(parts), 256, secret) or "$"


def dashboard_findings(
    url_path: str, title: str | None, items: list, *, secret: str,
) -> list[DependencyFinding]:
    """Entity references from one dashboard's walker items."""
    findings = []
    source_name = _bounded(title, 160, secret) if isinstance(title, str) else None
    for item in items:
        if not isinstance(item, dict) or item.get("kind") != "entity_reference":
            continue
        entity = _shown_entity(item.get("entity_id"), secret)
        rule = item.get("rule_id")
        pointer = item.get("pointer")
        if entity is None or not isinstance(rule, str) or not _RULE.fullmatch(rule) or not isinstance(pointer, str):
            continue
        relation = f"dashboard_{rule}"
        path = _config_path(pointer, secret)
        conditional = item.get("conditional") is True
        findings.append(DependencyFinding(
            evidence_id=evidence_id("dashboard", url_path, entity, relation, pointer),
            target_entity_id=entity,
            source_type="dashboard",
            source_id=url_path,
            source_entity_id=None,
            source_name=source_name,
            relation=relation,
            config_path=path,
            evidence_summary=(
                "Entity is referenced by a dashboard card"
                + (" shown only under a condition." if conditional else ".")
            ),
        ))
    return findings


_NOT_STARTED = object()


async def _within(
    start: Callable[[], Any], allowance: Callable[[], float], sleep: Callable[[float], Any],
) -> tuple[str, float, Any]:
    """Run one bounded read: ``(status, seconds, result)``.

    ``allowance()`` is evaluated inside the scheduled task, immediately before
    ``start()`` invokes the reader, with no event-loop turn between them. Work
    that waited behind other tasks therefore cannot start once the allowance is
    spent (HAMCP-277 R1-M1): it returns ``("not_started", seconds, None)``. A
    started read returns ``("done", seconds, result)``, or ``("timeout",
    seconds, None)`` when its ``seconds`` passed first.
    """

    seconds = 0.0
    timer: asyncio.Future | None = None

    async def expire(limit: float) -> None:
        await sleep(limit)
        task.cancel()

    async def operation() -> Any:
        nonlocal seconds, timer
        seconds = allowance()
        if seconds <= 0:
            return _NOT_STARTED
        timer = asyncio.ensure_future(expire(seconds))
        return await start()

    task = asyncio.ensure_future(operation())
    try:
        await asyncio.wait((task,))
    finally:
        if timer is not None:
            timer.cancel()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if timer is not None:
            await asyncio.gather(timer, return_exceptions=True)
    if task.cancelled():
        return "timeout", seconds, None
    result = task.result()
    if result is _NOT_STARTED:
        return "not_started", seconds, None
    return "done", seconds, result


def _outcome_warning(outcome: str, paths: list[str], secret: str) -> str:
    shown = [path for path in paths if screen(path, (secret,) if secret else ()) is None]
    shown = shown[:MAX_DASHBOARD_OUTCOME_PATHS]
    others = len(paths) - len(shown)
    return (f"Dashboards {outcome}: " + (", ".join(shown) if shown else "none shown")
            + (f", and {others} other{'s' if others != 1 else ''}" if others else ""))


async def collect_dashboard_sources(
    reader, *, secret: str, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, Any]:
    """Read each listed dashboard once, isolated per dashboard, within one budget.

    One deadline covers the session, the list and every read; the caller
    starts this phase after the other sources (HAMCP-277), so their work does
    not spend it. Each read is bounded by ``DASHBOARD_READ_SECONDS`` and the
    deadline. The deadline is checked inside each scheduled read, immediately
    before the reader is invoked, so no read starts once it has passed; a
    watchdog cancels whatever is still in flight when it expires, and the
    session context then releases its lease. Every listed dashboard gets one
    exact outcome, including those beyond the read limit.
    """
    started = clock()
    deadline = started + DASHBOARD_SCAN_SECONDS
    errors: Counter[str] = Counter()
    terminals: list[GlobalBoundedTerminal] = []
    findings: list[DependencyFinding] = []
    documents: list[tuple[str, str]] = []
    profile = {"list_requests": 0, "read_attempts": 0, "successful_documents": 0,
               "maximum_concurrent_reads": 0, "read_time_ms": 0.0}
    findings_bounded = False
    budget_exhausted = False
    inventory_read = False
    inventory_bounded = False
    listed: list[dict] = []
    omitted: list[dict] = []
    attempted: set[str] = set()
    outcomes: dict[str, str] = {}
    stale = False

    def expired() -> bool:
        nonlocal budget_exhausted
        if clock() >= deadline:
            budget_exhausted = True
        return budget_exhausted

    async def run() -> None:
        nonlocal listed, omitted, inventory_read, inventory_bounded
        async with reader.session() as session:
            if expired():
                return
            profile["list_requests"] += 1
            inventory = await session.list(MAX_DEPENDENCY_DASHBOARDS + 1)
            inventory_read = True
            entries = list(inventory["dashboards"])
            if inventory.get("partial"):
                errors["dashboard_inventory_partial"] += 1
            paths = [item["url_path"] for item in entries]
            if DEFAULT_DASHBOARD_PATH not in paths:
                errors["dashboard_default_not_listed"] += 1
            # Upstream truncation, or more entries than were requested, means the
            # total is only known to be at least the count accounted here.
            inventory_bounded = (inventory.get("truncated") is True
                                 or len(entries) > MAX_DEPENDENCY_DASHBOARDS + 1)
            if len(entries) > MAX_DEPENDENCY_DASHBOARDS or inventory_bounded:
                errors["dashboard_inventory_limit_exceeded"] += 1
                terminals.append(GlobalBoundedTerminal(
                    "dashboard", "dependency_provider", None, "dashboard_inventory_limit_exceeded",
                ))
            # A fixed order: the same household is read the same way each build.
            # Accounting keeps every listed entry (bounded at the request size);
            # only the first MAX_DEPENDENCY_DASHBOARDS are queued for reading,
            # and the rest are named, not dropped (HAMCP-277 R1-M2).
            listed = sorted(entries, key=lambda item: item["url_path"])[:MAX_DEPENDENCY_DASHBOARDS + 1]
            omitted = listed[MAX_DEPENDENCY_DASHBOARDS:]
            for entry in omitted:
                outcomes[entry["url_path"]] = "not_attempted_limit"
                errors["dashboard_not_read_inventory_limit"] += 1
            queued = listed[:MAX_DEPENDENCY_DASHBOARDS]
            queue = iter(queued)
            active = 0

            async def process(entry: dict) -> None:
                nonlocal active, findings_bounded, budget_exhausted
                path = entry["url_path"]
                read_started: float | None = None

                def allowance() -> float:
                    # Evaluated when the read actually runs, not when it was
                    # queued: other work may have spent the remainder meanwhile.
                    return min(DASHBOARD_READ_SECONDS, deadline - clock())

                def start():
                    nonlocal active, read_started
                    read_started = clock()
                    profile["read_attempts"] += 1
                    attempted.add(path)
                    active += 1
                    profile["maximum_concurrent_reads"] = max(profile["maximum_concurrent_reads"], active)
                    return session.read(path)

                try:
                    status, seconds, document = await _within(start, allowance, sleep)
                finally:
                    if read_started is not None:
                        active -= 1
                        profile["read_time_ms"] += (clock() - read_started) * 1000
                if status == "not_started":
                    # The deadline passed while this read waited to run; it is
                    # left unattempted and named not_attempted_budget below.
                    budget_exhausted = True
                    return
                if status == "timeout":
                    # Capped by the phase deadline: the budget, not the read, ran out.
                    budget_exhausted = budget_exhausted or seconds < DASHBOARD_READ_SECONDS
                    document = {"failure": "timeout"}
                if document.get("failure"):
                    failure = document["failure"]
                    errors[f"dashboard_read_{failure}"] += 1
                    outcomes[path] = ("too_large" if failure == "response_limit"
                                      else "timed_out" if failure == "timeout" else "failed")
                    if failure == "response_limit":
                        terminals.append(GlobalBoundedTerminal(
                            "dashboard", path, None, "dashboard_document_limit_exceeded",
                        ))
                    return
                outcomes[path] = "read"
                profile["successful_documents"] += 1
                documents.append((path, str(document.get("projection_hash") or "")))
                for reason, count in document.get("gaps", {}).items():
                    errors[f"dashboard_{reason}"] += count
                if document.get("processing_truncated"):
                    errors["dashboard_projection_limit_exceeded"] += 1
                    terminals.append(GlobalBoundedTerminal(
                        "dashboard", path, None, "dashboard_projection_limit_exceeded",
                    ))
                extracted = dashboard_findings(path, entry.get("title"), document.get("items", []), secret=secret)
                if len(findings) + len(extracted) > MAX_DASHBOARD_FINDINGS:
                    findings_bounded = True
                findings[:] = heapq.nsmallest(
                    MAX_DASHBOARD_FINDINGS, [*findings, *extracted], key=lambda item: item.evidence_id,
                )

            async def worker() -> None:
                nonlocal stale
                for entry in queue:
                    if expired():
                        return
                    if not reader.current():
                        stale = True
                        return
                    try:
                        await process(entry)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        outcomes[entry["url_path"]] = "failed"
                        errors["dashboard_read_or_parse_failure"] += 1

            workers = [asyncio.create_task(worker())
                       for _ in range(min(len(queued), DASHBOARD_READ_CONCURRENCY))]
            try:
                await asyncio.gather(*workers)
            finally:
                for task in workers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

    if reader is None:
        errors["dashboard_provider_unavailable"] += 1
    else:
        collection = asyncio.ensure_future(run())
        watchdog = asyncio.ensure_future(sleep(max(0.0, deadline - clock())))
        try:
            await asyncio.wait((collection, watchdog), return_when=asyncio.FIRST_COMPLETED)
        finally:
            watchdog.cancel()
            if not collection.done():
                budget_exhausted = True
                collection.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            outcome = (await asyncio.gather(collection, return_exceptions=True))[0]
        if isinstance(outcome, Exception):
            reason = getattr(outcome, "reason", None)
            errors[f"dashboard_inventory_{reason}" if reason in ANALYSIS_REASONS and not inventory_read
                   else "dashboard_inventory_unavailable" if not inventory_read
                   else "dashboard_read_or_parse_failure"] += 1
        if budget_exhausted:
            errors["dashboard_scan_budget_exhausted"] += 1
            terminals.append(GlobalBoundedTerminal(
                "dashboard", "dependency_provider", None, "dashboard_scan_budget_exhausted",
            ))
        for entry in listed:
            path = entry["url_path"]
            if path in outcomes:
                continue
            if path in attempted:
                # In flight when the phase deadline cancelled it.
                outcomes[path] = "timed_out"
                errors["dashboard_read_timeout"] += 1
                continue
            outcomes[path] = ("not_attempted_budget" if budget_exhausted
                              else "not_attempted_authority" if stale else "not_attempted_failure")
            errors["dashboard_not_read"] += 1
    if findings_bounded:
        errors["dashboard_finding_limit_exceeded"] += 1
        terminals.append(GlobalBoundedTerminal(
            "dashboard", "dependency_provider", None, "dashboard_finding_limit_exceeded",
        ))
    findings.sort(key=lambda item: item.evidence_id)
    successes = profile["successful_documents"]
    if not errors:
        completeness = "complete"
    elif successes or inventory_read:
        completeness = "partial"
    else:
        completeness = "unavailable"
    by_outcome: dict[str, list[str]] = {}
    for path, outcome in sorted(outcomes.items()):
        by_outcome.setdefault(outcome, []).append(path)
    profile["outcome_counts"] = {outcome: len(by_outcome[outcome])
                                 for outcome in DASHBOARD_OUTCOMES if outcome in by_outcome}
    warnings = []
    if inventory_read:
        # First, so a one-line reader (HAMCP-270 disclosure) sees the count.
        # The denominator is every listed dashboard accounted, not the read
        # queue; a truncated inventory is only a lower bound on the total.
        first = (f"Dashboards read: {successes} of at least {len(listed)} (inventory truncated)"
                 if inventory_bounded else f"Dashboards read: {successes} of {len(listed)} listed")
        if omitted:
            first += f"; {len(omitted)} not attempted ({MAX_DEPENDENCY_DASHBOARDS}-read limit)"
        warnings.append(first)
    warnings.extend(f"{key}: {count}" for key, count in sorted(errors.items()))
    warnings.extend(_outcome_warning(outcome, by_outcome[outcome], secret)
                    for outcome in DASHBOARD_OUTCOMES[1:] if outcome in by_outcome)
    if errors.get("dashboard_default_not_listed"):
        warnings.append(
            "The default dashboard (lovelace) was not in the reviewed dashboard list and was not read."
        )
    profile["read_time_ms"] = round(profile["read_time_ms"], 3)
    coverage = SourceCoverageItem(
        "dashboard", DASHBOARD_PROVIDER if reader is not None else "none", "dashboard_configuration",
        completeness, evidence_count=len(findings), failed_item_count=sum(errors.values()),
        warnings=warnings, duration_ms=(clock() - started) * 1000,
        policy=(f"{reader.provenance}; diagnostic only, outside the governance-bound index"
                if reader is not None else "admitted dashboard reader unavailable; no fallback"),
        obligation_ledger_completeness="unavailable",
        obligation_ledger_failed_item_count=0,
    )
    return {
        "findings": findings, "coverage": coverage, "terminals": terminals,
        "documents": sorted(documents), "errors": sorted(errors.items()),
        "profile": profile, "current": reader.current if reader is not None else None,
        "outcomes": dict(sorted(outcomes.items())),
    }


async def collect_global_sources(
    states: list, registry: list, *, registry_complete: bool, secret: str,
    dashboards: dict[str, Any] | None,
) -> GlobalSourceDiagnostics:
    """Combine loaded membership, the dashboard result and the template row."""
    memberships, template_count = collect_membership_sources(
        states, registry, registry_complete=registry_complete, secret=secret,
    )
    if dashboards is None:
        dashboards = await collect_dashboard_sources(None, secret=secret)
    findings = [item for result in memberships for item in result.findings]
    findings.extend(dashboards["findings"])
    terminals = [item for result in memberships for item in result.terminals]
    terminals.extend(dashboards["terminals"])
    terminals = sorted(set(terminals), key=lambda item: (
        item.source_type, item.source_id, item.source_entity_id or "", item.reason_code,
    ))[:MAX_GLOBAL_BOUNDED_TERMINALS]
    by_type = {result.coverage.source_type: result.coverage for result in memberships}
    coverage = (by_type["scene"], by_type["group"], template_row(template_count), dashboards["coverage"])
    findings.sort(key=lambda item: item.evidence_id)
    return GlobalSourceDiagnostics(
        tuple(findings), coverage,
        _digest([
            [(result.coverage.source_type, sorted(result.documents)) for result in memberships],
            [(item.source_type, item.completeness, item.failed_item_count) for item in coverage],
            dashboards["documents"], dashboards["errors"],
            [item.evidence_id for item in findings], template_count,
        ]),
        dashboards["current"],
        {"scene_sources": sum(1 for _ in memberships[0].documents),
         "group_sources": sum(1 for _ in memberships[1].documents),
         "template_registry_entries": template_count,
         "dashboard": dashboards["profile"]},
        (("scene", MEMBERSHIP_PROVIDER), ("group", MEMBERSHIP_PROVIDER),
         ("template", "none"), ("dashboard", dashboards["coverage"].provider)),
        tuple(terminals),
    )


# HAMCP-267: shared reading rules for the analyses that consume the partition
# (configuration integrity, incident correlation, handoff generation). Each
# caller reads ``visible()`` once and passes that view here.
CONSUMED_SOURCE_TYPES = ("scene", "group", "dashboard")


def scoped_partition_findings(
    partition: GlobalSourceDiagnostics | None, entity_ids, *, limit: int = 500,
) -> list[DependencyFinding]:
    """Visible partition findings whose target is one of ``entity_ids``."""
    if partition is None:
        return []
    scope = set(entity_ids)
    return [
        item for item in partition.findings
        if item.source_type in CONSUMED_SOURCE_TYPES and item.target_entity_id in scope
    ][:limit]


def incomplete_partition_rows(
    partition: GlobalSourceDiagnostics | None,
) -> list[SourceCoverageItem]:
    """The consumed partition rows that are not complete, in source order."""
    if partition is None:
        return []
    return [
        row for source_type in CONSUMED_SOURCE_TYPES
        if (row := partition.coverage_for(source_type)) is not None and row.completeness != "complete"
    ]


def partition_limitations(partition: GlobalSourceDiagnostics | None, prefix: str) -> list[str]:
    """``<prefix>_<source>_partial`` or ``<prefix>_<source>_unavailable`` per incomplete row."""
    return [
        f"{prefix}_{row.source_type}_{'partial' if row.completeness == 'partial' else 'unavailable'}"
        for row in incomplete_partition_rows(partition)
    ]


def partition_summary(item: DependencyFinding) -> str:
    """A one-line description that keeps loaded membership distinct from configuration."""
    if item.match_type == "loaded_membership":
        return (f"{item.source_type} {item.source_id} lists {item.target_entity_id} in its loaded membership "
                "(state attributes; stored configuration is not read).")
    return f"{item.source_type} {item.source_id} references {item.target_entity_id} at {item.config_path}."
