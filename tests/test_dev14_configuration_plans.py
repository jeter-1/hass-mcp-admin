import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.approval_web import _render_review  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantApiError,
)
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
    StepExecutionStatus,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationMutationCompletedUnexpectedlyError,
    ConfigurationMutationNotDispatchedError,
    normalize_resource_config,
    resource_fingerprint,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
    _configuration_approval_projection,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.tools.governance import (  # noqa: E402
    create_configuration_plan as create_configuration_plan_tool,
)


CURRENT_SCRIPT = {
    "id": "set_hvac_comfort",
    "alias": "Set HVAC comfort",
    "description": "Original script",
    "mode": "single",
    "sequence": [
        {
            "service": "climate.set_temperature",
            "target": {"entity_id": "climate.downstairs"},
            "data": {"temperature": 21},
        }
    ],
}
PROPOSED_SCRIPT = {
    "alias": "Set HVAC comfort",
    "description": "Use the governed target helper",
    "mode": "single",
    "sequence": [
        {
            "service": "climate.set_temperature",
            "target": {"entity_id": "climate.downstairs"},
            "data": {
                "temperature": "{{ states('input_number.hvac_target') | float }}"
            },
        }
    ],
}
CURRENT_AUTOMATION = {
    "id": "apply_hvac_comfort",
    "alias": "Apply HVAC comfort",
    "description": "Original automation",
    "mode": "single",
    "trigger": [
        {
            "platform": "state",
            "entity_id": "input_boolean.hvac_override",
            "to": "on",
        }
    ],
    "condition": [],
    "action": [{"service": "script.set_hvac_comfort"}],
}
PROPOSED_AUTOMATION = {
    "alias": "Apply HVAC comfort",
    "description": "Apply the governed helper-backed comfort script",
    "mode": "single",
    "trigger": [
        {
            "platform": "state",
            "entity_id": "input_boolean.hvac_override",
            "to": "on",
        }
    ],
    "condition": [],
    "action": [{"service": "script.set_hvac_comfort"}],
}
PROPOSED_HELPER = {
    "name": "HVAC target",
    "min": 16,
    "max": 30,
    "step": 0.5,
    "mode": "slider",
    "unit_of_measurement": "degC",
}


def hvac_operations():
    return [
        {
            "operation_id": "create_target_helper",
            "resource_type": "helper",
            "helper_type": "input_number",
            "action": "create",
            "target_id": "input_number.hvac_target",
            "depends_on": [],
            "proposed_config": copy.deepcopy(PROPOSED_HELPER),
        },
        {
            "operation_id": "update_comfort_script",
            "resource_type": "script",
            "action": "update",
            "target_id": "set_hvac_comfort",
            "depends_on": ["create_target_helper"],
            "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
        },
        {
            "operation_id": "update_comfort_automation",
            "resource_type": "automation",
            "action": "update",
            "target_id": "apply_hvac_comfort",
            "depends_on": ["update_comfort_script"],
            "proposed_config": copy.deepcopy(PROPOSED_AUTOMATION),
        },
    ]


class FakeConfigurationGateway:
    def __init__(self):
        self.configs = {
            ("script", "set_hvac_comfort"): copy.deepcopy(CURRENT_SCRIPT),
            (
                "automation",
                "apply_hvac_comfort",
            ): copy.deepcopy(CURRENT_AUTOMATION),
        }
        self.calls = []
        self.read_counts = {}
        self.replace_on_read_count = {}
        self.fail_write_target = None
        self.fail_write_exception = None
        self.mismatch_after_write_target = None
        self.validation_result = {"result": "valid", "errors": None}

    async def read(self, resource_type, resource_id):
        self.calls.append(("read", resource_type, resource_id))
        target = (resource_type, resource_id)
        self.read_counts[target] = self.read_counts.get(target, 0) + 1
        replacement = self.replace_on_read_count.get(
            (target, self.read_counts[target])
        )
        if replacement is not None:
            self.configs[target] = copy.deepcopy(replacement)
        return copy.deepcopy(self.configs.get(target))

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        self.calls.append(
            ("write", action, resource_type, resource_id)
        )
        if (resource_type, resource_id) == self.fail_write_target:
            if self.fail_write_exception is not None:
                raise self.fail_write_exception
            raise RuntimeError("deterministic fake write failure")
        stored = copy.deepcopy(approved_config)
        if resource_type in {"input_boolean", "input_number"}:
            stored["id"] = resource_id.split(".", 1)[1]
        else:
            stored["id"] = resource_id
        if (
            resource_type,
            resource_id,
        ) == self.mismatch_after_write_target:
            stored["id"] = "unexpected_identity"
        self.configs[(resource_type, resource_id)] = stored
        return {"result": "ok"}

    async def validate(self):
        self.calls.append(("validate",))
        return copy.deepcopy(self.validation_result)

    async def validate_all(self):
        self.calls.append(("validate_all",))
        return copy.deepcopy(self.validation_result)


class ConfigurationPlanTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.gateway = FakeConfigurationGateway()
        self.repository = ChangePlanRepository(self.root / "plans")
        self.audit_path = self.root / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=lambda: datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
            sensitive_values=(
                "dev14-test-access-secret",
                "dev14-test-ha-token",
            ),
        )
        self.telemetry, self.context = begin_request(
            "dev14-configuration-plan-request"
        )
        self.telemetry.caller_id = "dev14-mcp-caller"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def create_hvac_plan(self):
        return await self.service.create_configuration_plan(
            title="Governed HVAC helper, script, and automation",
            description="Repository-local Dev14 acceptance fixture",
            operations=hvac_operations(),
            caller_context={"acceptance_case": "hvac"},
        )

    def persisted_artifact_text(self):
        paths = [
            path
            for path in (self.root / "plans").rglob("*")
            if path.is_file()
        ]
        if self.audit_path.exists():
            paths.append(self.audit_path)
        return "\n".join(
            path.read_text(encoding="utf-8") for path in paths
        )

    def assert_secret_free_storage_error(self, raised, raw_secret):
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CHANGE_PLAN_STORAGE_ERROR,
        )
        exposed = (
            str(raised.exception)
            + "\n"
            + repr(raised.exception)
            + "\n"
            + json.dumps(raised.exception.details, sort_keys=True)
        )
        self.assertNotIn(raw_secret, exposed)

    async def assert_corrupt_v2_plan_refused_everywhere(
        self,
        *,
        created,
        pending,
        raw_secret,
    ):
        sync_surfaces = (
            (
                "get",
                lambda: self.service.get_plan(created["plan_id"]),
            ),
            ("list", self.service.list_plans),
            ("review-list", self.service.pending_external_reviews),
            (
                "approval-request",
                lambda: self.service.approve(
                    created["plan_id"], created["plan_hash"]
                ),
            ),
        )
        for name, call in sync_surfaces:
            with self.subTest(surface=name):
                with self.assertRaises(GovernanceError) as raised:
                    call()
                self.assert_secret_free_storage_error(
                    raised, raw_secret
                )

        async_surfaces = (
            (
                "review",
                lambda: self.service.issue_external_csrf(
                    created["plan_id"], pending["challenge_id"]
                ),
            ),
            (
                "decision",
                lambda: self.service.decide_external_approval(
                    plan_id=created["plan_id"],
                    challenge_id=pending["challenge_id"],
                    expected_plan_hash=created["plan_hash"],
                    approval_kind="apply",
                    csrf_nonce="unused-corrupt-storage-nonce",
                    decision="approve",
                    approver_principal=(
                        "home_assistant_admin_ingress:dev14-reviewer"
                    ),
                ),
            ),
            (
                "apply",
                lambda: self.service.apply(
                    created["plan_id"], created["plan_hash"]
                ),
            ),
        )
        for name, call in async_surfaces:
            with self.subTest(surface=name):
                with self.assertRaises(GovernanceError) as raised:
                    await call()
                self.assert_secret_free_storage_error(
                    raised, raw_secret
                )

        audit_text = (
            self.audit_path.read_text(encoding="utf-8")
            if self.audit_path.exists()
            else ""
        )
        self.assertNotIn(raw_secret, audit_text)
        self.assertEqual(self.gateway.calls, [])

    async def approve(self, created):
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        granted = await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=(
                "home_assistant_admin_ingress:dev14-reviewer"
            ),
        )
        return pending, review, granted


