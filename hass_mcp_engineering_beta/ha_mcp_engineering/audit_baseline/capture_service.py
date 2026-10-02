"""Process-local immutable exports; continuation performs no provider collection."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import time

from ..request_context import current_caller_id
from . import capture_contracts as c


@dataclass(frozen=True)
class Snapshot:
    encoded: bytes
    artifact_sha256: str
    expires: float
    caller: str


class BaselineCaptureService:
    def __init__(self, provider, *, response_limit=60_000, clock=time.monotonic):
        self.provider, self.clock = provider, clock
        self.page_limit = min(c.PAGE_BYTES, response_limit - 4096)
        self.snapshots = {}
        self.secret = secrets.token_bytes(32)
        self.active = False

    def _token(self, key, offset, caller):
        value = f"{key}:{offset}"
        mac = hmac.new(self.secret, (value + ":" + caller).encode(), hashlib.sha256).hexdigest()
        return value + ":" + mac

    def _resolve(self, token, caller):
        if not c.CURSOR.fullmatch(token):
            raise c.CaptureError("invalid_cursor")
        key, raw_offset, _ = token.split(":")
        offset = int(raw_offset)
        if not hmac.compare_digest(token, self._token(key, offset, caller)):
            raise c.CaptureError("invalid_cursor")
        snapshot = self.snapshots.get(key)
        if not snapshot or snapshot.expires <= self.clock():
            raise c.CaptureError("snapshot_expired")
        if snapshot.caller != caller:
            raise c.CaptureError("invalid_cursor")
        return key, offset, snapshot

    @staticmethod
    def _encode(report):
        baseline_bytes = c.canonical(report["baseline"])
        encoded = c.canonical(report)
        if len(baseline_bytes) > c.SNAPSHOT_BYTES or len(encoded) > c.SNAPSHOT_BYTES:
            raise c.CaptureError("output_limit")
        return encoded, "sha256:" + hashlib.sha256(baseline_bytes).hexdigest()

    def _page(self, key, offset, snapshot, limit):
        report = json.loads(snapshot.encoded)
        baseline = report["baseline"]
        records = baseline.pop("records")
        if offset > len(records) or (offset == len(records) and offset != 0):
            raise c.CaptureError("invalid_cursor")
        diagnostics = report["diagnostics"]
        outcomes = diagnostics.pop("record_outcomes")
        page = {"baseline_header": baseline, "records": [], "diagnostics": diagnostics,
                "record_outcomes": {}, "artifact_sha256": snapshot.artifact_sha256,
                "artifact_serialization": "python-json-sorted-compact-ascii-v1",
                "pagination": {"offset": offset, "returned": 0, "total_records": len(records),
                    "requested_limit": limit, "next_cursor": None, "complete_export": len(records) == 0},
                "snapshot": {"capture_id": key, "ttl_seconds": c.SNAPSHOT_TTL,
                    "remaining_seconds": max(0, int(snapshot.expires-self.clock())),
                    "storage": "process_memory_only", "provider_reads_for_continuation": 0}}
        # Reserve the maximal bounded footer and size each row once.
        reserve = dict(page["pagination"], next_cursor=self._token(key, c.MAX_RECORDS, snapshot.caller),
                       returned=1000, offset=1000)
        budget_page = dict(page, pagination=reserve)
        used = len(c.canonical(budget_page))
        for row in records[offset:offset+limit]:
            identifier = row["configuration_id"]
            reason = outcomes.get(identifier)
            cost = len(c.canonical(row)) + 1
            if reason:
                cost += len(c.canonical(identifier)) + len(c.canonical(reason)) + 2
            if used + cost > self.page_limit:
                break
            page["records"].append(row)
            if reason:
                page["record_outcomes"][identifier] = reason
            used += cost
        count = len(page["records"])
        if records and not count:
            raise c.CaptureError("output_limit")
        more = offset + count < len(records)
        page["pagination"].update(returned=count, complete_export=not more,
            next_cursor=self._token(key, offset+count, snapshot.caller) if more else None)
        if len(c.canonical(page)) > self.page_limit:
            raise c.CaptureError("output_limit")
        return page

    async def capture(self, *, limit=25, cursor=""):
        c.validate_arguments({"limit": limit, "cursor": cursor})
        caller = current_caller_id()
        if caller == "anonymous":
            raise c.CaptureError("access_denied")
        if cursor:
            key, offset, snapshot = self._resolve(cursor, caller)
            return await c.worker(self._page, key, offset, snapshot, limit)
        self.snapshots = {key: value for key, value in self.snapshots.items() if value.expires > self.clock()}
        if self.active or len(self.snapshots) >= c.MAX_SNAPSHOTS:
            raise c.CaptureError("capacity_busy")
        self.active = True
        try:
            report = await self.provider.collect()
            encoded, artifact = await c.worker(self._encode, report)
            key = secrets.token_hex(16)
            snapshot = Snapshot(encoded, artifact, self.clock()+c.SNAPSHOT_TTL, caller)
            page = await c.worker(self._page, key, 0, snapshot, limit)
            self.snapshots[key] = snapshot
            return page
        finally:
            self.active = False
