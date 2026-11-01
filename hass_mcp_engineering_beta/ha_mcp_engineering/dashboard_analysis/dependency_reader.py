"""HAMCP-227: bounded dashboard reads for diagnostic dependency coverage.

The reviewed ``list_dashboards`` path enumerates dashboards, and the reviewed
analysis read path (``get_analysis_configuration``) reads each one. The
existing dashboard walker (``rules.scan``) extracts the references. There is
no other route, no direct Home Assistant fallback and no write path. Every
dispatch is gated on both the dependency build's authority and a separately
acquired ``core.dashboard_configuration_read`` lease, and each one writes an
attributed audit event.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from ..request_context import begin_request, current_telemetry, end_request
from ..untrusted_text import screen
from . import contracts as c
from . import rules
from .models import Inventory

CAPABILITIES = ("core.dashboard_configuration_read",)
LIST_RESPONSE_BYTES = 262_144
PROVIDER = "upstream_dashboard"
_EMPTY_STATES = Inventory("states", (), False, 0, None, 0, 0)
_EMPTY_REGISTRY = Inventory("registry", (), False, 0, None, 0, 0)


def summarize_scan(configuration: Any, result: dict) -> dict:
    """Keep only the reference items and counted gap reasons of one walk."""
    strategy = isinstance(configuration, dict) and "strategy" in configuration
    truncated = result["counts"]["processing_truncated"] is True
    gaps: dict[str, int] = {}
    items = []
    for item in result["items"]:
        if item.get("kind") == "entity_reference":
            items.append({key: item.get(key) for key in ("kind", "entity_id", "rule_id", "pointer", "conditional")})
        elif item.get("kind") == "coverage_gap":
            reason = item.get("reason")
            if item.get("pointer") == "" and (
                (truncated and reason == "structural_limit")
                or (strategy and reason == "unsupported_component")
            ):
                # Reported once below as projection truncation or strategy.
                continue
            if isinstance(reason, str):
                gaps[reason] = gaps.get(reason, 0) + 1
    if strategy:
        gaps["strategy_not_expanded"] = 1
    return {"items": items, "gaps": gaps, "processing_truncated": truncated}


class DashboardDependencyReader:
    """One build's dashboard reader; ``current`` stays bound to its authority."""

    def __init__(self, core_runtime, upstream, *, audit=None, known_secrets=()):
        self.core_runtime, self.upstream, self.audit = core_runtime, upstream, audit
        self.known_secrets = tuple(secret for secret in known_secrets if secret)
        self._expected: tuple | None = None

    @property
    def provenance(self) -> str:
        return (f"{PROVIDER} reviewed list_dashboards and analysis read; "
                f"walker {c.MODEL}; no fallback")

    def _identity(self) -> tuple:
        status = self.core_runtime.route_status(CAPABILITIES)
        observation = self.core_runtime.current_observation
        if status.get("available") is not True or observation is None:
            raise c.AnalysisError("authority_unavailable")
        return (status.get("generation"), observation.version, observation.fingerprint,
                self.upstream.analysis_authority())

    def current(self) -> bool:
        if self._expected is None:
            return False
        try:
            return self._identity() == self._expected
        except Exception:
            return False

    @asynccontextmanager
    async def session(self):
        parent = current_telemetry()
        if parent is None or parent.core_dispatch_authorizer is None:
            raise c.AnalysisError("authority_unavailable")
        authority = self.core_runtime.acquire(CAPABILITIES)
        commits = None
        closed = False

        def authorize() -> bool:
            nonlocal commits, closed
            if closed or authority is None:
                return False
            if not parent.authorize_core_dispatch():
                closed = True
                return False
            if commits is None:
                commits = self.core_runtime.consume(authority)
            if commits is None or not self.core_runtime.revalidate(authority, commits):
                closed = True
                return False
            return True

        try:
            if authority is None:
                raise c.AnalysisError("authority_unavailable")
            yield _Session(self, parent, authorize)
        finally:
            closed = True
            if commits is not None:
                self.core_runtime.finish(commits)
            elif authority is not None:
                self.core_runtime.release(authority)


class _Session:
    def __init__(self, reader: DashboardDependencyReader, parent, authorize):
        self.reader, self.parent, self.authorize = reader, parent, authorize

    async def _child(self, tool_name: str, operation, call):
        telemetry, token = begin_request()
        telemetry.tool_name = tool_name
        telemetry.caller_id = self.parent.caller_id
        telemetry.core_dispatch_authorizer = self.authorize
        telemetry.audit_context["parent_request_id"] = self.parent.request_id
        status = "failed_or_cancelled"
        try:
            if not telemetry.authorize_core_dispatch():
                raise c.AnalysisError("authority_unavailable")
            result = await call()
            if not telemetry.authorize_core_dispatch():
                raise c.AnalysisError("authority_drift")
            status = "success"
            return result
        finally:
            try:
                if self.reader.audit is not None:
                    self.reader.audit.write({
                        "event": tool_name, "request_id": telemetry.request_id,
                        "parent_request_id": self.parent.request_id,
                        "provider": PROVIDER, "upstream_operation": operation,
                        "result_status": status,
                        "provider_dispatch_count": telemetry.provider_dispatch_count,
                        "fallback_occurred": False,
                    })
            finally:
                telemetry.core_dispatch_authorizer = lambda: False
                end_request(token)

    async def list(self, limit: int) -> dict:
        """One reviewed list request; returns validated, screened entries."""
        result = await self._child(
            "dependency_dashboard_list", "list_dashboards",
            lambda: self.reader.upstream.list_dashboards(limit=limit, response_limit=LIST_RESPONSE_BYTES),
        )
        data = result.data if isinstance(result.data, dict) else {}
        entries, invalid = [], 0
        for item in data.get("dashboards") or ():
            path = item.get("url_path") if isinstance(item, dict) else None
            if (not isinstance(path, str) or not c.PATH.fullmatch(path)
                    or screen(path, self.reader.known_secrets) is not None):
                invalid += 1
                continue
            title = item.get("title")
            entries.append({"url_path": path, "title": title if isinstance(title, str) else None})
        # The list may refresh reviewed admission; bind reads to the result.
        self.reader._expected = self.reader._identity()
        return {"dashboards": entries, "truncated": data.get("truncated") is True,
                "partial": result.completeness != "complete" or bool(invalid)}

    async def read(self, url_path: str) -> dict:
        """One reviewed analysis read and walk; failures stay with this dashboard."""
        if not c.PATH.fullmatch(url_path):
            return {"failure": "invalid_arguments"}
        if not self.reader.current():
            return {"failure": "authority_drift"}
        from ..clients.dashboard_analysis import CollectionBudget

        def check():
            if not self.authorize() or not self.reader.current():
                raise c.AnalysisError("authority_drift")

        try:
            data, _metadata = await self._child(
                "dependency_dashboard_read", "get_analysis_configuration",
                lambda: self.reader.upstream.get_analysis_configuration(
                    url_path=url_path, authorize=check, budget=CollectionBudget()),
            )
            configuration = data["configuration"]
            observation = self.reader.core_runtime.current_observation
            if observation is None:
                raise c.AnalysisError("authority_unavailable")
            result = await c.worker(
                rules.scan, configuration, data["engineering_config_hash"], _EMPTY_STATES,
                _EMPTY_REGISTRY, core_version=observation.version, known_secrets=self.reader.known_secrets,
            )
            summary = await c.worker(summarize_scan, configuration, result)
            del configuration, data
            check()
        except c.AnalysisError as error:
            return {"failure": error.reason}
        return {**summary, "projection_hash": result["projection_hash"]}
