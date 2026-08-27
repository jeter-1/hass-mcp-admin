"""Beta 49 target-scope propagation through the production dependency path."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.dependency.service import (  # noqa: E402
    EntityDependencyAnalysisService,
)
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
    helper_dependency_risk_assessment,
)
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    Clock,
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)


STANDARD_TARGET = "input_boolean.beta49_standard"
CONSEQUENTIAL_TARGET = "input_boolean.guest_mode"
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


LABEL_EXPANSION = (
    "{{ expand(label_entities('reviewed_sensors')) "
    "| map(attribute='state') | list }}"
)
FINITE_EXPANSION = (
    "{{ expand(['sensor.synthetic_alpha', "
    "'binary_sensor.synthetic_beta']) "
    "| map(attribute='state') | list }}"
)
LABEL_STATE_TRANSPORT = (
    "{{ label_entities('reviewed_sensors') | map('states') "
    "| map(attribute='state') | list }}"
)


def _condition(template: str) -> dict:
    return {"condition": "template", "value_template": template}


class SyntheticBeta49Rest:
    """Sanitized Home Assistant responses for the deployed-shape regression."""

    def __init__(self, *, arbitrary_only: bool = False) -> None:
        residual_conditions = (
            [_condition(LABEL_EXPANSION) for _ in range(8)]
            + [_condition(FINITE_EXPANSION) for _ in range(9)]
            + [_condition(LABEL_STATE_TRANSPORT) for _ in range(9)]
            + [
                _condition(
                    "{{ trigger.to_state.name }} "
                    "{{ trigger.to_state.state }} "
                    "{{ trigger.to_state.context.user_id }}"
                ),
                _condition(
                    "{{ states.sensor | map(attribute='state') | list }}"
                ),
                _condition(
                    "{{ states.binary_sensor "
                    "| selectattr('state', 'eq', 'on') | list }}"
                ),
            ]
        )
        self.configs = {
            "residual_scope": {
                "alias": "Synthetic Beta 49 residual scope",
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "person.synthetic_resident",
                    }
                ],
                "condition": residual_conditions,
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                ],
            },
        }
        if arbitrary_only:
            self.configs = {
                "arbitrary_selector": {
                    "alias": "Synthetic arbitrary selector",
                    "trigger": [],
                    "condition": [
                        _condition("{{ states(caller_supplied) }}")
                    ],
                    "action": [
                        {
                            "service": "cover.open_cover",
                            "target": {
                                "entity_id": "cover.synthetic_garage"
                            },
                        }
                    ],
                }
            }
            return
        for index in range(7):
            source_id = f"guest_consequence_{index}"
            self.configs[source_id] = {
                "alias": f"Synthetic guest consequence {index}",
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": CONSEQUENTIAL_TARGET,
                    }
                ],
                "condition": [],
                "action": [
                    {
                        "service": "lock.unlock",
                        "target": {
                            "entity_id": f"lock.synthetic_{index}"
                        },
                    }
                ],
            }

    async def request(self, method: str, path: str):
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            states = [
                {
                    "entity_id": STANDARD_TARGET,
                    "state": "off",
                    "attributes": {},
                },
                {
                    "entity_id": CONSEQUENTIAL_TARGET,
                    "state": "off",
                    "attributes": {},
                },
            ]
            states.extend(
                {
                    "entity_id": f"automation.{source_id}",
                    "state": "on",
                    "attributes": {
                        "id": source_id,
                        "friendly_name": config["alias"],
                    },
                }
                for source_id, config in self.configs.items()
            )
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return self.configs[path.removeprefix(prefix)]
        raise AssertionError(path)


class SyntheticBeta49WebSocket:
    def __init__(
        self, *, member_entities: tuple[str, ...] | None = None
    ) -> None:
        self.member_entities = member_entities or (
            "sensor.synthetic_alpha",
            "binary_sensor.synthetic_beta",
        )

    async def command(self, payload: dict):
        if payload == {"type": "config/entity_registry/list"}:
            return [
                {
                    "entity_id": entity_id,
                    "labels": ["reviewed_sensors"],
                }
                for entity_id in self.member_entities
            ]
        if payload == {"type": "config/label_registry/list"}:
            return [
                {
                    "label_id": "reviewed_sensors",
                    "name": "Reviewed Sensors",
                }
            ]
        raise AssertionError(payload)


class Beta49ProducerFalsificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta49Rest(),
                SyntheticBeta49WebSocket(),
                concurrency=4,
            )
        )
        self.snapshot, _rebuilt, _lookup_ms = await self.index.get(
            refresh=True
        )

    async def test_real_residual_matrix_is_closed_before_aggregation(self):
        residual = [
            item
            for item in self.snapshot.obligations
            if item.source_id == "residual_scope"
            and item.target_selector_scope == "target_capable"
        ]
        counts = Counter(item.reason_code for item in residual)

        self.assertEqual(0, len(residual), counts)
        self.assertNotIn(
            "expand_expanded_membership_opaque", counts
        )
        self.assertNotIn(
            "filter_member_attribute_receiver_opaque", counts
        )
        service = HelperDependencyRiskService(self.index)
        for target in (STANDARD_TARGET, CONSEQUENTIAL_TARGET):
            with self.subTest(target=target):
                evidence = await service.assess(target, refresh=False)
                residual_projection = [
                    item
                    for item in (
                        evidence["binding"]["obligation_evidence"]
                    )
                    if item["source_object_id"]
                    == "automation.residual_scope"
                ]
                self.assertEqual([], residual_projection)

    async def test_standard_and_consequential_controls_are_actionable(self):
        service = HelperDependencyRiskService(self.index)
        standard = await service.assess(STANDARD_TARGET, refresh=False)
        standard_binding = standard["binding"]
        standard_risk = helper_dependency_risk_assessment(standard)

        self.assertEqual(
            0, standard_binding["exact_dependency_obligation_count"]
        )
        self.assertEqual(0, standard_binding["opaque_obligation_count"])
        self.assertEqual([], standard_binding["downstream_profiles"])
        self.assertTrue(standard_binding["evidence_complete"])
        self.assertTrue(standard_binding["execution_eligible"])
        self.assertEqual("none", standard_binding["physical_consequence"])
        self.assertEqual("low", standard_risk.level.value)
        self.assertTrue(standard_risk.apply_allowed)

        consequential = await service.assess(
            CONSEQUENTIAL_TARGET, refresh=False
        )
        binding = consequential["binding"]
        risk = helper_dependency_risk_assessment(consequential)
        self.assertGreaterEqual(
            binding["exact_dependency_obligation_count"], 7
        )
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual(7, len(binding["downstream_profiles"]))
        self.assertEqual(
            "safety_critical", binding["physical_consequence"]
        )
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("high", risk.level.value)
        self.assertTrue(risk.apply_allowed)

    async def test_arbitrary_selector_remains_fail_closed(self):
        index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta49Rest(arbitrary_only=True),
                SyntheticBeta49WebSocket(),
            )
        )
        evidence = await HelperDependencyRiskService(index).assess(
            STANDARD_TARGET, refresh=True
        )
        # The standard target is deliberately evaluated against a snapshot
        # containing one separate arbitrary-selector source. That source must
        # remain relevant even after every finite residual is closed.
        binding = evidence["binding"]
        arbitrary = [
            item
            for item in binding["obligation_evidence"]
            if item["source_object_id"] == "automation.arbitrary_selector"
        ]
        self.assertTrue(arbitrary)
        self.assertTrue(
            all(
                item["target_outcome"] == "bounded_semantic_opaque"
                for item in arbitrary
            )
        )
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])

    async def test_recursive_group_expansion_remains_conservative(self):
        index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta49Rest(),
                SyntheticBeta49WebSocket(
                    member_entities=("group.synthetic_recursive",)
                ),
            )
        )
        binding = (
            await HelperDependencyRiskService(index).assess(
                STANDARD_TARGET, refresh=True
            )
        )["binding"]
        residual = [
            item
            for item in binding["obligation_evidence"]
            if item["source_object_id"] == "automation.residual_scope"
        ]
        self.assertTrue(residual)
        self.assertTrue(
            any(
                item["target_outcome"] == "bounded_semantic_opaque"
                and "group.synthetic_recursive"
                in item["candidate_entity_ids"]
                for item in residual
            )
        )
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])

    async def test_label_membership_drift_changes_target_scope_and_binding(self):
        excluded = (
            await HelperDependencyRiskService(self.index).assess(
                STANDARD_TARGET, refresh=False
            )
        )["binding"]
        included_index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta49Rest(),
                SyntheticBeta49WebSocket(
                    member_entities=(
                        "sensor.synthetic_alpha",
                        STANDARD_TARGET,
                    )
                ),
            )
        )
        included = (
            await HelperDependencyRiskService(included_index).assess(
                STANDARD_TARGET, refresh=True
            )
        )["binding"]

        self.assertEqual(0, excluded["exact_dependency_obligation_count"])
        self.assertGreater(
            included["exact_dependency_obligation_count"], 0
        )
        self.assertIn(
            "automation.residual_scope",
            included["relevant_downstream_object_ids"],
        )
        self.assertNotEqual(
            excluded["evidence_fingerprint"],
            included["evidence_fingerprint"],
        )


class Beta49ProductionPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rest = SyntheticBeta49Rest()
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                self.rest,
                SyntheticBeta49WebSocket(),
                concurrency=4,
            )
        )
        self.temp = tempfile.TemporaryDirectory()
        self.helper = FakeHelperStateGateway()
        self.risk_service = HelperDependencyRiskService(self.index)
        self.governance = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            UnusedLegacyGateway(),
            now=Clock(),
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.risk_service.assess,
            plan_observability_cursor_key=b"beta49-cursor-key" * 2,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _traverse(self, plan_id: str, section: str) -> tuple[list[dict], str]:
        items: list[dict] = []
        cursor = ""
        fingerprint = ""
        while True:
            result = self.governance.get_plan_observability(
                plan_id,
                detail_section=section,
                cursor=cursor,
                page_size=2,
            )
            detail = result["detail"]
            self.assertFalse(detail["fragments"])
            items.extend(detail["items"])
            if not fingerprint:
                fingerprint = detail["full_set_fingerprint"]
            self.assertEqual(fingerprint, detail["full_set_fingerprint"])
            if not detail["has_more"]:
                self.assertIsNone(detail["next_cursor"])
                break
            cursor = detail["next_cursor"]
            self.assertTrue(cursor)
        return items, fingerprint

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
            request.key
            for request in OperationalLockSetCalculator().calculate(operation)
        }

    async def _create_plan(self, target: str) -> tuple[dict, dict]:
        self.helper.entity_id = target
        created = await self.governance.create_helper_state_plan(
            entity_id=target,
            desired_state="on",
        )
        self.assertFalse(created["provider_dispatch_occurred"])
        plan = created["plan"]
        observed = self.governance.get_plan_observability(plan["plan_id"])
        return plan, observed

    async def test_complete_analysis_plan_persistence_and_lock_path(self):
        analysis = await EntityDependencyAnalysisService(self.index).analyze(
            entity_id=STANDARD_TARGET,
            detail_level="evidence",
            include_indirect=True,
            refresh_index=True,
        )
        # A zero-reference read is observation only. The target-specific
        # obligation binding below independently proves exclusion.
        self.assertEqual(
            0, analysis.data["overview"]["direct_reference_count"]
        )

        standard_plan, standard_observed = await self._create_plan(
            STANDARD_TARGET
        )
        standard_binding = standard_plan["operational"]["baseline"][
            "dependency_risk"
        ]
        standard_summary = standard_observed["canonical_summary"]
        self.assertEqual("helper-dependency-risk-v8", standard_binding["model"])
        self.assertEqual(0, standard_binding["opaque_obligation_count"])
        self.assertEqual([], standard_binding["downstream_profiles"])
        self.assertTrue(standard_plan["approval_actionable"])
        self.assertEqual("low", standard_plan["risk"]["level"])
        self.assertEqual(
            "standard_admin",
            standard_plan["policy_decision"]["policy_class"],
        )
        identity = self.index.active_identity()
        self.assertEqual(
            identity["generation"],
            standard_summary["dependency_index_generation"],
        )
        self.assertEqual(
            identity["fingerprint"],
            standard_summary["dependency_index_fingerprint"],
        )
        self.assertFalse(
            any(
                key.startswith("automation:")
                for key in self._lock_keys(
                    STANDARD_TARGET, standard_binding
                )
            )
        )

        guest_plan, guest_observed = await self._create_plan(
            CONSEQUENTIAL_TARGET
        )
        guest_binding = guest_plan["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertEqual(0, guest_binding["opaque_obligation_count"])
        self.assertEqual(7, len(guest_binding["downstream_profiles"]))
        self.assertEqual("high", guest_plan["risk"]["level"])
        self.assertEqual(
            "elevated_admin",
            guest_plan["policy_decision"]["policy_class"],
        )
        self.assertTrue(guest_plan["approval_actionable"])
        self.assertEqual(
            7,
            len(
                {
                    key
                    for key in self._lock_keys(
                        CONSEQUENTIAL_TARGET, guest_binding
                    )
                    if key.startswith("automation:")
                }
            ),
        )
        self.assertEqual(
            self.index.active_identity()["fingerprint"],
            guest_observed["canonical_summary"][
                "dependency_index_fingerprint"
            ],
        )

        for plan_id, sections in (
            (standard_plan["plan_id"], ("obligation_evidence",)),
            (
                guest_plan["plan_id"],
                ("obligation_evidence", "downstream_profiles"),
            ),
        ):
            for section in sections:
                first = self._traverse(plan_id, section)
                second = self._traverse(plan_id, section)
                self.assertEqual(first, second)
        self.assertEqual(0, self.helper.dispatch_count)

    def test_v3_through_v7_remain_read_only(self):
        self.assertEqual("helper-dependency-risk-v8", HELPER_DEPENDENCY_RISK_MODEL)
        self.assertEqual(
            frozenset({"helper-dependency-risk-v8"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for version in range(3, 8):
            model = f"helper-dependency-risk-v{version}"
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


if __name__ == "__main__":
    unittest.main()
