"""Explicit trace snapshot composition; no import-time I/O or polling."""

from pathlib import Path

from .contracts import TraceSnapshotError
from .poller import TraceWatchPoller
from .service import TraceSnapshotService


class TraceSnapshotRuntime:
    def __init__(self):
        self.service = None
        self.poller = None

    def configure(self, settings, core_runtime, *, audit, command, watch_session=None):
        self.service = TraceSnapshotService(
            Path(settings.governance_path), command, core_runtime, audit,
            known_secrets=(settings.access_secret, settings.ha_token))
        # HAMCP-431: the poller's held connection; manual captures keep
        # ``command`` (one connection per frame).
        self.poller = TraceWatchPoller(self.service, core_runtime, session=watch_session)

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
        health = {"configured": True, "paused": self.service.paused,
                  "ticks": self.poller.ticks, "failed_ticks": self.poller.failed_ticks}
        session = getattr(self.poller, "session", None)
        if session is not None:
            health["session"] = session.health()
        return health

    def watch_health(self):
        """HAMCP-296 ``get_server_health`` block; a failure degrades only this block."""

        try:
            if self.service is None:
                return {"configured": False, "status": "not_configured"}
            return {**self.health(), **self.service.watch_health()}
        except Exception:
            return {"configured": True, "status": "unavailable"}


TRACE_SNAPSHOTS = TraceSnapshotRuntime()
