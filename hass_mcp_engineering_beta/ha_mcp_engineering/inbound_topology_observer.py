"""Opt-in, one-use private observation of the MCP listener boundary.

No endpoint, authorization, provider dispatch, request-body access or public data
projection. File absence leaves the ordinary listener unchanged.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit

import uvicorn

from . import version

PRIVATE_ROOT = Path("/data/private-topology-probe")
MAX_ARM_BYTES = 4096
MAX_ARM_SECONDS = 900
LOGGER = logging.getLogger(__name__)

MARKER_HEADER = b"x-engineering-topology-probe"
REQUEST_HEADER = b"x-engineering-topology-request"
FORWARDING_NAMES = (
    b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto",
    b"x-forwarded-port", b"x-forwarded-server", b"x-real-ip", b"cf-connecting-ip",
    b"cf-visitor", b"via",
)
WINDOW_SECONDS = 180
MAX_RECORDS = 48
MAX_HEADERS = 256
MAX_HEADER_NAME = 256
MAX_VALUES = 4
MAX_VALUE_BYTES = 512
MAX_RECORD_BYTES = 4096
MAX_EXPORT_BYTES = 131072


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def authority_valid(value):
    if not value or len(value) > MAX_VALUE_BYTES:
        return False
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        return False
    if any(c in value for c in "@/?#,%\\"):
        return False
    try:
        parsed = urlsplit("//" + value)
        if parsed.username is not None or parsed.password is not None:
            return False
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return False
        if value.endswith(":"):
            return False
        host = parsed.hostname
        if not host:
            return False
        if ":" in host:
            ipaddress.IPv6Address(host)
            return bool(re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]{1,5})?", value))
        labels = (host[:-1] if host.endswith(".") else host).split(".")
        return len(host) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
            for part in labels)
    except (ValueError, UnicodeError):
        return False


def project_value(name, raw):
    # Nonconforming content is never echoed or hashed. This is an export
    # allowlist, not an inbound authorization/normalization policy.
    result = {"bytes": len(raw), "value_disclosed": False}
    if len(raw) > MAX_VALUE_BYTES:
        return {**result, "category": "value_bound"}
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return {**result, "category": "unsafe_syntax"}
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        return {**result, "category": "unsafe_syntax"}
    if name == b"origin":
        try:
            parts = urlsplit(value)
            safe = (parts.scheme in ("http", "https") and not parts.path and
                    not any(c in value for c in "?#") and authority_valid(parts.netloc))
        except ValueError:
            safe = False
    else:
        safe = authority_valid(value)
    if not safe:
        return {**result, "category": "unsafe_syntax"}
    return {**result, "value_disclosed": True, "value": value}


class Recorder:
    def __init__(self, marker, expected_source, current_source, *, clock=time.monotonic, port=8100,
                 utc_now=lambda: datetime.now(timezone.utc).isoformat()):
        if not re.fullmatch(r"topo-[0-9a-f]{32}", marker):
            raise ValueError("invalid_marker")
        if not re.fullmatch(r"[0-9a-f]{40}", expected_source):
            raise ValueError("invalid_source")
        if current_source() != expected_source:
            raise ValueError("identity_mismatch")
        self.marker = marker
        self.expected_source = expected_source
        self.current_source = current_source
        self.clock = clock
        self.utc_now = utc_now
        self.started_at = clock()
        self.deadline = self.started_at + WINDOW_SECONDS
        self.port = port
        self.state = "ACTIVE"
        self.failure = None
        self.records = []
        self.seen_requests = set()

    def stop(self, state, reason=None):
        if self.state == "ACTIVE":
            self.state, self.failure = state, reason

    def tick(self):
        if self.state == "ACTIVE" and self.clock() >= self.deadline:
            self.stop("WINDOW_CLOSED")

    def capture(self, scope):
        self.tick()
        if self.state != "ACTIVE" or scope.get("type") != "http":
            return
        if self.current_source() != self.expected_source:
            self.stop("FAILED", "identity_drift")
            return
        server = scope.get("server")
        if not isinstance(server, (tuple, list)) or len(server) != 2 or server[1] != self.port:
            return  # excludes approval ingress and unknown listeners
        headers = scope.get("headers")
        if not isinstance(headers, (tuple, list)) or len(headers) > MAX_HEADERS:
            self.stop("FAILED", "header_collection_bound")
            return
        selected = {key: [] for key in (MARKER_HEADER, REQUEST_HEADER, b"host", b"origin", *FORWARDING_NAMES)}
        for pair in headers:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                self.stop("FAILED", "header_shape")
                return
            name, value = pair
            if type(name) is not bytes or type(value) is not bytes or len(name) > MAX_HEADER_NAME:
                self.stop("FAILED", "header_shape")
                return
            key = name.lower()
            if key in selected:
                selected[key].append(value)
        markers = selected[MARKER_HEADER]
        if not markers:
            return
        if len(markers) != 1:
            self.stop("FAILED", "duplicate_marker")
            return
        if markers[0] != self.marker.encode("ascii"):
            return
        indexes = selected[REQUEST_HEADER]
        if len(indexes) != 1 or not re.fullmatch(rb"[1-9][0-9]?", indexes[0]):
            self.stop("FAILED", "request_correlation_invalid")
            return
        request_index = int(indexes[0])
        if request_index > MAX_RECORDS or request_index in self.seen_requests:
            self.stop("FAILED", "request_correlation_conflict")
            return
        if scope.get("method") not in ("POST", "GET", "DELETE"):
            self.stop("FAILED", "unexpected_marked_method")
            return
        if len(self.records) >= MAX_RECORDS:
            self.stop("FAILED", "record_limit")
            return
        peer = scope.get("client")
        try:
            if not isinstance(peer, (tuple, list)) or len(peer) != 2:
                raise ValueError()
            for address in (peer[0], server[0]):
                if type(address) is not str or len(address) > 45 or "%" in address:
                    raise ValueError()
                ipaddress.ip_address(address)
        except (ValueError, TypeError):
            self.stop("FAILED", "socket_identity_unavailable")
            return
        if any(len(selected[name]) > MAX_VALUES for name in (b"host", b"origin")):
            self.stop("FAILED", "header_value_count_bound")
            return
        record = {
            "ordinal": len(self.records) + 1,
            "request_index": request_index,
            "received_at": self.utc_now(),
            "elapsed_ms": round((self.clock() - self.started_at) * 1000),
            "method": scope["method"],
            "listener": {"address": server[0], "port": server[1]},
            "immediate_peer": peer[0],
            "headers": {name.decode(): {"count": len(selected[name]),
                         "values": [project_value(name, raw) for raw in selected[name]]}
                        for name in (b"host", b"origin")},
            "forwarding_header_counts": {name.decode(): len(selected[name])
                                         for name in FORWARDING_NAMES},
        }
        if len(encoded(record)) > MAX_RECORD_BYTES:
            self.stop("FAILED", "record_byte_bound")
            return
        # Reserve room for a bounded failure/seal summary without truncation.
        if len(encoded(self.records + [record])) > MAX_EXPORT_BYTES - 1024:
            self.stop("FAILED", "export_byte_bound")
            return
        self.records.append(record)
        self.seen_requests.add(request_index)

    def report(self):
        self.tick()
        return {"state": self.state, "failure": self.failure, "marker": self.marker,
                "source_sha": self.expected_source, "records": self.records,
                "capture_complete": False,
                "limitation": "completion_requires_separate_client_and_identity_verification"}


class ObservationRefused(Exception):
    """Internal fixed category; never constructed from external error text."""


def _utcnow():
    return datetime.now(timezone.utc)


def _utc(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ObservationRefused("arm_timestamp_invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ObservationRefused("arm_timestamp_invalid") from None


def build_identity():
    if (not re.fullmatch(r"[0-9a-f]{40}", version.BUILD_SHA)
            or version.BUILD_DIRTY is not False or version.BUILD_TIME == "unknown"):
        raise ObservationRefused("clean_build_identity_unavailable")
    return {"source_sha": version.BUILD_SHA, "version": version.SERVER_VERSION,
            "built_at": version.BUILD_TIME, "dirty": False}


def _closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationRefused("duplicate_json_key")
        result[key] = value
    return result


def parse_arm(raw, identity, port, now):
    try:
        arm = json.loads(raw, object_pairs_hook=_closed_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ObservationRefused("arm_json_invalid") from None
    keys = {"schema_version", "capture_id", "marker", "expected_source",
            "listener_port", "issued_at", "expires_at"}
    if type(arm) is not dict or set(arm) != keys:
        raise ObservationRefused("arm_fields_invalid")
    if (type(arm["schema_version"]) is not int or arm["schema_version"] != 1
            or type(arm["listener_port"]) is not int or arm["listener_port"] != port):
        raise ObservationRefused("arm_contract_mismatch")
    capture_id = arm["capture_id"]
    if type(capture_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", capture_id):
        raise ObservationRefused("capture_id_invalid")
    if arm["marker"] != "topo-" + capture_id:
        raise ObservationRefused("marker_mismatch")
    if arm["expected_source"] != identity["source_sha"]:
        raise ObservationRefused("source_mismatch")
    issued, expires = _utc(arm["issued_at"]), _utc(arm["expires_at"])
    if not 0 < (expires - issued).total_seconds() <= MAX_ARM_SECONDS:
        raise ObservationRefused("arm_interval_invalid")
    if not issued <= now < expires:
        raise ObservationRefused("arm_not_current")
    return arm


def _check_fd(fd, *, directory=False):
    info = os.fstat(fd)
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not valid_type or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise ObservationRefused("private_descriptor_invalid")
    return info


def _open_root(root):
    # Pin every directory component; O_NOFOLLOW on only the final path would
    # still follow a substituted ancestor. Ancestor permissions may differ.
    root = Path(root)
    if not root.is_absolute() or any(p in (".", "..") for p in root.parts):
        raise ObservationRefused("private_path_invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        _check_fd(fd, directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_arm(root_fd):
    fd = os.open("arm.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=root_fd)
    try:
        before = _check_fd(fd)
        if before.st_size > MAX_ARM_BYTES:
            raise ObservationRefused("arm_size_bound")
        raw = bytearray()
        while len(raw) <= MAX_ARM_BYTES:
            chunk = os.read(fd, MAX_ARM_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        after = _check_fd(fd)
        current = os.stat("arm.json", dir_fd=root_fd, follow_symlinks=False)
        binding = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if binding(before) != binding(after) or binding(after) != binding(current):
            raise ObservationRefused("arm_changed_during_read")
        if len(raw) > MAX_ARM_BYTES or len(raw) != after.st_size:
            raise ObservationRefused("arm_size_bound")
        return bytes(raw)
    finally:
        os.close(fd)


def _write_exclusive(directory_fd, name, raw):
    if len(raw) > MAX_EXPORT_BYTES:
        raise ObservationRefused("export_size_bound")
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        _check_fd(fd)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise ObservationRefused("short_export_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)


class ObservationSession:
    """A consumed arm: no rearming, polling, background thread or network I/O."""

    def __init__(self, root, root_fd, attempt_fd, arm, identity, *, now=_utcnow,
                 clock=time.monotonic, identity_reader=build_identity):
        self.root, self.root_fd, self.attempt_fd = root, root_fd, attempt_fd
        self.arm, self.identity = arm, identity
        self.now, self.clock, self.identity_reader = now, clock, identity_reader
        self.recorder = None
        self.timer = None
        self.closed = False
        self.export_status = "pending"
        self.prepared_at = clock()
        self.start_deadline = self.prepared_at + (_utc(arm["expires_at"]) - now()).total_seconds()

    def start(self, loop):
        if self.closed or self.recorder is not None:
            return
        if self.identity_reader() != self.identity:
            self.finish("IDENTITY_DRIFT")
            return
        remaining = min(self.start_deadline - self.clock(),
                        (_utc(self.arm["expires_at"]) - self.now()).total_seconds())
        if remaining <= 0:
            self.finish("ARM_EXPIRED")
            return
        self.recorder = Recorder(self.arm["marker"], self.identity["source_sha"],
                                 lambda: self.identity_reader()["source_sha"],
                                 clock=self.clock, port=self.arm["listener_port"],
                                 utc_now=lambda: self.now().isoformat())
        self.recorder.deadline = self.clock() + min(WINDOW_SECONDS, remaining)
        self.timer = loop.call_later(min(WINDOW_SECONDS, remaining), self.finish, "WINDOW_CLOSED")

    def capture(self, scope):
        if self.closed or self.recorder is None:
            return
        try:
            if self.now() >= _utc(self.arm["expires_at"]):
                self.finish("ARM_EXPIRED")
                return
            if self.identity_reader() != self.identity:
                self.recorder.stop("FAILED", "identity_drift")
            else:
                self.recorder.capture(scope)
            if self.recorder.state != "ACTIVE":
                self.finish(self.recorder.state)
        except Exception:
            if self.recorder is not None:
                self.recorder.stop("FAILED", "internal_capture_failure")
            self.finish("FAILED")

    def finish(self, reason):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        try:
            if self.recorder is None:
                report = {"state": reason, "failure": "listener_not_started", "records": [],
                          "capture_complete": False}
            else:
                self.recorder.stop(reason)
                report = self.recorder.report()
            report.update({"schema_version": 1, "capture_id": self.arm["capture_id"],
                           "marker": self.arm["marker"], "runtime": self.identity,
                           "finished_at": self.now().isoformat()})
            try:
                current_identity = self.identity_reader()
            except Exception:
                current_identity = None
            if current_identity != self.identity:
                report["state"], report["failure"] = "FAILED", "identity_drift"
            # Recheck the pinned directory against its current namespace before
            # writing private values. A rename/swap refuses rather than escaping.
            current_root = _open_root(self.root)
            try:
                original = _check_fd(self.root_fd, directory=True)
                actual = _check_fd(current_root, directory=True)
                if (original.st_dev, original.st_ino) != (actual.st_dev, actual.st_ino):
                    raise ObservationRefused("private_root_changed")
                entry = os.stat(self.arm["capture_id"], dir_fd=current_root, follow_symlinks=False)
                held = _check_fd(self.attempt_fd, directory=True)
                if (entry.st_dev, entry.st_ino) != (held.st_dev, held.st_ino):
                    raise ObservationRefused("attempt_directory_changed")
                _write_exclusive(self.attempt_fd, "report.json", encoded(report))
                self.export_status = "written"
            finally:
                os.close(current_root)
        except Exception:
            self.export_status = "failed"
            LOGGER.warning("topology_observation_export_failed")
        finally:
            for name in ("attempt_fd", "root_fd"):
                fd = getattr(self, name)
                if fd is not None:
                    setattr(self, name, None)
                    try:
                        os.close(fd)
                    except OSError:
                        self.export_status = "failed"
                        LOGGER.warning("topology_observation_cleanup_failed")
            if self.recorder is not None:
                self.recorder.records.clear()


def prepare_observation(port, *, root=PRIVATE_ROOT, now=_utcnow, clock=time.monotonic,
                        identity_reader=build_identity):
    """No arm means no observer. Every failure refuses capture, not service."""
    root_fd = attempt_fd = None
    try:
        try:
            root_fd = _open_root(root)
            raw = _read_arm(root_fd)
        except FileNotFoundError:
            return None
        identity = dict(identity_reader())
        arm = parse_arm(raw, identity, port, now())
        if _read_arm(root_fd) != raw:
            raise ObservationRefused("arm_changed_before_claim")
        os.mkdir(arm["capture_id"], 0o700, dir_fd=root_fd)
        os.fsync(root_fd)
        attempt_fd = os.open(arm["capture_id"], os.O_RDONLY | os.O_DIRECTORY
                             | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        _check_fd(attempt_fd, directory=True)
        _write_exclusive(attempt_fd, "consumed.json", encoded({
            "schema_version": 1, "capture_id": arm["capture_id"],
            "source_sha": identity["source_sha"], "claimed_at": now().isoformat(),
            "disposition": "consumed_not_proof_of_collection"}))
        session = ObservationSession(root, root_fd, attempt_fd, arm, identity,
                                     now=now, clock=clock, identity_reader=identity_reader)
        root_fd = attempt_fd = None  # ownership transfers exactly once
        return session
    except Exception:
        LOGGER.warning("topology_observation_arm_refused")
        return None
    finally:
        for fd in (attempt_fd, root_fd):
            if fd is not None:
                os.close(fd)


class BoundaryObserver:
    def __init__(self, app, observation):
        self.app, self.observation = app, observation

    async def __call__(self, scope, receive, send):
        try:
            self.observation.capture(scope)
        except Exception:
            try:
                self.observation.finish("CAPTURE_FAILED")
            except Exception:
                LOGGER.warning("topology_observation_retirement_failed")
        return await self.app(scope, receive, send)


class ObservedConfig(uvicorn.Config):
    def __init__(self, *args, observation, **kwargs):
        self.observation = observation
        super().__init__(*args, **kwargs)

    def load(self):
        super().load()
        self.loaded_app = BoundaryObserver(self.loaded_app, self.observation)


class ObservedServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            try:
                self.config.observation.start(asyncio.get_running_loop())
            except Exception:
                LOGGER.warning("topology_observation_start_failed")
                self.config.observation.finish("START_FAILED")

    async def serve(self, sockets=None):
        try:
            await super().serve(sockets=sockets)
        finally:
            self.config.observation.finish("SHUTDOWN")


def create_mcp_listener(app, *, port, log_level):
    observation = prepare_observation(port)
    options = {"host": "0.0.0.0", "port": port, "log_level": log_level, "access_log": False}
    if observation is None:
        return uvicorn.Server(uvicorn.Config(app, **options))
    try:
        return ObservedServer(ObservedConfig(app, observation=observation, **options))
    except BaseException:
        observation.finish("CONFIGURATION_FAILED")
        raise


def finish_mcp_observation(server):
    # Composition may fail before the serve coroutine starts, so its finally
    # block alone cannot own cleanup of an already-consumed private arm.
    if isinstance(server, ObservedServer):
        server.config.observation.finish("COMPOSITION_EXIT")
