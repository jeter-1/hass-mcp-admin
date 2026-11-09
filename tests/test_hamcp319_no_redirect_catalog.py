"""HAMCP-319 Phase 2 section 8.2: uniform catalog per selector, order across pages.

Design docs/HAMCP_319_PHASE2_DESIGN.md section 8.2 names two rows this module
covers with the production admission code (real ``UpstreamReadGateway``, real
``UpstreamDashboardProvider``, real no-redirect transport) against the loopback
``RedirectPeer``:

* **Uniform catalog per selector.** The identical 82-tool 9.1.0 catalog that is
  admitted on a ``strict_full`` value is served on every unadmitted selector
  (each accepted ``legacy`` row of the section 3.2 table that a loopback peer
  can serve). Each is still refused with the row's selector code, after
  initialize and before any ``tools/list`` or ``tools/call``.
* **Order across pages.** The catalog is served in 30-tool cursor pages and its
  order is taken across those pages: the wire order and the reviewed runtime
  order are admitted, while a global alphabetical re-sort, a per-page re-sort,
  a swap across a page boundary, and a missing reviewed tool block admission.
  A duplicate, changed description, changed annotation or changed input schema
  of an exposed tool quarantines that tool. 8.5.0 keeps the same behavior on
  its captured catalog (parity controls).

Catalogs come from committed evidence only (never hand-edited):

* 9.1.0 wire catalog: docs/evidence/hamcp319/phase1/catalogs/full-1.json, the
  tools of ``pages`` in page order (Phase 1 class C capture).
* 9.1.0 runtime order: docs/evidence/upstream-read-compatibility/
  ha-mcp-9.1.0-contract-review.json ``/runtime_catalog/runtime_tool_order``.
* 8.5.0 catalog: docs/evidence/hamcp162/captures/catalog-8.5.0-A.json
  ``/tools`` (captured 8.5.0 catalog with the optional reader).

Variants are derived in memory by reordering, removing, duplicating or
changing one field of a copy. Loopback peers only; every secret is synthetic.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.clients.ha_mcp_endpoint import (  # noqa: E402
    ENDPOINT_REFUSAL_CODES,
)
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from support.ha_mcp_redirect_peer import RedirectPeer  # noqa: E402
from test_hamcp319_endpoint_selector import (  # noqa: E402
    LOOPBACK_ROWS,
    SITES,
    _loopback_suffix,
)
from test_hamcp319_no_redirect import (  # noqa: E402
    CLIENTS,
    DEADLINE_SECONDS,
    _drive,
    _settings,
    _sinks,
    capture_all_logs,
)

PAGE_SIZE = 30
OPTIONAL_READER = "ha_config_get_yaml"
PROBE_TOOL = "ha_get_state"
CANARIES = ("synthetictoken123", "synthetic-upstream-secret", "synthetic-pass")


def _load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


_FULL = _load("docs/evidence/hamcp319/phase1/catalogs/full-1.json")
WIRE_91 = tuple(tool for page in _FULL["pages"] for tool in page["tools"])
_ORDER_91 = _load(
    "docs/evidence/upstream-read-compatibility/ha-mcp-9.1.0-contract-review.json"
)["runtime_catalog"]["runtime_tool_order"]
RUNTIME_91 = tuple(tool for tool in WIRE_91 if tool["name"] in set(_ORDER_91))
CATALOG_85 = tuple(_load("docs/evidence/hamcp162/captures/catalog-8.5.0-A.json")["tools"])
FORMS = {"9.1.0": "strict_full", "8.5.0": "legacy"}


def _index(tools, name: str) -> int:
    return [tool["name"] for tool in tools].index(name)


def _paged_resort(tools):
    pages = [tools[i : i + PAGE_SIZE] for i in range(0, len(tools), PAGE_SIZE)]
    return tuple(t for page in pages for t in sorted(page, key=lambda t: t["name"]))


def _boundary_swap(tools):
    swapped = list(tools)
    swapped[PAGE_SIZE - 1], swapped[PAGE_SIZE] = swapped[PAGE_SIZE], swapped[PAGE_SIZE - 1]
    return tuple(swapped)


def _changed(tools, name: str, mutate):
    copied = deepcopy(list(tools))
    mutate(copied[_index(copied, name)])
    return tuple(copied)


def _append_description(tool):
    tool["description"] = tool["description"] + " changed"


def _flip_read_only(tool):
    tool["annotations"]["readOnlyHint"] = not tool["annotations"].get("readOnlyHint")


def _extra_property(tool):
    tool["inputSchema"].setdefault("properties", {})["synthetic_extra"] = {
        "type": "string"
    }


def _pages(counters) -> int:
    return counters.origin["tools/list:first"] + counters.origin["tools/list:cursor"]


async def _gateway(version: str, tools, *, form=None, suffix=None, host=None):
    peer = RedirectPeer(
        version=version,
        form=form or FORMS[version],
        tools=tuple(tools),
        page_size=PAGE_SIZE,
        raw_suffix=suffix,
        host=host,
    )
    with peer.serve():
        gateway = UpstreamReadGateway()
        gateway.configure(
            replace(_settings(peer.url), ha_mcp_release_registry_enabled=False)
        )
        await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE_SECONDS + 5)
        health = gateway.health_snapshot()
        exposed = frozenset(gateway._registered_tool_registry.snapshot())
    return health, exposed, peer.counters, peer.url


async def _dashboard(version: str, tools, *, form=None, suffix=None, host=None):
    peer = RedirectPeer(
        version=version,
        form=form or FORMS[version],
        tools=tuple(tools),
        page_size=PAGE_SIZE,
        raw_suffix=suffix,
        host=host,
    )
    with peer.serve():
        provider = UpstreamDashboardProvider()
        provider.configure(_settings(peer.url))
        error = None
        try:
            await asyncio.wait_for(provider.refresh_capabilities(), DEADLINE_SECONDS + 5)
        except Exception as exc:  # noqa: BLE001 - recorded outcome
            error = exc
        health = provider.health_snapshot()
    return error, health, peer.counters, peer.url


async def _transport(client: str, version: str, tools, *, suffix, host):
    builder, kind, _requests, _full = CLIENTS[client]
    peer = RedirectPeer(
        version=version,
        form="raw",
        raw_suffix=suffix,
        host=host,
        tools=tuple(tools),
        page_size=PAGE_SIZE,
    )
    with peer.serve():
        transport = builder(peer.url)
        result = error = None
        try:
            request_type = "tools/call" if kind == "read" else "tools/list:first"
            result = await asyncio.wait_for(
                _drive(client, transport, kind, request_type), DEADLINE_SECONDS + 5
            )
        except Exception as exc:  # noqa: BLE001 - recorded outcome
            error = exc
        finally:
            closer = getattr(transport, "aclose", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
    return error, result, peer.counters, peer.url


def _failure_category(error) -> str | None:
    category = getattr(error, "category", None)
    if category:
        return category
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        return details.get("failure_category")
    return None


class UniformCatalogPerSelectorTests(unittest.TestCase):
    """Section 8.2: the admitted 82-tool catalog is still refused per selector."""

    def assert_no_leak(self, url: str, sinks):
        for sink in sinks:
            self.assertNotIn(url, sink)
            for canary in CANARIES:
                self.assertNotIn(canary, sink)

    def test_91_uniform_catalog_admitted_on_strict_full(self):
        async def scenario():
            return (
                await _gateway("9.1.0", WIRE_91),
                await _dashboard("9.1.0", WIRE_91),
            )

        (health, exposed, counters, _url), (error, _dh, dcounters, _durl) = asyncio.run(
            scenario()
        )
        self.assertEqual(len(WIRE_91), 82)
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["compatibility_status"], "exact")
        self.assertIn(PROBE_TOOL, exposed)
        self.assertGreaterEqual(counters.origin["tools/list:cursor"], 1)
        self.assertIsNone(error, repr(error))
        self.assertGreaterEqual(_pages(dcounters), 1)
        self.assertEqual(counters.destination_total() + dcounters.destination_total(), 0)

    def test_91_uniform_catalog_refused_on_each_unadmitted_selector(self):
        async def scenario():
            outcomes = []
            for row, value, code in LOOPBACK_ROWS:
                host, suffix = _loopback_suffix(value)
                kwargs = dict(form="raw", suffix=suffix, host=host)
                gateway = await _gateway("9.1.0", WIRE_91, **kwargs)
                dashboard = await _dashboard("9.1.0", WIRE_91, **kwargs)
                transports = {
                    client: await _transport(
                        client, "9.1.0", WIRE_91, suffix=suffix, host=host
                    )
                    for client in SITES
                }
                outcomes.append((row, value, code, gateway, dashboard, transports))
            return outcomes

        with capture_all_logs() as capture:
            outcomes = asyncio.run(scenario())
        self.assertEqual(len(outcomes), len(LOOPBACK_ROWS))
        self.assertGreater(len(outcomes), 20)
        for row, value, code, gateway, dashboard, transports in outcomes:
            tab = "\t" in value
            health, exposed, counters, url = gateway
            with self.subTest(site="gateway", row=row, value=value):
                self.assertNotIn(health["admission_status"], {"admitted_exact", "partially_admitted"})
                self.assertEqual(exposed, frozenset())
                self.assertEqual(_pages(counters), 0)
                self.assertEqual(counters.origin["tools/call"], 0)
                self.assertEqual(counters.executions, 0)
                self.assertEqual(counters.destination_total(), 0)
                if tab:
                    self.assertEqual(counters.origin_total(), 0)
                    self.assertNotIn(
                        health["last_discovery_failure_category"], ENDPOINT_REFUSAL_CODES
                    )
                else:
                    self.assertEqual(counters.origin["initialize"], 1)
                    self.assertEqual(health["last_discovery_failure_category"], code)
                self.assert_no_leak(url, [json.dumps(health, default=str)])
            error, dhealth, dcounters, durl = dashboard
            with self.subTest(site="dashboard_provider", row=row, value=value):
                self.assertIsNotNone(error)
                self.assertEqual(_pages(dcounters), 0)
                self.assertEqual(dcounters.origin["tools/call"], 0)
                self.assertEqual(dcounters.destination_total(), 0)
                if tab:
                    self.assertEqual(dcounters.origin_total(), 0)
                else:
                    self.assertEqual(dcounters.origin["initialize"], 1)
                    self.assertEqual(_failure_category(error), code)
                self.assert_no_leak(
                    durl, _sinks(error, None) + [json.dumps(dhealth, default=str)]
                )
            for client, (terror, tresult, tcounters, turl) in transports.items():
                with self.subTest(site=client, row=row, value=value):
                    self.assertIsInstance(terror, DashboardTransportError)
                    self.assertEqual(_pages(tcounters), 0)
                    self.assertEqual(tcounters.origin["tools/call"], 0)
                    self.assertEqual(tcounters.executions, 0)
                    self.assertEqual(tcounters.destination_total(), 0)
                    if tab:
                        self.assertEqual(tcounters.origin_total(), 0)
                    else:
                        self.assertEqual(tcounters.origin["initialize"], 1)
                        self.assertEqual(terror.category, code)
                        self.assertFalse(terror.retryable)
                    self.assert_no_leak(turl, _sinks(terror, tresult))
        # Each peer URL carries the synthetic base; none reaches a log line.
        for line in capture.lines:
            for canary in CANARIES:
                self.assertNotIn(canary, line)


class OrderAcrossPagesTests(unittest.TestCase):
    """Section 8.2: order across cursor pages; missing, duplicate, changed."""

    def _run(self, version: str, tools):
        return asyncio.run(_gateway(version, tools))

    def _baseline(self, version: str, tools):
        health, exposed, counters, _url = self._run(version, tools)
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["compatibility_status"], "exact")
        self.assertIn(PROBE_TOOL, exposed)
        # 30-tool pages: the order is assembled across several cursor pages.
        self.assertGreaterEqual(counters.origin["tools/list:cursor"], 2)
        self.assertEqual(counters.origin["tools/call"], 0)
        self.assertEqual(counters.destination_total(), 0)
        return exposed

    def assert_blocked(self, version: str, tools, *, label: str):
        health, exposed, counters, _url = self._run(version, tools)
        with self.subTest(version=version, variant=label):
            self.assertEqual(health["admission_status"], "blocked_incompatible_upstream")
            self.assertEqual(health["compatibility_status"], "incompatible")
            self.assertIn("catalog_order_mismatch", health["quarantine_reason_counts"])
            # No stock reviewed tool is exposed; only the separately reviewed
            # optional reader (HAMCP-162 behavior, unchanged) may remain.
            self.assertLessEqual(exposed, frozenset({OPTIONAL_READER}))
            self.assertNotIn(PROBE_TOOL, exposed)
            self.assertGreaterEqual(counters.origin["tools/list:cursor"], 1)
            self.assertEqual(counters.origin["tools/call"], 0)
            self.assertEqual(counters.executions, 0)

    def assert_admitted_as(self, version: str, tools, baseline, *, label: str):
        """Codex F1 (9.1.0): a reorder or an unused-tool change admits the same reads."""
        health, exposed, counters, _url = self._run(version, tools)
        with self.subTest(version=version, variant=label):
            self.assertEqual(health["admission_status"], "admitted_exact")
            self.assertEqual(health["compatibility_status"], "exact")
            self.assertEqual(health["quarantine_reason_counts"], {})
            self.assertEqual(exposed, baseline)
            self.assertEqual(len(exposed - {OPTIONAL_READER}), 27)
            self.assertGreaterEqual(counters.origin["tools/list:cursor"], 1)
            self.assertEqual(counters.origin["tools/call"], 0)
            self.assertEqual(counters.executions, 0)
            self.assertEqual(counters.destination_total(), 0)

    def assert_only_probe_missing(self, version: str, tools, baseline, *, label: str):
        health, exposed, counters, _url = self._run(version, tools)
        with self.subTest(version=version, variant=label):
            self.assertNotIn("catalog_order_mismatch", health["quarantine_reason_counts"])
            self.assertEqual(health["quarantine_reason_counts"], {})
            self.assertEqual(exposed, baseline - {PROBE_TOOL})
            self.assertNotEqual(health["admission_status"], "admitted_exact")
            self.assertEqual(counters.origin["tools/call"], 0)
            self.assertEqual(counters.executions, 0)

    def assert_structure_blocked(self, version: str, tools, *, label: str):
        health, exposed, counters, _url = self._run(version, tools)
        with self.subTest(version=version, variant=label):
            self.assertEqual(health["admission_status"], "blocked_incompatible_upstream")
            self.assertEqual(health["compatibility_status"], "incompatible")
            self.assertIn("catalog_structure_invalid", health["quarantine_reason_counts"])
            self.assertLessEqual(exposed, frozenset({OPTIONAL_READER}))
            self.assertNotIn(PROBE_TOOL, exposed)
            self.assertEqual(counters.origin["tools/call"], 0)
            self.assertEqual(counters.executions, 0)

    def assert_tool_quarantined(self, version: str, tools, baseline, *, reason: str):
        health, exposed, counters, _url = self._run(version, tools)
        with self.subTest(version=version, reason=reason):
            self.assertEqual(health["admission_status"], "partially_admitted")
            self.assertEqual(health["compatibility_status"], "partial")
            self.assertEqual(health["quarantine_reason_counts"], {reason: 1})
            self.assertEqual(exposed, baseline - {PROBE_TOOL})
            self.assertEqual(counters.origin["tools/call"], 0)
            self.assertEqual(counters.executions, 0)

    def test_91_catalog_order_wire_and_runtime_admitted_across_pages(self):
        wire = self._baseline("9.1.0", WIRE_91)
        runtime = self._baseline("9.1.0", RUNTIME_91)
        self.assertEqual(len(RUNTIME_91), len(_ORDER_91))
        self.assertEqual(runtime, wire - {OPTIONAL_READER})

    def test_91_catalog_order_resorted_pages_admitted(self):
        # Codex F1 (PR #406 verdict 6731785026): the runtime binds each used
        # tool, not the catalog order. The CI lanes keep the strict compare.
        for tools in (WIRE_91, RUNTIME_91):
            n = len(tools)
            baseline = self._baseline("9.1.0", tools)
            self.assert_admitted_as(
                "9.1.0", sorted(tools, key=lambda t: t["name"]), baseline, label=f"alpha{n}"
            )
            self.assert_admitted_as("9.1.0", _paged_resort(tools), baseline, label=f"per_page{n}")
            self.assert_admitted_as("9.1.0", _boundary_swap(tools), baseline, label=f"boundary{n}")
            self.assert_admitted_as("9.1.0", tuple(reversed(tools)), baseline, label=f"reversed{n}")

    def test_91_catalog_missing_used_read_withheld_alone(self):
        baseline = self._baseline("9.1.0", RUNTIME_91)
        index = _index(RUNTIME_91, PROBE_TOOL)
        self.assert_only_probe_missing(
            "9.1.0", RUNTIME_91[:index] + RUNTIME_91[index + 1 :], baseline, label="missing"
        )

    def test_91_unused_tool_changes_admitted(self):
        for version, tools in (("9.1.0", WIRE_91),):
            baseline = self._baseline(version, tools)
            index = _index(tools, "ha_restart")
            self.assert_admitted_as(
                version, tools[:index] + tools[index + 1 :], baseline, label="missing_unused"
            )
            self.assert_admitted_as(
                version, _changed(tools, "ha_restart", _append_description), baseline,
                label="changed_unused",
            )
            extra = dict(tools[_index(tools, PROBE_TOOL)], name="ha_synthetic_unreviewed_extra")
            for position in (0, PAGE_SIZE, len(tools)):
                self.assert_admitted_as(
                    version, tuple(tools[:position]) + (extra,) + tuple(tools[position:]),
                    baseline, label=f"extra_at_{position}",
                )

    def test_91_catalog_duplicate_and_changed_descriptor_quarantined(self):
        baseline = self._baseline("9.1.0", WIRE_91)
        self._quarantine_cases("9.1.0", WIRE_91, baseline)

    def test_91_catalog_duplicate_name_refused(self):
        # Sections 4 item 3 and 8.2: a strict-full catalog has no duplicate
        # names. A repeated read route or a repeated non-read reviewed name
        # (here the typed-write dependency ha_call_service) blocks every stock
        # read; nothing is called.
        for name in (PROBE_TOOL, "ha_call_service"):
            index = _index(WIRE_91, name)
            self.assert_structure_blocked(
                "9.1.0", tuple(WIRE_91) + (WIRE_91[index],), label=f"duplicate:{name}"
            )

    def test_85_catalog_order_parity(self):
        # Architect ruling 2026-11-08 (relayed): the Codex F1 lift is 9.1.0
        # only. 8.5.0 keeps its pre-existing order gate exactly: a reorder or
        # an absent reviewed tool, used or unused, still quarantines.
        baseline = self._baseline("8.5.0", CATALOG_85)
        self.assert_blocked(
            "8.5.0", sorted(CATALOG_85, key=lambda t: t["name"]), label="alpha"
        )
        self.assert_blocked("8.5.0", _paged_resort(CATALOG_85), label="per_page")
        self.assert_blocked("8.5.0", tuple(reversed(CATALOG_85)), label="reversed")
        index = _index(CATALOG_85, PROBE_TOOL)
        self.assert_blocked(
            "8.5.0", CATALOG_85[:index] + CATALOG_85[index + 1 :], label="missing"
        )
        index = _index(CATALOG_85, "ha_restart")
        self.assert_blocked(
            "8.5.0", CATALOG_85[:index] + CATALOG_85[index + 1 :], label="missing_unused"
        )
        self._quarantine_cases("8.5.0", CATALOG_85, baseline)

    def test_85_tolerated_unused_changes_unchanged(self):
        # Pre-existing 8.5.0 behavior, unchanged by #406: a changed unused
        # descriptor and an unreviewed extra name never entered the order gate.
        baseline = self._baseline("8.5.0", CATALOG_85)
        self.assert_admitted_as(
            "8.5.0", _changed(CATALOG_85, "ha_restart", _append_description), baseline,
            label="changed_unused",
        )
        extra = dict(CATALOG_85[_index(CATALOG_85, PROBE_TOOL)], name="ha_synthetic_unreviewed_extra")
        for position in (0, PAGE_SIZE, len(CATALOG_85)):
            self.assert_admitted_as(
                "8.5.0", tuple(CATALOG_85[:position]) + (extra,) + tuple(CATALOG_85[position:]),
                baseline, label=f"extra_at_{position}",
            )

    def _quarantine_cases(self, version, tools, baseline):
        index = _index(tools, PROBE_TOOL)
        if version != "9.1.0":
            # 8.5 keeps its released per-tool duplicate quarantine; 9.1 refuses
            # the whole catalog (test_91_catalog_duplicate_name_refused).
            self.assert_tool_quarantined(
                version, tuple(tools) + (tools[index],), baseline,
                reason="duplicate_tool_descriptor",
            )
        self.assert_tool_quarantined(
            version, _changed(tools, PROBE_TOOL, _append_description), baseline,
            reason="description_semantics_mismatch",
        )
        self.assert_tool_quarantined(
            version, _changed(tools, PROBE_TOOL, _flip_read_only), baseline,
            reason="annotation_mismatch",
        )
        self.assert_tool_quarantined(
            version, _changed(tools, PROBE_TOOL, _extra_property), baseline,
            reason="input_schema_mismatch",
        )

    def test_dashboard_descriptor_change_refused_both_versions(self):
        """The dashboard provider refuses a change to its own reviewed descriptor.

        It does not apply the gateway's global catalog-order check (8.5.0
        behavior, unchanged); that check lives in the gateway only.
        """

        def retitle(tool):
            tool["annotations"]["title"] = "Synthetic changed title"

        runtime = "upstream_runtime_contract_mismatch"
        security = "upstream_security_contract_mismatch"
        expected = {
            "9.1.0": {
                "description": runtime,
                "read_only": runtime,
                "input_schema": runtime,
                "title": runtime,
            },
            # 8.5.0's dashboard profile has no title rule; since HAMCP-319's
            # exact used-tool admission (2026-11-07) the whole-descriptor
            # release runtime contract refuses it instead of admitting it.
            "8.5.0": {
                "description": runtime,
                "read_only": security,
                "input_schema": "upstream_input_contract_mismatch",
                "title": runtime,
            },
        }
        mutations = {
            "description": _append_description,
            "read_only": _flip_read_only,
            "input_schema": _extra_property,
            "title": retitle,
        }
        catalogs = {"9.1.0": WIRE_91, "8.5.0": CATALOG_85}
        for version, rows in expected.items():
            tools = catalogs[version]
            ok_error, *_ = asyncio.run(_dashboard(version, tools))
            self.assertIsNone(ok_error, repr(ok_error))
            for label, category in rows.items():
                changed = _changed(tools, "ha_config_get_dashboard", mutations[label])
                error, _health, counters, url = asyncio.run(_dashboard(version, changed))
                with self.subTest(version=version, change=label):
                    self.assertEqual(_failure_category(error), category)
                    self.assertEqual(counters.origin["tools/call"], 0)
                    self.assertEqual(counters.destination_total(), 0)
                    for sink in _sinks(error, None):
                        self.assertNotIn(url, sink)


if __name__ == "__main__":
    unittest.main()
