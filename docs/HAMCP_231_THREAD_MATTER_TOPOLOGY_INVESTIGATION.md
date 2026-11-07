# HAMCP-231 Thread/Matter topology and health — source investigation

Status: **investigation only (2.6.0 design wave, docs only).** No build is
authorized. Under the binding decision in §1, any new signed Core profile or any
new provider boundary needs Josh's decision before a build starts. This document
does not authorize registry signing, a new provider, deployment, live Home
Assistant or Engineering access, or a release. All evidence comes from public
upstream source at exact tags and commits, plus repository source at the base.
Nothing here was observed on a live system.

- Register: HAMCP-231 (audit HAAC-E009). It is related to HAMCP-224 and
  HAMCP-234 (journald add-on logs), which were read only for this document.
- Base: `origin/release/2.6.0` at `7246f2d21693f039504542574569f03b71476489`
  (Engineering `2.4.2-beta.7`). There is one new file and no runtime,
  protected-path or version change.
- Authority model:
  [ADR-021](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md). Upstream
  citations use exact commits or tags. Line numbers are at those revisions.
- Prepared 2026-11-07 (US Central).

## 1. Register outcome and binding decisions

Register outcome (HAMCP-231, quoted):

> Engineering exposes bounded, read-only Thread/Matter topology and health
> (where Home Assistant/OTBR/Matter Server expose it) so Matter/Thread outages
> can be attributed from inside the gateway.

**Binding Josh decision.** The task prompt quotes the row's Next Action as it
stood on 2026-11-07, before this task replaced that field with a progress note.
The final register update restores it.

> Josh decided 2026-11-07 (design-complete scoping): IN 3.0, investigation
> first: which Core WebSocket commands, OTBR endpoints and Matter Server APIs
> expose border routers, datasets and next-hop/path data; any new provider
> boundary (e.g. Matter Server add-on) or new signed profile comes back to Josh
> before build. Read-only.

Consequences for this document:

1. The surface is read-only. This document proposes no write, service call,
   reload or physical action.
2. Every new signed Core profile (§6) and every new provider boundary (§5.2)
   is a decision for Josh. Neither is approved here.
3. **Discrepancy.** The row says "IN 3.0" but this task was dispatched as
   2.6.0 design work. This document does not resolve the discrepancy; see Q1.

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
  library differs (1.4.0 versus 1.5.5).

Abbreviations used below:

- `CT` = Core 2026.11.0 `homeassistant/components/thread/`
- `CO` = Core `otbr/`
- `CM` = Core `matter/`
- `PO` = `python_otbr_api/__init__.py` 3.1.0
- `OB` = ot-br-posix `src/rest/openapi.yaml`
- `MJ` = matterjs-server `docs/websockets_api.md`
- `MM` = matterjs-server `packages/ws-client/src/models/model.ts`
- `MC` = `matter_server/` in matter-python-client 1.5.5
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
source. Whether a bounded reason code should be preserved is a scope question
(Q12).

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
| `thread/list_datasets` (:152-182) | admin | core-ws | `dataset_id`, `created`, `source`, `network_name`, `extended_pan_id`, `pan_id`, `channel`, `preferred`, `preferred_border_agent_id`, `preferred_extended_address` | none in the response (no TLV is returned) | one row per stored dataset; upstream has no cap; ~250 B per row | DS yes; BR only as the preferred BR identifiers; Path no |
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
network key, into the Engineering process before the projection runs
(see Q3).

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
| `matter/network_topology` (:384-404) | admin | core-ws | `NetworkTopology{collected_at, nodes[], connections[]}`. Each node is annotated with `ha_device_id` (`_serialize_topology`, :365-381). Node fields (MC `common/models.py:307-380`): `id`, `kind` (`matter`, `border_router`, `thread_unknown`, `wifi_ap`), `network_type`, `node_id`, `role`, `available`, `is_bridge`, `ext_address`, `rloc16`, `ext_pan_id`, `network_name`, `ssid`, `bssid`, `host_name`, `vendor_name`, `model_name`, `last_seen`. Connection fields: `source`, `target`, `network`, `strength`, `source_to_target` and `target_to_source` (each `{strength, lqi, rssi}`), `via_route_table`, `path_cost` | no credential; `ssid`, `bssid`, `host_name` and `ext_address` are identifiers (Q7) | upstream has no cap; ~0.3 KB per node and ~0.25 KB per link | **BR yes** (mDNS-discovered BRs, including those not run by HA); DS: `ext_pan_id`/`network_name` only; **Path yes** (`via_route_table`, `path_cost`, per-direction LQI/RSSI) |
| `matter/subscribe_network_topology` (:407-466) | admin | core-ws subscription | the same snapshots, streamed | as above | unbounded stream | as above; **excluded** (stream) |
| `matter/node_diagnostics` (:232-251) | **none** (not `require_admin`) | core-ws | `NodeDiagnostics` (MC `client/models/node.py:623`): `node_id`, `network_type`, `node_type`, `network_name`, `ip_adresses` (sic), `mac_address`, `available`, `active_fabrics`, `active_fabric_index` | IPs and MAC are identifiers (Q7) | one node per call | BR no; DS no; Path no (per-node reachability) |
| `matter/ping_node` (:254-273) | none | core-ws | a per-IP ping result map | — | one node per call | **active ICMP to the device (Q6)** |
| `matter/commission`, `commission_on_network`, `set_thread`, `set_wifi_credentials`, `open_commissioning_window`, `remove_matter_fabric`, `interview_node` (:137-345) | admin | core-ws | — | `set_thread` carries a dataset; `set_wifi_credentials` carries a password | — | **writes and actions: forbidden** |

