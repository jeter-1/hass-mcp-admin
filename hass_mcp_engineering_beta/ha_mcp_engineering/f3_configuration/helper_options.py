"""F3 adapter for a governed options edit of one config-entry helper (HAMCP-232).

A child binds the read baseline (visible options) as its current state and a
closed options intent as its proposal.  The only write is one options-flow
step submit.

Admission is checked by the adapter itself at preflight, immediately before
durable intent and before every readback, because the Core dispatch guard is
installed only when a Core runtime exists.  Immediately before durable intent,
inside the runtime's pre-dispatch read window, the adapter opens the entry's
options flow and holds it: the step, the schema, every suggested value and
the entry's ``modified_at`` (read after the open) must still equal the plan,
or the flow is aborted and the child refuses as ``stale_target_state`` with
no submit.  The held flow is submitted exactly once; any held flow that is
not submitted is aborted, and a failed abort is disclosed as an orphan (an
in-memory flow that Core discards at its next restart).

Owner decision OD-7 accepts the residual window between that final read and
the submit: an options change made by something else inside it is
overwritten.  It is disclosed in the plan and the receipt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from ha_mcp_engineering.f3.contracts import NormalizedOperationOutcome

from ..errors import AuthorizationError
from ..governance import helper_options as contract
from ..governance.normalize import stable_hash
from ..governance.resources import ConfigurationMutationNotDispatchedError
from .adapter import (
    PREDISPATCH_READ_WINDOW,
    PRIOR_STATE_OBSERVED,
    ConfigurationOperationAdapter,
    PredispatchAuthorityRefused,
)
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
CONFIG_ENTRY_MISSING = "config_entry_missing"
CONFIG_ENTRY_NOT_LOADED = "config_entry_not_loaded"
ENTRY_ENTITIES_CHANGED = "entry_entities_changed"
FLOW_NOT_HELD = "options_flow_not_held"
FLOW_ORPHANED = "options_flow_orphaned"
OPTIONS_SCHEMA_CHANGED = "options_schema_changed"
EXACT_OPTIONS_VERIFIED = "exact_options_verified"
STALE = "stale_target_state"
_UNAVAILABLE_CODES = frozenset({"resource_read_unavailable", "permission_failure"})


class HelperOptionsStrategy(ConfigurationStrategy):
    """The closed options-flow submit contract for one config entry."""

    resource_type = contract.RESOURCE_TYPE
    provider = contract.PROVIDER
    provider_contract = contract.PROVIDER_CONTRACT
    validation_contract = "config_entry_helper_options_intent_validation_v1"
    # The options flow validates its own step; a configuration check of the
    # YAML configuration neither blocks nor explains it.
    requires_configuration_check = False

    def __init__(self) -> None:
        # Deliberately bypasses the create/update capability table.
        self.action = contract.ACTION

    @property
    def capability_identity(self) -> str:
        return contract.CAPABILITY_IDENTITY

    @property
    def transport(self) -> str:
        return "home_assistant_rest"

    @property
    def allowed_argument_names(self) -> tuple[str, ...]:
        return ("handler", "method", "route", "user_input")

    def canonical_target(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> str:
        del proposed_config
        if not isinstance(target_id, str) or not contract.ENTRY_ID_PATTERN.fullmatch(
            target_id
        ):
            raise ValueError("options target identity is not canonical")
        return target_id

    def validate(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
        errors = tuple(
            dict.fromkeys(contract.intent_errors(target_id, proposed_config))
        )
        return not errors, errors, ()

    def normalize(
        self, config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return None if config is None else contract.canonical_copy(config)

    def provider_operation(self, target_id: str) -> str:
        del target_id
        return "config_entry_options_flow_submit"

    def provider_payload(
        self, target_id: str, proposed_config: dict[str, Any]
    ) -> dict[str, Any]:
        return contract.provider_payload(
            target_id, contract.intent(proposed_config)["submit"]
        )

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
        # The submitted step dict is taken from the validated intent by the
        # adapter; nothing else from the plan is forwarded.
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
            and details.get("reason") == contract.SUBMIT_REJECTED_REASON
        ):
            category = details.get("category")
            return (
                contract.SUBMIT_REJECTED_REASON,
                *(
                    (category,)
                    if category in contract.SUBMIT_REJECTION_CATEGORIES
                    else ()
                ),
            )
        if isinstance(details, dict) and details.get("reason") == FLOW_NOT_HELD:
            return (FLOW_NOT_HELD,)
        return ()


class HelperOptionsAdapter(ConfigurationOperationAdapter):
    """One action-specific ``OperationAdapter`` for a governed options edit."""

    PREDISPATCH_READS_HOME_ASSISTANT = True

    def __init__(
        self,
        gateway: Any,
        *,
        admission: Callable[[], bool],
        metrics: ConfigurationAdapterMetrics | None = None,
        event_sink: ConfigurationEventSink | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        poll_attempts: int = contract.RELOAD_POLL_ATTEMPTS,
        poll_interval: float = contract.RELOAD_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(admission):
            raise TypeError("an options adapter requires an admission check")
        # The base constructor resolves a create/update strategy; the shared
        # state is set here instead.
        self.strategy = HelperOptionsStrategy()
        self.gateway = gateway
        self.metrics = metrics or ConfigurationAdapterMetrics()
        self.event_sink = event_sink or NullConfigurationEventSink()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._admission = admission
        self._sleep = sleep or asyncio.sleep
        self._poll_attempts = max(1, int(poll_attempts))
        self._poll_interval = max(0.0, float(poll_interval))
        # Flows opened for dispatch, keyed by prepared-operation hash.  At
        # most one per dispatch; never persisted (a flow is in-memory in Core).
        self._held: dict[str, contract.HeldForm] = {}
        self._orphaned: set[str] = set()

    def _admitted(self) -> bool:
        try:
            return self._admission() is True
        except Exception:
            return False

    @staticmethod
    def _expected_effect(action: str) -> str:
        if action == contract.ACTION:
            return contract.EXPECTED_EFFECT
        raise ValueError("options adapter supports update only")

    @staticmethod
    def _intent(operation: PreparedConfigurationOperation) -> dict[str, Any]:
        return contract.intent(operation.proposed_config())

    async def _entry_code(
        self, operation: PreparedConfigurationOperation, intent: dict[str, Any]
    ) -> str:
        """Compare the live entry facts with the plan's bound baseline."""

        try:
            facts = await self.gateway.options_entry(operation.target.target_id)
        except Exception as exc:
            return self.strategy.read_error_code(exc)
        if facts is None:
            return CONFIG_ENTRY_MISSING
        if facts.domain != intent["domain"]:
            return STALE
        if facts.state != "loaded" or facts.disabled_by is not None:
            return CONFIG_ENTRY_NOT_LOADED
        if facts.modified_at != intent["modified_at"]:
            # Core moves ``modified_at`` on every options or data change.
            return STALE
        return "state_matches_plan"

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
        intent = self._intent(operation)
        if self.strategy.fingerprint(intent["baseline"]) != (
            operation.current_state_fingerprint
        ):
            return self._preflight_rejected(
                operation, ("current_state_binding_mismatch",)
            )
        code = await self._entry_code(operation, intent)
        if code != "state_matches_plan":
            if code in {STALE, CONFIG_ENTRY_MISSING}:
                self._increment("stale_rejections")
            return self._preflight_rejected(
                operation,
                (code,),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                    if code in _UNAVAILABLE_CODES
                    else NormalizedOperationOutcome.PREFLIGHT_REJECTED
                ),
            )
        try:
            entities = await self.gateway.options_entry_entities(
                operation.target.target_id
            )
        except Exception as exc:
            return self._preflight_rejected(
                operation,
                (self.strategy.read_error_code(exc),),
                outcome=(
                    NormalizedOperationOutcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH
                ),
            )
        if list(entities) != intent["entity_ids"]:
            self._increment("stale_rejections")
            return self._preflight_rejected(
                operation, (ENTRY_ENTITIES_CHANGED,)
            )
        try:
            consumer_fingerprint = await self.gateway.options_consumer_fingerprint(
                intent["entity_ids"]
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
        # An unchanged ``modified_at`` proves the bound baseline is current;
        # the visible options are re-read from the held flow before intent.
        observed_fingerprint = operation.current_state_fingerprint
        evidence_hash = stable_hash(
            {
                "capability_identity": operation.capability_identity,
                "target": {
                    "target_type": operation.target.target_type,
                    "target_id": operation.target.target_id,
                },
                "observed_state_fingerprint": observed_fingerprint,
                "modified_at": intent["modified_at"],
                "entity_ids": list(intent["entity_ids"]),
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

    # --- dispatch ----------------------------------------------------------

    async def _abort(self, flow_id: str) -> bool:
        try:
            return await self.gateway.options_abort(flow_id) is True
        except Exception:  # noqa: BLE001 - an orphan is disclosed, never fatal
            return False

    async def _windowed_abort(self, flow_id: str) -> bool:
        """Abort a held flow outside the read window in its own short window.

        After the pre-dispatch read window has closed and before (or without)
        the submit, the client admits no call: the release abort gets its
        own Core-authority window, like the read that opened the flow.
        """

        window = PREDISPATCH_READ_WINDOW.get()
        if window is None:
            return await self._abort(flow_id)
        try:
            async with window():
                return await self._abort(flow_id)
        except PredispatchAuthorityRefused:
            return False

    async def _release(self, key: str, *, windowed: bool = False) -> None:
        held = self._held.pop(key, None)
        if held is None:
            return
        aborted = (
            await self._windowed_abort(held.flow_id)
            if windowed
            else await self._abort(held.flow_id)
        )
        if not aborted:
            self._orphaned.add(key)

    async def dispatch(self, operation, preflight, *, before_dispatch):
        key = operation.prepared_operation_hash

        async def record_intent() -> None:
            try:
                await before_dispatch()
            except BaseException:
                # Nothing was submitted; the held flow is released in its
                # own Core-authority window.  A refused or failed abort is
                # disclosed as an orphan.
                await self._release(key, windowed=True)
                raise

        try:
            return await super().dispatch(
                operation, preflight, before_dispatch=record_intent
            )
        finally:
            # Safety net for any path that left a flow held.
            await self._release(key, windowed=True)
            self._orphaned.discard(key)

    def _dispatch_result(self, operation, outcome, *, codes, **kwargs):
        if operation.prepared_operation_hash in self._orphaned:
            codes = (*codes, FLOW_ORPHANED)
        return super()._dispatch_result(operation, outcome, codes=codes, **kwargs)

    async def _predispatch_refusal(
        self, operation: PreparedConfigurationOperation
    ) -> tuple[str, ...]:
        if not self._admitted():
            return (CORE_ADMISSION_UNAVAILABLE,)
        intent = self._intent(operation)
        key = operation.prepared_operation_hash
        await self._release(key)
        try:
            held = await self.gateway.options_open_held(
                operation.target.target_id,
                domain=intent["domain"],
                expected_step_id=intent["step_id"],
            )
        except contract.HeldFlowRefused as exc:
            return (
                STALE if exc.drift else "resource_read_unavailable",
                *((FLOW_ORPHANED,) if exc.orphaned else ()),
            )
        except Exception as exc:
            return (self.strategy.read_error_code(exc),)

        async def refuse(code: str) -> tuple[str, ...]:
            aborted = await self._abort(held.flow_id)
            return (code, *(() if aborted else (FLOW_ORPHANED,)))

        if held.step_id != intent["step_id"] or not contract.visible_equal(
            held.visible, intent["baseline"]
        ):
            return await refuse(STALE)
        # ``modified_at`` read after the open: unchanged proves the held
        # flow's snapshot is the baseline this plan binds.
        code = await self._entry_code(operation, intent)
        if code != "state_matches_plan":
            return await refuse(code)
        self._held[key] = held
        return ()

    async def _send(
        self,
        operation: PreparedConfigurationOperation,
        provider_config: dict[str, Any],
    ) -> Any:
        if provider_config:
            raise ValueError("an options submit carries only the intent's step")
        held = self._held.pop(operation.prepared_operation_hash, None)
        if held is None:
            raise ConfigurationMutationNotDispatchedError(
                "options flow is not held",
                details={"reason": FLOW_NOT_HELD},
                call_not_sent=True,
            )
        try:
            return await self.gateway.options_submit(
                held.flow_id, self._intent(operation)["submit"]
            )
        except ConfigurationMutationNotDispatchedError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            if details.get("flow_open") is True and not await self._abort(
                held.flow_id
            ):
                self._orphaned.add(operation.prepared_operation_hash)
            raise

    # --- readback ----------------------------------------------------------

    async def observe(self, operation, dispatch):
        self._require_operation(operation)
        rejected = "provider_rejected_mutation" in tuple(
            getattr(dispatch, "diagnostic_codes", ()) or ()
        )
        # A confirmed rejection never moved the entry; one read settles it.
        return await self._observe(
            operation, attempt_count=1, poll_attempts=1 if rejected else None
        )

    async def _observe(
        self,
        operation: PreparedConfigurationOperation,
        *,
        attempt_count: int,
        error_result_sent: bool = False,
        poll_attempts: int | None = None,
    ) -> ConfigurationObservationResult:
        del error_result_sent
        self._increment("readbacks")
        if not self._admitted():
            return self._incomplete_observation(
                operation, attempt_count, CORE_ADMISSION_UNAVAILABLE
            )
        intent = self._intent(operation)
        entry_id = operation.target.target_id
        attempts = self._poll_attempts if poll_attempts is None else poll_attempts
        facts = None
        for poll in range(max(1, attempts)):
            try:
                facts = await self.gateway.options_entry(entry_id)
            except Exception as exc:
                return self._incomplete_observation(
                    operation, attempt_count, self.strategy.observe_error_code(exc)
                )
            if facts is None:
                break
            # Core stores the options (moving ``modified_at``) before it
            # schedules the reload; wait for the reload to settle.
            if facts.modified_at != intent["modified_at"] and facts.state == "loaded":
                break
            if poll + 1 < attempts:
                await self._sleep(self._poll_interval)
        if facts is None or facts.domain != intent["domain"]:
            return self._observation(
                operation,
                attempt_count,
                visible=None,
                modified_at=None,
                intended=False,
                exists=False,
                mismatches=["config_entry"],
                codes=("exact_readback_completed", CONFIG_ENTRY_MISSING),
                facts={"entry_present": False},
            )
        try:
            _step_id, visible, aborted = await self.gateway.options_read_visible(
                entry_id, domain=intent["domain"], expected_step_id=intent["step_id"]
            )
        except contract.HelperOptionsContractError:
            return self._observation(
                operation,
                attempt_count,
                visible=None,
                modified_at=facts.modified_at,
                intended=False,
                exists=True,
                mismatches=["options_schema"],
                codes=("exact_readback_completed", OPTIONS_SCHEMA_CHANGED),
                facts={"entry_present": True, "schema_recognized": False},
            )
        except Exception as exc:
            return self._incomplete_observation(
                operation, attempt_count, self.strategy.observe_error_code(exc)
            )
        try:
            entities = await self.gateway.options_entry_entities(entry_id)
        except Exception as exc:
            return self._incomplete_observation(
                operation, attempt_count, self.strategy.observe_error_code(exc)
            )
        moved = facts.modified_at != intent["modified_at"]
        loaded = facts.state == "loaded" and facts.disabled_by is None
        registered = set(intent["entity_ids"]) <= set(entities)
        proposed = contract.visible_equal(visible, intent["proposed"])
        baseline = contract.visible_equal(visible, intent["baseline"])
        codes: tuple[str, ...] = ("exact_readback_completed",)
        if not aborted:
            codes += (FLOW_ORPHANED,)
        mismatches: list[str] = []
        if proposed and moved and loaded and registered:
            intended = True
        elif proposed and moved:
            intended = False
            codes += (contract.ENTRY_UNHEALTHY,)
            if not loaded:
                mismatches.append("config_entry_state")
            if not registered:
                mismatches.append("entry_entities")
        else:
            intended = False
            mismatches.append("options")
            if baseline and not moved:
                codes += (PRIOR_STATE_OBSERVED,)
        return self._observation(
            operation,
            attempt_count,
            visible=visible,
            modified_at=facts.modified_at,
            intended=intended,
            exists=True,
            mismatches=mismatches,
            codes=codes,
            facts={
                "entry_present": True,
                "schema_recognized": True,
                "modified_at_moved": moved,
                "entry_loaded": loaded,
                "entities_registered": registered,
                "proposed_observed": proposed,
                "baseline_observed": baseline,
            },
        )

    def _observation(
        self,
        operation: PreparedConfigurationOperation,
        attempt_count: int,
        *,
        visible: dict[str, Any] | None,
        modified_at: float | None,
        intended: bool,
        exists: bool,
        mismatches: list[str],
        codes: tuple[str, ...],
        facts: dict[str, bool],
    ) -> ConfigurationObservationResult:
        # The resulting fingerprint binds the visible options and the entry's
        # ``modified_at``; a separately approved rollback refuses unless both
        # are still exactly what this readback verified.
        resulting = (
            contract.applied_state_fingerprint(visible, modified_at)
            if visible is not None and modified_at is not None
            else None
        )
        evidence = {
            "capability_identity": operation.capability_identity,
            "target_id": operation.target.target_id,
            "facts": facts,
            "resulting_state_fingerprint": resulting,
            "mismatch_fields": list(bounded_mismatches(mismatches)),
        }
        result = ConfigurationObservationResult(
            outcome=NormalizedOperationOutcome.OBSERVING,
            attempt_count=attempt_count,
            observation_complete=True,
            provider_reachable=True,
            target_reachable=exists,
            readback_state_fingerprint=resulting,
            intended_result_observed=intended,
            mismatch_fields=bounded_mismatches(mismatches),
            evidence_hash=stable_hash(evidence),
            diagnostic_codes=codes,
            identity_match=exists,
            resource_exists=exists,
            semantic_match=intended,
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
            and observation.resource_exists is True
            and observation.semantic_match is True
            and not observation.mismatch_fields
        ):
            self._increment("verification_successes")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.SUCCEEDED_VERIFIED,
                verified=True,
                codes=(EXACT_OPTIONS_VERIFIED,),
            )
        if contract.ENTRY_UNHEALTHY in observation.diagnostic_codes:
            # The options were stored but the entry did not come back loaded
            # with its entities: not success, and not "not applied".
            self._increment("manual_review_transitions")
            return self._verification_result(
                operation,
                observation,
                NormalizedOperationOutcome.MANUAL_REVIEW_REQUIRED,
                verified=False,
                codes=(contract.ENTRY_UNHEALTHY,),
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
    "CONFIG_ENTRY_MISSING",
    "CONFIG_ENTRY_NOT_LOADED",
    "CORE_ADMISSION_UNAVAILABLE",
    "ENTRY_ENTITIES_CHANGED",
    "EXACT_OPTIONS_VERIFIED",
    "FLOW_NOT_HELD",
    "FLOW_ORPHANED",
    "HelperOptionsAdapter",
    "HelperOptionsStrategy",
    "OPTIONS_SCHEMA_CHANGED",
]
