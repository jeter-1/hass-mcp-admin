# HAMCP-288: ha-mcp app LAN listener (source-only finding)

Status: **source-only; LAN reachability UNVERIFIED.** No host, port or
endpoint was probed. Nothing here was observed on a running system, and no
secret path value was read. The finding comes from the pinned upstream source
listed below. That source was fetched as untrusted data and read only.

## Source pins

| Pin | Repository | Commit | Files read |
| --- | --- | --- | --- |
| 8.5.0 (Engineering's pin, `helper_value/contracts.py:27`) | `homeassistant-ai/ha-mcp` | `311d6dc273fb4e9a5b8cde0de15f69472a64fe44` | `homeassistant-addon/config.yaml`, `homeassistant-addon/start.py` |
| 9.1 (`docs/evidence/hamcp251/part-b2-source-comparison.json:44`) | `homeassistant-ai/ha-mcp` | `d2e3f4b0647d815fb32d530fed15a6a99b1d4b33` | the same two files |

At these commits, the `version:` field in the app manifest reads `8.4.3` and
`9.0.0` (`config.yaml:3`). The pins are identified by commit, not by that
field.

## What the source says

| Claim | 8.5.0 (`311d6dc`) | 9.1 (`d2e3f4b`) |
| --- | --- | --- |
| The app shares the host network namespace | `config.yaml:34` `host_network: true` | `config.yaml:39` |
| It publishes 9583/tcp | `config.yaml:94` | `config.yaml:93` |
| The listener binds every interface by default | `start.py:994` `MCP_HOST` defaults to `"0.0.0.0"` | `start.py:699` |
| The MCP server runs on port 9583 | `start.py:911` | `start.py:624` |
| The only credential is the path (generated as `"/private_" + secrets.token_urlsafe(16)`) | `start.py:68` | `start.py:68` |
| A custom path can be set | `config.yaml:74` `secret_path: str?` | `config.yaml:90` |
| The app has Supervisor `manager` role | `config.yaml:30` | `config.yaml:30` |
| The app prints the full private URL and the path to its own log at startup | `start.py:915`, `start.py:917` | `start.py:576`, `start.py:578` (`_log_server_url`, `start.py:572`) |
| Upstream warns that wrapped log lines split the URL inside the secret (#1918) | `start.py:42-44` | `start.py:42-44` |
| An IPv6 LAN guard middleware exists (allowed networks are configurable) | not present | `start.py:448-451`, option `ipv6_allowed_networks` (`config.yaml:55`, `config.yaml:78`) |
| The app announces itself through Supervisor discovery | not present | `config.yaml:34` `discovery:` |

Both pins also expose ingress on the same port (`config.yaml:22-24`).

## Interpretation

With host networking and a wildcard bind, the source implies that the app's
MCP endpoint listens on port 9583 of every host interface, including the
LAN. Anyone who can reach that port and knows the path can use the app's
Home Assistant authority. At 9.1, the IPv6 LAN guard limits IPv6 clients to
the configured networks. This finding does not establish what the guard
does for IPv4 clients.

The startup log line (`start.py:915`/`576`) is why the app's own log source
leaks the path: any tool that returns that log returns the credential. That
is HAMCP-288 item 1 (High). Engineering now withholds that source. See the PR.

What remains **UNVERIFIED** and would need a separately authorized live,
read-only check:

- whether port 9583 is reachable from the LAN on the deployed host (no firewall or
  network evidence was gathered);
- whether `MCP_HOST` is overridden in the deployed app;
- which ha-mcp version is deployed, and therefore which guard applies;
- what the 9.1 discovery payload contains, and who can read it.

## Not done

- No probe, port scan, request or log read against any host.
- No change to the ha-mcp app, its options or its network.
- No recommendation is made here to change `host_network` or the bind host. That
  is an upstream app decision for Josh.
