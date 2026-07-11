"""Beta-only configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

OPTIONS_PATH = Path(os.environ.get("HAMCP_OPTIONS_PATH", "/data/options.json"))

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

    @property
    def api_url(self) -> str:
        return f"{self.ha_url}/api"

    @property
    def websocket_url(self) -> str:
        return self.ha_url.replace("http", "ws", 1) + "/websocket"


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
    return Settings(
        ha_url=ha_url,
        ha_token=token,
        access_secret=secret,
        port=int(os.environ.get("MCP_PORT", "8100")),
        audit_path=os.environ.get("AUDIT_PATH", "/data/audit.jsonl"),
        rate_limit_per_minute=int(options.get("rate_limit_per_minute", 120)),
        rate_limit_burst=int(options.get("rate_limit_burst", 25)),
        destructive_services=frozenset(destructive),
    )
