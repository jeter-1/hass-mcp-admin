# HAMCP-231 Thread/Matter topology and health — source investigation

Status: **investigation only (2.6.0 design wave, docs only), corrected after
the Codex design review of 2026-11-08 (§13), with Josh's decisions of
2026-11-08 recorded (§12, §14).** Josh decided the first-build read subset
(A1 + A2), the activity policy, the identifier policy and that nothing beyond
Core is in 2.6 (Josh decision (in-session 2026-11-08), items 16-20). No build is
authorized by this document: build start still needs the design delta review
to settle and a build task that names the protected paths. This document does not authorize registry signing, a new
provider, deployment, live Home Assistant or Engineering access, or a release.
All evidence comes from public upstream source at exact tags and commits, plus
repository source at the base. Nothing here was observed on a live system.

- Register: HAMCP-231 (audit HAAC-E009). It is related to HAMCP-224 and
  HAMCP-234 (journald add-on logs), which were read only for this document.
- Base: `origin/release/2.6.0` at `7246f2d21693f039504542574569f03b71476489`
  (Engineering `2.4.2-beta.7`). There is one new file and no runtime,
  protected-path or version change.
- Authority model:
  [ADR-021](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md). Upstream
  citations use exact commits or tags. Line numbers are at those revisions.
- Prepared 2026-11-07; corrected 2026-11-08, and Josh decisions recorded
  2026-11-08 (US Central).

## 1. Register outcome and binding decisions

Register outcome (HAMCP-231, quoted):

> Engineering exposes bounded, read-only Thread/Matter topology and health
> (where Home Assistant/OTBR/Matter Server expose it) so Matter/Thread outages
> can be attributed from inside the gateway.

**Josh direction (register 2026-11-07; confirmed in-session 2026-11-08).**

1. HAMCP-231 is delivered in **2.6.0**.
2. Profiles are **additive only**. No existing signed profile is version-bumped
   or replaced, and every existing fingerprint stays byte-identical.
3. New 2.6 profiles are signed for **Core 2026.11.0 only**. There are no
   2026.10.x targets for new profiles.
4. All new 2.6 profiles go into **one combined 2.6 signing**: one
   `extend-capabilities` extension of the existing reviewed Core 2026.11.0
   entry. There are no separate or phased 2.6 signings.
5. The work is read-only, and investigation comes first. Any new provider
   boundary (for example the Matter Server app) or new signed profile comes
   back to Josh before build. (This part of the 2026-11-07 row text stands.)

Consequences for this document:

1. The surface is read-only. This document proposes no write, service call,
   reload or physical action. It does disclose the upstream LAN and device
   reads that some proposed reads cause (§5.1.3).
2. The new signed Core profile (§7) is approved for the build contract with
   reads A1 + A2 only (Josh decision (in-session 2026-11-08), item 16). No new
   provider boundary (§5.2) is in 2.6 (item 19). Signing remains Josh's
   action in the one combined 2.6 signing.

**Superseded (historical).** The row's Next Action on 2026-11-07 read: "Josh
decided 2026-11-07 (design-complete scoping): IN 3.0, investigation first:
which Core WebSocket commands, OTBR endpoints and Matter Server APIs expose
border routers, datasets and next-hop/path data; any new provider boundary
(e.g. Matter Server add-on) or new signed profile comes back to Josh before
build. Read-only." Its "IN 3.0" release placement is superseded by item 1
above. The earlier draft's 3.0 versus 2.6.0 question (old Q1) is withdrawn.

Audit context (row Notes): HAAC-E009 reports that the `thread_network` health
section returns `section_unavailable`. The row Notes also record that HAMCP-224
journald reads expose Matter Server logs (audit E017).

## 2. Pinned versions and evidence

| Component | Exact revision used | How it was pinned |
|---|---|---|
| HA Core (newest signed) | `2026.11.0` at `74cf5a49c63ed8d4d87717844bf14f79c28c9cea` | `origin/registry` tip `e486028e` (sequence 17, entry `ha-core-2026.11.0-reviewed-v1`) |
| HA Core (base journal tip) | `2026.10.3` at `1f214b8461dd1fbbd66e10765c6905f49d3c1d86` | `upstream-trust/ha-core-release-registry.json` at the base (sequence 15) |
| `python-otbr-api` (Core `otbr` and `thread` requirement) | `3.1.0` at `9b796dbe2fab6b3e49a0a0fef140f8da7748cf54` | Core 2026.11.0 `otbr/manifest.json`, `thread/manifest.json` |
| `matter-python-client` (Core `matter` requirement) | `1.5.5`, wheel sha256 `5ebda957afa52b581937def2cfbab53532d10b9b8ddbe7d239d4129e8637d15b` | Core 2026.11.0 `matter/manifest.json` (Core 2026.10.3 pins `1.4.0`) |
| OpenThread Border Router app | `3.3.0`, `home-assistant/addons` at `ea53886db56564feedd4a8500b8762127a9975b3` | latest release commit; `openthread_border_router/build.yaml` stable `OTBR_VERSION` |
| `ot-br-posix` (REST API) | `fd872ab9d4afdfaf034b3b1154fed08b8b6d1e43` | `OTBR_VERSION` in that `build.yaml`; Dockerfile builds `-DOTBR_REST=ON` |
| Matter Server app | `9.3.0`, `home-assistant/addons` at `8d8141b14d44030f87033e21cafc49ead7ad316e`; `home-assistant/app-matter-server` tag `9.3.0` at `15ce0f75a050fa4f5bbb1f14fdc57f9521e13275` | app Dockerfile `BUILD_FROM=ghcr.io/matter-js/matterjs-server:1.5.5@sha256:ee149c7c67eff0e90f131e8f0d628f7b099591ec58473c8cda1209e8ae4df66e` |
| `matterjs-server` | `v1.5.5` at `a7922e85f4b94a5db94a1dbee1a02c685c2ec71d` (WebSocket schema 14) | the base image above |
| ha-mcp (reviewed current) | `8.6.0` at `fc54437a804858732e4bc927add98e202d879a09` | `hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_8_6_0.json` (`reviewed_source_commit`) |

- **Deployed versions are unknown.** Without live access this investigation
  cannot know which OTBR app version, Matter Server app version,
  `matterjs-server` schema, or Core version is installed. The repository
  records Matter Server app `9.2.0` (`matterjs-server` 1.4.0) as the version
  seen in HAMCP-234 evidence
  (`hass_mcp_engineering_beta/ha_mcp_engineering/incident_logs/container_levels.py:17-24`).
  That record is historical, not current runtime truth.
- **Core 2026.10.3 versus 2026.11.0.** `thread/websocket_api.py` and
  `matter/api.py` are byte-identical between the two tags.
  `otbr/websocket_api.py` differs by one import path (`from .types import
  OTBRConfigEntry`). So every Core command in §4 exists, with the same schema,
  on both the signed 2026.10.x and 2026.11.0 lines. Only the Matter client
  library differs (1.4.0 versus 1.5.5). This comparison is background only.
  It does **not** widen the signing target, which is Core 2026.11.0 only
  (§1).

Abbreviations used below:

- `CT` = Core 2026.11.0 `homeassistant/components/thread/`
- `CO` = Core `otbr/`
- `CM` = Core `matter/`
- `PO` = `python_otbr_api/__init__.py` 3.1.0
- `OB` = ot-br-posix `src/rest/openapi.yaml`
- `MJ` = matterjs-server `docs/websockets_api.md`
- `MM` = matterjs-server `packages/ws-client/src/models/model.ts`
- `MC` = `matter_server/` in matter-python-client 1.5.5 (the same bytes as
  matterjs-server `python_client/matter_server/` at `a7922e85`)
- `NT` = matterjs-server
  `packages/ws-controller/src/controller/NetworkTopologyService.ts` at
  `a7922e85`
- `WH` = matterjs-server
  `packages/ws-controller/src/server/WebSocketControllerHandler.ts` at
  `a7922e85`
- `MX` = matterjs-server
  `packages/ws-controller/src/controller/MatterController.ts` at `a7922e85`
- `TS` = ha-mcp 8.6.0 `src/ha_mcp/tools/tools_system.py`
- `TR` = ha-mcp 8.6.0 `tools_registry.py`

## 3. Current state at the base

### 3.1 What exists

- **Delegated `ha_get_system_health`, `thread_network` section.** ha-mcp 8.6.0
  sends one command, `otbr/info` (TS:1478-1524). It keeps `extended_address`,
  `channel`, `extended_pan_id` and `border_agent_id` for each router. On
  `success=false` it returns `{"error": "Thread/OTBR integration not
  available: <message>"}`. Its own docstring says that per-node Thread health
  is not exposed (TS:1484-1486).
- **Engineering projection.**
  - The section name is in the allowed set
    (`providers/upstream_platform_reads.py:43-44`).
  - `border_routers` is capped at 16 (`:127-128`).
  - The positive allowlist is `_BORDER_ROUTER` (`:440-443`), and the
    projection is at `:531-540`.
  - Any upstream `error` string becomes `{"error": "section_unavailable"}`,
    and the upstream message is dropped (`:497-502`).
