"""Deterministic offline fixtures for F3-C1 adapter conformance."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from f3_contracts.operation_adapter import RecoveryContext
from ha_mcp_engineering.errors import HomeAssistantApiError
from ha_mcp_engineering.f3_configuration.adapter import (
    ConfigurationOperationAdapter,
)
from ha_mcp_engineering.f3_configuration.models import (
    ConfigurationOperationProposal,
)
from ha_mcp_engineering.f3_configuration.strategies import strategy_for
from ha_mcp_engineering.governance.normalize import stable_hash
from ha_mcp_engineering.governance.resources import (
    ConfigurationMutationNotDispatchedError,
)


FIXED_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
FIXED_DEADLINE = "2026-08-05T12:00:00+00:00"


class SyntheticProcessLoss(BaseException):
    """A process boundary deliberately not caught as a provider exception."""


@dataclass
class FixtureCounters:
    reads: int = 0
    validation_calls: int = 0
    dispatches: int = 0
    simulated_mutations: int = 0
    observations: int = 0
    verification_calls: int = 0
    recovery_calls: int = 0
    create_absence_checks: int = 0


class SyntheticConfigurationGateway:
    """Exact closed gateway with fault injection and mutation counters."""

    def __init__(
        self,
        states: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.states = deepcopy(states or {})
        self.provider_admitted = True
        self.validation_result: Any = {"result": "valid", "errors": None}
        self.validation_error: Exception | None = None
        self.read_error_count = 0
        self.dispatch_mode = "success"
        self.reserved_entity_ids: set[str] = set()
        self.before_write_hook: Callable[
            ["SyntheticConfigurationGateway", str, str], None
        ] | None = None
        self.after_write_hook: Callable[
            ["SyntheticConfigurationGateway", str, str], None
        ] | None = None
        self.counters = FixtureCounters()

    async def read(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        self.counters.reads += 1
        if self.read_error_count:
            self.read_error_count -= 1
            raise HomeAssistantApiError(
                details={"reason": "synthetic_read_unavailable"}
            )
        return deepcopy(self.states.get((resource_type, target_id)))

    async def validate_all(self) -> Any:
        self.counters.validation_calls += 1
        if self.validation_error is not None:
            raise self.validation_error
        return deepcopy(self.validation_result)

    async def create_target_absent(
        self, resource_type: str, target_id: str
    ) -> tuple[bool, str]:
        self.counters.create_absence_checks += 1
        if (resource_type, target_id) in self.states:
            return False, "target_already_exists"
        if target_id in self.reserved_entity_ids:
            return False, "target_entity_id_reserved"
        return True, "target_absent"

    async def write(
        self,
        action: str,
        resource_type: str,
        target_id: str,
        proposed_config: dict[str, Any],
    ) -> Any:
        self.counters.dispatches += 1
        if self.before_write_hook is not None:
            self.before_write_hook(self, resource_type, target_id)
        if self.dispatch_mode == "confirmed_failure":
            raise ConfigurationMutationNotDispatchedError(
                details={"reason": "synthetic_confirmed_no_mutation"}
            )
        if self.dispatch_mode == "response_lost_before_effect":
            raise HomeAssistantApiError(
                details={"reason": "synthetic_response_lost"}
            )
        if self.dispatch_mode == "process_loss_before_effect":
            raise SyntheticProcessLoss()
        if action == "create" and (resource_type, target_id) in self.states:
            raise ConfigurationMutationNotDispatchedError(
                details={"reason": "target_already_exists"}
            )
        self.states[(resource_type, target_id)] = deepcopy(proposed_config)
        self.states[(resource_type, target_id)]["id"] = (
            target_id.split(".", 1)[1]
            if resource_type in {"input_boolean", "input_number"}
            else target_id
        )
        self.counters.simulated_mutations += 1
        if self.after_write_hook is not None:
            self.after_write_hook(self, resource_type, target_id)
        if self.dispatch_mode == "response_lost_after_effect":
            raise HomeAssistantApiError(
                details={"reason": "synthetic_response_lost"}
            )
        if self.dispatch_mode == "malformed_provider_response":
            raise HomeAssistantApiError(
                details={
                    "reason": "synthetic_malformed_response",
                    "provider_response_received": True,
                }
            )
        if self.dispatch_mode == "process_loss_after_effect":
            raise SyntheticProcessLoss()
        return {"accepted": True}


class ConfigurationLifecycleHarness:
    """Test-only protocol caller; it is not durable execution authority."""

    def __init__(self, adapter: ConfigurationOperationAdapter) -> None:
        self.adapter = adapter
        self.gateway = adapter.gateway
        self.intent_commits = 0

    async def intent(self) -> None:
        self.intent_commits += 1

    async def observe(self, operation, dispatch):
        self.gateway.counters.observations += 1
        return await self.adapter.observe(operation, dispatch)

    async def verify(self, operation, observation):
        self.gateway.counters.verification_calls += 1
        return await self.adapter.verify(operation, observation)

    async def recover(self, operation, context: RecoveryContext):
        self.gateway.counters.recovery_calls += 1
        return await self.adapter.recover(operation, context=context)


def valid_config(resource_type: str, *, updated: bool = False) -> dict[str, Any]:
    suffix = " Updated" if updated else ""
    if resource_type == "automation":
        return {
            "alias": f"Porch light{suffix}",
            "trigger": [{"platform": "state", "entity_id": "sensor.synthetic"}],
            "condition": [],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.synthetic"}}],
            "mode": "single",
        }
    if resource_type == "script":
        return {
            "alias": f"Notify{suffix}",
            "sequence": [{"service": "notify.synthetic", "data": {"message": "test"}}],
            "mode": "single",
        }
    if resource_type == "input_boolean":
        return {
            "name": f"Vacation Mode{suffix}",
            "icon": "mdi:toggle-switch",
            "initial": False,
        }
    if resource_type == "input_number":
        return {
            "name": f"Target Temperature{suffix}",
            "min": 10,
            "max": 30,
            "step": 0.5,
            "initial": 20,
            "mode": "slider",
            "unit_of_measurement": "C",
        }
    raise ValueError("unsupported synthetic resource")


def target_id(resource_type: str) -> str:
    return {
        "automation": "porch_light",
        "script": "notify_house",
        "input_boolean": "input_boolean.vacation_mode",
        "input_number": "input_number.target_temperature",
    }[resource_type]


def proposal_for(
    resource_type: str,
    action: str,
    *,
    operation_id: str = "step_1",
    order: int = 0,
    depends_on: tuple[str, ...] = (),
    plan_id: str = "a" * 32,
    task_id: str = "b" * 32,
    current_config: dict[str, Any] | None | object = ...,
    proposed_config: dict[str, Any] | None = None,
    approval_consumed: bool = True,
    policy_snapshot_valid: bool = True,
    provider_admitted: bool = True,
) -> ConfigurationOperationProposal:
    strategy = strategy_for(resource_type, action)
    if current_config is ...:
        current = None if action == "create" else valid_config(resource_type)
        if current is not None:
            current["id"] = (
                target_id(resource_type).split(".", 1)[1]
                if resource_type in {"input_boolean", "input_number"}
                else target_id(resource_type)
            )
    else:
        current = current_config
    proposed = proposed_config or valid_config(
        resource_type, updated=action == "update"
    )
    normalized_proposed = strategy.normalize(proposed)
    if normalized_proposed is None:
        raise AssertionError("synthetic proposal unexpectedly normalized away")
    return ConfigurationOperationProposal.from_configs(
        plan_id=plan_id,
        plan_hash="1" * 64,
        plan_contract_version=2,
        task_id=task_id,
        operation_id=operation_id,
        order=order,
        depends_on=depends_on,
        resource_type=resource_type,
        action=action,
        target_id=target_id(resource_type),
        current_config=current,
        proposed_config=proposed,
        current_state_fingerprint=strategy.fingerprint(current),
        proposed_config_hash=stable_hash(normalized_proposed),
        normalization_version=1,
        risk_level="medium",
        risk_evidence_hash="2" * 64,
        policy_class="standard_admin",
        policy_decision_hash="3" * 64,
        approval_bundle_hash="4" * 64,
        plan_expires_at=FIXED_DEADLINE,
        approval_consumed=approval_consumed,
        policy_snapshot_valid=policy_snapshot_valid,
        provider_admitted=provider_admitted,
        rollback_available=strategy.rollback_available(2),
    )


def adapter_for(
    resource_type: str,
    action: str,
    gateway: SyntheticConfigurationGateway,
) -> ConfigurationOperationAdapter:
    return ConfigurationOperationAdapter(
        resource_type,
        action,
        gateway,
        now=lambda: FIXED_NOW,
    )


def recovery_context(
    *,
    response_received: bool = False,
    prior_observations: int = 0,
    deadline: str = FIXED_DEADLINE,
) -> RecoveryContext:
    return RecoveryContext(
        dispatch_intent_recorded=True,
        provider_invocation_may_have_occurred=True,
        provider_response_received=response_received,
        prior_observation_attempts=prior_observations,
        prior_verification_attempts=0,
        post_dispatch_deadline=deadline,
    )
