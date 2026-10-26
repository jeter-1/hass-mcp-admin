"""Capture, list, read and watch sanitized automation trace snapshots (HAMCP-225).

Every Core read is ``trace/list`` or ``trace/get`` with ``domain:
"automation"``. Nothing unsanitized reaches the store: the ``trace/get``
result passes the shared trace exit, the complete record passes it again with
the same registry, and the stored bytes are the last pass's output (design
note section 2.3). Every read response passes the strict sanitizer once more
without a registry (decision R1), so a string partly withheld at capture is
withheld whole on read.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from ..errors import EngineeringServerError, HomeAssistantApiError
from ..reliability.timestamps import parse_timestamp
from ..request_context import current_caller_id, current_request_id, current_telemetry
from ..trace_normalization import (
    fetch_normalized_trace_list,
    sanitize_trace_detail,
    trace_sanitization_partial,
)
from ..sanitization import ReferenceMarkerRegistry
from . import contracts as c
from .store import StoreError, TraceSnapshotStore


_RECORD_PASSES = 3
_RUN_TEXT = ("state", "script_execution", "last_step")


def _ms(epoch):
    return int(epoch * 1000)


def _ended_after(header, boundary_ms):
    """Whether a run finished (or, without a finish time, started) after ``boundary_ms``."""

    instant = parse_timestamp(header.finished_at) or header.started_instant
    return instant.timestamp() * 1000 > boundary_ms


def _short(value, limit=160):
    return value if type(value) is str and 0 < len(value) <= limit else None


def _evidence_total(results):
    """Summed counts and the union of categories over every pass."""

    categories = sorted({name for result in results for name in result.redaction_categories})
    return {
        "redacted_field_count": sum(result.redacted_field_count for result in results),
        "categories": categories,
        "category_count": len(categories),
        "marker_collision_count": sum(result.marker_collision_count for result in results),
        "truncated_field_count": sum(result.truncated_field_count for result in results),
        "failed_closed": any(result.failed_closed for result in results),
    }


def _caller():
    """The stored attribution; an unexpected shape is never persisted."""

    caller = current_caller_id()
    return caller if type(caller) is str and c.CALLER.fullmatch(caller) else "unattributed"


def header_matches(mode, header):
    """Whether a stopped run is one the watch's mode captures."""

    if mode == "failures":
        return (header.script_execution in c.FAILURE_EXECUTIONS
                or header.error not in (None, ""))
    return header.script_execution != "failed_conditions" and not header.not_triggered


class Prepared:
    """One sanitized record ready to store, or the outcome that stopped it."""

    __slots__ = ("run_id", "outcome", "record", "raw", "entry")

    def __init__(self, run_id, outcome, record=None, raw=None, entry=None):
        self.run_id, self.outcome = run_id, outcome
        self.record, self.raw, self.entry = record, raw, entry


