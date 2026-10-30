"""HAMCP-252: exact readback verification of a sun condition used as a step.

The reported PR030c body was refused before dispatch with
``automation_verifier_unsupported_automation_simple_action_fields``.  The
refused construct is a ``sun`` condition *step* (``after``/``before``) at
``/action/0/choose/2/sequence/2``; the condition-step field allowlist lacked
the sun condition's fields.  Home Assistant stores and returns a config-view
automation verbatim, so the fields are admitted with exact comparison only.

``replica_config`` is a synthetic replica of the reported body: every key,
nesting level, step kind, list position and non-entity value is identical;
entity ids, names and template text are synthetic.  No household
configuration is committed.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.executor import SharedOperationExecutor
from ha_mcp_engineering.f3.locks import DurableLockStore
from ha_mcp_engineering.f3.models import EVIDENCE_PATTERN, ExecutionIdentity
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
from ha_mcp_engineering.f3_configuration.sequence import (
    prepare_configuration_sequence,
    single_operation_child_descriptor,
)
from ha_mcp_engineering.f3_runtime import pre_dispatch_reasons as reasons
from ha_mcp_engineering.governance import normalize
from ha_mcp_engineering.governance.normalize import (
    automation_verification_detail_code,
    automation_verification_location_codes,
    normalize_automation,
    normalize_automation_for_verification,
)
from ha_mcp_engineering.governance.resources import compare_resource_verification
from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
)
from tests.f3_synthetic_adapter import SyntheticApprovalRecorder
from tests.test_f3_configuration_lifecycle import (
    EXECUTOR_TIMING,
    LOCK_TIMING,
    FakeClock,
)

SIMPLE_FIELDS_RULE = (
    "automation_verifier_unsupported_automation_simple_action_fields"
)
SUN_RULE = "automation_verifier_invalid_automation_sun_condition"
SUN_STEP = {"after": "sunset", "before": "sunrise", "condition": "sun"}
SUN_PATH = ("action", 0, "choose", 2, "sequence", 2)
VALUE_MARKER = "synthetic-hamcp252-value-marker"
TEMPLATE = "{{ is_state('binary_sensor.synthetic_occupancy', 'on') }}"


def replica_config():
    def alone(trigger_id, entity_id):
        return {
            "conditions": [{"condition": "trigger", "id": trigger_id}],
            "sequence": [
                {"condition": "state", "entity_id": entity_id, "state": "on"}
            ],
        }

    guarded = {
        "alias": "Synthetic aliased template guard",
        "condition": "template",
        "value_template": TEMPLATE,
    }
    modes = [
        {
            "condition": "state",
            "entity_id": f"input_boolean.synthetic_{name}",
            "state": "off",
        }
        for name in ("away", "guest", "cleaner")
    ]
    return {
        "id": "porch_light",
        "action": [
            {
                "choose": [
                    alone("synthetic_a", "binary_sensor.synthetic_a"),
                    alone("synthetic_b", "binary_sensor.synthetic_b"),
                    {
                        "conditions": [
                            {"condition": "trigger", "id": "synthetic_c"}
                        ],
                        "sequence": [
                            {
                                "continue_on_timeout": False,
                                "timeout": "00:00:07",
                                "wait_template": TEMPLATE,
                            },
                            deepcopy(guarded),
                            deepcopy(SUN_STEP),
                            *deepcopy(modes),
                        ],
                    },
                ]
            },
            {
                "if": [
                    {
                        "condition": "state",
                        "entity_id": "timer.synthetic_snooze",
                        "state": "idle",
                    }
                ],
                "then": [
                    {
                        "action": "notify.synthetic_phone",
                        "data": {
                            "data": {
                                "actions": [
                                    {"action": "SYNTHETIC_A", "title": "A"},
                                    {"action": "SYNTHETIC_B", "title": "B"},
                                    {"action": "SYNTHETIC_C", "title": "C"},
                                ],
                                "channel": "Synthetic",
                                "clickAction": TEMPLATE,
                                "image": TEMPLATE,
                                "priority": "high",
                                "tag": TEMPLATE,
                                "ttl": 0,
                                "url": TEMPLATE,
                            },
                            "message": TEMPLATE,
                            "title": "Synthetic title",
                        },
                    }
                ],
            },
            {
                "action": "persistent_notification.create",
                "data": {
                    "message": TEMPLATE,
                    "notification_id": "synthetic_notification",
                    "title": "Synthetic title",
                },
            },
        ],
        "alias": "Synthetic HAMCP-252 replica",
        "condition": [deepcopy(guarded), deepcopy(SUN_STEP), *deepcopy(modes)],
        "description": "Synthetic HAMCP-252 replica.",
        "max": 10,
        "max_exceeded": "silent",
        "mode": "queued",
        "trigger": [
            {
                "entity_id": f"sensor.synthetic_{name}",
                "id": f"synthetic_{name}",
                "to": "ALERT",
                "trigger": "state",
            }
            for name in ("a", "b", "c")
        ],
    }


def at(config, path):
    value = config
    for token in path:
        value = value[token]
    return value


def wrap(actions, **extra):
    return {
        "id": "porch_light",
        "trigger": [{"trigger": "state", "entity_id": "sensor.synthetic"}],
        "action": actions,
        **extra,
    }


def in_choose(step):
    return [
        {
            "choose": [
                {
                    "conditions": [{"condition": "trigger", "id": "synthetic"}],
                    "sequence": [step],
                }
            ]
        }
    ]


def comparable(config):
    result = compare_resource_verification("automation", config, config)
    return result.normalization_valid and result.semantic_match


class SunConditionComparisonTests(unittest.TestCase):
    def test_replica_verifies_exactly_without_changing_binding_or_shape(self):
        config = replica_config()
        before = deepcopy(config)
        self.assertTrue(comparable(config))
        normalized, categories = normalize_automation_for_verification(config)
        self.assertEqual(normalized, normalize_automation(config))
        self.assertEqual(at(normalized, SUN_PATH), SUN_STEP)
        self.assertEqual(categories, ())
        self.assertIsNone(automation_verification_detail_code(config))
        self.assertEqual(automation_verification_location_codes(config), ())
        self.assertEqual(config, before)

    def test_part1_variants(self):
        template_guard = {
            "alias": "Synthetic",
            "condition": "template",
            "value_template": TEMPLATE,
        }
        passing = {
            "top-level aliased template condition": wrap(
                [{"action": "notify.synthetic"}], condition=[template_guard]
            ),
            "wait_template with timeout and continue_on_timeout": wrap(
                [
                    {
                        "wait_template": TEMPLATE,
                        "timeout": "00:00:07",
                        "continue_on_timeout": False,
                    }
                ]
            ),
            "action step with continue_on_error": wrap(
                [
                    {
                        "action": "switch.turn_on",
                        "target": {"entity_id": "switch.synthetic"},
                        "continue_on_error": True,
                    }
                ]
            ),
            "aliased template condition step in a choose sequence": wrap(
                in_choose(template_guard)
            ),
            "sun condition in the top-level condition list": wrap(
                [{"action": "notify.synthetic"}], condition=[dict(SUN_STEP)]
            ),
            # Refused before HAMCP-252; the exact reported construct.
            "sun condition step in a choose sequence": wrap(
                in_choose(dict(SUN_STEP))
            ),
            "sun condition step at the top-level action": wrap(
                [dict(SUN_STEP)]
            ),
        }
        for name, config in passing.items():
            with self.subTest(name):
                self.assertTrue(comparable(config))
                self.assertIsNone(automation_verification_detail_code(config))

    def test_supported_nested_positions_admit_the_sun_step(self):
        step = dict(SUN_STEP)
        variants = (
            [step],
            [{"choose": [], "default": [step]}],
            [{"if": [], "then": [step], "else": [step]}],
            [{"repeat": {"count": 2, "sequence": [step]}}],
            [{"parallel": [step, {"sequence": [step]}]}],
        )
        for actions in variants:
            with self.subTest(actions=actions):
                self.assertTrue(comparable(wrap(deepcopy(actions))))

    def test_valid_sun_field_forms_are_admitted(self):
        steps = (
            {"condition": "sun", "after": "sunset"},
            {"condition": "sun", "before": "sunrise"},
            {"condition": "sun", "after": "SUNSET", "before": "Sunrise"},
            {"condition": "sun", "after": "sunrise", "after_offset": "-00:30:00"},
            {"condition": "sun", "before": "sunset", "before_offset": {"minutes": 30}},
            {"condition": "sun", "after": "sunset", "after_offset": 90},
            {
                "condition": "sun",
                "after": "sunset",
                "before": "sunrise",
                "alias": "Synthetic",
                "enabled": True,
                "continue_on_error": False,
            },
            # Already admitted before HAMCP-252; never newly refused.
            {"condition": "sun"},
        )
        for step in steps:
            with self.subTest(step=step):
                self.assertTrue(comparable(wrap(in_choose(dict(step)))))

    def test_changed_or_missing_readback_values_never_compare_equal(self):
        approved = replica_config()

        def changed(edit):
            observed = deepcopy(approved)
            edit(at(observed, SUN_PATH), at(observed, SUN_PATH[:-1]))
            return observed

        variants = {
            "after changed": lambda step, seq: step.update(after="sunrise"),
            "before changed": lambda step, seq: step.update(before="sunset"),
            "case changed": lambda step, seq: step.update(after="Sunset"),
            "before missing": lambda step, seq: step.pop("before"),
            "after missing": lambda step, seq: step.pop("after"),
            "offset added": lambda step, seq: step.update(after_offset="00:10:00"),
            "step disabled": lambda step, seq: step.update(enabled=False),
            "step dropped": lambda step, seq: seq.pop(2),
            "steps reordered": lambda step, seq: seq.insert(3, seq.pop(2)),
        }
        for name, edit in variants.items():
            with self.subTest(name):
                result = compare_resource_verification(
                    "automation", approved, changed(edit)
                )
                self.assertTrue(result.normalization_valid)
                self.assertFalse(result.semantic_match)

    def test_changed_offset_values_never_compare_equal(self):
        approved = wrap([{"condition": "sun", "after": "sunset", "after_offset": "-00:30:00"}])
        for offset in ("-00:30:01", {"minutes": -30}, -1800, "00:30:00"):
            with self.subTest(offset=offset):
                observed = deepcopy(approved)
                observed["action"][0]["after_offset"] = offset
                result = compare_resource_verification("automation", approved, observed)
                self.assertTrue(result.normalization_valid)
                self.assertFalse(result.semantic_match)

    def test_unsupported_neighbors_remain_refused_with_location(self):
        cases = (
            ({"condition": "time", "after": "22:00:00"}, ["after"]),
            ({"condition": "time", "before": "06:00:00", "weekday": "mon"}, ["before", "weekday"]),
            ({**SUN_STEP, "weekday": "mon"}, ["weekday"]),
            ({**SUN_STEP, "zone": "zone.synthetic"}, ["zone"]),
            ({"condition": "template", "value_template": TEMPLATE, "after": "sunset"}, ["after"]),
            ({"condition": "state", "entity_id": "a.b", "state": "on", "match": "any"}, ["match"]),
            ({"condition": "trigger", "id": "x", "after": "sunset"}, ["after"]),
            ({**SUN_STEP, VALUE_MARKER: "x"}, ["unrecognized_field"]),
        )
        for step, fields in cases:
            with self.subTest(step=step):
                config = wrap(in_choose(step))
                self.assertFalse(comparable(config))
                self.assertEqual(
                    automation_verification_detail_code(config),
                    SIMPLE_FIELDS_RULE,
                )
                self.assertEqual(
                    automation_verification_location_codes(config),
                    (
                        "automation_verifier_path_action_0_choose_0_sequence_0",
                        *(f"automation_verifier_field_{name}" for name in fields),
                    ),
                )

    def test_invalid_sun_values_are_refused(self):
        steps = (
            {"condition": "sun", "after": "noon"},
            {"condition": "sun", "after": " sunset"},
            {"condition": "sun", "after": ""},
            {"condition": "sun", "after": None},
            {"condition": "sun", "after": 1},
            {"condition": "sun", "after": ["sunset"]},
            {"condition": "sun", "before": {"event": "sunrise"}},
            {"condition": "sun", "after": "sunset", "after_offset": True},
            {"condition": "sun", "after": "sunset", "after_offset": ""},
            {"condition": "sun", "after": "sunset", "after_offset": None},
            {"condition": "sun", "before": "sunrise", "before_offset": []},
        )
        for step in steps:
            with self.subTest(step=step):
                config = wrap(in_choose(dict(step)))
                self.assertFalse(comparable(config))
                self.assertEqual(automation_verification_detail_code(config), SUN_RULE)
                self.assertEqual(
                    automation_verification_location_codes(config),
                    ("automation_verifier_path_action_0_choose_0_sequence_0",),
                )

    def test_non_string_values_that_render_as_sun_events_are_refused(self):
        class RendersAsSunEvent:
            def __str__(self):
                return "sunset"

        for field in ("after", "before"):
            with self.subTest(field=field):
                config = wrap(in_choose({"condition": "sun", field: RendersAsSunEvent()}))
                self.assertFalse(comparable(config))
                self.assertEqual(automation_verification_detail_code(config), SUN_RULE)

    def test_readback_that_turns_unverifiable_is_a_mismatch(self):
        approved = replica_config()
        observed = deepcopy(approved)
        at(observed, SUN_PATH)["weekday"] = "mon"
        result = compare_resource_verification("automation", approved, observed)
        self.assertFalse(result.semantic_match)


class VerifierLocationTests(unittest.TestCase):
    def test_location_names_each_structural_position(self):
        bad = {"condition": "sun", "weekday": "mon"}
        cases = (
            (wrap([bad]), "action_0"),
            ({"actions": [{"action": "notify.synthetic"}, bad]}, "actions_1"),
            (wrap([{"choose": [], "default": [{"delay": 1}, bad]}]), "action_0_default_1"),
            (wrap([{"if": [], "then": [bad]}]), "action_0_then_0"),
            (wrap([{"if": [], "then": [], "else": [bad]}]), "action_0_else_0"),
            (wrap([{"repeat": {"count": 1, "sequence": [bad]}}]), "action_0_repeat_sequence_0"),
            (wrap([{"parallel": [{"delay": 1}, bad]}]), "action_0_parallel_1"),
            (wrap([{"parallel": [{"sequence": [bad]}]}]), "action_0_parallel_0_sequence_0"),
        )
        for config, path in cases:
            with self.subTest(path=path):
                self.assertEqual(
                    automation_verification_location_codes(config),
                    (
                        "automation_verifier_path_" + path,
                        "automation_verifier_field_weekday",
                    ),
                )

    def test_structural_refusals_name_their_container(self):
        cases = (
            (wrap([{"choose": [{"conditions": [], "sequence": [], "extra": 1}]}]),
             ("automation_verifier_path_action_0_choose_0", "automation_verifier_field_unrecognized_field")),
            (wrap([{"choose": [{"conditions": [], "sequence": [], "default": []}]}]),
             ("automation_verifier_path_action_0_choose_0", "automation_verifier_field_default")),
            (wrap([{"repeat": {"count": 1, "sequence": [], "for_each": []}}]),
             ("automation_verifier_path_action_0_repeat",)),
            (wrap([{"repeat": {"count": 1, "sequence": [], "mode": "x"}}]),
             ("automation_verifier_path_action_0_repeat", "automation_verifier_field_unrecognized_field")),
            (wrap([{"action": "notify.synthetic", "entity_id": "a.b"}]),
             ("automation_verifier_path_action_0", "automation_verifier_field_entity_id")),
            (wrap([{"and": []}]),
             ("automation_verifier_path_action_0", "automation_verifier_field_and")),
            (wrap([{"if": [], "then": [], "default": []}]),
             ("automation_verifier_path_action_0",)),
            (wrap(["not a step"]), ("automation_verifier_path_action_0",)),
            (wrap([{"if": [], "then": "not a sequence"}]), ("automation_verifier_path_action_0_then",)),
        )
        for config, codes in cases:
            with self.subTest(codes=codes):
                self.assertIsNotNone(automation_verification_detail_code(config))
                self.assertEqual(automation_verification_location_codes(config), codes)

    def test_refusal_outside_the_action_tree_has_no_location(self):
        for config in (
            {"action": [], "actions": []},
            {"action": [], 1: "x"},
        ):
            with self.subTest(config=repr(config)):
                self.assertIsNotNone(automation_verification_detail_code(config))
                self.assertEqual(automation_verification_location_codes(config), ())
        self.assertEqual(automation_verification_location_codes(replica_config()), ())

    def test_fields_are_vocabulary_only_and_capped(self):
        step = {
            "condition": "time",
            "after": "x",
            "before": "x",
            "weekday": "x",
            "zone": "x",
            "match": "x",
            "Mixed-Case": VALUE_MARKER,
            VALUE_MARKER: VALUE_MARKER,
        }
        codes = automation_verification_location_codes(wrap([step]))
        self.assertEqual(
            codes,
            (
                "automation_verifier_path_action_0",
                "automation_verifier_field_after",
                "automation_verifier_field_before",
                "automation_verifier_field_match",
                "automation_verifier_field_unrecognized_field",
                normalize.AUTOMATION_VERIFIER_FIELDS_TRUNCATED,
            ),
        )

    def test_deep_path_is_truncated_at_whole_tokens(self):
        many = [{"delay": 1}] * 1000
        inner = [*many, {"condition": "sun", "weekday": "mon"}]
        for _ in range(4):
            inner = [*many, {"choose": [*([{"conditions": [], "sequence": []}] * 1000),
                                        {"conditions": [], "sequence": inner}]}]
        codes = automation_verification_location_codes(wrap(inner))
        path, truncated, field = codes
        self.assertEqual(truncated, normalize.AUTOMATION_VERIFIER_PATH_TRUNCATED)
        self.assertEqual(field, "automation_verifier_field_weekday")
        self.assertLessEqual(len(path), 96)
        self.assertTrue(
            "automation_verifier_path_action_1000_choose_1000_sequence_1000_choose_1000".startswith(path)
            or path.startswith("automation_verifier_path_action_1000_choose_1000_sequence_1000_choose_1000")
        )
        decoded = reasons.verifier_location(tuple(sorted((SUN_RULE, *codes))))
        self.assertTrue(decoded["path_truncated"])
        self.assertTrue(decoded["path"].startswith("/action/1000/choose/1000/"))

    def test_path_encoder_refuses_tokens_outside_the_fixed_vocabulary(self):
        encode = normalize._location_path_codes
        self.assertEqual(encode(("action", 0, "sequence")), ("automation_verifier_path_action_0_sequence",))
        for path in (
            ("action", 0, "bogus"),
            ("action", 0, VALUE_MARKER),
            ("weekday",),
            ("action", True),
            ("action", -1),
            ("action", 0.0),
            ["action", 0],
            (),
            None,
        ):
            with self.subTest(path=path):
                self.assertEqual(encode(path), ())

    def test_codes_are_bounded_and_carry_no_values(self):
        configs = []
        for field in (VALUE_MARKER, "weekday", "x" * 300):
            for step in ({"condition": "sun", field: VALUE_MARKER}, {"action": "a.b", field: VALUE_MARKER},
                         {"device_id": "d", "domain": "x", "type": "t", field: VALUE_MARKER}):
                configs.append(wrap(in_choose(step)))
        configs.append(wrap([{"condition": "sun", "after": VALUE_MARKER}]))
        configs.append(wrap([{"wait_template": "", "alias": VALUE_MARKER}]))
        for config in configs:
            codes = automation_verification_location_codes(config)
            self.assertTrue(codes)
            self.assertLessEqual(len(codes), 7)
            for code in codes:
                self.assertRegex(code, EVIDENCE_PATTERN)
                self.assertNotIn("marker", code)
                self.assertNotIn("xxxx", code)
            path_codes = [c for c in codes if c.startswith(normalize.AUTOMATION_VERIFIER_PATH_PREFIX)]
            self.assertEqual(len(path_codes), 1)
            tokens = path_codes[0][len(normalize.AUTOMATION_VERIFIER_PATH_PREFIX):].split("_")
            for token in tokens:
                self.assertTrue(
                    token in normalize.AUTOMATION_VERIFIER_PATH_TOKENS or token.isdigit(),
                    token,
                )

    def test_location_is_reporting_only(self):
        """The location never changes an accept/refuse decision or a rule."""
        config = wrap(in_choose({"condition": "time", "after": "22:00:00"}))
        original = normalize.AutomationVerificationNormalizationError.locate
        try:
            normalize.AutomationVerificationNormalizationError.locate = lambda self, path: None
            self.assertEqual(automation_verification_detail_code(config), SIMPLE_FIELDS_RULE)
            self.assertFalse(comparable(config))
        finally:
            normalize.AutomationVerificationNormalizationError.locate = original


class VerifierLocationReasonTests(unittest.TestCase):
    def test_vocabulary_is_pinned_to_the_normalizer(self):
        self.assertEqual(reasons.VERIFIER_KNOWN_FIELDS, normalize.AUTOMATION_VERIFIER_KNOWN_FIELDS)
        self.assertEqual(reasons.VERIFIER_PATH_TOKENS, normalize.AUTOMATION_VERIFIER_PATH_TOKENS)
        self.assertEqual(reasons.VERIFIER_PATH_PREFIX, normalize.AUTOMATION_VERIFIER_PATH_PREFIX)
        self.assertEqual(reasons.VERIFIER_FIELD_PREFIX, normalize.AUTOMATION_VERIFIER_FIELD_PREFIX)
        self.assertEqual(reasons.VERIFIER_PATH_TRUNCATED, normalize.AUTOMATION_VERIFIER_PATH_TRUNCATED)
        self.assertEqual(reasons.VERIFIER_FIELDS_TRUNCATED, normalize.AUTOMATION_VERIFIER_FIELDS_TRUNCATED)
        self.assertEqual(reasons.UNRECOGNIZED_FIELD, normalize.AUTOMATION_VERIFIER_UNRECOGNIZED_FIELD)
        self.assertEqual(reasons.MAX_LOCATION_FIELDS, normalize.MAX_AUTOMATION_VERIFIER_LOCATION_FIELDS)
        for name in reasons.VERIFIER_KNOWN_FIELDS | {reasons.UNRECOGNIZED_FIELD}:
            self.assertRegex(reasons.VERIFIER_FIELD_PREFIX + name, reasons.EVIDENCE_PATTERN)
        self.assertIn(SUN_RULE, reasons.AUTOMATION_VERIFIER_DETAILS)
        for code in (
            normalize.AUTOMATION_VERIFIER_PATH_TRUNCATED,
            normalize.AUTOMATION_VERIFIER_FIELDS_TRUNCATED,
            normalize.AUTOMATION_VERIFIER_PATH_PREFIX + "action_0",
            normalize.AUTOMATION_VERIFIER_FIELD_PREFIX + "after",
        ):
            self.assertNotIn(code, reasons.AUTOMATION_VERIFIER_DETAILS)

    def reason(self, codes):
        return reasons.build_reason(
            codes=tuple(sorted(codes)),
            declaration={"operation_id": "op", "target_type": "automation", "target_id": "porch_light"},
            normalized_outcome="preflight_rejected",
        )

    def test_reason_carries_the_decoded_location(self):
        config = wrap(in_choose({**SUN_STEP, "weekday": "mon", VALUE_MARKER: VALUE_MARKER}))
        codes = (
            "configuration_not_verifiable",
            "automation_verification_structure",
            automation_verification_detail_code(config),
            *automation_verification_location_codes(config),
        )
        reason = self.reason(codes)
        self.assertEqual(sorted(reason), sorted(reasons.REASON_FIELDS))
        self.assertEqual(reason["check"], "readback_verifiability")
        self.assertEqual(reason["verifier_rule"], SIMPLE_FIELDS_RULE)
        self.assertEqual(
            reason["verifier_location"],
            {
                "path": "/action/0/choose/0/sequence/0",
                "path_truncated": False,
                "fields": ["unrecognized_field", "weekday"],
                "fields_truncated": False,
            },
        )
        self.assertNotIn(VALUE_MARKER, json.dumps(reason))
        self.assertEqual(reasons.recorded(reason), reason)
        self.assertEqual(reasons.log_reason(reason), reason)

    def test_decoder_refuses_to_invent_a_location(self):
        rule = SIMPLE_FIELDS_RULE
        cases = (
            ((rule,), None),
            (("configuration_not_verifiable",), None),
            # Location codes without a named verifier rule are not decoded.
            (("configuration_not_verifiable", "automation_verifier_path_action_0"), None),
            ((rule, "automation_verifier_path_action_0_secret"), None),
            ((rule, "automation_verifier_path_choose_0"), None),
            ((rule, "automation_verifier_path_action_01"), None),
            ((rule, "automation_verifier_path_action_0_action_1"), None),
            ((rule, "automation_verifier_path_action_0", "automation_verifier_path_action_1"), None),
            ((rule, "automation_verifier_field_secret_thing"),
             {"path": None, "path_truncated": False, "fields": ["unrecognized_field"], "fields_truncated": False}),
            ((rule, "automation_verifier_path_action_0", "automation_verifier_truncated_path"),
             {"path": "/action/0", "path_truncated": True, "fields": [], "fields_truncated": False}),
            ((rule, "automation_verifier_path_actions_0", "automation_verifier_truncated_fields",
              *(f"automation_verifier_field_{n}" for n in ("after", "before", "match", "weekday", "zone"))),
             {"path": "/actions/0", "path_truncated": False,
              "fields": ["after", "before", "match", "weekday"], "fields_truncated": True}),
        )
        for codes, expected in cases:
            with self.subTest(codes=codes):
                self.assertEqual(self.reason(codes)["verifier_location"], expected)

    def test_legacy_and_non_verifier_reasons_have_no_location(self):
        self.assertIsNone(reasons.unknown_legacy()["verifier_location"])
        self.assertIsNone(reasons.legacy_f1_reason("validation_failed", None)["verifier_location"])
        self.assertIsNone(self.reason(("stale_target_state",))["verifier_location"])
        stored = self.reason((SIMPLE_FIELDS_RULE, "configuration_not_verifiable"))
        stored.pop("verifier_location")
        self.assertIsNone(reasons.recorded(stored)["verifier_location"])


class SunConditionDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, proposed, *, action="update", mutate=None, duplicate=False):
        current = wrap([{"action": "notify.synthetic"}]) if action == "update" else None
        proposal = proposal_for("automation", action, current_config=current, proposed_config=proposed)
        states = {("automation", "porch_light"): current} if current else {}
        gateway = SyntheticConfigurationGateway(states)
        if mutate:
            gateway.after_write_hook = lambda gateway, kind, target: mutate(gateway.states[(kind, target)])
        adapter = adapter_for("automation", action, gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(prepared, acquired_locks=adapter.lock_requests(prepared))
        child = single_operation_child_descriptor(prepare_configuration_sequence((prepared,)))
        identity = ExecutionIdentity(task_id=child.public_task_id, plan_id=child.plan_id, attempt_id=child.attempt_id, request_id="synthetic-hamcp252-request", owner_id="synthetic-hamcp252-owner")
        approval = SyntheticApprovalRecorder()
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = SharedOperationExecutor(lock_store=DurableLockStore(root), execution_repository=DurableExecutionRepository(root), lock_timing=LOCK_TIMING, executor_timing=EXECUTOR_TIMING, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)
            first = await executor.execute(adapter=adapter, prepared=prepared, identity=identity, approval_consumption=approval)
            second = await executor.execute(adapter=adapter, prepared=prepared, identity=identity, approval_consumption=approval) if duplicate else None
            self.record = executor.execution_repository.get(identity.task_id)
        return preflight, first, second, gateway, approval

    async def test_replica_preflights_and_verifies_exact_readback(self):
        for action in ("create", "update"):
            with self.subTest(action=action):
                preflight, first, second, gateway, approval = await self._run(replica_config(), action=action, duplicate=True)
                self.assertTrue(preflight.eligible)
                self.assertEqual(first.outcome, "succeeded_verified")
                self.assertEqual(second.outcome, "succeeded_verified")
                self.assertTrue(second.duplicate_execution)
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(approval.consumptions, 1)
                self.assertEqual(at(gateway.states[("automation", "porch_light")], SUN_PATH), SUN_STEP)

    async def test_altered_or_missing_readback_is_a_mismatch(self):
        edits = {
            "altered": lambda config: at(config, SUN_PATH).update(after="sunrise"),
            "missing": lambda config: at(config, SUN_PATH).pop("before"),
            "dropped": lambda config: at(config, SUN_PATH[:-1]).pop(2),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                _, first, second, gateway, approval = await self._run(replica_config(), mutate=edit, duplicate=True)
                self.assertEqual(first.outcome, "verification_mismatch")
                self.assertEqual(second.outcome, "verification_mismatch")
                self.assertTrue(second.duplicate_execution)
                self.assertEqual(gateway.counters.dispatches, 1)
                self.assertEqual(approval.consumptions, 1)

    async def test_unsupported_neighbor_refuses_before_approval_with_location(self):
        config = replica_config()
        at(config, SUN_PATH)["weekday"] = "mon"
        at(config, SUN_PATH)[VALUE_MARKER] = VALUE_MARKER
        preflight, first, _, gateway, approval = await self._run(config)
        self.assertFalse(preflight.eligible)
        self.assertEqual(
            preflight.diagnostic_codes,
            tuple(sorted((
                "automation_verification_structure",
                "automation_verifier_field_unrecognized_field",
                "automation_verifier_field_weekday",
                "automation_verifier_path_action_0_choose_2_sequence_2",
                SIMPLE_FIELDS_RULE,
                "configuration_not_verifiable",
            ))),
        )
        self.assertEqual(first.outcome, "preflight_rejected")
        self.assertNotIn(VALUE_MARKER, str(first))
        self.assertNotIn("synthetic-hamcp252", str(preflight))
        self.assertEqual(approval.consumptions, 0)
        self.assertEqual(gateway.counters.dispatches, 0)
        self.assertEqual(gateway.counters.simulated_mutations, 0)
        # The reason is derived from the durable child terminal event only.
        reason = reasons.child_reason({"operation_id": "op"}, self.record)
        self.assertEqual(reason["verifier_rule"], SIMPLE_FIELDS_RULE)
        self.assertNotIn(VALUE_MARKER, json.dumps(reason))
        self.assertEqual(reason["verifier_location"]["path"], "/action/0/choose/2/sequence/2")
        self.assertEqual(reason["verifier_location"]["fields"], ["unrecognized_field", "weekday"])

    async def test_worst_case_location_never_displaces_the_refusal_codes(self):
        # Truncated path, four fields and the fields marker: at most ten codes,
        # under the sixteen-code bound, so the refusal and rule always survive.
        many = [{"delay": 1}] * 1000
        inner = [*many, {"condition": "time", "after": "x", "before": "x", "weekday": "x", "zone": "x", "match": "x"}]
        for _ in range(4):
            inner = [*many, {"choose": [*([{"conditions": [], "sequence": []}] * 1000), {"conditions": [], "sequence": inner}]}]
        preflight, first, _, gateway, approval = await self._run(wrap(inner))
        self.assertFalse(preflight.eligible)
        codes = preflight.diagnostic_codes
        self.assertEqual(len(codes), 10)
        for code in (
            "configuration_not_verifiable",
            "automation_verification_structure",
            SIMPLE_FIELDS_RULE,
            normalize.AUTOMATION_VERIFIER_PATH_TRUNCATED,
            normalize.AUTOMATION_VERIFIER_FIELDS_TRUNCATED,
        ):
            self.assertIn(code, codes)
        self.assertEqual(first.outcome, "preflight_rejected")
        self.assertEqual(approval.consumptions, 0)
        self.assertEqual(gateway.counters.dispatches, 0)
        reason = reasons.child_reason({"operation_id": "op"}, self.record)
        self.assertEqual(reason["error_code"], "configuration_not_verifiable")
        self.assertTrue(reason["verifier_location"]["path_truncated"])
        self.assertTrue(reason["verifier_location"]["fields_truncated"])


if __name__ == "__main__":
    unittest.main()