class PlanCreationTests(ConfigurationPlanTestCase):
    async def test_hvac_plan_is_one_exact_ordered_non_writing_proposal(self):
        created = await self.create_hvac_plan()
        self.assertEqual(created["contract_version"], 2)
        self.assertEqual(created["operation"], "configuration_plan")
        self.assertEqual(created["status"], "awaiting_approval")
        self.assertEqual(created["execution_outcome"], "not_started")
        self.assertEqual(len(created["operations"]), 3)
        self.assertEqual(
            [item["operation_id"] for item in created["operations"]],
            [
                "create_target_helper",
                "update_comfort_script",
                "update_comfort_automation",
            ],
        )
        self.assertEqual(
            created["operations"][1]["depends_on"],
            ["create_target_helper"],
        )
        self.assertEqual(
            created["operations"][0]["target_id"],
            "input_number.hvac_target",
        )
        self.assertEqual(
            [call[0] for call in self.gateway.calls],
            ["read", "read", "read"],
        )
        self.assertFalse(
            any(call[0] == "write" for call in self.gateway.calls)
        )
        hidden_config_fields = {
            "proposed_config",
            "current_config",
            "normalized_proposed_config",
            "normalized_current_config",
            "snapshot",
        }
        self.assertTrue(hidden_config_fields.isdisjoint(created))
        for operation in created["operations"]:
            self.assertTrue(hidden_config_fields.isdisjoint(operation))
            self.assertIn("operation_id", operation)
            self.assertIn("execution_receipt", operation)
            self.assertIn("verification", operation)

    async def test_hash_binds_order_dependencies_targets_and_configs(self):
        created = await self.create_hvac_plan()
        plan = self.repository.get(created["plan_id"])
        original = self.service.plan_hash(plan)
        mutations = (
            lambda value: (
                setattr(value.operations[0], "order", 1),
                setattr(value.operations[1], "order", 0),
            ),
            lambda value: value.operations[1].depends_on.clear(),
            lambda value: setattr(
                value.operations[0],
                "target_id",
                "input_number.other_target",
            ),
            lambda value: value.operations[0].proposed_config.update(
                {"name": "Different approved name"}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(plan)
                mutate(candidate)
                self.assertNotEqual(
                    self.service.plan_hash(candidate), original
                )

    async def test_invalid_shape_and_unsupported_operations_fail_before_io(self):
        cases = (
            [],
            [
                {
                    "operation_id": "unsupported",
                    "resource_type": "dashboard",
                    "action": "update",
                    "target_id": "lovelace",
                    "proposed_config": {},
                }
            ],
            [
                {
                    "operation_id": "delete_script",
                    "resource_type": "script",
                    "action": "delete",
                    "target_id": "set_hvac_comfort",
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                }
            ],
            [
                {
                    "operation_id": "unknown_field",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "set_hvac_comfort",
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                    "unapproved_extension": True,
                }
            ],
            [
                {
                    "operation_id": "operation_alias",
                    "resource_type": "script",
                    "operation": "update",
                    "target_id": "set_hvac_comfort",
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                }
            ],
            [
                {
                    "operation_id": "target_alias",
                    "resource_type": "script",
                    "action": "update",
                    "resource_id": "set_hvac_comfort",
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                }
            ],
            [
                {
                    "operation_id": "helper_alias",
                    "resource_type": "input_number",
                    "action": "create",
                    "target_id": "input_number.hvac_target",
                    "proposed_config": copy.deepcopy(PROPOSED_HELPER),
                }
            ],
        )
        for operations in cases:
            with self.subTest(operations=operations):
                self.gateway.calls.clear()
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="Invalid",
                        description="Must fail closed",
                        operations=operations,
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                )
                self.assertEqual(self.gateway.calls, [])

    async def test_duplicate_target_and_forward_dependency_fail_closed(self):
        duplicate = hvac_operations()
        duplicate[1]["resource_type"] = "helper"
        duplicate[1]["helper_type"] = "input_number"
        duplicate[1]["target_id"] = "input_number.hvac_target"
        duplicate[1]["proposed_config"] = copy.deepcopy(PROPOSED_HELPER)
        forward = hvac_operations()
        forward[0]["depends_on"] = ["update_comfort_script"]
        for operations in (duplicate, forward):
            with self.subTest(operations=operations):
                self.gateway.calls.clear()
                with self.assertRaises(GovernanceError):
                    await self.service.create_configuration_plan(
                        title="Invalid ordering",
                        description="Must fail closed",
                        operations=operations,
                    )
                self.assertFalse(
                    any(call[0] == "write" for call in self.gateway.calls)
                )

    async def test_helper_create_name_must_match_target_before_io(self):
        operations = [copy.deepcopy(hvac_operations()[0])]
        operations[0]["target_id"] = "input_number.different_target"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Mismatched helper identity",
                description="Must fail before reading or approval",
                operations=operations,
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_VALIDATION_FAILED,
        )
        self.assertIn(
            "exactly match",
            " ".join(raised.exception.details["validation_errors"]),
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_recursive_secret_keys_reject_before_io_or_persistence(self):
        sensitive_keys = (
            "access_token",
            "refresh_token",
            "api_secret",
            "client_secret",
            "secret",
            "credential",
            "credentials",
            "webhook_secret",
            "webhook_id",
            "auth_flow_id",
            "setup_code",
            "setup_payload",
        )
        for key in sensitive_keys:
            with self.subTest(key=key):
                operations = [copy.deepcopy(hvac_operations()[1])]
                operations[0]["depends_on"] = []
                raw_secret = f"synthetic-dev14-{key}-value"
                operations[0]["proposed_config"]["sequence"][0][
                    "data"
                ] = {key: raw_secret}
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="Credential refusal fixture",
                        description="Must reject before configuration I/O",
                        operations=operations,
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                )
                self.assertNotIn(
                    raw_secret,
                    json.dumps(raised.exception.details, sort_keys=True),
                )
                self.assertEqual(self.gateway.calls, [])
                self.assertEqual(self.repository.list(), [])
                self.assertFalse(self.audit_path.exists())
                self.assertNotIn(
                    raw_secret, self.persisted_artifact_text()
                )

    async def test_secret_text_in_title_or_description_rejects_before_io(self):
        cases = (
            (
                "Authorization: Bearer synthetic-dev14-title-token",
                "Safe description",
                "synthetic-dev14-title-token",
            ),
            (
                "Safe title",
                "Bearer synthetic-dev14-description-token",
                "synthetic-dev14-description-token",
            ),
            (
                "Known token dev14-test-ha-token",
                "Safe description",
                "dev14-test-ha-token",
            ),
            (
                "Safe title",
                "Matter setup payload MT:TESTDEV14PAYLOAD123",
                "MT:TESTDEV14PAYLOAD123",
            ),
        )
        for title, description, raw_secret in cases:
            with self.subTest(raw_secret=raw_secret):
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title=title,
                        description=description,
                        operations=hvac_operations(),
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                )
                self.assertNotIn(
                    raw_secret,
                    json.dumps(raised.exception.details, sort_keys=True),
                )
                self.assertEqual(self.gateway.calls, [])
                self.assertEqual(self.repository.list(), [])
                self.assertNotIn(
                    raw_secret, self.persisted_artifact_text()
                )

    async def test_known_secret_in_operation_metadata_rejects_before_io(self):
        operations = [copy.deepcopy(hvac_operations()[1])]
        operations[0]["depends_on"] = []
        operations[0]["operation_id"] = "dev14-test-ha-token"

        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Sensitive operation metadata refusal",
                description="All caller-provided operation data is checked",
                operations=operations,
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_VALIDATION_FAILED,
        )
        self.assertNotIn(
            "dev14-test-ha-token",
            json.dumps(raised.exception.details, sort_keys=True),
        )
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.repository.list(), [])
        self.assertFalse(self.audit_path.exists())

    async def test_secret_text_in_proposed_config_rejects_before_io(self):
        cases = (
            (
                "Authorization: Bearer synthetic-dev14-config-auth",
                "synthetic-dev14-config-auth",
            ),
            (
                "Bearer synthetic-dev14-config-bearer",
                "synthetic-dev14-config-bearer",
            ),
            (
                "known runtime value dev14-test-access-secret",
                "dev14-test-access-secret",
            ),
            (
                "callback /api/webhook/syntheticdev14webhookidentifier123",
                "syntheticdev14webhookidentifier123",
            ),
            (
                "flow /auth/login_flow/synthetic-dev14-flow",
                "synthetic-dev14-flow",
            ),
            (
                "pairing payload MT:SYNTHETICDEV14PAYLOAD123",
                "MT:SYNTHETICDEV14PAYLOAD123",
            ),
        )
        for description, raw_secret in cases:
            with self.subTest(raw_secret=raw_secret):
                operation = copy.deepcopy(hvac_operations()[1])
                operation["depends_on"] = []
                operation["proposed_config"]["description"] = description
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_configuration_plan(
                        title="Sensitive configuration refusal",
                        description="Must reject before configuration I/O",
                        operations=[operation],
                    )
                self.assertEqual(
                    raised.exception.code,
                    ErrorCode.CONFIGURATION_VALIDATION_FAILED,
                )
                self.assertEqual(self.gateway.calls, [])
                self.assertEqual(self.repository.list(), [])
                self.assertNotIn(
                    raw_secret,
                    json.dumps(raised.exception.details, sort_keys=True),
                )
                self.assertNotIn(
                    raw_secret, self.persisted_artifact_text()
                )

    async def test_sensitive_current_config_fails_closed_after_exact_read(self):
        raw_secret = "synthetic-dev14-current-client-secret"
        current = copy.deepcopy(CURRENT_SCRIPT)
        current["sequence"][0]["data"]["client_secret"] = raw_secret
        self.gateway.configs[("script", "set_hvac_comfort")] = current
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []

        with self.assertRaises(GovernanceError) as raised:
            await self.service.create_configuration_plan(
                title="Sensitive current-config refusal",
                description="Current configuration cannot enter a plan",
                operations=[operation],
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_VALIDATION_FAILED,
        )
        self.assertEqual(
            self.gateway.calls,
            [("read", "script", "set_hvac_comfort")],
        )
        self.assertEqual(self.repository.list(), [])
        self.assertNotIn(raw_secret, self.persisted_artifact_text())
        self.assertNotIn(
            raw_secret,
            json.dumps(raised.exception.details, sort_keys=True),
        )

    async def test_sensitive_caller_context_is_dropped_not_persisted(self):
        secrets = (
            "synthetic-dev14-context-bearer",
            "synthetic-dev14-context-client-secret",
            "dev14-test-ha-token",
        )
        created = await self.service.create_configuration_plan(
            title="Caller-context sanitation",
            description="Only bounded safe attribution may persist",
            operations=hvac_operations(),
            caller_context={
                "ticket": "safe-123",
                "authorization_note": (
                    f"Authorization: Bearer {secrets[0]}"
                ),
                "nested": {
                    "client_secret": secrets[1],
                },
                "runtime_note": f"known={secrets[2]}",
            },
        )

        self.assertEqual(created["caller_context"], {"ticket": "safe-123"})
        encoded = json.dumps(created, sort_keys=True)
        stored = self.persisted_artifact_text()
        for raw_secret in secrets:
            self.assertNotIn(raw_secret, encoded)
            self.assertNotIn(raw_secret, stored)

    async def test_corrupt_v2_proposed_config_fails_closed_everywhere(self):
        raw_secret = "dev14-test-access-secret"
        created = await self.create_hvac_plan()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        plan = self.repository.get(created["plan_id"])
        operation = plan.operations[1]
        operation.proposed_config["description"] = raw_secret
        operation.normalized_proposed_config = (
            normalize_resource_config(
                "script", operation.proposed_config
            )
            or {}
        )
        operation.proposed_config_hash = stable_hash(
            operation.normalized_proposed_config
        )
        self.repository.save(plan)
        self.gateway.calls.clear()

        await self.assert_corrupt_v2_plan_refused_everywhere(
            created=created,
            pending=pending,
            raw_secret=raw_secret,
        )

    async def test_corrupt_v2_current_config_fails_closed_everywhere(self):
        raw_secret = "dev14-test-ha-token"
        created = await self.create_hvac_plan()
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        plan = self.repository.get(created["plan_id"])
        operation = plan.operations[1]
        operation.current_config["description"] = raw_secret
        operation.normalized_current_config = normalize_resource_config(
            "script", operation.current_config
        )
        operation.current_state_fingerprint = resource_fingerprint(
            "script", operation.current_config
        )
        self.repository.save(plan)
        self.gateway.calls.clear()

        await self.assert_corrupt_v2_plan_refused_everywhere(
            created=created,
            pending=pending,
            raw_secret=raw_secret,
        )

    async def test_mcp_failure_output_and_artifacts_never_echo_secret(self):
        raw_secret = "synthetic-dev14-mcp-output-secret"
        operations = [copy.deepcopy(hvac_operations()[1])]
        operations[0]["depends_on"] = []
        operations[0]["proposed_config"]["sequence"][0]["data"] = {
            "client_secret": raw_secret,
        }
        prior_service = GOVERNANCE.service
        GOVERNANCE.service = self.service
        try:
            output = await create_configuration_plan_tool(
                title="MCP secret refusal",
                description="MCP response must remain secret-free",
                operations=operations,
            )
        finally:
            GOVERNANCE.service = prior_service

        self.assertNotIn(raw_secret, output)
        response = json.loads(output)
        self.assertEqual(
            response["error_code"],
            ErrorCode.CONFIGURATION_VALIDATION_FAILED.value,
        )
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.repository.list(), [])
        self.assertFalse(self.audit_path.exists())
        self.assertNotIn(raw_secret, self.persisted_artifact_text())

    async def test_review_projection_is_bounded_and_contains_no_configs(self):
        created = await self.create_hvac_plan()
        _pending, review, _granted = await self.approve(created)
        self.assertEqual(review["operation_count"], 3)
        self.assertEqual(len(review["operation_summaries"]), 3)
        encoded = json.dumps(review, sort_keys=True).lower()
        self.assertNotIn("proposed_config", encoded)
        self.assertNotIn("current_config", encoded)
        self.assertIn("climate.set_temperature", encoded)
        self.assertIn("climate.downstairs", encoded)
        self.assertIn("data.temperature", encoded)
        self.assertTrue(
            all(
                item["semantic_projection"]["status"] == "complete"
                for item in review["operation_summaries"]
            )
        )
        self.assertIn("non_atomic_failure_policy", review)

    async def test_same_count_action_target_and_value_changes_render_differently(
        self,
    ):
        first = await self.create_hvac_plan()
        _pending, first_review, _granted = await self.approve(first)

        changed = hvac_operations()
        changed[1]["proposed_config"]["sequence"] = [
            {
                "service": "climate.set_humidity",
                "data": {
                    "entity_id": "climate.upstairs",
                    "humidity": 45,
                },
            }
        ]
        second = await self.service.create_configuration_plan(
            title="Changed one-action HVAC plan",
            description="Same action count with different semantics",
            operations=changed,
        )
        _pending, second_review, _granted = await self.approve(second)

        first_projection = first_review["operation_summaries"][1][
            "semantic_projection"
        ]
        second_projection = second_review["operation_summaries"][1][
            "semantic_projection"
        ]
        first_html = _render_review("", first_review, "first-csrf")
        second_html = _render_review("", second_review, "second-csrf")
        self.assertNotEqual(first_projection, second_projection)
        self.assertNotEqual(first_html, second_html)
        self.assertIn("climate.set_temperature", first_html)
        self.assertIn("climate.downstairs", first_html)
        self.assertIn("data.temperature", first_html)
        self.assertIn("climate.set_humidity", second_html)
        self.assertIn("data.entity_id=climate.upstairs", second_html)
        self.assertIn("data.humidity", second_html)
        self.assertIn("data.humidity=45", second_html)
        self.assertEqual(
            len(first_projection["actions"]),
            1,
        )
        self.assertEqual(
            len(second_projection["actions"]),
            1,
        )

    def test_semantic_projection_redacts_secret_like_action_data(self):
        proposed = copy.deepcopy(PROPOSED_SCRIPT)
        proposed["sequence"] = [
            {
                "service": "notify.mobile_app_test",
                "target": {"entity_id": "notify.test_phone"},
                "data": {
                    "client_secret": "dev14-client-secret-value",
                    "message": (
                        "Authorization: Bearer "
                        "dev14-approval-secret-value"
                    )
                },
            }
        ]
        projection = _configuration_approval_projection(
            "script",
            proposed,
            known_secrets=("dev14-approval-secret-value",),
        )
        encoded = json.dumps(projection, sort_keys=True)

        self.assertNotIn("dev14-approval-secret-value", encoded)
        self.assertNotIn("dev14-client-secret-value", encoded)
        self.assertIn("[REDACTED:token]", encoded)
        self.assertNotIn("proposed_config", encoded)
        self.assertTrue(projection["redaction_applied"])
        self.assertEqual(projection["status"], "incomplete")

    async def test_same_count_trigger_and_condition_changes_are_visible(self):
        first_operations = hvac_operations()
        first_operations[2]["proposed_config"]["action"] = [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.test_fixture"},
            }
        ]
        first_operations[2]["proposed_config"]["condition"] = [
            {
                "condition": "state",
                "entity_id": "input_boolean.guest_mode",
                "state": "off",
            }
        ]
        first = await self.service.create_configuration_plan(
            title="First trigger and condition fixture",
            description="One trigger and one condition",
            operations=first_operations,
        )
        _pending, first_review, _granted = await self.approve(first)

        second_operations = hvac_operations()
        second_operations[2]["proposed_config"]["action"] = [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.test_fixture"},
            }
        ]
        second_operations[2]["proposed_config"]["trigger"] = [
            {
                "platform": "state",
                "entity_id": "input_boolean.away_mode",
                "from": "off",
                "to": "on",
            }
        ]
        second_operations[2]["proposed_config"]["condition"] = [
            {
                "condition": "state",
                "entity_id": "input_boolean.window_open",
                "state": "on",
            }
        ]
        second = await self.service.create_configuration_plan(
            title="Second trigger and condition fixture",
            description="Same counts with different control semantics",
            operations=second_operations,
        )
        _pending, second_review, _granted = await self.approve(second)

        first_projection = first_review["operation_summaries"][2][
            "semantic_projection"
        ]
        second_projection = second_review["operation_summaries"][2][
            "semantic_projection"
        ]
        first_html = _render_review("", first_review, "first-control-csrf")
        second_html = _render_review(
            "", second_review, "second-control-csrf"
        )
        self.assertEqual(len(first_projection["controls"]), 2)
        self.assertEqual(len(second_projection["controls"]), 2)
        self.assertNotEqual(
            first_projection["controls"],
            second_projection["controls"],
        )
        self.assertIn("Automation triggers and conditions", first_html)
        self.assertIn(
            "entity_id=input_boolean.hvac_override",
            first_html,
        )
        self.assertIn("entity_id=input_boolean.guest_mode", first_html)
        self.assertIn("entity_id=input_boolean.away_mode", second_html)
        self.assertIn("entity_id=input_boolean.window_open", second_html)
        self.assertIn("state=on", second_html)

    async def test_choose_conditions_are_explicit_and_distinguishable(self):
        async def review_for(entity_id, state):
            operation = copy.deepcopy(hvac_operations()[1])
            operation["depends_on"] = []
            operation["proposed_config"]["sequence"] = [
                {
                    "choose": [
                        {
                            "conditions": [
                                {
                                    "condition": "state",
                                    "entity_id": entity_id,
                                    "state": state,
                                }
                            ],
                            "sequence": [
                                {
                                    "service": "light.turn_on",
                                    "target": {
                                        "entity_id": "light.hallway"
                                    },
                                }
                            ],
                        }
                    ],
                    "default": [
                        {
                            "service": "light.turn_off",
                            "target": {"entity_id": "light.hallway"},
                        }
                    ],
                }
            ]
            created = await self.service.create_configuration_plan(
                title=f"Choose {entity_id} {state}",
                description="Nested choose approval semantics",
                operations=[operation],
            )
            _pending, review, _granted = await self.approve(created)
            return review

        away_review = await review_for(
            "input_boolean.away_mode", "on"
        )
        guest_review = await review_for(
            "input_boolean.guest_mode", "off"
        )
        away_projection = away_review["operation_summaries"][0][
            "semantic_projection"
        ]
        guest_projection = guest_review["operation_summaries"][0][
            "semantic_projection"
        ]
        away_html = _render_review("", away_review, "away-csrf")
        guest_html = _render_review("", guest_review, "guest-csrf")

        self.assertEqual(away_projection["status"], "complete")
        self.assertEqual(guest_projection["status"], "complete")
        self.assertNotEqual(away_projection, guest_projection)
        self.assertIn("choose_condition", away_html)
        self.assertIn("entity_id=input_boolean.away_mode", away_html)
        self.assertIn("state=on", away_html)
        self.assertIn("entity_id=input_boolean.guest_mode", guest_html)
        self.assertIn("state=off", guest_html)

    async def test_long_same_prefix_values_are_incomplete_and_refused(self):
        projections = []
        for suffix in ("first", "second"):
            operation = copy.deepcopy(hvac_operations()[1])
            operation["depends_on"] = []
            operation["proposed_config"]["sequence"] = [
                {
                    "service": "notify.test",
                    "data": {"message": ("x" * 200) + suffix},
                }
            ]
            created = await self.service.create_configuration_plan(
                title=f"Long value {suffix}",
                description="Truncation must disable informed approval",
                operations=[operation],
            )
            pending = self.service.approve(
                created["plan_id"], created["plan_hash"]
            )
            review, csrf = await self.service.issue_external_csrf(
                created["plan_id"], pending["challenge_id"]
            )
            projection = review["operation_summaries"][0][
                "semantic_projection"
            ]
            html = _render_review("", review, csrf)
            projections.append(projection)

            self.assertEqual(projection["status"], "incomplete")
            self.assertTrue(projection["truncation_applied"])
            self.assertIn("Approval is disabled", html)
            self.assertNotIn("Approve exact plan", html)
            with self.assertRaises(GovernanceError) as raised:
                await self.service.decide_external_approval(
                    plan_id=created["plan_id"],
                    challenge_id=pending["challenge_id"],
                    expected_plan_hash=created["plan_hash"],
                    approval_kind="apply",
                    csrf_nonce=csrf,
                    decision="approve",
                    approver_principal=(
                        "home_assistant_admin_ingress:dev14-reviewer"
                    ),
                )
            self.assertEqual(
                raised.exception.code,
                ErrorCode.EXTERNAL_APPROVAL_INVALID,
            )

        self.assertFalse(
            projections[0]["status"] == "complete"
            and projections[0] == projections[1]
        )

    async def test_repeat_wait_and_variables_are_explicit(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        operation["proposed_config"]["variables"] = {
            "target_light": "light.hallway"
        }
        operation["proposed_config"]["sequence"] = [
            {"variables": {"repeat_count": 2}},
            {
                "repeat": {
                    "count": 2,
                    "sequence": [
                        {
                            "service": "light.toggle",
                            "target": {
                                "entity_id": "light.hallway"
                            },
                        }
                    ],
                }
            },
            {
                "if": [
                    {
                        "condition": "state",
                        "entity_id": "input_boolean.hvac_override",
                        "state": "on",
                    }
                ],
                "then": [
                    {
                        "service": "climate.turn_on",
                        "target": {"entity_id": "climate.downstairs"},
                    }
                ],
                "else": [
                    {
                        "service": "climate.turn_off",
                        "target": {"entity_id": "climate.downstairs"},
                    }
                ],
            },
            {
                "wait_for_trigger": [
                    {
                        "platform": "state",
                        "entity_id": "binary_sensor.hvac_ready",
                        "to": "on",
                    }
                ],
                "timeout": {"seconds": 30},
                "continue_on_timeout": False,
            },
        ]
        created = await self.service.create_configuration_plan(
            title="Explicit nested controls",
            description="Variables, repeat, and wait trigger projection",
            operations=[operation],
        )
        _pending, review, _granted = await self.approve(created)
        projection = review["operation_summaries"][0][
            "semantic_projection"
        ]
        encoded = json.dumps(projection, sort_keys=True)

        self.assertEqual(projection["status"], "complete")
        self.assertIn("variables.target_light", encoded)
        self.assertIn("variables.repeat_count", encoded)
        self.assertIn("repeat.count", encoded)
        self.assertIn("if_condition", encoded)
        self.assertIn("input_boolean.hvac_override", encoded)
        self.assertIn("wait_trigger", encoded)
        self.assertIn("binary_sensor.hvac_ready", encoded)
        self.assertIn("timeout.seconds", encoded)

    async def test_unsupported_nested_construct_refuses_approval(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        operation["proposed_config"]["sequence"] = [
            {
                "service": "light.turn_on",
                "unsupported_nested": [
                    {
                        "service": "light.turn_off",
                        "target": {"entity_id": "light.hidden"},
                    }
                ],
            }
        ]
        created = await self.service.create_configuration_plan(
            title="Unsupported nested construct",
            description="Unrepresented semantics must fail closed",
            operations=[operation],
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        projection = review["operation_summaries"][0][
            "semantic_projection"
        ]
        html = _render_review("", review, csrf)

        self.assertEqual(projection["status"], "incomplete")
        self.assertNotIn("light.hidden", json.dumps(review))
        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind="apply",
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:dev14-reviewer"
                ),
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.EXTERNAL_APPROVAL_INVALID,
        )

    async def test_over_bound_semantic_projection_cannot_be_approved(self):
        operation = copy.deepcopy(hvac_operations()[1])
        operation["depends_on"] = []
        operation["proposed_config"]["sequence"] = [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": f"climate.zone_{index}"},
                "data": {"temperature": 20 + index},
            }
            for index in range(17)
        ]
        created = await self.service.create_configuration_plan(
            title="Over-bound semantic projection",
            description="The approval decision must fail closed",
            operations=[operation],
        )
        pending = self.service.approve(
            created["plan_id"], created["plan_hash"]
        )
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        html = _render_review("", review, csrf)

        self.assertEqual(
            review["operation_summaries"][0]["semantic_projection"][
                "status"
            ],
            "incomplete",
        )
        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.decide_external_approval(
                plan_id=created["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"],
                approval_kind="apply",
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:dev14-reviewer"
                ),
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.EXTERNAL_APPROVAL_INVALID,
        )


