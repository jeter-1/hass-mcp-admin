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
