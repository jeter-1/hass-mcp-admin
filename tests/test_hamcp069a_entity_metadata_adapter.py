"""HAMCP-069A-1 entity metadata configuration-adapter tests (V8, V9).

Offline and synthetic.  V8 (owner direction 2026-10-12): Home Assistant's
configuration check never gates an entity metadata update, while it still
gates every existing configuration child.  V9: a final state already present
at preflight is verified with zero dispatch.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome  # noqa: E402
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.models import (  # noqa: E402
    ConfigurationOperationProposal,
)
from ha_mcp_engineering.f3_configuration.strategies import (  # noqa: E402
    strategy_for,
)
from ha_mcp_engineering.governance.entity_metadata import (  # noqa: E402
    ENTITY_METADATA_BINDINGS_KEY,
    ENTITY_METADATA_RESOURCE_TYPE,
)
from ha_mcp_engineering.governance.normalize import stable_hash  # noqa: E402

from tests.f3_configuration_fixtures import (  # noqa: E402
    FIXED_DEADLINE,
    FIXED_NOW,
    SyntheticConfigurationGateway,
    adapter_for,
    proposal_for,
)


TARGET = "light.porch"
FAILING_CHECK = {"result": "invalid", "errors": "synthetic unrelated YAML error"}


def entry(**changes: Any) -> dict[str, Any]:
    value = {
        "entity_id": TARGET,
        "id": "synthetic-registry-id-porch",
        "platform": "synthetic",
        "device_id": None,
        "config_entry_id": None,
        "disabled_by": None,
        "entity_category": None,
        "has_entity_name": False,
        "original_name": "Porch",
        "name": None,
        "icon": None,
        "aliases": [],
        "area_id": None,
        "hidden_by": None,
        "labels": [],
        "categories": {},
        "modified_at": 1.0,
    }
    value.update(changes)
    return value


def final(**changes: Any) -> dict[str, Any]:
    value = entry(**changes)
    value[ENTITY_METADATA_BINDINGS_KEY] = {
        "reference_definitions": {},
        "consumer_evidence_fingerprint": "5" * 64,
    }
    return value


class SyntheticEntityRegistryGateway(SyntheticConfigurationGateway):
    """Applies the closed update body as Home Assistant merges it."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__({(ENTITY_METADATA_RESOURCE_TYPE, TARGET): current})
        self.evidence_codes: tuple[str, ...] = ()
        self.sent: list[dict[str, Any]] = []

    async def entity_metadata_preflight_evidence(
        self, target_id, current, proposed
    ) -> tuple[str, ...]:
        del target_id, current, proposed
        return self.evidence_codes

    async def write(self, action, resource_type, target_id, proposed_config):
        self.counters.dispatches += 1
        self.sent.append(deepcopy(proposed_config))
        state = self.states[(resource_type, target_id)]
        for field, value in proposed_config.items():
            if field == "categories":
                merged = dict(state.get("categories") or {})
                for scope, category in value.items():
                    if category is None:
                        merged.pop(scope, None)
                    else:
                        merged[scope] = category
                state["categories"] = merged
            else:
                state[field] = deepcopy(value)
        state["modified_at"] = float(state.get("modified_at", 0)) + 1
        self.counters.simulated_mutations += 1
        return {"accepted": True}


def entity_proposal(
    current: dict[str, Any], proposed: dict[str, Any]
) -> ConfigurationOperationProposal:
    strategy = strategy_for(ENTITY_METADATA_RESOURCE_TYPE, "update")
    return ConfigurationOperationProposal.from_configs(
        plan_id="a" * 32,
        plan_hash="1" * 64,
        plan_contract_version=2,
        task_id="b" * 32,
        operation_id="step_1",
        order=0,
        depends_on=(),
        resource_type=ENTITY_METADATA_RESOURCE_TYPE,
        action="update",
        target_id=TARGET,
        current_config=current,
        proposed_config=proposed,
        current_state_fingerprint=strategy.fingerprint(current),
        proposed_config_hash=stable_hash(strategy.normalize(proposed)),
        normalization_version=1,
        risk_level="low",
        risk_evidence_hash="2" * 64,
        policy_class="standard_admin",
        policy_decision_hash="3" * 64,
        approval_bundle_hash="4" * 64,
        plan_expires_at=FIXED_DEADLINE,
        policy_snapshot_valid=True,
        provider_admitted=True,
        rollback_available=strategy.rollback_available(2),
    )


def entity_adapter(gateway) -> ConfigurationOperationAdapter:
    return ConfigurationOperationAdapter(
        ENTITY_METADATA_RESOURCE_TYPE,
        "update",
        gateway,
        now=lambda: FIXED_NOW,
    )


async def _no_intent() -> None:
    return None


