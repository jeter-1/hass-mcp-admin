from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
from threading import Barrier
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
TESTS = ROOT / "tests"
FOUNDATION = TESTS / "fixtures" / "automatic_readmission" / "foundation_v1.json"
VECTORS = TESTS / "fixtures" / "automatic_readmission" / "contract_vectors_v2.json"
sys.path.insert(0, str(BETA))
sys.path.insert(0, str(TESTS))

from ha_mcp_engineering.ha_core_readmission import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_IDENTITY,
    CORE_PROTOCOL,
    SUPPORTED_CORE_RELEASES,
    CoreAuthoritySelection,
    CoreAuthoritySource,
    CoreAuthorityStatus,
    CoreCapabilityClass,
    CoreCapabilityEvidence,
    CoreCapabilityProfile,
    CoreDisposition,
    CoreObservationCollector,
    CoreReadmissionCoordinator,
    CoreReadmissionError,
    compiled_exact_authority,
    fingerprint,
    stable_observation,
)
from support.automatic_readmission import (  # noqa: E402
    OfflineUpdateHarness,
    ReferenceContractAdapter,
    UpstreamSurface,
    run_contract_vector,
)


def _profile(capability_id: str) -> CoreCapabilityProfile:
    return next(
        item for item in CORE_CAPABILITY_PROFILES if item.capability_id == capability_id
    )


def _evidence(
    *,
    omit: set[str] = frozenset(),
    unstable: set[str] = frozenset(),
    semantic_drift: set[str] = frozenset(),
    extra: tuple[dict, ...] = (),
) -> list[dict]:
    values = []
    for profile in CORE_CAPABILITY_PROFILES:
        if profile.capability_id in omit:
            continue
        checks = list(profile.required_checks)
        if profile.capability_id in unstable:
            checks = checks[:-1]
        values.append(
            {
                "capability_id": profile.capability_id,
                "passed_checks": checks,
                "semantic_fingerprint": (
                    (
                        "sha256:" + "f" * 64
                        if profile.capability_id in semantic_drift
                        else profile.contract_fingerprint
                    )
                    if profile.capability_class.semantic
                    else None
                ),
            }
        )
    values.extend(extra)
    return values


def _snapshot(
    version: str = "2026.8.2",
    *,
    identity: str = CORE_IDENTITY,
    rest_version: str | None = None,
    auth_version: str | None = None,
    config_version: str | None = None,
    session_id: str = "synthetic-core-session-1",
    connected: bool = True,
    authenticated: bool = True,
    evidence: list[dict] | None = None,
) -> dict:
    return {
        "identity": identity,
        "connected": connected,
        "authenticated": authenticated,
        "session_id": session_id,
        "rest_config": {"version": rest_version or version},
        "websocket_auth_ok": {
            "type": "auth_ok",
            "ha_version": auth_version or version,
        },
        "websocket_get_config": {"version": config_version or version},
        "capability_evidence": evidence if evidence is not None else _evidence(),
    }


def _observation(version: str = "2026.8.2", **kwargs):
    first = _snapshot(version, **kwargs)
    return stable_observation(first, deepcopy(first))


def _verified_authority(
    version: str = "2026.8.2",
    *,
    capability_ids: set[str] | None = None,
    status: CoreAuthorityStatus = CoreAuthorityStatus.POSITIVE,
) -> tuple[CoreAuthoritySelection, ...]:
    selected = capability_ids or {
        item.capability_id for item in CORE_CAPABILITY_PROFILES
    }
    return tuple(
        CoreAuthoritySelection(
            source=CoreAuthoritySource.VERIFIED_COMPATIBILITY,
            status=status,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            adapter_id=profile.adapter_id,
            subject_identity=CORE_IDENTITY,
            subject_version=version,
            protocol=CORE_PROTOCOL,
            capability_ids=(profile.capability_id,),
            reason_code="verified_compatible_release",
            evidence_fingerprint=fingerprint(
                {
                    "model": "synthetic-verified-core-authority-v1",
                    "version": version,
                    "profile": profile.profile_id,
                    "status": status.value,
                }
            ),
        )
        for profile in CORE_CAPABILITY_PROFILES
        if profile.capability_id in selected
    )


