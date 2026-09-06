"""Core-local reconciliation, generation, and lease lifecycle.

This component intentionally has no Home Assistant provider invocation.  The
later shared coordinator can allocate generations and publish routes through
the narrow interfaces here; the Core package remains the owner of Core
observation and compatibility decisions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from .models import (
    CORE_IDENTITY,
    CORE_PROTOCOL,
    MAX_ACTIVE_COMMITS,
    MAX_AUTHORITY_SELECTIONS,
    MAX_CAPABILITIES,
    MAX_ISSUED_LEASES,
    MAX_REPORT_BYTES,
    MAX_RETIREMENT_HISTORY,
    MAX_SAFE_INTEGER,
    MODEL_VERSION,
    SURFACE,
    CoreAuthoritySelection,
    CoreAuthoritySource,
    CoreAuthorityStatus,
    CoreCapabilityClass,
    CoreCapabilityDecision,
    CoreCapabilityProfile,
    CoreDecisionGeneration,
    CoreDispatchCommit,
    CoreDisposition,
    CoreObservation,
    CoreReadmissionError,
    CoreReconciliationResult,
    CoreRouteLease,
    canonical_json,
    fingerprint,
)


_DENIAL = frozenset(
    {
        CoreAuthorityStatus.REVOKED,
        CoreAuthorityStatus.DENY_ONLY,
    }
)
_EXPLICIT_UNAVAILABLE = frozenset({CoreAuthorityStatus.UNAVAILABLE})


class CoreGenerationAllocator(Protocol):
    """Narrow future shared-coordinator boundary; it grants no authority."""

    def next_generation(self) -> int:
        """Return a new positive monotonic scheduling generation."""


class LocalCoreGenerationAllocator:
    """Core-local allocator used until final shared-coordinator integration."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._value = 0

    def next_generation(self) -> int:
        with self._lock:
            if self._value >= MAX_SAFE_INTEGER:
                raise CoreReadmissionError("generation_capacity_exhausted")
            self._value += 1
            return self._value


@dataclass(frozen=True)
class CoreReconciliationAttempt:
    generation: int
    observation: CoreObservation
    authority: tuple[CoreAuthoritySelection, ...]
    material_fingerprint: str
    previous_generation: int | None
    idempotent: bool


