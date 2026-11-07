"""HAMCP-319 Phase 2 section 5.1: the three acceptance configurations.

Design docs/HAMCP_319_PHASE2_DESIGN.md section 5.1 names these tests, which
this module implements with the production code (real registry, real
``UpstreamReadGateway``, real ``UpstreamDashboardProvider``, real no-redirect
transport, real typed power service) against loopback ``RedirectPeer`` peers:

* ``test_85_config_corpus_still_accepted`` (8.5-only): the section 3.2 rows
  5-17 (``legacy`` values) are accepted at configuration time, exactly as
  today; Step 1 only labels them. Kills "Make Step 1 refuse at configuration
  time" (section 8.4).
* ``test_85_useful_calls_per_row`` (8.5-only): on **both** accepted URL forms,
  one useful authorized call per 8.5.0 ledger row that the gateway serves
  (rows 1-28), each answered by its committed fixture and attributed to
  8.5.0. Kills "Drop the 8.5.0 registry entry".
* ``test_91_strict_admission_positive`` (9.1-only): on a ``strict_full`` value
  the gateway admits 9.1.0 exactly from the reviewed registry (9.1.0 in
  ``reviewed_supported_versions``, complete catalog in wire order across
  cursor pages), exposes the 9.1 ledger surface without row 15, serves a
  useful read, and the dashboard provider admits the same catalog. Kills
  "Drop the 9.1.0 registry entry".
* ``test_side_by_side_stage_sequence`` (side by side): one gateway, one
  Engineering build with both registry entries, the configuration generation
  changed without restart through A -> B1 -> B2 -> rollback with the suffix
  left -> rollback with the suffix removed -> Engineering rollback.
* ``test_rollback_record_not_redispatched`` (side by side): a typed power task
  written under 9.1.0 (terminal and non-terminal) is read and reconciled under
  an 8.5.0 configuration, displayed by its recorded 9.1.0 contract, and never
  re-dispatched.

``test_generation_change_closes_retained_worker`` (the third side-by-side
test) lives in tests/test_hamcp319_endpoint_selector.py.

Fixture provenance (committed evidence only, never hand-edited):

* Rows 1-11, 13, 14, 16, 17, 19-24 (8.5.0 per-row calls): the decoded
  standalone records of tests/fixtures/ha_mcp_8_6_0/read-results.json
  (provenance.json in that directory), replayed on the 8.5.0 policy exactly as
  tests/test_hamcp319_parity_config.py already does for rows 1-11.
* Row 25 (8.5.0) and the 9.1.0 delegated read: the cases of
  tests/test_hamcp319_no_redirect_useful.py (8.5.0 blueprint capture and the
  9.1.0 row 1 class S fixture).
* Rows 26-28 (8.5.0): tests/fixtures/hamcp129/results-8.5.0.json (see
  tests/fixtures/hamcp129/provenance.json) with the public arguments of
  tests/test_hamcp129_platform_reads.py ``FIXTURE_PUBLIC``.
* Typed power: the TW-area templates through
  ``test_hamcp319_typed_writes.Peer``.

The Engineering-rollback stage is modelled by this build with the 9.1.0 entry
removed from the loaded registry; the older Engineering binary itself is not
executed here. Loopback peers only; every secret is synthetic.
"""

from __future__ import annotations

import asyncio
import json
import socketserver
import sys
import tempfile
import unittest
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.application import validate_settings  # noqa: E402
from ha_mcp_engineering.clients.ha_mcp_endpoint import (  # noqa: E402
    LEGACY,
    UPSTREAM_CATALOG_MODE_UNADMITTED,
    classify_endpoint_form,
    snapshot_is_current,
)
from ha_mcp_engineering.configuration import (  # noqa: E402
    parse_upstream_dashboard_endpoint,
)
from ha_mcp_engineering.power.contracts import POWER_RELEASES, digest  # noqa: E402
from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService  # noqa: E402
from ha_mcp_engineering.providers import upstream_platform_reads as platform  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_power import PowerProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from support.ha_mcp_redirect_peer import ALL_CANARIES, RedirectPeer  # noqa: E402
import test_hamcp319_no_redirect as part1  # noqa: E402
import test_hamcp319_no_redirect_useful as useful  # noqa: E402
import test_hamcp319_typed_writes as typed_fixtures  # noqa: E402
from test_hamcp319_endpoint_selector import ROWS, UPSTREAM_CONFIG_ERROR  # noqa: E402
from tests.test_typed_fan import Clock, Core  # noqa: E402

