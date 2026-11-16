# S2: source findings for the read side

Identities, method and claim marks are in `README.md`. Upstream paths are
relative to `src/ha_mcp/` unless stated. "A" is v9.1.2, "B" is v9.2.0. `P` is
`hass_mcp_engineering_beta/ha_mcp_engineering` at `aeb3b314`.

Four questions were read:

- (a) which admitted reads use the ha-mcp-to-Core websocket (input to P9);
- (b) what the v9.2.0 component manifest requires (input to profile B);
- (c) how the component-path zone row is built and where `friendly_name` goes;
- (d) whether upstream redaction is on by default and whether the `secret`
  rule reaches the `ha_search` flow-helper probe.

Skills content was not read from source. It is hashed from the two pinned
images in a later step.

## (a) Transports per admitted read

### How the table was made

`trace/trace_reads.py` parses the source text of each tool at each tag and
records every REST call and websocket message type the tool's code can reach.
The raw output is `trace/trace-v9.1.2.txt` and `trace/trace-v9.2.0.txt`; the
methods reached through mixins are in `trace/trace-mixins-v9.1.2.txt` and
`trace/trace-mixins-v9.2.0.txt`. The tracer's limits are listed in `README.md`.

Three limits matter when reading the table:

1. A tool is traced as a whole. A multi-mode tool therefore shows calls that an
   admitted read never makes. `ha_manage_blueprints` shows `blueprint/delete`,
   `blueprint/import`, `blueprint/save` and `POST /services/{}/{}` because the
   same tool also writes; `ha_config_list_helpers` and `ha_get_integration`
   show flow `POST` calls that sit behind describe and schema modes.
2. Every tool that calls `get_component_caps` also sends the component info
   probe over the websocket (`tools/component_api.py` A `:494`, B `:500`).
   That alone puts a tool in the websocket column.
3. The table states what the code can reach. What one admitted call sends is
   settled by observation in P9. **[capture]**

### Table at v9.2.0

All rows are **[source]** for "reachable in code" and **[capture]** (P9) for
"sent by an admitted call". Line numbers are at B.

