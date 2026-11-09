"""Helper value receipts, safety warning and authority; shared ownership and recovery.

Helper value changes are ordinary runtime control (HAMCP-311): no plan and no
approval panel. Before the declaration, existing dependency evidence may require
a chat confirmation; that is model-conveyed, never external approval. When that
evidence cannot be established the change is never dispatched blind (HAMCP-327):
the first call asks instead.
"""
from __future__ import annotations

import contextvars

from ..fan.audit import FanExecutionRepository, request_summary as _request_summary
from ..fan.authority import FanCoreAuthority
from ..fan.contracts import CORE_REQUIREMENTS
from ..fan.service import FanService
from ..ha_core_readmission.typed_operations import operation_profile
from ..providers.upstream_helper_value import HelperProvider
from ..request_context import current_telemetry
from .adapter import EFFECTS, HelperAdapter, prepare_record
from .contracts import FAMILY, PROVIDER, HelperRefusal, HelperRequest, desired, digest, verification_basis
from .safety import (CONFIRMATION, CONFIRMATION_EVIDENCE_UNAVAILABLE, CONFIRMATION_REASONS,
                     CONFIRMATION_SAFETY_CRITICAL, assess, read_helper_dependency_evidence)

_GATE = contextvars.ContextVar("helper_value_gate", default=None)
SAFETY_GATE_VALUES = frozenset({
    "not_evaluated_value_already_current", "not_safety_critical",
    "confirmation_required", "acknowledged", "acknowledged_evidence_unavailable",
})
_ACKNOWLEDGED = frozenset({"acknowledged", "acknowledged_evidence_unavailable"})
OUTCOMES = frozenset({"confirmation_required"})
REDACTED = "redacted_password_mode"


class _ConfirmationRequired(Exception):
    def __init__(self, payload):
        super().__init__("helper_safety_confirmation_required")
        self.payload = payload


class HelperCoreAuthority(FanCoreAuthority):
    family = FAMILY
    legacy_contracts = ()
    trigger = "helper_value_authority"
    refusal = HelperRefusal

    async def acquire(self, prepared=None, preflight=None):
        # Signed-only: the base class falls back to the legacy compiled Core
        # version when no signed profile is available. This family has no
        # legacy contract, so it stays unavailable until a signed registry
        # sequence names core.typed_helper_value_operation.
        binding = getattr(prepared, "core_binding", None)
        if binding is None:
            await self.runtime.reconcile_once(self.trigger)
            requirements = CORE_REQUIREMENTS + (operation_profile(self.family).capability_id,)
            if not self.runtime.route_status(requirements)["available"]:
                return None
            return self.runtime.acquire(requirements, expected_core_version=None,
                                        target=getattr(prepared, "target", None))
        return await super().acquire(prepared, preflight)


class HelperExecutionRepository(FanExecutionRepository):
    kind = "helper_value"
    provider_name = PROVIDER


def request_summary(telemetry):
    safe = _request_summary(telemetry, kind="helper_value", provider=PROVIDER)
    context = telemetry.audit_context
    outcome = context.get("helper_value_outcome")
    if isinstance(outcome, str) and outcome in OUTCOMES:
        safe["helper_value_outcome"] = outcome
    gate = context.get("helper_value_safety_gate")
    if isinstance(gate, str) and gate in SAFETY_GATE_VALUES:
        safe["safety_gate"] = gate
        if gate in _ACKNOWLEDGED:
            # The user's confirmation reached Engineering only through the model.
            safe["safety_confirmation"] = CONFIRMATION
            safe["external_approval"] = False
    reason = context.get("helper_value_confirmation_reason")
    if isinstance(reason, str) and reason in CONFIRMATION_REASONS:
        safe["safety_confirmation_reason"] = reason
    return safe


_CONSUMER_KEYS = ("known_consumer_count", "known_consumers", "template_reader_count", "template_readers")
# Gate fields: a disclosure-only result evaluated no confirmation gate.
_GATE_KEYS = frozenset({"safety_critical", "confirmation_reason"})
DISCLOSURE = "informational_read_after_result"