- **The HAMCP-129 design** names `otbr/info` as the source and excludes
  `active_dataset_tlvs` and `url`
  ([HAMCP-129 design](HAMCP_129_PLATFORM_READS_DESIGN.md) lines 103-104 and
  272). Finding F14 (line 695) records that there is no per-node Thread or
  Matter health.
- **`matter_network` section.** This is presence only: `config_entry_id`,
  `state` and `title` (TS:1527-1560; projection `_MATTER` at
  `upstream_platform_reads.py:444` and `:541-542`).
- **Delegated `ha_get_device` for Matter devices.** ha-mcp 8.6.0 calls
  `matter/node_diagnostics` for any device whose `integration_type ==
  "matter"` (TR:500-530, call site TR:587-590). It returns `network_type`,
  `node_type`, `available`, `network_name`, `ip_addresses`, `mac_address`,
  `active_fabrics` and `active_fabric_index`.
  - No Engineering source, test or doc at the base mentions
    `node_diagnostics`; a repository-wide grep finds no hits outside
    generated JSON.
  - This investigation therefore does not establish how the read gateway
    projects that sub-object. See §8.
  - **Upstream device read (source-derived, not observed).** Core
    (`CM api.py:232-251`) forwards the command to `MC client.py:558-672`
    `node_diagnostics`. That method calls `get_matter_fabrics`
    (`:471-485`), which, for an **available** node, calls `refresh_attribute`
    on `OperationalCredentials.Fabrics`: a Matter read interaction with the
    device. It also calls `get_node_ip_addresses(prefer_cache=True)`
    (`:524-538`). Whether the server resolves addresses on the LAN when its
    cache is empty was not traced, so that is **unknown**. The existing
    delegated `ha_get_device` path can therefore already cause one device
    read per Matter device. This document does not change that path.
- **Journald logs (HAMCP-224, extended by HAMCP-234).** `get_incident_logs`
  reads named add-on journals, including `addon:core_matter_server`, and parses
  matterjs-server log levels (`incident_logs/container_levels.py:17-24`). These
  are text events (commissioning, subscription loss, MeshCoP and REST
  diagnostic failures). They contain no structured topology.
- **Existing repairs and entity reads can already show some Thread evidence.**
  - Core OTBR raises the repairs `otbr_zha_channel_collision` and
    `insecure_thread_network` (CO `util.py`).
  - Core Matter defines diagnostic sensors that are disabled by default:
    `thread_channel`, `thread_routing_role`, `thread_network_name`,
    `wifi_rssi`, `reboot_count` and `uptime` (CM `sensor.py:1805-1905`; role
    map at `:150-157`). Once enabled, the existing governed availability
    operation makes them readable through ordinary state reads.

### 3.2 What is missing

- There is no signed Core profile for any `thread/*`, `otbr/*` or `matter/*`
  command. The 32 capabilities in registry sequence 17 and
  `ha_core_readmission/profiles.py` (`CORE_CAPABILITY_PROFILES` at `:156`,
  runtime set at `:891`) contain none.
- There are no border routers that are not run by HA's OTBR (Apple, Google,
  vendor hubs). No dataset inventory. No mesh, neighbour, next-hop or path
  data.

### 3.3 HAAC-E009 cause: not determinable from source

`otbr/info` (CO `websocket_api.py:44-91`) fails in three ways:

1. `not_loaded` when no OTBR config entry is loaded. Example: a home whose
   only border routers are Apple or Google devices.
2. `otbr_info_failed` when a REST call to the OTBR app fails.
3. Core's admin check, because the command is `require_admin`.

ha-mcp turns each of these into an error string. Engineering then drops the
string (`upstream_platform_reads.py:497-502`). The reason behind HAAC-E009 is
therefore not visible through Engineering, and it cannot be inferred from
source. Bounded reason codes for the delegated section are a **separate
item**, not HAMCP-231 scope (Josh decision (in-session 2026-11-08), item 20; §12).

## 4. Source inventory

Column meanings:

- **Authority** is the Core permission check (`require_admin` or none).
- **Reach** says whether Engineering can reach the source through its existing
  authenticated Core WebSocket origin (`core-ws`). The alternative is a
  direct connection to an add-on on the Supervisor network, which would be a
  **new provider boundary**.
- **BR / DS / Path** says whether the source returns border routers, datasets,
  or next-hop/path data.

### 4.1 Core `thread` WebSocket (CT `websocket_api.py`)

| Command (line) | Authority | Reach | Fields | Secrets to drop | Bound | BR / DS / Path |
|---|---|---|---|---|---|---|
| `thread/list_datasets` (:152-182) | admin | core-ws | `dataset_id`, `created`, `source`, `network_name`, `extended_pan_id`, `pan_id`, `channel`, `preferred`, `preferred_border_agent_id`, `preferred_extended_address` | none in the response (no TLV is returned); `preferred_border_agent_id` and `preferred_extended_address` are BR identifiers (§5.1.2) | one row per stored dataset; upstream has no cap; ~250 B per row | DS yes; BR only as the preferred BR identifiers; Path no |
| `thread/get_dataset_tlv` (:130-150) | admin | core-ws | full active dataset TLV hex | **the whole response**: it contains the network key and PSKc | — | **forbidden** |
| `thread/discover_routers` (:185-238) | admin | core-ws, but as a **subscription** | streamed `router_discovered` and `router_removed` events. `data` is `ThreadRouterDiscoveryData` (CT `discovery.py:50-64`): `instance_name`, `addresses`, `border_agent_id`, `brand`, `extended_address`, `extended_pan_id`, `model_name`, `network_name`, `server`, `thread_version`, `unconfigured`, `vendor_name` | none secret; `addresses` and `server` are LAN identifiers | unbounded stream until unsubscribe | BR **yes, including routers not run by HA**; DS no; Path no |
| `thread/add_dataset_tlv`, `set_preferred_border_agent`, `set_preferred_dataset`, `delete_dataset` (:26-128) | admin | core-ws | — | — | — | **writes: forbidden** |

How `discover_routers` behaves: `async_start` adds a service listener for
`_meshcop._udp.local.` on HA's shared zeroconf instance (CT
`discovery.py:253-259`), and stops only on unsubscribe. The listener reports
both cached and newly resolved records. Starting a browser sends mDNS queries,
so this is an active, though low-cost, LAN operation, not a pure cache read.
Core has a passive cache reader, `async_read_zeroconf_cache`
(`discovery.py:121-152`), but only config-entry diagnostics use it
(`diagnostics.py:176`). No WebSocket command exposes it.

### 4.2 Core `otbr` WebSocket (CO `websocket_api.py`) and the OTBR REST calls Core makes

| Command (line) | Authority | Reach | Fields | Secrets to drop | Bound | BR / DS / Path |
|---|---|---|---|---|---|---|
| `otbr/info` (:44-91) | admin | core-ws | a mapping from each HA-configured OTBR's `extended_address` to `active_dataset_tlvs`, `border_agent_id`, `channel`, `extended_address`, `extended_pan_id`, `url` | **`active_dataset_tlvs`** (hex, contains the network key and PSKc); `url` (internal host and port) | one entry per HA OTBR config entry (usually 1); ~0.5 KB each | BR yes, **only for HA-run OTBRs**; DS identifiers only; Path no |
| `otbr/create_network` (:138), `otbr/set_network` (:204), `otbr/set_channel` (:265) | admin | core-ws | — | — | — | **writes (one is a factory reset): forbidden** |

To answer `otbr/info`, Core calls these OTBR REST endpoints through
`python-otbr-api`:

- `GET /node/ba-id` (PO:299-316)
- `GET /node/dataset/active` (PO:330-375)
- `GET /node/ext-address` (PO:582-600)
- the key-format probe `GET /api/actions` (PO:249-268)

`python-otbr-api` 3.1.0 has **no** method for `/api/node`, `/api/diagnostics`
or the per-field `/node/*` reads (rloc16, state, leader-data,
num-of-router). Core therefore cannot surface OTBR mesh diagnostics.

**Important:** even with an Engineering-side projection, the raw `otbr/info`
WebSocket response carries the active dataset TLV, which includes the
network key, into the Engineering process before the projection runs.
That is one reason A3 is not in the first build (§12, item 16).

### 4.3 OTBR REST, called directly (ot-br-posix `fd872ab9`; new provider boundary)

