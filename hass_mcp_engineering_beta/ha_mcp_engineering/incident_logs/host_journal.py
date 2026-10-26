"""Read-only host journal reader for the journal sources (HAMCP-224).

With ``journald: true`` the Supervisor bind-mounts the host's
``/var/log/journal`` and ``/run/log/journal`` read-only into this container.
This module reads them with the base image's own ``libsystemd.so.0``
(Debian ``libsystemd0`` 257.13-1~deb13u1) through ``ctypes``:

- **Binding allowlist.** Only the symbols in ``BOUND_SYMBOLS`` are resolved.
  No field-enumeration, unique-value, descriptor, cursor-seek or write
  symbol is bound, so only the fields named in ``READ_FIELDS`` can be read
  (design §7) and a caller value never becomes a libsystemd cursor.
- **One handle.** The container root is opened ``O_RDONLY | O_DIRECTORY |
  O_NOFOLLOW | O_CLOEXEC`` and passed with ``SD_JOURNAL_OS_ROOT``, so
  libsystemd merges the runtime and persistent journals beneath it in one
  order (systemd v257.13 ``sd-journal.c:2206-2224,2448-2477``). The handle
  is opened, read and closed inside one worker thread: every libsystemd call
  refuses a handle used from another process (``journal_origin_changed``).
- **Bounds.** Each field is copied up to ``MAX_FIELD_BYTES`` (257.13 returns
  the whole payload, so the cap is ours), at most ``MAX_CALL_FIELD_BYTES``
  per call, at most ``MAX_SCAN_ENTRIES`` entries per source per call, and
  never past the call deadline. Every step counts against them, the
  earliest/latest edge probes included, and each is checked before the
  step or the copy it limits (HAMCP-224 M3): no field is read once the
  deadline has passed, and no copy takes the call past its field bytes.
- **Positions.** A continuation carries an entry's realtime and a keyed
  digest of its libsystemd cursor, never the cursor (it embeds the raw boot
  ID). It seeks that realtime and walks to the entry with that digest; no
  match is a stale cursor.
- **Unavailability.** Decided from ``/proc/self/mountinfo`` and the open
  itself, never from an empty result (design §8).

Nothing here sanitizes: raw field bytes go to the service's single exit
point, which screens and sanitizes them (``screen_text`` is called only
there).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import errno
import os
import re
import threading
import time
from types import MappingProxyType
from typing import Callable

from ..configuration_definitions import credentials
from ..configuration_definitions.credentials import (
    OMISSION as WITHHELD_OMISSION, UNVERIFIABLE_MARKER, UNVERIFIABLE_OMISSION, WITHHELD_MARKER,
)


LIBRARY = "libsystemd.so.0"
LIBC = "libc.so.6"
LIBSYSTEMD_PIN = "libsystemd0 257.13-1~deb13u1 (systemd v257.13, 70b5d110)"
IDENTIFIERS_PIN = "Supervisor 2026.09.1 (40e3ee76) supervisor/data/syslog-identifiers.json"
READER = "host_journal"
BASIS_JOURNAL = "journal_realtime_utc"
LEVEL_BASIS = "journal_priority"

# The container paths the Supervisor bind-mounts (SV docker/app.py:650-665).
MOUNT_POINTS = ("/var/log/journal", "/run/log/journal")
SD_JOURNAL_OS_ROOT = 1 << 4
OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
MOUNTINFO_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
MAX_MOUNTINFO_BYTES = 1024 * 1024

# A bound that stops a source's scan; it resumes on the next page.
BOUND_REASONS = frozenset({"deadline_reached", "scan_entries_limit", "field_bytes_limit"})
MAX_SCAN_ENTRIES = 20_000
MAX_FIELD_BYTES = 16 * 1024
MAX_CALL_FIELD_BYTES = 4 * 1024 * 1024
# Entries walked from the head or the tail to the first one its source's
# full match accepts, for earliest/latest_available.
EDGE_PROBE_ENTRIES = 64

# The only libsystemd symbols resolved (design §4 as refined in §0).
BOUND_SYMBOLS = (
    "sd_journal_open_directory_fd", "sd_journal_close",
    "sd_journal_add_match", "sd_journal_add_disjunction", "sd_journal_flush_matches",
    "sd_journal_seek_realtime_usec", "sd_journal_seek_head", "sd_journal_seek_tail",
    "sd_journal_next", "sd_journal_previous",
    "sd_journal_get_realtime_usec", "sd_journal_get_cursor", "sd_journal_get_data",
    "sd_journal_get_cutoff_realtime_usec",
)
# The only fields ever read, by name (design §7).
READ_FIELDS = (
    "MESSAGE", "PRIORITY", "SYSLOG_IDENTIFIER", "CONTAINER_NAME", "_TRANSPORT",
    "_KERNEL_SUBSYSTEM", "_UDEV_SYSNAME", "_BOOT_ID", "CONTAINER_PARTIAL_MESSAGE",
)

# The Supervisor's default host identifier set, pinned (52 identifiers).
HOST_IDENTIFIERS = frozenset({
    "NetworkManager", "bluetoothd", "bthelper", "btuart", "containerd", "dbus-broker",
    "dbus-broker-launch", "docker", "docker-prepare", "dockerd", "dropbear", "fstrim",
    "haos-apparmor", "haos-config", "haos-data-disk-detach", "haos-expand", "haos-overlay",
    "haos-persists", "haos-supervisor", "haos-swapfile", "haos-wipe", "haos-zram",
    "hassos-apparmor", "hassos-config", "hassos-expand", "hassos-overlay", "hassos-persists",
    "hassos-supervisor", "hassos-zram", "kernel", "mount", "os-agent", "qemu-ga", "rauc",
    "raucdb-update", "sm-notify", "systemd", "systemd-coredump", "systemd-fsck",
    "systemd-growfs", "systemd-hostnamed", "systemd-journal-gatewayd", "systemd-journald",
    "systemd-logind", "systemd-random-seed", "systemd-resolved", "systemd-sysctl",
    "systemd-time-wait-sync", "systemd-timesyncd", "systemd-tmpfiles", "systemd-udevd",
    "wpa_supplicant",
})
STORAGE_SUBSYSTEMS = frozenset({
    "block", "scsi", "nvme", "mmc", "mmc_host", "ata_port", "ata_link", "ata_device",
})
OS_IDENTIFIERS = frozenset({"rauc", "os-agent", "dockerd", "containerd"} | {
    name for name in HOST_IDENTIFIERS if name.startswith(("haos-", "hassos-"))
})
SUPERVISOR_IDENTIFIER = "hassio_supervisor"

Term = tuple[tuple[str, frozenset[str]], ...]


def _term(**fields: frozenset[str] | set[str] | str) -> Term:
    return tuple(sorted(
        (name, frozenset({values}) if type(values) is str else frozenset(values))
        for name, values in fields.items()
    ))


_KERNEL = _term(_TRANSPORT="kernel")
# Source key -> its match: a disjunction of terms; within a term fields are
# ANDed and the values of one field ORed (libsystemd's match semantics).
HOST_MATCHES = MappingProxyType({
    "supervisor": (_term(SYSLOG_IDENTIFIER=SUPERVISOR_IDENTIFIER),),
    "host": (_term(SYSLOG_IDENTIFIER=HOST_IDENTIFIERS), _KERNEL),
    "host:kernel": (_KERNEL,),
    "host:usb": (_term(_TRANSPORT="kernel", _KERNEL_SUBSYSTEM="usb"),
                 _term(SYSLOG_IDENTIFIER="systemd-udevd")),
    "host:network": (_term(SYSLOG_IDENTIFIER={"NetworkManager", "wpa_supplicant", "systemd-resolved",
                                              "systemd-timesyncd"}),),
    "host:storage": (_term(_TRANSPORT="kernel", _KERNEL_SUBSYSTEM=STORAGE_SUBSYSTEMS),
                     _term(SYSLOG_IDENTIFIER={"systemd-fsck", "fstrim", "systemd-growfs",
                                              "haos-data-disk-detach"})),
    "host:os": (_term(SYSLOG_IDENTIFIER=OS_IDENTIFIERS),),
})
HOST_SOURCES = frozenset(HOST_MATCHES)

# Closed per-source unavailability reasons (design §8; ``journal_mount_incomplete``
# is the §0 refinement for exactly one of the two mounts).
UNAVAILABLE_DETAIL = MappingProxyType({
    "journald_not_enabled": (
        "the add-on option journald is not enabled for this Engineering container; the Supervisor "
        "mounts the host journal only when it is"
    ),
    "journal_mount_incomplete": "only one of /var/log/journal and /run/log/journal is mounted",
    "journal_mount_not_read_only": "a host journal mount is not read-only, so it is not read",
    "journal_unreadable": "the host journal directory or mount table could not be opened",
    "journal_reader_unavailable": "libsystemd.so.0 or one of its bound symbols is unavailable",
    "journal_empty_or_unsupported": "no journal file with entries could be opened",
})


@dataclass(frozen=True)
class SourceMatch:
    """What libsystemd filters on, and the full rule every entry is checked against."""

    library: tuple[Term, ...]
    check: tuple[Term, ...]
    # Fields whose values must be equal (an add-on's identifier and container).
    equal: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(sorted({name for term in self.check for name, _values in term}))


def source_match(key: str) -> SourceMatch:
    if key in HOST_MATCHES:
        return SourceMatch(HOST_MATCHES[key], HOST_MATCHES[key])
    slug = key[len("addon:"):]
    # The Supervisor's own add-on rule (SV api/__init__.py:706-713): the
    # container name, current or legacy. Every entry must also come from that
    # Docker container through journald's native protocol (design §3).
    names = frozenset({f"app_{slug}", f"addon_{slug}"})
    return SourceMatch(
        (_term(SYSLOG_IDENTIFIER=names),),
        (_term(SYSLOG_IDENTIFIER=names, CONTAINER_NAME=names, _TRANSPORT="journal"),),
        ("SYSLOG_IDENTIFIER", "CONTAINER_NAME"),
    )


def accepts(match: SourceMatch, values: dict[str, str | None]) -> bool:
    if match.equal and len({values.get(name) for name in match.equal}) != 1:
        return False
    return any(all(values.get(name) in allowed for name, allowed in term) for term in match.check)


# -- mounts ---------------------------------------------------------------------

_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def mount_options(mountinfo: bytes) -> dict[str, frozenset[str]]:
    """Mount point -> per-mount options of its topmost mount (``proc(5)``)."""

    mounts: dict[str, frozenset[str]] = {}
    for line in mountinfo.decode("utf-8", "replace").splitlines():
        parts = line.split(" ")
        if len(parts) < 6:
            continue
        point = _MOUNT_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), parts[4])
        mounts[point] = frozenset(parts[5].split(","))
    return mounts


def mount_state(mountinfo: bytes) -> str | None:
    """The unavailability reason the mount table gives, or ``None``."""

    mounts = mount_options(mountinfo)
    present = [point for point in MOUNT_POINTS if point in mounts]
    if not present:
        return "journald_not_enabled"
    if any("ro" not in mounts[point] for point in present):
        return "journal_mount_not_read_only"
    if len(present) != len(MOUNT_POINTS):
        return "journal_mount_incomplete"
    return None


def _read_mountinfo(path: str) -> bytes:
    fd = os.open(path, MOUNTINFO_FLAGS)
    try:
        chunks, size = [], 0
        while size <= MAX_MOUNTINFO_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    raise OSError(errno.EFBIG, "mount table exceeds its bound")


# -- binding --------------------------------------------------------------------

class _Library:
    def __init__(self, systemd: ctypes.CDLL, libc: ctypes.CDLL):
        handle, p_handle = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        u64p, sizep = ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_size_t)
        signatures = {
            "sd_journal_open_directory_fd": ([p_handle, ctypes.c_int, ctypes.c_int], ctypes.c_int),
            "sd_journal_close": ([handle], None),
            "sd_journal_add_match": ([handle, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int),
            "sd_journal_add_disjunction": ([handle], ctypes.c_int),
            "sd_journal_flush_matches": ([handle], None),
            "sd_journal_seek_realtime_usec": ([handle, ctypes.c_uint64], ctypes.c_int),
            "sd_journal_seek_head": ([handle], ctypes.c_int),
            "sd_journal_seek_tail": ([handle], ctypes.c_int),
            "sd_journal_next": ([handle], ctypes.c_int),
            "sd_journal_previous": ([handle], ctypes.c_int),
            "sd_journal_get_realtime_usec": ([handle, u64p], ctypes.c_int),
            "sd_journal_get_cursor": ([handle, p_handle], ctypes.c_int),
            "sd_journal_get_data": ([handle, ctypes.c_char_p, p_handle, sizep], ctypes.c_int),
            "sd_journal_get_cutoff_realtime_usec": ([handle, u64p, u64p], ctypes.c_int),
        }
        assert tuple(signatures) == BOUND_SYMBOLS
        for name, (argtypes, restype) in signatures.items():
            function = getattr(systemd, name)
            function.argtypes, function.restype = argtypes, restype
            setattr(self, name[len("sd_journal_"):], function)
        self.free = libc.free
        self.free.argtypes, self.free.restype = [handle], None


_LOAD_LOCK = threading.Lock()
_LOADED: dict[str, _Library | None] = {}


def load_library(name: str = LIBRARY) -> _Library | None:
    """The bound library, or ``None`` when it or a bound symbol is missing."""

    with _LOAD_LOCK:
        if name not in _LOADED:
            try:
                _LOADED[name] = _Library(ctypes.CDLL(name), ctypes.CDLL(LIBC))
            except (OSError, AttributeError):
                _LOADED[name] = None
        return _LOADED[name]


def _errno_name(code: int) -> str:
    return errno.errorcode.get(abs(code), "EUNKNOWN")


class JournalError(Exception):
    def __init__(self, reason: str, code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.errno_name = None if code is None else _errno_name(code)


# -- scan -----------------------------------------------------------------------

@dataclass(frozen=True)
class Record:
    """One journal entry a source's libsystemd match yielded."""

    rt_us: int
    cursor_digest: str
    # ``None`` when accepted; otherwise why it is never returned.
    rejected: str | None = None
    message: bytes | None = None
    message_truncated: bool = False
    # The returned metadata fields (``process``, ``kernel_subsystem``,
    # ``kernel_device``) longer than ``MAX_FIELD_BYTES``: never screened
    # whole, so never shown.
    truncated_fields: tuple[str, ...] = ()
    priority: bytes | None = None
    identifier: bytes | None = None
    kernel_subsystem: bytes | None = None
    kernel_device: bytes | None = None
    boot_ref: str | None = None
    partial_line: bool = False