class _Source:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    async def capture_core_snapshot(self):
        value = self.snapshots.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class CoreObservationTests(unittest.TestCase):
    def test_rest_auth_and_get_config_versions_must_agree(self):
        observed = _observation(config_version="2026.8.1")
        self.assertFalse(observed.versions_agree)
        self.assertEqual(observed.reason_code, "core_version_disagreement")

    def test_two_stable_snapshots_retain_only_bounded_evidence(self):
        observed = _observation()
        self.assertTrue(observed.identity_agrees)
        self.assertTrue(observed.stable)
        self.assertEqual(observed.version, "2026.8.2")
        self.assertEqual(
            {item.capability_id for item in observed.capability_evidence},
            {item.capability_id for item in CORE_CAPABILITY_PROFILES},
        )
        encoded = json.dumps(observed.to_mapping())
        self.assertNotIn("synthetic-core-session-1", encoded)
        self.assertNotIn("endpoint", encoded)
        self.assertNotIn("token", encoded)

    def test_malformed_or_partial_config_evidence_fails_closed(self):
        malformed = _snapshot()
        malformed["rest_config"] = {"location_name": "synthetic"}
        observed = stable_observation(malformed, deepcopy(malformed))
        self.assertFalse(observed.complete)
        self.assertEqual(observed.reason_code, "malformed_core_evidence")

        malformed_version = _snapshot(version="not-a-core-version")
        observed = stable_observation(
            malformed_version,
            deepcopy(malformed_version),
        )
        self.assertFalse(observed.complete)
        self.assertEqual(observed.reason_code, "malformed_core_evidence")

    def test_session_and_identity_changes_fail_closed(self):
        first = _snapshot()
        second = _snapshot(session_id="synthetic-core-session-2")
        session_drift = stable_observation(first, second)
        self.assertEqual(session_drift.reason_code, "core_session_changed")
        second = _snapshot(identity="different-core-identity")
        identity_drift = stable_observation(first, second)
        self.assertEqual(identity_drift.reason_code, "core_identity_changed")

    def test_unstable_capability_holds_only_that_capability(self):
        first = _snapshot()
        second = _snapshot(evidence=_evidence(unstable={"core.basic_rest_read"}))
        observed = stable_observation(first, second)
        self.assertTrue(observed.stable)
        rest = observed.evidence_for("core.basic_rest_read")[0]
        websocket = observed.evidence_for("core.basic_websocket_read")[0]
        self.assertFalse(rest.stable)
        self.assertTrue(websocket.stable)

    def test_collector_sanitizes_source_failure(self):
        collector = CoreObservationCollector(
            _Source([RuntimeError("synthetic secret must not survive")])
        )
        observed = asyncio.run(collector.collect())
        self.assertEqual(observed.reason_code, "core_observation_unavailable")
        self.assertNotIn("secret", json.dumps(observed.to_mapping()))


class CoreCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)

    def test_compiled_exact_supported_releases_are_source_bound(self):
        self.assertEqual(
            tuple(item[0] for item in SUPPORTED_CORE_RELEASES),
            ("2026.7.2", "2026.8.0", "2026.8.1"),
        )
        for version, _commit, _image in SUPPORTED_CORE_RELEASES:
            result = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES).reconcile(
                _observation(version),
                compiled_exact_authority(version),
            )
            self.assertTrue(result.published)
            self.assertEqual(result.disposition, CoreDisposition.ADMITTED_EXACT)
            self.assertEqual(
                result.generation.decision_for("core.basic_rest_read").disposition,
                CoreDisposition.ADMITTED_EXACT,
            )
        self.assertEqual(compiled_exact_authority("2026.8.2"), ())

    def test_compatible_patch_restores_reads_without_restart(self):
        old = self.coordinator.reconcile(
            _observation("2026.8.1"),
            compiled_exact_authority("2026.8.1"),
        )
        updated = self.coordinator.reconcile(
            _observation("2026.8.2"),
            _verified_authority("2026.8.2"),
        )
        self.assertEqual(updated.previous_generation, old.generation.generation)
        self.assertGreater(updated.generation.generation, old.generation.generation)
        for capability in (
            "core.basic_rest_read",
            "core.basic_websocket_read",
            "core.entity_service_discovery",
            "core.registry_read",
            "core.dashboard_configuration_read",
            "core.typed_helper_operation",
        ):
            self.assertEqual(
                updated.generation.decision_for(capability).disposition,
                CoreDisposition.ADMITTED_COMPATIBLE,
            )

    def test_partial_readmission_does_not_disable_siblings(self):
        observation = _observation(
            evidence=_evidence(unstable={"core.basic_rest_read"})
        )
        result = self.coordinator.reconcile(
            observation,
            _verified_authority(),
        )
        self.assertEqual(result.disposition, CoreDisposition.PARTIAL)
        self.assertEqual(
            result.generation.decision_for("core.basic_rest_read").reason_code,
            "capability_contract_changed",
        )
        self.assertTrue(
            result.generation.decision_for("core.basic_websocket_read").disposition.admitted
        )

    def test_changed_service_contract_holds_discovery_only(self):
        result = self.coordinator.reconcile(
            _observation(
                evidence=_evidence(
                    unstable={"core.entity_service_discovery"}
                )
            ),
            _verified_authority(),
        )
        discovery = result.generation.decision_for(
            "core.entity_service_discovery"
        )
        registry = result.generation.decision_for("core.registry_read")
        self.assertEqual(discovery.reason_code, "capability_contract_changed")
        self.assertEqual(discovery.disposition, CoreDisposition.QUARANTINED)
        self.assertTrue(registry.disposition.admitted)

    def test_unknown_template_semantics_hold_only_semantic_path(self):
        result = self.coordinator.reconcile(
            _observation(
                evidence=_evidence(semantic_drift={"core.template_semantics"})
            ),
            _verified_authority(),
        )
        template = result.generation.decision_for("core.template_semantics")
        ordinary = result.generation.decision_for("core.basic_rest_read")
        self.assertEqual(template.reason_code, "semantic_contract_unverified")
        self.assertEqual(template.disposition, CoreDisposition.QUARANTINED)
        self.assertTrue(ordinary.disposition.admitted)

    def test_version_disagreement_and_identity_change_hold_all(self):
        for observation, reason in (
            (_observation(config_version="2026.8.1"), "core_version_disagreement"),
            (_observation(identity="different-core"), "core_identity_disagreement"),
        ):
            with self.subTest(reason=reason):
                result = CoreReadmissionCoordinator(
                    CORE_CAPABILITY_PROFILES
                ).reconcile(observation, _verified_authority())
                self.assertEqual(result.disposition, CoreDisposition.UNAVAILABLE)
                self.assertTrue(
                    all(
                        item.reason_code == reason
                        for item in result.generation.decisions
                        if item.capability_id
                        != "core.configuration_mutation_action"
                    )
                )

    def test_identical_reconciliation_is_idempotent(self):
        observation = _observation()
        authority = _verified_authority()
        first = self.coordinator.reconcile(observation, authority)
        second = self.coordinator.reconcile(observation, tuple(reversed(authority)))
        self.assertTrue(second.idempotent)
        self.assertEqual(
            first.generation.generation,
            second.generation.generation,
        )

    def test_late_verification_cannot_replace_newer_generation(self):
        older = self.coordinator.begin_reconciliation(
            _observation("2026.8.1"),
            compiled_exact_authority("2026.8.1"),
        )
        newer = self.coordinator.begin_reconciliation(
            _observation("2026.8.2"),
            _verified_authority("2026.8.2"),
        )
        stale = self.coordinator.complete_reconciliation(older)
        current = self.coordinator.complete_reconciliation(newer)
        self.assertFalse(stale.published)
        self.assertEqual(stale.reason_code, "verification_generation_stale")
        self.assertTrue(current.published)

    def test_unknown_profile_adapter_and_capability_fail_closed(self):
        authority = list(_verified_authority())
        authority[0] = replace(authority[0], adapter_id="unknown-adapter")
        result = self.coordinator.reconcile(_observation(), tuple(authority))
        self.assertTrue(
            all(
                item.reason_code == "authority_bundle_invalid"
                for item in result.generation.decisions
                if item.capability_id != "core.configuration_mutation_action"
            )
        )
        unknown = {
            "capability_id": "core.generic_forwarding",
            "passed_checks": ["generic_forwarding_claimed"],
            "semantic_fingerprint": None,
        }
        result = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES).reconcile(
            _observation(evidence=_evidence(extra=(unknown,))),
            _verified_authority(),
        )
        decision = result.generation.decision_for("core.generic_forwarding")
        self.assertEqual(decision.reason_code, "capability_not_compiled")
        self.assertEqual(decision.disposition, CoreDisposition.QUARANTINED)

    def test_unknown_capability_capacity_fails_closed_without_unbounded_report(self):
        unknown = [
            {
                "capability_id": f"core.unknown_{index:02d}",
                "passed_checks": ["untrusted_claim"],
                "semantic_fingerprint": None,
            }
            for index in range(64)
        ]
        result = self.coordinator.reconcile(
            _observation(evidence=unknown),
            _verified_authority(),
        )
        self.assertEqual(result.disposition, CoreDisposition.UNAVAILABLE)
        self.assertEqual(len(result.generation.decisions), len(CORE_CAPABILITY_PROFILES))
        self.assertTrue(
            all(
                item.reason_code == "capability_evidence_oversized"
                for item in result.generation.decisions
            )
        )
        self.assertLessEqual(
            len(json.dumps(self.coordinator.update_assessment()).encode("utf-8")),
            32_768,
        )

    def test_revocation_holds_selected_capability(self):
        authority = _verified_authority()
        revoked = replace(
            authority[0],
            status=CoreAuthorityStatus.REVOKED,
            reason_code="reviewed_capability_revoked",
        )
        result = self.coordinator.reconcile(
            _observation(),
            (revoked, *authority),
        )
        decision = result.generation.decision_for(revoked.capability_ids[0])
        self.assertEqual(decision.reason_code, "reviewed_capability_revoked")
        self.assertEqual(decision.disposition, CoreDisposition.QUARANTINED)

    def test_expired_rollback_and_replay_conflict_authority_fail_closed(self):
        capability_id = "core.basic_rest_read"
        for status, reason in (
            (CoreAuthorityStatus.EXPIRED, "verified_authority_expired"),
            (CoreAuthorityStatus.ROLLBACK, "verified_authority_rollback"),
            (
                CoreAuthorityStatus.REPLAY_CONFLICT,
                "verified_authority_replay_conflict",
            ),
        ):
            authority = _verified_authority(
                capability_ids={capability_id},
                status=status,
            )
            with self.subTest(status=status.value):
                result = CoreReadmissionCoordinator(
                    CORE_CAPABILITY_PROFILES
                ).reconcile(_observation(), authority)
                decision = result.generation.decision_for(capability_id)
                self.assertEqual(decision.reason_code, reason)
                self.assertFalse(decision.disposition.admitted)

    def test_core_generation_is_independent_from_other_surface_state(self):
        result = self.coordinator.reconcile(_observation(), _verified_authority())
        fake_ha_mcp_generation = 44
        fake_ha_mcp_generation += 1
        self.assertEqual(
            self.coordinator.current_generation.generation,
            result.generation.generation,
        )
        self.assertEqual(fake_ha_mcp_generation, 45)


class CoreLeaseTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        self.observation = _observation()
        self.result = self.coordinator.reconcile(
            self.observation,
            _verified_authority(),
        )

    def test_retired_generation_and_session_drift_are_rejected(self):
        lease = self.coordinator.acquire_route(
            "core.basic_rest_read",
            session_fingerprint=self.observation.session_fingerprint,
        )
        self.assertIsNotNone(lease)
        self.coordinator.reconcile(
            _observation("2026.8.3"),
            _verified_authority("2026.8.3"),
        )
        self.assertFalse(
            self.coordinator.validate_pre_dispatch(
                lease,
                observation=self.observation,
            )
        )

    def test_typed_helper_requires_exact_session_target_and_current_observation(self):
        target = fingerprint({"entity_id": "input_boolean.synthetic"})
        lease = self.coordinator.acquire_route(
            "core.typed_helper_operation",
            session_fingerprint=self.observation.session_fingerprint,
            target_fingerprint=target,
        )
        self.assertIsNotNone(lease)
        self.assertIsNone(
            self.coordinator.commit_route(
                lease,
                observation=self.observation,
                target_fingerprint=fingerprint({"entity_id": "input_boolean.other"}),
            )
        )
        commit = self.coordinator.commit_route(
            lease,
            observation=self.observation,
            target_fingerprint=target,
        )
        self.assertIsNotNone(commit)
        self.assertIsNone(
            self.coordinator.commit_route(
                lease,
                observation=self.observation,
                target_fingerprint=target,
            )
        )
        self.assertTrue(self.coordinator.finish_commit(commit))

    def test_same_generation_session_drift_prevents_commit(self):
        lease = self.coordinator.acquire_route(
            "core.basic_rest_read",
            session_fingerprint=self.observation.session_fingerprint,
        )
        drifted = _observation(session_id="synthetic-core-session-2")
        self.assertFalse(
            self.coordinator.validate_pre_dispatch(
                lease,
                observation=drifted,
            )
        )
        self.assertIsNone(
            self.coordinator.commit_route(
                lease,
                observation=drifted,
            )
        )

    def test_concurrent_duplicate_commit_has_one_winner(self):
        lease = self.coordinator.acquire_route(
            "core.basic_rest_read",
            session_fingerprint=self.observation.session_fingerprint,
        )
        barrier = Barrier(8)

        def commit_once():
            barrier.wait()
            return self.coordinator.commit_route(
                lease,
                observation=self.observation,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            commits = tuple(executor.submit(commit_once) for _index in range(8))
            values = tuple(item.result(timeout=5) for item in commits)
        self.assertEqual(sum(item is not None for item in values), 1)

    def test_cross_surface_and_unregistered_lease_fail_closed(self):
        lease = self.coordinator.acquire_route(
            "core.basic_rest_read",
            session_fingerprint=self.observation.session_fingerprint,
        )
        self.assertIsNotNone(lease)
        self.assertFalse(
            self.coordinator.validate_pre_dispatch(
                object(),
                observation=self.observation,
            )
        )
        self.assertFalse(self.coordinator.release_route(replace(lease, lease_id="sha256:" + "a" * 64)))

    def test_no_generic_forward_or_provider_dispatch_surface_exists(self):
        self.assertFalse(hasattr(self.coordinator, "dispatch"))
        self.assertFalse(hasattr(self.coordinator, "forward"))
        report = self.coordinator.update_assessment()
        self.assertEqual(report["fallback_count"], 0)

    def test_lease_capacity_is_bounded_and_recovers_after_release(self):
        leases = tuple(
            self.coordinator.acquire_route(
                "core.basic_rest_read",
                session_fingerprint=self.observation.session_fingerprint,
            )
            for _index in range(64)
        )
        self.assertTrue(all(lease is not None for lease in leases))
        self.assertIsNone(
            self.coordinator.acquire_route(
                "core.basic_rest_read",
                session_fingerprint=self.observation.session_fingerprint,
            )
        )
        self.assertTrue(self.coordinator.release_route(leases[0]))
        self.assertIsNotNone(
            self.coordinator.acquire_route(
                "core.basic_rest_read",
                session_fingerprint=self.observation.session_fingerprint,
            )
        )
        report = self.coordinator.health_projection()
        self.assertEqual(report["capacity_exhaustion_count"], 1)
        self.assertEqual(
            report["capacity_exhaustion_reason"],
            "issued_lease_capacity_exhausted",
        )


class CoreProjectionTests(unittest.TestCase):
    def test_reports_are_bounded_sanitized_and_actionable(self):
        coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        coordinator.reconcile(_observation(), _verified_authority())
        report = coordinator.update_assessment()
        health = coordinator.health_projection()
        for value in (report, health):
            encoded = json.dumps(value, sort_keys=True)
            self.assertLessEqual(len(encoded.encode("utf-8")), 32_768)
            self.assertNotIn("synthetic-core-session", encoded)
            self.assertNotIn("endpoint", encoded)
            self.assertNotIn("token", encoded)
            self.assertNotIn("exception", encoded)
            self.assertEqual(value["fallback_count"], 0)
        self.assertEqual(report["observed_core_version"], "2026.8.2")
        self.assertEqual(report["identity_agreement"], True)
        self.assertGreater(report["compatible_count"], 0)
        self.assertFalse(report["engineering_code_change_required"])
        self.assertFalse(report["compatibility_data_update_required"])
        self.assertIn(
            "core.configuration_mutation_action",
            {
                item["capability_id"] for item in report["held_capabilities"]
            },
        )

    def test_report_distinguishes_code_and_compatibility_data_changes(self):
        code = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        code.reconcile(
            _observation(
                evidence=_evidence(semantic_drift={"core.template_semantics"})
            ),
            _verified_authority(),
        )
        self.assertTrue(code.update_assessment()["engineering_code_change_required"])

        data = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        data.reconcile(_observation(), ())
        report = data.update_assessment()
        self.assertFalse(report["engineering_code_change_required"])
        self.assertTrue(report["compatibility_data_update_required"])


class _VectorAllocator:
    def __init__(self):
        self.next_value = None

    def next_generation(self):
        if self.next_value is None:
            raise AssertionError("vector generation was not prepared")
        value = self.next_value
        self.next_value = None
        return value


class ProductionCoreVectorAdapter:
    """Test-only bridge; production never imports ADR-020 test support."""

    def __init__(self, foundation: dict):
        self.foundation = deepcopy(foundation)
        self.reference = ReferenceContractAdapter(self.foundation)
        harness = OfflineUpdateHarness.from_mapping(self.foundation)
        self.profiles = tuple(
            self._profile_from_reference(item)
            for item in harness.profiles
            if item.surface.value == "home_assistant_core"
        )
        self.allocator = _VectorAllocator()
        self.coordinator = CoreReadmissionCoordinator(
            self.profiles,
            generation_allocator=self.allocator,
        )
        self.leases = {}
        self.last_observation = None

    @staticmethod
    def _profile_from_reference(item):
        classes = {
            "ordinary_read": CoreCapabilityClass.BASIC_REST_READ,
            "registry_read": CoreCapabilityClass.REGISTRY_READ,
            "template_semantics": CoreCapabilityClass.TEMPLATE_SEMANTICS,
            "configuration_semantics": CoreCapabilityClass.TEMPLATE_SEMANTICS,
            "governed_write": CoreCapabilityClass.CONFIGURATION_MUTATION_ACTION,
        }
        capability = item.capabilities[0]
        return CoreCapabilityProfile(
            capability_id=capability.capability_id,
            capability_class=classes[capability.kind.value],
            profile_id=item.profile_id,
            profile_version=item.profile_version,
            adapter_id=item.adapter_id,
            contract_fingerprint=capability.contract_fingerprint,
            required_checks=("vector_contract_match",),
            auto_eligible=capability.kind.value != "governed_write",
        )

    def _fixture(self, arguments):
        value = deepcopy(self.foundation)
        for mutation in arguments.get("fixture_mutations", []):
            parent = value
            path = mutation["path"]
            for token in path[:-1]:
                parent = parent[token]
            if mutation["operation"] != "set":
                raise AssertionError("Core vector bridge only accepts set mutations")
            parent[path[-1]] = mutation["value"]
        return OfflineUpdateHarness.from_mapping(value)

    def _observation(self, harness, observation_id):
        source = harness.observation(observation_id)
        profiles = {item.capability_id: item for item in self.profiles}
        evidence = []
        for item in source.capabilities:
            profile = profiles.get(item.capability_id)
            if profile is None:
                continue
            evidence.append(
                CoreCapabilityEvidence(
                    capability_id=item.capability_id,
                    passed_checks=("vector_contract_match",),
                    semantic_fingerprint=(
                        item.contract_fingerprint
                        if profile.capability_class.semantic
                        else None
                    ),
                )
            )
        reason = (
            "transport_unavailable"
            if not source.connected
            else "authentication_failed"
            if not source.authenticated
            else "core_version_disagreement"
            if not source.core_versions_agree
            else "observation_complete"
        )
        return __import__(
            "ha_mcp_engineering.ha_core_readmission",
            fromlist=["CoreObservation"],
        ).CoreObservation(
            identity=source.identity,
            version=source.version,
            protocol=source.protocol_version,
            rest_version=source.core_rest_version,
            websocket_auth_version=source.core_websocket_auth_version,
            websocket_config_version=source.core_websocket_config_version,
            session_fingerprint=fingerprint(
                {
                    "model": "ha-core-authenticated-session-v1",
                    "session_id": source.session_id,
                }
            ),
            capability_evidence=tuple(evidence),
            connected=source.connected,
            authenticated=source.authenticated,
            complete=source.catalog_complete,
            stable=True,
            reason_code=reason,
        )

    def _authority(self, harness, arguments):
        identifiers = arguments.get("authority_ids") or [arguments["authority_id"]]
        profiles = {
            (item.profile_id, item.profile_version): item for item in self.profiles
        }
        output = []
        for identifier in identifiers:
            for item in harness.authority(identifier).decisions:
                if (item.profile_id, item.profile_version) not in profiles:
                    continue
                output.append(
                    CoreAuthoritySelection(
                        source=(
                            CoreAuthoritySource.COMPILED_EXACT
                            if item.source.value == "compiled_exact"
                            else CoreAuthoritySource.VERIFIED_COMPATIBILITY
                        ),
                        status=CoreAuthorityStatus(item.status.value),
                        profile_id=item.profile_id,
                        profile_version=item.profile_version,
                        adapter_id=item.adapter_id,
                        subject_identity=item.subject_identity,
                        subject_version=item.subject_version,
                        protocol=item.protocol_version,
                        capability_ids=item.capability_ids,
                        reason_code=item.reason_code,
                        evidence_fingerprint=fingerprint(item.to_mapping()),
                    )
                )
        return tuple(output)

    @staticmethod
    def _normalized(result):
        generation = result.generation
        reason_alias = {
            "automatic_core_admission_prohibited": "write_capability_prohibited",
        }
        return {
            "surface": "home_assistant_core",
            "generation": generation.generation if generation else None,
            "retired_generation": result.previous_generation,
            "published": result.published,
            "idempotent": result.idempotent,
            "disposition": result.disposition.value,
            "admitted": sorted(
                item.capability_id
                for item in generation.decisions
                if item.disposition.admitted
            ) if generation else [],
            "quarantined": sorted(
                (
                    {
                        "capability_id": item.capability_id,
                        "reason_code": reason_alias.get(item.reason_code, item.reason_code),
                    }
                    for item in generation.decisions
                    if item.disposition is CoreDisposition.QUARANTINED
                ),
                key=lambda item: item["capability_id"],
            ) if generation else [],
            "unavailable": sorted(
                (
                    {
                        "capability_id": item.capability_id,
                        "reason_code": item.reason_code,
                    }
                    for item in generation.decisions
                    if item.disposition is CoreDisposition.UNAVAILABLE
                ),
                key=lambda item: item["capability_id"],
            ) if generation else [],
            "write_action_reachability": 0,
        }

    def execute(self, operation, arguments):
        reference = self.reference.execute(operation, arguments)
        if operation == "acquire_lease" and str(arguments.get("capability_id", "")).startswith("core."):
            lease = self.coordinator.acquire_route(
                arguments["capability_id"],
                session_fingerprint=fingerprint(
                    {
                        "model": "ha-core-authenticated-session-v1",
                        "session_id": arguments["session_id"],
                    }
                ),
            )
            if lease:
                self.leases[arguments["lease_id"]] = lease
            actual = {
                "granted": lease is not None,
                "surface": lease.surface if lease else None,
                "generation": lease.generation if lease else None,
            }
            if actual != reference:
                raise AssertionError((actual, reference))
            return actual
        if operation == "validate_lease" and arguments.get("lease_id") in self.leases:
            lease = self.leases[arguments["lease_id"]]
            actual = {
                "valid": self.coordinator.validate_pre_dispatch(
                    lease,
                    observation=self.last_observation,
                )
            }
            if actual != reference:
                raise AssertionError((actual, reference))
            return actual
        observation_id = arguments.get("observation_id")
        if operation not in {"reconcile", "probe_capability"} or not observation_id:
            return reference
        harness = self._fixture(arguments)
        is_core_observation = (
            harness.observation(observation_id).surface.value
            == "home_assistant_core"
        )
        if operation == "reconcile" and is_core_observation:
            observation = self._observation(harness, observation_id)
            self.last_observation = observation
            self.allocator.next_value = reference["generation"]
            result = self.coordinator.reconcile(
                observation,
                self._authority(harness, arguments),
            )
            actual = self._normalized(result)
            if actual != reference:
                raise AssertionError((actual, reference))
            return actual
        if operation == "probe_capability" and is_core_observation:
            observation = self._observation(harness, observation_id)
            self.last_observation = observation
            reference_generation = self.reference._coordinator.generation_for(
                UpstreamSurface.HOME_ASSISTANT_CORE
            )
            self.allocator.next_value = reference_generation.generation
            result = self.coordinator.reconcile(
                observation,
                self._authority(harness, arguments),
            )
            decision = result.generation.decision_for(arguments["capability_id"])
            lease = self.coordinator.acquire_route(
                arguments["capability_id"],
                session_fingerprint=fingerprint(
                    {
                        "model": "ha-core-authenticated-session-v1",
                        "session_id": arguments["session_id"],
                    }
                ),
            )
            commit = (
                self.coordinator.commit_route(lease, observation=observation)
                if lease
                else None
            )
            reason = {
                "verified_compatible_contract_matched": "signed_compatible_contract_matched",
            }.get(decision.reason_code, decision.reason_code)
            actual = {
                "disposition": decision.disposition.value,
                "reason_code": reason,
                "adapter_present": decision.adapter_id is not None,
                "lease_granted": lease is not None,
                "committed": commit is not None,
                "fallback_count": 0,
                "write_action_reachability": 0,
            }
            if actual != reference:
                raise AssertionError((actual, reference))
            return actual
        return reference


class Adr020ProductionCoreVectorTests(unittest.TestCase):
    def test_every_core_vector_matches_through_production_adapter(self):
        foundation = json.loads(FOUNDATION.read_text(encoding="utf-8"))
        suite = json.loads(VECTORS.read_text(encoding="utf-8"))
        selected = [
            vector
            for vector in suite["vectors"]
            if "core" in json.dumps(vector, sort_keys=True)
        ]
        self.assertGreaterEqual(len(selected), 5)
        reports = [
            run_contract_vector(vector, ProductionCoreVectorAdapter(foundation))
            for vector in selected
        ]
        self.assertTrue(all(item["matched"] for item in reports), reports)
        self.assertGreater(sum(item["step_count"] for item in reports), 20)


class CoreRuntimeIsolationTests(unittest.TestCase):
    def test_application_routing_registration_and_clients_do_not_import_component(self):
        paths = (
            BETA / "ha_mcp_engineering" / "application.py",
            BETA / "ha_mcp_engineering" / "mcp_server.py",
            BETA / "ha_mcp_engineering" / "capabilities.py",
            BETA / "ha_mcp_engineering" / "providers" / "routing.py",
            BETA / "ha_mcp_engineering" / "providers" / "dispatch.py",
            BETA / "ha_mcp_engineering" / "clients" / "rest.py",
            BETA / "ha_mcp_engineering" / "clients" / "websocket.py",
        )
        for path in paths:
            self.assertNotIn("ha_core_readmission", path.read_text(encoding="utf-8"), path)

    def test_production_package_never_imports_test_support(self):
        package = BETA / "ha_mcp_engineering" / "ha_core_readmission"
        for path in package.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("support.automatic_readmission", text, path)
            self.assertNotIn("tests.", text, path)


if __name__ == "__main__":
    unittest.main()
