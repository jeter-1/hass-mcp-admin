"""Safe runtime state exposed by the beta-native health tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .observability import METRICS
from .capabilities import BETA_NATIVE_CAPABILITIES, CAPABILITIES
from .version import SERVER_ID, SERVER_NAME, SERVER_VERSION


@dataclass
class HealthRegistry:
    settings: Any = None
    audit: Any = None
    gateway: Any = None
    configuration_valid: bool = False
    governance: Any = None
    dependency: Any = None

    def configure(self, settings, audit, gateway, governance=None, dependency=None) -> None:
        self.settings = settings
        self.audit = audit
        self.gateway = gateway
        self.configuration_valid = True
        self.governance = governance
        self.dependency = dependency

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
            "registered_tool_count": len(CAPABILITIES) + len(BETA_NATIVE_CAPABILITIES),
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
            },
            "dependency_analysis": {
                **metrics["dependency_analysis"],
                "index": self.dependency.health() if self.dependency else {"configured": False},
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