class EntityMetadataConfigurationCheckTests(unittest.IsolatedAsyncioTestCase):
    """V8: no ``check_config`` gate for entity metadata only."""

    async def _run_entity(self, gateway):
        current = deepcopy(
            gateway.states[(ENTITY_METADATA_RESOURCE_TYPE, TARGET)]
        )
        adapter = entity_adapter(gateway)
        prepared = await adapter.prepare(
            entity_proposal(current, final(labels=["outdoor"]))
        )
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        return adapter, prepared, preflight

    async def test_failing_check_config_does_not_block_entity_metadata(self):
        for label, configure in (
            ("invalid", lambda g: setattr(g, "validation_result", FAILING_CHECK)),
            (
                "unavailable",
                lambda g: setattr(g, "validation_error", OSError("synthetic")),
            ),
        ):
            with self.subTest(check=label):
                gateway = SyntheticEntityRegistryGateway(entry())
                configure(gateway)
                adapter, prepared, preflight = await self._run_entity(gateway)
                self.assertTrue(preflight.eligible, preflight.diagnostic_codes)
                self.assertEqual(
                    preflight.configuration_check_status, "not_applicable"
                )
                dispatch = await adapter.dispatch(
                    prepared, preflight, before_dispatch=_no_intent
                )
                self.assertEqual(dispatch.mutating_invocation_count, 1)
                self.assertEqual(gateway.sent, [{"labels": ["outdoor"]}])
                observation = await adapter.observe(prepared, dispatch)
                verification = await adapter.verify(prepared, observation)
                self.assertEqual(
                    verification.outcome,
                    NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                )
                self.assertTrue(verification.verified)
                self.assertEqual(
                    observation.configuration_check_status, "not_applicable"
                )
                self.assertNotIn(
                    "configuration_check", observation.mismatch_fields
                )
                # The configuration check is never invoked at all.
                self.assertEqual(gateway.counters.validation_calls, 0)

    async def test_failing_check_config_still_blocks_existing_children(self):
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            with self.subTest(resource_type=resource_type):
                proposal = proposal_for(resource_type, "update")
                gateway = SyntheticConfigurationGateway(
                    {(resource_type, proposal.target_id): proposal.current_config()}
                )
                gateway.validation_result = FAILING_CHECK
                adapter = adapter_for(resource_type, "update", gateway)
                prepared = await adapter.prepare(proposal)
                result = await adapter.preflight(
                    prepared, acquired_locks=adapter.lock_requests(prepared)
                )
                self.assertFalse(result.eligible)
                self.assertIn(
                    "configuration_validation_failed", result.diagnostic_codes
                )
                self.assertEqual(gateway.counters.validation_calls, 1)
                self.assertEqual(gateway.counters.dispatches, 0)

    async def test_existing_child_verification_still_requires_check_config(
        self,
    ):
        proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): proposal.current_config()}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertTrue(preflight.eligible)
        dispatch = await adapter.dispatch(
            prepared, preflight, before_dispatch=_no_intent
        )
        gateway.validation_result = FAILING_CHECK
        observation = await adapter.observe(prepared, dispatch)
        verification = await adapter.verify(prepared, observation)
        self.assertEqual(observation.configuration_check_status, "failed")
        self.assertIn("configuration_check", observation.mismatch_fields)
        self.assertNotEqual(
            verification.outcome,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
        )

    def test_hook_is_set_only_on_the_entity_metadata_strategy(self):
        self.assertFalse(
            strategy_for(
                ENTITY_METADATA_RESOURCE_TYPE, "update"
            ).requires_configuration_check
        )
        for resource_type in (
            "automation",
            "script",
            "input_boolean",
            "input_number",
        ):
            for action in ("create", "update"):
                with self.subTest(resource_type=resource_type, action=action):
                    self.assertTrue(
                        strategy_for(
                            resource_type, action
                        ).requires_configuration_check
                    )


class EntityMetadataAlreadyReachedTests(unittest.IsolatedAsyncioTestCase):
    """V9 at the adapter boundary: verified, zero dispatch."""

    async def test_final_state_already_present_is_verified_without_dispatch(
        self,
    ):
        current = entry()
        gateway = SyntheticEntityRegistryGateway(current)
        adapter = entity_adapter(gateway)
        prepared = await adapter.prepare(
            entity_proposal(current, final(labels=["outdoor"]))
        )
        # Someone else applied the same final state after planning.
        gateway.states[(ENTITY_METADATA_RESOURCE_TYPE, TARGET)] = entry(
            labels=["outdoor"], modified_at=2.0
        )
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(preflight.eligible)
        self.assertEqual(
            preflight.outcome, NormalizedOperationOutcome.SUCCEEDED_VERIFIED
        )
        self.assertEqual(
            preflight.diagnostic_codes, ("desired_state_already_reached",)
        )
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_other_concurrent_change_is_a_stale_refusal(self):
        current = entry()
        gateway = SyntheticEntityRegistryGateway(current)
        adapter = entity_adapter(gateway)
        prepared = await adapter.prepare(
            entity_proposal(current, final(labels=["outdoor"]))
        )
        gateway.states[(ENTITY_METADATA_RESOURCE_TYPE, TARGET)] = entry(
            labels=["indoor"], modified_at=2.0
        )
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertFalse(preflight.eligible)
        self.assertEqual(
            preflight.outcome, NormalizedOperationOutcome.PREFLIGHT_REJECTED
        )
        self.assertIn("stale_target_state", preflight.diagnostic_codes)
        self.assertEqual(gateway.counters.dispatches, 0)

    async def test_existing_children_never_report_already_reached(self):
        proposal = proposal_for("automation", "update")
        gateway = SyntheticConfigurationGateway(
            {("automation", proposal.target_id): proposal.current_config()}
        )
        adapter = adapter_for("automation", "update", gateway)
        prepared = await adapter.prepare(proposal)
        reached = deepcopy(proposal.proposed_config())
        reached["id"] = proposal.target_id
        gateway.states[("automation", proposal.target_id)] = reached
        preflight = await adapter.preflight(
            prepared, acquired_locks=adapter.lock_requests(prepared)
        )
        self.assertIn("stale_target_state", preflight.diagnostic_codes)
        self.assertNotIn(
            "desired_state_already_reached", preflight.diagnostic_codes
        )


if __name__ == "__main__":
    unittest.main()