def _confirmation_payload(request, baseline, safety):
    """The no-dispatch confirmation result; nothing is saved or sent."""
    reason = safety["confirmation_reason"]
    payload = {
        "status": "confirmation_required",
        "reason": reason,
        "entity_id": request.entity_id, "action": request.action,
        "value": _public_value(request, baseline),
        "operation_id": request.operation_id,
        "dispatch": "no_dispatch", "task_created": False,
    }
    if reason == CONFIRMATION_SAFETY_CRITICAL:
        payload["consequential_automations"] = safety["consequential_automations"]
        ask = ("Tell the user which automations have safety-critical consequences and ask "
               "whether to proceed.")
    else:
        payload["evidence"] = "unavailable"
        payload["failure_category"] = safety["failure_category"]
        ask = ("Tell the user that this helper's automation consumers could not be checked, so a "
               "safety-critical automation cannot be ruled out, and ask whether to proceed "
               "without that check.")
    payload["consequence"] = safety["consequence"]
    payload["consumers"] = {key: safety[key] for key in _CONSUMER_KEYS}
    payload["coverage_complete"] = safety["coverage_complete"]
    payload["next_step"] = (
        ask + " Only if they confirm, call control_helper again with the same arguments and "
        "acknowledge_safety_critical=true (a new operation_id if this one has expired). This "
        "is a chat confirmation, not external approval.")
    payload.update({"authorization": "authenticated_connector", "provider": PROVIDER,
                    "fallback": "none"})
    return payload


def _public_value(request, baseline):
    attributes = (baseline or {}).get("attributes") or {}
    if request.domain == "input_text" and attributes.get("mode") == "password":
        return REDACTED
    return request.value


def status_of(receipt):
    """Contract status from durable facts only (docs/TYPED_HELPER_CONTROL.md)."""
    outcome = receipt.get("terminal_outcome")
    dispatched = receipt.get("dispatch_intent_recorded") is True
    if receipt.get("terminal") is not True:
        return "changed_unverified" if dispatched else "in_progress"
    if outcome == "succeeded_verified":
        return "succeeded_verified" if dispatched else "no_change"
    if not dispatched:
        return "provider_unavailable" if outcome == "provider_unavailable_pre_dispatch" else "refused"
    if outcome == "dispatch_failed_confirmed" or receipt.get("provider_dispatch_occurred") is False:
        return "provider_unavailable"
    if receipt.get("provider_response_received") is True:
        return "verification_failed"
    return "changed_unverified"