| # | Read | REST reachable | Websocket reachable | Class |
|---|---|---|---|---|
| 1 | `ha_config_get_automation` | `GET /config/automation/config/{}` (`client/rest_client.py:1418`), `GET /states` (`:660`), `GET /states/{}` (`:679`) | `config/entity_registry/get` (`client/rest_client.py:1375`, `tools/config_write_helpers.py:47`), `config/entity_registry/list` (`tools/util_helpers.py:520`), `repairs/list_issues` (`tools/automation_registry_details.py:82`), `search/related` (`:63`), component (`tools/component_automation.py:37`) | both |
| 2 | `ha_config_get_calendar_events` | `GET /calendars/{}` (`tools/tools_calendar.py:232`) | none found | REST only |
| 3 | `ha_config_get_category` | none found | run-time type (`tools/tools_categories.py:93`) | websocket only |
| 4 | `ha_config_get_label` | none found | `config/label_registry/list` (`tools/tools_labels.py:76`) | websocket only |
| 5 | `ha_config_get_scene` | `GET /states` (`client/rest_client.py:660`), `GET /states/{}` (`:679`), run-time path (`:2421`) | `config/entity_registry/get` (`client/rest_client.py:2286`), `config/entity_registry/list` (`tools/scene_discovery.py:111`, `tools/tools_config_scenes.py:242`), `ha_mcp_tools/search` (`tools/scene_discovery.py:71`), component | both |
| 6 | `ha_config_get_script` | run-time path (`client/rest_client.py:2165`) | `config/entity_registry/get` (`client/rest_client.py:2135`, `tools/config_write_helpers.py:47`), `config/entity_registry/list` (`tools/util_helpers.py:520`) | both |
| 7 | `ha_config_list_dashboard_resources` | none found | `lovelace/resources` (`tools/tools_resources.py:233`), `lovelace/info` (`:778`) | websocket only |
| 8 | `ha_config_list_groups` | `GET /states` (`client/rest_client.py:660`) | none found | REST only |
| 9 | `ha_config_list_helpers` | `GET /config/config_entries/entry` (`client/rest_client.py:1829`); flow `POST` calls (`:1627`, `:1710`) in describe paths | `ha_mcp_tools/helpers_list` (`tools/tools_config_helpers.py:394`, `:507`), `config/entity_registry/list` (`tools/config_helpers/registry.py:466`), `config/entity_registry/get` (`tools/config_helpers/describe.py:474`), `config_entries/get_single` (`client/config_entry_read.py:27`), `frontend/get_translations` (`tools/flow_translations.py:40`), component | both |
| 10 | `ha_eval_template` | none found | run-time type (`tools/tools_utility.py:153`), `ha_mcp_tools/template_diagnose` (`:268`), `test_condition` (`:297`), component probe | websocket only |
| 11 | `ha_get_automation_traces` | `GET /states/{}` (`client/rest_client.py:679`) | `trace/list` (`tools/tools_traces.py:277`), `trace/get` (`:239`), `automation/config` (`:491`), `config/entity_registry/get` (`:388`) | both |
| 12 | `ha_manage_blueprints` | `GET /config` (`client/rest_client.py:644`), `GET /services` (`:812`); write-mode `POST /services/{}/{}` (`:745`) | `blueprint/list` (`tools/tools_blueprints.py:381`), `ha_mcp_tools/blueprint_get` (`tools/blueprint_sources.py:210`), `search/related` (`tools/tools_blueprints.py:628`); write-mode types listed in the raw output | both |
| 13 | `ha_get_device` | none found | `config/device_registry/list` (`tools/tools_registry.py:259`), `config/entity_registry/list` (`:224`, `:240`), `config/entity_registry/get` (`:332`), `zha/devices` (`tools/device_enrichment.py:21`), `zwave_js/node_status` (`:59`), `matter/node_diagnostics` (`:101`), component | websocket only |
| 14 | `ha_get_entity` | none found | `config/entity_registry/get_entries` (`tools/tools_entities.py:1337`), `config/entity_registry/list` (`:1380`), component | websocket only |
| 15 | `ha_get_entity_exposure` | none found | run-time types (`tools/tools_voice_assistant.py:109`, `:293`), component probe | websocket only |
| 16 | `ha_get_history` | `GET /config` (`client/rest_client.py:644`) | `history/history_during_period` (`tools/tools_history.py:906`), `recorder/statistics_during_period` (`:1022`, `tools/statistics_resets.py:88`), `recorder/get_statistics_metadata` (`tools/statistics_helpers.py:21`), `ha_mcp_tools/statistics_units` (`:137`), component | both |
| 17 | `ha_get_overview` | `GET /config` (`client/rest_client.py:644`) | `ha_mcp_tools/overview` (`tools/search/overview.py:520`), `persistent_notification/get` (`:324`), `repairs/list_issues` (`:379`), component probe | both |
| 18 | `ha_get_skill_guide` | none found | none found (`server.py:935`) | no Core call in the tool body |
| 19 | `ha_get_state` | `GET /states/{}` (`client/rest_client.py:679`), `GET /config` (`:644`) | `ha_mcp_tools/states` (`tools/search/state.py:350`), component probe | both |
| 20 | `ha_get_system_health` | `GET /states` (`client/rest_client.py:660`), `POST /config/core/check_config` (`:879`), run-time path (`tools/diagnostics_helpers.py:76`) | `repairs/list_issues` (`tools/tools_system.py:1237`), `config_entries/get` (`:1355`, `:1486`, `:1633`), `config/entity_registry/list` (`:1630`), `frontend/get_themes` (`:1525`), `otbr/info` (`:1423`), `zha/devices` (`:1287`), `zwave_js/network_status` (`:1369`), component | both |
| 21 | `ha_get_todo` | `GET /states` (`client/rest_client.py:660`), `GET /states/{}` (`:679`) | `todo/item/list` (`tools/tools_todo.py:215`), one more send at `:379` | both |
| 22 | `ha_get_zone` | `GET /states` (`client/rest_client.py:660`) | `ha_mcp_tools/helpers_list` (`tools/tools_zones.py:344`), `ha_mcp_tools/states` (`:358`), `zone/list` (`:393`), `config/entity_registry/list` (`:451`), component probe | both |
| 23 | `ha_list_floors_areas` | none found | `config/area_registry/list` (`tools/tools_areas.py:307`), `config/floor_registry/list` (`:308`), component (`tools/component_registries.py:128`) | websocket only |
| 24 | `ha_list_services` | `GET /services` (`client/rest_client.py:812`), `GET /config` (`:644`) | `frontend/get_translations` (`tools/tools_services.py:369`), component (`:326`), component probe | both |
| 25 | `ha_search` | legacy path `GET /states` (`client/rest_client.py:660`); flow-helper probe `POST /config/config_entries/options/flow` (see (d)) | `ha_mcp_tools/search` (`tools/search/component.py:919`), legacy `config/entity_registry/list` (`tools/search/response.py:370`), component probe | both |
| 26 | `ha_get_integration` | `GET /config/config_entries/entry` (`client/rest_client.py:1829`, `tools/tools_integrations.py:1178`), run-time path (`tools/diagnostics_helpers.py:76`), `POST /config/config_entries/options/flow` (`client/rest_client.py:1710`), `POST /config/config_entries/subentries/flow` (`:1780`) | `config_entries/get_single` (`client/config_entry_read.py:27`), `config_entries/subentries/list` (`client/rest_client.py:1807`), `frontend/get_translations` (`tools/flow_translations.py:40`), `logger/log_info` (`tools/util_helpers.py:415`), component (`tools/component_config_entries.py:129`, `tools/component_api.py:645`) | both |
| 27 | `ha_get_app` | none found | `supervisor/api` (`tools/tools_addons.py:448`) | websocket only |

