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
