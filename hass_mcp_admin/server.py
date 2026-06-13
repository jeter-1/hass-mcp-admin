"""HA MCP Admin — full-access Model Context Protocol server for Home Assistant.

Runs as a Home Assistant add-on. Talks to HA Core through the Supervisor
proxy (REST + WebSocket) using the injected SUPERVISOR_TOKEN, so no
long-lived access token is required.

Exposes a streamable-HTTP MCP endpoint protected by a secret URL path:

    https://<your-tunnel-domain>/<access_secret>/mcp

Designed for Claude (claude.ai custom connectors), but works with any
streamable-HTTP MCP client.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
import uvicorn
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPTIONS_PATH = "/data/options.json"


def _load_options() -> dict:
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


OPTS = _load_options()

# Supervisor proxy (default when running as an add-on with homeassistant_api).
# For standalone/Docker use, set HA_URL=http://<ha-host>:8123 and HA_TOKEN=<llat>.
HA_URL = os.environ.get("HA_URL", "http://supervisor/core").rstrip("/")
HA_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN", "")

ACCESS_SECRET = (OPTS.get("access_secret") or os.environ.get("ACCESS_SECRET", "")).strip()

DEFAULT_DESTRUCTIVE = [
    "lock.unlock",
    "lock.open",
    "cover.open_cover",
    "alarm_control_panel.alarm_disarm",
    "homeassistant.restart",
    "homeassistant.stop",
]
DESTRUCTIVE_SERVICES = set(OPTS.get("destructive_services") or DEFAULT_DESTRUCTIVE)

PORT = int(os.environ.get("MCP_PORT", "8099"))

API = f"{HA_URL}/api"
WS_URL = HA_URL.replace("http", "ws", 1) + "/websocket"
HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

MAX_CHARS = 60_000  # cap tool output so huge payloads don't blow up context


# ---------------------------------------------------------------------------
# HA REST / WebSocket helpers
# ---------------------------------------------------------------------------


async def rest(method: str, path: str, body: Any = None, raw: bool = False) -> Any:
    url = f"{API}{path}"
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, headers=HEADERS, json=body) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HA API {resp.status} on {method} {path}: {text[:500]}")
            if raw:
                return text
            try:
                return json.loads(text) if text else None
            except json.JSONDecodeError:
                return text


async def ws_command(payload: dict) -> Any:
    """Run a single authenticated command against the HA WebSocket API."""
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(WS_URL) as ws:
            msg = await ws.receive_json()  # auth_required
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"Unexpected WS handshake: {msg}")
            await ws.send_json({"type": "auth", "access_token": HA_TOKEN})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"WS auth failed: {msg}")
            await ws.send_json({"id": 1, **payload})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1 and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise RuntimeError(f"WS command failed: {json.dumps(msg.get('error'))}")
                    return msg.get("result")


def dump(data: Any) -> str:
    out = json.dumps(data, indent=2, default=str)
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS] + f"\n... [truncated at {MAX_CHARS} chars — narrow the query]"
    return out


def _utc(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

INSTRUCTIONS = """Operating procedure for this Home Assistant admin server:
1. Debug with evidence, not hypothesis: pull get_automation_trace, get_history,
   or get_logbook before proposing a cause for misbehavior.
2. If an automation config contains 'use_blueprint', read the blueprint source
   with get_blueprint before reasoning about its triggers/conditions — the
   inputs alone do not describe the logic.
3. Test every Jinja template with render_template against live state before
   placing it in any automation or script config.
4. After upsert_automation, verify the stored_config_read_back matches intent;
   run check_config after any structural change.
5. Never call a destructive service (confirm=true) unless the user explicitly
   requested that physical action in the current conversation.
