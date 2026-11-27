"""HAMCP-510: a parent request for tests that call a fixed gateway reader.

Production reaches the script, YAML and history readers only inside a parent
request that carries Core dispatch authority: a gateway ``tools/call`` or the
dependency index build. A reader called with no parent, or a parent with no
authorizer, refuses before it starts a request. Tests that call a reader
directly open a parent here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from ha_mcp_engineering.request_context import RequestTelemetry, begin_request, end_request


@contextmanager
def authorized_parent(authorizer: Callable[[], bool] = lambda: True) -> Iterator[RequestTelemetry]:
    telemetry, token = begin_request()
    telemetry.core_dispatch_authorizer = authorizer
    try:
        yield telemetry
    finally:
        end_request(token)


def enter_authorized_parent(case, authorizer: Callable[[], bool] = lambda: True) -> RequestTelemetry:
    """Open a parent for one IsolatedAsyncioTestCase test, from its setUp.

    setUp, the test and its cleanups share one context (Python 3.11+), so the
    parent is current for the whole test and is ended by the cleanup.
    """

    return case.enterContext(authorized_parent(authorizer))
