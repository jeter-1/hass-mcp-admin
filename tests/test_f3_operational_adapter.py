"""F3-C2 capability, preparation, preflight, and lifecycle conformance."""

from __future__ import annotations

from dataclasses import replace
import json
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
from ha_mcp_engineering.f3.contracts import (
    LockMode,
    RecoveryContext,
)
from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict
from ha_mcp_engineering.f3.models import LockOwner, LockTiming
from ha_mcp_engineering.f3.operational_models import (
    CAPABILITY_IDENTITIES,
    CONTROLLED_RELOAD,
    CREATE_FULL_BACKUP,
    OPERATIONAL_PREPARED_AUTHORITY_MODEL,
    PROVIDER_CONTRACT_MODELS,
    PROVIDER_OPERATIONS,
    RELOAD_PROVIDER_TARGETS,
    RESTART_ADDON,
    RESTART_HOME_ASSISTANT,
    SET_INPUT_BOOLEAN_STATE,
    SUPPORTED_OPERATIONS,
    OperationalPreparationRequest,
    canonical_json,
    recompute_operational_prepared_hash,
    stable_hash,
    validate_prepared_operational_authority,
)
from ha_mcp_engineering.f3.persistence import DurableExecutionRepository
from ha_mcp_engineering.f3_configuration.locks import (
    helper_dependency_lock_key,
    lock_set_hash,
    resource_lock_key,
    unconstrained_helper_dependency_lock_key,
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
    valid_config as configuration_valid_config,
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
                self.assertEqual(
                    capability.provider_contract_model,
                    PROVIDER_CONTRACT_MODELS[operation],
                )
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

    async def test_prepare_uses_one_canonical_authority_payload_and_child_binding(self):
        for operation in SUPPORTED_OPERATIONS:
            with self.subTest(operation=operation):
                context = make_context(self.root / operation, operation)
                prepared = await prepare_context(context)
                validate_prepared_operational_authority(prepared)
                self.assertEqual(
                    prepared.prepared_operation_hash,
                    recompute_operational_prepared_hash(prepared),
                )
                self.assertEqual(
                    context.authority.prepared_authority_model,
                    OPERATIONAL_PREPARED_AUTHORITY_MODEL,
                )
                self.assertEqual(
                    context.authority.prepared_operation_hash,
                    prepared.prepared_operation_hash,
                )

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
            SET_INPUT_BOOLEAN_STATE: {
                "domain": "input_boolean",
                "service": "turn_on",
                "target": {"entity_id": "input_boolean.synthetic_exact"},
            },
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


class OperationalPreparedAuthorityIntegrityTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _rehash(prepared):
        return replace(
            prepared,
            prepared_operation_hash=recompute_operational_prepared_hash(
                prepared
            ),
        )

    async def test_stale_hash_tamper_matrix_rejects_before_claim_or_approval(self):
        mutations = {
            "requested_name": lambda p: replace(
                p, requested_name="Changed Backup"
            ),
            "target_id": lambda p: replace(
                p, target=replace(p.target, target_id="other_backup")
            ),
            "target_class": lambda p: replace(p, target_class="other"),
            "provider_id": lambda p: replace(
                p, provider_id="upstream_operational_lifecycle"
            ),
            "provider_operation": lambda p: replace(
                p, provider_operation="ha_restart"
            ),
            "provider_arguments_json": lambda p: replace(
                p,
                provider_arguments_json=canonical_json(
                    {
                        "scope": "snapshot",
                        "action": "create",
                        "name": "Changed Backup",
                    }
                ),
            ),
            "provider_arguments_hash": lambda p: replace(
                p, provider_arguments_hash="f" * 64
            ),
            "provider_evidence_json": lambda p: replace(
                p,
                provider_evidence_json=canonical_json(
                    {**p.provider_evidence, "server_name": "tampered"}
                ),
            ),
            "baseline_json": lambda p: replace(
                p,
                baseline_json=canonical_json(
                    {**p.baseline, "tampered": True}
                ),
            ),
            "verification_model": lambda p: replace(
                p, verification_contract_model="unknown-verification-v1"
            ),
            "verification_contract_json": lambda p: replace(
                p,
                verification_contract_json=canonical_json(
                    {
                        **json.loads(p.verification_contract_json),
                        "required": ["tampered"],
                    }
                ),
            ),
            "verification_contract_hash": lambda p: replace(
                p, verification_contract_hash="f" * 64
            ),
            "expected_effect_codes": lambda p: replace(
                p, expected_effects=("tampered_effect",)
            ),
            "policy_class": lambda p: replace(
                p, policy_class="elevated_admin"
            ),
            "risk_delta": lambda p: replace(p, risk_delta="high"),
            "physical_consequence": lambda p: replace(
                p, physical_consequence="direct"
            ),
            "risk_level": lambda p: replace(p, risk_level="high"),
            "selective_hold_key": lambda p: replace(
                p, selective_hold_keys=("backup:other",)
            ),
            "evidence_deadline_seconds": lambda p: replace(
                p, evidence_deadline_seconds=86_399
            ),
            "rollback_flag": lambda p: replace(
                p, rollback_available=True
            ),
            "prepared_operation_hash": lambda p: replace(
                p, prepared_operation_hash="f" * 64
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                root = self.root / name
                context = make_context(root, CREATE_FULL_BACKUP)
                prepared = await prepare_context(context)
                tampered = mutate(prepared)
                with self.assertRaises(OperationalAdapterError) as caught:
                    await execute_operational(
                        make_executor(root, prepared=tampered),
                        adapter=context.adapter,
                        prepared=tampered,
                        identity=execution_identity(),
                        approval_consumption=context.approval.consume,
                    )
                self.assertEqual(
                    caught.exception.category,
                    "prepared_operation_integrity",
                )
                self.assertIsNone(DurableExecutionRepository(root).get(TASK_ID))
                self.assertEqual(context.approval.callback_count, 0)
                self.assertEqual(context.backup.provider_dispatches, 0)
                self.assertEqual(context.backup.simulated_effects, 0)

    async def test_recomputed_hash_tampering_hits_authoritative_child_binding(self):
        cases = []

        for name in ("requested_backup_name", "provider_arguments"):
            root = self.root / name
            context = make_context(root, CREATE_FULL_BACKUP)
            prepared = await prepare_context(context)
            requested_name = f"Changed {name}"
            arguments_json = canonical_json(
                {
                    "scope": "snapshot",
                    "action": "create",
                    "name": requested_name,
                }
            )
            changed = replace(
                prepared,
                requested_name=requested_name,
                provider_arguments_json=arguments_json,
                provider_arguments_hash=stable_hash(arguments_json),
            )
            cases.append((name, root, context, self._rehash(changed)))

        root = self.root / "baseline"
        context = make_context(root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        changed = replace(
            prepared,
            baseline_json=canonical_json(
                {**prepared.baseline, "external_change": "bounded"}
            ),
        )
        cases.append(("baseline", root, context, self._rehash(changed)))

        root = self.root / "verification_contract"
        context = make_context(root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        verification = json.loads(prepared.verification_contract_json)
        verification["required"] = [
            *verification["required"],
            "additional_bounded_readback",
        ]
        verification_json = canonical_json(verification)
        changed = replace(
            prepared,
            verification_contract_json=verification_json,
            verification_contract_hash=stable_hash(verification_json),
        )
        cases.append(
            ("verification_contract", root, context, self._rehash(changed))
        )

        root = self.root / "selective_hold"
        context = make_context(root, CONTROLLED_RELOAD, target_id="automation")
        await prepare_context(context)
        foreign = make_context(
            root / "foreign", CONTROLLED_RELOAD, target_id="script"
        )
        changed = await prepare_context(foreign)
        cases.append(("selective_hold", root, context, changed))

        root = self.root / "evidence_deadline"
        context = make_context(root, CREATE_FULL_BACKUP)
        await prepare_context(context)
        foreign = make_context(root / "foreign", CONTROLLED_RELOAD)
        changed = await prepare_context(foreign)
        cases.append(("evidence_deadline", root, context, changed))

        for name, root, context, tampered in cases:
            with self.subTest(case=name):
                validate_prepared_operational_authority(tampered)
                self.assertEqual(
                    tampered.prepared_operation_hash,
                    recompute_operational_prepared_hash(tampered),
                )
                preflight = await context.adapter.preflight(
                    tampered,
                    acquired_locks=context.adapter.lock_requests(tampered),
                )
                self.assertFalse(preflight.eligible)
                self.assertEqual(
                    preflight.diagnostic_codes,
                    ("prepared_operation_authority",),
                )
                self.assertIn(
                    "prepared_operation_authority",
                    preflight.mismatch_fields,
                )
                result = await execute_operational(
                    make_executor(root, prepared=tampered),
                    adapter=context.adapter,
                    prepared=tampered,
                    identity=execution_identity(),
                    approval_consumption=context.approval.consume,
                )
                record = DurableExecutionRepository(root).get(TASK_ID)
                self.assertEqual(result.outcome, "preflight_rejected")
                self.assertEqual(result.dispatch_count, 0)
                self.assertIsNotNone(record)
                self.assertIsNone(record.dispatch_intent)
                self.assertEqual(context.approval.callback_count, 0)
                self.assertEqual(context.backup.provider_dispatches, 0)
                self.assertEqual(context.lifecycle.provider_dispatches, 0)
                self.assertEqual(context.backup.simulated_effects, 0)
                self.assertEqual(context.lifecycle.simulated_effects, 0)

    async def test_every_operational_boundary_revalidates_prepared_authority(self):
        context = make_context(self.root, CREATE_FULL_BACKUP)
        prepared = await prepare_context(context)
        tampered = replace(
            prepared,
            baseline_json=canonical_json(
                {**prepared.baseline, "tampered": True}
            ),
        )
        callback_count = 0

        async def before_dispatch() -> None:
            nonlocal callback_count
            callback_count += 1

        with self.assertRaises(OperationalAdapterError):
            context.adapter.lock_requests(tampered)
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.preflight(tampered, acquired_locks=())
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.dispatch(
                tampered, None, before_dispatch=before_dispatch
            )
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.observe(tampered, None)
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.verify(tampered, None)
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.recover(
                tampered,
                context=RecoveryContext(
                    dispatch_intent_recorded=True,
                    provider_invocation_may_have_occurred=True,
                    provider_response_received=False,
                    prior_observation_attempts=0,
                    prior_verification_attempts=0,
                    post_dispatch_deadline=None,
                ),
            )
        with self.assertRaises(OperationalAdapterError):
            await context.adapter.prepare_rollback(
                tampered, expected_current_fingerprint="f" * 64
            )
        with self.assertRaises(OperationalAdapterError):
            validate_execution_binding(tampered, execution_identity())
        with self.assertRaises(OperationalAdapterError):
            await execute_operational(
                make_executor(self.root, prepared=tampered),
                adapter=context.adapter,
                prepared=tampered,
                identity=execution_identity(),
                approval_consumption=context.approval.consume,
            )
        self.assertEqual(callback_count, 0)
        self.assertEqual(context.approval.callback_count, 0)
        self.assertEqual(context.evidence.read_count, 0)
        self.assertEqual(context.backup.provider_dispatches, 0)
        self.assertEqual(context.backup.simulated_effects, 0)
        self.assertIsNone(DurableExecutionRepository(self.root).get(TASK_ID))


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
            SET_INPUT_BOOLEAN_STATE: {
                "helper:input_boolean.synthetic_exact": "exclusive",
                "helper_dependency:input_boolean.synthetic_exact": "shared",
                "helper_dependency:input_boolean_dynamic": "shared",
                "home_assistant:core": "shared",
                "reload:automation": "shared",
                "reload:input_boolean": "shared",
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

    @staticmethod
    def _lock_owner(name: str) -> LockOwner:
        return LockOwner(
            f"owner-{name}",
            f"task-{name}",
            f"plan-{name}",
            f"operation-{name}",
            f"attempt-{name}",
        )

    async def _helper_lock_sets(self):
        operational_context = make_context(
            self.root / "state",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.vacation_mode",
        )
        state = await prepare_context(operational_context)
        state_locks = operational_context.adapter.lock_requests(state)

        configuration_gateway = SyntheticConfigurationGateway()
        configuration_gateway.states[
            ("input_boolean", "input_boolean.vacation_mode")
        ] = configuration_proposal_for(
            "input_boolean", "update"
        ).current_config()
        configuration_adapter = configuration_adapter_for(
            "input_boolean", "update", configuration_gateway
        )
        configuration = await configuration_adapter.prepare(
            configuration_proposal_for("input_boolean", "update")
        )
        configuration_locks = configuration_adapter.lock_requests(
            configuration
        )

        reload_context = make_context(
            self.root / "reload-input-boolean",
            CONTROLLED_RELOAD,
            target_id="input_boolean",
        )
        reload_operation = await prepare_context(reload_context)
        reload_locks = reload_context.adapter.lock_requests(reload_operation)
        return state, state_locks, configuration_locks, reload_locks

    async def test_helper_state_and_same_helper_configuration_conflict_both_directions(self):
        _state, state_locks, configuration_locks, _reload_locks = (
            await self._helper_lock_sets()
        )
        timing = LockTiming(60, 10, 0)
        for first, second, first_name, second_name in (
            (state_locks, configuration_locks, "state", "configuration"),
            (configuration_locks, state_locks, "configuration", "state"),
        ):
            with self.subTest(first=first_name):
                with tempfile.TemporaryDirectory() as temporary:
                    store = DurableLockStore(temporary)
                    handle = store.acquire_once(
                        first,
                        owner=self._lock_owner(first_name),
                        timing=timing,
                    )
                    with self.assertRaises(LockConflict):
                        store.acquire_once(
                            second,
                            owner=self._lock_owner(second_name),
                            timing=timing,
                        )
                    store.release(handle)

    async def test_helper_state_and_matching_reload_conflict_both_directions(self):
        _state, state_locks, _configuration_locks, reload_locks = (
            await self._helper_lock_sets()
        )
        timing = LockTiming(60, 10, 0)
        for first, second, first_name, second_name in (
            (state_locks, reload_locks, "state", "reload"),
            (reload_locks, state_locks, "reload", "state"),
        ):
            with self.subTest(first=first_name):
                with tempfile.TemporaryDirectory() as temporary:
                    store = DurableLockStore(temporary)
                    handle = store.acquire_once(
                        first,
                        owner=self._lock_owner(first_name),
                        timing=timing,
                    )
                    with self.assertRaises(LockConflict):
                        store.acquire_once(
                            second,
                            owner=self._lock_owner(second_name),
                            timing=timing,
                        )
                    store.release(handle)

    async def test_unrelated_helper_state_and_configuration_operations_remain_concurrent(self):
        state_a_context = make_context(
            self.root / "state-a",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.helper_a",
        )
        state_b_context = make_context(
            self.root / "state-b",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.helper_b",
        )
        state_a = await prepare_context(state_a_context)
        state_b = await prepare_context(state_b_context)
        state_a_locks = state_a_context.adapter.lock_requests(state_a)
        state_b_locks = state_b_context.adapter.lock_requests(state_b)

        configuration_gateway = SyntheticConfigurationGateway()
        configuration_adapter = configuration_adapter_for(
            "input_boolean", "update", configuration_gateway
        )
        configuration = await configuration_adapter.prepare(
            configuration_proposal_for("input_boolean", "update")
        )
        configuration_locks = configuration_adapter.lock_requests(
            configuration
        )
        timing = LockTiming(60, 10, 0)

        for first, second, names in (
            (state_a_locks, state_b_locks, ("state-a", "state-b")),
            (
                configuration_locks,
                state_b_locks,
                ("configuration-a", "state-b"),
            ),
        ):
            with self.subTest(pair=names):
                with tempfile.TemporaryDirectory() as temporary:
                    store = DurableLockStore(temporary)
                    first_handle = store.acquire_once(
                        first,
                        owner=self._lock_owner(names[0]),
                        timing=timing,
                    )
                    second_handle = store.acquire_once(
                        second,
                        owner=self._lock_owner(names[1]),
                        timing=timing,
                    )
                    store.release(second_handle)
                    store.release(first_handle)

    async def test_helper_lock_order_hash_and_recovery_identity_are_deterministic(self):
        state, state_locks, _configuration_locks, _reload_locks = (
            await self._helper_lock_sets()
        )
        expected_resource = resource_lock_key(
            "input_boolean", "input_boolean.vacation_mode"
        )

        self.assertEqual(
            [item.key for item in state_locks],
            sorted(item.key for item in state_locks),
        )
        self.assertEqual(
            lock_set_hash(state_locks),
            lock_set_hash(tuple(reversed(state_locks))),
        )
        self.assertEqual(state.selective_hold_keys, (expected_resource,))
        self.assertEqual(
            {item.key: item.mode for item in state_locks},
            {
                expected_resource: LockMode.EXCLUSIVE,
                helper_dependency_lock_key(
                    "input_boolean.vacation_mode"
                ): LockMode.SHARED,
                unconstrained_helper_dependency_lock_key(): LockMode.SHARED,
                "home_assistant:core": LockMode.SHARED,
                "reload:automation": LockMode.SHARED,
                "reload:input_boolean": LockMode.SHARED,
            },
        )

    async def test_helper_serializes_with_bound_automation_and_reload(self):
        helper_context = make_context(
            self.root / "helper-bound",
            SET_INPUT_BOOLEAN_STATE,
            dependency_automation_ids=("porch_light",),
        )
        helper = await prepare_context(helper_context)
        helper_locks = helper_context.adapter.lock_requests(helper)

        configuration_gateway = SyntheticConfigurationGateway()
        configuration_gateway.states[("automation", "porch_light")] = (
            configuration_proposal_for(
                "automation", "update"
            ).current_config()
        )
        configuration_adapter = configuration_adapter_for(
            "automation", "update", configuration_gateway
        )
        configuration = await configuration_adapter.prepare(
            configuration_proposal_for("automation", "update")
        )
        configuration_locks = configuration_adapter.lock_requests(
            configuration
        )

        reload_context = make_context(
            self.root / "reload-automation",
            CONTROLLED_RELOAD,
            target_id="automation",
        )
        reload_operation = await prepare_context(reload_context)
        reload_locks = reload_context.adapter.lock_requests(reload_operation)

        self.assertEqual(
            {item.key: item.mode for item in helper_locks},
            {
                "automation:porch_light": LockMode.SHARED,
                "helper:input_boolean.synthetic_exact": LockMode.EXCLUSIVE,
                "helper_dependency:input_boolean.synthetic_exact": (
                    LockMode.SHARED
                ),
                "helper_dependency:input_boolean_dynamic": LockMode.SHARED,
                "home_assistant:core": LockMode.SHARED,
                "reload:automation": LockMode.SHARED,
                "reload:input_boolean": LockMode.SHARED,
            },
        )
        timing = LockTiming(60, 10, 0)
        for first, second, names in (
            (
                helper_locks,
                configuration_locks,
                ("helper", "automation-configuration"),
            ),
            (
                configuration_locks,
                helper_locks,
                ("automation-configuration", "helper"),
            ),
            (helper_locks, reload_locks, ("helper", "automation-reload")),
            (reload_locks, helper_locks, ("automation-reload", "helper")),
        ):
            with self.subTest(pair=names):
                with tempfile.TemporaryDirectory() as temporary:
                    store = DurableLockStore(temporary)
                    handle = store.acquire_once(
                        first,
                        owner=self._lock_owner(names[0]),
                        timing=timing,
                    )
                    with self.assertRaises(LockConflict):
                        store.acquire_once(
                            second,
                            owner=self._lock_owner(names[1]),
                            timing=timing,
                        )
                    store.release(handle)

    async def test_unrelated_automation_configuration_remains_concurrent(self):
        helper_context = make_context(
            self.root / "helper-unrelated",
            SET_INPUT_BOOLEAN_STATE,
            dependency_automation_ids=("other_automation",),
        )
        helper = await prepare_context(helper_context)
        helper_locks = helper_context.adapter.lock_requests(helper)

        configuration_gateway = SyntheticConfigurationGateway()
        configuration_gateway.states[("automation", "porch_light")] = (
            configuration_proposal_for(
                "automation", "update"
            ).current_config()
        )
        configuration_adapter = configuration_adapter_for(
            "automation", "update", configuration_gateway
        )
        configuration = await configuration_adapter.prepare(
            configuration_proposal_for("automation", "update")
        )
        configuration_locks = configuration_adapter.lock_requests(
            configuration
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = DurableLockStore(temporary)
            timing = LockTiming(60, 10, 0)
            helper_handle = store.acquire_once(
                helper_locks,
                owner=self._lock_owner("helper"),
                timing=timing,
            )
            configuration_handle = store.acquire_once(
                configuration_locks,
                owner=self._lock_owner("unrelated-configuration"),
                timing=timing,
            )
            store.release(configuration_handle)
            store.release(helper_handle)

    async def test_new_relevant_automation_content_conflicts_but_unrelated_remains_concurrent(self):
        helper_context = make_context(
            self.root / "content-derived-helper",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
        )
        helper = await prepare_context(helper_context)
        helper_locks = helper_context.adapter.lock_requests(helper)
        base = configuration_valid_config("automation")
        relevant = configuration_valid_config("automation")
        relevant["condition"] = [
            {
                "condition": "state",
                "entity_id": "input_boolean.synthetic_exact",
                "state": "on",
            }
        ]
        altered = {
            **relevant,
            "action": [{"service": "light.turn_off"}],
        }
        dynamic = configuration_valid_config("automation")
        dynamic["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ states(entity_variable) }}",
            }
        ]
        bounded_relevant = configuration_valid_config("automation")
        bounded_relevant["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{% for entity in ['sensor.a', "
                    "'input_boolean.synthetic_exact'] %}"
                    "{{ states(entity) }}{% endfor %}"
                ),
            }
        ]
        bounded_unrelated = configuration_valid_config("automation")
        bounded_unrelated["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{% for entity in ['sensor.a', 'sensor.b'] %}"
                    "{{ states(entity) }}{% endfor %}"
                ),
            }
        ]
        unresolved_blueprint = configuration_valid_config("automation")
        unresolved_blueprint["use_blueprint"] = {
            "path": "synthetic/hardcoded_helper.yaml",
            "input": {},
        }

        async def configuration_locks(action, current, proposed):
            gateway = SyntheticConfigurationGateway()
            proposal = configuration_proposal_for(
                "automation",
                action,
                current_config=current,
                proposed_config=proposed,
            )
            if current is not None:
                gateway.states[("automation", "porch_light")] = current
            adapter = configuration_adapter_for(
                "automation", action, gateway
            )
            prepared = await adapter.prepare(proposal)
            return adapter.lock_requests(prepared)

        relevant_cases = (
            ("create", None, relevant, "create_adds_dependency"),
            ("update", base, relevant, "update_adds_dependency"),
            ("update", relevant, base, "update_removes_dependency"),
            ("update", relevant, altered, "update_alters_dependency"),
            ("update", base, dynamic, "unconstrained_dynamic"),
            (
                "update",
                base,
                bounded_relevant,
                "bounded_dynamic_adds_exact_dependency",
            ),
            (
                "create",
                None,
                unresolved_blueprint,
                "blueprint_source_may_introduce_dependency",
            ),
            (
                "update",
                unresolved_blueprint,
                base,
                "blueprint_source_may_remove_dependency",
            ),
        )
        timing = LockTiming(60, 10, 0)
        with tempfile.TemporaryDirectory() as temporary:
            store = DurableLockStore(temporary)
            helper_handle = store.acquire_once(
                helper_locks,
                owner=self._lock_owner("helper-content"),
                timing=timing,
            )
            for action, current, proposed, name in relevant_cases:
                with self.subTest(case=name):
                    locks = await configuration_locks(
                        action, current, proposed
                    )
                    with self.assertRaises(LockConflict):
                        store.acquire_once(
                            locks,
                            owner=self._lock_owner(name),
                            timing=timing,
                        )

            unrelated_locks = await configuration_locks(
                "update",
                base,
                bounded_unrelated,
            )
            unrelated_handle = store.acquire_once(
                unrelated_locks,
                owner=self._lock_owner("unrelated-content"),
                timing=timing,
            )
            store.release(unrelated_handle)
            store.release(helper_handle)

    async def test_dependency_refresh_occurs_only_after_exact_locks_are_held(self):
        context = make_context(
            self.root / "refresh-after-locks",
            SET_INPUT_BOOLEAN_STATE,
            dependency_automation_ids=("porch_light",),
        )
        prepared = await prepare_context(context)
        locks = context.adapter.lock_requests(prepared)

        missing = await context.adapter.preflight(
            prepared, acquired_locks=locks[:-1]
        )

        self.assertFalse(missing.eligible)
        self.assertEqual(missing.outcome, "lock_conflict")
        self.assertNotIn("helper_dependency_read", context.trace)
        self.assertNotIn("helper_dependency_fenced_read", context.trace)

        exact = await context.adapter.preflight(
            prepared, acquired_locks=locks
        )

        self.assertTrue(exact.eligible)
        # B39-136-R2: the read that decides execution eligibility happens
        # after the complete lock set is held and is fenced, so evidence read
        # before the lock cannot satisfy it.
        self.assertEqual(
            context.trace[-1], "helper_dependency_fenced_read"
        )
        self.assertNotIn("helper_dependency_read", context.trace)

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
                prepared = await prepare_context(context)
                context.adapter.authority_reader = lambda _prepared, f=field, v=value, a=context.authority: replace(a, **{f: v})
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
            prepared = await prepare_context(context)
            context.adapter.authority_reader = lambda _prepared, a=context.authority: replace(
                a, elevated_acknowledgement_bound=False
            )
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
            prepared = await prepare_context(context)
            context.adapter.authority_reader = lambda _prepared, f=field, a=context.authority: replace(a, **{f: "unhealthy"})
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
                    make_executor(root, prepared=prepared),
                    adapter=context.adapter,
                    prepared=prepared,
                    identity=execution_identity(), approval_consumption=context.approval.consume,
                )
                gateway = context.backup if operation == CREATE_FULL_BACKUP else context.lifecycle
                self.assertEqual(result.outcome, "succeeded_verified")
                self.assertEqual(result.dispatch_count, 1)
                self.assertEqual(gateway.provider_dispatches, 1)
                self.assertEqual(gateway.simulated_effects, 1)

    async def test_dependency_mutation_races_are_resolved_after_helper_preflight(self):
        context = make_context(
            self.root / "executor-dependency-race",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
        )
        prepared = await prepare_context(context)
        unrelated = configuration_valid_config("automation")
        relevant = configuration_valid_config("automation", updated=True)
        relevant["condition"] = [
            {
                "condition": "state",
                "entity_id": "input_boolean.synthetic_exact",
                "state": "on",
            }
        ]
        altered = {
            **relevant,
            "action": [{"service": "light.turn_off"}],
        }

        async def configuration_locks(
            action, current, proposed, *, target="porch_light"
        ):
            current = (
                {**current, "id": target}
                if current is not None
                else None
            )
            proposed = {**proposed, "id": target}
            gateway = SyntheticConfigurationGateway()
            if current is not None:
                gateway.states[("automation", target)] = current
            adapter = configuration_adapter_for(
                "automation", action, gateway
            )
            proposal = configuration_proposal_for(
                "automation",
                action,
                current_config=current,
                proposed_config=proposed,
            )
            configuration = await adapter.prepare(
                replace(proposal, target_id=target)
            )
            return adapter.lock_requests(configuration)

        conflicting = {
            "create_adds": await configuration_locks(
                "create", None, relevant
            ),
            "update_adds": await configuration_locks(
                "update",
                unrelated,
                relevant,
            ),
            "update_removes": await configuration_locks(
                "update", relevant, unrelated
            ),
            "update_alters": await configuration_locks(
                "update", relevant, altered
            ),
        }
        unrelated_locks = await configuration_locks(
            "update",
            unrelated,
            configuration_valid_config("automation", updated=True),
            target="unrelated_automation",
        )
        reload_context = make_context(
            self.root / "executor-dependency-race-reload",
            CONTROLLED_RELOAD,
            target_id="automation",
        )
        reload_prepared = await prepare_context(reload_context)
        reload_locks = reload_context.adapter.lock_requests(reload_prepared)
        blocked: set[str] = set()
        conflict_keys: dict[str, tuple[str, ...]] = {}
        concurrent: set[str] = set()
        executor = None

        def attempt_race(stage):
            if stage != "after_preflight_before_durable_intent":
                return
            candidates = {
                # Exercise the compatible reader before the conflicting
                # reload writer enters the lock store's fairness queue.
                "unrelated": unrelated_locks,
                **conflicting,
                "reload": reload_locks,
            }
            for name, requests in candidates.items():
                try:
                    handle = executor.lock_store.acquire_once(
                        requests,
                        owner=LockOwner(
                            f"owner-{name}",
                            f"task-{name}",
                            f"plan-{name}",
                            f"operation-{name}",
                            f"attempt-{name}",
                        ),
                        timing=LockTiming(60, 10, 0),
                        now=executor.now(),
                    )
                except LockConflict as exc:
                    blocked.add(name)
                    conflict_keys[name] = exc.keys
                else:
                    concurrent.add(name)
                    executor.lock_store.release(handle)

        executor = make_executor(
            self.root / "executor-dependency-race",
            prepared=prepared,
            executor_fault_hook=attempt_race,
        )
        result = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )

        self.assertEqual(
            blocked,
            {
                "create_adds",
                "update_adds",
                "update_removes",
                "update_alters",
                "reload",
            },
            conflict_keys,
        )
        self.assertEqual(concurrent, {"unrelated"})
        self.assertEqual(result.outcome, "succeeded_verified")
        self.assertEqual(
            context.adapter.strategies[
                SET_INPUT_BOOLEAN_STATE
            ].gateway.provider_dispatches,
            1,
        )

    async def test_home_assistant_restart_recovers_outage_then_verifies(self):
        context = make_context(self.root, RESTART_HOME_ASSISTANT)
        prepared = await prepare_context(context)
        executor = make_executor(self.root, prepared=prepared)
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
        executor = make_executor(self.root, prepared=prepared)
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
