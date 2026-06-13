# HA MCP Admin

A full-access MCP (Model Context Protocol) server for Home Assistant, packaged as a
Home Assistant OS add-on. It gives Claude (via a claude.ai custom connector) admin-plane
capabilities the built-in HA MCP integration doesn't expose:

| Category | Tools |
|---|---|
| Debugging | `get_history`, `get_logbook`, `get_error_log`, `list_automation_traces`, `get_automation_trace`, `render_template` |
| Automations | `list_automations`, `get_automation_config`, `upsert_automation`, `delete_automation`, `reload_domain`, `check_config` |
| State | `get_entity`, `search_entities` |
| Registries | `list_areas`, `list_devices`, `list_entity_registry`, `list_services` |
| Escape hatch | `call_service` (any domain/service, with a destructive-services confirm gate) |

It runs against the Supervisor's internal HA proxy, so **no long-lived access token is
needed** — auth to HA is handled by the injected `SUPERVISOR_TOKEN`.

---

## Security model (read this first)

- The MCP endpoint is only reachable at `/<access_secret>/mcp`. Everything else 404s.
  The secret is effectively a bearer credential embedded in the URL, because claude.ai
  custom connectors don't send custom headers for non-OAuth servers. **Minimum 24 chars;
  the server refuses to start otherwise.** Generate one: `openssl rand -hex 24`
- Services that physically open/unlock things (configurable list) require an explicit
  `confirm=true` parameter on top of the secret.
- Expose it through a **Cloudflare Tunnel** — never port-forward 8099. Add a Cloudflare
  WAF rate-limiting rule on the hostname as a brute-force backstop.
- Rotate the secret by changing it in the add-on config and updating the connector URL.

## Install

### 1. Put this repo on GitHub

```bash
git init && git add -A && git commit -m "HA MCP Admin add-on"
git remote add origin git@github.com:YOUR_GITHUB_USERNAME/hass-mcp-admin.git
git push -u origin main
```

Then replace `YOUR_GITHUB_USERNAME` in `repository.yaml` and
`hass_mcp_admin/config.yaml` with your actual username (and push again).

### 2. Install the add-on

1. HA → **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/YOUR_GITHUB_USERNAME/hass-mcp-admin`
3. Refresh, open **HA MCP Admin**, click **Install** (it builds locally, ~1–2 min)
4. **Configuration** tab → set `access_secret` to your generated value → **Save**
5. **Start** the add-on. Check the **Log** tab — you should see
   `HA MCP Admin starting on :8099`.

### 3. Expose it with Cloudflare Tunnel

Using the community **Cloudflared** add-on (recommended if you already use it or have a
domain on Cloudflare):

1. Install the Cloudflared add-on and authenticate it to your Cloudflare account.
2. In its configuration, add an additional host:

```yaml
additional_hosts:
  - hostname: ha-mcp.yourdomain.com
    service: http://homeassistant:8099
```

> If `homeassistant` doesn't resolve from the cloudflared container, use your HA host's
> LAN IP instead (e.g. `http://192.168.1.x:8099`). Port 8099 is published on the host
> by this add-on.

3. Restart Cloudflared, then verify: `https://ha-mcp.yourdomain.com/health` → `ok`,
   and `https://ha-mcp.yourdomain.com/anything-else` → 404.

### 4. Connect claude.ai

1. claude.ai → **Settings → Connectors → Add custom connector**
2. URL: `https://ha-mcp.yourdomain.com/<access_secret>/mcp`
3. No OAuth — it should connect directly and show ~19 tools.

For current claude.ai connector documentation, see https://support.claude.com.

## Standalone / Docker use (non-HAOS installs)

The same image runs anywhere. Provide env vars instead of the supervisor proxy:

```bash
docker build -t ha-mcp-admin ./hass_mcp_admin
docker run -d -p 8099:8099 \
  -e HA_URL=http://YOUR_HA_HOST:8123 \
  -e HA_TOKEN=YOUR_LONG_LIVED_ACCESS_TOKEN \
  -e ACCESS_SECRET=$(openssl rand -hex 24) \
  ha-mcp-admin
```

## Notes & limitations

- `upsert_automation` writes via HA's automation config API — automations created in
  YAML packages (not the UI store) aren't editable through it.
- Tool output is capped at 60k characters; narrow queries (filters, `limit`, shorter
  history windows) rather than fighting truncation.
- Traces only exist for runs since the last HA restart (HA keeps the last 5 per
  automation by default — raise with `stored_traces` if needed).

## Timeout architecture

Per-request timeout to HA is 60s (`aiohttp.ClientTimeout`), deliberately under
Cloudflare's ~100s origin-response limit, so tool calls fail cleanly origin-side
rather than as opaque edge 524s. `get_history` is single-entity and capped at 168h
per call. If you ever raise the aiohttp timeout past ~90s, also tune cloudflared's
`originRequest.connectTimeout`/`keepAliveTimeout` for the hostname.

## Future refactor candidates

- **Persistent WebSocket connection**: `ws_command` currently opens one connection
  per command (~10–20ms overhead via the supervisor proxy, self-healing across HA
  restarts). If bulk registry operations are added, refactor to a lazy singleton
  with an asyncio lock, monotonic message IDs, and reconnect-on-failure.

## Rate limiting & audit logging

**Rate limiting** (token buckets, configurable in add-on options):
- Per-client: `rate_limit_per_minute` (default 120) sustained, `rate_limit_burst`
  (default 25) burst. Client identity = `CF-Connecting-IP` when present (the tunnel
  is the intended sole ingress), else peer IP.
- Global bucket at 2x the per-client limits — protects HA Core from any runaway loop.
- Failed-auth attempts (wrong path) per IP: burst 5, refill 30/hour, then 429s. This
  is the origin-side brute-force backstop under your Cloudflare WAF rule.

**Audit log**: JSONL at `/data/audit.jsonl` (rotates at 5MB to `.1`). Events:
`tool_call` (tool, summarized args, client IP, HTTP status, destructive_confirmed
flag), `auth_failure`, `auth_failure_throttled`, `rate_limited`. Logged at the
transport layer by parsing JSON-RPC `tools/call` bodies, so coverage is uniform
across all tools. Limitation: in-tool refusals (e.g. the destructive gate) return
HTTP 200 — the log records the attempt and whether `confirm` was set, not the gate
outcome. Review from chat via the `get_audit_log` tool.