Counts at B **[source]**: 9 websocket only (3, 4, 7, 10, 13, 14, 15, 23, 27),
2 REST only (2, 8), 15 both, 1 with no Core call in the tool body (18).

So 24 of the 27 admitted reads can use the ha-mcp-to-Core websocket, and for 9
of them it is the only transport found. **[source]** This is the set P9 has to
cover when it observes the socket. **[capture]**

For reads 10 and 27 the registered tool is a thin wrapper; the row is the trace
of the wrapped method (`eval_template`, `get_addon`).

For reads 17 and 25 the trace stops at calls on another object
(`self._smart_tools.get_system_overview` at `tools/search/overview.py:538`,
`self._smart_tools.deep_search` at `tools/search/legacy.py:556`,
`self._smart_tools._search_dashboards_surface` at
`tools/search/component.py:884`). Those were not followed. **[capture]** (P9)

### Differences between v9.1.2 and v9.2.0

Comparing the reachable sets for the 27 reads at the two tags gives three
additions and no removals **[source]**:

| Read | Added at B | Where |
|---|---|---|
| `ha_get_zone` | websocket `ha_mcp_tools/states` | `tools/tools_zones.py:358`, in `_zone_states` (`:351`); see (c) |
| `ha_list_services` | REST `GET /config` | `tools/tools_services.py:260-279`, `_configured_language`; covered in `sw-write-providers.md` |
| `ha_get_integration` | a component websocket command through `ask_component` | `tools/component_api.py:645` |

The mixin traces are identical at both tags apart from line numbers.
**[source]**

For P9 this means **[source, inference]**: a zone read at B makes one more
socket request than at A when the component is present, and a services read at
B makes one more REST request before the catalog read. Both change the request
count a capture sees; neither changes the transport class of the read.

### An observation about the Engineering Core-requirement table

`P/ha_core_readmission/routes.py:253-286` (`DELEGATED_CORE_REQUIREMENTS`) names
the Core capability profile each delegated read needs. For some reads the named
profile does not match the transports traced above **[source]**:

| Read | Profile named | Traced at both tags |
|---|---|---|
| `ha_config_get_calendar_events` | `core.basic_websocket_read` | REST only |
| `ha_config_list_groups` | `core.basic_websocket_read` | REST only |
| `ha_get_zone` | `core.basic_rest_read` (`:282`) | REST and websocket |
| `ha_get_history` | `core.basic_rest_read` | REST and websocket |
| `ha_get_skill_guide` | `core.basic_rest_read` | no Core call in the tool body |

This is recorded as an observation, not as a defect. The table may express a
requirement on Engineering's own Core access rather than on the upstream
tool's transport, and the tracer over-reports for multi-mode tools. It is the
same at A, so it is not a 9.2.0 change. That file is a protected path outside
the authorized lists and was not edited. It is an input to P9: the socket
observation should record, per read, which transport an admitted call used.
**[capture]**

