"""F3 adapter for governed deletion of one automation or script (HAMCP-155).

A delete child records the exact pre-image as its current state and a closed
delete intent as its proposal; its final state is absence.  The adapter is a
separate class so the generic configuration adapter keeps its create/update
contract unchanged.

Admission is checked by the adapter itself, at preflight and again
immediately before durable intent, because the Core dispatch guard is only
installed when a Core runtime exists.  Immediately before the DELETE the live
configuration is re-read and compared with the bound pre-image fingerprint;
drift refuses as stale with no write.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome

from ..errors import AuthorizationError
from ..governance import configuration_delete as delete_contract
from ..governance.normalize import stable_hash
from ..governance.resources import resource_identity_matches
from .adapter import PRIOR_STATE_OBSERVED, ConfigurationOperationAdapter
from .locks import lock_set_hash
from .models import (
    ConfigurationObservationResult,
    ConfigurationPreflightResult,
    ConfigurationProviderDescriptor,
    ConfigurationVerificationResult,
    PreparedConfigurationOperation,
    bounded_mismatches,
)
from .observability import (
    ConfigurationAdapterMetrics,
    ConfigurationEventSink,
    NullConfigurationEventSink,
)
from .strategies import ConfigurationStrategy


CORE_ADMISSION_UNAVAILABLE = "core_admission_unavailable"
DELETE_TARGET_MISSING = "delete_target_missing"
REGISTRY_ENTRY_MISSING = "registry_entry_missing"
REGISTRY_ENTRY_CHANGED = "registry_entry_changed"
CONFIG_STILL_PRESENT = "config_still_present"
EXACT_ABSENCE_VERIFIED = "exact_absence_verified"


class ConfigurationDeleteStrategy(ConfigurationStrategy):
    """The closed no-body DELETE contract for one automation or script."""

    provider = delete_contract.PROVIDER
    provider_contract = delete_contract.PROVIDER_CONTRACT
    validation_contract = "configuration_delete_intent_validation_v1"
    # Core's delete view reads only its own YAML file; an unrelated
    # configuration error neither blocks nor explains the delete.
    requires_configuration_check = False

    def __init__(self, resource_type: str) -> None:
        # Deliberately bypasses the create/update action check.
        if resource_type not in delete_contract.RESOURCE_TYPES:
            raise ValueError("configuration delete resource type is not reviewed")
        self.resource_type = resource_type
        self.action = delete_contract.ACTION

    @property
    def capability_identity(self) -> str:
        return delete_contract.CAPABILITY_IDENTITIES[self.resource_type]

    @property
    def transport(self) -> str:
        return "home_assistant_rest"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("method", "path")

    def canonical_target(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> str:
        del proposed_config
        from ..governance.resources import _validate_resource_id

        errors = _validate_resource_id(self.resource_type, target_id)
        if errors:
            raise ValueError("; ".join(errors))
        if not isinstance(target_id, str) or target_id != target_id.strip():
            raise ValueError("configuration target identity is not canonical")
        return target_id

    def validate(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        errors = tuple(
            dict.fromkeys(
                delete_contract.intent_errors(
                    self.resource_type, target_id, proposed_config
                )
            )
        )
        return not errors, errors, ()

    def normalize(
        self, config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return delete_contract.normalize_intent(config)

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return f"{self.resource_type}_configuration_delete"

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        del proposed_config
        return delete_contract.provider_payload(self.resource_type, target_id)

    def provider_descriptor(
        self,
        target_id: str,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> ConfigurationProviderDescriptor:
        del current_config
        return ConfigurationProviderDescriptor(
            provider=self.provider,
            contract_model=self.provider_contract,
            transport=self.transport,
            operation=self.provider_operation(target_id),
            argument_names=self.allowed_argument_names,
            arguments_hash=stable_hash(
                self.provider_payload(target_id, proposed_config)
            ),
        )

    def argument_names(
        self, proposed_config: dict[str, Any]
    ) -> tuple[str, ...]:
        del proposed_config
        return self.allowed_argument_names

    def dispatch_config(
        self,
        proposed_config: dict[str, Any],
        current_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # A DELETE carries no body; nothing from the plan is forwarded.
        del proposed_config, current_config
        return {}

    def read_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "permission_failure"
        return "resource_read_unavailable"

    def observe_error_code(self, exc: BaseException) -> str:
        if isinstance(exc, AuthorizationError):
            return "readback_permission_failure"
        return "readback_unavailable"

    def dispatch_error_codes(self, exc: BaseException) -> tuple[str, ...]:
        details = getattr(exc, "details", None)
        if (
            isinstance(details, dict)
            and details.get("reason") == delete_contract.DELETE_REJECTED_REASON
        ):
            return (delete_contract.DELETE_REJECTED_REASON,)
        return ()


class ConfigurationDeleteAdapter(ConfigurationOperationAdapter):
    """One action-specific ``OperationAdapter`` for a governed delete."""

    def __init__(
        self,
        resource_type: str,
        gateway: Any,
        *,
        admission: Callable[[], bool],
        metrics: ConfigurationAdapterMetrics | None = None,
        event_sink: ConfigurationEventSink | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        poll_attempts: int = delete_contract.ABSENCE_POLL_ATTEMPTS,
        poll_interval: float = delete_contract.ABSENCE_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(admission):
            raise TypeError("a delete adapter requires an admission check")
        # The base constructor resolves a create/update strategy, which a
        # delete never has; the shared state is set here instead.
        self.strategy = ConfigurationDeleteStrategy(resource_type)
        self.gateway = gateway
        self.metrics = metrics or ConfigurationAdapterMetrics()
        self.event_sink = event_sink or NullConfigurationEventSink()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._admission = admission
        self._sleep = sleep or asyncio.sleep
        self._poll_attempts = max(1, int(poll_attempts))
        self._poll_interval = max(0.0, float(poll_interval))

    def _admitted(self) -> bool:
        try:
            return self._admission() is True
        except Exception:
            return False

    @staticmethod
    def _expected_effect(action: str) -> str:
        if action == delete_contract.ACTION:
            return delete_contract.EXPECTED_EFFECT
        raise ValueError("configuration delete adapter supports delete only")

    @staticmethod
    def _intent(operation: PreparedConfigurationOperation) -> dict[str, Any]:
        return delete_contract.intent(operation.proposed_config())

    async def _live_state_code(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[dict[str, Any] | None, str]:
        """Read the live config and compare it with the bound pre-image."""

        try:
            current = await self.gateway.delete_read_config(
                operation.resource_type, operation.target.target_id
            )
        except Exception as exc:
            return None, self.strategy.read_error_code(exc)
        if current is None:
            # Already absent is never a verified delete by Engineering.
            return None, DELETE_TARGET_MISSING
        if not resource_identity_matches(
            operation.resource_type, operation.target.target_id, current
        ):
            return current, "resource_identity_mismatch"
        if self.strategy.fingerprint(current) != (
            operation.current_state_fingerprint
        ):
            return current, "stale_target_state"
        return current, "state_matches_plan"

    async def preflight(
        self,
        operation: PreparedConfigurationOperation,
        *,
        acquired_locks,
    ) -> ConfigurationPreflightResult:
        self._increment("preflight_attempts")
        required_locks, rejection = self._preflight_gates(
            operation, acquired_locks
        )
        if rejection is not None:
            return rejection
        if not self._admitted():
            return self._preflight_rejected(
                operation,
                (CORE_ADMISSION_UNAVAILABLE,),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                ),
            )
        proposed = operation.proposed_config()
        valid, _errors, _warnings = self.strategy.validate(
            operation.target.target_id, proposed
        )
        if not valid:
            self._increment("validation_failures")
            return self._preflight_rejected(
                operation, ("static_validation_failed",)
            )
        if stable_hash(self.strategy.normalize(proposed)) != (
            operation.normalized_proposed_hash
        ):
            return self._preflight_rejected(
                operation, ("proposed_hash_mismatch",)
            )
        state, state_code = await self._live_state_code(operation)
        if state_code != "state_matches_plan":
            if state_code in {
                DELETE_TARGET_MISSING,
                "stale_target_state",
                "resource_identity_mismatch",
            }:
                self._increment("stale_rejections")
            return self._preflight_rejected(
                operation,
                (state_code,),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                    if state_code
                    in {"resource_read_unavailable", "permission_failure"}
                    else NormalizedOperationOutcome.PREFLIGHT_REJECTED
                ),
            )
        intent = self._intent(operation)
        try:
            entry = await self.gateway.delete_registry_entry(
                operation.resource_type, operation.target.target_id
            )
        except Exception as exc:
            return self._preflight_rejected(
                operation,
                (self.strategy.read_error_code(exc),),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                ),
            )
        if entry is None:
            self._increment("stale_rejections")
            return self._preflight_rejected(
                operation, (REGISTRY_ENTRY_MISSING,)
            )
        if (
            entry.get("entity_id") != intent["entity_id"]
            or entry.get("id") != intent["registry_entry_id"]
        ):
            self._increment("stale_rejections")
            return self._preflight_rejected(
                operation, (REGISTRY_ENTRY_CHANGED,)
            )
        try:
            consumer_fingerprint = await self.gateway.delete_consumer_fingerprint(
                intent["entity_id"]
            )
        except AuthorizationError:
            return self._preflight_rejected(operation, ("permission_failure",))
        except Exception:
            consumer_fingerprint = None
        if consumer_fingerprint is None:
            return self._preflight_rejected(
                operation, ("dependency_evidence_unavailable",)
            )
        if consumer_fingerprint != intent["consumer_evidence_fingerprint"]:
            self._increment("stale_rejections")
            return self._preflight_rejected(
                operation, ("dependency_evidence_changed",)
            )
        provider = operation.provider_descriptor
        observed_fingerprint = self.strategy.fingerprint(state)
        evidence_hash = stable_hash(
            {
                "capability_identity": operation.capability_identity,
                "target": {
                    "target_type": operation.target.target_type,
                    "target_id": operation.target.target_id,
                },
                "observed_state_fingerprint": observed_fingerprint,
                "registry_entry_id": intent["registry_entry_id"],
                "consumer_evidence_fingerprint": consumer_fingerprint,
                "provider_arguments_hash": provider.arguments_hash,
                "lock_set_hash": lock_set_hash(required_locks),
                "configuration_check_status": "not_applicable",
            }
        )
        result = ConfigurationPreflightResult(
            eligible=True,
            outcome=None,
            confirmed_target=operation.target,
            observed_state_fingerprint=observed_fingerprint,
            provider_contract=provider.contract_model,
            provider_operation=provider.operation,
            provider_arguments_hash=provider.arguments_hash,
            evidence_hash=evidence_hash,
            diagnostic_codes=("preflight_complete",),
            mismatch_fields=(),
            capability_identity=operation.capability_identity,
            configuration_check_status="not_applicable",
            target_existence="present",
            lock_set_hash=lock_set_hash(required_locks),
        )
        self._emit(
            "preflight",
            operation.target.target_id,
            "eligible",
            result.diagnostic_codes,
        )
        return result

    async def _predispatch_refusal(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[str, ...]:
        # Admission again, then drift: the live target must still be exactly
        # the bound pre-image immediately before the DELETE is committed.
        if not self._admitted():
            return (CORE_ADMISSION_UNAVAILABLE,)
        _state, state_code = await self._live_state_code(operation)
        if state_code != "state_matches_plan":
            return (state_code,)
        return ()

    async def _send(
        self,
        operation: PreparedConfigurationOperation,
        provider_config: dict[str, Any],
    ) -> Any:
        if provider_config:
            raise ValueError("a configuration delete carries no body")
        return await self.gateway.delete_configuration(
            operation.resource_type, operation.target.target_id
        )

    async def _observe(
        self,
        operation: PreparedConfigurationOperation,
        *,
        attempt_count: int,
        error_result_sent: bool = False,
    ) -> ConfigurationObservationResult:
        del error_result_sent
        self._increment("readbacks")
        if not self._admitted():
            return self._incomplete_observation(
                operation, attempt_count, CORE_ADMISSION_UNAVAILABLE
            )
        intent = self._intent(operation)
        facts: dict[str, bool] = {}
        present_fingerprint: str | None = None
        for poll in range(self._poll_attempts):
            try:
                config = await self.gateway.delete_read_config(
                    operation.resource_type, operation.target.target_id
                )
                if config is not None:
                    present_fingerprint = self.strategy.fingerprint(config)
                    facts = {
                        "config_absent": False,
                        "registry_entry_absent": False,
                        "state_absent": False,
                    }
                    break
                entry = await self.gateway.delete_registry_entry(
                    operation.resource_type, operation.target.target_id
                )
                state_present = await self.gateway.delete_state_exists(
                    intent["entity_id"]
                )
            except Exception as exc:
                return self._incomplete_observation(
                    operation,
                    attempt_count,
                    self.strategy.observe_error_code(exc),
                )
            facts = {
                "config_absent": True,
                "registry_entry_absent": entry is None,
                "state_absent": state_present is False,
            }
            if delete_contract.absence_proven(facts):
                break
            if poll + 1 < self._poll_attempts:
                await self._sleep(self._poll_interval)
        absent = delete_contract.absence_proven(facts)
        mismatches: list[str] = []
        codes: tuple[str, ...] = ("exact_readback_completed",)
        if not facts["config_absent"]:
            mismatches.append("configuration_present")
            codes += (CONFIG_STILL_PRESENT,)
            if present_fingerprint == operation.current_state_fingerprint:
                codes += (PRIOR_STATE_OBSERVED,)
        elif not absent:
            if not facts["registry_entry_absent"]:
                mismatches.append("registry_entry_present")
            if not facts["state_absent"]:
                mismatches.append("entity_state_present")
            codes += (delete_contract.RUNTIME_UNVERIFIED,)
        resulting = (
            self.strategy.fingerprint(None) if absent else present_fingerprint
        )
        evidence = {
            "capability_identity": operation.capability_identity,
            "target_id": operation.target.target_id,
            "entity_id": intent["entity_id"],
            "facts": facts,
            "mismatch_fields": list(bounded_mismatches(mismatches)),
        }
        result = ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=not facts["config_absent"],
            readback_state_fingerprint=resulting,
            intended_result_observed=absent,
            mismatch_fields=bounded_mismatches(mismatches),
            evidence_hash=stable_hash(evidence),
            diagnostic_codes=codes,
            identity_match=True if facts["config_absent"] else None,
            resource_exists=not facts["config_absent"],
            semantic_match=absent,
            normalization_valid=True,
            configuration_check_status="not_applicable",
            normalized_observed_fingerprint=resulting,
        )
        self._emit(
            "observation",
            operation.target.target_id,
            "observing",
            result.diagnostic_codes,
        )
        return result

    def _incomplete_observation(
        self,
        operation: PreparedConfigurationOperation,
        attempt_count: int,
        code: str,
    ) -> ConfigurationObservationResult:
        self._emit(
            "observation", operation.target.target_id, "observing", (code,)
        )
        return ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=False,
            provider_reachable=(
                None if code == CORE_ADMISSION_UNAVAILABLE else False
            ),
            target_reachable=None,
            readback_state_fingerprint=None,
            intended_result_observed=None,
            mismatch_fields=(),
            evidence_hash=None,
            diagnostic_codes=(code,),
            identity_match=None,
            resource_exists=None,
            semantic_match=None,
            normalization_valid=None,
            configuration_check_status="unavailable",
            normalized_observed_fingerprint=None,
        )

    async def verify(
        self,
        operation: PreparedConfigurationOperation,
        observation: ConfigurationObservationResult,
    ) -> ConfigurationVerificationResult:
        self._require_operation(operation)
        if not observation.observation_complete:
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.OBSERVING,
                verified=None,
                codes=("readback_incomplete",),
            )
        if (
            observation.intended_result_observed is True
            and observation.resource_exists is False
            and observation.semantic_match is True
            and not observation.mismatch_fields
        ):
            self._increment("verification_successes")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                verified=True,
                codes=(EXACT_ABSENCE_VERIFIED,),
            )
        if (
            observation.resource_exists is False
            and delete_contract.RUNTIME_UNVERIFIED
            in observation.diagnostic_codes
        ):
            # The configuration is gone but Core's registry removal was not
            # observed within the bound: not success, and not "not applied".
            self._increment("manual_review_transitions")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                verified=False,
                codes=(delete_contract.RUNTIME_UNVERIFIED,),
            )
        self._increment("verification_mismatches")
        return self._verification_result(
            operation,
            observation,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            verified=False,
            codes=("exact_readback_mismatch",),
        )


__all__ = [
    "CONFIG_STILL_PRESENT",
    "CORE_ADMISSION_UNAVAILABLE",
    "ConfigurationDeleteAdapter",
    "ConfigurationDeleteStrategy",
    "DELETE_TARGET_MISSING",
    "EXACT_ABSENCE_VERIFIED",
    "REGISTRY_ENTRY_CHANGED",
    "REGISTRY_ENTRY_MISSING",
]
