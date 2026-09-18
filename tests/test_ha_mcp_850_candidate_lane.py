"""Offline controls for the candidate synthetic exact-image acceptance lane."""
import importlib.util
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assessment850", ROOT / "scripts/ha_mcp_850_container_acceptance.py")
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_ACTIONS="true", GITHUB_REPOSITORY="jeter-1/hass-mcp-admin",
                        GITHUB_REF="refs/pull/999/merge", GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1",
                        GITHUB_EVENT_NAME="pull_request", GITHUB_JOB="exact-addon-runtime-acceptance",
                        GITHUB_SHA="a" * 40)

    def test_both_architectures(self):
        for arch in ("amd64", "arm64"):
            self.assertEqual(lane.execution_guard(self.env, arch), "h850-1234-1-" + arch)

    def test_refuses_other_repositories_branches_and_non_ci(self):
        for field, value in (("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/unrelated"),
                             ("GITHUB_ACTIONS", "false"), ("GITHUB_JOB", "another"), ("GITHUB_SHA", "bad"), ("GITHUB_RUN_ID", "../x"),
                             ("GITHUB_RUN_ATTEMPT", "1\n")):
            with self.subTest(field=field), self.assertRaises(lane.Refusal):
                lane.execution_guard({**self.env, field: value}, "amd64")

    def test_missing_identity_refuses(self):
        for field in self.env:
            env = self.env.copy()
            del env[field]
            with self.subTest(field=field), self.assertRaises(lane.Refusal):
                lane.execution_guard(env, "amd64")

    def test_unsupported_architecture_refuses(self):
        with self.assertRaises(lane.Refusal):
            lane.execution_guard(self.env, "arm/v7")

    def test_relay_import_at_its_actual_root_mount(self):
        namespace = {"__name__": "relay_import_probe", "__file__": "/assessment.py"}
        source = (ROOT / "scripts/ha_mcp_850_container_acceptance.py").read_text()
        exec(compile(source, "/assessment.py", "exec"), namespace)
        self.assertEqual(namespace["ROOT"], Path("/"))