| Endpoint (OB line) | Authority | Reach | Fields | Secrets | Bound | BR / DS / Path |
|---|---|---|---|---|---|---|
| `GET /api/node` (:81-182) | **none** (`security: []`, OB:30) | direct, on the app's internal IP | `rloc16`, `extAddress`, `mlEidIid`, `omrIpv6Address[]`, `hostname`, `eui`, `role`, `mode`, `baId`, `routerCount`, `rlocAddress`, `networkName`, `routerId`, `leaderData{partitionId, weighting, dataVersion, stableDataVersion, leaderRouterId}`, `extPanId` | none secret; IPv6, EUI-64 and hostname are identifiers | one object | BR yes (this BR); partition and leader yes; Path no |
| `GET /node/dataset/active` (:1442-1495) | none | direct | `ActiveDataset`: `networkKey`, `pskc`, `meshLocalPrefix`, … (OB:2262-2312) | **`networkKey`, `pskc`** | — | **forbidden** |
| `GET /api/diagnostics` (:909-1160) | none | direct | the results of earlier `getNetworkDiagnosticTask` actions | — | a collection with a server-defined limit | Path: yes, but only after a POST |
| `POST /api/actions` with `getNetworkDiagnosticTask` (OB:1858-1912) | none | direct | requests TLVs including `route` (Route64), `childTable`, `routerNeighbors`, `leaderData`, `ipv6Addresses`, `macCounters`, `eui`, … | `ipv6Addresses`, `eui` are identifiers | — | **a POST that causes mesh radio traffic and stores state on the OTBR** |
| `DELETE /node`, `PUT /node/dataset/active`, `PUT /node/state`, `POST /node/ba-epskc/key`, … | none | direct | — | — | — | **factory reset, writes, ephemeral-key issuance: forbidden** |

Reach: the OTBR app is `host_network: true`. With no user-set port, its REST
server listens on the app's own Supervisor-network address on port 8081
(`openthread_border_router/rootfs/etc/s6-overlay/s6-rc.d/otbr-agent/run:114-142`
at `ea53886d`). The Engineering container therefore has a plausible network
path to it, but this is **not verified without live access**. The API has
no authentication. One port serves reads, writes and a factory reset.

### 4.4 Core `matter` WebSocket (CM `api.py`)

| Command (line) | Authority | Reach | Fields | Secrets to drop | Bound | BR / DS / Path |
|---|---|---|---|---|---|---|
| `matter/network_topology` (:384-404) | admin | core-ws | `NetworkTopology{collected_at, nodes[], connections[]}`. Each node is annotated with `ha_device_id` (`_serialize_topology`, :365-381). Node fields (MC `common/models.py:307-380`): `id`, `kind` (`matter`, `border_router`, `thread_unknown`, `wifi_ap`), `network_type`, `node_id`, `role`, `available`, `is_bridge`, `ext_address`, `rloc16`, `ext_pan_id`, `network_name`, `ssid`, `bssid`, `host_name`, `vendor_name`, `model_name`, `last_seen`. Connection fields: `source`, `target`, `network`, `strength`, `source_to_target` and `target_to_source` (each `{strength, lqi, rssi}`), `via_route_table`, `path_cost` | no credential. LAN identifiers appear in **aliases as well as named fields**: `ssid`, `bssid`, `host_name` and `ext_address`; the graph `id` and the connection `source`/`target` (which embed BSSIDs and extended addresses, see below); and `network_name` on `wifi_ap` nodes, which upstream sets to the BSSID (§5.1.2) | upstream has no cap; ~0.3 KB per node and ~0.25 KB per link | **BR partly**: only border routers that are Thread neighbours of a commissioned node with a retained edge, **not** a full inventory of discovered BRs; DS: `ext_pan_id`/`network_name` only; **Path partly**: `via_route_table`, `path_cost` and per-direction LQI/RSSI per link, not an end-to-end forwarding path |
| `matter/subscribe_network_topology` (:407-466) | admin | core-ws subscription | the same snapshots, streamed | as above | unbounded stream | as above; **excluded** (stream) |
| `matter/node_diagnostics` (:232-251) | **none** (not `require_admin`) | core-ws | `NodeDiagnostics` (MC `client/models/node.py:623`): `node_id`, `network_type`, `node_type`, `network_name`, `ip_adresses` (sic), `mac_address`, `available`, `active_fabrics`, `active_fabric_index` | IPs and MAC are identifiers. `network_name` is the Thread network name for Thread nodes, but for Wi-Fi (and `unknown` with the Wi-Fi feature) nodes it falls back to the **SSID** from `NetworkCommissioning` (MC `client.py:620-657`) | one node per call; **causes a device read** on an available node (§3.1, §5.1.3) | BR no; DS no; Path no (per-node reachability) |
| `matter/ping_node` (:254-273) | none | core-ws | a per-IP ping result map | — | one node per call | **active ICMP to the device; excluded (§12, item 17)** |
| `matter/commission`, `commission_on_network`, `set_thread`, `set_wifi_credentials`, `open_commissioning_window`, `remove_matter_fabric`, `interview_node` (:137-345) | admin | core-ws | — | `set_thread` carries a dataset; `set_wifi_credentials` carries a password | — | **writes and actions: forbidden** |

Topology semantics, from MJ:504-524 and matterjs-server
`docs/websocket-api-schema-changelog.md:199-217`:

- `refresh` defaults to `false`. Then the graph is built from the server's
  attribute cache (`NT:180-182` `getTopology` → `#build`), and the per-node
  table-refresh fan-out does not run.
- **`refresh=false` is not blanket passive.** The handler calls
  `startBorderRouterDiscovery()` before it looks at `refresh` (`WH:2220-2229`).
  That function starts border-router discovery, "which also probes every
  Border Router found for a REST API". Discovery normally starts with the
  controller, but with auto subscription disabled the first command that
  needs border routers starts it (`MX:478-490`). It does nothing when thread
  diagnostics are disabled or discovery is already running. Once started it
  keeps running in the background. Whether the deployed server has already
  started discovery is **unknown** without live access.
