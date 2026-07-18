"""Beta-only configuration loading."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

OPTIONS_PATH = Path(os.environ.get("HAMCP_OPTIONS_PATH", "/data/options.json"))
MIN_ACCESS_SECRET_LENGTH = 24
MAX_TRUSTED_PROXY_CIDRS = 64

DEFAULT_DESTRUCTIVE_SERVICES = (
    "lock.unlock",
    "lock.open",
    "cover.open_cover",
    "alarm_control_panel.alarm_disarm",
    "homeassistant.restart",
    "homeassistant.stop",
)


@dataclass(frozen=True)
class Settings:
    ha_url: str
    ha_token: str
    access_secret: str
    port: int
    audit_path: str
    rate_limit_per_minute: int
    rate_limit_burst: int
    destructive_services: frozenset[str]
    audit_enabled: bool = True
    audit_max_payload_chars: int = 8192
    log_level: str = "INFO"
    ha_timeout_seconds: float = 60.0
    response_size_limit: int = 60_000
    redaction_enabled: bool = True
    governance_path: str = "/data/governance/change_plans"
    governance_retention_days: int = 90
    trust_cf_connecting_ip: bool = False
    trusted_proxy_cidrs: tuple[str, ...] = ()
    ingress_port: int = 8110
    upstream_dashboard_mcp_url: str = field(default="", repr=False)
    # ``dependency_index_prewarm`` is retained as an options compatibility alias.
    dependency_index_prewarm: bool = False
    prewarm_enabled: bool = True
    prewarm_startup_delay_seconds: float = 45.0
    prewarm_retry_delay_seconds: float = 300.0
    dependency_index_soft_ttl_seconds: float = 600.0
    dependency_index_hard_ttl_seconds: float = 3600.0

    @property
    def api_url(self) -> str:
        return f"{self.ha_url}/api"

    @property
    def websocket_url(self) -> str:
        parsed = urlsplit(self.ha_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        if parsed.hostname == "supervisor" and base_path == "/core":
            path = "/core/websocket"
        else:
            path = f"{base_path}/api/websocket"
        return urlunsplit((scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class UpstreamDashboardEndpoint:
    """Validated secret-bearing upstream MCP endpoint.

    The complete URL and derived secret values are deliberately excluded from
    representations so configuration diagnostics cannot expose them.
    """

    url: str = field(repr=False)
    credential_present: bool
    secret_values: tuple[str, ...] = field(repr=False)


def parse_upstream_dashboard_endpoint(
    value: str | None,
) -> UpstreamDashboardEndpoint | None:
    """Validate the optional dashboard MCP URL without returning it in errors."""

    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The upstream dashboard MCP URL is malformed.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "The upstream dashboard MCP URL must use http or https and include a host."
        )
    if parsed.fragment:
        raise ValueError("The upstream dashboard MCP URL must not include a fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("The upstream dashboard MCP URL contains an invalid port.")

    generic_segments = {"mcp", "sse"}
    path_segments = tuple(
        segment for segment in parsed.path.split("/") if segment
    )
    credential_segments = tuple(
        segment
        for segment in path_segments
        if segment.lower() not in generic_segments and len(segment) >= 8
    )
    credential_present = bool(
        parsed.username
        or parsed.password
        or parsed.query
        or credential_segments
    )
    if not credential_present:
        raise ValueError(
            "The upstream dashboard MCP URL must contain a secret-bearing path or credential."
        )

    secrets = [candidate]
    if parsed.netloc:
        secrets.append(parsed.netloc)
    if parsed.hostname:
        secrets.append(parsed.hostname)
    if parsed.path and parsed.path != "/":
        secrets.append(parsed.path)
        decoded_path = unquote(parsed.path)
        if decoded_path != parsed.path:
            secrets.append(decoded_path)
    if parsed.query:
        secrets.append(parsed.query)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True):
            secrets.extend((key, item))
    if parsed.username:
        secrets.append(parsed.username)
    if parsed.password:
        secrets.append(parsed.password)
    secrets.extend(
        value
        for segment in credential_segments
        for value in (segment, unquote(segment))
    )
    return UpstreamDashboardEndpoint(
        url=candidate,
        credential_present=True,
        secret_values=tuple(dict.fromkeys(secret for secret in secrets if secret)),
    )


def _read_options(path: Path = OPTIONS_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load_settings() -> Settings:
    options = _read_options()
    ha_url = os.environ.get("HA_URL", "http://supervisor/core").rstrip("/")
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN", "")
    secret = (options.get("access_secret") or os.environ.get("ACCESS_SECRET", "")).strip()
    destructive = options.get("destructive_services") or DEFAULT_DESTRUCTIVE_SERVICES
    configured_proxies = options.get("trusted_proxy_cidrs", [])
    if not isinstance(configured_proxies, (list, tuple)):
        configured_proxies = [configured_proxies]
    return Settings(
        ha_url=ha_url,
        ha_token=token,
        access_secret=secret,
        port=int(os.environ.get("MCP_PORT", "8100")),
        audit_path=str(options.get("audit_path", os.environ.get("AUDIT_PATH", "/data/audit.jsonl"))),
        rate_limit_per_minute=int(options.get("rate_limit_per_minute", 120)),
        rate_limit_burst=int(options.get("rate_limit_burst", 25)),
        destructive_services=frozenset(destructive),
        audit_enabled=bool(options.get("audit_enabled", True)),
        audit_max_payload_chars=int(options.get("audit_max_payload_chars", 8192)),
        log_level=str(options.get("log_level", os.environ.get("LOG_LEVEL", "INFO"))).upper(),
        ha_timeout_seconds=float(options.get("ha_timeout_seconds", 60)),
        response_size_limit=int(options.get("response_size_limit", 60_000)),
        redaction_enabled=bool(options.get("redaction_enabled", True)),
        governance_path=os.environ.get(
            "GOVERNANCE_PATH", "/data/governance/change_plans"
        ),
        governance_retention_days=int(os.environ.get("GOVERNANCE_RETENTION_DAYS", "90")),
        trust_cf_connecting_ip=bool(options.get("trust_cf_connecting_ip", False)),
        trusted_proxy_cidrs=tuple(
            str(value).strip() for value in configured_proxies if str(value).strip()
        ),
        ingress_port=int(os.environ.get("APPROVAL_INGRESS_PORT", "8110")),
        upstream_dashboard_mcp_url=str(
            options.get(
                "upstream_dashboard_mcp_url",
                os.environ.get("UPSTREAM_DASHBOARD_MCP_URL", ""),
            )
            or ""
        ).strip(),
        dependency_index_prewarm=bool(options.get("dependency_index_prewarm", False)),
        # RC2dev4 stored ``dependency_index_prewarm: false`` as its default.
        # Treating that legacy default as authoritative would silently disable
        # RC2dev5 prewarming on upgrades. The new key is the sole control; its
        # absence selects the safe beta default.
        prewarm_enabled=bool(options.get("prewarm_enabled", True)),
        prewarm_startup_delay_seconds=float(
            options.get("prewarm_startup_delay_seconds", 45)
        ),
        prewarm_retry_delay_seconds=float(
            options.get("prewarm_retry_delay_seconds", 300)
        ),
        dependency_index_soft_ttl_seconds=float(
            options.get("dependency_index_soft_ttl_seconds", 600)
        ),
        dependency_index_hard_ttl_seconds=float(
            options.get("dependency_index_hard_ttl_seconds", 3600)
        ),
    )