@dataclass(frozen=True)
class Plan:
    key: str
    start_us: int
    end_us: int
    target: int
    # ``(realtime_us, cursor_digest)`` of the last consumed entry.
    resume: tuple[int, str] | None = None


@dataclass
class SourceScan:
    key: str
    records: list[Record] = field(default_factory=list)
    # "end_reached" or "tail_reached"; ``None`` while more may follow.
    finished: str | None = None
    # The bound that stopped an unfinished scan.
    stopped: str | None = None
    stale: bool = False
    failure: str | None = None
    errno_name: str | None = None
    scanned: int = 0
    resume_walked: int = 0
    # Steps of the earliest/latest edge probes.
    probed: int = 0
    rejected: int = 0
    unreadable: int = 0
    # Entries without a realtime or a cursor: never records, never returned.
    positionless: int = 0
    clock_regressions: int = 0
    earliest_us: int | None = None
    latest_us: int | None = None


@dataclass
class JournalRead:
    """One call's journal reads: a reason every source shares, or per-source scans."""

    unavailable: str | None = None
    errno_name: str | None = None
    cutoff_from_us: int | None = None
    cutoff_to_us: int | None = None
    scans: dict[str, SourceScan] = field(default_factory=dict)
    field_bytes: int = 0


class HostJournalReader:
    def __init__(self, *, root: str = "/", mountinfo: str = "/proc/self/mountinfo",
                 library: Callable[[], _Library | None] = load_library):
        self.root = root
        self.mountinfo = mountinfo
        self.library = library

    def read(self, plans: list[Plan], digest: Callable[[str, bytes], str], deadline: float) -> JournalRead:
        """Run in a worker thread: open, read every plan and close one handle."""

        result = JournalRead()
        try:
            reason = mount_state(_read_mountinfo(self.mountinfo))
        except OSError as error:
            result.unavailable, result.errno_name = "journal_unreadable", _errno_name(error.errno or 0)
            return result
        if reason is not None:
            result.unavailable = reason
            return result
        lib = self.library()
        if lib is None:
            result.unavailable = "journal_reader_unavailable"
            return result
        try:
            root = os.open(self.root, OPEN_FLAGS)
        except OSError as error:
            result.unavailable, result.errno_name = "journal_unreadable", _errno_name(error.errno or 0)
            return result
        try:
            for point in MOUNT_POINTS:
                # The directory itself must open; libsystemd skips one it cannot.
                try:
                    os.close(os.open(point.lstrip("/"), OPEN_FLAGS, dir_fd=root))
                except OSError as error:
                    result.unavailable = "journal_unreadable"
                    result.errno_name = _errno_name(error.errno or 0)
                    return result
            handle = ctypes.c_void_p()
            code = lib.open_directory_fd(ctypes.byref(handle), root, SD_JOURNAL_OS_ROOT)
            if code < 0:
                result.unavailable, result.errno_name = "journal_unreadable", _errno_name(code)
                return result
            try:
                _Session(lib, handle, digest, deadline, result).run(plans)
            finally:
                lib.close(handle)
        finally:
            # Without SD_JOURNAL_TAKE_DIRECTORY_FD the descriptor stays ours.
            os.close(root)
        return result


