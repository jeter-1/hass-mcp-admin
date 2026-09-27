"""Bounded frozen sanitized snapshots; continuations never recollect evidence."""

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

    def _binding(self, target, integration, limit):
        authority = self.provider.authority()
        caller = current_caller_id()
        if caller == "anonymous":
            raise InspectionError("authority_unavailable")
        return caller, target, integration, limit, *authority

    def _remove(self, key):
        snapshot = self.snapshots.pop(key)
        for token in snapshot.tokens:
            self.cursors.pop(token, None)

    def _expire(self):
        for key, snapshot in tuple(self.snapshots.items()):
            if snapshot.expires <= self.clock():
                self._remove(key)

    def _retain(self, report, binding):
        # Scope and counts survive any snapshot-size omission. Evidence is
        # pruned with its row; no emitted pointer loses its local excerpt.
        records = report["records"]
        omitted = 0
        original_truncation = report["truncation"]
        original_omissions = report["pagination"]["omitted_records"]
        while True:
            report["evidence_entries"] = page_evidence(report, records)
            report["records"] = records
            report["pagination"]["total_retained_records"] = len(records)
            report["pagination"]["snapshot_fingerprint"] = ""
            if omitted:
                report["assessment"] = "partial"
                report["truncation"] = {"occurred": True,
                    "reasons": sorted(set(original_truncation["reasons"] + ["output_bytes"])),
                    "omitted_count": None if original_truncation["omitted_count"] is None else original_truncation["omitted_count"] + omitted}
                report["pagination"]["omitted_records"] = None if original_omissions is None else original_omissions + omitted
            # Reserve per-offset token bookkeeping in the bounded cache budget.
            encoded = c.canonical(report)
            if len(encoded) + (len(records) + 1) * 256 + 64 <= c.SNAPSHOT_BYTES:
                break
            if not records:
                raise InspectionError("output_budget_unavailable")
            records = records[:-1]
            omitted += 1
        report["pagination"]["snapshot_fingerprint"] = c.digest(report)
        encoded = c.canonical(report)
        tokens = tuple(secrets.token_urlsafe(32) for _ in range(len(records) + 1))
        snapshot = Snapshot(encoded, self.clock() + c.SNAPSHOT_TTL, binding, tokens, len(encoded) + len(tokens) * 256)
        key = secrets.token_hex(16)
        self._expire()
        while self.snapshots and (len(self.snapshots) >= c.MAX_SNAPSHOTS or
                sum(s.footprint for s in self.snapshots.values()) + snapshot.footprint > c.MAX_SNAPSHOTS * c.SNAPSHOT_BYTES):
            self._remove(next(iter(self.snapshots)))
        self.snapshots[key] = snapshot
        for offset, token in enumerate(tokens):
            self.cursors[token] = (key, offset)
        return key, snapshot

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
        else:
            if self.active >= c.MAX_COLLECTORS:
                raise InspectionError("capacity_busy")
            self.active += 1
            try:
                report = await self.provider.collect(alarm_entity_id)
                if self._binding(alarm_entity_id, integration, limit) != binding:
                    raise InspectionError("identity_drift")
                # Validating sanitized output is safe: provider values cannot
                # flow into public error text (the tool uses fixed failures).
                Inspection.model_validate(report)
                key, snapshot = self._retain(report, binding)
            finally:
                self.active -= 1
            offset = 0
        report = self._page(snapshot, offset, limit, continuation=bool(cursor))
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
        omitted = 0

        def finish(candidate):
            candidate["pagination"] = {**frozen["pagination"], "requested_limit": limit,
                "effective_limit": len(candidate["records"]), "offset": offset, "returned": len(candidate["records"]),
                "has_more": next_offset < len(records), "next_cursor": snapshot.tokens[next_offset] if next_offset < len(records) else None}
            if omitted:
                candidate["assessment"] = "partial"
                candidate["truncation"] = {"occurred": True, "reasons": sorted(set(frozen["truncation"]["reasons"] + ["output_bytes"])),
                                           "omitted_count": None if frozen["truncation"]["omitted_count"] is None else frozen["truncation"]["omitted_count"] + omitted}
                candidate["pagination"]["omitted_records"] = None if frozen["pagination"]["omitted_records"] is None else frozen["pagination"]["omitted_records"] + omitted
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
                omitted += 1
                continue
            report = candidate
        report = finish(report)
        if len(c.canonical(report)) > page_budget:
            raise InspectionError("output_budget_unavailable")
        return Inspection.model_validate(report).model_dump()
