"""HAMCP-213 PR 1 correction batch 3: structural inversion (H3, H4, H5).

* A (H4): the D2 comparison is an allowlist of ignorable differences. On any
  path that can reach a protected effect, the normalized structures must be
  identical except for the committed ``IGNORABLE_DIFFERENCES`` table; any
  other difference, an ``enabled`` flag at any nesting level or an unknown
  key included, is ``changed``. The design document carries the same table.
* B (H3): the unlock/disarm floor is computed first, independent of every
  bound. A comparison that exceeds a bound is incomplete and prohibited
  ("too many protected objects to review: split the change") on every
  route, the legacy contract-v1 route included; never a weaker class.
* C (H5): the called-script snapshot is bound into the comparison projection
  and re-read at every dispatch; a changed callee refuses the plan as stale
  with no write, and an unchanged callee applies.

The reviewer's reproductions run through both the configuration service and
the F3 runtime. All fixtures are synthetic and anonymized.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalPolicyClass,
    ChangeRiskAssessment,
    ConfigurationOperation,
    RiskLevel,
)
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    RESOURCE_NORMALIZATION_VERSION,
    normalize_resource_config,
)
from ha_mcp_engineering.governance import physical_effects as pe  # noqa: E402
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    CALLED_SCRIPT_MODEL,
    IGNORABLE_DIFFERENCES,
    MAX_CALLED_SCRIPTS,
    MAX_ROWS,
    VERDICT_CHANGED,
    VERDICT_EQUIVALENT,
    VERDICT_NONE,
    compare_operation,
    compare_physical_effects,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    SAFETY_CRITICAL_REVIEW_BOUNDS_EXCEEDED,
    SPLIT_THE_CHANGE,
    configuration_operation_policy,
)
from tests import test_hamcp213_called_script_effects as cs  # noqa: E402
from tests import test_hamcp213_review_corrections as rc  # noqa: E402
from tests.test_beta25_external_approval import (  # noqa: E402
    ExternalApprovalTestCase,
)

DESIGN = ROOT / "docs" / "HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md"
UNLOCK_OR_DISARM = rc.UNLOCK_OR_DISARM


def branch(step, enabled):
    # Reviewer H4 shape: a parallel branch ``sequence`` wrapper whose
    # ``enabled`` flag pinned Home Assistant Core accepts.
    return {"parallel": [{"sequence": [copy.deepcopy(step)], "enabled": enabled}]}


def snapshot(scripts=None, entities=None, truncated=False):
    value = {
        "model": CALLED_SCRIPT_MODEL,
        "scripts": {
            key: {"configs": [copy.deepcopy(config)]}
            for key, config in (scripts or {}).items()
        },
        "entities": dict(entities or {}),
    }
    if truncated:
        value["truncated"] = True
    return value


GARAGE_SNAPSHOT = snapshot({cs.GARAGE_SCRIPT: cs.CLOSE_WITH_RETRY})


def _many(service, domain, count):
    return {
        "action": service,
        "target": {"entity_id": [f"{domain}.review_{index}" for index in range(count)]},
    }


# -- A. enabled flags at every nesting level ---------------------------------
#
# Each builder places the effect at one nesting position and puts the
# ``enabled`` flag on one structural element of the path to it. The flag
# differs between the two sides; nothing else does.


def _sweep_builders():
    return {
        "effect_step": lambda effect, flag: [{**effect, "enabled": flag}],
        "condition_step_before": lambda effect, flag: [
            {**rc.GUARD, "enabled": flag},
            effect,
        ],
        "delay_step_before": lambda effect, flag: [
            {**rc.DELAY, "enabled": flag},
            effect,
        ],
        "sequence_step": lambda effect, flag: [
            {"sequence": [effect], "enabled": flag}
        ],
        "sequence_member": lambda effect, flag: [
            {"sequence": [{**rc.GUARD, "enabled": flag}, effect]}
        ],
        "parallel_step": lambda effect, flag: [
            {"parallel": [{"sequence": [effect]}], "enabled": flag}
        ],
        "parallel_branch_wrapper": lambda effect, flag: [branch(effect, flag)],
        "parallel_member": lambda effect, flag: [
            {"parallel": [{**effect, "enabled": flag}, rc.DELAY]}
        ],
        "choose_step": lambda effect, flag: [
            {"choose": [{"conditions": [rc.GUARD], "sequence": [effect]}], "enabled": flag}
        ],
        "choose_option_condition": lambda effect, flag: [
            {
                "choose": [
                    {
                        "conditions": [{**rc.GUARD, "enabled": flag}],
                        "sequence": [effect],
                    }
                ]
            }
        ],
        "choose_option": lambda effect, flag: [
            {
                "choose": [
                    {"conditions": [rc.GUARD], "sequence": [effect], "enabled": flag}
                ]
            }
        ],
        "choose_default_member": lambda effect, flag: [
            {
                "choose": [{"conditions": [rc.GUARD], "sequence": [rc.DELAY]}],
                "default": [{**rc.GUARD, "enabled": flag}, effect],
            }
        ],
        "if_step": lambda effect, flag: [
            {"if": [rc.GUARD], "then": [effect], "enabled": flag}
        ],
        "if_condition": lambda effect, flag: [
            {"if": [{**rc.GUARD, "enabled": flag}], "then": [effect]}
        ],
        "else_member": lambda effect, flag: [
            {"if": [rc.GUARD], "then": [rc.DELAY], "else": [{**effect, "enabled": flag}]}
        ],
        "repeat_step": lambda effect, flag: [
            {"repeat": {"count": 2, "sequence": [effect]}, "enabled": flag}
        ],
        "repeat_member": lambda effect, flag: [
            {"repeat": {"count": 2, "sequence": [{**rc.DELAY, "enabled": flag}, effect]}}
        ],
        "nested_parallel_in_choose": lambda effect, flag: [
            {
                "choose": [
                    {
                        "conditions": [rc.GUARD],
                        "sequence": [branch(effect, flag)],
                    }
                ]
            }
        ],
        "reference_step_before": lambda effect, flag: [
            rc.note("Closing", enabled=flag),
            effect,
        ],
    }


# Every key added on the effect path (step, wrapper, option, branch, repeat,
# configuration) must be ``changed``: unknown keys are never ignorable.
def _unknown_key_builders():
    marker = {"x_unreviewed": True}
    return {
        "effect_step": lambda effect: [{**effect, **marker}],
        "effect_target": lambda effect: [
            {**effect, "target": {**effect.get("target", {}), **marker}}
        ]
        if "target" in effect
        else [{**effect, **marker}],
        "condition_before": lambda effect: [{**rc.GUARD, **marker}, effect],
        "parallel_branch_wrapper": lambda effect: [
            {"parallel": [{"sequence": [effect], **marker}]}
        ],
        "choose_option": lambda effect: [
            {"choose": [{"conditions": [rc.GUARD], "sequence": [effect], **marker}]}
        ],
        "repeat": lambda effect: [
            {"repeat": {"count": 2, "sequence": [effect], **marker}}
        ],
        "if_step": lambda effect: [{"if": [rc.GUARD], "then": [effect], **marker}],
        "sequence_step": lambda effect: [{"sequence": [effect], **marker}],
        "reference_step_before": lambda effect: [rc.note("Closing", **marker), effect],
    }


def _compare_script(before_steps, after_steps, called=None):
    return compare_physical_effects(
        "script",
        "update",
        rc.script(*before_steps),
        rc.script(*after_steps),
        called,
    )


EFFECTS = {
    "direct_close": (rc.CLOSE, None),
    "called_garage_script": (cs.run(), GARAGE_SNAPSHOT),
}


class IgnorableDifferencesTableTests(unittest.TestCase):
    def test_design_document_table_equals_the_committed_table(self):
        text = DESIGN.read_text(encoding="utf-8")
        match = re.search(
            r"<!-- ignorable-differences:start -->(.*?)<!-- ignorable-differences:end -->",
            text,
            re.S,
        )
        self.assertIsNotNone(match, "the design document lacks the table markers")
        rows = []
        for line in match.group(1).strip().splitlines():
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            if len(cells) != 3 or cells[0] in {"Scope"} or set(cells[0]) <= {"-", " "}:
                continue
            rows.append(tuple(cells))
        self.assertEqual(tuple(rows), IGNORABLE_DIFFERENCES)

    def test_the_comparison_derives_its_ignorable_sets_from_the_table(self):
        self.assertEqual(pe._DISPLAY_STEP_KEYS, frozenset({"alias"}))
        self.assertEqual(pe._CHOOSE_OPTION_IGNORED_KEYS, frozenset({"alias"}))
        self.assertEqual(pe._PARALLEL_BRANCH_IGNORED_KEYS, frozenset({"alias"}))
        self.assertEqual(pe._DISPLAY_DATA_KEYS, frozenset({"message", "title"}))
        self.assertEqual(
            pe._CONTEXT_EXCLUDED_KEYS,
            {
                "automation": frozenset({"action", "alias", "description"}),
                "script": frozenset({"sequence", "alias", "description", "icon"}),
            },
        )
        # No reviewed behavioral key is ever in an ignorable set.
        ignorable = (
            pe._DISPLAY_STEP_KEYS
            | pe._CHOOSE_OPTION_IGNORED_KEYS
            | pe._PARALLEL_BRANCH_IGNORED_KEYS
            | pe._CONTEXT_EXCLUDED_KEYS["automation"]
            | pe._CONTEXT_EXCLUDED_KEYS["script"]
        )
        for key in (
            "enabled", "continue_on_error", "mode", "max", "max_exceeded",
            "variables", "timeout", "continue_on_timeout", "response_variable",
            "trigger", "condition", "fields", "x_unreviewed",
        ):
            self.assertNotIn(key, ignorable)


class AllowlistInversionTests(unittest.TestCase):
    """Pure comparison: everything not in the table is ``changed``."""

    def test_enabled_flag_sweep_at_every_nesting_level(self):
        for effect_name, (effect, called) in EFFECTS.items():
            for level, build in _sweep_builders().items():
                for before_flag, after_flag in ((True, False), (False, True)):
                    with self.subTest(effect=effect_name, level=level, before=before_flag):
                        result = _compare_script(
                            build(copy.deepcopy(effect), before_flag),
                            build(copy.deepcopy(effect), after_flag),
                            called,
                        )
                        self.assertEqual(result.verdict, VERDICT_CHANGED, result.reason_codes)
                        self.assertIn("run_structure_changed", result.reason_codes)
                        same = _compare_script(
                            build(copy.deepcopy(effect), before_flag),
                            build(copy.deepcopy(effect), before_flag),
                            called,
                        )
                        self.assertEqual(same.verdict, VERDICT_EQUIVALENT, same.reason_codes)

    def test_unknown_key_added_on_the_effect_path_is_changed(self):
        for effect_name, (effect, called) in EFFECTS.items():
            for level, build in _unknown_key_builders().items():
                with self.subTest(effect=effect_name, level=level):
                    result = _compare_script(
                        [copy.deepcopy(effect)] if level != "reference_step_before"
                        else [rc.note("Closing"), copy.deepcopy(effect)],
                        build(copy.deepcopy(effect)),
                        called,
                    )
                    self.assertEqual(result.verdict, VERDICT_CHANGED, result.reason_codes)
        for kind in ("automation", "script"):
            with self.subTest(configuration=kind):
                before = (
                    rc.automation(rc.CLOSE) if kind == "automation" else rc.script(rc.CLOSE)
                )
                after = {**copy.deepcopy(before), "x_unreviewed": True}
                result = compare_physical_effects(kind, "update", before, after)
                self.assertEqual(result.verdict, VERDICT_CHANGED, result.reason_codes)
                self.assertIn("reachability_context_changed", result.reason_codes)

    def test_behavioral_fields_are_changed(self):
        wait = {"wait_template": "{{ true }}", "timeout": "00:00:10"}
        cases = {
            "continue_on_error": ([rc.CLOSE], [{**rc.CLOSE, "continue_on_error": True}]),
            "wait_timeout": ([wait, rc.CLOSE], [{**wait, "timeout": "00:00:20"}, rc.CLOSE]),
            "continue_on_timeout": (
                [wait, rc.CLOSE],
                [{**wait, "continue_on_timeout": False}, rc.CLOSE],
            ),
            "response_variable": (
                [rc.CLOSE],
                [{**rc.CLOSE, "response_variable": "reply"}],
            ),
            "step_variables": (
                [{"variables": {"a": 1}}, rc.CLOSE],
                [{"variables": {"a": 2}}, rc.CLOSE],
            ),
            "then_without_if_is_kept": (
                [rc.CLOSE, {"then": [rc.DELAY]}],
                [rc.CLOSE, {"then": [rc.OPEN]}],
            ),
            "reference_after_close_selector": (
                [rc.CLOSE, rc.note("Closed", target={"device_id": "a"})],
                [rc.CLOSE, rc.note("Closed", target={"device_id": "b"})],
            ),
            "reference_after_close_non_mapping_data": (
                [rc.CLOSE, {"action": "notify.mobile_app_review", "data": "{{ a }}"}],
                [rc.CLOSE, {"action": "notify.mobile_app_review", "data": "{{ b }}"}],
            ),
        }
        for name, (before, after) in cases.items():
            with self.subTest(field=name):
                result = _compare_script(before, after)
                self.assertEqual(result.verdict, VERDICT_CHANGED, result.reason_codes)
        for name, change in {
            "mode": {"mode": "restart"},
            "max": {"max": 3},
            "variables": {"variables": {"a": 1}},
            "fields": {"fields": {"a": {}}},
        }.items():
            with self.subTest(context=name):
                before = rc.script(rc.CLOSE)
                result = compare_physical_effects(
                    "script", "update", before, {**copy.deepcopy(before), **change}
                )
                self.assertEqual(result.verdict, VERDICT_CHANGED, result.reason_codes)

    def test_ignorable_fields_stay_equivalent(self):
        aliased = {**rc.CLOSE, "alias": "Close"}
        cases = {
            "step_alias": ([rc.CLOSE], [aliased]),
            "choose_option_alias": (
                [{"choose": [{"conditions": [rc.GUARD], "sequence": [rc.CLOSE]}]}],
                [
                    {
                        "choose": [
                            {"alias": "Shut", "conditions": [rc.GUARD], "sequence": [rc.CLOSE]}
                        ]
                    }
                ],
            ),
            "parallel_branch_alias": (
                [{"parallel": [{"sequence": [rc.CLOSE]}]}],
                [{"parallel": [{"alias": "Shut", "sequence": [rc.CLOSE]}]}],
            ),
            "trailing_text": ([rc.CLOSE, rc.note("a")], [rc.CLOSE, rc.note("b")]),
            "trailing_payload": (
                [rc.CLOSE, rc.nested_note("a")],
                [rc.CLOSE, rc.nested_note("b")],
            ),
            "trailing_service_key": (
                [rc.CLOSE, rc.note("a")],
                [
                    rc.CLOSE,
                    {"service": "notify.mobile_app_review", "data": {"message": "a"}},
                ],
            ),
            "trailing_non_aborting_step": (
                [rc.CLOSE, rc.note("a", continue_on_error=True)],
                [rc.CLOSE],
            ),
        }
        for name, (before, after) in cases.items():
            with self.subTest(field=name):
                result = _compare_script(before, after)
                self.assertEqual(result.verdict, VERDICT_EQUIVALENT, result.reason_codes)
        before = rc.script(rc.CLOSE)
        after = {**copy.deepcopy(before), "alias": "B", "description": "C", "icon": "mdi:x"}
        result = compare_physical_effects("script", "update", before, after)
        self.assertEqual(result.verdict, VERDICT_EQUIVALENT, result.reason_codes)


class OverflowFloorTests(unittest.TestCase):
    """B: the floor first, then overflow is prohibited, never weaker."""

    def test_bound_overflow_keeps_the_floor_and_reports_bounds(self):
        for service, domain in (
            ("lock.unlock", "lock"),
            ("alarm_control_panel.alarm_disarm", "alarm_control_panel"),
        ):
            for count, exceeded in ((MAX_ROWS, False), (MAX_ROWS + 1, True)):
                with self.subTest(service=service, count=count):
                    effect = _many(service, domain, count)
                    comparison = compare_physical_effects(
                        "automation",
                        "update",
                        rc.automation(effect),
                        rc.automation(effect, conditions=(rc.GUARD,)),
                    )
                    self.assertTrue(comparison.unlock_or_disarm)
                    self.assertEqual(comparison.bounds_exceeded, exceeded)
                    if exceeded:
                        (row,) = comparison.projection["rows"]
                        self.assertIn(UNLOCK_OR_DISARM, row["reason_codes"])

    def test_floor_is_independent_of_every_bound(self):
        cases = {
            "steps": [rc.DELAY] * (pe.MAX_STEPS + 1) + [rc.UNLOCK],
            "depth": [_nested(rc.UNLOCK, pe.MAX_DEPTH + 2)],
            "dynamic_service": [{"action": "{{ svc }}"}] + [rc.DELAY] * (pe.MAX_STEPS + 1),
            "device_disarm": [
                {"domain": "alarm_control_panel", "device_id": "d", "type": "disarm"}
            ] + [rc.DELAY] * (pe.MAX_STEPS + 1),
        }
        for name, steps in cases.items():
            with self.subTest(bound=name):
                comparison = compare_physical_effects(
                    "script", "update", rc.script(rc.DELAY), rc.script(*steps)
                )
                self.assertTrue(comparison.bounds_exceeded, comparison.reason_codes)
                self.assertTrue(comparison.unlock_or_disarm)
        control = compare_physical_effects(
            "script",
            "update",
            rc.script(rc.DELAY),
            rc.script(*([rc.DELAY] * (pe.MAX_STEPS + 1)), rc.CLOSE),
        )
        self.assertTrue(control.bounds_exceeded)
        self.assertFalse(control.unlock_or_disarm)

    def test_overflow_is_prohibited_on_every_route(self):
        for service, domain in (
            ("lock.unlock", "lock"),
            ("alarm_control_panel.alarm_disarm", "alarm_control_panel"),
            ("cover.close_cover", "cover.garage"),
        ):
            effect = _many(service, domain, MAX_ROWS + 1)
            operation = _stored_operation(
                rc.automation(effect), rc.automation(effect, conditions=(rc.GUARD,))
            )
            for route, kwargs in {
                "typed": {},
                "legacy_current_policy": {
                    "owner_authoritative": False,
                    "safety_critical_comparison": False,
                    "unlock_or_disarm_floor": True,
                },
            }.items():
                with self.subTest(service=service, route=route):
                    result = configuration_operation_policy(operation, **kwargs)
                    self.assertEqual(result.policy_class, ApprovalPolicyClass.PROHIBITED)
                    self.assertIn(SAFETY_CRITICAL_REVIEW_BOUNDS_EXCEEDED, result.reason_codes)
                    self.assertIn(SPLIT_THE_CHANGE, result.reason_codes)
                    self.assertFalse(result.safety_critical_acknowledgement)
                    self.assertEqual(
                        UNLOCK_OR_DISARM in result.reason_codes, service != "cover.close_cover"
                    )

    def test_called_script_overflow_is_prohibited(self):
        chain = {
            f"review_chain_{index}": rc.script({"action": f"script.review_chain_{index + 1}"})
            for index in range(pe.MAX_CALL_DEPTH + 1)
        }
        cases = {
            "depth": snapshot(chain),
            "truncated": snapshot(truncated=True),
        }
        for name, called in cases.items():
            with self.subTest(bound=name):
                comparison = compare_physical_effects(
                    "automation",
                    "update",
                    rc.automation(cs.run("review_chain_0"), conditions=(rc.GUARD,)),
                    rc.automation(cs.run("review_chain_0")),
                    called,
                )
                self.assertTrue(comparison.bounds_exceeded, comparison.reason_codes)
                # An unresolved callee may unlock or disarm (OD-6(b)).
                self.assertTrue(comparison.unlock_or_disarm)

    def test_projection_binds_the_called_script_fingerprints(self):
        before = rc.automation(cs.run(), conditions=(rc.GUARD,))
        after = rc.automation(cs.run())
        first = compare_physical_effects("automation", "update", before, after, GARAGE_SNAPSHOT)
        fingerprints = first.projection["called_script_fingerprints"]
        self.assertEqual(sorted(fingerprints["scripts"]), [cs.GARAGE_SCRIPT])
        changed = snapshot({cs.GARAGE_SCRIPT: rc.script(rc.CLOSE, rc.DELAY)})
        second = compare_physical_effects("automation", "update", before, after, changed)
        self.assertNotEqual(first.projection_hash, second.projection_hash)


def _nested(step, depth):
    value = step
    for _ in range(depth):
        value = {"sequence": [value]}
    return value


def _stored_operation(before, after, called=None, kind="automation"):
    """A minimal stored configuration operation for the policy function."""

    return ConfigurationOperation(
        operation_id="op",
        order=0,
        depends_on=[],
        resource_type=kind,
        action="update",
        target_id="review_bounds",
        helper_type=None,
        proposed_config=copy.deepcopy(after),
        current_config=copy.deepcopy(before),
        normalized_proposed_config=normalize_resource_config(kind, copy.deepcopy(after)),
        normalized_current_config=normalize_resource_config(kind, copy.deepcopy(before)),
        current_state_fingerprint="0" * 64,
        proposed_config_hash="1" * 64,
        normalization_version=RESOURCE_NORMALIZATION_VERSION,
        risk=ChangeRiskAssessment(level=RiskLevel.MEDIUM),
        validation_results={"valid": True},
        called_scripts=called,
    )


# -- Service and F3 path reproductions ----------------------------------------

H4_CASES = (
    cs.Case(
        "parallel_enabled_direct",
        "script",
        rc.script(branch(rc.CLOSE, False)),
        rc.script(branch(rc.CLOSE, True)),
        VERDICT_CHANGED,
    ),
    cs.Case(
        "parallel_enabled_called",
        "script",
        rc.script(branch(cs.run(), False)),
        rc.script(branch(cs.run(), True)),
        VERDICT_CHANGED,
        scripts=cs.GARAGE_SCRIPTS,
    ),
)

LIGHT_CALLEE = cs.Case(
    "callee_changed_after_plan",
    "automation",
    rc.automation(cs.run(), conditions=(rc.GUARD,)),
    rc.automation(cs.run()),
    VERDICT_NONE,
    scripts={cs.GARAGE_SCRIPT: cs.LIGHT_SCRIPT},
)
REMAPPED_ENTITY = cs.Case(
    "entity_remapped_after_plan",
    "automation",
    rc.automation(cs.turn_on(cs.GARAGE_ENTITY), conditions=(rc.GUARD,)),
    rc.automation(cs.turn_on(cs.GARAGE_ENTITY)),
    VERDICT_NONE,
    scripts={cs.GARAGE_SCRIPT: cs.LIGHT_SCRIPT, "review_protected": rc.script(rc.CLOSE)},
    registry=cs.GARAGE_REGISTRY,
)
GARAGE_CALLEE = cs.Case(
    "garage_callee_changed_after_acknowledgement",
    "automation",
    rc.automation(cs.run(), conditions=(rc.GUARD,)),
    rc.automation(cs.run()),
    VERDICT_CHANGED,
    scripts=cs.GARAGE_SCRIPTS,
)


def _bounds_cases():
    cases = []
    for service, domain, count in (
        ("lock.unlock", "lock", MAX_ROWS + 1),
        ("alarm_control_panel.alarm_disarm", "alarm_control_panel", MAX_ROWS + 1),
    ):
        effect = _many(service, domain, count)
        cases.append(
            cs.Case(
                f"{domain}_{count}_bounds_guard",
                "automation",
                rc.automation(effect),
                rc.automation(effect, conditions=(rc.GUARD,)),
                "unresolved",
            )
        )
    return tuple(cases)


class _InversionPathChecks(cs._CalledScriptChecks):
    # Reuse the shared checks only; the other modules run their own cases.
    test_called_script_effects_require_the_acknowledgement = None
    test_unchanged_or_non_protected_calls_keep_plan_approval_only = None
    test_condition_before_garage_script_rows = None
    test_unresolved_reasons_are_named = None

    async def assert_refused_stale(self, created, target, before):
        writes = len(self.writes())
        try:
            result = await self.service.apply(created["plan_id"], created["plan_hash"])
        except GovernanceError as exc:
            self.assertEqual(exc.code, ErrorCode.STALE_TARGET_STATE)
            self.assertEqual(exc.details.get("reason"), "stale_called_script", exc.details)
        else:
            self.assertFalse(result.get("provider_dispatch_occurred"), result)
            self.assertEqual(result.get("task_state"), "failed_pre_dispatch", result)
            children = self.runtime.children.declarations_for_task(result["task_id"])
            records = [self.runtime.children.get(item["child_id"]) for item in children]
            records = [record for record in records if record is not None]
            self.assertTrue(records)
            self.assertTrue(all(record.dispatch_count == 0 for record in records))
            self.assertIn(
                "stale_called_script",
                json.dumps(
                    [[record.evidence, record.events] for record in records],
                    default=str,
                ),
            )
        self.assertEqual(len(self.writes()), writes)
        self.assertEqual(self.gateway.configs[target], before)

    async def test_h4_parallel_branch_enabled_flag_requires_the_acknowledgement(self):
        for case in H4_CASES:
            with self.subTest(case=case.name):
                await self.assert_acknowledgement_gated(case)

    async def test_h5_callee_changed_after_plan_is_refused_as_stale(self):
        created, _operation, target = await self.create(LIGHT_CALLEE)
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"], ["plan_approval"]
        )
        self.gateway.configs[("script", cs.GARAGE_SCRIPT)] = rc.script(rc.UNLOCK)
        granted = await self.approve_plan_only(created)
        self.assertEqual(granted["status"], "approved")
        await self.assert_refused_stale(created, target, LIGHT_CALLEE.before)

    async def test_h5_script_entity_remapped_after_plan_is_refused_as_stale(self):
        created, _operation, target = await self.create(REMAPPED_ENTITY)
        self.gateway.configs[(cs.REGISTRY, cs.GARAGE_ENTITY)] = cs.registry_entry(
            cs.GARAGE_ENTITY, "review_protected"
        )
        granted = await self.approve_plan_only(created)
        self.assertEqual(granted["status"], "approved")
        await self.assert_refused_stale(created, target, REMAPPED_ENTITY.before)

    async def test_h5_callee_changed_after_acknowledgement_is_refused_as_stale(self):
        created, _operation, target = await self.create(GARAGE_CALLEE)
        plan_only = await self.approve_plan_only(created)
        granted, _safety = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        self.gateway.configs[("script", cs.GARAGE_SCRIPT)] = rc.script(rc.UNLOCK)
        await self.assert_refused_stale(created, target, GARAGE_CALLEE.before)

    async def test_h5_unchanged_callee_applies(self):
        for case in (LIGHT_CALLEE, REMAPPED_ENTITY):
            with self.subTest(case=case.name):
                await self.assert_plan_approval_only(case)
        writes = len(self.writes())
        created, _operation, target = await self.create(GARAGE_CALLEE)
        plan_only = await self.approve_plan_only(created)
        granted, _safety = await self.acknowledge(created, plan_only)
        self.assertEqual(granted["status"], "approved")
        await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assert_applied_once(GARAGE_CALLEE, target, writes)

    async def test_h3_bound_overflow_is_prohibited_with_no_write(self):
        for case in _bounds_cases():
            with self.subTest(case=case.name):
                writes = len(self.writes())
                created, operation, target = await self.create(case)
                comparison = compare_operation(operation)
                self.assertTrue(comparison.bounds_exceeded)
                self.assertTrue(comparison.unlock_or_disarm)
                decision = created["policy_decision"]
                self.assertEqual(decision["policy_class"], "prohibited")
                plan = self.repository.get(created["plan_id"])
                self.assertIn(SPLIT_THE_CHANGE, plan.policy_decision.reason_codes)
                with self.assertRaises(GovernanceError):
                    self.service.approve(created["plan_id"], created["plan_hash"])
                with self.assertRaises(GovernanceError):
                    await self.service.apply(created["plan_id"], created["plan_hash"])
                self.assertEqual(len(self.writes()), writes)
                self.assertEqual(self.gateway.configs[target], case.before)

    async def test_h3_plan_acknowledgement_overflow_is_prohibited(self):
        # Two operations of 17 protected covers each: every comparison is in
        # bounds, but the plan needs more acknowledgement rows than one
        # review can bind.
        count = MAX_ROWS // 2 + 1
        operations = []
        for index in range(2):
            effect = _many("cover.close_cover", f"cover.garage_{index}", count)
            target_id = f"review_plan_bounds_{index}"
            self.gateway.configs[("automation", target_id)] = rc.automation(effect)
            operations.append(
                {
                    "operation_id": f"op{index}",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": target_id,
                    "depends_on": [],
                    "proposed_config": rc.automation(effect, conditions=(rc.GUARD,)),
                }
            )
        created = await self.service.create_configuration_plan(
            title="Plan acknowledgement bounds",
            description="HAMCP-213 synthetic plan-level overflow",
            operations=operations,
        )
        self.assertEqual(created["policy_decision"]["policy_class"], "prohibited")
        plan = self.repository.get(created["plan_id"])
        self.assertIn(SPLIT_THE_CHANGE, plan.policy_decision.reason_codes)
        self.assertEqual(self.writes(), [])


class ServicePathInversionTests(_InversionPathChecks, cs.dev14.ConfigurationPlanTestCase):
    """The reproductions through the configuration service."""


class F3PathInversionTests(_InversionPathChecks, cs.dev14.ConfigurationPlanTestCase):
    """The same reproductions through the F3 runtime."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = cs.f3.F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=cs.f3._ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=cs.f3._provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")


