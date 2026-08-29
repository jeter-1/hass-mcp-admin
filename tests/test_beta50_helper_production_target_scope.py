"""Production-path helper target-scope regression coverage.

The original aggregate fixture is a synthetic reconstruction of the bounded
classes observed in deployed Beta 49.  The HAMCP-089 replay fixture is the
later, source/dataflow-preserving sanitized capture from deployed Beta 50.  It
contains only pseudonymized configurations and bounded membership evidence;
its provenance explicitly does not claim current-source/plan-snapshot byte
equivalence.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.obligation_ledger import (
    MAX_TEMPLATE_OBLIGATIONS,
    TemplateContextEvidence,
    TemplateObligationAnalyzer,
)
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
from ha_mcp_engineering.dependency.semantic_registry import (
    supported_home_assistant_versions,
)
from ha_mcp_engineering.f3.operational_locks import (
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict
from ha_mcp_engineering.f3.models import LockOwner, LockTiming
from ha_mcp_engineering.f3_configuration.locks import (
    operation_lock_requests,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance.helper_dependency import (
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
    HelperDependencyRiskService,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.service import ChangeGovernanceService
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from tests.test_beta37_exact_helper_state import (
    Clock,
    FakeHelperStateGateway,
    UnusedLegacyGateway,
)
from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for as configuration_adapter_for,
    proposal_for as configuration_proposal_for,
    valid_config as configuration_valid_config,
)
from tests.test_beta49_helper_obligation_target_scope import (
    SyntheticBeta49Rest,
    SyntheticBeta49WebSocket,
)


STANDARD_TARGET = "input_boolean.beta50_standard"
CONSEQUENTIAL_TARGET = "input_boolean.beta50_consequential"
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]
RELEASED_BETA49_RESIDUAL_COUNT = 59
RELEASED_BETA49_PROFILE_COUNT = 6
CAPTURED_REPLAY_FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "dependency"
    / "hamcp089_beta50_standard_helper_replay_v1.json"
)


_REPLAY_ENTITY_ID = re.compile(
    r"(?<![A-Za-z0-9_.])([a-z0-9_]+\.[a-z0-9_]+)"
    r"(?![A-Za-z0-9_.])"
)
_NON_STATE_DOMAINS = {
    "logbook",
    "notify",
    "persistent_notification",
    "repeat",
    "states",
    "this",
    "trigger",
    "wait",
}


NUMERIC_TRIGGER_CONTEXT = (
    "{{ trigger.to_state.name }} "
    "{{ trigger.to_state.state }} "
    "{{ trigger.to_state.last_changed }} "
    "{{ trigger.to_state.context.user_id }} "
    "{{ (as_timestamp(now()) - "
    "as_timestamp(trigger.from_state.last_changed)) | int }}"
)
LABEL_STATE_LOOP = (
    "{% set ns = namespace(items=[]) %}"
    "{% for entity in label_entities('synthetic_reviewed_states') %}"
    "{% if states(entity) not in ['unknown', 'unavailable'] %}"
    "{% set ns.items = ns.items + [states[entity]] %}"
    "{% endif %}"
    "{% endfor %}"
    "{{ ns.items | map(attribute='last_changed') | list }}"
)
FINITE_STATE_FILTER = (
    "{% set items = [states.sensor.synthetic_alpha, "
    "states.binary_sensor.synthetic_beta] %}"
    "{{ items | selectattr('state', 'eq', 'on') "
    "| map(attribute='last_changed') | list }}"
)
LABEL_MEMBERSHIP_NAME = (
    "{% set labeled = label_entities('synthetic_reviewed_states') %}"
    "{% for entity in labeled %}"
    "{% if entity in labeled and states(entity) %}"
    "{{ entity_name(entity) }}"
    "{% endif %}"
    "{% endfor %}"
)
CLOSED_DOMAINS = (
    "{{ states.sensor | map(attribute='state') | list }} "
    "{{ states.binary_sensor "
    "| selectattr('state', 'eq', 'on') | list }}"
)
FINITE_SELECTOR_TRANSPORT = (
    "{% set ids = ['sensor.synthetic_alpha', "
    "'binary_sensor.synthetic_beta'] %}"
    "{% set selected = ids[0] if true else ids[1] %}"
    "{% for entity in ids %}"
    "{{ states(entity) }} {{ state_attr(entity, 'name') }} "
    "{{ is_state(entity, 'on') }}"
    "{% endfor %}"
    "{{ states(selected) }}"
)
EXPANDED_LABEL_TRANSPORT = (
    "{{ expand(label_entities('synthetic_reviewed_states')) "
    "| map(attribute='state') | list }}"
)


def _template_condition(value: str) -> dict:
    return {"condition": "template", "value_template": value}


def _residual_source(
    source_number: int,
    *,
    variables: dict[str, str] | None = None,
    conditions: list[dict] | None = None,
    triggers: list[dict] | None = None,
) -> dict:
    return {
        "alias": f"Synthetic Beta 50 residual source {source_number}",
        "triggers": triggers or [],
        "conditions": conditions or [],
        "variables": variables or {},
        "actions": [
            {
                "action": "cover.open_cover",
                "target": {"entity_id": f"cover.synthetic_{source_number}"},
            }
        ],
    }


class SyntheticBeta50Rest(SyntheticBeta49Rest):
    """Sanitized full-index fixture for the six residual source classes."""

    def __init__(
        self,
        *,
        arbitrary_only: bool = False,
        filter_without_attribute: str | None = None,
    ) -> None:
        super().__init__(arbitrary_only=False)
        if arbitrary_only:
            self.configs = {
                "arbitrary_selector": {
                    "alias": "Synthetic arbitrary selector",
                    "triggers": [],
                    "conditions": [
                        _template_condition("{{ states(caller_supplied) }}")
                    ],
                    "actions": [
                        {
                            "action": "cover.open_cover",
                            "target": {"entity_id": "cover.synthetic_arbitrary"},
                        }
                    ],
                }
            }
            return
        if filter_without_attribute is not None:
            self.configs = {
                "member_filter_no_attribute": _residual_source(
                    99,
                    variables={"rendered": filter_without_attribute},
                )
            }
            return

        guest_sources = {
            f"consequential_{index}": {
                "alias": f"Synthetic consequential source {index}",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": CONSEQUENTIAL_TARGET,
                    }
                ],
                "conditions": [],
                "actions": [
                    {
                        "action": "lock.unlock",
                        "target": {"entity_id": f"lock.synthetic_{index}"},
                    }
                ],
            }
            for index in range(7)
        }
        self.configs = {
            "residual_1": _residual_source(
                1,
                triggers=[
                    {
                        "trigger": "numeric_state",
                        "entity_id": "sensor.synthetic_alpha",
                        "above": 1,
                    }
                ],
                conditions=[_template_condition(NUMERIC_TRIGGER_CONTEXT)],
            ),
            "residual_2": _residual_source(
                2,
                variables={
                    f"label_loop_{index}": LABEL_STATE_LOOP
                    for index in range(3)
                },
            ),
            "residual_3": _residual_source(
                3,
                variables={
                    f"finite_filter_{index}": FINITE_STATE_FILTER
                    for index in range(5)
                },
            ),
            "residual_4": _residual_source(
                4,
                variables={
                    f"label_name_{index}": LABEL_MEMBERSHIP_NAME
                    for index in range(6)
                },
            ),
            "residual_5": _residual_source(
                5,
                variables={
                    "closed_domains": CLOSED_DOMAINS,
                    "finite_transport": FINITE_SELECTOR_TRANSPORT,
                    "label_loop": LABEL_STATE_LOOP,
                },
            ),
            "residual_6": _residual_source(
                6,
                variables={
                    "expanded_label": EXPANDED_LABEL_TRANSPORT,
                    **{
                        f"finite_filter_{index}": FINITE_STATE_FILTER
                        for index in range(4)
                    },
                },
            ),
            **guest_sources,
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
                    "state": "2",
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
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return self.configs[path.removeprefix(prefix)]
        raise AssertionError(path)


class Beta51StateConcatenationAccountingTests(unittest.TestCase):
    """One State evaluation contributes one relationship to concatenation."""

    @staticmethod
    def _analyze(
        source: str,
        *,
        context: TemplateContextEvidence | None = None,
    ):
        return TemplateObligationAnalyzer(
            source_type="automation",
            source_id="state_concatenation",
            config_path="$.condition[0].value_template",
            relation="condition",
            source_entity_id="automation.state_concatenation",
            source_name="Synthetic State concatenation",
            source_state="on",
            configuration_fingerprint="c" * 64,
            entity_id_validator=lambda value: bool(
                isinstance(value, str)
                and value.count(".") == 1
                and all(part for part in value.split("."))
            ),
            context=context,
        ).analyze(source)

    @staticmethod
    def _exact(result):
        return [
            item
            for item in result.obligations
            if item.outcome == "exact_dependency"
        ]

    def test_direct_state_concatenation_is_accounted_exactly_once(self):
        cases = (
            (
                '{{ states.sensor.foo ~ "" }}',
                "states_domain_object_entity_access",
            ),
            (
                '{{ "" ~ states.sensor.foo }}',
                "states_domain_object_entity_access",
            ),
            (
                '{{ states["sensor.foo"] ~ "" }}',
                "states_item_entity_access",
            ),
        )
        for source, reason in cases:
            with self.subTest(source=source):
                exact = self._exact(self._analyze(source))
                self.assertEqual(1, len(exact), exact)
                self.assertEqual(("sensor.foo",), exact[0].exact_entity_ids)
                self.assertEqual(reason, exact[0].reason_code)

    def test_fixed_trigger_state_concatenation_retains_each_read_once(self):
        result = self._analyze(
            "{{ trigger.from_state ~ '' }}"
            "{{ '' ~ trigger.to_state }}",
            context=TemplateContextEvidence(
                trigger_entity_ids=("person.synthetic",),
                trigger_from_state_entity_ids=("person.synthetic",),
                trigger_to_state_entity_ids=("person.synthetic",),
                provenance=("trigger:state",),
            ),
        )
        exact = self._exact(result)
        self.assertEqual(2, len(exact), exact)
        self.assertEqual(
            ["trigger_exact_configuration_provenance"] * 2,
            [item.reason_code for item in exact],
        )
        self.assertTrue(
            all(
                item.exact_entity_ids == ("person.synthetic",)
                for item in exact
            )
        )

    def test_unaccounted_state_operand_is_still_consumed_once(self):
        result = self._analyze(
            "{{ this ~ '' }}",
            context=TemplateContextEvidence(
                this_entity_id="automation.state_concatenation",
            ),
        )
        exact = self._exact(result)
        self.assertEqual(1, len(exact), exact)
        self.assertEqual(
            "state_value_rendered_by_concatenation",
            exact[0].reason_code,
        )
        self.assertEqual(
            ("automation.state_concatenation",),
            exact[0].exact_entity_ids,
        )

    def test_independent_state_reads_are_not_globally_deduplicated(self):
        result = self._analyze(
            "{{ states.sensor.foo ~ '' }}"
            "{{ states.sensor.foo ~ '' }}"
        )
        exact = self._exact(result)
        self.assertEqual(2, len(exact), exact)
        self.assertTrue(
            all(item.exact_entity_ids == ("sensor.foo",) for item in exact)
        )
        self.assertEqual(2, len({item.evidence_id for item in exact}))

    def test_mixed_accounted_and_pending_conditional_branches_emit_once(self):
        result = self._analyze(
            "{{ (states.sensor.foo if enabled else this) ~ '' }}",
            context=TemplateContextEvidence(
                this_entity_id="automation.state_concatenation",
            ),
        )
        exact = self._exact(result)
        self.assertEqual(2, len(exact), exact)
        by_entity = {
            entity_id: sum(
                entity_id in item.exact_entity_ids for item in exact
            )
            for entity_id in (
                "sensor.foo",
                "automation.state_concatenation",
            )
        }
        self.assertEqual(
            {
                "sensor.foo": 1,
                "automation.state_concatenation": 1,
            },
            by_entity,
        )

    def test_rendered_state_scalar_reuse_by_selectors_stays_conservative(self):
        selectors = (
            "states(states.sensor.foo ~ '')",
            "is_state(states.sensor.foo ~ '', 'on')",
            "state_attr(states.sensor.foo ~ '', 'name')",
            "states[states.sensor.foo ~ '']",
        )
        for selector in selectors:
            with self.subTest(selector=selector):
                result = self._analyze("{{ " + selector + " }}")
                exact = self._exact(result)
                opaque = [
                    item
                    for item in result.obligations
                    if item.outcome == "bounded_semantic_opaque"
                ]
                self.assertEqual(1, len(exact), exact)
                self.assertEqual(("sensor.foo",), exact[0].exact_entity_ids)
                self.assertTrue(opaque, result.obligations)
                self.assertTrue(
                    any(
                        item.lock_projection == "conservative"
                        for item in opaque
                    )
                )

    def test_state_concatenation_obligation_budget_is_proportional(self):
        below_count = (MAX_TEMPLATE_OBLIGATIONS - 2) // 2
        below_source = "".join(
            f"{{{{ states.sensor.item_{index:03d} ~ '' }}}}"
            for index in range(below_count)
        )
        below = self._analyze(below_source)
        self.assertFalse(below.coverage_failed)
        self.assertEqual(below_count, len(self._exact(below)))

        above_source = below_source + (
            f"{{{{ states.sensor.item_{below_count:03d} ~ '' }}}}"
        )
        above = self._analyze(above_source)
        self.assertTrue(above.coverage_failed)
        self.assertTrue(
            any(
                item.reason_code == "template_obligation_limit_exceeded"
                for item in above.obligations
            )
        )


class SyntheticBeta50WebSocket(SyntheticBeta49WebSocket):
    def __init__(self) -> None:
        super().__init__(
            member_entities=(
                "sensor.synthetic_alpha",
                "binary_sensor.synthetic_beta",
            )
        )

    async def command(self, payload: dict):
        if payload == {"type": "config/label_registry/list"}:
            return [
                {
                    "label_id": "synthetic_reviewed_states",
                    "name": "Synthetic Reviewed States",
                }
            ]
        return await super().command(payload)


def _replay_entity_ids(value) -> set[str]:
    """Collect only sanitized HA entity literals from one replay config."""

    found: set[str] = set()
    if isinstance(value, dict):
        for nested in value.values():
            found.update(_replay_entity_ids(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_replay_entity_ids(nested))
    elif isinstance(value, str):
        for candidate in _REPLAY_ENTITY_ID.findall(value):
            if candidate.partition(".")[0] not in _NON_STATE_DOMAINS:
                found.add(candidate)
    return found


class CapturedBeta50ReplayRest:
    """Production-path adapter over the sanitized read-only capture."""

    def __init__(self, fixture: dict, *, extra_configs=()) -> None:
        self.fixture = fixture
        self.configs = {
            item["configuration"]["id"]: copy.deepcopy(
                item["configuration"]
            )
            for item in fixture["configurations"]
        }
        for config in extra_configs:
            self.configs[config["id"]] = copy.deepcopy(config)
        ids: set[str] = set()
        for config in self.configs.values():
            ids.update(_replay_entity_ids(config))
        evidence = fixture["membership_evidence"]
        for label in evidence["labels"]:
            ids.update(label["members"])
        for group in evidence["groups"]:
            ids.add(group["group_id"])
            ids.update(group["members"])
        ids.add(evidence["target_helper"]["entity_id"])
        self.ids = ids
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, path: str):
        self.calls.append((method, path))
        if path == "/config":
            return {
                "version": self.fixture["provenance"][
                    "home_assistant_version"
                ]
            }
        if path == "/states":
            groups = {
                item["group_id"]: item["members"]
                for item in self.fixture["membership_evidence"]["groups"]
            }
            states = []
            for entity_id in sorted(self.ids):
                if entity_id.startswith("automation."):
                    continue
                attributes = {"friendly_name": entity_id}
                if entity_id in groups:
                    attributes["entity_id"] = list(groups[entity_id])
                states.append(
                    {
                        "entity_id": entity_id,
                        "state": "off",
                        "attributes": attributes,
                    }
                )
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
            return copy.deepcopy(
                self.configs[path.removeprefix(prefix)]
            )
        raise AssertionError((method, path))


class CapturedBeta50ReplayWebSocket:
    def __init__(self, fixture: dict, entity_ids: set[str]) -> None:
        self.fixture = fixture
        self.entity_ids = entity_ids
        self.calls: list[dict] = []

    async def command(self, payload: dict):
        self.calls.append(copy.deepcopy(payload))
        if payload == {"type": "config/label_registry/list"}:
            return [
                {"label_id": item["label_id"], "name": item["label_id"]}
                for item in self.fixture["membership_evidence"]["labels"]
            ]
        if payload == {"type": "config/entity_registry/list"}:
            memberships: dict[str, list[str]] = {}
            for label in self.fixture["membership_evidence"]["labels"]:
                for entity_id in label["members"]:
                    memberships.setdefault(entity_id, []).append(
                        label["label_id"]
                    )
            target = self.fixture["membership_evidence"]["target_helper"]
            memberships[target["entity_id"]] = list(target["labels"])
            return [
                {
                    "entity_id": entity_id,
                    "labels": sorted(memberships.get(entity_id, [])),
                    "platform": entity_id.partition(".")[0],
                }
                for entity_id in sorted(self.entity_ids)
            ]
        raise AssertionError(payload)


class Beta50ProductionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rest = SyntheticBeta50Rest()
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                self.rest,
                SyntheticBeta50WebSocket(),
                concurrency=4,
            )
        )
        self.snapshot, _rebuilt, _lookup_ms = await self.index.get(
            refresh=True
        )
        self.risk = HelperDependencyRiskService(self.index)

    def test_sanitized_deployed_beta49_aggregate_is_internally_consistent(self):
        fixture = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "beta50_deployed_beta49_residual_matrix_v1.json"
            ).read_text(encoding="utf-8")
        )
        obligation = fixture["obligation_collection"]
        self.assertEqual(59, obligation["total_count"])
        self.assertEqual(
            59, sum(obligation["reason_counts"].values())
        )
        self.assertEqual(
            59, sum(obligation["source_obligation_counts"].values())
        )
        self.assertEqual(
            {"target_capable": 59},
            obligation["target_selector_scope_counts"],
        )
        self.assertEqual(
            {"conservative": 59},
            obligation["lock_projection_counts"],
        )
        self.assertEqual(
            6, fixture["downstream_profile_collection"]["total_count"]
        )
        self.assertTrue(
            fixture["provenance"]["logical_results_byte_identical"]
        )
        self.assertFalse(fixture["provenance"]["raw_configuration_retained"])
        self.assertEqual(
            0, fixture["provenance"]["home_assistant_request_count"]
        )
        self.assertEqual(
            0, fixture["provenance"]["upstream_request_count"]
        )
        self.assertFalse(
            fixture["provenance"]
            ["record_level_obligation_evidence_retained"]
        )
        self.assertFalse(
            fixture["provenance"]["source_semantic_replay_retained"]
        )

    async def test_residual_producer_terminals_precede_helper_aggregation(self):
        residual = [
            item
            for item in self.snapshot.obligations
            if item.source_id.startswith("residual_")
            and item.target_selector_scope == "target_capable"
        ]
        self.assertEqual([], residual)

        standard_evidence = await self.risk.assess(
            STANDARD_TARGET, refresh=False
        )
        standard = standard_evidence["binding"]
        assessment = helper_dependency_risk_assessment(standard_evidence)
        self.assertEqual(0, standard["exact_dependency_obligation_count"])
        self.assertEqual(0, standard["opaque_obligation_count"])
        self.assertEqual([], standard["downstream_profiles"])
        self.assertTrue(standard["evidence_complete"])
        self.assertTrue(standard["execution_eligible"])
        self.assertEqual("none", standard["physical_consequence"])
        self.assertEqual("low", assessment.level.value)
        self.assertTrue(assessment.apply_allowed)

    async def test_consequential_and_arbitrary_controls_remain_proportional(self):
        consequential_evidence = await self.risk.assess(
            CONSEQUENTIAL_TARGET, refresh=False
        )
        consequential = consequential_evidence["binding"]
        assessment = helper_dependency_risk_assessment(
            consequential_evidence
        )
        self.assertEqual(
            7, consequential["exact_dependency_obligation_count"]
        )
        self.assertEqual(0, consequential["opaque_obligation_count"])
        self.assertEqual(7, len(consequential["downstream_profiles"]))
        self.assertEqual(
            "safety_critical", consequential["physical_consequence"]
        )
        self.assertTrue(consequential["evidence_complete"])
        self.assertTrue(consequential["execution_eligible"])
        self.assertEqual("high", assessment.level.value)
        self.assertTrue(assessment.apply_allowed)

        arbitrary_index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta50Rest(arbitrary_only=True),
                SyntheticBeta50WebSocket(),
            )
        )
        arbitrary = (await HelperDependencyRiskService(
            arbitrary_index
        ).assess(STANDARD_TARGET, refresh=True))["binding"]
        self.assertGreater(arbitrary["opaque_obligation_count"], 0)
        self.assertFalse(arbitrary["evidence_complete"])
        self.assertFalse(arbitrary["execution_eligible"])
        self.assertTrue(
            arbitrary["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        arbitrary_operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=STANDARD_TARGET),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": arbitrary},
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            {
                item.key
                for item in OperationalLockSetCalculator().calculate(
                    arbitrary_operation
                )
            },
        )


class Beta50CapturedProductionReplayTests(
    unittest.IsolatedAsyncioTestCase
):
    """Replay the exact sanitized source families captured from Beta 50."""

    async def asyncSetUp(self) -> None:
        self.fixture = json.loads(
            CAPTURED_REPLAY_FIXTURE.read_text(encoding="utf-8")
        )
        self.rest = CapturedBeta50ReplayRest(self.fixture)
        self.websocket = CapturedBeta50ReplayWebSocket(
            self.fixture, self.rest.ids
        )
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                self.rest,
                self.websocket,
                concurrency=4,
            )
        )
        self.snapshot, rebuilt, _lookup_ms = await self.index.get(
            refresh=True
        )
        self.assertTrue(rebuilt)
        self.target = self.fixture["target_entity_id"]

    def test_capture_provenance_and_sanitized_payload_are_bound(self):
        provenance = self.fixture["provenance"]
        canonical = lambda value: json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(
            provenance["configuration_set_sha256"],
            hashlib.sha256(
                canonical(self.fixture["configurations"])
            ).hexdigest(),
        )
        self.assertEqual(
            provenance["membership_evidence_sha256"],
            hashlib.sha256(
                canonical(self.fixture["membership_evidence"])
            ).hexdigest(),
        )
        self.assertTrue(provenance["sanitized_before_hashing"])
        self.assertEqual("READY_FOR_OFFLINE_REPLAY", provenance["capture_status"])
        self.assertFalse(provenance["snapshot_equivalence_proven"])

    async def test_standard_helper_closes_every_captured_source_before_risk_aggregation(
        self,
    ):
        target_capable = [
            item
            for item in self.snapshot.obligations
            if item.target_selector_scope == "target_capable"
        ]
        coverage_failures = [
            item
            for item in self.snapshot.obligations
            if item.coverage_failure_authority
        ]
        self.assertEqual([], target_capable)
        self.assertEqual([], coverage_failures)
        self.assertNotIn(
            self.target,
            {
                entity_id
                for item in self.snapshot.obligations
                for entity_id in item.exact_entity_ids
            },
        )
        fake_state_members = {
            "binary_sensor.entity_id",
            "binary_sensor.name",
            "binary_sensor.state",
            "sensor.entity_id",
            "sensor.name",
            "sensor.state",
        }
        self.assertTrue(
            fake_state_members.isdisjoint(
                {
                    entity_id
                    for item in self.snapshot.obligations
                    for entity_id in item.exact_entity_ids
                }
            )
        )

        evidence = await HelperDependencyRiskService(self.index).assess(
            self.target, refresh=False
        )
        binding = evidence["binding"]
        assessment = helper_dependency_risk_assessment(evidence)
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual(0, binding["opaque_obligation_count"])
        self.assertEqual([], binding["downstream_profiles"])
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["evidence_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("none", binding["physical_consequence"])
        self.assertEqual("low", assessment.level.value)
        self.assertTrue(assessment.apply_allowed)
        self.assertFalse(
            binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertEqual(
            [],
            binding["dependency_lock_projection"]
            ["automation_resource_ids"],
        )

    async def test_finite_label_and_cross_action_candidates_are_not_erased(
        self,
    ):
        label_target = "input_boolean.entity_001"
        finite_action_target = "sensor.entity_025"
        label_binding = (
            await HelperDependencyRiskService(self.index).assess(
                label_target, refresh=False
            )
        )["binding"]
        self.assertGreater(
            label_binding["exact_dependency_obligation_count"], 0
        )
        self.assertEqual(0, label_binding["opaque_obligation_count"])
        self.assertEqual(
            {"source_02", "source_03"},
            {
                item["automation_resource_id"]
                for item in label_binding["downstream_profiles"]
            },
        )

        finite_sources = {
            item.source_id
            for item in self.snapshot.obligations
            if finite_action_target in item.exact_entity_ids
        }
        self.assertIn("source_05", finite_sources)

    async def test_unbounded_selector_and_dynamic_concat_remain_conservative(
        self,
    ):
        arbitrary = {
            "id": "source_arbitrary",
            "alias": "automation.source_arbitrary",
            "triggers": [],
            "conditions": [
                {
                    "condition": "template",
                    "value_template": (
                        "{{ states('input_boolean.' ~ caller_supplied) }}"
                    ),
                }
            ],
            "actions": [
                {
                    "action": "persistent_notification.create",
                    "data": {"message": "arbitrary selector"},
                }
            ],
        }
        statement_prefixed = {
            "id": "source_statement_prefixed",
            "alias": "automation.source_statement_prefixed",
            "triggers": [],
            "conditions": [],
            "actions": [
                {
                    "variables": {
                        "selected": (
                            "{% set candidate = 'sensor.entity_001' %}"
                            "{{ candidate }}"
                        )
                    }
                },
                {
                    "condition": "template",
                    "value_template": "{{ states(selected) }}",
                },
            ],
        }
        incomplete_conditional = {
            "id": "source_incomplete_conditional",
            "alias": "automation.source_incomplete_conditional",
            "triggers": [],
            "conditions": [],
            "actions": [
                {
                    "variables": {
                        "selected": (
                            "{{ 'sensor.entity_001' if enabled "
                            "else caller_supplied }}"
                        )
                    }
                },
                {
                    "condition": "template",
                    "value_template": "{{ states(selected) }}",
                },
            ],
        }
        rest = CapturedBeta50ReplayRest(
            self.fixture,
            extra_configs=(
                arbitrary,
                statement_prefixed,
                incomplete_conditional,
            ),
        )
        index = DependencyIndex(
            DirectHaDependencyProvider(
                rest,
                CapturedBeta50ReplayWebSocket(self.fixture, rest.ids),
            )
        )
        evidence = await HelperDependencyRiskService(index).assess(
            self.target, refresh=True
        )
        binding = evidence["binding"]
        target_capable_sources = {
            item.source_id
            for item in index.snapshot.obligations
            if item.target_selector_scope == "target_capable"
        }
        self.assertTrue(
            {
                "source_statement_prefixed",
                "source_incomplete_conditional",
            }.issubset(target_capable_sources),
            target_capable_sources,
        )
        arbitrary_scopes = {
            item.target_selector_scope
            for item in index.snapshot.obligations
            if item.source_id == "source_arbitrary"
            and item.outcome != "proven_dependency_neutral"
        }
        self.assertIn("closed_entity_domains", arbitrary_scopes)
        self.assertGreater(binding["opaque_obligation_count"], 0)
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertTrue(
            binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )

    async def test_rendered_state_scalar_selector_reuse_blocks_planning(self):
        scalar_reuse = {
            "id": "source_scalar_reuse",
            "alias": "automation.source_scalar_reuse",
            "triggers": [],
            "conditions": [
                {
                    "condition": "template",
                    "value_template": (
                        "{% set rendered = "
                        "states.sensor.entity_001 ~ '' %}"
                        "{{ states(rendered) }}"
                    ),
                }
            ],
            "actions": [
                {
                    "action": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_scalar_reuse"},
                }
            ],
        }
        rest = CapturedBeta50ReplayRest(
            self.fixture,
            extra_configs=(scalar_reuse,),
        )
        index = DependencyIndex(
            DirectHaDependencyProvider(
                rest,
                CapturedBeta50ReplayWebSocket(self.fixture, rest.ids),
            )
        )
        snapshot, _rebuilt, _lookup_ms = await index.get(refresh=True)
        source_exact = [
            item
            for item in snapshot.obligations
            if item.source_id == "source_scalar_reuse"
            and item.outcome == "exact_dependency"
            and "sensor.entity_001" in item.exact_entity_ids
        ]
        self.assertEqual(1, len(source_exact), source_exact)

        with tempfile.TemporaryDirectory() as temporary:
            helper = FakeHelperStateGateway()
            helper.entity_id = self.target
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(temporary) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=(
                    HelperDependencyRiskService(index).assess
                ),
                plan_observability_cursor_key=b"scalar-reuse-key" * 2,
            )
            result = await governance.create_helper_state_plan(
                entity_id=self.target,
                desired_state="on",
            )

        plan = result["plan"]
        binding = plan["operational"]["baseline"]["dependency_risk"]
        self.assertGreater(binding["opaque_obligation_count"], 0)
        self.assertFalse(binding["evidence_complete"])
        self.assertFalse(binding["execution_eligible"])
        self.assertFalse(plan["approval_actionable"])
        self.assertTrue(
            binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertIn(
            "source_scalar_reuse",
            {
                item["automation_resource_id"]
                for item in binding["downstream_profiles"]
            },
        )
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(0, helper.dispatch_count)

    async def test_governance_plan_reuses_explicit_fresh_snapshot(self):
        initial_identity = (
            self.snapshot.generation,
            self.snapshot.fingerprint,
            self.snapshot.source_epoch,
        )
        calls_after_refresh = (
            len(self.rest.calls),
            len(self.websocket.calls),
        )
        with tempfile.TemporaryDirectory() as temporary:
            helper = FakeHelperStateGateway()
            helper.entity_id = self.target
            risk = HelperDependencyRiskService(self.index)
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(temporary) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=risk.assess,
                plan_observability_cursor_key=b"captured-replay-key" * 2,
            )
            result = await governance.create_helper_state_plan(
                entity_id=self.target,
                desired_state="on",
            )

        self.assertEqual(
            calls_after_refresh,
            (len(self.rest.calls), len(self.websocket.calls)),
        )
        self.assertFalse(result["provider_dispatch_occurred"])
        plan = result["plan"]
        binding = plan["operational"]["baseline"]["dependency_risk"]
        self.assertEqual(
            initial_identity,
            (
                binding["dependency_index_generation"],
                binding["dependency_index_fingerprint"],
                binding["dependency_index_source_epoch"],
            ),
        )
        self.assertTrue(plan["approval_actionable"])
        self.assertEqual("low", plan["risk"]["level"])
        self.assertEqual(
            "standard_admin",
            plan["policy_decision"]["policy_class"],
        )
        lock_keys = {
            item.key
            for item in Beta50PlanningPathTests._lock_requests(
                self.target, binding
            )
        }
        self.assertFalse(
            any(key.startswith("automation:") for key in lock_keys)
        )
        self.assertIn(f"helper_dependency:{self.target}", lock_keys)
        self.assertIn(
            unconstrained_helper_dependency_lock_key(), lock_keys
        )
        self.assertEqual(0, helper.dispatch_count)

    async def test_governance_plan_waits_for_refresh_when_shared_snapshot_is_stale(
        self,
    ):
        prior_generation = self.snapshot.generation
        self.snapshot.built_at_monotonic -= (
            self.index.soft_ttl_seconds + 1.0
        )
        with tempfile.TemporaryDirectory() as temporary:
            helper = FakeHelperStateGateway()
            helper.entity_id = self.target
            risk = HelperDependencyRiskService(self.index)
            governance = ChangeGovernanceService(
                ChangePlanRepository(Path(temporary) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=helper,
                helper_dependency_risk_reader=risk.assess,
                plan_observability_cursor_key=b"stale-replay-key" * 2,
            )
            result = await governance.create_helper_state_plan(
                entity_id=self.target,
                desired_state="on",
            )

        binding = result["plan"]["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertGreater(
            binding["dependency_index_generation"], prior_generation
        )
        self.assertEqual(
            "current",
            self.index.evidence_metadata(self.index.snapshot)["freshness"],
        )
        self.assertTrue(result["plan"]["approval_actionable"])
        self.assertEqual(0, helper.dispatch_count)

    async def test_risk_read_closes_current_identity_ttl_race(self):
        snapshot = self.snapshot

        class RacingIndex:
            def __init__(self):
                self.calls: list[bool] = []

            @staticmethod
            def active_identity():
                return {"current": True}

            async def get(self, *, refresh=False, min_source_epoch=None):
                self.calls.append(refresh)
                return snapshot, refresh, 1.0

            def evidence_metadata(self, _snapshot):
                return {
                    "freshness": (
                        "stale_within_hard_ttl"
                        if len(self.calls) == 1
                        else "current"
                    ),
                    "evidence_age_seconds": 0.0,
                }

        racing = RacingIndex()
        evidence = await HelperDependencyRiskService(racing).assess(
            self.target, refresh=True
        )
        self.assertEqual([False, True], racing.calls)
        self.assertEqual("current", evidence["provenance"]["freshness"])
        self.assertTrue(evidence["provenance"]["refreshed"])
        self.assertTrue(evidence["binding"]["execution_eligible"])


class Beta50PlanningPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta50Rest(),
                SyntheticBeta50WebSocket(),
                concurrency=4,
            )
        )
        self.temp = tempfile.TemporaryDirectory()
        self.helper = FakeHelperStateGateway()
        self.risk = HelperDependencyRiskService(self.index)
        self.governance = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            UnusedLegacyGateway(),
            now=Clock(),
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=self.risk.assess,
            plan_observability_cursor_key=b"beta50-cursor-key" * 2,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _lock_requests(target: str, binding: dict):
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_boolean_state",
            target=SimpleNamespace(target_id=target),
            authoritative_provider_slug="direct_home_assistant_state",
            baseline={"dependency_risk": binding},
        )
        return OperationalLockSetCalculator().calculate(operation)

    @classmethod
    def _lock_keys(cls, target: str, binding: dict) -> set[str]:
        return {
            item.key
            for item in cls._lock_requests(target, binding)
        }

    @staticmethod
    def _lock_owner(name: str) -> LockOwner:
        return LockOwner(
            owner_id=f"owner-{name}",
            task_id=f"task-{name}",
            plan_id=f"plan-{name}",
            operation_id=f"operation-{name}",
            attempt_id=f"attempt-{name}",
        )

    async def _create_plan(self, target: str) -> tuple[dict, dict]:
        self.helper.entity_id = target
        created = await self.governance.create_helper_state_plan(
            entity_id=target,
            desired_state="on",
        )
        self.assertFalse(created["provider_dispatch_occurred"])
        plan = created["plan"]
        return plan, self.governance.get_plan_observability(plan["plan_id"])

    async def _create_filter_plan(self, template: str) -> dict:
        index = DependencyIndex(
            DirectHaDependencyProvider(
                SyntheticBeta50Rest(filter_without_attribute=template),
                SyntheticBeta50WebSocket(),
            )
        )
        risk = HelperDependencyRiskService(index)
        governance = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "filter-plans"),
            UnusedLegacyGateway(),
            now=Clock(),
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=risk.assess,
            plan_observability_cursor_key=b"beta50-filter-key" * 2,
        )
        self.helper.entity_id = STANDARD_TARGET
        created = await governance.create_helper_state_plan(
            entity_id=STANDARD_TARGET,
            desired_state="on",
        )
        self.assertFalse(created["provider_dispatch_occurred"])
        return created["plan"]

    def _traverse(self, plan_id: str, section: str) -> tuple[list[dict], str]:
        items: list[dict] = []
        cursor = ""
        fingerprint = ""
        while True:
            observed = self.governance.get_plan_observability(
                plan_id,
                detail_section=section,
                cursor=cursor,
                page_size=2,
            )["detail"]
            items.extend(observed["items"])
            fingerprint = fingerprint or observed["full_set_fingerprint"]
            self.assertEqual(fingerprint, observed["full_set_fingerprint"])
            if not observed["has_more"]:
                self.assertIsNone(observed["next_cursor"])
                break
            cursor = observed["next_cursor"]
            self.assertTrue(cursor)
        return items, fingerprint

    async def test_plans_bind_fresh_identity_and_persist_terminal_evidence(self):
        self.snapshot, _rebuilt, _lookup_ms = await self.index.get(
            refresh=True
        )
        refreshed_identity = (
            self.snapshot.generation,
            self.snapshot.fingerprint,
            self.snapshot.source_epoch,
        )
        standard, observed_standard = await self._create_plan(STANDARD_TARGET)
        standard_binding = standard["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertEqual(
            "helper-dependency-risk-v10", standard_binding["model"]
        )
        self.assertTrue(standard["approval_actionable"])
        self.assertEqual("low", standard["risk"]["level"])
        self.assertEqual(
            "standard_admin",
            standard["policy_decision"]["policy_class"],
        )
        standard_keys = self._lock_keys(STANDARD_TARGET, standard_binding)
        self.assertFalse(
            any(key.startswith("automation:") for key in standard_keys)
        )
        self.assertIn(
            f"helper_dependency:{STANDARD_TARGET}", standard_keys
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(), standard_keys
        )

        first_identity = (
            observed_standard["canonical_summary"]
            ["dependency_index_generation"],
            observed_standard["canonical_summary"]
            ["dependency_index_fingerprint"],
            observed_standard["canonical_summary"]
            ["dependency_index_source_epoch"],
        )
        self.assertEqual(
            refreshed_identity,
            (
                standard_binding["dependency_index_generation"],
                standard_binding["dependency_index_fingerprint"],
                standard_binding["dependency_index_source_epoch"],
            ),
        )
        consequential, observed_consequential = await self._create_plan(
            CONSEQUENTIAL_TARGET
        )
        consequential_binding = consequential["operational"]["baseline"][
            "dependency_risk"
        ]
        second_identity = (
            observed_consequential["canonical_summary"]
            ["dependency_index_generation"],
            observed_consequential["canonical_summary"]
            ["dependency_index_fingerprint"],
            observed_consequential["canonical_summary"]
            ["dependency_index_source_epoch"],
        )
        self.assertEqual(
            refreshed_identity,
            (
                consequential_binding["dependency_index_generation"],
                consequential_binding["dependency_index_fingerprint"],
                consequential_binding["dependency_index_source_epoch"],
            ),
        )
        self.assertEqual(refreshed_identity, first_identity)
        self.assertEqual(refreshed_identity, second_identity)
        self.assertTrue(consequential["approval_actionable"])
        self.assertEqual("high", consequential["risk"]["level"])
        self.assertEqual(
            "elevated_admin",
            consequential["policy_decision"]["policy_class"],
        )
        self.assertEqual(
            7,
            len(
                {
                    key
                    for key in self._lock_keys(
                        CONSEQUENTIAL_TARGET, consequential_binding
                    )
                    if key.startswith("automation:")
                }
            ),
        )

        for plan, sections in (
            (standard, ("obligation_evidence",)),
            (consequential, ("obligation_evidence", "downstream_profiles")),
        ):
            for section in sections:
                self.assertEqual(
                    self._traverse(plan["plan_id"], section),
                    self._traverse(plan["plan_id"], section),
                )
        self.assertEqual(0, self.helper.dispatch_count)

    async def test_clean_helper_stability_fence_conflicts_with_unresolved_automation_both_orders(
        self,
    ):
        standard, _observed = await self._create_plan(STANDARD_TARGET)
        binding = standard["operational"]["baseline"]["dependency_risk"]
        self.assertFalse(
            binding["dependency_lock_projection"][
                "conservative_helper_dependency"
            ]
        )
        helper_locks = self._lock_requests(STANDARD_TARGET, binding)
        fence_key = unconstrained_helper_dependency_lock_key()
        self.assertEqual(
            "shared",
            next(item.mode.value for item in helper_locks if item.key == fence_key),
        )

        dynamic = configuration_valid_config("automation")
        dynamic["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ states(caller_supplied) }}",
            }
        ]
        timing = LockTiming(60, 10, 0)
        for action in ("create", "update"):
            current = (
                None
                if action == "create"
                else configuration_valid_config("automation")
            )
            gateway = SyntheticConfigurationGateway()
            if current is not None:
                gateway.states[("automation", "porch_light")] = current
            adapter = configuration_adapter_for("automation", action, gateway)
            prepared = await adapter.prepare(
                configuration_proposal_for(
                    "automation",
                    action,
                    current_config=current,
                    proposed_config=dynamic,
                )
            )
            configuration_locks = operation_lock_requests(prepared)
            self.assertEqual(
                "exclusive",
                next(
                    item.mode.value
                    for item in configuration_locks
                    if item.key == fence_key
                ),
            )
            for first, second, names in (
                (
                    helper_locks,
                    configuration_locks,
                    ("helper", "configuration"),
                ),
                (
                    configuration_locks,
                    helper_locks,
                    ("configuration", "helper"),
                ),
            ):
                with self.subTest(action=action, first=names[0]):
                    with tempfile.TemporaryDirectory() as temporary:
                        store = DurableLockStore(temporary)
                        handle = store.acquire_once(
                            first,
                            owner=self._lock_owner(names[0]),
                            timing=timing,
                        )
                        with self.assertRaises(LockConflict) as caught:
                            store.acquire_once(
                                second,
                                owner=self._lock_owner(names[1]),
                                timing=timing,
                            )
                        self.assertEqual((fence_key,), caught.exception.keys)
                        store.release(handle)
            self.assertEqual(0, gateway.counters.dispatches)

    async def test_collection_filters_without_attribute_consume_state_scope(self):
        global_plan = await self._create_filter_plan(
            "{{ states | join(',') }}"
        )
        global_binding = global_plan["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertGreater(global_binding["opaque_obligation_count"], 0)
        self.assertEqual(1, len(global_binding["downstream_profiles"]))
        self.assertFalse(global_binding["evidence_complete"])
        self.assertFalse(global_binding["execution_eligible"])
        self.assertFalse(global_plan["approval_actionable"])
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            self._lock_keys(STANDARD_TARGET, global_binding),
        )

        excluded_plan = await self._create_filter_plan(
            "{{ states.sensor | join(',') }}"
        )
        excluded_binding = excluded_plan["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertEqual(0, excluded_binding["opaque_obligation_count"])
        self.assertEqual([], excluded_binding["downstream_profiles"])
        self.assertTrue(excluded_binding["evidence_complete"])
        self.assertTrue(excluded_binding["execution_eligible"])
        self.assertTrue(excluded_plan["approval_actionable"])
        self.assertFalse(
            excluded_binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            self._lock_keys(STANDARD_TARGET, excluded_binding),
        )

        exact_plan = await self._create_filter_plan(
            "{{ [states.input_boolean.beta50_standard] | join(',') }}"
        )
        exact_binding = exact_plan["operational"]["baseline"][
            "dependency_risk"
        ]
        self.assertGreater(
            exact_binding["exact_dependency_obligation_count"], 0
        )
        self.assertEqual(1, len(exact_binding["downstream_profiles"]))
        self.assertTrue(exact_binding["evidence_complete"])
        self.assertTrue(exact_binding["execution_eligible"])
        self.assertTrue(exact_plan["approval_actionable"])
        self.assertFalse(
            exact_binding["dependency_lock_projection"]
            ["conservative_helper_dependency"]
        )
        self.assertIn(
            unconstrained_helper_dependency_lock_key(),
            self._lock_keys(STANDARD_TARGET, exact_binding),
        )
        self.assertEqual(0, self.helper.dispatch_count)

    def test_v3_through_v9_are_readable_but_non_authoritative(self):
        self.assertEqual(
            "helper-dependency-risk-v10", HELPER_DEPENDENCY_RISK_MODEL
        )
        self.assertEqual(
            frozenset({"helper-dependency-risk-v10"}),
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        for version in range(3, 10):
            model = f"helper-dependency-risk-v{version}"
            self.assertIn(model, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS)
            self.assertNotIn(model, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS)


if __name__ == "__main__":
    unittest.main()
