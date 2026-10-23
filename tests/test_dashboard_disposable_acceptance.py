"""Offline ownership/refusal checks for the disposable-only CI driver."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


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
        verify = functions["verify_case"]
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
