"""Bounded, authenticated GitHub reads for CI (HAMCP-221).

Self-contained and standard-library only, so it runs as ``python3 -I -S``.
One retry policy serves two read-only transports:

* ``download`` fetches one exact-commit codeload.github.com archive over
  HTTPS (TLS 1.2 or newer, no redirects) with the job's ``GITHUB_TOKEN``
  (exported as ``GH_TOKEN``), streams it to a temporary file, checks an
  optional exact SHA-256 and only then moves it into place.
* ``api-get`` runs one repository-relative ``gh api`` GET (gh authenticates
  with ``GH_TOKEN``); ``--include`` exposes the HTTP status and Retry-After.

Only HTTP 429, HTTP 5xx and transient network failures are retried, at most
``MAX_ATTEMPTS`` times within ``MAX_TOTAL_SECONDS``, honoring Retry-After up
to ``RETRY_AFTER_CAP_SECONDS`` and otherwise backing off exponentially with
jitter. Every other status (401, 403, 404, any redirect or other 4xx), a
malformed or oversized response and a digest mismatch fail immediately.
Failure messages name only the host and the last status; the token is never
printed, logged or written.
"""

import argparse
import email.utils
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_ATTEMPTS = 5
MAX_TOTAL_SECONDS = 180.0
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 60.0
GH_ATTEMPT_TIMEOUT_SECONDS = 30.0
MIN_ATTEMPT_SECONDS = 1.0
HEADER_BOUND = 65536
UNAVAILABLE_STATUSES = frozenset({"network", "timeout", "gh_unavailable"})


class GitHubReadError(Exception):
    """A read that failed closed; ``str()`` names only the host and status."""

    def __init__(self, host, status, attempts, exhausted):
        self.host = host
        self.status = status
        self.attempts = attempts
        self.exhausted = exhausted
        self.category = ("github_read_unavailable" if status in UNAVAILABLE_STATUSES
                         else "github_read_failed")
        reason = "retry bound reached" if exhausted else "not retried"
        super().__init__(f"GitHub read failed: host {host}, last status {status}, "
                         f"{attempts} attempt(s), {reason}")