PAGE_SIZE = 30
DEADLINE = part1.DEADLINE_SECONDS + 5

# Design section 7: the 29 ledger rows, in row order.
LEDGER = (
    "ha_config_get_automation",
    "ha_config_get_calendar_events",
    "ha_config_get_category",
    "ha_config_get_label",
    "ha_config_get_scene",
    "ha_config_get_script",
    "ha_config_list_dashboard_resources",
    "ha_config_list_groups",
    "ha_config_list_helpers",
    "ha_eval_template",
    "ha_get_automation_traces",
    "ha_get_device",
    "ha_get_entity",
    "ha_get_entity_exposure",
    "ha_get_hacs_info",
    "ha_get_history",
    "ha_get_overview",
    "ha_get_skill_guide",
    "ha_get_state",
    "ha_get_todo",
    "ha_get_zone",
    "ha_list_floors_areas",
    "ha_list_services",
    "ha_search",
    "ha_get_blueprint",
    "ha_get_app",
    "ha_get_integration",
    "ha_get_system_health",
    "ha_config_get_yaml",
)
ROW = {name: index for index, name in enumerate(LEDGER, start=1)}
HACS = "ha_get_hacs_info"  # row 15, H on 9.1.0
YAML = "ha_config_get_yaml"  # row 29, optional; its own provider path
# Rows whose 8.5.0 disposition is a bounded projection or redaction rather
# than byte-identical pass-through (row 12 section 6.5 projection, row 15 the
# released 8.5 hacs projection, row 18 the shared acknowledgement redaction).
PROJECTED_85 = {"ha_get_device", HACS, "ha_get_skill_guide"}

_READS_86 = json.loads(
    (ROOT / "tests/fixtures/ha_mcp_8_6_0/read-results.json").read_text("utf-8")
)["standalone"]
CAPTURED = {entry["public_name"]: entry for entry in _READS_86}
_PLATFORM_85 = json.loads(
    (ROOT / "tests/fixtures/hamcp129/results-8.5.0.json").read_text("utf-8")
)
# Public arguments as tests/test_hamcp129_platform_reads.py FIXTURE_PUBLIC.
PLATFORM_CASES = {
    platform.APP: ("apps_installed", {}),
    platform.INTEGRATION: ("integrations_list", {}),
    platform.HEALTH: ("health_baseline", {}),
}
# Rows 1-24 except row 15, and rows 26-28 must succeed complete.
# hamcp129 FIXTURE_EXPECT: apps_installed and integrations_list are partial
# (string_truncated) by the released bounded projection.
PLATFORM_PARTIAL = {platform.APP, platform.INTEGRATION}


def _settings(url: str):
    return replace(part1._settings(url), ha_mcp_release_registry_enabled=False)


def _peer(version: str, form: str, **kwargs) -> RedirectPeer:
    return RedirectPeer(
        version=version,
        form=form,
        tools=useful.reviewed_catalog(version),
        page_size=PAGE_SIZE,
        **kwargs,
    )


def _delegated_case(version: str):
    # The case builder never reads instance state.
    return useful.NoRedirectUseful._delegated_read_case(None, version)


async def _run_tool(gateway, name: str, arguments: dict) -> dict:
    _telemetry, token = begin_request("synthetic-hamcp319-side-by-side")
    try:
        tool = gateway._registered_tool_registry.snapshot()[name]
        return json.loads(await tool.run(deepcopy(arguments)))
    finally:
        end_request(token)


def _list_pages(counters) -> int:
    return counters.origin["tools/list:first"] + counters.origin["tools/list:cursor"]


class _Assertions:
    def assert_no_canary(self, value, label):
        text = json.dumps(value, default=repr, sort_keys=True)
        for canary in ALL_CANARIES:
            self.assertNotIn(canary, text, label)

    def assert_useful_read(self, result, *, version, upstream, label, complete=True):
        self.assertIs(result.get("success"), True, f"{label}: {result}")
        metadata = result["metadata"]
        self.assertEqual(metadata["upstream_version"], version, label)
        self.assertEqual(metadata["upstream_tool"], upstream, label)
        self.assertEqual(metadata["fallback"], "none", label)
        self.assertIs(metadata["fallback_occurred"], False, label)
        if complete:
            self.assertEqual(metadata["completeness"], "complete", label)
        self.assert_no_canary(result, label)


