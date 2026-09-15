import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering import inbound_topology_observer as observer
from ha_mcp_engineering.inbound_topology_observer import (
    BoundaryObserver, MAX_EXPORT_BYTES, MAX_HEADERS, MAX_RECORDS, MARKER_HEADER, REQUEST_HEADER,
    ObservedConfig, Recorder, WINDOW_SECONDS, encoded, project_value,
)

SOURCE = "0" * 40
MARKER = "topo-" + "a" * 32


def scope(headers=None, index=1, **extra):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "scheme": "http", "method": "POST", "path": "/synthetic-private-route",
            "raw_path": b"/synthetic-private-route", "query_string": b"token=synthetic-query-secret",
            "server": ("192.0.2.2", 8100), "client": ("127.0.0.1", 12345),
            "headers": [(MARKER_HEADER, MARKER.encode()), (REQUEST_HEADER, str(index).encode()),
                        *(headers or [])], **extra}


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.time = 100.0
        self.identity = SOURCE
        self.r = Recorder(MARKER, SOURCE, lambda: self.identity, clock=lambda: self.time,
                          utc_now=lambda: "2026-09-15T01:00:00+00:00")

    async def test_records_only_selected_fields(self):
        self.r.capture(scope([(b"host", b"example.invalid:8100"),
                              (b"origin", b"https://client.invalid")]))
        record = self.r.records[0]
        self.assertEqual(record["headers"]["host"]["values"][0]["value"], "example.invalid:8100")
        self.assertEqual(record["immediate_peer"], "127.0.0.1")
        self.assertEqual(record["received_at"], "2026-09-15T01:00:00+00:00")
        self.assertEqual(record["elapsed_ms"], 0)
        self.assertNotIn("path", record)

    async def test_absent_origin_and_host_are_explicit(self):
        self.r.capture(scope())
        self.assertEqual(self.r.records[0]["headers"],
                         {"host": {"count": 0, "values": []}, "origin": {"count": 0, "values": []}})

    async def test_duplicate_values_and_spelling_preserved(self):
        self.r.capture(scope([(b"Host", b"One.invalid"), (b"host", b"two.invalid"),
                              (b"Origin", b"https://first.invalid"), (b"origin", b"https://last.invalid")]))
        h = self.r.records[0]["headers"]
        self.assertEqual(h["host"]["count"], 2)
        self.assertEqual([v["value"] for v in h["host"]["values"]], ["One.invalid", "two.invalid"])
        self.assertEqual(h["origin"]["count"], 2)

    async def test_credentials_paths_queries_and_session_values_excluded(self):
        self.r.capture(scope([(b"authorization", b"synthetic-auth-secret"),
                              (b"cookie", b"synthetic-cookie-secret"),
                              (b"mcp-session-id", b"synthetic-session-secret"),
                              (b"x-forwarded-for", b"synthetic-forwarded-secret")]))
        raw = encoded(self.r.report())
        self.assertNotIn(b"synthetic", raw)
        self.assertEqual(self.r.records[0]["forwarding_header_counts"]["x-forwarded-for"], 1)

    async def test_unsafe_values_retain_only_length_category(self):
        for raw in [b"https://user:password@example.invalid", b"https://example.invalid/path-secret",
                    b"https://example.invalid?secret", b"https://example.invalid#secret", b"null",
                    b"https://example.invalid\n", b"https:\r\n//example.invalid", b"\xff"]:
            with self.subTest(raw=raw):
                result = project_value(b"origin", raw)
                self.assertFalse(result["value_disclosed"])
                self.assertNotIn("value", result)
                self.assertEqual(result["bytes"], len(raw))

    async def test_invalid_host_and_oversized_unicode_are_not_echoed(self):
        for raw in [b"user:secret@example.invalid", b"example.invalid/path", b"x\r\ny",
                    b"a" * 513, "秘密".encode(), b"example.invalid:0", b"example.invalid:65536"]:
            self.assertFalse(project_value(b"host", raw)["value_disclosed"])

    async def test_malformed_bracket_suffix_and_repeated_trailing_dots_not_exported(self):
        for raw in [b"[::1]synthetic-secret", b"example.invalid..", b"[fe80::1%secret]:8100"]:
            self.assertFalse(project_value(b"host", raw)["value_disclosed"])

    async def test_ipv4_and_ipv6_literal_values_are_preserved(self):
        for value in [b"192.0.2.2:8100", b"[2001:db8::1]:8100"]:
            self.assertEqual(project_value(b"host", value)["value"], value.decode())

    async def test_no_marker_or_wrong_marker_collects_nothing(self):
        s = scope();s["headers"] = []
        self.r.capture(s)
        s["headers"] = [(MARKER_HEADER, b"topo-" + b"b" * 32)]
        self.r.capture(s)
        self.assertEqual(self.r.records, [])

    async def test_duplicate_marker_refuses_capture(self):
        self.r.capture(scope([(MARKER_HEADER, MARKER.encode())]))
        self.assertEqual(self.r.failure, "duplicate_marker")
        self.assertEqual(self.r.records, [])

    async def test_wrong_listener_and_lifespan_not_collected(self):
        self.r.capture(scope(server=("192.0.2.2", 8110)))
        self.r.capture({"type": "lifespan"})
        self.assertEqual(self.r.records, [])

    async def test_identity_drift_retires(self):
        self.identity = "1" * 40
        self.r.capture(scope())
        self.assertEqual(self.r.failure, "identity_drift")
        self.identity = SOURCE
        self.r.capture(scope())
        self.assertEqual(self.r.records, [])

    async def test_initial_identity_mismatch_refuses(self):
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            Recorder(MARKER, SOURCE, lambda: "1" * 40)

    async def test_marker_shape_refuses(self):
        with self.assertRaisesRegex(ValueError, "invalid_marker"):
            Recorder("arbitrary-value", SOURCE, lambda: SOURCE)

    async def test_deadline_refuses_late_capture_without_renewal(self):
        self.time += WINDOW_SECONDS
        self.r.capture(scope())
        self.assertEqual(self.r.state, "WINDOW_CLOSED")
        self.time = 100.0
        self.r.capture(scope())
        self.assertEqual(self.r.records, [])

    async def test_retired_capture_does_not_reopen(self):
        self.r.stop("RETIRED")
        self.r.capture(scope())
        self.assertEqual(self.r.state, "RETIRED")
        self.assertEqual(self.r.records, [])

    async def test_record_limit_retains_valid_bounded_export(self):
        for i in range(MAX_RECORDS + 1):
            self.r.capture(scope(index=i + 1))
        self.assertEqual(len(self.r.records), MAX_RECORDS)
        self.assertEqual(self.r.failure, "request_correlation_conflict")
        raw = encoded(self.r.report())
        self.assertLessEqual(len(raw), MAX_EXPORT_BYTES)
        self.assertEqual(len(json.loads(raw)["records"]), MAX_RECORDS)

    async def test_export_byte_limit_retains_prior_records_and_refuses_more(self):
        hostname = b".".join([b"a" * 63, b"b" * 63, b"c" * 63, b"d" * 61])
        pairs = [(b"host", hostname)] * 4 + [(b"origin", b"https://" + hostname)] * 4
        for i in range(MAX_RECORDS):
            self.r.capture(scope(pairs, index=i + 1))
        self.assertEqual(self.r.failure, "export_byte_bound")
        self.assertLess(len(self.r.records), MAX_RECORDS)
        self.assertLessEqual(len(encoded(self.r.report())), MAX_EXPORT_BYTES)
        self.assertTrue(self.r.records)

    async def test_out_of_order_arrival_retains_client_correlation(self):
        self.r.capture(scope(index=2));self.r.capture(scope(index=1))
        self.assertEqual([r["request_index"] for r in self.r.records], [2, 1])
        self.assertEqual([r["ordinal"] for r in self.r.records], [1, 2])

    async def test_duplicate_request_correlation_refuses(self):
        self.r.capture(scope());self.r.capture(scope())
        self.assertEqual(self.r.failure, "request_correlation_conflict")
        self.assertEqual(len(self.r.records), 1)

    async def test_missing_correlation_is_not_filled_in(self):
        s = scope();s["headers"] = [(MARKER_HEADER, MARKER.encode())]
        self.r.capture(s)
        self.assertEqual(self.r.failure, "request_correlation_invalid")

    async def test_duplicate_correlation_refuses(self):
        self.r.capture(scope([(REQUEST_HEADER, b"2")]))
        self.assertEqual(self.r.failure, "request_correlation_invalid")

    async def test_header_count_bound_refuses(self):
        self.r.capture(scope([(b"x", b"y")] * MAX_HEADERS))
        self.assertEqual(self.r.failure, "header_collection_bound")

    async def test_duplicate_value_count_bound_refuses(self):
        self.r.capture(scope([(b"host", b"example.invalid")] * 5))
        self.assertEqual(self.r.failure, "header_value_count_bound")

    async def test_invalid_scope_shape_is_bounded(self):
        s = scope();s["headers"] = [42]
        self.r.capture(s)
        self.assertEqual(self.r.failure, "header_shape")

    async def test_unknown_socket_identity_is_not_invented(self):
        self.r.capture(scope(client=None))
        self.assertEqual(self.r.failure, "socket_identity_unavailable")

    async def test_socket_zone_text_is_never_exported(self):
        self.r.capture(scope(client=("fe80::1%synthetic-secret", 1)))
        self.assertEqual(self.r.failure, "socket_identity_unavailable")
        self.assertNotIn(b"synthetic-secret", encoded(self.r.report()))

    async def test_unexpected_marked_method_retires_capture_only(self):
        app = AsyncMock()
        await BoundaryObserver(app, self.r)(scope(method="PUT"), AsyncMock(), AsyncMock())
        app.assert_awaited_once()
        self.assertEqual(self.r.failure, "unexpected_marked_method")

    async def test_no_claim_of_complete_mcp_collection(self):
        self.r.capture(scope());self.r.stop("SEALED")
        self.assertFalse(self.r.report()["capture_complete"])

    async def test_wrapper_does_not_read_body_or_mutate_scope(self):
        s = scope([(b"host", b"example.invalid")]);before = copy.deepcopy(s)
        app = AsyncMock();receive = AsyncMock();send = AsyncMock()
        await BoundaryObserver(app, self.r)(s, receive, send)
        self.assertEqual(s, before)
        app.assert_awaited_once_with(s, receive, send)
        receive.assert_not_awaited();send.assert_not_awaited()

    async def test_capture_failure_preserves_single_application_invocation(self):
        self.r.capture = lambda _: (_ for _ in ()).throw(RuntimeError("synthetic-secret"))
        self.r.finish = lambda reason: self.r.stop("FAILED", "internal_capture_failure")
        app = AsyncMock()
        await BoundaryObserver(app, self.r)(scope(), AsyncMock(), AsyncMock())
        app.assert_awaited_once()
        self.assertEqual(self.r.failure, "internal_capture_failure")
        self.assertNotIn(b"synthetic-secret", encoded(self.r.report()))

    async def test_cancellation_and_application_failure_are_not_swallowed(self):
        for error in [asyncio.CancelledError(), RuntimeError("application-failure")]:
            app = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await BoundaryObserver(app, self.r)(scope(), AsyncMock(), AsyncMock())
            app.assert_awaited_once()

    async def test_observer_sees_peer_before_real_uvicorn_proxy_middleware(self):
        app = AsyncMock()
        async def endpoint(scope, receive, send):
            return await app(scope, receive, send)
        config = ObservedConfig(endpoint, observation=self.r, host="0.0.0.0", port=8100,
                                interface="asgi3", http="h11", ws="none", lifespan="off",
                                log_config=None, access_log=False, proxy_headers=True,
                                forwarded_allow_ips="127.0.0.1")
        config.load()
        s = scope([(b"host", b"receiver.invalid:8100"), (b"x-forwarded-for", b"192.0.2.55"),
                   (b"x-forwarded-proto", b"https")])
        await config.loaded_app(s, AsyncMock(), AsyncMock())
        self.assertEqual(self.r.records[0]["immediate_peer"], "127.0.0.1")
        app.assert_awaited_once()
        delivered = app.await_args.args[0]
        self.assertEqual(delivered["client"], ("192.0.2.55", 0))
        self.assertEqual(delivered["scheme"], "https")

    async def test_untrusted_forwarding_remains_untrusted(self):
        app = AsyncMock()
        async def endpoint(scope, receive, send):
            return await app(scope, receive, send)
        config = ObservedConfig(endpoint, observation=self.r, host="0.0.0.0", port=8100,
                                interface="asgi3", http="h11", ws="none", lifespan="off",
                                log_config=None, access_log=False, proxy_headers=True,
                                forwarded_allow_ips="192.0.2.99")
        config.load()
        await config.loaded_app(scope([(b"x-forwarded-for", b"192.0.2.55")]),
                                AsyncMock(), AsyncMock())
        self.assertEqual(app.await_args.args[0]["client"], ("127.0.0.1", 12345))