class CatalogTests(unittest.TestCase):
    def test_complete_multiple_pages(self):
        a, b = {"name": "first"}, {"name": "second"}
        self.assertEqual(lane.complete_catalog([{"tools": [a], "nextCursor": "next"}, {"tools": [b]}]), [a, b])

    def test_incomplete_chain_refuses(self):
        for pages in ([{"tools": [], "nextCursor": "unread"}],
                      [{"tools": []}, {"tools": []}],
                      [{"tools": [], "nextCursor": "same"}, {"tools": [], "nextCursor": "same"}, {"tools": []}]):
            with self.subTest(pages=pages), self.assertRaises(lane.Refusal):
                lane.complete_catalog(pages)

    def test_duplicates_refuse(self):
        with self.assertRaisesRegex(lane.Refusal, "catalog_duplicate"):
            lane.complete_catalog([{"tools": [{"name": "same"}, {"name": "same"}]}])

    def test_malformed_tool_refuses(self):
        for tools in ([None], [{}], [{"name": 1}], [{"name": ""}]):
            with self.subTest(tools=tools), self.assertRaises(lane.Refusal):
                lane.complete_catalog([{"tools": tools}])

    def test_bounds_refuse(self):
        for pages in ([], [{"tools": []}] * 17,
                      [{"tools": [{"name": str(i)} for i in range(513)]}],
                      [{"tools": [{"name": "large", "description": "x" * lane.MAX_BYTES}]}]):
            with self.assertRaises(lane.Refusal):
                lane.complete_catalog(pages)

    def test_512_tools_are_complete(self):
        self.assertEqual(len(lane.complete_catalog([{"tools": [{"name": str(i)} for i in range(512)]}])), 512)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    """Actual relay HTTP/WS handlers with disposable loopback peers, never HA."""
    async def asyncSetUp(self):
        import aiohttp
        from aiohttp import web
        from aiohttp.test_utils import TestServer

        self.body = b"synthetic-response\n" * 10000
        backend = web.Application()
        async def chunked(request):
            response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
            await response.prepare(request)
            for start in range(0, len(self.body), 4096):
                await response.write(self.body[start:start + 4096])
            await response.write_eof()
            return response
        async def service(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer synthetic-only")
            return web.json_response({"received": await request.json()})
        async def websocket(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.send_json({"type": "auth_required"})
            self.assertEqual(await socket.receive_json(), {"type": "auth", "access_token": "synthetic-only"})
            await socket.send_json({"type": "auth_ok"})
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await socket.send_str(message.data)
            return socket
        backend.router.add_get("/api/stream", chunked)
        backend.router.add_post("/api/services/fan/turn_on", service)
        backend.router.add_get("/api/websocket", websocket)
        self.backend = TestServer(backend)
        await self.backend.start_server()
        self.relay = TestServer(lane.create_relay_app())
        await self.relay.start_server()
        original = self.relay.app["client"]
        await original.close()
        client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))
        backend_url = self.backend.make_url
        class MappedClient:
            # Test-only transport substitution: runtime targets stay closed.
            def url(self, target):
                if not target.startswith("http://core:8123/"):
                    raise AssertionError("Unexpected relay destination")
                return backend_url(target[len("http://core:8123"):])
            def request(self, method, target, **kwargs):
                return client.request(method, self.url(target), **kwargs)
            def ws_connect(self, target, **kwargs):
                return client.ws_connect(self.url(target), **kwargs)
            async def close(self):
                await client.close()
        self.relay.app["client"] = MappedClient()
        self.front = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))

    async def asyncTearDown(self):
        await self.front.close()
        await self.relay.close()
        await self.backend.close()

    async def test_rest_reads_complete_chunked_response(self):
        async with self.front.get(self.relay.make_url("/core/api/stream")) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), self.body)

    async def test_exact_service_route_and_count(self):
        async with self.front.post(self.relay.make_url("/core/api/services/fan/turn_on"),
                                   headers={"Authorization": "Bearer synthetic-only"},
                                   json={"entity_id": lane.FAN}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"received": {"entity_id": lane.FAN}})
        async with self.front.get(self.relay.make_url("/_assessment/stats")) as response:
            self.assertEqual((await response.json())["service_posts"], 1)

    async def test_supervisor_websocket_route_auth_and_cleanup(self):
        async with asyncio.timeout(5):
            async with self.front.ws_connect(self.relay.make_url("/core/websocket")) as socket:
                self.assertEqual(await socket.receive_json(), {"type": "auth_required"})
                await socket.send_json({"type": "auth", "access_token": "synthetic-only"})
                self.assertEqual(await socket.receive_json(), {"type": "auth_ok"})
                command = {"id": 1, "type": "call_service", "domain": "fan"}
                await socket.send_json(command)
                self.assertEqual(await socket.receive_json(), command)
            for _ in range(30):
                async with self.front.get(self.relay.make_url("/_assessment/stats")) as response:
                    stats = await response.json()
                if stats.get("active_websockets") == 0:
                    break
                await asyncio.sleep(0)
            self.assertEqual(stats["active_websockets"], 0)
            self.assertEqual(stats["call_service"], 1)

    async def test_unrelated_supervisor_routes_refuse(self):
        for path in ("/addons/self/options", "/core/other", "/api/websocket", "/core/apiextra"):
            async with self.front.get(self.relay.make_url(path)) as response:
                self.assertEqual(response.status, 404)


