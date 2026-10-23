"""Offline ownership/refusal checks for the disposable-only CI driver."""
import ast
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dashboard_disposable_driver", ROOT / "scripts/dashboard_analysis_disposable_acceptance.py"
)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


class DisposableOwnershipTests(unittest.TestCase):
    def lane(self, directory):
        lane = object.__new__(driver.Lane)
        lane.out = Path(directory)
        lane.name = "dashboard-gate-0123456789ab"
        lane.label = driver.LABEL_KEY + "=" + lane.name
        lane.core, lane.upstream, lane.seed = [lane.name + x for x in ("-core", "-mcp", "-seed")]
        lane.network, lane.volume = lane.name + "-net", lane.name + "-config"
        lane.commands = []
        lane.attempted = True
        return lane

    def test_cleanup_never_removes_foreign_label_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = self.lane(directory)
            def docker(*args, **kwargs):
                return subprocess.CompletedProcess(args, 0, b"foreign-label\n" if args[1] == "inspect" else b"", b"")
            lane.docker = Mock(side_effect=docker)
            self.assertEqual(lane.cleanup()["status"], "FAIL")
            self.assertFalse(any("rm" in call.args or "stop" in call.args for call in lane.docker.call_args_list))
            self.assertEqual(json.loads((lane.out / "cleanup.json").read_text())["status"], "FAIL")

    def test_cleanup_daemon_failure_is_not_absence_and_has_fixed_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = self.lane(directory)
            lane.docker = Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE_DETAIL"))
            self.assertEqual(lane.cleanup()["status"], "FAIL")
            self.assertNotIn("SYNTHETIC_PRIVATE_DETAIL", (lane.out / "cleanup.json").read_text())

    def test_cleanup_removes_only_exact_owned_resources_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = self.lane(directory)
            existing = {lane.core, lane.upstream, lane.seed, lane.volume, lane.network}
            def docker(*args, **kwargs):
                if args[1] == "inspect":
                    return subprocess.CompletedProcess(args, 0 if args[-1] in existing else 1,
                        (lane.name + "\n").encode() if args[-1] in existing else b"", b"")
                if "rm" in args:
                    self.assertIn(args[-1], existing)
                    existing.remove(args[-1])
                return subprocess.CompletedProcess(args, 0, b"", b"")
            lane.docker = Mock(side_effect=docker)
            self.assertEqual(lane.cleanup()["status"], "PASS")
            self.assertFalse(existing)
            removed = [c.args[-1] for c in lane.docker.call_args_list if "rm" in c.args]
            self.assertCountEqual(removed, [lane.core, lane.upstream, lane.seed, lane.volume, lane.network])
            self.assertEqual(lane.cleanup()["status"], "PASS")
            self.assertEqual(len([c for c in lane.docker.call_args_list if "rm" in c.args]), 5)

    def test_plan_only_cleanup_uses_no_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = self.lane(directory)
            lane.attempted = False
            lane.docker = Mock(side_effect=AssertionError("unexpected Docker"))
            self.assertEqual(lane.cleanup()["status"], "NOT_NEEDED")
            lane.docker.assert_not_called()

    def test_cleanup_cli_refuses_unbound_resource_name_before_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "plan.json").write_text(json.dumps({"label": driver.LABEL_KEY + "=unrelated-resource"}))
            argv = ["driver", "--repo", str(ROOT), "--fixture", directory, "--output", directory, "--cleanup"]
            with patch.object(driver.sys, "argv", argv), patch.object(driver.Lane, "docker") as docker:
                with self.assertRaisesRegex(RuntimeError, "invalid cleanup identity"):
                    driver.main()
                docker.assert_not_called()


class DisposableWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_endpoint_refuses_before_transport_and_restores_hooks(self):
        import httpx
        original = httpx.AsyncHTTPTransport.handle_async_request
        events = {"path": "dashboard-gate", "mcp": [], "native_http": [], "native_ws": []}
        with driver.observe_wire(events, "http://127.0.0.1:1234", "http://127.0.0.1:1235/mcp"):
            with self.assertRaisesRegex(RuntimeError, "unexpected MCP endpoint"):
                await httpx.AsyncHTTPTransport.handle_async_request(None, httpx.Request("POST", "https://synthetic.invalid/mcp"))
        self.assertIs(httpx.AsyncHTTPTransport.handle_async_request, original)
        self.assertFalse(events["mcp"])

    async def test_changed_tool_selector_refuses_before_transport(self):
        import httpx
        events = {"path": "dashboard-gate", "mcp": [], "native_http": [], "native_ws": []}
        endpoint = "http://127.0.0.1:1235/mcp"
        request = httpx.Request("POST", endpoint, json={"method": "tools/call", "params": {
            "name": "ha_call_service", "arguments": {}}})
        with driver.observe_wire(events, "http://127.0.0.1:1234", endpoint):
            with self.assertRaisesRegex(RuntimeError, "selector drift"):
                await httpx.AsyncHTTPTransport.handle_async_request(None, request)
        self.assertFalse(events["mcp"])


class DisposableNetworkTests(unittest.TestCase):
    def fixtures(self, lane):
        network = {"internal": True, "driver": "bridge", "id": "a" * 64,
                   "label": lane.name, "ipam": [{"Subnet": "172.28.0.0/16", "Gateway": "172.28.0.1"}]}
        container = {"running": True, "label": lane.name, "network_count": 1, "ports": None,
                     "network": {"NetworkID": "a" * 64, "IPAddress": "172.28.0.2"}}
        return network, container

    def docker(self, network, container):
        return Mock(side_effect=lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps(network if args[0] == "network" else container).encode(), b""))

    def test_internal_owned_endpoint_needs_no_published_host_port(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = DisposableOwnershipTests().lane(directory)
            network, container = self.fixtures(lane)
            lane.docker = self.docker(network, container)
            self.assertEqual(lane.owned_url(lane.core, 8123), "http://172.28.0.2:8123")
            receipt = json.loads((lane.out / "endpoint-8123.json").read_text())
            self.assertTrue(receipt["internal"])
            self.assertFalse(receipt["published_ports"])
            self.assertEqual(lane.docker.call_count, 2)

    def test_wrong_ownership_topology_or_address_never_yields_an_endpoint(self):
        import copy
        cases = [("network", "internal", False), ("network", "driver", "host"),
                 ("network", "label", "foreign"), ("container", "running", False),
                 ("container", "label", "foreign"), ("container", "network_count", 2),
                 ("container", "ports", {"8123/tcp": [{"HostIp": "0.0.0.0"}]}),
                 ("endpoint", "NetworkID", "b" * 64),
                 *( ("endpoint", "IPAddress", ip) for ip in
                    ["8.8.8.8", "127.0.0.1", "169.254.1.2", "0.0.0.0", "172.29.0.2", "172.28.0.1"])]
        with tempfile.TemporaryDirectory() as directory:
            lane = DisposableOwnershipTests().lane(directory)
            original = self.fixtures(lane)
            for target, key, value in cases:
                with self.subTest(target=target, key=key, value=value):
                    network, container = copy.deepcopy(original)
                    {"network": network, "container": container, "endpoint": container["network"]}[target][key] = value
                    lane.docker = self.docker(network, container)
                    with self.assertRaises(RuntimeError):
                        lane.owned_url(lane.core, 8123)
                    self.assertFalse((lane.out / "endpoint-8123.json").exists())

    def test_unknown_container_or_port_refuses_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = DisposableOwnershipTests().lane(directory)
            lane.docker = Mock(side_effect=AssertionError("unexpected Docker"))
            for name, port in [("unrelated", 8123), (lane.core, 80), (lane.upstream, 8123)]:
                with self.subTest(name=name, port=port), self.assertRaises(RuntimeError):
                    lane.owned_url(name, port)
            lane.docker.assert_not_called()

    def test_failure_locations_retain_no_exception_values_or_locals(self):
        try:
            raise TypeError("SYNTHETIC_PRIVATE_DETAIL")
        except TypeError as error:
            locations = driver.failure_locations(error, ROOT)
        self.assertTrue(locations)
        self.assertNotIn("SYNTHETIC_PRIVATE_DETAIL", json.dumps(locations))
        self.assertTrue(all(set(x) == {"file", "line"} for x in locations))
        self.assertTrue(all(not Path(x["file"]).is_absolute() for x in locations))


class DisposableProjectionTests(unittest.TestCase):
    def projection(self):
        import sys
        sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
        from ha_mcp_engineering.dashboard_analysis.rules import scan
        from ha_mcp_engineering.dashboard_analysis.provider import project_inventory
        states = project_inventory("states", [
            {"entity_id": "light.hamcp_contract_light", "state": "off"},
            {"entity_id": "input_text.dashboard_private", "state": driver.SENTINEL}])
        registry = project_inventory("registry", [])
        result = scan(driver.DASHBOARD, "sha256:" + "a" * 64, states, registry, core_version="2026.9.4")
        header = {"coverage": result["coverage"], "counts": result["counts"],
                  "sources": [states.metadata(), registry.metadata()]}
        return header, result["items"]

    def test_complete_inventories_and_partial_custom_card_semantics_are_distinct(self):
        header, items = self.projection()
        driver.verify_mixed_projection(header, items)
        self.assertNotIn(driver.SENTINEL, json.dumps(items))
        self.assertTrue(all(row["complete"] for row in header["sources"]))
        self.assertEqual(header["coverage"], {"references": "partial", "availability": "partial", "controls": "partial"})

    def test_harness_refuses_incomplete_inventory_and_false_complete_coverage(self):
        import copy
        header, items = self.projection()
        for kind in ("states", "registry"):
            altered = copy.deepcopy(header)
            next(row for row in altered["sources"] if row["kind"] == kind)["complete"] = False
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, "incomplete fixture inventories"):
                driver.verify_mixed_projection(altered, items)
        for category in header["coverage"]:
            altered = copy.deepcopy(header)
            altered["coverage"][category] = "complete"
            with self.subTest(category=category), self.assertRaisesRegex(RuntimeError, "partial coverage"):
                driver.verify_mixed_projection(altered, items)
        for field in ("omitted", "invalid", "duplicate"):
            altered = copy.deepcopy(header)
            altered["sources"][0][field] = 1
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                driver.verify_mixed_projection(altered, items)

    def test_harness_still_requires_exact_pointers_and_useful_findings(self):
        import copy
        header, items = self.projection()
        variants = [[row for row in items if row["kind"] != excluded]
                    for excluded in ("control", "coverage_gap", "entity_reference")]
        moved = copy.deepcopy(items)
        next(row for row in moved if row["kind"] == "entity_reference")["pointer"] = "/invented"
        variants.append(moved)
        unassessed = copy.deepcopy(items)
        next(row for row in unassessed if row.get("entity_id") == "sensor.dashboard_absent")["availability"] = "unassessed"
        variants.append(unassessed)
        for altered in variants:
            with self.assertRaises(RuntimeError):
                driver.verify_mixed_projection(header, altered)


