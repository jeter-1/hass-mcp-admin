"""Beta 22 complete, authoritative approval-review regression coverage."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.approval_web import (  # noqa: E402
    MAX_HTML_BYTES,
    _render_review,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.migration import (  # noqa: E402
    proposal_from_configuration_operation,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    evaluate_change_policy,
)
from ha_mcp_engineering.governance.semantic_projection import (  # noqa: E402
    MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION,
    SEMANTIC_PROJECTION_SCHEMA_VERSION,
    SemanticProjectionError,
    build_semantic_projection,
    canonical_projection_bytes,
)

from tests.test_dev14_configuration_plans import (  # noqa: E402
    CURRENT_AUTOMATION,
    CURRENT_SCRIPT,
    PROPOSED_AUTOMATION,
    PROPOSED_SCRIPT,
    ConfigurationPlanTestCase,
    hvac_operations,
)
from tests.test_f3_configuration_identity import (  # noqa: E402
    SyntheticConfigurationGateway,
)


def helper_create_operation(index: int) -> dict[str, object]:
    return {
        "operation_id": f"create_beta22_helper_{index}",
        "resource_type": "helper",
        "helper_type": "input_boolean",
        "action": "create",
        "target_id": f"input_boolean.beta22_helper_{index}",
        "depends_on": (
            []
            if index == 0
            else [f"create_beta22_helper_{index - 1}"]
        ),
        "proposed_config": {
            "name": f"Beta22 helper {index}",
            "icon": "mdi:check-circle-outline",
            "initial": bool(index % 2),
        },
    }


class Beta22ReviewabilityTests(ConfigurationPlanTestCase):
    async def _create_script(self, proposed=None, *, suffix="one"):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["operation_id"] = f"update_script_{suffix}"
        operation["depends_on"] = []
        if proposed is not None:
            operation["proposed_config"] = copy.deepcopy(proposed)
        return await self.service.create_configuration_plan(
            title=f"Beta 22 reviewability {suffix}",
            description="Complete authoritative projection fixture",
            operations=[operation],
        )

    async def test_single_operation_helper_update_is_completely_reviewable(self):
        current = {
            "id": "beta22_level",
            "name": "Beta22 level",
            "min": 0,
            "max": 10,
            "step": 1,
            "mode": "slider",
            "initial": 2,
        }
        self.gateway.configs[("input_number", "input_number.beta22_level")] = (
            copy.deepcopy(current)
        )
        proposed = copy.deepcopy(current)
        proposed.update({"max": 20, "step": 0.5, "initial": 4})
        proposed.pop("id")
        created = await self.service.create_configuration_plan(
            title="Update one helper",
            description="Single-operation helper review",
            operations=[
                {
                    "operation_id": "update_beta22_level",
                    "resource_type": "helper",
                    "helper_type": "input_number",
                    "action": "update",
                    "target_id": "input_number.beta22_level",
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )

        operation = created["operations"][0]
        projection = operation["semantic_projection"]
        self.assertTrue(projection["projection_complete"])
        self.assertEqual(
            projection["projection_schema_version"],
            SEMANTIC_PROJECTION_SCHEMA_VERSION,
        )
        self.assertEqual(
            [item["path"] for item in projection["changes"]],
            ["/initial", "/max", "/step"],
        )
        self.assertEqual(
            projection["binding"]["projection_hash"],
            operation["semantic_projection_hash"],
        )

    async def test_eight_operations_and_more_than_eight_changes_render_in_order(self):
        created = await self.service.create_configuration_plan(
            title="Eight ordered operations",
            description="Maximum supported operation count",
            operations=[helper_create_operation(index) for index in range(8)],
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        self.gateway.calls.clear()
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        html = _render_review("", review, csrf)

        self.assertEqual(review["operation_count"], 8)
        self.assertEqual(
            [item["order"] for item in review["operation_summaries"]],
            list(range(8)),
        )
        self.assertGreater(
            sum(
                len(item["semantic_projection"]["changes"])
                for item in review["operation_summaries"]
            ),
            8,
        )
        positions = [
            html.index(f"create_beta22_helper_{index}")
            for index in range(8)
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertLessEqual(len(html.encode("utf-8")), MAX_HTML_BYTES)
        self.assertEqual(self.gateway.calls, [])

    async def test_long_automation_and_script_values_are_not_clipped(self):
        long_template = "{{ " + ("states('sensor.beta22') ~ " * 40) + "'done' }}"
        long_scalar = "beta22-long-non-sensitive-" + ("x" * 900)
        script = copy.deepcopy(PROPOSED_SCRIPT)
        script["description"] = long_scalar
        script["sequence"] = [
            {
                "service": "notify.beta22",
                "data": {"message": long_template},
            }
        ]
        automation = copy.deepcopy(PROPOSED_AUTOMATION)
        automation["description"] = long_scalar
        automation["trigger"] = [
            {
                "platform": "template",
                "value_template": long_template,
            }
        ]
        automation["condition"] = [
            {
                "condition": "and",
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": long_template,
                    }
                ],
            }
        ]
        automation["action"] = [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "template",
                                "value_template": long_template,
                            }
                        ],
                        "sequence": [
                            {
                                "service": "notify.beta22",
                                "data": {"message": long_scalar},
                            }
                        ],
                    }
                ]
            }
        ]
        operations = [
            {
                "operation_id": "long_script",
                "resource_type": "script",
                "action": "update",
                "target_id": "set_hvac_comfort",
                "depends_on": [],
                "proposed_config": script,
            },
            {
                "operation_id": "long_automation",
                "resource_type": "automation",
                "action": "update",
                "target_id": "apply_hvac_comfort",
                "depends_on": ["long_script"],
                "proposed_config": automation,
            },
        ]
        created = await self.service.create_configuration_plan(
            title="Long script and automation",
            description="Values longer than the old 200-character boundary",
            operations=operations,
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        html = _render_review("", review, csrf)
        encoded = json.dumps(review, sort_keys=True)

        self.assertIn(long_template, encoded)
        self.assertIn(long_scalar, encoded)
        self.assertNotIn("...<truncated>", encoded)
        self.assertIn("Inspect complete value", html)
        self.assertTrue(
            all(
                item["semantic_projection"]["projection_complete"]
                for item in review["operation_summaries"]
            )
        )

    async def test_projection_is_deterministic_for_identical_prepared_input(self):
        created = await self._create_script()
        operation = self.repository.get(created["plan_id"]).operations[0]
        classification = evaluate_change_policy(
            self.repository.get(created["plan_id"])
        )
        first = build_semantic_projection(
            operation,
            policy_class=classification.policy_class.value,
            physical_impact=classification.physical_consequence.value,
            known_secrets=self.service.sensitive_values,
        )
        second = build_semantic_projection(
            copy.deepcopy(operation),
            policy_class=classification.policy_class.value,
            physical_impact=classification.physical_consequence.value,
            known_secrets=self.service.sensitive_values,
        )
        self.assertEqual(first, second)

    async def test_render_is_stable_and_requires_no_mutable_state_query(self):
        created = await self.service.create_configuration_plan(
            title="Eight-operation render measurement",
            description="Persist once and render repeatedly",
            operations=[helper_create_operation(index) for index in range(8)],
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        self.gateway.calls.clear()
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        started = time.perf_counter()
        rendered = [_render_review("", review, csrf) for _ in range(10)]
        elapsed = time.perf_counter() - started

        self.assertTrue(all(value == rendered[0] for value in rendered))
        self.assertLessEqual(len(rendered[0].encode("utf-8")), MAX_HTML_BYTES)
        self.assertEqual(self.gateway.calls, [])
        self.assertGreaterEqual(elapsed, 0.0)

    async def test_html_json_yaml_and_template_metacharacters_are_escaped(self):
        payload = '<script>alert("beta22")</script> & {yaml: [json]}'
        proposed = copy.deepcopy(PROPOSED_SCRIPT)
        proposed["alias"] = payload
        proposed["sequence"] = [
            {
                "service": "notify.beta22",
                "data": {"message": "{{ " + payload + " }}"},
            }
        ]
        created = await self._create_script(proposed, suffix="escaping")
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        html = _render_review("", review, csrf)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("{yaml: [json]}", html)
        self.assertIn("Approve exact plan", html)

    async def test_f3_prepared_hash_is_transitively_bound_to_projection_hash(self):
        created = await self._create_script(suffix="f3-binding")
        plan = self.repository.get(created["plan_id"])
        operation = plan.operations[0]
        plan_hash = self.service.plan_hash(plan)
        proposal = proposal_from_configuration_operation(
            plan,
            operation,
            task_id="beta22-task",
            plan_hash=plan_hash,
            approval_bundle_hash="a" * 64,
            provider_admitted=True,
            policy_snapshot_valid=True,
        )
        adapter = ConfigurationOperationAdapter(
            "script",
            "update",
            SyntheticConfigurationGateway(),
        )
        prepared = await adapter.prepare(proposal)

        altered = copy.deepcopy(plan)
        altered.operations[0].semantic_projection_hash = "f" * 64
        altered_plan_hash = self.service.plan_hash(altered)
        altered_proposal = proposal_from_configuration_operation(
            altered,
            altered.operations[0],
            task_id="beta22-task",
            plan_hash=altered_plan_hash,
            approval_bundle_hash="a" * 64,
            provider_admitted=True,
            policy_snapshot_valid=True,
        )
        altered_prepared = await adapter.prepare(altered_proposal)

        self.assertNotEqual(plan_hash, altered_plan_hash)
        self.assertNotEqual(
            prepared.prepared_operation_hash,
            altered_prepared.prepared_operation_hash,
        )


class Beta22CreationRejectionTests(ConfigurationPlanTestCase):
    def assert_no_external_or_execution_effects(self):
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.service.task_repository.list(), [])
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"],
            [],
        )
        self.assertFalse(self.audit_path.exists())

    async def test_projection_serialization_failure_rejects_before_persistence(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        with patch(
            "ha_mcp_engineering.governance.service.build_semantic_projection",
            side_effect=SemanticProjectionError(
                "projection_serialization_failed"
            ),
        ):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.create_configuration_plan(
                    title="Serialization failure",
                    description="No approval artifact may survive",
                    operations=[operation],
                )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PROJECTION_UNREVIEWABLE,
        )
        self.assertEqual(
            raised.exception.details["projection_error"],
            "projection_serialization_failed",
        )
        self.assert_no_external_or_execution_effects()

    async def test_projection_marked_incomplete_rejects_at_creation(self):
        original = build_semantic_projection
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []

        def incomplete(*args, **kwargs):
            projection, digest = original(*args, **kwargs)
            projection["projection_complete"] = False
            return projection, digest

        with patch(
            "ha_mcp_engineering.governance.service.build_semantic_projection",
            side_effect=incomplete,
        ):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.create_configuration_plan(
                    title="Incomplete projection",
                    description="Creation gate fixture",
                    operations=[operation],
                )
        self.assertEqual(
            raised.exception.details["projection_error"],
            "projection_incomplete",
        )
        self.assert_no_external_or_execution_effects()

    async def test_nondeterministic_projection_input_rejects_at_creation(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        operation["proposed_config"]["sequence"][0]["data"][
            "nondeterministic"
        ] = {"unordered", "set"}
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Nondeterministic input",
                description="Non-JSON structures are not reviewable",
                operations=[operation],
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PROJECTION_UNREVIEWABLE,
        )
        self.assertEqual(
            raised.exception.details["projection_error"],
            "projection_input_nondeterministic",
        )
        self.assert_no_external_or_execution_effects()

    async def test_genuine_projection_product_limit_rejects_at_creation(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        operation["proposed_config"]["sequence"] = [
            {
                "service": "notify.beta22",
                "data": {
                    "message": "x"
                    * (MAX_SEMANTIC_PROJECTION_BYTES_PER_OPERATION + 1)
                },
            }
        ]
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Projection product boundary",
                description="Genuine canonical byte limit",
                operations=[operation],
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PROJECTION_UNREVIEWABLE,
        )
        self.assertEqual(
            raised.exception.details["projection_error"],
            "projection_size_limit_exceeded",
        )
        self.assert_no_external_or_execution_effects()

    async def test_missing_before_state_and_malformed_operation_create_nothing(self):
        cases = (
            {
                "operation_id": "missing_before",
                "resource_type": "script",
                "action": "update",
                "target_id": "does_not_exist",
                "depends_on": [],
                "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
            },
            {
                "operation_id": "malformed",
                "resource_type": "script",
                "action": "update",
                "depends_on": [],
                "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
            },
        )
        for operation in cases:
            with self.subTest(operation=operation["operation_id"]):
                self.gateway.calls.clear()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="Invalid operation",
                        description="No approval or execution artifact",
                        operations=[operation],
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                )
                self.assert_no_external_or_execution_effects()


class Beta22TamperAndHistoricalTests(ConfigurationPlanTestCase):
    def _strip_projection_and_rebind_historical_policy(self, plan):
        for operation in plan.operations:
            operation.semantic_projection = None
            operation.semantic_projection_hash = None
        plan.policy_decision = evaluate_change_policy(plan)
        self.service._bind_new_plan_policy(plan)
        return plan

    async def test_authoritative_material_tampering_fails_closed(self):
        mutations = {
            "target": lambda plan: setattr(
                plan.operations[0], "target_id", "different_target"
            ),
            "operation_order": lambda plan: setattr(
                plan.operations[0], "order", 7
            ),
            "changed_path": lambda plan: plan.operations[0]
            .semantic_projection["changes"][0]
            .__setitem__("path", "/tampered"),
            "before_value": lambda plan: plan.operations[0]
            .semantic_projection["changes"][0]
            .__setitem__("before", {"state": "value", "value": "tampered"}),
            "after_value": lambda plan: plan.operations[0]
            .semantic_projection["changes"][0]
            .__setitem__("after", {"state": "value", "value": "tampered"}),
            "schema_version": lambda plan: plan.operations[0]
            .semantic_projection.__setitem__("projection_schema_version", 99),
            "complete_flag": lambda plan: plan.operations[0]
            .semantic_projection.__setitem__("projection_complete", False),
            "prepared_configuration": lambda plan: plan.operations[0]
            .proposed_config.__setitem__("description", "tampered"),
            "projection_hash": lambda plan: setattr(
                plan.operations[0], "semantic_projection_hash", "0" * 64
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(material=label):
                created = await self.service.create_configuration_plan(
                    title=f"Tamper {label}",
                    description="All authority changes fail closed",
                    operations=[
                        {
                            **copy.deepcopy(hvac_operations()[1]),
                            "depends_on": [],
                        }
                    ],
                )
                plan = self.repository.get(created["plan_id"])
                mutate(plan)
                self.repository.save(plan)
                self.gateway.calls.clear()

                with self.assertRaises(GovernanceError):
                    self.service.approve(
                        created["plan_id"], created["plan_hash"]
                    )
                self.assertEqual(self.service.task_repository.list(), [])
                self.assertEqual(self.gateway.calls, [])

    async def test_projection_tampering_after_approval_cannot_reach_dispatch(self):
        created = await self.service.create_configuration_plan(
            title="Post-approval tamper",
            description="Mutation after approval must fail closed",
            operations=[helper_create_operation(0)],
        )
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.operations[0].semantic_projection["changes"][0]["path"] = (
            "/tampered"
        )
        self.repository.save(plan)
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PROJECTION_UNREVIEWABLE,
        )
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.service.task_repository.list(), [])

    async def test_historical_projection_variants_are_nonapprovable(self):
        variants = ("missing", "truncated", "unbound", "malformed")
        for index, variant in enumerate(variants):
            with self.subTest(variant=variant):
                created = await self.service.create_configuration_plan(
                    title=f"Historical {variant}",
                    description="Beta 21 compatibility behavior",
                    operations=[
                        {
                            **helper_create_operation(index),
                            "depends_on": [],
                        }
                    ],
                )
                plan = self.repository.get(created["plan_id"])
                if variant == "missing":
                    plan = self._strip_projection_and_rebind_historical_policy(
                        plan
                    )
                elif variant == "truncated":
                    plan.operations[0].semantic_projection = {
                        "status": "incomplete",
                        "changes": [],
                    }
                    plan.operations[0].semantic_projection_hash = None
                    plan.policy_decision = evaluate_change_policy(plan)
                    self.service._bind_new_plan_policy(plan)
                elif variant == "unbound":
                    plan.operations[0].semantic_projection["binding"].pop(
                        "projection_hash"
                    )
                else:
                    plan.operations[0].semantic_projection = "malformed"
                self.repository.save(plan)
                self.gateway.calls.clear()

                with self.assertRaises(GovernanceError):
                    self.service.approve(
                        created["plan_id"],
                        self.service.plan_hash(plan),
                    )
                self.assertEqual(self.gateway.calls, [])
                self.assertEqual(self.service.task_repository.list(), [])

    async def test_historical_completed_record_remains_readable_without_redispatch(self):
        created = await self.service.create_configuration_plan(
            title="Historical completed record",
            description="Audit readable, execution terminal",
            operations=[helper_create_operation(0)],
        )
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        for operation in plan.operations:
            operation.semantic_projection = None
            operation.semantic_projection_hash = None
        plan.policy_decision = evaluate_change_policy(plan)
        plan.approval.policy_decision_hash = (
            plan.policy_decision.policy_decision_hash
        )
        plan.approval.policy_class = plan.policy_decision.policy_class.value
        plan.status = PlanStatus.APPLIED
        plan.approval.state = ApprovalState.CONSUMED
        plan.approval.bundle_state = "consumed"
        plan.approval.consumed_at = "2026-07-23T12:00:00+00:00"
        plan.applied_at = "2026-07-23T12:00:00+00:00"
        historical_hash = self.service.plan_hash(plan)
        plan.approval.bound_plan_hash = historical_hash
        self.repository.save(plan)
        self.gateway.calls.clear()

        readable = self.service.get_plan(created["plan_id"])
        self.assertEqual(readable["status"], "applied")
        self.assertNotIn(
            "semantic_projection",
            readable["operations"][0],
        )
        try:
            await self.service.apply(created["plan_id"], historical_hash)
        except GovernanceError as exc:
            self.assertIn(
                exc.code,
                {
                    ErrorCode.CONFIGURATION_PROJECTION_UNREVIEWABLE,
                    ErrorCode.EXTERNAL_APPROVAL_REQUIRED,
                    ErrorCode.APPROVAL_ALREADY_CONSUMED,
                },
            )
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"],
            [],
        )

    def test_non_json_projection_serialization_is_stably_rejected(self):
        with self.assertRaises(SemanticProjectionError) as raised:
            canonical_projection_bytes({"unsupported": {"set"}})
        self.assertEqual(
            raised.exception.reason,
            "projection_serialization_failed",
        )


class Beta22ObservationEvidenceTests(unittest.TestCase):
    def test_ha_set_integration_schema_diff_is_exact_and_reproducible(self):
        evidence_path = (
            ROOT
            / "docs"
            / "evidence"
            / "upstream-read-compatibility"
            / "ha-set-integration-8.0.0-to-8.1.0-input-schema.json"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        observed_descriptions = []
        for version in ("8.0.0", "8.1.0"):
            source = evidence["sources"][version]
            capture_path = ROOT / source["capture_path"]
            capture_bytes = capture_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(capture_bytes).hexdigest(),
                source["capture_sha256"],
            )
            capture = json.loads(capture_bytes)
            tool = next(
                item
                for item in capture["tools"]
                if item["name"] == evidence["tool"]
            )
            schema = tool["inputSchema"]
            canonical = json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.assertEqual(schema, source["canonical_input_schema"])
            self.assertEqual(
                hashlib.sha256(canonical).hexdigest(),
                source["input_schema_sha256"],
            )
            observed_descriptions.append(
                schema["properties"]["config"]["description"]
            )
        self.assertEqual(
            evidence["diff"],
            [
                {
                    "after": observed_descriptions[1],
                    "before": observed_descriptions[0],
                    "change": "replace",
                    "json_pointer": "/properties/config/description",
                }
            ],
        )
        self.assertEqual(
            evidence["sources"]["8.1.0"]["registry_input_schema_sha256"],
            evidence["sources"]["8.1.0"]["input_schema_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