# ---------------------------------------------------------------------------
# 8.5-only
# ---------------------------------------------------------------------------
class EightFiveOnlyTests(_Assertions, unittest.TestCase):
    def test_85_config_corpus_still_accepted(self):
        legacy_rows = [row for row in ROWS if 5 <= row[0] <= 17]
        self.assertEqual({row[0] for row in legacy_rows}, set(range(5, 18)))
        for row, value, accepted, form, _rule, _code in legacy_rows:
            with self.subTest(row=row, value=repr(value)):
                self.assertTrue(accepted)
                self.assertEqual(form, LEGACY)
                # Step 0 accepts the value; Step 1 only labels it.
                endpoint = parse_upstream_dashboard_endpoint(value)
                self.assertIsNotNone(endpoint)
                self.assertEqual(classify_endpoint_form(endpoint.url), LEGACY)
                try:
                    validate_settings(part1._settings(value))
                except Exception as exc:  # noqa: BLE001 - inspected below
                    self.assertNotIn(UPSTREAM_CONFIG_ERROR, str(exc))
                    self.assertNotIn("catalog", str(exc).lower())
                # Both delegated consumers configure the value without a
                # configuration-time refusal; no request is made here.
                gateway = UpstreamReadGateway()
                gateway.configure(_settings(value))
                health = gateway.health_snapshot()
                self.assertIs(health["configured"], True)
                self.assertIsNone(health["last_discovery_failure_category"])
                self.assertIsNotNone(gateway._transport)
                provider = UpstreamDashboardProvider()
                provider.configure(part1._settings(value))
                self.assertIsNone(provider.health_snapshot()["operator_remediation"])
        # The served 8.5.0 legacy value and a strict-form value on an 8.5 peer
        # are admitted at runtime as today.
        async def scenario():
            outcomes = []
            for form in ("legacy", "strict_full_like_85"):
                peer = _peer("8.5.0", form)
                with peer.serve():
                    gateway = UpstreamReadGateway()
                    gateway.configure(_settings(peer.url))
                    await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE)
                    outcomes.append((form, gateway.health_snapshot(), peer.counters))
            return outcomes

        for form, health, counters in asyncio.run(scenario()):
            with self.subTest(form=form):
                self.assertEqual(health["admission_status"], "admitted_exact")
                self.assertEqual(health["upstream_server_version"], "8.5.0")
                self.assertEqual(counters.destination_total(), 0)

    def test_85_useful_calls_per_row(self):
        hamcp129_calls = {
            name: _PLATFORM_85[case] for name, (case, _public) in PLATFORM_CASES.items()
        }
        blueprint = _delegated_case("8.5.0")
        upstream_by_public = {
            **{entry["public_name"]: entry["upstream_name"] for entry in _READS_86},
            **{name: fixture["tool"] for name, fixture in hamcp129_calls.items()},
        }

        def handler(name, arguments):
            if name == blueprint.upstream[0]:
                self.assertEqual(arguments, blueprint.upstream[1])
                return deepcopy(blueprint.structured)
            for fixture in hamcp129_calls.values():
                if name == fixture["tool"] and arguments == fixture["arguments"]:
                    return deepcopy(fixture["result"])
            for entry in _READS_86:
                if name == entry["upstream_name"]:
                    return deepcopy(entry["result"]["structuredContent"])
            raise AssertionError(f"unexpected upstream call {name}")

        async def scenario(form):
            peer = _peer("8.5.0", form, call_handler=handler)
            results = {}
            with peer.serve():
                gateway = UpstreamReadGateway()
                gateway.configure(_settings(peer.url))
                await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE)
                health = gateway.health_snapshot()
                exposed = frozenset(gateway._registered_tool_registry.snapshot())
                for name in LEDGER:
                    if name not in exposed:
                        continue
                    if name == "ha_get_blueprint":
                        arguments = blueprint.public_arguments
                    elif name in PLATFORM_CASES:
                        arguments = PLATFORM_CASES[name][1]
                    else:
                        arguments = CAPTURED[name]["arguments"]
                    before = len(peer.calls)
                    result = await asyncio.wait_for(_run_tool(gateway, name, arguments), DEADLINE)
                    results[name] = (result, peer.calls[before:])
            return health, exposed, results, peer.counters

        for form in ("legacy", "strict_full_like_85"):
            health, exposed, results, counters = asyncio.run(scenario(form))
            with self.subTest(form=form, check="admission"):
                self.assertEqual(health["admission_status"], "admitted_exact")
                self.assertEqual(health["upstream_server_version"], "8.5.0")
                self.assertIn("8.5.0", health["reviewed_supported_versions"])
                # Every ledger row the gateway serves on 8.5.0 (rows 1-28);
                # row 29 is the optional reader on its own provider path
                # (test_hamcp319_yaml.test_85_parity_row_29_positive).
                self.assertEqual(sorted(exposed & set(LEDGER)), sorted(set(LEDGER) - {YAML}))
                self.assertEqual(counters.destination_total(), 0)
                self.assertEqual(counters.injected, 0)
            for name in LEDGER[:-1]:
                with self.subTest(form=form, row=ROW[name], tool=name):
                    result, sent = results[name]
                    upstream = (
                        blueprint.upstream[0]
                        if name == "ha_get_blueprint"
                        else upstream_by_public[name]
                    )
                    # Exactly one upstream call, to the row's own 8.5 tool.
                    self.assertEqual([call[0] for call in sent], [upstream])
                    complete = name not in PLATFORM_PARTIAL | {"ha_get_skill_guide"}
                    self.assert_useful_read(
                        result,
                        version="8.5.0",
                        upstream=upstream,
                        label=f"row {ROW[name]} {form}",
                        complete=complete,
                    )
                    if name in CAPTURED and name not in PROJECTED_85 | {"ha_get_blueprint"}:
                        self.assertEqual(sent[0][1], CAPTURED[name]["arguments"])
                        self.assertEqual(result["data"], CAPTURED[name]["decoded"])
                    elif name in PLATFORM_CASES:
                        self.assertEqual(sent[0][1], hamcp129_calls[name]["arguments"])
                        self.assertTrue(result["data"]["completeness"]["status"])
                    elif name == "ha_get_blueprint":
                        listed = result["data"]["data"]["blueprints"]
                        self.assertTrue(listed)
                    else:
                        self.assertTrue(result["data"])


