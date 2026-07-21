"""Safe runtime state exposed by the beta-native health tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .observability import METRICS
from .capabilities import (
    BETA_NATIVE_CAPABILITIES,
    CAPABILITIES,
    dynamic_upstream_capabilities,
)
from .version import SERVER_ID, SERVER_NAME, SERVER_VERSION


@dataclass
class HealthRegistry:
    settings: Any = None
    audit: Any = None
    gateway: Any = None
    configuration_valid: bool = False
    governance: Any = None
    dependency: Any = None
    reliability: Any = None
    impact: Any = None
    integrity: Any = None
    incident: Any = None
    handoff: Any = None
    upstream_dashboard: Any = None
    upstream_read_gateway: Any = None

    def configure(
        self,
        settings,
        audit,
        gateway,
        governance=None,
        dependency=None,
        reliability=None,
        impact=None,
        integrity=None,
        incident=None,
        handoff=None,
        upstream_dashboard=None,
        upstream_read_gateway=None,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.gateway = gateway
        self.configuration_valid = True
        self.governance = governance
        self.dependency = dependency
        self.reliability = reliability
        self.impact = impact
        self.integrity = integrity
        self.incident = incident
        self.handoff = handoff
        self.upstream_dashboard = upstream_dashboard
        self.upstream_read_gateway = upstream_read_gateway

    def snapshot(self, ha_connection: dict[str, Any]) -> dict[str, Any]:
        metrics = METRICS.snapshot()
        return {
            "server": {"id": SERVER_ID, "name": SERVER_NAME, "version": SERVER_VERSION},
            "runtime": "home_assistant_addon" if self.settings and self.settings.ha_url == "http://supervisor/core" else "standalone",
            "uptime_seconds": metrics["uptime_seconds"],
            "home_assistant": ha_connection,
            "latency": {
                "mcp_operations": metrics["mcp_operation_latency"],
                "tools": metrics["tool_latency"],
                "home_assistant": metrics["home_assistant_latency"],
            },
            "transport": {
                "completed_request_count": metrics["transport_request_count"],
                "session_lifetime_in_latency": False,
            },
            "mcp_operation_count": metrics["mcp_operation_count"],
            "mcp_operation_methods": metrics["mcp_operation_methods"],
            "registered_tool_count": (
                len(CAPABILITIES)
                + len(BETA_NATIVE_CAPABILITIES)
                + len(dynamic_upstream_capabilities())
            ),
            "engineering_registered_tool_count": (
                len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES)
            ),
            "dynamic_upstream_tool_count": len(dynamic_upstream_capabilities()),
            "audit": self.audit.state() if self.audit else {"enabled": False, "configured": False},
            "logging": {
                "structured": True,
                "level": self.settings.log_level if self.settings else "unknown",
                "redaction_filter": True,
            },
            "recent_error_counts": metrics["recent_error_counts"],
            "provider_routing": {
                **metrics["provider_routing"],
                "standard_ha_mcp_delegation": "unavailable",
                "direct_fallback_requires_explicit_policy": True,
                "approved_direct_read_tools": [
                    "search_entities",
                    "get_entity",
                    "list_areas",
                    "search_services",
                    "list_services",
                ],
                "standard_ha_mcp_exact_mapping_count": 0,
            },
            "upstream_dashboard": (
                self.upstream_dashboard.health_snapshot()
                if self.upstream_dashboard
                else {
                    "configured": False,
                    "credential_present": False,
                    "reachable": False,
                    "capability_status": "unconfigured",
                    "trust_mode": None,
                    "trust_profile": None,
                    "reviewed_contract_match": False,
                    "expected_input_schema_fingerprint": None,
                    "observed_input_schema_fingerprint": None,
                    "input_schema_match": False,
                    "expected_reviewed_security_contract_fingerprint": None,
                    "observed_reviewed_security_contract_fingerprint": None,
                    "reviewed_security_contract_match": False,
                    "expected_fixture_runtime_descriptor_fingerprint": None,
                    "expected_published_runtime_descriptor_fingerprint": None,
                    "observed_runtime_descriptor_fingerprint": None,
                    "runtime_descriptor_match": False,
                    "published_runtime_descriptor_match": False,
                    "runtime_descriptor_drift": "not_observed",
                    "argument_constraints_active": True,
                    "screenshots_allowed": False,
                    "preference_writes_allowed": False,
                    "writes_allowed": False,
                }
            ),
            "upstream_read_gateway": (
                self.upstream_read_gateway.health_snapshot()
                if self.upstream_read_gateway
                else {
                    "configured": False,
                    "initialized": False,
                    "generic_delegation_available": False,
                    "dynamically_exposed_count": 0,
                    "writes_allowed": False,
                    "direct_ha_fallback_allowed": False,
                }
            ),
            "dependency_analysis": {
                **metrics["dependency_analysis"],
                "index": self.dependency.health() if self.dependency else {"configured": False},
            },
            "automation_reliability_analysis": {
                **metrics["automation_reliability_analysis"],
                "runtime": self.reliability.health() if self.reliability else {"configured": False},
            },
            "change_impact_analysis": {
                **metrics["change_impact_analysis"],
                "runtime": self.impact.health()
                if self.impact
                else {"configured": False},
            },
            "configuration_integrity_analysis": {
                **metrics["configuration_integrity_analysis"],
                "runtime": self.integrity.health()
                if self.integrity
                else {"configured": False},
            },
            "incident_correlation": {
                **metrics["incident_correlation"],
                "runtime": self.incident.health()
                if self.incident
                else {"configured": False},
            },
            "handoff_generation": {
                **metrics["handoff_generation"],
                "runtime": self.handoff.health()
                if self.handoff
                else {"configured": False},
            },
            "rate_limiter": self.gateway.rate_limiter_state() if self.gateway else {"configured": False},
            "redaction": {"enabled": bool(self.settings and self.settings.redaction_enabled)},
            "configuration": {"valid": self.configuration_valid},
            "tool_call_count": metrics["tool_call_count"],
            "retry_count": metrics["retry_count"],
            "timeout_count": metrics["timeout_count"],
            "governance": (
                self.governance.health_summary()
                if self.governance
                else {"enabled": False, "storage": {"configured": False}}
            ),
        }


HEALTH = HealthRegistry()