class RetryableRead(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(status)
        self.status = status
        self.retry_after = retry_after


class FinalRead(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def retryable_status(status):
    return type(status) is int and (status == 429 or 500 <= status <= 599)


def http_outcome(status, retry_after):
    # Callers return the body for HTTP 200; every other status raises here.
    if retryable_status(status):
        raise RetryableRead(status, retry_after)
    raise FinalRead(status)


def retry_after_seconds(value, now):
    """Parse Retry-After as delta-seconds or an HTTP-date; None if unusable."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,10}", value):
        return float(int(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return max(0.0, when.timestamp() - now)


def backoff_delay(attempt, retry_after, jitter):
    """Seconds to wait before the next attempt; ``jitter`` is in [0, 1)."""
    if retry_after is not None:
        # Honor the server's request, but never wait longer than the cap.
        return min(RETRY_AFTER_CAP_SECONDS, retry_after) + jitter
    exponential = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (attempt - 1))
    return exponential * (0.5 + jitter / 2)


def bounded_retry(attempt_read, host, clock=None, sleep=None, jitter=None, wall=None):
    """Return ``attempt_read(remaining_seconds)`` under the shared bound."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    jitter, wall = jitter or random.random, wall or time.time
    start = clock()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = MAX_TOTAL_SECONDS - (clock() - start)
        try:
            return attempt_read(remaining)
        except FinalRead as exc:
            raise GitHubReadError(host, exc.status, attempt, False) from None
        except RetryableRead as exc:
            delay = backoff_delay(attempt, retry_after_seconds(exc.retry_after, wall()), jitter())
            # No attempt starts unless it can have MIN_ATTEMPT_SECONDS before the
            # total bound; each attempt is limited to the time that remains.
            if (attempt == MAX_ATTEMPTS
                    or clock() - start + delay + MIN_ATTEMPT_SECONDS > MAX_TOTAL_SECONDS):
                raise GitHubReadError(host, exc.status, attempt, True) from None
            sleep(delay)
    raise AssertionError("unreachable")


def parse_included(raw):
    """Split ``gh api --include`` output into status, lowercase headers, body."""
    end = raw.find(b"\n")
    if end < 0 or end > HEADER_BOUND:
        raise FinalRead("malformed_response")
    match = re.fullmatch(rb"HTTP/[0-9.]{1,3} ([0-9]{3})(?: [^\r\n]*)?\r?", raw[:end])
    if match is None:
        raise FinalRead("malformed_response")
    headers = {}
    position = end + 1
    while True:
        end = raw.find(b"\n", position)
        if end < 0 or end > HEADER_BOUND:
            raise FinalRead("malformed_response")
        line = raw[position:end].rstrip(b"\r")
        position = end + 1
        if not line:
            return int(match.group(1)), headers, raw[position:]
        name, separator, value = line.partition(b":")
        if not separator:
            raise FinalRead("malformed_response")
        headers[name.strip().lower().decode("latin-1")] = value.strip().decode("latin-1")


def gh_read_attempt(route, max_bytes, timeout, run=None):
    # The route is repository-relative and GET-only; gh authenticates with
    # GH_TOKEN. Neither gh diagnostics nor response text is exported.
    with tempfile.TemporaryFile() as output:
        try:
            result = (run or subprocess.run)(
                ["gh", "api", route, "--include", "--hostname", "github.com",
                 "--method", "GET", "-H", "Accept: application/vnd.github+json"],
                stdout=output, stderr=subprocess.DEVNULL,
                timeout=min(GH_ATTEMPT_TIMEOUT_SECONDS, timeout), check=False,
            )
        except subprocess.TimeoutExpired:
            raise RetryableRead("timeout") from None
        except OSError:
            raise FinalRead("gh_unavailable") from None
        output.seek(0)
        raw = output.read(HEADER_BOUND + max_bytes + 1)
    if not raw:
        # gh prints no response when none arrived; exit 4 means no credentials.
        if result.returncode == 4:
            raise FinalRead("authentication_required")
        raise RetryableRead("network")
    status, headers, body = parse_included(raw)
    if status == 200 and result.returncode == 0:
        return body[:max_bytes + 1]
    http_outcome(status, headers.get("retry-after"))


def bounded_gh_read(route, max_bytes, run=None, **bounds):
    """One GitHub API GET under the shared bound.  At most ``max_bytes`` + 1
    body bytes are kept, so callers keep their own size refusal."""
    return bounded_retry(lambda timeout: gh_read_attempt(route, max_bytes, timeout, run),
                         "api.github.com", **bounds)


# ---------------------------------------------------------------------------
# Archive downloads (this script only; not copied into other scripts).

DOWNLOAD_HOST = "codeload.github.com"
DOWNLOAD_PATH = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:legacy\.)?tar\.gz/[0-9a-f]{40}")
DOWNLOAD_BOUND = 512 * 1024 * 1024
DOWNLOAD_IDLE_TIMEOUT_SECONDS = 30.0
CHUNK_BYTES = 1 << 20
API_BOUND = 4_000_000
API_ROUTE = re.compile(r"repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+(?:\?[A-Za-z0-9_.=&-]+)?")
MAX_PAGES = 50
PAGE_SIZE = 100


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect surfaces as its 3xx HTTPError and fails closed: the token is
    # never offered to another URL.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_opener():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect)


def network_failure(exc):
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
        exc.close()
        if exc.status == 200:
            return FinalRead("malformed_response")
        try:
            http_outcome(exc.status, retry_after)
        except (RetryableRead, FinalRead) as outcome:
            return outcome
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return FinalRead("tls_verification_failed")
    return RetryableRead("network")


def download_attempt(request, output, sha256, timeout, opener, clock):
    # Every socket operation waits at most ``idle`` and the next one starts only
    # before ``deadline``, so the attempt ends within ``timeout``.
    idle = min(DOWNLOAD_IDLE_TIMEOUT_SECONDS, timeout / 3)
    deadline = clock() + timeout - idle
    descriptor, partial = tempfile.mkstemp(dir=output.parent, prefix=".partial-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            try:
                response = opener.open(request, timeout=idle)
            except (OSError, http.client.HTTPException) as exc:
                raise network_failure(exc) from None
            with response:
                if response.status != 200:
                    http_outcome(response.status, response.headers.get("Retry-After"))
                digest = hashlib.sha256()
                size = 0
                while True:
                    if clock() > deadline:
                        raise RetryableRead("timeout")
                    try:
                        chunk = response.read(CHUNK_BYTES)
                    except (OSError, http.client.HTTPException) as exc:
                        raise network_failure(exc) from None
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > DOWNLOAD_BOUND:
                        raise FinalRead("response_too_large")
                    digest.update(chunk)
                    handle.write(chunk)
        # A digest mismatch is an integrity failure and is never retried.
        if sha256 is not None and digest.hexdigest() != sha256:
            raise FinalRead("sha256_mismatch")
        os.replace(partial, output)
        return {"bytes": size, "sha256": digest.hexdigest()}
    finally:
        if os.path.exists(partial):
            os.unlink(partial)


def download(url, output, token, sha256=None, opener=None, **bounds):
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != DOWNLOAD_HOST or parts.port is not None
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or not DOWNLOAD_PATH.fullmatch(parts.path)):
        raise ValueError("download_url_not_allowed")
    if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("invalid_sha256")
    if not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]{1,4096}", token):
        raise GitHubReadError(DOWNLOAD_HOST, "token_missing", 0, False)
    output = Path(output)
    output.unlink(missing_ok=True)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "hass-mcp-admin-ci"})
    # Unredirected: even a followed redirect could never carry the token.
    request.add_unredirected_header("Authorization", "Bearer " + token)
    opener = opener or default_opener()
    clock = bounds.get("clock") or time.monotonic
    return bounded_retry(
        lambda timeout: download_attempt(request, output, sha256, timeout, opener, clock),
        DOWNLOAD_HOST, **bounds)


