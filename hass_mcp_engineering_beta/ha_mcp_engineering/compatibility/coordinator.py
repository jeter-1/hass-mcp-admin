"""Inert, two-phase coordinator for capability-scoped readmission decisions."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from .models import (
    AdmissionDisposition,
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityContract,
    CapabilityDecision,
    CapabilityProfile,
    CompatibilityModelError,
    CompatibilityObservation,
    DecisionGeneration,
    DispatchCommit,
    MAX_PROFILES,
    ReconciliationResult,
    RouteLease,
    UpstreamSurface,
    evidence_fingerprint,
)


_ADMITTED = frozenset(
    {
        AdmissionDisposition.ADMITTED_EXACT,
        AdmissionDisposition.ADMITTED_COMPATIBLE,
    }
)
_DENYING = frozenset({AuthorityStatus.REVOKED, AuthorityStatus.DENY_ONLY})


@dataclass(frozen=True)
class ReconciliationAttempt:
    generation: int
    observation: CompatibilityObservation
    authority: AuthorityBundle
    material_fingerprint: str
    retired_generation: int | None
    idempotent: bool
    events: tuple[str, ...]


class CapabilityAdmissionCoordinator:
    """Pure in-memory state machine; it performs no observation or dispatch I/O."""

    def __init__(self, profiles: tuple[CapabilityProfile, ...]):
        if not profiles or len(profiles) > MAX_PROFILES:
            raise CompatibilityModelError("profile_registry_invalid")
        profile_keys = tuple((item.profile_id, item.profile_version) for item in profiles)
        if len(profile_keys) != len(set(profile_keys)):
            raise CompatibilityModelError("profile_registry_duplicate")
        capability_ids = tuple(
            capability.capability_id
            for profile in profiles
            for capability in profile.capabilities
        )
        if len(capability_ids) != len(set(capability_ids)):
            raise CompatibilityModelError("profile_registry_capability_duplicate")
        self._profiles = tuple(
            sorted(profiles, key=lambda item: (item.surface.value, item.profile_id))
        )
        self._profile_registry_fingerprint = evidence_fingerprint(
            {
                "profiles": [item.to_mapping() for item in self._profiles],
            }
        )
        self._lock = RLock()
        self._next_generation = 0
        self._pending_generation: int | None = None
        self._published: DecisionGeneration | None = None
        self._published_material_fingerprint: str | None = None
        self._retired_generations: set[int] = set()
        self._lease_counter = 0
        self._committed: set[str] = set()

    @property
    def current_generation(self) -> DecisionGeneration | None:
        with self._lock:
            return self._published

    @property
    def profile_registry_fingerprint(self) -> str:
        return self._profile_registry_fingerprint

    def begin_reconciliation(
        self,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> ReconciliationAttempt:
        """Retire stale authority before any new verification can publish."""

        material_fingerprint = evidence_fingerprint(
            {
                "observation": observation.to_mapping(include_session=True),
                "authority": authority.to_mapping(),
                "profile_registry_fingerprint": self._profile_registry_fingerprint,
            }
        )
        with self._lock:
            if (
                self._published is not None
                and self._published_material_fingerprint == material_fingerprint
                and self._published.generation not in self._retired_generations
            ):
                return ReconciliationAttempt(
                    generation=self._published.generation,
                    observation=observation,
                    authority=authority,
                    material_fingerprint=material_fingerprint,
                    retired_generation=None,
                    idempotent=True,
                    events=("observation_unchanged",),
                )

            retired_generation = None
            if self._published is not None:
                retired_generation = self._published.generation
                self._retired_generations.add(retired_generation)
                self._published = None
                self._published_material_fingerprint = None
            self._next_generation += 1
            generation = self._next_generation
            self._pending_generation = generation
            events = (
                (("generation_retired",) if retired_generation else ())
                + ("generation_created", "verification_started")
            )
            return ReconciliationAttempt(
                generation=generation,
                observation=observation,
                authority=authority,
                material_fingerprint=material_fingerprint,
                retired_generation=retired_generation,
                idempotent=False,
                events=events,
            )

    def complete_reconciliation(
        self,
        attempt: ReconciliationAttempt,
    ) -> ReconciliationResult:
        """Evaluate and atomically publish only the newest verification ticket."""

        with self._lock:
            if attempt.idempotent:
                generation = self._published
                if generation is None or generation.generation != attempt.generation:
                    return ReconciliationResult(
                        generation=None,
                        disposition=AdmissionDisposition.UNAVAILABLE,
                        retired_generation=None,
                        published=False,
                        idempotent=False,
                        reason_code="idempotent_generation_retired",
                        events=("verification_stale",),
                    )
                return ReconciliationResult(
                    generation=generation,
                    disposition=generation.disposition,
                    retired_generation=None,
                    published=True,
                    idempotent=True,
                    reason_code="observation_unchanged",
                    events=attempt.events,
                )
            if self._pending_generation != attempt.generation:
                return ReconciliationResult(
                    generation=None,
                    disposition=AdmissionDisposition.UNAVAILABLE,
                    retired_generation=attempt.retired_generation,
                    published=False,
                    idempotent=False,
                    reason_code="verification_generation_stale",
                    events=attempt.events + ("verification_stale",),
                )

            decisions, disposition = self._evaluate(
                attempt.observation,
                attempt.authority,
            )
            generation = DecisionGeneration(
                generation=attempt.generation,
                surface=attempt.observation.surface,
                disposition=disposition,
                observation_fingerprint=attempt.observation.fingerprint,
                authority_fingerprint=attempt.authority.fingerprint,
                profile_registry_fingerprint=self._profile_registry_fingerprint,
                session_fingerprint=attempt.observation.session_fingerprint,
                decisions=decisions,
            )
            self._published = generation
            self._published_material_fingerprint = attempt.material_fingerprint
            self._pending_generation = None
            return ReconciliationResult(
                generation=generation,
                disposition=disposition,
                retired_generation=attempt.retired_generation,
                published=True,
                idempotent=False,
                reason_code="verification_complete",
                events=attempt.events
                + ("verification_completed", "generation_published"),
            )

    def reconcile(
        self,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> ReconciliationResult:
        return self.complete_reconciliation(
            self.begin_reconciliation(observation, authority)
        )

    def acquire_route(self, capability_id: str, *, session_id: str) -> RouteLease | None:
        """Acquire a short immutable route lease from the current generation."""

        session_fingerprint = evidence_fingerprint({"session_id": session_id})
        with self._lock:
            generation = self._published
            if (
                generation is None
                or generation.generation in self._retired_generations
                or generation.session_fingerprint != session_fingerprint
            ):
                return None
            decision = generation.decision_for(capability_id)
            if (
                decision is None
                or decision.disposition not in _ADMITTED
                or decision.adapter_id is None
            ):
                return None
            self._lease_counter += 1
            lease_id = evidence_fingerprint(
                {
                    "generation": generation.generation,
                    "capability_id": capability_id,
                    "adapter_id": decision.adapter_id,
                    "session_fingerprint": session_fingerprint,
                    "lease_ordinal": self._lease_counter,
                }
            )
            return RouteLease(
                lease_id=lease_id,
                generation=generation.generation,
                capability_id=capability_id,
                adapter_id=decision.adapter_id,
                session_fingerprint=session_fingerprint,
            )

    def validate_pre_dispatch(self, lease: RouteLease, *, session_id: str) -> bool:
        session_fingerprint = evidence_fingerprint({"session_id": session_id})
        with self._lock:
            generation = self._published
            if (
                generation is None
                or generation.generation != lease.generation
                or lease.generation in self._retired_generations
                or lease.session_fingerprint != session_fingerprint
                or generation.session_fingerprint != session_fingerprint
            ):
                return False
            decision = generation.decision_for(lease.capability_id)
            return bool(
                decision
                and decision.disposition in _ADMITTED
                and decision.adapter_id == lease.adapter_id
            )

    def commit_route(
        self,
        lease: RouteLease,
        *,
        session_id: str,
    ) -> DispatchCommit | None:
        """Atomically validate and mark a logical route committed; no I/O occurs."""

        with self._lock:
            if not self.validate_pre_dispatch(lease, session_id=session_id):
                return None
            commit_id = evidence_fingerprint(
                {
                    "lease_id": lease.lease_id,
                    "generation": lease.generation,
                    "state": "committed_after_validation",
                }
            )
            self._committed.add(commit_id)
            return DispatchCommit(lease=lease, commit_id=commit_id)

    def finish_committed(self, commit: DispatchCommit) -> bool:
        """Allow completion without granting any route-publication authority."""

        with self._lock:
            if commit.commit_id not in self._committed:
                return False
            self._committed.remove(commit.commit_id)
            return True

    def health_projection(self) -> dict[str, Any]:
        """Return only fingerprints, counts, dispositions, and reason codes."""

        with self._lock:
            generation = self._published
            if generation is None:
                return {
                    "model_version": 1,
                    "disposition": AdmissionDisposition.VERIFYING.value
                    if self._pending_generation is not None
                    else AdmissionDisposition.UNAVAILABLE.value,
                    "generation": self._pending_generation,
                    "admitted_count": 0,
                    "quarantined_count": 0,
                    "unavailable_count": 0,
                    "reason_counts": [],
                    "fallback_count": 0,
                }
            reason_counts: dict[str, int] = {}
            for decision in generation.decisions:
                reason_counts[decision.reason_code] = (
                    reason_counts.get(decision.reason_code, 0) + 1
                )
            projection = {
                "model_version": 1,
                "surface": generation.surface.value,
                "disposition": generation.disposition.value,
                "generation": generation.generation,
                "decision_fingerprint": generation.decision_fingerprint,
                "observation_fingerprint": generation.observation_fingerprint,
                "authority_fingerprint": generation.authority_fingerprint,
                "profile_registry_fingerprint": generation.profile_registry_fingerprint,
                "admitted_count": sum(
                    decision.disposition in _ADMITTED for decision in generation.decisions
                ),
                "quarantined_count": sum(
                    decision.disposition is AdmissionDisposition.QUARANTINED
                    for decision in generation.decisions
                ),
                "unavailable_count": sum(
                    decision.disposition is AdmissionDisposition.UNAVAILABLE
                    for decision in generation.decisions
                ),
                "reason_counts": [
                    {"reason_code": code, "count": count}
                    for code, count in sorted(reason_counts.items())
                ],
                "fallback_count": 0,
            }
            evidence_fingerprint(projection)
            return projection

    def audit_projection(self, result: ReconciliationResult) -> dict[str, Any]:
        """Return a bounded event projection without raw identities or catalogs."""

        generation = result.generation
        projection: dict[str, Any] = {
            "model_version": 1,
            "event": "capability_reconciliation",
            "disposition": result.disposition.value,
            "published": result.published,
            "idempotent": result.idempotent,
            "reason_code": result.reason_code,
            "retired_generation": result.retired_generation,
            "events": list(result.events),
            "fallback_count": 0,
        }
        if generation is not None:
            projection.update(
                {
                    "generation": generation.generation,
                    "surface": generation.surface.value,
                    "decision_fingerprint": generation.decision_fingerprint,
                    "admitted_count": len(generation.admitted_capability_ids),
                    "decision_count": len(generation.decisions),
                }
            )
        evidence_fingerprint(projection)
        return projection

    def _evaluate(
        self,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> tuple[tuple[CapabilityDecision, ...], AdmissionDisposition]:
        profiles = tuple(
            item for item in self._profiles if item.surface is observation.surface
        )
        if not profiles:
            return (), AdmissionDisposition.UNAVAILABLE

        global_reason = self._global_observation_reason(observation)
        observed_by_id: dict[str, list[Any]] = {}
        for item in observation.capabilities:
            observed_by_id.setdefault(item.capability_id, []).append(item)

        decisions: list[CapabilityDecision] = []
        declared_ids: set[str] = set()
        for profile in profiles:
            for contract in profile.capabilities:
                declared_ids.add(contract.capability_id)
                decisions.append(
                    self._evaluate_contract(
                        profile,
                        contract,
                        observed_by_id.get(contract.capability_id, []),
                        observation,
                        authority,
                        global_reason,
                    )
                )

        for capability_id, observed_items in observed_by_id.items():
            if capability_id in declared_ids:
                continue
            observed = observed_items[0]
            decisions.append(
                CapabilityDecision(
                    capability_id=capability_id,
                    profile_id=None,
                    disposition=AdmissionDisposition.QUARANTINED,
                    reason_code=(
                        "write_capability_prohibited"
                        if observed.kind.write_capable
                        else "capability_not_compiled"
                    ),
                    authority_source=None,
                    adapter_id=None,
                    contract_fingerprint=observed.contract_fingerprint,
                )
            )

        ordered = tuple(sorted(decisions, key=lambda item: item.capability_id))
        declared = tuple(item for item in ordered if item.profile_id is not None)
        admitted = tuple(item for item in declared if item.disposition in _ADMITTED)
        admissible_declared = tuple(
            item
            for item in declared
            if not self._profile_contract(item.capability_id).kind.write_capable
        )
        aggregate_decisions = admissible_declared or declared
        if admissible_declared and len(admitted) == len(admissible_declared):
            disposition = (
                AdmissionDisposition.ADMITTED_COMPATIBLE
                if any(
                    item.disposition is AdmissionDisposition.ADMITTED_COMPATIBLE
                    for item in admitted
                )
                else AdmissionDisposition.ADMITTED_EXACT
            )
        elif admitted:
            disposition = AdmissionDisposition.PARTIAL
        elif any(
            item.disposition is AdmissionDisposition.QUARANTINED
            for item in aggregate_decisions
        ):
            disposition = AdmissionDisposition.QUARANTINED
        else:
            disposition = AdmissionDisposition.UNAVAILABLE
        return ordered, disposition

    def _evaluate_contract(
        self,
        profile: CapabilityProfile,
        contract: CapabilityContract,
        observed_items: list[Any],
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
        global_reason: str | None,
    ) -> CapabilityDecision:
        observed_fingerprint = (
            observed_items[0].contract_fingerprint
            if observed_items
            else contract.contract_fingerprint
        )
        if contract.kind.write_capable:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.QUARANTINED,
                "write_capability_prohibited",
            )
        if observation.identity != profile.expected_identity:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.UNAVAILABLE,
                "profile_identity_disagreement",
            )
        if observation.protocol_version not in profile.supported_protocols:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.UNAVAILABLE,
                "profile_protocol_disagreement",
            )
        if global_reason is not None:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.UNAVAILABLE,
                global_reason,
            )
        if len(observed_items) > 1:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.QUARANTINED,
                "capability_duplicate",
            )
        if not observed_items:
            return self._decision(
                profile,
                contract,
                contract.contract_fingerprint,
                AdmissionDisposition.UNAVAILABLE,
                "capability_missing",
            )

        denial = self._denial_for(profile, contract, observation, authority)
        if denial is not None:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.QUARANTINED,
                denial.reason_code,
                denial.source,
            )
        positive = self._positive_for(profile, contract, observation, authority)
        if positive is None:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.UNAVAILABLE,
                self._missing_authority_reason(profile, observation, authority),
            )
        observed = observed_items[0]
        if observed.kind is not contract.kind:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.QUARANTINED,
                "capability_classification_changed",
                positive.source,
            )
        if observed.contract_fingerprint != contract.contract_fingerprint:
            return self._decision(
                profile,
                contract,
                observed_fingerprint,
                AdmissionDisposition.QUARANTINED,
                "capability_contract_changed",
                positive.source,
            )
        return self._decision(
            profile,
            contract,
            observed_fingerprint,
            (
                AdmissionDisposition.ADMITTED_EXACT
                if positive.source is AuthoritySource.COMPILED_EXACT
                else AdmissionDisposition.ADMITTED_COMPATIBLE
            ),
            (
                "compiled_exact_contract_matched"
                if positive.source is AuthoritySource.COMPILED_EXACT
                else "signed_compatible_contract_matched"
            ),
            positive.source,
        )

    @staticmethod
    def _global_observation_reason(
        observation: CompatibilityObservation,
    ) -> str | None:
        if not observation.connected:
            return "transport_unavailable"
        if not observation.authenticated:
            return "authentication_failed"
        if not observation.catalog_complete:
            return "catalog_incomplete"
        if observation.surface is UpstreamSurface.HOME_ASSISTANT_CORE and not observation.core_versions_agree:
            return "core_version_disagreement"
        return None

    def _denial_for(
        self,
        profile: CapabilityProfile,
        contract: CapabilityContract,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> AuthorityDecision | None:
        for decision in authority.decisions:
            if (
                decision.status in _DENYING
                and self._authority_selects_profile(decision, profile)
                and decision.matches_observation(observation)
                and (
                    not decision.capability_ids
                    or contract.capability_id in decision.capability_ids
                )
            ):
                return decision
        return None

    def _positive_for(
        self,
        profile: CapabilityProfile,
        contract: CapabilityContract,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> AuthorityDecision | None:
        candidates: list[AuthorityDecision] = []
        profile_ids = {item.capability_id for item in profile.capabilities}
        for decision in authority.decisions:
            if decision.status is not AuthorityStatus.POSITIVE:
                continue
            if decision.source is AuthoritySource.LIVE_OBSERVATION:
                continue
            if not self._authority_selects_profile(decision, profile):
                continue
            if not set(decision.capability_ids) <= profile_ids:
                continue
            if contract.capability_id not in decision.capability_ids:
                continue
            if not decision.matches_observation(observation):
                continue
            if (
                decision.source is AuthoritySource.SIGNED_REGISTRY
                and (
                    decision.expires_at_epoch is None
                    or decision.expires_at_epoch <= authority.evaluated_at_epoch
                )
            ):
                continue
            candidates.append(decision)
        candidates.sort(
            key=lambda item: (
                0 if item.source is AuthoritySource.COMPILED_EXACT else 1,
                -(item.registry_sequence or 0),
            )
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _authority_selects_profile(
        decision: AuthorityDecision,
        profile: CapabilityProfile,
    ) -> bool:
        return (
            decision.profile_id == profile.profile_id
            and decision.profile_version == profile.profile_version
            and decision.adapter_id == profile.adapter_id
        )

    def _missing_authority_reason(
        self,
        profile: CapabilityProfile,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> str:
        relevant = tuple(
            item
            for item in authority.decisions
            if self._authority_selects_profile(item, profile)
            and item.matches_observation(observation)
        )
        if any(item.status is AuthorityStatus.ROLLBACK for item in relevant):
            return "signed_registry_rollback"
        if any(item.status is AuthorityStatus.REPLAY_CONFLICT for item in relevant):
            return "signed_registry_replay_conflict"
        if any(
            item.source is AuthoritySource.LIVE_OBSERVATION
            and item.status is AuthorityStatus.POSITIVE
            for item in relevant
        ):
            return "live_observation_not_authority"
        if any(
            item.status is AuthorityStatus.EXPIRED
            or (
                item.source is AuthoritySource.SIGNED_REGISTRY
                and item.status is AuthorityStatus.POSITIVE
                and (
                    item.expires_at_epoch is None
                    or item.expires_at_epoch <= authority.evaluated_at_epoch
                )
            )
            for item in relevant
        ):
            return "signed_positive_authority_expired"
        if any(
            item.profile_id == profile.profile_id
            and item.profile_version == profile.profile_version
            for item in authority.decisions
        ):
            return "identity_or_protocol_disagreement"
        return "positive_authority_missing"

    def _decision(
        self,
        profile: CapabilityProfile,
        contract: CapabilityContract,
        observed_fingerprint: str,
        disposition: AdmissionDisposition,
        reason_code: str,
        source: AuthoritySource | None = None,
    ) -> CapabilityDecision:
        return CapabilityDecision(
            capability_id=contract.capability_id,
            profile_id=profile.profile_id,
            disposition=disposition,
            reason_code=reason_code,
            authority_source=source,
            adapter_id=profile.adapter_id if disposition in _ADMITTED else None,
            contract_fingerprint=observed_fingerprint,
        )

    def _profile_contract(self, capability_id: str) -> CapabilityContract:
        for profile in self._profiles:
            contract = profile.capability(capability_id)
            if contract is not None:
                return contract
        raise CompatibilityModelError("profile_contract_missing")