@unittest.skipUnless(sys.platform.startswith("linux"), "Linux add-on descriptor contract")
class PrivateArmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private-topology-probe"
        self.root.mkdir(mode=0o700)
        self.now = datetime(2026, 9, 15, tzinfo=timezone.utc)
        self.clock = 100.0
        self.identity = {"source_sha": SOURCE, "version": "synthetic-version",
                         "built_at": "2026-09-15T00:00:00Z", "dirty": False}
        self.arm = {"schema_version": 1, "capture_id": "a" * 32, "marker": MARKER,
                    "expected_source": SOURCE, "listener_port": 8100,
                    "issued_at": "2026-09-15T00:00:00Z", "expires_at": "2026-09-15T00:05:00Z"}
        self.write_arm()
        self.loop = Mock()
        self.loop.call_later.return_value = Mock()

    def write_arm(self, raw=None):
        path = self.root / "arm.json"
        path.write_bytes(raw if raw is not None else encoded(self.arm))
        path.chmod(0o600)

    def prepare(self):
        session = observer.prepare_observation(8100, root=self.root, now=lambda: self.now,
                                               clock=lambda: self.clock,
                                               identity_reader=lambda: dict(self.identity))
        if session is not None:
            self.addCleanup(session.finish, "SHUTDOWN")
        return session

    def active(self):
        session = self.prepare()
        self.assertIsNotNone(session)
        session.start(self.loop)
        return session

    def read_report(self):
        return json.loads((self.root / self.arm["capture_id"] / "report.json").read_bytes())

    async def test_absent_arm_does_not_create_files_or_read_identity(self):
        (self.root / "arm.json").unlink()
        reader = Mock(side_effect=AssertionError("must not read identity"))
        self.assertIsNone(observer.prepare_observation(8100, root=self.root, identity_reader=reader))
        reader.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_durable_claim_precedes_capture_and_restart_refuses(self):
        session = self.prepare()
        self.assertIsNone(session.recorder)
        claim = self.root / self.arm["capture_id"] / "consumed.json"
        self.assertEqual(json.loads(claim.read_bytes())["disposition"], "consumed_not_proof_of_collection")
        self.assertEqual(stat.S_IMODE(claim.stat().st_mode), 0o600)
        self.assertIsNone(self.prepare())
        session.start(self.loop)
        session.capture(scope([(b"host", b"example.invalid")]))
        session.finish("WINDOW_CLOSED")
        self.assertEqual(self.read_report()["state"], "WINDOW_CLOSED")
        self.assertIsNone(self.prepare())

    async def test_timer_expiry_without_requests_and_exactly_once_cleanup(self):
        session = self.active()
        delay, callback, reason = self.loop.call_later.call_args.args
        self.assertEqual(delay, 180)
        timer = session.timer
        callback(reason)
        original = (self.root / self.arm["capture_id"] / "report.json").read_bytes()
        session.finish("SHUTDOWN")
        self.assertEqual(original, (self.root / self.arm["capture_id"] / "report.json").read_bytes())
        timer.cancel.assert_called_once()
        self.assertIsNone(session.root_fd)
        self.assertIsNone(session.attempt_fd)
        self.assertFalse(self.read_report()["capture_complete"])

    async def test_wall_clock_rollback_does_not_extend_monotonic_bound(self):
        session = self.prepare()
        self.clock += 250
        self.now -= timedelta(hours=1)
        session.start(self.loop)
        self.assertEqual(self.loop.call_later.call_args.args[0], 50)

    async def test_expiry_during_startup_consumes_without_observing(self):
        session = self.prepare()
        self.clock += 301
        session.start(self.loop)
        self.assertTrue(session.closed)
        self.assertEqual(self.read_report()["state"], "ARM_EXPIRED")
        self.loop.call_later.assert_not_called()

    async def test_future_expired_and_overlong_intervals_refuse(self):
        for issued, expires in [("2026-09-15T00:01:00Z", "2026-09-15T00:05:00Z"),
                                ("2026-09-14T23:00:00Z", "2026-09-14T23:05:00Z"),
                                ("2026-09-15T00:00:00Z", "2026-09-15T01:00:00Z")]:
            self.arm.update(issued_at=issued, expires_at=expires);self.write_arm()
            self.assertIsNone(self.prepare())

    async def test_source_port_marker_unknown_field_and_bool_version_refuse(self):
        original = dict(self.arm)
        for key, value in [("expected_source", "1" * 40), ("listener_port", 8110),
                           ("marker", "topo-" + "b" * 32), ("unexpected", "synthetic-secret"),
                           ("schema_version", True), ("capture_id", "../elsewhere")]:
            self.arm = {**original, key: value};self.write_arm()
            self.assertIsNone(self.prepare())

    async def test_duplicate_json_and_oversized_arm_refuse(self):
        for raw in [b'{"schema_version":1,"schema_version":1}', b"x" * 4097, b"\xff", b"[NaN]"]:
            self.write_arm(raw)
            self.assertIsNone(self.prepare())

    async def test_private_permissions_and_wrong_owner_refuse(self):
        (self.root / "arm.json").chmod(0o640)
        self.assertIsNone(self.prepare())
        (self.root / "arm.json").chmod(0o600)
        self.root.chmod(0o750)
        self.assertIsNone(self.prepare())
        self.root.chmod(0o700)
        with patch.object(observer.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertIsNone(self.prepare())

    async def test_symlink_hardlink_and_ancestor_symlink_refuse(self):
        arm = self.root / "arm.json";target = self.root / "target"
        arm.rename(target);arm.symlink_to(target)
        self.assertIsNone(self.prepare())
        arm.unlink();os.link(target, arm)
        self.assertIsNone(self.prepare())
        arm.unlink();target.rename(arm)
        link = Path(self.temp.name) / "link";link.symlink_to(self.root, target_is_directory=True)
        self.assertIsNone(observer.prepare_observation(8100, root=link))

    async def test_fifo_with_no_writer_refuses_before_external_timeout(self):
        arm = self.root / "arm.json";arm.unlink();os.mkfifo(arm, 0o600)
        code = ("import sys;sys.path.insert(0,sys.argv[1]);"
                "from ha_mcp_engineering.inbound_topology_observer import prepare_observation;"
                "assert prepare_observation(8100,root=sys.argv[2]) is None")
        completed = subprocess.run([sys.executable, "-I", "-B", "-c", code,
                                    str(ROOT / "hass_mcp_engineering_beta"), str(self.root)],
                                   capture_output=True, timeout=3, check=False)
        self.assertEqual(completed.returncode, 0)

    async def test_arm_swap_between_reads_refuses_without_claim(self):
        original = observer._read_arm
        calls = 0
        def swap(fd):
            nonlocal calls
            raw = original(fd);calls += 1
            return raw if calls == 1 else raw + b" "
        with patch.object(observer, "_read_arm", side_effect=swap):
            self.assertIsNone(self.prepare())
        self.assertFalse((self.root / self.arm["capture_id"]).exists())

    async def test_existing_attempt_and_preexisting_report_are_never_overwritten(self):
        session = self.active()
        path = self.root / self.arm["capture_id"] / "report.json"
        path.write_bytes(b"preserve-existing");path.chmod(0o600)
        session.finish("WINDOW_CLOSED")
        self.assertEqual(path.read_bytes(), b"preserve-existing")
        self.assertEqual(session.export_status, "failed")
        self.assertIsNone(session.root_fd)

    async def test_export_write_failure_closes_descriptors_and_preserves_claim(self):
        session = self.active()
        with patch.object(observer.os, "write", side_effect=OSError("synthetic-secret")):
            with self.assertLogs(observer.LOGGER, level="WARNING") as captured:
                session.finish("WINDOW_CLOSED")
        self.assertNotIn("synthetic-secret", str(captured.output))
        self.assertEqual(session.export_status, "failed")
        self.assertIsNone(session.root_fd)
        self.assertIsNone(self.prepare())

    async def test_directory_swap_refuses_private_export(self):
        session = self.active()
        moved = self.root.with_name("moved");self.root.rename(moved)
        self.root.mkdir(mode=0o700)
        session.finish("WINDOW_CLOSED")
        self.assertEqual(session.export_status, "failed")
        self.assertFalse((moved / self.arm["capture_id"] / "report.json").exists())

    async def test_identity_drift_at_export_fails_receipt(self):
        session = self.active();session.capture(scope())
        self.identity["version"] = "changed"
        session.finish("WINDOW_CLOSED")
        self.assertEqual(self.read_report()["failure"], "identity_drift")

    async def test_request_failure_and_shutdown_do_not_repeat_dispatch(self):
        session = self.active();app = AsyncMock(side_effect=RuntimeError("synthetic-app-failure"))
        with self.assertRaises(RuntimeError):
            await observer.BoundaryObserver(app, session)(scope(), AsyncMock(), AsyncMock())
        session.finish("SHUTDOWN")
        app.assert_awaited_once()
        self.assertEqual(self.read_report()["state"], "SHUTDOWN")

    async def test_refusal_does_not_leak_fds(self):
        fd_dir = Path("/proc/self/fd")
        before = len(list(fd_dir.iterdir()))
        self.write_arm(b"synthetic-secret-invalid-json")
        for _ in range(8):self.assertIsNone(self.prepare())
        self.assertEqual(len(list(fd_dir.iterdir())), before)

    async def test_clean_build_identity_controls(self):
        from ha_mcp_engineering import version
        with patch.object(version, "BUILD_SHA", SOURCE), patch.object(version, "BUILD_DIRTY", False), \
                patch.object(version, "BUILD_TIME", "2026-09-15T00:00:00Z"):
            self.assertEqual(observer.build_identity()["source_sha"], SOURCE)
            with patch.object(version, "BUILD_DIRTY", True):
                with self.assertRaises(observer.ObservationRefused):observer.build_identity()

    async def test_normal_listener_is_unchanged_when_disabled(self):
        with patch.object(observer, "prepare_observation", return_value=None), \
                patch.object(observer.uvicorn, "Config") as config, \
                patch.object(observer.uvicorn, "Server") as server:
            app = object();result = observer.create_mcp_listener(app, port=8100, log_level="info")
        config.assert_called_once_with(app, host="0.0.0.0", port=8100, log_level="info", access_log=False, proxy_headers=False)
        server.assert_called_once_with(config.return_value)
        self.assertIs(result, server.return_value)

    async def test_server_shutdown_and_startup_failure_finalize(self):
        session = self.prepare()
        config = observer.ObservedConfig(AsyncMock(), observation=session, interface="asgi3",
                                         log_config=None, lifespan="off")
        server = observer.ObservedServer(config)
        with patch.object(observer.uvicorn.Server, "serve", new=AsyncMock(side_effect=RuntimeError("startup"))):
            with self.assertRaises(RuntimeError):await server.serve()
        self.assertTrue(session.closed)
        self.assertEqual(self.read_report()["state"], "SHUTDOWN")

    async def test_composition_failure_closes_consumed_observation(self):
        from types import SimpleNamespace
        from ha_mcp_engineering import application
        from tests.test_beta_v2 import beta_settings
        session = self.prepare()
        with patch.object(application, "create_application", return_value=object()), \
                patch.object(application, "GOVERNANCE", SimpleNamespace(service=None)), \
                patch.object(observer, "prepare_observation", return_value=session), \
                patch.object(application, "create_approval_application",
                             side_effect=RuntimeError("synthetic-composition")):
            with self.assertRaises(RuntimeError):
                await application._serve(beta_settings(str(Path(self.temp.name) / "audit.jsonl")))
        self.assertTrue(session.closed)
        self.assertIsNone(session.root_fd)
        self.assertIsNone(session.attempt_fd)
        self.assertEqual(self.read_report()["state"], "COMPOSITION_EXIT")
        self.assertIsNone(self.prepare())

    async def test_task_creation_failure_closes_arm_before_serve_starts(self):
        from types import SimpleNamespace
        from ha_mcp_engineering import application
        from tests.test_beta_v2 import beta_settings
        session = self.prepare()
        def refuse_task(coroutine, **kwargs):
            coroutine.close()
            raise RuntimeError("synthetic-task-creation")
        with patch.object(application, "create_application", return_value=object()), \
                patch.object(application, "GOVERNANCE", SimpleNamespace(service=None)), \
                patch.object(observer, "prepare_observation", return_value=session), \
                patch.object(application, "create_approval_application", return_value=AsyncMock()), \
                patch.object(application.asyncio, "create_task", side_effect=refuse_task):
            with self.assertRaises(RuntimeError):
                await application._serve(beta_settings(str(Path(self.temp.name) / "audit.jsonl")))
        self.assertTrue(session.closed)
        self.assertIsNone(session.root_fd)
        self.assertIsNone(session.attempt_fd)
        self.assertFalse(self.read_report()["capture_complete"])

    async def test_real_server_starts_timer_only_after_ready(self):
        session = self.prepare()
        config = observer.ObservedConfig(AsyncMock(), observation=session, log_config=None)
        server = observer.ObservedServer(config)
        self.assertIsNone(session.timer)
        async def ready(*, sockets=None):
            self.assertIsNone(session.timer)
            server.started = True
        with patch.object(observer.uvicorn.Server, "startup", side_effect=ready):
            await server.startup()
        self.assertIsNotNone(session.timer)
        self.assertEqual(session.recorder.state, "ACTIVE")
        session.finish("SHUTDOWN")

    async def test_observation_start_failure_preserves_ready_server(self):
        session = self.prepare()
        server = observer.ObservedServer(observer.ObservedConfig(AsyncMock(), observation=session,
                                                                 log_config=None))
        server.started = True
        with patch.object(observer.uvicorn.Server, "startup", new=AsyncMock()), \
                patch.object(session, "start", side_effect=OSError("synthetic-secret")), \
                self.assertLogs(observer.LOGGER, level="WARNING") as captured:
            await server.startup()
        self.assertTrue(server.started)
        self.assertTrue(session.closed)
        self.assertNotIn("synthetic-secret", str(captured.output))

    async def test_server_cancellation_finalizes_partial_without_resuming(self):
        session = self.active()
        session.capture(scope())
        server = observer.ObservedServer(observer.ObservedConfig(AsyncMock(), observation=session,
                                                                 log_config=None))
        with patch.object(observer.uvicorn.Server, "serve", new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await server.serve()
        report = self.read_report()
        self.assertEqual(report["state"], "SHUTDOWN")
        self.assertEqual(len(report["records"]), 1)
        self.assertFalse(report["capture_complete"])
        self.assertIsNone(self.prepare())

    async def test_crash_after_claim_leaves_no_report_and_refuses_replay(self):
        code = ("import sys,os,json;from datetime import datetime,timezone;"
                "sys.path.insert(0,sys.argv[1]);"
                "from ha_mcp_engineering.inbound_topology_observer import prepare_observation;"
                "s=prepare_observation(8100,root=sys.argv[2],"
                "now=lambda:datetime(2026,9,15,tzinfo=timezone.utc),"
                "identity_reader=lambda:json.loads(sys.argv[3]));"
                "os._exit(0 if s is not None else 1)")
        completed = subprocess.run([sys.executable, "-I", "-B", "-c", code,
                                    str(ROOT / "hass_mcp_engineering_beta"), str(self.root),
                                    json.dumps(self.identity)], capture_output=True, timeout=3)
        self.assertEqual(completed.returncode, 0)
        attempt = self.root / self.arm["capture_id"]
        self.assertTrue((attempt / "consumed.json").exists())
        self.assertFalse((attempt / "report.json").exists())
        self.assertIsNone(self.prepare())

    async def test_short_write_and_fsync_failure_do_not_rearm(self):
        session = self.active()
        with patch.object(observer.os, "write", return_value=0):
            session.finish("WINDOW_CLOSED")
        self.assertEqual(session.export_status, "failed")
        self.assertIsNone(session.attempt_fd)
        self.assertIsNone(self.prepare())

    async def test_fsync_failure_preserves_claim_and_closes(self):
        session = self.active()
        with patch.object(observer.os, "fsync", side_effect=OSError("synthetic-secret")):
            session.finish("WINDOW_CLOSED")
        self.assertEqual(session.export_status, "failed")
        self.assertIsNone(session.attempt_fd)
        self.assertIsNone(self.prepare())

    async def test_capture_and_retirement_failure_still_invoke_application_once(self):
        observation = Mock()
        observation.capture.side_effect = RuntimeError("synthetic-secret")
        observation.finish.side_effect = RuntimeError("synthetic-secret")
        app = AsyncMock()
        with self.assertLogs(observer.LOGGER, level="WARNING") as captured:
            await observer.BoundaryObserver(app, observation)(scope(), AsyncMock(), AsyncMock())
        app.assert_awaited_once()
        self.assertNotIn("synthetic-secret", str(captured.output))

    async def test_production_factory_and_http_parser_preserve_authentication(self):
        from ha_mcp_engineering.application import create_application
        from tests.test_beta_v2 import beta_settings
        from uvicorn.protocols.http.h11_impl import H11Protocol
        from uvicorn.server import ServerState

        session = self.active()
        gateway = create_application(beta_settings(str(Path(self.temp.name) / "audit.jsonl")))
        config = observer.ObservedConfig(gateway, observation=session, host="0.0.0.0", port=8100,
                                         log_level="error", access_log=False, log_config=None,
                                         interface="asgi3", http="h11", ws="none", lifespan="off")
        config.load()

        class Transport(asyncio.Transport):
            def __init__(self):self.data = bytearray();self.closed = False
            def get_extra_info(self, name, default=None):
                return {"sockname": ("192.0.2.2", 8100), "peername": ("127.0.0.1", 1234)}.get(name, default)
            def write(self, data):self.data.extend(data)
            def close(self):self.closed = True
            def is_closing(self):return self.closed
            def pause_reading(self):pass
            def resume_reading(self):pass

        async def request(raw):
            state = ServerState()
            protocol = H11Protocol(config, state, app_state={})
            transport = Transport()
            protocol.connection_made(transport)
            protocol.data_received(raw)
            if state.tasks:
                await asyncio.wait_for(asyncio.gather(*list(state.tasks)), timeout=2)
            protocol.connection_lost(None)
            return bytes(transport.data)

        prefix = (b"X-Engineering-Topology-Probe: " + MARKER.encode() + b"\r\n"
                  b"X-Engineering-Topology-Request: ")
        with patch("socket.socket.connect", side_effect=AssertionError("network_forbidden")), \
                patch.object(gateway._core_runtime, "acquire",
                             side_effect=AssertionError("authority_forbidden")) as acquire:
            first = await request(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1:8100\r\n" +
                                  prefix + b"1\r\nConnection: close\r\n\r\n")
            self.assertIn(b"200", first.split(b"\r\n", 1)[0])
            refused = await request(b"POST /synthetic-wrong-secret HTTP/1.1\r\nHost: 127.0.0.1:8100\r\n" +
                                    prefix + b"2\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            self.assertIn(b"404", refused.split(b"\r\n", 1)[0])
            malformed = await request(b"GET /health HTTP/1.1\r\nHost: one.invalid\r\nHost: two.invalid\r\n" +
                                      prefix + b"3\r\n\r\n")
            self.assertIn(b"400", malformed.split(b"\r\n", 1)[0])
            acquire.assert_not_called()
        self.assertEqual([r["request_index"] for r in session.recorder.records], [1, 2])
        self.assertEqual(session.recorder.records[0]["immediate_peer"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
