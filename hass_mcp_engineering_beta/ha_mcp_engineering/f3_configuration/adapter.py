"""F3-conformant, runtime-inert configuration operation adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from ha_mcp_engineering.f3.contracts import (
    DispatchIntentRecorder,
    F3_ADAPTER_CONTRACT_MODEL,
    NormalizedOperationOutcome,
    OperationTarget,
    RecoveryContext,
)
from ha_mcp_engineering.f3.locks import (
    normalize_lock_requests as normalize_durable_lock_requests,
)

from ..errors import EngineeringServerError
from ..governance.config_validation import normalize_configuration_validation
from ..governance.entity_metadata import EntityMetadataUpdateErrorResult
from ..governance.normalize import (
    AUTOMATION_NORMALIZATION_VERSION,
    stable_hash,
)
from ..governance.resources import (
    RESOURCE_NORMALIZATION_VERSION,
    ConfigurationMutationCompletedUnexpectedlyError,
    ConfigurationMutationNotDispatchedError,
    compare_resource_verification,
    resource_identity_matches,
)
from .locks import lock_set_hash, normalize_lock_requests, operation_lock_requests
from .models import (
    CONFIGURATION_VERIFICATION_CONTRACT_MODEL,
    ConfigurationDispatchResult,
    ConfigurationObservationResult,
    ConfigurationOperationProposal,
    ConfigurationPreflightResult,
    ConfigurationVerificationResult,
    PreparedConfigurationOperation,
    bounded_codes,
    bounded_mismatches,
)
from .observability import (
    ConfigurationAdapterEvent,
    ConfigurationAdapterMetrics,
    ConfigurationEventSink,
    NullConfigurationEventSink,
)
from .strategies import ConfigurationStrategy, strategy_for


# OD-15 (HAMCP-183): a sent update answered with a non-authorization error
# result.  Readback then settles it: the planned state is verified success, the
# exact prior state is verified not applied, and any other state needs manual
# review because neither the plan nor the absence of a change explains it.
ERROR_RESULT_AFTER_SEND = "provider_error_after_send"
PRIOR_STATE_OBSERVED = "prior_state_observed"
ERROR_RESULT_READBACK_UNEXPLAINED = "readback_matches_neither_plan_nor_prior"


class ConfigurationGateway(Protocol):
    provider_admitted: bool

    async def read(
        self, resource_type: str, target_id: str
    ) -> dict[str, Any] | None:
        ...

    async def validate_all(self) -> Any:
        ...

    async def create_target_absent(
        self, resource_type: str, target_id: str
    ) -> tuple[bool, str]:
        ...

    async def write(
        self,
        action: str,
        resource_type: str,
        target_id: str,
        proposed_config: dict[str, Any],
    ) -> Any:
        ...


class ConfigurationOperationAdapter:
    """One action-specific implementation of ``OperationAdapter``.

    Durable task claims, atomic durable lock acquisition, fencing validation,
    and intent persistence remain owned by F3-A.  This adapter calculates the
    complete required lock set and calls the supplied durable-intent callback
    immediately before its sole fixed gateway write.
    """

    def __init__(
        self,
        resource_type: str,
        action: str,
        gateway: ConfigurationGateway,
        *,
        metrics: ConfigurationAdapterMetrics | None = None,
        event_sink: ConfigurationEventSink | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.strategy: ConfigurationStrategy = strategy_for(
            resource_type, action
        )
        self.gateway = gateway
        self.metrics = metrics or ConfigurationAdapterMetrics()
        self.event_sink = event_sink or NullConfigurationEventSink()
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def capabilities(self):
        return self.strategy.capabilities

    async def prepare(
        self, proposal: ConfigurationOperationProposal
    ) -> PreparedConfigurationOperation:
        self._increment("preparations")
        self._validate_proposal_identity(proposal)
        proposed = proposal.proposed_config()
        current = proposal.current_config()
        target_id = self.strategy.canonical_target(
            proposal.target_id, proposed
        )
        valid, errors, _warnings = self.strategy.validate(
            target_id, proposed
        )
        if not valid:
            self._emit(
                "planning",
                target_id,
                "preflight_rejected",
                ("static_validation_failed",),
            )
            raise ValueError("configuration proposal failed existing validation")

        normalized_proposed = self.strategy.normalize(proposed)
        if normalized_proposed is None:
            raise ValueError("proposed configuration cannot normalize to absence")
        if stable_hash(normalized_proposed) != proposal.proposed_config_hash:
            raise ValueError("proposed configuration hash is inconsistent")
        if self.strategy.fingerprint(current) != proposal.current_state_fingerprint:
            raise ValueError("current-state fingerprint is inconsistent")
        if proposal.rollback_available:
            raise ValueError("forward configuration rollback is unavailable")

        provider = self.strategy.provider_descriptor(
            target_id, proposed, current
        )
        verification_payload = {
            "model": CONFIGURATION_VERIFICATION_CONTRACT_MODEL,
            "capability_identity": self.strategy.capability_identity,
            "resource_type": self.strategy.resource_type,
            "action": self.strategy.action,
            "target_id": target_id,
            "expected_proposed_hash": proposal.proposed_config_hash,
            "identity_required": True,
            "full_configuration_check_required": True,
        }
        verification_hash = stable_hash(verification_payload)
        expected_effect = self._expected_effect(self.strategy.action)
        prepared_payload = {
            "contract_model": F3_ADAPTER_CONTRACT_MODEL,
            "adapter_id": "configuration_operation",
            "capability_identity": self.strategy.capability_identity,
            "plan_id": proposal.plan_id,
            "plan_hash": proposal.plan_hash,
            "plan_contract_version": proposal.plan_contract_version,
            "task_id": proposal.task_id,
            "operation_id": proposal.operation_id,
            "order": proposal.order,
            "depends_on": list(proposal.depends_on),
            "resource_type": self.strategy.resource_type,
            "action": self.strategy.action,
            "target_id": target_id,
            "current_configuration_json": proposal.current_configuration_json,
            "proposed_configuration_json": proposal.proposed_configuration_json,
            "current_state_fingerprint": proposal.current_state_fingerprint,
            "proposed_config_hash": proposal.proposed_config_hash,
            "provider": {
                "provider": provider.provider,
                "contract_model": provider.contract_model,
                "transport": provider.transport,
                "operation": provider.operation,
                "argument_names": list(provider.argument_names),
                "arguments_hash": provider.arguments_hash,
            },
            "normalization_version": proposal.normalization_version,
            "risk_level": proposal.risk_level,
            "risk_evidence_hash": proposal.risk_evidence_hash,
            "policy_class": proposal.policy_class,
            "policy_decision_hash": proposal.policy_decision_hash,
            "approval_bundle_hash": proposal.approval_bundle_hash,
            "plan_expires_at": proposal.plan_expires_at,
            "policy_snapshot_valid": proposal.policy_snapshot_valid,
            "provider_admitted": proposal.provider_admitted,
            "expected_effects": [expected_effect],
            "verification_contract_hash": verification_hash,
            "rollback_available": proposal.rollback_available,
        }
        prepared = PreparedConfigurationOperation(
            contract_model=F3_ADAPTER_CONTRACT_MODEL,
            adapter_id="configuration_operation",
            operation=self.strategy.capability_identity,
            target=OperationTarget(
                target_type=self.strategy.resource_type,
                target_id=target_id,
            ),
            current_state_fingerprint=proposal.current_state_fingerprint,
            normalized_proposed_hash=proposal.proposed_config_hash,
            prepared_operation_hash=stable_hash(prepared_payload),
            risk_level=proposal.risk_level,
            policy_decision_hash=proposal.policy_decision_hash,
            approval_bundle_hash=proposal.approval_bundle_hash,
            expected_effects=(expected_effect,),
            verification_contract_model=(
                CONFIGURATION_VERIFICATION_CONTRACT_MODEL
            ),
            verification_contract_hash=verification_hash,
            rollback_available=proposal.rollback_available,
            capability_identity=self.strategy.capability_identity,
            plan_id=proposal.plan_id,
            plan_hash=proposal.plan_hash,
            plan_contract_version=proposal.plan_contract_version,
            task_id=proposal.task_id,
            operation_id=proposal.operation_id,
            order=proposal.order,
            depends_on=proposal.depends_on,
            resource_type=self.strategy.resource_type,
            action=self.strategy.action,
            current_configuration_json=proposal.current_configuration_json,
            proposed_configuration_json=proposal.proposed_configuration_json,
            provider_descriptor=provider,
            normalization_version=proposal.normalization_version,
            risk_evidence_hash=proposal.risk_evidence_hash,
            policy_class=proposal.policy_class,
            plan_expires_at=proposal.plan_expires_at,
            policy_snapshot_valid=proposal.policy_snapshot_valid,
            provider_admitted=proposal.provider_admitted,
        )
        self._emit("planning", target_id, "prepared", ())
        return prepared

    def lock_requests(
        self, operation: PreparedConfigurationOperation
    ):
        self._require_operation(operation)
        return operation_lock_requests(operation)

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

        validation_status = await self._configuration_check_status()
        if validation_status != self._expected_configuration_check_status:
            self._increment("validation_failures")
            return self._preflight_rejected(
                operation,
                (
                    "configuration_validation_unavailable"
                    if validation_status == "unavailable"
                    else "configuration_validation_failed"
                ),
            )
        # Prove the candidate can be compared before any provider mutation.
        # Structural validation alone does not establish that the stricter
        # readback verifier supports every action in an automation. This also
        # protects retained plans; it does not alter their binding hashes.
        comparison = compare_resource_verification(
            operation.resource_type, proposed, proposed
        )
        if not comparison.normalization_valid or not comparison.semantic_match:
            self._increment("validation_failures")
            return self._preflight_rejected(
                operation, ("configuration_not_verifiable",)
            )

        # This is the final authoritative mutable-state decision.  The shared
        # executor consumes approval only after this preflight returns and
        # before it commits durable intent through ``before_dispatch``.
        state, state_code = await self._authoritative_state(operation)
        if state_code == "desired_state_already_reached":
            return self._preflight_already_reached(
                operation, state, required_locks, validation_status
            )
        if state_code == "state_matches_plan":
            evidence_codes = await self.strategy.preflight_evidence(
                self.gateway,
                operation.target.target_id,
                operation.current_config(),
                proposed,
            )
            if evidence_codes:
                if {
                    "dependency_evidence_changed",
                    "stale_reference_definition",
                    "label_namespace_changed",
                    "stale_target_state",
                } & set(evidence_codes):
                    self._increment("stale_rejections")
                return self._preflight_rejected(operation, evidence_codes)
        if state_code != "state_matches_plan":
            if state_code in {
                "target_already_exists",
                "target_entity_id_reserved",
                "update_target_missing",
            }:
                self._increment("absence_existence_rejections")
            if state_code in {
                "stale_target_state",
                "resource_identity_mismatch",
            }:
                self._increment("stale_rejections")
            outcome = (
                NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                if state_code == "resource_read_unavailable"
                else NormalizedOperationOutcome.PREFLIGHT_REJECTED
            )
            return self._preflight_rejected(
                operation, (state_code,), outcome=outcome
            )
        provider = operation.provider_descriptor
        evidence_hash = stable_hash(
            {
                "capability_identity": operation.capability_identity,
                "target": {
                    "target_type": operation.target.target_type,
                    "target_id": operation.target.target_id,
                },
                "observed_state_fingerprint": self.strategy.fingerprint(state),
                "provider_arguments_hash": provider.arguments_hash,
                "lock_set_hash": lock_set_hash(required_locks),
                "configuration_check_status": validation_status,
            }
        )
        result = ConfigurationPreflightResult(
            eligible=True,
            outcome=None,
            confirmed_target=operation.target,
            observed_state_fingerprint=self.strategy.fingerprint(state),
            provider_contract=provider.contract_model,
            provider_operation=provider.operation,
            provider_arguments_hash=provider.arguments_hash,
            evidence_hash=evidence_hash,
            diagnostic_codes=("preflight_complete",),
            mismatch_fields=(),
            capability_identity=operation.capability_identity,
            configuration_check_status=validation_status,
            target_existence="absent" if state is None else "present",
            lock_set_hash=lock_set_hash(required_locks),
        )
        self._emit(
            "preflight",
            operation.target.target_id,
            "eligible",
            result.diagnostic_codes,
        )
        return result

    def _preflight_gates(
        self,
        operation: PreparedConfigurationOperation,
        acquired_locks,
    ) -> tuple[Any, ConfigurationPreflightResult | None]:
        """Shared lock, policy, expiry and provider-admission gates."""

        try:
            self._require_operation(operation)
        except ValueError:
            return (), self._preflight_rejected(
                operation, ("prepared_operation_invalid",)
            )
        required_locks = operation_lock_requests(operation)
        try:
            normalized_acquired = normalize_durable_lock_requests(
                acquired_locks
            )
            normalized_required = normalize_durable_lock_requests(
                required_locks
            )
        except (TypeError, ValueError):
            self._increment("lock_conflicts")
            return (), self._preflight_rejected(
                operation,
                ("lock_set_invalid",),
                outcome=NormalizedOperationOutcome.LOCK_CONFLICT,
            )
        if normalized_acquired != normalized_required:
            self._increment("lock_conflicts")
            return (), self._preflight_rejected(
                operation,
                ("complete_lock_set_not_held",),
                outcome=NormalizedOperationOutcome.LOCK_CONFLICT,
            )
        if not operation.policy_snapshot_valid:
            return (), self._preflight_rejected(
                operation, ("policy_snapshot_mismatch",)
            )
        if self._expired(operation.plan_expires_at):
            return (), self._preflight_rejected(
                operation, ("plan_expired",)
            )
        if not (
            operation.provider_admitted
            and getattr(self.gateway, "provider_admitted", False) is True
        ):
            return (), self._preflight_rejected(
                operation,
                ("provider_not_admitted",),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                ),
            )
        return required_locks, None

    async def dispatch(
        self,
        operation: PreparedConfigurationOperation,
        preflight: ConfigurationPreflightResult,
        *,
        before_dispatch: DispatchIntentRecorder,
    ) -> ConfigurationDispatchResult:
        self._require_operation(operation)
        if (
            not preflight.eligible
            or preflight.confirmed_target != operation.target
            or preflight.provider_arguments_hash
            != operation.provider_descriptor.arguments_hash
        ):
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                intent=False,
                invocation_count=0,
                may_have_dispatched=False,
                response_received=False,
                codes=("dispatch_without_valid_preflight",),
                provider_mutation_count=0,
            )

        refusal = await self._predispatch_refusal(operation)
        if refusal:
            if any(
                code in {"stale_target_state", "delete_target_missing"}
                for code in refusal
            ):
                self._increment("stale_rejections")
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                intent=False,
                invocation_count=0,
                may_have_dispatched=False,
                response_received=False,
                codes=refusal,
                provider_mutation_count=0,
            )
        proposed_config = operation.proposed_config()
        provider_config = self.strategy.dispatch_config(
            proposed_config, operation.current_config()
        )
        try:
            await before_dispatch()
        except Exception:
            self._increment("intent_failures")
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.FAILED_PRE_DISPATCH,
                intent=False,
                invocation_count=0,
                may_have_dispatched=False,
                response_received=False,
                codes=("dispatch_intent_persistence_failed",),
                provider_mutation_count=0,
            )

        try:
            await self._send(operation, provider_config)
        except ConfigurationMutationNotDispatchedError as exc:
            self._increment("intents_committed")
            self._increment("dispatch_attempts")
            response_received = bool(
                isinstance(exc.details, dict)
                and exc.details.get("provider_response_received") is True
            )
            self._increment(
                "responses_received"
                if response_received
                else "responses_lost"
            )
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED,
                intent=True,
                invocation_count=1,
                may_have_dispatched=True,
                response_received=response_received,
                codes=(
                    "provider_rejected_mutation"
                    if response_received
                    else "provider_confirmed_no_mutation",
                    *self.strategy.dispatch_error_codes(exc),
                ),
                provider_mutation_count=0,
            )
        except ConfigurationMutationCompletedUnexpectedlyError:
            self._increment("intents_committed")
            self._increment("dispatch_attempts")
            self._increment("responses_received")
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
                intent=True,
                invocation_count=1,
                may_have_dispatched=True,
                response_received=True,
                codes=("provider_completed_unexpectedly",),
                provider_mutation_count=1,
            )
        except EntityMetadataUpdateErrorResult:
            # OD-15 (HAMCP-183): the sent registry update was answered with an
            # error result that is not an authorization refusal.  That is not
            # a non-mutation proof, so the dispatch is recorded as uncertain
            # (never confirmed, never a zero mutation count) and the shared
            # observe/verify readback decides; ``provider_error_after_send``
            # keeps the received error visible next to whatever readback finds.
            self._increment("intents_committed")
            self._increment("dispatch_attempts")
            self._increment("responses_received")
            return self._dispatch_result(
                operation,
                NormalizedOperationOutcome.DISPATCH_INDETERMINATE,
                intent=True,
                invocation_count=1,
                may_have_dispatched=True,
                response_received=True,
                codes=(
                    ERROR_RESULT_AFTER_SEND,
                    "provider_response_indeterminate",
                ),
                provider_mutation_count=None,
            )
        except Exception as exc:
            self._increment("intents_committed")
            self._increment("dispatch_attempts")
            response_received = bool(
                isinstance(exc, EngineeringServerError)
                and exc.details.get("provider_response_received") is True
            )
            confirmed_no_mutation = bool(
                getattr(exc, "mutation_dispatched", None) is False
            )
            self._increment(
                "responses_received" if response_received else "responses_lost"
            )
            return self._dispatch_result(
                operation,
                (
                    NormalizedOperationOutcome.DISPATCH_FAILED_CONFIRMED
                    if confirmed_no_mutation
                    else NormalizedOperationOutcome.DISPATCH_INDETERMINATE
                ),
                intent=True,
                invocation_count=1,
                may_have_dispatched=True,
                response_received=response_received,
                codes=(
                    "provider_confirmed_no_mutation"
                    if confirmed_no_mutation
                    else "provider_response_indeterminate",
                ),
                provider_mutation_count=(0 if confirmed_no_mutation else None),
            )

        self._increment("intents_committed")
        self._increment("dispatch_attempts")
        self._increment("responses_received")
        return self._dispatch_result(
            operation,
            NormalizedOperationOutcome.OBSERVING,
            intent=True,
            invocation_count=1,
            may_have_dispatched=True,
            response_received=True,
            codes=("provider_response_received",),
            provider_mutation_count=None,
        )

    async def observe(
        self,
        operation: PreparedConfigurationOperation,
        dispatch: ConfigurationDispatchResult | None,
    ) -> ConfigurationObservationResult:
        self._require_operation(operation)
        return await self._observe(
            operation,
            attempt_count=1,
            error_result_sent=ERROR_RESULT_AFTER_SEND
            in tuple(getattr(dispatch, "diagnostic_codes", ()) or ()),
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
        if observation.configuration_check_status == "unavailable":
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.OBSERVING,
                verified=None,
                codes=("configuration_validation_unavailable",),
            )
        exact = bool(
            observation.identity_match
            and observation.semantic_match
            and observation.normalization_valid
            and observation.configuration_check_status
            == self._expected_configuration_check_status
        )
        if exact:
            self._increment("verification_successes")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                verified=True,
                codes=("exact_readback_verified",),
            )
        if (
            ERROR_RESULT_AFTER_SEND in observation.diagnostic_codes
            and PRIOR_STATE_OBSERVED not in observation.diagnostic_codes
        ):
            # The error result left the target in neither the planned nor the
            # prior state; do not report it as an ordinary not-applied failure.
            self._increment("manual_review_transitions")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                verified=False,
                codes=(ERROR_RESULT_READBACK_UNEXPLAINED,),
            )
        self._increment("verification_mismatches")
        return self._verification_result(
            operation,
            observation,
            NormalizedOperationOutcome.VERIFICATION_MISMATCH,
            verified=False,
            codes=("exact_readback_mismatch",),
        )

    async def recover(
        self,
        operation: PreparedConfigurationOperation,
        *,
        context: RecoveryContext,
    ) -> ConfigurationObservationResult:
        self._require_operation(operation)
        self._increment("recovery_attempts")
        self._increment("blind_redispatch_preventions")
        if not context.dispatch_intent_recorded:
            self._increment("manual_review_transitions")
            return ConfigurationObservationResult(
                outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                attempt_count=max(1, context.prior_observation_attempts + 1),
                observation_complete=False,
                provider_reachable=None,
                target_reachable=None,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                mismatch_fields=(),
                evidence_hash=None,
                diagnostic_codes=("recovery_context_contradictory",),
            )
        if self._deadline_expired(context.post_dispatch_deadline):
            self._increment("manual_review_transitions")
            return ConfigurationObservationResult(
                outcome=NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                attempt_count=max(1, context.prior_observation_attempts + 1),
                observation_complete=False,
                provider_reachable=None,
                target_reachable=None,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                mismatch_fields=(),
                evidence_hash=None,
                diagnostic_codes=("post_dispatch_evidence_deadline_expired",),
            )
        return await self._observe(
            operation,
            attempt_count=max(1, context.prior_observation_attempts + 1),
            error_result_sent=ERROR_RESULT_AFTER_SEND
            in tuple(getattr(context, "dispatch_diagnostic_codes", ()) or ()),
        )

    async def prepare_rollback(
        self,
        operation: PreparedConfigurationOperation,
        *,
        expected_current_fingerprint: str,
    ) -> PreparedConfigurationOperation | None:
        """Fail closed until a distinct rollback task/approval is supplied."""

        self._require_operation(operation)
        del expected_current_fingerprint
        return None

    async def _authoritative_state(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            current = await self.gateway.read(
                operation.resource_type, operation.target.target_id
            )
            if operation.action == "create":
                if current is not None:
                    return current, "target_already_exists"
                available, reason = await self.gateway.create_target_absent(
                    operation.resource_type, operation.target.target_id
                )
                if not available:
                    return current, reason
            elif current is None:
                return None, "update_target_missing"
            if current is not None and not resource_identity_matches(
                operation.resource_type,
                operation.target.target_id,
                current,
            ):
                return current, "resource_identity_mismatch"
            if self.strategy.fingerprint(current) != (
                operation.current_state_fingerprint
            ):
                if self.strategy.already_reached(
                    current, operation.proposed_config()
                ):
                    return current, "desired_state_already_reached"
                return current, "stale_target_state"
            return current, "state_matches_plan"
        except Exception as exc:
            return None, self.strategy.read_error_code(exc)

    @property
    def _expected_configuration_check_status(self) -> str:
        return (
            "valid"
            if self.strategy.requires_configuration_check
            else "not_applicable"
        )

    async def _configuration_check_status(self) -> str:
        if not self.strategy.requires_configuration_check:
            return "not_applicable"
        try:
            result = await self.gateway.validate_all()
        except Exception:
            return "unavailable"
        status, _details = normalize_configuration_validation(result)
        return status

    async def _observe(
        self,
        operation: PreparedConfigurationOperation,
        *,
        attempt_count: int,
        error_result_sent: bool = False,
    ) -> ConfigurationObservationResult:
        self._increment("readbacks")
        try:
            observed = await self.gateway.read(
                operation.resource_type, operation.target.target_id
            )
        except Exception as exc:
            readback_code = self.strategy.observe_error_code(exc)
            self._emit(
                "observation",
                operation.target.target_id,
                "observing",
                (readback_code,),
            )
            return ConfigurationObservationResult(
                outcome=NormalizedOperationOutcome.OBSERVING,
                attempt_count=attempt_count,
                observation_complete=False,
                provider_reachable=False,
                target_reachable=None,
                readback_state_fingerprint=None,
                intended_result_observed=None,
                mismatch_fields=(),
                evidence_hash=None,
                diagnostic_codes=(readback_code,),
                identity_match=None,
                resource_exists=None,
                semantic_match=None,
                normalization_valid=None,
                configuration_check_status="unavailable",
                normalized_observed_fingerprint=None,
            )
        comparison = compare_resource_verification(
            operation.resource_type,
            operation.proposed_config(),
            observed,
        )
        identity_match = resource_identity_matches(
            operation.resource_type,
            operation.target.target_id,
            observed,
        )
        config_status = await self._configuration_check_status()
        mismatches = list(comparison.mismatch_categories)
        if not identity_match:
            mismatches.append("resource_identity")
        if config_status == "failed":
            mismatches.append("configuration_check")
        evidence = {
            "identity_match": identity_match,
            "resource_exists": observed is not None,
            "semantic_match": comparison.semantic_match,
            "normalization_valid": comparison.normalization_valid,
            "binding_observed_fingerprint": (
                comparison.binding_observed_fingerprint
            ),
            "normalized_observed_fingerprint": (
                comparison.normalized_observed_fingerprint
            ),
            "configuration_check_status": config_status,
            "mismatch_fields": list(bounded_mismatches(mismatches)),
        }
        codes: tuple[str, ...] = ("exact_readback_completed",)
        # HAMCP-069B: a tolerated new exposure key is always reported, in the
        # closed code alphabet (``tolerated_new_exposure_key_cloud_alexa``).
        codes += tuple(
            category.replace(":", "_").replace(".", "_")
            for category in comparison.canonicalization_categories
            if category.startswith("tolerated_new_exposure_key:")
        )
        if error_result_sent:
            codes += (ERROR_RESULT_AFTER_SEND,)
            if self.strategy.fingerprint(observed) == (
                operation.current_state_fingerprint
            ):
                codes += (PRIOR_STATE_OBSERVED,)
        result = ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=observed is not None,
            readback_state_fingerprint=(
                comparison.binding_observed_fingerprint
            ),
            intended_result_observed=bool(
                identity_match and comparison.semantic_match
            ),
            mismatch_fields=bounded_mismatches(mismatches),
            evidence_hash=stable_hash(evidence),
            diagnostic_codes=codes,
            identity_match=identity_match,
            resource_exists=observed is not None,
            semantic_match=comparison.semantic_match,
            normalization_valid=comparison.normalization_valid,
            configuration_check_status=config_status,
            normalized_observed_fingerprint=(
                comparison.normalized_observed_fingerprint
            ),
        )
        self._emit(
            "observation",
            operation.target.target_id,
            "observing",
            result.diagnostic_codes,
        )
        return result

    def _verification_result(
        self,
        operation: PreparedConfigurationOperation,
        observation: ConfigurationObservationResult,
        outcome: NormalizedOperationOutcome,
        *,
        verified: bool | None,
        codes: tuple[str, ...],
    ) -> ConfigurationVerificationResult:
        mismatches = bounded_mismatches(observation.mismatch_fields)
        result = ConfigurationVerificationResult(
            outcome=outcome,
            attempt_count=observation.attempt_count,
            verified=verified,
            resulting_state_fingerprint=(
                observation.readback_state_fingerprint
            ),
            mismatch_fields=mismatches,
            evidence_hash=observation.evidence_hash,
            manual_review_reason_code=(
                codes[0]
                if outcome == NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED
                else None
            ),
            configuration_check_status=(
                observation.configuration_check_status
            ),
            identity_match=observation.identity_match,
        )
        self._emit(
            "verification",
            operation.target.target_id,
            outcome.value,
            codes,
        )
        return result

    def _preflight_already_reached(
        self,
        operation: PreparedConfigurationOperation,
        state: dict[str, Any] | None,
        required_locks,
        validation_status: str,
    ) -> ConfigurationPreflightResult:
        """Verified success with zero dispatch; approval is not consumed.

        Only a strategy that proves exact final-state equality reaches this
        path.  The executor finishes the child as ``succeeded_verified``
        without committing dispatch intent.
        """

        provider = operation.provider_descriptor
        codes = bounded_codes(("desired_state_already_reached",))
        observed_fingerprint = self.strategy.fingerprint(state)
        result = ConfigurationPreflightResult(
            eligible=False,
            outcome=NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
            confirmed_target=operation.target,
            observed_state_fingerprint=observed_fingerprint,
            provider_contract=provider.contract_model,
            provider_operation=provider.operation,
            provider_arguments_hash=provider.arguments_hash,
            evidence_hash=stable_hash(
                {
                    "capability_identity": operation.capability_identity,
                    "target": {
                        "target_type": operation.target.target_type,
                        "target_id": operation.target.target_id,
                    },
                    "observed_state_fingerprint": observed_fingerprint,
                    "expected_proposed_hash": (
                        operation.normalized_proposed_hash
                    ),
                    "lock_set_hash": lock_set_hash(required_locks),
                    "diagnostic_codes": list(codes),
                }
            ),
            diagnostic_codes=codes,
            mismatch_fields=(),
            capability_identity=operation.capability_identity,
            configuration_check_status=validation_status,
            target_existence="present",
            lock_set_hash=lock_set_hash(required_locks),
        )
        self._emit(
            "preflight",
            operation.target.target_id,
            NormalizedOperationOutcome.SUCCEEDED_VERIFIED.value,
            codes,
        )
        return result

    def _preflight_rejected(
        self,
        operation: PreparedConfigurationOperation,
        codes: tuple[str, ...],
        *,
        outcome: NormalizedOperationOutcome = (
            NormalizedOperationOutcome.PREFLIGHT_REJECTED
        ),
    ) -> ConfigurationPreflightResult:
        safe_codes = bounded_codes(codes)
        self._emit(
            "preflight",
            operation.target.target_id,
            outcome.value,
            safe_codes,
        )
        return ConfigurationPreflightResult(
            eligible=False,
            outcome=outcome,
            confirmed_target=None,
            observed_state_fingerprint=None,
            provider_contract=None,
            provider_operation=None,
            provider_arguments_hash=None,
            evidence_hash=stable_hash(
                {
                    "capability_identity": operation.capability_identity,
                    "outcome": outcome.value,
                    "diagnostic_codes": list(safe_codes),
                }
            ),
            diagnostic_codes=safe_codes,
            mismatch_fields=(),
            capability_identity=operation.capability_identity,
        )

    def _dispatch_result(
        self,
        operation: PreparedConfigurationOperation,
        outcome: NormalizedOperationOutcome,
        *,
        intent: bool,
        invocation_count: int,
        may_have_dispatched: bool,
        response_received: bool,
        codes: tuple[str, ...],
        provider_mutation_count: int | None,
    ) -> ConfigurationDispatchResult:
        safe_codes = bounded_codes(codes)
        result = ConfigurationDispatchResult(
            outcome=outcome,
            dispatch_intent_recorded=intent,
            mutating_invocation_count=invocation_count,
            may_have_dispatched=may_have_dispatched,
            provider_response_received=response_received,
            provider_operation_id=None,
            response_evidence_hash=stable_hash(
                {
                    "outcome": outcome.value,
                    "intent": intent,
                    "invocation_count": invocation_count,
                    "may_have_dispatched": may_have_dispatched,
                    "response_received": response_received,
                    "diagnostic_codes": list(safe_codes),
                }
            ),
            diagnostic_codes=safe_codes,
            adapter_dispatch_count=invocation_count,
            provider_mutation_count=provider_mutation_count,
        )
        self._emit(
            "dispatch",
            operation.target.target_id,
            outcome.value,
            safe_codes,
        )
        return result

    def _validate_proposal_identity(
        self, proposal: ConfigurationOperationProposal
    ) -> None:
        if proposal.resource_type != self.strategy.resource_type:
            raise ValueError("proposal resource type does not match adapter")
        if proposal.action != self.strategy.action:
            raise ValueError("proposal action does not match adapter")
        for name, value in (
            ("plan_id", proposal.plan_id),
            ("task_id", proposal.task_id),
            ("operation_id", proposal.operation_id),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or value != value.strip()
                or any(
                    character
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
                    for character in value
                )
            ):
                raise ValueError(f"{name} is invalid")
        for name, value in (
            ("plan_hash", proposal.plan_hash),
            ("current_state_fingerprint", proposal.current_state_fingerprint),
            ("proposed_config_hash", proposal.proposed_config_hash),
            ("risk_evidence_hash", proposal.risk_evidence_hash),
            ("policy_decision_hash", proposal.policy_decision_hash),
            ("approval_bundle_hash", proposal.approval_bundle_hash),
        ):
            self._require_hash(name, value)
        if proposal.plan_contract_version not in {1, 2}:
            raise ValueError("configuration plan contract is unsupported")
        if proposal.risk_level not in {"low", "medium", "high"}:
            raise ValueError("configuration risk level is unsupported")
        if proposal.policy_class not in {
            "standard_admin",
            "elevated_admin",
            "prohibited",
        }:
            raise ValueError("configuration policy class is unsupported")
        if proposal.normalization_version not in {
            1,
            2,
            AUTOMATION_NORMALIZATION_VERSION,
            RESOURCE_NORMALIZATION_VERSION,
        }:
            raise ValueError("configuration normalization version is unsupported")
        if not 0 <= proposal.order <= 7:
            raise ValueError("configuration operation order is invalid")
        if (
            len(proposal.depends_on) > 7
            or len(proposal.depends_on) != len(set(proposal.depends_on))
        ):
            raise ValueError("configuration dependencies exceed the plan bound")
        for dependency in proposal.depends_on:
            if (
                not isinstance(dependency, str)
                or not 1 <= len(dependency) <= 128
                or dependency != dependency.strip()
                or any(
                    character
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
                    for character in dependency
                )
            ):
                raise ValueError("configuration dependency identity is invalid")
        self._parse_timestamp(proposal.plan_expires_at)

    def _require_operation(
        self, operation: PreparedConfigurationOperation
    ) -> None:
        if (
            operation.contract_model != F3_ADAPTER_CONTRACT_MODEL
            or operation.adapter_id != "configuration_operation"
            or operation.capability_identity
            != self.strategy.capability_identity
            or operation.resource_type != self.strategy.resource_type
            or operation.action != self.strategy.action
            or operation.operation != self.strategy.capability_identity
            or operation.target.target_type != self.strategy.resource_type
            or operation.verification_contract_model
            != CONFIGURATION_VERIFICATION_CONTRACT_MODEL
            or operation.expected_effects
            != (self._expected_effect(operation.action),)
        ):
            raise ValueError("prepared operation does not match adapter")
        self._require_hash(
            "prepared_operation_hash", operation.prepared_operation_hash
        )
        self._require_hash(
            "verification_contract_hash",
            operation.verification_contract_hash,
        )
        self._require_hash(
            "provider_arguments_hash",
            operation.provider_descriptor.arguments_hash,
        )
        proposed = operation.proposed_config()
        current = operation.current_config()
        canonical_target = self.strategy.canonical_target(
            operation.target.target_id, proposed
        )
        if canonical_target != operation.target.target_id:
            raise ValueError("prepared target is not canonical")
        valid, _errors, _warnings = self.strategy.validate(
            canonical_target, proposed
        )
        if not valid:
            raise ValueError("prepared configuration is invalid")
        normalized = self.strategy.normalize(proposed)
        if normalized is None or stable_hash(normalized) != (
            operation.normalized_proposed_hash
        ):
            raise ValueError("prepared proposed hash is inconsistent")
        if self.strategy.fingerprint(current) != (
            operation.current_state_fingerprint
        ):
            raise ValueError("prepared current fingerprint is inconsistent")
        if operation.rollback_available:
            raise ValueError("prepared forward rollback must be unavailable")
        provider = self.strategy.provider_descriptor(
            canonical_target, proposed, current
        )
        if provider != operation.provider_descriptor:
            raise ValueError("prepared provider descriptor is inconsistent")
        verification_hash = stable_hash(
            {
                "model": CONFIGURATION_VERIFICATION_CONTRACT_MODEL,
                "capability_identity": operation.capability_identity,
                "resource_type": operation.resource_type,
                "action": operation.action,
                "target_id": canonical_target,
                "expected_proposed_hash": operation.normalized_proposed_hash,
                "identity_required": True,
                "full_configuration_check_required": True,
            }
        )
        if verification_hash != operation.verification_contract_hash:
            raise ValueError("prepared verification contract is inconsistent")
        expected_effect = self._expected_effect(operation.action)
        prepared_payload = {
            "contract_model": operation.contract_model,
            "adapter_id": operation.adapter_id,
            "capability_identity": operation.capability_identity,
            "plan_id": operation.plan_id,
            "plan_hash": operation.plan_hash,
            "plan_contract_version": operation.plan_contract_version,
            "task_id": operation.task_id,
            "operation_id": operation.operation_id,
            "order": operation.order,
            "depends_on": list(operation.depends_on),
            "resource_type": operation.resource_type,
            "action": operation.action,
            "target_id": canonical_target,
            "current_configuration_json": operation.current_configuration_json,
            "proposed_configuration_json": operation.proposed_configuration_json,
            "current_state_fingerprint": operation.current_state_fingerprint,
            "proposed_config_hash": operation.normalized_proposed_hash,
            "provider": {
                "provider": provider.provider,
                "contract_model": provider.contract_model,
                "transport": provider.transport,
                "operation": provider.operation,
                "argument_names": list(provider.argument_names),
                "arguments_hash": provider.arguments_hash,
            },
            "normalization_version": operation.normalization_version,
            "risk_level": operation.risk_level,
            "risk_evidence_hash": operation.risk_evidence_hash,
            "policy_class": operation.policy_class,
            "policy_decision_hash": operation.policy_decision_hash,
            "approval_bundle_hash": operation.approval_bundle_hash,
            "plan_expires_at": operation.plan_expires_at,
            "policy_snapshot_valid": operation.policy_snapshot_valid,
            "provider_admitted": operation.provider_admitted,
            "expected_effects": [expected_effect],
            "verification_contract_hash": verification_hash,
            "rollback_available": operation.rollback_available,
        }
        if stable_hash(prepared_payload) != operation.prepared_operation_hash:
            raise ValueError("prepared operation hash is inconsistent")

    @staticmethod
    def _expected_effect(action: str) -> str:
        """Name the one effect of a reviewed action; never default one.

        HAMCP-155: an action other than create or update must not inherit
        the update effect.  The delete adapter overrides this explicitly.
        """

        if action == "create":
            return "configuration_resource_created"
        if action == "update":
            return "configuration_resource_updated"
        raise ValueError("configuration action has no reviewed effect")

    async def _predispatch_refusal(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[str, ...]:
        """Final checks before durable intent; any code refuses with no send."""

        del operation
        return ()

    async def _send(
        self,
        operation: PreparedConfigurationOperation,
        provider_config: dict[str, Any],
    ) -> Any:
        return await self.gateway.write(
            operation.action,
            operation.resource_type,
            operation.target.target_id,
            provider_config,
        )

    @staticmethod
    def _require_hash(name: str, value: str) -> None:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{name} must be a lower-case SHA-256 digest")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    def _expired(self, value: str) -> bool:
        return self.now().astimezone(timezone.utc) >= self._parse_timestamp(value)

    def _deadline_expired(self, value: str | None) -> bool:
        return value is not None and self._expired(value)

    def _increment(self, metric: str) -> None:
        self.metrics.increment(
            metric,
            resource_type=self.strategy.resource_type,
            action=self.strategy.action,
        )

    def _emit(
        self,
        phase: str,
        target_id: str,
        outcome: str,
        codes: tuple[str, ...],
    ) -> None:
        self.event_sink.emit(
            ConfigurationAdapterEvent(
                phase=phase,
                capability_identity=self.strategy.capability_identity,
                resource_type=self.strategy.resource_type,
                action=self.strategy.action,
                target_identity_hash=stable_hash(
                    {
                        "resource_type": self.strategy.resource_type,
                        "target_id": target_id,
                    }
                ),
                outcome=outcome,
                diagnostic_codes=bounded_codes(codes),
            )
        )
