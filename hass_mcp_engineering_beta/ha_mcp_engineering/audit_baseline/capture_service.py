"""Durable caller-bound baseline exports; continuation performs no provider collection.

HAMCP-228: each finished capture is committed to the bounded Engineering-local
baseline store (``store.py``); pages, comparisons and resumes read the stored,
digest-verified copy. A capture that does not finish writes nothing.
"""

import functools
import hashlib
import hmac
import json
import secrets
import time

from .. import untrusted_text
from ..request_context import current_caller_id
from . import capture_contracts as c
from .comparison import (PREFIX_MATCH, bound_report_dict, compare_baselines,
                         compare_recorded_fingerprints, comparison_report_dict)
from .store import StoreError
from .validation import BaselineValidationError, baseline_from_value

_STORE_REASONS = {"store_busy": "capacity_busy", "store_full": "store_full",
                  "baseline_not_found": "baseline_not_found", "baseline_damaged": "baseline_damaged"}


class BaselineCaptureService:
    def __init__(self, provider, store, *, response_limit=60_000, clock=time.monotonic, wall=time.time):
        self.provider, self.store, self.clock, self.wall = provider, store, clock, wall
        self.page_limit = min(c.PAGE_BYTES, response_limit - 4096)
        # Cursor validity only (key -> (expires, caller)); the baseline itself is durable.
        self.cursors = {}
        self.secret = secrets.token_bytes(32)
        self.active = False

    def now_ms(self):
        return int(self.wall() * 1000)

    async def stored(self, method, *args, **kwargs):
        """Run one blocking store call off the loop; fixed reasons only."""

        try:
            return await c.worker(functools.partial(getattr(self.store, method), *args, **kwargs))
        except StoreError as exc:
            raise c.CaptureError(_STORE_REASONS.get(exc.reason, "store_unavailable"),
                                 oldest_age_seconds=exc.oldest_age_seconds) from None

    async def load(self, capture_id, caller):
        """The caller's own stored report and index entry (HAMCP-228 compare and resume)."""

        return await self.stored("get", capture_id, caller, self.now_ms())

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
        cursor = self.cursors.get(key)
        if not cursor or cursor[0] <= self.clock():
            raise c.CaptureError("snapshot_expired")
        if cursor[1] != caller:
            raise c.CaptureError("invalid_cursor")
        return key, offset, cursor[0]

    @staticmethod
    def _encode(report):
        baseline_bytes = c.canonical(report["baseline"])
        encoded = c.canonical(report)
        if len(baseline_bytes) > c.SNAPSHOT_BYTES or len(encoded) > c.SNAPSHOT_BYTES:
            raise c.CaptureError("output_limit")
        return encoded, "sha256:" + hashlib.sha256(baseline_bytes).hexdigest()

    def _page(self, key, offset, report, artifact_sha256, caller, expires, resumed_from, limit):
        baseline = report["baseline"]
        records = baseline.pop("records")
        if offset > len(records) or (offset == len(records) and offset != 0):
            raise c.CaptureError("invalid_cursor")
        diagnostics = report["diagnostics"]
        outcomes = diagnostics.pop("record_outcomes")
        page = {"baseline_header": baseline, "records": [], "diagnostics": diagnostics,
                "record_outcomes": {}, "artifact_sha256": artifact_sha256,
                "artifact_serialization": "python-json-sorted-compact-ascii-v1",
                "pagination": {"offset": offset, "returned": 0, "total_records": len(records),
                    "requested_limit": limit, "next_cursor": None, "complete_export": len(records) == 0},
                "snapshot": {"capture_id": key, "ttl_seconds": c.SNAPSHOT_TTL,
                    "remaining_seconds": max(0, int(expires-self.clock())),
                    "storage": "durable_engineering_store", "retention_seconds": c.STORE_RETENTION_SECONDS,
                    "resumed_from": resumed_from, "provider_reads_for_continuation": 0}}
        # Reserve the maximal bounded footer and size each row once.
        reserve = dict(page["pagination"], next_cursor=self._token(key, c.MAX_RECORDS, caller),
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
            next_cursor=self._token(key, offset+count, caller) if more else None)
        if len(c.canonical(page)) > self.page_limit:
            raise c.CaptureError("output_limit")
        return page

    async def capture(self, *, limit=25, cursor="", resume_capture_id=""):
        c.validate_arguments({"limit": limit, "cursor": cursor, "resume_capture_id": resume_capture_id})
        caller = current_caller_id()
        if caller == "anonymous":
            raise c.CaptureError("access_denied")
        if cursor:
            key, offset, expires = self._resolve(cursor, caller)
            report, entry = await self.load(key, caller)
            return await c.worker(self._page, key, offset, report, entry["artifact_sha256"], caller,
                                  expires, entry["resumed_from"], limit)
        self.cursors = {key: value for key, value in self.cursors.items() if value[0] > self.clock()}
        if self.active:
            raise c.CaptureError("capacity_busy")
        self.active = True
        try:
            base = None
            if resume_capture_id:
                # Same caller, digest verified, before any Home Assistant read.
                base, _ = await self.load(resume_capture_id, caller)
                if not self.provider.retry_ids(base["baseline"], base["diagnostics"]["record_outcomes"]):
                    raise c.CaptureError("resume_not_needed")
            await self.stored("check_capacity", self.now_ms())
            report = await (self.provider.collect(base) if base is not None else self.provider.collect())
            encoded, artifact = await c.worker(self._encode, report)
            key = secrets.token_hex(16)
            expires = self.clock() + c.SNAPSHOT_TTL
            resumed_from = resume_capture_id or None
            page = await c.worker(self._page, key, 0, json.loads(encoded), artifact, caller, expires,
                                  resumed_from, limit)
            await self.stored("put", capture_id=key, caller=caller, encoded=encoded, artifact_sha256=artifact,
                              baseline_id=report["baseline"]["baseline_id"], resumed_from=resumed_from,
                              now_ms=self.now_ms())
            self.cursors[key] = (expires, caller)
            return page
        finally:
            self.active = False

    async def compare(self, *, earlier_capture_id, later_capture_id="", recorded_fingerprints=None,
                      fingerprint_model=""):
        """HAMCP-228: compare the caller's stored capture with a later capture or recorded fingerprints.

        Reads only the caller's own digest-verified stored baselines; no Home
        Assistant read. The result is credential-screened, then bounded.
        """

        arguments = {"earlier_capture_id": earlier_capture_id, "later_capture_id": later_capture_id,
                     "fingerprint_model": fingerprint_model}
        if recorded_fingerprints is not None:
            arguments["recorded_fingerprints"] = recorded_fingerprints
        fingerprints = c.validate_compare_arguments(arguments)
        caller = current_caller_id()
        if caller == "anonymous":
            raise c.CaptureError("access_denied")
        earlier, _ = await self.load(earlier_capture_id, caller)
        later = (await self.load(later_capture_id, caller))[0] if later_capture_id else None
        return await c.worker(self._compare, earlier_capture_id, earlier, later_capture_id or None, later,
                              fingerprints, fingerprint_model)

    def _compare(self, earlier_id, earlier, later_id, later, fingerprints, model):
        try:
            first = baseline_from_value(earlier["baseline"])
            second = baseline_from_value(later["baseline"]) if later is not None else None
        except (BaselineValidationError, KeyError, TypeError):
            raise c.CaptureError("baseline_damaged") from None
        if second is not None:
            value = comparison_report_dict(compare_baselines(first, second))
            value["counts"] = {**value["counts"], PREFIX_MATCH: 0}
            value.update(mode="stored_captures", earlier_capture_id=earlier_id, later_capture_id=later_id)
        else:
            value = compare_recorded_fingerprints(first, fingerprints, model)
            value.update(mode="recorded_fingerprints", earlier_capture_id=earlier_id, later_capture_id=None)
        # Rows needing attention first, so a bounded result keeps them.
        value["records"].sort(key=lambda row: (row["classification"] in {"UNCHANGED", PREFIX_MATCH},
                                               row["configuration_id"]))
        value["record_order"] = "not_unchanged_or_prefix_match_first_then_configuration_id"
        value, withheld = untrusted_text.screen_tree(value, tuple(getattr(getattr(self.provider, "projector", None), "known_secrets", ())))
        value["screened_withheld_count"] = withheld
        return bound_report_dict(value, max_bytes=self.page_limit)
