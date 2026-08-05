"""F3-C2 capability, preparation, preflight, and lifecycle conformance."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.operational_adapter import (
    OperationalAdapterError,
    execute_operational,
    validate_execution_binding,
)
from ha_mcp_engineering.f3.operational_models import (
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    OPERATIONAL_PROVIDER_CONTRACT_MODEL,
    PROVIDER_OPERATIONS,
    RELOAD_PROVIDER_TARGETS,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SUPPORTED_OPERATIONS,
    OperationalPreparationRequest,
)
from ha_mcp_engineering.governance.models import ApprovalState

from tests.f3_operational_fixtures import (
    PLAN_HASH,
    PROVIDER_IDENTITY_HASH,
    PROVIDER_SLUG,
    PUBLIC_TASK_ID,
    TASK_ID,
    execution_identity,
    make_context,
    make_executor,
    prepare_context,
)
from tests.f3_configuration_fixtures import (  # noqa: E402
    SyntheticConfigurationGateway,
    adapter_for as configuration_adapter_for,
    proposal_for as configuration_proposal_for,
)


class OperationalCapabilityAndPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_capability_identities_are_closed_and_operation_specific(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        self.assertEqual(tuple(context.adapter.capabilities.supported_operations), SUPPORTED_OPERATIONS)
        self.assertEqual(set(context.adapter.capability_descriptors), set(SUPPORTED_OPERATIONS))
        for operation, capability in context.adapter.capability_descriptors.items():
            with self.subTest(operation=operation):
                self.assertEqual(capability.capability_id, CAPABILITY_IDENTITIES[operation])
                self.assertEqual(capability.provider_operation, PROVIDER_OPERATIONS[operation])
                self.assertEqual(capability.provider_contract_model, OPERATIONAL_PROVIDER_CONTRACT_MODEL)
                self.assertFalse(capability.rollback_supported)
                self.assertTrue(capability.recovery_supported)

    async def test_prepare_preserves_current_plan_semantics_for_every_operation(self):
        for operation in SUPPORTED_OPERATIONS:
            with self.subTest(operation=operation):
                context = make_context(self.root / operation, operation)
                prepared = await prepare_context(context)
                operational = context.plan.operational
                assert operational is not None
                self.assertEqual(prepared.operation, operation)
                self.assertEqual(prepared.target.target_type, context.plan.target_type)
                self.assertEqual(prepared.target.target_id, context.plan.target_id)
                self.assertEqual(prepared.policy_class, context.plan.policy_decision.policy_class.value)
                self.assertEqual(prepared.risk_delta, context.plan.policy_decision.risk_delta.value)
                self.assertEqual(
                    prepared.physical_consequence,
                    context.plan.policy_decision.physical_consequence.value,
                )
                self.assertEqual(prepared.risk_level, context.plan.risk.level.value)
                self.assertEqual(prepared.provider_id, operational.provider)
                self.assertEqual(prepared.provider_evidence, operational.provider_capability_evidence)
                self.assertEqual(prepared.expected_effect_descriptions, tuple(operational.expected_effects))
                self.assertEqual(prepared.warnings, tuple(context.plan.warnings))
                self.assertEqual(prepared.limitations, tuple(operational.limitations))
                self.assertFalse(prepared.rollback_available)

    async def test_exact_provider_arguments_are_the_only_reachable_shapes(self):
        expectations = {
            CREATE_FULL_BACKUP: {
                "scope": "snapshot",
                "action": "create",
                "name": "Synthetic Backup",
            },
            CONTROLLED_RELOAD: {"target": "automations"},
            RESTART_ADDON: {"slug": "local_example", "action": "restart"},
            RESTART_HOME_ASSISTANT: {"confirm": True},
        }
        prohibited = {
            "restore",
            "delete",
            "download",
            "start",
            "stop",
            "install",
            "uninstall",
            "update",
            "options",
            "service_data",
            "entry_id",
        }
        for operation, expected in expectations.items():
            with self.subTest(operation=operation):
                prepared = await prepare_context(make_context(self.root / operation, operation))
                self.assertEqual(prepared.provider_arguments, expected)
                self.assertFalse(prohibited & set(prepared.provider_arguments))

    async def test_every_supported_reload_domain_maps_exactly(self):
        for domain, upstream in RELOAD_PROVIDER_TARGETS.items():
            with self.subTest(domain=domain):
                context = make_context(self.root / domain, CONTROLLED_RELOAD, target_id=domain)
                prepared = await prepare_context(context)
                self.assertEqual(prepared.provider_arguments, {"target": upstream})

    async def test_unsupported_reload_domain_fails_during_preparation(self):
        context = make_context(self.root, CONTROLLED_RELOAD, target_id="light")
        with self.assertRaisesRegex(ValueError, "unsupported reload target"):
            await prepare_context(context)

    async def test_unknown_operation_is_rejected_before_preflight(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        context.plan.operation = "unknown_operation"  # type: ignore[assignment]
        context.plan.operational.operation = "unknown_operation"
        with self.assertRaises(OperationalAdapterError) as caught:
            await prepare_context(context)
        self.assertEqual(caught.exception.category, "unknown_operation")

    async def test_approval_hash_policy_and_target_drift_fail_closed(self):
        cases = {
            "approval": lambda context: setattr(context.plan.approval, "bound_plan_hash", "e" * 64),
            "policy": lambda context: setattr(
                context.plan,
                "policy_decision",
                replace(context.plan.policy_decision, policy_decision_hash="f" * 64),
            ),
            "target": lambda context: setattr(context.plan.target, "target_id", "other"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                context = make_context(self.root / name, RESTART_HOME_ASSISTANT)
                mutate(context)
                with self.assertRaises((OperationalAdapterError, ValueError, AttributeError)):
                    await prepare_context(context)

    async def test_provider_slug_is_explicit_evidence_not_endpoint_inference(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        with self.assertRaisesRegex(ValueError, "provider slug"):
            await context.adapter.prepare(
                OperationalPreparationRequest(
                    plan=context.plan,
                    expected_plan_hash=PLAN_HASH,
                    public_task_id=PUBLIC_TASK_ID,
                    child_execution_id=TASK_ID,
                    authoritative_provider_slug="https://addon.invalid",
                    provider_identity_evidence_hash=PROVIDER_IDENTITY_HASH,
                )
            )

    async def test_prepare_is_read_only(self):
        context = make_context(self.root, RESTART_ADDON)
        before = context.plan.to_dict()
        prepared = await prepare_context(context)
        self.assertEqual(context.plan.to_dict(), before)
        self.assertEqual(context.lifecycle.provider_dispatches, 0)
        self.assertEqual(context.lifecycle.simulated_effects, 0)
        self.assertEqual(context.evidence.read_count, 0)
        self.assertEqual(context.approval.callback_count, 0)

    async def test_consumed_public_approval_cannot_prepare_a_new_child(self):
        context = make_context(self.root, RESTART_ADDON)
        context.plan.approval.state = ApprovalState.CONSUMED
        with self.assertRaises(OperationalAdapterError) as caught:
            await prepare_context(context)
        self.assertEqual(caught.exception.category, "approval_not_available")
        self.assertEqual(context.lifecycle.provider_dispatches, 0)


class OperationalLockAndPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_complete_lock_sets_are_canonical(self):
        expected = {
            CREATE_FULL_BACKUP: {
                "addon:local_ha_mcp": "shared",
                "backup:local_full_backup": "exclusive",
                "home_assistant:core": "shared",
            },
            CONTROLLED_RELOAD: {
                "addon:local_ha_mcp": "shared",
                "home_assistant:core": "shared",
                "reload:automation": "exclusive",
            },
            RESTART_ADDON: {
                "addon:local_example": "exclusive",
                "addon:local_ha_mcp": "shared",
                "home_assistant:core": "shared",
            },
            RESTART_HOME_ASSISTANT: {
                "addon:local_ha_mcp": "shared",
                "home_assistant:core": "exclusive",
            },
        }
        for operation, keys in expected.items():
            with self.subTest(operation=operation):
                context = make_context(self.root / operation, operation)
                prepared = await prepare_context(context)
                requests = context.adapter.lock_requests(prepared)
                self.assertEqual([item.key for item in requests], sorted(keys))
                self.assertEqual(
                    {item.key: item.mode.value for item in requests}, keys
                )

    async def test_upstream_addon_restart_unions_provider_and_resource_lock(self):
        context = make_context(
            self.root,
            RESTART_ADDON,
            target_id=PROVIDER_SLUG,
            target_class="upstream_ha_mcp_addon",
        )
        prepared = await prepare_context(context)
        request = {
            item.key: item for item in context.adapter.lock_requests(prepared)
        }[f"addon:{PROVIDER_SLUG}"]
        self.assertEqual(request.mode.value, "exclusive")
        self.assertEqual(
            tuple(scope.value for scope in request.scopes),
            ("provider", "resource"),
        )
        self.assertEqual(
            request.reason_codes,
            ("installed_addon_restart", "upstream_provider_dependency"),
        )

    async def test_reload_keys_exactly_conflict_with_beta18_configuration_writes(self):
        for domain in RELOAD_PROVIDER_TARGETS:
            with self.subTest(domain=domain):
                operational_context = make_context(
                    self.root / domain,
                    CONTROLLED_RELOAD,
                    target_id=domain,
                )
                operational = await prepare_context(operational_context)
                operational_reload = next(
                    request
                    for request in operational_context.adapter.lock_requests(
                        operational
                    )
                    if request.key.startswith("reload:")
                )
                config_gateway = SyntheticConfigurationGateway()
                config_adapter = configuration_adapter_for(
                    domain, "create", config_gateway
                )
                configuration = await config_adapter.prepare(
                    configuration_proposal_for(domain, "create")
                )
                config_reload = next(
                    request
                    for request in config_adapter.lock_requests(configuration)
                    if request.key.startswith("reload:")
                )
                self.assertEqual(
                    operational_reload.key, config_reload.key
                )
                self.assertEqual(operational_reload.mode.value, "exclusive")
                self.assertEqual(config_reload.mode.value, "shared")

    async def test_unrelated_reload_domains_and_addons_remain_compatible(self):
        reload_keys = []
        for domain in ("automation", "script"):
            context = make_context(
                self.root / f"reload-{domain}",
                CONTROLLED_RELOAD,
                target_id=domain,
            )
            prepared = await prepare_context(context)
            reload_keys.append(
                next(
                    request.key
                    for request in context.adapter.lock_requests(prepared)
                    if request.key.startswith("reload:")
                )
            )
        self.assertEqual(len(set(reload_keys)), 2)

        addon_keys = []
        for slug in ("local_one", "local_two"):
            context = make_context(
                self.root / f"addon-{slug}",
                RESTART_ADDON,
                target_id=slug,
            )
            prepared = await prepare_context(context)
            addon_keys.append(
                next(
                    request.key
                    for request in context.adapter.lock_requests(prepared)
                    if request.mode.value == "exclusive"
                )
            )
        self.assertEqual(len(set(addon_keys)), 2)

    async def test_manual_review_holds_are_exact_and_deadlines_do_not_release(self):
        expected = {
            CREATE_FULL_BACKUP: (("backup:local_full_backup",), 86_400),
            CONTROLLED_RELOAD: (("reload:automation",), 900),
            RESTART_ADDON: (("addon:local_example",), 1_800),
            RESTART_HOME_ASSISTANT: (("home_assistant:core",), 1_800),
        }
        for operation, value in expected.items():
            prepared = await prepare_context(make_context(self.root / operation, operation))
            self.assertEqual(
                (prepared.selective_hold_keys, prepared.evidence_deadline_seconds),
                value,
            )

    async def test_exact_acquired_lock_set_is_required(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        requests = context.adapter.lock_requests(prepared)
        result = await context.adapter.preflight(prepared, acquired_locks=requests[:-1])
        self.assertFalse(result.eligible)
        self.assertEqual(result.outcome, "lock_conflict")
        self.assertEqual(result.mismatch_fields, ("lock_set",))
        self.assertEqual(context.backup.provider_dispatches, 0)

    async def test_authority_task_policy_approval_and_storage_matrix_rejects(self):
        fields = {
            "child_execution_id": "wrong-task",
            "plan_hash": "f" * 64,
            "authorization_evidence_status": "invalid",
            "execution_task_storage_status": "unhealthy",
            "approval_bundle_hash": "f" * 64,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                context = make_context(self.root / field, CREATE_FULL_BACKUP)
                context.adapter.authority_reader = lambda _prepared, f=field, v=value, a=context.authority: replace(a, **{f: v})
                prepared = await prepare_context(context)
                result = await context.adapter.preflight(
                    prepared,
                    acquired_locks=context.adapter.lock_requests(prepared),
                )
                self.assertFalse(result.eligible)
                self.assertEqual(context.backup.provider_dispatches, 0)
                self.assertEqual(context.approval.callback_count, 0)

    async def test_elevated_acknowledgement_is_required_for_restarts(self):
        for operation in (RESTART_ADDON, RESTART_HOME_ASSISTANT):
            context = make_context(self.root / operation, operation)
            context.adapter.authority_reader = lambda _prepared, a=context.authority: replace(
                a, elevated_acknowledgement_bound=False
            )
            prepared = await prepare_context(context)
            result = await context.adapter.preflight(
                prepared, acquired_locks=context.adapter.lock_requests(prepared)
            )
            self.assertFalse(result.eligible)
            self.assertIn(
                "elevated_acknowledgement_binding", result.mismatch_fields
            )
            self.assertEqual(context.approval.callback_count, 0)

    async def test_home_assistant_restart_requires_all_persistent_storage(self):
        for field in ("governance_storage_status", "audit_storage_status", "execution_task_storage_status"):
            context = make_context(self.root / field, RESTART_HOME_ASSISTANT)
            context.adapter.authority_reader = lambda _prepared, f=field, a=context.authority: replace(a, **{f: "unhealthy"})
            prepared = await prepare_context(context)
            result = await context.adapter.preflight(
                prepared, acquired_locks=context.adapter.lock_requests(prepared)
            )
            self.assertFalse(result.eligible)
            self.assertEqual(context.lifecycle.provider_dispatches, 0)

    async def test_backup_stale_inventory_rejects_before_intent(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        context.backup.baseline["backup_ids"].append("external-backup")
        result = await context.adapter.preflight(
            prepared, acquired_locks=context.adapter.lock_requests(prepared)
        )
        self.assertFalse(result.eligible)
        self.assertIn("backup_inventory", result.mismatch_fields)

    async def test_reload_revalidates_config_service_and_inventory(self):
        mutations = {
            "configuration": lambda baseline: baseline["configuration_validation"].update(status="invalid"),
            "service": lambda baseline: baseline.update(service_available=False),
            "inventory": lambda baseline: baseline["domain_evidence"].update(matching_entity_count=3),
        }
        for name, mutate in mutations.items():
            context = make_context(self.root / name, CONTROLLED_RELOAD)
            prepared = await prepare_context(context)
            mutate(context.lifecycle.baseline)
            result = await context.adapter.preflight(
                prepared, acquired_locks=context.adapter.lock_requests(prepared)
            )
            self.assertFalse(result.eligible)
            self.assertEqual(context.lifecycle.provider_dispatches, 0)

    async def test_addon_identity_version_repository_endpoint_and_state_drift_reject(self):
        mutations = {
            "version": lambda baseline: baseline["addon"].update(version="9.9.9"),
            "repository": lambda baseline: baseline["target_identity"].update(resolved_repository="wrong"),
            "endpoint": lambda baseline: baseline["upstream_addon_identity"].update(endpoint_host="wrong-host"),
            "stopped": lambda baseline: baseline["addon"].update(state="stopped"),
        }
        for name, mutate in mutations.items():
            context = make_context(self.root / name, RESTART_ADDON)
            prepared = await prepare_context(context)
            mutate(context.lifecycle.baseline)
            result = await context.adapter.preflight(
                prepared, acquired_locks=context.adapter.lock_requests(prepared)
            )
            self.assertFalse(result.eligible)
            self.assertEqual(context.lifecycle.provider_dispatches, 0)

    async def test_provider_release_and_protocol_drift_fail_closed(self):
        for field, value in (("server_version", "8.0.1"), ("protocol_version", "2099-01-01")):
            context = make_context(self.root / field, RESTART_ADDON)
            prepared = await prepare_context(context)
            context.lifecycle.evidence[field] = value
            result = await context.adapter.preflight(
                prepared, acquired_locks=context.adapter.lock_requests(prepared)
            )
            self.assertFalse(result.eligible)
            self.assertIn("provider_contract", result.mismatch_fields)


class OperationalExecutorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_f3a_executor_success_dispatches_and_mutates_once(self):
        for operation in (CREATE_FULL_BACKUP, CONTROLLED_RELOAD, RESTART_ADDON):
            with self.subTest(operation=operation):
                root = self.root / operation
                context = make_context(root, operation)
                prepared = await prepare_context(context)
                result = await execute_operational(
                    make_executor(root),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(), approval_consumption=context.approval.consume,
                )
                gateway = context.backup if operation == CREATE_FULL_BACKUP else context.lifecycle
                self.assertEqual(result.outcome, "succeeded_verified")
                self.assertEqual(result.dispatch_count, 1)
                self.assertEqual(gateway.provider_dispatches, 1)
                self.assertEqual(gateway.simulated_effects, 1)

    async def test_home_assistant_restart_recovers_outage_then_verifies(self):
        context = make_context(self.root, RESTART_HOME_ASSISTANT)
        prepared = await prepare_context(context)
        executor = make_executor(self.root)
        first = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(), approval_consumption=context.approval.consume,
        )
        self.assertEqual(first.outcome, "observing")
        second = first
        for _ in range(4):
            second = await execute_operational(
                executor,
                adapter=context.adapter,
                prepared=prepared,
                identity=execution_identity(),
                approval_consumption=context.approval.consume,
            )
            if second.terminal:
                break
        self.assertEqual(second.outcome, "succeeded_verified")
        self.assertEqual(context.lifecycle.provider_dispatches, 1)
        self.assertEqual(context.lifecycle.simulated_effects, 1)

    async def test_execution_identity_is_bound_before_f3a_claim(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        wrong = replace(execution_identity(), task_id="different-task")
        with self.assertRaises(OperationalAdapterError) as caught:
            validate_execution_binding(prepared, wrong)
        self.assertEqual(caught.exception.category, "execution_identity_mismatch")
        self.assertEqual(context.backup.provider_dispatches, 0)

    async def test_terminal_duplicate_returns_existing_without_second_dispatch(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        executor = make_executor(self.root)
        first = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )
        second = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )
        self.assertEqual(first.outcome, second.outcome)
        self.assertTrue(second.duplicate_execution)
        self.assertEqual(context.backup.provider_dispatches, 1)
        self.assertEqual(context.backup.simulated_effects, 1)


if __name__ == "__main__":
    unittest.main()
