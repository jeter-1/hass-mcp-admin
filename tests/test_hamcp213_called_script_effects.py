"""HAMCP-213 PR 1: protected effects reached through called scripts.

A call to a script (``script.<key>``, ``script.turn_on`` with a script entity,
``homeassistant.turn_on`` with a script entity) counts as the protected
effects of the called script, directly or through further scripts up to a
bounded depth. The calling step's reachability is compared exactly as for a
direct effect. A called script that cannot be resolved (templated name,
unknown, unreadable, depth limit, cycle) is ``unresolved``, which requires
the acknowledgement. A change to the called script itself stays covered by
script parity on that script's own operation.

Every reproduction runs through both the configuration service and the F3
runtime with the shared review-correction checks: plan approval alone leaves
the plan pending with no write, and the exact acknowledgement dispatches it
once. All fixtures are synthetic and anonymized.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ChangePlan,
    ConfigurationOperation,
)
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    CALLED_SCRIPT_MODEL,
    MAX_CALL_DEPTH,
    VERDICT_CHANGED,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    VERDICT_UNRESOLVED,
    compare_operation,
    compare_physical_effects,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    policy_subject_payload,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from tests import test_dev14_configuration_plans as dev14  # noqa: E402
from tests import test_f3_runtime_integration as f3  # noqa: E402
from tests import test_hamcp213_pr001_fixtures as pr001  # noqa: E402
from tests import test_hamcp213_review_corrections as rc  # noqa: E402

GARAGE_SCRIPT = "close_review_garage_with_retry"
GARAGE_ENTITY = f"script.{GARAGE_SCRIPT}"
REGISTRY = "entity_registry_metadata"
GUARD = rc.GUARD
EDITED = "Edited description"
UNLOCK_OR_DISARM = rc.UNLOCK_OR_DISARM

# Anonymized shape of a garage close with one bounded retry.
CLOSE_WITH_RETRY = rc.script(
    rc.CLOSE,
    {
        "wait_template": "{{ is_state('cover.garage_main', 'closed') }}",
        "timeout": "00:00:30",
        "continue_on_timeout": True,
    },
    {
        "if": [{"condition": "state", "entity_id": rc.GARAGE, "state": "closed"}],
        "then": [{"stop": "Closed"}],
    },
    rc.CLOSE,
    alias="Close review garage with retry",
)
UNLOCK_SCRIPT = rc.script(rc.UNLOCK, alias="Review entry unlock")
RELAY_SCRIPT = rc.script(
    {"action": "script.review_entry_unlock"}, alias="Review relay"
)
LIGHT_SCRIPT = rc.script(
    rc.call("light.turn_on", target={"entity_id": "light.review_porch"}),
    alias="Review porch light",
)


def run(key=GARAGE_SCRIPT):
    return {"action": f"script.{key}"}


def turn_on(entity_id, service="script.turn_on"):
    return {"action": service, "target": {"entity_id": entity_id}}


def registry_entry(entity_id, unique_id, platform="script"):
    return {
        "entity_id": entity_id,
        "platform": platform,
        "unique_id": unique_id,
    }


GARAGE_SCRIPTS = {GARAGE_SCRIPT: CLOSE_WITH_RETRY}
GARAGE_REGISTRY = {GARAGE_ENTITY: registry_entry(GARAGE_ENTITY, GARAGE_SCRIPT)}
UNLOCK_SCRIPTS = {
    "review_relay": RELAY_SCRIPT,
    "review_entry_unlock": UNLOCK_SCRIPT,
}
CYCLE_SCRIPTS = {
    "review_cycle_a": rc.script({"action": "script.review_cycle_b"}, rc.CLOSE),
    "review_cycle_b": rc.script({"action": "script.review_cycle_a"}),
}
CHAIN_SCRIPTS = {
    f"review_chain_{index}": rc.script({"action": f"script.review_chain_{index + 1}"})
    for index in range(MAX_CALL_DEPTH)
}
CHAIN_SCRIPTS[f"review_chain_{MAX_CALL_DEPTH}"] = rc.script(rc.CLOSE)


@dataclass(frozen=True)
class Case(rc.Case):
    scripts: dict = field(default_factory=dict)
    registry: dict = field(default_factory=dict)
    unreadable: tuple = ()


GATED_CASES = (
    # Required: an added condition before the garage-closing script call.
    Case(
        "condition_before_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation(run(), conditions=(GUARD,)),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "condition_step_before_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation(GUARD, run()),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "trigger_changed_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation(run(), trigger_to="off"),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "mode_changed_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation(run(), mode="restart"),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "wait_before_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation(rc.DELAY, run()),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "branch_moved_garage_script",
        "automation",
        rc.automation(run()),
        rc.automation({"if": [GUARD], "then": [run()]}),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "reference_step_before_garage_script",
        "automation",
        rc.automation(rc.note("Closing"), run()),
        rc.automation(rc.note("Closing", continue_on_error=True), run()),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "garage_script_call_added",
        "automation",
        rc.automation(rc.DELAY),
        rc.automation(rc.DELAY, run()),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "garage_script_call_removed",
        "automation",
        rc.automation(rc.DELAY, run()),
        rc.automation(rc.DELAY),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    # Entity invocation forms resolve through the entity registry.
    Case(
        "script_turn_on_entity_condition",
        "automation",
        rc.automation(turn_on(GARAGE_ENTITY)),
        rc.automation(turn_on(GARAGE_ENTITY), conditions=(GUARD,)),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
        registry=GARAGE_REGISTRY,
    ),
    Case(
        "homeassistant_turn_on_entity_condition",
        "automation",
        rc.automation(turn_on(GARAGE_ENTITY, "homeassistant.turn_on")),
        rc.automation(
            turn_on(GARAGE_ENTITY, "homeassistant.turn_on"), conditions=(GUARD,)
        ),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
        registry=GARAGE_REGISTRY,
    ),
    Case(
        "script_caller_condition_before_garage_script",
        "script",
        rc.script(run()),
        rc.script(GUARD, run()),
        VERDICT_CHANGED,
        scripts=GARAGE_SCRIPTS,
    ),
    # Required: a templated script name is unresolved.
    Case(
        "templated_script_entity",
        "automation",
        rc.automation(turn_on("{{ 'script.close_review_garage_with_retry' }}")),
        rc.automation(turn_on("{{ 'script.close_review_garage_with_retry' }}"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "variable_script_entity",
        "automation",
        rc.automation({"variables": {"door": GARAGE_ENTITY}}, turn_on("{{ door }}")),
        rc.automation({"variables": {"door": GARAGE_ENTITY}}, turn_on("{{ door }}"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "templated_script_service",
        "automation",
        rc.automation({"action": "script.{{ door }}"}),
        rc.automation({"action": "script.{{ door }}"}, description=EDITED),
        VERDICT_UNRESOLVED,
        True,
    ),
    Case(
        "script_turn_on_area",
        "automation",
        rc.automation({"action": "script.turn_on", "target": {"area_id": "review"}}),
        rc.automation({"action": "script.turn_on", "target": {"area_id": "review"}}, description=EDITED),
        VERDICT_UNRESOLVED,
        True,
    ),
    # Required: a script calling a script containing lock.unlock keeps the
    # OD-6(b) floor, even when the caller is otherwise equivalent.
    Case(
        "nested_unlock_unchanged_call",
        "automation",
        rc.automation(run("review_relay")),
        rc.automation(run("review_relay"), description=EDITED),
        VERDICT_EQUIVALENT,
        True,
        scripts=UNLOCK_SCRIPTS,
    ),
    Case(
        "nested_unlock_condition_added",
        "automation",
        rc.automation(run("review_relay")),
        rc.automation(run("review_relay"), conditions=(GUARD,)),
        VERDICT_CHANGED,
        True,
        scripts=UNLOCK_SCRIPTS,
    ),
    # Required: a cycle is unresolved.
    Case(
        "script_cycle",
        "automation",
        rc.automation(run("review_cycle_a")),
        rc.automation(run("review_cycle_a"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=CYCLE_SCRIPTS,
    ),
    Case(
        "unknown_script",
        "automation",
        rc.automation(run("review_missing")),
        rc.automation(run("review_missing"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
    ),
    Case(
        "unreadable_script",
        "automation",
        rc.automation(run()),
        rc.automation(run(), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=GARAGE_SCRIPTS,
        unreadable=(("script", GARAGE_SCRIPT),),
    ),
    Case(
        "blueprint_script",
        "automation",
        rc.automation(run("review_blueprint")),
        rc.automation(run("review_blueprint"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts={
            "review_blueprint": {
                "alias": "Review blueprint",
                "use_blueprint": {"path": "review.yaml", "input": {}},
            }
        },
    ),
    Case(
        "depth_limit",
        "automation",
        rc.automation(run("review_chain_0")),
        rc.automation(run("review_chain_0"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=CHAIN_SCRIPTS,
    ),
    Case(
        "unknown_script_entity",
        "automation",
        rc.automation(turn_on("script.review_unregistered")),
        rc.automation(turn_on("script.review_unregistered"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
    ),
    Case(
        "non_script_registry_entity",
        "automation",
        rc.automation(turn_on("script.review_foreign")),
        rc.automation(turn_on("script.review_foreign"), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        registry={
            "script.review_foreign": registry_entry(
                "script.review_foreign", "review_foreign", platform="template"
            )
        },
    ),
    Case(
        "unreadable_script_entity",
        "automation",
        rc.automation(turn_on(GARAGE_ENTITY)),
        rc.automation(turn_on(GARAGE_ENTITY), description=EDITED),
        VERDICT_UNRESOLVED,
        True,
        scripts=GARAGE_SCRIPTS,
        unreadable=((REGISTRY, GARAGE_ENTITY),),
    ),
)

PLAN_APPROVAL_ONLY_CASES = (
    # Required: an unchanged call is equivalent (plan approval only).
    Case(
        "unchanged_garage_script_call",
        "automation",
        rc.automation(run()),
        rc.automation(run(), description=EDITED),
        VERDICT_EQUIVALENT,
        scripts=GARAGE_SCRIPTS,
    ),
    Case(
        "unchanged_garage_entity_call",
        "automation",
        rc.automation(turn_on(GARAGE_ENTITY)),
        rc.automation(turn_on(GARAGE_ENTITY), description=EDITED),
        VERDICT_EQUIVALENT,
        scripts=GARAGE_SCRIPTS,
        registry=GARAGE_REGISTRY,
    ),
    Case(
        "trailing_wording_after_garage_script",
        "automation",
        rc.automation(run(), rc.note("Closed")),
        rc.automation(run(), rc.note("Garage closed")),
        VERDICT_EQUIVALENT,
        scripts=GARAGE_SCRIPTS,
    ),
    # A called script without a protected effect adds none.
    Case(
        "non_protected_script_condition_added",
        "automation",
        rc.automation(run("review_porch_light")),
        rc.automation(run("review_porch_light"), conditions=(GUARD,)),
        VERDICT_NONE,
        scripts={"review_porch_light": LIGHT_SCRIPT},
    ),
    Case(
        "script_reload",
        "automation",
        rc.automation({"action": "script.reload"}),
        rc.automation({"action": "script.reload"}, conditions=(GUARD,)),
        VERDICT_NONE,
    ),
)


class _UnreadableGateway:
    """Wrap the fake gateway so selected reads fail like a provider error."""

    def __init__(self, delegate, unreadable):
        self.delegate = delegate
        self.unreadable = set(unreadable)

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def read(self, resource_type, resource_id):
        if (resource_type, resource_id) in self.unreadable:
            raise RuntimeError("synthetic unreadable resource")
        return await self.delegate.read(resource_type, resource_id)


class _CalledScriptChecks(rc._ReviewCorrectionChecks):
    # Reuse the shared checks only; the review-correction module runs its own
    # reproductions.
    test_h1_unresolvable_effects_require_the_acknowledgement = None
    test_h2_reference_steps_before_an_effect_are_compared_in_full = None
    test_h3_unlock_and_disarm_always_require_the_acknowledgement = None
    test_controls_keep_plan_approval_only = None
    test_garage_retained_effect_exception_is_unchanged = None
    test_pr001_verdicts_are_unchanged_by_the_corrections = None

    async def create(self, case):
        for key, config in case.scripts.items():
            self.gateway.configs[("script", key)] = copy.deepcopy(config)
        for entity_id, entry in case.registry.items():
            self.gateway.configs[(REGISTRY, entity_id)] = copy.deepcopy(entry)
        original = self.service.gateway
        if case.unreadable:
            self.service.gateway = _UnreadableGateway(original, case.unreadable)
        try:
            return await super().create(case)
        finally:
            self.service.gateway = original

    async def test_called_script_effects_require_the_acknowledgement(self):
        for case in GATED_CASES:
            with self.subTest(case=case.name):
                await self.assert_acknowledgement_gated(case)

    async def test_unchanged_or_non_protected_calls_keep_plan_approval_only(self):
        for case in PLAN_APPROVAL_ONLY_CASES:
            with self.subTest(case=case.name):
                await self.assert_plan_approval_only(case)

    async def test_condition_before_garage_script_rows(self):
        created, operation, _target = await self.create(GATED_CASES[0])
        comparison = compare_operation(operation)
        (row,) = comparison.projection["rows"]
        self.assertEqual(row["object_id"], rc.GARAGE)
        self.assertEqual(row["verdict"], "changed")
        self.assertTrue(row["acknowledgement_required"])
        self.assertTrue(
            all(
                item["service"].startswith(f"script.{GARAGE_SCRIPT}>cover.close_cover")
                for item in row["before"] + row["after"]
            )
        )
        snapshot = operation.called_scripts
        self.assertEqual(snapshot["model"], CALLED_SCRIPT_MODEL)
        self.assertEqual(sorted(snapshot["scripts"]), [GARAGE_SCRIPT])
        self.assertEqual(created["policy_decision"]["policy_class"], "elevated_admin")

    async def test_unresolved_reasons_are_named(self):
        expected = {
            "templated_script_entity": "called_script_dynamic",
            "script_cycle": "called_script_cycle",
            "unknown_script": "called_script_unknown",
            "unreadable_script": "called_script_unreadable",
            "blueprint_script": "called_script_unreadable",
            "depth_limit": "called_script_depth_exceeded",
            "unknown_script_entity": "called_script_unknown",
            "non_script_registry_entity": "called_script_entity_unresolved",
            "unreadable_script_entity": "called_script_unreadable",
        }
        cases = {case.name: case for case in GATED_CASES}
        for name, reason in expected.items():
            with self.subTest(case=name):
                _created, operation, _target = await self.create(cases[name])
                comparison = compare_operation(operation)
                self.assertIn(reason, comparison.reason_codes)
                self.assertTrue(comparison.unlock_or_disarm)

    async def test_called_script_changed_in_the_same_plan(self):
        # The called script's own change is compared by its own operation
        # (script parity); the unchanged caller stays equivalent.
        self.gateway.configs[("script", GARAGE_SCRIPT)] = copy.deepcopy(CLOSE_WITH_RETRY)
        self.gateway.configs[("automation", "review_caller")] = rc.automation(run())
        changed_script = copy.deepcopy(CLOSE_WITH_RETRY)
        changed_script["sequence"].insert(0, copy.deepcopy(GUARD))
        created = await self.service.create_configuration_plan(
            title="Called script and caller",
            description="HAMCP-213 called-script parity",
            operations=[
                {
                    "operation_id": "script",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": GARAGE_SCRIPT,
                    "depends_on": [],
                    "proposed_config": changed_script,
                },
                {
                    "operation_id": "caller",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "review_caller",
                    "depends_on": [],
                    "proposed_config": rc.automation(
                        run(), description=EDITED
                    ),
                },
            ],
        )
        stored = {
            item.operation_id: item
            for item in self.repository.get(created["plan_id"]).operations
        }
        self.assertEqual(compare_operation(stored["script"]).verdict, VERDICT_CHANGED)
        self.assertEqual(compare_operation(stored["caller"]).verdict, VERDICT_EQUIVALENT)
        self.assertEqual(
            len(stored["caller"].called_scripts["scripts"][GARAGE_SCRIPT]["configs"]), 2
        )
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"],
            ["plan_approval", rc.SAFETY],
        )
        plan_only = await self.approve_plan_only(created)
        self.assertEqual(plan_only["status"], "approval_pending")
        with self.assertRaises(GovernanceError):
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(self.writes(), [])

    async def test_pr001_verdicts_are_unchanged(self):
        for op, resource_type, target in pr001.PLAN:
            self.gateway.configs[(resource_type, target)] = pr001.load(
                f"{op}_before.yaml"
            )
        operations = []
        for op, resource_type, target in pr001.PLAN:
            proposed = pr001.load(f"{op}_after.yaml")
            proposed.pop("id", None)
            operations.append(
                {
                    "operation_id": op,
                    "resource_type": resource_type,
                    "action": "update",
                    "target_id": target,
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            )
        created = await self.service.create_configuration_plan(
            title="PR001 audit plan",
            description="HAMCP-213 anonymized PR001 regression",
            operations=operations,
        )
        for operation in self.repository.get(created["plan_id"]).operations:
            with self.subTest(op=operation.operation_id):
                self.assertIsNone(operation.called_scripts)
                comparison = compare_operation(operation)
                self.assertEqual(
                    comparison.verdict, rc.PR001_VERDICTS[operation.operation_id]
                )
                self.assertFalse(comparison.unlock_or_disarm)


class ServicePathCalledScriptTests(_CalledScriptChecks, dev14.ConfigurationPlanTestCase):
    """The reproductions through the configuration service."""

    async def test_snapshot_is_bound_and_absent_fields_are_not_serialized(self):
        created, _caller, _target = await self.create(GATED_CASES[0])
        plan = self.repository.get(created["plan_id"])
        serialized = plan.to_dict()
        self.assertIn("called_scripts", serialized["operations"][0])
        self.assertEqual(
            ChangePlan.from_dict(copy.deepcopy(serialized)).operations[0].called_scripts,
            plan.operations[0].called_scripts,
        )
        tampered = ChangePlan.from_dict(copy.deepcopy(serialized))
        tampered.operations[0].called_scripts["scripts"][GARAGE_SCRIPT] = {
            "configs": [rc.script(rc.DELAY)]
        }
        self.assertNotEqual(
            ChangeGovernanceService.plan_hash(tampered),
            ChangeGovernanceService.plan_hash(plan),
        )
        self.assertNotEqual(
            policy_subject_payload(tampered), policy_subject_payload(plan)
        )

        # An operation without a script call serializes exactly as before.
        created, plain, _target = await self.create(PLAN_APPROVAL_ONLY_CASES[-1])
        self.assertIsNone(plain.called_scripts)
        serialized = self.repository.get(created["plan_id"]).to_dict()
        self.assertNotIn("called_scripts", serialized["operations"][0])
        restored = ConfigurationOperation.from_dict(serialized["operations"][0])
        self.assertIsNone(restored.called_scripts)


class F3PathCalledScriptTests(_CalledScriptChecks, dev14.ConfigurationPlanTestCase):
    """The same reproductions through the F3 runtime."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = f3.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=f3._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=f3._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")


