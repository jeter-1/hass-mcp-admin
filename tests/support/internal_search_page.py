"""HAMCP-501 W6 (G1, as relayed by the architect): the internal-only route.

The delegated 9.2.0 ``ha_search`` is admitted internal-only: never published,
never a direct read. A test that pinned it as a published read reaches its
route only the way the native search page reader does: ``execute`` with a page
capture and the fixed page arguments, inside a request that carries a Core
dispatch authorizer. The route still acquires its own Core requirement (G2).
"""

from copy import deepcopy
from dataclasses import replace
import json

from ha_mcp_engineering.ha_core_readmission.routes import INTERNAL_ONLY_DELEGATED_READS
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.search import subcalls

# One helper page; the sub-call pages are configuration-only.
PAGE_ARGUMENTS = subcalls.page_arguments("helper", "synthetic", True, 0)
# A synthetic endpoint whose parts occur in no page: a page the sanitizer
# changed fails closed (W4 D2), and the default test endpoint's host occurs in
# the Row 25 projection's own key names.
_PAGE_HOST, _PAGE_SEGMENT = "qzsynthpagehost", "qzsynthpagesegmentnotreal"


def page_settings(settings):
    """``settings`` with an endpoint a sub-call page never collides with."""

    return replace(settings, upstream_dashboard_mcp_url=(
        f"http://{_PAGE_HOST}:9583/{_PAGE_SEGMENT}/mcp"))


def is_internal_only(name):
    return name in INTERNAL_ONLY_DELEGATED_READS


async def execute_page(gateway, name="ha_search", arguments=None, *, bucket="helpers",
                       authorizer=lambda: True, caller="synthetic-w6-internal-page",
                       route=None):
    """One sub-call page through the internal route; the decoded reply.

    ``route`` pins an earlier admitted route (a flip or drift test); by
    default the route currently admitted.
    """

    if route is None:
        with gateway._lock:
            route = gateway._internal[name]
    telemetry, token = begin_request(caller)
    telemetry.core_dispatch_authorizer = authorizer
    try:
        return json.loads(await gateway.execute(
            exposed_name=name,
            arguments=deepcopy(PAGE_ARGUMENTS if arguments is None else arguments),
            # The schema the gateway's own page reader validates with.
            reviewed_schema=gateway_module._public_read_schema(
                route.entry, route.observed_tool["inputSchema"]),
            policy_entry=route.entry,
            admission_generation=route.generation,
            contract_fingerprint=route.contract_fingerprint,
            search_page=gateway_module._SearchPageCapture(bucket),
        ))
    finally:
        end_request(token)