Topology semantics, from MJ:504-524 and matterjs-server
`docs/websocket-api-schema-changelog.md:199-217`:

- `refresh` defaults to `false`, which builds the graph from the server's
  attribute cache.
- `refresh: true` re-reads the neighbour and route tables from every online
  node ("seconds, real radio traffic — user-initiated only, never polled";
  also MC `client.py:417-437`).
- Links are derived from each commissioned node's own
  `ThreadNetworkDiagnostics` neighbour and route tables, and border routers
  from passive mDNS discovery.
- Neighbours that are not commissioned on HA's fabric appear as
  `thread_unknown`.
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
- `get_thread_diagnostics` with `ext_pan_id` or `force` starts a collection.
  That collection uses MeshCoP CoAP/DTLS or OTBR REST traffic over about
  20 s (MJ:451-468).

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

This is a single Engineering-native read profile. Its proposed identifier is
`core.thread_matter_topology_read` (registry capability
`core_thread_matter_topology_read_v1`), and both names are pending Josh. It
sends only the exact commands below, with fixed arguments built by
Engineering. No caller-supplied argument reaches Core except an optional
`device_id` that has been validated against the device registry.

| # | Exact Core message | Projection kept | Dropped | Proposed cap |
|---|---|---|---|---|
| A1 | `{"type": "thread/list_datasets"}` | `dataset_id`, `network_name`, `extended_pan_id`, `pan_id`, `channel`, `preferred`, `source`, `created`, `preferred_border_agent_id`, `preferred_extended_address` | nothing secret is present; reject the whole response if any key matching `tlv`, `network_key` or `pskc` appears | 16 datasets |
| A2 | `{"type": "matter/network_topology", "refresh": false}` (the literal `false`; the caller cannot set it) | nodes: `id`, `kind`, `network_type`, `role`, `available`, `is_bridge`, `rloc16`, `ext_pan_id`, `network_name` (Thread only), `vendor_name`, `model_name`, `node_id`, `ha_device_id`, `last_seen`. connections: `source`, `target`, `network`, `strength`, `source_to_target{strength, lqi, rssi}`, `target_to_source{…}`, `via_route_table`, `path_cost`. top level: `collected_at` | `ssid`, `bssid`, `host_name`, `ext_address` (Q7) | 200 nodes, 800 connections, with truncation flags and totals as in HAMCP-129 |
| A3 (optional, Q3) | `{"type": "otbr/info"}` | for each router: `extended_address`, `border_agent_id`, `channel`, `extended_pan_id` | **`active_dataset_tlvs`, `url`**, and any unknown key | 16 routers |
| A4 (optional) | `{"type": "matter/node_diagnostics", "device_id": <registry-validated id>}` | `network_type`, `node_type`, `available`, `network_name`, `active_fabrics` (count only) | `ip_adresses`, `mac_address` (Q7) | 1 device per call |

What A1-A4 deliver against the outcome:

- **Border routers:** HA-run OTBRs come from A3. Apple, Google and vendor BRs
  come from the A2 `border_router` nodes.
- **Datasets:** non-secret identities come from A1.
- **Next-hop and path:** A2 connections give `via_route_table`, `path_cost`
  and per-direction LQI and RSSI between HA-fabric nodes and their
  neighbours.
