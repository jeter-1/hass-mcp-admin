"""Identity, descriptor, and lock-set conformance for F3-C1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import (
    AdapterCapabilityDescriptor,
    LockMode,
    LockRequest,
    LockScope,
    PreparedOperation,
)
from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict
from ha_mcp_engineering.f3.models import LockOwner, LockTiming
from ha_mcp_engineering.f3_configuration.locks import (
    complete_configuration_lock_set,
    helper_dependency_lock_key,
    lock_set_hash,
    normalize_lock_requests,
    operation_lock_requests,
    resource_lock_key,
    unconstrained_helper_dependency_lock_key,
)
from ha_mcp_engineering.f3_configuration.strategies import (
    CAPABILITY_IDENTITIES,
    strategy_for,
)
from ha_mcp_engineering.governance.normalize import stable_hash

from tests.f3_configuration_fixtures import (
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
    target_id,
    valid_config,
)


class CapabilityIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_capability_identities_are_exact_and_closed(self):
        expected = {
            "create_automation_configuration",
            "update_automation_configuration",
            "create_script_configuration",
            "update_script_configuration",
            "create_input_boolean_configuration",
            "update_input_boolean_configuration",
            "create_input_number_configuration",
            "update_input_number_configuration",
        }
        self.assertEqual(set(CAPABILITY_IDENTITIES.values()), expected)
        for (resource_type, action), identity in CAPABILITY_IDENTITIES.items():
            with self.subTest(resource_type=resource_type, action=action):
                strategy = strategy_for(resource_type, action)
                descriptor = strategy.capabilities
                self.assertEqual(descriptor.capability_identity, identity)
                self.assertEqual(descriptor.contract_model, "f3-operation-adapter-v1")
                self.assertEqual(descriptor.resource_type, resource_type)
                self.assertEqual(descriptor.action, action)
                self.assertEqual(
                    descriptor.provider,
                    "home_assistant_configuration_gateway",
                )
                self.assertTrue(descriptor.readback_recovery_supported)
                self.assertTrue(descriptor.exact_provider_contract_required)
                self.assertFalse(descriptor.rollback_supported)
                self.assertIsInstance(
                    descriptor, AdapterCapabilityDescriptor
                )
                if resource_type in {"automation", "script"}:
                    self.assertEqual(
                        descriptor.argument_names,
                        ("body", "method", "path"),
                    )
                else:
                    self.assertIn("type", descriptor.argument_names)
                    self.assertIn("name", descriptor.argument_names)
                    if action == "update":
                        self.assertIn(
                            f"{resource_type}_id",
                            descriptor.argument_names,
                        )

    def test_unknown_resource_types_actions_and_capabilities_fail_closed(self):
        for resource_type, action in (
            ("scene", "create"),
            ("group", "update"),
            ("automation", "delete"),
            ("script", "rename"),
            ("input_boolean", "enable"),
        ):
            with self.subTest(resource_type=resource_type, action=action):
                with self.assertRaises(ValueError):
                    strategy_for(resource_type, action)

    async def test_prepared_configuration_is_deeply_immutable_json(self):
        original = valid_config("automation")
        proposal = proposal_for(
            "automation", "create", proposed_config=original
        )
        original["alias"] = "mutated after construction"
        adapter = adapter_for(
            "automation", "create", SyntheticConfigurationGateway()
        )
        prepared = await adapter.prepare(proposal)
        self.assertEqual(prepared.proposed_config()["alias"], "Porch light")
        decoded = prepared.proposed_config()
        decoded["alias"] = "local mutation"
        self.assertEqual(prepared.proposed_config()["alias"], "Porch light")
        with self.assertRaises(FrozenInstanceError):
            prepared.action = "delete"  # type: ignore[misc]

    async def test_prepared_hash_binds_preflight_authority_and_effects(self):
        proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): proposal.current_config()}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        for altered in (
            replace(prepared, policy_snapshot_valid=False),
            replace(prepared, provider_admitted=False),
            replace(prepared, expected_effects=("unreviewed_effect",)),
        ):
            with self.subTest(altered=altered):
                result = await adapter.preflight(
                    altered,
                    acquired_locks=adapter.lock_requests(prepared),
                )
                self.assertFalse(result.eligible)
                self.assertIn(
                    "prepared_operation_invalid",
                    result.diagnostic_codes,
                )
                self.assertEqual(gateway.counters.dispatches, 0)
        self.assertIsInstance(prepared, PreparedOperation)

    async def test_all_forward_operations_have_no_executable_rollback(self):
        for resource_type, action in CAPABILITY_IDENTITIES:
            with self.subTest(resource_type=resource_type, action=action):
                gateway = SyntheticConfigurationGateway()
                adapter = adapter_for(resource_type, action, gateway)
                prepared = await adapter.prepare(
                    proposal_for(resource_type, action)
                )
                self.assertFalse(adapter.capabilities.rollback_supported)
                self.assertFalse(prepared.rollback_available)
                rollback = await adapter.prepare_rollback(
                    prepared,
                    expected_current_fingerprint=(
                        prepared.normalized_proposed_hash
                    ),
                )
                self.assertIsNone(rollback)
                self.assertEqual(gateway.counters.dispatches, 0)
                self.assertEqual(gateway.counters.simulated_mutations, 0)

    async def test_proposal_cannot_assert_unavailable_rollback_authority(self):
        proposal = replace(
            proposal_for("automation", "update"),
            rollback_available=True,
        )
        adapter = adapter_for(
            "automation", "update", SyntheticConfigurationGateway()
        )
        with self.assertRaisesRegex(ValueError, "rollback is unavailable"):
            await adapter.prepare(proposal)


class CanonicalIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_automation_preserves_bare_internal_id_and_rejects_entity_alias(self):
        strategy = strategy_for("automation", "create")
        config = valid_config("automation")
        self.assertEqual(strategy.canonical_target("porch_light", config), "porch_light")
        with self.assertRaises(ValueError):
            strategy.canonical_target("automation.porch_light", config)
        with self.assertRaises(ValueError):
            strategy.canonical_target(" porch_light", config)

    async def test_automation_case_variants_share_one_lock_identity(self):
        self.assertEqual(
            resource_lock_key("automation", "Porch_Light"),
            resource_lock_key("automation", "porch_light"),
        )
        self.assertEqual(
            resource_lock_key("automation", "Porch_Light"),
            "automation:porch_light",
        )

    async def test_script_accepts_only_bare_storage_key(self):
        strategy = strategy_for("script", "create")
        config = valid_config("script")
        self.assertEqual(strategy.canonical_target("notify_house", config), "notify_house")
        for invalid in ("script.notify_house", "Notify_House", " notify_house", "reload"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    strategy.canonical_target(invalid, config)

    async def test_helpers_require_exact_full_domain_qualified_identity(self):
        boolean = strategy_for("input_boolean", "create")
        number = strategy_for("input_number", "create")
        self.assertEqual(
            boolean.canonical_target(
                "input_boolean.vacation_mode", valid_config("input_boolean")
            ),
            "input_boolean.vacation_mode",
        )
        self.assertEqual(
            number.canonical_target(
                "input_number.target_temperature", valid_config("input_number")
            ),
            "input_number.target_temperature",
        )
        for strategy, invalid, config_type in (
            (boolean, "vacation_mode", "input_boolean"),
            (boolean, "input_number.vacation_mode", "input_boolean"),
            (number, "input_number.Target", "input_number"),
            (number, "input_number.target_temperature ", "input_number"),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    strategy.canonical_target(invalid, valid_config(config_type))

    async def test_create_identity_cannot_switch_to_generated_suffix(self):
        strategy = strategy_for("input_boolean", "create")
        valid, errors, _ = strategy.validate(
            "input_boolean.different_id", valid_config("input_boolean")
        )
        self.assertFalse(valid)
        self.assertTrue(any("deterministic object ID" in error for error in errors))


class ProviderDescriptorTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_descriptors_match_existing_fixed_endpoints(self):
        for resource_type in ("automation", "script"):
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    strategy = strategy_for(resource_type, action)
                    config = valid_config(resource_type, updated=action == "update")
                    descriptor = strategy.provider_descriptor(
                        target_id(resource_type), config
                    )
                    path = f"/config/{resource_type}/config/{target_id(resource_type)}"
                    self.assertEqual(descriptor.transport, "home_assistant_rest")
                    self.assertEqual(
                        descriptor.operation,
                        f"{resource_type}_configuration_write",
                    )
                    self.assertEqual(descriptor.argument_names, ("body", "method", "path"))
                    self.assertEqual(
                        descriptor.arguments_hash,
                        stable_hash({"method": "POST", "path": path, "body": config}),
                    )

    async def test_helper_descriptors_match_exact_create_and_update_commands(self):
        for resource_type, id_field in (
            ("input_boolean", "input_boolean_id"),
            ("input_number", "input_number_id"),
        ):
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    strategy = strategy_for(resource_type, action)
                    config = valid_config(resource_type, updated=action == "update")
                    descriptor = strategy.provider_descriptor(
                        target_id(resource_type), config
                    )
                    payload = {"type": f"{resource_type}/{action}"}
                    if action == "update":
                        payload[id_field] = target_id(resource_type).split(".", 1)[1]
                    payload.update(config)
                    self.assertEqual(descriptor.transport, "home_assistant_websocket")
                    self.assertEqual(
                        descriptor.operation, f"{resource_type}_{action}"
                    )
                    self.assertEqual(descriptor.arguments_hash, stable_hash(payload))
                    self.assertNotIn("service", descriptor.argument_names)
                    self.assertNotIn("delete", descriptor.argument_names)


class LockSetTests(unittest.IsolatedAsyncioTestCase):
    async def _prepared(self, resource_type: str, action: str, **kwargs):
        gateway = SyntheticConfigurationGateway()
        proposal = proposal_for(resource_type, action, **kwargs)
        if action == "update":
            gateway.states[(resource_type, proposal.target_id)] = proposal.current_config()
        return await adapter_for(resource_type, action, gateway).prepare(proposal)

    async def test_each_operation_has_exact_resource_reload_and_core_locks(self):
        expected = {
            "automation": ("automation:porch_light", "reload:automation"),
            "script": ("script:notify_house", "reload:script"),
            "input_boolean": (
                "helper:input_boolean.vacation_mode",
                "reload:input_boolean",
            ),
            "input_number": (
                "helper:input_number.target_temperature",
                "reload:input_number",
            ),
        }
        for resource_type, (key, reload_key) in expected.items():
            with self.subTest(resource_type=resource_type):
                prepared = await self._prepared(resource_type, "create")
                locks = operation_lock_requests(prepared)
                by_key = {lock.key: lock for lock in locks}
                self.assertEqual(
                    set(by_key),
                    {key, reload_key, "home_assistant:core"},
                )
                self.assertEqual(by_key[key].mode, LockMode.EXCLUSIVE)
                self.assertEqual(by_key[reload_key].mode, LockMode.SHARED)
                self.assertEqual(by_key["home_assistant:core"].mode, LockMode.SHARED)
                self.assertEqual(by_key[key].scopes, (LockScope.RESOURCE,))
                self.assertEqual(
                    by_key[reload_key].scopes, (LockScope.RESOURCE,)
                )
                self.assertEqual(
                    by_key["home_assistant:core"].scopes,
                    (LockScope.RESOURCE,),
                )
                self.assertFalse(
                    any(lock.key.startswith("addon:") for lock in locks)
                )

    async def test_complete_multi_operation_lock_set_is_atomic_input_order_independent(self):
        first = await self._prepared("automation", "create", operation_id="one", order=0)
        second = await self._prepared(
            "input_number",
            "create",
            operation_id="two",
            order=1,
            depends_on=("one",),
        )
        locks = complete_configuration_lock_set((first, second))
        self.assertEqual(
            [lock.key for lock in locks],
            sorted((lock.key for lock in locks), key=lambda value: value.encode("utf-8")),
        )
        self.assertEqual(sum(lock.key == "home_assistant:core" for lock in locks), 1)
        self.assertEqual(lock_set_hash(locks), lock_set_hash(tuple(reversed(locks))))

    def test_duplicate_lock_evidence_unions_and_exclusive_dominates(self):
        normalized = normalize_lock_requests(
            (
                LockRequest(
                    "home_assistant:core",
                    (LockScope.PROVIDER,),
                    LockMode.SHARED,
                    ("provider_dependency",),
                ),
                LockRequest(
                    "home_assistant:core",
                    (LockScope.RESOURCE,),
                    LockMode.EXCLUSIVE,
                    ("restart_conflict",),
                ),
            )
        )
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].mode, LockMode.EXCLUSIVE)
        self.assertEqual(
            normalized[0].scopes,
            (LockScope.PROVIDER, LockScope.RESOURCE),
        )
        self.assertEqual(
            normalized[0].reason_codes,
            ("provider_dependency", "restart_conflict"),
        )

    async def test_duplicate_resource_target_is_rejected_before_lock_acquisition(self):
        first = await self._prepared("automation", "create", operation_id="one", order=0)
        second = await self._prepared("automation", "create", operation_id="two", order=1)
        with self.assertRaises(ValueError):
            complete_configuration_lock_set((first, second))

    async def test_engineering_same_resource_conflicts_but_different_resources_do_not(self):
        first = await self._prepared(
            "automation", "create", operation_id="same_one", order=0
        )
        same = await self._prepared(
            "automation", "update", operation_id="same_two", order=0
        )
        different = await self._prepared(
            "script", "create", operation_id="different", order=0
        )
        first_locks = {lock.key: lock for lock in operation_lock_requests(first)}
        same_locks = {lock.key: lock for lock in operation_lock_requests(same)}
        different_locks = {
            lock.key: lock for lock in operation_lock_requests(different)
        }
        self.assertEqual(
            first_locks["automation:porch_light"].mode,
            same_locks["automation:porch_light"].mode,
        )
        self.assertEqual(
            first_locks["automation:porch_light"].mode,
            LockMode.EXCLUSIVE,
        )
        self.assertNotIn("automation:porch_light", different_locks)
        self.assertEqual(
            first_locks["home_assistant:core"].mode,
            different_locks["home_assistant:core"].mode,
        )
        self.assertEqual(
            first_locks["home_assistant:core"].mode,
            LockMode.SHARED,
        )
        self.assertNotIn("reload:automation", different_locks)
        self.assertIn("reload:script", different_locks)

    async def test_automation_dependency_locks_derive_from_current_and_proposed_content(self):
        helper = "input_boolean.synthetic_exact"
        unrelated = valid_config("automation")
        relevant = valid_config("automation")
        relevant["condition"] = [
            {
                "condition": "state",
                "entity_id": helper,
                "state": "on",
            }
        ]
        altered = {**relevant, "action": [{"service": "light.turn_off"}]}
        cases = (
            ("create", None, relevant, "add_create"),
            ("update", unrelated, relevant, "add_update"),
            ("update", relevant, unrelated, "remove_update"),
            ("update", relevant, altered, "retain_alter_update"),
        )
        expected = helper_dependency_lock_key(helper)
        for action, current, proposed, name in cases:
            with self.subTest(case=name):
                prepared = await self._prepared(
                    "automation",
                    action,
                    current_config=current,
                    proposed_config=proposed,
                )
                locks = {
                    item.key: item
                    for item in operation_lock_requests(prepared)
                }
                self.assertEqual(locks[expected].mode, LockMode.EXCLUSIVE)

        unrelated_prepared = await self._prepared(
            "automation",
            "update",
            current_config=unrelated,
            proposed_config=valid_config("automation", updated=True),
        )
        self.assertNotIn(
            expected,
            {
                item.key
                for item in operation_lock_requests(unrelated_prepared)
            },
        )

    async def test_unconstrained_dynamic_dependency_uses_conservative_lock_only(self):
        base = valid_config("automation")
        unconstrained = valid_config("automation")
        unconstrained["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ states(entity_variable) }}",
            }
        ]
        sensor_constrained = valid_config("automation")
        sensor_constrained["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ states('sensor.' ~ room) }}",
            }
        ]
        dynamic_key = unconstrained_helper_dependency_lock_key()
        dynamic_prepared = await self._prepared(
            "automation",
            "update",
            current_config=base,
            proposed_config=unconstrained,
        )
        constrained_prepared = await self._prepared(
            "automation",
            "update",
            current_config=base,
            proposed_config=sensor_constrained,
        )

        self.assertIn(
            dynamic_key,
            {item.key for item in operation_lock_requests(dynamic_prepared)},
        )
        self.assertNotIn(
            dynamic_key,
            {
                item.key
                for item in operation_lock_requests(constrained_prepared)
            },
        )

    async def test_filter_test_and_domain_collection_dependency_locks(self):
        helper = "input_boolean.synthetic_exact"
        exact_key = helper_dependency_lock_key(helper)
        dynamic_key = unconstrained_helper_dependency_lock_key()
        base = valid_config("automation")
        forms = (
            f'{{{{ "{helper}" | states }}}}',
            f'{{{{ "{helper}" | state_attr("friendly_name") }}}}',
            f'{{{{ "{helper}" | has_value }}}}',
            f'{{{{ "{helper}" is is_state("on") }}}}',
            f'{{{{ "{helper}" is is_state_attr("mode", "on") }}}}',
            f'{{{{ "{helper}" is has_value }}}}',
        )
        for action in ("create", "update"):
            for index, template in enumerate(forms):
                with self.subTest(action=action, template=template):
                    relevant = valid_config("automation")
                    relevant["condition"] = [
                        {
                            "condition": "template",
                            "value_template": template,
                        }
                    ]
                    prepared = await self._prepared(
                        "automation",
                        action,
                        current_config=(base if action == "update" else None),
                        proposed_config=relevant,
                    )
                    locks = {
                        item.key
                        for item in operation_lock_requests(prepared)
                    }
                    self.assertIn(exact_key, locks)

        domain_cases = (
            (
                "{{ states.input_boolean "
                "| selectattr('state', 'eq', 'on') | list }}",
                True,
            ),
            (
                "{{ states.sensor "
                "| selectattr('state', 'eq', 'on') | list }}",
                False,
            ),
            ("{{ helper_entity | states }}", True),
        )
        for template, expects_dynamic_lock in domain_cases:
            with self.subTest(template=template):
                proposed = valid_config("automation")
                proposed["condition"] = [
                    {
                        "condition": "template",
                        "value_template": template,
                    }
                ]
                prepared = await self._prepared(
                    "automation",
                    "update",
                    current_config=base,
                    proposed_config=proposed,
                )
                locks = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertEqual(dynamic_key in locks, expects_dynamic_lock)

        ambiguous_expressions = (
            "'sensor.' ~ room if use_sensor else helper_entity",
            "'sensor.' ~ room and helper_entity",
            "'sensor.' ~ room or helper_entity",
            "('sensor.' ~ room)",
            "'sensor.' ~ room | lower",
        )
        for index, expression in enumerate(ambiguous_expressions):
            with self.subTest(expression=expression):
                ambiguous = valid_config("automation")
                ambiguous["condition"] = [
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ states(" + expression + ") }}"
                        ),
                    }
                ]
                prepared = await self._prepared(
                    "automation",
                    "update",
                    operation_id=f"ambiguous_{index}",
                    current_config=base,
                    proposed_config=ambiguous,
                )
                self.assertIn(
                    dynamic_key,
                    {
                        item.key
                        for item in operation_lock_requests(prepared)
                    },
                )

    async def test_matching_reload_and_restart_exclusive_locks_conflict_atomically(self):
        timing = LockTiming(60, 10, 0)
        expected_reload = {
            "automation": "reload:automation",
            "script": "reload:script",
            "input_boolean": "reload:input_boolean",
            "input_number": "reload:input_number",
        }
        for resource_type, reload_key in expected_reload.items():
            with self.subTest(resource_type=resource_type):
                with tempfile.TemporaryDirectory() as temporary:
                    store = DurableLockStore(temporary)
                    blocker = store.acquire_once(
                        (
                            LockRequest(
                                reload_key,
                                (LockScope.RESOURCE,),
                                LockMode.EXCLUSIVE,
                                ("matching_reload_execution",),
                            ),
                        ),
                        owner=LockOwner(
                            "reload-owner",
                            "reload-task",
                            "reload-plan",
                            "reload-operation",
                            "reload-attempt",
                        ),
                        timing=timing,
                    )
                    prepared = await self._prepared(resource_type, "create")
                    with self.assertRaises(LockConflict):
                        store.acquire_once(
                            operation_lock_requests(prepared),
                            owner=LockOwner(
                                "config-owner",
                                "config-task",
                                "config-plan",
                                "config-operation",
                                "config-attempt",
                            ),
                            timing=timing,
                        )
                    self.assertEqual(len(store.records()), 1)
                    store.release(blocker)

        with tempfile.TemporaryDirectory() as temporary:
            store = DurableLockStore(temporary)
            blocker = store.acquire_once(
                (
                    LockRequest(
                        "home_assistant:core",
                        (LockScope.RESOURCE,),
                        LockMode.EXCLUSIVE,
                        ("home_assistant_restart",),
                    ),
                ),
                owner=LockOwner(
                    "restart-owner",
                    "restart-task",
                    "restart-plan",
                    "restart-operation",
                    "restart-attempt",
                ),
                timing=timing,
            )
            prepared = await self._prepared("automation", "create")
            with self.assertRaises(LockConflict):
                store.acquire_once(
                    operation_lock_requests(prepared),
                    owner=LockOwner(
                        "config-owner",
                        "config-task",
                        "config-plan",
                        "config-operation",
                        "config-attempt",
                    ),
                    timing=timing,
                )
            self.assertEqual(len(store.records()), 1)
            store.release(blocker)

    async def test_different_exact_resources_remain_lock_compatible(self):
        timing = LockTiming(60, 10, 0)
        first = await self._prepared("automation", "create")
        proposal = replace(
            proposal_for("automation", "create"),
            target_id="porch_light_two",
            operation_id="step_two",
        )
        second = await adapter_for(
            "automation", "create", SyntheticConfigurationGateway()
        ).prepare(proposal)
        with tempfile.TemporaryDirectory() as temporary:
            store = DurableLockStore(temporary)
            first_handle = store.acquire_once(
                operation_lock_requests(first),
                owner=LockOwner(
                    "owner-one", "task-one", "plan-one", "op-one", "attempt-one"
                ),
                timing=timing,
            )
            second_handle = store.acquire_once(
                operation_lock_requests(second),
                owner=LockOwner(
                    "owner-two", "task-two", "plan-two", "op-two", "attempt-two"
                ),
                timing=timing,
            )
            self.assertEqual(len(store.records()), 6)
            store.release(second_handle)
            store.release(first_handle)


if __name__ == "__main__":
    unittest.main()
