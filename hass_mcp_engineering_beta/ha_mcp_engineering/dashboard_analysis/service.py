"""Frozen bounded continuation primitive, not yet composed into a public tool.

The collector contract is internal: collect(path) returns only a FrozenReport;
authority() checks current in-memory authority without dispatch or refresh.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time

from ..request_context import current_caller_id
from . import contracts as c
from .models import FrozenReport, Snapshot, validate_projection


class DashboardAnalysisService:
    def __init__(self, provider, *, response_limit, clock=time.monotonic, known_secrets=()):
        self.provider, self.clock = provider, clock
        self.page_bytes = min(c.PAGE_BYTES, response_limit) - c.ENVELOPE_RESERVE
        self.snapshots = {}
        # Same process-local ephemeral cursor-secret mechanism as existing
        # snapshot services. No credential file, option or external secret.
        self._secret = secrets.token_bytes(32)
        self.active = False
        self._known_secrets = tuple(known_secrets)

    def _token(self, key, offset, caller, path):
        payload = c.canonical([c.MODEL, key, offset, caller, path])
        mac = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return f"{key}:{offset}:{mac}"

    def _resolve(self, token, caller, path):
        if not c.CURSOR.fullmatch(token):
            raise c.AnalysisError("invalid_cursor")
        key, raw_offset, _ = token.split(":")
        offset = int(raw_offset)
        if not hmac.compare_digest(token, self._token(key, offset, caller, path)):
            raise c.AnalysisError("invalid_cursor")
        snapshot = self.snapshots.get(key)
        if snapshot is None or snapshot.expires <= self.clock():
            raise c.AnalysisError("snapshot_expired")
        if snapshot.caller != caller or snapshot.path != path:
            raise c.AnalysisError("invalid_cursor")
        return key, offset, snapshot

    def _check_authority(self, snapshot):
        if self.provider.authority() != snapshot.report.authority:
            raise c.AnalysisError("authority_drift")
        if snapshot.expires <= self.clock():
            raise c.AnalysisError("snapshot_expired")

    def _page(self, key, offset, snapshot, limit):
        report = snapshot.report
        total = len(report.items)
        if offset > total or (offset == total and offset != 0):
            raise c.AnalysisError("invalid_cursor")
        page = {"header": json.loads(report.header), "items": [],
                "snapshot_id": key, "report_digest": report.digest,
                "pagination": {"offset": offset, "returned": 0, "retained_items": total,
                    "requested_limit": limit, "next_cursor": None,
                    "complete_export": total == 0, "provider_reads_for_continuation": 0}}
        # Each pre-serialized item is costed once; fitting is not repeated
        # full-report serialization. A maximal footer reserves numeric growth.
        reserved = dict(page, pagination=dict(page["pagination"], returned=c.ITEMS,
                        offset=c.ITEMS, next_cursor=self._token(key, c.ITEMS, snapshot.caller, snapshot.path)))
        used = len(c.canonical(reserved))
        for encoded in report.items[offset:offset + limit]:
            cost = len(encoded) + 1
            if used + cost > self.page_bytes:
                break
            page["items"].append(json.loads(encoded))
            used += cost
        returned = len(page["items"])
        if total and not returned:
            raise c.AnalysisError("output_limit")
        more = offset + returned < total
        page["pagination"].update(returned=returned, complete_export=not more,
            next_cursor=self._token(key, offset + returned, snapshot.caller, snapshot.path) if more else None)
        if len(c.canonical(page)) > self.page_bytes:
            raise c.AnalysisError("output_limit")
        return page

    def _prepare(self, report, path):
        if type(report) is not FrozenReport or len(report.items) > c.ITEMS:
            raise c.AnalysisError("output_limit")
        if type(report.header) is not bytes or any(type(item) is not bytes for item in report.items):
            raise c.AnalysisError("malformed_response")
        if len(report.header) + sum(map(len, report.items)) > c.SNAPSHOT_BYTES - 4096:
            raise c.AnalysisError("output_limit")
        header = c.parse(report.header, maximum=c.SNAPSHOT_BYTES, depth=16)
        items = [c.parse(item, maximum=c.SNAPSHOT_BYTES, depth=8) for item in report.items]
        validate_projection(header, items, known_secrets=self._known_secrets)
        # Normalize once so supplied whitespace cannot change fitting costs.
        encoded_items = tuple(c.canonical(item) for item in items)
        if (type(header) is not dict or header.get("requested_path") != path
                or header.get("canonical_path") != path or header.get("model") != c.MODEL):
            raise c.AnalysisError("identity_mismatch")
        # Producer bytes are detached and immutable. Lifecycle metadata is set
        # once, so repeated pages preserve exactly the same header.
        now = datetime.now(timezone.utc)
        header["snapshot_created_at"] = now.isoformat()
        header["snapshot_expires_at"] = datetime.fromtimestamp(now.timestamp() + c.TTL_SECONDS,
                                                               tz=timezone.utc).isoformat()
        header["snapshot_ttl_seconds"] = c.TTL_SECONDS
        encoded_header = c.canonical(header)
        hashed = hashlib.sha256()
        # Length-prefixed chunks avoid ambiguous concatenation.
        for chunk in (encoded_header, *encoded_items):
            hashed.update(len(chunk).to_bytes(8, "big"))
            hashed.update(chunk)
        return FrozenReport(encoded_header, encoded_items, report.authority, "sha256:" + hashed.hexdigest())

    async def analyze(self, *, url_path, limit=25, cursor=""):
        c.validate_arguments({"url_path": url_path, "limit": limit, "cursor": cursor})
        caller = current_caller_id()
        if not caller or caller == "anonymous":
            raise c.AnalysisError("access_denied")
        if cursor:
            key, offset, snapshot = self._resolve(cursor, caller, url_path)
            self._check_authority(snapshot)
            page = await c.worker(self._page, key, offset, snapshot, limit)
            self._check_authority(snapshot)
            return page
        if self.active:
            raise c.AnalysisError("capacity_busy")
        self.snapshots = {k: v for k, v in self.snapshots.items() if v.expires > self.clock()}
        if len(self.snapshots) >= c.SNAPSHOTS:
            raise c.AnalysisError("capacity_busy")
        self.active = True
        try:
            async with asyncio.timeout(c.COLLECTION_SECONDS):
                expected = self.provider.authority()
                report = await self.provider.collect(url_path)
                if type(report) is not FrozenReport:
                    raise c.AnalysisError("malformed_response")
                if report.authority != expected or self.provider.authority() != expected:
                    raise c.AnalysisError("authority_drift")
                report = await c.worker(self._prepare, report, url_path)
                key = secrets.token_hex(16)
                snapshot = Snapshot(report, caller, url_path, self.clock() + c.TTL_SECONDS)
                self._check_authority(snapshot)
                page = await c.worker(self._page, key, 0, snapshot, limit)
                self._check_authority(snapshot)
                self.snapshots[key] = snapshot
                return page
        except TimeoutError:
            raise c.AnalysisError("timeout") from None
        finally:
            self.active = False