- **Health:** A2 `available` and `role`, plus A4 per-node reachability.

Each source maps to a typed unavailable reason, not a generic error:

| Source condition | Reason |
|---|---|
| `otbr/info` returns `not_loaded` | `otbr_not_configured` |
| `otbr/info` returns `otbr_info_failed` | `otbr_unreachable` |
| `matter/network_topology` returns `ERR_NOT_SUPPORTED` | `matter_server_schema_below_13` |
| the Matter integration is not loaded | `matter_not_loaded` |
| an admin refusal | `core_authority_refused` |

Explicitly **not** in (a), each fail-closed by an exact allowlist:

- `thread/get_dataset_tlv`
- every `thread/*`, `otbr/*` and `matter/*` write or action
- `matter/network_topology` with `refresh: true` (Q5)
- `matter/subscribe_network_topology`
- `thread/discover_routers` (Q4)
- `matter/ping_node` (Q6)
- the diagnostics downloads (Q10)

### 5.2 (b) Needs a new provider boundary (decision for Josh)

| Boundary | What it would add beyond (a) | Why it is a new boundary | Key risks |
|---|---|---|---|
| B1: direct Matter Server WebSocket (`get_thread_border_routers`, `get_thread_diagnostics`, `read_attribute` on 0x0035) | full-mesh Route64 router-to-router next hops, child tables, leader and partition data, MLE counters, raw neighbour and route tables | not reached through Core; a direct socket to an add-on on the Supervisor network | no authentication, and the same socket carries commissioning, node removal, credentials and camera commands, so Engineering would have to self-restrict a full-authority channel; collection triggers mesh traffic; the deployed schema is unknown |
| B2: direct OTBR REST (`GET /api/node`; optionally `/api/diagnostics`) | partition ID, leader router, router count and role for HA-run OTBRs; mesh diagnostic TLVs (only after a `POST /api/actions`) | not reached through Core; a direct HTTP connection to an add-on | no authentication, and the same port serves factory reset, dataset writes and ephemeral-key issuance; diagnostics need a POST that changes OTBR state and causes radio traffic; `/node/dataset/active` returns the network key |

Neither boundary is designed here. If Josh wants either one, it needs its own
security architecture design. That design would cover the trust artifact,
an exact endpoint and command allowlist, negative reachability, address
pinning, and HAMCP-129-style projection. See Q8 and Q9.

### 5.3 (c) Not available

| Need | Why not |
|---|---|
| Mesh diagnostics (Route64, child tables) through Core | No Core 2026.11.0 command reaches OTBR `/api/diagnostics` or the Matter Server's `get_thread_diagnostics`. `python-otbr-api` 3.1.0 has no such call (§4.2). |
| Raw Matter attribute reads through Core | Core has no generic `read_attribute` WebSocket command (§4.6). |
| Routes inside other ecosystems (Apple or Google BR internals) | They are visible only where those devices are neighbours of HA-fabric nodes (`thread_unknown` or `border_router` nodes in A2). |
| Historical topology | Topology is a point-in-time snapshot. The Matter Server caches about 1 h of diagnostics but keeps no history. |
| Host-to-BR IPv6 route and neighbour data | Only Thread config-entry diagnostics provide it (§4.7). That path is excluded by the HAMCP-129 diagnostics policy (Q10). |
| The cause of HAAC-E009 | The upstream message is dropped at `upstream_platform_reads.py:497-502` (§3.3, Q12). |
| Deployed app versions and schema | Not knowable without live access. They must be established by an authorized read-only observation, not by this document. |

## 6. Public schema and tool-registration impact

This investigation PR has **no impact**: it adds one document only.

If Josh approves (a), a build would add a new Engineering-native read tool.
Its name and shape are pending Q11. One option is
`get_thread_matter_topology`, with a closed input of an optional
`sections` enum (`datasets`, `topology`, `border_routers`, `node`) and an
optional `device_id`. That would raise the static tool count by one (81 → 82
at the base, connector total 109 → 110). The surfaces it would touch are all
security-sensitive:

- public input and output schemas
- tool registration
- Core profile selection
- redaction
- audit

The delegated `ha_get_system_health` `thread_network` section would stay as it
is unless Q12 is decided.

## 7. Signing impact