6. Prefer narrow queries (filters, limits, short history windows) over broad
   dumps; output is truncated at 60k characters."""

mcp = FastMCP(
    "ha-admin",
    instructions=INSTRUCTIONS,
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
)


# ----- Visibility & debugging ----------------------------------------------


@mcp.tool()
async def get_entity(entity_id: str) -> str:
    """Get full current state and all attributes for one entity."""
    return dump(await rest("GET", f"/states/{entity_id}"))


@mcp.tool()
async def search_entities(query: str = "", domain: str = "", limit: int = 50) -> str:
    """Search all entity states. Filters by substring match on entity_id or
    friendly_name, optionally restricted to a domain (e.g. 'binary_sensor').
    Returns entity_id, state, and friendly_name only — use get_entity for
    full attributes."""
    states = await rest("GET", "/states")
    q = query.lower()
    results = []
    for s in states:
        eid = s["entity_id"]
        if domain and not eid.startswith(domain + "."):
            continue
        name = (s.get("attributes", {}).get("friendly_name") or "")
        if q and q not in eid.lower() and q not in name.lower():
            continue
        results.append({"entity_id": eid, "state": s.get("state"), "friendly_name": name})
        if len(results) >= limit:
            break
    return dump({"count": len(results), "results": results})


@mcp.tool()
async def get_history(entity_id: str, hours: float = 24, minimal: bool = True) -> str:
    """State history for one entity over the last N hours (max 168 — for
    longer windows, make multiple chunked calls). Set minimal=False to
    include attribute changes (much larger output)."""
    if hours > 168:
        return (
            f"Refused: {hours}h exceeds the 168h (7-day) per-call limit. "
            "Chunk the range into multiple calls to stay inside gateway timeouts."
        )
    start = _utc(hours)
    path = f"/history/period/{start}?filter_entity_id={entity_id}"
    if minimal:
        path += "&minimal_response&no_attributes"
    data = await rest("GET", path)
    return dump(data)


@mcp.tool()
async def get_logbook(hours: float = 12, entity_id: str = "") -> str:
    """Logbook entries (what happened, triggered by what) for the last N
    hours, optionally filtered to one entity."""
    start = _utc(hours)
    path = f"/logbook/{start}"
    if entity_id:
        path += f"?entity={entity_id}"
    return dump(await rest("GET", path))


@mcp.tool()
async def get_error_log(tail_lines: int = 200) -> str:
    """The Home Assistant core error log (most recent lines)."""
    text = await rest("GET", "/error_log", raw=True)
    lines = text.splitlines()
    return "\n".join(lines[-tail_lines:])


@mcp.tool()
async def render_template(template: str) -> str:
    """Render a Jinja2 template against live HA state. Use this to test
    template logic before putting it in an automation."""
    return await rest("POST", "/template", {"template": template}, raw=True)


@mcp.tool()
async def list_automation_traces(automation_id: str) -> str:
    """List recent execution traces for an automation. automation_id is the
    internal id (the 'id' attribute, not the entity_id) — get it from
    list_automations. Returns run_ids, timestamps, and last_step."""
    result = await ws_command(
        {"type": "trace/list", "domain": "automation", "item_id": automation_id}
    )
    slim = [
        {
            "run_id": t.get("run_id"),
            "timestamp": t.get("timestamp"),
            "state": t.get("state"),
            "script_execution": t.get("script_execution"),
            "last_step": t.get("last_step"),
            "error": t.get("error"),
        }
        for t in (result or [])
    ]
    return dump(slim)


@mcp.tool()
async def get_automation_trace(automation_id: str, run_id: str) -> str:
    """Full step-by-step trace of one automation run: which triggers fired,
    how each condition evaluated, and where execution stopped. The single
    best debugging tool here."""
    result = await ws_command(
        {
            "type": "trace/get",
            "domain": "automation",
            "item_id": automation_id,
            "run_id": run_id,
        }
    )
    return dump(result)


# ----- Automations & config read/write --------------------------------------


@mcp.tool()
async def list_automations(query: str = "", limit: int = 100) -> str:
    """Automations with entity_id, friendly_name, state, last_triggered, and
    the internal id needed for config edits and traces. Optional substring
    filter on entity_id/friendly_name — prefer filtering over full dumps to
    save context."""
    states = await rest("GET", "/states")
    q = query.lower()
    autos = []
    for s in states:
        if not s["entity_id"].startswith("automation."):
            continue
        attrs = s.get("attributes", {})
        name = attrs.get("friendly_name") or ""
        if q and q not in s["entity_id"].lower() and q not in name.lower():
            continue
        autos.append(
            {
                "entity_id": s["entity_id"],
                "friendly_name": name,
                "state": s.get("state"),
                "last_triggered": attrs.get("last_triggered"),
                "id": attrs.get("id"),
            }
        )
        if len(autos) >= limit:
            break
    return dump({"count": len(autos), "automations": autos})


@mcp.tool()
async def get_automation_config(automation_id: str) -> str:
    """Full YAML-equivalent config (triggers/conditions/actions) of an
    automation, by internal id. If the result contains 'use_blueprint', the
    actual logic lives in the blueprint — read it with get_blueprint before
    reasoning about behavior."""
    return dump(await rest("GET", f"/config/automation/config/{automation_id}"))


# ----- Blueprints ------------------------------------------------------------

BLUEPRINT_BASES = ["/homeassistant/blueprints", "/config/blueprints"]


def _blueprint_base() -> Optional[str]:
    for base in BLUEPRINT_BASES:
        if os.path.isdir(base):
            return base
    return None


@mcp.tool()
async def list_blueprints(domain: str = "automation") -> str:
    """List installed blueprints for a domain ('automation' or 'script'),
    with their path (used by get_blueprint and by automations'
    use_blueprint.path), name, and input definitions."""
    if domain not in ("automation", "script"):
        return "Refused: domain must be 'automation' or 'script'."
    result = await ws_command({"type": "blueprint/list", "domain": domain})
    return dump(result)


@mcp.tool()
async def get_blueprint(path: str, domain: str = "automation") -> str:
    """Read the raw YAML source of an installed blueprint — the actual
    trigger/condition/action logic behind any use_blueprint automation.
    path: as shown in use_blueprint.path or list_blueprints (e.g.
    'homeassistant/motion_light.yaml'). Read-only; restricted to the
    blueprints directory."""
    if domain not in ("automation", "script"):
        return "Refused: domain must be 'automation' or 'script'."
    base = _blueprint_base()
    if base is None:
        return (
            "Blueprint directory not mounted. Ensure the add-on config.yaml "
            "includes map: [homeassistant_config:ro] and the add-on was rebuilt."
        )
    root = os.path.realpath(os.path.join(base, domain))
    full = os.path.realpath(os.path.join(root, path))
    if not (full == root or full.startswith(root + os.sep)):
        return "Refused: path escapes the blueprints directory."
    if not full.endswith((".yaml", ".yml")):
        return "Refused: only .yaml/.yml blueprint files are readable."
    if not os.path.isfile(full):
        return f"Not found: {os.path.relpath(full, root)} (use list_blueprints to see installed paths)."
    with open(full, encoding="utf-8") as f:
        text = f.read()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... [truncated]"
    return text


@mcp.tool()
async def upsert_automation(automation_id: str, config_json: str) -> str:
    """Create or replace an automation. automation_id: internal id (for a new
    automation, use a new unique string, e.g. a timestamp). config_json: the
    full automation config as a JSON object with alias, description, mode,
    triggers, conditions, actions. HA validates the schema server-side before
    persisting and reloads automations automatically. The response includes a
    read-back of the stored config — verify it matches intent. After any
    structural change, also run check_config."""
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        return (
            f"Refused: config_json is not valid JSON — {e.msg} at line {e.lineno}, "
            f"column {e.colno}. Fix the JSON and retry; nothing was written."
        )
    if not isinstance(config, dict):
        return "Refused: config_json must be a JSON object, not a list or scalar."
    has_trigger = any(k in config for k in ("trigger", "triggers"))
    has_action = any(k in config for k in ("action", "actions"))
    missing = []
    if not has_trigger:
        missing.append("triggers")
    if not has_action:
        missing.append("actions")
    if missing:
        return (
            f"Refused: config is missing required key(s): {', '.join(missing)}. "
            "Nothing was written."
        )
    write_result = await rest("POST", f"/config/automation/config/{automation_id}", config)
    stored = await rest("GET", f"/config/automation/config/{automation_id}")
    return dump(
        {
            "write_result": write_result,
            "stored_config_read_back": stored,
            "note": "Verify stored config matches intent. Run check_config after structural changes.",
        }
    )


@mcp.tool()
async def delete_automation(automation_id: str, confirm: bool = False) -> str:
    """Delete an automation by internal id. Requires confirm=true."""
    if not confirm:
        return "Refused: pass confirm=true to delete this automation."
    return dump(await rest("DELETE", f"/config/automation/config/{automation_id}"))


@mcp.tool()
async def check_config() -> str:
    """Run HA's full configuration check. Returns 'valid' or the errors.
    Run this after config writes and before any restart."""
    return dump(await rest("POST", "/config/core/check_config"))


@mcp.tool()
async def call_service(domain: str, service: str, data_json: str = "{}", confirm: bool = False) -> str:
    """Call any HA service. data_json is the JSON service data, e.g.
    '{"entity_id": "light.office"}'. Services on the destructive list
    (locks, garage doors, alarm disarm, core restart) additionally require
    confirm=true."""
    key = f"{domain}.{service}"
    if key in DESTRUCTIVE_SERVICES and not confirm:
        return (
            f"Refused: '{key}' is on the destructive-services list. "
            "Re-call with confirm=true only if the user explicitly asked for this action."
        )
    data = json.loads(data_json) if data_json else {}
    result = await rest("POST", f"/services/{domain}/{service}", data)
    return dump(result)


@mcp.tool()
async def reload_domain(domain: str) -> str:
    """Reload config for a domain without restarting HA. Allowed:
    automation, script, scene, template, input_boolean, input_number,
    input_select, input_datetime, input_text, timer, group."""
    allowed = {
        "automation", "script", "scene", "template", "input_boolean",
        "input_number", "input_select", "input_datetime", "input_text",
        "timer", "group",
    }
    if domain not in allowed:
        return f"Refused: '{domain}' not in reloadable set {sorted(allowed)}."
    await rest("POST", f"/services/{domain}/reload", {})
    return f"Reloaded {domain}."


# ----- Registries ------------------------------------------------------------


@mcp.tool()
async def list_areas() -> str:
    """The area registry: area_id and name for every area."""
    return dump(await ws_command({"type": "config/area_registry/list"}))


@mcp.tool()
async def list_devices(query: str = "", limit: int = 100) -> str:
    """The device registry (manufacturer, model, area, connections), with
    optional substring filter on name/manufacturer/model. Use this to find
    orphaned or stale devices."""
    devices = await ws_command({"type": "config/device_registry/list"})
    q = query.lower()
    out = []
    for d in devices:
        blob = " ".join(
            str(d.get(k) or "") for k in ("name", "name_by_user", "manufacturer", "model")
        ).lower()
        if q and q not in blob:
            continue
        out.append(
            {
                "id": d.get("id"),
                "name": d.get("name_by_user") or d.get("name"),
                "manufacturer": d.get("manufacturer"),
                "model": d.get("model"),
                "area_id": d.get("area_id"),
                "disabled_by": d.get("disabled_by"),
            }
        )
        if len(out) >= limit:
            break
    return dump({"count": len(out), "devices": out})


@mcp.tool()
async def list_entity_registry(query: str = "", limit: int = 100) -> str:
    """The entity registry: platform, device_id, area, disabled/hidden
    status. This is where stale entity references and orphaned entities
    show up. Optional substring filter on entity_id/name."""
    entities = await ws_command({"type": "config/entity_registry/list"})
    q = query.lower()
    out = []
    for e in entities:
        blob = f"{e.get('entity_id','')} {e.get('name') or ''} {e.get('original_name') or ''}".lower()
        if q and q not in blob:
            continue
        out.append(
            {
                "entity_id": e.get("entity_id"),
                "name": e.get("name") or e.get("original_name"),
                "platform": e.get("platform"),
                "device_id": e.get("device_id"),
                "area_id": e.get("area_id"),
                "disabled_by": e.get("disabled_by"),
                "hidden_by": e.get("hidden_by"),
            }
        )
        if len(out) >= limit:
            break
    return dump({"count": len(out), "entities": out})


@mcp.tool()
async def search_services(query: str, limit: int = 30) -> str:
    """Find services by substring match on 'domain.service', name, or
    description. Returns slim results (service, description, field names
    only) — use this for discovery, then list_services(domain) for full
    field schemas. Much cheaper on context than list_services."""
    services = await rest("GET", "/services")
    q = query.lower()
    out = []
    for dom in services:
        domain = dom.get("domain", "")
        for svc, meta in (dom.get("services") or {}).items():
            meta = meta or {}
            key = f"{domain}.{svc}"
            blob = f"{key} {meta.get('name') or ''} {meta.get('description') or ''}".lower()
            if q not in blob:
                continue
            out.append(
                {
                    "service": key,
                    "description": (meta.get("description") or meta.get("name") or "")[:160],
                    "fields": sorted((meta.get("fields") or {}).keys()),
                }
            )
            if len(out) >= limit:
                return dump({"count": len(out), "results": out, "note": "limit reached"})
    return dump({"count": len(out), "results": out})


@mcp.tool()
async def list_services(domain: str = "") -> str:
    """All available services with FULL field schemas — large output. For
    discovery, use search_services first; only call this (with a domain
    filter) when you need complete field metadata."""
    services = await rest("GET", "/services")
    if domain:
        services = [s for s in services if s.get("domain") == domain]
    return dump(services)



@mcp.tool()
async def get_audit_log(lines: int = 50, event: str = "") -> str:
    """Tail this server's own audit log (tool calls, auth failures, rate
    limit events). Optional event filter: tool_call, auth_failure,
    auth_failure_throttled, rate_limited."""
    path = os.environ.get("AUDIT_PATH", "/data/audit.jsonl")
    if not os.path.exists(path):
        return "No audit log yet."
    with open(path, encoding="utf-8") as f:
        rows = f.readlines()
    if event:
        rows = [r for r in rows if f'"event": "{event}"' in r or f'"event":"{event}"' in r]
    return "".join(rows[-min(lines, 500):]) or "No matching entries."


# ---------------------------------------------------------------------------
# ASGI gateway: secret-path auth + rate limiting + audit logging
# ---------------------------------------------------------------------------

AUDIT_PATH = os.environ.get("AUDIT_PATH", "/data/audit.jsonl")
AUDIT_MAX_BYTES = 5 * 1024 * 1024
RATE_PER_MINUTE = int(OPTS.get("rate_limit_per_minute", 120))
RATE_BURST = int(OPTS.get("rate_limit_burst", 25))


class TokenBucket:
    def __init__(self, per_minute: float, burst: float):
        self.rate = per_minute / 60.0
        self.cap = burst
        self.tokens = burst
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def audit_write(entry: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    try:
        if os.path.exists(AUDIT_PATH) and os.path.getsize(AUDIT_PATH) > AUDIT_MAX_BYTES:
            os.replace(AUDIT_PATH, AUDIT_PATH + ".1")
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as e:
        print(f"audit write failed: {e}", flush=True)


def _summarize_args(args: Any, limit: int = 300) -> Any:
    if isinstance(args, dict):
        return {k: (v if not isinstance(v, str) or len(v) <= limit else v[:limit] + "…")
                for k, v in args.items()}
    return args


class Gateway:
    """Order of checks: health -> client identification -> auth (strict
    bucket on failures) -> rate limit (per-client + global) -> audit
    tools/call bodies -> strip secret prefix -> forward."""

    def __init__(self, app, secret: str):
        self.app = app
        self.prefix = f"/{secret}"
        self.clients: dict[str, TokenBucket] = {}
        self.global_bucket = TokenBucket(RATE_PER_MINUTE * 2, RATE_BURST * 2)
        self.auth_fail: dict[str, TokenBucket] = {}

    def _client_ip(self, scope) -> str:
        for name, value in scope.get("headers", []):
            if name == b"cf-connecting-ip":
                return value.decode("latin-1")
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _bucket(self, store: dict, key: str, per_minute: float, burst: float) -> TokenBucket:
        if key not in store:
            if len(store) > 1000:
                store.clear()  # crude pruning; keys are IPs, state is advisory
            store[key] = TokenBucket(per_minute, burst)
        return store[key]

    @staticmethod
    async def _respond(send, status: int, body: bytes):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/health":
            return await self._respond(send, 200, b"ok")

        ip = self._client_ip(scope)

        # Auth: wrong path = failed credential attempt. Strict bucket: burst
        # 5, refill 30/hour per IP — then we stop even answering 404s.
        if not path.startswith(self.prefix + "/") and path != self.prefix:
            fb = self._bucket(self.auth_fail, ip, per_minute=0.5, burst=5)
            if not fb.allow():
                audit_write({"event": "auth_failure_throttled", "client_ip": ip, "path": path[:64]})
                return await self._respond(send, 429, b"too many requests")
            audit_write({"event": "auth_failure", "client_ip": ip, "path": path[:64]})
            return await self._respond(send, 404, b"not found")

        # Rate limits (authenticated traffic)
        cb = self._bucket(self.clients, ip, RATE_PER_MINUTE, RATE_BURST)
        if not cb.allow() or not self.global_bucket.allow():
            audit_write({"event": "rate_limited", "client_ip": ip})
            return await self._respond(send, 429, b"rate limited")

        # Audit: buffer POST body, parse JSON-RPC, log tools/call
        new_receive = receive
        tool_entry = None
        if scope.get("method") == "POST":
            chunks, more, total = [], True, 0
            while more:
                msg = await receive()
                chunks.append(msg)
                total += len(msg.get("body", b""))
                more = msg.get("more_body", False)
                if total > 2_000_000:
                    break
            body = b"".join(m.get("body", b"") for m in chunks)
            try:
                rpc = json.loads(body)
                if isinstance(rpc, dict) and rpc.get("method") == "tools/call":
                    params = rpc.get("params", {})
                    args = params.get("arguments", {}) or {}
                    tool_entry = {
                        "event": "tool_call",
                        "tool": params.get("name"),
                        "args": _summarize_args(args),
                        "client_ip": ip,
                        "user": "claude",
                        "destructive_confirmed": bool(args.get("confirm")) or None,
                    }
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            queue = list(chunks)

            async def replay():
                if queue:
                    return queue.pop(0)
                return await receive()

            new_receive = replay

        # Capture response status for the audit entry
        status_holder = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        scope = dict(scope)
        scope["path"] = path[len(self.prefix):] or "/"
        if scope.get("raw_path"):
            raw, pref = scope["raw_path"], self.prefix.encode()
            if raw.startswith(pref):
                scope["raw_path"] = raw[len(pref):] or b"/"
        try:
            await self.app(scope, new_receive, send_wrapper)
        finally:
            if tool_entry is not None:
                tool_entry["http_status"] = status_holder.get("status")
                audit_write(tool_entry)


def main() -> None:
    if not HA_TOKEN:
        sys.exit("FATAL: no SUPERVISOR_TOKEN/HA_TOKEN available — cannot reach Home Assistant.")
    if not ACCESS_SECRET or len(ACCESS_SECRET) < 24:
        sys.exit(
            "FATAL: access_secret is unset or too short (min 24 chars). "
            "Set it in the add-on configuration — generate one with: openssl rand -hex 24"
        )
    print(f"HA MCP Admin starting on :{PORT}  (HA at {HA_URL})", flush=True)
    print(f"MCP endpoint path: /{ACCESS_SECRET[:4]}.../mcp", flush=True)
    print(f"Destructive-service gate: {sorted(DESTRUCTIVE_SERVICES)}", flush=True)
    app = Gateway(mcp.streamable_http_app(), ACCESS_SECRET)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
