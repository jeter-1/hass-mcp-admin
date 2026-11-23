"""HAMCP-371 SB: the typed power, fan and helper-value providers on ha-mcp 9.2.0.

The 9.2.0 analogue of the per-version provider coverage HAMCP-351 relied on
for 9.1.2, plus the refusal cases the plan requires. Each provider is bound on
9.2.0 only by its release row (``power/contracts.py``, ``fan/contracts.py``,
``helper_value/contracts.py``); no provider code changed. The rows name the S8
registry entry ``ha-mcp-v9.2.0-9de5bf6c`` and source commit ``51e3aac7``.

* The shipped power and helper-value suites run unchanged against the
  committed 9.2.0 capture in runtime order (``Power920Tests``,
  ``Helper920Tests``).
* Power and fan through the public tools over the pinned MCP ClientSession and
  a synthetic in-memory peer (the HAMCP-175 harness and its synthetic,
  not source-cited, reply shapes). 9.2.0 is admitted only on the strict
  ``/catalog/full`` endpoint form (``clients/ha_mcp_endpoint.py``
  ``STRICT_FULL_SELECTOR_RELEASES``), as 9.1.0 and 9.1.2 are, so the peer is
  reached through a synthetic, never-resolved ``/catalog/full`` URL. A 9.2.0
  peer reaches ``succeeded_verified`` with one exact ``ha_call_service``.
* Refusals on 9.2.0, each before any task with zero ``tools/call``: a drifted
  required descriptor; the 9.2.0 row removed (the provider unbound, as on
  ``main``); an unreviewed 9.2.1 peer; the legacy endpoint form.
* HAMCP-437: only 9.2.0 is admitted (D1); the 9.1.2 binding case is retired.

No endpoint, household target or production material is used.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.clients import upstream_read  # noqa: E402
from ha_mcp_engineering.fan.contracts import FAN_CONTRACTS, FAN_RELEASES  # noqa: E402
from ha_mcp_engineering.helper_value.contracts import HELPER_RELEASES, HelperRefusal  # noqa: E402
from ha_mcp_engineering.power.contracts import POWER_CONTRACTS, POWER_RELEASES  # noqa: E402
from ha_mcp_engineering.providers.upstream_fan import REQUIRED_TOOLS, FanProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry  # noqa: E402
from tests import test_typed_helper_value as typed_helper  # noqa: E402
from tests import test_typed_power as typed_power  # noqa: E402
import test_hamcp175_power_fan_admission as h175  # noqa: E402

VERSION = "9.2.0"
ENTRY = "ha-mcp-v9.2.0-9de5bf6c"
SOURCE = "51e3aac788482a081807591cd5d58d07dc4e540b"
ROWS = {"power": POWER_RELEASES, "fan": FAN_RELEASES, "helper": HELPER_RELEASES}
PROVIDERS = {"power": PowerProvider, "fan": FanProvider, "helper": HelperProvider}


def catalog(version=VERSION, *, server_version=None):
    """The committed capture in its reviewed runtime order."""

    folder = ROOT / "docs/evidence/upstream-read-compatibility"
    raw = json.loads((folder / f"ha-mcp-{version}.json").read_text(encoding="utf-8"))
    review = json.loads((folder / f"ha-mcp-{version}-contract-review.json").read_text(encoding="utf-8"))
    by_name = {tool["name"]: tool for tool in raw["tools"]}
    tools = tuple(deepcopy(by_name[name]) for name in review["runtime_catalog"]["runtime_tool_order"])
    return upstream_read.McpReadCatalog("2025-03-26", "ha-mcp", server_version or version, tools, 0)


STRICT_URL = "http://synthetic.invalid/base/catalog/full"  # never resolved; the client is replaced


class Peer920(h175.MemoryPeer):
    """The HAMCP-175 peer reached through the strict endpoint form."""

    url = STRICT_URL

    @asynccontextmanager
    async def connect(self, url, **kwargs):
        assert url == self.url, url
        async with h175.MemoryPeer.connect(self, url, **kwargs) as streams:
            yield streams


def without_920(rows):
    return {version: row for version, row in rows.items() if version != VERSION}


class ReleaseRows(unittest.TestCase):

    def test_rows_name_the_reviewed_920_registry_entry(self):
        release = load_reviewed_upstream_release_registry().by_version[VERSION]
        self.assertEqual((release.entry_id, release.source_commit), (ENTRY, SOURCE))
        self.assertEqual(release.provider_disposition("read_gateway"), "admitted")
        for kind, rows in ROWS.items():
            with self.subTest(kind=kind):
                self.assertEqual(rows[VERSION][:2], (ENTRY, SOURCE))
        self.assertEqual(POWER_RELEASES[VERSION][2], "core-2026.9.2-ha-mcp-9.2.0-single-power-v1")
        self.assertEqual(FAN_RELEASES[VERSION][2], "core-2026.9.2-ha-mcp-9.2.0-single-fan-v1")
        self.assertIn(POWER_RELEASES[VERSION][2], POWER_CONTRACTS)
        self.assertIn(FAN_RELEASES[VERSION][2], FAN_CONTRACTS)

    def test_920_catalog_binds_each_provider(self):
        for kind, provider_type in PROVIDERS.items():
            with self.subTest(kind=kind):
                version, contract, authority = provider_type(None, lambda *a: "a").validate_catalog(catalog())
                self.assertEqual((version, contract, authority), (VERSION, ROWS[kind][VERSION][2], "a"))

    def test_unbound_or_unreviewed_920_refuses_at_admission(self):
        for kind, provider_type in PROVIDERS.items():
            refusal = provider_type.refusal
            with self.subTest(kind=kind, case="unbound"), \
                 patch.object(provider_type, "releases", without_920(provider_type.releases)):
                with self.assertRaises(refusal) as caught:
                    provider_type(None, lambda *a: "a").validate_catalog(catalog())
                self.assertTrue(str(caught.exception).endswith("_provider_identity_unavailable"))
            with self.subTest(kind=kind, case="9.2.1"):
                with self.assertRaises(refusal) as caught:
                    provider_type(None, lambda *a: "a").validate_catalog(catalog(server_version="9.2.1"))
                self.assertTrue(str(caught.exception).endswith("_provider_identity_unavailable"))


class Public920(h175.Harness):
    """Power and fan through the public tools against a 9.2.0 peer."""

    def operation(self, kind="power", *, action="turn_off", tools=None, version=VERSION, url=STRICT_URL):
        provider_type, _, service_type, core_type, _, _, entity, _, _ = h175.KINDS[kind]
        peer = Peer920(tools if tools is not None else catalog().tools, entity, version=version)
        peer.url = url
        transport = h175.Transport(url, timeout_seconds=3, client_version="synthetic")
        provider = provider_type(transport, lambda *args: self.authority)
        service = service_type(self.root / h175.uuid.uuid4().hex, provider, core_type(self.core),
                               now=lambda: self.clock)
        self.addAsyncCleanup(service.close)
        request = self.request(kind, action=action)
        peer.expected = request.arguments()
        return peer, service, request

    async def test_920_peer_succeeds_verified_with_one_exact_write(self):
        for kind in h175.KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind)
                result = await self.public_call(peer, service, request)
                self.assertEqual(result.get("data", {}).get("state"), "succeeded_verified", result)
                self.assertEqual(peer.writes, 1)
                called = [name for name, _ in peer.calls]
                self.assertTrue(set(called) <= set(REQUIRED_TOOLS), called)
                self.assertEqual([args for name, args in peer.calls if name == "ha_call_service"],
                                 [request.arguments()])
                self.assertEqual(service.locks.records(), ())
                self.balanced()

    async def test_920_required_descriptor_drift_refuses_before_any_task(self):
        for kind in h175.KINDS:
            for name in REQUIRED_TOOLS:
                for component in h175.COMPONENTS:
                    with self.subTest(kind=kind, tool=name, component=component):
                        tools = tuple(h175.drifted(catalog().tools, name, component))
                        peer, service, request = self.operation(kind, tools=tools)
                        result = await self.public_call(peer, service, request)
                        self.assert_pre_save_refusal(result, peer, service, request,
                                                     "provider_contract_mismatch")
                        self.assertEqual(peer.calls, [])

    async def test_unbound_920_provider_fails_closed_with_zero_tool_calls(self):
        for kind in h175.KINDS:
            provider_type = h175.KINDS[kind][0]
            with self.subTest(kind=kind), \
                 patch.object(provider_type, "releases", without_920(provider_type.releases)):
                peer, service, request = self.operation(kind)
                result = await self.public_call(peer, service, request)
                self.assert_pre_save_refusal(result, peer, service, request, "provider_identity_unavailable")
                self.assertEqual(peer.calls, [])
                self.assertEqual(peer.events, ["initialize", "notifications/initialized", "tools/list"])

    async def test_unreviewed_921_peer_refuses_before_any_task(self):
        for kind in h175.KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, version="9.2.1")
                result = await self.public_call(peer, service, request)
                self.assert_pre_save_refusal(result, peer, service, request, "provider_identity_unavailable")
                self.assertEqual(peer.calls, [])


    async def test_920_on_the_legacy_endpoint_form_never_lists_or_calls(self):
        for kind in h175.KINDS:
            with self.subTest(kind=kind):
                peer, service, request = self.operation(kind, url="")
                result = await self.public_call(peer, service, request)
                self.assertFalse(result["success"], result)
                self.assertEqual(result["metadata"]["dispatch"], "no_dispatch")
                self.assertEqual(result["metadata"]["reconciliation"], "no_task")
                self.assertNotIn("task_id", result["metadata"])
                self.assertNotIn("tools/list", peer.events)
                self.assertEqual((peer.calls, peer.writes), ([], 0))
                self.assertIsNone(service.load(request.task_id))
                self.balanced()


class Power920Tests(typed_power.PowerTests):
    """The shipped power suite on the 9.2.0 capture."""

    version = VERSION


class Helper920Tests(typed_helper.HelperTests):
    """The shipped helper-value suite on the 9.2.0 capture, plus the unbound case."""

    version = VERSION

    async def test_unbound_920_helper_fails_closed_with_zero_tool_calls(self):
        with patch.object(HelperProvider, "releases", without_920(HelperProvider.releases)):
            with self.assertRaisesRegex(HelperRefusal, "helper_provider_identity_unavailable") as caught:
                await self.call(self.request())
        self.assertEqual(caught.exception.details["status"], "provider_unavailable")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.transport.writes, 0)
        self.settled()


if __name__ == "__main__":
    unittest.main()