| Profile id | New or changed (from version X) | Core commands / contract added | Why | Fingerprint impact | Can it batch with other 2.6 profiles |
|---|---|---|---|---|---|
| `core.thread_matter_topology_read` (capability `core_thread_matter_topology_read_v1`; name pending Josh) | **New**. Proposed for a new reviewed entry on Core `2026.11.0` (`74cf5a49`), and on any other supported Core version that is admitted; the command schemas are identical on 2026.10.3 | `thread/list_datasets`; `matter/network_topology` with `refresh=false` fixed; optional `otbr/info` (projected, Q3); optional `matter/node_diagnostics` (one validated `device_id`). The contract also carries the projection allowlists, caps, typed unavailable reasons, `never_sent` list and `principal_origin: existing_configured_authenticated_origin_only` (the `integration_inspection/contracts.py:78-89` pattern) | the only way Engineering-native code may issue these admin-only commands; the outcome needs border-router, dataset and path data that no existing profile covers | a new capability fingerprint over the exact command list, response contract and `never_sent`; the registry entry's capability set changes, so a new owner-signed journal sequence is needed; **no existing profile's fingerprint changes** | Yes. It is additive and independent of other profiles, so it can share one owner-signed journal append with other 2.6 additions (for example HAMCP-290's new contract) on the same Core entry. Approval is still per profile (Josh) |
| `core.basic_websocket_read` and all other existing profiles | Unchanged | — | (a) uses a dedicated profile and does not widen the generic read | none | — |

The boundaries in (b) have **no** Core profile. The signed Core registry
does not cover add-on endpoints. B1 or B2 would need a new kind of trust
artifact, which is part of Q8 and Q9.

## 8. Negative reachability and refusal (for a future build)

A build of (a) must prove each of the following:

1. **Exact allowlist.** The profile can emit only A1-A4, byte-for-byte.
   A table test feeds every other `thread/*`, `otbr/*` and `matter/*` command
   name found in the pinned Core source (§4) and expects refusal before
   dispatch with no WebSocket send. Those commands are:
   - `get_dataset_tlv`
   - `discover_routers`
   - `subscribe_network_topology`
   - `ping_node`
   - every write and action
2. **`refresh` is pinned.** The caller cannot set `refresh`. A test sends
   `refresh: true` through every input path and expects refusal or
   no effect. The dispatched message is asserted to be the literal `false`.
3. **Secret canaries.** Fixtures add these canaries:
   - `active_dataset_tlvs` (in the A3 response)
   - `networkKey` and `pskc` keys
   - TLV-shaped hex in unexpected fields

   The test asserts that none of them appears in tool output, audit records,
   logs or error text. Any unknown key fails closed.
4. **Identifier policy.** Whatever Q7 decides, it is enforced by a positive
   allowlist with a test.
5. **Bounds.** A response one item over each cap is truncated with
   `truncated=true` and accurate totals. Oversized strings are capped.
6. **Typed failure.** `not_loaded`, `otbr_info_failed`, `ERR_NOT_SUPPORTED`,
   an admin refusal and a malformed payload each map to their typed reason.
   A failure in one source does not hide the other sources (partial result,
   no fallback).
7. **No fallback.** An unavailable profile or registry refuses the tool. It
   never falls back to ha-mcp, direct REST, or an add-on address.
8. **`node_diagnostics` passthrough.** Before or with the build, add a
   regression test that pins how the delegated `ha_get_device` output treats
   ha-mcp's `node_diagnostics` sub-object (§3.1 gap).

## 9. Acceptance (for a future build)

- **Useful success.** Use a fixture home with an HA OTBR, an Apple BR, and
  Matter-over-Thread and Wi-Fi nodes. The tool returns:
  - both BRs (A3 and A2)
  - non-secret dataset identities (A1)
  - nodes with role and availability
  - links with `path_cost`, `via_route_table` and LQI

  Each node links back to its HA `device_id`, so an operator can say which
  node lost its route to which BR.
- **Refusal.** Every non-allowlisted command and `refresh: true` is refused
  before dispatch (§8).
- **Failure preservation.** A home with only Apple BRs gets
  `otbr_not_configured` together with the A1 and A2 data. A Matter Server
  below schema 13 gets `matter_server_schema_below_13`. Neither is a generic
  `section_unavailable`.
- **Verification.** This is a read only, so there is no readback.
  `collected_at` is surfaced so that stale cache data is visible.
- **Recovery.** A failed read is reported and never re-dispatched. Retry is
  the caller's choice.
- **Exact-Core evidence.** Profile admission follows the existing
  exact-Core lane and registry process. This investigation does not claim
  any of that evidence.

## 10. Security

- **Credential exposure.** Three sources return Thread credentials:
  - `thread/get_dataset_tlv`
  - the `active_dataset_tlvs` field of `otbr/info`
  - OTBR `GET /node/dataset/active`

  The first and third are never called. `otbr/info` puts the credential
  into Engineering memory even when it is projected away. Q3 decides whether
  to accept that, with first-parse dropping and never logging, or to exclude
  A3.
- **Authority.** Every proposed Core command except A4 is `require_admin`.
  They run under the existing configured authenticated origin, so the build
  adds no new credential. Whether that origin's principal is an admin at
  runtime is not established from source, and is one of the candidate causes
  of HAAC-E009.
- **No physical effect, with two caveats.**
  - `refresh=false` builds from cache, so it causes no radio traffic.
  - `thread/discover_routers` (mDNS queries), `matter/ping_node` (ICMP) and
    `refresh=true` (mesh-wide reads) are active and stay excluded unless
    Josh decides otherwise (Q4-Q6).
- **New provider boundaries.** B1 and B2 are unauthenticated, full-authority
  add-on endpoints. Adding either one is a security architecture change
  under root AGENTS.md: it needs explicit scope, negative tests and focused
  review.
- **Identifiers.** IP addresses, MAC and EUI-64 addresses, hostnames, SSID and
  BSSID, extended addresses and fabric IDs describe the household network.
  They are not secrets, but they are decision support for Josh (Q7).

## 11. Build plan (conditional; nothing is approved)

1. Josh answers Q1-Q12. Q1 sets the release (2.6.0 or 3.0).
2. **PR 1, profile and contract.** Add the new profile and contract in
   `ha_core_readmission/profiles.py` and a contract module, with the tests in
   §8 and the exact-Core evidence. This touches a protected path, so the
   task must name it.
3. **Owner-signed registry append** adds the capability to the reviewed Core
   entry or entries. It is Josh's action, and it can batch with other 2.6
   profiles (§7).
4. **PR 2, tool.** Add the native tool, its schema and registration, the
   projection and redaction, docs, and acceptance.
5. **Dependencies.**
   - Nothing here depends on unlanded 2.5.0 work.
   - If #385 (ha-mcp 9.1.0) changes the delegated `thread_network` section,
     2.5.0 wins and §3.1 is re-checked.
   - HAMCP-290 also adds a Core contract, which is a batching candidate.
6. **Rollback.** Remove the capability from the next signed entry, or roll
   back the release. Without the profile, the tool refuses (fail closed). No
   persisted state is added.

## 12. Open questions — pending Josh

1. **Q1, release.** The row says "IN 3.0", but this task was dispatched as
   2.6.0 design work. Which release owns HAMCP-231?
2. **Q2, profile.** Approve a new signed Core read profile for (a)? If yes,
   with which subset of A1-A4?
3. **Q3, `otbr/info`.** Accept a native call whose raw response carries the
   active dataset TLV (network key and PSKc) into Engineering before
   projection? The alternative is to exclude A3. BRs would then come from A2
   `border_router` nodes and the existing delegated section.
4. **Q4, `thread/discover_routers`.** Allow a bounded subscribe-collect-
   unsubscribe window? It is an active mDNS browse and a stream. Excluding it
   loses BR discovery when no Matter Server runs.
5. **Q5, topology refresh.** Is `refresh=true` (mesh-wide radio reads,
   "never polled" upstream) ever allowed, for example owner-initiated? Or is
   it permanently excluded?
6. **Q6, `matter/ping_node`.** Include an active per-node ICMP check, or not?
7. **Q7, identifiers.** Return or drop each of these: IP addresses,
   MAC/EUI-64 addresses, `ext_address`, `host_name`, SSID/BSSID, fabric IDs.
8. **Q8, Matter Server direct (B1).** Authorize design of a new provider
   boundary to an unauthenticated, full-authority socket for full-mesh
   diagnostics, or not?
9. **Q9, OTBR REST direct (B2).** The same question for OTBR REST.
10. **Q10, diagnostics.** Make a domain-scoped exception to the HAMCP-129
    diagnostics exclusion for Thread config-entry diagnostics (host-to-BR
    routes), or keep it excluded?
11. **Q11, tool shape.** Use a new native tool (+1 tool count), or sections
    on an existing native tool? What name?
12. **Q12, HAAC-E009 reason codes.** Should the delegated `thread_network`
    section keep a bounded reason code instead of `section_unavailable`? Is
    that in HAMCP-231 scope or a separate item? This is a possible scope
    change, so it is reported and not designed here.