class _Session:
    def __init__(self, lib: _Library, handle: ctypes.c_void_p, digest: Callable[[str, bytes], str],
                 deadline: float, result: JournalRead):
        self.lib, self.handle, self.digest, self.deadline, self.result = lib, handle, digest, deadline, result

    def run(self, plans: list[Plan]) -> None:
        low, high = ctypes.c_uint64(), ctypes.c_uint64()
        code = self.lib.get_cutoff_realtime_usec(self.handle, ctypes.byref(low), ctypes.byref(high))
        if code < 0:
            self.result.unavailable, self.result.errno_name = "journal_unreadable", _errno_name(code)
            return
        if code == 0:
            # No opened file has an entry: nothing is readable, never "empty".
            self.result.unavailable = "journal_empty_or_unsupported"
            return
        self.result.cutoff_from_us, self.result.cutoff_to_us = low.value, high.value
        for plan in plans:
            scan = SourceScan(plan.key)
            self.result.scans[plan.key] = scan
            try:
                self._scan(plan, scan)
            except JournalError as error:
                scan.failure, scan.errno_name = error.reason, error.errno_name

    # -- primitives

    def _check(self, code: int, reason: str = "journal_read_failed") -> int:
        if code < 0:
            raise JournalError(reason, code)
        return code

    def _matches(self, match: SourceMatch) -> None:
        self.lib.flush_matches(self.handle)
        for number, term in enumerate(match.library):
            if number:
                self._check(self.lib.add_disjunction(self.handle))
            for name, values in term:
                for value in sorted(values):
                    data = f"{name}={value}".encode("utf-8")
                    self._check(self.lib.add_match(self.handle, data, len(data)))

    def _realtime(self) -> int | None:
        value = ctypes.c_uint64()
        if self.lib.get_realtime_usec(self.handle, ctypes.byref(value)) < 0:
            return None
        return value.value

    def _cursor_digest(self) -> str | None:
        pointer = ctypes.c_void_p()
        if self.lib.get_cursor(self.handle, ctypes.byref(pointer)) < 0 or not pointer.value:
            return None
        try:
            return self.digest("journal_cursor", ctypes.string_at(pointer.value))
        finally:
            self.lib.free(pointer)

    def _field(self, name: str) -> tuple[bytes | None, bool]:
        """``(value, truncated)``; ``None`` when absent. Raises on a read error.

        Raises ``_Bound`` before the read once the deadline has passed, and
        before the copy when it would take the call past its field bytes.
        """

        if time.monotonic() >= self.deadline:
            raise _Bound("deadline_reached")
        data, size = ctypes.c_void_p(), ctypes.c_size_t()
        code = self.lib.get_data(self.handle, name.encode("ascii"), ctypes.byref(data), ctypes.byref(size))
        if code == -errno.ENOENT:
            return None, False
        if code < 0 or not data.value:
            raise _EntryUnreadable(code)
        prefix = name.encode("ascii") + b"="
        take = min(size.value, len(prefix) + MAX_FIELD_BYTES)
        if self.result.field_bytes + take > MAX_CALL_FIELD_BYTES:
            raise _Bound("field_bytes_limit")
        raw = ctypes.string_at(data.value, take)
        if not raw.startswith(prefix):
            raise _EntryUnreadable(-errno.EBADMSG)
        self.result.field_bytes += take
        return raw[len(prefix):], size.value > take

    def _values(self, match: SourceMatch) -> dict[str, str | None]:
        values = {}
        for name in match.fields:
            value, truncated = self._field(name)
            values[name] = None if value is None or truncated else value.decode("utf-8", "replace")
        return values

    def _bounded(self, scan: SourceScan) -> str | None:
        if time.monotonic() >= self.deadline:
            return "deadline_reached"
        if scan.scanned + scan.resume_walked + scan.probed >= MAX_SCAN_ENTRIES:
            return "scan_entries_limit"
        if self.result.field_bytes >= MAX_CALL_FIELD_BYTES:
            return "field_bytes_limit"
        return None

    # -- one source

    def _scan(self, plan: Plan, scan: SourceScan) -> None:
        try:
            self._walk(plan, scan)
        except _Bound as bound:
            # A bound met inside a step: the partly read entry is not a record.
            scan.stopped = bound.reason

    def _walk(self, plan: Plan, scan: SourceScan) -> None:
        match = source_match(plan.key)
        self._matches(match)
        if plan.resume is None:
            scan.earliest_us = self._edge(match, scan, self.lib.seek_head, self.lib.next)
            scan.latest_us = self._edge(match, scan, self.lib.seek_tail, self.lib.previous)
            self._check(self.lib.seek_realtime_usec(self.handle, plan.start_us))
        elif not self._resume(plan, scan):
            return
        accepted_in_window = 0
        highest = None
        while True:
            stop = self._bounded(scan)
            if stop is not None:
                scan.stopped = stop
                return
            if not self._check(self.lib.next(self.handle)):
                scan.finished = "tail_reached"
                return
            scan.scanned += 1
            record = self._record(match, plan)
            if record is None:
                continue
            if record.rejected is None and record.rt_us >= plan.end_us:
                scan.finished = "end_reached"
                return
            if highest is not None and record.rt_us < highest:
                scan.clock_regressions += 1
            highest = record.rt_us if highest is None else max(highest, record.rt_us)
            scan.records.append(record)
            if record.rejected is None and record.rt_us >= plan.start_us:
                accepted_in_window += 1
                if accepted_in_window >= plan.target:
                    return

    def _record(self, match: SourceMatch, plan: Plan) -> Record | None:
        rt = self._realtime()
        cursor = self._cursor_digest()
        if rt is None or cursor is None:
            # No position to resume at or time to place it: not a record.
            self.result.scans[plan.key].positionless += 1
            return None
        scan = self.result.scans[plan.key]
        try:
            values = self._values(match)
            if not accepts(match, values):
                scan.rejected += 1
                return Record(rt, cursor, rejected="match_rejected")
            if rt >= plan.end_us:
                return Record(rt, cursor)
            message, truncated = self._field("MESSAGE")
            priority, _ = self._field("PRIORITY")
            identifier, cut_identifier = self._field("SYSLOG_IDENTIFIER")
            boot, _ = self._field("_BOOT_ID")
            partial, _ = self._field("CONTAINER_PARTIAL_MESSAGE")
            transport = values.get("_TRANSPORT")
            if transport is None:
                raw, _ = self._field("_TRANSPORT")
                transport = None if raw is None else raw.decode("utf-8", "replace")
            subsystem = device = None
            cut_subsystem = cut_device = False
            if transport == "kernel":
                subsystem, cut_subsystem = self._field("_KERNEL_SUBSYSTEM")
                device, cut_device = self._field("_UDEV_SYSNAME")
        except _EntryUnreadable:
            scan.unreadable += 1
            return Record(rt, cursor, rejected="entry_unreadable")
        cut = (("process", cut_identifier), ("kernel_subsystem", cut_subsystem), ("kernel_device", cut_device))
        return Record(
            rt, cursor, message=message, message_truncated=truncated, priority=priority,
            identifier=identifier, kernel_subsystem=subsystem, kernel_device=device,
            truncated_fields=tuple(name for name, value in cut if value),
            boot_ref=None if boot is None else self.digest("journal_boot", boot)[:12],
            partial_line=partial == b"true",
        )

    def _edge(self, match: SourceMatch, scan: SourceScan, seek, step) -> int | None:
        self._check(seek(self.handle))
        for _ in range(EDGE_PROBE_ENTRIES):
            stop = self._bounded(scan)
            if stop is not None:
                raise _Bound(stop)
            if not self._check(step(self.handle)):
                return None
            scan.probed += 1
            try:
                values = self._values(match)
            except _EntryUnreadable:
                continue
            if accepts(match, values):
                return self._realtime()
        return None

    def _resume(self, plan: Plan, scan: SourceScan) -> bool:
        realtime, wanted = plan.resume
        self._check(self.lib.seek_realtime_usec(self.handle, realtime))
        while True:
            stop = self._bounded(scan)
            if stop is not None or not self._check(self.lib.next(self.handle)):
                # The consumed entry was not found within the bounds: it was
                # vacuumed or rotated away, or the clock order hides it.
                scan.stale = True
                return False
            scan.resume_walked += 1
            if self._cursor_digest() == wanted:
                return True


