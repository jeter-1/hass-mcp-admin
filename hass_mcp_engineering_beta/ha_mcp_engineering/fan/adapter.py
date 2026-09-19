"""One fan mutation through the shipped F3 ownership and intent machinery."""
from dataclasses import dataclass
from types import SimpleNamespace

from ..f3.contracts import (
    AdapterCapabilityDescriptor, DispatchResult, F3_ADAPTER_CONTRACT_MODEL,
    HA_MCP_PROVIDER_LOCK_KEY, LockMode, LockRequest, LockScope,
    NormalizedOperationOutcome as Outcome, ObservationResult, OperationTarget,
    PreflightResult, PreparedOperation, VerificationResult,
)
from .contracts import FanRequest, FanRefusal, FAN_CONTRACT, FAN_CONTRACTS, check_features, desired, digest


@dataclass(frozen=True)
class PreparedFan(PreparedOperation):
    request: FanRequest
    baseline: dict
    provider_contract: str


def prepare_record(request, baseline, contract=FAN_CONTRACT):
    if contract not in FAN_CONTRACTS:
        raise FanRefusal("fan_contract_unknown")
    material = {"request": request.model_dump(), "baseline": baseline, "contract": contract}
    authority_hash = digest({"kind": "authenticated_connector", "request": request.model_dump()})
    return PreparedFan(
        F3_ADAPTER_CONTRACT_MODEL, "typed_fan", request.action,
        OperationTarget("fan", request.entity_id), digest(baseline),
        digest(request.arguments()), digest(material), "physical_action",
        digest({"fan_contract": contract}), authority_hash,
        ("fan_state_or_speed_changes", "automation_reactions_possible", "consumer_coverage_incomplete"),
        "fan-exact-state-percentage-v1", digest({"desired": request.model_dump()}),
        False, request, baseline, contract,
    )


class FanAdapter:
    prepare_record = staticmethod(prepare_record)
    desired = staticmethod(desired)
    check_features = staticmethod(check_features)
    refusal = FanRefusal
    target_reason = "exact_fan_target"
    mismatch_field = "state_or_percentage"

    @staticmethod
    def diagnostic(code):
        return code

    @staticmethod
    def domain(request):
        return "fan"

    capabilities = AdapterCapabilityDescriptor(
        "typed_fan", F3_ADAPTER_CONTRACT_MODEL, "ordinary_fan",
        ("turn_on", "turn_off", "set_percentage"), False, True, True,
    )

    def __init__(self, provider, core):
        self.provider, self.core = provider, core

    async def prepare(self, request, *, contract=None):
        async def read(check):
            selected = await self.provider.services(request, check, contract=contract)
            state = await self.provider.state(request.entity_id, check, contract=selected)
            self.check_features(request, state)
            return state, selected
        baseline, selected = await self.core.read(
            read, SimpleNamespace(target=OperationTarget(self.domain(request), request.entity_id)),
        )
        return self.prepare_record(request, baseline, selected)

    def lock_requests(self, prepared):
        return (
            LockRequest("entity:" + prepared.request.entity_id, (LockScope.RESOURCE,),
                        LockMode.EXCLUSIVE, (self.target_reason,)),
            LockRequest("home_assistant:core", (LockScope.RESOURCE,),
                        LockMode.SHARED, ("home_assistant_availability_dependency",)),
            LockRequest(HA_MCP_PROVIDER_LOCK_KEY, (LockScope.PROVIDER,),
                        LockMode.SHARED, ("upstream_provider_dependency",)),
        )

    async def preflight(self, prepared, *, acquired_locks):
        current = await self.prepare(prepared.request, contract=prepared.provider_contract)
        fresh = current.current_state_fingerprint == prepared.current_state_fingerprint
        no_op = fresh and self.desired(prepared.request, current.baseline)
        return PreflightResult(
            fresh and not no_op,
            Outcome.SUCCEEDED_VERIFIED if no_op else None if fresh else Outcome.PREFLIGHT_REJECTED,
            prepared.target, current.current_state_fingerprint, prepared.provider_contract,
            "ha_call_service", digest(prepared.request.arguments()),
            digest(current.baseline), ("desired_state_already_reached",) if no_op else () if fresh else (self.diagnostic("fan_state_changed"),),
        )

    async def dispatch(self, prepared, preflight, *, before_dispatch):
        dispatched = False
        async def once():
            nonlocal dispatched
            if dispatched:
                raise ValueError("fan_duplicate_dispatch")
            state = await self.core.read(
                lambda check: self.provider.state(prepared.request.entity_id, check, contract=prepared.provider_contract), prepared,
            )
            if digest(state) != prepared.current_state_fingerprint:
                raise self.refusal("fan_state_changed")
            await before_dispatch()
            dispatched = True
        try:
            evidence = await self.provider.dispatch(prepared.request, once, contract=prepared.provider_contract)
            if not dispatched:
                raise ValueError("fan_dispatch_boundary_missing")
            return DispatchResult(Outcome.OBSERVING, True, 1, True, True,
                                  response_evidence_hash=evidence)
        except Exception:
            if not dispatched:
                raise self.refusal("fan_dispatch_refused") from None
            return DispatchResult(
                Outcome.DISPATCH_INDETERMINATE, True, 1, True, False,
                diagnostic_codes=(self.diagnostic("fan_response_uncertain"),),
            )

    async def observe(self, prepared, dispatch):
        state = await self.core.read(
            lambda check: self.provider.state(prepared.request.entity_id, check, contract=prepared.provider_contract), prepared,
        )
        matches = self.desired(prepared.request, state)
        return ObservationResult(
            Outcome.OBSERVING, 1, True, True, True, digest(state), matches,
            () if matches else (self.mismatch_field,), digest(state),
        )

    async def verify(self, prepared, observation):
        matches = observation.intended_result_observed is True
        return VerificationResult(
            Outcome.SUCCEEDED_VERIFIED if matches else Outcome.OBSERVING,
            1, True if matches else None, observation.readback_state_fingerprint,
            observation.mismatch_fields, observation.evidence_hash,
        )

    async def recover(self, prepared, *, context):
        return await self.observe(prepared, None)