class HelperService(FanService):
    namespace = "ordinary-helper-value-v1"
    request_type = HelperRequest
    adapter_type = HelperAdapter
    repository_type = HelperExecutionRepository
    contracts = ()  # no unsigned declarations exist to recover
    prepare_record = staticmethod(prepare_record)
    provider_name = PROVIDER
    binding_attribute = "ordinary_helper_value_binding"
    outcome_key = "helper_value_outcome"
    refusal = HelperRefusal

    def __init__(self, root, provider, core, *, dependency_reader=read_helper_dependency_evidence, **kwargs):
        super().__init__(root, provider, core, **kwargs)
        self.dependency_reader = dependency_reader

    @staticmethod
    def receipt_parameters(request):
        return {"domain": request.domain, "value": request.value,
                "acknowledge_safety_critical": request.acknowledge_safety_critical,
                "verification_basis": verification_basis(request),
                "consequences": list(EFFECTS)}

    def receipt(self, task_id):
        value = super().receipt(task_id)
        if value is None:
            return None
        prepared = self.load(task_id)
        value["value"] = _public_value(prepared.request, prepared.baseline)
        value["status"] = status_of(value)
        return value

    async def before_declaration(self, prepared, check):
        notes = _GATE.get()
        notes = notes if notes is not None else {}
        request = prepared.request
        if desired(request, prepared.baseline):
            # No dispatch can follow: preflight requires this same baseline.
            notes["safety"] = {"evaluated": False, "reason": "value_already_current"}
            notes["gate"] = "not_evaluated_value_already_current"
            return
        try:
            safety = assess(await self.dependency_reader(request.entity_id))
        except Exception as exc:
            safety = assess({"available": False, "failure_category": type(exc).__name__[:64]})
        check()
        reason = safety.get("confirmation_reason")
        notes["confirmation_reason"] = reason if reason in CONFIRMATION_REASONS else None
        if reason is not None and not request.acknowledge_safety_critical:
            raise _ConfirmationRequired(_confirmation_payload(request, prepared.baseline, safety))
        notes["safety"] = {key: value for key, value in safety.items()
                           if key != "safety_critical"} | {"evaluated": True,
                                                           "safety_critical": safety["safety_critical"]}
        if reason == CONFIRMATION_EVIDENCE_UNAVAILABLE:
            notes["gate"] = "acknowledged_evidence_unavailable"
        elif reason == CONFIRMATION_SAFETY_CRITICAL:
            notes["gate"] = "acknowledged"
        else:
            notes["gate"] = "not_safety_critical"
        if notes["gate"] in _ACKNOWLEDGED:
            notes["safety"]["confirmation"] = CONFIRMATION
            notes["safety"]["external_approval"] = False

    async def control(self, request):
        telemetry = current_telemetry()
        notes = {}
        token = _GATE.set(notes)
        try:
            result = await super().control(request)
        except _ConfirmationRequired as gate:
            if telemetry:
                telemetry.audit_context.pop("task_id", None)
                telemetry.audit_context[self.outcome_key] = "confirmation_required"
                telemetry.audit_context["helper_value_safety_gate"] = "confirmation_required"
                telemetry.audit_context["helper_value_confirmation_reason"] = gate.payload["reason"]
            return gate.payload
        finally:
            _GATE.reset(token)
        if telemetry and notes.get("gate"):
            telemetry.audit_context["helper_value_safety_gate"] = notes["gate"]
            if notes.get("confirmation_reason") and notes["gate"] in _ACKNOWLEDGED:
                telemetry.audit_context["helper_value_confirmation_reason"] = notes["confirmation_reason"]
        result = dict(result)
        safety = notes.get("safety")
        if safety is None or safety.get("evaluated") is not True:
            # HAMCP-327 R1-M1: a no-op or a reconciliation evaluates no gate,
            # but still discloses the known consumers. Read after the result,
            # so it can neither ask nor change what was (not) dispatched.
            reason = safety["reason"] if safety else "reconciliation_of_existing_task"
            safety = await self._disclosure(request.entity_id, reason)
        result["safety"] = safety
        return result

    async def _disclosure(self, entity_id, reason):
        """Bounded consumer information for a result that evaluated no gate.

        Read-only and informational: it never raises, never asks and never
        dispatches. Unavailable evidence is reported as unknown (null fields).
        """
        try:
            safety = assess(await self.dependency_reader(entity_id))
        except Exception as exc:
            safety = assess({"available": False, "failure_category": type(exc).__name__[:64]})
        return {"evaluated": False, "reason": reason, "disclosure": DISCLOSURE} | {
            key: value for key, value in safety.items() if key not in _GATE_KEYS}

    async def _fresh_readback(self, prepared):
        inner = self.adapter.inner
        try:
            state = await inner.core.read(
                lambda check: inner.provider.state(prepared.request.entity_id, check,
                                                   contract=prepared.provider_contract), prepared,
            )
            matches = desired(prepared.request, state, prepared.baseline) is True
        except Exception:
            raise self.refusal("fan_hold_release_readback_unavailable") from None
        return state, matches, digest(state)

    @staticmethod
    def _state_comparison(request, state):
        if request.domain == "input_text":
            return [("state", "not_disclosed", "not_disclosed")]
        expected = request.value if request.key[1] in {"select_option", "set_value", "set_datetime"} else None
        if request.domain == "input_boolean":
            expected = "on" if request.action == "turn_on" else "off"
        if expected is not None and not isinstance(expected, str):
            expected = str(expected)
        return [("state", expected or verification_basis(request), state["state"])]


class HelperRuntime:
    service = None

    def require(self):
        if self.service is None:
            raise HelperRefusal("helper_runtime_unavailable")
        return self.service

    def configure(self, settings, core_runtime, read_gateway, *, audit=None):
        self.service = HelperService(settings.governance_path,
                                     HelperProvider.configured(settings, read_gateway),
                                     HelperCoreAuthority(core_runtime), audit=audit)


HELPER_VALUE_OPERATIONS = HelperRuntime()
