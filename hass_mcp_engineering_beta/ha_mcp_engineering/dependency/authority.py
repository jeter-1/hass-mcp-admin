"""Manager-owned Core authority for a bounded dependency build."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import time
from typing import Any

from ..errors import HomeAssistantUnavailableError
from ..request_context import begin_request, current_telemetry, end_request


# A whole scan, including queued reads, must not retain authority indefinitely.
# This is independent of the evidence TTLs and individual transport timeouts.
BUILD_TIMEOUT_SECONDS = 300.0


@contextmanager
def dependency_build_authority(
    core_runtime: Any, deadline: float
) -> Iterator[Callable[[], None]]:
    """Own one exact lease/commit; never borrow or revive caller authority."""

    parent = current_telemetry()
    telemetry, token = begin_request()
    telemetry.tool_name = "dependency_index_build"
    if parent is not None:
        telemetry.caller_id = parent.caller_id
        telemetry.audit_context = dict(parent.audit_context)
        telemetry.audit_context["parent_request_id"] = parent.request_id
    owner = asyncio.current_task()
    authority = None
    commits = None
    closed = False

    def authorize() -> bool:
        nonlocal commits, closed
        if closed:
            return False
        if (
            authority is None
            or time.monotonic() >= deadline
            or owner is None
            or owner.cancelling()
            or owner.done()
        ):
            closed = True
            return False
        if commits is None:
            commits = core_runtime.consume(authority)
        if commits is None or not core_runtime.revalidate(authority, commits):
            closed = True
            return False
        return True

    def require_current() -> None:
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
        if not telemetry.authorize_core_dispatch():
            raise HomeAssistantUnavailableError()

    # Install refusal before acquisition, including acquisition failures. No
    # provider work may exploit RequestTelemetry's no-authorizer default.
    telemetry.core_dispatch_authorizer = authorize
    try:
        if core_runtime is not None:
            authority = core_runtime.acquire(("core.dependency_helper_planning",))
        require_current()
        yield require_current
    finally:
        # Close both the telemetry slot and any callback captured before exit.
        closed = True
        telemetry.core_dispatch_authorizer = lambda: False
        try:
            if commits is not None:
                core_runtime.finish(commits)
            elif authority is not None:
                core_runtime.release(authority)
        finally:
            end_request(token)
