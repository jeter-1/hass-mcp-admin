"""Beta 53 canonical entity-registry and selector diagnostic regressions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.provider import (
    DirectHaDependencyProvider,
    MAX_ENTITY_REGISTRY_CANONICAL_RECORD_BYTES,
    MAX_ENTITY_REGISTRY_CANONICAL_RECORD_NODES,
    MAX_EXPAND_SNAPSHOT_ENTITIES,
    _build_expand_snapshot_evidence,
    _build_label_membership_evidence,
    _deduplicate_identical_entity_registry_records,
)
from ha_mcp_engineering.dependency.extraction import LABEL_LOOKUP_MODEL
from ha_mcp_engineering.dependency.models import LABEL_SELECTOR_AUTHORITY_MODEL
from ha_mcp_engineering.f3.operational_locks import (
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3.operational_models import (
    SET_INPUT_BOOLEAN_STATE,
)
from ha_mcp_engineering.f3_configuration.locks import (
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.governance.normalize import stable_hash

from tests.f3_operational_fixtures import make_context, prepare_context
from tests.support.replay_hamcp089_beta52_production_path import (
    _OBLIGATION_NORMALIZED_FIELDS,
    _PROFILE_NORMALIZED_FIELDS,
    _normalized_semantic_rows,
    _transport_fixture,
)
from tests.test_beta37_exact_helper_state import (
    Clock,
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)
from tests.test_beta50_helper_production_target_scope import (
    CapturedBeta50ReplayRest,
    CapturedBeta50ReplayWebSocket,
)


REPLAY = (
    ROOT
    / "tests"
    / "fixtures"
    / "dependency"
    / "hamcp089_beta52_standard_helper_replay_v1.json"
)
RUNNER = (
    ROOT
    / "tests"
    / "support"
    / "replay_hamcp089_beta52_production_path.py"
)


def _labels(*selectors: str) -> list[dict[str, str]]:
    return [
        {"label_id": selector, "name": selector.replace("_", " ").title()}
        for selector in selectors
    ]


def _record(
    entity_id: str = "sensor.member",
    labels: list[str] | None = None,
    **extra,
) -> dict:
    return {
        "entity_id": entity_id,
        "labels": list(labels if labels is not None else ["label_a"]),
        "platform": "synthetic",
        **extra,
    }


def _evidence(
    records: list,
    *,
    selectors: tuple[str, ...] = ("label_a",),
):
    return _build_label_membership_evidence(
        list(selectors),
        entity_registry=records,
        label_registry=_labels(*selectors),
    )


class Beta53CanonicalEntityRegistryTests(unittest.TestCase):
    def test_identical_multiplicity_and_order_are_semantically_idempotent(self):
        nested_first = _record(
            nested={"b": 2, "a": {"d": 4, "c": 3}}
        )
        nested_reordered = {
            "nested": {"a": {"c": 3, "d": 4}, "b": 2},
            "platform": "synthetic",
            "labels": ["label_a"],
            "entity_id": "sensor.member",
        }
        control = _evidence([nested_first])
        for multiplicity in (2, 3, 64):
            with self.subTest(multiplicity=multiplicity):
                records = [
                    copy.deepcopy(
                        nested_first if index % 2 == 0 else nested_reordered
                    )
                    for index in range(multiplicity)
                ]
                observed = _evidence(list(reversed(records)))
                repeated = _evidence(copy.deepcopy(records))
                self.assertEqual(control.memberships, observed.memberships)
                self.assertEqual(control.fingerprints, observed.fingerprints)
                self.assertEqual(
                    control.selector_authority["label_a"].authority_fingerprint,
                    observed.selector_authority["label_a"].authority_fingerprint,
                )
                self.assertTrue(observed.selector_complete["label_a"])
                self.assertEqual(
                    multiplicity - 1,
                    observed.selector_authority[
                        "label_a"
                    ].identical_duplicates_collapsed,
                )
                self.assertNotEqual(
                    control.selector_authority["label_a"].anomaly_fingerprint,
                    observed.selector_authority["label_a"].anomaly_fingerprint,
                )
                self.assertEqual(
                    repeated.selector_authority["label_a"].anomaly_fingerprint,
                    observed.selector_authority["label_a"].anomaly_fingerprint,
                )

    def test_complete_canonical_record_conflict_matrix(self):
        cases = {
            "list_order": (
                _record(options=[1, 2]),
                _record(options=[2, 1]),
            ),
            "label_order": (
                _record(labels=["label_a", "label_b"]),
                _record(labels=["label_b", "label_a"]),
            ),
            "integer_float": (_record(value=1), _record(value=1.0)),
            "boolean_integer": (_record(value=True), _record(value=1)),
            "different_labels": (
                _record(labels=["label_a"]),
                _record(labels=["label_b"]),
            ),
            "different_unrelated_field": (
                _record(name="first"),
                _record(name="second"),
            ),
        }
        for name, records in cases.items():
            with self.subTest(name=name):
                observed = _evidence(
                    list(records), selectors=("label_a", "label_b")
                )
                affected = [
                    selector
                    for selector in ("label_a", "label_b")
                    if selector
                    in set(records[0]["labels"]) | set(records[1]["labels"])
                ]
                for selector in affected:
                    diagnostic = observed.selector_authority[selector]
                    self.assertFalse(diagnostic.complete)
                    self.assertEqual(1, diagnostic.conflicting_duplicate_count)
                    self.assertIn(
                        "entity_registry_conflicting_duplicate",
                        diagnostic.failure_reason_codes,
                    )

    def test_unsupported_and_non_finite_values_are_malformed(self):
        for value in (object(), float("nan"), float("inf"), float("-inf")):
            with self.subTest(value_type=type(value).__name__, value=str(value)):
                observed = _evidence([_record(untrusted=value)])
                diagnostic = observed.selector_authority["label_a"]
                self.assertFalse(diagnostic.complete)
                self.assertEqual(1, diagnostic.malformed_relevant_record_count)
                self.assertIn(
                    "entity_registry_malformed_relevant_record",
                    diagnostic.failure_reason_codes,
                )

    def test_oversized_and_wide_canonical_records_fail_closed_before_dedup(self):
        oversized = _record(
            unrelated="x"
            * (MAX_ENTITY_REGISTRY_CANONICAL_RECORD_BYTES + 1)
        )
        wide = _record(
            unrelated=[
                None
                for _index in range(
                    MAX_ENTITY_REGISTRY_CANONICAL_RECORD_NODES + 1
                )
            ]
        )
        for name, record in (("oversized", oversized), ("wide", wide)):
            with self.subTest(name=name):
                semantic = _deduplicate_identical_entity_registry_records(
                    [copy.deepcopy(record), copy.deepcopy(record)]
                )
                self.assertEqual(2, len(semantic))
                observed = _evidence(semantic)
                diagnostic = observed.selector_authority["label_a"]
                self.assertFalse(diagnostic.complete)
                self.assertEqual(
                    2, diagnostic.malformed_relevant_record_count
                )
                self.assertIn(
                    "entity_registry_malformed_relevant_record",
                    diagnostic.failure_reason_codes,
                )

    def test_malformed_peer_prevents_partial_identical_collapse(self):
        canonical = _record()
        malformed = _record(untrusted=object())
        semantic = _deduplicate_identical_entity_registry_records(
            [copy.deepcopy(canonical), copy.deepcopy(canonical), malformed]
        )
        self.assertEqual(3, len(semantic))
        observed = _evidence(semantic)
        diagnostic = observed.selector_authority["label_a"]
        self.assertFalse(diagnostic.complete)
        self.assertEqual(0, diagnostic.identical_duplicates_collapsed)
        self.assertEqual(1, diagnostic.malformed_relevant_record_count)
        self.assertIn(
            "entity_registry_malformed_relevant_record",
            diagnostic.failure_reason_codes,
        )

    def test_conflicts_and_malformed_records_remain_selector_local(self):
        one_selector = _evidence(
            [
                _record(labels=["label_a"], name="first"),
                _record(labels=["label_a"], name="second"),
                _record("sensor.b", ["label_b"]),
            ],
            selectors=("label_a", "label_b"),
        )
        self.assertFalse(one_selector.selector_complete["label_a"])
        self.assertTrue(one_selector.selector_complete["label_b"])

        multiple = _evidence(
            [
                _record(labels=["label_a"], name="first"),
                _record(labels=["label_b"], name="second"),
            ],
            selectors=("label_a", "label_b"),
        )
        self.assertEqual(
            {"label_a": False, "label_b": False},
            multiple.selector_complete,
        )

        unrelated = _evidence(
            [
                _record("sensor.member", ["label_a"]),
                _record("sensor.unrelated", ["label_c"], value=1),
                _record("sensor.unrelated", ["label_c"], value=2),
                _record("malformed", [], unsupported=object()),
            ]
        )
        self.assertTrue(unrelated.selector_complete["label_a"])

        unrelated_identical_record = _record(
            "sensor.unrelated_duplicate", ["label_c"]
        )
        unrelated_identical = _evidence(
            [
                _record("sensor.member", ["label_a"]),
                copy.deepcopy(unrelated_identical_record),
                copy.deepcopy(unrelated_identical_record),
            ]
        )
        self.assertTrue(unrelated_identical.selector_complete["label_a"])
        self.assertEqual(
            unrelated.memberships["label_a"],
            unrelated_identical.memberships["label_a"],
        )

        relevant_malformed = _evidence(
            [_record("malformed", ["label_a"])]
        )
        self.assertFalse(relevant_malformed.selector_complete["label_a"])

        unreadable_labels = _evidence(
            [{"entity_id": "sensor.unrelated", "labels": "label_c"}],
            selectors=("label_a", "label_b"),
        )
        self.assertEqual(
            {"label_a": False, "label_b": False},
            unreadable_labels.selector_complete,
        )

    def test_raw_record_bound_precedes_identical_deduplication(self):
        exact_limit = _evidence(
            [_record() for _index in range(MAX_EXPAND_SNAPSHOT_ENTITIES)]
        )
        exact_diagnostic = exact_limit.selector_authority["label_a"]
        self.assertTrue(exact_diagnostic.complete)
        self.assertFalse(exact_diagnostic.raw_bound_exceeded)
        self.assertEqual(
            MAX_EXPAND_SNAPSHOT_ENTITIES - 1,
            exact_diagnostic.identical_duplicates_collapsed,
        )

        over_limit = _evidence(
            [
                _record()
                for _index in range(MAX_EXPAND_SNAPSHOT_ENTITIES + 1)
            ]
        )
        over_diagnostic = over_limit.selector_authority["label_a"]
        self.assertFalse(over_diagnostic.complete)
        self.assertTrue(over_diagnostic.raw_bound_exceeded)
        self.assertIn(
            "entity_registry_raw_bound_exceeded",
            over_diagnostic.failure_reason_codes,
        )
        self.assertNotEqual(
            exact_diagnostic.authority_fingerprint,
            over_diagnostic.authority_fingerprint,
        )

    def test_identical_deduplication_precedes_expand_source_projection(self):
        state = {
            "entity_id": "sensor.synthetic_group",
            "attributes": {"entity_id": ["sensor.member"]},
        }
        source = _record(
            "sensor.synthetic_group",
            ["label_a"],
            platform="group",
        )
        single_registry = _deduplicate_identical_entity_registry_records(
            [copy.deepcopy(source)]
        )
        duplicate_registry = _deduplicate_identical_entity_registry_records(
            [copy.deepcopy(source), copy.deepcopy(source)]
        )
        self.assertEqual(single_registry, duplicate_registry)
        single = _build_expand_snapshot_evidence(
            states=[state],
            entity_registry=single_registry,
            entity_registry_complete=True,
        )
        duplicate = _build_expand_snapshot_evidence(
            states=[state],
            entity_registry=duplicate_registry,
            entity_registry_complete=True,
        )
        self.assertEqual(single, duplicate)
        self.assertTrue(duplicate.source_inventory_complete)
        self.assertEqual(
            ("sensor.member",),
            duplicate.entities["sensor.synthetic_group"].member_entity_ids,
        )

        conflicting_source = copy.deepcopy(source)
        conflicting_source["platform"] = "sensor"
        conflict_registry = _deduplicate_identical_entity_registry_records(
            [source, conflicting_source]
        )
        self.assertEqual(2, len(conflict_registry))
        conflict = _build_expand_snapshot_evidence(
            states=[state],
            entity_registry=conflict_registry,
            entity_registry_complete=True,
        )
        self.assertFalse(conflict.source_inventory_complete)
        self.assertEqual(
            "unknown",
            conflict.entities["sensor.synthetic_group"].expandable_kind,
        )

    def test_selector_and_label_raw_bounds_are_explicit_diagnostics(self):
        selector_overflow = _build_label_membership_evidence(
            ["label_a"],
            entity_registry=[_record()],
            label_registry=_labels("label_a"),
            selector_inventory_complete=False,
        )
        selector_diagnostic = selector_overflow.selector_authority[
            "label_a"
        ]
        self.assertFalse(selector_diagnostic.complete)
        self.assertIn(
            "literal_label_selector_bound_exceeded",
            selector_diagnostic.failure_reason_codes,
        )

        label_overflow = _build_label_membership_evidence(
            ["label_a"],
            entity_registry=[_record()],
            label_registry=_labels("label_a"),
            label_inventory_available=False,
            label_inventory_raw_bound_exceeded=True,
        )
        label_diagnostic = label_overflow.selector_authority["label_a"]
        self.assertFalse(label_diagnostic.complete)
        self.assertIn(
            "label_registry_raw_bound_exceeded",
            label_diagnostic.failure_reason_codes,
        )
        self.assertNotIn(
            "label_inventory_unavailable",
            label_diagnostic.failure_reason_codes,
        )

        selectors = tuple(f"label_{index:03d}" for index in range(300))
        bounded = _build_label_membership_evidence(
            list(selectors),
            entity_registry=[],
            label_registry=_labels(*selectors),
        )
        self.assertEqual(256, len(bounded.selector_authority))
        self.assertTrue(
            all(
                "literal_label_selector_bound_exceeded"
                in item.failure_reason_codes
                for item in bounded.selector_authority.values()
            )
        )

    def test_id_first_and_normalized_name_lookup_are_preserved(self):
        id_first = _build_label_membership_evidence(
            ["label_a"],
            entity_registry=[_record()],
            label_registry=[
                {"label_id": "label_a", "name": "Primary"},
                {"label_id": "different", "name": "Label A"},
            ],
        )
        self.assertTrue(id_first.complete)
        self.assertEqual(
            "label_id", id_first.selector_authority["label_a"].lookup_mode
        )

        normalized = _build_label_membership_evidence(
            ["Label A"],
            entity_registry=[_record()],
            label_registry=[{"label_id": "label_a", "name": "Label A"}],
        )
        self.assertTrue(normalized.complete)
        self.assertEqual(
            "normalized_name",
            normalized.selector_authority["Label A"].lookup_mode,
        )

    def test_membership_and_authority_fingerprints_keep_distinct_models(self):
        observed = _evidence([_record()])
        evidence = observed.selector_authority["label_a"]
        lookup_material = {
            "model": LABEL_LOOKUP_MODEL,
            "selector": "label_a",
            "lookup_mode": "label_id",
            "resolved_label_id": "label_a",
            "entity_ids": ["sensor.member"],
            "complete": True,
        }
        authority_material = {
            **lookup_material,
            "model": LABEL_SELECTOR_AUTHORITY_MODEL,
            "failure_reason_codes": [],
            "entity_inventory_available": True,
            "entity_inventory_complete": True,
            "label_inventory_available": True,
            "label_inventory_complete": True,
            "raw_bound_exceeded": False,
        }

        def fingerprint(material: dict) -> str:
            return hashlib.sha256(
                json.dumps(
                    material,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()

        self.assertEqual(
            fingerprint(lookup_material),
            observed.fingerprints["label_a"],
        )
        self.assertEqual(
            fingerprint(authority_material), evidence.authority_fingerprint
        )
        self.assertNotEqual(
            observed.fingerprints["label_a"],
            evidence.authority_fingerprint,
        )


class VariantReplayWebSocket(CapturedBeta50ReplayWebSocket):
    def __init__(self, fixture: dict, entity_ids: set[str], mode: str) -> None:
        super().__init__(fixture, entity_ids)
        self.mode = mode

    async def command(self, payload: dict):
        result = await super().command(payload)
        if payload != {"type": "config/entity_registry/list"}:
            return result
        result = list(result)
        selected = next(item for item in result if len(item["labels"]) >= 2)
        if self.mode == "identical_duplicate":
            result.append(copy.deepcopy(selected))
        elif self.mode == "conflicting_duplicate":
            conflict = copy.deepcopy(selected)
            conflict["labels"] = []
            result.append(conflict)
        elif self.mode == "malformed_relevant":
            selected["entity_id"] = "malformed"
        return result


class Beta53ProductionReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.capture = json.loads(REPLAY.read_text(encoding="utf-8"))
        self.fixture = _transport_fixture(self.capture)
        self.target = self.fixture["target_entity_id"]

    async def _run(self, mode: str):
        rest = CapturedBeta50ReplayRest(self.fixture)
        websocket = VariantReplayWebSocket(self.fixture, rest.ids, mode)
        index = DependencyIndex(
            DirectHaDependencyProvider(rest, websocket, concurrency=4)
        )
        snapshot, rebuilt, _lookup_ms = await index.get(refresh=True)
        self.assertTrue(rebuilt)
        evidence = await HelperDependencyRiskService(index).assess(
            self.target, refresh=False
        )
        return rest, websocket, index, snapshot, evidence

    @staticmethod
    def _lock_keys(target: str, binding: dict) -> set[str]:
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=target),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": binding},
        )
        return {
            item.key
            for item in OperationalLockSetCalculator().calculate(operation)
        }

    async def test_identical_duplicate_matches_complete_semantic_authority(self):
        complete = await self._run("complete")
        duplicate = await self._run("identical_duplicate")
        complete_snapshot = complete[3]
        duplicate_snapshot = duplicate[3]
        complete_binding = complete[4]["binding"]
        duplicate_binding = duplicate[4]["binding"]

        self.assertEqual("helper-dependency-risk-v12", HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(complete_snapshot.fingerprint, duplicate_snapshot.fingerprint)
        self.assertEqual(
            complete_snapshot.label_memberships,
            duplicate_snapshot.label_memberships,
        )
        self.assertEqual(
            complete_snapshot.label_membership_fingerprints,
            duplicate_snapshot.label_membership_fingerprints,
        )
        self.assertEqual(
            complete_binding["evidence_fingerprint"],
            duplicate_binding["evidence_fingerprint"],
        )
        semantic_fields = (
            "exact_dependency_obligation_count",
            "opaque_obligation_count",
            "downstream_profiles",
            "coverage_complete",
            "evidence_complete",
            "semantic_precision",
            "physical_consequence",
            "execution_eligible",
            "dependency_lock_projection",
        )
        for field in semantic_fields:
            self.assertEqual(
                complete_binding[field], duplicate_binding[field], field
            )
        self.assertEqual(
            self._lock_keys(self.target, complete_binding),
            self._lock_keys(self.target, duplicate_binding),
        )
        lock_keys = self._lock_keys(self.target, duplicate_binding)
        self.assertIn(f"helper:{self.target}", lock_keys)
        self.assertIn("helper_dependency:input_boolean_dynamic", lock_keys)
        self.assertFalse(
            any(key.startswith("automation:") for key in lock_keys)
        )
        self.assertFalse(
            duplicate_binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        assessment = helper_dependency_risk_assessment(duplicate[4])
        self.assertEqual("low", assessment.level.value)
        self.assertTrue(assessment.apply_allowed)
        self.assertEqual(0, duplicate_binding["opaque_obligation_count"])
        self.assertEqual([], duplicate_binding["downstream_profiles"])
        duplicate_counts = sorted(
            item["identical_duplicates_collapsed"]
            for item in duplicate_binding["selector_authority_diagnostics"]
        )
        self.assertEqual([0, 1, 1], duplicate_counts)
        self.assertTrue(
            all(
                item["target_disposition"] == "excluded"
                for item in duplicate_binding[
                    "selector_authority_diagnostics"
                ]
            )
        )
        self.assertEqual(0, complete[0].calls.count(("POST", "/")))

    async def test_conflict_and_malformed_controls_remain_fail_closed(self):
        for mode, reason in (
            (
                "conflicting_duplicate",
                "entity_registry_conflicting_duplicate",
            ),
            (
                "malformed_relevant",
                "entity_registry_malformed_relevant_record",
            ),
        ):
            with self.subTest(mode=mode):
                rest, _websocket, _index, _snapshot, evidence = await self._run(
                    mode
                )
                binding = evidence["binding"]
                self.assertFalse(binding["execution_eligible"])
                self.assertFalse(binding["evidence_complete"])
                self.assertTrue(
                    binding["dependency_lock_projection"]
                    ["conservative_helper_dependency"]
                )
                self.assertIn(
                    unconstrained_helper_dependency_lock_key(),
                    self._lock_keys(self.target, binding),
                )
                self.assertTrue(
                    any(
                        reason in item["failure_reason_codes"]
                        for item in binding[
                            "selector_authority_diagnostics"
                        ]
                    )
                )
                self.assertFalse(
                    any(method != "GET" for method, _path in rest.calls)
                )

    async def test_selector_diagnostics_persist_paginate_and_redact(self):
        rest, websocket, index, _snapshot, _evidence = await self._run(
            "identical_duplicate"
        )
        helper = FakeHelperStateGateway()
        helper.entity_id = self.target
        with tempfile.TemporaryDirectory() as temporary:
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(temporary) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=(
                    HelperDependencyRiskService(index).assess
                ),
                plan_observability_cursor_key=b"beta53-selector-detail" * 2,
            )
            created = await governance.create_helper_state_plan(
                entity_id=self.target,
                desired_state="on",
            )
            plan_id = created["plan"]["plan_id"]
            items: list[dict] = []
            cursor = ""
            full_set_fingerprint = None
            while True:
                observed = governance.get_plan_observability(
                    plan_id,
                    detail_section="summary",
                    cursor=cursor,
                    page_size=1,
                )
                detail = observed["detail"]
                items.extend(detail["items"])
                full_set_fingerprint = (
                    full_set_fingerprint
                    or detail["full_set_fingerprint"]
                )
                self.assertEqual(
                    full_set_fingerprint,
                    detail["full_set_fingerprint"],
                )
                if not detail["has_more"]:
                    break
                cursor = detail["next_cursor"]
            self.assertEqual(3, len(items))
            self.assertEqual(
                3,
                observed["canonical_summary"]
                ["selector_authority_diagnostic_count"],
            )
            encoded = json.dumps(items, sort_keys=True)
            for raw_selector in (
                item["selector"]
                for item in self.capture["membership_evidence"]["labels"]
            ):
                self.assertNotIn(raw_selector, encoded)
            self.assertFalse(created["provider_dispatch_occurred"])
            self.assertEqual(0, helper.dispatch_count)
        self.assertFalse(any(method != "GET" for method, _path in rest.calls))
        self.assertEqual(
            0,
            sum(
                payload.get("type", "").endswith(("update", "delete"))
                for payload in websocket.calls
            ),
        )

    async def test_final_preflight_accepts_duplicate_anomaly_but_rejects_conflict(self):
        complete = await self._run("complete")
        duplicate = await self._run("identical_duplicate")
        conflict = await self._run("conflicting_duplicate")
        planned_binding = complete[4]["binding"]

        async def preflight(observed_binding: dict, suffix: str):
            with tempfile.TemporaryDirectory() as temporary:
                context = make_context(
                    Path(temporary) / suffix,
                    SET_INPUT_BOOLEAN_STATE,
                    target_id=self.target,
                )
                strategy = context.adapter.strategies[
                    SET_INPUT_BOOLEAN_STATE
                ]
                strategy.gateway.baseline["entity_id"] = self.target
                context.plan.operational.baseline["dependency_risk"] = (
                    copy.deepcopy(planned_binding)
                )
                context.plan.current_state_fingerprint = stable_hash(
                    context.plan.operational.baseline
                )

                async def risk_reader(
                    entity_id: str,
                    *,
                    refresh: bool = True,
                    fenced: bool = False,
                ):
                    self.assertEqual(self.target, entity_id)
                    self.assertTrue(refresh)
                    self.assertTrue(fenced)
                    return {
                        "binding": copy.deepcopy(observed_binding),
                        "provenance": {
                            "provider": "dependency_index",
                            "completeness": "complete",
                            "generation": 1,
                            "fingerprint": "9" * 64,
                            "freshness": "current",
                            "fallback": "none",
                        },
                    }

                strategy.dependency_risk_reader = risk_reader
                prepared = await prepare_context(context)
                locks = context.adapter.lock_requests(prepared)
                result = await context.adapter.preflight(
                    prepared, acquired_locks=locks
                )
                return result, strategy, context

        accepted, accepted_strategy, accepted_context = await preflight(
            duplicate[4]["binding"], "duplicate"
        )
        self.assertTrue(accepted.eligible)
        self.assertEqual(0, accepted_strategy.gateway.provider_dispatches)
        self.assertEqual(0, accepted_context.approval.consumption_count)

        rejected, rejected_strategy, rejected_context = await preflight(
            conflict[4]["binding"], "conflict"
        )
        self.assertFalse(rejected.eligible)
        self.assertIn(
            "dependency_risk_execution_eligibility",
            rejected.mismatch_fields,
        )
        self.assertEqual(0, rejected_strategy.gateway.provider_dispatches)
        self.assertEqual(0, rejected_context.approval.consumption_count)


class Beta53HistoricalFalsificationTests(unittest.TestCase):
    def test_beta53_raw_overflow_precedes_deduplication_and_cannot_dispatch(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--source-root",
                str(ROOT),
                "--fixture",
                str(REPLAY),
                "--entity-registry-mode",
                "raw_overflow",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        observed = json.loads(completed.stdout)
        self.assertEqual("helper-dependency-risk-v12", observed["risk_model"])
        self.assertFalse(observed["coverage_complete"])
        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertFalse(observed["approval_actionable"])
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            observed["lock_keys"],
        )
        target = self.capture["read_only_accounting"][
            "target_state_baseline"
        ]["entity_id"]
        self.assertIn(f"helper:{target}", observed["lock_keys"])
        self.assertTrue(
            all(
                item["raw_bound_exceeded"]
                and "entity_registry_raw_bound_exceeded"
                in item["failure_reason_codes"]
                for item in observed["selector_authority_diagnostics"]
            )
        )
        self.assertEqual(0, observed["provider_dispatch_count"])

    def test_exact_beta52_complete_and_identical_duplicate_controls(self):
        source_commit = self.capture["provenance"]["source_build_sha"]
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary) / "beta52-source"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--shared",
                    "--no-checkout",
                    "--quiet",
                    str(ROOT),
                    str(source_root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "checkout",
                    "--detach",
                    "--quiet",
                    source_commit,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            def replay(mode: str) -> dict:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(RUNNER),
                        "--source-root",
                        str(source_root),
                        "--fixture",
                        str(REPLAY),
                        "--entity-registry-mode",
                        mode,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(completed.stdout)

            control = replay("complete")
            duplicate = replay("identical_duplicate")

        self.assertEqual(source_commit, control["source_release_commit"])
        self.assertEqual("helper-dependency-risk-v11", control["risk_model"])
        self.assertEqual(
            "helper-dependency-risk-v11", duplicate["risk_model"]
        )
        self.assertEqual((0, 0, 0), (
            control["exact_dependency_count"],
            control["target_capable_opaque_obligation_count"],
            control["downstream_profile_count"],
        ))
        self.assertTrue(control["coverage_complete"])
        self.assertTrue(control["approval_actionable"])
        self.assertEqual((0, 24, 2), (
            duplicate["exact_dependency_count"],
            duplicate["target_capable_opaque_obligation_count"],
            duplicate["downstream_profile_count"],
        ))
        self.assertEqual(
            {"$.variables.issue_message": 14, "$.variables.issue_signature": 10},
            duplicate["configuration_path_counts"],
        )
        self.assertEqual([2, 22], sorted(duplicate["source_obligation_counts"].values()))
        self.assertEqual({"target_capable": 24}, duplicate["target_selector_scope_counts"])
        self.assertEqual({"conservative": 24}, duplicate["lock_projection_counts"])
        self.assertEqual(
            {
                "filter_state_operand": 4,
                "global_entity_name": 1,
                "global_label_entities": 5,
                "global_states": 3,
                "membership_state_operand": 3,
                "state_collection_iteration": 2,
                "state_object_attribute": 2,
                "states_item_access": 4,
            },
            duplicate["obligation_kind_counts"],
        )
        self.assertEqual(
            {
                "entity_name_entity_access_target_opaque": 1,
                "label_entities_entity_set_membership_unavailable": 5,
                "membership_iterates_state_value_target_opaque": 3,
                "state_collection_iterated_target_opaque": 2,
                "state_object_last_changed_access_target_opaque": 2,
                "state_value_consumed_by_filter_target_opaque": 4,
                "states_entity_access_target_opaque": 3,
                "states_item_entity_access_target_opaque": 4,
            },
            duplicate["reason_code_counts"],
        )
        self.assertEqual(
            _normalized_semantic_rows(
                self.capture["obligations"],
                _OBLIGATION_NORMALIZED_FIELDS,
            ),
            duplicate["normalized_obligation_rows"],
        )
        self.assertEqual(
            _normalized_semantic_rows(
                self.capture["downstream_profiles"],
                _PROFILE_NORMALIZED_FIELDS,
            ),
            duplicate["normalized_downstream_profile_rows"],
        )
        self.assertEqual(
            ["action_profile_semantic_incomplete"],
            duplicate["coverage_failure_reason_codes"],
        )
        self.assertEqual("unknown", duplicate["physical_consequence"])
        self.assertEqual("high", duplicate["risk_level"])
        self.assertEqual("elevated_admin", duplicate["policy_class"])
        self.assertFalse(duplicate["execution_eligible"])
        self.assertFalse(duplicate["approval_actionable"])
        downstream_locks = [
            key
            for key in duplicate["lock_keys"]
            if key.startswith("automation:")
        ]
        self.assertEqual(2, len(downstream_locks))
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            duplicate["lock_keys"],
        )
        self.assertEqual(0, duplicate["provider_dispatch_count"])

    def setUp(self) -> None:
        self.capture = json.loads(REPLAY.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
