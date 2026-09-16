"""Conditional binary fan semantics; signed read contracts are not write grants."""
from .contracts import CORE_VERSION, CORE_REQUIREMENTS, FanRefusal


class FanCoreAuthority:
    def __init__(self, runtime):
        self.runtime = runtime

    async def acquire(self, prepared=None, preflight=None):
        await self.runtime.reconcile_once("fan_authority")
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
            raise FanRefusal("fan_core_authority_unavailable")
        commits = self.consume(authority)
        if commits is None:
            self.release(authority)
            raise FanRefusal("fan_core_authority_unavailable")
        def check():
            if not self.revalidate(authority, commits):
                raise FanRefusal("fan_core_authority_retired")
        try:
            check()
            value = await callback(check)
            check()
            return value
        finally:
            self.finish(commits)