def api_get(route, pages=None, run=None, **bounds):
    """Return one route's body, or with ``pages`` the concatenated JSON list
    of numbered pages (never a URL taken from a response)."""
    if not API_ROUTE.fullmatch(route) or ".." in route or "//" in route:
        raise ValueError("api_route_not_allowed")
    if pages is None:
        body = bounded_gh_read(route, API_BOUND, run, **bounds)
        if len(body) > API_BOUND:
            raise ValueError("github_response_too_large")
        return body
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(route).query)
    if query.get("per_page") != [str(PAGE_SIZE)] or "page" in query or not 1 <= pages <= MAX_PAGES:
        raise ValueError("pagination_not_allowed")
    items = []
    for page in range(1, pages + 1):
        body = bounded_gh_read(f"{route}&page={page}", API_BOUND, run, **bounds)
        if len(body) > API_BOUND:
            raise ValueError("github_response_too_large")
        value = json.loads(body)
        if not isinstance(value, list):
            raise ValueError("github_response_malformed")
        items.extend(value)
        if len(value) < PAGE_SIZE:
            return json.dumps(items).encode()
    raise ValueError("pagination_bound")


def redact(message, token):
    return message.replace(token, "***") if token else message


def main(argv=None, environ=os.environ, stdout=None, stderr=None):
    stdout = stdout or sys.stdout.buffer
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("download")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--output", required=True, type=Path)
    fetch.add_argument("--sha256")
    read = commands.add_parser("api-get")
    read.add_argument("--route", required=True)
    read.add_argument("--pages", type=int)
    args = parser.parse_args(argv)
    token = environ.get("GH_TOKEN", "")
    try:
        if args.command == "download":
            result = download(args.url, args.output, token, args.sha256)
            stdout.write((json.dumps(result, sort_keys=True) + "\n").encode())
        else:
            stdout.write(api_get(args.route, args.pages))
    except GitHubReadError as exc:
        print(redact(str(exc), token), file=stderr)
        return 1
    except (ValueError, OSError) as exc:
        # Only the fixed category or exception class; never response text.
        detail = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z0-9_]+", str(exc)) \
            else type(exc).__name__
        print(redact(f"GitHub read refused: {detail}", token), file=stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