# ---------------------------------------------------------------------------
# 9.1-only
# ---------------------------------------------------------------------------
class NineOneOnlyTests(_Assertions, unittest.TestCase):
    def test_91_strict_admission_positive(self):
        case = _delegated_case("9.1.0")

        def handler(name, arguments):
            self.assertEqual((name, arguments), case.upstream)
            return deepcopy(case.structured)

        async def scenario():
            peer = _peer("9.1.0", "strict_full", call_handler=handler)
            with peer.serve():
                gateway = UpstreamReadGateway()
                gateway.configure(_settings(peer.url))
                await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE)
                health = gateway.health_snapshot()
                exposed = frozenset(gateway._registered_tool_registry.snapshot())
                result = await asyncio.wait_for(
                    _run_tool(gateway, case.public_tool, case.public_arguments), DEADLINE
                )
                listed_pages = _list_pages(peer.counters)
                provider = UpstreamDashboardProvider()
                provider.configure(part1._settings(peer.url))
                error = None
                try:
                    await asyncio.wait_for(provider.refresh_capabilities(), DEADLINE)
                except Exception as exc:  # noqa: BLE001 - recorded outcome
                    error = exc
                dashboard_health = provider.health_snapshot()
            return health, exposed, result, listed_pages, error, dashboard_health, peer

        health, exposed, result, listed_pages, error, dashboard_health, peer = asyncio.run(
            scenario()
        )
        registry = load_reviewed_upstream_release_registry()
        self.assertIn("9.1.0", registry.by_version)
        entry = registry.by_version["9.1.0"]
        self.assertEqual(entry.entry_id, "ha-mcp-v9.1.0-4259f247")
        # Exact admission from the reviewed registry entry.
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["compatibility_status"], "exact")
        self.assertEqual(health["version_status"], "reviewed_exact")
        self.assertEqual(health["upstream_server_version"], "9.1.0")
        self.assertEqual(health["reviewed_upstream_version"], "9.1.0")
        self.assertIs(health["admission_complete"], True)
        self.assertIsNone(health["last_discovery_failure_category"])
        self.assertIn("9.1.0", health["reviewed_supported_versions"])
        self.assertIn("8.5.0", health["reviewed_supported_versions"])
        # The whole catalog was listed in wire order across cursor pages.
        self.assertGreaterEqual(listed_pages, 2)
        self.assertGreaterEqual(peer.counters.origin["tools/list:cursor"], 1)
        # The 9.1 ledger surface: every row the gateway serves except row 15
        # (H) and the optional row 29.
        self.assertEqual(
            sorted(exposed & set(LEDGER)), sorted(set(LEDGER) - {HACS, YAML})
        )
        self.assertNotIn(HACS, exposed)
        self.assert_useful_read(
            result, version="9.1.0", upstream=case.upstream[0], label="9.1 read"
        )
        self.assertEqual(result["data"]["config"], case.structured["config"])
        self.assertEqual(peer.calls[-1], case.upstream)
        # The dashboard provider admits the same strict catalog.
        self.assertIsNone(error, repr(error))
        self.assertIsNone(dashboard_health["operator_remediation"])
        self.assertEqual(peer.counters.destination_total(), 0)
        self.assertEqual(peer.counters.injected, 0)