class _EntryUnreadable(Exception):
    def __init__(self, code: int):
        super().__init__(_errno_name(code))


class _Bound(Exception):
    """A bound met before a step or a field copy: the scan stops with ``reason``."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# -- exit-point screening (called only from the service's ``_finish``) -----------

# A well-formed ANSI CSI sequence (SGR colours included): ESC [, at most 32
# parameter bytes, at most 4 intermediate bytes and one final byte. It is a
# recognized display format (owner decision, HAMCP-224 review 2), not a
# reason to withhold; any other escape or control character still is.
_CSI = re.compile(r"\x1b\[[0-?]{0,32}[ -/]{0,4}[@-~]")
_CSI_ESCAPE = re.compile(r"\x1b(?=\[[0-?]{0,32}[ -/]{0,4}[@-~])")
OMISSIONS = {WITHHELD_MARKER: WITHHELD_OMISSION, UNVERIFIABLE_MARKER: UNVERIFIABLE_OMISSION}


def screen_text(raw: bytes, known_secrets: tuple[str, ...], truncated: bool = False) -> tuple[str, str | None]:
    """``(text, omission)``: the decoded text without its CSI sequences, or a whole-text marker.

    HAMCP-224 H1/H2: the decision is the configuration-definitions screen
    (``credentials.screen``) with its labelled forms, on the complete field
    as read, before anything is cut for display. A field longer than
    ``MAX_FIELD_BYTES`` (``truncated``) was never read whole, and bytes that
    are not UTF-8 cannot be read as text: both are withheld whole as
    unverifiable.

    Review 2 M4 (owner decision): two forms are screened and either one
    withholds. The text as shown, with every well-formed CSI sequence
    removed, so a credential split by a colour code (``pass``, SGR,
    ``word=``) is seen whole; and the original text with only each such
    sequence's ESC removed, so a label the sequence's own bytes spell
    (``ESC[password=``, whose ``p`` ends the sequence) is seen too. A
    well-formed CSI sequence alone never withholds; any other escape (a
    stray ESC, an OSC title) or control character is in both forms and is
    withheld as unverifiable. The omission is ``unverifiable_text_withheld``
    or ``credential_text_withheld`` when the text is replaced, else ``None``.
    """

    if truncated:
        return UNVERIFIABLE_MARKER, UNVERIFIABLE_OMISSION
    try:
        original = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return UNVERIFIABLE_MARKER, UNVERIFIABLE_OMISSION
    text = _CSI.sub("", original)
    marker = credentials.screen(text, known_secrets, labelled=True)
    if marker is None and text != original:
        marker = credentials.screen(_CSI_ESCAPE.sub("", original), known_secrets, labelled=True)
    if marker is not None:
        return marker, OMISSIONS[marker]
    return text, None


LEVELS_BY_PRIORITY = MappingProxyType({
    b"0": "critical", b"1": "critical", b"2": "critical", b"3": "error", b"4": "warning",
    b"5": "info", b"6": "info", b"7": "debug",
})
