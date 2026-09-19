"""Separate power receipts and authority; shared durable ownership and recovery."""
from ..fan.audit import FanExecutionRepository, request_summary as _request_summary
from ..fan.authority import FanCoreAuthority
from ..fan.service import FanService
from ..providers.upstream_power import PowerProvider
from .adapter import PowerAdapter, prepare_record
from .contracts import POWER_CONTRACTS, PROVIDER, PowerRequest, PowerRefusal


class PowerCoreAuthority(FanCoreAuthority):
    trigger = "power_authority"
    refusal = PowerRefusal


class PowerExecutionRepository(FanExecutionRepository):
    kind = "power"
    provider_name = PROVIDER


def request_summary(telemetry):
    return _request_summary(telemetry, kind="power", provider=PROVIDER)


class PowerService(FanService):
    namespace = "ordinary-power-v1"
    request_type = PowerRequest
    adapter_type = PowerAdapter
    repository_type = PowerExecutionRepository
    contracts = POWER_CONTRACTS
    prepare_record = staticmethod(prepare_record)
    provider_name = PROVIDER
    binding_attribute = "ordinary_power_binding"
    outcome_key = "power_outcome"
    refusal = PowerRefusal

    @staticmethod
    def receipt_parameters(request):
        return {"domain": request.domain,
                "consequences": ["target_power_changes", "integration_defaults_and_group_members_may_apply",
                                 "automation_reactions_possible", "consumer_coverage_incomplete"]}


class PowerRuntime:
    service = None

    def require(self):
        if self.service is None:
            raise PowerRefusal("power_service_unavailable")
        return self.service

    def configure(self, settings, core_runtime, read_gateway, *, audit=None):
        self.service = PowerService(settings.governance_path,
                                    PowerProvider.configured(settings, read_gateway),
                                    PowerCoreAuthority(core_runtime), audit=audit)


POWER_OPERATIONS = PowerRuntime()
