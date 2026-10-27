"""Explicit trace snapshot composition; no import-time I/O or polling."""

from pathlib import Path

from .contracts import TraceSnapshotError
from .poller import TraceWatchPoller
from .service import TraceSnapshotService


class TraceSnapshotRuntime:
    def __init__(self):
        self.service = None
        self.poller = None

    def configure(self, settings, core_runtime, *, audit, command):
        self.service = TraceSnapshotService(
            Path(settings.governance_path), command, core_runtime, audit,
            known_secrets=(settings.access_secret, settings.ha_token))
        self.poller = TraceWatchPoller(self.service, core_runtime)

    def require(self):
        if self.service is None:
            raise TraceSnapshotError("authority_unavailable")
        return self.service

    def start_poller(self):
        return self.poller.start() if self.poller is not None else None

    async def shutdown(self):
        if self.poller is not None:
            await self.poller.shutdown()

    def health(self):
        if self.service is None:
            return {"configured": False}
        return {"configured": True, "paused": self.service.paused,
                "ticks": self.poller.ticks, "failed_ticks": self.poller.failed_ticks}


TRACE_SNAPSHOTS = TraceSnapshotRuntime()