- `refresh: true` re-reads the neighbour and route tables from every
  available node ("seconds, real radio traffic — user-initiated only, never
  polled"; `NT:196-244`; also MC `client.py:417-437`).
- Links are derived from each commissioned node's own
  `ThreadNetworkDiagnostics` neighbour and route tables, and Wi-Fi station
  links from each station's BSSID (`NT:397-428`).
- **Graph ids embed LAN identifiers.** Commissioned nodes use the decimal
  node id. Externals use `br_<XA>` and `unknown_<EXTADDR>`, and Wi-Fi APs use
  `ap_<BSSID>` with colons stripped (`MM:274-276`; built at `NT:402`).
  Connection `source`/`target` carry the same ids. A `wifi_ap` node also
  copies the BSSID into `network_name` "for pre-`bssid` consumers"
  (`NT:402-415`).
- **External nodes appear only with a retained edge.** Neighbours that are
  not commissioned on HA's fabric appear as `thread_unknown`, and a
  neighbour that matches a discovered border router appears as
  `border_router`. Each is published only when one of its edges survives
  (`NT:377-395`). A discovered border router that is not a neighbour of any
  commissioned node does not appear at all.
- **`collected_at` is graph assembly time.** `#build` sets it to
  `Date.now()` on every build (`NT:305-306`). It says nothing about how old
  the cached neighbour, route or Wi-Fi attributes are. An old route table
  gets a new `collected_at` on every call. The only age-like node field is
  `last_seen`, on `border_router` externals, which comes from the BR's mDNS
  record and is not a link or route age.
- **`ha_device_id` is nullable.** Core sets `ha_device_id` to `None` when no
  device-registry match exists for a node (`CM api.py:364-381`). It is always
  `None` for `border_router`, `thread_unknown` and `wifi_ap` nodes, which
  have no `node_id`, and it can be `None` for a commissioned node whose
  registry entry is missing.
- Core returns `ERR_NOT_SUPPORTED` when the server's `schema_version` is
  below 13 (`TOPOLOGY_SCHEMA_VERSION`, CM `api.py:35`, check at :348-361).
  matterjs-server 1.4.0 (Matter Server app 9.2.0) shipped schema 13.
  Upstream 1.5.5 reports schema 14 (MJ:2365-2367).

### 4.5 Matter Server WebSocket, called directly (matterjs-server 1.5.5; new provider boundary)

| Command (MJ line) | Fields (MM) | Secrets | BR / DS / Path |
|---|---|---|---|
| `get_thread_border_routers` (:470-476) | discovered BR records, including the raw mDNS FQDN | none secret | BR yes |
| `get_thread_diagnostics` with optional `ext_pan_id` and `force` (:478-497) | `ThreadDiagnosticsBatch{extPanIdHex, networkName, collectedAt, source, nodes[], partialReason, expiresInMs}` (MM:199-230). `ThreadDiagnosticsNode` (MM:137-176): `extMacAddress`, `rloc16`, `mode`, `connectivity`, `route64`, `leaderData`, `networkData`, `ipv6Addresses`, `macCounters`, `childTable`, `eui64`, `version`, `vendor*`, `mleCounters`, `batteryLevel`, … | `ipv6Addresses`, `eui64` and `networkData` are identifiers; the server only collects with a stored `networkKey` and `pskc` (MeshCoP) or through OTBR REST | **full mesh: Route64 (router-to-router next hops and costs), child tables, leader and partition** |
| `get_network_topology` (:504-524) | as in §4.4, without `ha_device_id` | as in §4.4 | as in §4.4 |
| `read_attribute` | any attribute, including cluster 0x0035 `NeighborTable` and `RouteTable` (§4.6) | depends on the attribute | Path yes |
| commissioning, credentials, `remove_node`, ACL, OTA, camera stream, `set_thread_dataset`, … | — | credentials | **writes and actions** |

Reach and authority:

- The Matter Server app is `host_network: true`, and its external port 5580
  is disabled by default (`matter_server/config.yaml` at `8d8141b1`).
- With no user-set port, the app binds the WebSocket server to its own
  Supervisor-network address and `127.0.0.1` (app-matter-server
  `matter_server/rootfs/etc/s6-overlay/s6-rc.d/matter-server/run:122-126` at
  tag `9.3.0`).
- The server has **no authentication**. Upstream warns that the socket is
  reachable by any host that can connect and leaves access control to the
  operator (matterjs-server `docs/cli.md:61-67`).
- A direct client therefore holds the same full controller authority as Core:
  it can commission, remove nodes, set credentials and stream cameras.
- **Every form of `get_thread_diagnostics` can start work.** With no
  arguments, the handler calls `startBorderRouterDiscovery()` and then
  `threadDiagnostics.refreshAllKnown({force})` before it returns the cached
  batches (`WH:2194-2202`), so even the no-argument form starts a
  background refresh. With `ext_pan_id` it may start discovery and then
  `getOrFetch` (`WH:2203-2216`). A collection uses MeshCoP CoAP/DTLS or OTBR
  REST traffic over about 20 s (MJ:451-468).

### 4.6 Thread Network Diagnostics cluster (0x0035)

- **Cluster definition.** matter-python-client 1.5.5
  `chip/clusters/cluster_defs/ThreadNetworkDiagnostics.py`.
- **Attributes:**
  - `Channel`, `RoutingRole`, `NetworkName`, `PanId`, `ExtendedPanId`,
    `MeshLocalPrefix`
  - `NeighborTable` (:452), `RouteTable` (:468)
  - `PartitionId` (:484), `Weighting`, `DataVersion`, `LeaderRouterId` (:548)
  - `ActiveTimestamp`, `SecurityPolicy`, `ChannelPage0Mask`,
    `OperationalDatasetComponents`, `ActiveNetworkFaultsList`
  - `ExtAddress` (:1348), `Rloc16` (:1364)
- **`NeighborTableStruct` (:212)** has `extAddress`, `age`, `rloc16`,
  `linkFrameCounter`, `mleFrameCounter`, `lqi`, `averageRssi`, `lastRssi`,
  `frameErrorRate`, `messageErrorRate`, `rxOnWhenIdle`, `fullThreadDevice`,
  `fullNetworkData` and `isChild`.
- **`RouteTableStruct` (:249)** has `extAddress`, `rloc16`, `routerId`,
  `nextHop`, `pathCost`, `LQIIn`, `LQIOut`, `age`, `allocated` and
  `linkEstablished`. This is the true next-hop and path-cost source.
- **How each attribute can be reached:**
  - Core 2026.11.0 has **no** generic Matter attribute-read WebSocket command.
  - `Channel`, `RoutingRole` and `NetworkName` are exposed as disabled-by-default
    sensor entities (§3.1).
  - `NeighborTable` and `RouteTable` reach Core only as derived topology
    links (`via_route_table`, `path_cost`) through `matter/network_topology`.
  - Raw table reads need the Matter Server's `read_attribute`, which is a new
    provider boundary.

### 4.7 Core diagnostics downloads (excluded by current policy)

- **Endpoints.** `diagnostics/list` and `diagnostics/get` (WebSocket, admin;
  Core `diagnostics/__init__.py:123-150`) and
  `GET /api/diagnostics/{d_type}/{d_id}` (REST, admin; `:247-254`).
- **Thread config-entry diagnostics** (CT `diagnostics.py`, no redaction):
  - uses `pyroute2` on the HA host to read the IPv6 routes that have a
    gateway, and the neighbour cache (lladdr, state, probes) (`:78-121`);
  - joins these to the zeroconf border routers (`:176`);
  - reports host-to-BR next hops, mesh-local and OMR prefixes, and "unexpected
    routers".
- **OTBR diagnostics** (CO `diagnostics.py`) returns channel, channel mask,
  security policy and coprocessor version. It omits keys on purpose.
- **Matter diagnostics** (CM `diagnostics.py`) dumps server info and every node's
  attributes, redacting only `wifi_ssid` and `BasicInformation.Location`.
- HAMCP-129 excludes all diagnostics downloads because they run integration
  code and their output is unbounded and unredacted
  ([HAMCP-129 design](HAMCP_129_PLATFORM_READS_DESIGN.md) line 89).

### 4.8 ha-mcp 8.6.0

- **`ha_get_system_health`** covers `thread_network` through `otbr/info`
  (§3.1) and `matter_network` (presence only).
- **`ha_get_device`** adds `matter/node_diagnostics` for Matter devices.
- **No topology, dataset list or border-router discovery tool** exists. A
  grep for `otbr`, `thread` and `matter` in TS and TR finds only the call
  sites above.
- The reviewed upstream bump to 9.1.0 (HAMCP-319, #385 on release/2.5.0) is
  not landed. Its Thread or Matter changes, if any, were not reviewed here.

### 4.9 Overlap with HAMCP-224 and HAMCP-234

The two sources are complementary, not overlapping:

- **Journald** gives the narrative: Matter Server errors, subscription and
  CASE failures, and OTBR app logs if requested by name.
- **This investigation's sources** give the structure: which border routers
  exist, which network each node is on, its role, and link quality and path
  cost.

An outage attribution needs both. Journald already covers the "why did
the server log fail" half.

## 5. Recommended bounded read surface

### 5.1 (a) Feasible through Core WebSocket under a new signed read profile

This is a single, additive Engineering-native read profile.

- Proposed `capability_id` (dotted): `core.thread_matter_topology_read`.
- Proposed `profile_id` (underscore, versioned):
  `core_thread_matter_topology_read_v1`. The existing `_contract` builder
  derives it as `capability_id.replace(".", "_") + "_v1"`
  (`ha_core_readmission/profiles.py:119`, inside `_contract` at `:109`).
- Both names are proposals for the build contract. The profile itself is
  approved with A1 + A2 only (Josh decision (in-session 2026-11-08), item 16).

It sends only the exact commands below, with fixed arguments built by
Engineering. No caller-supplied argument reaches Core. (A4's registry-validated
`device_id` would have been the only one; A4 is not in the first build.)

| # | Exact Core message | Projection kept (under the default identifier policy, §5.1.2) | Dropped or rewritten | Proposed cap |
|---|---|---|---|---|
| A1 | `{"type": "thread/list_datasets"}` | `dataset_id`, `network_name` (Thread network name), `extended_pan_id`, `pan_id`, `channel`, `preferred`, `source`, `created`; `preferred_border_agent_id` and `preferred_extended_address` **rewritten to per-response opaque references** | nothing secret is present; reject the whole response if any key matching `tlv`, `network_key` or `pskc` appears | 16 datasets |
| A2 | `{"type": "matter/network_topology", "refresh": false}` (the literal `false`; the caller cannot set it) | nodes: `id` (**rewritten**), `kind`, `network_type`, `role`, `available`, `is_bridge`, `rloc16`, `ext_pan_id`, `network_name` (**only** when `network_type == "thread"` and `kind != "wifi_ap"`), `vendor_name`, `model_name`, `node_id`, `ha_device_id` (nullable), `last_seen` (labelled as BR mDNS last-seen). connections: `source` and `target` (**rewritten** consistently with node ids), `network`, `strength`, `source_to_target{strength, lqi, rssi}`, `target_to_source{…}`, `via_route_table`, `path_cost`. top level: `collected_at`, relabelled `graph_assembled_at` (§5.1.4) | `ssid`, `bssid`, `host_name`, `ext_address`; every upstream graph id; `network_name` on Wi-Fi, Ethernet, unknown and `wifi_ap` nodes | 200 nodes, 800 connections, with truncation flags and totals as in HAMCP-129 |
| A3 (**not in the first build**, item 16) | `{"type": "otbr/info"}` | for each router: `channel`, `extended_pan_id`; `extended_address` and `border_agent_id` **rewritten to opaque references** | **`active_dataset_tlvs`, `url`**, and any unknown key | 16 routers |
| A4 (**not in the first build**, item 16) | `{"type": "matter/node_diagnostics", "device_id": <registry-validated id>}` | `network_type`, `node_type`, `available`, `network_name` (**only** when `network_type == "thread"`), `active_fabrics` (count only) | `ip_adresses`, `mac_address`, `active_fabric_index`, every fabric descriptor field; `network_name` for any non-Thread network type (it can be the Wi-Fi SSID) | 1 device per call |

#### 5.1.1 What A1-A4 deliver, and what they do not

- **Border routers (partial).** A3 would have listed HA-run OTBRs; it is not
  in the first build (§12, item 16), so every BR, HA-run or not (Apple,
  Google, vendor), appears in A2 **only** when they are a Thread neighbour of a
  commissioned node and keep a graph edge (§4.4). A2 is **not** a complete
  inventory of discovered border routers. An isolated BR is invisible to
  A1-A4, and the output must say so (`coverage.border_routers:
  "neighbours_of_commissioned_nodes_only"`).
- **Datasets.** Non-secret identities come from A1.
- **Links (not paths).** A2 connections give `via_route_table`, `path_cost`
  and per-direction LQI and RSSI for each link that a commissioned node
  reports. This is per-link evidence. It is **not** an end-to-end forwarding
  path, and the tool must not present a route from a node to a BR as proven
  unless every hop is a reported link.
- **Health.** A2 `available` and `role`. (A4 per-node reachability is not in
  the first build.)
- **HA identity (nullable).** `ha_device_id` is `null` for every
  `border_router`, `thread_unknown` and `wifi_ap` node, and for a commissioned
  node with no registry match (§4.4). The tool keeps it `null`, with
  `identity: "not_an_ha_device"` or `identity: "ha_registry_match_missing"`;
  it never infers or fabricates a device. Wi-Fi APs and external
  neighbours are not HA devices.

#### 5.1.2 Identifier policy (default: drop LAN identifiers; applies to every alias and every section)

The privacy boundary is the **identifier value**, not the field name. One
LAN identifier can reach the output through several fields and sections:

| Identifier class | Where it appears upstream |
|---|---|
| Thread extended address (EUI-64-like MAC) | A2 `ext_address`; A2 node `id` and connection `source`/`target` (`br_<XA>`, `unknown_<EXTADDR>`); A1 `preferred_extended_address`; A3 map keys and `extended_address` |
| Wi-Fi BSSID | A2 `bssid`; A2 `id`, `source`/`target` (`ap_<BSSID>`, colons stripped); A2 `network_name` on `wifi_ap` nodes |
| Wi-Fi SSID | A2 `ssid`; A4 `network_name` for Wi-Fi nodes (and `unknown` nodes with the Wi-Fi feature) |
| Border agent id | A1 `preferred_border_agent_id`; A3 `border_agent_id` |
| IP address, MAC address, hostname | A4 `ip_adresses`, `mac_address`; A2 `host_name`; A3 `url` |
| Fabric descriptors | A4 `active_fabrics` entries, `active_fabric_index` |

Kept by default, as network-level rather than device- or radio-level
identifiers: `ext_pan_id`/`extended_pan_id`, `pan_id`, `channel`, the Thread
network name, `rloc16`, Matter `node_id` and `ha_device_id`. (The existing
delegated `thread_network` section already returns `extended_address` and
`border_agent_id` at the base. This proposal does not change that section.
Aligning it is a separate item after 2.6, HAMCP-341; Josh decision
(in-session 2026-11-08), §12.)

Default rules, which apply to A1-A4 together:

1. **Opaque per-response graph ids.** Engineering assigns each upstream A2
   node, in upstream order and before truncation, an id `g1`, `g2`, …. Every
   connection `source` and `target` is rewritten through the same map. A
   connection whose endpoint is not an emitted node is dropped and counted
   (`connections_dropped_unresolved`); it is never emitted with a raw
   endpoint.
2. **One alias table across sections.** Engineering normalizes each
   identifier value (uppercase hex, separators removed) and keeps one table
   per response. A BR whose extended address appears in A2, A1
   (`preferred_extended_address`) and, in a later change that adds it, A3
   gets the **same** opaque reference
   everywhere, so "this OTBR is graph node `g7` and the preferred BR of
   dataset D" still resolves. A value with no graph node gets a fresh `x<k>`
   reference. Border agent ids use their own `b<k>` references. (Linking a
   `b<k>` to a graph id needed A3, which is not in the first build, so in
   the first build a `b<k>` stands alone.)
3. **No derived bytes.** References are counters, not hashes. Hashed MAC
   and EUI-64 values can be brute-forced over the vendor-prefix space.
   References are not stable across calls; stable handles are `node_id` and
   `ha_device_id` only.
4. **Network names by network type.** `network_name` is kept only for Thread
   (A2 `network_type == "thread"` and `kind != "wifi_ap"`; A4
   `network_type == "thread"`). Otherwise it is withheld as
   `network_name_withheld: true`.
5. **Everywhere, not only in results.** The same rewrite applies before any
   value reaches tool output, audit records, logs, error text or the typed
   unavailable reasons. Upstream error messages are never echoed (§5.1.5).
   Audit records counts and opaque ids only.
6. **Positive allowlist.** A field not listed in the A1-A4 table is never
   emitted. That includes unknown future fields.

This default is the decided policy: opaque per-response ids for every LAN
identifier class (Josh decision (in-session 2026-11-08), item 18). No class is
returned raw. The A3 and A4 rows above stay as the record for a later,
separately reviewed change; they are not first-build aliases.

#### 5.1.3 Upstream activity behind each read (source-derived; disclosed, not observed)

"Read-only" is not the same as "no upstream traffic". The proposed reads
fall into three classes:

| Class | Meaning | Proposed reads in the class |
|---|---|---|
| **C: cached graph construction / Core state** | builds output from data Core or the Matter Server already holds | A1 (Core dataset store); A2's graph build from the attribute cache (no per-node table refresh, because `refresh` is fixed `false`) |
| **L: LAN discovery and probing** | mDNS browsing, or HTTP to a LAN or host service; no Matter device read | **A2 may start it.** The Matter Server's handler calls `startBorderRouterDiscovery()` even with `refresh=false` (`WH:2220-2229`). If discovery has not already started (auto subscription disabled), this first call starts mDNS BR discovery plus REST probing of every BR found, and it then keeps running in the background (`MX:478-490`). **A3** (not in the first build) would make local REST GETs from Core to HA's OTBR app (§4.2). Radio effects of those OTBR GETs were not traced into ot-br-posix and are unknown. |
| **R: radio / device reads** | a Matter read interaction with a device | **A4** (not in the first build). For an available node, `get_matter_fabrics` refreshes `OperationalCredentials.Fabrics` on the device (`MC client.py:471-485`). Whether `get_node_ip_addresses(prefer_cache=True)` resolves on the LAN when uncached is unknown. |

Excluded active operations (not in (a)): `refresh: true` (per-node table
reads on every available node, class R), `thread/discover_routers`
(mDNS browse, class L), `matter/ping_node` (ICMP to the device, which crosses the Thread radio for Thread nodes, class R), and every direct
`get_thread_diagnostics` form (B1; even the no-argument form starts
discovery and a background refresh, §4.5).

The tool output carries an `upstream_activity` list per section, with
values `cached_graph` and `may_start_br_discovery` in the first build
(`otbr_rest_get` and `device_read` belonged to A3 and A4, which are not in
it), so a caller sees the class of each read. A2's possible discovery start
is **accepted with this disclosure**, and none of `refresh: true`,
`thread/discover_routers` or `matter/ping_node` is included
(Josh decision (in-session 2026-11-08), item 17). The first build has no
class R read.

#### 5.1.4 Freshness

- Upstream `collected_at` is **graph assembly time** (`Date.now()` at each
  build, `NT:305-306`). The tool renames it `graph_assembled_at`.
- The age of the underlying neighbour, route and Wi-Fi attributes is not
  exposed by `matter/network_topology`. The tool reports
  `observation_age: "unknown"` for A2 links and nodes.
- BR `last_seen` is passed through as `border_router_mdns_last_seen`. It is
  not a link or route age.
- The tool and its documentation must not conclude that an outage or a route
  change is happening now from `graph_assembled_at` alone.

#### 5.1.5 Typed unavailable reasons

Each source maps to a typed unavailable reason, not a generic error. A
failure in one section does not hide the others.

| Source condition | Reason |
|---|---|
| `otbr/info` returns `not_loaded` | `otbr_not_configured` (A3 only; not in the first build) |
| `otbr/info` returns `otbr_info_failed` | `otbr_unreachable` (A3 only; not in the first build) |
| `matter/network_topology` returns `ERR_NOT_SUPPORTED` | `matter_server_schema_below_13` |
| the Matter integration is not loaded | `matter_not_loaded` |
| an admin refusal | `core_authority_refused` |
| a malformed or unknown-shape payload | `malformed_upstream_response` |

Upstream error text is never echoed, because it can contain identifiers.

#### 5.1.6 Exclusions

Explicitly **not** in (a), each fail-closed by an exact allowlist:

- `thread/get_dataset_tlv`
- every `thread/*`, `otbr/*` and `matter/*` write or action
- `otbr/info` and `matter/node_diagnostics` (A3 and A4; not in the first
  build, item 16)
- `matter/network_topology` with `refresh: true` (item 17)
- `matter/subscribe_network_topology`
- `thread/discover_routers` (item 17)
- `matter/ping_node` (item 17)
- the diagnostics downloads (item 19)

### 5.2 (b) Needs a new provider boundary (not in 2.6: Josh decision (in-session 2026-11-08), item 19)

| Boundary | What it would add beyond (a) | Why it is a new boundary | Key risks |
|---|---|---|---|
| B1: direct Matter Server WebSocket (`get_thread_border_routers`, `get_thread_diagnostics`, `read_attribute` on 0x0035) | full-mesh Route64 router-to-router next hops, child tables, leader and partition data, MLE counters, raw neighbour and route tables | not reached through Core; a direct socket to an add-on on the Supervisor network | no authentication, and the same socket carries commissioning, node removal, credentials and camera commands, so Engineering would have to self-restrict a full-authority channel; collection triggers mesh traffic; the deployed schema is unknown |
| B2: direct OTBR REST (`GET /api/node`; optionally `/api/diagnostics`) | partition ID, leader router, router count and role for HA-run OTBRs; mesh diagnostic TLVs (only after a `POST /api/actions`) | not reached through Core; a direct HTTP connection to an add-on | no authentication, and the same port serves factory reset, dataset writes and ephemeral-key issuance; diagnostics need a POST that changes OTBR state and causes radio traffic; `/node/dataset/active` returns the network key |

Neither boundary is designed here, and neither is in 2.6 (item 19). Any later
proposal needs its own security architecture design. That design would cover the trust artifact,
an exact endpoint and command allowlist, negative reachability, address
pinning, and HAMCP-129-style projection. A Core signed profile cannot admit
either boundary.

### 5.3 (c) Not available

| Need | Why not |
|---|---|
| Mesh diagnostics (Route64, child tables) through Core | No Core 2026.11.0 command reaches OTBR `/api/diagnostics` or the Matter Server's `get_thread_diagnostics`. `python-otbr-api` 3.1.0 has no such call (§4.2). |
| Raw Matter attribute reads through Core | Core has no generic `read_attribute` WebSocket command (§4.6). |
| Routes inside other ecosystems (Apple or Google BR internals) | They are visible only where those devices are neighbours of HA-fabric nodes (`thread_unknown` or `border_router` nodes in A2). |
| Historical topology | Topology is a point-in-time snapshot. The Matter Server caches about 1 h of diagnostics but keeps no history. |
| Age of cached link and route data | `matter/network_topology` exposes only graph assembly time (§5.1.4). Observation age stays `unknown`. |
| A complete inventory of discovered border routers through Core | A2 shows only BRs that are neighbours of commissioned nodes (§4.4). `thread/discover_routers` is an active stream and is excluded (item 17). |

| Host-to-BR IPv6 route and neighbour data | Only Thread config-entry diagnostics provide it (§4.7). That path is excluded by the HAMCP-129 diagnostics policy; no exception in 2.6 (item 19). |
| The cause of HAAC-E009 | The upstream message is dropped at `upstream_platform_reads.py:497-502` (§3.3). Reason codes are a separate item (item 20). |
| Deployed app versions and schema | Not knowable without live access. They must be established by an authorized read-only observation, not by this document. |

## 6. Public schema and tool-registration impact

This investigation PR has **no impact**: it adds one document only.

**Proposed tool shape (for the build contract; not a question).** With (a)
approved as A1 + A2 (Josh decision (in-session 2026-11-08), item 16), the build
adds one Engineering-native read tool:

- Name: `get_thread_matter_topology`.
- Input (closed, `additionalProperties: false`):
  - `sections`: optional array of the enum `datasets` (A1) and `topology`
    (A2). Default: both. No other input exists. In particular, there is no
    `refresh` input and no `device_id` input (A3 `border_routers` and A4
    `node` are not in the first build).
- Output: one object per requested section, each with `status` (`ok` or
  the typed reason of §5.1.5), `items` (or `nodes` and `connections`),
  `total`, `truncated` and `upstream_activity` (§5.1.3). `topology` also has
  `graph_assembled_at`, `observation_age: "unknown"`,
  `connections_dropped_unresolved` and `coverage`. The top level has
  `identifier_policy: "opaque_per_response_v1"` (the decided policy, item 18).
- Count: the static tool count rises by one (81 → 82 at this document's
  base; connector total 109 → 110). The build recomputes both counts against
  the actual 2.6.0 base, because other 2.6 work also adds tools.

The surfaces it would touch are all security-sensitive:

- public input and output schemas
- tool registration
- Core profile selection
- redaction
- audit

The delegated `ha_get_system_health` `thread_network` section stays as it is.
Richer reason codes for it are a separate item (item 20).

## 7. Signing impact

Signing follows the Josh direction in §1: additive profile, **Core 2026.11.0
only**, **one combined 2.6 signing**.

| `capability_id` / `profile_id` | New or changed | Core commands / contract added | Why | Fingerprint impact | Signing |
|---|---|---|---|---|---|
| `core.thread_matter_topology_read` / `core_thread_matter_topology_read_v1` (proposed) | **New, additive** | `thread/list_datasets`; `matter/network_topology` with `refresh=false` fixed. Nothing else: `otbr/info` and `matter/node_diagnostics` are not in the first build (Josh decision (in-session 2026-11-08), item 16) and are in the contract's `never_sent`. The contract also carries the projection allowlists, the identifier policy and alias rewrite (§5.1.2), the activity classes (§5.1.3), caps, typed unavailable reasons, `never_sent` list and `principal_origin: existing_configured_authenticated_origin_only` (the `integration_inspection/contracts.py:78-89` pattern). All of this is fixed before the evidence and fingerprint freeze. | the only way Engineering-native code may issue these admin-only commands; the outcome needs border-router, dataset and link data that no existing profile covers | a new capability fingerprint over the exact command list, response contract and `never_sent`; **no existing profile's fingerprint changes** | a capability reference appended to the existing reviewed Core 2026.11.0 entry (`ha-core-2026.11.0-reviewed-v1`, `74cf5a49`) in the **single combined 2.6 `extend-capabilities` signing**, together with every other approved 2.6 profile. No other Core version. No separate or phased signing. |
| `core.basic_websocket_read` and all other existing profiles | Unchanged | — | (a) uses a dedicated profile and does not widen the generic read | none (byte-identical) | existing references preserved exactly, in order |

How the existing journal tool constrains this (`scripts/prepare_core_release_registry.py`
at the base):

- An `extend-capabilities` candidate must change **exactly one existing
  entry**, keep its release and probe identity, and **append** to the exact
  original capability references: `new_refs[:len(old_refs)] == old_refs`,
  with more references than before (`:184-191`). Every other entry must be
  unchanged (`:165-166`; the validator re-runs these checks at signing, `:130-197`).
- There is therefore **no per-capability subtraction**. A later candidate
  that drops this capability from the 2026.11.0 entry is rejected.
- `revoke` withdraws a **whole release entry** and adds a denial
  (`:152-162`, `:226`), and a revoked release cannot be re-added (`:168-170`, `:217`).
- HAMCP-290 is not assumed to be an unlanded batching dependency. The
  combined signing uses the actual reviewed 2.6.0 release plan and the
  signed predecessor current at preparation time.

The boundaries in (b) have **no** Core profile. The signed Core registry
does not cover add-on endpoints. B1 or B2 would need a new kind of trust
artifact; neither is in 2.6 (item 19).

## 8. Negative reachability and refusal (for a future build)

A build of (a) must prove each of the following:

1. **Exact allowlist.** The profile can emit only A1 and A2 (item 16),
   byte-for-byte.
   A table test feeds every other `thread/*`, `otbr/*` and `matter/*` command
   name found in the pinned Core source (§4) and expects refusal before
   dispatch with no WebSocket send. Those commands are:
   - `get_dataset_tlv`
   - `otbr/info` and `node_diagnostics` (A3 and A4, item 16)
   - `discover_routers`
   - `subscribe_network_topology`
   - `ping_node`
   - every write and action
2. **`refresh` is pinned.** The caller cannot set `refresh`. A test sends
   `refresh: true` through every input path and expects refusal or
   no effect. The dispatched message is asserted to be the literal `false`.
3. **Secret canaries.** Fixtures add these canaries to the A1 and A2
   responses:
   - `networkKey` and `pskc` keys
   - TLV-shaped hex in unexpected fields

   The test asserts that none of them appears in tool output, audit records,
   logs or error text. Any unknown key fails closed. It also asserts that
   `otbr/info` (whose response carries `active_dataset_tlvs`) and
   `thread/get_dataset_tlv` are never dispatched.
4. **Identifier canaries across aliases (§5.1.2).** Fixtures use synthetic
   canaries: a BSSID (`AA:BB:CC:DD:EE:FF`, which upstream also emits as
   `ap_AABBCCDDEEFF` and as a `wifi_ap` `network_name`), Thread extended
   addresses or EUI-64 values (in `ext_address`, `br_<XA>`,
   `unknown_<EXTADDR>` and A1 `preferred_extended_address`), a border agent
   id (A1 `preferred_border_agent_id`), a Wi-Fi SSID (A2 `ssid`), a
   hostname (A2 `host_name`), and an IP address and a MAC address placed in
   unknown fields. The test
   searches tool output, audit records, logs and error text for each canary
   in upper case, lower case, with and without separators, and expects no
   match. In the same run, every emitted connection `source` and `target`
   resolves to an emitted node id, the edge count equals the upstream edge
   count minus `connections_dropped_unresolved`, and a BR seen in A1 and A2
   carries one shared opaque reference. No identifier class is returned raw
   (item 18).
5. **Network name by type.** An A2 Wi-Fi station node and an A2 `wifi_ap`
   node whose `network_name` carries a BSSID-like value both return
   `network_name_withheld: true`. A Thread node keeps its network name.
6. **Activity (§5.1.3).** Against the exact-version Matter Server
   (matterjs-server `a7922e85`, or the version the build pins) in a
   disposable lane, with synthetic nodes:
   - first A2 call with auto subscription disabled: record whether BR
     discovery and REST probing start, and assert that no per-node table
     read is sent;
   - the same call with thread diagnostics disabled: assert no discovery
     start;
   - across a full tool call: assert that no device read is sent (no
     `matter/node_diagnostics`, no `ping_node`, no `refresh: true`, no
     `discover_routers`; the first build has no class R read, item 17);
   - the `topology` section reports `upstream_activity` with
     `may_start_br_discovery` (accepted with disclosure, item 17) and the
     `datasets` section reports `cached_graph`.

   Each test asserts and reports the actual sends. None of them requires or
   authorizes running against a household system.
7. **Freshness (§5.1.4).** With unchanged, old cached attributes, two graph
   builds some time apart give an advancing `graph_assembled_at` while
   `observation_age` stays `"unknown"`, and no output field claims the links
   are current.
8. **Bounds.** A response one item over each cap is truncated with
   `truncated=true` and accurate totals. Oversized strings are capped.
9. **Typed failure.** `ERR_NOT_SUPPORTED`, the Matter integration not
   loaded, an admin refusal and a malformed payload each map to their typed
   reason. (The `otbr/info` reasons apply only to A3, which is not in the
   first build.)
   A failure in one source does not hide the other sources (partial result,
   no fallback).
10. **No fallback and nothing beyond Core.** An unavailable profile or
    registry refuses the tool. It never falls back to ha-mcp, direct REST,
    or an add-on address, and no tool path opens a connection to the Matter
    Server or OTBR app directly (B1 and B2 are not in 2.6, item 19).
11. **`node_diagnostics` passthrough.** Before or with the build, add a
    regression test that pins how the delegated `ha_get_device` output treats
    ha-mcp's `node_diagnostics` sub-object (§3.1 gap).
12. **Delegated section unchanged (item 20).** The delegated
    `ha_get_system_health` `thread_network` section output is unchanged by
    the build (same fixture, before and after); reason codes are not added
    there by HAMCP-231.

## 9. Acceptance (for a future build)

- **Useful success.** Use a fixture home that contains:
  - an HA OTBR that is a Thread neighbour of a commissioned node and is the
    preferred BR of an A1 dataset;
  - a non-HA border router (for example an Apple BR) that is a neighbour of
    a commissioned node, so it appears in A2;
  - an **isolated discovered BR** that is not a neighbour of any
    commissioned node;
  - an **uncommissioned Thread neighbour** (`thread_unknown`);
  - a Wi-Fi station and its **Wi-Fi AP**;
  - a **commissioned device whose HA registry entry is missing**;
  - valid commissioned Thread links with `path_cost`, `via_route_table` and
    LQI.

  The tool returns non-secret dataset identities (A1), the HA OTBR and the
  neighbouring non-HA BR as A2 border-router nodes, the HA OTBR under one
  shared opaque reference with its A1 preferred-BR value, nodes with role and availability, and links with
  `path_cost`, `via_route_table`, LQI and RSSI whose endpoints all resolve.
  It also states its limits truthfully:
  - the isolated BR is absent, and `coverage.border_routers` says A2 lists
    only neighbours of commissioned nodes;
  - the AP and the uncommissioned neighbour have `ha_device_id: null` with
    `identity: "not_an_ha_device"`;
  - the commissioned device with no registry match has
    `ha_device_id: null` with `identity: "ha_registry_match_missing"`;
  - links are reported as links, not as a proven end-to-end path.

  With that output an operator can see which commissioned node reports a
  weak or missing link to which neighbour or BR. Unknown identity and
  coverage stay unknown; nothing is fabricated.
- **Privacy.** The canary test of §8 item 4 passes on the same fixture.
- **Refusal.** Every non-allowlisted command and `refresh: true` is refused
  before dispatch (§8).
- **Failure preservation.** A home with only Apple BRs still gets the A1
  and A2 data, with no OTBR-specific failure (A3 is not in the first build).
  A Matter Server
  below schema 13 gets `matter_server_schema_below_13`. Neither is a generic
  `section_unavailable`.
- **Verification.** This is a read only, so there is no readback.
  `graph_assembled_at` is graph assembly time, not data age, and
  `observation_age` is `"unknown"` (§5.1.4). The tool does not claim a
  current outage or route change from that timestamp.
- **Activity disclosure.** Each section reports its `upstream_activity`
  class, and §8 item 6 evidence matches it. The first-build output never
  reports a class R read.
- **Recovery.** A failed read is reported and never re-dispatched. Retry is
  the caller's choice.
- **Exact-Core evidence.** Profile admission follows the existing
  exact-Core lane and registry process for Core 2026.11.0. This
  investigation does not claim any of that evidence.

## 10. Security

- **Credential exposure.** Three sources return Thread credentials:
  - `thread/get_dataset_tlv`
  - the `active_dataset_tlvs` field of `otbr/info`
  - OTBR `GET /node/dataset/active`

  None of the three is called in the first build. `otbr/info` would put the
  credential into Engineering memory even when projected away; A3 is
  excluded (Josh decision (in-session 2026-11-08), item 16), so no
  credential-bearing response is read.
- **Authority.** Both first-build Core commands (A1, A2) are `require_admin`.
  They run under the existing configured authenticated origin, so the build
  adds no new credential. Whether that origin's principal is an admin at
  runtime is not established from source, and is one of the candidate causes
  of HAAC-E009.
- **No write or physical action, but not zero upstream activity**
  (§5.1.3):
  - `refresh=false` prevents the per-node table-refresh fan-out. It does not
    make A2 passive: the call may start Matter Server BR discovery and REST
    probing if discovery is not already running.
  - A4's device read (the Fabrics refresh) and A3's REST GETs to HA's OTBR
    app do not occur: neither read is in the first build.
  - `thread/discover_routers` (mDNS queries), `matter/ping_node` (ICMP) and
    `refresh=true` (mesh-wide reads) are active and are excluded (item 17).
    A2's possible discovery start is accepted with disclosure (item 17).
- **New provider boundaries.** B1 and B2 are unauthenticated, full-authority
  add-on endpoints. Adding either one is a security architecture change
  under root AGENTS.md: it needs explicit scope, negative tests and focused
  review. Neither is in 2.6 (item 19).
- **Identifiers.** IP addresses, MAC and EUI-64 addresses, extended
  addresses, border agent ids, hostnames, SSID and BSSID, and fabric
  descriptors describe the household network. They are not secrets. They
  also hide in graph ids, connection endpoints and `network_name` aliases.
  The decided policy drops them and rewrites ids to opaque per-response
  references (§5.1.2; item 18).

## 11. Build plan (conditional; nothing is approved)

1. J1-J5 are decided (Josh decision (in-session 2026-11-08), items 16-20; §12).
   Release placement (2.6.0), the additive rule, the Core 2026.11.0 target
   and the single combined signing were already settled (§1).
2. **PR 1, profile and contract.** Add the new profile and contract in
   `ha_core_readmission/profiles.py` and a contract module, with the tests in
   §8 and the exact-Core 2026.11.0 evidence. This touches a protected path,
   so the task must name it.
3. **Combined 2.6 signing.** One owner-signed `extend-capabilities` candidate
   appends this capability, with every other approved 2.6 profile, to the
   existing reviewed Core 2026.11.0 entry. Proving check: the candidate
   preserves every prior capability reference in order and changes only the
   2026.11.0 entry; a candidate that removes a reference is rejected
   (§7). Signing is Josh's action.
4. **PR 2, tool.** Add the native tool (§6), its schema and registration,
   the projection, alias rewrite and redaction, docs, and acceptance.
5. **Dependencies.**
   - Nothing here depends on unlanded 2.5.0 work.
   - If #385 (ha-mcp 9.1.0) changes the delegated `thread_network` section,
     2.5.0 wins and §3.1 is re-checked.
   - The combined signing uses the actual reviewed 2.6.0 release plan and
     signed predecessor at preparation time. HAMCP-290 is not assumed to be
     a dependency.
6. **Rollback.** There is **no per-capability registry removal** (§7), and
   rollback does not rely on a journal downgrade. The supported options are:
   - **Reviewed runtime release rollback (preferred).** Ship or roll back to
     a reviewed Engineering release whose runtime does not register the tool
     or carry this profile. The signed 2026.11.0 entry keeps the extra
     reference. Before relying on this, the build must verify registry
     compatibility: that runtime still admits every other 2026.11.0
     capability while the entry carries a reference it does not know, and
     the topology tool is absent or refuses. That behavior is **not
     established** by this investigation.
   - **Whole-entry revocation (separately authorized).** A `revoke` of the
     Core 2026.11.0 entry withdraws **every** capability on that release, not
     only this one, and the validator does not allow the revoked release to
     be re-added (§7). It is a wider, owner-authorized emergency action, not
     a feature rollback.

   Proving check: the selected rollback actually disables the topology tool
   (absent or refused, fail closed) while the other approved capabilities
   behave as the selected option says. No persisted state is added.

## 12. Decided choices (J1-J5 and the J3 sub-question)

Already settled before these decisions (§1): release 2.6.0; additive
profiles; Core 2026.11.0 only; one combined 2.6 signing. The public tool
shape is proposed concretely in §6 and is part of the build contract. J1-J5
are now decided, and so is the J3 sub-question (item 6):

1. **J1, initial read subset. Decided: A1 + A2.** Josh decision (in-session 2026-11-08),
   item 16. The new signed Core read profile is approved for the build
   contract with `thread/list_datasets` and `matter/network_topology`
   (`refresh` fixed `false`) only. A3 (`otbr/info`, which carries the active
   dataset TLV into Engineering) and A4 (`matter/node_diagnostics`, a device
   read) are not in the first build. Sections: status, §1, §4.2, §5.1
   (table, §5.1.2, §5.1.3, §5.1.5, §5.1.6), §6, §7, §8 items 1, 3-6, §9,
   §10.
2. **J2, active effects. Decided: accept A2's possible BR discovery start
   with disclosure; none of `refresh: true`, `thread/discover_routers` or
   `matter/ping_node`.** Josh decision (in-session 2026-11-08), item 17. Sections:
   §4.4 table, §5.1.3, §5.1.6, §5.3, §8 item 6, §9, §10.
3. **J3, LAN identifiers. Decided: (a) opaque per-response ids** for every
   identifier class and alias (§5.1.2). Josh decision (in-session 2026-11-08),
   item 18. Sections: §5.1.2, §6, §8 item 4, §10.
4. **J4, beyond Core. Decided: nothing beyond Core in 2.6.** No B1 Matter
   Server provider, no B2 OTBR REST provider, and no Thread diagnostics
   exception to the HAMCP-129 policy. Josh decision (in-session 2026-11-08),
   item 19. Sections: §5.1.6, §5.2, §5.3, §7, §8 items 6 and 10, §10.
5. **J5, HAAC-E009 richer reasons. Decided: a separate item.**
   Josh decision (in-session 2026-11-08), item 20. The delegated `thread_network`
   section is unchanged by HAMCP-231. The separate register item is
   HAMCP-340 (Backlog). Sections: status, §3.3, §5.3, §6, §8 item 12.

6. **J3 sub-question, delegated `thread_network` identifiers. Decided: a
   separate item after 2.6, HAMCP-341 (Backlog).** Josh decision (in-session
   2026-11-08). The question was whether the delegated `thread_network`
   section, which already returns `extended_address` and `border_agent_id`
   at the base, should be aligned with the opaque-id policy. HAMCP-231 does
   not change that section, and 2.6 ships it as it is at the base. Sections:
   §5.1.2, §12.

**Superseded (historical).** The previous revision (head `8917c19a`) offered
A1 + A2 + A3, A1 + A2 + A4 or all of A1-A4 (J1); withholding A2, an
owner-triggered refresh, or all three active operations (J2); returning
selected or all identifier classes (J3); B1, B2 or a diagnostics exception
(J4); and bringing reason codes into HAMCP-231 or leaving them out (J5).
None of these is a live choice. The A3 and A4 analysis in §4 and §5.1 stays
as the record for a possible later, separately reviewed additive change.

## 13. Codex design review 2026-11-08 — disposition

Review of head `6e4d05ed` against base `7246f2d2`. Upstream claims were
re-read at matterjs-server `a7922e85f4b94a5db94a1dbee1a02c685c2ec71d` and in
the verified Core 2026.11.0 archive (SHA-256
`85f3c42c…eec70ff1`, commit `74cf5a49`).

| # | Severity | Finding | Where and how it is corrected |
|---|---|---|---|
| 1 | Medium | Identifier projection leaked identifiers through graph ids, edge endpoints and `network_name` | §4.4 (aliases documented); §5.1 table (rewritten ids and endpoints, network name by type); new §5.1.2 (identifier classes across A1-A4, opaque per-response ids, one alias table, no hashes, applies to logs, audit and errors); §8 items 4-5 (canary and link-resolution tests); §10; J3 |
| 2 | Medium | `refresh=false`, A4 and direct diagnostics were described as passive | §3.1 (delegated `node_diagnostics` device read); §4.4 (`startBorderRouterDiscovery` on every topology call); §4.5 (no-argument `get_thread_diagnostics` starts a refresh); new §5.1.3 (classes C, L, R and `upstream_activity`); §8 item 6 (activity tests); §10; J1-J2 |
| 3 | Medium | `collected_at` presented as cache freshness | §4.4; new §5.1.4 (`graph_assembled_at`, `observation_age: "unknown"`); §5.3; §8 item 7; §9 Verification |
| 4 | Medium | Coverage overpromised BR inventory and HA identity | §4.4 (externals only with edges, nullable `ha_device_id`); new §5.1.1; §5.3; §9 Useful success (fixture with isolated BR, uncommissioned neighbour, Wi-Fi AP, missing registry match, valid links) |
| 5 | Medium | Signing and rollback conflicted with settled scope and the journal validator | §1 (Josh direction; old Q1 withdrawn, 3.0 superseded); §2 (2026.10.3 comparison does not widen the target); §7 (Core 2026.11.0 only, one combined `extend-capabilities`, validator constraints, no per-capability subtraction); §11 steps 3 and 6 (supported rollbacks and proving check) |
| 6 | Low | Profile and capability identifiers reversed | §5.1 and §7 (dotted `capability_id`, underscore versioned `profile_id`, cited to `profiles.py:119`) |

Proving tests named here are requirements for future build evidence. None of
them was run for this document.

## 14. Josh decisions 2026-11-08 — disposition

Josh answered J1-J5 in-session on 2026-11-08 (choices file items 16-20).
Each is recorded as "Josh decision (in-session 2026-11-08), item N".

| Item | Decision | Sections changed |
|---|---|---|
| 16 (J1) | A1 + A2 only; profile approved for the build contract | status; §1 consequences; §4.2 note; §5.1 table (A3, A4 marked not in the first build), §5.1.2 rule 2 (`b<k>` standalone), §5.1.5 (OTBR reasons A3-only), §5.1.6 (A3/A4 excluded); §6 (input `datasets`/`topology` only, no `device_id`); §7 (command list, A3/A4 in `never_sent`); §8 items 1, 3, 4, 5; §9 (fixture and failure preservation without A3); §10 (no credential-bearing response, authority) |
| 17 (J2) | Accept A2 discovery with disclosure; no refresh, discover or ping | §4.4 table (ping excluded); §5.1.3 (accepted with disclosure; no class R read); §5.1.6; §5.3; §8 item 6 (no device read; disclosure asserted); §9 activity disclosure; §10 |
| 18 (J3) | (a) opaque per-response ids | §5.1.2 (default is the decided policy; delegated-section alignment is HAMCP-341, after 2.6); §6 `identifier_policy`; §8 item 4 (no class returned raw); §10 |
| 19 (J4) | Nothing beyond Core in 2.6 | §5.1.6; §5.2 (heading and text); §5.3; §7; §8 items 6 and 10 (B1 case removed; no direct add-on connection); §10 |
| 20 (J5) | HAAC-E009 richer reasons is a separate item (HAMCP-340) | status; §3.3; §5.3; §6; §8 item 12 (delegated section unchanged) |
| J3 sub-question (asked after the list) | Aligning the delegated `thread_network` ids with the opaque policy is a separate item after 2.6, HAMCP-341 | §5.1.2; §12 (no longer pending) |

**Effect on the Codex corrections (§13).** Findings 3, 5 and 6 are
unchanged. Finding 1's identifier policy is now the decided policy; the A3
and A4 alias rows stay as record, but the canary test (§8 item 4) now covers
the A1 and A2 aliases only. Finding 2's activity classes stay, but the first
build has no class R read and no OTBR REST GET, so §8 item 6 asserts their
absence instead of recording A4 reads. Finding 4's coverage limits stay;
without A3, HA-run OTBRs appear only as A2 border-router neighbours, which
the existing `coverage.border_routers` statement already covers.
