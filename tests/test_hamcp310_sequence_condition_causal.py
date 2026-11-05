"""HAMCP-310: a condition step inside an action sequence is a causal read.

Home Assistant Core's ``ACTIONS_MAP`` (2026.7.2 through 2026.9.1) treats an
action step carrying ``condition``, ``and``, ``or`` or ``not`` as a
check-condition action that gates every later step. Its entity reads must be
causal helper evidence exactly like top-level, choose and if conditions, while
literal action targets and service data stay non-causal. All configurations,
transports and dependency evidence are synthetic and offline.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering import dependency as dependency_package  # noqa: E402
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document,
    extract_document_with_obligations,
    resolve_blueprint_roles,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyIndexSnapshot,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.f3_configuration import (  # noqa: E402
    locks as locks_module,
)
from ha_mcp_engineering.f3_configuration.locks import (  # noqa: E402
    helper_dependency_lock_key,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_dependency as helper_dependency_module,
)
from ha_mcp_engineering.governance.input_boolean_dependency import (  # noqa: E402
    INPUT_BOOLEAN_DEPENDENCY_MODEL,
    build_input_boolean_dependency_binding,
)
from ha_mcp_engineering.helper_value.contracts import digest  # noqa: E402
from ha_mcp_engineering.helper_value.safety import (  # noqa: E402
    assess,
    read_helper_dependency_evidence,
)
from ha_mcp_engineering.helper_value.service import (  # noqa: E402
    HelperCoreAuthority,
    HelperService,
    request_summary,
)
from ha_mcp_engineering.providers.upstream_helper_value import (  # noqa: E402
    HelperProvider,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests import test_typed_helper_value as typed  # noqa: E402


GUEST_MODE = "input_boolean.guest_mode"
LITERAL_HELPER = "input_boolean.synthetic"
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]
NON_CAUSAL = frozenset({"action_data", "action_target", "service_target"})
CONDITION_RELATIONS = frozenset(
    {"condition", "choose_condition", "if_condition", "repeat_condition"}
)


def _gate(entity_id: str = GUEST_MODE) -> dict:
    return {"condition": "state", "entity_id": entity_id, "state": "on"}


UNLOCK = {"action": "lock.unlock", "target": {"entity_id": "lock.front"}}

#: One condition step per action-sequence placement, including the shorthand
#: condition forms. The helper is read nowhere else in each configuration.
SEQUENCE_PLACEMENTS = {
    "top_level_actions": {"actions": [_gate(), UNLOCK]},
    "legacy_action_key": {"action": [_gate(), UNLOCK]},
    "choose_sequence": {
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [_gate("input_boolean.other")],
                        "sequence": [_gate(), UNLOCK],
                    }
                ]
            }
        ]
    },
    "choose_default": {
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [_gate("input_boolean.other")],
                        "sequence": [UNLOCK],
                    }
                ],
                "default": [_gate(), UNLOCK],
            }
        ]
    },
    "if_then": {
        "actions": [
            {
                "if": [_gate("input_boolean.other")],
                "then": [_gate(), UNLOCK],
            }
        ]
    },
    "if_else": {
        "actions": [
            {
                "if": [_gate("input_boolean.other")],
                "then": [UNLOCK],
                "else": [_gate(), UNLOCK],
            }
        ]
    },
    "parallel_branch_step": {"actions": [{"parallel": [_gate(), UNLOCK]}]},
    "parallel_nested_sequence": {
        "actions": [{"parallel": [{"sequence": [_gate(), UNLOCK]}]}]
    },
    "repeat_sequence": {
        "actions": [{"repeat": {"count": 2, "sequence": [_gate(), UNLOCK]}}]
    },
    "nested_sequence_action": {
        "actions": [{"sequence": [{"sequence": [_gate(), UNLOCK]}]}]
    },
    "shorthand_and": {"actions": [{"and": [_gate()]}, UNLOCK]},
    "shorthand_or": {"actions": [{"or": [_gate()]}, UNLOCK]},
    "shorthand_not": {"actions": [{"not": [_gate()]}, UNLOCK]},
    "shorthand_not_mapping": {"actions": [{"not": _gate()}, UNLOCK]},
    "deep_choose_if_parallel": {
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [],
                        "sequence": [
                            {
                                "if": [],
                                "then": [
                                    {"parallel": [{"and": [_gate()]}]}
                                ],
                            }
                        ],
                    }
                ]
            },
            UNLOCK,
        ]
    },
}


def _structured(config: dict, entity_id: str = GUEST_MODE, **kwargs):
    findings, _dynamic = extract_document(
        source_type=kwargs.get("source_type", "automation"),
        source_id="hamcp310",
        config=config,
    )
    _findings, _dynamic, obligations = extract_document_with_obligations(
        source_type=kwargs.get("source_type", "automation"),
        source_id="hamcp310",
        source_entity_id="automation.hamcp310",
        source_name="HAMCP-310 synthetic",
        source_state="on",
        config=config,
        secret="",
    )
    return (
        [item for item in findings if item.target_entity_id == entity_id],
        [
            item
            for item in obligations
            if item.obligation_kind == "structured_entity_reference"
            and entity_id in item.exact_entity_ids
        ],
    )


def _snapshot(result) -> DependencyIndexSnapshot:
    return DependencyIndexSnapshot(
        fingerprint="3" * 64,
        generation=310,
        built_at_monotonic=time.monotonic(),
        built_at="2026-11-05T12:00:00+00:00",
        findings=tuple(result.findings),
        dynamic_references=tuple(result.dynamic_references),
        target_metadata=result.target_metadata,
        coverage=tuple(result.coverage),
        automation_action_profiles=tuple(result.automation_action_profiles),
        automation_read_failures=tuple(result.automation_read_failures),
        obligations=tuple(result.obligations),
        obligation_ledger_model=result.obligation_ledger_model,
        label_memberships=result.label_memberships,
        label_membership_fingerprints=result.label_membership_fingerprints,
        label_membership_truncated=result.label_membership_truncated,
        label_registry_complete=result.label_registry_complete,
        home_assistant_version=result.home_assistant_version,
        home_assistant_version_status=result.home_assistant_version_status,
    )


def _binding(snapshot: DependencyIndexSnapshot, entity_id: str) -> dict:
    return build_input_boolean_dependency_binding(
        snapshot,
        entity_id=entity_id,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class SyntheticRest:
    """Guest-mode-shaped automations: one helper read only mid-sequence."""

    def __init__(self) -> None:
        self.configs = {
            # input_boolean.guest_mode is read ONLY by the condition step that
            # gates the unlock; the trigger watches an unrelated person.
            "guest_unlock": {
                "alias": "Unlock front door for guests",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": "person.synthetic_resident",
                        "to": "home",
                    }
                ],
                "conditions": [],
                "actions": [
                    _gate(),
                    {
                        "action": "lock.unlock",
                        "target": {"entity_id": "lock.synthetic_front"},
                    },
                ],
                "mode": "single",
            },
            # input_boolean.synthetic appears only as a literal action target
            # and in service data beside a safety-critical unlock.
            "literal_only": {
                "alias": "Literal helper target",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": "person.synthetic_resident",
                        "to": "not_home",
                    }
                ],
                "conditions": [],
                "actions": [
                    {
                        "action": "input_boolean.turn_on",
                        "target": {"entity_id": LITERAL_HELPER},
                    },
                    {
                        "action": "notify.mobile_app_synthetic",
                        "data": {
                            "message": "Literal helper changed",
                            "entity_id": LITERAL_HELPER,
                        },
                    },
                    {
                        "action": "lock.unlock",
                        "target": {"entity_id": "lock.synthetic_back"},
                    },
                ],
                "mode": "single",
            },
        }

    async def request(self, method, path):
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            states = [
                {"entity_id": GUEST_MODE, "state": "off", "attributes": {}},
                {"entity_id": LITERAL_HELPER, "state": "off", "attributes": {}},
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


class SyntheticWebSocket:
    async def command(self, payload):
        if payload in (
            {"type": "config/entity_registry/list"},
            {"type": "config/label_registry/list"},
        ):
            return []
        raise AssertionError(payload)


def _provider() -> DirectHaDependencyProvider:
    return DirectHaDependencyProvider(
        SyntheticRest(), SyntheticWebSocket(), concurrency=2
    )


class Hamcp310ExtractionRelationTests(unittest.TestCase):
    def test_every_sequence_condition_placement_is_a_causal_condition(self):
        for name, config in SEQUENCE_PLACEMENTS.items():
            with self.subTest(placement=name):
                findings, obligations = _structured(config)
                self.assertTrue(findings, name)
                self.assertTrue(obligations, name)
                for item in [*findings, *obligations]:
                    self.assertIn(item.relation, CONDITION_RELATIONS, name)
                    self.assertNotIn(item.relation, NON_CAUSAL, name)
                for item in obligations:
                    self.assertEqual("exact_dependency", item.outcome)

    def test_plain_sequence_step_uses_the_top_level_condition_relation(self):
        findings, obligations = _structured(
            SEQUENCE_PLACEMENTS["top_level_actions"]
        )
        self.assertEqual(
            [("condition", "$.actions[0].entity_id")],
            [(item.relation, item.config_path) for item in findings],
        )
        self.assertEqual(
            ["condition"], [item.relation for item in obligations]
        )

    def test_branch_sequence_steps_reuse_the_existing_branch_vocabulary(self):
        expected = {
            "choose_sequence": "choose_condition",
            # ``default`` already maps to the plain condition relation.
            "choose_default": "condition",
            "if_then": "condition",
            "if_else": "condition",
            "repeat_sequence": "repeat_condition",
        }
        for name, relation in expected.items():
            with self.subTest(placement=name):
                findings, obligations = _structured(SEQUENCE_PLACEMENTS[name])
                self.assertEqual(
                    {relation},
                    {item.relation for item in [*findings, *obligations]},
                )

    def test_script_branch_condition_steps_are_causal(self):
        config = {
            "sequence": [
                {"if": [_gate("input_boolean.other")], "then": [_gate()]},
                {"parallel": [{"or": [_gate()]}]},
            ]
        }
        findings, obligations = _structured(config, source_type="script")
        self.assertEqual(2, len(findings))
        for item in [*findings, *obligations]:
            self.assertNotIn(item.relation, NON_CAUSAL)

    def test_existing_condition_positions_are_unchanged(self):
        config = {
            "triggers": [{"trigger": "state", "entity_id": "person.x"}],
            "conditions": [_gate("input_boolean.top")],
            "actions": [
                {
                    "choose": [
                        {
                            "conditions": [_gate("input_boolean.chosen")],
                            "sequence": [UNLOCK],
                        }
                    ]
                },
                {"if": [_gate("input_boolean.iffed")], "then": [UNLOCK]},
                {
                    "repeat": {
                        "while": [_gate("input_boolean.looped")],
                        "sequence": [UNLOCK],
                    }
                },
                {
                    "condition": "and",
                    "conditions": [_gate("input_boolean.nested")],
                },
            ],
        }
        expected = {
            "input_boolean.top": "condition",
            "input_boolean.chosen": "choose_condition",
            "input_boolean.iffed": "if_condition",
            "input_boolean.looped": "repeat_condition",
            "input_boolean.nested": "condition",
        }
        for entity_id, relation in expected.items():
            with self.subTest(entity_id=entity_id):
                findings, obligations = _structured(config, entity_id)
                self.assertEqual(
                    {relation},
                    {item.relation for item in [*findings, *obligations]},
                )

    def test_literal_targets_and_service_data_stay_non_causal(self):
        config = {
            "actions": [
                # Explicit target and data, beside a gating condition step.
                _gate("input_boolean.other"),
                {
                    "action": "input_boolean.turn_on",
                    "target": {"entity_id": GUEST_MODE},
                },
                # Service data that happens to carry condition-shaped keys.
                {
                    "action": "script.synthetic",
                    "data": {
                        "condition": "state",
                        "and": [],
                        "entity_id": GUEST_MODE,
                    },
                },
                # Legacy step-level entity_id on a service call.
                {"service": "input_boolean.turn_off", "entity_id": GUEST_MODE},
                # Device action entity.
                {
                    "device_id": "synthetic_device",
                    "domain": "input_boolean",
                    "type": "turn_on",
                    "entity_id": GUEST_MODE,
                },
                # A condition-keyed mapping below a literal target.
                {
                    "action": "input_boolean.toggle",
                    "target": {"condition": "state", "entity_id": GUEST_MODE},
                },
            ]
        }
        findings, obligations = _structured(config)
        self.assertEqual(
            [
                ("service_target", "$.actions[1].target.entity_id"),
                ("action_data", "$.actions[2].data.entity_id"),
                ("action_target", "$.actions[3].entity_id"),
                ("action_target", "$.actions[4].entity_id"),
                ("service_target", "$.actions[5].target.entity_id"),
            ],
            sorted(
                ((item.relation, item.config_path) for item in findings),
                key=lambda value: value[1],
            ),
        )
        self.assertEqual(
            {"service_target", "action_data", "action_target"},
            {item.relation for item in obligations},
        )
        for item in obligations:
            self.assertEqual(
                "proven_dependency_neutral",
                helper_dependency_module._obligation_targets_helper(
                    item, GUEST_MODE
                ),
            )

    def test_blueprint_input_in_a_sequence_condition_resolves_causally(self):
        blueprint = {
            "actions": [
                {
                    "condition": "state",
                    "entity_id": {"__blueprint_input__": "gate"},
                    "state": "on",
                },
                {
                    "action": "lock.unlock",
                    "target": {
                        "entity_id": {"__blueprint_input__": "lock"}
                    },
                },
            ]
        }
        findings, _dynamic = extract_document(
            source_type="automation",
            source_id="hamcp310_blueprint",
            config={
                "use_blueprint": {
                    "path": "synthetic.yaml",
                    "input": {"gate": GUEST_MODE, "lock": "lock.front"},
                }
            },
        )
        resolved = resolve_blueprint_roles(
            findings, blueprint, source_id="hamcp310_blueprint"
        )
        roles = {
            item.target_entity_id: item.config_path for item in resolved
        }
        self.assertEqual(
            "use_blueprint.input.gate -> condition", roles[GUEST_MODE]
        )
        self.assertEqual(
            "use_blueprint.input.lock -> service_target", roles["lock.front"]
        )


class Hamcp310HelperProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.snapshot = _snapshot(await _provider().scan())

    def test_sequence_condition_helper_projects_exact_dependency(self):
        binding = _binding(self.snapshot, GUEST_MODE)
        self.assertEqual(INPUT_BOOLEAN_DEPENDENCY_MODEL, binding["model"])
        self.assertEqual(1, binding["exact_dependency_obligation_count"])
        self.assertEqual("safety_critical", binding["physical_consequence"])
        projected = [
            item
            for item in binding["obligation_evidence"]
            if item["configuration_path"] == "$.actions[0].entity_id"
        ]
        self.assertEqual(1, len(projected))
        self.assertEqual("condition", projected[0]["relation"])
        self.assertEqual("exact_dependency", projected[0]["target_outcome"])
        self.assertEqual(
            [
                (
                    "automation.guest_unlock",
                    "referenced",
                    "safety_critical",
                )
            ],
            [
                (
                    item["automation_id"],
                    item["evidence_class"],
                    item["physical_consequence"],
                )
                for item in binding["downstream_profiles"]
            ],
        )

    def test_literal_only_helper_stays_dependency_neutral(self):
        binding = _binding(self.snapshot, LITERAL_HELPER)
        self.assertEqual(0, binding["exact_dependency_obligation_count"])
        self.assertEqual([], binding["downstream_profiles"])
        self.assertEqual("none", binding["potential_physical_consequence"])
        self.assertNotEqual("safety_critical", binding["physical_consequence"])
        literal = [
            item
            for item in binding["obligation_evidence"]
            if LITERAL_HELPER in item["candidate_entity_ids"]
        ]
        for item in literal:
            self.assertIn(item["relation"], NON_CAUSAL)
            self.assertEqual(
                "proven_dependency_neutral", item["target_outcome"]
            )

    def test_causal_source_index_counts_the_sequence_condition(self):
        self.assertEqual(
            {"guest_unlock": {"condition"}},
            helper_dependency_module._causal_automation_sources(
                self.snapshot, GUEST_MODE
            ),
        )
        self.assertEqual(
            {},
            helper_dependency_module._causal_automation_sources(
                self.snapshot, LITERAL_HELPER
            ),
        )


class Hamcp310F3LockTests(unittest.TestCase):
    @staticmethod
    def _keys(config: dict) -> set[str]:
        operation = SimpleNamespace(
            resource_type="automation",
            current_config=lambda: None,
            proposed_config=lambda: config,
        )
        return {
            item.key
            for item in locks_module._automation_helper_dependency_locks(
                operation
            )
        }

    def test_sequence_condition_helpers_receive_exact_locks(self):
        for name, config in SEQUENCE_PLACEMENTS.items():
            with self.subTest(placement=name):
                self.assertIn(
                    helper_dependency_lock_key(GUEST_MODE), self._keys(config)
                )
        selector = {
            "actions": [
                {
                    "if": [],
                    "then": [_gate("input_select.house_mode"), UNLOCK],
                }
            ]
        }
        self.assertIn(
            helper_dependency_lock_key("input_select.house_mode"),
            self._keys(selector),
        )

    def test_literal_targets_and_data_receive_no_helper_lock(self):
        config = {
            "actions": [
                {
                    "action": "input_boolean.turn_on",
                    "target": {"entity_id": GUEST_MODE},
                },
                {
                    "action": "script.synthetic",
                    "data": {"condition": "state", "entity_id": GUEST_MODE},
                },
                {"service": "input_boolean.turn_off", "entity_id": GUEST_MODE},
            ]
        }
        keys = self._keys(config)
        self.assertNotIn(helper_dependency_lock_key(GUEST_MODE), keys)
        self.assertNotIn(unconstrained_helper_dependency_lock_key(), keys)


class Hamcp310ControlHelperTests(unittest.IsolatedAsyncioTestCase):
    """control_helper over the shipped evidence reader and a real index."""

    version = typed.HelperTests.version

    async def asyncSetUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock, self.core = typed.Clock(), typed.SignedCore()
        self.transport = typed.HelperTransport(self.version)
        self.provider = HelperProvider(
            self.transport, lambda *args: self.transport.authority
        )
        index = DependencyIndex(_provider())
        runtime = SimpleNamespace(
            require=lambda: SimpleNamespace(index=index)
        )
        patcher = patch.object(
            dependency_package, "DEPENDENCY_ANALYSIS", runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # The service's default reader is the shipped production reader.
        self.service = HelperService(
            self.tmp.name,
            self.provider,
            HelperCoreAuthority(self.core),
            now=self.clock,
            sleep=self.clock.sleep,
        )
        self.addAsyncCleanup(self.service.close)
        self.telemetry, token = begin_request("hamcp310-helper-request")
        self.addCleanup(end_request, token)

    request = typed.HelperTests.request

    async def call(self, request):
        self.telemetry.ordinary_helper_value_binding = digest(
            request.model_dump()
        )
        return await self.service.control(request)

    def dispatches(self):
        return [
            args
            for tool, args in self.transport.calls
            if tool == "ha_call_service"
        ]

    async def test_shipped_reader_sees_exact_sequence_condition_evidence(self):
        evidence = await read_helper_dependency_evidence(GUEST_MODE)
        self.assertTrue(evidence["available"], evidence)
        safety = assess(deepcopy(evidence))
        self.assertTrue(safety["safety_critical"])
        self.assertEqual(
            [
                {
                    "automation_id": "automation.guest_unlock",
                    "name": "Unlock front door for guests",
                    "physical_consequence": "safety_critical",
                }
            ],
            safety["consequential_automations"],
        )

    async def test_mid_sequence_condition_helper_requires_confirmation(self):
        request = self.request(GUEST_MODE, "turn_on")
        result = await self.call(request)
        self.assertEqual("confirmation_required", result["status"], result)
        self.assertEqual("no_dispatch", result["dispatch"])
        self.assertFalse(result["task_created"])
        self.assertEqual(
            ["automation.guest_unlock"],
            [
                item["automation_id"]
                for item in result["consequential_automations"]
            ],
        )
        self.assertEqual([], self.dispatches())
        self.assertEqual(0, self.transport.writes)
        self.assertEqual(
            "confirmation_required",
            request_summary(self.telemetry)["safety_gate"],
        )

        acknowledged = request.model_copy(
            update={"acknowledge_safety_critical": True}
        )
        result = await self.call(acknowledged)
        self.assertEqual("succeeded_verified", result["status"], result)
        self.assertTrue(result["safety"]["safety_critical"])
        self.assertEqual(1, self.transport.writes)

    async def test_literal_target_helper_is_not_safety_critical(self):
        result = await self.call(self.request(LITERAL_HELPER, "turn_on"))
        self.assertEqual("succeeded_verified", result["status"], result)
        self.assertFalse(result["safety"]["safety_critical"])
        self.assertEqual(
            "not_safety_critical",
            request_summary(self.telemetry)["safety_gate"],
        )
        self.assertEqual(1, self.transport.writes)


if __name__ == "__main__":
    unittest.main()
