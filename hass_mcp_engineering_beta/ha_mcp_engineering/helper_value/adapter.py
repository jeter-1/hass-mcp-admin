"""Helper value preparation over the shared ordinary-action lifecycle."""
from dataclasses import dataclass

from ..fan.adapter import FanAdapter, PreparedFan
from ..f3.contracts import (AdapterCapabilityDescriptor, F3_ADAPTER_CONTRACT_MODEL,
                            NormalizedOperationOutcome as Outcome, ObservationResult, OperationTarget)
from ..ha_core_readmission.typed_operations import checked_binding
from .contracts import (ACTIONS, FAMILY, HELPER_SEMANTIC_CONTRACTS, HelperRefusal, HelperRequest,
                        check_live, checked_state, desired, digest, verification_basis)

EFFECTS = ("helper_value_changes", "automation_reactions_possible", "consumer_coverage_incomplete")


@dataclass(frozen=True)
class PreparedHelper(PreparedFan):
    request: HelperRequest


def prepare_record(request, baseline, contract=None, core_binding=None):
    request = HelperRequest.model_validate(request.model_dump()).checked()
    baseline = checked_state(baseline, request.entity_id)
    if core_binding is None:
        # Signed-only: no legacy Core version or unsigned contract exists.
        raise HelperRefusal("helper_core_authority_unavailable")
    core_binding = checked_binding(core_binding, FAMILY)
    if contract not in HELPER_SEMANTIC_CONTRACTS.values():
        raise HelperRefusal("helper_contract_unknown")
    material = {"request": request.model_dump(), "baseline": baseline, "contract": contract,
                "core_binding": core_binding}
    return PreparedHelper(
        F3_ADAPTER_CONTRACT_MODEL, "typed_helper_value", request.action,
        OperationTarget(request.domain, request.entity_id), digest(baseline),
        digest(request.arguments()), digest(material), "physical_action",
        digest({"helper_value_contract": contract, "core_binding": core_binding}),
        digest({"kind": "authenticated_connector", "request": request.model_dump()}),
        EFFECTS, "helper-value-exact-or-transition-v1",
        digest({"desired": request.model_dump(), "basis": verification_basis(request)}),
        False, request, baseline, contract, core_binding,
    )


class HelperAdapter(FanAdapter):
    capabilities = AdapterCapabilityDescriptor(
        "typed_helper_value", F3_ADAPTER_CONTRACT_MODEL, "ordinary_helper_value",
        ACTIONS, False, True, True,
    )
    prepare_record = staticmethod(prepare_record)
    # Preflight calls desired(request, baseline) without a pre-dispatch
    # baseline: transition actions (press, increment, timer) are never no-ops.
    desired = staticmethod(desired)
    refusal = HelperRefusal
    target_reason = "exact_helper_target"
    mismatch_field = "state_or_transition"

    @staticmethod
    def diagnostic(code):
        return "helper_" + code.removeprefix("fan_")

    @staticmethod
    def domain(request):
        return request.domain

    @staticmethod
    def check_features(request, state):
        # Live validation before every preparation, including the locked
        # preflight: options, ranges, steps, lengths, pattern, date/time fields
        # and timer/counter preconditions are refused rather than forwarded.
        check_live(request, checked_state(state, request.entity_id))

    async def observe(self, prepared, dispatch):
        state = await self.core.read(
            lambda check: self.provider.state(prepared.request.entity_id, check,
                                              contract=prepared.provider_contract), prepared,
        )
        # Independent readback against the declared pre-dispatch baseline.
        matches = desired(prepared.request, state, prepared.baseline)
        return ObservationResult(
            Outcome.OBSERVING, 1, True, True, True, digest(state), matches,
            () if matches else (self.mismatch_field,), digest(state),
        )
