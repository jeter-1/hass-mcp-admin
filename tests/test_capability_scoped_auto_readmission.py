from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
RUNTIME = BETA / "ha_mcp_engineering"
STABLE_RUNTIME = ROOT / "hass_mcp_admin"
TESTS = ROOT / "tests"
REFERENCE_MODEL = TESTS / "support" / "automatic_readmission"
FIXTURE = TESTS / "fixtures" / "automatic_readmission" / "foundation_v1.json"
VECTORS = TESTS / "fixtures" / "automatic_readmission" / "contract_vectors_v2.json"
sys.path.insert(0, str(TESTS))

from support.automatic_readmission import (  # noqa: E402
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
    ReferenceContractAdapter,
    UpstreamSurface,
    VECTOR_SCHEMA_VERSION,
    canonical_json,
    classify_registry_refresh,
    run_contract_suite,
    run_contract_vector,
)
from support.automatic_readmission.models import (  # noqa: E402
    MAX_ACTIVE_COMMITS,
    MAX_AUTHORITY_DECISIONS,
    MAX_ISSUED_LEASES,
    MAX_OBSERVED_CAPABILITIES,
    MAX_PROJECTION_BYTES,
    MAX_RETIREMENT_DIAGNOSTICS,
    MAX_SAFE_INTEGER,
)


def fixture_mapping() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def vector_mapping() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


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

    def test_harness_rejects_every_non_boolean_json_shape(self):
        invalid_values = ("true", "false", 0, 1, None, [], {}, "other")
        for field in ("connected", "authenticated", "catalog_complete"):
            for value in invalid_values:
                malformed = fixture_mapping()
                malformed["scenarios"]["ha_mcp_exact"][field] = value
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaisesRegex(
                        CompatibilityModelError, f"harness_{field}_invalid"
                    ):
                        OfflineUpdateHarness.from_mapping(malformed).observation(
                            "ha_mcp_exact"
                        )

    def test_integer_and_non_finite_bounds_fail_before_reconciliation(self):
        for value in (True, False, MAX_SAFE_INTEGER + 1, -1):
            malformed = fixture_mapping()
            malformed["authority_sets"]["compiled_ha_mcp_exact"][
                "evaluated_at_epoch"
            ] = value
            with self.subTest(authority_time=repr(value)):
                with self.assertRaisesRegex(
                    CompatibilityModelError, "harness_authority_time_invalid"
                ):
                    OfflineUpdateHarness.from_mapping(malformed).authority(
                        "compiled_ha_mcp_exact"
                    )

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(non_finite=repr(value)):
                with self.assertRaisesRegex(
                    CompatibilityModelError, "canonical_value_invalid"
                ):
                    canonical_json({"value": value})

    def test_catalog_pagination_topology_rejects_contradictions(self):
        invalid_pages = (
            [{"tools": [], "next_cursor": None}, {"tools": [], "next_cursor": None}],
            [{"tools": [], "next_cursor": "cursor"}],
            [
                {"tools": [], "next_cursor": "cursor"},
                {"tools": [], "next_cursor": "cursor"},
                {"tools": [], "next_cursor": None},
            ],
            [{"tools": [], "next_cursor": 1}, {"tools": [], "next_cursor": None}],
        )
        for pages in invalid_pages:
            malformed = fixture_mapping()
            malformed["scenarios"]["ha_mcp_exact"]["tools_list_pages"] = pages
            with self.subTest(pages=repr(pages)):
                with self.assertRaises(CompatibilityModelError):
                    OfflineUpdateHarness.from_mapping(malformed).observation(
                        "ha_mcp_exact"
                    )


class ImplementationNeutralVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapping = vector_mapping()

    def test_vectors_are_data_only_complete_and_implementation_neutral(self):
        self.assertEqual(
            set(self.mapping),
            {"schema_version", "foundation_fixture", "vectors"},
        )
        self.assertEqual(self.mapping["schema_version"], VECTOR_SCHEMA_VERSION)
        self.assertEqual(self.mapping["foundation_fixture"], FIXTURE.name)
        self.assertGreaterEqual(len(self.mapping["vectors"]), 16)
        ids = [item["vector_id"] for item in self.mapping["vectors"]]
        self.assertEqual(len(ids), len(set(ids)))
        for vector in self.mapping["vectors"]:
            self.assertEqual(
                set(vector),
                {"vector_id", "requirements", "steps"},
            )
            self.assertTrue(vector["requirements"])
            self.assertTrue(vector["steps"])
            step_ids = [step["step_id"] for step in vector["steps"]]
            self.assertEqual(len(step_ids), len(set(step_ids)))
            for step in vector["steps"]:
                self.assertEqual(
                    set(step), {"step_id", "operation", "arguments", "expected"}
                )

        raw = VECTORS.read_text(encoding="utf-8")
        for implementation_name in (
            "CapabilityAdmissionCoordinator",
            "AdmissionDisposition",
            "OfflineUpdateHarness",
            "support.automatic_readmission",
            "SurfaceState",
            "ReferenceContractAdapter",
        ):
            self.assertNotIn(implementation_name, raw)

    def test_vectors_cover_the_independently_stated_security_contract(self):
        required = {
            "independent_surfaces",
            "surface_specific_retirement",
            "transport_not_provider_authority",
            "duplicate_capability_ownership",
            "revocation",
            "expiry_crossing",
            "deny_only",
            "lower_sequence_rollback",
            "equal_sequence_replay",
            "equal_sequence_content_conflict",
            "idempotent_replay",
            "partial_admission",
            "missing_tool",
            "duplicate_tool",
            "incomplete_catalog",
            "unknown_tool",
            "write_tools_unreachable",
            "contract_drift",
            "begin_reconciliation",
            "surface_bound_leases",
            "precommit_generation_revalidation",
            "committed_call_completion",
            "stale_precommit_rejection",
            "stale_verification_completion",
            "core_identity_agreement",
            "core_identity_disagreement",
            "structural_reads_only",
            "semantic_capabilities_held",
            "proxy_interruption",
            "proxy_restoration",
            "exact_booleans",
            "integers_reject_booleans",
            "catalog_page_bound",
            "continuation_required",
            "cursor_uniqueness",
            "terminal_page_rules",
            "profile_bound",
            "decision_bound",
            "tool_bound",
            "health_audit_bounds",
            "sensitive_output_redaction",
            "decision_order_independence",
            "profile_order_independence",
            "clock_stability",
            "zero_write_authority",
            "cross_surface_authority_projection",
            "unrelated_core_authority_preserves_ha_mcp",
            "unrelated_ha_mcp_authority_preserves_core",
            "transport_restoration_no_provider_retirement",
            "provider_authority_preserves_transport",
            "stale_surface_isolation",
            "single_use_lease",
            "sequential_duplicate_commit_rejection",
            "concurrent_duplicate_commit_rejection",
            "replay_after_finish_rejection",
            "finish_exactly_once",
            "explicit_lease_release",
            "uncommitted_retired_lease_rejection",
            "committed_completion_after_retirement",
            "issued_lease_capacity",
            "active_commit_capacity",
            "capacity_exhaustion_fails_closed",
            "bounded_retirement_history",
            "prolonged_material_churn",
            "unknown_signed_profile",
            "unknown_signed_adapter",
            "unknown_signed_capability",
            "signed_scope_broadening_rejected",
            "action_kind_unreachable",
            "governed_write_kind_unreachable",
            "persistent_write_kind_unreachable",
            "mixed_kind_unreachable",
            "unknown_future_read_unreachable",
            "real_audit_operation",
            "audit_lifecycle_bounds",
            "audit_sensitive_output_redaction",
            "bounded_lifecycle_counts",
            "zero_fallback",
        }
        represented = {
            requirement
            for vector in self.mapping["vectors"]
            for requirement in vector["requirements"]
        }
        self.assertEqual(required - represented, set())

    def test_contract_vectors_replay_with_literal_expected_outcomes(self):
        report = run_contract_suite(
            self.mapping,
            lambda: ReferenceContractAdapter(fixture_mapping()),
        )
        self.assertTrue(report["matched"], report)
        self.assertEqual(report["vector_count"], len(self.mapping["vectors"]))
        self.assertEqual(report["step_count"], 136)
        self.assertEqual(report["mismatch_count"], 0)
        self.assertTrue(all(item["mismatches"] == [] for item in report["reports"]))

    def test_vector_suite_bounds_and_schema_fail_closed(self):
        adapter = lambda: ReferenceContractAdapter(fixture_mapping())
        malformed = deepcopy(self.mapping)
        malformed["schema_version"] = True
        with self.assertRaisesRegex(
            CompatibilityModelError, "vector_schema_unsupported"
        ):
            run_contract_suite(malformed, adapter)

        duplicate = deepcopy(self.mapping)
        duplicate["vectors"].append(deepcopy(duplicate["vectors"][0]))
        with self.assertRaisesRegex(CompatibilityModelError, "vector_id_duplicate"):
            run_contract_suite(duplicate, adapter)

    def test_defective_adapters_are_detected_by_literal_vectors(self):
        cases = {
            "global_authority_fingerprint": (
                "independent_surface_authority_lifecycles",
                lambda operation, arguments: operation == "reconcile"
                and arguments.get("observation_id") == "ha_mcp_exact"
                and bool(arguments.get("fixture_mutations")),
                {"idempotent": False, "generation": 4, "retired_generation": 1},
            ),
            "shared_generation": (
                "independent_surface_authority_lifecycles",
                lambda operation, arguments: operation == "validate_lease"
                and arguments.get("lease_id") == "ha",
                {"valid": False},
            ),
            "truthy_string": (
                "malformed_boolean_and_integer_evidence",
                lambda operation, arguments: operation == "validate_fixture"
                and any(
                    mutation.get("path", [])[-1:] == ["connected"]
                    for mutation in arguments.get("fixture_mutations", [])
                    if isinstance(mutation, dict)
                ),
                {"accepted": True, "error_code": None},
            ),
            "clock_sensitive": (
                "signed_expiry_boundary_is_material",
                lambda operation, arguments: operation == "reconcile"
                and any(
                    mutation.get("value") == 1800000001
                    for mutation in arguments.get("fixture_mutations", [])
                    if isinstance(mutation, dict)
                ),
                {"idempotent": False, "generation": 2, "retired_generation": 1},
            ),
            "duplicate_lease_commit": (
                "single_use_commit_lifecycle",
                lambda operation, arguments: operation == "commit_lease"
                and arguments.get("lease_id") == "sequential",
                {"committed": True},
            ),
            "stale_generation": (
                "surface_specific_update_and_lease_retirement",
                lambda operation, arguments: operation == "validate_lease"
                and arguments.get("lease_id") == "old_ha",
                {"valid": True},
            ),
            "write_admission": (
                "prohibited_capability_kinds_and_signed_scope",
                lambda operation, arguments: operation == "probe_capability"
                and arguments.get("capability_id") == "ha_call_service",
                {
                    "disposition": "admitted_exact",
                    "reason_code": "exact_compiled_contract",
                    "adapter_present": True,
                    "lease_granted": True,
                    "committed": True,
                    "fallback_count": 0,
                    "write_action_reachability": 1,
                },
            ),
            "unsafe_audit_projection": (
                "bounded_accumulation_and_projection",
                lambda operation, arguments: operation == "audit",
                {"bounded": False, "sensitive_material_present": True},
            ),
            "unbounded_lifecycle_retention": (
                "bounded_lifecycle_capacity_and_retirement",
                lambda operation, arguments: operation == "reconcile_churn",
                {
                    "retained_retirement_diagnostic_count": 31,
                    "within_retirement_bound": False,
                },
            ),
        }

        vectors = {item["vector_id"]: item for item in self.mapping["vectors"]}
        for defect, (vector_id, predicate, override) in cases.items():
            class DefectiveAdapter:
                def __init__(self):
                    self.delegate = ReferenceContractAdapter(fixture_mapping())

                def execute(self, current_operation, arguments):
                    result = dict(self.delegate.execute(current_operation, arguments))
                    if predicate(current_operation, arguments):
                        result.update(override)
                    return result

            with self.subTest(defect=defect):
                report = run_contract_vector(vectors[vector_id], DefectiveAdapter())
                self.assertFalse(report["matched"])
                self.assertGreaterEqual(report["mismatch_count"], 1)
                self.assertLessEqual(len(canonical_json(report)), MAX_PROJECTION_BYTES)

    def test_fixture_rendering_is_byte_identical_across_two_generations(self):
        def render(value: dict) -> bytes:
            return (
                json.dumps(
                    value,
                    sort_keys=True,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")

        for path in (FIXTURE, VECTORS):
            with self.subTest(path=path.name):
                first = render(json.loads(path.read_text(encoding="utf-8")))
                second = render(json.loads(first.decode("utf-8")))
                self.assertEqual(first, second)
                self.assertEqual(path.read_bytes(), first)


class AuthorityAndAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = OfflineUpdateHarness.from_mapping(fixture_mapping())

    def coordinator(self):
        return CapabilityAdmissionCoordinator(self.harness.profiles)

    def test_ar_r1_reconciling_core_preserves_admitted_ha_mcp_route(self):
        coordinator = self.coordinator()
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        self.assertIsNotNone(
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
        )

        coordinator.reconcile(
            self.harness.observation("core_unknown_compatible"),
            self.harness.authority("signed_core_ordinary_only"),
        )

        self.assertIsNotNone(
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
        )
        self.assertIsNotNone(
            coordinator.acquire_route(
                "core.states_read", session_id="synthetic-session-core-1"
            )
        )

    def test_ar_r2_truthy_boolean_strings_are_rejected_before_reconciliation(self):
        for field in ("connected", "authenticated", "catalog_complete"):
            malformed = fixture_mapping()
            malformed["scenarios"]["ha_mcp_exact"][field] = "false"
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    CompatibilityModelError,
                    f"harness_{field}_invalid",
                ):
                    OfflineUpdateHarness.from_mapping(malformed).observation(
                        "ha_mcp_exact"
                    )

    def test_ar_r3_time_progress_without_expiry_transition_is_idempotent(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_compatible")
        authority = self.harness.authority("signed_ha_mcp_compatible")
        first = coordinator.reconcile(observation, authority)
        later = replace(authority, evaluated_at_epoch=authority.evaluated_at_epoch + 1)
        second = coordinator.reconcile(observation, later)
        self.assertTrue(second.idempotent)
        self.assertEqual(first.generation.generation, second.generation.generation)

    def test_ar_r3_authority_decision_order_is_not_material(self):
        authority = self.harness.authority("compiled_ha_mcp_exact")
        reordered = replace(authority, decisions=tuple(reversed(authority.decisions)))
        self.assertEqual(authority.fingerprint, reordered.fingerprint)

    def test_ar_r3_profile_order_is_not_material(self):
        profiles = self.harness.profiles
        first = CapabilityAdmissionCoordinator(profiles)
        second = CapabilityAdmissionCoordinator(tuple(reversed(profiles)))
        self.assertEqual(
            first.profile_registry_fingerprint,
            second.profile_registry_fingerprint,
        )

    def test_ar_r3_expiry_boundary_is_material_but_preexpiry_time_is_not(self):
        authority = self.harness.authority("signed_ha_mcp_compatible")
        preexpiry = replace(
            authority, evaluated_at_epoch=authority.evaluated_at_epoch + 1
        )
        expires_at = authority.decisions[0].expires_at_epoch
        self.assertIsNotNone(expires_at)
        expired = replace(authority, evaluated_at_epoch=expires_at)
        self.assertEqual(authority.fingerprint, preexpiry.fingerprint)
        self.assertNotEqual(authority.fingerprint, expired.fingerprint)

    def test_ar_r6_unrelated_authority_changes_are_surface_local(self):
        cases = (
            (
                "ha_mcp_exact",
                "compiled_ha_mcp_exact",
                "signed_core_ordinary_only",
                "ha_get_state",
                "synthetic-session-ha-mcp-1",
            ),
            (
                "core_unknown_compatible",
                "signed_core_ordinary_only",
                "compiled_ha_mcp_exact",
                "core.states_read",
                "synthetic-session-core-1",
            ),
            (
                "transport_restored",
                "compiled_transport",
                "compiled_ha_mcp_exact",
                "transport.streamable_http",
                "synthetic-session-transport-1",
            ),
        )
        for observation_id, primary_id, unrelated_id, capability_id, session_id in cases:
            with self.subTest(surface=observation_id):
                coordinator = self.coordinator()
                primary = self.harness.authority(primary_id)
                unrelated = self.harness.authority(unrelated_id)
                observation = self.harness.observation(observation_id)
                first = coordinator.reconcile(observation, primary)
                lease = coordinator.acquire_route(capability_id, session_id=session_id)
                self.assertIsNotNone(lease)
                combined = AuthorityBundle(
                    evaluated_at_epoch=max(
                        primary.evaluated_at_epoch,
                        unrelated.evaluated_at_epoch,
                    ),
                    decisions=primary.decisions + unrelated.decisions,
                )

                second = coordinator.reconcile(observation, combined)

                self.assertTrue(second.idempotent)
                self.assertIsNone(second.retired_generation)
                self.assertEqual(
                    second.generation.generation,
                    first.generation.generation,
                )
                self.assertTrue(
                    coordinator.validate_pre_dispatch(lease, session_id=session_id)
                )

    def test_ar_r6_applicable_authority_change_retires_only_owner_surface(self):
        coordinator = self.coordinator()
        ha_authority = self.harness.authority("compiled_ha_mcp_exact")
        core_authority = self.harness.authority("signed_core_ordinary_only")
        combined = AuthorityBundle(
            evaluated_at_epoch=max(
                ha_authority.evaluated_at_epoch,
                core_authority.evaluated_at_epoch,
            ),
            decisions=ha_authority.decisions + core_authority.decisions,
        )
        ha_result = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"), combined
        )
        core_result = coordinator.reconcile(
            self.harness.observation("core_unknown_compatible"), combined
        )
        ha_lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        core_lease = coordinator.acquire_route(
            "core.states_read", session_id="synthetic-session-core-1"
        )
        changed_ha = replace(
            ha_authority.decisions[0],
            status=AuthorityStatus.REVOKED,
            reason_code="synthetic_ha_revocation",
        )
        changed = replace(
            combined,
            decisions=(changed_ha,) + ha_authority.decisions[1:] + core_authority.decisions,
        )

        retired = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"), changed
        )

        self.assertFalse(retired.idempotent)
        self.assertEqual(retired.retired_generation, ha_result.generation.generation)
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                ha_lease, session_id="synthetic-session-ha-mcp-1"
            )
        )
        self.assertEqual(
            coordinator.generation_for(UpstreamSurface.HOME_ASSISTANT_CORE),
            core_result.generation,
        )
        self.assertTrue(
            coordinator.validate_pre_dispatch(
                core_lease, session_id="synthetic-session-core-1"
            )
        )

    def test_ar_r6_unknown_profile_is_irrelevant_but_unknown_adapter_is_material(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_compatible")
        authority = self.harness.authority("signed_ha_mcp_compatible")
        first = coordinator.reconcile(observation, authority)
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-2"
        )
        unknown_profile = replace(
            authority.decisions[0],
            profile_id="synthetic_unknown_profile",
            reason_code="synthetic_unknown_profile_attempt",
        )
        unrelated = replace(
            authority,
            decisions=authority.decisions + (unknown_profile,),
        )
        same = coordinator.reconcile(observation, unrelated)
        self.assertTrue(same.idempotent)
        self.assertEqual(same.generation.generation, first.generation.generation)
        self.assertTrue(
            coordinator.validate_pre_dispatch(
                lease, session_id="synthetic-session-ha-mcp-2"
            )
        )

        unknown_adapter = replace(
            authority.decisions[0],
            adapter_id="synthetic_unknown_adapter",
            reason_code="synthetic_unknown_adapter_attempt",
        )
        material = replace(
            authority,
            decisions=(unknown_adapter,) + authority.decisions[1:],
        )
        changed = coordinator.reconcile(observation, material)
        self.assertFalse(changed.idempotent)
        self.assertEqual(changed.retired_generation, first.generation.generation)
        self.assertFalse(
            decision(changed, "ha_get_state").disposition.admitted
        )

    def test_registry_envelope_refresh_without_effective_change_does_not_churn(self):
        coordinator = self.coordinator()
        observation = self.harness.observation("ha_mcp_compatible")
        authority = self.harness.authority("signed_ha_mcp_compatible")
        first = coordinator.reconcile(observation, authority)
        refreshed = replace(
            authority,
            decisions=tuple(
                replace(
                    item,
                    registry_sequence=item.registry_sequence + 1,
                    registry_digest="sha256:" + "c" * 64,
                )
                for item in authority.decisions
            ),
        )
        second = coordinator.reconcile(observation, refreshed)
        self.assertTrue(second.idempotent)
        self.assertEqual(second.generation.generation, first.generation.generation)

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

    def test_ar_r7_route_lease_is_consumed_by_one_sequential_commit(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        first = coordinator.commit_route(
            lease, session_id="synthetic-session-ha-mcp-1"
        )
        second = coordinator.commit_route(
            lease, session_id="synthetic-session-ha-mcp-1"
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_ar_r7_concurrent_duplicate_commit_has_exactly_one_winner(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        barrier = Barrier(2)

        def attempt_commit():
            barrier.wait()
            return coordinator.commit_route(
                lease, session_id="synthetic-session-ha-mcp-1"
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(attempt_commit) for _index in range(2))
            results = tuple(item.result(timeout=5) for item in futures)

        self.assertEqual(sum(item is not None for item in results), 1)

    def test_ar_r9_retirement_and_lifecycle_state_are_bounded(self):
        lifecycle_capacity = MAX_RETIREMENT_DIAGNOSTICS
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        exact_observation = self.harness.observation("ha_mcp_exact")
        exact_authority = self.harness.authority("compiled_ha_mcp_exact")
        compatible_observation = self.harness.observation("ha_mcp_compatible")
        compatible_authority = self.harness.authority("signed_ha_mcp_compatible")

        for index in range(lifecycle_capacity * 4):
            if index % 2:
                coordinator.reconcile(compatible_observation, compatible_authority)
            else:
                coordinator.reconcile(exact_observation, exact_authority)

        state = coordinator._surface_states[UpstreamSurface.HA_MCP]
        retained = state.retired_generation_diagnostics
        self.assertLessEqual(len(retained), lifecycle_capacity)
        self.assertEqual(len(retained), lifecycle_capacity)

        lease_coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        lease_coordinator.reconcile(exact_observation, exact_authority)
        leases = tuple(
            lease_coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
            for _index in range(MAX_ISSUED_LEASES + 1)
        )
        self.assertTrue(all(item is not None for item in leases[:MAX_ISSUED_LEASES]))
        self.assertIsNone(leases[-1])

        commit_coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        commit_coordinator.reconcile(exact_observation, exact_authority)
        commits = []
        for _index in range(MAX_ACTIVE_COMMITS + 1):
            lease = commit_coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
            commits.append(
                commit_coordinator.commit_route(
                    lease, session_id="synthetic-session-ha-mcp-1"
                )
            )
        self.assertTrue(all(item is not None for item in commits[:MAX_ACTIVE_COMMITS]))
        self.assertIsNone(commits[-1])

    def test_exact_stored_lease_release_replay_and_retirement_contract(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        released = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        forged = replace(released, adapter_id="synthetic_forged_adapter")
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                forged, session_id="synthetic-session-ha-mcp-1"
            )
        )
        self.assertIsNone(
            coordinator.commit_route(
                forged, session_id="synthetic-session-ha-mcp-1"
            )
        )
        self.assertTrue(coordinator.release_route(released))
        self.assertFalse(coordinator.release_route(released))
        self.assertIsNone(
            coordinator.commit_route(
                released, session_id="synthetic-session-ha-mcp-1"
            )
        )

        replayed = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        commit = coordinator.commit_route(
            replayed, session_id="synthetic-session-ha-mcp-1"
        )
        self.assertTrue(coordinator.finish_committed(commit))
        self.assertFalse(coordinator.finish_committed(commit))
        self.assertIsNone(
            coordinator.commit_route(
                replayed, session_id="synthetic-session-ha-mcp-1"
            )
        )

        retired = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        coordinator.begin_reconciliation(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        self.assertFalse(coordinator.release_route(retired))
        self.assertIsNone(
            coordinator.commit_route(
                retired, session_id="synthetic-session-ha-mcp-1"
            )
        )

    def test_failed_validation_releases_only_the_exact_presented_lease(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        first = coordinator.acquire_route(
            "ha_get_state",
            session_id="synthetic-session-ha-mcp-1",
        )
        second = coordinator.acquire_route(
            "ha_search",
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.lease_id, second.lease_id)

        forged = replace(first, capability_id="ha_search")
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                forged,
                session_id="synthetic-session-ha-mcp-1",
            )
        )
        self.assertTrue(
            coordinator.validate_pre_dispatch(
                first,
                session_id="synthetic-session-ha-mcp-1",
            )
        )
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                first,
                session_id="synthetic-wrong-session",
            )
        )
        self.assertFalse(
            coordinator.validate_pre_dispatch(
                first,
                session_id="synthetic-session-ha-mcp-1",
            )
        )
        self.assertTrue(
            coordinator.validate_pre_dispatch(
                second,
                session_id="synthetic-session-ha-mcp-1",
            )
        )
        self.assertEqual(
            coordinator.health_projection()["issued_lease_count"],
            1,
        )

    def test_lifecycle_capacity_refuses_cleanly_and_recovers_after_cleanup(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        leases = [
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
            for _index in range(MAX_ISSUED_LEASES)
        ]
        self.assertIsNone(
            coordinator.acquire_route(
                "ha_get_state", session_id="synthetic-session-ha-mcp-1"
            )
        )
        health = coordinator.health_projection()
        self.assertEqual(health["issued_lease_count"], MAX_ISSUED_LEASES)
        self.assertEqual(
            health["capacity_exhaustion_reason"],
            "issued_lease_capacity_exhausted",
        )
        self.assertTrue(coordinator.release_route(leases[0]))
        replacement = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        self.assertIsNotNone(replacement)

        for lease in leases[1:] + [replacement]:
            commit = coordinator.commit_route(
                lease, session_id="synthetic-session-ha-mcp-1"
            )
            self.assertIsNotNone(commit)
        waiting = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        self.assertIsNotNone(waiting)
        self.assertIsNone(
            coordinator.commit_route(
                waiting, session_id="synthetic-session-ha-mcp-1"
            )
        )
        health = coordinator.health_projection()
        self.assertEqual(health["active_commit_count"], MAX_ACTIVE_COMMITS)
        self.assertEqual(
            health["capacity_exhaustion_reason"],
            "active_commit_capacity_exhausted",
        )

    def test_generation_capacity_exhaustion_preserves_published_authority(self):
        coordinator = CapabilityAdmissionCoordinator(self.harness.profiles)
        first = coordinator.reconcile(
            self.harness.observation("ha_mcp_exact"),
            self.harness.authority("compiled_ha_mcp_exact"),
        )
        lease = coordinator.acquire_route(
            "ha_get_state", session_id="synthetic-session-ha-mcp-1"
        )
        coordinator._next_generation = MAX_SAFE_INTEGER
        with self.assertRaisesRegex(
            CompatibilityModelError,
            "generation_capacity_exhausted",
        ):
            coordinator.begin_reconciliation(
                self.harness.observation("ha_mcp_compatible"),
                self.harness.authority("signed_ha_mcp_compatible"),
            )
        self.assertEqual(
            coordinator.generation_for(UpstreamSurface.HA_MCP),
            first.generation,
        )
        self.assertTrue(
            coordinator.validate_pre_dispatch(
                lease, session_id="synthetic-session-ha-mcp-1"
            )
        )


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
        lease = coordinator.acquire_route(
            "ha_get_state",
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(lease)
        commit = coordinator.commit_route(
            lease,
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(commit)
        coordinator.reconcile(
            self.harness.observation("ha_mcp_compatible"),
            self.harness.authority("signed_ha_mcp_compatible"),
        )
        health = coordinator.health_projection()
        audit = coordinator.audit_projection(result)
        for projection in (health, audit):
            encoded = canonical_json(projection)
            self.assertLessEqual(len(encoded), MAX_PROJECTION_BYTES)
            self.assertEqual(projection["fallback_count"], 0)
            self.assertEqual(projection["issued_lease_count"], 0)
            self.assertEqual(projection["active_commit_count"], 1)
            self.assertLessEqual(
                projection["retained_retirement_diagnostic_count"],
                MAX_RETIREMENT_DIAGNOSTICS * len(UpstreamSurface),
            )
            self.assertEqual(projection["capacity_exhaustion_count"], 0)
            self.assertIsNone(projection["capacity_exhaustion_reason"])
            text = encoded.decode("utf-8")
            self.assertNotIn("synthetic-session", text)
            self.assertNotIn("1.0.0-synthetic", text)
            self.assertNotIn("synthetic-ha-mcp", text)
            self.assertNotIn("ha_get_state", text)
            for prohibited_key in (
                '"catalog"',
                '"credential"',
                '"endpoint"',
                '"exception"',
                '"headers"',
                '"identity"',
                '"schema"',
                '"session"',
                '"signature"',
                '"token"',
            ):
                self.assertNotIn(prohibited_key, text)
        self.assertTrue(coordinator.finish_committed(commit))

    def test_reference_model_has_no_transport_subprocess_credentials_or_file_io(self):
        package = REFERENCE_MODEL
        prohibited_imports = {
            "aiohttp",
            "httpx",
            "mcp",
            "os",
            "pathlib",
            "requests",
            "shutil",
            "socket",
            "subprocess",
            "urllib",
            "websockets",
        }
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".", 1)[0] for alias in node.names}
                    self.assertFalse(roots & prohibited_imports, path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".", 1)[0], prohibited_imports)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(
                            node.func.id,
                            {"open", "exec", "eval", "compile", "getenv"},
                            path,
                        )
                    elif isinstance(node.func, ast.Attribute):
                        self.assertNotIn(
                            node.func.attr,
                            {
                                "connect",
                                "dispatch",
                                "getenv",
                                "open",
                                "register_tool",
                                "run",
                                "send",
                                "write",
                                "write_bytes",
                                "write_text",
                            },
                            path,
                        )

    def test_reference_model_is_unreferenced_by_production_runtime(self):
        forbidden_modules = (
            "support.automatic_readmission",
            "tests.support.automatic_readmission",
        )
        for production_root in (RUNTIME, STABLE_RUNTIME):
            for path in production_root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(any(
                                alias.name == module
                                or alias.name.startswith(module + ".")
                                for module in forbidden_modules
                            ), path)
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        self.assertFalse(any(
                            module == forbidden
                            or module.startswith(forbidden + ".")
                            for forbidden in forbidden_modules
                        ), path)
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("OfflineUpdateHarness", text, path)
        self.assertTrue(
            (RUNTIME / "ha_mcp_readmission" / "coordinator.py").is_file()
        )

    def test_runtime_export_and_import_surface_excludes_reference_model(self):
        script = """
import sys
import ha_mcp_engineering
assert not hasattr(ha_mcp_engineering, 'CapabilityAdmissionCoordinator')
assert not hasattr(ha_mcp_engineering, 'OfflineUpdateHarness')
blocked = [name for name in sys.modules if name.startswith(('support.automatic_readmission', 'tests.support.automatic_readmission'))]
assert blocked == [], blocked
"""
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(BETA),
        }
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout + completed.stderr)[:1000],
        )

    def test_production_artifact_context_excludes_reference_model(self):
        dockerfile = (BETA / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ha_mcp_engineering ./ha_mcp_engineering", dockerfile)
        self.assertNotIn("tests", dockerfile)
        self.assertNotIn("automatic_readmission", dockerfile)
        self.assertFalse(any((RUNTIME / "compatibility").glob("*.py")))
        dockerignore = (BETA / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("__pycache__/", dockerignore)
        self.assertIn("*.py[cod]", dockerignore)
        self.assertTrue(REFERENCE_MODEL.is_dir())
        runtime_paths = {
            path.relative_to(BETA).as_posix()
            for path in BETA.rglob("*")
            if path.is_file()
        }
        self.assertFalse(any("automatic_readmission" in path for path in runtime_paths))

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
            "same current generation",
            "single-use",
            "capacity exhaustion",
            "client/connector responsibility",
        ):
            self.assertIn(term, adr_lower)
        self.assertIn("executable reference model is intentionally non-authoritative", normalized_guide)
        self.assertIn("does not restore provider authority", normalized_guide)
        self.assertIn("global registry refresh causes reevaluation", normalized_guide)
        self.assertIn("duplicate commit is rejected", normalized_guide)

    def test_runtime_does_not_enable_tool_list_change_notifications(self):
        for path in RUNTIME.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ToolListChangedNotification", text, path)
            self.assertNotIn("listChanged=True", text, path)


if __name__ == "__main__":
    unittest.main()
