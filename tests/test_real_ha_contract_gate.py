import ast
import copy
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "scripts" / "real_ha_contract_tests.py"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_PATH = ROOT / ".github" / "workflows" / "publish-rc-image.yml"
DEVICE_FIXTURE_ROOT = (
    ROOT
    / "tests"
    / "fixtures"
    / "real_ha_device_migration"
    / "custom_components"
    / "beta23_device_fixture"
)
RESOURCE_TYPES = {
    "automation",
    "script",
    "input_boolean",
    "input_number",
}


def load_workflow(path):
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def workflow_events(workflow):
    return workflow.get("on", workflow.get(True))


def call_name(call):
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def calls_under(node, name=None):
    calls = [item for item in ast.walk(node) if isinstance(item, ast.Call)]
    if name is None:
        return calls
    return [call for call in calls if call_name(call) == name]


def assigned_constructor_names(tree):
    assignments = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Call)
        ):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = call_name(node.value)
    return assignments


def literal_keyword(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                return None
    return None


class _F2AcceptanceGateway:
    """Offline resource fixture matching the disposable HA readback shape."""

    def __init__(self, contract):
        helper = copy.deepcopy(contract.CREATE_CONFIGS["input_boolean"])
        helper["id"] = contract.RESOURCE_IDS["input_boolean"].split(
            ".", 1
        )[1]
        automation = copy.deepcopy(contract.LEGACY_AUTOMATION_CONFIG)
        automation["id"] = contract.RESOURCE_IDS["automation"]
        self.configs = {
            (
                "input_boolean",
                contract.RESOURCE_IDS["input_boolean"],
            ): helper,
            (
                "automation",
                contract.RESOURCE_IDS["automation"],
            ): automation,
        }
        self.write_count = 0
        self.websocket_client = SimpleNamespace(command=self.command)

    async def command(self, _payload):
        return []

    async def read(self, resource_type, resource_id):
        return copy.deepcopy(self.configs.get((resource_type, resource_id)))

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        self.write_count += 1
        stored = copy.deepcopy(approved_config)
        stored["id"] = (
            resource_id.split(".", 1)[1]
            if resource_type in {"input_boolean", "input_number"}
            else resource_id
        )
        self.configs[(resource_type, resource_id)] = stored
        return {"result": "ok", "action": action}

    async def validate_all(self):
        return {"result": "valid", "errors": None, "warnings": None}


class _RejectingF2AcceptanceGateway(_F2AcceptanceGateway):
    """Return one explicit bounded HTTP rejection for the elevated write."""

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        if resource_type == "automation":
            from ha_mcp_engineering.errors import HomeAssistantApiError

            raise HomeAssistantApiError(
                "SECRET_RESPONSE_BODY",
                details={
                    "status": 400,
                    "method": "POST",
                    "endpoint_category": "config/automation",
                    "provider_response_received": True,
                    "response_body": "SECRET_RESPONSE_BODY",
                    "authorization": "Bearer SECRET_TOKEN",
                    "administrator_identity": "SECRET_ADMIN",
                    "approval_token": "SECRET_APPROVAL",
                    "full_plan_id": "SECRET_FULL_PLAN_IDENTIFIER",
                },
            )
        return await super().write(
            action, resource_type, resource_id, approved_config
        )


class _CanonicalizingF2AcceptanceGateway(_F2AcceptanceGateway):
    """Accept the write but return a different bounded canonical form."""

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        stored = copy.deepcopy(approved_config)
        if resource_type == "automation":
            for step in stored.get("action", []):
                if isinstance(step, dict) and "service" in step:
                    step["action"] = step.pop("service")
        return await super().write(
            action, resource_type, resource_id, stored
        )


class _BehavioralMismatchF2AcceptanceGateway(
    _CanonicalizingF2AcceptanceGateway
):
    """Accept the write but return a behaviorally different target."""

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        result = await super().write(
            action, resource_type, resource_id, approved_config
        )
        if resource_type == "automation":
            self.configs[(resource_type, resource_id)]["action"][0][
                "target"
            ] = {"entity_id": "light.behaviorally_different"}
        return result


class _IndeterminateF2AcceptanceGateway(_F2AcceptanceGateway):
    """Lose the elevated write result and make exact readback unavailable."""

    def __init__(self, contract):
        super().__init__(contract)
        self.elevated_write_attempted = False

    async def read(self, resource_type, resource_id):
        if resource_type == "automation" and self.elevated_write_attempted:
            from ha_mcp_engineering.errors import (
                HomeAssistantUnavailableError,
            )

            raise HomeAssistantUnavailableError(
                "SECRET_READBACK_FAILURE",
                details={
                    "method": "GET",
                    "endpoint_category": "config/automation",
                },
            )
        return await super().read(resource_type, resource_id)

    async def write(
        self, action, resource_type, resource_id, approved_config
    ):
        if resource_type == "automation":
            from ha_mcp_engineering.errors import (
                HomeAssistantUnavailableError,
            )

            self.elevated_write_attempted = True
            raise HomeAssistantUnavailableError(
                "SECRET_WRITE_FAILURE",
                details={
                    "method": "POST",
                    "endpoint_category": "config/automation",
                },
            )
        return await super().write(
            action, resource_type, resource_id, approved_config
        )


class RealHomeAssistantDev14GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CONTRACT_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(CONTRACT_PATH))
        cls.functions = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        spec = importlib.util.spec_from_file_location(
            "_real_ha_contract_gate_subject",
            CONTRACT_PATH,
        )
        if spec is None or spec.loader is None:
            raise AssertionError("could not load real Home Assistant contract runner")
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    def function_with_calls(self, required_names):
        matches = []
        for function in self.functions.values():
            names = {call_name(call) for call in calls_under(function)}
            if set(required_names).issubset(names):
                matches.append(function)
        self.assertTrue(
            matches,
            f"no contract function calls all of {sorted(required_names)!r}",
        )
        return matches[0]

    def test_real_resource_gateway_is_wired_to_real_clients(self):
        assignments = assigned_constructor_names(self.tree)
        gateway_calls = calls_under(self.tree, "ConfigurationResourceGateway")
        self.assertEqual(len(gateway_calls), 1)
        gateway = gateway_calls[0]
        argument_names = {
            argument.id
            for argument in gateway.args
            if isinstance(argument, ast.Name)
        } | {
            keyword.value.id
            for keyword in gateway.keywords
            if isinstance(keyword.value, ast.Name)
        }
        constructors = {assignments.get(name) for name in argument_names}
        self.assertIn("HomeAssistantRestClient", constructors)
        self.assertIn("HomeAssistantWebSocketClient", constructors)

        service_calls = calls_under(self.tree, "ChangeGovernanceService")
        self.assertEqual(len(service_calls), 4)
        governed_gateway_constructors = []
        for service_call in service_calls:
            service_argument_names = {
                argument.id
                for argument in service_call.args
                if isinstance(argument, ast.Name)
            } | {
                keyword.value.id
                for keyword in service_call.keywords
                if isinstance(keyword.value, ast.Name)
            }
            call_constructors = {
                assignments.get(name) for name in service_argument_names
            }
            governed_gateway_constructors.extend(call_constructors)
            self.assertTrue(
                {
                    "_ObservedConfigurationGateway",
                    "_LegacyAutomationCompatibilityGateway",
                }
                & call_constructors
            )
        self.assertEqual(
            governed_gateway_constructors.count(
                "_LegacyAutomationCompatibilityGateway"
            ),
            2,
        )
        observed = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "_ObservedConfigurationGateway"
        )
        observed_calls = {call_name(call) for call in calls_under(observed)}
        self.assertTrue(
            {"read", "write", "validate_all"}.issubset(observed_calls)
        )
        governed = self.functions["_run_governed_configuration_contract"]
        gateway_parameter = governed.args.args[0]
        self.assertEqual(
            ast.unparse(gateway_parameter.annotation),
            "ConfigurationResourceGateway",
        )
        legacy_calls = calls_under(self.tree, "AutomationGateway")
        self.assertEqual(len(legacy_calls), 1)
        self.assertIn(
            "HomeAssistantRestClient",
            {
                assignments.get(argument.id)
                for argument in legacy_calls[0].args
                if isinstance(argument, ast.Name)
            },
        )
        legacy_contract = self.function_with_calls(
            {"get", "write", "validate", "normalize_automation"}
        )
        runner_calls = {
            call_name(call)
            for call in calls_under(self.functions["run_contracts"])
        }
        self.assertIn(legacy_contract.name, runner_calls)

    def test_beta37_helper_state_uses_real_f3_lifecycle_on_every_ha_lane(self):
        contract = self.functions[
            "_run_governed_helper_state_contract"
        ]
        contract_text = ast.unparse(contract)
        for required in (
            "HelperStateGateway",
            "DirectHaDependencyProvider",
            "HelperDependencyRiskService",
            "F3RuntimeIntegration",
            "create_helper_state_plan",
            "succeeded_verified",
            "already_applied",
            "redispatch_performed",
            "durable_intent_committed",
            "provider_dispatched",
            "lose_next_response",
            "provider_response_received=False",
            "fallback_count",
        ):
            with self.subTest(required=required):
                self.assertIn(required, contract_text)

        helper_plan_calls = calls_under(
            contract, "create_helper_state_plan"
        )
        self.assertEqual(len(helper_plan_calls), 3)
        for call in helper_plan_calls:
            with self.subTest(call=ast.unparse(call)):
                keywords = {
                    keyword.arg: ast.literal_eval(keyword.value)
                    for keyword in call.keywords
                    if keyword.arg
                    in {"expiration_minutes", "expires_in_seconds"}
                }
                self.assertEqual(keywords["expiration_minutes"], 5)
                self.assertNotIn("expires_in_seconds", keywords)
        self.assertEqual(len(calls_under(contract, "apply")), 4)
        self.assertEqual(
            len(calls_under(contract, "_approve_helper_state_plan")), 3
        )
        self.assertEqual(
            len(calls_under(contract, "_assert_helper_state_task")), 3
        )
        self.assertNotIn("/services/", contract_text)
        self.assertNotIn("ha_call_service", contract_text)

        runner_calls = {
            call_name(call)
            for call in calls_under(self.functions["run_contracts"])
        }
        self.assertIn(contract.name, runner_calls)

    def test_all_four_resources_have_create_read_update_reread_coverage(self):
        resource_ids = self.contract.RESOURCE_IDS
        create_configs = self.contract.CREATE_CONFIGS
        update_configs = self.contract.UPDATE_CONFIGS
        self.assertEqual(set(resource_ids), RESOURCE_TYPES)
        self.assertEqual(set(create_configs), RESOURCE_TYPES)
        self.assertEqual(set(update_configs), RESOURCE_TYPES)

        operations = self.contract._configuration_operations()
        resolved = {
            (
                operation["helper_type"]
                if operation["resource_type"] == "helper"
                else operation["resource_type"]
            ): operation
            for operation in operations
        }
        self.assertEqual(set(resolved), RESOURCE_TYPES)
        for index, resource_type in enumerate(self.contract.RESOURCE_ORDER):
            operation = resolved[resource_type]
            with self.subTest(resource_type=resource_type):
                self.assertEqual(operation["action"], "create")
                self.assertEqual(
                    operation["target_id"],
                    resource_ids[resource_type],
                )
                self.assertEqual(
                    operation["proposed_config"],
                    create_configs[resource_type],
                )
                expected_dependency = (
                    []
                    if index == 0
                    else [operations[index - 1]["operation_id"]]
                )
                self.assertEqual(
                    operation["depends_on"],
                    expected_dependency,
                )

        direct_update = self.function_with_calls({"read", "update"})
        self.assertGreaterEqual(len(calls_under(direct_update, "read")), 1)
        direct_text = ast.unparse(direct_update)
        self.assertIn("RESOURCE_IDS", direct_text)
        self.assertIn("UPDATE_CONFIGS", direct_text)

        runner = self.functions["run_contracts"]
        runner_calls = {call_name(call) for call in calls_under(runner)}
        governed = self.function_with_calls(
            {
                "create_configuration_plan",
                "approve",
                "issue_external_csrf",
                "decide_external_approval",
                "apply",
            }
        )
        governed_calls = {
            call_name(call) for call in calls_under(governed)
        }
        self.assertIn("read", governed_calls)
        self.assertIn("_assert_exact_resource", governed_calls)
        self.assertIn(governed.name, runner_calls)
        self.assertIn(
            "_run_f2_policy_acceptance_contract", runner_calls
        )
        self.assertIn(direct_update.name, runner_calls)

    def test_exact_identity_and_semantic_verification_are_required(self):
        exact = self.function_with_calls(
            {
                "resource_identity_matches",
                "compare_resource_verification",
            }
        )
        self.assertEqual(
            len(calls_under(exact, "compare_resource_verification")),
            1,
        )
        self.assertGreaterEqual(
            len(
                [
                    node
                    for node in ast.walk(exact)
                    if isinstance(node, ast.Assert)
                ]
            ),
            2,
        )
        exact_text = ast.unparse(exact)
        self.assertIn("desired", exact_text)
        self.assertIn("actual", exact_text)
        self.assertIn("semantic_match", exact_text)
        self.assertIn("binding_approved_fingerprint", exact_text)

    def test_configuration_check_uses_the_strict_contract_v2_response(self):
        validate = self.contract._assert_strict_configuration_check
        exact = {
            "result": "valid",
            "errors": None,
            "warnings": None,
        }
        validate(exact)
        invalid = (
            {"result": "ok", "errors": None, "warnings": None},
            {"result": "valid", "errors": [], "warnings": None},
            {"result": "valid", "errors": None},
            {**exact, "extra": None},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(AssertionError):
                    validate(value)
        runner_calls = {
            call_name(call)
            for call in calls_under(self.functions["run_contracts"])
        }
        self.assertIn("validate_all", runner_calls)
        self.assertIn("_assert_strict_configuration_check", runner_calls)

    def test_helper_ids_are_deterministic_and_fixtures_are_behavior_free(self):
        resource_ids = self.contract.RESOURCE_IDS
        create_configs = self.contract.CREATE_CONFIGS
        for helper_type in ("input_boolean", "input_number"):
            with self.subTest(helper_type=helper_type):
                generated = re.sub(
                    r"[ _-]+",
                    "_",
                    create_configs[helper_type]["name"],
                ).lower()
                self.assertEqual(
                    resource_ids[helper_type],
                    f"{helper_type}.{generated}",
                )

        for fixture_set_name in ("CREATE_CONFIGS", "UPDATE_CONFIGS"):
            fixture_set = getattr(self.contract, fixture_set_name)
            script = fixture_set["script"]
            automation = fixture_set["automation"]
            with self.subTest(fixture_set=fixture_set_name, resource="script"):
                self.assertTrue(script["sequence"])
                self.assertTrue(
                    all("event" in action for action in script["sequence"])
                )
            with self.subTest(
                fixture_set=fixture_set_name,
                resource="automation",
            ):
                self.assertTrue(automation["trigger"])
                self.assertTrue(
                    all(
                        trigger.get("platform") == "event"
                        for trigger in automation["trigger"]
                    )
                )
                self.assertTrue(automation["action"])
                self.assertTrue(
                    all("event" in action for action in automation["action"])
                )
            encoded = repr(
                {
                    "script": script,
                    "automation": automation,
                }
            ).lower()
            for prohibited in (
                "'service'",
                "'device_id'",
                "'entity_id'",
                "'target'",
            ):
                self.assertNotIn(prohibited, encoded)

    def test_contract_runs_exact_external_approval_and_idempotent_apply(self):
        lifecycle = self.function_with_calls(
            {
                "create_configuration_plan",
                "approve",
                "issue_external_csrf",
                "decide_external_approval",
                "apply",
            }
        )
        lifecycle_text = ast.unparse(lifecycle)
        for required in (
            "contract_version",
            "awaiting_approval",
            "plan_hash",
            "challenge_id",
            "approved",
            "applied",
            "desired_fingerprint",
            "actual_fingerprint",
            "resulting_fingerprint",
            "RESOURCE_ORDER",
            "observed.mutations",
        ):
            self.assertIn(required, lifecycle_text)

        decisions = calls_under(lifecycle, "decide_external_approval")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            literal_keyword(decisions[0], "approval_kind"),
            "apply",
        )
        self.assertEqual(
            literal_keyword(decisions[0], "decision"),
            "approve",
        )
        self.assertIn(
            "plan_hash",
            ast.unparse(
                next(
                    keyword.value
                    for keyword in decisions[0].keywords
                    if keyword.arg == "expected_plan_hash"
                )
            ),
        )

        apply_calls = calls_under(lifecycle, "apply")
        self.assertEqual(len(apply_calls), 2)
        self.assertEqual(
            ast.dump(apply_calls[0].args[0], include_attributes=False),
            ast.dump(apply_calls[1].args[0], include_attributes=False),
        )
        self.assertEqual(
            ast.dump(apply_calls[0].args[1], include_attributes=False),
            ast.dump(apply_calls[1].args[1], include_attributes=False),
        )
        self.assertGreaterEqual(
            len(
                [
                    node
                    for node in ast.walk(lifecycle)
                    if isinstance(node, ast.Assert)
                ]
            ),
            5,
        )

    def test_f2_disposable_contract_covers_all_policy_classes(self):
        contract = self.functions["_run_f2_policy_acceptance_contract"]
        contract_text = ast.unparse(contract)
        for required in (
            "standard_admin",
            "elevated_admin",
            "safety_critical_owner_authoritative",
            "uncertain_device_target_owner_authoritative",
            "required_acknowledgements'] == ['plan_approval",
            "same_principal_confirmed",
            "duplicate_apply_prevented",
            "task_reused",
            "trace_ids_before",
        ):
            with self.subTest(required=required):
                self.assertIn(required, contract_text)

        self.assertEqual(
            len(calls_under(contract, "create_configuration_plan")), 4
        )
        self.assertEqual(len(calls_under(contract, "apply")), 4)
        self.assertEqual(
            len(calls_under(contract, "fetch_normalized_trace_list")), 2
        )
        self.assertGreaterEqual(
            len(calls_under(contract, "_assert_single_task_dispatch")),
            4,
        )
        self.assertIn("observed.mutations", contract_text)
        self.assertNotIn("/services/", contract_text)
        self.assertNotIn("/events/", contract_text)

        action_helper = self.functions["_decide_f2_action"]
        decision_calls = calls_under(
            action_helper, "decide_external_approval"
        )
        self.assertEqual(len(decision_calls), 1)
        self.assertIn("approval_action", ast.unparse(decision_calls[0]))

        task_helper = self.functions["_assert_single_task_dispatch"]
        task_text = ast.unparse(task_helper)
        self.assertIn("provider_attempt_count", task_text)
        self.assertIn("provider_response_received", task_text)
        self.assertIn("dispatch_attempted", task_text)
        self.assertIn("succeeded_verified", task_text)
        for projection_evidence in (
            "approval_actionable",
            "plans_awaiting_approval",
            "plans_requiring_approval",
            "prohibited_policy_decisions",
        ):
            with self.subTest(projection_evidence=projection_evidence):
                self.assertIn(projection_evidence, contract_text)

    def test_f2_disposable_fixtures_bound_future_actions_without_triggering(self):
        self.assertEqual(
            self.contract.F2_STANDARD_HELPER_CONFIG["icon"],
            "mdi:shield-check",
        )
        elevated = self.contract.F2_ELEVATED_AUTOMATION_CONFIG
        prohibited = self.contract.F2_PROHIBITED_AUTOMATION_CONFIG
        prohibited_device = (
            self.contract.F2_PROHIBITED_DEVICE_TARGET_AUTOMATION_CONFIG
        )
        self.assertEqual(
            elevated["action"][0]["service"], "light.turn_on"
        )
        self.assertEqual(
            prohibited["action"][0]["service"], "lock.unlock"
        )
        self.assertEqual(
            prohibited_device["action"][0]["service"], "lock.unlock"
        )
        self.assertEqual(
            prohibited_device["action"][0]["target"],
            {"device_id": "disposable_nonexistent_lock_device"},
        )
        self.assertNotEqual(
            self.contract.F2_ADMIN_A, self.contract.F2_ADMIN_B
        )

    def test_contract_cleanup_is_awaited_from_finally(self):
        cleanup_candidates = []
        for function in self.functions.values():
            text = ast.unparse(function)
            names = {call_name(call) for call in calls_under(function)}
            if (
                {"request", "command"}.issubset(names)
                and "DELETE" in text
                and "/delete" in text
            ):
                cleanup_candidates.append(function)
        self.assertEqual(len(cleanup_candidates), 1)
        cleanup = cleanup_candidates[0]
        cleanup_text = ast.unparse(cleanup)
        self.assertIn("RESOURCE_ORDER", cleanup_text)
        self.assertIn("RESOURCE_IDS", cleanup_text)
        self.assertIn("resource_type}_id", cleanup_text)
        self.assertGreaterEqual(len(calls_under(cleanup, "read")), 2)
        self.assertEqual(
            set(self.contract.RESOURCE_ORDER),
            RESOURCE_TYPES,
        )

        runner = self.functions["run_contracts"]
        final_calls = []
        awaited_final_calls = []
        for try_node in (
            node for node in ast.walk(runner) if isinstance(node, ast.Try)
        ):
            for statement in try_node.finalbody:
                final_calls.extend(calls_under(statement))
                awaited_final_calls.extend(
                    node.value
                    for node in ast.walk(statement)
                    if isinstance(node, ast.Await)
                    and isinstance(node.value, ast.Call)
                )
        self.assertIn(
            cleanup.name,
            {call_name(call) for call in final_calls},
        )
        self.assertIn(
            cleanup.name,
            {call_name(call) for call in awaited_final_calls},
        )


