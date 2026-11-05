"""Helper value receipts, safety warning and authority; shared ownership and recovery.

Helper value changes are ordinary runtime control (HAMCP-311): no plan and no
approval panel. Before the declaration, existing dependency evidence may require
a chat confirmation; that is model-conveyed, never external approval.
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
from .safety import CONFIRMATION, assess, read_helper_dependency_evidence

_GATE = contextvars.ContextVar("helper_value_gate", default=None)
SAFETY_GATE_VALUES = frozenset({
    "not_evaluated_value_already_current", "not_safety_critical", "evidence_unavailable",
    "confirmation_required", "acknowledged",
})
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
        if gate == "acknowledged":
            # The user's confirmation reached Engineering only through the model.
            safe["safety_confirmation"] = CONFIRMATION
            safe["external_approval"] = False
    return safe


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
        if safety["safety_critical"] and not request.acknowledge_safety_critical:
            raise _ConfirmationRequired({
                "status": "confirmation_required",
                "entity_id": request.entity_id, "action": request.action,
                "value": _public_value(request, prepared.baseline),
                "operation_id": request.operation_id,
                "dispatch": "no_dispatch", "task_created": False,
                "consequential_automations": safety["consequential_automations"],
                "consequence": safety["consequence"],
                "consumers": {key: safety[key] for key in (
                    "known_consumer_count", "known_consumers", "template_reader_count", "template_readers")},
                "next_step": (
                    "Tell the user which automations have safety-critical consequences and ask "
                    "whether to proceed. Only if they confirm, call control_helper again with the "
                    "same arguments and acknowledge_safety_critical=true (a new operation_id if "
                    "this one has expired). This is a chat confirmation, not external approval."),
                "authorization": "authenticated_connector", "provider": PROVIDER, "fallback": "none",
            })
        notes["safety"] = {key: value for key, value in safety.items()
                           if key != "safety_critical"} | {"evaluated": True,
                                                           "safety_critical": safety["safety_critical"]}
        if safety["evidence"] == "unavailable":
            notes["gate"] = "evidence_unavailable"
        elif safety["safety_critical"]:
            notes["gate"] = "acknowledged"
            notes["safety"]["confirmation"] = CONFIRMATION
            notes["safety"]["external_approval"] = False
        else:
            notes["gate"] = "not_safety_critical"

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
            return gate.payload
        finally:
            _GATE.reset(token)
        if telemetry and notes.get("gate"):
            telemetry.audit_context["helper_value_safety_gate"] = notes["gate"]
        result = dict(result)
        result["safety"] = notes.get("safety") or {
            "evaluated": False, "reason": "reconciliation_of_existing_task"}
        return result

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
