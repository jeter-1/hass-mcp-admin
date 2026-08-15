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

    async def test_non_selector_template_dataflow_does_not_take_helper_lock(self):
        base = valid_config("automation")
        ordinary = valid_config("automation")
        ordinary["action"] = [
            {
                "service": "notify.notify",
                "data": {
                    "message": (
                        "{% set states = messages %}"
                        "{{ states | join(', ') }}"
                    )
                },
            }
        ]
        selector = valid_config("automation")
        selector["condition"] = [
            {
                "condition": "template",
                "value_template": "{{ states(variable) }}",
            }
        ]
        dynamic_key = unconstrained_helper_dependency_lock_key()

        ordinary_prepared = await self._prepared(
            "automation",
            "update",
            current_config=base,
            proposed_config=ordinary,
        )
        selector_prepared = await self._prepared(
            "automation",
            "update",
            current_config=ordinary,
            proposed_config=selector,
        )

        self.assertNotIn(
            dynamic_key,
            {
                item.key
                for item in operation_lock_requests(ordinary_prepared)
            },
        )
        self.assertIn(
            dynamic_key,
            {
                item.key
                for item in operation_lock_requests(selector_prepared)
            },
        )

    async def test_callable_aliases_take_exact_or_conservative_helper_lock(self):
        helper = "input_boolean.synthetic_exact"
        exact_key = helper_dependency_lock_key(helper)
        dynamic_key = unconstrained_helper_dependency_lock_key()
        base = valid_config("automation")
        known_alias = valid_config("automation")
        known_alias["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{% set states = is_state %}"
                    f"{{{{ states('{helper}', 'on') }}}}"
                ),
            }
        ]
        loop_alias = valid_config("automation")
        loop_alias["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{% for states in [is_state] %}"
                    f"{{{{ states('{helper}', 'on') }}}}"
                    "{% endfor %}"
                ),
            }
        ]
        unknown_alias = valid_config("automation")
        unknown_alias["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    "{% set states = unknown_callable %}"
                    f"{{{{ states('{helper}') }}}}"
                ),
            }
        ]

        for proposed in (known_alias, loop_alias):
            with self.subTest(
                template=proposed["condition"][0]["value_template"]
            ):
                prepared = await self._prepared(
                    "automation",
                    "update",
                    current_config=base,
                    proposed_config=proposed,
                )
                keys = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertIn(exact_key, keys)
                self.assertNotIn(dynamic_key, keys)

        prepared = await self._prepared(
            "automation",
            "update",
            current_config=base,
            proposed_config=unknown_alias,
        )
        keys = {
            item.key for item in operation_lock_requests(prepared)
        }
        self.assertIn(dynamic_key, keys)
        self.assertNotIn(exact_key, keys)

    async def test_collection_and_unreviewed_aliases_take_dependency_locks(self):
        helper = "input_boolean.synthetic_exact"
        exact_key = helper_dependency_lock_key(helper)
        dynamic_key = unconstrained_helper_dependency_lock_key()
        base = valid_config("automation")
        deep_argument = f"lookup('{helper}', 'on')"
        for _index in range(10):
            deep_argument = (
                f"helpers.get('message', {deep_argument})"
            )
        deep_argument_template = (
            "{% set lookup = is_state %}"
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ {deep_argument} }}}}"
        )
        method_chain_statements = [
            f"{{% set level0 = {{'{helper}': states}} %}}"
        ]
        for index in range(1, 11):
            method_chain_statements.append(
                "{% set level"
                f"{index} = {{'next': level{index - 1}.get}} %}}"
            )
        method_chain_expression = (
            "level10.get('next')" + "('next')" * 9
        )
        method_chain_expression += f"('{helper}')('{helper}')"
        method_chain_template = "".join(method_chain_statements) + (
            f"{{{{ {method_chain_expression} }}}}"
        )
        exact_templates = (
            "{% set lookup = states %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set first = states %}{% set lookup = first %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set lookup = helpers.lookup %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set lookup = helpers['lookup'] %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% for lookup in [states] %}"
            f"{{{{ lookup['{helper}'] }}}}"
            "{% endfor %}",
            "{% set lookup = states %}"
            "{{ lookup.input_boolean.synthetic_exact }}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers.lookup['{helper}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers['lookup']['{helper}'] }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            f"{{{{ helpers.lookup('{helper}', 'on') }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            f"{{{{ helpers['lookup']('{helper}', 'on') }}}}",
            "{% set helpers = {'nested': {'lookup': states}} %}"
            f"{{{{ helpers.nested.lookup['{helper}'] }}}}",
            "{% set helpers = {'nested': {'lookup': is_state}} %}"
            f"{{{{ helpers['nested']['lookup']('{helper}', 'on') }}}}",
            "{% set helpers = {'get': 'ordinary', "
            f"'{helper}': states}} %}}"
            f"{{{{ helpers.get('{helper}')('{helper}') }}}}",
            "{% set helpers = {'get': 'ordinary', "
            f"'{helper}': states}} %}}"
            f"{{{{ helpers.get('{helper}')['{helper}'] }}}}",
            "{% set helpers = {'get': is_state} %}"
            f"{{{{ helpers['get']('{helper}', 'on') }}}}",
            "{% set helpers = {'values': states} %}"
            f"{{{{ helpers['values']['{helper}'] }}}}",
            "{% set helpers = {'items': is_state} %}"
            f"{{{{ helpers['items']('{helper}', 'on') }}}}",
            "{% set helpers = {'keys': states} %}"
            f"{{{{ helpers['keys']['{helper}'] }}}}",
            "{% set helpers = {'nested': {'lookup': is_state}} %}"
            f"{{{{ helpers.get('nested').get('lookup')('{helper}', 'on') }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', is_state)('{helper}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers.get('message', states('{helper}')) }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% for lookup in helpers.values() %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set helpers = {{'{helper}': 'ordinary'}} %}}"
            "{% for entity in helpers.keys() %}"
            "{{ states(entity) }}{% endfor %}",
            f"{{% set helpers = {{'{helper}': states}} %}}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('{helper}')('{helper}') }}}}",
            f"{{% set helpers = {{'{helper}': states}} %}}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('{helper}')['{helper}'] }}}}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% set values = helpers.values %}"
            "{% for lookup in values() %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set helpers = {{'{helper}': 'ordinary'}} %}}"
            "{% set keys = helpers.keys %}"
            "{% for entity in keys() %}"
            "{{ states(entity) }}{% endfor %}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods.getter('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods['getter']('{helper}')['{helper}'] }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods.get('getter')('{helper}')['{helper}'] }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', states)('{helper}') }}}}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', states)['{helper}'] }}}}",
            f"{{% set helpers = {{'{helper}': 'ordinary'}} %}}"
            "{% for entity in helpers['keys']() %}"
            "{{ states(entity) }}{% endfor %}",
            "{% set helpers = {'lookup': is_state} %}"
            "{% for lookup in helpers['values']() %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endfor %}",
            f"{{% set with_value = {{'{helper}': 'ordinary'}} %}}"
            "{% set without_value = {} %}"
            "{% set getter = "
            "with_value.get if enabled else without_value.get %}"
            f"{{{{ getter('{helper}', states)['{helper}'] }}}}",
            f"{{% set helpers = {{'{helper}': 'ordinary'}} "
            "if enabled else {} %}"
            f"{{{{ helpers.get('{helper}', states)['{helper}'] }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set level1 = {'next': source.get} %}"
            "{% set level2 = {'next': level1.get} %}"
            "{{ level2.get('next')('next')"
            f"('{helper}')('{helper}') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'get' %}"
            "{{ helpers[dynamic_key]("
            "'missing', is_state)("
            f"'{helper}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers[states('{helper}')] }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set lookup = is_state %}"
            f"{{{{ helpers[lookup('{helper}', 'on')] }}}}",
        )
        conservative_templates = (
            "{% set original = states %}"
            "{% set lookup = original if enabled else unknown_collection %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set lookup = states if enabled else is_state %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set lookup = unknown_collection %}"
            f"{{{{ lookup['{helper}'] }}}}",
            "{% set lookup = unknown_collection %}"
            "{{ lookup.input_boolean.synthetic_exact }}",
            "{% set lookup = states %}{{ lookup }}",
            "{% set lookup = states %}"
            "{% for item in lookup %}{{ item }}{% endfor %}",
            "{% macro check() %}{% set lookup = is_state %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endmacro %}{{ check() }}",
            "{% with lookup = is_state %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endwith %}",
            "{% set lookup = states %}{% macro check() %}"
            f"{{{{ lookup['{helper}'] }}}}"
            "{% endmacro %}{{ check() }}",
            "{% call(lookup) supply(is_state) %}"
            f"{{{{ lookup('{helper}', 'on') }}}}"
            "{% endcall %}",
            "{% set helpers = {'lookup': unknown_callable} %}"
            f"{{{{ helpers.lookup('{helper}') }}}}",
            "{% set helpers = "
            "{'lookup': states if enabled else unknown_collection} %}"
            f"{{{{ helpers.lookup['{helper}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers[dynamic_key]['{helper}'] }}}}",
            "{% set helpers = {'lookup': states} %}"
            f"{{{{ helpers[dynamic_key]('{helper}') }}}}",
            "{% set helpers = {'nested': {'lookup': unknown_callable}} %}"
            f"{{{{ helpers.nested.lookup('{helper}') }}}}",
            "{% set helpers = {'message': 'ready', 'lookup': states} %}"
            f"{{{{ helpers.get(dynamic_key)('{helper}') }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.items() | list }}",
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.values() | list }}",
            "{% set helpers = {} %}"
            f"{{{{ helpers.get('missing', unknown_callable)('{helper}') }}}}",
            "{% set helpers = {'nested': {'lookup': unknown_callable}} %}"
            f"{{{{ helpers.get('nested').get('lookup')('{helper}') }}}}",
            "{% set helpers = {'message': 'ready', 'lookup': states} %}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter(dynamic_key)('{helper}') }}}}",
            "{% set helpers = {'lookup': states} %}"
            "{% set items = helpers.items %}"
            "{{ items() | list }}",
            "{% set helpers = {'lookup': states} %}"
            "{% set values = helpers.values if enabled else unknown_callable %}"
            "{{ values() | list }}",
            "{% set source = {'lookup': states} %}"
            "{% set methods = "
            "{'getter': source.get if enabled else unknown_callable} %}"
            f"{{{{ methods.getter('lookup')('{helper}') }}}}",
            "{% set source = {'lookup': states} %}"
            "{% set methods = {'getter': source.get} %}"
            f"{{{{ methods[dynamic_key]('lookup')('{helper}') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message', states | list) }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(states | list, 'fallback') }}",
            f"{{% set helpers = {{'{helper}': states}} %}}"
            f"{{{{ (helpers | attr('get'))('{helper}')('{helper}') }}}}",
            f"{{% set helpers = {{'{helper}': states}} %}}"
            f"{{{{ ((helpers) | attr('get'))('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set outer = {'source': source} %}"
            "{{ (outer['source'] | attr('get'))"
            f"('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set outer = {'source': source} %}"
            "{{ (outer.get('source') | attr('get'))"
            f"('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='get') | first)"
            f"('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map('attr', 'get') | first)"
            f"('{helper}')('{helper}') }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute=method_name) | first)"
            f"('{helper}')('{helper}') }}}}",
            "{% set source = {'lookup': states} %}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='lookup') | first)"
            f"('{helper}') }}}}",
            "{% set source = {'nested': {'lookup': is_state}} %}"
            "{% set containers = [source] %}"
            "{{ (containers | map(attribute='nested.lookup') | first)"
            f"('{helper}', 'on') }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ (containers | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ (containers | select | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ (containers | reject | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ (containers | selectattr('message', 'defined') | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ (containers | rejectattr('message', 'defined') | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ ((containers | first) if enabled else {})"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ [(containers | first)][0]"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{{ {'selected': (containers | first)}['selected']"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set containers = [{{'{helper}': states}}] %}}"
            "{% set fallback = containers[0] %}"
            "{{ (containers | map(attribute='missing', default=fallback)"
            " | first)"
            f".get('{helper}')['{helper}'] }}}}",
            f"{{% set source = {{'{helper}': states}} %}}"
            "{% set fallback = source.get %}{% set containers = [{}] %}"
            "{{ (containers | map(attribute='missing', default=fallback)"
            f" | first)('{helper}')['{helper}'] }}}}",
            "{% set containers = [{}] %}"
            "{{ (containers | map(attribute='missing', default=unknown)"
            f" | first)('{helper}') }}}}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | unknown_filter | first)"
            f"('{helper}') }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ (containers | first].lookup['{helper}'] }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ [containers | first).lookup['{helper}'] }}}}",
            "{% set containers = [{'lookup': states}] %}"
            f"{{{{ {{containers | first].lookup['{helper}'] }}}}",
            "{% set containers = [{}] %}"
            f"{{% set fallback = {{'{helper}': states}} %}}"
            "{{ ((containers | groupby('missing', default=fallback)"
            f" | first)[0]).get('{helper}')['{helper}'] }}}}",
            "{% set messages = ['ready'] %}"
            f"{{{{ (messages | batch(2, states) | first)[1]('{helper}') }}}}",
            "{% set messages = ['ready'] %}"
            f"{{{{ (messages | slice(2, states) | list | first)[1]('{helper}') }}}}",
            "{% set value = '' %}"
            "{{ (value | default(default_value=states, boolean=true))"
            f"('{helper}') }}}}",
            "{% set value = 'ready' %}"
            "{{ (value | default('fallback', true, boolean=false))"
            ".upper() }}",
            "{% set helpers = {'lookup': 'ordinary'} "
            "if enabled else unknown_mapping %}"
            "{% set getter = helpers.get %}"
            f"{{{{ getter('lookup')('{helper}') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers[dynamic_key]("
            "'missing', is_state)("
            f"'{helper}', 'on') }}}}",
            "{% set helpers = {'get': 'ordinary', "
            "'items': 'ordinary', 'keys': 'ordinary', "
            "'values': 'ordinary', 'message': 'ready'} %}"
            "{{ helpers[dynamic_key]().get("
            "'missing', is_state)("
            f"'{helper}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            f"{{{{ helpers[dynamic_key](*['missing', is_state])"
            f"('{helper}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(*('missing', is_state))("
            f"'{helper}', 'on') }}}}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set lookup = unknown_callable %}"
            f"{{{{ helpers[lookup('{helper}', 'on')] }}}}",
            deep_argument_template,
            method_chain_template,
        )
        unrelated_templates = (
            "{% set helpers = {'lookup': states} %}"
            "{{ helpers.lookup['sensor.unrelated'] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.message }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            "{% set helpers = {'get': states, 'message': 'ready'} %}"
            "{{ helpers.get('message') }}",
            "{% set helpers = {'get': 'ready'} %}"
            "{{ helpers['get'] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get(dynamic_key, 'fallback') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.items() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.values() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.keys() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set getter = helpers.get %}"
            "{{ getter('message') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set items = helpers.items %}"
            "{{ items() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set values = helpers.values %}"
            "{{ values() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set keys = helpers.keys %}"
            "{{ keys() | list }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers.get('message', states) }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{{ helpers['get']('message') }}",
            "{% set source = {'message': 'ready'} %}"
            "{% set methods = {'getter': source.get} %}"
            "{{ methods.getter('message') }}",
            "{% set source = {'message': 'ready'} %}"
            "{% set methods = {'getter': source.get} %}"
            "{{ methods['getter']('message') }}",
            "{% set with_value = {'message': 'ready'} %}"
            "{% set without_value = {} %}"
            "{% set getter = "
            "with_value.get if enabled else without_value.get %}"
            "{{ getter('message', 'fallback') }}",
            f"{{% set first = {{'{helper}': 'ordinary'}} %}}"
            f"{{% set second = {{'{helper}': 'ordinary'}} %}}"
            "{% set getter = first.get if enabled else second.get %}"
            f"{{{{ getter('{helper}', states) }}}}",
            "{{ \"example | attr('get')\" }}",
            "{{ 'documentation: | attr(' }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | first).get('message') }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | selectattr('message', 'defined') | first)"
            ".get('message') }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ containers | map(attribute='message') | list }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ [((containers | first) if enabled else {})][0]"
            ".get('message', 'fallback') }}",
            "{% set containers = [{}] %}"
            "{{ (containers | map(attribute='message', default='ready')"
            " | first) }}",
            "{% set containers = "
            "[{'priority': 1, 'message': 'ready'}] %}"
            "{{ (containers | selectattr('priority', 'eq', 1) | first)"
            ".message }}",
            "{% set containers = [{'message': 'ready'}] %}"
            "{{ (containers | sort(reverse=true) | first).message }}",
            "{% set messages = ['ready', 'done'] %}"
            "{{ (messages | batch(2) | first)[0] }}",
            "{% set value = 'ready' %}"
            "{{ (value | default('fallback', use_boolean)).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(default_value='fallback')).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(boolean=true)).upper() }}",
            "{% set value = 'ready' %}"
            "{{ (value | default(default_value='fallback', "
            "boolean=use_boolean)).upper() }}",
            "{% set containers = [{}] %}"
            "{{ (containers | groupby('missing', default='ordinary')"
            " | first)[0] }}",
            "{% set messages = ['ready'] %}"
            "{{ (messages | batch(2, 'ordinary') | first)[1] }}",
            "{% set messages = ['ready'] %}"
            "{{ (messages | slice(2, 'ordinary') | list | first)[1] }}",
            "{% set helpers = {'message': 'ready', 'title': 'done'} %}"
            "{% set dynamic_key = 'message' if enabled else 'title' %}"
            "{{ helpers[dynamic_key] }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'get' %}"
            "{{ helpers[dynamic_key]('missing', 'fallback') }}",
            "{% set helpers = {'message': 'ready'} %}"
            "{% set dynamic_key = 'keys' if enabled else 'values' %}"
            "{{ helpers[dynamic_key]() | list }}",
        )

        for template in exact_templates:
            with self.subTest(kind="exact", template=template):
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
                keys = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertIn(exact_key, keys)
                self.assertNotIn(dynamic_key, keys)

        for template in conservative_templates:
            with self.subTest(kind="conservative", template=template):
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
                keys = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertIn(dynamic_key, keys)
                self.assertNotIn(exact_key, keys)

        for template in unrelated_templates:
            with self.subTest(kind="unrelated", template=template):
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
                keys = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertNotIn(exact_key, keys)
                self.assertNotIn(dynamic_key, keys)

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
            f'{{{{ "{helper}" is not is_state("off") }}}}',
            f'{{{{ "{helper}" is not is_state_attr("mode", "off") }}}}',
            f'{{{{ "{helper}" is not has_value }}}}',
            f'{{{{ ["{helper}"] | select("is_state", "on") | list }}}}',
            f'{{{{ ["{helper}"] '
            '| select("is_state_attr", "mode", "on") | list }}',
            f'{{{{ ["{helper}"] | select("has_value") | list }}}}',
            f'{{{{ ["{helper}"] | map("states") | list }}}}',
            f'{{{{ ["{helper}"] '
            '| map("state_attr", "friendly_name") | list }}',
            f'{{{{ ["{helper}"] | map("has_value") | list }}}}',
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

        collection_cases = (
            (
                '{{ ["sensor.a"] | select("is_state", "on") | list }}',
                False,
            ),
            (
                "{{ states.sensor | map(attribute='entity_id') "
                '| select("is_state", "on") | list }}',
                False,
            ),
            (
                "{{ states.sensor "
                "| rejectattr('entity_id', 'has_value') | list }}",
                False,
            ),
            (
                '{{ helper_entities '
                '| select("is_state", "on") | list }}',
                True,
            ),
            (
                f'{{{{ ["{helper}"] '
                '| select(test_name, "on") | list }}',
                True,
            ),
            (
                f'{{{{ [{{"entity_id": "{helper}"}}] '
                '| selectattr("entity_id", "has_value") | list }}',
                True,
            ),
            (f'{{{{ ["{helper}"] | map( }}}}', True),
        )
        for template, expects_dynamic_lock in collection_cases:
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
                self.assertEqual(
                    dynamic_key in locks, expects_dynamic_lock
                )

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

    async def test_nested_collection_dependency_locks_are_exact_or_conservative(self):
        helper = "input_boolean.synthetic_exact"
        exact_key = helper_dependency_lock_key(helper)
        dynamic_key = unconstrained_helper_dependency_lock_key()
        base = valid_config("automation")
        exact_forms = (
            f'{{{{ (["{helper}"] '
            '| select("is_state", "on") | list) | count }}}}',
            f'{{{{ (["{helper}"] | map("states") | list) '
            '| select("defined") | list }}}}',
            f'{{{{ (["{helper}"] '
            '| select("is_state", "on") | list) '
            '| map("states") | list }}}}',
            f'{{{{ (["{helper}"] '
            '| reject("has_value") | list) if enabled else [] }}}}',
            f'{{{{ ["{helper}"] | select("is_state", "on") '
            "if enabled else [] }}",
        )
        for action in ("create", "update"):
            for index, template in enumerate(exact_forms):
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
                        operation_id=f"nested_exact_{action}_{index}",
                        current_config=(
                            base if action == "update" else None
                        ),
                        proposed_config=relevant,
                    )
                    locks = {
                        item.key
                        for item in operation_lock_requests(prepared)
                    }
                    self.assertIn(exact_key, locks)

        reviewed_helper_operator = (
            f'["{helper}"] | select("is_state", "on") | list'
        )
        malformed_forms = tuple(
            "{{ " + delimiter + reviewed_helper_operator + " }}"
            for delimiter in ("(", "[", "{")
        ) + tuple(
            "{{ " + reviewed_helper_operator + " + " + delimiter + " }}"
            for delimiter in ("(", "[", "{")
        ) + (
            "{{ "
            + reviewed_helper_operator
            + " if enabled else ( }}",
        )
        dynamic_forms = (
            '{{ (helper_entities '
            '| select("is_state", "on") | list) | count }}',
            f'{{{{ (["{helper}"] '
            '| select(test_name, "on") | list) | count }}}}',
            '{{ (helper_entities | map("states") | list) '
            'if enabled else [] }}',
            f'{{{{ (["{helper}"] | select("is_state", "on") '
            "| list) if enabled else (helper_entities "
            '| map("states") | list) }}',
            "{{ "
            + "(" * 10_000
            + f'["{helper}"] | select("is_state", "on") | list'
            + " }}",
        ) + malformed_forms
        for index, template in enumerate(dynamic_forms):
            with self.subTest(template=template):
                unresolved = valid_config("automation")
                unresolved["condition"] = [
                    {
                        "condition": "template",
                        "value_template": template,
                    }
                ]
                prepared = await self._prepared(
                    "automation",
                    "update",
                    operation_id=f"nested_dynamic_{index}",
                    current_config=base,
                    proposed_config=unresolved,
                )
                locks = {
                    item.key
                    for item in operation_lock_requests(prepared)
                }
                self.assertIn(dynamic_key, locks)

        unrelated = valid_config("automation")
        unrelated["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    '{{ (["sensor.a", "sensor.b"] '
                    '| select("is_state", "on") | list) | count }}'
                ),
            }
        ]
        unrelated_prepared = await self._prepared(
            "automation",
            "update",
            operation_id="nested_unrelated",
            current_config=base,
            proposed_config=unrelated,
        )
        unrelated_locks = {
            item.key
            for item in operation_lock_requests(unrelated_prepared)
        }
        self.assertNotIn(exact_key, unrelated_locks)
        self.assertNotIn(dynamic_key, unrelated_locks)

        conditional_unrelated = valid_config("automation")
        conditional_unrelated["condition"] = [
            {
                "condition": "template",
                "value_template": (
                    '{{ ["sensor.a"] | select("is_state", "on") '
                    "if enabled else [] }}"
                ),
            }
        ]
        conditional_unrelated_prepared = await self._prepared(
            "automation",
            "update",
            operation_id="conditional_unrelated",
            current_config=base,
            proposed_config=conditional_unrelated,
        )
        conditional_unrelated_locks = {
            item.key
            for item in operation_lock_requests(
                conditional_unrelated_prepared
            )
        }
        self.assertNotIn(exact_key, conditional_unrelated_locks)
        self.assertNotIn(dynamic_key, conditional_unrelated_locks)

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
