"""Bounded frozen sanitized snapshots; continuations never recollect evidence."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import secrets
import time

from ..request_context import current_caller_id, current_telemetry
from . import contracts as c
from .models import Inspection, InspectionError
from .provider import utc_now


def references(value):
    """Traverse only normalized typed output, never a provider object."""
    if isinstance(value, dict):
        if set(value) == {"source_id", "pointer"}:
            yield value["source_id"], value["pointer"]
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def page_evidence(report, records):
    wanted = set(references(records))
    entries = [item for item in report["evidence_entries"] if (item["source_id"], item["pointer"]) in wanted]
    if len(entries) != len(wanted):
        raise InspectionError("malformed_response")
    return entries


@dataclass(frozen=True)
class Snapshot:
    encoded: bytes
    expires: float
    binding: tuple
    tokens: tuple[str, ...]
    footprint: int


class IntegrationInspectionService:
    def __init__(self, provider, *, response_limit=60_000, clock=time.monotonic):
        self.provider = provider
        self.response_limit = min(response_limit, c.ENVELOPE_BYTES)
        self.clock = clock
        self.snapshots = OrderedDict()
        self.cursors = {}
        self.active = 0
        self.invalidation_generation = 0

    def _invalidate(self):
        self.invalidation_generation += 1
        self.snapshots.clear()
        self.cursors.clear()

    def _require_current(self, binding):
        if binding[-1] != self.invalidation_generation:
            raise InspectionError("authority_unavailable")

    def _binding(self, target, integration, limit):
        caller = current_caller_id()
        if caller == "anonymous":
            raise InspectionError("authority_unavailable")
        try:
            authority = self.provider.authority()
        except InspectionError:
            self._invalidate()
            raise
        for key, snapshot in tuple(self.snapshots.items()):
            if snapshot.binding[4:6] != authority:
                self._remove(key)
        return caller, target, integration, limit, *authority, self.invalidation_generation

    def _remove(self, key):
        snapshot = self.snapshots.pop(key)
        for token in snapshot.tokens:
            self.cursors.pop(token, None)

    def _expire(self):
        for key, snapshot in tuple(self.snapshots.items()):
            if snapshot.expires <= self.clock():
                self._remove(key)

    @staticmethod
    def _omissions(report, count):
        result = {**report, "pagination": {**report["pagination"]}}
        if count:
            result["assessment"] = "partial"
            before = report["truncation"]
            result["truncation"] = {"occurred": True,
                "reasons": sorted(set(before["reasons"] + ["output_bytes"])),
                "omitted_count": None if before["omitted_count"] is None else before["omitted_count"] + count}
            previous = report["pagination"]["omitted_records"]
            result["pagination"]["omitted_records"] = None if previous is None else previous + count
        return result

    def _header_budget(self, report, limit, omitted):
        # Upper bound the stored header and a single-row continuation header.
        # Only normalized metadata is serialized; record bodies are sized once.
        sizes = []
        for count in (0, max(1, omitted)):
            header = self._omissions(report, count)
            header["records"], header["evidence_entries"] = [], []
            header["freshness"] = {**report["freshness"], "status": "frozen_snapshot",
                "served_at": "9999-12-31T23:59:59.999999Z"}
            header["pagination"].update(requested_limit=limit, effective_limit=1,
                offset=max(0, len(report["records"]) - 1), returned=1, has_more=True,
                next_cursor="x" * 43, snapshot_fingerprint="0" * 64)
            sizes.append(len(c.canonical(header)))
        return max(sizes)

    async def _retain(self, report, binding):
        self._require_current(binding)
        records = report["records"]
        header_bytes = self._header_budget(report, binding[3], 0)
        page_budget = min(c.PAGE_BYTES, self.response_limit - 2048)
        if header_bytes > page_budget or header_bytes + 320 > c.SNAPSHOT_BYTES:
            raise InspectionError("output_budget_unavailable")
        # One index and one sizing pass replace quadratic full-report trimming.
        # Yield during bounded batches so deadlines and cancellation can run.
        entries, sizes = {}, {}
        for index, entry in enumerate(report["evidence_entries"]):
            if index % 32 == 0:
                await asyncio.sleep(0)
                self._require_current(binding)
            key = entry["source_id"], entry["pointer"]
            if key in entries:
                raise InspectionError("malformed_response")
            entries[key], sizes[key] = entry, len(c.canonical(entry))
        rows = []
        for index, row in enumerate(records):
            if index % 16 == 0:
                await asyncio.sleep(0)
                self._require_current(binding)
            refs = set(references(row))
            if not refs.issubset(entries):
                raise InspectionError("malformed_response")
            rows.append((row, refs, len(c.canonical(row))))
        # Refit sizes only if decimal omission counters grow the header. At
        # most 801 normalized rows exist; four passes cover every digit width.
        # No row or report is reserialized during fitting.
        for _ in range(4):
            retained, wanted = [], set()
            row_bytes = evidence_bytes = omitted = 0
            for index, (row, refs, size) in enumerate(rows):
                if index % 16 == 0:
                    await asyncio.sleep(0)
                    self._require_current(binding)
                single_bytes = header_bytes + size + sum(sizes[key] for key in refs) + max(0, len(refs) - 1)
                if single_bytes > page_budget:
                    omitted += 1
                    continue
                added = refs - wanted
                next_evidence_bytes = evidence_bytes + sum(sizes[key] for key in added)
                next_evidence_count = len(wanted) + len(added)
                # Canonical JSON array payloads add exactly one comma per gap.
                next_size = (header_bytes + row_bytes + size + len(retained)
                             + next_evidence_bytes + max(0, next_evidence_count - 1))
                if next_size + (len(retained) + 2) * 256 + 64 > c.SNAPSHOT_BYTES:
                    omitted += len(records) - index
                    break
                retained.append(row)
                wanted.update(added)
                row_bytes += size
                evidence_bytes = next_evidence_bytes
            required_header = self._header_budget(report, binding[3], omitted)
            if required_header <= header_bytes:
                break
            header_bytes = required_header
        else:
            raise InspectionError("output_budget_unavailable")
        report = self._omissions(report, omitted)
        report["records"] = retained
        report["evidence_entries"] = [entry for key, entry in entries.items() if key in wanted]
        report["pagination"].update(total_retained_records=len(retained), snapshot_fingerprint="")
        await asyncio.sleep(0)
        self._require_current(binding)
        report["pagination"]["snapshot_fingerprint"] = c.digest(report)
        encoded = c.canonical(report)
        footprint = len(encoded) + (len(retained) + 1) * 256 + 64
        if footprint > c.SNAPSHOT_BYTES:
            raise InspectionError("output_budget_unavailable")
        tokens = tuple(secrets.token_urlsafe(32) for _ in range(len(retained) + 1))
        snapshot = Snapshot(encoded, self.clock() + c.SNAPSHOT_TTL, binding, tokens, footprint)
        key = secrets.token_hex(16)
        self._expire()
        while self.snapshots and (len(self.snapshots) >= c.MAX_SNAPSHOTS or
                sum(s.footprint for s in self.snapshots.values()) + snapshot.footprint > c.MAX_SNAPSHOTS * c.SNAPSHOT_BYTES):
            self._remove(next(iter(self.snapshots)))
        self.snapshots[key] = snapshot
        for offset, token in enumerate(tokens):
            self.cursors[token] = (key, offset)
        return key, snapshot

    async def _fresh(self, target, integration, limit, binding):
        started = time.monotonic()
        key, published = None, False
        try:
            async with asyncio.timeout(c.COLLECTION_SECONDS):
                report = await self.provider.collect(target)
                self._require_current(binding)
                if self._binding(target, integration, limit) != binding:
                    raise InspectionError("identity_drift")
                Inspection.model_validate(report)
                key, snapshot = await self._retain(report, binding)
                await asyncio.sleep(0)
                self._require_current(binding)
                report = self._page(snapshot, 0, limit, continuation=False)
                if time.monotonic() - started >= c.COLLECTION_SECONDS:
                    raise InspectionError("timeout")
                if self._binding(target, integration, limit) != binding:
                    raise InspectionError("authority_unavailable")
                published = True
                return report
        except TimeoutError:
            raise InspectionError("timeout") from None
        except InspectionError as exc:
            if exc.authority_lost or (exc.reason in ("authority_unavailable", "identity_drift")
                                     and binding[-1] == self.invalidation_generation):
                # A new loss invalidates pending captures as well as cache. An
                # obsolete capture must not purge data collected after that loss.
                self._invalidate()
            raise
        finally:
            if not published and key is not None and key in self.snapshots:
                self._remove(key)

    async def inspect(self, *, alarm_entity_id, integration="alarmo", limit=25, cursor=""):
        c.validate_arguments(dict(alarm_entity_id=alarm_entity_id, integration=integration, limit=limit, cursor=cursor))
        binding = self._binding(alarm_entity_id, integration, limit)
        if cursor:
            lookup = self.cursors.get(cursor)
            if lookup is None:
                raise InspectionError("invalid_cursor")
            key, offset = lookup
            snapshot = self.snapshots[key]
            if snapshot.expires <= self.clock():
                self._remove(key)
                raise InspectionError("snapshot_expired")
            if binding != snapshot.binding:
                raise InspectionError("invalid_cursor")
            self.snapshots.move_to_end(key)
            report = self._page(snapshot, offset, limit, continuation=True)
        else:
            if self.active >= c.MAX_COLLECTORS:
                raise InspectionError("capacity_busy")
            self.active += 1
            try:
                report = await self._fresh(alarm_entity_id, integration, limit, binding)
            finally:
                self.active -= 1
        if self._binding(alarm_entity_id, integration, limit) != binding:
            raise InspectionError("authority_unavailable")
        telemetry = current_telemetry()
        if telemetry:
            telemetry.result_status = "partial" if report["assessment"] != "complete" else "success"
            telemetry.completeness = report["assessment"]
            telemetry.audit_context.update(
                provider="engineering", profile=c.PROFILE_ID, upstream_calls=0, fallback="none",
                source_outcomes=[{"kind": s["kind"], "status": s["status"], "records_retained": s["records_retained"], "duration_ms": s["duration_ms"]} for s in report["sources"]],
                omitted_count=report["truncation"]["omitted_count"],
            )
        return report

    def _page(self, snapshot, offset, limit, *, continuation):
        frozen = json.loads(snapshot.encoded)
        records = frozen["records"]
        report = {**frozen, "records": [], "evidence_entries": []}
        report["freshness"] = {**frozen["freshness"], "served_at": utc_now(),
                               "status": "frozen_snapshot" if continuation else frozen["freshness"]["status"]}
        page_budget = min(c.PAGE_BYTES, self.response_limit - 2048)
        # A full fixed failure fits the setting's minimum; never generic-truncate
        # a successful report, its pointers or cursor metadata.
        if page_budget < 2048:
            raise InspectionError("output_budget_unavailable")
        next_offset = offset

        def finish(candidate):
            candidate["pagination"] = {**frozen["pagination"], "requested_limit": limit,
                "effective_limit": len(candidate["records"]), "offset": offset, "returned": len(candidate["records"]),
                "has_more": next_offset < len(records), "next_cursor": snapshot.tokens[next_offset] if next_offset < len(records) else None}
            return candidate

        while next_offset < len(records) and len(report["records"]) < limit:
            proposed = [*report["records"], records[next_offset]]
            candidate = {**report, "records": proposed, "evidence_entries": page_evidence(frozen, proposed)}
            next_offset += 1
            finish(candidate)
            if len(c.canonical(candidate)) > page_budget:
                if report["records"]:
                    next_offset -= 1
                    break
                # Single-row fit was established before freezing coverage.
                # Refuse an invariant failure rather than silently changing it.
                raise InspectionError("output_budget_unavailable")
            report = candidate
        report = finish(report)
        if len(c.canonical(report)) > page_budget:
            raise InspectionError("output_budget_unavailable")
        return Inspection.model_validate(report).model_dump()