class ApplyTests(ConfigurationPlanTestCase):
    async def test_one_approval_applies_in_order_reads_back_and_validates(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.calls.clear()

        result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["execution_outcome"], "applied")
        self.assertEqual(result["configuration_check_status"], "valid")
        self.assertEqual(
            [item["execution_status"] for item in result["operations"]],
            ["applied_verified", "applied_verified", "applied_verified"],
        )
        self.assertEqual(
            self.gateway.calls,
            [
                ("read", "input_number", "input_number.hvac_target"),
                ("read", "script", "set_hvac_comfort"),
                ("read", "automation", "apply_hvac_comfort"),
                ("read", "input_number", "input_number.hvac_target"),
                (
                    "write",
                    "create",
                    "input_number",
                    "input_number.hvac_target",
                ),
                ("read", "input_number", "input_number.hvac_target"),
                ("read", "script", "set_hvac_comfort"),
                (
                    "write",
                    "update",
                    "script",
                    "set_hvac_comfort",
                ),
                ("read", "script", "set_hvac_comfort"),
                ("read", "automation", "apply_hvac_comfort"),
                (
                    "write",
                    "update",
                    "automation",
                    "apply_hvac_comfort",
                ),
                ("read", "automation", "apply_hvac_comfort"),
                ("validate_all",),
            ],
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertEqual(
            persisted.approval.state, ApprovalState.CONSUMED
        )
        events = [
            json.loads(line)["event"]
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertEqual(
            events.count("configuration_operation_started"), 3
        )
        self.assertEqual(
            events.count("configuration_operation_verified"), 3
        )

    async def test_stale_preflight_stops_before_consumption_or_any_write(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        changed = copy.deepcopy(CURRENT_AUTOMATION)
        changed["description"] = "Changed outside the approved plan"
        self.gateway.configs[
            ("automation", "apply_hvac_comfort")
        ] = changed
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code, ErrorCode.STALE_TARGET_STATE
        )
        self.assertFalse(
            any(call[0] == "write" for call in self.gateway.calls)
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPROVED)
        self.assertEqual(
            persisted.approval.state, ApprovalState.APPROVED
        )

    async def test_external_edit_after_preflight_is_not_overwritten(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        changed = copy.deepcopy(CURRENT_SCRIPT)
        changed["description"] = "Changed after the all-target preflight"
        target = ("script", "set_hvac_comfort")
        # Plan creation already read the target once. The next read is the
        # all-target apply preflight; mutate on the operation-local re-read.
        self.gateway.replace_on_read_count[
            (target, self.gateway.read_counts[target] + 2)
        ] = changed
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        self.assertEqual(
            raised.exception.details["cause_error_code"],
            ErrorCode.STALE_TARGET_STATE.value,
        )
        self.assertFalse(raised.exception.details["write_attempted"])
        self.assertEqual(
            self.gateway.configs[("script", "set_hvac_comfort")],
            changed,
        )
        self.assertEqual(
            [
                call
                for call in self.gateway.calls
                if call[0] == "write"
            ],
            [
                (
                    "write",
                    "create",
                    "input_number",
                    "input_number.hvac_target",
                )
            ],
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(
            persisted.approval.state, ApprovalState.CONSUMED
        )
        self.assertEqual(persisted.execution_outcome, "partial_failure")
        self.assertEqual(
            persisted.operations[1].execution_status,
            StepExecutionStatus.FAILED,
        )
        self.assertFalse(
            persisted.operations[1].execution_receipt[
                "write_attempted"
            ]
        )
        self.assertEqual(
            persisted.operations[1].execution_receipt["reason"],
            "stale_target_state",
        )
        self.assertEqual(
            persisted.operations[2].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertIn(("validate_all",), self.gateway.calls)
        events = [
            json.loads(line)["event"]
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertEqual(events.count("external_approval_consumed"), 1)
        self.assertIn(
            "configuration_operation_prewrite_revalidation_failed",
            events,
        )

    async def test_external_edit_cannot_turn_planned_noop_into_false_success(self):
        operations = hvac_operations()
        operations[1]["proposed_config"] = {
            key: copy.deepcopy(value)
            for key, value in CURRENT_SCRIPT.items()
            if key != "id"
        }
        created = await self.service.create_configuration_plan(
            title="No-op script revalidation",
            description="The script starts at the desired state",
            operations=operations,
        )
        await self.approve(created)
        target = ("script", "set_hvac_comfort")
        changed = copy.deepcopy(CURRENT_SCRIPT)
        changed["description"] = "Externally changed after preflight"
        self.gateway.replace_on_read_count[
            (target, self.gateway.read_counts[target] + 2)
        ] = changed
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        persisted = self.repository.get(created["plan_id"])
        script_step = persisted.operations[1]
        self.assertEqual(
            script_step.execution_status, StepExecutionStatus.FAILED
        )
        self.assertFalse(script_step.execution_receipt["write_attempted"])
        self.assertEqual(
            script_step.execution_receipt["reason"],
            "stale_target_state",
        )
        self.assertEqual(
            [
                call for call in self.gateway.calls if call[0] == "write"
            ],
            [
                (
                    "write",
                    "create",
                    "input_number",
                    "input_number.hvac_target",
                )
            ],
        )

    async def test_partial_failure_reports_prior_success_and_stops(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.fail_write_target = (
            "script",
            "set_hvac_comfort",
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.execution_outcome, "partial_failure")
        self.assertEqual(
            [item.execution_status for item in persisted.operations],
            [
                StepExecutionStatus.APPLIED_VERIFIED,
                StepExecutionStatus.FAILED,
                StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
            ],
        )
        self.assertIn(
            ("input_number", "input_number.hvac_target"),
            self.gateway.configs,
        )
        self.assertEqual(
            self.gateway.configs[
                ("automation", "apply_hvac_comfort")
            ],
            CURRENT_AUTOMATION,
        )
        self.assertEqual(
            [
                call
                for call in self.gateway.calls
                if call[0] == "write"
            ],
            [
                (
                    "write",
                    "create",
                    "input_number",
                    "input_number.hvac_target",
                ),
                (
                    "write",
                    "update",
                    "script",
                    "set_hvac_comfort",
                ),
            ],
        )
        self.assertIn(("validate_all",), self.gateway.calls)
        self.assertFalse(persisted.rollback.available)

    async def test_unexpected_helper_identity_is_reported_as_orphan_risk(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.fail_write_target = (
            "input_number",
            "input_number.hvac_target",
        )
        self.gateway.fail_write_exception = (
            ConfigurationMutationCompletedUnexpectedlyError(
            details={
                "operation": "input_number_config_create",
                "resource_id": "input_number.hvac_target",
                "reason": "generated_identity_mismatch",
                "unexpected_resource_id": "input_number.hvac_target_2",
                "orphan_risk": True,
            }
            )
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        self.assertEqual(
            raised.exception.details["unexpected_resource_id"],
            "input_number.hvac_target_2",
        )
        self.assertTrue(raised.exception.details["orphan_risk"])
        first = raised.exception.details["operations"][0]
        self.assertEqual(
            first["execution_receipt"]["unexpected_resource_id"],
            "input_number.hvac_target_2",
        )
        self.assertTrue(first["execution_receipt"]["orphan_risk"])
        self.assertTrue(first["execution_receipt"]["write_attempted"])
        self.assertTrue(first["execution_receipt"]["write_completed"])
        self.assertEqual(
            first["execution_receipt"]["outcome"],
            "unexpected_resource_created",
        )
        self.assertEqual(
            raised.exception.details["successful_write_count"], 1
        )
        self.assertEqual(
            raised.exception.details["ambiguous_write_count"], 0
        )
        self.assertEqual(
            raised.exception.details["operations"][1]["execution_status"],
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE.value,
        )
        self.assertEqual(
            [
                call for call in self.gateway.calls if call[0] == "write"
            ],
            [
                (
                    "write",
                    "create",
                    "input_number",
                    "input_number.hvac_target",
                )
            ],
        )
        self.assertIn(("validate_all",), self.gateway.calls)

    async def test_helper_collision_is_not_dispatched_or_partial(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.fail_write_target = (
            "input_number",
            "input_number.hvac_target",
        )
        self.gateway.fail_write_exception = (
            ConfigurationMutationNotDispatchedError(
                details={
                    "operation": "input_number_config_create",
                    "resource_id": "input_number.hvac_target",
                    "reason": "target_already_exists",
                }
            )
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code, ErrorCode.CONFIGURATION_CONFLICT
        )
        self.assertEqual(
            raised.exception.details["execution_outcome"], "not_applied"
        )
        self.assertEqual(
            raised.exception.details["attempted_write_count"], 0
        )
        self.assertEqual(
            raised.exception.details["successful_write_count"], 0
        )
        self.assertEqual(
            raised.exception.details["ambiguous_write_count"], 0
        )
        first = raised.exception.details["operations"][0]
        self.assertFalse(first["execution_receipt"]["write_attempted"])
        self.assertFalse(first["execution_receipt"]["write_completed"])
        self.assertEqual(
            first["execution_receipt"]["write_result"], "not_dispatched"
        )
        self.assertEqual(
            first["execution_receipt"]["reason"],
            "target_already_exists",
        )
        self.assertEqual(
            raised.exception.details["operations"][1]["execution_status"],
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE.value,
        )
        self.assertNotIn(("validate_all",), self.gateway.calls)
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.execution_outcome, "not_applied")
        self.assertEqual(
            persisted.failure_information["error_code"],
            ErrorCode.CONFIGURATION_CONFLICT.value,
        )

    async def test_helper_collision_after_prior_write_is_partial_only_for_prior(self):
        operations = hvac_operations()
        script = operations[1]
        helper = operations[0]
        automation = operations[2]
        script["depends_on"] = []
        helper["depends_on"] = [script["operation_id"]]
        automation["depends_on"] = [helper["operation_id"]]
        created = await self.service.create_configuration_plan(
            title="Prior write then helper collision",
            description="Only the already-completed script write is partial",
            operations=[script, helper, automation],
        )
        await self.approve(created)
        self.gateway.fail_write_target = (
            "input_number",
            "input_number.hvac_target",
        )
        self.gateway.fail_write_exception = (
            ConfigurationMutationNotDispatchedError(
                details={
                    "operation": "input_number_config_create",
                    "resource_id": "input_number.hvac_target",
                    "reason": "target_already_exists",
                }
            )
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        self.assertEqual(
            raised.exception.details["attempted_write_count"], 1
        )
        self.assertEqual(
            raised.exception.details["successful_write_count"], 1
        )
        self.assertEqual(
            raised.exception.details["ambiguous_write_count"], 0
        )
        receipts = raised.exception.details["operations"]
        self.assertTrue(receipts[0]["execution_receipt"]["write_completed"])
        self.assertFalse(receipts[1]["execution_receipt"]["write_attempted"])
        self.assertFalse(receipts[1]["execution_receipt"]["write_completed"])
        self.assertEqual(
            receipts[2]["execution_status"],
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE.value,
        )
        self.assertIn(("validate_all",), self.gateway.calls)

    async def test_first_write_transport_failure_is_ambiguous_and_checked(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.fail_write_target = (
            "input_number",
            "input_number.hvac_target",
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.execution_outcome, "partial_failure")
        self.assertEqual(
            persisted.configuration_check_status, "valid"
        )
        self.assertEqual(
            persisted.failure_information["successful_write_count"], 0
        )
        self.assertEqual(
            persisted.failure_information["ambiguous_write_count"], 1
        )
        self.assertEqual(
            persisted.operations[0].execution_receipt["outcome"],
            "write_and_resulting_state_unconfirmed",
        )
        self.assertEqual(
            persisted.operations[1].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(
            persisted.operations[2].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertIn(("validate_all",), self.gateway.calls)

    async def test_readback_identity_mismatch_is_ambiguous_partial_failure(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.mismatch_after_write_target = (
            "input_number",
            "input_number.hvac_target",
        )
        self.gateway.calls.clear()

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(persisted.execution_outcome, "partial_failure")
        self.assertEqual(
            persisted.operations[0].execution_status,
            StepExecutionStatus.VERIFICATION_FAILED,
        )
        self.assertIn(
            "resource_identity",
            persisted.operations[0].verification.mismatch_fields,
        )
        self.assertEqual(
            persisted.operations[1].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(
            len(
                [
                    call
                    for call in self.gateway.calls
                    if call[0] == "write"
                ]
            ),
            1,
        )

    async def test_partial_verification_failure_remains_terminal_after_expiry(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.mismatch_after_write_target = (
            "input_number",
            "input_number.hvac_target",
        )

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )

        before = self.repository.get(created["plan_id"])
        before_receipts = [
            copy.deepcopy(operation.execution_receipt)
            for operation in before.operations
        ]
        before_statuses = [
            operation.execution_status for operation in before.operations
        ]
        self.service.now = lambda: datetime(
            2026, 7, 23, 13, 1, tzinfo=timezone.utc
        )

        resolved = self.service.get_plan(created["plan_id"])
        after = self.repository.get(created["plan_id"])

        self.assertEqual(resolved["status"], "verification_failed")
        self.assertEqual(after.status, PlanStatus.VERIFICATION_FAILED)
        self.assertEqual(after.execution_outcome, "partial_failure")
        self.assertEqual(
            [operation.execution_status for operation in after.operations],
            before_statuses,
        )
        self.assertEqual(
            [operation.execution_receipt for operation in after.operations],
            before_receipts,
        )
        self.assertNotIn(
            "change_plan_expired",
            [event.event for event in after.events],
        )

    async def test_failed_configuration_check_preserves_exact_step_receipts(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.validation_result = {
            "result": "invalid",
            "errors": ["deterministic invalid fixture"],
        }

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_VERIFICATION_FAILED,
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(
            persisted.execution_outcome, "verification_failed"
        )
        self.assertEqual(
            persisted.configuration_check_status, "failed"
        )
        self.assertEqual(
            persisted.failure_information["configuration_check"]["result"],
            "invalid",
        )
        self.assertEqual(
            persisted.failure_information["configuration_check"]["errors"],
            ["deterministic invalid fixture"],
        )
        self.assertEqual(
            raised.exception.details["configuration_check"]["result"],
            "invalid",
        )
        self.assertEqual(
            raised.exception.details["configuration_check"]["errors"],
            ["deterministic invalid fixture"],
        )
        self.assertTrue(
            all(
                item.execution_status
                == StepExecutionStatus.APPLIED_VERIFIED
                for item in persisted.operations
            )
        )
        self.assertFalse(persisted.rollback.available)

    async def test_configuration_check_accepts_only_explicit_valid_object(self):
        invalid_results = (
            {},
            None,
            "",
            "none",
            "ok",
            {"result": "valid"},
            {"result": "valid", "errors": []},
            {"result": "VALID", "errors": None},
            {"result": "unknown", "errors": None},
        )
        for result in invalid_results:
            with self.subTest(result=result):
                self.gateway.validation_result = copy.deepcopy(result)
                status, details = (
                    await self.service._config_check_with_details()
                )
                self.assertEqual(status, "failed")
                self.assertNotEqual(
                    details["reason"], "explicit_valid_result"
                )

        self.gateway.validation_result = {
            "result": "valid",
            "errors": None,
        }
        status, details = (
            await self.service._config_check_with_details()
        )
        self.assertEqual(status, "valid")
        self.assertEqual(details["result"], "valid")
        self.assertIsNone(details["errors"])
        self.assertEqual(details["reason"], "explicit_valid_result")

    async def test_empty_configuration_check_object_cannot_finish_apply(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        self.gateway.validation_result = {}

        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                created["plan_id"], created["plan_hash"]
            )

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CONFIGURATION_VERIFICATION_FAILED,
        )
        self.assertEqual(
            raised.exception.details["configuration_check_status"],
            "failed",
        )
        self.assertEqual(
            raised.exception.details["configuration_check"]["reason"],
            "missing_result",
        )
        persisted = self.repository.get(created["plan_id"])
        self.assertEqual(
            persisted.status, PlanStatus.VERIFICATION_FAILED
        )
        self.assertEqual(
            persisted.failure_information["configuration_check"][
                "reason"
            ],
            "missing_result",
        )

    async def test_configuration_plan_rollback_is_unavailable(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"])
        self.assertEqual(
            raised.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE
        )


class RestartRecoveryTests(ConfigurationPlanTestCase):
    def restart_service(self):
        repository = ChangePlanRepository(self.root / "plans")
        service = ChangeGovernanceService(
            repository,
            self.gateway,
            AuditLogger(
                str(self.audit_path), "dev14-test-access-secret"
            ),
            now=lambda: datetime(
                2026, 7, 23, 12, 1, tzinfo=timezone.utc
            ),
            sensitive_values=(
                "dev14-test-access-secret",
                "dev14-test-ha-token",
            ),
        )
        return repository, service

    async def test_restart_recovers_known_no_write_failure_as_not_applied(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        plan.operations[0].execution_status = StepExecutionStatus.FAILED
        plan.operations[0].execution_receipt = {
            "write_attempted": False,
            "write_completed": False,
            "readback_completed": False,
            "write_result": "not_dispatched",
            "outcome": "not_applied",
            "reason": "target_already_exists",
        }
        plan.operations[0].failure_information = {
            "error_code": ErrorCode.CONFIGURATION_CONFLICT.value,
            "reason": "target_already_exists",
            "mutation_dispatched": False,
        }
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.status, PlanStatus.FAILED)
        self.assertEqual(recovered.execution_outcome, "not_applied")
        self.assertEqual(
            recovered.failure_information["error_code"],
            ErrorCode.CONFIGURATION_CONFLICT.value,
        )
        self.assertEqual(
            recovered.failure_information["attempted_write_count"], 0
        )
        self.assertEqual(
            recovered.failure_information["successful_write_count"], 0
        )
        self.assertEqual(
            recovered.failure_information["ambiguous_write_count"], 0
        )
        self.assertEqual(
            recovered.failure_information["completed_operation_count"], 0
        )
        self.assertFalse(
            recovered.operations[0].execution_receipt["write_attempted"]
        )
        self.assertEqual(
            recovered.operations[1].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_restart_noop_then_known_no_write_failure_is_not_partial(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        plan.operations[0].execution_status = (
            StepExecutionStatus.APPLIED_VERIFIED
        )
        plan.operations[0].execution_receipt = {
            "write_attempted": False,
            "write_completed": False,
            "readback_completed": True,
            "outcome": "already_desired",
        }
        plan.operations[1].execution_status = StepExecutionStatus.FAILED
        plan.operations[1].execution_receipt = {
            "write_attempted": False,
            "write_completed": False,
            "readback_completed": False,
            "write_result": "not_dispatched",
            "outcome": "not_applied",
            "reason": "helper_create_preflight_unavailable",
        }
        plan.operations[1].failure_information = {
            "error_code": ErrorCode.CONFIGURATION_APPLY_FAILED.value,
            "reason": "helper_create_preflight_unavailable",
            "mutation_dispatched": False,
        }
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.execution_outcome, "not_applied")
        self.assertEqual(
            recovered.failure_information["error_code"],
            ErrorCode.CONFIGURATION_APPLY_FAILED.value,
        )
        self.assertEqual(
            recovered.failure_information["completed_operation_count"], 0
        )
        self.assertEqual(
            recovered.failure_information["attempted_write_count"], 0
        )
        self.assertEqual(
            recovered.operations[0].execution_status,
            StepExecutionStatus.APPLIED_VERIFIED,
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_restart_during_first_write_is_ambiguous_partial_failure(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        plan.operations[0].execution_status = StepExecutionStatus.APPLYING
        plan.operations[0].execution_receipt = {
            "write_attempted": True,
            "write_completed": False,
            "readback_completed": False,
        }
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.status, PlanStatus.FAILED)
        self.assertEqual(recovered.execution_outcome, "partial_failure")
        self.assertEqual(
            recovered.failure_information["error_code"],
            ErrorCode.CONFIGURATION_PARTIAL_FAILURE.value,
        )
        self.assertEqual(
            recovered.failure_information["successful_write_count"], 0
        )
        self.assertEqual(
            recovered.failure_information["ambiguous_write_count"], 1
        )
        self.assertEqual(
            recovered.configuration_check_status,
            "not_run_after_restart",
        )
        self.assertEqual(
            recovered.operations[0].execution_status,
            StepExecutionStatus.FAILED,
        )
        self.assertEqual(
            recovered.operations[0].execution_receipt["outcome"],
            "interrupted_before_exact_verification",
        )
        self.assertEqual(
            recovered.operations[1].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(
            recovered.operations[2].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_restart_preserves_verified_receipt_and_never_resumes(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        plan.operations[0].execution_status = (
            StepExecutionStatus.APPLIED_VERIFIED
        )
        plan.operations[0].execution_receipt = {
            "write_attempted": True,
            "write_completed": True,
            "readback_completed": True,
            "outcome": "applied_verified",
        }
        plan.operations[1].execution_status = StepExecutionStatus.APPLYING
        plan.operations[1].execution_receipt = {
            "write_attempted": True,
        }
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.execution_outcome, "partial_failure")
        self.assertEqual(
            recovered.failure_information["successful_write_count"], 1
        )
        self.assertEqual(
            recovered.failure_information["ambiguous_write_count"], 1
        )
        self.assertEqual(
            recovered.operations[0].execution_status,
            StepExecutionStatus.APPLIED_VERIFIED,
        )
        self.assertEqual(
            recovered.operations[1].execution_status,
            StepExecutionStatus.FAILED,
        )
        self.assertEqual(
            recovered.operations[2].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_restart_recovers_persisted_ambiguous_failure_window(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        plan.operations[0].execution_status = StepExecutionStatus.FAILED
        plan.operations[0].execution_receipt = {
            "write_attempted": True,
            "write_completed": False,
            "readback_completed": True,
            "outcome": "write_and_resulting_state_unconfirmed",
        }
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.execution_outcome, "partial_failure")
        self.assertEqual(
            recovered.failure_information["ambiguous_write_count"], 1
        )
        self.assertEqual(
            recovered.failure_information["interrupted_write_count"], 0
        )
        self.assertEqual(
            recovered.operations[0].execution_status,
            StepExecutionStatus.FAILED,
        )
        self.assertEqual(
            recovered.operations[1].execution_status,
            StepExecutionStatus.NOT_ATTEMPTED_DEPENDENCY_FAILURE,
        )
        self.assertEqual(self.gateway.calls, [])

    async def test_restart_after_all_readbacks_never_claims_final_success(self):
        created = await self.create_hvac_plan()
        await self.approve(created)
        plan = self.repository.get(created["plan_id"])
        plan.status = PlanStatus.APPLYING
        plan.approval.state = ApprovalState.CONSUMED
        for operation in plan.operations:
            operation.execution_status = (
                StepExecutionStatus.APPLIED_VERIFIED
            )
            operation.execution_receipt = {
                "write_attempted": True,
                "write_completed": True,
                "readback_completed": True,
                "outcome": "applied_verified",
            }
            operation.verification.status = "passed"
        self.repository.save(plan)
        self.gateway.calls.clear()

        repository, _service = self.restart_service()

        recovered = repository.get(created["plan_id"])
        self.assertEqual(recovered.status, PlanStatus.FAILED)
        self.assertEqual(recovered.execution_outcome, "partial_failure")
        self.assertEqual(
            recovered.configuration_check_status,
            "not_run_after_restart",
        )
        self.assertEqual(
            recovered.failure_information["successful_write_count"], 3
        )
        self.assertEqual(
            recovered.failure_information["verified_write_count"], 3
        )
        self.assertEqual(
            recovered.failure_information["completed_operation_count"], 3
        )
        self.assertEqual(self.gateway.calls, [])


class ApprovalRenderingTests(unittest.TestCase):
    def test_ordered_review_is_visible_non_atomic_and_config_free(self):
        review = {
            "plan_id": "a" * 32,
            "title": "HVAC fixture",
            "description": "Three ordered operations",
            "plan_hash": "b" * 64,
            "plan_version": 1,
            "approval_kind": "apply",
            "risk_level": "medium",
            "expires_at": "2026-07-23T13:00:00+00:00",
            "challenge_expires_at": "2026-07-23T12:15:00+00:00",
            "operation_summaries": [
                {
                    "operation_id": "create_helper",
                    "order": 0,
                    "action": "create",
                    "resource_type": "helper",
                    "helper_type": "input_number",
                    "target_id": "input_number.hvac_target",
                    "depends_on": [],
                    "risk_level": "medium",
                    "semantic_projection": {
                        "status": "complete",
                        "metadata": [
                            {
                                "field": "name",
                                "value": "HVAC target",
                            }
                        ],
                        "actions": [],
                        "controls": [],
                        "redaction_applied": False,
                    },
                    "changed_fields": [
                        {
                            "field": "name",
                            "before": "",
                            "after": "HVAC target",
                        }
                    ],
                }
            ],
        }
        html = _render_review(
            "/api/hassio_ingress/dev14fixture", review, "csrf-value"
        )
        self.assertIn("Ordered configuration operations", html)
        self.assertIn("non-atomic", html)
        self.assertIn("input_number.hvac_target", html)
        self.assertIn("Semantic approval detail", html)
        self.assertIn("HVAC target", html)
        self.assertIn("Approve exact plan", html)
        self.assertNotIn("proposed_config", html)
        self.assertNotIn("current_config", html)

    def test_incomplete_semantic_projection_disables_approval(self):
        review = {
            "plan_id": "a" * 32,
            "plan_hash": "b" * 64,
            "operation_summaries": [
                {
                    "operation_id": "update_script",
                    "order": 0,
                    "action": "update",
                    "resource_type": "script",
                    "target_id": "bounded_script",
                    "semantic_projection": {
                        "status": "incomplete",
                        "metadata": [],
                        "actions": [],
                        "controls": [],
                    },
                }
            ],
        }
        html = _render_review("", review, "csrf-value")
        self.assertIn("semantic operation projection is incomplete", html)
        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        self.assertIn("Reject plan", html)

    def test_redacted_semantic_projection_disables_approval(self):
        review = {
            "plan_id": "a" * 32,
            "plan_hash": "b" * 64,
            "operation_summaries": [
                {
                    "operation_id": "update_script",
                    "order": 0,
                    "action": "update",
                    "resource_type": "script",
                    "target_id": "bounded_script",
                    "semantic_projection": {
                        "status": "complete",
                        "metadata": [],
                        "actions": [],
                        "controls": [],
                        "redaction_applied": True,
                    },
                }
            ],
        }
        html = _render_review("", review, "csrf-value")
        self.assertIn(
            "semantic operation projection is incomplete", html
        )
        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        self.assertIn("Reject plan", html)

    def test_oversized_projection_disables_approval(self):
        review = {
            "plan_id": "a" * 32,
            "plan_hash": "b" * 64,
            "operation_summaries": [
                {
                    "operation_id": f"step-{index}",
                    "order": index,
                    "action": "update",
                    "resource_type": "script",
                    "target_id": f"script-{index}",
                }
                for index in range(9)
            ],
        }
        html = _render_review("", review, "csrf-value")
        self.assertIn("Approval is disabled", html)
        self.assertNotIn("Approve exact plan", html)
        self.assertIn("Reject plan", html)


class NegativeReachabilityTests(unittest.TestCase):
    def test_public_operation_schema_is_explicit_bounded_and_closed(self):
        tool = next(
            item
            for item in get_registered_server()._tool_manager.list_tools()
            if item.name == "create_configuration_plan"
        )
        schema = tool.parameters
        operations = schema["properties"]["operations"]
        self.assertEqual(operations["type"], "array")
        self.assertEqual(operations["minItems"], 1)
        self.assertEqual(operations["maxItems"], 8)
        reference = operations["items"]["$ref"]
        definition_name = reference.rsplit("/", 1)[-1]
        operation = schema["$defs"][definition_name]
        self.assertFalse(operation["additionalProperties"])
        self.assertEqual(
            set(operation["required"]),
            {
                "operation_id",
                "resource_type",
                "action",
                "target_id",
                "proposed_config",
            },
        )
        properties = operation["properties"]
        self.assertEqual(
            properties["resource_type"]["enum"],
            ["automation", "script", "helper"],
        )
        self.assertEqual(
            properties["action"]["enum"], ["create", "update"]
        )
        self.assertEqual(
            properties["helper_type"]["enum"],
            ["input_boolean", "input_number"],
        )
        self.assertEqual(properties["depends_on"]["maxItems"], 8)

    def test_only_governed_configuration_planner_is_newly_exposed(self):
        names = {
            tool.name
            for tool in get_registered_server()._tool_manager.list_tools()
        }
        self.assertIn("create_configuration_plan", names)
        for prohibited in (
            "ha_config_set_automation",
            "ha_config_set_script",
            "ha_config_set_helper",
            "raw_home_assistant_write",
            "delete_script",
            "delete_helper",
        ):
            self.assertNotIn(prohibited, names)


if __name__ == "__main__":
    unittest.main()
