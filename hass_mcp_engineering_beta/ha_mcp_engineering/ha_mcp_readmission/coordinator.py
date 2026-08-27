"""Production generation and lease coordinator for capability readmission."""

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
    MAX_ACTIVE_COMMITS,
    MAX_ISSUED_LEASES,
    MAX_PROFILES,
    MAX_RETIREMENT_DIAGNOSTICS,
    MAX_SAFE_INTEGER,
    ReconciliationResult,
    RouteLease,
    UpstreamSurface,
    canonical_json,
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


@dataclass
class SurfaceState:
    """Independent authority lifecycle for exactly one upstream surface."""

    pending_generation: int | None = None
    published: DecisionGeneration | None = None
    published_material_fingerprint: str | None = None
    retired_generation_diagnostics: list[int] | None = None
    last_observation_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.retired_generation_diagnostics is None:
            self.retired_generation_diagnostics = []


class CapabilityAdmissionCoordinator:
    """Publish bounded decisions and enforce single-use route leases.

    The coordinator performs no provider I/O. Callers must supply separately
    validated observations and binary-owned profiles/authority.
    """

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
            sorted(profiles, key=lambda item: evidence_fingerprint(item.to_mapping()))
        )
        self._profile_registry_fingerprint = evidence_fingerprint(
            {
                "profiles": [item.to_mapping() for item in self._profiles],
            }
        )
        self._surface_profile_fingerprints = {
            surface: evidence_fingerprint(
                {
                    "surface": surface.value,
                    "profiles": [
                        item.to_mapping()
                        for item in self._profiles
                        if item.surface is surface
                    ],
                }
            )
            for surface in UpstreamSurface
        }
        self._capability_surfaces = {
            capability.capability_id: profile.surface
            for profile in self._profiles
            for capability in profile.capabilities
        }
        self._lock = RLock()
        self._next_generation = 0
        self._surface_states = {
            surface: SurfaceState() for surface in UpstreamSurface
        }
        self._lease_counter = 0
        self._issued_leases: dict[str, RouteLease] = {}
        self._active_commits: dict[str, DispatchCommit] = {}
        self._capacity_exhaustion_count = 0
        self._capacity_exhaustion_reason: str | None = None

    @property
    def current_generation(self) -> DecisionGeneration | None:
        with self._lock:
            published = tuple(
                state.published
                for state in self._surface_states.values()
                if state.published is not None
            )
            return max(published, key=lambda item: item.generation, default=None)

    def generation_for(self, surface: UpstreamSurface) -> DecisionGeneration | None:
        if not isinstance(surface, UpstreamSurface):
            raise CompatibilityModelError("surface_state_invalid")
        with self._lock:
            return self._surface_states[surface].published

    @property
    def profile_registry_fingerprint(self) -> str:
        return self._profile_registry_fingerprint

    def replace_surface_profile(
        self,
        profile: CapabilityProfile,
    ) -> int | None:
        """Install one binary-owned surface profile and retire old leases.

        Profile replacement is a caller-owned binary configuration event, not
        signed or observed authority. Runtime integration uses it only after an
        exact compiled policy binding has selected the profile.
        """

        if not isinstance(profile, CapabilityProfile):
            raise CompatibilityModelError("profile_registry_invalid")
        with self._lock:
            current = tuple(
                item
                for item in self._profiles
                if item.surface is profile.surface
            )
            if current == (profile,):
                return None
            retained = tuple(
                item
                for item in self._profiles
                if item.surface is not profile.surface
            )
            candidate = retained + (profile,)
            profile_keys = tuple(
                (item.profile_id, item.profile_version)
                for item in candidate
            )
            capability_ids = tuple(
                capability.capability_id
                for item in candidate
                for capability in item.capabilities
            )
            if (
                len(candidate) > MAX_PROFILES
                or len(profile_keys) != len(set(profile_keys))
                or len(capability_ids) != len(set(capability_ids))
            ):
                raise CompatibilityModelError(
                    "profile_registry_duplicate"
                )
            state = self._surface_states[profile.surface]
            retired = (
                state.published.generation
                if state.published is not None
                else None
            )
            if retired is not None:
                self._retire_surface(profile.surface, retired)
            state.pending_generation = None
            self._profiles = tuple(
                sorted(
                    candidate,
                    key=lambda item: evidence_fingerprint(
                        item.to_mapping()
                    ),
                )
            )
            self._profile_registry_fingerprint = evidence_fingerprint(
                {
                    "profiles": [
                        item.to_mapping() for item in self._profiles
                    ],
                }
            )
            self._surface_profile_fingerprints[profile.surface] = (
                evidence_fingerprint(
                    {
                        "surface": profile.surface.value,
                        "profiles": [profile.to_mapping()],
                    }
                )
            )
            self._capability_surfaces = {
                capability.capability_id: item.surface
                for item in self._profiles
                for capability in item.capabilities
            }
            return retired

    def retire_surface_authority(
        self,
        surface: UpstreamSurface,
    ) -> int | None:
        """Retire published authority and every unused lease for one surface."""

        if not isinstance(surface, UpstreamSurface):
            raise CompatibilityModelError("surface_state_invalid")
        with self._lock:
            state = self._surface_states[surface]
            retired = (
                state.published.generation
                if state.published is not None
                else None
            )
            if retired is not None:
                self._retire_surface(surface, retired)
            state.pending_generation = None
            return retired

    def _authority_for_surface(
        self,
        authority: AuthorityBundle,
        surface: UpstreamSurface,
    ) -> AuthorityBundle:
        """Project one bundle to decisions targeting binary-known surface profiles."""

        profile_keys = {
            (profile.profile_id, profile.profile_version)
            for profile in self._profiles
            if profile.surface is surface
        }
        return AuthorityBundle(
            evaluated_at_epoch=authority.evaluated_at_epoch,
            decisions=tuple(
                decision
                for decision in authority.decisions
                if (decision.profile_id, decision.profile_version) in profile_keys
            ),
        )

    def _retire_surface(
        self,
        surface: UpstreamSurface,
        generation: int,
    ) -> None:
        state = self._surface_states[surface]
        diagnostics = state.retired_generation_diagnostics
        if diagnostics is None:
            raise CompatibilityModelError("retirement_state_invalid")
        diagnostics.append(generation)
        if len(diagnostics) > MAX_RETIREMENT_DIAGNOSTICS:
            del diagnostics[:-MAX_RETIREMENT_DIAGNOSTICS]
        state.published = None
        state.published_material_fingerprint = None
        for lease_id, lease in tuple(self._issued_leases.items()):
            if lease.surface is surface:
                self._issued_leases.pop(lease_id)

    def _record_capacity_exhaustion(self, reason_code: str) -> None:
        self._capacity_exhaustion_count = min(
            MAX_SAFE_INTEGER,
            self._capacity_exhaustion_count + 1,
        )
        self._capacity_exhaustion_reason = reason_code

    def _lifecycle_projection(self) -> dict[str, Any]:
        return {
            "issued_lease_count": len(self._issued_leases),
            "active_commit_count": len(self._active_commits),
            "retained_retirement_diagnostic_count": sum(
                len(state.retired_generation_diagnostics or ())
                for state in self._surface_states.values()
            ),
            "capacity_exhaustion_count": self._capacity_exhaustion_count,
            "capacity_exhaustion_reason": self._capacity_exhaustion_reason,
        }

    def begin_reconciliation(
        self,
        observation: CompatibilityObservation,
        authority: AuthorityBundle,
    ) -> ReconciliationAttempt:
        """Retire stale authority before any new verification can publish."""

        surface_authority = self._authority_for_surface(
            authority,
            observation.surface,
        )
        material_fingerprint = evidence_fingerprint(
            {
                "observation": observation.to_mapping(include_session=True),
                "authority": surface_authority.material_mapping(),
                "profile_registry_fingerprint": self._surface_profile_fingerprints[
                    observation.surface
                ],
            }
        )
        with self._lock:
            state = self._surface_states[observation.surface]
            if (
                state.published is not None
                and state.published_material_fingerprint == material_fingerprint
            ):
                return ReconciliationAttempt(
                    generation=state.published.generation,
                    observation=observation,
                    authority=surface_authority,
                    material_fingerprint=material_fingerprint,
                    retired_generation=None,
                    idempotent=True,
                    events=("observation_unchanged",),
                )

            retired_generation = None
            if state.published is not None:
                if self._next_generation >= MAX_SAFE_INTEGER:
                    self._record_capacity_exhaustion("generation_capacity_exhausted")
                    raise CompatibilityModelError("generation_capacity_exhausted")
                retired_generation = state.published.generation
                self._retire_surface(observation.surface, retired_generation)
            elif self._next_generation >= MAX_SAFE_INTEGER:
                self._record_capacity_exhaustion("generation_capacity_exhausted")
                raise CompatibilityModelError("generation_capacity_exhausted")
            self._next_generation += 1
            generation = self._next_generation
            state.pending_generation = generation
            state.last_observation_fingerprint = observation.fingerprint
            events = (
                (("generation_retired",) if retired_generation else ())
                + ("generation_created", "verification_started")
            )
            return ReconciliationAttempt(
                generation=generation,
                observation=observation,
                authority=surface_authority,
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
            state = self._surface_states[attempt.observation.surface]
            if attempt.idempotent:
                generation = state.published
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
            if state.pending_generation != attempt.generation:
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
                profile_registry_fingerprint=self._surface_profile_fingerprints[
                    attempt.observation.surface
                ],
                session_fingerprint=attempt.observation.session_fingerprint,
                decisions=decisions,
            )
            state.published = generation
            state.published_material_fingerprint = attempt.material_fingerprint
            state.pending_generation = None
            state.last_observation_fingerprint = generation.observation_fingerprint
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
            surface = self._capability_surfaces.get(capability_id)
            if surface is None:
                return None
            state = self._surface_states[surface]
            generation = state.published
            if (
                generation is None
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
            if len(self._issued_leases) >= MAX_ISSUED_LEASES:
                self._record_capacity_exhaustion("issued_lease_capacity_exhausted")
                return None
            if self._lease_counter >= MAX_SAFE_INTEGER:
                self._record_capacity_exhaustion("lease_sequence_exhausted")
                return None
            self._lease_counter += 1
            lease_id = evidence_fingerprint(
                {
                    "generation": generation.generation,
                    "surface": surface.value,
                    "capability_id": capability_id,
                    "adapter_id": decision.adapter_id,
                    "session_fingerprint": session_fingerprint,
                    "lease_ordinal": self._lease_counter,
                }
            )
            lease = RouteLease(
                lease_id=lease_id,
                generation=generation.generation,
                surface=surface,
                capability_id=capability_id,
                adapter_id=decision.adapter_id,
                session_fingerprint=session_fingerprint,
            )
            self._issued_leases[lease.lease_id] = lease
            return lease

    def validate_pre_dispatch(self, lease: RouteLease, *, session_id: str) -> bool:
        session_fingerprint = evidence_fingerprint({"session_id": session_id})
        with self._lock:
            stored = self._issued_leases.get(lease.lease_id)
            if stored != lease:
                return False
            state = self._surface_states[lease.surface]
            generation = state.published
            valid_generation = not (
                generation is None
                or generation.generation != lease.generation
                or generation.surface is not lease.surface
                or lease.session_fingerprint != session_fingerprint
                or generation.session_fingerprint != session_fingerprint
            )
            if not valid_generation:
                self._issued_leases.pop(lease.lease_id)
                return False
            decision = generation.decision_for(lease.capability_id)
            valid_decision = bool(
                decision
                and decision.disposition in _ADMITTED
                and decision.adapter_id == lease.adapter_id
            )
            if not valid_decision:
                self._issued_leases.pop(lease.lease_id)
            return valid_decision

    def commit_route(
        self,
        lease: RouteLease,
        *,
        session_id: str,
    ) -> DispatchCommit | None:
        """Atomically validate and mark a logical route committed; no I/O occurs."""

        with self._lock:
            stored = self._issued_leases.get(lease.lease_id)
            if stored != lease:
                return None
            if not self.validate_pre_dispatch(lease, session_id=session_id):
                self._issued_leases.pop(lease.lease_id, None)
                return None
            if len(self._active_commits) >= MAX_ACTIVE_COMMITS:
                self._record_capacity_exhaustion("active_commit_capacity_exhausted")
                return None
            commit_id = evidence_fingerprint(
                {
                    "lease_id": lease.lease_id,
                    "generation": lease.generation,
                    "surface": lease.surface.value,
                    "state": "committed_after_validation",
                }
            )
            commit = DispatchCommit(lease=lease, commit_id=commit_id)
            self._issued_leases.pop(lease.lease_id)
            self._active_commits[commit_id] = commit
            return commit

    def release_route(self, lease: RouteLease) -> bool:
        """Release one exact unused lease without changing surface authority."""

        with self._lock:
            stored = self._issued_leases.get(lease.lease_id)
            if stored != lease:
                return False
            self._issued_leases.pop(lease.lease_id)
            return True

    def finish_committed(self, commit: DispatchCommit) -> bool:
        """Allow completion without granting any route-publication authority."""

        with self._lock:
            stored = self._active_commits.get(commit.commit_id)
            if stored != commit:
                return False
            self._active_commits.pop(commit.commit_id)
            return True

    def health_projection(self) -> dict[str, Any]:
        """Return only fingerprints, counts, dispositions, and reason codes."""

        with self._lock:
            surfaces: list[dict[str, Any]] = []
            aggregate_reasons: dict[str, int] = {}
            admitted_count = 0
            quarantined_count = 0
            unavailable_count = 0
            dispositions: list[AdmissionDisposition] = []
            for surface in sorted(UpstreamSurface, key=lambda item: item.value):
                state = self._surface_states[surface]
                generation = state.published
                surface_issued = sum(
                    lease.surface is surface
                    for lease in self._issued_leases.values()
                )
                surface_commits = sum(
                    commit.lease.surface is surface
                    for commit in self._active_commits.values()
                )
                retirement_count = len(state.retired_generation_diagnostics or ())
                if generation is None:
                    disposition = (
                        AdmissionDisposition.VERIFYING
                        if state.pending_generation is not None
                        else AdmissionDisposition.UNAVAILABLE
                    )
                    surfaces.append(
                        {
                            "surface": surface.value,
                            "disposition": disposition.value,
                            "generation": state.pending_generation,
                            "admitted_count": 0,
                            "quarantined_count": 0,
                            "unavailable_count": 0,
                            "issued_lease_count": surface_issued,
                            "active_commit_count": surface_commits,
                            "retained_retirement_diagnostic_count": retirement_count,
                            "reason_counts": [],
                        }
                    )
                    dispositions.append(disposition)
                    continue
                reason_counts: dict[str, int] = {}
                for decision in generation.decisions:
                    reason_counts[decision.reason_code] = (
                        reason_counts.get(decision.reason_code, 0) + 1
                    )
                    aggregate_reasons[decision.reason_code] = (
                        aggregate_reasons.get(decision.reason_code, 0) + 1
                    )
                admitted = sum(
                    decision.disposition in _ADMITTED
                    for decision in generation.decisions
                )
                quarantined = sum(
                    decision.disposition is AdmissionDisposition.QUARANTINED
                    for decision in generation.decisions
                )
                unavailable = sum(
                    decision.disposition is AdmissionDisposition.UNAVAILABLE
                    for decision in generation.decisions
                )
                admitted_count += admitted
                quarantined_count += quarantined
                unavailable_count += unavailable
                dispositions.append(generation.disposition)
                surfaces.append(
                    {
                        "surface": surface.value,
                        "disposition": generation.disposition.value,
                        "generation": generation.generation,
                        "decision_fingerprint": generation.decision_fingerprint,
                        "observation_fingerprint": generation.observation_fingerprint,
                        "authority_fingerprint": generation.authority_fingerprint,
                        "profile_registry_fingerprint": generation.profile_registry_fingerprint,
                        "admitted_count": admitted,
                        "quarantined_count": quarantined,
                        "unavailable_count": unavailable,
                        "issued_lease_count": surface_issued,
                        "active_commit_count": surface_commits,
                        "retained_retirement_diagnostic_count": retirement_count,
                        "reason_counts": [
                            {"reason_code": code, "count": count}
                            for code, count in sorted(reason_counts.items())
                        ],
                    }
                )
            aggregate_disposition = (
                dispositions[0]
                if dispositions and len(set(dispositions)) == 1
                else AdmissionDisposition.PARTIAL
            )
            projection = {
                "model_version": 2,
                "disposition": aggregate_disposition.value,
                "surface_count": len(surfaces),
                "surfaces": surfaces,
                "admitted_count": admitted_count,
                "quarantined_count": quarantined_count,
                "unavailable_count": unavailable_count,
                "reason_counts": [
                    {"reason_code": code, "count": count}
                    for code, count in sorted(aggregate_reasons.items())
                ],
                **self._lifecycle_projection(),
                "fallback_count": 0,
            }
            evidence_fingerprint(projection)
            return projection

    def audit_projection(self, result: ReconciliationResult) -> dict[str, Any]:
        """Return a bounded event projection without raw identities or catalogs."""

        with self._lock:
            generation = result.generation
            projection: dict[str, Any] = {
                "model_version": 2,
                "event": "capability_reconciliation",
                "disposition": result.disposition.value,
                "published": result.published,
                "idempotent": result.idempotent,
                "reason_code": result.reason_code,
                "retired_generation": result.retired_generation,
                "events": list(result.events),
                **self._lifecycle_projection(),
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
        candidates = [
            decision
            for decision in authority.decisions
            if (
                decision.status in _DENYING
                and self._authority_selects_profile(decision, profile)
                and decision.matches_observation(observation)
                and (
                    not decision.capability_ids
                    or contract.capability_id in decision.capability_ids
                )
            )
        ]
        candidates.sort(
            key=lambda item: canonical_json(
                item.material_mapping(
                    evaluated_at_epoch=authority.evaluated_at_epoch,
                )
            )
        )
        return candidates[0] if candidates else None

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
                canonical_json(
                    item.material_mapping(
                        evaluated_at_epoch=authority.evaluated_at_epoch,
                    )
                ),
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