## (b) The v9.2.0 component manifest

`custom_components/ha_mcp_tools/manifest.json` at B **[source]**:

| Field | Value | Lines |
|---|---|---|
| `requirements` | `mcp>=1.24.0`, `ruamel.yaml>=0.18.0`, `voluptuous-openapi>=0.4.1`, `ha-mcp==9.2.0` | `:31-36` |
| `dependencies` | `webhook` | `:25-27` |
| `after_dependencies` | `backup`, `cloud`, `config`, `energy`, `frontend`, `http`, `logbook`, `lovelace`, `network`, `recorder` | `:4-15` |
| `config_flow` | `true` | |
| `iot_class` | `local_push` | |
| `version` | `9.2.0` | `:37` |

The diff from A is two lines: `ha-mcp==9.1.2` becomes `ha-mcp==9.2.0`, and
`version` becomes `9.2.0` (blob `c4a067100c` to `152989463a`). **[source]**

`hacs.json` at B names `"homeassistant": "2026.8.0"` as the minimum Core.
**[source]**

Consequences for profile B **[source, inference]**:

- Installing the component makes Core install `ha-mcp==9.2.0` from the package
  index into Core's own Python environment. The component and the peer are then
  the same release only if the peer image is also 9.2.0. A lane that pairs a
  9.1.2 peer with a 9.2.0 component, or the reverse, is a mixed pairing and
  must be named as such.
- That install needs package-index access from the disposable Core at start,
  or a pre-populated environment. Which one the lane uses is a lane design
  point, and the installed version is to be read back from the container.
  **[capture]** (P1, P5)
- The other three requirements are ranges, so the versions Core resolves are
  not fixed by the manifest. **[capture]** (P1)
- `dependencies` is `webhook` only. The `after_dependencies` list means the
  component loads after `recorder`, `lovelace` and the rest when they are
  present, but does not require them. A bare Core without `recorder` still
  loads the component. **[capture]** (P5)

The component directory changed in 56 files between the tags (+1422 -380). Only
the manifest was read for this question; the files that back the governed
writes are covered in `sw-write-providers.md`.

## (c) Zone rows and `friendly_name`

File `tools/tools_zones.py`. The registered name is `ha_get_zone` (A `:165`,
B `:185`).

### Component path

- `_shape_component_zone_record` (A `:108`, B `:112-135`) has the same body at
  both tags. The row is `dict(config)` plus `id`, `entity_id`, `editable` and
  `source`. It does not read, add or remove `friendly_name`. **[source]**
- A `friendly_name` key therefore appears on a component-path row only if the
  component's `config` for that zone carries one. Whether it does, per
  profile, was not read. **[capture]** (P2 payload capture for `ha_get_zone`
  on profile B)
- `_with_registry_icons` (A `:133`, B `:137-148`) changed. At A it overrides
  `icon` with the entity-registry icon. At B it leaves `icon` alone and sets a
  new key `entity_icon`. **[source]**
- New at B: `_OCCUPANCY_ATTRS = ("persons", "device_trackers")` (`:50`),
  `_with_occupancy` (`:152-164`) and `_zone_states` (`:351`). Rows gain
  `persons` and `device_trackers` taken from a states read: the component
  command `ha_mcp_tools/states` (`:358`) when available, otherwise REST
  `get_states`. If that read fails, the response carries the warning "Who is in
  each zone (persons) is missing: ..." (`:329`). **[source]**
- `_shape_component_zone_rows` is at A `:147`, B `:167-175`. **[source]**

### Legacy path (no component)

`_legacy_zone_rows` is at B `:381`. Both tags read REST `get_states` for the
rows that are not in zone storage. **[source]**

- At A (`:369`) a non-storage row is built with
  `"name": attrs.get("friendly_name", object_id)` and the attributes spread in,
  so the row has both `name` and `friendly_name`. **[source]**
- At B (`:433-436`) the attributes are copied and
  `name = attrs.pop("friendly_name", object_id)`, so the row has `name` and no
  `friendly_name`. **[source]**
- B also applies `_with_occupancy` to storage rows and changes the failure
  warning to "Zones outside the zone storage such as 'home', and who is in
  each zone, are missing: ..." (`:425-426`). **[source]**

