"""Domain-specific preparation over the shared ordinary-action lifecycle."""
from dataclasses import dataclass

from ..fan.adapter import FanAdapter, PreparedFan
from ..f3.contracts import AdapterCapabilityDescriptor, F3_ADAPTER_CONTRACT_MODEL, OperationTarget
from .contracts import POWER_CONTRACTS, POWER_SEMANTIC_CONTRACTS, PowerRequest, PowerRefusal, checked_state, desired, digest
from ..ha_core_readmission.typed_operations import checked_binding


@dataclass(frozen=True)
class PreparedPower(PreparedFan):
    request: PowerRequest


def prepare_record(request, baseline, contract=POWER_CONTRACTS[0], core_binding=None):
    request = PowerRequest.model_validate(request.model_dump()).checked()
    baseline = checked_state(baseline, request.entity_id)
    if core_binding is not None:
        core_binding = checked_binding(core_binding, "power")
        allowed = POWER_SEMANTIC_CONTRACTS.values()
    else:
        allowed = POWER_CONTRACTS
    if contract not in allowed:
        raise PowerRefusal("power_contract_unknown")
    material = {"request": request.model_dump(), "baseline": baseline, "contract": contract}
    if core_binding is not None:
        material["core_binding"] = core_binding
    return PreparedPower(
        F3_ADAPTER_CONTRACT_MODEL, "typed_power", request.action,
        OperationTarget(request.domain, request.entity_id), digest(baseline),
        digest(request.arguments()), digest(material), "physical_action",
        digest({"power_contract": contract, **({"core_binding": core_binding} if core_binding else {})}),
        digest({"kind": "authenticated_connector", "request": request.model_dump()}),
        ("target_power_changes", "integration_defaults_and_group_members_may_apply",
         "automation_reactions_possible", "consumer_coverage_incomplete"),
        "power-exact-state-v1", digest({"desired": request.model_dump()}),
        False, request, baseline, contract, core_binding,
    )


class PowerAdapter(FanAdapter):
    capabilities = AdapterCapabilityDescriptor(
        "typed_power", F3_ADAPTER_CONTRACT_MODEL, "ordinary_power",
        ("turn_on", "turn_off"), False, True, True,
    )
    prepare_record = staticmethod(prepare_record)
    desired = staticmethod(desired)
    refusal = PowerRefusal
    target_reason = "exact_power_target"
    mismatch_field = "state"

    @staticmethod
    def diagnostic(code):
        return "power_" + code.removeprefix("fan_")

    @staticmethod
    def domain(request):
        return request.domain

    @staticmethod
    def check_features(request, state):
        # Core light/switch on/off does not use the fan feature-bit contract.
        # Exact registered services and available state are checked separately.
        checked_state(state, request.entity_id)
