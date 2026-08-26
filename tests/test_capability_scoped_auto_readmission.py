from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
RUNTIME = BETA / "ha_mcp_engineering"
FIXTURE = ROOT / "tests" / "fixtures" / "capability_readmission" / "foundation_v1.json"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.compatibility import (  # noqa: E402
    AdmissionDisposition,
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityAdmissionCoordinator,
    CapabilityKind,
    CompatibilityModelError,
    ObservedCapability,
    OfflineUpdateHarness,
    UpstreamSurface,
    canonical_json,
    classify_registry_refresh,
)
from ha_mcp_engineering.compatibility.models import (  # noqa: E402
    MAX_AUTHORITY_DECISIONS,
    MAX_OBSERVED_CAPABILITIES,
    MAX_PROJECTION_BYTES,
)


def fixture_mapping() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def decision(result, capability_id):
    if result.generation is None:
        raise AssertionError("expected a published generation")
    value = result.generation.decision_for(capability_id)
    if value is None:
        raise AssertionError(f"missing decision for {capability_id}")
    return value


class HarnessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = fixture_mapping()
        self.harness = OfflineUpdateHarness.from_mapping(self.mapping)

    def test_fixture_is_synthetic_bounded_and_deterministic(self):
        raw = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("http://", raw)
        self.assertNotIn("https://", raw)
        self.assertNotIn("supervisor", raw.lower())
        self.assertNotIn("nabu", raw.lower())
        self.assertNotIn("bearer", raw.lower())
        first = self.harness.observation("ha_mcp_exact")
        second = OfflineUpdateHarness.from_mapping(
            json.loads(raw)
        ).observation("ha_mcp_exact")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            self.harness.authority("compiled_ha_mcp_exact").fingerprint,
            OfflineUpdateHarness.from_mapping(json.loads(raw))
            .authority("compiled_ha_mcp_exact")
            .fingerprint,
        )

    def test_harness_models_core_rest_websocket_and_complete_mcp_catalog(self):
        core = self.harness.observation("core_unknown_compatible")
        self.assertTrue(core.core_versions_agree)
        self.assertEqual(core.core_rest_version, "2026.9.0-synthetic")
        mcp = self.harness.observation("ha_mcp_exact")
        self.assertTrue(mcp.catalog_complete)
        self.assertEqual(len(mcp.capabilities), 3)

    def test_catalog_addition_removal_duplicate_and_drift_are_representable(self):
        exact = self.harness.observation("ha_mcp_exact")
        added = replace(
            exact,
            capabilities=exact.capabilities
            + (
                ObservedCapability(
                    capability_id="synthetic_future_read",
                    kind=CapabilityKind.ORDINARY_READ,
                    contract_fingerprint="sha256:" + "d" * 64,
                ),
            ),
        )
        removed = replace(exact, capabilities=exact.capabilities[:1])
        duplicate = replace(exact, capabilities=exact.capabilities + (exact.capabilities[0],))
        drifted = replace(
            exact,
            capabilities=(
                exact.capabilities[0],
                replace(exact.capabilities[1], contract_fingerprint="sha256:" + "e" * 64),
                exact.capabilities[2],
            ),
        )
        self.assertEqual(len(added.capabilities), 4)
        self.assertEqual(len(removed.capabilities), 1)
        self.assertEqual(duplicate.duplicate_capability_ids, {"ha_get_state"})
        self.assertNotEqual(exact.fingerprint, drifted.fingerprint)
        self.assertEqual(
            exact.fingerprint,
            replace(exact, capabilities=tuple(reversed(exact.capabilities))).fingerprint,
        )

    def test_harness_rejects_unknown_fields_and_oversized_catalog(self):
        malformed = deepcopy(self.mapping)
        malformed["scenarios"]["ha_mcp_exact"]["unexpected"] = True
        harness = OfflineUpdateHarness.from_mapping(malformed)
        with self.assertRaisesRegex(CompatibilityModelError, "harness_ha_mcp_fields_invalid"):
            harness.observation("ha_mcp_exact")

        exact = self.harness.observation("ha_mcp_exact")
        with self.assertRaisesRegex(CompatibilityModelError, "observed_catalog_oversized"):
            replace(
                exact,
                capabilities=tuple(
                    ObservedCapability(
                        capability_id=f"synthetic_capability_{index}",
                        kind=CapabilityKind.ORDINARY_READ,
                        contract_fingerprint="sha256:" + "f" * 64,
                    )
                    for index in range(MAX_OBSERVED_CAPABILITIES + 1)
                ),
            )


class AuthorityAndAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = OfflineUpdateHarness.from_mapping(fixture_mapping())

    def coordinator(self):
        return CapabilityAdmissionCoordinator(self.harness.profiles)

    def test_identical_reconciliation_is_idempotent(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_exact")
        authority = self.harness.authority("compiled_ha_mcp_exact")
        first = coordinator.reconcile(observation, authority)
        second = coordinator.reconcile(observation, authority)
        self.assertEqual(first.generation, second.generation)
        self.assertEqual(first.generation.generation, 1)
        self.assertTrue(second.idempotent)
        self.assertEqual(second.events, ("observation_unchanged",))

    def test_version_change_retires_before_verification(self):
        coordinator = self.coordinator()
        first = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        self.assertIsNotNone(lease)
        attempt = coordinator.begin_reconciliation(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        self.assertEqual(attempt.retired_generation, first.generation.generation)
        self.assertEqual(
            attempt.events,
            ("generation_retired", "generation_created", "verification_started"),
        )
        self.assertIsNone(coordinator.current_generation)
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                lease, session_id="synthetic-session-ha-mcp-1"
            )
        )
        completed = coordinator.complete_reconciliation(attempt)
        self.assertEqual(completed.generation.generation, 2)
        self.assertEqual(completed.disposition, AdmissionDisposition.ADMITTED_COMPATIBLE)

    def test_one_changed_read_is_quarantined_while_other_read_remains(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_exact")
        changed = replace(
            observation,
            capabilities=(
                observation.capabilities[0],
                replace(
                    observation.capabilities[1],
                    contract_fingerprint="sha256:" + "c" * 64,
                ),
                observation.capabilities[2],
            ),
        )
        result = coordinator.reconcile(
            changed, self.harness.authority("compiled_ha_mcp_exact")
        )
        self.assertEqual(result.disposition, AdmissionDisposition.PARTIAL)
        self.assertEqual(
            decision(result, "ha_get_state").disposition,
            AdmissionDisposition.ADMITTED_EXACT,
        )
        self.assertEqual(
            decision(result, "ha_search").reason_code,
            "capability_contract_changed",
        )
        self.assertIsNotNone(
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
        )
        self.assertIsNone(
            coordinator.acquire_route(
                "ha_search", session_id="synthetic-session-ha-mcp-1"
            )
        )

    def test_unknown_additions_never_gain_routes_or_reduce_exact_read_admission(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_exact")
        future = ObservedCapability(
            capability_id="synthetic_future_read",
            kind=CapabilityKind.ORDINARY_READ,
            contract_fingerprint="sha256:" + "d" * 64,
        )
        result = coordinator.reconcile(
            replace(observation, capabilities=observation.capabilities + (future,)),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        self.assertEqual(result.disposition, AdmissionDisposition.ADMITTED_EXACT)
        self.assertEqual(
            decision(result, "synthetic_future_read").reason_code,
            "capability_not_compiled",
        )
        self.assertIsNone(
            coordinator.acquire_route(
                "synthetic_future_read", session_id="synthetic-session-ha-mcp-1"
            )
        )

    def test_action_and_write_capabilities_are_unreachable_under_every_authority(self):
        exact_observation = self.harness.observation("ha_mcp_exact")
        compatible_observation = self.harness.observation("ha_mcp_compatible")
        cases = (
            (
                exact_observation,
                self.harness.authority("compiled_ha_mcp_exact"),
            ),
            (
                compatible_observation,
                self.harness.authority("signed_ha_mcp_compatible"),
            ),
            (
                exact_observation,
                AuthorityBundle(
                    evaluated_at_epoch=1800000000,
                    decisions=tuple(
                        replace(
                            item,
                            source=AuthoritySource.LIVE_OBSERVATION,
                            subject_version="1.0.0-synthetic",
                            registry_sequence=None,
                            registry_digest=None,
                            expires_at_epoch=None,
                        )
                        for item in self.harness.authority(
                            "compiled_ha_mcp_exact"
                        ).decisions
                    ),
                ),
            ),
        )
        for kind in (
            CapabilityKind.GOVERNED_WRITE,
            CapabilityKind.ACTION,
            CapabilityKind.PERSISTENT_WRITE,
            CapabilityKind.MIXED,
        ):
            profiles = tuple(
                replace(
                    profile,
                    capabilities=tuple(
                        replace(contract, kind=kind)
                        for contract in profile.capabilities
                    ),
                )
                if profile.profile_id == "ha_mcp_write_v1"
                else profile
                for profile in self.harness.profiles
            )
            for base_observation, authority in cases:
                observation = replace(
                    base_observation,
                    capabilities=tuple(
                        replace(item, kind=kind)
                        if item.capability_id == "ha_call_service"
                        else item
                        for item in base_observation.capabilities
                    ),
                )
                source = authority.decisions[0].source.value
                with self.subTest(kind=kind.value, source=source):
                    coordinator = CapabilityAdmissionCoordinator(profiles)
                    result = coordinator.reconcile(observation, authority)
                    self.assertEqual(
                        decision(result, "ha_call_service").reason_code,
                        "write_capability_prohibited",
                    )
                    self.assertIsNone(
                        coordinator.acquire_route(
                            "ha_call_service", session_id=observation.session_id
                        )
                    )

        for kind in (
            CapabilityKind.ACTION,
            CapabilityKind.PERSISTENT_WRITE,
            CapabilityKind.MIXED,
        ):
            with self.subTest(unreviewed_kind=kind.value):
                coordinator = self.coordinator()
                addition = ObservedCapability(
                    capability_id=f"synthetic_{kind.value}_tool",
                    kind=kind,
                    contract_fingerprint="sha256:" + "9" * 64,
                )
                result = coordinator.reconcile(
                    replace(
                        exact_observation,
                        capabilities=exact_observation.capabilities + (addition,),
                    ),
                    self.harness.authority("compiled_ha_mcp_exact"),
                )
                self.assertEqual(
                    decision(result, addition.capability_id).reason_code,
                    "write_capability_prohibited",
                )
                self.assertEqual(
                    result.disposition,
                    AdmissionDisposition.ADMITTED_EXACT,
                )
                self.assertIsNone(
                    coordinator.acquire_route(
                        addition.capability_id,
                        session_id=exact_observation.session_id,
                    )
                )

    def test_signed_revocation_overrides_compiled_positive_authority(self):
        compiled = self.harness.authority("compiled_ha_mcp_exact")
        retained_revocation = AuthorityDecision(
            source=AuthoritySource.SIGNED_REGISTRY,
            status=AuthorityStatus.DENY_ONLY,
            profile_id="ha_mcp_read_v1",
            profile_version=1,
            adapter_id="compiled_ha_mcp_read_adapter_v1",
            subject_identity="synthetic-ha-mcp",
            subject_version="1.0.0-synthetic",
            protocol_version="2025-06-18",
            capability_ids=(),
            reason_code="retained_signed_revocation",
            registry_sequence=13,
            registry_digest="sha256:" + "a" * 64,
            expires_at_epoch=1700000000,
        )
        result = self.coordinator().reconcile(
            self.harness.observation("ha_mcp_exact"),
            AuthorityBundle(
                evaluated_at_epoch=1800000000,
                decisions=compiled.decisions + (retained_revocation,),
            ),
        )
        self.assertEqual(
            decision(result, "ha_get_state").reason_code,
            "retained_signed_revocation",
        )
        self.assertEqual(
            decision(result, "ha_search").disposition,
            AdmissionDisposition.QUARANTINED,
        )

    def test_expired_signed_positive_cannot_admit_remote_only_release(self):
        signed = self.harness.authority("signed_ha_mcp_compatible")
        expired = AuthorityBundle(
            evaluated_at_epoch=1800000000,
            decisions=tuple(
                replace(item, expires_at_epoch=1799999999) for item in signed.decisions
            ),
        )
        result = self.coordinator().reconcile(
            self.harness.observation("ha_mcp_compatible"), expired
        )
        self.assertEqual(
            decision(result, "ha_get_state").reason_code,
            "signed_positive_authority_expired",
        )
        self.assertEqual(result.disposition, AdmissionDisposition.UNAVAILABLE)

    def test_signed_rollback_and_replay_conflict_fail_closed(self):
        signed = self.harness.authority("signed_ha_mcp_compatible")
        for status, reason in (
            (AuthorityStatus.ROLLBACK, "signed_registry_rollback"),
            (AuthorityStatus.REPLAY_CONFLICT, "signed_registry_replay_conflict"),
        ):
            with self.subTest(status=status.value):
                invalid = AuthorityBundle(
                    evaluated_at_epoch=signed.evaluated_at_epoch,
                    decisions=tuple(replace(item, status=status) for item in signed.decisions),
                )
                result = self.coordinator().reconcile(
                    self.harness.observation("ha_mcp_compatible"), invalid
                )
                self.assertEqual(decision(result, "ha_get_state").reason_code, reason)
                self.assertFalse(result.generation.admitted_capability_ids)

    def test_registry_sequence_refresh_detects_idempotence_rollback_and_conflict(self):
        current = "sha256:" + "a" * 64
        newer = "sha256:" + "b" * 64
        initial = classify_registry_refresh(
            current_sequence=None,
            current_digest=None,
            candidate_sequence=10,
            candidate_digest=current,
        )
        self.assertTrue(initial.accepted)
        identical = classify_registry_refresh(
            current_sequence=10,
            current_digest=current,
            candidate_sequence=10,
            candidate_digest=current,
        )
        self.assertTrue(identical.idempotent)
        rollback = classify_registry_refresh(
            current_sequence=10,
            current_digest=current,
            candidate_sequence=9,
            candidate_digest=newer,
        )
        self.assertEqual(rollback.status, AuthorityStatus.ROLLBACK)
        self.assertFalse(rollback.accepted)
        conflict = classify_registry_refresh(
            current_sequence=10,
            current_digest=current,
            candidate_sequence=10,
            candidate_digest=newer,
        )
        self.assertEqual(conflict.status, AuthorityStatus.REPLAY_CONFLICT)
        self.assertFalse(conflict.accepted)
        accepted = classify_registry_refresh(
            current_sequence=10,
            current_digest=current,
            candidate_sequence=11,
            candidate_digest=newer,
        )
        self.assertTrue(accepted.accepted)
        self.assertFalse(accepted.idempotent)

    def test_removed_reviewed_read_is_unavailable_without_harming_sibling(self):
        coordinator = self.coordinator()
        exact = self.harness.observation("ha_mcp_exact")
        removed = replace(
            exact,
            capabilities=tuple(
                item for item in exact.capabilities if item.capability_id != "ha_search"
            ),
        )
        result = coordinator.reconcile(
            removed, self.harness.authority("compiled_ha_mcp_exact")
        )
        self.assertEqual(result.disposition, AdmissionDisposition.PARTIAL)
        self.assertEqual(decision(result, "ha_search").reason_code, "capability_missing")
        self.assertTrue(decision(result, "ha_get_state").disposition.admitted)

    def test_live_observation_never_creates_authority(self):
        compiled = self.harness.authority("compiled_ha_mcp_exact")
        live = AuthorityBundle(
            evaluated_at_epoch=compiled.evaluated_at_epoch,
            decisions=tuple(
                replace(item, source=AuthoritySource.LIVE_OBSERVATION)
                for item in compiled.decisions
            ),
        )
        result = self.coordinator().reconcile(
            self.harness.observation("ha_mcp_exact"), live
        )
        self.assertEqual(result.disposition, AdmissionDisposition.UNAVAILABLE)
        self.assertEqual(
            decision(result, "ha_get_state").reason_code,
            "live_observation_not_authority",
        )

    def test_signed_data_cannot_select_unknown_adapter_profile_or_capability(self):
        signed = self.harness.authority("signed_ha_mcp_compatible")
        mutations = (
            replace(signed.decisions[0], adapter_id="synthetic_unknown_adapter"),
            replace(signed.decisions[0], profile_id="synthetic_unknown_profile"),
            replace(
                signed.decisions[0],
                capability_ids=("ha_get_state", "synthetic_invented_tool"),
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation.adapter_id, profile=mutation.profile_id):
                authority = AuthorityBundle(
                    evaluated_at_epoch=signed.evaluated_at_epoch,
                    decisions=(mutation, signed.decisions[1]),
                )
                result = self.coordinator().reconcile(
                    self.harness.observation("ha_mcp_compatible"), authority
                )
                self.assertFalse(decision(result, "ha_get_state").disposition.admitted)

    def test_identity_and_protocol_disagreement_prevent_admission(self):
        exact = self.harness.observation("ha_mcp_exact")
        authority = self.harness.authority("compiled_ha_mcp_exact")
        for changed in (
            replace(exact, identity="synthetic-other-server"),
            replace(exact, protocol_version="2024-11-05"),
        ):
            with self.subTest(identity=changed.identity, protocol=changed.protocol_version):
                result = self.coordinator().reconcile(changed, authority)
                self.assertIn(
                    decision(result, "ha_get_state").reason_code,
                    {"profile_identity_disagreement", "profile_protocol_disagreement"},
                )

    def test_incomplete_or_duplicate_catalog_fails_closed_per_capability(self):
        exact = self.harness.observation("ha_mcp_exact")
        authority = self.harness.authority("compiled_ha_mcp_exact")
        incomplete = replace(exact, catalog_complete=False, evidence_reason="catalog_truncated")
        result = self.coordinator().reconcile(incomplete, authority)
        self.assertEqual(decision(result, "ha_get_state").reason_code, "catalog_incomplete")

        duplicate = replace(exact, capabilities=exact.capabilities + (exact.capabilities[0],))
        result = self.coordinator().reconcile(duplicate, authority)
        self.assertEqual(decision(result, "ha_get_state").reason_code, "capability_duplicate")
        self.assertTrue(decision(result, "ha_search").disposition.admitted)


class SurfaceSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = OfflineUpdateHarness.from_mapping(fixture_mapping())

    def test_core_rest_and_websocket_version_disagreement_fails_closed(self):
        result = CapabilityAdmissionCoordinator(self.harness.profiles).reconcile(
            self.harness.observation("core_version_disagreement"),
            self.harness.authority("signed_core_ordinary_only"),
        )
        self.assertEqual(result.disposition, AdmissionDisposition.UNAVAILABLE)
        self.assertEqual(
            decision(result, "core.states_read").reason_code,
            "core_version_disagreement",
        )

    def test_unknown_core_can_admit_structural_reads_without_semantic_or_write_profiles(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        result = coordinator.reconcile(
            self.harness.observation("core_unknown_compatible"),
            self.harness.authority("signed_core_ordinary_only"),
        )
        self.assertEqual(result.disposition, AdmissionDisposition.PARTIAL)
        self.assertEqual(
            decision(result, "core.states_read").disposition,
            AdmissionDisposition.ADMITTED_COMPATIBLE,
        )
        self.assertEqual(
            decision(result, "core.entity_registry_read").disposition,
            AdmissionDisposition.ADMITTED_COMPATIBLE,
        )
        self.assertEqual(
            decision(result, "core.template_semantics").reason_code,
            "positive_authority_missing",
        )
        self.assertEqual(
            decision(result, "core.configuration_semantics").reason_code,
            "positive_authority_missing",
        )
        self.assertEqual(
            decision(result, "core.helper_write").reason_code,
            "write_capability_prohibited",
        )

    def test_transport_restoration_does_not_restore_provider_authority(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        result = coordinator.reconcile(
            self.harness.observation("transport_restored"),
            self.harness.authority("compiled_transport"),
        )
        self.assertEqual(result.disposition, AdmissionDisposition.ADMITTED_EXACT)
        self.assertIsNotNone(
            coordinator.acquire_route(
                "transport.streamable_http",
                session_id="synthetic-session-transport-1",
            )
        )
        self.assertIsNone(
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-transport-1"
            )
        )

    def test_transport_disconnect_and_authentication_failure_are_unavailable(self):
        restored = self.harness.observation("transport_restored")
        authority = self.harness.authority("compiled_transport")
        for observation, reason in (
            (replace(restored, connected=False), "transport_unavailable"),
            (replace(restored, authenticated=False), "authentication_failed"),
        ):
            with self.subTest(reason=reason):
                result = CapabilityAdmissionCoordinator(self.harness.profiles).reconcile(
                    observation, authority
                )
                self.assertEqual(
                    decision(result, "transport.streamable_http").reason_code,
                    reason,
                )


class GenerationLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = OfflineUpdateHarness.from_mapping(fixture_mapping())

    def test_precommit_retired_call_fails_without_logical_dispatch(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        self.assertIsNotNone(lease)
        coordinator.begin_reconciliation(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        self.assertIsNone(
            coordinator.commit_route(lease, session_id="synthetic-session-ha-mcp-1")
        )

    def test_postcommit_call_may_finish_but_cannot_revive_old_generation(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        first = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        commit = coordinator.commit_route(
            lease, session_id="synthetic-session-ha-mcp-1"
        )
        self.assertIsNotNone(commit)
        second = coordinator.reconcile(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        self.assertEqual(second.generation.generation, first.generation.generation + 1)
        self.assertTrue(coordinator.finish_committed(commit))
        self.assertEqual(
            coordinator.current_generation.generation,
            second.generation.generation,
        )
        self.assertFalse(coordinator.finish_committed(commit))

    def test_same_generation_same_session_is_required_immediately_before_commit(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                lease, session_id="synthetic-session-ha-mcp-other"
            )
        )
        self.assertIsNone(
            coordinator.commit_route(
                lease, session_id="synthetic-session-ha-mcp-other"
            )
        )

    def test_stale_verification_cannot_publish_after_newer_generation(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        first = coordinator.begin_reconciliation(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        second = coordinator.begin_reconciliation(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        stale = coordinator.complete_reconciliation(first)
        self.assertFalse(stale.published)
        self.assertEqual(stale.reason_code, "verification_generation_stale")
        current = coordinator.complete_reconciliation(second)
        self.assertTrue(current.published)
        self.assertEqual(coordinator.current_generation, current.generation)


class BoundsProjectionAndInertnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = OfflineUpdateHarness.from_mapping(fixture_mapping())

    def test_identity_reason_and_authority_bounds_fail_closed(self):
        exact = self.harness.observation("ha_mcp_exact")
        with self.assertRaisesRegex(CompatibilityModelError, "observation_identity_invalid"):
            replace(exact, identity="x" * 129)
        with self.assertRaisesRegex(CompatibilityModelError, "reason_code_invalid"):
            replace(exact, evidence_reason="x" * 97)
        decision_value = self.harness.authority("compiled_ha_mcp_exact").decisions[0]
        with self.assertRaisesRegex(CompatibilityModelError, "authority_decisions_oversized"):
            AuthorityBundle(
                evaluated_at_epoch=1800000000,
                decisions=tuple(
                    decision_value for _index in range(MAX_AUTHORITY_DECISIONS + 1)
                ),
            )

    def test_health_and_audit_are_bounded_sanitized_and_fallback_free(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        result = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        health = coordinator.health_projection()
        audit = coordinator.audit_projection(result)
        for projection in (health, audit):
            encoded = canonical_json(projection)
            self.assertLessEqual(len(encoded), MAX_PROJECTION_BYTES)
            self.assertEqual(projection["fallback_count"], 0)
            text = encoded.decode("utf-8")
            self.assertNotIn("synthetic-session", text)
            self.assertNotIn("1.0.0-synthetic", text)
            self.assertNotIn("synthetic-ha-mcp", text)
            self.assertNotIn("ha_get_state", text)

    def test_new_package_has_no_transport_subprocess_credentials_or_file_io(self):
        package = RUNTIME / "compatibility"
        prohibited_imports = {
            "aiohttp",
            "httpx",
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "websockets",
            "mcp",
        }
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertFalse(roots & prohibited_imports, path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".", 1)[0], prohibited_imports)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "exec", "eval", "compile"})

    def test_new_package_is_unreferenced_by_production_runtime(self):
        package = RUNTIME / "compatibility"
        for path in RUNTIME.rglob("*.py"):
            if package in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name == "ha_mcp_engineering.compatibility"
                            or alias.name.startswith("ha_mcp_engineering.compatibility."),
                            path,
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    package_parts = ["ha_mcp_engineering"] + list(
                        path.parent.relative_to(RUNTIME).parts
                    )
                    if node.level:
                        keep = len(package_parts) - (node.level - 1)
                        resolved_parts = package_parts[:keep]
                        if module:
                            resolved_parts.extend(module.split("."))
                        resolved_module = ".".join(resolved_parts)
                    else:
                        resolved_module = module
                    self.assertFalse(
                        resolved_module == "ha_mcp_engineering.compatibility"
                        or resolved_module.startswith(
                            "ha_mcp_engineering.compatibility."
                        ),
                        path,
                    )

    def test_adr_and_operator_contract_cover_required_inert_boundaries(self):
        adr = (
            ROOT
            / "docs"
            / "architecture"
            / "ADR-020-CAPABILITY-SCOPED-AUTOMATIC-READMISSION.md"
        ).read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "UPSTREAM_COMPATIBILITY_OPERATOR_GUIDE.md").read_text(
            encoding="utf-8"
        )
        adr_lower = adr.lower()
        normalized_guide = " ".join(guide.split())
        for term in (
            "home assistant core",
            "ha-mcp",
            "configured transport",
            "observation",
            "authority",
            "generation",
            "deny-only",
            "rollback",
            "replay",
            "tools.listchanged=true",
            "same current generation and same session",
            "client/connector responsibility",
        ):
            self.assertIn(term, adr_lower)
        self.assertIn("foundation is intentionally inert", normalized_guide)
        self.assertIn("does not restore provider authority", normalized_guide)

    def test_runtime_does_not_enable_tool_list_change_notifications(self):
        for path in RUNTIME.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ToolListChangedNotification", text, path)
            self.assertNotIn("listChanged=True", text, path)


if __name__ == "__main__":
    unittest.main()