class PureCalledScriptComparisonTests(unittest.TestCase):
    """The comparison is pure; without a snapshot a call is unresolved."""

    def _compare(self, snapshot):
        caller = {"alias": "Caller", "sequence": [run()]}
        return compare_physical_effects("script", "update", caller, caller, snapshot)

    def test_missing_snapshot_is_unresolved_never_none(self):
        for snapshot in (None, {}, {"model": "other"}, {"model": CALLED_SCRIPT_MODEL}):
            with self.subTest(snapshot=snapshot):
                comparison = self._compare(snapshot)
                self.assertEqual(comparison.verdict, VERDICT_UNRESOLVED)
                self.assertIn("called_script_not_captured", comparison.reason_codes)
                self.assertTrue(comparison.acknowledgement_required)
                self.assertTrue(comparison.unlock_or_disarm)

    def test_malformed_snapshot_entries_are_unresolved(self):
        for entry in (
            {"configs": []},
            {"configs": "x"},
            {"configs": [1]},
            {"unresolved": "invented_reason"},
            "x",
        ):
            with self.subTest(entry=entry):
                comparison = self._compare(
                    {"model": CALLED_SCRIPT_MODEL, "scripts": {GARAGE_SCRIPT: entry}}
                )
                self.assertEqual(comparison.verdict, VERDICT_UNRESOLVED)

    def test_resolved_snapshot_is_deterministic(self):
        snapshot = {
            "model": CALLED_SCRIPT_MODEL,
            "scripts": {GARAGE_SCRIPT: {"configs": [CLOSE_WITH_RETRY]}},
            "entities": {},
        }
        hashes = {self._compare(copy.deepcopy(snapshot)).projection_hash for _ in range(3)}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(self._compare(snapshot).verdict, VERDICT_EQUIVALENT)


if __name__ == "__main__":
    unittest.main()
