"""Signed operation applicability plus exact legacy receipt compatibility."""
from .contracts import CORE_VERSION, CORE_REQUIREMENTS, FAN_CONTRACTS, FanRefusal
from ..ha_core_readmission.typed_operations import operation_profile, checked_binding


class FanCoreAuthority:
    trigger = "fan_authority"
    refusal = FanRefusal
    family = "fan"
    legacy_contracts = FAN_CONTRACTS

    def __init__(self, runtime):
        self.runtime = runtime

    async def acquire(self, prepared=None, preflight=None):
        await self.runtime.reconcile_once(self.trigger)
        binding = getattr(prepared, "core_binding", None)
        profile = operation_profile(self.family)
        if binding is not None:
            binding = checked_binding(binding, self.family)
            return self.runtime.acquire(
                CORE_REQUIREMENTS + (profile.capability_id,),
                expected_core_version=binding["version"],
                target=getattr(prepared, "target", None),
            )
        if getattr(prepared, "provider_contract", None) is None:
            # Initial selection: no version list. A signed exact reference to
            # this compiled semantic profile must accompany the base authority.
            requirements = CORE_REQUIREMENTS + (profile.capability_id,)
            if self.runtime.route_status(requirements)["available"]:
                # Acquisition may fail after this observation. Do not switch
                # contracts to conceal retirement or a concurrent authority change.
                return self.runtime.acquire(
                    requirements, expected_core_version=None,
                    target=getattr(prepared, "target", None),
                )
        # Preserve the exact shipped contract, including historical recovery.
        # Do not add future releases here or reinterpret a legacy receipt.
        contract = getattr(prepared, "provider_contract", None)
        if contract is not None and contract not in self.legacy_contracts:
            return None
        return self.runtime.acquire(
            CORE_REQUIREMENTS, expected_core_version=CORE_VERSION,
            target=getattr(prepared, "target", None),
        )

    def consume(self, authority):
        return self.runtime.consume(authority)

    def revalidate(self, authority, commits):
        return self.runtime.revalidate(authority, commits)

    def release(self, authority):
        self.runtime.release(authority)

    def finish(self, commits):
        self.runtime.finish(commits)

    async def read(self, callback, prepared=None):
        authority = await self.acquire(prepared)
        if authority is None:
            raise self.refusal("fan_core_authority_unavailable")
        commits = self.consume(authority)
        if commits is None:
            self.release(authority)
            raise self.refusal("fan_core_authority_unavailable")
        def check():
            if not self.revalidate(authority, commits):
                raise self.refusal("fan_core_authority_retired")
        profile = operation_profile(self.family)
        check.core_binding = (
            {"version": authority.core_version, "contract_fingerprint": profile.contract_fingerprint}
            if profile.capability_id in getattr(authority, "capability_ids", ()) else None
        )
        try:
            check()
            value = await callback(check)
            check()
            return value
        finally:
            self.finish(commits)
