"""HAMCP-274 input_select option relevance by evidence.

Live evidence: an input_select that no automation names was planned as
``prohibited`` / ``critical`` / ``safety_critical`` because global
dependency gaps (a lock graph over its resource limit, opaque obligations,
an unrelated malformed registry record, call_service filters for another
domain) were attributed to every helper.  The fixtures here are shaped like
that installation: dozens of template-heavy automations with broad triggers
that never name the helper, one unrelated malformed registry record, and
label selectors that do not include it.

Every service test drives the shipped governance service and F3 runtime with
the real input_select dependency reader (``HelperDependencyRiskService`` +
``build_input_select_dependency_binding``) over a real ``DependencyIndex``
built from synthetic automation configurations.  The reproduction tests use
only the public plan surface so the same test can be run against the parent
commit, where it fails with the live prohibited classification.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency import provider as dependency_provider  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3.contracts import LockMode  # noqa: E402
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    input_select_option as contract,
)
from ha_mcp_engineering.governance import (  # noqa: E402
    helper_dependency as helper_module,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
    helper_consumers_disclosed,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.input_select_dependency import (  # noqa: E402
    INPUT_SELECT_DEPENDENCY_MODEL,
    INPUT_SELECT_DEPENDENCY_MODEL_V1,
    build_input_select_dependency_binding,
)

from tests.hamcp145_input_select_fixtures import (  # noqa: E402
    ENTITY,
    OPTIONS,
    InputSelectServiceTestCase,
)


STALE = "stale_target_state"
PENDING = "pending_signed_registry_admission"
RESPONSE_LIMIT = 60_000
NOISE_AUTOMATIONS = 60
LABEL_AUTOMATIONS = 5
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]


# --------------------------------------------------------------------------
# Installation-shaped synthetic Home Assistant
# --------------------------------------------------------------------------


def installation_configs() -> dict[str, dict[str, Any]]:
    """Template-heavy automations with broad triggers; none names the helper."""

    configs: dict[str, dict[str, Any]] = {}
    for n in range(NOISE_AUTOMATIONS):
        configs[f"room_{n:02d}"] = {
            "alias": f"Synthetic room {n}",
            "triggers": [
                {"trigger": "state", "entity_id": f"sensor.room_{n}"},
                {"trigger": "event", "event_type": "call_service",
                 "event_data": {"domain": "light", "service": "turn_on"}},
            ],
            "conditions": [{
                "condition": "template",
                "value_template": (
                    "{{ states('sensor.room_%d') | float(0) > 3 "
                    "and now().hour > 6 }}" % n
                ),
            }],
            "actions": [{"action": "light.turn_on",
                         "target": {"entity_id": f"light.room_{n}"}}],
        }
    for n in range(LABEL_AUTOMATIONS):
        configs[f"kitchen_{n}"] = {
            "alias": f"Synthetic kitchen {n}",
            "triggers": [{"trigger": "time_pattern", "minutes": "/5"}],
            "conditions": [{
                "condition": "template",
                "value_template": (
                    "{{ label_entities('kitchen') | select('is_state', 'on') "
                    "| list | count > 0 }}"
                ),
            }],
            "actions": [{"action": "lock.lock",
                         "target": {"entity_id": f"lock.door_{n}"}}],
        }
    return configs


def installation_registry() -> list[Any]:
    return [
        {"entity_id": "switch.kitchen_kettle", "labels": ["kitchen"]},
        # One unrelated malformed record: labels must be a list.
        {"entity_id": "sensor.unrelated_bad", "labels": "kitchen"},
    ]


def consumer(alias: str, *, triggers=None, conditions=None, actions=None):
    return {
        "alias": alias,
        "triggers": triggers or [{"trigger": "time_pattern", "minutes": "/5"}],
        "conditions": conditions or [],
        "actions": actions or [{"action": "notify.notify",
                                "data": {"message": "synthetic"}}],
    }


def template(value: str) -> list[dict[str, Any]]:
    return [{"condition": "template", "value_template": value}]


class InstallationRest:
    def __init__(self, configs: dict[str, dict[str, Any]],
                 extra_states: list[dict[str, Any]]) -> None:
        self.configs = configs
        self.extra_states = extra_states

    async def request(self, method: str, path: str, **_: Any) -> Any:
        if method != "GET":
            raise AssertionError(f"unreviewed dependency request: {method} {path}")
        if path == "/config":
            return {"version": SUPPORTED_HA_VERSION}
        if path == "/states":
            states = [
                {"entity_id": ENTITY, "state": "Home",
                 "attributes": {"options": list(OPTIONS)}},
                {"entity_id": "sensor.which", "state": "house_mode",
                 "attributes": {}},
                *deepcopy(self.extra_states),
            ]
            states += [
                {"entity_id": f"automation.{key}", "state": "on",
                 "attributes": {"id": key, "friendly_name": value["alias"]}}
                for key, value in self.configs.items()
            ]
            return states
        prefix = "/config/automation/config/"
        if path.startswith(prefix):
            return deepcopy(self.configs[path.removeprefix(prefix)])
        raise AssertionError(f"unreviewed dependency request: {path}")


class InstallationWebSocket:
    def __init__(self, registry: list[Any], labels: list[dict[str, Any]]) -> None:
        self.registry = registry
        self.labels = labels

    async def command(self, payload: dict[str, Any]) -> Any:
        if payload == {"type": "config/entity_registry/list"}:
            return deepcopy(self.registry)
        if payload == {"type": "config/label_registry/list"}:
            return deepcopy(self.labels)
        raise AssertionError(f"unreviewed dependency command: {payload}")


class RealInputSelectReader:
    """The shipped reader composition over a real index of the fixture."""

    def __init__(self, configs, registry, labels, extra_states=()) -> None:
        self.rest = InstallationRest(configs, list(extra_states))
        self.ws = InstallationWebSocket(registry, labels)
        self.index = DependencyIndex(
            DirectHaDependencyProvider(self.rest, self.ws, concurrency=4)
        )
        self.service = HelperDependencyRiskService(
            self.index, binding_builder=build_input_select_dependency_binding
        )
        self.calls: list[bool] = []

    async def __call__(self, entity_id: str, *, refresh: bool = True,
                       fenced: bool = False) -> dict[str, Any]:
        self.calls.append(fenced)
        return await self.service.assess(
            entity_id, refresh=refresh, fenced=fenced
        )


class InstallationTestCase(InputSelectServiceTestCase):
    """The HAMCP-145 service harness with the real dependency reader."""

    async def install(self, *, extra=None, registry=None, labels=None,
                      extra_states=(), base_registry=True) -> RealInputSelectReader:
        configs = installation_configs()
        configs.update(extra or {})
        reader = RealInputSelectReader(
            configs,
            (installation_registry() if base_registry else [])
            + list(registry or []),
            [{"label_id": "kitchen", "name": "Kitchen"}, *(labels or [])],
            extra_states,
        )
        self.dependency = reader
        self.service.input_select_dependency_risk_reader = reader
        # The F3 preflight binds the reader when the runtime starts.
        await self.restart_runtime()
        return reader

    async def planned(self, option: str = "Away", **install: Any) -> dict[str, Any]:
        await self.install(**install)
        created = await self.create_select(option)
        self.assertEqual(created["status"], "awaiting_approval", created)
        return created

    @staticmethod
    def binding(plan: dict[str, Any]) -> dict[str, Any]:
        return plan["operational"]["baseline"]["dependency_risk"]

    @staticmethod
    def chars(value: Any) -> int:
        return len(json.dumps(value, indent=2, ensure_ascii=True, default=str))

    def stored_binding(self, plan: dict[str, Any]) -> dict[str, Any]:
        stored = self.service._load(plan["plan_id"])
        return stored.operational.baseline["dependency_risk"]

    def lock_modes(self, plan: dict[str, Any], *, readback: bool = False):
        stored = self.service._load(plan["plan_id"])
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_select_option",
            target=SimpleNamespace(target_id=ENTITY),
            authoritative_provider_slug=contract.PROVIDER,
            baseline=deepcopy(stored.operational.baseline),
        )
        calculator = OperationalLockSetCalculator()
        requests = (
            calculator.calculate_for_readback(operation)
            if readback else calculator.calculate(operation)
        )
        return {item.key: item.mode for item in requests}

    def assert_elevated_not_prohibited(
        self, plan: dict[str, Any], *, risk: str = "high"
    ) -> None:
        decision = plan["policy_decision"]
        self.assertEqual(decision["policy_class"], "elevated_admin", decision)
        self.assertEqual(plan["risk"]["level"], risk)
        self.assertTrue(plan["approval_actionable"])
        self.assertNotIn("technical_execution_authority_required",
                         decision["reason_codes"])
        binding = self.binding(plan)
        self.assertTrue(binding["execution_contract_complete"])
        self.assertEqual(binding["execution_block_reason_codes"], [])
        self.assertTrue(binding["execution_eligible"])

    def consumers(self, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            item["automation_id"]: item
            for item in self.binding(plan)["downstream_profiles"]
        }

    async def apply_verified(self, plan: dict[str, Any], option: str) -> dict[str, Any]:
        before = len(self.sent())
        result = await self.apply(plan)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.sent()[before:],
                         [contract.select_option_payload(ENTITY, option)],
                         "exactly one provider call")
        self.assertEqual(self.current(), option)
        self.assertIn("exact_readback_verified",
                      self.child_codes(result["task_id"]))
        return result


# --------------------------------------------------------------------------
# Reproduction (live evidence A)
# --------------------------------------------------------------------------


class ReproductionTests(InstallationTestCase):
    async def test_an_unreferenced_helper_in_the_installation_is_standard(self):
        created = await self.planned("Away")
        plan = created["plan"]
        decision = plan["policy_decision"]
        self.assertEqual(decision["policy_class"], "standard_admin", decision)
        self.assertEqual(decision["risk_delta"], "low")
        self.assertEqual(decision["physical_consequence"], "none")
        self.assertIn("exact_input_select_option_standard_policy",
                      decision["reason_codes"])
        self.assertEqual(plan["risk"]["level"], "low")
        self.assertTrue(plan["approval_actionable"])
        binding = self.binding(plan)
        self.assertEqual(binding["physical_consequence"], "none")
        self.assertEqual(binding["potential_physical_consequence"], "none")
        self.assertEqual(
            binding["relevant_consumer_counts_by_consequence"]["safety_critical"], 0
        )
        self.assertEqual(binding["downstream_profiles"], [])
        self.assertLess(self.chars(created), RESPONSE_LIMIT)
        self.assertEqual(self.sent(), [])

    async def test_the_installation_noise_is_decided_not_attributed(self):
        plan = (await self.planned("Away"))["plan"]
        binding = self.binding(plan)
        self.assertEqual(binding["model"], INPUT_SELECT_DEPENDENCY_MODEL)
        self.assertTrue(binding["execution_contract_complete"])
        self.assertEqual(binding["execution_block_reason_codes"], [])
        self.assertEqual(binding["physical_consequence"], "none")
        self.assertEqual(binding["relevant_consumer_count"], 0)
        self.assertEqual(binding["relevant_downstream_object_ids"], [])
        self.assertEqual(binding["opaque_obligation_count"], 0)
        self.assertEqual(binding["call_service_exclusions_retained"],
                         NOISE_AUTOMATIONS)
        self.assertEqual(binding["reprojected_call_service_obligations"], 0)
        self.assertGreater(binding["label_obligations_target_decided"], 0)
        self.assertFalse(
            binding["dependency_lock_projection"]["coarse_automation_fence"]
        )
        # The unrelated malformed record has no effect and names no entity.
        for diagnostic in binding["selector_authority_diagnostics"]:
            self.assertFalse(diagnostic["malformed_target_record"])
            self.assertEqual(diagnostic["malformed_record_entity_ids"], [])
        self.assertNotIn("entity_registry_malformed_relevant_record",
                         binding["consequence_uncertainty_reason_codes"])


# --------------------------------------------------------------------------
# Classification matrix
# --------------------------------------------------------------------------


class ClassificationMatrixTests(InstallationTestCase):
    async def test_literal_reference_is_elevated_and_named(self):
        plan = (await self.planned("Away", extra={"uses_mode": consumer(
            "Uses mode",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "Away"}],
            actions=[{"action": "light.turn_off",
                      "target": {"entity_id": "light.room_0"}}],
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.binding(plan)
        self.assertEqual(binding["relevant_downstream_object_ids"],
                         ["automation.uses_mode"])
        self.assertEqual(binding["relevant_consumer_counts_by_class"]["referenced"], 1)
        named = self.consumers(plan)["automation.uses_mode"]
        self.assertEqual(named["evidence_class"], "referenced")
        self.assertTrue(named["relationships"])
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "direct")

    async def test_label_target_including_the_helper_is_elevated_and_named(self):
        plan = (await self.planned(
            "Away",
            extra={"mode_label": consumer(
                "Mode label",
                conditions=template(
                    "{{ label_entities('modes') | select('is_state', 'Away') "
                    "| list | count > 0 }}"
                ),
            )},
            registry=[{"entity_id": ENTITY, "labels": ["modes"]}],
            labels=[{"label_id": "modes", "name": "Modes"}],
        ))["plan"]
        # A notify-only consumer has no physical consequence, yet it is a
        # bounded relevant consumer: elevated and named, never standard.
        self.assert_elevated_not_prohibited(plan, risk="medium")
        decision = plan["policy_decision"]
        self.assertEqual(decision["physical_consequence"], "none")
        self.assertEqual(decision["risk_delta"], "moderate")
        self.assertIn("relevant_helper_consumer_disclosed",
                      decision["reason_codes"])
        self.assertIn("automation.mode_label",
                      self.binding(plan)["relevant_downstream_object_ids"])
        self.assertIn("automation.mode_label", self.consumers(plan))

    async def test_complete_label_membership_including_the_helper_is_relevant(self):
        # With no malformed record anywhere the label evidence is complete,
        # and its membership alone decides relevance.
        plan = (await self.planned(
            "Away",
            extra={"mode_label": consumer(
                "Mode label",
                conditions=template("{{ label_entities('modes') | count > 0 }}"),
            )},
            registry=[{"entity_id": ENTITY, "labels": ["modes"]}],
            labels=[{"label_id": "modes", "name": "Modes"}],
            base_registry=False,
        ))["plan"]
        self.assert_elevated_not_prohibited(plan, risk="medium")
        binding = self.stored_binding(plan)
        self.assertIn("automation.mode_label",
                      binding["relevant_downstream_object_ids"])
        self.assertNotIn("automation.kitchen_0",
                         binding["relevant_downstream_object_ids"])
        diagnostics = binding["selector_authority_diagnostics"]
        self.assertTrue(diagnostics)
        for item in diagnostics:
            self.assertTrue(item["complete"])
            self.assertEqual(item["target_disposition"], "included")

    async def test_group_and_expand_membership_is_elevated_and_named(self):
        plan = (await self.planned(
            "Away",
            extra={"mode_group": consumer(
                "Mode group",
                conditions=template(
                    "{{ expand('group.modes') | selectattr('state', 'eq', 'Away') "
                    "| list | count > 0 }}"
                ),
            )},
            extra_states=[{"entity_id": "group.modes", "state": "on",
                           "attributes": {"entity_id": [ENTITY]}}],
        ))["plan"]
        self.assert_elevated_not_prohibited(plan)
        self.assertIn("automation.mode_group",
                      self.binding(plan)["relevant_downstream_object_ids"])
        self.assertIn("automation.mode_group", self.consumers(plan))

    async def test_area_iteration_is_elevated_and_named(self):
        plan = (await self.planned("Away", extra={"mode_area": consumer(
            "Mode area",
            conditions=template(
                "{{ area_entities('living_room') | select('is_state', 'Away') "
                "| list | count > 0 }}"
            ),
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        self.assertIn("automation.mode_area",
                      self.binding(plan)["relevant_downstream_object_ids"])

    async def test_domain_wide_iteration_is_elevated_and_named(self):
        plan = (await self.planned("Away", extra={"all_selects": consumer(
            "All selects",
            conditions=template(
                "{{ states.input_select | selectattr('state', 'eq', 'Away') "
                "| list | count > 0 }}"
            ),
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        self.assertIn("automation.all_selects",
                      self.binding(plan)["relevant_downstream_object_ids"])
        self.assertIn("automation.all_selects", self.consumers(plan))

    async def test_a_dynamic_identifier_is_elevated_with_disclosure_not_prohibited(self):
        plan = (await self.planned("Away", extra={"dynamic": consumer(
            "Dynamic",
            conditions=template(
                "{{ is_state('input_select.' ~ states('sensor.which'), 'Away') }}"
            ),
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.binding(plan)
        self.assertEqual(binding["relevant_downstream_object_ids"],
                         ["automation.dynamic"])
        self.assertEqual(binding["relevant_consumer_counts_by_class"]["opaque"], 1)
        self.assertIn("opaque_downstream_obligations",
                      binding["consequence_uncertainty_reason_codes"])
        self.assertEqual(self.consumers(plan)["automation.dynamic"]["evidence_class"],
                         "opaque")
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "unknown")

    async def test_template_heavy_automations_that_never_touch_it_are_not_relevant(self):
        extra = {
            f"heavy_{n}": consumer(
                f"Heavy {n}",
                triggers=[{"trigger": "state", "entity_id": "sensor.room_1"},
                          {"trigger": "template", "value_template":
                           "{{ states('sensor.room_2') | int(0) > 4 }}"}],
                conditions=template(
                    "{{ states.sensor | selectattr('state', 'eq', 'on') | list "
                    "| count > 1 and is_state('input_select.other', 'On') "
                    "and state_attr('sensor.room_3', 'unit') == 'C' }}"
                ),
            )
            for n in range(5)
        }
        plan = (await self.planned("Away", extra=extra))["plan"]
        self.assertEqual(plan["policy_decision"]["policy_class"], "standard_admin")
        self.assertEqual(self.binding(plan)["relevant_consumer_count"], 0)

    async def test_a_related_malformed_record_is_reported_and_elevated(self):
        plan = (await self.planned(
            "Away", registry=[{"entity_id": ENTITY, "labels": "kitchen"}],
        ))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        self.assertIn("entity_registry_malformed_relevant_record",
                      binding["consequence_uncertainty_reason_codes"])
        reported = [
            item for item in binding["selector_authority_diagnostics"]
            if item["malformed_target_record"]
        ]
        self.assertTrue(reported)
        for item in reported:
            self.assertEqual(item["malformed_record_entity_ids"], [ENTITY])
        # The label consumers are disclosed, each as opaque, never as
        # referenced, and the policy label stays unknown.
        self.assertEqual(
            sorted(binding["relevant_downstream_object_ids"]),
            [f"automation.kitchen_{n}" for n in range(LABEL_AUTOMATIONS)],
        )
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "unknown")

    async def test_a_malformed_record_naming_no_entity_leaves_labels_unresolved(self):
        # A record with no valid entity_id could be the target's own record,
        # so the kitchen label stays unresolved for this target.
        plan = (await self.planned(
            "Away", registry=[{"entity_id": "not valid", "labels": "kitchen"}],
        ))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        self.assertEqual(
            sorted(binding["relevant_downstream_object_ids"]),
            [f"automation.kitchen_{n}" for n in range(LABEL_AUTOMATIONS)],
        )
        self.assertTrue(any(
            item["malformed_unattributable_record_count"] > 0
            for item in binding["selector_authority_diagnostics"]
        ))
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "unknown")

    async def test_a_non_canonical_target_record_is_reported_by_entity_id(self):
        # A well-formed label list whose record cannot be canonicalized is
        # still a malformed record naming the target.
        plan = (await self.planned(
            "Away",
            extra={"mode_label": consumer(
                "Mode label",
                conditions=template("{{ label_entities('modes') | count > 0 }}"),
            )},
            registry=[{"entity_id": ENTITY, "labels": ["modes"],
                       "name": float("nan")}],
            labels=[{"label_id": "modes", "name": "Modes"}],
        ))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        reported = [
            item for item in binding["selector_authority_diagnostics"]
            if item["malformed_target_record"]
        ]
        self.assertTrue(reported)
        for item in reported:
            self.assertEqual(item["malformed_record_entity_ids"], [ENTITY])
        self.assertIn("automation.mode_label",
                      binding["relevant_downstream_object_ids"])


# --------------------------------------------------------------------------
# Execution uncertainty stays refused (fail closed)
# --------------------------------------------------------------------------


class ExecutionUncertaintyTests(InstallationTestCase):
    async def assert_refused_before_send(self, result, code):
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(code, self.child_codes(result["task_id"]))
        self.assertEqual(self.sent(), [])

    async def test_stale_state_is_refused_with_no_send(self):
        await self.install()
        plan = await self.approved_select("Away")
        self.select_rest.set_external(ENTITY, state="Night")
        await self.assert_refused_before_send(await self.apply(plan), STALE)

    async def test_ambiguous_or_invalid_targets_are_refused_before_planning(self):
        await self.install()
        for entity_id in ("input_select.*", "input_select.house_mode,input_select.other",
                          "input_boolean.house_mode", ""):
            with self.subTest(entity_id=entity_id):
                with self.assertRaises(GovernanceError):
                    await self.service.create_input_select_option_plan(
                        {"entity_id": entity_id, "option": "Away",
                         "reason": "Synthetic"}
                    )
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.sent(), [])

    async def test_an_unadmitted_provider_is_refused(self):
        await self.install()
        plan = await self.approved_select("Away")
        self.core.withdraw()
        with self.assertRaises(GovernanceError) as raised:
            await self.apply(plan)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(raised.exception.details["reason_code"], PENDING)
        self.assertEqual(self.sent(), [])

    async def test_unreadable_dependency_evidence_stays_prohibited(self):
        reader = await self.install()

        async def broken(*_: Any, **__: Any) -> Any:
            raise RuntimeError("synthetic index failure")

        reader.index.get = broken
        created = await self.create_select("Away")
        plan = created["plan"]
        self.assertEqual(plan["policy_decision"]["policy_class"], "prohibited")
        self.assertIn("technical_execution_authority_required",
                      plan["policy_decision"]["reason_codes"])
        self.assertFalse(plan["approval_actionable"])
        self.assertFalse(self.binding(plan)["execution_contract_complete"])
        self.assertEqual(self.sent(), [])

    async def test_dependency_drift_after_approval_is_refused(self):
        reader = await self.install()
        plan = await self.approved_select("Away")
        reader.rest.configs["late"] = consumer(
            "Late", triggers=[{"trigger": "state", "entity_id": ENTITY}],
        )
        await self.assert_refused_before_send(await self.apply(plan),
                                              "dependency_risk_drift")


# --------------------------------------------------------------------------
# Consequence labels are positive evidence only
# --------------------------------------------------------------------------


class ConsequenceLabelTests(InstallationTestCase):
    async def test_incomplete_evidence_alone_is_never_safety_critical(self):
        plan = (await self.planned("Away", extra={"dynamic_lock": consumer(
            "Dynamic lock",
            conditions=template(
                "{{ is_state('input_select.' ~ states('sensor.which'), 'Away') }}"
            ),
            actions=[{"action": "lock.lock",
                      "target": {"entity_id": "lock.front"}}],
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.binding(plan)
        self.assertEqual(binding["physical_consequence"], "unknown")
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "unknown")
        # The strongest consequence among in-scope consumers is disclosed.
        self.assertEqual(binding["potential_physical_consequence"],
                         "safety_critical")

    async def test_positive_evidence_is_still_safety_critical(self):
        plan = (await self.planned("Away", extra={"away_lock": consumer(
            "Away lock",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "Away"}],
            actions=[{"action": "lock.lock",
                      "target": {"entity_id": "lock.front"}}],
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        self.assertEqual(self.binding(plan)["physical_consequence"],
                         "safety_critical")
        self.assertEqual(plan["policy_decision"]["physical_consequence"],
                         "safety_critical")

    async def test_a_safety_critical_owner_authorized_change_still_executes(self):
        await self.install(extra={"away_lock": consumer(
            "Away lock",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "Away"}],
            actions=[{"action": "lock.lock",
                      "target": {"entity_id": "lock.front"}}],
        )})
        plan = await self.approved_select("Away")
        await self.apply_verified(plan, "Away")


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------


def over_budget_consumers(count: int = 60) -> dict[str, dict[str, Any]]:
    return {
        f"svc_{n:02d}": consumer(
            f"Service watch {n}",
            triggers=[{"trigger": "event", "event_type": "call_service",
                       "event_data": {"domain": "input_select",
                                      "service": "select_option"}}],
        )
        for n in range(count)
    }


class LockTests(InstallationTestCase):
    async def test_a_bounded_graph_locks_each_consumer_precisely(self):
        plan = (await self.planned("Away", extra={"uses_mode": consumer(
            "Uses mode",
            triggers=[{"trigger": "state", "entity_id": ENTITY}],
        )}))["plan"]
        modes = self.lock_modes(plan)
        self.assertEqual(modes["automation:uses_mode"], LockMode.SHARED)
        self.assertNotEqual(modes.get("reload:automation"), LockMode.EXCLUSIVE)
        self.assertNotIn("automation:room_00", modes)

    async def test_an_unreferenced_helper_takes_no_automation_locks(self):
        plan = (await self.planned("Away"))["plan"]
        modes = self.lock_modes(plan)
        self.assertFalse([key for key in modes if key.startswith("automation:")])
        self.assertNotEqual(modes.get("reload:automation"), LockMode.EXCLUSIVE)
        self.assertEqual(modes["input_select:" + ENTITY.split(".", 1)[1]]
                         if "input_select:" + ENTITY.split(".", 1)[1] in modes
                         else LockMode.EXCLUSIVE, LockMode.EXCLUSIVE)

    async def test_an_over_budget_graph_takes_the_coarse_automation_fence(self):
        plan = (await self.planned("Away", extra=over_budget_consumers()))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        projection = binding["dependency_lock_projection"]
        self.assertTrue(projection["coarse_automation_fence"])
        self.assertTrue(projection["coarse_automation_fence_reason_codes"])
        self.assertTrue(binding["relevant_consumers_truncated"])
        modes = self.lock_modes(plan)
        self.assertEqual(modes["reload:automation"], LockMode.EXCLUSIVE)

    async def test_a_consumer_without_lock_identity_takes_the_fence(self):
        # An automation id outside the lock-key grammar cannot be locked
        # precisely, so the plan takes the coarse fence instead of refusing.
        plan = (await self.planned("Away", extra={"Uses_Mode": consumer(
            "Upper",
            triggers=[{"trigger": "state", "entity_id": ENTITY}],
            actions=[{"action": "notify.notify", "data": {"message": "x"}}],
        )}))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        projection = binding["dependency_lock_projection"]
        self.assertTrue(projection["coarse_automation_fence"])
        # ``automation.Uses_Mode`` is not a valid state entity id, so the
        # state inventory cannot prove the helper is in no group (HAMCP-285
        # R1-H1): the plan also holds for unresolved group membership.
        self.assertEqual(projection["coarse_automation_fence_reason_codes"],
                         ["automation_lock_identity_unavailable",
                          "group_membership_unresolved"])
        self.assertEqual(
            binding["group_membership_hold"]["unresolved_reason_codes"],
            ["group_membership_state_inventory_incomplete"],
        )
        self.assertEqual(self.lock_modes(plan)["reload:automation"],
                         LockMode.EXCLUSIVE)

    async def test_an_unattributable_coverage_gap_scopes_all_under_the_fence(self):
        # An automation inventory overflow names no automation: every
        # inventoried automation is in scope and the plan takes the fence.
        with mock.patch.object(dependency_provider, "MAX_AUTOMATION_SOURCES", 10):
            plan = (await self.planned("Away"))["plan"]
        self.assert_elevated_not_prohibited(plan)
        binding = self.stored_binding(plan)
        self.assertEqual(binding["coverage_failure_attribution"],
                         "conservative_all")
        self.assertEqual(len(binding["relevant_downstream_object_ids"]), 10)
        projection = binding["dependency_lock_projection"]
        self.assertTrue(projection["coarse_automation_fence"])
        self.assertEqual(projection["coarse_automation_fence_reason_codes"],
                         ["unattributable_coverage_failure"])
        self.assertEqual(self.lock_modes(plan)["reload:automation"],
                         LockMode.EXCLUSIVE)
        self.assertEqual(plan["policy_decision"]["physical_consequence"], "unknown")

    async def test_an_over_budget_graph_executes_under_the_fence(self):
        await self.install(extra=over_budget_consumers())
        plan = await self.approved_select("Away")
        await self.apply_verified(plan, "Away")

    async def test_an_unstated_fence_is_invalid_lock_evidence(self):
        await self.install()
        plan = await self.approved_select("Away")
        stored = self.service._load(plan["plan_id"])
        baseline = deepcopy(stored.operational.baseline)
        del baseline["dependency_risk"]["dependency_lock_projection"][
            "coarse_automation_fence"]
        operation = SimpleNamespace(
            validate=lambda: None,
            operation="set_input_select_option",
            target=SimpleNamespace(target_id=ENTITY),
            authoritative_provider_slug=contract.PROVIDER,
            baseline=baseline,
        )
        with self.assertRaises(ValueError):
            OperationalLockSetCalculator().calculate(operation)

    async def test_no_lock_set_means_no_dispatch(self):
        await self.install()
        plan = await self.approved_select("Away")

        def refuse(*_: Any, **__: Any) -> Any:
            raise ValueError("helper dependency lock projection is invalid")

        with mock.patch.object(OperationalLockSetCalculator, "calculate", refuse):
            try:
                result = await self.apply(plan)
            except (GovernanceError, ValueError):
                result = None
        if result is not None:
            self.assertNotEqual(result["task_state"], "succeeded_verified", result)
            self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.current(), "Home")


# --------------------------------------------------------------------------
# Response size
# --------------------------------------------------------------------------


class ResponseSizeTests(InstallationTestCase):
    async def test_an_over_budget_plan_response_is_bounded_with_counts(self):
        created = await self.planned("Away", extra=over_budget_consumers(120))
        self.assertLess(self.chars(created), RESPONSE_LIMIT)
        plan = created["plan"]
        paging = plan["response_paging"]
        self.assertTrue(paging["response_compacted"])
        self.assertEqual(paging["paging_tool"], "get_change_plan")
        self.assertEqual(paging["plan_id"], plan["plan_id"])
        sections = paging["omitted_sections"]
        self.assertTrue(sections["downstream_profiles"]["truncated"])
        self.assertGreater(sections["downstream_profiles"]["total_count"], 0)
        self.assertEqual(sections["downstream_profiles"]["returned_count"], 0)
        # The plan hash still binds the complete stored evidence.
        stored = self.stored_binding(plan)
        self.assertEqual(len(stored["downstream_profiles"]),
                         sections["downstream_profiles"]["total_count"])
        self.assertEqual(plan["plan_hash"],
                         self.service.plan_hash(self.service._load(plan["plan_id"])))
        page = self.service.get_plan_observability(
            plan["plan_id"], detail_section="downstream_profiles",
        )
        self.assertLess(self.chars(page), RESPONSE_LIMIT)

    async def test_a_small_plan_response_is_not_compacted(self):
        created = await self.planned("Away")
        self.assertNotIn("response_paging", created["plan"])
        self.assertIn("downstream_profiles", self.binding(created["plan"]))


# --------------------------------------------------------------------------
# Superseded v1 evidence
# --------------------------------------------------------------------------


class SupersededModelTests(InstallationTestCase):
    async def test_a_v1_binding_requires_a_replan_and_never_dispatches(self):
        from ha_mcp_engineering.governance.input_select_dependency import (
            INPUT_SELECT_DEPENDENCY_MODEL_V1,
        )

        # Synthetic relabel of current evidence; the exact beta.58 records are
        # exercised by HistoricalBeta58RecordTests below.
        reader = await self.install()

        async def relabelled_v1(entity_id: str, **kwargs: Any) -> dict[str, Any]:
            evidence = await RealInputSelectReader.__call__(reader, entity_id, **kwargs)
            binding = evidence["binding"]
            binding["model"] = INPUT_SELECT_DEPENDENCY_MODEL_V1
            for key in ("coarse_automation_fence",
                        "coarse_automation_fence_reason_codes"):
                binding["dependency_lock_projection"].pop(key)
            return evidence

        self.service.input_select_dependency_risk_reader = relabelled_v1
        plan = (await self.create_select("Away"))["plan"]
        stored = self.service._load(plan["plan_id"])
        self.assertTrue(self.service._helper_dependency_replan_required(stored))
        self.assertFalse(plan["approval_actionable"])
        self.assertTrue(plan["helper_dependency_replan_required"])
        self.assertEqual(plan["next_required_operation"], "create_change_plan")
        # Recovery may reconstruct the shipped v1 graph; execution may not.
        self.assertIn("home_assistant:core", self.lock_modes(plan, readback=True))
        with self.assertRaises(ValueError):
            self.lock_modes(plan)
        with self.assertRaises(GovernanceError):
            await self.approve(plan)
        with self.assertRaises(GovernanceError):
            await self.apply(plan)
        self.assertEqual(self.sent(), [])


# --------------------------------------------------------------------------
# Exact beta.58 v1 records (provenance-backed, hash-bearing bytes untouched)
# --------------------------------------------------------------------------


BETA58_FIXTURES = ROOT / "tests" / "fixtures" / "hamcp274_beta58_input_select_v1"
BETA58_PROVENANCE = (
    ROOT / "tests" / "fixtures" / "hamcp274_beta58_input_select_v1_provenance.json"
)
BETA58_GENERATOR = (
    ROOT / "scripts" / "generate_hamcp274_beta58_input_select_v1_fixture.py"
)
BETA58_COMMIT = "d09fc8a6196593880832de2d77cc5ce143562891"
SUPERSEDED_REPLAN = {
    "reason": "helper_dependency_evidence_superseded_replan_required",
    "replan_required": True,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class HistoricalBeta58RecordTests(InputSelectServiceTestCase):
    """Records written by exact v2.4.1-beta.58 code (R1-M1)."""

    @staticmethod
    def provenance() -> dict[str, Any]:
        return json.loads(BETA58_PROVENANCE.read_text(encoding="utf-8"))

    def restore(self, kind: str) -> dict[str, Any]:
        record = self.provenance()["records"][kind]
        for relative, digest in record["files"].items():
            source = BETA58_FIXTURES / kind / relative
            self.assertEqual(_sha256(source), digest, relative)
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        return record

    def operation(self, plan_id: str) -> SimpleNamespace:
        stored = self.service._load(plan_id)
        return SimpleNamespace(
            validate=lambda: None,
            operation="set_input_select_option",
            target=SimpleNamespace(target_id=ENTITY),
            authoritative_provider_slug=contract.PROVIDER,
            baseline=deepcopy(stored.operational.baseline),
        )

    def test_provenance_binds_the_exact_beta58_writer(self):
        provenance = self.provenance()
        self.assertEqual(provenance["source"]["commit"], BETA58_COMMIT)
        self.assertEqual(provenance["source"]["tag"], "v2.4.1-beta.58")
        self.assertEqual(provenance["generator_sha256"], _sha256(BETA58_GENERATOR))
        self.assertEqual(set(provenance["records"]), {"pending", "approved"})
        for kind, record in provenance["records"].items():
            with self.subTest(kind=kind):
                self.assertEqual(_tree_hashes(BETA58_FIXTURES / kind),
                                 record["files"])
                self.assertEqual(record["dependency_model"],
                                 INPUT_SELECT_DEPENDENCY_MODEL_V1)
                self.assertEqual(record["provider_write_count"], 0)
        self.assertEqual(provenance["records"]["pending"]["approval_state"],
                         "required")
        self.assertEqual(provenance["records"]["approved"]["approval_state"],
                         "approved")

    async def test_the_pending_record_is_readable_and_requires_a_replan(self):
        record = self.restore("pending")
        public = self.service.get_plan_observability(record["plan_id"])
        self.assertTrue(public["helper_dependency_replan_required"])
        self.assertFalse(public["approval_actionable"])
        self.assertEqual(public["next_required_operation"], "create_change_plan")
        with self.assertRaises(GovernanceError) as raised:
            await self.approve(public)
        self.assertEqual(raised.exception.code,
                         ErrorCode.OPERATIONAL_VALIDATION_FAILED)
        self.assertEqual(raised.exception.details,
                         {"resource_id": record["plan_id"], **SUPERSEDED_REPLAN})
        self.assertEqual(self.sent(), [])

    async def test_the_approved_record_apply_returns_the_structured_replan(self):
        record = self.restore("approved")
        plan_id = record["plan_id"]
        public = self.service.get_plan_observability(plan_id)
        self.assertEqual(public["status"], "approved")
        self.assertTrue(public["helper_dependency_replan_required"])
        before = _tree_hashes(self.root)
        try:
            await self.service.apply(plan_id, record["plan_hash"])
        except Exception as exc:  # noqa: BLE001
            raised = exc
        else:
            raised = None
        # Any other outcome, an unhandled error included, fails here.
        self.assertIsInstance(raised, GovernanceError, repr(raised))
        self.assertEqual(raised.code, ErrorCode.OPERATIONAL_VALIDATION_FAILED)
        self.assertEqual(raised.details,
                         {"resource_id": plan_id, **SUPERSEDED_REPLAN})
        self.assertEqual(self.sent(), [], "zero provider calls")
        self.assertEqual(_tree_hashes(self.root), before,
                         "persisted files byte-unchanged")
        self.assertIsNone(self.service.task_repository.get_for_plan(plan_id))
        stored = self.service._load(plan_id)
        self.assertEqual(stored.approval.state.value, "approved",
                         "approval not consumed")
        self.assertEqual(self.runtime.readiness_state()["faults"], [])

    def test_the_recovery_lock_graph_equals_the_shipped_v1_graph(self):
        for kind in ("pending", "approved"):
            with self.subTest(kind=kind):
                record = self.restore(kind)
                operation = self.operation(record["plan_id"])
                calculator = OperationalLockSetCalculator()
                try:
                    graph = [
                        [item.key, item.mode.value]
                        for item in calculator.calculate_for_readback(operation)
                    ]
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"shipped v1 recovery graph not reconstructed: {exc!r}")
                self.assertEqual(graph, record["shipped_execution_lock_graph"])
                # Recovery reconstructs the shipped graph; fresh execution
                # stays closed to v1 evidence.
                with self.assertRaises(ValueError):
                    calculator.calculate(operation)


class HistoricalBeta58ExistingTaskTests(InputSelectServiceTestCase):
    """A task that beta.58 itself created for the approved v1 record (HAMCP-285).

    beta.58 executed v1 bindings, so the patch below rebuilds exactly that
    build's authority: no replan gate and the v1 graph as execution locks.
    """

    provenance = staticmethod(HistoricalBeta58RecordTests.provenance)
    restore = HistoricalBeta58RecordTests.restore

    async def beta58_task(self) -> tuple[dict[str, Any], Any]:
        record = self.restore("approved")
        locks = mock.patch.object(
            OperationalLockSetCalculator, "calculate",
            OperationalLockSetCalculator.calculate_for_readback,
        )
        gate = mock.patch.object(
            type(self.service), "_helper_dependency_replan_required",
            staticmethod(lambda _plan: False),
        )
        with locks, gate:
            plan = self.service._load(record["plan_id"])
            await self.runtime._initialize(plan, record["plan_hash"])
        task = self.service.task_repository.get_for_plan(record["plan_id"])
        self.assertIsNotNone(task)
        self.assertTrue(self.runtime._pre_intent_task(task))
        return record, task

    async def test_a_pre_intent_v1_task_is_refused_with_the_structured_replan(self):
        record, _task = await self.beta58_task()
        before = _tree_hashes(self.root)
        try:
            await self.service.apply(record["plan_id"], record["plan_hash"])
        except Exception as exc:  # noqa: BLE001
            raised = exc
        else:
            raised = None
        # beta.59 raised an unstructured lock-calculator ValueError here.
        self.assertIsInstance(raised, GovernanceError, repr(raised))
        self.assertEqual(raised.code, ErrorCode.OPERATIONAL_VALIDATION_FAILED)
        self.assertEqual(raised.details,
                         {"resource_id": record["plan_id"], **SUPERSEDED_REPLAN})
        self.assertEqual(self.sent(), [], "zero provider calls")
        self.assertEqual(_tree_hashes(self.root), before)

    async def test_a_pre_intent_v1_task_can_still_be_cancelled(self):
        _record, task = await self.beta58_task()
        try:
            result = await self.service.cancel_execution_task(task.task_id)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"cancel must not raise: {exc!r}")
        self.assertEqual(result["status"], "cancelled_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.sent(), [])


# --------------------------------------------------------------------------
# End to end (real governance, real F3, real reader)
# --------------------------------------------------------------------------


class EndToEndTests(InstallationTestCase):
    async def test_apply_restore_and_no_op_on_an_unreferenced_helper(self):
        await self.install()
        forward = await self.approved_select("Away")
        self.assertEqual(forward["policy_decision"]["policy_class"], "standard_admin")
        await self.apply_verified(forward, "Away")
        self.assertEqual(self.dependency.calls, [False, True],
                         "planning read unfenced, preflight read fenced")

        rollback = await self.service.rollback_change(forward["plan_id"])
        self.assertEqual(rollback["status"], "rollback_plan_created", rollback)
        reverse = rollback["plan"]
        self.assertEqual(reverse["policy_decision"]["policy_class"], "standard_admin")
        await self.approve(reverse)
        await self.apply_verified(reverse, "Home")
        self.assertEqual(len(self.sent()), 2)

        no_op = await self.create_select("Home")
        self.assertEqual(no_op["status"], "no_change")
        self.assertIs(no_op["plan_created"], False)
        self.assertIs(no_op["provider_dispatch_occurred"], False)
        self.assertEqual(len(self.sent()), 2)

    async def test_a_bounded_notify_only_consumer_change_executes_once(self):
        # HAMCP-285: the medium elevated family that policy issues for a
        # bounded, non-consequential consumer was approvable but always
        # refused at apply with policy_snapshot_mismatch.
        await self.install(extra={"mode_notice": consumer(
            "Mode notice",
            triggers=[{"trigger": "state", "entity_id": ENTITY, "to": "Away"}],
        )})
        plan = await self.approved_select("Away")
        decision = plan["policy_decision"]
        self.assertEqual(
            (decision["policy_class"], decision["risk_delta"],
             decision["physical_consequence"], plan["risk"]["level"]),
            ("elevated_admin", "moderate", "none", "medium"),
        )
        await self.apply_verified(plan, "Away")
        self.assertEqual(len(self.sent()), 1)

    async def test_an_opaque_consumer_change_executes_once_after_approval(self):
        await self.install(extra={"dynamic": consumer(
            "Dynamic",
            conditions=template(
                "{{ is_state('input_select.' ~ states('sensor.which'), 'Away') }}"
            ),
        )})
        plan = await self.approved_select("Away")
        self.assertEqual(plan["policy_decision"]["policy_class"], "elevated_admin")
        await self.apply_verified(plan, "Away")
        modes = self.lock_modes(plan)
        self.assertEqual(modes["automation:dynamic"], LockMode.SHARED)


class CoverageAttributionTests(unittest.TestCase):
    """A gap named only by a synthetic provider source is never attributed."""

    def attribution(self, source_id: str) -> Any:
        obligation = SimpleNamespace(source_type="automation", source_id=source_id)
        return helper_module._attributable_coverage_sources(
            SimpleNamespace(automation_read_failures=()),
            projected_pairs=[({"target_outcome": "coverage_failure"}, obligation)],
            automation_coverage=object(),
            automation_completeness="partial",
            automation_failures=1,
            blueprint_coverage=object(),
            blueprint_completeness="complete",
            blueprint_failures=0,
        )

    def test_a_named_automation_gap_is_attributed(self):
        self.assertEqual(self.attribution("kitchen_0"), {"kitchen_0"})

    def test_synthetic_provider_sources_are_unattributable(self):
        # Even when the counts reconcile, these sources stand for an
        # inventory overflow or an unknown pending write, not one automation.
        for source_id in ("dependency_provider",
                          "dependency_provider_pending_writes"):
            with self.subTest(source_id=source_id):
                self.assertIsNone(self.attribution(source_id))


class ConsumerDisclosureScopeTests(unittest.TestCase):
    """The rule-B elevation reads only the target-scoped v2 binding."""

    def binding(self, **changes: Any) -> dict[str, Any]:
        binding = {
            "relevance_model": "target_scoped_evidence",
            "relevant_consumer_count": 1,
            "execution_contract_complete": True,
            "consequence_evidence_complete": True,
            "physical_consequence": "none",
        }
        binding.update(changes)
        return {key: value for key, value in binding.items() if value is not None}

    def test_disclosed_only_for_target_scoped_positive_counts(self):
        self.assertTrue(helper_consumers_disclosed(self.binding()))
        for changes in (
            {"relevant_consumer_count": 0},
            {"relevant_consumer_count": True},
            {"relevant_consumer_count": "1"},
            {"relevant_consumer_count": None},
            {"relevance_model": None},
            {"relevance_model": "helper_wide"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(helper_consumers_disclosed(self.binding(**changes)))

    def test_risk_level_is_medium_only_for_disclosed_consumers(self):
        provenance = {"provider": "dependency_index", "completeness": "complete"}
        disclosed = helper_dependency_risk_assessment(
            {"binding": self.binding(), "provenance": provenance}
        )
        self.assertEqual(disclosed.level.value, "medium")
        for changes in ({"relevant_consumer_count": 0}, {"relevance_model": None}):
            with self.subTest(changes=changes):
                assessment = helper_dependency_risk_assessment(
                    {"binding": self.binding(**changes), "provenance": provenance}
                )
                self.assertEqual(assessment.level.value, "low")
        consequential = helper_dependency_risk_assessment(
            {
                "binding": self.binding(physical_consequence="direct"),
                "provenance": provenance,
            }
        )
        self.assertEqual(consequential.level.value, "high")


if __name__ == "__main__":
    unittest.main()
