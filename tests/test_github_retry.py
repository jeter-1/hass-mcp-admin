"""HAMCP-221: bounded, authenticated GitHub reads for CI.

Offline only: gh, the HTTPS opener, the clock and sleep are synthetic.  The
token values are unmistakable synthetic strings, never credentials.
"""

import email.message
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

import yaml

from tests.test_signing_workflow_supply_chain import (
    _isolated_run, _sibling_imports, _stdlib_violations,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "github_retry.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
AUTO_MERGE_WORKFLOW = ROOT / ".github" / "workflows" / "enable-auto-merge.yml"
spec = importlib.util.spec_from_file_location("github_retry", HELPER)
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)

# Token-shaped but synthetic; split so the repository secret scan stays exact.
SYNTHETIC_TOKEN = "ghs_" + "SYNTHETICtestTOKENnotAcredential0000"
ROUTE = "repos/jeter-1/hass-mcp-admin/pulls/164"
CORE_SHA = "abe1fdd7" + "0" * 32
CORE_URL = f"https://codeload.github.com/home-assistant/core/tar.gz/{CORE_SHA}"
BODY = b"synthetic archive bytes"
BODY_SHA = hashlib.sha256(BODY).hexdigest()


class Clock:
    """A monotonic clock that only advances when the helper sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def bounds(self, jitter=0.25):
        return {"clock": self, "sleep": self.sleep, "jitter": lambda: jitter,
                "wall": lambda: 1_000_000.0}


def included(status, body=b"{}", reason="Status", **headers):
    lines = [f"HTTP/2.0 {status} {reason}\n".encode()]
    lines += [f"{name.replace('_', '-')}: {value}\r\n".encode() for name, value in headers.items()]
    return b"".join(lines) + b"\r\n" + body


class FakeGh:
    """Stands in for ``subprocess.run`` of ``gh api --include``."""

    def __init__(self, *responses, clock=None, cost=0.0):
        self.responses = list(responses)
        self.calls = []
        self.clock = clock
        self.cost = cost

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.clock is not None:
            # subprocess.run kills gh at its timeout.
            self.clock.now += min(self.cost, kwargs["timeout"])
            if self.cost >= kwargs["timeout"]:
                self.responses.pop(0)
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        raw, code, *diagnostic = item
        kwargs["stdout"].write(raw)
        kwargs["stderr"].write(b"".join(diagnostic))
        return subprocess.CompletedProcess(args, code)


# What gh prints, on standard error only, when no response arrived.
GH_DIAL_TIMEOUT = (b"", 1, b'Get "https://api.github.com/x": dial tcp 140.82.112.6:443: i/o timeout\n')
GH_TLS_REFUSAL = (b"", 1, b'Get "https://api.github.com/x": tls: failed to verify certificate: '
                  b"x509: certificate signed by unknown authority\n")
GH_EOF = (b"", 1, b'Get "https://api.github.com/x": EOF\n')


def gh_status(status, **headers):
    return (included(status, b'{"message":"synthetic"}', **headers), 0 if status == 200 else 1)


class FakeResponse:
    def __init__(self, body=BODY, fail_after=None):
        self.status = 200
        self.headers = email.message.Message()
        self.stream = io.BytesIO(body)
        self.fail_after = fail_after

    def read1(self, size):
        if self.fail_after is not None and self.stream.tell() >= self.fail_after:
            raise http.client.IncompleteRead(b"")
        limit = size if self.fail_after is None else min(size, self.fail_after - self.stream.tell())
        return self.stream.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, **headers):
    message = email.message.Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return urllib.error.HTTPError(CORE_URL, code, "synthetic", message, io.BytesIO(b"private"))


class FakeOpener:
    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class RetryPolicyTests(unittest.TestCase):
    def read(self, fake, clock, **kwargs):
        return retry.bounded_gh_read(ROUTE, 4_000_000, fake, **clock.bounds(**kwargs))

    def test_429_with_retry_after_is_retried_then_succeeds(self):
        clock = Clock()
        fake = FakeGh(gh_status(429, Retry_After="7"), (included(200, b'{"ok":1}'), 0))
        self.assertEqual(self.read(fake, clock), b'{"ok":1}')
        self.assertEqual(len(fake.calls), 2)
        # Retry-After is honored exactly, plus jitter in [0, 1).
        self.assertEqual(clock.sleeps, [7.25])

    def test_retry_after_http_date_is_honored(self):
        clock = Clock()
        when = "Mon, 12 Jan 1970 13:47:00 GMT"  # 1_000_020 s after the epoch
        fake = FakeGh(gh_status(429, Retry_After=when), (included(200), 0))
        self.read(fake, clock)
        self.assertEqual(clock.sleeps, [20.25])

    def test_429_without_retry_after_backs_off_exponentially_with_jitter(self):
        clock = Clock()
        fake = FakeGh(gh_status(429), gh_status(429), gh_status(429), (included(200), 0))
        self.read(fake, clock, jitter=0.5)
        # BASE * 2**(n-1) scaled by (0.5 + jitter / 2) = 0.75.
        self.assertEqual(clock.sleeps, [1.5, 3.0, 6.0])
        clock = Clock()
        self.read(FakeGh(gh_status(429), (included(200), 0)), clock, jitter=0.0)
        self.assertEqual(clock.sleeps, [1.0])

    def test_5xx_and_transient_network_failures_are_retried_then_succeed(self):
        for failure in (gh_status(500), gh_status(502), gh_status(503), gh_status(504),
                        GH_DIAL_TIMEOUT, subprocess.TimeoutExpired(["gh"], 30)):
            with self.subTest(failure=failure):
                clock = Clock()
                fake = FakeGh(failure, (included(200, b"[]"), 0))
                self.assertEqual(self.read(fake, clock), b"[]")
                self.assertEqual(len(fake.calls), 2)
                self.assertEqual(len(clock.sleeps), 1)

    def test_attempt_bound_fails_closed_naming_host_and_last_status(self):
        clock = Clock()
        fake = FakeGh(*[gh_status(503)] * 10)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.read(fake, clock, jitter=0.0)
        self.assertEqual(len(fake.calls), retry.MAX_ATTEMPTS)
        self.assertEqual(retry.MAX_ATTEMPTS, 5)
        self.assertEqual(len(clock.sleeps), retry.MAX_ATTEMPTS - 1)
        self.assertEqual(str(caught.exception), "GitHub read failed: host api.github.com, "
                         "last status 503, 5 attempt(s), retry bound reached")
        self.assertEqual(caught.exception.category, "github_read_failed")

    def test_retry_after_is_capped_and_the_total_time_bound_fails_closed(self):
        clock = Clock()
        fake = FakeGh(*[gh_status(429, Retry_After="3600")] * 10)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.read(fake, clock, jitter=0.0)
        # Capped at 60 s per wait; the third wait would reach the 180 s bound.
        self.assertEqual(retry.RETRY_AFTER_CAP_SECONDS, 60)
        self.assertEqual(clock.sleeps, [60.0, 60.0])
        self.assertEqual(len(fake.calls), 3)
        self.assertIn("last status 429, 3 attempt(s), retry bound reached", str(caught.exception))

    def test_total_time_bound_includes_slow_attempts(self):
        clock = Clock()
        # Each answered attempt takes 29 s and asks for a 25 s wait.
        fake = FakeGh(*[gh_status(503, Retry_After="25")] * 10, clock=clock, cost=29.0)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.read(fake, clock, jitter=0.0)
        self.assertEqual(retry.MAX_TOTAL_SECONDS, 180)
        self.assertLessEqual(clock.now, retry.MAX_TOTAL_SECONDS)
        self.assertLess(len(fake.calls), retry.MAX_ATTEMPTS)
        self.assertTrue(caught.exception.exhausted)
        # The fourth attempt gets only the 18 s that remain and times out.
        timeouts = [kwargs["timeout"] for _args, kwargs in fake.calls]
        self.assertEqual(timeouts, [30.0, 30.0, 30.0, 18.0])
        self.assertEqual(caught.exception.status, "timeout")
        self.assertEqual(clock.now, 180.0)

    def test_no_attempt_starts_without_minimum_time_remaining(self):
        clock = Clock()
        # After the second 60 s wait only 0.5 s would remain for a third attempt.
        fake = FakeGh(*[gh_status(429, Retry_After="60")] * 3, (included(200), 0),
                      clock=clock, cost=29.75)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.read(fake, clock, jitter=0.0)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(clock.sleeps, [60.0])
        self.assertEqual(clock.now, 119.5)
        self.assertTrue(caught.exception.exhausted)

    def test_401_403_404_and_other_statuses_fail_immediately_without_retry(self):
        for status in (301, 302, 400, 401, 403, 404, 410, 422):
            with self.subTest(status=status):
                clock = Clock()
                fake = FakeGh(gh_status(status, Retry_After="1"), (included(200), 0))
                with self.assertRaises(retry.GitHubReadError) as caught:
                    self.read(fake, clock)
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertFalse(caught.exception.exhausted)
                self.assertIn(f"last status {status}, 1 attempt(s), not retried",
                              str(caught.exception))

    def test_missing_gh_credentials_unavailable_gh_and_malformed_output_are_final(self):
        for failure, status, category in (
            ((b"", 4), "authentication_required", "github_read_failed"),
            (OSError("no gh"), "gh_unavailable", "github_read_unavailable"),
            ((b"not an http response", 0), "malformed_response", "github_read_failed"),
            ((b"HTTP/2.0 200 OK\nbroken header\r\n\r\n{}", 0), "malformed_response",
             "github_read_failed"),
        ):
            with self.subTest(status=status):
                clock = Clock()
                fake = FakeGh(failure, (included(200), 0))
                with self.assertRaises(retry.GitHubReadError) as caught:
                    self.read(fake, clock)
                self.assertEqual((caught.exception.status, caught.exception.category),
                                 (status, category))
                self.assertEqual(len(fake.calls), 1)

    def test_network_exhaustion_is_reported_as_unavailable(self):
        clock = Clock()
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.read(FakeGh(*[GH_DIAL_TIMEOUT] * 5), clock)
        self.assertEqual(caught.exception.category, "github_read_unavailable")

    def test_gh_invocation_is_a_fixed_read_only_get(self):
        fake = FakeGh((included(200), 0))
        self.read(fake, Clock())
        args, kwargs = fake.calls[0]
        self.assertEqual(args, ["gh", "api", ROUTE, "--include", "--hostname", "github.com",
                                "--method", "GET", "-H", "Accept: application/vnd.github+json"])
        # Standard error is captured for classification only, never exported.
        self.assertNotIn(kwargs["stderr"], (None, subprocess.DEVNULL, subprocess.PIPE))
        self.assertFalse(kwargs["check"])

    def test_every_gh_tls_failure_and_unrecognized_transport_failure_is_final(self):
        for failure, status in (
            (GH_TLS_REFUSAL, "tls_failed"),
            ((b"", 1, b'Get "https://api.github.com/x": remote error: tls: handshake failure\n'),
             "tls_failed"),
            ((b"", 1, b"net/http: TLS handshake timeout\n"), "tls_failed"),
            (GH_EOF, "transport_failed"),
            ((b"", 1, b"read: connection reset by peer\n"), "transport_failed"),
            ((b"", 1), "transport_failed"),
        ):
            with self.subTest(status=status, failure=failure):
                clock = Clock()
                fake = FakeGh(failure, (included(200), 0))
                with self.assertRaises(retry.GitHubReadError) as caught:
                    self.read(fake, clock)
                self.assertEqual(caught.exception.status, status)
                self.assertFalse(caught.exception.exhausted)
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(clock.sleeps, [])
                # The fixed message carries no gh diagnostic.
                self.assertNotIn("x509", str(caught.exception))
                self.assertNotIn("handshake", str(caught.exception))

    def test_retry_after_cap_applies_to_the_final_jittered_delay(self):
        self.assertEqual(retry.backoff_delay(1, 3600, 0.75), 60.0)
        self.assertEqual(retry.backoff_delay(1, 59.5, 0.75), 60.0)
        self.assertEqual(retry.backoff_delay(1, 60, 0.0), 60.0)
        self.assertEqual(retry.backoff_delay(1, 10, 0.75), 10.75)
        clock = Clock()
        fake = FakeGh(gh_status(429, Retry_After="3600"), (included(200), 0))
        self.read(fake, clock, jitter=0.75)
        self.assertEqual(clock.sleeps, [60.0])

    def test_oversized_body_stays_detectable(self):
        fake = FakeGh((included(200, b"x" * 100), 0))
        body = retry.bounded_gh_read(ROUTE, 10, fake, **Clock().bounds())
        self.assertEqual(len(body), 11)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.output = self.directory / "core.tar.gz"
        self.clock = Clock()

    def download(self, opener, sha256=BODY_SHA, url=CORE_URL, token=SYNTHETIC_TOKEN):
        return retry.download(url, self.output, token, sha256, opener, **self.clock.bounds())

    def leftovers(self):
        return sorted(path.name for path in self.directory.iterdir())

    def test_429_and_5xx_are_retried_and_the_retried_download_is_verified(self):
        opener = FakeOpener(http_error(429, Retry_After="2"), http_error(503),
                            FakeResponse(fail_after=4), FakeResponse())
        self.assertEqual(self.download(opener), {"bytes": len(BODY), "sha256": BODY_SHA})
        self.assertEqual(self.output.read_bytes(), BODY)
        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(len(self.clock.sleeps), 3)
        self.assertEqual(self.clock.sleeps[0], 2.25)
        self.assertEqual(self.leftovers(), ["core.tar.gz"])

    def test_a_retried_download_with_the_wrong_digest_fails_without_further_retry(self):
        opener = FakeOpener(http_error(502), FakeResponse(b"tampered bytes"), FakeResponse())
        self.output.write_bytes(b"stale earlier archive")
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.download(opener)
        self.assertEqual(caught.exception.status, "sha256_mismatch")
        self.assertFalse(caught.exception.exhausted)
        self.assertEqual(len(opener.requests), 2)
        # Neither the mismatched bytes nor a stale file remain.
        self.assertEqual(self.leftovers(), [])

    def test_hash_mismatch_on_the_first_attempt_is_never_retried(self):
        opener = FakeOpener(FakeResponse(b"other form"), FakeResponse())
        with self.assertRaises(retry.GitHubReadError):
            self.download(opener)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.leftovers(), [])

    def test_401_403_404_and_redirects_fail_immediately(self):
        for code in (301, 302, 307, 401, 403, 404):
            with self.subTest(code=code):
                self.clock = Clock()
                opener = FakeOpener(http_error(code, Retry_After="1"), FakeResponse())
                with self.assertRaises(retry.GitHubReadError) as caught:
                    self.download(opener)
                self.assertEqual(caught.exception.status, code)
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(self.clock.sleeps, [])
                self.assertEqual(self.leftovers(), [])

    def test_tls_verification_failure_is_final_and_other_network_errors_retry(self):
        opener = FakeOpener(urllib.error.URLError(ssl.SSLCertVerificationError("synthetic")),
                            FakeResponse())
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.download(opener)
        self.assertEqual(caught.exception.status, "tls_verification_failed")
        self.assertEqual(len(opener.requests), 1)
        for error in (urllib.error.URLError(ConnectionResetError()), TimeoutError(),
                      urllib.error.URLError(TimeoutError("attempt_deadline")),
                      http.client.RemoteDisconnected("synthetic")):
            with self.subTest(error=error):
                self.clock = Clock()
                opener = FakeOpener(error, FakeResponse())
                self.download(opener)
                self.assertEqual(len(opener.requests), 2)

    def test_a_slow_download_attempt_stops_within_its_time_budget(self):
        clock = self.clock

        starts = []

        class Trickle(FakeResponse):
            def read1(self, size):
                starts.append(clock.now)
                clock.now += 20.0  # each receive waits 20 s, under the idle cap
                # A finite stream, so a missing deadline check completes the
                # download and fails below instead of writing without end.
                return b"x" if len(starts) <= 100 else b""

        opener = FakeOpener(Trickle())
        with self.assertRaises(retry.RetryableRead) as caught:
            retry.download_attempt(retry.urllib.request.Request(CORE_URL), self.output,
                                   None, 90.0, opener, clock)
        self.assertEqual(caught.exception.status, "timeout")
        # The opener gets the whole budget as one absolute deadline, and no read
        # starts at or after it.  (This fake read ignores the deadline; the
        # default opener's sockets also cap each receive at the time left, see
        # test_buffered_socket_reads_hold_the_absolute_deadline.)
        self.assertEqual(opener.requests[0][1], 90.0)
        self.assertEqual(starts, [0.0, 20.0, 40.0, 60.0, 80.0])
        self.assertEqual(self.leftovers(), [])

    def test_every_tls_failure_is_final_direct_wrapped_or_chained(self):
        def chained():
            try:
                raise ssl.SSLError(1, "synthetic")
            except ssl.SSLError:
                try:
                    raise ConnectionResetError()
                except ConnectionResetError as exc:
                    return urllib.error.URLError(exc)

        for error, status in (
            (ssl.SSLError(1, "synthetic handshake failure"), "tls_failed"),
            (urllib.error.URLError(ssl.SSLError(1, "synthetic")), "tls_failed"),
            (urllib.error.URLError(ssl.SSLEOFError(8, "synthetic")), "tls_failed"),
            (urllib.error.URLError(ssl.SSLZeroReturnError(6, "synthetic")), "tls_failed"),
            (urllib.error.URLError(retry.TLSFailure("tls_handshake_failed")), "tls_failed"),
            (chained(), "tls_failed"),
            (urllib.error.URLError(ssl.SSLCertVerificationError("synthetic")),
             "tls_verification_failed"),
        ):
            with self.subTest(error=error):
                self.clock = Clock()
                opener = FakeOpener(error, FakeResponse())
                with self.assertRaises(retry.GitHubReadError) as caught:
                    self.download(opener)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(self.clock.sleeps, [])
                self.assertNotIn("synthetic", str(caught.exception))
                self.assertEqual(self.leftovers(), [])
        # A TLS failure while reading the body is final too.
        self.clock = Clock()

        class Broken(FakeResponse):
            def read1(self, size):
                raise ssl.SSLError(1, "synthetic record failure")

        opener = FakeOpener(Broken(), FakeResponse())
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.download(opener)
        self.assertEqual((caught.exception.status, len(opener.requests)), ("tls_failed", 1))
        # A cyclic or deep exception chain is walked boundedly.
        loop = OSError("synthetic")
        loop.__context__ = loop
        self.assertIsNone(retry.tls_failure(loop))

    def test_a_real_tls_handshake_failure_is_final(self):
        # A loopback peer that answers the ClientHello with bytes that are not
        # TLS; nothing leaves the host.
        try:
            client, server = socket.socketpair()
        except OSError:
            self.skipTest("socket pairs unavailable")
        self.addCleanup(client.close)
        self.addCleanup(server.close)

        def peer():
            with server:
                server.settimeout(5)
                try:
                    server.recv(65536)
                    server.sendall(b"HTTP/1.1 400 not a TLS record\r\n\r\n")
                except OSError:
                    pass

        thread = threading.Thread(target=peer, daemon=True)
        thread.start()
        client.settimeout(5)
        with self.assertRaises(OSError) as caught:
            retry.tls_context().wrap_socket(client, server_hostname=retry.DOWNLOAD_HOST)
        thread.join(5)
        self.assertIsInstance(caught.exception, ssl.SSLError)
        outcome = retry.network_failure(urllib.error.URLError(caught.exception))
        self.assertIsInstance(outcome, retry.FinalRead)
        self.assertEqual(outcome.status, "tls_failed")

    def test_a_peer_that_closes_during_the_handshake_is_a_final_tls_failure(self):
        try:
            client, server = socket.socketpair()
        except OSError:
            self.skipTest("socket pairs unavailable")
        self.addCleanup(client.close)
        server.close()
        client.settimeout(5)
        with self.assertRaises(OSError) as caught:
            retry.tls_context().wrap_socket(client, server_hostname=retry.DOWNLOAD_HOST)
        self.assertIsInstance(caught.exception, ssl.SSLError)
        self.assertEqual(retry.network_failure(caught.exception).status, "tls_failed")

    def test_any_failure_while_establishing_tls_is_a_tls_failure(self):
        sock = retry.DeadlineSSLSocket.__new__(retry.DeadlineSSLSocket)
        for error in (ConnectionResetError(), TimeoutError(), OSError("synthetic")):
            with self.subTest(error=error), \
                    patch.object(ssl.SSLSocket, "do_handshake", side_effect=error):
                with self.assertRaises(retry.TLSFailure) as caught:
                    sock.do_handshake()
                self.assertEqual(retry.network_failure(
                    urllib.error.URLError(caught.exception)).status, "tls_failed")
        handshake = ssl.SSLError(1, "synthetic")
        with patch.object(ssl.SSLSocket, "do_handshake", side_effect=handshake):
            with self.assertRaises(ssl.SSLError) as caught:
                sock.do_handshake()
        self.assertIs(caught.exception, handshake)

    def test_buffered_socket_reads_hold_the_absolute_deadline(self):
        # Slow headers: every receive takes 20 s, under the 30 s idle cap, so
        # only the per-receive budget can stop a buffered read at the deadline.
        clock = Clock()
        try:
            near, far = socket.socketpair()
        except OSError:
            self.skipTest("socket pairs unavailable")
        self.addCleanup(far.close)
        far.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 400)
        # End of stream after the header bytes, so a missing budget fails the
        # assertions below instead of blocking on an idle pair.
        far.shutdown(socket.SHUT_WR)

        class SlowPeer(socket.socket):
            def recv_into(self, buffer, nbytes=0, flags=0):
                clock.now += 20.0
                return super().recv_into(memoryview(buffer)[:1], 1, flags)

        class Probe(retry.DeadlineIO, SlowPeer):
            pass

        probe = Probe(near.family, near.type, near.proto, near.detach())
        self.addCleanup(probe.close)
        probe.clock = clock
        probe.deadline = 180.0
        timeouts = []
        real_settimeout = probe.settimeout
        probe.settimeout = lambda value: (timeouts.append(value), real_settimeout(value))[1]
        response = http.client.HTTPResponse(probe)
        with self.assertRaises(TimeoutError):
            response.begin()
        self.assertEqual(clock.now, 180.0)
        self.assertEqual(max(timeouts), retry.DOWNLOAD_IDLE_TIMEOUT_SECONDS)
        self.assertEqual(timeouts[-1], 20.0)
        self.assertEqual(retry.network_failure(urllib.error.URLError(TimeoutError())).status,
                         "timeout")

    def test_resolution_and_connection_share_the_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hanging(*args, **kwargs):
            release.wait(30)
            return []

        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            retry.open_tcp(("codeload.github.com", 443), started + 0.2, resolve=hanging)
        self.assertLess(time.monotonic() - started, 5)

        def failing(*args, **kwargs):
            raise socket.gaierror("synthetic")

        with self.assertRaises(OSError):
            retry.open_tcp(("codeload.github.com", 443), time.monotonic() + 5, resolve=failing)

        made = []
        with patch.object(retry, "DeadlineSocket", lambda *a: made.append(a)):
            with self.assertRaises(TimeoutError):
                retry.open_tcp(("codeload.github.com", 443), 100.0, clock=lambda: 100.0,
                               resolve=lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6,
                                                         "", ("192.0.2.1", 443))])
        self.assertEqual(made, [])

    def test_a_connected_socket_carries_the_deadline_to_the_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "s")
            try:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(path)
                listener.listen(1)
            except OSError:
                self.skipTest("unix sockets unavailable")
            self.addCleanup(listener.close)
            clock = Clock()
            sock = retry.open_tcp(
                ("codeload.github.com", 443), 50.0, clock=clock,
                resolve=lambda *a, **k: [(socket.AF_UNIX, socket.SOCK_STREAM, 0, "", path)])
            self.addCleanup(sock.close)
            self.assertIsInstance(sock, retry.DeadlineSocket)
            self.assertEqual((sock.deadline, sock.gettimeout()), (50.0, 50.0))
            # A wrapping TLS socket inherits at most the time left.
            clock.now = 45.0
            self.assertEqual(sock.gettimeout(), 5.0)
            clock.now = 60.0
            self.assertEqual(sock.gettimeout(), 0.001)
            with self.assertRaises(TimeoutError):
                sock.sendall(b"x")

    def test_the_default_connection_carries_one_deadline_to_its_socket(self):
        connection = retry.DeadlineHTTPSConnection(
            retry.DOWNLOAD_HOST, timeout=42.0, context=retry.tls_context())
        self.assertAlmostEqual(connection.deadline - time.monotonic(), 42.0, delta=5)
        connection = retry.DeadlineHTTPSConnection(retry.DOWNLOAD_HOST, timeout=900.0)
        self.assertLessEqual(connection.deadline - time.monotonic(), retry.MAX_TOTAL_SECONDS)

    def test_download_attempt_bound_fails_closed(self):
        opener = FakeOpener(*[http_error(503)] * 10)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.download(opener)
        self.assertEqual(len(opener.requests), retry.MAX_ATTEMPTS)
        self.assertEqual(str(caught.exception), "GitHub read failed: host codeload.github.com, "
                         "last status 503, 5 attempt(s), retry bound reached")
        self.assertEqual(self.leftovers(), [])

    def test_only_exact_https_codeload_commit_archives_are_allowed(self):
        for url in (
            CORE_URL.replace("https://", "http://"),
            CORE_URL.replace("codeload.github.com", "api.github.com"),
            CORE_URL.replace("codeload.github.com", "codeload.github.com:8443"),
            CORE_URL.replace("codeload.github.com", "user@codeload.github.com"),
            CORE_URL + "?token=x",
            CORE_URL + "#x",
            CORE_URL.replace(CORE_SHA, "main"),
            CORE_URL.replace(CORE_SHA, CORE_SHA[:39]),
            CORE_URL.replace("tar.gz", "zip"),
            CORE_URL.replace("home-assistant/core", "home-assistant/core/../x"),
        ):
            with self.subTest(url=url):
                opener = FakeOpener(FakeResponse())
                with self.assertRaisesRegex(ValueError, "download_url_not_allowed"):
                    self.download(opener, url=url)
                self.assertEqual(opener.requests, [])
        with self.assertRaisesRegex(ValueError, "invalid_sha256"):
            self.download(FakeOpener(), sha256="A" * 64)
        with self.assertRaises(retry.GitHubReadError) as caught:
            self.download(FakeOpener(), token="")
        self.assertEqual(caught.exception.status, "token_missing")

    def test_token_is_an_unredirected_bearer_header_and_redirects_are_refused(self):
        opener = FakeOpener(FakeResponse())
        self.download(opener)
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.unredirected_hdrs, {"Authorization": "Bearer " + SYNTHETIC_TOKEN})
        self.assertNotIn("Authorization", request.headers)
        # The attempt's whole remaining budget is its one absolute deadline.
        self.assertEqual(timeout, retry.MAX_TOTAL_SECONDS)
        self.assertIsNone(retry.NoRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://example.invalid/"))
        opener = retry.default_opener()
        self.assertTrue(any(isinstance(handler, retry.NoRedirect) for handler in opener.handlers))
        https = next(h for h in opener.handlers if isinstance(h, retry.urllib.request.HTTPSHandler))
        self.assertEqual(https._context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsInstance(https, retry.DeadlineHTTPSHandler)
        self.assertIs(https._context.sslsocket_class, retry.DeadlineSSLSocket)
        self.assertTrue(issubclass(retry.DeadlineSSLSocket, retry.DeadlineIO))


class ApiGetTests(unittest.TestCase):
    TIMELINE = "repos/jeter-1/hass-mcp-admin/issues/164/timeline?per_page=100"

    def test_pages_are_numbered_concatenated_and_bounded(self):
        full = json.dumps([{"id": index} for index in range(100)]).encode()
        fake = FakeGh((included(200, full), 0), (included(200, b'[{"id":100}]'), 0))
        value = json.loads(retry.api_get(self.TIMELINE, 50, fake, **Clock().bounds()))
        self.assertEqual([item["id"] for item in value], list(range(101)))
        self.assertEqual([args[2] for args, _ in fake.calls],
                         [self.TIMELINE + "&page=1", self.TIMELINE + "&page=2"])
        fake = FakeGh(*[(included(200, full), 0)] * 3)
        with self.assertRaisesRegex(ValueError, "pagination_bound"):
            retry.api_get(self.TIMELINE, 3, fake, **Clock().bounds())
        self.assertEqual(json.loads(retry.api_get(
            self.TIMELINE, 1, FakeGh((included(200, b"[]"), 0)), **Clock().bounds())), [])

    def test_routes_are_repository_relative_and_pagination_is_explicit(self):
        for route, pages in (
            ("https://api.github.com/repos/o/r/pulls/1", None), ("graphql", None),
            ("repos/o/r/../x/pulls/1", None), ("repos/o/r//pulls", None),
            ("/repos/o/r/pulls/1", None), ("repos/o/r/pulls/1 --method POST", None),
            ("repos/o/r/issues/1/timeline", 2), ("repos/o/r/issues/1/timeline?per_page=50", 2),
            (self.TIMELINE + "&page=2", 2), (self.TIMELINE, 0), (self.TIMELINE, 51),
        ):
            with self.subTest(route=route, pages=pages):
                fake = FakeGh()
                with self.assertRaises(ValueError):
                    retry.api_get(route, pages, fake, **Clock().bounds())
                self.assertEqual(fake.calls, [])

    def test_page_length_and_aggregate_are_bounded_locally(self):
        oversized = json.dumps([{"id": index} for index in range(101)]).encode()
        fake = FakeGh((included(200, oversized), 0), (included(200, b"[]"), 0))
        with self.assertRaisesRegex(ValueError, "github_page_oversized"):
            retry.api_get(self.TIMELINE, 50, fake, **Clock().bounds())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(retry.MAX_ITEMS, 5000)
        full = json.dumps([{"id": index} for index in range(100)]).encode()
        fake = FakeGh(*[(included(200, full), 0)] * 3)
        with patch.object(retry, "MAX_ITEMS", 150), \
                self.assertRaisesRegex(ValueError, "pagination_bound"):
            retry.api_get(self.TIMELINE, 50, fake, **Clock().bounds())
        self.assertEqual(len(fake.calls), 2)
        # Exactly 5,000 events across 50 full pages still fails closed: a full
        # last page cannot prove the timeline ended.
        fake = FakeGh(*[(included(200, full), 0)] * 50)
        with self.assertRaisesRegex(ValueError, "pagination_bound"):
            retry.api_get(self.TIMELINE, 50, fake, **Clock().bounds())
        self.assertEqual(len(fake.calls), 50)
        partial = json.dumps([{"id": index} for index in range(99)]).encode()
        fake = FakeGh(*[(included(200, full), 0)] * 49, (included(200, partial), 0))
        self.assertEqual(len(json.loads(retry.api_get(self.TIMELINE, 50, fake,
                                                      **Clock().bounds()))), 4999)

    def test_a_failed_page_fails_the_whole_read(self):
        full = json.dumps([{}] * 100).encode()
        fake = FakeGh((included(200, full), 0), gh_status(404))
        with self.assertRaises(retry.GitHubReadError):
            retry.api_get(self.TIMELINE, 5, fake, **Clock().bounds())


class RedactionTests(unittest.TestCase):
    """The token never reaches standard output, standard error or files."""

    def run_main(self, argv, opener=None):
        stdout, stderr = io.BytesIO(), io.StringIO()
        with patch.object(retry.time, "sleep"), patch.object(retry, "default_opener", lambda: opener):
            code = retry.main(argv, {"GH_TOKEN": SYNTHETIC_TOKEN}, stdout, stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_no_token(self, *outputs):
        for output in outputs:
            text = output.decode("latin-1") if isinstance(output, bytes) else output
            self.assertNotIn(SYNTHETIC_TOKEN, text)
            self.assertNotIn(SYNTHETIC_TOKEN[4:20], text)

    def test_a_message_that_would_contain_the_token_is_redacted(self):
        # Defense in depth: no current message carries the token, but if one
        # ever did, main redacts it before printing.
        leaked = retry.GitHubReadError(retry.DOWNLOAD_HOST, SYNTHETIC_TOKEN, 1, False)
        with patch.object(retry, "download", side_effect=leaked):
            code, stdout, stderr = self.run_main(
                ["download", "--url", CORE_URL, "--output", os.devnull])
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "GitHub read failed: host codeload.github.com, last status ***, "
                         "1 attempt(s), not retried\n")
        self.assert_no_token(stdout, stderr)
        self.assertEqual(retry.redact("a " + SYNTHETIC_TOKEN + " b", SYNTHETIC_TOKEN), "a *** b")
        self.assertEqual(retry.redact("no token", ""), "no token")

    def test_download_failures_and_success_never_print_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "core.tar.gz"
            cases = (
                ([http_error(403)], 1, "last status 403"),
                ([http_error(503)] * 5, 1, "last status 503, 5 attempt(s), retry bound reached"),
                ([FakeResponse(SYNTHETIC_TOKEN.encode())], 1, "last status sha256_mismatch"),
                ([FakeResponse()], 0, ""),
            )
            for items, expected, message in cases:
                with self.subTest(message=message):
                    opener = FakeOpener(*items)
                    code, stdout, stderr = self.run_main(
                        ["download", "--url", CORE_URL, "--output", str(output),
                         "--sha256", BODY_SHA], opener)
                    self.assertEqual(code, expected)
                    self.assertIn(message, stderr)
                    if expected:
                        self.assertIn("host codeload.github.com", stderr)
                    self.assert_no_token(stdout, stderr)
                    for path in Path(directory).iterdir():
                        self.assert_no_token(path.read_bytes())

    def test_refusals_print_only_fixed_categories(self):
        code, stdout, stderr = self.run_main(
            ["download", "--url", CORE_URL + "?" + SYNTHETIC_TOKEN, "--output", "x"])
        self.assertEqual((code, stderr), (1, "GitHub read refused: download_url_not_allowed\n"))
        self.assert_no_token(stdout, stderr)

    def test_cli_api_get_never_prints_the_token_from_gh_or_responses(self):
        # A hostile fake gh echoes the token on stderr and in the 403 body.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "gh.log"
            fake = root / "gh"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$MOCK_GH_LOG\"\n"
                "printf 'token %s\\n' \"$GH_TOKEN\" >&2\n"
                "printf 'HTTP/2.0 403 Forbidden\\r\\n\\r\\n{\"message\":\"%s\"}' \"$GH_TOKEN\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            script = root / "github_retry.py"
            script.write_bytes(HELPER.read_bytes())
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(script), "api-get", "--route", ROUTE],
                cwd=root, env={"PATH": f"{root}:/usr/bin:/bin", "GH_TOKEN": SYNTHETIC_TOKEN,
                               "MOCK_GH_LOG": str(log)},
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "GitHub read failed: host api.github.com, "
                             "last status 403, 1 attempt(s), not retried\n")
            self.assert_no_token(result.stdout, result.stderr, log.read_text())
            self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_cli_gh_tls_refusal_is_final_and_its_diagnostic_is_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "gh.log"
            fake = root / "gh"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$MOCK_GH_LOG\"\n"
                "printf 'Get \"https://api.github.com/x\": tls: failed to verify certificate: "
                "x509: synthetic %s\\n' \"$GH_TOKEN\" >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            script = root / "github_retry.py"
            script.write_bytes(HELPER.read_bytes())
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(script), "api-get", "--route", ROUTE],
                cwd=root, env={"PATH": f"{root}:/usr/bin:/bin", "GH_TOKEN": SYNTHETIC_TOKEN,
                               "MOCK_GH_LOG": str(log)},
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "GitHub read failed: host api.github.com, "
                             "last status tls_failed, 1 attempt(s), not retried\n")
            self.assert_no_token(result.stdout, result.stderr, log.read_text())
            self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_consumers_report_host_and_status_but_not_the_token(self):
        for name in ("validate_ci_provenance", "publication_handoff"):
            module_spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            reader = module.github_get if name == "validate_ci_provenance" else module.bounded_read_api
            fake = FakeGh(*[gh_status(429)] * 5)
            with self.subTest(script=name), patch.object(module.subprocess, "run", fake), \
                    patch.object(module.time, "sleep"), \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                    patch.dict(os.environ, {"GH_TOKEN": SYNTHETIC_TOKEN}):
                with self.assertRaises(module.Refusal) as caught:
                    reader(ROUTE)
                self.assertEqual(str(caught.exception), "github_read_failed")
                self.assertEqual(len(fake.calls), 5)
                self.assertIn("host api.github.com, last status 429", stderr.getvalue())
                self.assert_no_token(stderr.getvalue(), str(caught.exception))


class BackstopAndWorkflowTests(unittest.TestCase):
    def test_helper_passes_the_hamcp_204_static_backstop_and_runs_isolated(self):
        self.assertEqual(_stdlib_violations(HELPER), [])
        self.assertEqual(_sibling_imports(HELPER), set())
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / HELPER.name
            script.write_bytes(HELPER.read_bytes())
            result = _isolated_run(script, "--help")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)

    def test_consumers_handoff_produce_uses_the_bounded_reader_and_verify_does_not(self):
        source = (ROOT / "scripts/publication_handoff.py").read_text(encoding="utf-8")
        self.assertIn("def produce(env, ready, api=bounded_read_api, run_git=git):", source)
        self.assertIn("def verify(run_id, attempt, authority, api=read_api,", source)

    def test_ci_alarmo_step_is_the_only_ci_change_and_stays_read_only(self):
        workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertNotIn("permissions", workflow["jobs"]["real-ha-contract-tests"])
        users = [
            (job_name, step.get("name"))
            for job_name, job in workflow["jobs"].items()
            for step in job.get("steps", [])
            if "github_retry.py" in str(step.get("run", "")) or "GH_TOKEN" in (step.get("env") or {})
        ]
        self.assertEqual(users, [(
            "real-ha-contract-tests",
            "Stage exact Alarmo and independent interval observer for the Core slot",
        )])

    def test_merge_job_reads_use_the_helper_and_permissions_are_unchanged(self):
        workflow = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
        job = workflow["jobs"]["merge-authorized-head"]
        self.assertEqual(job["permissions"], {
            "actions": "read", "checks": "read", "contents": "write", "issues": "read",
            "pull-requests": "write",
        })
        steps = {step.get("name"): str(step.get("run", "")) for step in job["steps"]}
        wait = steps["Wait for the exact-head deterministic validate check"]
        merge = steps["Revalidate Ready authority and merge the exact head"]
        for script in (wait, merge):
            self.assertNotIn("gh api", script)
        helper = "python3 -I -S scripts/github_retry.py api-get"
        self.assertEqual(wait.count(helper), 1)
        self.assertEqual(merge.count(helper), 2)
        self.assertIn('api-get --pages 50 \\\n  --route '
                      '"repos/${REPOSITORY}/issues/${PR_NUMBER}/timeline?per_page=100"', merge)
        self.assertEqual(re.findall(r"gh pr merge[^\n]*", merge), ['gh pr merge "$PR_NUMBER" \\'])
        # The pre-checkout context read cannot run repository scripts; unchanged.
        self.assertIn('pr_json="$(gh api "repos/${REPOSITORY}/pulls/${PR_NUMBER}")"',
                      steps["Resolve trusted pull-request context"])


if __name__ == "__main__":
    unittest.main()