OBSERVER_SPEC = importlib.util.spec_from_file_location("dashboard_interval_observer_fixture",
    ROOT / "tests/fixtures/dashboard_interval_observer/custom_components/dashboard_interval_observer/__init__.py")
observer = importlib.util.module_from_spec(OBSERVER_SPEC)
OBSERVER_SPEC.loader.exec_module(observer)

sys.path[:0] = [str(path) for path in (ROOT / "scripts", ROOT / "hass_mcp_engineering_beta") if str(path) not in sys.path]
import alarmo_inspection_contract_acceptance as alarmo  # noqa: E402
# Loaded here so no replay first imports it inside a test's event loop.
import ha_mcp_engineering.clients.mcp  # noqa: E402,F401
SCRIPT = ROOT / "scripts/dashboard_analysis_disposable_acceptance.py"
SCENARIO = {node.name: node for node in ast.walk(ast.parse(SCRIPT.read_text()))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


class FakeStore:
    def __init__(self, key):
        self.key = key
        self._data = {"private": "DO_NOT_RECORD"}


class DisposableRetryOnceCleanTests(unittest.TestCase):
    """HAMCP-219 review 1: a repairs delayed save reruns a dashboard window once; never tolerated.

    The beta.32 publish failure was a background ``storage_delay_save`` of
    ``repairs.issue_registry``. The observer's origin label cannot prove that
    save was independent (Core's executor and thread-safe callbacks drop the
    request context), so the 2026.9.4 dashboard lane discards that window and
    reruns it once through the alarmo lane's ``observe_retry_once_clean``; the
    rerun verifies strictly. Only the delayed-save kind may ask for the rerun.
    """

    REPAIRS = "repairs.issue_registry"

    def events(self, key, kind="storage_delay_save", *, command=False):
        ledger = observer.Ledger(lambda: 1)
        identity = ledger.start("inspection", {})
        token = observer.REQUEST.set("lovelace/config") if command else None
        try:
            ledger.record(kind, store=FakeStore(key))
        finally:
            if token is not None:
                observer.REQUEST.reset(token)
        value = ledger.finish(identity, {}, {})
        self.assertNotIn("DO_NOT_RECORD", json.dumps(value))
        return value["events"]

    def test_repairs_delayed_save_is_never_tolerated_and_refuses_strictly(self):
        for kind in driver.BACKGROUND_SAVE_KINDS:
            with self.subTest(kind=kind):
                [event] = self.events(self.REPAIRS, kind)
                self.assertEqual(event, {"kind": kind, "origin": "background", "store": self.REPAIRS})
                self.assertFalse(driver.background_bookkeeping(event))
                with self.assertRaisesRegex(RuntimeError, "observed write/flow/service"):
                    driver.window_commands([event])

    def test_only_the_background_delayed_save_may_ask_for_a_rerun(self):
        command = {"kind": "command", "origin": "command", "command": "lovelace/config"}
        [delayed] = self.events(self.REPAIRS)
        self.assertEqual(driver.window_commands([command, delayed, delayed], retry_allowed=True),
                         (["lovelace/config"], 2))
        for name, event in (("save", self.events(self.REPAIRS, "storage_save")[0]),
                            ("write", self.events(self.REPAIRS, "storage_write")[0]),
                            ("command_origin", self.events(self.REPAIRS, command=True)[0]),
                            ("other_repairs", self.events("repairs.other")[0]),
                            ("no_origin", {"kind": "storage_delay_save", "store": self.REPAIRS})):
            with self.subTest(event=name), self.assertRaisesRegex(RuntimeError, "observed write/flow/service"):
                driver.window_commands([delayed, event], retry_allowed=True)
        with self.assertRaises(RuntimeError):
            driver.window_commands([delayed], retry_allowed=1)

    def test_previously_tolerated_background_stores_still_pass(self):
        for key in ("auth", "core.restore_state"):
            with self.subTest(key=key):
                [event] = self.events(key)
                self.assertTrue(driver.background_bookkeeping(event))
                self.assertEqual(driver.window_commands([event]), ([], 0))

    def test_command_origin_save_of_a_tolerated_store_is_refused(self):
        for key in driver.TOLERATED_STORES:
            with self.subTest(key=key):
                [event] = self.events(key, command=True)
                self.assertEqual(event["origin"], "command")
                self.assertFalse(driver.background_bookkeeping(event))

    def test_other_stores_and_non_save_kinds_are_refused(self):
        for key in ("core.entity_registry", "core.config_entries", "alarmo.storage", "repairs.other", "unknown"):
            with self.subTest(key=key):
                [event] = self.events(key)
                self.assertFalse(driver.background_bookkeeping(event))
                self.assertFalse(driver.retryable_delayed_save(event))
        for kind in ("storage_remove", "flow", "service"):
            with self.subTest(kind=kind):
                event = {"kind": kind, "origin": "background", "store": self.REPAIRS}
                self.assertFalse(driver.background_bookkeeping(event))
                self.assertFalse(driver.retryable_delayed_save(event))

    def test_fixture_names_the_retry_store_and_never_hashes_or_tolerates_it(self):
        self.assertEqual(observer.TOLERATED_KEYS, frozenset(driver.TOLERATED_STORES))
        self.assertEqual(observer.TOLERATED_KEYS, {"auth", "core.restore_state"})
        self.assertEqual(observer.RETRY_KEYS, frozenset({driver.RETRY_STORE}))
        self.assertEqual(driver.RETRY_EVENT_KIND, "storage_delay_save")
        self.assertLessEqual(observer.TOLERATED_KEYS | observer.RETRY_KEYS, observer.STORE_KEYS)
        self.assertEqual(observer.HASH_KEYS, ("alarmo.storage", "core.config_entries", "core.device_registry",
                                              "core.entity_registry"))

    def test_pending_repairs_save_holds_start_so_a_rerun_opens_after_its_flush(self):
        import threading
        ledger = observer.Ledger(lambda: 1)
        repairs = FakeStore(self.REPAIRS)
        repairs._write_lock = threading.Lock()
        ledger.record("storage_delay_save", store=repairs)
        self.assertTrue(ledger.pending_setup_storage())
        repairs._data = None
        self.assertFalse(ledger.pending_setup_storage())
        with repairs._write_lock:
            self.assertTrue(ledger.pending_setup_storage())
        auth = FakeStore("auth")
        auth._write_lock = threading.Lock()
        ledger.record("storage_delay_save", store=auth)
        self.assertFalse(ledger.pending_setup_storage())

    def scenario(self):
        import ast
        tree = ast.parse((ROOT / "scripts/dashboard_analysis_disposable_acceptance.py").read_text())
        return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_every_event_refuses_through_the_strict_predicate(self):
        import ast
        checks = [node for node in ast.walk(self.scenario()["window_commands"]) if isinstance(node, ast.Call)
                  and getattr(node.func, "id", None) == "require"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(ast.unparse(checks[0]), "require(background_bookkeeping(event), 'observed write/flow/service')")

    def test_rerun_is_requested_only_after_every_other_check_and_only_through_the_shared_helper(self):
        import ast
        functions = self.scenario()
        # The live window check delegates to the one used by the offline replay (review 2).
        [delegate] = functions["verify_case"].body
        self.assertEqual(delegate.value.func.id, "verify_case_window")
        self.assertEqual([ast.unparse(x) for x in delegate.value.args],
                         ["outcome[2]", "observed", "retry_allowed", "native_reads", "upstream_read"])
        verify = functions["verify_case_window"]
        last = verify.body[-1]
        self.assertEqual(ast.unparse(last.test), "retryable")
        self.assertIsInstance(last.body[0], ast.Raise)
        self.assertEqual(last.body[0].exc.func.id, "RetryableObservation")
        raises = [node for node in ast.walk(verify) if isinstance(node, ast.Raise)]
        self.assertEqual(raises, [last.body[0]])
        calls = [ast.unparse(node) for node in ast.walk(verify) if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "window_commands"]
        self.assertEqual(calls, ["window_commands(observed['events'], retry_allowed=retry_allowed)"])
        helper = [node for node in ast.walk(functions["call"]) if isinstance(node, ast.Call)
                  and getattr(node.func, "id", None) == "observe_retry_once_clean"]
        self.assertEqual(len(helper), 1)
        # No other code path may ask for a lenient window.
        source = (ROOT / "scripts/dashboard_analysis_disposable_acceptance.py").read_text()
        self.assertEqual(source.count("retry_allowed=True"), 0)
        self.assertEqual(source.count("observe_retry_once_clean("), 1)


class DisposableRetainedWindowReplayTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-219 review 2 (M3): retained dashboard windows decide acceptance offline.

    Every scenario case runs through the real ``call``/``verify_case`` bodies of
    the driver (as the reviewer's probe extracts them) with synthetic
    observations; no Core, Docker or network. ``verify_retained_windows`` must
    then reload each kept and discarded window, check it against the digests the
    receipts took from the windows in memory, verify the kept window strictly
    and re-verify the discarded one through the alarmo lane's shared
    ``verify_discarded``. Missing, altered or inconsistent evidence refuses.
    """

    SEQUENCE = ["mixed", "continuation-0", "continuation-1", "invalid", "invalid-cursor", "provider-refusal",
                "retired-new", "retired-continuation"]
    DELAYED = {"kind": "storage_delay_save", "origin": "background", "store": "repairs.issue_registry"}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dashboard-replay-")
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)
        fixture = self.out / "fixture"
        fixture.mkdir()
        (fixture / "__init__.py").write_text("# synthetic observer\n")
        self.lane = SimpleNamespace(out=self.out, fixture=fixture)
        self.observer_sha256 = driver.digest(b"# synthetic observer\n")

    def observation(self, native_reads, upstream_read, extra=()):
        commands = (["lovelace/config"] if upstream_read else []) + (
            ["config/entity_registry/list"] if native_reads else [])
        hashes = {"core.entity_registry": "a" * 64}
        return {"interval_id": uuid.uuid4().hex, "overflow": False, "expired": False, "core_version": "2026.9.4",
                "coverage": {"synthetic": True}, "observer_sha256": self.observer_sha256,
                "baseline_store_hashes": hashes, "final_store_hashes": dict(hashes),
                "events": [{"kind": "command", "origin": "command", "command": x} for x in commands] + list(extra)}

    @staticmethod
    def wire(name, native_reads, upstream_read):
        return {"path": name, "mcp": ["initialize", "notifications/initialized", "tools/list", "tools/call"]
                if upstream_read else [],
                "native_http": ["GET /api/states", "GET /api/websocket"] if native_reads else [],
                "native_ws": ["auth", "config/entity_registry/list"] if native_reads else []}

    async def run_lane(self, retry=("mixed",), *, before_receipt=None):
        """Each case once, or twice when its first window holds a repairs delayed save.

        ``before_receipt(name)`` runs during a rerun, before ``call`` writes that
        case's receipt, as the reviewer's probe alters retained evidence.
        """
        for path in self.out.glob("*.json"):
            path.unlink()
        runs = Counter()

        async def run_case(name, arguments, expected, native_reads, upstream_read):
            runs[name] += 1
            observed = self.observation(native_reads, upstream_read,
                                        [self.DELAYED] if name in retry and runs[name] == 1 else [])
            events = self.wire(name, native_reads, upstream_read)
            if runs[name] == 2 and before_receipt is not None:
                before_receipt(name)
            driver.save(self.out / (name + "-observation.json"), observed)
            driver.save(self.out / (name + "-wire.json"), events)
            value = {"success": expected, "timing": {}}
            return (value, json.dumps(value), events), observed

        namespace = {**vars(driver), "lane": self.lane, "run_case": run_case, "receipts": [], "discarded": [],
                     "observe_retry_once_clean": alarmo.observe_retry_once_clean}
        exec(compile(ast.Module(body=[SCENARIO["verify_case"], SCENARIO["call"]], type_ignores=[]),
                     str(SCRIPT), "exec"), namespace)
        for name in self.SEQUENCE:
            value = await namespace["call"](name, {}, *driver.case_expectation(name))
            self.assertIs(value["success"], driver.case_expectation(name)[0])
        self.assertEqual(sum(runs.values()), len(self.SEQUENCE) + len(retry))
        self.declared = namespace["discarded"]
        return namespace["receipts"]

    def replay(self):
        return driver.verify_retained_windows(self.out, self.observer_sha256)

    def rewrite(self, filename, change, *, rebind=None):
        """Alter one retained file; ``rebind`` also records its new digest in the receipt."""
        path = self.out / filename
        value = json.loads(path.read_text())
        change(value)
        driver.save(path, value)
        if rebind is not None:
            receipts = json.loads((self.out / "case-receipts.json").read_text())
            [receipt] = [x for x in receipts if x["case"] == rebind[0]]
            if rebind[1] == "discarded":
                receipt["discarded_sha256"][rebind[2]] = driver.digest(path.read_bytes())
            else:
                receipt[rebind[2]] = driver.digest(path.read_bytes())
            driver.save(self.out / "case-receipts.json", receipts)

    async def test_clean_control_and_discarded_windows_replay(self):
        receipts = await self.run_lane(retry=())
        self.assertEqual(self.replay(), [])
        self.assertTrue(all(x["discarded_window"] is None and x["discarded_sha256"] is None for x in receipts))
        receipts = await self.run_lane(retry=("mixed", "provider-refusal"))
        self.assertEqual(self.replay(), ["mixed", "provider-refusal"])
        self.assertEqual(self.declared, ["mixed", "provider-refusal"])
        mixed = receipts[0]
        self.assertEqual(mixed["discarded_window"], {"condition": "retryable_delayed_save", "retryable_event_count": 1})
        self.assertEqual(mixed["observation_sha256"], driver.digest((self.out / "mixed-observation.json").read_bytes()))
        self.assertEqual(mixed["discarded_sha256"]["wire_sha256"],
                         driver.digest((self.out / "mixed-discarded-wire.json").read_bytes()))

    async def test_missing_discarded_or_kept_window_fails(self):
        for filename in ("mixed-discarded-observation.json", "mixed-discarded-wire.json",
                         "mixed-observation.json", "mixed-wire.json", "invalid-observation.json"):
            with self.subTest(filename=filename):
                await self.run_lane()
                (self.out / filename).unlink()
                with self.assertRaisesRegex(RuntimeError, "retained window missing"):
                    self.replay()

    async def test_altered_discarded_window_fails(self):
        for filename, change in (
                ("mixed-discarded-observation.json", lambda x: x.update(expired=True)),
                ("mixed-discarded-observation.json", lambda x: x["events"].pop()),
                ("mixed-discarded-wire.json", lambda x: x["native_http"].append("POST /api/services/x/y"))):
            with self.subTest(filename=filename):
                await self.run_lane()
                self.rewrite(filename, change)
                with self.assertRaisesRegex(RuntimeError, "retained window altered"):
                    self.replay()

    async def test_altered_retry_window_fails(self):
        for filename, change in (
                ("mixed-observation.json", lambda x: x["events"].append(self.DELAYED)),
                ("mixed-wire.json", lambda x: x["mcp"].append("tools/call")),
                ("continuation-1-observation.json", lambda x: x.update(core_version="2026.10.0"))):
            with self.subTest(filename=filename):
                await self.run_lane()
                self.rewrite(filename, change)
                with self.assertRaisesRegex(RuntimeError, "retained window altered"):
                    self.replay()

    async def test_non_clean_retry_window_fails_even_with_a_matching_digest(self):
        for change in (lambda x: x["events"].append(self.DELAYED),
                       lambda x: x["events"].append({"kind": "storage_write", "origin": "background",
                                                     "store": "core.entity_registry"})):
            await self.run_lane()
            self.rewrite("mixed-observation.json", change, rebind=("mixed", "kept", "observation_sha256"))
            with self.assertRaisesRegex(RuntimeError, "observed write/flow/service"):
                self.replay()

    async def test_discarded_window_with_a_non_allowlisted_event_fails(self):
        for event in ({"kind": "storage_write", "origin": "command", "store": "core.entity_registry"},
                      {"kind": "storage_write", "origin": "background", "store": "repairs.issue_registry"},
                      {"kind": "storage_save", "origin": "background", "store": "repairs.issue_registry"},
                      {"kind": "storage_delay_save", "origin": "command", "store": "repairs.issue_registry"},
                      {"kind": "service", "origin": "background", "store": "auth"},
                      {"kind": "command", "origin": "command", "command": "config/entity_registry/update"}):
            with self.subTest(event=event):
                await self.run_lane()
                self.rewrite("mixed-discarded-observation.json", lambda x: x["events"].append(event))
                with self.assertRaisesRegex(RuntimeError, "retained window altered"):
                    self.replay()
                # Even when the receipt is rebound to the altered file, the
                # discarded window is re-verified and refused.
                self.rewrite("mixed-discarded-observation.json", lambda x: None,
                             rebind=("mixed", "discarded", "observation_sha256"))
                with self.assertRaises(alarmo.ObservationError) as caught:
                    self.replay()
                self.assertNotIsInstance(caught.exception, alarmo.RetryableObservation)

    async def test_discarded_window_with_an_illegal_wire_request_fails(self):
        for change in (lambda x: x["native_http"].append("POST /api/services/x/y"),
                       lambda x: x["mcp"].append("tools/call")):
            await self.run_lane()
            self.rewrite("mixed-discarded-wire.json", change, rebind=("mixed", "discarded", "wire_sha256"))
            with self.assertRaises(alarmo.ObservationError):
                self.replay()

    async def test_discarded_window_must_be_retryable_with_its_recorded_diagnostic(self):
        await self.run_lane()
        self.rewrite("mixed-discarded-observation.json", lambda x: x["events"].remove(self.DELAYED),
                     rebind=("mixed", "discarded", "observation_sha256"))
        with self.assertRaisesRegex(ValueError, "not retryable"):
            self.replay()
        await self.run_lane()
        self.rewrite("case-receipts.json", lambda x: x[0]["discarded_window"].update(retryable_event_count=2))
        with self.assertRaisesRegex(ValueError, "diagnostic mismatch"):
            self.replay()

    async def test_undeclared_or_inconsistent_evidence_fails(self):
        cases = (
            ("stray discarded window", lambda: (self.out / "invalid-discarded-wire.json").write_text("{}\n"),
             "undeclared discarded window"),
            ("dropped receipt", lambda: self.rewrite("case-receipts.json", lambda x: x.pop(3)),
             "case receipt sequence mismatch"),
            # Only the continuation numbering can refuse these two; the swapped
            # receipts are otherwise intact and consistent with their files.
            ("dropped first continuation", lambda: self.rewrite("case-receipts.json", lambda x: x.pop(1)),
             "case receipt sequence mismatch"),
            ("reordered continuations", lambda: self.rewrite(
                "case-receipts.json", lambda x: x.__setitem__(slice(1, 3), x[2:0:-1])),
             "case receipt sequence mismatch"),
            ("unreceipted window", lambda: self.rewrite("case-receipts.json", lambda x: x.pop(2)),
             "unreceipted retained window"),
            ("unknown window", lambda: (self.out / "extra-wire.json").write_text("{}\n"),
             "unreceipted retained window"),
            ("flipped outcome", lambda: self.rewrite("case-receipts.json", lambda x: x[3].update(success=True)),
             "case outcome mismatch"),
            ("receipt wire", lambda: self.rewrite("case-receipts.json", lambda x: x[0]["wire"].update(path="other")),
             "case receipt wire mismatch"),
            ("missing digests", lambda: self.rewrite("case-receipts.json", lambda x: x[0].update(discarded_sha256=None)),
             "discarded window digests missing"),
            ("reused interval", lambda: self.rewrite(
                "mixed-discarded-observation.json",
                lambda x: x.update(interval_id=json.loads((self.out / "mixed-observation.json").read_text())["interval_id"]),
                rebind=("mixed", "discarded", "observation_sha256")), "observer interval reused"),
            ("observer drift", lambda: (self.lane.fixture / "__init__.py").write_text("# other\n"), None),
        )
        for label, alter, message in cases:
            with self.subTest(label):
                await self.run_lane()
                alter()
                with self.assertRaisesRegex(RuntimeError, message or "observer identity drift"):
                    driver.verify_retained_windows(
                        self.out, driver.digest((self.lane.fixture / "__init__.py").read_bytes()))
                (self.lane.fixture / "__init__.py").write_text("# synthetic observer\n")

    async def test_reviewer_probe_variants_still_reach_receipts_but_fail_the_replay(self):
        def missing(name):
            (self.out / (name + "-discarded-observation.json")).unlink()

        def illegal_event(name):
            path = self.out / (name + "-discarded-observation.json")
            value = json.loads(path.read_text())
            value["events"].append({"kind": "storage_write", "origin": "command", "store": "core.entity_registry"})
            driver.save(path, value)

        def illegal_wire(name):
            driver.save(self.out / (name + "-discarded-wire.json"),
                        {**self.wire(name, 2, 1), "native_http": ["POST /api/services/x/y"]})

        for alter, message in ((missing, "retained window missing"), (illegal_event, "retained window altered"),
                               (illegal_wire, "retained window altered")):
            with self.subTest(variant=alter.__name__):
                receipts = await self.run_lane(before_receipt=alter)
                self.assertEqual(receipts[0]["discarded_window"]["condition"], "retryable_delayed_save")
                with self.assertRaisesRegex(RuntimeError, message):
                    self.replay()

    def test_acceptance_is_written_only_after_the_replay_and_records_it(self):
        scenario = SCENARIO["scenario"]
        statements = list(ast.walk(scenario))
        [replay] = [node for node in statements if isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "verify_retained_windows"]
        self.assertEqual(ast.unparse(replay), "replayed = verify_retained_windows(lane.out, digest((lane.fixture / "
                         "'__init__.py').read_bytes()))")
        [accepted] = [node for node in statements if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "save"
                      and "acceptance.json" in ast.unparse(node.args[0])]
        self.assertLess(replay.lineno, accepted.lineno)
        record = {ast.unparse(k): ast.unparse(v) for k, v in zip(accepted.args[1].keys, accepted.args[1].values)}
        self.assertEqual(record["'status'"], "'PASS'")
        self.assertEqual(record["'discarded_windows'"], "replayed")
        [declared] = [node for node in statements if isinstance(node, ast.Expr) and ast.unparse(node) ==
                      "require(replayed == sorted(discarded), 'discarded window declaration mismatch')"]
        self.assertLess(replay.lineno, declared.lineno)
        self.assertLess(declared.lineno, accepted.lineno)
        # The offline replay and the live calls use one case table.
        calls = [node for node in statements if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "call"]
        self.assertEqual(len(calls), 7)
        for node in calls:
            self.assertEqual(ast.unparse(node.args[-1]).split("(")[0], "*case_expectation")