class EvidenceTests(unittest.TestCase):
    def test_small_structured_and_text_success(self):
        expected = {"success": True, "data": {"state": "off"}}
        self.assertEqual(lane.payload({"structuredContent": expected}), expected)
        self.assertEqual(lane.payload({"content": [{"type": "text", "text": json.dumps(expected)}]}), expected)

    def test_tool_errors_do_not_become_success(self):
        for value in ({"isError": True}, {"structuredContent": {"success": False}},
                      {"structuredContent": {"data": "not-success"}}):
            with self.assertRaises(lane.Refusal):
                lane.payload(value)

    def test_state_uses_its_existing_metadata_envelope(self):
        expected = {"data": {"entity_id": lane.FAN, "state": "off"}, "metadata": {}}
        self.assertEqual(lane.payload({"structuredContent": expected}, "ha_get_state"), expected)
        with self.assertRaises(lane.Refusal):
            lane.payload({"structuredContent": expected}, "ha_call_service")
        for invalid in ({"data": {}}, {"data": {"entity_id": "other", "state": "off"}},
                        {**expected, "success": False}):
            with self.assertRaises(lane.Refusal):
                lane.payload({"structuredContent": invalid}, "ha_get_state")

    def test_existing_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            lane.save(folder, "receipt.json", {"status": "FAIL"})
            before = (folder / "receipt.json").read_bytes()
            with self.assertRaises(FileExistsError):
                lane.save(folder, "receipt.json", {"status": "PASS"})
            self.assertEqual((folder / "receipt.json").read_bytes(), before)

    def test_oversized_evidence_is_not_written(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(lane.Refusal, "evidence_bound"):
                lane.save(Path(name), "huge.json", {"data": "x" * lane.MAX_BYTES})
            self.assertEqual(list(Path(name).iterdir()), [])

    def test_command_failures_do_not_expose_captured_output(self):
        with patch.object(lane.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "SYNTHETIC_SECRET", "SYNTHETIC_SECRET")):
            with self.assertRaises(lane.Refusal) as error:
                lane.docker("pull", "fixed")
            self.assertEqual(str(error.exception), "docker_command_failed")

    def test_command_timeout_is_finite(self):
        with patch.object(lane.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                lane.docker("info", timeout=15)
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            self.assertEqual(run.call_args.args[0][:3], ["docker", "--host", "unix:///var/run/docker.sock"])


class CleanupTests(unittest.TestCase):
    identity = "h850-1234-1-amd64"

    def test_diagnostics_export_only_selected_markers(self):
        def fake(*args, **kwargs):
            if args[0] == "logs":
                return subprocess.CompletedProcess([], 0, "PermissionError SYNTHETIC_SECRET arbitrary message", "")
            value = self.identity if ".Config.Labels" in args[2] else json.dumps({"Running": False, "ExitCode": 1, "OOMKilled": False})
            return subprocess.CompletedProcess([], 0, value, "")
        with patch.object(lane, "docker", side_effect=fake):
            result = lane.startup_diagnostics(self.identity)
            self.assertEqual(len(result), 4)
            self.assertTrue(all(r["known_markers"] == ["permission_error"] for r in result))
            self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_diagnostics_refuse_foreign_resources(self):
        with patch.object(lane, "docker", return_value=subprocess.CompletedProcess([], 0, "foreign", "")) as docker:
            with self.assertRaises(lane.Refusal):
                lane.startup_diagnostics(self.identity)
            self.assertEqual(docker.call_count, 1)

    def test_invalid_resource_names_never_reach_docker(self):
        with patch.object(lane, "docker") as docker:
            with self.assertRaises(lane.Refusal):
                lane.cleanup("other-container")
            docker.assert_not_called()

    def test_foreign_owner_is_not_stopped(self):
        with patch.object(lane, "docker", return_value=subprocess.CompletedProcess([], 0, "foreign\n", "")) as docker:
            with self.assertRaisesRegex(lane.Refusal, "cleanup_owner_mismatch"):
                lane.cleanup(self.identity)
            self.assertEqual(docker.call_count, 1)

    def test_absence_is_checked_against_available_daemon(self):
        def fake(*args, **kwargs):
            return subprocess.CompletedProcess([], 1 if args[0] == "inspect" or args[:2] == ("network", "inspect") else 0, "", "")
        with patch.object(lane, "docker", side_effect=fake) as docker:
            records = lane.cleanup(self.identity)
            self.assertEqual(len(records), 4)
            self.assertTrue(all(r["status"] == "absent" for r in records))
            self.assertFalse(any(c.args[0] in ("stop", "rm") for c in docker.call_args_list))

    def test_daemon_failure_does_not_claim_cleanup(self):
        def fake(*args, **kwargs):
            if args[0] == "info":
                raise lane.Refusal("docker_command_failed")
            return subprocess.CompletedProcess([], 1, "", "")
        with patch.object(lane, "docker", side_effect=fake), self.assertRaises(lane.Refusal):
            lane.cleanup(self.identity)

    def test_retained_resources_refuse(self):
        def fake(*args, **kwargs):
            if args[0] == "inspect" or args[:2] == ("network", "inspect"):
                return subprocess.CompletedProcess([], 1, "", "")
            return subprocess.CompletedProcess([], 0, "remaining" if args[0] == "ps" else "", "")
        with patch.object(lane, "docker", side_effect=fake), self.assertRaisesRegex(lane.Refusal, "containers_retained"):
            lane.cleanup(self.identity)


class WorkflowTests(unittest.TestCase):
    def test_matrix_installs_locked_contract_dependencies_before_loading_registry(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        steps = workflow["jobs"]["prepare_exact_image_matrix"]["steps"]
        install = next(i for i, step in enumerate(steps)
                       if step.get("name") == "Install reviewed registry runtime dependencies")
        derive = next(i for i, step in enumerate(steps)
                      if step.get("id") == "reviewed-registry")
        self.assertLess(install, derive)
        self.assertIn("--require-hashes --only-binary=:all:", steps[install]["run"])
        self.assertIn("-r hass_mcp_engineering_beta/requirements.lock", steps[install]["run"])
        result = subprocess.run([sys.executable, "scripts/review_upstream_read_release.py", "ci-matrix"],
                                cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = json.loads(result.stdout)["include"]
        self.assertEqual({entry["upstream_version"] for entry in entries},
                         {"7.14.1", "7.14.2", "8.0.0", "8.1.0", "8.1.1", "8.2.0", "8.4.1", "8.4.3", "8.5.0"})
        candidate = next(entry for entry in entries if entry["upstream_version"] == "8.5.0")
        self.assertEqual(candidate["source_commit"], "311d6dc273fb4e9a5b8cde0de15f69472a64fe44")

    def test_closed_scope_and_always_cleanup(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["exact-addon-runtime-acceptance"]
        lanes = [x for x in job["strategy"]["matrix"]["include"] if x["upstream_version"] == "8.5.0"]
        self.assertEqual({x["architecture"] for x in lanes}, {"amd64", "arm64"})
        self.assertEqual({x["runner"] for x in lanes}, {"ubuntu-latest", "ubuntu-24.04-arm"})
        self.assertEqual(job["timeout-minutes"], 20)
        self.assertIn("exact-addon-runtime-acceptance", workflow["jobs"]["validate"]["needs"])
        for step in job["steps"]:
            if "uses" in step:
                self.assertRegex(step["uses"].split()[0], r"@[a-f0-9]{40}$")
            if step.get("name", "").startswith(("Always reconcile", "Remove disposable private", "Retain bounded")):
                self.assertEqual(step["if"], "always() && matrix.upstream_version == '8.5.0'")
        self.assertNotIn("secrets.", json.dumps(workflow))
        self.assertNotIn("packages: write", json.dumps(workflow))

    def test_inputs_are_digest_pinned_and_complete(self):
        pins = json.loads(lane.PINS.read_bytes())
        self.assertEqual(pins["version"], "8.5.0")
        self.assertEqual(pins["upstream_source"], "311d6dc273fb4e9a5b8cde0de15f69472a64fe44")
        self.assertRegex(pins["core_image"], r"2026\.9\.2@sha256:[a-f0-9]{64}$")
        self.assertEqual(set(pins["images"]), {"amd64", "arm64"})
        for images in pins["images"].values():
            self.assertEqual(set(images), {"standalone", "addon"})
            for image in images.values():
                self.assertRegex(image["image"], r"@sha256:[a-f0-9]{64}$")
                for field in ("configuration", "manifest"):
                    self.assertRegex(image[field], r"^sha256:[a-f0-9]{64}$")