# ---------------------------------------------------------------------------
# Side by side
# ---------------------------------------------------------------------------
class SideBySideTests(_Assertions, unittest.TestCase):
    def test_side_by_side_stage_sequence(self):
        registry = load_reviewed_upstream_release_registry()
        self.assertIn("8.5.0", registry.by_version)
        self.assertIn("9.1.0", registry.by_version)
        engineering_rollback = replace(
            registry,
            releases=tuple(item for item in registry.releases if item.version != "9.1.0"),
        )
        gateway = UpstreamReadGateway()
        abandoned: list[object] = []
        records: dict[str, dict] = {}

        def track(transport):
            real = transport.abandon

            def abandon():
                abandoned.append(transport)
                real()

            transport.abandon = abandon

        async def stage(name, version, form, *, url=None, release_registry=None,
                        peer_kwargs=None, call=True, stale_tool=None):
            case = _delegated_case(version)

            def handler(tool, arguments):
                self.assertEqual((tool, arguments), case.upstream, name)
                return deepcopy(case.structured)

            peer = _peer(version, form, call_handler=handler, **(peer_kwargs or {}))
            with peer.serve():
                previous = gateway._transport
                kwargs = {"release_registry": release_registry} if release_registry else {}
                gateway.configure(_settings(url(peer) if url else peer.url), **kwargs)
                track(gateway._transport)
                stale = None
                if stale_tool is not None:
                    # A tool object published by the previous generation is
                    # invoked after the configuration changed.
                    _telemetry, token = begin_request("synthetic-hamcp319-stale")
                    try:
                        stale = json.loads(
                            await asyncio.wait_for(stale_tool.run(deepcopy(stale_args)), DEADLINE)
                        )
                    except Exception as exc:  # noqa: BLE001 - recorded outcome
                        stale = exc
                    finally:
                        end_request(token)
                await asyncio.wait_for(gateway.initialize(FastMCP(name)), DEADLINE)
                health = gateway.health_snapshot()
                registry_snapshot = gateway._registered_tool_registry.snapshot()
                result = None
                if call and case.public_tool in registry_snapshot:
                    result = await asyncio.wait_for(
                        _run_tool(gateway, case.public_tool, case.public_arguments), DEADLINE
                    )
                records[name] = {
                    "case": case,
                    "health": health,
                    "exposed": frozenset(registry_snapshot),
                    "tools": registry_snapshot,
                    "result": result,
                    "stale": stale,
                    "counters": peer.counters,
                    "calls": list(peer.calls),
                    "previous": previous,
                    "transport": gateway._transport,
                }

        stale_args: dict = {}

        async def scenario():
            nonlocal stale_args
            await stage("A", "8.5.0", "legacy")
            await stage("B1", "9.1.0", "legacy")
            await stage("B2", "9.1.0", "strict_full")
            b2 = records["B2"]
            stale_args = b2["case"].public_arguments
            # Rollback, suffix left: the 8.5.0 app serves only its legacy path
            # and answers the appended /catalog/full path with 404.
            await stage(
                "rollback_suffix_left",
                "8.5.0",
                "legacy",
                url=lambda peer: peer.url.replace("/mcp?", "/mcp/catalog/full?"),
                peer_kwargs={"unbound_status": 404},
                stale_tool=b2["tools"][b2["case"].public_tool],
            )
            await stage("rollback_suffix_removed", "8.5.0", "legacy")
            # Engineering rollback: this build without its 9.1.0 entry, with
            # the app still on 9.1.0 at the Stage B2 value.
            await stage(
                "engineering_rollback",
                "9.1.0",
                "strict_full",
                release_registry=engineering_rollback,
            )

        with patch.object(socketserver.BaseServer, "handle_error", lambda *_a: None):
            asyncio.run(scenario())

        def admitted(name, version, exposed_count):
            record = records[name]
            health = record["health"]
            with self.subTest(stage=name):
                self.assertEqual(health["admission_status"], "admitted_exact")
                self.assertEqual(health["upstream_server_version"], version)
                self.assertEqual(health["reviewed_upstream_version"], version)
                self.assertEqual(len(record["exposed"] & set(LEDGER)), exposed_count)
                self.assert_useful_read(
                    record["result"],
                    version=version,
                    upstream=record["case"].upstream[0],
                    label=name,
                )
                self.assertEqual(record["calls"], [record["case"].upstream])
                self.assertEqual(record["counters"].destination_total(), 0)

        def refused(name, *, category, listed):
            record = records[name]
            health = record["health"]
            with self.subTest(stage=name):
                self.assertNotEqual(health["admission_status"], "admitted_exact")
                self.assertIs(health["admission_complete"], False)
                self.assertEqual(health["last_discovery_failure_category"], category)
                self.assertEqual(record["exposed"] & set(LEDGER), frozenset())
                self.assertIsNone(record["result"])
                self.assertEqual(record["calls"], [])
                self.assertEqual(record["counters"].executions, 0)
                self.assertEqual(_list_pages(record["counters"]), listed)

        # A: 8.5.0 on its legacy value, exactly as today.
        admitted("A", "8.5.0", 28)
        # B1: 9.1.0 on the unchanged legacy value: refused after initialize and
        # before any list, naming /catalog/full, with no partial surface.
        refused("B1", category=UPSTREAM_CATALOG_MODE_UNADMITTED, listed=0)
        self.assertEqual(records["B1"]["counters"].origin["initialize"], 1)
        self.assertEqual(records["B1"]["counters"].destination_total(), 0)
        # B2: suffix appended; full 9.1 parity surface on the strict form.
        admitted("B2", "9.1.0", 27)
        self.assertNotIn(HACS, records["B2"]["exposed"])
        # Rollback with the suffix left: 8.5.0 answers 404, classified by the
        # existing transport rule as endpoint_rejected, and the delegated
        # surface stays fail-closed (one initialize to the unserved path only).
        left = records["rollback_suffix_left"]
        refused("rollback_suffix_left", category="endpoint_rejected", listed=0)
        self.assertEqual(left["counters"].origin_total(), 0)
        self.assertEqual(dict(left["counters"].destination), {"POST:initialize": 1})
        # The stale 9.1 tool never dispatches against the 8.5 configuration.
        stale = left["stale"]
        if isinstance(stale, dict):
            self.assertIsNot(stale.get("success"), True, stale)
        self.assertEqual(left["calls"], [])
        # Rollback with the suffix removed: back to the Stage A state.
        admitted("rollback_suffix_removed", "8.5.0", 28)
        # Engineering rollback: no 9.1.0 entry, the existing unsupported
        # refusal, zero tools/call.
        rollback = records["engineering_rollback"]
        refused("engineering_rollback", category="upstream_version_mismatch", listed=3)
        self.assertEqual(rollback["health"]["version_status"], "rejected_unreviewed")
        self.assertNotIn("9.1.0", rollback["health"]["reviewed_supported_versions"])
        self.assertIn("8.5.0", rollback["health"]["reviewed_supported_versions"])
        # Every generation change replaced and abandoned the previous
        # transport; the previous snapshot is no longer current.
        order = list(records)
        for earlier, later in zip(order, order[1:]):
            with self.subTest(transition=f"{earlier}->{later}"):
                previous = records[later]["previous"]
                self.assertIs(previous, records[earlier]["transport"])
                self.assertIsNot(records[later]["transport"], previous)
                self.assertIn(previous, abandoned)
                self.assertFalse(snapshot_is_current(previous._snapshot))
                self.assertGreater(
                    records[later]["transport"]._snapshot.generation,
                    previous._snapshot.generation,
                )
        self.assertTrue(snapshot_is_current(records[order[-1]]["transport"]._snapshot))

    def test_rollback_record_not_redispatched(self):
        family = typed_fixtures.FAMILIES["power"]
        contract_91 = POWER_RELEASES["9.1.0"][2]
        self.assertNotEqual(contract_91, POWER_RELEASES["8.5.0"][2])

        async def session(version, form, root, clock, core, *, drop_write=False, task_id=None):
            state = typed_fixtures.Peer("power", version)

            def handler(name, arguments):
                if name == "ha_call_service":
                    if drop_write:
                        # The write reaches the peer but no response returns.
                        raise ConnectionAbortedError("synthetic dropped response")
                    state.apply(arguments)
                return state.respond(name, arguments)

            peer = _peer(version, form, call_handler=handler)
            with peer.serve():
                transport = part1.CLIENTS["power"][0](peer.url)
                provider = PowerProvider(transport, lambda *_args: "synthetic-current")
                service = PowerService(
                    root, provider, PowerCoreAuthority(core), now=clock, sleep=clock.sleep
                )
                telemetry, token = begin_request("synthetic-hamcp319-rollback-record")
                try:
                    if task_id is None:
                        request = family.request(
                            entity_id=family.entity,
                            action=family.action,
                            operation_id=f"{int(clock().timestamp())}-{uuid.uuid4().hex}",
                        )
                        setattr(telemetry, family.binding, digest(request.model_dump()))
                        outcome = await service.control(request)
                    else:
                        before = service.receipt(task_id)
                        # Past every claim and authorization window.
                        clock.advance(3600)
                        reconciled = await service.reconcile(task_id)
                        await service.recover_once()
                        after = service.receipt(task_id)
                        outcome = (before, reconciled, after)
                finally:
                    end_request(token)
                    await service.close()
            return outcome, [name for name, _ in peer.calls]

        async def scenario(drop_write):
            scratch = ROOT / ".artifacts" / "tmp"
            scratch.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=scratch) as root:
                clock, core = Clock(), Core()
                written, calls_91 = await session(
                    "9.1.0", "strict_full", root, clock, core, drop_write=drop_write
                )
                task_id = written["task_id"]
                reads, calls_85 = await session(
                    "8.5.0", "legacy", root, clock, core, task_id=task_id
                )
            return written, calls_91, reads, calls_85

        with patch.object(socketserver.BaseServer, "handle_error", lambda *_a: None):
            outcomes = {drop: asyncio.run(scenario(drop)) for drop in (False, True)}

        for drop_write, (written, calls_91, reads, calls_85) in outcomes.items():
            label = "non_terminal" if drop_write else "terminal"
            with self.subTest(record=label):
                # Written under 9.1.0 with exactly one write dispatch.
                self.assertEqual(written["provider_contract"], contract_91)
                self.assertEqual(calls_91.count("ha_call_service"), 1)
                self.assertTrue(written["dispatch_intent_recorded"])
                self.assertEqual(written["provider_attempt_count"], 1)
                if drop_write:
                    self.assertIs(written["terminal"], False)
                    self.assertFalse(written["provider_response_received"])
                else:
                    self.assertEqual(written["state"], "succeeded_verified")
                # Under the 8.5.0 configuration: displayed by its recorded
                # 9.1.0 contract, reconciled read-only, never re-dispatched.
                self.assertEqual(calls_85.count("ha_call_service"), 0)
                self.assertEqual(calls_85, [])
                for receipt in reads:
                    self.assertEqual(receipt["task_id"], written["task_id"])
                    self.assertEqual(receipt["provider_contract"], contract_91)
                    self.assertIs(receipt["redispatch_prohibited"], True)
                    self.assertEqual(receipt["provider_attempt_count"], 1)
                    self.assertEqual(receipt["operation_hash"], written["operation_hash"])
                final = reads[-1]
                self.assertIs(final["terminal"], True)
                self.assertEqual(
                    final["state"],
                    "manual_review_required" if drop_write else "succeeded_verified",
                )


if __name__ == "__main__":
    unittest.main()
