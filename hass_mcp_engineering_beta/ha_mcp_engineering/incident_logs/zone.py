"""Home Assistant's configured time zone, read from Core per call (HAMCP-220).

The zone the journal text is read in is Home Assistant's ``time_zone`` from
``GET /api/config`` (the registered HAMCP-200 ``/config`` route). Core owns
it and may change it at runtime (``core_config.py`` ``async_update``); the
add-on container's ``TZ`` is set once, when Supervisor creates the
container, so it is only a cross-check.

The read is one ``GET`` under this module's own ``core.basic_rest_read``
lease: acquired, committed and revalidated here, never borrowed from the
gateway (``get_incident_logs`` acquires no gateway lease, so that the
journals stay readable while Core is down). Any refusal, error, timeout or
malformed payload is ``None``: the caller then reads no journal, never one
in the add-on's ``TZ`` or in UTC.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.paths import rest_path
from ..request_context import begin_request, current_telemetry, end_request
from . import contracts as c


REQUIREMENTS = ("core.basic_rest_read",)


class HomeAssistantZoneReader:
    """``await reader(deadline_seconds)`` is the zone name, or ``None``."""

    def __init__(self, client: Any, core_runtime: Any):
        self.client = client
        self.core_runtime = core_runtime

    async def __call__(self, deadline_seconds: float = c.ZONE_READ_DEADLINE_SECONDS) -> str | None:
        client, core = self.client, self.core_runtime
        if client is None or core is None or deadline_seconds <= 0:
            return None
        authority = core.acquire(REQUIREMENTS)
        if authority is None:
            return None
        commits = core.consume(authority)
        if commits is None:
            core.release(authority)
            return None
        closed = False

        def authorize() -> bool:
            return not closed and core.revalidate(authority, commits) is True

        telemetry, token = current_telemetry(), None
        if telemetry is None:
            # Never rely on the transport's no-telemetry default.
            telemetry, token = begin_request()
        prior, prior_error = telemetry.core_dispatch_authorizer, telemetry.error_code

        def authorize_core_dispatch() -> bool:
            # The gateway installs no authorizer for this Core-independent
            # tool; any prior one still has to agree.
            if prior is not None and prior() is not True:
                return False
            return authorize()

        telemetry.core_dispatch_authorizer = authorize_core_dispatch
        try:
            async with asyncio.timeout(min(deadline_seconds, c.ZONE_READ_DEADLINE_SECONDS)):
                payload = await client.request("GET", rest_path("/config"))
            if not authorize():
                return None
        except Exception:
            return None
        finally:
            closed = True
            if token is not None:
                telemetry.core_dispatch_authorizer = lambda: False
                end_request(token)
            else:
                # A failed zone read is this tool's disclosed partial result
                # (time_zone_unavailable), not the call's provider error.
                telemetry.core_dispatch_authorizer, telemetry.error_code = prior, prior_error
            core.finish(commits)
        if not isinstance(payload, dict):
            return None
        zone = payload.get("time_zone")
        return zone if type(zone) is str else None
