"""One device-configuration change through the shipped F3 ownership machinery.

Composite target proof (§5.5, §5.7) at preparation, in the locked preflight and
in ``once()`` immediately before the single send; the shipped at-most-once
outcome mapping (§5.8); lenient composite readback (§5.9).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import SimpleNamespace

from ..f3.contracts import (
    CALLBACK_FAILED_NOT_SENT_DECLARATION, PROVIDER_CALL_NOT_SENT,
    AdapterCapabilityDescriptor, DispatchResult, F3_ADAPTER_CONTRACT_MODEL,
    LockMode, LockRequest, LockScope, NormalizedOperationOutcome as Outcome,
    ObservationResult, OperationTarget, PreflightResult, VerificationResult,
)
from ..fan.adapter import FanAdapter, PreparedFan
from ..ha_core_readmission.profiles import CORE_DEVICE_CONFIGURATION_PROFILES
from .contracts import (
    ACTIONS, ADAPTER_ID, PROVIDER_CONTRACT, STATE_CHANGED, DeviceConfigurationRefusal,
    DeviceConfigurationRequest, Refusal, baseline_value, check_enabled_and_kind, check_value,
    checked_device, checked_entry, checked_state, digest, forward_value, identity, matches,
    observation, verified,
)

PROFILE = CORE_DEVICE_CONFIGURATION_PROFILES[0]
EFFECTS = ("device_configuration_changes", "automation_reactions_possible",
           "consumer_coverage_incomplete")
VERIFICATION_MODEL = "device-configuration-exact-composite-v1"
AUTHORITY_REFUSALS = frozenset({
    "device_configuration_core_authority_unavailable",
    "device_configuration_core_authority_retired",
})
# D1: once() refusals that keep their code to the caller (FAILED_PRE_DISPATCH).
PRE_SEND_REFUSALS = AUTHORITY_REFUSALS | {STATE_CHANGED}
BASELINE_KEYS = frozenset({"observation", "display", "restore", "target_value", "undo_of"})
_RESTORE_REFUSALS = frozenset({
    "device_configuration_value_not_offered", "device_configuration_number_out_of_range",
    "device_configuration_number_step_misaligned", "device_configuration_invalid_value",
})
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})")


def category(exc) -> str | None:
    if isinstance(exc, DeviceConfigurationRefusal):
        details = exc.details if isinstance(exc.details, dict) else {}
        value = details.get("category")
        return value if isinstance(value, str) else None
    return None


def checked_binding(value) -> dict:
    """The persisted signed Core identity; the explicit profile, never a generator."""
    if (not isinstance(value, dict) or set(value) != {"version", "contract_fingerprint"}
            or not isinstance(value["version"], str)
            or _VERSION.fullmatch(value["version"]) is None
            or value["contract_fingerprint"] != PROFILE.contract_fingerprint):
        raise ValueError("typed_core_binding_invalid")
    return dict(value)


@dataclass(frozen=True)
class PreparedDeviceConfiguration(PreparedFan):
    request: DeviceConfigurationRequest


def arguments(request, baseline) -> dict:
    return {"entity_id": request.entity_id, "action": request.action,
            "value": baseline["target_value"]}


def _checked_baseline(request, baseline) -> dict:
    if not isinstance(baseline, dict) or set(baseline) != BASELINE_KEYS:
        raise Refusal("device_configuration_receipt_corrupt")
    observed = baseline["observation"]
    if not isinstance(observed, dict) or observed.get("entity_id") != request.entity_id:
        raise Refusal("device_configuration_receipt_corrupt")
    undo = baseline["undo_of"]
    if request.is_undo:
        if (not isinstance(undo, dict) or set(undo) != {"task_id", "prepared_hash"}
                or undo["task_id"] != request.undo_task_id
                or not isinstance(baseline["target_value"], str)):
            raise Refusal("device_configuration_receipt_corrupt")
    elif undo is not None or baseline["target_value"] != forward_value(request):
        raise Refusal("device_configuration_receipt_corrupt")
    return baseline


def prepare_record(request, baseline, contract=None, core_binding=None):
    request = DeviceConfigurationRequest.model_validate(request.model_dump()).checked()
    if core_binding is None:
        # Signed-only: no legacy Core version or unsigned contract exists.
        raise Refusal("device_configuration_core_authority_unavailable")
    core_binding = checked_binding(core_binding)
    if contract != PROVIDER_CONTRACT:
        raise Refusal("device_configuration_contract_unknown")
    baseline = _checked_baseline(request, baseline)
    material = {"request": request.model_dump(), "baseline": baseline, "contract": contract,
                "core_binding": core_binding}
    return PreparedDeviceConfiguration(
        F3_ADAPTER_CONTRACT_MODEL, ADAPTER_ID, request.action,
        OperationTarget(request.domain, request.entity_id),
        # §5.7: the fingerprint is the composite observation alone.
        digest(baseline["observation"]),
        digest(arguments(request, baseline)), digest(material), "physical_action",
        digest({"device_configuration_contract": contract, "core_binding": core_binding}),
        digest({"kind": "authenticated_connector", "request": request.model_dump()}),
        EFFECTS, VERIFICATION_MODEL,
        digest({"desired": request.model_dump(), "target_value": baseline["target_value"]}),
        False, request, baseline, contract, core_binding,
    )


def _no_undo_source(request):
    raise Refusal("device_configuration_undo_unavailable")


class DeviceConfigurationAdapter(FanAdapter):
    capabilities = AdapterCapabilityDescriptor(
        ADAPTER_ID, F3_ADAPTER_CONTRACT_MODEL, "ordinary_device_configuration",
        ACTIONS, False, True, True,
    )
    prepare_record = staticmethod(prepare_record)
    refusal = DeviceConfigurationRefusal
    target_reason = "exact_device_configuration_target"
    mismatch_field = "device_configuration_observation"

    def __init__(self, provider, core):
        super().__init__(provider, core)
        # Wired by the service: the original receipt and sidecar of an undo.
        self.undo_source = _no_undo_source
        # Wired by the service: durable sidecar of a verified forward change.
        self.record_verified = None
        # Unit-test hook only (§8 success tests): additional target kinds. The
        # production runtime never sets it; the signed allowlist is ALLOWLIST.
        self.extra_kinds = ()

    @staticmethod
    def diagnostic(code):
        return "device_configuration_" + code.removeprefix("fan_")

    @staticmethod
    def domain(request):
        return request.domain

    async def _read(self, entity_id, check):
        """The composite projection: registry proof first, state last (§5.5)."""
        domain = entity_id.split(".", 1)[0]
        registry = checked_entry(entity_id, await self.provider.registry_entry(entity_id))
        check()
        device, device_name = checked_device(registry, await self.provider.devices())
        check()
        check_enabled_and_kind(domain, registry, device, self.extra_kinds)
        state, entity_name = checked_state(entity_id, await self.provider.state(entity_id),
                                           registry)
        check()
        return observation(state, registry, device), {"device_name": device_name,
                                                      "entity_name": entity_name}

    def _baseline(self, request, observed, display, undo):
        domain = request.domain
        if undo is None:
            value = forward_value(request)
            check_value(domain, value, observed)
            undo_of = None
        else:
            if identity(observed) != undo["identity"]:
                raise Refusal("device_configuration_undo_identity_changed")
            if digest(observed) != undo["post_fingerprint"]:
                raise Refusal("device_configuration_undo_post_state_unproven")
            value = undo["restore"]
            try:
                check_value(domain, value, observed)
            except DeviceConfigurationRefusal as exc:
                if category(exc) in _RESTORE_REFUSALS:
                    raise Refusal("device_configuration_baseline_not_restorable") from None
                raise
            undo_of = {"task_id": undo["task_id"], "prepared_hash": undo["prepared_hash"]}
        return {"observation": observed, "display": display, "restore": baseline_value(observed),
                "target_value": value, "undo_of": undo_of}

    async def prepare(self, request, *, contract=None, core_binding=None):
        async def read(check):
            # Eligibility is local (no Home Assistant I/O) and follows authority.
            undo = self.undo_source(request) if request.is_undo else None
            observed, display = await self._read(request.entity_id, check)
            return observed, display, undo, check.core_binding
        observed, display, undo, binding = await self.core.read(
            read, SimpleNamespace(target=OperationTarget(request.domain, request.entity_id),
                                  provider_contract=contract, core_binding=core_binding),
        )
        return self.prepare_record(request, self._baseline(request, observed, display, undo),
                                   PROVIDER_CONTRACT, binding)

    @staticmethod
    def desired(request, baseline):
        return matches(request.domain, baseline["target_value"], baseline["observation"])

    def lock_requests(self, prepared):
        entity_id = prepared.request.entity_id
        return (
            LockRequest("entity:" + entity_id, (LockScope.RESOURCE,),
                        LockMode.EXCLUSIVE, (self.target_reason,)),
            LockRequest("entity_registry:" + entity_id, (LockScope.RESOURCE,),
                        LockMode.SHARED, ("entity_registry_identity_dependency",)),
            LockRequest("home_assistant:core", (LockScope.RESOURCE,),
                        LockMode.SHARED, ("home_assistant_availability_dependency",)),
        )

    async def preflight(self, prepared, *, acquired_locks):
        """Re-run §5.5 inside the locks; a refusal keeps its code (no exception)."""
        args = digest(arguments(prepared.request, prepared.baseline))
        def rejected(outcome, code, fingerprint=None):
            return PreflightResult(False, outcome, prepared.target, fingerprint,
                                   prepared.provider_contract, "ha_call_service", args, None,
                                   (code,))
        try:
            current = await self.prepare(prepared.request, contract=prepared.provider_contract,
                                         core_binding=prepared.core_binding)
        except DeviceConfigurationRefusal as exc:
            code = category(exc) or STATE_CHANGED
            if code in AUTHORITY_REFUSALS or code == "device_configuration_provider_unavailable":
                return rejected(Outcome.PROVIDER_UNAVAILABLE_PRE_DISPATCH, code)
            return rejected(Outcome.PREFLIGHT_REJECTED, code)
        fingerprint = current.current_state_fingerprint
        if fingerprint != prepared.current_state_fingerprint:
            return rejected(Outcome.PREFLIGHT_REJECTED, STATE_CHANGED, fingerprint)
        no_op = self.desired(prepared.request, current.baseline)
        return PreflightResult(
            not no_op, Outcome.SUCCEEDED_VERIFIED if no_op else None, prepared.target,
            fingerprint, prepared.provider_contract, "ha_call_service", args,
            digest(current.baseline["observation"]),
            ("desired_state_already_reached",) if no_op else (),
        )

    async def dispatch(self, prepared, preflight, *, before_dispatch):
        request = prepared.request
        ran = dispatched = callback_failed = False

        async def once():
            nonlocal ran, dispatched, callback_failed
            if ran:
                raise ValueError("device_configuration_duplicate_dispatch")
            ran = True
            # §5.7 review F2: the full composite, not the state digest. A
            # failed or changed read, or a re-read outside the allowlist or
            # the config category, refuses before before_dispatch.
            try:
                observed, _ = await self.core.read(
                    lambda check: self._read(request.entity_id, check), prepared)
            except DeviceConfigurationRefusal as exc:
                if category(exc) in AUTHORITY_REFUSALS:
                    raise
                raise Refusal(STATE_CHANGED) from None
            if digest(observed) != prepared.current_state_fingerprint:
                raise Refusal(STATE_CHANGED)
            try:
                await before_dispatch()
            except Exception:
                callback_failed = True
                raise
            dispatched = True

        try:
            evidence = await self.provider.dispatch(request.entity_id, request.domain,
                                                    prepared.baseline["target_value"], once)
            if not dispatched:
                raise ValueError("device_configuration_dispatch_boundary_missing")
            return DispatchResult(Outcome.OBSERVING, True, 1, True, True,
                                  response_evidence_hash=evidence)
        except Exception as exc:
            if not dispatched:
                if callback_failed:
                    return DispatchResult(
                        Outcome.FAILED_PRE_DISPATCH, False, 0, False, False,
                        diagnostic_codes=(self.diagnostic("fan_dispatch_refused"),
                                          CALLBACK_FAILED_NOT_SENT_DECLARATION),
                    )
                code = category(exc)
                if code in PRE_SEND_REFUSALS:
                    # D1: returned, not raised, so the executor's pre-intent
                    # refusal path keeps the code the caller sees.
                    return DispatchResult(Outcome.FAILED_PRE_DISPATCH, False, 0, False, False,
                                          diagnostic_codes=(code,))
                raise self.refusal("fan_dispatch_refused") from None
            if isinstance(exc, self.refusal) and getattr(exc, "call_not_sent", False) is True:
                return DispatchResult(
                    Outcome.DISPATCH_FAILED_CONFIRMED, True, 1, True, False,
                    diagnostic_codes=(PROVIDER_CALL_NOT_SENT,
                                      self.diagnostic("fan_provider_authority_retired")),
                )
            return DispatchResult(
                Outcome.DISPATCH_INDETERMINATE, True, 1, True, False,
                diagnostic_codes=(self.diagnostic("fan_response_uncertain"),),
            )

    async def observe(self, prepared, dispatch):
        """Composite readback; an unreadable or out-of-proof target is a mismatch."""
        request = prepared.request
        try:
            observed, _ = await self.core.read(
                lambda check: self._read(request.entity_id, check), prepared)
        except DeviceConfigurationRefusal as exc:
            code = category(exc)
            if code in AUTHORITY_REFUSALS or code == "device_configuration_provider_unavailable":
                raise
            fingerprint = digest({"unreadable": code})
            return ObservationResult(Outcome.OBSERVING, 1, True, True, True, fingerprint, False,
                                     (self.mismatch_field,), fingerprint)
        fingerprint = digest(observed)
        ok = verified(request.domain, prepared.baseline["target_value"], observed,
                      prepared.baseline["observation"])
        return ObservationResult(Outcome.OBSERVING, 1, True, True, True, fingerprint, ok,
                                 () if ok else (self.mismatch_field,), fingerprint)

    async def verify(self, prepared, observation_result):
        result = await super().verify(prepared, observation_result)
        if (result.outcome == Outcome.SUCCEEDED_VERIFIED and not prepared.request.is_undo
                and self.record_verified is not None):
            # §5.11.1: written before the terminal commit; undo eligibility
            # additionally requires the terminal record succeeded_verified.
            self.record_verified(prepared, observation_result.readback_state_fingerprint)
        return result
