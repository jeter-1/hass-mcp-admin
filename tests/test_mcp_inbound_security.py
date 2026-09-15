"""Production-boundary security tests; all endpoints and data are synthetic."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.configuration import Settings
from ha_mcp_engineering.mcp_server import create_mcp_server
from ha_mcp_engineering.routing import AuthenticatedMcpGateway
from ha_mcp_engineering import configuration, inbound_security as policy
from ha_mcp_engineering.application import validate_settings
from ha_mcp_engineering.errors import ConfigurationError
from ha_mcp_engineering.inbound_topology_observer import create_mcp_listener, BoundaryObserver, Recorder
from ha_mcp_engineering.observability import METRICS
from ha_mcp_engineering.request_context import current_telemetry
from tests.same_thread_asgi_client import SameThreadAsgiTestClient

SECRET = "synthetic-inbound-security-secret"


def settings_for(directory, **extra):
    return Settings(
        ha_url="http://synthetic-ha.invalid", ha_token="synthetic-unused-token",
        access_secret=SECRET, port=8100,
        audit_path=str(Path(directory) / "audit.jsonl"),
        rate_limit_per_minute=1000, rate_limit_burst=100,
        destructive_services=frozenset(), **extra,
    )


class ProductionBoundaryTests(unittest.TestCase):
    def fixture(self, directory, **extra):
        settings = settings_for(directory, **extra)
        server = create_mcp_server(settings)
        calls = []

        @server.tool()
        async def synthetic_read() -> str:
            calls.append("read")
            return "synthetic-read-ok"

        inner = server.streamable_http_app()
        gateway = AuthenticatedMcpGateway(
            inner, settings, AuditLogger(settings.audit_path, SECRET),
        )
        client = SameThreadAsgiTestClient(
            gateway, lifespan_app=inner, base_url="http://127.0.0.1:8100",
        )
        return gateway, client, calls

    def call(self, client, headers=None, secret=SECRET):
        return client.post(
            "/" + secret + "/mcp",
            headers={"accept": "application/json, text/event-stream", **(headers or {})},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "synthetic_read", "arguments": {}}},
        )

    def test_hostile_host_refused_before_synthetic_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            _, client, calls = self.fixture(directory)
            with client:
                response = self.call(client, {"host": "hostile.example.invalid"})
            self.assertEqual(response.status_code, 421)
            self.assertEqual(calls, [])
            self.assertTrue(client.loop_closed)
            self.assertEqual(client.pending_task_count, 0)

    def test_hostile_origin_refused_before_synthetic_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            _, client, calls = self.fixture(directory)
            with client:
                response = self.call(client, {"origin": "https://hostile.example.invalid"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(calls, [])
            self.assertTrue(client.loop_closed)
            self.assertEqual(client.pending_task_count, 0)

    def test_native_client_succeeds_and_wrong_secret_still_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            _, client, calls = self.fixture(directory)
            with client:
                response = self.call(client)
                wrong = self.call(client, secret="synthetic-wrong-secret")
            self.assertEqual(response.status_code, 200)
            self.assertIn("synthetic-read-ok", response.text)
            self.assertEqual(wrong.status_code, 404)
            self.assertEqual(calls, ["read"])
            self.assertEqual(client.pending_task_count, 0)

    def test_configured_browser_and_alias_preserve_useful_success(self):
        with tempfile.TemporaryDirectory() as directory:
            _, client, calls = self.fixture(
                directory, mcp_allowed_hosts=("engineering.example.invalid:8100",),
                mcp_allowed_origins=("https://client.example.invalid",),
            )
            with client:
                response = self.call(client, {"host": "ENGINEERING.example.invalid.:8100",
                                              "origin": "https://CLIENT.example.invalid:443"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(calls, ["read"])
            self.assertEqual(client.pending_task_count, 0)

    def test_sdk_policy_stays_single_owned_at_outer_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_mcp_server(settings_for(directory))
            self.assertIsNone(server.settings.transport_security)
            gateway, client, calls = self.fixture(directory)
            with client:
                for path in ["/health", "/ready", "/mcp", "/" + SECRET + "/mcp/"]:
                    response = client.get(path, headers={"host": "hostile.example.invalid"})
                    self.assertEqual(response.status_code, 421)
            self.assertEqual(calls, [])


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy.compile_policy(None, (), 8100)

    def check(self, host=b"127.0.0.1:8100", origin=None):
        headers = [] if host is None else [(b"host", host)]
        if origin is not None:
            headers.append((b"origin", origin))
        return self.policy.check(headers)

    def test_default_hosts_and_nondefault_listener_port(self):
        for value in [b"127.0.0.1:8100", b"[::1]:8100", b"LOCALHOST.:8100"]:
            self.assertIsNone(self.check(value))
        other = policy.compile_policy(None, (), 9000)
        self.assertIsNone(other.check([(b"host", b"localhost:9000")]))
        self.assertEqual(other.check([(b"host", b"localhost:8100")]).status, 421)

    def test_configured_internal_lan_tunnel_dns_and_ipv6_authorities(self):
        aliases = ("engineering-internal:8100", "192.0.2.25:8100",
                   "tunnel.example.invalid", "[2001:db8::1]:8100")
        compiled = policy.compile_policy(aliases, (), 8100)
        for value in aliases:
            self.assertIsNone(compiled.check([(b"host", value.encode())]))
        self.assertIsNone(compiled.check([(b"host", b"[2001:0db8:0:0:0:0:0:1]:8100")]))
        self.assertEqual(compiled.check([(b"host", b"127.0.0.1:8100")]).status, 421)

    def test_host_port_absence_is_not_implicit_alias(self):
        compiled = policy.compile_policy(("example.invalid",), (), 8100)
        self.assertIsNone(compiled.check([(b"host", b"example.invalid")]))
        for value in [b"example.invalid:80", b"example.invalid:443", b"example.invalid:8100"]:
            self.assertEqual(compiled.check([(b"host", value)]).status, 421)

    def test_malformed_host_matrix(self):
        for value in [b"", b"localhost ", b"a,b", b"user:pass@host", b"host/path",
                      b"host?x", b"host#x", b"host\\path", b"localhost\r\nx:y",
                      b"\xff", b"local_host", b"a..b", b"a..", b"-a", b"a-",
                      b"127.1", b"0177.0.0.1", b"2130706433", b"0x7f000001",
                      b"127.0.0.1.", b"::1", b"[::1]suffix", b"[::1", b"[host]",
                      b"[fe80::1%eth0]", b"localhost:", b"localhost:0",
                      b"localhost:65536", b"localhost:-1", b"localhost:1:2",
                      b"localhost:*", b"a" * 513]:
            with self.subTest(value=value):
                self.assertEqual(self.check(value).status, 400)

    def test_hostile_valid_hosts_fail_without_suffix_matching(self):
        for value in [b"localhost.evil.invalid:8100", b"evil.invalid", b"127.0.0.2:8100",
                      b"localhost:8101", b"localhost", b"[::2]:8100"]:
            self.assertEqual(self.check(value).status, 421)

    def test_missing_and_duplicate_host_fail_even_if_equal(self):
        self.assertEqual(self.check(None).status, 400)
        for second in [b"localhost:8100", b"evil.invalid"]:
            denial = self.policy.check([(b"Host", b"localhost:8100"), (b"HOST", second)])
            self.assertEqual(denial.status, 400)

    def test_missing_origin_allowed_but_any_present_origin_default_refused(self):
        self.assertIsNone(self.check())
        for value in [b"", b"null", b"https://client.example.invalid", b"\xff", b"a" * 513]:
            self.assertEqual(self.check(origin=value).status, 403)

    def test_origin_exact_scheme_host_and_effective_port(self):
        self.policy = policy.compile_policy(None, ("https://client.example.invalid",), 8100)
        for value in [b"https://client.example.invalid", b"HTTPS://CLIENT.example.invalid.:443"]:
            self.assertIsNone(self.check(origin=value))
        for value in [b"http://client.example.invalid", b"https://client.example.invalid:444",
                      b"https://client.example.invalid.evil.invalid", b"https://client.example.invalid/",
                      b"https://user:pass@client.example.invalid", b"https://client.example.invalid?x",
                      b"https://client.example.invalid#x", b"file://client.example.invalid",
                      b"https://client.example.invalid https://evil.invalid", b"null", b""]:
            self.assertEqual(self.check(origin=value).status, 403)

    def test_origin_duplicate_and_precedence(self):
        self.policy = policy.compile_policy(None, ("https://client.example.invalid",), 8100)
        self.assertEqual(self.policy.check([(b"origin", b"https://client.example.invalid"),
                                           (b"ORIGIN", b"https://client.example.invalid")]).status, 403)
        self.assertEqual(self.check(host=b"malformed:port", origin=b"null").status, 403)

    def test_header_envelope_bounds_and_malformed_entries(self):
        headers = [(b"host", b"localhost:8100")] + [(b"x-unused", b"value")] * 255
        self.assertIsNone(self.policy.check(headers))
        self.assertEqual(self.policy.check(headers + [(b"x-last", b"value")]).status, 431)
        for value in [None, {}, "headers", [None], [(b"host",)], [("host", b"localhost")],
                      [(b"host", "localhost")], [(b"bad name", b"value")],
                      [(b"x" * 257, b"value")], [(b"", b"value")]]:
            self.assertEqual(self.policy.check(value).status, 400)

    def test_forwarded_host_origin_and_scheme_do_not_grant_authority(self):
        bad = [(b"host", b"evil.invalid"), (b"x-forwarded-host", b"localhost:8100"),
               (b"forwarded", b"host=localhost:8100;proto=https"), (b"x-forwarded-proto", b"https")]
        self.assertEqual(self.policy.check(bad).status, 421)
        good = [(b"host", b"localhost:8100"), (b"x-forwarded-host", b"evil.invalid")]
        self.assertIsNone(self.policy.check(good))

    def test_configuration_refuses_nonlists_empty_hosts_and_nested_values(self):
        for hosts, origins in [((), ()), ([], ()), ("localhost", ()), ((1,), ()),
                               (([],), ()), (("localhost",), None), (("localhost",), "https://a")]:
            with self.subTest(hosts=hosts, origins=origins), self.assertRaises(ValueError):
                policy.compile_policy(hosts, origins, 8100)

    def test_configuration_bound_and_semantic_duplicates(self):
        hosts = tuple(f"host-{i}.example.invalid" for i in range(32))
        self.assertEqual(policy.compile_policy(hosts, (), 8100).summary()["host_count"], 32)
        for h, o in [(hosts + ("extra.invalid",), ()), (("a" * 513,), ()),
                     (("EXAMPLE.invalid", "example.invalid."), ()),
                     (("[::1]:8100", "[0:0:0:0:0:0:0:1]:8100"), ()),
                     (("localhost",), ("https://a.invalid", "https://a.invalid:443"))]:
            with self.assertRaises(ValueError):
                policy.compile_policy(h, o, 8100)

    def test_dns_label_limits_and_valid_port_edges(self):
        name = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
        for host in [name, name + ".", "example.invalid:1", "example.invalid:65535"]:
            self.assertIsNone(policy.compile_policy((host,), (), 8100).check([(b"host", host.encode())]))
        for host in [name + "x", "a" * 64 + ".invalid", "*.invalid"]:
            with self.assertRaises(ValueError):
                policy.compile_policy((host,), (), 8100)

    def test_settings_and_policy_representations_omit_host_values(self):
        value = "private-synthetic.example.invalid"
        s = settings_for("/tmp", mcp_allowed_hosts=(value,))
        compiled = policy.compile_policy(s.mcp_allowed_hosts, (), 8100)
        self.assertNotIn(value, repr(s))
        self.assertNotIn(value, repr(compiled))
        self.assertNotIn(value, json.dumps(compiled.summary()))


class ConfigurationTests(unittest.TestCase):
    def loaded(self, options, port="8100"):
        with patch.object(configuration, "_read_options", return_value=options), patch.dict(
            os.environ, {"HA_TOKEN": "synthetic-token", "ACCESS_SECRET": SECRET,
                         "MCP_PORT": port}, clear=True,
        ):
            return configuration.load_settings()

    def test_absent_options_use_port_bound_defaults(self):
        s = self.loaded({}, "9000")
        validate_settings(s)
        self.assertIsNone(policy.compile_policy(s.mcp_allowed_hosts, s.mcp_allowed_origins, s.port)
                          .check([(b"host", b"localhost:9000")]))

    def test_json_lists_are_frozen_and_explicit_empty_hosts_refuse(self):
        options = {"mcp_allowed_hosts": ["engineering.example.invalid:8100"]}
        s = self.loaded(options)
        options["mcp_allowed_hosts"].append("evil.invalid")
        self.assertEqual(s.mcp_allowed_hosts, ("engineering.example.invalid:8100",))
        with self.assertRaises(ConfigurationError):
            validate_settings(self.loaded({"mcp_allowed_hosts": []}))

    def test_invalid_options_never_coerce_or_expose_values(self):
        for value in [None, "synthetic-sensitive", 1, {}, [1], [["nested"]], ["x" * 513]]:
            for name in ["mcp_allowed_hosts", "mcp_allowed_origins"]:
                with self.subTest(name=name, kind=type(value).__name__):
                    with self.assertRaises(ConfigurationError) as error:
                        self.loaded({name: value})
                    self.assertNotIn("synthetic-sensitive", str(error.exception))

    def test_invalid_policy_refuses_main_before_application_or_async_run(self):
        from ha_mcp_engineering import application
        for options in [{"mcp_allowed_hosts": None}, {"mcp_allowed_hosts": ["https://bad.invalid"]}]:
            with patch.object(configuration, "_read_options", return_value=options), \
                    patch.dict(os.environ, {"HA_TOKEN": "synthetic-token", "ACCESS_SECRET": SECRET}, clear=True), \
                    patch.object(application, "configure_logging"), patch.object(application, "log_event") as log, \
                    patch.object(application.asyncio, "run") as run, self.assertRaises(SystemExit):
                application.main()
            run.assert_not_called()
            self.assertNotIn("bad.invalid", str(log.call_args_list))

    def test_metadata_defaults_match_compiled_policy(self):
        import yaml
        metadata = yaml.safe_load((ROOT / "hass_mcp_engineering_beta/config.yaml").read_text())
        self.assertEqual(tuple(metadata["options"]["mcp_allowed_hosts"]), policy.default_hosts(8100))
        self.assertEqual(metadata["options"]["mcp_allowed_origins"], [])
        for key in ["mcp_allowed_hosts", "mcp_allowed_origins"]:
            self.assertEqual(metadata["schema"][key], ["str"])

    def test_valid_startup_reports_counts_without_host_or_origin_values(self):
        from ha_mcp_engineering import application
        s = settings_for("/tmp", mcp_allowed_hosts=("private-synthetic.example.invalid",),
                         mcp_allowed_origins=("https://client-synthetic.example.invalid",))
        # Close the unstarted coroutine; no listener, worker or provider runs.
        with patch.object(application, "load_settings", return_value=s), \
                patch.object(application, "configure_logging"), \
                patch.object(application, "log_event") as log, \
                patch.object(application.asyncio, "run", side_effect=lambda coroutine: coroutine.close()):
            application.main()
        context = log.call_args.kwargs["context"]
        self.assertEqual(context["mcp_inbound_policy"], {
            "enabled": True, "configuration_valid": True, "source": "configured_hosts",
            "host_count": 1, "origin_count": 1,
        })
        self.assertNotIn("private-synthetic.example.invalid", str(log.call_args_list))
        self.assertNotIn("client-synthetic.example.invalid", str(log.call_args_list))


class EarlyRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_paths_and_methods_refuse_before_body_sdk_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            inner = AsyncMock(side_effect=AssertionError("sdk_forbidden"))
            core = Mock()
            gateway = AuthenticatedMcpGateway(inner, s, AuditLogger(s.audit_path, SECRET), core_runtime=core)
            for path in ["/health", "/ready", "/mcp", "/" + SECRET + "/mcp", "/" + SECRET + "/mcp/"]:
                for method in ["POST", "GET", "DELETE", "OPTIONS"]:
                    receive = AsyncMock(side_effect=AssertionError("body_forbidden"))
                    send = AsyncMock()
                    await gateway({"type": "http", "path": path, "method": method,
                                   "headers": [(b"host", b"evil.invalid"),
                                               (b"x-request-id", b"synthetic-request-id")]}, receive, send)
                    self.assertEqual(send.call_args_list[0].args[0]["status"], 421)
                    self.assertIn((b"x-request-id", b"synthetic-request-id"), send.call_args_list[0].args[0]["headers"])
                    receive.assert_not_awaited()
                    self.assertIsNone(current_telemetry())
            inner.assert_not_awaited()

            self.assertEqual(core.mock_calls, [])
            self.assertEqual(len(gateway.clients), 0)
            self.assertEqual(len(gateway.auth_failures), 0)

    async def test_malformed_scope_headers_refuse_without_secondary_header_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            inner = AsyncMock()
            gateway = AuthenticatedMcpGateway(inner, s, AuditLogger(s.audit_path, SECRET))
            for headers in [None, {}, [None], [(b"x-request-id",)], [(b"x" * 257, b"v")]]:
                receive, send = AsyncMock(), AsyncMock()
                await gateway({"type": "http", "headers": headers}, receive, send)
                self.assertEqual(send.call_args_list[0].args[0]["status"], 400)
                receive.assert_not_awaited()
            inner.assert_not_awaited()

    async def test_refusal_cancellation_and_logging_failure_clear_context(self):
        from ha_mcp_engineering import routing
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            gateway = AuthenticatedMcpGateway(AsyncMock(), s, AuditLogger(s.audit_path, SECRET))
            for error in [asyncio.CancelledError(), RuntimeError("synthetic-send-failure")]:
                with self.assertRaises(type(error)):
                    await gateway({"type": "http", "headers": []}, AsyncMock(), AsyncMock(side_effect=error))
                self.assertIsNone(current_telemetry())
            with patch.object(routing, "log_event", side_effect=RuntimeError("synthetic-log-failure")):
                with self.assertRaises(RuntimeError):
                    await gateway({"type": "http", "headers": []}, AsyncMock(), AsyncMock())
            self.assertIsNone(current_telemetry())

    async def test_repeated_denials_have_bounded_accounting_and_no_secret_output(self):
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            gateway = AuthenticatedMcpGateway(AsyncMock(), s, AuditLogger(s.audit_path, SECRET))
            before = METRICS.snapshot()["provider_routing"]
            with self.assertLogs(gateway.logger, level="INFO") as logs:
                for _ in range(20):
                    send = AsyncMock()
                    await gateway({"type": "http", "path": "/" + SECRET,
                                   "headers": [(b"host", b"private-marker.invalid"),
                                               (b"authorization", b"Bearer synthetic-bearer")]}, AsyncMock(), send)
                    self.assertLess(len(send.call_args_list[1].args[0]["body"]), 64)
            records = [json.loads(line) for line in Path(s.audit_path).read_text().splitlines()]
            self.assertEqual(len(records), 20)
            self.assertTrue(all(r["event"] == "inbound_request_rejected" for r in records))
            for secret in [SECRET, "private-marker.invalid", "synthetic-bearer"]:
                self.assertNotIn(secret, json.dumps(records) + str(logs.output))
            self.assertEqual(len(logs.output), 20)
            self.assertEqual(METRICS.snapshot()["provider_routing"], before)
            self.assertFalse(gateway.clients or gateway.auth_failures)

    async def test_armed_observer_cannot_bypass_enforcement(self):
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            inner = AsyncMock()
            gateway = AuthenticatedMcpGateway(inner, s, AuditLogger(s.audit_path, SECRET))
            marker = "topo-" + "a" * 32
            recorder = Recorder(marker, "0" * 40, lambda: "0" * 40)
            wrapped = BoundaryObserver(gateway, recorder)
            send = AsyncMock()
            await wrapped({"type": "http", "method": "POST", "server": ("192.0.2.2", 8100),
                           "client": ("192.0.2.3", 1234),
                           "headers": [(b"host", b"evil.invalid"),
                                       (b"x-engineering-topology-probe", marker.encode()),
                                       (b"x-engineering-topology-request", b"1")]}, AsyncMock(), send)
            self.assertEqual(send.call_args_list[0].args[0]["status"], 421)
            self.assertEqual(len(recorder.records), 1)
            inner.assert_not_awaited()

    async def test_lifespan_delegates_and_websocket_does_not_reach_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            s = settings_for(directory)
            inner = AsyncMock()
            gateway = AuthenticatedMcpGateway(inner, s, AuditLogger(s.audit_path, SECRET))
            await gateway({"type": "lifespan"}, AsyncMock(), AsyncMock())
            inner.assert_awaited_once()
            inner.reset_mock()
            send = AsyncMock()
            await gateway({"type": "websocket"}, AsyncMock(), send)
            send.assert_awaited_once_with({"type": "websocket.close", "code": 1008})
            inner.assert_not_awaited()

class HttpParserTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, config, raw):
        from uvicorn.protocols.http.h11_impl import H11Protocol
        from uvicorn.server import ServerState

        class Transport(asyncio.Transport):
            def __init__(self):
                self.data = bytearray()
                self.closed = False

            def get_extra_info(self, name, default=None):
                return {"sockname": ("192.0.2.2", 8100),
                        "peername": ("192.0.2.3", 1234)}.get(name, default)

            def write(self, data):
                self.data.extend(data)

            def close(self):
                self.closed = True

            def is_closing(self):
                return self.closed

            def pause_reading(self):
                pass

            def resume_reading(self):
                pass

        state, transport = ServerState(), Transport()
        protocol = H11Protocol(config, state, app_state={})
        protocol.connection_made(transport)
        try:
            protocol.data_received(raw)
            if state.tasks:
                await asyncio.wait_for(asyncio.gather(*list(state.tasks)), timeout=2)
        finally:
            protocol.connection_lost(None)
            transport.close()
        self.assertFalse(state.connections)
        self.assertFalse(state.tasks)
        self.assertIsNone(protocol.timeout_keep_alive_task)
        return bytes(transport.data)

    def config(self, gateway):
        from ha_mcp_engineering import inbound_topology_observer as observer
        with patch.object(observer, "prepare_observation", return_value=None), \
                patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "*"}):
            server = create_mcp_listener(gateway, port=8100, log_level="error")
        self.assertFalse(server.config.proxy_headers)
        self.assertEqual(server.config.host, "0.0.0.0")
        server.config.http = "h11"
        server.config.ws = "none"
        server.config.lifespan = "off"
        server.config.log_config = None
        server.config.load()
        return server.config

    async def test_actual_factory_http_parser_health_auth_and_header_refusals(self):
        from ha_mcp_engineering.application import create_application
        with tempfile.TemporaryDirectory() as directory:
            gateway = create_application(settings_for(directory))
            config = self.config(gateway)
            cases = [
                (b"GET /health HTTP/1.1\r\nHost: localhost:8100\r\n", 200),
                (b"GET /health HTTP/1.1\r\nHost: evil.invalid\r\n", 421),
                (b"GET /ready HTTP/1.1\r\nHost: localhost:8100\r\nOrigin: null\r\n", 403),
                (b"POST /synthetic-wrong-secret HTTP/1.1\r\nHost: localhost:8100\r\nContent-Length: 0\r\n", 404),
                (b"GET /health HTTP/1.1\r\n", 400),
                (b"GET /health HTTP/1.1\r\nHost: localhost:8100\r\nHost: localhost:8100\r\n", 400),
                (b"GET /health HTTP/1.1\r\nHost: localhost:8100\r\nOrigin: \r\n", 403),
                (b"GET /health HTTP/1.1\r\nHost: localhost:8100\r\nOrigin: null\r\nOrigin: null\r\n", 403),
            ]
            with patch.object(gateway._core_runtime, "acquire", side_effect=AssertionError("authority_forbidden")) as core, \
                    patch("socket.socket.connect", side_effect=AssertionError("network_forbidden")):
                for raw, expected in cases:
                    response = await self.exchange(config, raw + b"Connection: close\r\n\r\n")
                    self.assertEqual(int(response.split(b" ", 2)[1]), expected)
                core.assert_not_called()

    async def test_proxy_environment_cannot_replace_direct_peer_or_scheme(self):
        seen = []

        async def app(scope, receive, send):
            seen.append((scope["client"], scope["scheme"]))
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        config = self.config(app)
        response = await self.exchange(config, b"GET / HTTP/1.1\r\nHost: localhost:8100\r\n"
                                       b"X-Forwarded-For: 192.0.2.99\r\nX-Forwarded-Proto: https\r\n"
                                       b"Connection: close\r\n\r\n")
        self.assertIn(b"204", response.split(b"\r\n", 1)[0])
        self.assertEqual(seen, [(("192.0.2.3", 1234), "http")])