class LegacyRouteOverflowTests(ExternalApprovalTestCase):
    """Reviewer reproduction: the legacy contract-v1 route also refuses."""

    async def test_legacy_route_overflow_is_prohibited(self):
        for service, domain in (
            ("lock.unlock", "lock"),
            ("alarm_control_panel.alarm_disarm", "alarm_control_panel"),
        ):
            for count in (1, MAX_ROWS + 1):
                with self.subTest(service=service, count=count):
                    writes = self.gateway.writes
                    effect = _many(service, domain, count)
                    self.gateway.configs["fixture"] = dict(rc.automation(effect), id="fixture")
                    created = await self.service.create_plan(
                        title="Legacy bounds review",
                        description="Synthetic offline",
                        operation="update_automation",
                        automation_id="fixture",
                        proposed_config=rc.automation(effect, conditions=(rc.GUARD,)),
                    )
                    self.assertEqual(created["policy_decision"]["policy_class"], "prohibited")
                    if count > MAX_ROWS:
                        plan = self.repository.get(created["plan_id"])
                        self.assertIn(SPLIT_THE_CHANGE, plan.policy_decision.reason_codes)
                    with self.assertRaises(GovernanceError):
                        await self.grant(created)
                    self.assertEqual(self.gateway.writes, writes)


if __name__ == "__main__":
    unittest.main()