class TraceSnapshotService:
    def __init__(self, store_root, command, core_runtime, audit, *, known_secrets=(),
                 clock=time.time):
        self.store = TraceSnapshotStore(store_root)
        self.command = command
        self.core_runtime = core_runtime
        self.audit = audit
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self.clock = clock
        # One store owner inside this process; flock covers other processes.
        self.lock = asyncio.Lock()
        self.secret = secrets.token_bytes(32)
        # Set by the poller: Core authority was unavailable at the last tick.
        self.paused = False
        # The watch polled first; advanced every authorized tick (review M2).
        self.rotation = 0

    # ------------------------------------------------------------------
    # Authority, Core reads and record construction

    def authority(self):
        """The request's admitted Core generation and version, or refusal."""

        runtime = self.core_runtime
        if runtime is None:
            raise c.TraceSnapshotError("authority_unavailable")
        status = runtime.route_status(c.REQUIREMENTS)
        observation = runtime.current_observation
        telemetry = current_telemetry()
        if (status.get("available") is not True or observation is None or telemetry is None
                or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            raise c.TraceSnapshotError("authority_unavailable")
        return status["generation"], observation.version

    async def list_runs(self, automation_id):
        try:
            return await fetch_normalized_trace_list(
                self.command, automation_id, known_secrets=self.known_secrets)
        except (EngineeringServerError, TypeError, ValueError):
            raise c.TraceSnapshotError("source_unavailable") from None

    async def read_detail(self, automation_id, run_id):
        """``(outcome, detail)``; the detail is raw and must be sanitized next."""

        try:
            detail = await self.command({"type": "trace/get", "domain": "automation",
                                         "item_id": automation_id, "run_id": run_id})
        except HomeAssistantApiError as exc:
            status = exc.details.get("status") if isinstance(exc.details, dict) else None
            return ("trace_not_retained" if status == 404 else "read_failed"), None
        except EngineeringServerError:
            return "source_unavailable", None
        if not isinstance(detail, dict):
            return "read_failed", None
        return "captured", detail

    def prepare(self, detail, *, automation_id, run_id, origin, watch_id, caller,
                request_id, authority, note, now):
        """Sanitize at capture, before storage (design note section 2.3)."""

        registry = ReferenceMarkerRegistry()
        first = sanitize_trace_detail(detail, known_secrets=self.known_secrets, registry=registry)
        del detail
        value = first.value
        if not isinstance(value, dict):
            return Prepared(run_id, "read_failed")
        if value.get("state") == "running":
            return Prepared(run_id, "run_in_progress")
        snapshot_id = secrets.token_hex(16)
        timestamp = value.get("timestamp") if isinstance(value.get("timestamp"), dict) else {}
        trace = value.get("trace")
        steps = [[path, entries] for path, entries in trace.items()] if isinstance(trace, dict) else []
        passes = [first]
        record = {
            "model": c.RECORD_MODEL,
            "snapshot_id": snapshot_id,
            "automation_id": automation_id,
            "run_id": run_id,
            "origin": origin,
            "watch_id": watch_id,
            "captured_at": c.utc_text(now),
            "captured_by": caller,
            "request_id": request_id,
            "core": {"version": authority[1], "generation": authority[0]},
            "note": note or None,
            "run": {
                **{key: value.get(key) for key in _RUN_TEXT},
                "started_at": timestamp.get("start"),
                "finished_at": timestamp.get("finish"),
                "error": value.get("error"),
                "trigger": value.get("trigger"),
                "not_triggered": value.get("not_triggered") is True,
            },
            "completeness": "partial" if trace_sanitization_partial(first) else "complete",
            "sanitization": _evidence_total(passes),
            "trace": {
                "config": value.get("config"),
                "blueprint_inputs": value.get("blueprint_inputs"),
                "context": value.get("context"),
                "steps": steps,
            },
        }
        # One strict pass over the complete record. A pass that changes
        # anything is tallied, the record is rebuilt from its output and
        # scanned again; only a pass that changes nothing is stored. (A value
        # under a credential-named key is re-counted on every pass even when
        # it is already the marker, so "changed" is judged by the value.)
        for _ in range(_RECORD_PASSES):
            result = sanitize_trace_detail(record, known_secrets=self.known_secrets,
                                           registry=registry)
            if result.value == record and not result.failed_closed:
                record = result.value
                break
            passes.append(result)
            record = result.value
            if not isinstance(record, dict) or record.get("snapshot_id") != snapshot_id:
                return Prepared(run_id, "read_failed")
            record["completeness"] = "partial"
            record["sanitization"] = _evidence_total(passes)
        else:
            return Prepared(run_id, "read_failed")
        try:
            raw = c.canonical(record)
        except (TypeError, ValueError, RecursionError):
            return Prepared(run_id, "read_failed")
        if len(raw) > c.SNAPSHOT_BYTES:
            return Prepared(run_id, "over_size_bound")
        run = record["run"]
        entry = {
            "snapshot_id": snapshot_id, "automation_id": automation_id, "run_id": run_id,
            "origin": origin, "watch_id": watch_id, "captured_at_ms": _ms(now),
            "captured_by": caller, "completeness": record["completeness"],
            "bytes": len(raw), "sha256": c.sha256(raw),
            "state": _short(run.get("state")), "script_execution": _short(run.get("script_execution")),
            "started_at": _short(run.get("started_at")),
        }
        return Prepared(run_id, "captured", record, raw, entry)

    # ------------------------------------------------------------------
    # Store transactions (run in a worker thread under ``self.lock``)

    def _sweep(self, index, now_ms):
        """Drop expired snapshots, quarantine stubs and watches; return what went.

        An aged quarantine stub is reported with the removed snapshots so the
        index is rewritten; its record file was deleted when it was damaged.
        """

        cutoff = now_ms - c.RETENTION_SECONDS * 1000
        removed = [e["snapshot_id"] for e in index["snapshots"] if e["captured_at_ms"] <= cutoff]
        expired = [w for w in index["watches"] if w["expires_at_ms"] <= now_ms]
        quarantine = index["quarantine"]
        aged = [q["snapshot_id"] for q in quarantine["entries"] if q["quarantined_at_ms"] <= cutoff]
        if removed:
            gone = set(removed)
            index["snapshots"] = [e for e in index["snapshots"] if e["snapshot_id"] not in gone]
        if aged:
            quarantine["entries"] = [q for q in quarantine["entries"]
                                     if q["quarantined_at_ms"] > cutoff]
        if expired:
            ended = {w["watch_id"] for w in expired}
            index["watches"] = [w for w in index["watches"] if w["watch_id"] not in ended]
        return removed + aged, expired

    @staticmethod
    def _quarantine(index, entry, damage, now_ms):
        """Replace a damaged entry by a content-free stub; the file goes in ``_tidy``."""

        index["snapshots"] = [e for e in index["snapshots"] if e["snapshot_id"] != entry["snapshot_id"]]
        quarantine = index["quarantine"]
        quarantine["entries"].append({
            "snapshot_id": entry["snapshot_id"], "automation_id": entry["automation_id"],
            "run_id": entry["run_id"], "quarantined_at_ms": now_ms, **damage})
        overflow = len(quarantine["entries"]) - c.QUARANTINE_MAX
        if overflow > 0:
            quarantine["entries"] = quarantine["entries"][overflow:]
            quarantine["dropped"] += overflow

    def _tidy(self, index, removed):
        """After the index commit: unlink removed, orphaned and pending files."""

        committed, pending = self.store.record_files()
        known = {e["snapshot_id"] for e in index["snapshots"]}
        for snapshot_id in set(removed) | (committed - known):
            self.store.remove_record(snapshot_id)
        for name in pending:
            self.store.remove_pending(name)

    def _audit_watch_expiry(self, expired):
        for watch in expired:
            self._write_audit({
                "event": "trace_watch_expired", "tool_name": "set_automation_trace_watch",
                "request_id": watch["request_id"], "result_status": "expired",
                "caller_id": watch["caller_id"], "watch_id": watch["watch_id"],
                "automation_id": watch["automation_id"],
                "captured_count": watch["captured_count"],
            }, required=False)

    def _write_audit(self, entry, *, required=True):
        audit = self.audit
        entry = {**entry, "audit_event_id": c.sha256(c.canonical(
            {"model": "trace-snapshot-audit-v1", "body": entry, "nonce": secrets.token_hex(16)}))}
        if audit is None or not audit.enabled:
            if required:
                raise c.TraceSnapshotError("audit_unavailable")
            return False
        try:
            written = audit.write(entry) is True
        except Exception:
            written = False
        if not written and required:
            raise c.TraceSnapshotError("audit_unavailable")
        return written

    def _maintain(self, now_ms):
        """Sweep, commit when something expired, tidy; return the index."""

        with self.store.transaction():
            index = self.store.load_index()
            removed, expired = self._sweep(index, now_ms)
            if removed or expired:
                self.store.write_index(index)
            self._tidy(index, removed)
        self._audit_watch_expiry(expired)
        return index

    def _commit(self, prepared, now_ms, *, watch_id=None, update_watch=None, audit=None):
        """Store ``prepared`` records in one transaction; return outcomes.

        A manual capture is refused at capacity (E1). A watch capture evicts
        only its own oldest snapshot past the watch quota (E2); the global
        bounds still refuse. ``update_watch(watch)`` applies poll state to
        the current watch row, which may have been replaced or removed since
        the poll began.
        """

        outcomes = {}
        stored, evicted = [], []
        with self.store.transaction():
            index = self.store.load_index()
            removed, expired = self._sweep(index, now_ms)
            watch = None
            if watch_id is not None:
                watch = next((w for w in index["watches"] if w["watch_id"] == watch_id), None)
                if watch is None:
                    # Removed or expired while the poll ran: store nothing.
                    self._tidy_after(index, removed, expired)
                    return {item.run_id: "watch_removed" for item in prepared}, [], []
            runs = {(e["automation_id"], e["run_id"]) for e in index["snapshots"]}
            for item in prepared:
                if item.outcome != "captured":
                    outcomes[item.run_id] = item.outcome
                    continue
                entry = item.entry
                if (entry["automation_id"], entry["run_id"]) in runs:
                    outcomes[item.run_id] = "already_captured"
                    continue
                victims = []
                if watch is not None:
                    own = sorted((e for e in index["snapshots"] if e["watch_id"] == watch_id),
                                 key=lambda e: (e["captured_at_ms"], e["snapshot_id"]))
                    victims = own[:max(0, len(own) - c.WATCH_QUOTA + 1)]
                count = len(index["snapshots"]) - len(victims) + 1
                size = (sum(e["bytes"] for e in index["snapshots"])
                        - sum(e["bytes"] for e in victims) + entry["bytes"])
                if count > c.MAX_SNAPSHOTS or size > c.STORE_BYTES:
                    outcomes[item.run_id] = "store_full"
                    continue
                gone = {e["snapshot_id"] for e in victims}
                index["snapshots"] = [e for e in index["snapshots"] if e["snapshot_id"] not in gone]
                index["snapshots"].append(entry)
                runs.add((entry["automation_id"], entry["run_id"]))
                evicted.extend(sorted(gone))
                stored.append(item)
                outcomes[item.run_id] = "captured"
            if watch is not None:
                watch["captured_count"] += len(stored)
                watch["evicted_count"] += len(evicted)
                if update_watch is not None:
                    update_watch(watch, outcomes)
            if not stored and watch is None:
                if removed or expired:
                    self.store.write_index(index)
                self._tidy(index, removed)
                self._audit_watch_expiry(expired)
                return outcomes, [], []
            if audit is not None and (stored or evicted):
                # Written before the commit; a failed row stores nothing.
                self._write_audit(audit([item.entry["snapshot_id"] for item in stored], evicted))
            written = []
            try:
                for item in stored:
                    self.store.write_record(item.entry["snapshot_id"], item.raw)
                    written.append(item.entry["snapshot_id"])
                self.store.write_index(index)
            except StoreError:
                if not self.store.commit_unknown:
                    for snapshot_id in written:
                        try:
                            self.store.remove_record(snapshot_id)
                        except StoreError:
                            pass
                raise
            # Evicted and swept files go only after the index no longer names them.
            self._tidy(index, [*removed, *evicted])
        self._audit_watch_expiry(expired)
        return outcomes, [item.entry for item in stored], evicted

    def _tidy_after(self, index, removed, expired):
        if removed or expired:
            self.store.write_index(index)
        self._tidy(index, removed)

    async def _run(self, function, *args, **kwargs):
        async with self.lock:
            # A started local transaction is never abandoned: a cancelled
            # caller (a timed-out tick) waits for it to finish before the lock
            # is released, so the next caller does not meet a held file lock.
            work = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await asyncio.wait({work})
                if not work.cancelled():
                    work.exception()
                raise
            except StoreError as exc:
                raise c.TraceSnapshotError(exc.reason) from None

    # ------------------------------------------------------------------
    # Tools

    async def capture(self, *, automation_id, run_id="", include_condition_failures=False, note=""):
        c.validate_capture({"automation_id": automation_id, "run_id": run_id,
                            "include_condition_failures": include_condition_failures, "note": note})
        caller, request_id = _caller(), current_request_id()
        authority = self.authority()
        listing = await self.list_runs(automation_id)
        excluded = {"not_triggered": 0, "failed_conditions": 0}
        omitted = 0
        if run_id:
            selected = [run_id]
        else:
            eligible = []
            for header in listing.headers:
                if header.not_triggered:
                    excluded["not_triggered"] += 1
                elif header.script_execution == "failed_conditions" and not include_condition_failures:
                    excluded["failed_conditions"] += 1
                else:
                    eligible.append(header.run_id)
            selected, omitted = eligible[:c.MAX_CAPTURES], max(0, len(eligible) - c.MAX_CAPTURES)
        states = {header.run_id: header.state for header in listing.headers}
        prepared = []
        now = self.clock()
        for selected_id in selected:
            if not c.RUN_ID.fullmatch(selected_id):
                prepared.append(Prepared(selected_id, "read_failed"))
                continue
            if states.get(selected_id) == "running":
                prepared.append(Prepared(selected_id, "run_in_progress"))
                continue
            if self.authority() != authority:
                raise c.TraceSnapshotError("authority_drift")
            outcome, detail = await self.read_detail(automation_id, selected_id)
            if outcome != "captured":
                prepared.append(Prepared(selected_id, "read_failed" if outcome == "source_unavailable" else outcome))
                continue
            prepared.append(await asyncio.to_thread(
                self.prepare, detail, automation_id=automation_id, run_id=selected_id,
                origin="manual", watch_id=None, caller=caller, request_id=request_id,
                authority=authority, note=note, now=now))
            del detail
        if self.authority() != authority:
            raise c.TraceSnapshotError("authority_drift")
        outcomes, stored, _ = await self._run(self._commit, prepared, _ms(now))
        runs = [{"run_id": item.run_id, "outcome": outcomes[item.run_id],
                 **({"snapshot_id": item.entry["snapshot_id"], "completeness": item.entry["completeness"],
                     "bytes": item.entry["bytes"]} if outcomes[item.run_id] == "captured" else {})}
                for item in prepared]
        index = await self._run(self.store.load_index)
        result = {
            "automation_id": automation_id,
            "requested_run_id": run_id or None,
            "runs": runs,
            "selection": {
                "retained_runs_listed": len(listing.headers),
                "excluded": excluded,
                "omitted_by_capture_bound": omitted,
                "capture_bound": c.MAX_CAPTURES,
                "source_truncated": listing.source_truncated,
            },
            "core": {"version": authority[1], "generation": authority[0]},
            "store": self._usage(index),
        }
        if "store_full" in outcomes.values():
            oldest = min((e["captured_at_ms"] for e in index["snapshots"]), default=None)
            result["store"]["oldest_snapshot_age_seconds"] = (
                None if oldest is None else max(0, int(now - oldest / 1000)))
        captured = [item for item in runs if item["outcome"] == "captured"]
        held = sum(1 for item in runs if item["outcome"] in {"captured", "already_captured"})
        partial = (not selected or held != len(selected) or omitted
                   or any(item["completeness"] == "partial" for item in captured)
                   or listing.source_truncated or listing.sanitization_failed_closed)
        return self._exit(result, partial, audit={
            "automation_id": automation_id,
            "snapshot_ids": [item["snapshot_id"] for item in captured],
            "outcomes": sorted({item["outcome"] for item in runs})})

    def _usage(self, index):
        return {
            "snapshots": len(index["snapshots"]),
            "bytes": sum(e["bytes"] for e in index["snapshots"]),
            "watches": len(index["watches"]),
            "quarantined": len(index["quarantine"]["entries"]),
            "quarantine_dropped": index["quarantine"]["dropped"],
            "limits": {"snapshots": c.MAX_SNAPSHOTS, "bytes": c.STORE_BYTES,
                       "snapshot_bytes": c.SNAPSHOT_BYTES, "retention_days": c.RETENTION_SECONDS // 86400,
                       "watches": c.MAX_WATCHES, "watch_quota": c.WATCH_QUOTA,
                       "quarantine": c.QUARANTINE_MAX},
        }

    def _exit(self, value, partial, *, audit=None):
        """One strict exit for every response (R1); nothing is added after it."""

        result = sanitize_trace_detail(value, known_secrets=self.known_secrets)
        partial = bool(partial or trace_sanitization_partial(result))
        telemetry = current_telemetry()
        if telemetry is not None:
            telemetry.completeness = "partial" if partial else "complete"
            telemetry.result_status = "partial" if partial else "success"
            if audit:
                telemetry.audit_context.update(audit)
        return result.value, partial

    @staticmethod
    def _public_entry(entry):
        return {
            **{key: entry[key] for key in ("snapshot_id", "automation_id", "run_id", "origin", "watch_id",
                                           "captured_by", "completeness", "bytes", "state",
                                           "script_execution", "started_at")},
            "captured_at": c.utc_text(entry["captured_at_ms"] / 1000),
            "expires_at": c.utc_text(entry["captured_at_ms"] / 1000 + c.RETENTION_SECONDS),
        }

    def _mac(self, value, caller):
        return hmac.new(self.secret, (value + ":" + caller).encode(), hashlib.sha256).hexdigest()

    async def list(self, *, automation_id="", limit=20, cursor=""):
        c.validate_list({"automation_id": automation_id, "limit": limit, "cursor": cursor})
        caller = current_caller_id()
        after = None
        if cursor:
            captured_ms, snapshot_id, mac = cursor.split(":")
            expected = self._mac(f"list:{captured_ms}:{snapshot_id}:{automation_id}", caller)
            if not hmac.compare_digest(mac, expected):
                raise c.TraceSnapshotError("invalid_cursor")
            after = (int(captured_ms), snapshot_id)
        index = await self._run(self._maintain, _ms(self.clock()))
        entries = sorted((e for e in index["snapshots"]
                          if not automation_id or e["automation_id"] == automation_id),
                         key=lambda e: (e["captured_at_ms"], e["snapshot_id"]), reverse=True)
        if after is not None:
            entries = [e for e in entries if (e["captured_at_ms"], e["snapshot_id"]) < after]
        page, more = entries[:limit], len(entries) > limit
        next_cursor = None
        if more:
            last = page[-1]
            key = f"{last['captured_at_ms']}:{last['snapshot_id']}"
            next_cursor = key + ":" + self._mac(f"list:{key}:{automation_id}", caller)
        result = {
            "snapshots": [self._public_entry(e) for e in page],
            "next_cursor": next_cursor,
            "store": self._usage(index),
        }
        return self._exit(result, False, audit={"returned_snapshots": len(page),
                                                "continuation": bool(cursor)})

    @staticmethod
    def _units(record):
        trace = record.get("trace") if isinstance(record.get("trace"), dict) else {}
        units = [{"kind": kind, "value": trace.get(kind)}
                 for kind in ("config", "blueprint_inputs", "context")]
        for path, entries in trace.get("steps") or []:
            for position, element in enumerate(entries if isinstance(entries, list) else [entries]):
                units.append({"kind": "step", "path": path, "index": position, "value": element})
        return units

    @staticmethod
    def _pages(units):
        """Deterministic greedy pages; an oversized unit is named, not sent."""

        pages, current, used = [], [], 0
        for unit in units:
            size = len(c.canonical(unit)) + 1
            if size > c.PAGE_BYTES:
                current.append({"kind": unit["kind"], "path": unit.get("path"),
                                "index": unit.get("index"), "omitted_bytes": size})
                continue
            if current and used + size > c.PAGE_BYTES:
                pages.append(current)
                current, used = [], 0
            current.append(unit)
            used += size
        pages.append(current)
        return pages

    def _read(self, snapshot_id, now_ms):
        with self.store.transaction():
            index = self.store.load_index()
            removed, expired = self._sweep(index, now_ms)
            entry = next((e for e in index["snapshots"] if e["snapshot_id"] == snapshot_id), None)
            record, damage = self.store.read_record(entry) if entry is not None else (None, None)
            damaged = damage is not None
            if damaged:
                self._quarantine(index, entry, damage, now_ms)
            if removed or expired or damaged:
                self.store.write_index(index)
            # A damaged file is unlinked only after the index no longer names it.
            self._tidy(index, removed)
        self._audit_watch_expiry(expired)
        if entry is None:
            raise c.TraceSnapshotError("snapshot_not_found")
        if damaged:
            raise c.TraceSnapshotError("store_invalid")
        return entry, record

    async def get(self, *, snapshot_id, cursor=""):
        c.validate_get({"snapshot_id": snapshot_id, "cursor": cursor})
        caller = current_caller_id()
        entry, record = await self._run(self._read, snapshot_id, _ms(self.clock()))
        page_index = 0
        if cursor:
            cursor_id, raw_page, mac = cursor.split(":")
            page_index = int(raw_page)
            expected = self._mac(f"get:{cursor_id}:{page_index}:{entry['sha256']}", caller)
            if cursor_id != snapshot_id or page_index == 0 or not hmac.compare_digest(mac, expected):
                raise c.TraceSnapshotError("invalid_cursor")
        pages = await asyncio.to_thread(self._pages, self._units(record))
        if page_index >= len(pages):
            raise c.TraceSnapshotError("invalid_cursor")
        units = [unit for unit in pages[page_index] if "omitted_bytes" not in unit]
        omitted = [unit for unit in pages[page_index] if "omitted_bytes" in unit]
        following = page_index + 1
        next_cursor = None
        if following < len(pages):
            next_cursor = f"{snapshot_id}:{following}:" + self._mac(
                f"get:{snapshot_id}:{following}:{entry['sha256']}", caller)
        result = {
            "snapshot": self._public_entry(entry),
            "run": record.get("run"),
            "core": record.get("core"),
            "note": record.get("note"),
            "capture_sanitization": record.get("sanitization"),
            "page": {"index": page_index, "count": len(pages), "next_cursor": next_cursor,
                     "omitted_units": omitted, "page_bytes_bound": c.PAGE_BYTES},
            "units": units,
        }
        partial = entry["completeness"] == "partial" or bool(omitted)
        return self._exit(result, partial, audit={"snapshot_id": snapshot_id, "page": page_index})

    # ------------------------------------------------------------------
    # Watch list (no plan or approval; audited and bounded)

    def _public_watch(self, watch, index):
        held = sum(1 for e in index["snapshots"] if e["watch_id"] == watch["watch_id"])
        status = ("paused_core_unavailable" if self.paused and watch["baseline_done"] else
                  "watching" if watch["baseline_done"] else "awaiting_first_poll")
        return {
            "watch_id": watch["watch_id"], "automation_id": watch["automation_id"],
            "mode": watch["mode"], "created_by": watch["caller_id"],
            "created_at": c.utc_text(watch["created_at_ms"] / 1000),
            "expires_at": c.utc_text(watch["expires_at_ms"] / 1000),
            "status": status, "snapshots_held": held, "snapshot_quota": c.WATCH_QUOTA,
            "captured_count": watch["captured_count"], "evicted_count": watch["evicted_count"],
            "skipped_polls": watch["skipped_polls"], "uncaptured_runs": watch["uncaptured_runs"],
            "last_poll_at": (None if watch["last_poll_at_ms"] is None
                             else c.utc_text(watch["last_poll_at_ms"] / 1000)),
        }

    def _watch_list(self, index):
        return [self._public_watch(w, index)
                for w in sorted(index["watches"], key=lambda w: (w["created_at_ms"], w["watch_id"]))]

    def _change_watch(self, action, automation_id, mode, hours, caller, request_id, now):
        now_ms = _ms(now)
        with self.store.transaction():
            index = self.store.load_index()
            removed, expired = self._sweep(index, now_ms)
            existing = next((w for w in index["watches"] if w["automation_id"] == automation_id), None)
            changed = None
            if action == "add":
                if existing is not None:
                    existing["mode"] = mode
                    existing["expires_at_ms"] = now_ms + hours * 3_600_000
                    changed = existing
                else:
                    if len(index["watches"]) >= c.MAX_WATCHES:
                        self._tidy_after(index, removed, expired)
                        raise c.TraceSnapshotError("watch_limit", watches=self._watch_list(index))
                    changed = {
                        "watch_id": secrets.token_hex(16), "automation_id": automation_id,
                        "mode": mode, "created_at_ms": now_ms,
                        "expires_at_ms": now_ms + hours * 3_600_000, "caller_id": caller,
                        "request_id": request_id, "seen_run_ids": [], "baseline_done": False,
                        "skipped_polls": 0, "uncaptured_runs": 0, "captured_count": 0,
                        "evicted_count": 0, "last_poll_at_ms": None,
                    }
                    index["watches"].append(changed)
            elif action == "remove":
                if existing is None:
                    self._tidy_after(index, removed, expired)
                    raise c.TraceSnapshotError("watch_not_found", watches=self._watch_list(index))
                index["watches"] = [w for w in index["watches"] if w is not existing]
                changed = existing
            if changed is not None:
                try:
                    # The owner-visible audit row precedes the commit (W1 addition).
                    self._write_audit({
                        "event": "trace_watch_change", "tool_name": "set_automation_trace_watch",
                        "request_id": request_id, "result_status": action, "caller_id": caller,
                        "watch_id": changed["watch_id"], "automation_id": automation_id,
                        "mode": changed["mode"],
                        "expires_at": c.utc_text(changed["expires_at_ms"] / 1000),
                        "replaced": action == "add" and existing is not None,
                    })
                except c.TraceSnapshotError:
                    raise c.TraceSnapshotError(
                        "audit_unavailable", watches=self._watch_list(self.store.load_index())) from None
            if changed is not None or removed or expired:
                self.store.write_index(index)
            self._tidy(index, removed)
        self._audit_watch_expiry(expired)
        return index, changed

    async def watch(self, *, action, automation_id="", mode="failures", duration_hours=24):
        arguments = {"action": action}
        if action != "list" or automation_id:
            arguments["automation_id"] = automation_id
        if action == "add":
            arguments.update(mode=mode, duration_hours=duration_hours)
        c.validate_watch(arguments)
        caller, request_id = _caller(), current_request_id()
        index, changed = await self._run(self._change_watch, action, automation_id, mode,
                                         duration_hours, caller, request_id, self.clock())
        result = {
            "action": action,
            "changed": changed is not None,
            "watch": None if changed is None else self._public_watch(changed, index),
            "watches": self._watch_list(index),
            "poll_seconds": c.POLL_SECONDS,
            "limits": {"watches": c.MAX_WATCHES, "duration_hours": c.WATCH_MAX_HOURS,
                       "snapshot_quota": c.WATCH_QUOTA},
        }
        return self._exit(result, False, audit={"watch_action": action,
                                                "watch_count": len(index["watches"])})

    # ------------------------------------------------------------------
    # Poller tick (W1); see ``poller.py`` for the authority it runs under

    async def poll_once(self, *, authority, deadline=None):
        """One bounded tick: at most one ``trace/list`` per watch.

        ``authority`` is the tick's own admitted ``(generation, version)``,
        or ``None`` when Core authority was unavailable (the tick pauses).
        ``deadline`` (``time.monotonic``) ends the tick's Core reads. Each
        watch gets an equal share of the time left when its turn comes, and
        the first watch rotates every tick, so one slow automation cannot
        starve the others (review M2).
        """

        authorized = authority is not None
        now = self.clock()
        index = await self._run(self._maintain, _ms(now))
        if not index["watches"]:
            self.paused = not authorized
            return
        if not authorized:
            self.paused = True
            await self._run(self._skip_all, [w["watch_id"] for w in index["watches"]], _ms(now))
            return
        self.paused = False
        if deadline is None:
            deadline = time.monotonic() + c.TICK_SECONDS * c.READ_SHARE
        watches = sorted(index["watches"], key=lambda w: (w["created_at_ms"], w["watch_id"]))
        first = self.rotation % len(watches)
        self.rotation += 1
        order = watches[first:] + watches[:first]
        for position, watch in enumerate(order):
            share = (deadline - time.monotonic()) / (len(order) - position)
            if share <= 0 or not await self._poll_watch(watch, authority, time.monotonic() + share):
                # Out of time, or Core authority retired: nothing more is read
                # or stored this tick; every watch not polled counts a skip.
                rest = [w["watch_id"] for w in order[position + (0 if share <= 0 else 1):]]
                if rest:
                    await self._run(self._skip_all, rest, _ms(self.clock()))
                return

    def _skip_all(self, watch_ids, now_ms):
        with self.store.transaction():
            index = self.store.load_index()
            for watch in index["watches"]:
                if watch["watch_id"] in watch_ids:
                    watch["skipped_polls"] += 1
                    watch["last_poll_at_ms"] = now_ms
            self.store.write_index(index)

    def _still_authorized(self, authority):
        """The tick's lease and its exact generation still hold (review M3)."""

        telemetry = current_telemetry()
        if (telemetry is None or telemetry.core_dispatch_authorizer is None
                or not telemetry.authorize_core_dispatch()):
            return False
        runtime = self.core_runtime
        status = runtime.route_status(c.REQUIREMENTS) if runtime is not None else {}
        return status.get("available") is True and status.get("generation") == authority[0]

    async def _poll_watch(self, watch, authority, watch_deadline=None):
        """Poll one watch until ``watch_deadline``; ``False`` when authority retired.

        Reads finished before the deadline are kept, so a watch slower than
        its share still makes bounded progress; the rest is read next tick.
        Before anything read from Core is committed, the tick's authority is
        checked again; after retirement nothing from this watch is stored.
        """

        if watch_deadline is None:
            watch_deadline = time.monotonic() + c.TICK_SECONDS * c.READ_SHARE
        watch_id, automation_id = watch["watch_id"], watch["automation_id"]
        try:
            async with asyncio.timeout_at(self._loop_deadline(watch_deadline)):
                listing = await self.list_runs(automation_id)
        except (c.TraceSnapshotError, TimeoutError):
            if not self._still_authorized(authority):
                self.paused = True
                await self._run(self._skip_all, [watch_id], _ms(self.clock()))
                return False
            await self._run(self._skip_all, [watch_id], _ms(self.clock()))
            return True
        listed = [h for h in listing.headers if c.RUN_ID.fullmatch(h.run_id)]
        seen = set(watch["seen_run_ids"])
        settled = {h.run_id for h in listed if h.state != "running"}
        if watch["baseline_done"]:
            fresh = [h for h in listed if h.run_id not in seen and h.state != "running"]
            handled = set()
        else:
            # First poll: runs before the watch existed are history, not
            # captured; runs that finished after its creation are (review M1).
            fresh = [h for h in listed if h.state != "running"
                     and _ended_after(h, watch["created_at_ms"])]
            handled = settled - {h.run_id for h in fresh}
        fresh.sort(key=lambda h: (h.started_instant, h.run_id))
        matching = [h for h in fresh if header_matches(watch["mode"], h)]
        handled |= {h.run_id for h in fresh if not header_matches(watch["mode"], h)}
        prepared, failed, deferred = [], 0, False
        now = self.clock()
        for header in matching[:c.MAX_CAPTURES]:
            try:
                async with asyncio.timeout_at(self._loop_deadline(watch_deadline)):
                    outcome, detail = await self.read_detail(automation_id, header.run_id)
            except TimeoutError:
                outcome, detail = "source_unavailable", None
            if outcome == "source_unavailable":
                # Authority, transport or time loss: retried next tick, not a gap.
                deferred = True
                break
            handled.add(header.run_id)
            if outcome != "captured":
                failed += 1
                continue
            prepared.append(await asyncio.to_thread(
                self.prepare, detail, automation_id=automation_id, run_id=header.run_id,
                origin="trace_watch", watch_id=watch_id, caller=watch["caller_id"],
                request_id=current_request_id(), authority=authority,
                note=None, now=now))
            del detail
        if not self._still_authorized(authority):
            self.paused = True
            await self._run(self._skip_all, [watch_id], _ms(self.clock()))
            return False

        def update(row, outcomes):
            bad = failed + sum(1 for value in outcomes.values()
                               if value in {"over_size_bound", "store_full", "read_failed",
                                            "trace_not_retained"})
            row["uncaptured_runs"] += bad
            if deferred:
                row["skipped_polls"] += 1
            # Seen IDs are pruned to runs Core still lists; never more than its list.
            kept = [r for r in row["seen_run_ids"] if r in settled or r in handled]
            row["seen_run_ids"] = sorted(set(kept) | handled)[-c.MAX_SEEN_RUNS:]
            row["baseline_done"] = True
            row["last_poll_at_ms"] = _ms(self.clock())

        def audit(snapshot_ids, evicted):
            return {
                "event": "trace_watch_capture", "tool_name": "set_automation_trace_watch",
                "request_id": current_request_id(), "result_status": "captured",
                "caller_id": watch["caller_id"], "watch_id": watch_id,
                "automation_id": automation_id, "snapshot_ids": snapshot_ids,
                "evicted_snapshot_ids": evicted,
            }

        try:
            await self._run(self._commit, prepared, _ms(self.clock()), watch_id=watch_id,
                            update_watch=update, audit=audit)
        except c.TraceSnapshotError as exc:
            if exc.reason != "audit_unavailable":
                raise
            # Nothing was stored; the runs count as a gap and are not retried.
            for item in prepared:
                item.outcome = "read_failed"
            await self._run(self._commit, prepared, _ms(self.clock()), watch_id=watch_id,
                            update_watch=update)
        return True

    @staticmethod
    def _loop_deadline(monotonic_deadline):
        """``time.monotonic`` deadline to the running loop's clock."""

        loop = asyncio.get_running_loop()
        return loop.time() + (monotonic_deadline - time.monotonic())
