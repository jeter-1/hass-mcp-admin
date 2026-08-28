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
    _build_expand_snapshot_evidence,
)
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    _resolve_expand_candidates,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.dependency.service import (  # noqa: E402
    EntityDependencyAnalysisService,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
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
    UnusedConfigurationGateway,
    UnusedLegacyGateway,
    forbidden_upstream_identity,
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

    def __init__(
        self,
        *,
        arbitrary_only: bool = False,
        extra_states: tuple[dict, ...] = (),
    ) -> None:
        self.extra_states = extra_states
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
                {
                    "entity_id": "sensor.synthetic_alpha",
                    "state": "1",
                    "attributes": {},
                },
                {
                    "entity_id": "binary_sensor.synthetic_beta",
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
            states.extend(self.extra_states)
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return self.configs[path.removeprefix(prefix)]
        raise AssertionError(path)


class SyntheticBeta49WebSocket:
    def __init__(
        self,
        *,
        member_entities: tuple[str, ...] | None = None,
        member_platform: str = "synthetic",
        extra_registry_entries: tuple[dict, ...] = (),
    ) -> None:
        self.member_entities = (
            member_entities
            if member_entities is not None
            else (
                "sensor.synthetic_alpha",
                "binary_sensor.synthetic_beta",
            )
        )
        self.member_platform = member_platform
        self.extra_registry_entries = extra_registry_entries

    async def command(self, payload: dict):
        if payload == {"type": "config/entity_registry/list"}:
            entries = [
                {
                    "entity_id": entity_id,
                    "labels": ["reviewed_sensors"],
                    "platform": self.member_platform,
                }
                for entity_id in self.member_entities
            ]
            for entity_id, platform in (
                ("sensor.synthetic_alpha", "sensor"),
                ("binary_sensor.synthetic_beta", "binary_sensor"),
            ):
                if entity_id not in self.member_entities:
                    entries.append(
                        {
                            "entity_id": entity_id,
                            "labels": [],
                            "platform": platform,
                        }
                    )
            return entries + list(self.extra_registry_entries)
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

    @staticmethod
    async def _expanded_index(
        *,
        label_entity_id: str,
        label_platform: str = "synthetic",
        extra_states: tuple[dict, ...],
        extra_registry_entries: tuple[dict, ...],
    ) -> tuple[DependencyIndex, object]:
        index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta49Rest(extra_states=extra_states),
                SyntheticBeta49WebSocket(
                    member_entities=(label_entity_id,),
                    member_platform=label_platform,
                    extra_registry_entries=extra_registry_entries,
                ),
            )
        )
        snapshot, _rebuilt, _lookup_ms = await index.get(refresh=True)
        return index, snapshot

    @staticmethod
    def _expand_resolution_fingerprints(snapshot) -> tuple[str, ...]:
        return tuple(
            sorted(
                value
                for obligation in snapshot.obligations
                for value in obligation.context_provenance
                if value.startswith("expand_resolution_fingerprint:")
            )
        )

    def test_source_domains_require_canonical_bounded_home_assistant_domain(
        self,
    ):
        cases = (
            ("group", True, "group"),
            ("mqtt", True, "leaf"),
            ("integration_2", True, "leaf"),
            (" group ", False, "unknown"),
            ("Group", False, "unknown"),
            ("group\n", False, "unknown"),
            ("group.example", False, "unknown"),
            ("group-name", False, "unknown"),
            ("gro up", False, "unknown"),
            (" ", False, "unknown"),
            ("", False, "unknown"),
            (None, False, "unknown"),
            (1, False, "unknown"),
            (["group"], False, "unknown"),
            ("a" * 65, False, "unknown"),
            ("group\x00", False, "unknown"),
            ("gr\u043eup", False, "unknown"),
            ("123_456", False, "unknown"),
            ("___", False, "unknown"),
        )
        for platform, valid, expected_kind in cases:
            with self.subTest(platform=platform):
                snapshot = _build_expand_snapshot_evidence(
                    states=[
                        {
                            "entity_id": "light.synthetic_source",
                            "state": "on",
                            "attributes": {
                                "entity_id": ["light.synthetic_member"]
                            },
                        },
                        {
                            "entity_id": "light.synthetic_member",
                            "state": "on",
                            "attributes": {},
                        },
                    ],
                    entity_registry=[
                        {
                            "entity_id": "light.synthetic_source",
                            "platform": platform,
                        },
                        {
                            "entity_id": "light.synthetic_member",
                            "platform": "light",
                        },
                    ],
                    entity_registry_complete=True,
                )
                evidence = snapshot.entities["light.synthetic_source"]
                self.assertEqual(expected_kind, evidence.expandable_kind)
                if valid:
                    self.assertTrue(snapshot.source_inventory_complete)
                    self.assertEqual(platform, evidence.source_domain)
                    self.assertIsNone(evidence.failure_reason)
                else:
                    self.assertFalse(snapshot.source_inventory_complete)
                    self.assertIsNone(evidence.source_domain)
                    self.assertEqual(
                        "expand_entity_source_malformed",
                        evidence.failure_reason,
                    )

    def test_source_invalidation_is_monotonic_across_order_and_repetition(
        self,
    ):
        state = {
            "entity_id": "light.synthetic_group",
            "state": "on",
            "attributes": {"entity_id": ["light.synthetic_member"]},
        }
        valid = {
            "entity_id": "light.synthetic_group",
            "platform": "group",
        }
        invalid = {
            "entity_id": "light.synthetic_group",
            "platform": " group ",
        }
        other = {
            "entity_id": "light.synthetic_group",
            "platform": "mqtt",
        }
        member = {
            "entity_id": "light.synthetic_member",
            "platform": "light",
        }
        cases = (
            (valid, invalid),
            (invalid, valid),
            (valid, invalid, valid),
            (invalid, valid, other),
            (other, invalid, valid),
        )
        fingerprints = set()
        for entries in cases:
            with self.subTest(entries=entries):
                snapshot = _build_expand_snapshot_evidence(
                    states=[
                        state,
                        {
                            "entity_id": "light.synthetic_member",
                            "state": "on",
                            "attributes": {},
                        },
                    ],
                    entity_registry=[*entries, member],
                    entity_registry_complete=True,
                )
                evidence = snapshot.entities["light.synthetic_group"]
                self.assertFalse(snapshot.source_inventory_complete)
                self.assertIsNone(evidence.source_domain)
                self.assertEqual("unknown", evidence.expandable_kind)
                self.assertEqual(
                    "expand_entity_source_malformed",
                    evidence.failure_reason,
                )
                fingerprints.add(evidence.membership_fingerprint)
        self.assertEqual(1, len(fingerprints))

    def test_duplicate_registry_sources_fail_closed_order_independently(self):
        states = [
            {
                "entity_id": "light.synthetic_group",
                "state": "on",
                "attributes": {"entity_id": ["light.synthetic_member"]},
            },
            {
                "entity_id": "light.synthetic_member",
                "state": "on",
                "attributes": {},
            },
        ]
        member_entry = {
            "entity_id": "light.synthetic_member",
            "platform": "light",
        }
        duplicate_cases = (
            (
                {
                    "entity_id": "light.synthetic_group",
                    "platform": "group",
                },
                {
                    "entity_id": "light.synthetic_group",
                    "platform": "light",
                },
            ),
            (
                {
                    "entity_id": "light.synthetic_group",
                    "platform": "group",
                },
                {
                    "entity_id": "light.synthetic_group",
                    "platform": "group",
                },
            ),
            (
                {
                    "entity_id": "light.synthetic_group",
                    "platform": "group",
                },
                {
                    "entity_id": "light.synthetic_group",
                    "platform": None,
                },
            ),
        )
        for duplicates in duplicate_cases:
            with self.subTest(duplicates=duplicates):
                projections = []
                for ordered in (duplicates, tuple(reversed(duplicates))):
                    snapshot = _build_expand_snapshot_evidence(
                        states=states,
                        entity_registry=[*ordered, member_entry],
                        entity_registry_complete=True,
                    )
                    evidence = snapshot.entities["light.synthetic_group"]
                    self.assertEqual("unknown", evidence.expandable_kind)
                    self.assertIsNone(evidence.source_domain)
                    self.assertEqual(
                        "expand_entity_source_malformed",
                        evidence.failure_reason,
                    )
                    projections.append(evidence)
                self.assertEqual(projections[0], projections[1])

    def test_generic_groups_and_zones_ignore_unneeded_source_conflicts(self):
        cases = (
            (
                "group.synthetic_container",
                "entity_id",
                "light.synthetic_member",
                "group",
            ),
            (
                "zone.synthetic_container",
                "persons",
                "person.synthetic_member",
                "zone",
            ),
        )
        for container_id, attribute, member_id, expected_kind in cases:
            with self.subTest(container_id=container_id):
                registry_variants = (
                    (
                        {"entity_id": container_id, "platform": "group"},
                        {
                            "entity_id": container_id,
                            "platform": "synthetic",
                        },
                    ),
                    (
                        {
                            "entity_id": container_id,
                            "platform": " Group ",
                        },
                    ),
                )
                for container_entries in registry_variants:
                    snapshot = _build_expand_snapshot_evidence(
                        states=[
                            {
                                "entity_id": container_id,
                                "state": "on",
                                "attributes": {attribute: [member_id]},
                            },
                            {
                                "entity_id": member_id,
                                "state": "on",
                                "attributes": {},
                            },
                        ],
                        entity_registry=[
                            *container_entries,
                            {
                                "entity_id": member_id,
                                "platform": member_id.split(".", 1)[0],
                            },
                        ],
                        entity_registry_complete=True,
                    )
                    evidence = snapshot.entities[container_id]
                    self.assertEqual(expected_kind, evidence.expandable_kind)
                    self.assertIsNone(evidence.source_domain)
                    self.assertEqual(
                        (member_id,), evidence.member_entity_ids
                    )
                    self.assertTrue(evidence.membership_complete)
                    self.assertIsNone(evidence.failure_reason)

    def test_duplicate_valid_members_are_semantically_idempotent(self):
        member_id = "light.synthetic_member"

        def project(raw_members: list[str]):
            return _build_expand_snapshot_evidence(
                states=[
                    {
                        "entity_id": "group.synthetic_container",
                        "state": "on",
                        "attributes": {"entity_id": raw_members},
                    },
                    {
                        "entity_id": member_id,
                        "state": "on",
                        "attributes": {},
                    },
                ],
                entity_registry=[
                    {"entity_id": member_id, "platform": "light"}
                ],
                entity_registry_complete=True,
            ).entities["group.synthetic_container"]

        single = project([member_id])
        duplicate = project([member_id, member_id])
        self.assertTrue(single.membership_complete)
        self.assertTrue(duplicate.membership_complete)
        self.assertEqual((member_id,), duplicate.member_entity_ids)
        self.assertEqual(1, duplicate.membership_count)
        self.assertEqual(
            single.membership_fingerprint,
            duplicate.membership_fingerprint,
        )

    async def test_conflicted_domain_group_is_incomplete_and_locked(self):
        rest = SyntheticBeta49Rest(
            extra_states=(
                {
                    "entity_id": "light.synthetic_conflicted_group",
                    "state": "on",
                    "attributes": {"entity_id": [STANDARD_TARGET]},
                },
            )
        )
        rest.configs = {
            "conflicted_expand": {
                "alias": "Synthetic conflicted finite expand",
                "trigger": [],
                "condition": [
                    _condition(
                        "{{ expand(['light.synthetic_conflicted_group']) "
                        "| map(attribute='state') | list }}"
                    )
                ],
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                ],
            }
        }
        index = DependencyIndex(
            DirectHaDependencyProvider(
                rest,
                SyntheticBeta49WebSocket(
                    member_entities=(),
                    extra_registry_entries=(
                        {
                            "entity_id": "light.synthetic_conflicted_group",
                            "labels": [],
                            "platform": "group",
                        },
                        {
                            "entity_id": "light.synthetic_conflicted_group",
                            "labels": [],
                            "platform": "light",
                        },
                        {
                            "entity_id": STANDARD_TARGET,
                            "labels": [],
                            "platform": "input_boolean",
                        },
                    ),
                ),
            )
        )
        await index.get(refresh=True)
        risk_service = HelperDependencyRiskService(index)
        evidence = await risk_service.assess(STANDARD_TARGET, refresh=False)
        binding = evidence["binding"]
        risk = helper_dependency_risk_assessment(evidence)
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertGreater(
            binding["opaque_obligation_count"]
            + binding["coverage_failure_count"],
            0,
        )
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertFalse(risk.apply_allowed)
        self.assertTrue(
            binding["dependency_lock_projection"][
                "conservative_helper_dependency"
            ]
        )
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=STANDARD_TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": binding},
        )
        lock_keys = {
            request.key
            for request in OperationalLockSetCalculator().calculate(operation)
        }
        self.assertIn(unconstrained_helper_dependency_lock_key(), lock_keys)

        with tempfile.TemporaryDirectory() as root:
            helper = FakeHelperStateGateway()
            helper.entity_id = STANDARD_TARGET
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(root) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=risk_service.assess,
            )
            created = await governance.create_helper_state_plan(
                entity_id=STANDARD_TARGET,
                desired_state="on",
            )
            self.assertFalse(created["provider_dispatch_occurred"])
            self.assertFalse(created["plan"]["approval_actionable"])
            self.assertEqual(0, helper.dispatch_count)

    async def test_malformed_source_is_deterministically_incomplete_and_locked(
        self,
    ):
        rest = SyntheticBeta49Rest(
            extra_states=(
                {
                    "entity_id": "light.synthetic_malformed_group",
                    "state": "on",
                    "attributes": {"entity_id": [STANDARD_TARGET]},
                },
            )
        )
        rest.configs = {
            "malformed_expand": {
                "alias": "Synthetic malformed source expand",
                "trigger": [],
                "condition": [
                    _condition(
                        "{{ expand(['light.synthetic_malformed_group']) "
                        "| map(attribute='state') | list }}"
                    )
                ],
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.synthetic_garage"},
                    }
                ],
            }
        }
        index = DependencyIndex(
            DirectHaDependencyProvider(
                rest,
                SyntheticBeta49WebSocket(
                    member_entities=(),
                    extra_registry_entries=(
                        {
                            "entity_id": "light.synthetic_malformed_group",
                            "labels": [],
                            "platform": " group ",
                        },
                        {
                            "entity_id": STANDARD_TARGET,
                            "labels": [],
                            "platform": "input_boolean",
                        },
                    ),
                ),
            )
        )
        await index.get(refresh=True)
        risk_service = HelperDependencyRiskService(index)
        first = await risk_service.assess(STANDARD_TARGET, refresh=False)
        second = await risk_service.assess(STANDARD_TARGET, refresh=False)
        binding = first["binding"]
        risk = helper_dependency_risk_assessment(first)
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertGreater(
            binding["opaque_obligation_count"]
            + binding["coverage_failure_count"],
            0,
        )
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertFalse(risk.apply_allowed)
        self.assertEqual(
            binding["evidence_fingerprint"],
            second["binding"]["evidence_fingerprint"],
        )
        self.assertTrue(
            binding["dependency_lock_projection"][
                "conservative_helper_dependency"
            ]
        )
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=STANDARD_TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": binding},
        )
        lock_keys = {
            request.key
            for request in OperationalLockSetCalculator().calculate(operation)
        }
        self.assertIn(unconstrained_helper_dependency_lock_key(), lock_keys)

        with tempfile.TemporaryDirectory() as root:
            helper = FakeHelperStateGateway()
            helper.entity_id = STANDARD_TARGET
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(root) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=risk_service.assess,
            )
            created = await governance.create_helper_state_plan(
                entity_id=STANDARD_TARGET,
                desired_state="on",
            )
            self.assertFalse(created["provider_dispatch_occurred"])
            self.assertFalse(created["plan"]["approval_actionable"])
            self.assertEqual(0, helper.dispatch_count)

    def test_recursive_expansion_depth_and_work_limits_remain_conservative(
        self,
    ):
        depth_states = []
        for index in range(35):
            member = (
                f"group.synthetic_depth_{index + 1}"
                if index < 34
                else "sensor.synthetic_depth_leaf"
            )
            depth_states.append(
                {
                    "entity_id": f"group.synthetic_depth_{index}",
                    "state": "on",
                    "attributes": {"entity_id": [member]},
                }
            )
        depth_states.append(
            {
                "entity_id": "sensor.synthetic_depth_leaf",
                "state": "on",
                "attributes": {},
            }
        )
        depth_evidence = _build_expand_snapshot_evidence(
            states=depth_states,
            entity_registry=[
                {
                    "entity_id": "sensor.synthetic_depth_leaf",
                    "platform": "sensor",
                }
            ],
            entity_registry_complete=True,
        )
        _leaves, material, complete, limit_exceeded = (
            _resolve_expand_candidates(
                ("group.synthetic_depth_0",), depth_evidence
            )
        )
        self.assertFalse(complete)
        self.assertTrue(limit_exceeded)
        self.assertIn(
            "expand_resolution_depth_limit_exceeded",
            material["failure_reasons"],
        )

        work_states = [
            {
                "entity_id": "group.synthetic_work_root",
                "state": "on",
                "attributes": {
                    "entity_id": [
                        f"group.synthetic_work_{index}"
                        for index in range(9)
                    ]
                },
            }
        ]
        work_registry = []
        for group_index in range(9):
            members = [
                f"sensor.synthetic_work_{group_index}_{member_index}"
                for member_index in range(128)
            ]
            work_states.append(
                {
                    "entity_id": f"group.synthetic_work_{group_index}",
                    "state": "on",
                    "attributes": {"entity_id": members},
                }
            )
            for member_id in members:
                work_states.append(
                    {
                        "entity_id": member_id,
                        "state": "on",
                        "attributes": {},
                    }
                )
                work_registry.append(
                    {"entity_id": member_id, "platform": "sensor"}
                )
        work_evidence = _build_expand_snapshot_evidence(
            states=work_states,
            entity_registry=work_registry,
            entity_registry_complete=True,
        )
        _leaves, material, complete, limit_exceeded = (
            _resolve_expand_candidates(
                ("group.synthetic_work_root",), work_evidence
            )
        )
        self.assertFalse(complete)
        self.assertTrue(limit_exceeded)
        self.assertIn(
            "expand_resolution_entity_limit_exceeded",
            material["failure_reasons"],
        )

    async def test_domain_group_and_zone_members_are_not_false_exclusions(self):
        cases = (
            (
                "light.synthetic_group",
                "light.synthetic_member",
                {"entity_id": ["light.synthetic_member"]},
                "group",
            ),
            (
                "zone.synthetic_zone",
                "person.synthetic_member",
                {"persons": ["person.synthetic_member"]},
                "zone",
            ),
        )
        for container_id, member_id, attributes, platform in cases:
            with self.subTest(container_id=container_id):
                index, _snapshot = await self._expanded_index(
                    label_entity_id=container_id,
                    label_platform=platform,
                    extra_states=(
                        {
                            "entity_id": container_id,
                            "state": "on",
                            "attributes": attributes,
                        },
                        {
                            "entity_id": member_id,
                            "state": "on",
                            "attributes": {},
                        },
                    ),
                    extra_registry_entries=(
                        {
                            "entity_id": member_id,
                            "labels": [],
                            "platform": member_id.split(".", 1)[0],
                        },
                    ),
                )
                analysis = await EntityDependencyAnalysisService(
                    index
                ).analyze(
                    entity_id=member_id,
                    detail_level="evidence",
                    include_indirect=True,
                    refresh_index=False,
                )
                self.assertGreater(
                    analysis.data["overview"]["direct_reference_count"],
                    0,
                    (container_id, member_id, platform),
                )
                evidence = await HelperDependencyRiskService(index).assess(
                    member_id, refresh=False
                )
                self.assertGreater(
                    evidence["binding"][
                        "exact_dependency_obligation_count"
                    ],
                    0,
                )

    async def test_nested_groups_resolve_and_cycles_remain_incomplete(self):
        index, _snapshot = await self._expanded_index(
            label_entity_id="group.synthetic_outer",
            extra_states=(
                {
                    "entity_id": "group.synthetic_outer",
                    "state": "on",
                    "attributes": {"entity_id": ["light.synthetic_inner"]},
                },
                {
                    "entity_id": "light.synthetic_inner",
                    "state": "on",
                    "attributes": {"entity_id": ["light.synthetic_leaf"]},
                },
                {
                    "entity_id": "light.synthetic_leaf",
                    "state": "on",
                    "attributes": {},
                },
            ),
            extra_registry_entries=(
                {
                    "entity_id": "light.synthetic_inner",
                    "labels": [],
                    "platform": "group",
                },
                {
                    "entity_id": "light.synthetic_leaf",
                    "labels": [],
                    "platform": "light",
                },
            ),
        )
        exact = await HelperDependencyRiskService(index).assess(
            "light.synthetic_leaf", refresh=False
        )
        self.assertGreater(
            exact["binding"]["exact_dependency_obligation_count"], 0
        )

        cycle_index, _snapshot = await self._expanded_index(
            label_entity_id="group.synthetic_cycle_a",
            extra_states=(
                {
                    "entity_id": "group.synthetic_cycle_a",
                    "state": "on",
                    "attributes": {
                        "entity_id": ["light.synthetic_cycle_b"]
                    },
                },
                {
                    "entity_id": "light.synthetic_cycle_b",
                    "state": "on",
                    "attributes": {
                        "entity_id": ["group.synthetic_cycle_a"]
                    },
                },
            ),
            extra_registry_entries=(
                {
                    "entity_id": "light.synthetic_cycle_b",
                    "labels": [],
                    "platform": "group",
                },
            ),
        )
        cycle = await HelperDependencyRiskService(cycle_index).assess(
            STANDARD_TARGET, refresh=False
        )
        self.assertGreater(cycle["binding"]["opaque_obligation_count"], 0)
        self.assertFalse(cycle["binding"]["evidence_complete"])
        self.assertFalse(cycle["binding"]["execution_eligible"])

    async def test_missing_malformed_and_overflow_membership_fail_closed(self):
        cases = (
            (
                "group.synthetic_missing",
                {"entity_id": ["input_boolean.synthetic_missing"]},
                (),
            ),
            (
                "group.synthetic_malformed",
                {"entity_id": "input_boolean.synthetic_malformed"},
                (),
            ),
            (
                "group.synthetic_overflow",
                {
                    "entity_id": [
                        f"input_boolean.synthetic_{index}"
                        for index in range(129)
                    ]
                },
                tuple(
                    {
                        "entity_id": f"input_boolean.synthetic_{index}",
                        "state": "off",
                        "attributes": {},
                    }
                    for index in range(129)
                ),
            ),
        )
        for group_id, attributes, members in cases:
            with self.subTest(group_id=group_id):
                extra_states = (
                    {
                        "entity_id": group_id,
                        "state": "on",
                        "attributes": attributes,
                    },
                    *members,
                )
                extra_registry_entries = tuple(
                    {
                        "entity_id": item["entity_id"],
                        "labels": [],
                        "platform": "input_boolean",
                    }
                    for item in members
                )
                index, first = await self._expanded_index(
                    label_entity_id=group_id,
                    extra_states=extra_states,
                    extra_registry_entries=extra_registry_entries,
                )
                binding = (
                    await HelperDependencyRiskService(index).assess(
                        STANDARD_TARGET, refresh=False
                    )
                )["binding"]
                self.assertFalse(binding["evidence_complete"])
                self.assertFalse(binding["execution_eligible"])
                self.assertTrue(
                    binding["dependency_lock_projection"][
                        "conservative_helper_dependency"
                    ]
                )
                _second_index, second = await self._expanded_index(
                    label_entity_id=group_id,
                    extra_states=extra_states,
                    extra_registry_entries=extra_registry_entries,
                )
                first_fingerprints = self._expand_resolution_fingerprints(
                    first
                )
                self.assertTrue(first_fingerprints)
                self.assertEqual(
                    first_fingerprints,
                    self._expand_resolution_fingerprints(second),
                )

        partial_index, _snapshot = await self._expanded_index(
            label_entity_id="group.synthetic_partial_source",
            extra_states=(
                {
                    "entity_id": "group.synthetic_partial_source",
                    "state": "on",
                    "attributes": {
                        "entity_id": ["sensor.synthetic_unregistered"]
                    },
                },
                {
                    "entity_id": "sensor.synthetic_unregistered",
                    "state": "on",
                    "attributes": {},
                },
            ),
            extra_registry_entries=(),
        )
        partial = (
            await HelperDependencyRiskService(partial_index).assess(
                STANDARD_TARGET, refresh=False
            )
        )["binding"]
        self.assertFalse(partial["evidence_complete"])
        self.assertFalse(partial["execution_eligible"])
        self.assertTrue(
            partial["dependency_lock_projection"][
                "conservative_helper_dependency"
            ]
        )

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

    async def test_expanded_membership_drift_changes_binding(self):
        async def binding(member_id: str) -> dict:
            member_states = (
                ()
                if member_id == STANDARD_TARGET
                else (
                    {
                        "entity_id": member_id,
                        "state": "off",
                        "attributes": {},
                    },
                )
            )
            index, _snapshot = await self._expanded_index(
                label_entity_id="group.synthetic_drift",
                extra_states=(
                    {
                        "entity_id": "group.synthetic_drift",
                        "state": "on",
                        "attributes": {"entity_id": [member_id]},
                    },
                    *member_states,
                ),
                extra_registry_entries=(
                    {
                        "entity_id": member_id,
                        "labels": [],
                        "platform": member_id.split(".", 1)[0],
                    },
                ),
            )
            return (
                await HelperDependencyRiskService(index).assess(
                    STANDARD_TARGET, refresh=False
                )
            )["binding"]

        excluded = await binding("sensor.synthetic_drift_member")
        included = await binding(STANDARD_TARGET)
        self.assertEqual(0, excluded["exact_dependency_obligation_count"])
        self.assertGreater(
            included["exact_dependency_obligation_count"], 0
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

    async def test_expanded_membership_drift_rejects_before_dispatch(self):
        group_state = {
            "entity_id": "group.synthetic_preflight",
            "state": "on",
            "attributes": {"entity_id": ["sensor.synthetic_alpha"]},
        }
        rest = SyntheticBeta49Rest(extra_states=(group_state,))
        websocket = SyntheticBeta49WebSocket(
            member_entities=("group.synthetic_preflight",),
            extra_registry_entries=(
                {
                    "entity_id": STANDARD_TARGET,
                    "labels": [],
                    "platform": "input_boolean",
                },
            ),
        )
        index = DependencyIndex(
            DirectHaDependencyProvider(rest, websocket, concurrency=4)
        )
        helper = FakeHelperStateGateway()
        helper.entity_id = STANDARD_TARGET
        root = Path(self.temp.name) / "preflight-drift"
        service = ChangeGovernanceService(
            ChangePlanRepository(root / "plans"),
            UnusedLegacyGateway(),
            AuditLogger(str(root / "audit.jsonl"), "beta49-drift-secret"),
            now=Clock(),
            helper_state_gateway=helper,
            helper_dependency_risk_reader=(
                HelperDependencyRiskService(index).assess
            ),
        )
        telemetry, context = begin_request("beta49-expand-drift")
        telemetry.caller_id = "mcp-requester"
        try:
            runtime = F3RuntimeIntegration(
                service=service,
                storage_root=str(root / "plans"),
                configuration_gateway=UnusedConfigurationGateway(),
                backup_gateway=None,
                lifecycle_gateway=None,
                helper_state_gateway=helper,
                provider_identity_reader=forbidden_upstream_identity,
                retention_days=90,
            )
            service.f3_runtime = runtime
            await runtime.recover_once("startup")
            created = await service.create_helper_state_plan(
                entity_id=STANDARD_TARGET,
                desired_state="on",
            )
            plan = created["plan"]
            self.assertTrue(plan["approval_actionable"])
            pending = service.approve(plan["plan_id"], plan["plan_hash"])
            _review, csrf = await service.issue_external_csrf(
                plan["plan_id"], pending["challenge_id"]
            )
            await service.decide_external_approval(
                plan_id=plan["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=plan["plan_hash"],
                approval_kind="apply",
                approval_action=pending["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal="home_assistant_admin_ingress:beta49",
            )
            group_state["attributes"]["entity_id"] = [STANDARD_TARGET]
            result = await service.apply(plan["plan_id"], plan["plan_hash"])
            self.assertEqual("failed_pre_dispatch", result["task_state"])
            self.assertFalse(result["provider_dispatch_occurred"])
            self.assertEqual(0, helper.dispatch_count)
        finally:
            end_request(context)

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
