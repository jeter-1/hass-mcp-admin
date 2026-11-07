"""HAMCP-319 Phase 2 section 8.1 item 1: useful results without a redirect.

Design docs/HAMCP_319_PHASE2_DESIGN.md section 8.1 item 1 names two tests:

* ``test_85_no_redirect_useful_both_forms``: an 8.5.0 peer that answers
  without a redirect, on a ``legacy`` value and on a strict-form value,
  returns useful results on every client: a delegated read, a governed
  dashboard list and get, and a typed power, fan or helper operation with
  verified readback.
* ``test_91_no_redirect_useful_strict_full``: the same on a 9.1.0 peer with a
  ``strict_full`` value.

Every operation runs over the production no-redirect HTTP transport (real
``streamable_http`` client, real ``NoRedirectBinding`` hooks, real catalog
admission) against the loopback ``RedirectPeer`` serving the reviewed runtime
catalog of the exact release. The peer never redirects here; each case proves
that the no-redirect guard costs no useful behavior: no binding latches, no
response hook sees a 3xx, the redirect destination is never contacted, and no
synthetic URL canary reaches a result.

Upstream response shapes come from committed fixtures only:

* 8.5.0 delegated read: tests/fixtures/ha_mcp_8_5_0/read-results.json
  ``standalone-call-04.json`` (captured 8.5.0 blueprint list).
* 9.1.0 delegated read: tests/fixtures/ha_mcp_9_1_0/config/responses.json
  ``/success/row_1_automation`` (class S, see its provenance.json).
* Dashboard list and get: the DB-area envelopes
  (tests/fixtures/ha_mcp_9_1_0/dashboard/envelopes.json) for 9.1.0 and the
  8.5.0 shapes, both through ``test_hamcp319_dashboard``.
* Typed power: the TW-area templates
  (tests/fixtures/ha_mcp_9_1_0/typed/responses.json) for 9.1.0 and the 8.5.0
  shapes, both through ``test_hamcp319_typed_writes.Peer.respond``.

Nothing here contacts Home Assistant or a deployed endpoint; all secrets,
credentials and canaries are synthetic.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.power.contracts import POWER_RELEASES, digest  # noqa: E402
from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from support.ha_mcp_redirect_peer import ALL_CANARIES, RedirectPeer  # noqa: E402
import test_hamcp319_dashboard as dashboard_fixtures  # noqa: E402
import test_hamcp319_no_redirect as part1  # noqa: E402
import test_hamcp319_no_redirect_matrix as part2  # noqa: E402
import test_hamcp319_typed_writes as typed_fixtures  # noqa: E402
from tests.test_typed_fan import Clock, Core  # noqa: E402

READ_RESULTS_85 = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_8_5_0/read-results.json").read_text()
)
CONFIG_91 = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_9_1_0/config/responses.json").read_text()
)["success"]["row_1_automation"]
URL_PATH = dashboard_fixtures.URL_PATH
# Request types the peer sees for one successful client session (no redirect).
SESSION_REQUESTS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "get_stream",
        "tools/list:first",
        "tools/list:cursor",
        "tools/call",
        "delete",
    }
)


def reviewed_catalog(version: str) -> tuple[dict, ...]:
    return tuple(dashboard_fixtures.catalog(version))


@contextmanager
def observed_bindings():
    """Record every NoRedirectBinding the production clients create."""

    bindings: list = []
    token = part2._CELL_BINDINGS.set(bindings)
    try:
        with part2.recording_bindings():
            yield bindings
    finally:
        part2._CELL_BINDINGS.reset(token)


class _UsefulAssertions:
    def assert_no_redirect_cost(self, peer, bindings, result, *, label):
        counters = peer.counters
        self.assertTrue(bindings, f"{label}: no NoRedirectBinding was in the path")
        for binding in bindings:
            self.assertIsNone(binding.latch_code, label)
            self.assertEqual(binding.refuse_callers, [], label)
            self.assertEqual(binding.hook_redirects, 0, label)
            self.assertGreater(binding.hook_calls, 0, label)
        self.assertEqual(counters.destination_total(), 0, label)
        self.assertEqual(counters.injected, 0, label)
        self.assertLessEqual(set(counters.origin), SESSION_REQUESTS, label)
        self.assertGreater(counters.origin["tools/call"], 0, label)
        self.assertEqual(counters.origin["tools/call"], len(peer.calls), label)
        # Sessions open and close in pairs: one DELETE per initialize.
        self.assertEqual(counters.origin["delete"], counters.origin["initialize"], label)
        text = json.dumps(result, default=repr, sort_keys=True)
        for canary in ALL_CANARIES:
            self.assertNotIn(canary, text, label)


class NoRedirectUseful(_UsefulAssertions, unittest.IsolatedAsyncioTestCase):
    """Section 8.1 item 1: useful authorized success on every accepted form."""

    # -- delegated read (S-a, governed public read gateway) ------------------

    def _delegated_read_case(self, version: str):
        if version == "8.5.0":
            record = READ_RESULTS_85["standalone-call-04.json"]
            return SimpleNamespace(
                public_tool="ha_get_blueprint",
                public_arguments={},
                upstream=("ha_manage_blueprints", record["arguments"]),
                structured=record["result"]["structuredContent"],
            )
        return SimpleNamespace(
            public_tool="ha_config_get_automation",
            public_arguments=dict(CONFIG_91["arguments"]),
            upstream=(
                "ha_config_get_automation",
                {"entity_id": CONFIG_91["arguments"]["identifier"]},
            ),
            structured=CONFIG_91["structuredContent"],
        )

    async def delegated_read(self, version: str, form: str):
        case = self._delegated_read_case(version)
        label = f"delegated_read {version} {form}"

        def handler(name, arguments):
            self.assertEqual((name, arguments), case.upstream, label)
            return deepcopy(case.structured)

        peer = RedirectPeer(
            version=version,
            form=form,
            tools=reviewed_catalog(version),
            page_size=30,
            call_handler=handler,
        )
        with observed_bindings() as bindings, peer.serve():
            gateway = UpstreamReadGateway()
            gateway.configure(
                replace(part1._settings(peer.url), ha_mcp_release_registry_enabled=False)
            )
            await gateway.initialize(FastMCP("synthetic-hamcp319-useful"))
            health = gateway.health_snapshot()
            self.assertEqual(health["admission_status"], "admitted_exact", label)
            self.assertEqual(health["compatibility_status"], "exact", label)
            self.assertIsNone(health["last_discovery_failure_category"], label)
            tool = gateway._registered_tool_registry.snapshot()[case.public_tool]
            _telemetry, token = begin_request("synthetic-hamcp319-useful-read")
            try:
                result = json.loads(await tool.run(case.public_arguments))
            finally:
                end_request(token)
        self.assertIs(result["success"], True, result)
        self.assertEqual(result["operation"], case.public_tool, result)
        metadata = result["metadata"]
        self.assertEqual(metadata["completeness"], "complete", result)
        self.assertEqual(metadata["upstream_tool"], case.upstream[0], result)
        self.assertEqual(metadata["upstream_version"], version, result)
        self.assertEqual(metadata["fallback"], "none", result)
        self.assertIs(metadata["fallback_occurred"], False, result)
        self.assertEqual(result["timing"]["upstream_request_count"], 1, result)
        self.assertEqual(peer.calls, [case.upstream], label)
        if version == "8.5.0":
            listed = result["data"]["data"]["blueprints"]
            self.assertEqual(
                [item["path"] for item in listed],
                [item["path"] for item in case.structured["data"]["blueprints"]],
            )
        else:
            self.assertEqual(result["data"]["config"], case.structured["config"], result)
        self.assert_no_redirect_cost(peer, bindings, result, label=label)

    # -- governed dashboard list and get (S-c) -------------------------------

    async def dashboard_list_and_get(self, version: str, form: str):
        label = f"dashboard {version} {form}"
        configuration = {"title": "Before", "views": [{"title": "Home", "cards": []}]}
        rows = [dashboard_fixtures.listed()]
        config_hash = dashboard_fixtures.compute_config_hash_91(configuration)

        def handler(name, arguments):
            self.assertEqual(name, dashboard_fixtures.GETTER, label)
            if arguments.get("list_only") is True:
                if version == "9.1.0":
                    return dashboard_fixtures.fill(
                        dashboard_fixtures.ENVELOPES["list"], rows=deepcopy(rows), count=len(rows)
                    )
                return {"success": True, "action": "list", "count": len(rows), "dashboards": deepcopy(rows)}
            if version == "9.1.0":
                return dashboard_fixtures.fill(
                    dashboard_fixtures.ENVELOPES["get_stored_config"],
                    config=deepcopy(configuration),
                    config_hash=config_hash,
                    config_size_bytes=len(json.dumps(configuration)),
                ) | {"url_path": arguments["url_path"]}
            return {
                "success": True,
                "action": "get",
                "url_path": arguments["url_path"],
                "config": deepcopy(configuration),
                "config_hash": config_hash,
            }

        peer = RedirectPeer(
            version=version,
            form=form,
            tools=reviewed_catalog(version),
            page_size=30,
            call_handler=handler,
        )
        with observed_bindings() as bindings, peer.serve():
            provider = UpstreamDashboardProvider()
            provider.configure(part1._settings(peer.url))
            _telemetry, token = begin_request("synthetic-hamcp319-useful-dashboard")
            try:
                listing = await provider.list_dashboards(limit=200, response_limit=60_000)
                got = await provider.get_dashboard_config(
                    url_path=URL_PATH, force_reload=True, response_limit=60_000
                )
            finally:
                end_request(token)
            health = provider.health_snapshot()
        self.assertEqual(listing.completeness, "complete", label)
        self.assertEqual([row["url_path"] for row in listing.data["dashboards"]], [URL_PATH])
        self.assertEqual(got.completeness, "complete", label)
        self.assertEqual(got.data["configuration"], configuration, label)
        self.assertEqual(got.data["config_hash"], config_hash, label)
        self.assertIsNone(health["operator_remediation"], label)
        self.assertEqual(
            peer.calls,
            [
                (dashboard_fixtures.GETTER, {"list_only": True, "include_screenshot": False}),
                (
                    dashboard_fixtures.GETTER,
                    {
                        "url_path": URL_PATH,
                        "list_only": False,
                        "force_reload": True,
                        "include_screenshot": False,
                    },
                ),
            ],
            label,
        )
        self.assert_no_redirect_cost(
            peer, bindings, {"list": listing.data, "get": got.data}, label=label
        )

    # -- typed power with verified readback (C-family, S-a transport) --------

    async def typed_power(self, version: str, form: str):
        label = f"power {version} {form}"
        family = typed_fixtures.FAMILIES["power"]
        state = typed_fixtures.Peer("power", version)

        def handler(name, arguments):
            if name == "ha_call_service":
                state.apply(arguments)
            return state.respond(name, arguments)

        peer = RedirectPeer(
            version=version,
            form=form,
            tools=reviewed_catalog(version),
            page_size=30,
            call_handler=handler,
        )
        scratch = ROOT / ".artifacts" / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        with observed_bindings() as bindings, peer.serve(), tempfile.TemporaryDirectory(
            dir=scratch
        ) as tmp:
            transport = part1.CLIENTS["power"][0](peer.url)
            clock, core = Clock(), Core()
            provider = PowerProvider(transport, lambda *_args: "synthetic-current")
            service = PowerService(
                tmp, provider, PowerCoreAuthority(core), now=clock, sleep=clock.sleep
            )
            telemetry, token = begin_request("synthetic-hamcp319-useful-power")
            try:
                request = family.request(
                    entity_id=family.entity,
                    action=family.action,
                    operation_id=f"{int(clock().timestamp())}-{uuid.uuid4().hex}",
                )
                setattr(telemetry, family.binding, digest(request.model_dump()))
                result = await service.control(request)
            finally:
                end_request(token)
                await service.close()
        self.assertEqual(result["state"], "succeeded_verified", result)
        self.assertEqual(result["provider"], family.provider_name, result)
        self.assertEqual(result["provider_contract"], POWER_RELEASES[version][2], result)
        self.assertEqual(result["provider_attempt_count"], 1, result)
        self.assertTrue(result["provider_response_received"], result)
        self.assertEqual(state.writes, 1, label)
        self.assertEqual(state.state, "off", label)
        names = [name for name, _ in peer.calls]
        self.assertEqual(names.count("ha_call_service"), 1, label)
        # Verified readback: a state read follows the single write.
        self.assertEqual(names[-1], "ha_get_state", label)
        self.assertLess(names.index("ha_call_service"), len(names) - 1, label)
        self.assertEqual(
            dict(peer.calls)["ha_call_service"],
            request.arguments(),
            label,
        )
        self.assert_no_redirect_cost(peer, bindings, result, label=label)

    async def _useful(self, version: str, form: str):
        for name, case in (
            ("delegated_read", self.delegated_read),
            ("dashboard_list_and_get", self.dashboard_list_and_get),
            ("typed_power", self.typed_power),
        ):
            with self.subTest(version=version, form=form, client=name):
                await case(version, form)

    async def test_85_no_redirect_useful_both_forms(self):
        for form in ("legacy", "strict_full"):
            await self._useful("8.5.0", form)

    async def test_91_no_redirect_useful_strict_full(self):
        await self._useful("9.1.0", "strict_full")


if __name__ == "__main__":
    unittest.main()