### Descriptor

The `ha_get_zone` docstring at B gains text about `icon` and `entity_icon`
(`:210-216`). The descriptor description changes, so its fingerprint changes.
**[source]** for the text, **[capture]** (P2) for the fingerprint.

### Engineering side

`P/providers/upstream_reads_9_1_entities.py:250-259` (`_project_zone`) requires
only that `zones` is a list of dicts or `zone` is a dict. Its comment at
`:248-249` calls extra rows and the keys `entity_id`, `editable`, `source` and
`icon` additive. **[source]** The 9.1 zone fingerprint is at
`P/providers/upstream_reads_9_1.py:39`.

So **[source, inference]**:

- the B row-field changes (`entity_icon`, `persons`, `device_trackers`, the
  missing `friendly_name` on legacy non-storage rows) pass that shape check
  unchanged; they are not refused by the existing projection;
- a client that read `friendly_name` from a non-storage zone row gets it at A
  and not at B on the legacy path;
- the meaning of `icon` on a component-path row changes from "registry icon if
  set, else zone icon" at A to "zone icon" at B, with the registry icon moved
  to `entity_icon`.

The actual rows on each profile are **[capture]** (P2).

## (d) Redaction default and the `secret` rule

### Default

`redact_secrets` is declared at `config_settings.py:554-556` at both tags as
`Field(False, alias="REDACT_SECRETS")`. It is off by default. The file differs
between the tags by one comment line (blob `650db42d` to `f343da9f`).
**[source]**

`redaction_enabled()` is at A `:88`, B `:89-91`. `RedactSecretsMiddleware` is
always registered (`server.py:308-316`) and passes responses through while the
setting is off. **[source]**

### The rule

`redaction.py` changed (+12 -8, blob `15172062` to `2785f2b4`).
`is_password_flow_field` (A `:467`, B `:468`) gains, at B `:479-480`:
`if field.get("secret") is True: return True`. A flow field marked `secret` is
now treated as a password field. **[source]**

### Reach into the `ha_search` flow-helper probe

The call chain is the same file content at both tags except where noted
**[source]**:

1. `tools/smart_search/_flow_helpers.py:177-181` (same blob `991e08f6`) calls
   `fetch_entry_options_with_status`.
2. `tools/tools_integrations.py:182` (`fetch_entry_options_with_status`) calls
   `client.start_options_flow(entry_id)` (`:227`) and
   `options_from_form_flow(flow)` (`:236`).
3. `tools/integration_schema.py:40` (same blob `5ac637d8`) sets
   `redact = redaction_enabled()` (`:63`) and tests
   `if redact and is_password_flow_field(field)` (`:75`).

So the `secret` rule does reach the probe at B, and only when `redact_secrets`
is on. **[source]** With the default, the probed option values are returned
unredacted at B exactly as at A. **[source, inference]**

Two things follow for the lane **[source, inference]**:

- A peer started with default settings does not exercise the new rule at all.
  If the lane is meant to show the rule working, the peer has to be started
  with `REDACT_SECRETS` on, and the plan should say which setting each profile
  uses. **[capture]** (P2, per profile)
- The probe starts an options flow with REST
  `POST /config/config_entries/options/flow` (`client/rest_client.py` B
  `:1692-1712`), unchanged from A. A search that reaches the probe therefore
  sends a `POST` to Core. Any lane assertion of the form "admitted reads send
  no write request" has to account for it. **[capture]** (P9)

## Summary

| Question | Answer | Mark |
|---|---|---|
| (a) reads on the socket | 24 of 27 can use it; 9 use only it; 2 are REST only; 1 makes no Core call | source; per-call set by capture (P9) |
| (a) change A to B | three additions: zone states read, services `GET /config`, integration component command | source |
| (b) manifest | pins `ha-mcp==9.2.0`; only the pin and version changed | source; installed versions by capture (P1, P5) |
| (c) `friendly_name` | component path never handles it; legacy path keeps it at A and drops it at B | source; rows by capture (P2) |
| (c) other zone changes | `entity_icon`, `persons`, `device_trackers`, one more states read, description text | source; fingerprint by capture (P2) |
| (d) redaction default | off at both tags | source |
| (d) `secret` rule reach | reaches the probe only when redaction is on | source |