class CoreReadmissionCoordinator:
    """Atomically publish Core-only capability decisions and route leases."""

    def __init__(
        self,
        profiles: tuple[CoreCapabilityProfile, ...],
        *,
        generation_allocator: CoreGenerationAllocator | None = None,
    ) -> None:
        if not profiles or len(profiles) > MAX_CAPABILITIES:
            raise CoreReadmissionError("profile_registry_invalid")
        if any(not isinstance(item, CoreCapabilityProfile) for item in profiles):
            raise CoreReadmissionError("profile_registry_invalid")
        capability_ids = tuple(item.capability_id for item in profiles)
        profile_keys = tuple(
            (item.profile_id, item.profile_version) for item in profiles
        )
        adapter_ids = tuple(item.adapter_id for item in profiles)
        if len(capability_ids) != len(set(capability_ids)):
            raise CoreReadmissionError("profile_capability_duplicate")
        if len(profile_keys) != len(set(profile_keys)):
            raise CoreReadmissionError("profile_identity_duplicate")
        if len(adapter_ids) != len(set(adapter_ids)):
            raise CoreReadmissionError("profile_adapter_duplicate")
        self._profiles = tuple(sorted(profiles, key=lambda item: item.capability_id))
        self._profile_by_capability = {
            item.capability_id: item for item in self._profiles
        }
        self._profile_registry_fingerprint = fingerprint(
            {
                "model": "ha-core-profile-registry-v1",
                "profiles": [item.to_mapping() for item in self._profiles],
            }
        )
        self._allocator = generation_allocator or LocalCoreGenerationAllocator()
        self._lock = RLock()
        self._pending_generation: int | None = None
        self._published: CoreDecisionGeneration | None = None
        self._published_material_fingerprint: str | None = None
        self._last_observation: CoreObservation | None = None
        self._last_result: CoreReconciliationResult | None = None
        self._retired_generations: list[int] = []
        self._lease_counter = 0
        self._issued_leases: dict[str, CoreRouteLease] = {}
        self._active_commits: dict[str, CoreDispatchCommit] = {}
        self._capacity_exhaustion_count = 0
        self._capacity_exhaustion_reason: str | None = None

    @property
    def current_generation(self) -> CoreDecisionGeneration | None:
        with self._lock:
            return self._published

    @property
    def profile_registry_fingerprint(self) -> str:
        return self._profile_registry_fingerprint

    def begin_reconciliation(
        self,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> CoreReconciliationAttempt:
        """Retire the prior Core generation before verifying new evidence."""

        if not isinstance(observation, CoreObservation):
            raise CoreReadmissionError("reconciliation_observation_invalid")
        if (
            not isinstance(authority, tuple)
            or len(authority) > MAX_AUTHORITY_SELECTIONS
            or any(not isinstance(item, CoreAuthoritySelection) for item in authority)
        ):
            raise CoreReadmissionError("reconciliation_authority_invalid")
        material_fingerprint = fingerprint(
            {
                "model": "ha-core-reconciliation-material-v1",
                "observation": observation.to_mapping(),
                "authority": [
                    item.to_mapping()
                    for item in sorted(authority, key=lambda item: canonical_json(item.to_mapping()))
                ],
                "profile_registry_fingerprint": self._profile_registry_fingerprint,
            }
        )
        with self._lock:
            if (
                self._published is not None
                and self._published_material_fingerprint == material_fingerprint
            ):
                return CoreReconciliationAttempt(
                    generation=self._published.generation,
                    observation=observation,
                    authority=authority,
                    material_fingerprint=material_fingerprint,
                    previous_generation=None,
                    idempotent=True,
                )
            previous_generation = None
            prior_pending = self._pending_generation
            if self._published is not None:
                previous_generation = self._published.generation
                self._retire_locked(previous_generation)
            generation = self._allocator.next_generation()
            if type(generation) is not int or not 1 <= generation <= MAX_SAFE_INTEGER:
                raise CoreReadmissionError("generation_allocator_invalid")
            prior_generations = [
                *self._retired_generations,
                *([prior_pending] if prior_pending is not None else []),
            ]
            if prior_generations and generation <= max(prior_generations):
                raise CoreReadmissionError("generation_allocator_nonmonotonic")
            self._pending_generation = generation
            self._last_observation = observation
            return CoreReconciliationAttempt(
                generation=generation,
                observation=observation,
                authority=authority,
                material_fingerprint=material_fingerprint,
                previous_generation=previous_generation,
                idempotent=False,
            )

    def complete_reconciliation(
        self,
        attempt: CoreReconciliationAttempt,
    ) -> CoreReconciliationResult:
        """Publish only the newest Core verification ticket."""

        if not isinstance(attempt, CoreReconciliationAttempt):
            raise CoreReadmissionError("reconciliation_attempt_invalid")
        with self._lock:
            if attempt.idempotent:
                generation = self._published
                if generation is None or generation.generation != attempt.generation:
                    return CoreReconciliationResult(
                        generation=None,
                        disposition=CoreDisposition.UNAVAILABLE,
                        previous_generation=None,
                        published=False,
                        idempotent=False,
                        reason_code="idempotent_generation_retired",
                    )
                result = CoreReconciliationResult(
                    generation=generation,
                    disposition=generation.disposition,
                    previous_generation=None,
                    published=True,
                    idempotent=True,
                    reason_code="observation_unchanged",
                )
                self._last_result = result
                return result
            if self._pending_generation != attempt.generation:
                return CoreReconciliationResult(
                    generation=None,
                    disposition=CoreDisposition.UNAVAILABLE,
                    previous_generation=attempt.previous_generation,
                    published=False,
                    idempotent=False,
                    reason_code="verification_generation_stale",
                )
            decisions = self._evaluate(attempt.observation, attempt.authority)
            disposition = self._aggregate(decisions)
            generation = CoreDecisionGeneration(
                generation=attempt.generation,
                disposition=disposition,
                observation_fingerprint=attempt.observation.fingerprint,
                authority_fingerprint=fingerprint(
                    {
                        "model": "ha-core-effective-authority-v1",
                        "authority": [
                            item.to_mapping()
                            for item in sorted(
                                attempt.authority,
                                key=lambda item: canonical_json(item.to_mapping()),
                            )
                        ],
                    }
                ),
                profile_registry_fingerprint=self._profile_registry_fingerprint,
                session_fingerprint=attempt.observation.session_fingerprint,
                decisions=decisions,
            )
            self._published = generation
            self._published_material_fingerprint = attempt.material_fingerprint
            self._pending_generation = None
            self._last_observation = attempt.observation
            result = CoreReconciliationResult(
                generation=generation,
                disposition=disposition,
                previous_generation=attempt.previous_generation,
                published=True,
                idempotent=False,
                reason_code="verification_complete",
            )
            self._last_result = result
            return result

    def reconcile(
        self,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> CoreReconciliationResult:
        return self.complete_reconciliation(
            self.begin_reconciliation(observation, authority)
        )

    def retire_current_generation(self) -> int | None:
        """Retire current authority before replacement evidence is collected."""

        with self._lock:
            generation = self._published
            if generation is None:
                return None
            self._retire_locked(generation.generation)
            return generation.generation

    def acquire_route(
        self,
        capability_id: str,
        *,
        session_fingerprint: str,
        target_fingerprint: str | None = None,
    ) -> CoreRouteLease | None:
        """Issue one registered single-use lease from current Core authority."""

        with self._lock:
            generation = self._published
            profile = self._profile_by_capability.get(capability_id)
            if generation is None or profile is None:
                return None
            decision = generation.decision_for(capability_id)
            if (
                decision is None
                or not decision.disposition.admitted
                or decision.adapter_id is None
                or session_fingerprint != generation.session_fingerprint
            ):
                return None
            if profile.capability_class.mutation_capable and target_fingerprint is None:
                return None
            if len(self._issued_leases) >= MAX_ISSUED_LEASES:
                self._record_capacity_locked("issued_lease_capacity_exhausted")
                return None
            if self._lease_counter >= MAX_SAFE_INTEGER:
                self._record_capacity_locked("lease_sequence_exhausted")
                return None
            self._lease_counter += 1
            lease_id = fingerprint(
                {
                    "model": "ha-core-route-lease-v1",
                    "sequence": self._lease_counter,
                    "generation": generation.generation,
                    "capability_id": capability_id,
                    "adapter_id": decision.adapter_id,
                    "profile_id": decision.profile_id,
                    "session_fingerprint": session_fingerprint,
                    "target_fingerprint": target_fingerprint,
                }
            )
            lease = CoreRouteLease(
                lease_id=lease_id,
                surface=SURFACE,
                capability_id=capability_id,
                adapter_id=decision.adapter_id,
                profile_id=decision.profile_id,
                generation=generation.generation,
                session_fingerprint=session_fingerprint,
                observation_fingerprint=generation.observation_fingerprint,
                target_fingerprint=target_fingerprint,
            )
            self._issued_leases[lease_id] = lease
            return lease

    def acquire_routes(
        self,
        capability_ids: tuple[str, ...],
        *,
        session_fingerprint: str,
        target_fingerprint: str | None = None,
    ) -> tuple[CoreRouteLease, ...] | None:
        """Atomically issue the complete, deterministic Core authority set."""

        if (
            not isinstance(capability_ids, tuple)
            or not capability_ids
            or len(capability_ids) > MAX_CAPABILITIES
            or len(capability_ids) != len(set(capability_ids))
        ):
            return None
        ordered = tuple(sorted(capability_ids, key=lambda item: item.encode("utf-8")))
        with self._lock:
            leases: list[CoreRouteLease] = []
            for capability_id in ordered:
                profile = self._profile_by_capability.get(capability_id)
                if profile is None:
                    for lease in leases:
                        self._issued_leases.pop(lease.lease_id, None)
                    return None
                lease = self.acquire_route(
                    capability_id,
                    session_fingerprint=session_fingerprint,
                    target_fingerprint=(
                        target_fingerprint
                        if profile.capability_class.mutation_capable
                        else None
                    ),
                )
                if lease is None:
                    for issued in leases:
                        self._issued_leases.pop(issued.lease_id, None)
                    return None
                leases.append(lease)
            return tuple(leases)

    def validate_pre_dispatch(
        self,
        lease: CoreRouteLease,
        *,
        observation: CoreObservation,
        target_fingerprint: str | None = None,
    ) -> bool:
        """Revalidate current identity, capability, target, session, and lease."""

        with self._lock:
            return self._valid_lease_locked(
                lease,
                observation=observation,
                target_fingerprint=target_fingerprint,
            )

    def commit_route(
        self,
        lease: CoreRouteLease,
        *,
        observation: CoreObservation,
        target_fingerprint: str | None = None,
    ) -> CoreDispatchCommit | None:
        """Consume one lease immediately before an existing typed provider call."""

        with self._lock:
            if not self._valid_lease_locked(
                lease,
                observation=observation,
                target_fingerprint=target_fingerprint,
            ):
                return None
            if len(self._active_commits) >= MAX_ACTIVE_COMMITS:
                self._record_capacity_locked("active_commit_capacity_exhausted")
                return None
            self._issued_leases.pop(lease.lease_id, None)
            commit = CoreDispatchCommit(
                commit_id=fingerprint(
                    {
                        "model": "ha-core-dispatch-commit-v1",
                        "lease_id": lease.lease_id,
                    }
                ),
                lease=lease,
            )
            self._active_commits[commit.commit_id] = commit
            return commit

    def commit_routes(
        self,
        leases: tuple[CoreRouteLease, ...],
        *,
        observation: CoreObservation,
        target_fingerprint: str | None = None,
    ) -> tuple[CoreDispatchCommit, ...] | None:
        """Atomically validate and consume a complete Core lease set."""

        if (
            not isinstance(leases, tuple)
            or not leases
            or len(leases) > MAX_CAPABILITIES
            or len({item.lease_id for item in leases}) != len(leases)
        ):
            return None
        with self._lock:
            for lease in leases:
                profile = self._profile_by_capability.get(lease.capability_id)
                if profile is None or not self._valid_lease_locked(
                    lease,
                    observation=observation,
                    target_fingerprint=(
                        target_fingerprint
                        if profile.capability_class.mutation_capable
                        else None
                    ),
                ):
                    return None
            if len(self._active_commits) + len(leases) > MAX_ACTIVE_COMMITS:
                self._record_capacity_locked("active_commit_capacity_exhausted")
                return None
            commits: list[CoreDispatchCommit] = []
            for lease in leases:
                self._issued_leases.pop(lease.lease_id, None)
                commit = CoreDispatchCommit(
                    commit_id=fingerprint(
                        {
                            "model": "ha-core-dispatch-commit-v1",
                            "lease_id": lease.lease_id,
                        }
                    ),
                    lease=lease,
                )
                self._active_commits[commit.commit_id] = commit
                commits.append(commit)
            return tuple(commits)

    def release_route(self, lease: CoreRouteLease) -> bool:
        with self._lock:
            if self._issued_leases.get(getattr(lease, "lease_id", None)) != lease:
                return False
            self._issued_leases.pop(lease.lease_id, None)
            return True

    def release_routes(self, leases: tuple[CoreRouteLease, ...]) -> bool:
        """Release an unconsumed complete set without partial success."""

        with self._lock:
            if any(
                self._issued_leases.get(getattr(item, "lease_id", None)) != item
                for item in leases
            ):
                return False
            for lease in leases:
                self._issued_leases.pop(lease.lease_id, None)
            return True

    def finish_commit(self, commit: CoreDispatchCommit) -> bool:
        with self._lock:
            if self._active_commits.get(getattr(commit, "commit_id", None)) != commit:
                return False
            self._active_commits.pop(commit.commit_id, None)
            return True

    def finish_commits(self, commits: tuple[CoreDispatchCommit, ...]) -> bool:
        """Finish a complete committed set without accepting stale members."""

        with self._lock:
            if any(
                self._active_commits.get(getattr(item, "commit_id", None)) != item
                for item in commits
            ):
                return False
            for commit in commits:
                self._active_commits.pop(commit.commit_id, None)
            return True

    def update_assessment(self) -> dict[str, Any]:
        """Return a bounded operator assessment with no raw Core evidence."""

        with self._lock:
            generation = self._published
            observation = self._last_observation
            result = self._last_result
            decisions = generation.decisions if generation is not None else ()
            compatible = tuple(
                item for item in decisions if item.disposition.admitted
            )
            held = tuple(
                item for item in decisions
                if item.disposition is CoreDisposition.HELD
            )
            quarantined = tuple(
                item for item in decisions
                if item.disposition is CoreDisposition.QUARANTINED
            )
            unavailable = tuple(
                item for item in decisions
                if item.disposition is CoreDisposition.UNAVAILABLE
            )
            withheld = (*held, *quarantined, *unavailable)
            code_change_reasons = {
                "capability_contract_changed",
                "capability_not_compiled",
                "semantic_contract_unverified",
            }
            data_update_reasons = {
                "positive_authority_missing",
                "authority_identity_or_protocol_disagreement",
                "authority_bundle_invalid",
            }
            reason_codes = {item.reason_code for item in withheld}
            categories = self._change_categories(reason_codes)
            projection = {
                "model_version": MODEL_VERSION,
                "surface": SURFACE,
                "observed_core_version": observation.version if observation else None,
                "previous_generation": (
                    result.previous_generation if result is not None else None
                ),
                "current_generation": generation.generation if generation else None,
                "identity_agreement": bool(
                    observation and observation.identity_agrees
                ),
                "disposition": (
                    generation.disposition.value
                    if generation is not None
                    else CoreDisposition.UNAVAILABLE.value
                ),
                "compatible_count": len(compatible),
                "held_count": len(held),
                "quarantined_count": len(quarantined),
                "unavailable_count": len(unavailable),
                "withheld_count": len(withheld),
                "compatible_capabilities": [
                    item.capability_id for item in compatible
                ],
                "held_capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "reason_code": item.reason_code,
                    }
                    for item in held
                ],
                "quarantined_capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "reason_code": item.reason_code,
                    }
                    for item in quarantined
                ],
                "unavailable_capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "reason_code": item.reason_code,
                    }
                    for item in unavailable
                ],
                "engineering_code_change_required": bool(
                    reason_codes & code_change_reasons
                ),
                "compatibility_data_update_required": bool(
                    reason_codes & data_update_reasons
                ) and not bool(reason_codes & code_change_reasons),
                "change_categories": categories,
                "issued_lease_count": len(self._issued_leases),
                "active_commit_count": len(self._active_commits),
                "retained_retirement_count": len(self._retired_generations),
                "capacity_exhaustion_count": self._capacity_exhaustion_count,
                "capacity_exhaustion_reason": self._capacity_exhaustion_reason,
                "fallback_count": 0,
            }
            canonical_json(projection, maximum=MAX_REPORT_BYTES)
            return projection

    def health_projection(self) -> dict[str, Any]:
        """Return a smaller bounded capability and lifecycle summary."""

        assessment = self.update_assessment()
        projection = {
            key: assessment[key]
            for key in (
                "model_version",
                "surface",
                "current_generation",
                "disposition",
                "compatible_count",
                "held_count",
                "quarantined_count",
                "unavailable_count",
                "withheld_count",
                "issued_lease_count",
                "active_commit_count",
                "retained_retirement_count",
                "capacity_exhaustion_count",
                "capacity_exhaustion_reason",
                "fallback_count",
            )
        }
        canonical_json(projection, maximum=MAX_REPORT_BYTES)
        return projection

    def _valid_lease_locked(
        self,
        lease: CoreRouteLease,
        *,
        observation: CoreObservation,
        target_fingerprint: str | None,
    ) -> bool:
        if not isinstance(lease, CoreRouteLease) or not isinstance(
            observation,
            CoreObservation,
        ):
            return False
        issued = self._issued_leases.get(lease.lease_id)
        generation = self._published
        profile = self._profile_by_capability.get(lease.capability_id)
        if issued != lease or generation is None or profile is None:
            return False
        decision = generation.decision_for(lease.capability_id)
        return bool(
            decision is not None
            and decision.disposition.admitted
            and decision.adapter_id == lease.adapter_id
            and decision.profile_id == lease.profile_id
            and lease.surface == SURFACE
            and lease.generation == generation.generation
            and lease.session_fingerprint == generation.session_fingerprint
            and observation.session_fingerprint == generation.session_fingerprint
            and observation.fingerprint == generation.observation_fingerprint
            and lease.observation_fingerprint == generation.observation_fingerprint
            and lease.target_fingerprint == target_fingerprint
            and (
                target_fingerprint is not None
                if profile.capability_class.mutation_capable
                else target_fingerprint is None
            )
        )

    def _retire_locked(self, generation: int) -> None:
        self._retired_generations.append(generation)
        if len(self._retired_generations) > MAX_RETIREMENT_HISTORY:
            del self._retired_generations[:-MAX_RETIREMENT_HISTORY]
        self._published = None
        self._published_material_fingerprint = None
        self._issued_leases.clear()

    def _record_capacity_locked(self, reason: str) -> None:
        self._capacity_exhaustion_count = min(
            MAX_SAFE_INTEGER,
            self._capacity_exhaustion_count + 1,
        )
        self._capacity_exhaustion_reason = reason

    def _evaluate(
        self,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> tuple[CoreCapabilityDecision, ...]:
        global_reason = self._global_reason(observation)
        authority_invalid = self._authority_invalid(authority)
        decisions: list[CoreCapabilityDecision] = []
        for profile in self._profiles:
            evidence = observation.evidence_for(profile.capability_id)
            decisions.append(
                self._evaluate_profile(
                    profile,
                    evidence,
                    observation,
                    authority,
                    global_reason=(
                        "authority_bundle_invalid"
                        if authority_invalid
                        else global_reason
                    ),
                )
            )
        compiled_ids = set(self._profile_by_capability)
        observed_ids = {
            item.capability_id for item in observation.capability_evidence
        }
        unknown_ids = observed_ids - compiled_ids
        if len(decisions) + len(unknown_ids) > MAX_CAPABILITIES:
            return tuple(
                self._decision(
                    profile,
                    CoreDisposition.UNAVAILABLE,
                    "capability_evidence_oversized",
                )
                for profile in self._profiles
            )
        for capability_id in sorted(unknown_ids):
            values = observation.evidence_for(capability_id)
            decisions.append(
                CoreCapabilityDecision(
                    capability_id=capability_id,
                    capability_class=CoreCapabilityClass.UNCLASSIFIED,
                    profile_id="uncompiled_core_capability",
                    disposition=CoreDisposition.QUARANTINED,
                    reason_code="capability_not_compiled",
                    authority_source=None,
                    adapter_id=None,
                    contract_fingerprint=fingerprint(
                        {
                            "model": "uncompiled-core-capability-observation-v1",
                            "evidence": [item.to_mapping() for item in values],
                        }
                    ),
                )
            )
        return tuple(sorted(decisions, key=lambda item: item.capability_id))

    def _evaluate_profile(
        self,
        profile: CoreCapabilityProfile,
        evidence: tuple[Any, ...],
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
        *,
        global_reason: str | None,
    ) -> CoreCapabilityDecision:
        if not profile.auto_eligible:
            return self._decision(
                profile,
                CoreDisposition.QUARANTINED,
                "automatic_core_admission_prohibited",
            )
        if global_reason is not None:
            return self._decision(profile, CoreDisposition.UNAVAILABLE, global_reason)
        denial = self._denial(profile, observation, authority)
        if denial is not None:
            return self._decision(
                profile,
                (
                    CoreDisposition.HELD
                    if denial.status is CoreAuthorityStatus.DENY_ONLY
                    else CoreDisposition.QUARANTINED
                ),
                denial.reason_code,
                source=denial.source,
            )
        unavailable = self._explicit_unavailable(
            profile, observation, authority
        )
        if unavailable is not None:
            return self._decision(
                profile,
                CoreDisposition.UNAVAILABLE,
                unavailable.reason_code,
                source=unavailable.source,
            )
        if len(evidence) > 1:
            return self._decision(
                profile,
                CoreDisposition.QUARANTINED,
                "capability_evidence_duplicate",
            )
        if not evidence:
            return self._decision(
                profile,
                CoreDisposition.UNAVAILABLE,
                "capability_evidence_missing",
            )
        observed = evidence[0]
        observed_fingerprint = observed.contract_fingerprint_for(profile)
        if observed_fingerprint is None:
            reason = (
                "capability_observation_unstable"
                if not observed.stable
                else "semantic_contract_unverified"
                if profile.capability_class.semantic
                else "capability_contract_changed"
            )
            return self._decision(
                profile,
                CoreDisposition.QUARANTINED,
                reason,
            )
        positive = self._positive(profile, observation, authority)
        if positive is None:
            return self._decision(
                profile,
                CoreDisposition.UNAVAILABLE,
                self._missing_authority_reason(profile, observation, authority),
            )
        disposition = (
            CoreDisposition.ADMITTED_EXACT
            if positive.source is CoreAuthoritySource.COMPILED_EXACT
            else CoreDisposition.ADMITTED_COMPATIBLE
        )
        return self._decision(
            profile,
            disposition,
            (
                "compiled_exact_contract_matched"
                if disposition is CoreDisposition.ADMITTED_EXACT
                else "verified_compatible_contract_matched"
            ),
            source=positive.source,
        )

    def _authority_invalid(
        self,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> bool:
        profile_keys = {
            (item.profile_id, item.profile_version, item.adapter_id): item
            for item in self._profiles
        }
        for item in authority:
            profile = profile_keys.get(
                (item.profile_id, item.profile_version, item.adapter_id)
            )
            if profile is None:
                return True
            if not set(item.capability_ids) <= {profile.capability_id}:
                return True
        return False

    @staticmethod
    def _global_reason(observation: CoreObservation) -> str | None:
        if observation.identity != CORE_IDENTITY:
            return "core_identity_disagreement"
        if observation.protocol != CORE_PROTOCOL:
            return "core_protocol_disagreement"
        if not observation.connected:
            return "transport_unavailable"
        if not observation.authenticated:
            return "authentication_failed"
        if not observation.versions_agree:
            return "core_version_disagreement"
        if not observation.stable:
            return observation.reason_code
        if not observation.complete:
            return "core_observation_incomplete"
        if observation.reason_code != "observation_complete":
            return observation.reason_code
        return None

    def _positive(
        self,
        profile: CoreCapabilityProfile,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> CoreAuthoritySelection | None:
        candidates = tuple(
            item
            for item in authority
            if item.status is CoreAuthorityStatus.POSITIVE
            and self._selects(item, profile)
            and item.matches(observation)
            and profile.capability_id in item.capability_ids
        )
        return min(
            candidates,
            key=lambda item: (
                0 if item.source is CoreAuthoritySource.COMPILED_EXACT else 1,
                canonical_json(item.to_mapping()),
            ),
            default=None,
        )

    def _denial(
        self,
        profile: CoreCapabilityProfile,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> CoreAuthoritySelection | None:
        candidates = tuple(
            item
            for item in authority
            if item.status in _DENIAL
            and self._selects(item, profile)
            and item.matches(observation)
            and profile.capability_id in item.capability_ids
        )
        return min(
            candidates,
            key=lambda item: canonical_json(item.to_mapping()),
            default=None,
        )

    def _explicit_unavailable(
        self,
        profile: CoreCapabilityProfile,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> CoreAuthoritySelection | None:
        candidates = tuple(
            item
            for item in authority
            if item.status in _EXPLICIT_UNAVAILABLE
            and self._selects(item, profile)
            and item.matches(observation)
            and profile.capability_id in item.capability_ids
        )
        return min(
            candidates,
            key=lambda item: canonical_json(item.to_mapping()),
            default=None,
        )

    @staticmethod
    def _selects(
        authority: CoreAuthoritySelection,
        profile: CoreCapabilityProfile,
    ) -> bool:
        return (
            authority.profile_id == profile.profile_id
            and authority.profile_version == profile.profile_version
            and authority.adapter_id == profile.adapter_id
        )

    def _missing_authority_reason(
        self,
        profile: CoreCapabilityProfile,
        observation: CoreObservation,
        authority: tuple[CoreAuthoritySelection, ...],
    ) -> str:
        relevant = tuple(
            item
            for item in authority
            if self._selects(item, profile) and item.matches(observation)
        )
        if any(item.status is CoreAuthorityStatus.ROLLBACK for item in relevant):
            return "verified_authority_rollback"
        if any(
            item.status is CoreAuthorityStatus.REPLAY_CONFLICT
            for item in relevant
        ):
            return "verified_authority_replay_conflict"
        if any(item.status is CoreAuthorityStatus.EXPIRED for item in relevant):
            return "verified_authority_expired"
        if any(self._selects(item, profile) for item in authority):
            return "authority_identity_or_protocol_disagreement"
        return "positive_authority_missing"

    @staticmethod
    def _decision(
        profile: CoreCapabilityProfile,
        disposition: CoreDisposition,
        reason: str,
        *,
        source: CoreAuthoritySource | None = None,
    ) -> CoreCapabilityDecision:
        return CoreCapabilityDecision(
            capability_id=profile.capability_id,
            capability_class=profile.capability_class,
            profile_id=profile.profile_id,
            disposition=disposition,
            reason_code=reason,
            authority_source=source,
            adapter_id=profile.adapter_id if disposition.admitted else None,
            contract_fingerprint=profile.contract_fingerprint,
        )

    def _aggregate(
        self,
        decisions: tuple[CoreCapabilityDecision, ...],
    ) -> CoreDisposition:
        known = tuple(
            item
            for item in decisions
            if item.capability_class is not CoreCapabilityClass.UNCLASSIFIED
            and self._profile_by_capability[item.capability_id].auto_eligible
        )
        admitted = tuple(item for item in known if item.disposition.admitted)
        if known and len(admitted) == len(known):
            return (
                CoreDisposition.ADMITTED_COMPATIBLE
                if any(
                    item.disposition is CoreDisposition.ADMITTED_COMPATIBLE
                    for item in admitted
                )
                else CoreDisposition.ADMITTED_EXACT
            )
        if admitted:
            return CoreDisposition.PARTIAL
        aggregate_decisions = tuple(
            item
            for item in decisions
            if item in known
            or item.capability_class is CoreCapabilityClass.UNCLASSIFIED
        )
        if any(
            item.disposition is CoreDisposition.QUARANTINED
            for item in aggregate_decisions
        ):
            return CoreDisposition.QUARANTINED
        if any(
            item.disposition is CoreDisposition.HELD
            for item in aggregate_decisions
        ):
            return CoreDisposition.HELD
        return CoreDisposition.UNAVAILABLE

    @staticmethod
    def _change_categories(reason_codes: set[str]) -> list[str]:
        categories: set[str] = set()
        for reason in reason_codes:
            if reason in {"transport_unavailable", "core_session_changed"}:
                categories.add("transport")
            elif "semantic" in reason:
                categories.add("semantic")
            elif "authority" in reason or "identity" in reason:
                categories.add("provider")
            else:
                categories.add("schema")
        return sorted(categories)


__all__ = [
    "CoreGenerationAllocator",
    "CoreReadmissionCoordinator",
    "CoreReconciliationAttempt",
    "LocalCoreGenerationAllocator",
]