class RealHomeAssistantF2RunnerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "_real_ha_f2_runner_subject",
            CONTRACT_PATH,
        )
        if spec is None or spec.loader is None:
            raise AssertionError("could not load F2 disposable contract runner")
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    async def _capture_f2_failure(self, gateway):
        no_traces = SimpleNamespace(headers=[])
        with patch.object(
            self.contract,
            "fetch_normalized_trace_list",
            new=AsyncMock(return_value=no_traces),
        ):
            with self.assertRaises(self.contract.GovernanceError) as raised:
                await self.contract._run_f2_policy_acceptance_contract(
                    gateway,
                    "synthetic-f2-runner-token",
                )
        failure = raised.exception
        self.assertEqual(
            failure.code,
            self.contract.ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        diagnostic = getattr(failure, "contract_diagnostic", None)
        self.assertIsInstance(diagnostic, dict)
        return failure, diagnostic

    async def test_all_f2_scenarios_complete_without_redispatch_or_actuation(self):
        gateway = _F2AcceptanceGateway(self.contract)
        no_traces = SimpleNamespace(headers=[])
        with patch.object(
            self.contract,
            "fetch_normalized_trace_list",
            new=AsyncMock(return_value=no_traces),
        ):
            result = await self.contract._run_f2_policy_acceptance_contract(
                gateway,
                "synthetic-f2-runner-token",
            )

        self.assertEqual(
            result["completed_scenarios"],
            [
                "standard_admin",
                "elevated_admin",
                "safety_critical_owner_authoritative",
                "uncertain_device_target_owner_authoritative",
                "persisted_beta6_prohibited_upgrade",
                "persisted_beta6_legacy_expired_upgrade",
            ],
        )
        self.assertEqual(result["configuration_mutation_count"], 2)
        self.assertEqual(result["fallback_count"], 0)
        self.assertFalse(result["physical_actuation_observed"])
        self.assertEqual(gateway.write_count, 2)

    async def test_http_rejection_has_bounded_transport_diagnostic(self):
        failure, diagnostic = await self._capture_f2_failure(
            _RejectingF2AcceptanceGateway(self.contract)
        )

        self.assertEqual(
            diagnostic["diagnostic_classification"],
            "transport_rejected",
        )
        self.assertEqual(diagnostic["scenario"], "elevated_admin")
        self.assertEqual(
            diagnostic["operation_id"], "elevated_automation_update"
        )
        self.assertTrue(diagnostic["write_attempted"])
        self.assertFalse(diagnostic["write_completed"])
        self.assertTrue(diagnostic["readback_attempted"])
        self.assertTrue(diagnostic["readback_completed"])
        self.assertFalse(diagnostic["desired_state_proven"])
        self.assertEqual(diagnostic["attempted_write_count"], 1)
        self.assertEqual(diagnostic["successful_write_count"], 0)
        self.assertEqual(diagnostic["verified_write_count"], 0)
        self.assertEqual(diagnostic["ambiguous_write_count"], 1)
        self.assertEqual(diagnostic["task_state"], "failed_post_dispatch")
        self.assertEqual(
            diagnostic["task_terminal_outcome"], "failed_post_dispatch"
        )
        self.assertTrue(diagnostic["approval_consumed"])
        self.assertEqual(diagnostic["provider_attempt_count"], 1)
        self.assertTrue(diagnostic["provider_response_received"])
        self.assertEqual(diagnostic["observed_mutation"], "not_recorded")
        self.assertEqual(
            diagnostic["cause_chain"][-1],
            {
                "exception_type": "HomeAssistantApiError",
                "error_code": "home_assistant_api_error",
                "http_status": 400,
                "http_method": "POST",
                "endpoint_category": "config/automation",
            },
        )
        rendered = repr(diagnostic)
        for unsafe in (
            "SECRET_RESPONSE_BODY",
            "SECRET_TOKEN",
            "SECRET_ADMIN",
            "SECRET_APPROVAL",
            "SECRET_FULL_PLAN_IDENTIFIER",
            "synthetic-f2-runner-token",
            "light.turn_on",
            "dev14_real_contract_trigger",
            '"condition"',
        ):
            self.assertNotIn(unsafe, rendered)
        self.assertEqual(
            failure.code,
            self.contract.ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )

    async def test_canonicalized_action_alias_completes_all_f2_scenarios(self):
        gateway = _CanonicalizingF2AcceptanceGateway(self.contract)
        no_traces = SimpleNamespace(headers=[])
        with patch.object(
            self.contract,
            "fetch_normalized_trace_list",
            new=AsyncMock(return_value=no_traces),
        ):
            result = await self.contract._run_f2_policy_acceptance_contract(
                gateway,
                "synthetic-f2-runner-token",
            )

        self.assertEqual(
            result["completed_scenarios"],
            [
                "standard_admin",
                "elevated_admin",
                "safety_critical_owner_authoritative",
                "uncertain_device_target_owner_authoritative",
                "persisted_beta6_prohibited_upgrade",
                "persisted_beta6_legacy_expired_upgrade",
            ],
        )
        self.assertEqual(result["configuration_mutation_count"], 2)
        self.assertEqual(result["fallback_count"], 0)
        self.assertFalse(result["physical_actuation_observed"])
        self.assertEqual(gateway.write_count, 2)

    async def test_successful_write_behavioral_mismatch_is_distinguished(self):
        _failure, diagnostic = await self._capture_f2_failure(
            _BehavioralMismatchF2AcceptanceGateway(self.contract)
        )

        self.assertEqual(
            diagnostic["diagnostic_classification"],
            "write_completed_readback_mismatch",
        )
        self.assertTrue(diagnostic["write_attempted"])
        self.assertTrue(diagnostic["write_completed"])
        self.assertTrue(diagnostic["readback_attempted"])
        self.assertTrue(diagnostic["readback_completed"])
        self.assertFalse(diagnostic["desired_state_proven"])
        self.assertEqual(diagnostic["attempted_write_count"], 1)
        self.assertEqual(diagnostic["successful_write_count"], 1)
        self.assertEqual(diagnostic["verified_write_count"], 0)
        self.assertEqual(diagnostic["ambiguous_write_count"], 0)
        self.assertEqual(diagnostic["mismatch_categories"], ["actions"])
        self.assertEqual(diagnostic["observed_mutation"], "recorded")
        self.assertTrue(diagnostic["provider_response_received"])
        self.assertEqual(diagnostic["cause_chain"], [
            {
                "exception_type": "GovernanceError",
                "error_code": "configuration_partial_failure",
            }
        ])

    async def test_indeterminate_write_and_failed_readback_remain_unknown(self):
        _failure, diagnostic = await self._capture_f2_failure(
            _IndeterminateF2AcceptanceGateway(self.contract)
        )

        self.assertEqual(
            diagnostic["diagnostic_classification"],
            "write_outcome_indeterminate",
        )
        self.assertTrue(diagnostic["write_attempted"])
        self.assertFalse(diagnostic["write_completed"])
        self.assertTrue(diagnostic["readback_attempted"])
        self.assertFalse(diagnostic["readback_completed"])
        self.assertFalse(diagnostic["desired_state_proven"])
        self.assertEqual(diagnostic["ambiguous_write_count"], 1)
        self.assertEqual(diagnostic["observed_mutation"], "not_recorded")
        self.assertNotIn(
            "http_status",
            diagnostic["cause_chain"][-1],
        )

    def test_cleanup_failure_is_bounded_without_masking_primary_failure(self):
        failure = self.contract.GovernanceError(
            self.contract.ErrorCode.CONFIGURATION_PARTIAL_FAILURE
        )
        failure.contract_diagnostic = {
            "diagnostic_classification": "write_outcome_indeterminate"
        }
        cleanup_failure = RuntimeError("SECRET_CLEANUP_FAILURE")

        self.contract._attach_cleanup_evidence(
            failure,
            attempted=True,
            succeeded=False,
            failure=cleanup_failure,
        )

        self.assertEqual(
            failure.code,
            self.contract.ErrorCode.CONFIGURATION_PARTIAL_FAILURE,
        )
        self.assertTrue(failure.contract_diagnostic["cleanup_attempted"])
        self.assertFalse(failure.contract_diagnostic["cleanup_succeeded"])
        self.assertEqual(
            failure.contract_diagnostic["cleanup_failure_category"],
            "RuntimeError",
        )
        self.assertNotIn(
            "SECRET_CLEANUP_FAILURE", repr(failure.contract_diagnostic)
        )

    def test_exception_chain_is_capped_redacted_and_cycle_safe(self):
        errors = [RuntimeError(f"SECRET_CHAIN_{index}") for index in range(7)]
        for current, cause in zip(errors, errors[1:]):
            current.__cause__ = cause
        errors[-1].__cause__ = errors[-2]
        errors[1].details = {
            "status": 503,
            "method": "POST",
            "endpoint_category": "config/automation",
            "response_body": "SECRET_RESPONSE_BODY",
            "authorization": "Bearer SECRET_TOKEN",
        }

        chain = self.contract._bounded_exception_chain(errors[0])
        rendered = repr(chain)

        self.assertEqual(len(chain), 5)
        self.assertEqual(chain[1]["http_status"], 503)
        self.assertEqual(chain[1]["http_method"], "POST")
        self.assertEqual(
            chain[1]["endpoint_category"], "config/automation"
        )
        for unsafe in (
            "SECRET_CHAIN",
            "SECRET_RESPONSE_BODY",
            "SECRET_TOKEN",
        ):
            self.assertNotIn(unsafe, rendered)

    def test_diagnostic_identifiers_are_shortened(self):
        plan_id = "planidentifier0123456789"
        task_id = "taskidentifier0123456789"

        self.assertEqual(
            self.contract._short_diagnostic_identifier(plan_id),
            plan_id[:12],
        )
        self.assertEqual(
            self.contract._short_diagnostic_identifier(task_id),
            task_id[:12],
        )
        rendered = repr(
            {
                "plan_id_short": (
                    self.contract._short_diagnostic_identifier(plan_id)
                ),
                "task_id_short": (
                    self.contract._short_diagnostic_identifier(task_id)
                ),
            }
        )
        self.assertNotIn(plan_id, rendered)
        self.assertNotIn(task_id, rendered)

    def test_main_emits_deterministic_bounded_diagnostic_json(self):
        failure = self.contract.GovernanceError(
            self.contract.ErrorCode.CONFIGURATION_PARTIAL_FAILURE
        )
        failure.contract_phase = "f2_policy_acceptance"
        failure.contract_scenario = "elevated_admin"
        failure.contract_diagnostic = {
            "scenario": "elevated_admin",
            "operation_id": "elevated_automation_update",
            "diagnostic_classification": "transport_rejected",
        }
        stderr = io.StringIO()

        with patch.object(
            self.contract,
            "run_contracts",
            new=AsyncMock(side_effect=failure),
        ), redirect_stderr(stderr):
            result = self.contract.main()

        self.assertEqual(result, 1)
        diagnostic = stderr.getvalue()
        self.assertIn("code=configuration_partial_failure", diagnostic)
        self.assertIn(
            'diagnostic={"diagnostic_classification":'
            '"transport_rejected","operation_id":'
            '"elevated_automation_update","scenario":'
            '"elevated_admin"}',
            diagnostic,
        )

    def test_missing_fixture_key_is_reported_with_bounded_context(self):
        failure = KeyError("approval_reference")
        failure.contract_phase = "f2_policy_acceptance"
        failure.contract_scenario = "elevated_admin"
        failure.contract_missing_key = "approval_reference"
        stderr = io.StringIO()
        with patch.object(
            self.contract,
            "run_contracts",
            new=AsyncMock(side_effect=failure),
        ), redirect_stderr(stderr):
            result = self.contract.main()

        self.assertEqual(result, 1)
        diagnostic = stderr.getvalue()
        self.assertIn("phase=f2_policy_acceptance", diagnostic)
        self.assertIn("scenario=elevated_admin", diagnostic)
        self.assertIn("type=KeyError", diagnostic)
        self.assertIn("missing_key=approval_reference", diagnostic)

    def test_unbounded_missing_key_is_not_reported(self):
        failure = KeyError("<unsafe-fixture-key>")
        failure.contract_phase = "f2_policy_acceptance"
        failure.contract_scenario = "standard_admin"
        failure.contract_missing_key = "<unsafe-fixture-key>"
        stderr = io.StringIO()
        with patch.object(
            self.contract,
            "run_contracts",
            new=AsyncMock(side_effect=failure),
        ), redirect_stderr(stderr):
            result = self.contract.main()

        self.assertEqual(result, 1)
        self.assertIn("missing_key=none", stderr.getvalue())
        self.assertNotIn("<unsafe-fixture-key>", stderr.getvalue())


class RealHomeAssistantWorkflowGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = load_workflow(CI_PATH)
        cls.publish = load_workflow(PUBLISH_PATH)
        cls.source = CONTRACT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(cls.source, filename=str(CONTRACT_PATH))
        cls.functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        spec = importlib.util.spec_from_file_location(
            "_real_ha_contract_workflow_subject",
            CONTRACT_PATH,
        )
        if spec is None or spec.loader is None:
            raise AssertionError("could not load real Home Assistant contract runner")
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    def test_real_ha_job_runs_contract_runner_and_always_destroys_config(self):
        job = self.ci["jobs"]["real-ha-contract-tests"]
        self.assertFalse(job["strategy"]["fail-fast"])
        matrix = job["strategy"]["matrix"]["include"]
        self.assertEqual(
            matrix,
            [
                {
                    "lane": "ha-2026-7-2",
                    "ha_version": "2026.7.2",
                    "ha_image": (
                        "ghcr.io/home-assistant/home-assistant:2026.7.2@"
                        "sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b"
                    ),
                },
                {
                    "lane": "ha-2026-8-0",
                    "ha_version": "2026.8.0",
                    "ha_image": (
                        "ghcr.io/home-assistant/home-assistant:2026.8.0@"
                        "sha256:a21689ef0510df9760ee11bab4d6b2fef3ed5c1a29ed9c3224271597a23729eb"
                    ),
                },
                {
                    "lane": "ha-2026-8-1",
                    "ha_version": "2026.8.1",
                    "ha_image": (
                        "ghcr.io/home-assistant/home-assistant:2026.8.1@"
                        "sha256:6340a3de3917a9b19368e767310a96dd090f6a19aca8aeadf87fd1145cec9682"
                    ),
                },
            ],
        )
        self.assertEqual(job["env"]["HA_CONTRACT_VERSION"], "${{ matrix.ha_version }}")
        self.assertEqual(job["env"]["HA_CONTRACT_IMAGE"], "${{ matrix.ha_image }}")
        self.assertEqual(
            job["env"]["HA_FIXTURE_WRITER_IMAGE"], matrix[0]["ha_image"]
        )
        self.assertEqual(
            job["env"]["REAL_HA_UPSTREAM_IMAGE"],
            "ghcr.io/homeassistant-ai/ha-mcp:8.2.0@"
            "sha256:dbcfc0ee8ad02d2190ebde69e5cc6167175c79608bbf1d55cff9034e256face1",
        )
        scripts = [
            str(step["run"])
            for step in job["steps"]
            if "run" in step
        ]
        self.assertTrue(
            any(
                "python scripts/real_ha_contract_tests.py" in script
                for script in scripts
            )
        )
        preparation = next(
            step
            for step in job["steps"]
            if step.get("name")
            == "Prepare exact source and disposable migration configuration"
        )
        startup_script = str(preparation["run"])
        self.assertIn(
            "https://codeload.github.com/homeassistant-ai/ha-mcp/"
            "legacy.tar.gz/098540ba22d495fdb1701daf830d54762350fd46",
            startup_script,
        )
        self.assertIn(
            "945faf6eb7a10c9b687fd6c45f50b09d997d41f5549784f8835f2b29fda181ff",
            startup_script,
        )
        self.assertNotIn("tar.gz/refs/tags/v8.2.0", startup_script)
        self.assertIn(
            "script: !include scripts.yaml",
            startup_script,
        )
        self.assertIn("'http:'", startup_script)
        self.assertIn("'  server_port: 8123'", startup_script)
        self.assertIn("input_boolean: {}", startup_script)
        self.assertIn("input_number: {}", startup_script)
        self.assertIn("beta23_device_fixture", startup_script)
        self.assertIn("custom_components/ha_mcp_tools", startup_script)
        self.assertNotIn("runner.temp", str(job["env"]))
        self.assertIn('>> "$GITHUB_ENV"', startup_script)
        for variable in (
            "REAL_HA_CONTRACT_DIR",
            "REAL_HA_DEVICE_FIXTURE",
            "REAL_HA_TOKEN_FILE",
        ):
            self.assertIn(f'echo "{variable}=', startup_script)
        self.assertIn(
            ': > "$contract_dir/scripts.yaml"',
            startup_script,
        )
        writer = next(
            step
            for step in job["steps"]
            if step.get("name")
            == "Persist migration fixture with exact Home Assistant 2026.7.2"
        )
        writer_script = str(writer["run"])
        self.assertIn("$HA_FIXTURE_WRITER_IMAGE", writer_script)
        self.assertIn("--prepare-migration-fixture", writer_script)
        self.assertIn('docker stop --time 30 "$HA_WRITER_CONTAINER"', writer_script)
        target = next(
            step
            for step in job["steps"]
            if step.get("name")
            == "Start disposable exact target Home Assistant Core"
        )
        self.assertIn("$HA_CONTRACT_IMAGE", str(target["run"]))
        cleanup = next(
            step
            for step in job["steps"]
            if step.get("name")
            == "Sanitize and remove disposable Home Assistant"
        )
        self.assertEqual(cleanup["if"], "always()")
        cleanup_script = str(cleanup["run"])
        for container in (
            "REAL_HA_UPSTREAM_CONTAINER",
            "HA_CONTRACT_CONTAINER",
            "HA_WRITER_CONTAINER",
        ):
            self.assertIn(f'"${container}"', cleanup_script)
        self.assertIn('rm -f "$REAL_HA_TOKEN_FILE"', cleanup_script)
        self.assertIn('sudo rm -rf "$REAL_HA_CONTRACT_DIR"', cleanup_script)
        self.assertFalse(any(step.get("continue-on-error") for step in job["steps"]))

    def test_real_ha_device_fixture_is_a_normal_two_entry_switch_platform(self):
        self.assertEqual(
            {
                path.name
                for path in DEVICE_FIXTURE_ROOT.iterdir()
                if path.name != "__pycache__"
            },
            {"__init__.py", "config_flow.py", "manifest.json", "switch.py"},
        )
        manifest = json.loads(
            (DEVICE_FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["config_flow"])
        switch_source = (DEVICE_FIXTURE_ROOT / "switch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "class Beta23DeviceFixtureSwitch(SwitchEntity)", switch_source
        )
        self.assertIn("identifiers={SHARED_IDENTIFIER}", switch_source)
        self.assertIn("async def async_turn_on", switch_source)
        self.assertIn("async def async_turn_off", switch_source)

    def test_real_ha_contract_covers_migration_lookup_dependency_and_impact(self):
        runner = self.functions["_run_device_migration_contract"]
        call_names = {call_name(call) for call in calls_under(runner)}
        self.assertTrue(
            {
                "EntityDependencyAnalysisService",
                "DirectHaDependencyProvider",
                "DirectHaImpactProvider",
                "ChangeImpactAnalysisService",
                "_call_exact_upstream_get_device",
                "adapt_ha_get_device_composite_result",
            }.issubset(call_names)
        )
        source = ast.get_source_segment(self.source, runner) or ""
        for contract in (
            "config/device_registry/list",
            "config/device_registry/list_composite_splits",
            "config/entity_registry/list",
            "ha_mcp_tools/device_get",
            '"service": "turn_on"',
            '"service": "turn_off"',
            '/states/{entity_id}',
            "primary_config_entry_id",
            "primary_id",
            "device_id",
            "composite_device_id",
            "direct_automation_reference",
            "device_registry_relationship",
        ):
            self.assertIn(contract, source)
        self.assertIn("_assert_device_contract", call_names)
        for scenario in (
            "component_lookup",
            "dependency_index",
            "direct_device_target",
            "impact_analysis",
            "persisted_references",
            "registry_shape",
            "split_projection",
            "upstream_device_identity",
            "upstream_device_shape",
            "upstream_entity_count",
            "upstream_entity_identity",
            "upstream_query_mode",
            "upstream_response_adapter",
            "upstream_success",
        ):
            self.assertIn(f'"{scenario}"', self.source)
        self.assertIn("*_DEVICE_CONTRACT_SCENARIOS", self.source)
        self.assertIn(
            "_assert_http_configuration_contract",
            {call_name(call) for call in calls_under(self.functions["run_contracts"])},
        )
        http_contract = self.functions["_assert_http_configuration_contract"]
        self.assertIn(
            "_docker_command",
            {call_name(call) for call in calls_under(http_contract)},
        )
        http_source = ast.get_source_segment(self.source, http_contract) or ""
        self.assertIn("/config/.storage/http", http_source)
        self.assertNotIn("storage_path.read_text", http_source)
        self.assertIn('stored.get("version") == 1', http_source)
        self.assertIn('stored.get("version") == 2', http_source)

    def test_response_adapter_is_bound_to_the_exact_reviewed_ha_release(self):
        expected_adapter = self.contract._expected_device_response_adapter
        adapter_ids = self.contract.HA_DEVICE_ADAPTER_IDS_BY_HA_VERSION

        self.assertIsNone(
            expected_adapter(home_assistant_version="2026.7.2")
        )
        self.assertEqual(set(adapter_ids), {"2026.8.0", "2026.8.1"})
        self.assertNotEqual(adapter_ids["2026.8.0"], adapter_ids["2026.8.1"])
        for version, adapter_id in adapter_ids.items():
            with self.subTest(version=version):
                self.assertEqual(
                    expected_adapter(home_assistant_version=version),
                    adapter_id,
                )

        with self.assertRaises(ValueError):
            expected_adapter(home_assistant_version="2026.9.0")

    def test_composite_device_contract_evidence_is_bounded_and_structural(self):
        project = self.contract._bounded_device_lookup_shape
        payload = {
            "success": True,
            "queried_by": "device_id",
            "entity_count": 1,
            "entities": [
                {
                    "entity_id": "switch.synthetic_a",
                    "device_id": "split-a",
                    "config_entry_id": "entry-a",
                    "platform": "synthetic_fixture",
                    "name": "must-not-be-projected",
                    "attributes": {"secret": "must-not-be-projected"},
                }
            ],
            "device": {
                "device_id": "legacy-composite-id",
                "config_entries": ["entry-a", "entry-b"],
                "entities": [],
                "connections": [["synthetic", "must-not-be-projected"]],
                "identifiers": [["synthetic", "must-not-be-projected"]],
                "name": "must-not-be-projected",
            },
            "unreviewed_body": {"secret": "must-not-be-projected"},
        }

        evidence = project(payload)

        encoded = json.dumps(evidence, sort_keys=True)
        self.assertEqual(evidence["entity_count"], 1)
        self.assertEqual(evidence["entities"][0]["device_id"], "split-a")
        self.assertEqual(
            evidence["device"]["config_entries"], ["entry-a", "entry-b"]
        )
        self.assertIn("connections", evidence["device"]["fields"])
        self.assertIn("identifiers", evidence["device"]["fields"])
        self.assertIn("unreviewed_body", evidence["fields"])
        self.assertNotIn("must-not-be-projected", encoded)
        self.assertNotIn("secret", encoded)

    def test_validate_job_regenerates_every_beta6_compatibility_fixture(self):
        validate = self.ci["jobs"]["validate"]
        step = next(
            item
            for item in validate["steps"]
            if item.get("name")
            == "Regenerate historical compatibility fixtures"
        )
        script = str(step["run"])
        self.assertIn(
            "5c7eebf962837f85f2309b1b5099401fb075cd6e",
            script,
        )
        self.assertIn("git worktree add --detach", script)
        self.assertIn("git worktree remove", script)
        self.assertIn("cmp", script)
        for fixture in (
            "beta6_prohibited_superseded_contract_v2_a.json",
            "beta6_prohibited_superseded_contract_v2_b.json",
            "beta6_prohibited_superseded_contract_v2_provenance.json",
            "beta6_legacy_prohibited_expired_automation_a.json",
            "beta6_legacy_prohibited_expired_automation_b.json",
            "beta6_legacy_prohibited_expired_automation_provenance.json",
        ):
            self.assertIn(fixture, script)

    def test_automatic_publication_requires_the_reusable_complete_ci(self):
        events = workflow_events(self.ci)
        self.assertIn("workflow_call", events)
        jobs = self.publish["jobs"]
        self.assertEqual(
            jobs["validate"]["uses"],
            "./.github/workflows/ci.yml",
        )
        self.assertEqual(jobs["detect-release"]["needs"], "validate")
        self.assertIn("validate", jobs["promote"]["needs"])


if __name__ == "__main__":
    unittest.main()
