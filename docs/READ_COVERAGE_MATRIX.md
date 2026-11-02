# Read-Coverage Matrix

HAMCP-265 (includes former HAMCP-230). Source of truth for the tables below:
[`read_coverage_matrix.json`](read_coverage_matrix.json). The test
`tests/test_read_coverage_matrix.py` guards both files.

## Purpose

This document maps each Home Assistant data source to every Engineering MCP
path that reads it or writes it. For each source it records:

- the read paths: tool, provider, and purpose binding;
- the admission or signing status of each path;
- how complete the reads are, with the reason;
- the write paths: operations, tool and policy;
- each gap, with the exact requirement that would close it.

The document describes current source behaviour only. It is decision support
for planning; it does not grant authority to read or write anything. Runtime
admission depends on the signed registry and the deployed versions. Under
[`ADR-021`](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md), only an
exact deployed read-only observation establishes what a running system does.

[`FEATURE_READINESS_MAP.md`](FEATURE_READINESS_MAP.md) (HAMCP-266) builds on
this matrix. Its gap ids (`RCG-NNN`) refer to the gaps defined here.

## How to read the tables

**Admission**:

- `Core profile <id>`: the path runs only through the named compiled Core
  capability profile in
  [`profiles.py`](../hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/profiles.py).
  The signed registry must admit that profile for the observed Core version.
- `ha-mcp automatic_read`: a reviewed delegated `ha-mcp` read. Its policy is in
  `upstream_tool_policy*.json`. The tool is available only when the observed
  upstream version matches a reviewed policy.
- `ha-mcp adapter <id>`: a compiled special adapter that wraps one reviewed
  upstream tool, with narrowed arguments and bounded output.
- `direct read policy <id>`: an Engineering-native read under
  `DIRECT_HA_READ_POLICIES` in
  [`routing.py`](../hass_mcp_engineering_beta/ha_mcp_engineering/providers/routing.py).
- `reader purpose <id>`: a purpose-bound internal reader of the upstream read
  gateway. A client cannot select these readers directly.
- `Engineering-local`: the data is Engineering's own persisted store. No Home
  Assistant call is made.

**Completeness**:

- `complete`: every record of the source can be read through an admitted path.
- `partial`: some records, fields or sizes cannot be read. The reason column
  says which.
- `unavailable`: no admitted path reads the source.

**Requirement types** (to close a gap):

| Type | Meaning |
|---|---|
| `new_reader_purpose` | a new, separately audited purpose for the internal read gateway, inside what an existing adapter can already read |
| `new_reader_contract` | an explicitly reviewed extension or new bounded file-reader contract, with its adapter and admission work, before any purpose can use it |
| `new_signed_profile` | a new compiled Core capability profile, signed in a registry sequence |
| `upstream_admission` | a reviewed `ha-mcp` policy change that admits a delegated tool |
| `impossible_via_ha_api` | Home Assistant exposes no supported API for this data |
| `owner_decision` | a product or privacy decision for Josh before any design |
| `feature_work` | Engineering code only, inside existing admission |

## Versions and limits

- **ha-mcp policy.** The reviewed policies range from 7.14.1 to 8.6.0. The
  default reviewed release is 7.14.1. The 8.6.0 policy and its public-read
  adapters are compiled but on HOLD (HAMCP-156). A delegated tool marked
  "8.6.0" is reachable only once that version is admitted and observed.
- **Core profiles.** The table "Compiled Core profile index" lists every
  compiled profile. A profile admits nothing until the signed registry admits
  it for the exact observed Core release. Sequence 15 is the active signing:
  22 profiles for Core 2026.10.0, 26 for 2026.10.1 and all 31 for 2026.10.3
  (the matrix-wide notes list the exact deltas). Sequence 16 carries the 31
  existing profiles forward to Core 2026.11. A new profile becomes signable
  only after its feature is implemented, reviewed and merged; see
  **Signing** in the readiness map.
- **Citations.** Citations give `file:line` and an anchor string. The guard test
  fails if a cited file is missing or no longer contains its anchor. Line
  numbers can drift as files change; `--write` refreshes them to the nearest
  line containing the anchor.
- **Example data.** All examples are synthetic. This document holds no
  household data.

## Helper definition readability (former HAMCP-230)

The source entries under **UI helpers** record, for each helper type, which
parts of the definition can be read, and whether a governed CREATE exists.
Today, governed CREATE exists only for `input_boolean` and `input_number`.

Config-entry helper definitions have three distinct read paths, which this
matrix keeps apart:

- **Native Core metadata.** `get_configuration_definitions` returns entry
  metadata only for UI helpers (`definition_not_readable_without_flow`), plus
  allowlisted YAML keys from `configuration.yaml`.
- **Delegated helper list.** The admitted `ha_config_list_helpers` route
  (`routes.py`, `core.basic_websocket_read`) can return `ConfigEntry.options`
  where the reviewed `ha-mcp` version and its component helper-list path
  support it. The reviewed 8.5.0 source preserves options in each flow-helper
  record. This is evidenced for `filter` (HAMCP-230, HAAC-E008) and unverified
  for the other types; unverified is not impossible. `ConfigEntry.options` is
  not `ConfigEntry.data`, and is not proof of a complete definition for every
  type.
- **Options-flow baseline.** Inside `create_helper_options_plan` only, for
  the catalogued types. HAMCP-232 OD-1 keeps it a planning read, never a
  general read.

This change adds no runtime read exposure, no component and no route. Where
the delegated list proves insufficient for a type, a read-only options-flow
profile remains possible only after an owner reversal of OD-1.

## Updating the matrix

1. When the guard test reports drift, read its message. It names the
   inventory and the items that were added, removed or changed.
2. Edit the affected entries in `read_coverage_matrix.json`: read paths, write
   paths, admission, completeness, gaps and the index tables.
3. Run `python tests/read_coverage_inventory.py --write`. It records the new
   fingerprints, refreshes line numbers and regenerates the tables below.
4. Review the diff. Only the JSON file and the generated block may change.

Do not edit the generated block by hand.

## Matrix

<!-- BEGIN GENERATED: tests/read_coverage_inventory.py --write -->

### Summary

| Group | Source | Completeness | Read paths | Write paths | Gaps |
|---|---|---|---|---|---|
| Automations, scripts, scenes, groups and blueprints | [Automations (UI-managed storage and YAML-defined)](#automations) | partial | 5 | 6 | RCG-001 |
| Automations, scripts, scenes, groups and blueprints | [Scripts](#scripts) | partial | 3 | 3 | RCG-002, RCG-003 |
| Automations, scripts, scenes, groups and blueprints | [Scenes](#scenes) | partial | 2 | 2 | RCG-004, RCG-005, RCG-006, RCG-007 |
| Automations, scripts, scenes, groups and blueprints | [Groups (YAML group: and group helpers)](#groups) | partial | 5 | 1 | RCG-008, RCG-009, RCG-010 |
| Automations, scripts, scenes, groups and blueprints | [Blueprints](#blueprints) | partial | 5 | 0 | RCG-011, RCG-012 |
| YAML, packages, secrets and restore state | [YAML templates](#yaml-templates) | partial | 4 | 1 | RCG-013, RCG-014, RCG-015 |
| YAML, packages, secrets and restore state | [Packages, configuration.yaml paths and !include files](#packages-and-yaml-files) | partial | 3 | 0 | RCG-016, RCG-017, RCG-018 |
| YAML, packages, secrets and restore state | [secrets.yaml (presence only)](#secrets) | unavailable | 2 | 0 | RCG-019 |
| YAML, packages, secrets and restore state | [.storage/core.restore_state](#restore-state) | unavailable | 0 | 0 | RCG-020 |
| UI helpers | [input_boolean and input_number helpers](#helpers-input-boolean-number) | partial | 5 | 2 | RCG-021, RCG-022, RCG-023, RCG-024 |
| UI helpers | [input_select helpers](#helpers-input-select) | partial | 4 | 1 | RCG-025, RCG-026, RCG-027, RCG-028, RCG-029 |
| UI helpers | [Other storage-collection helpers](#helpers-storage-other) | partial | 4 | 0 | RCG-030, RCG-031, RCG-032, RCG-033 |
| UI helpers | [template, filter and statistics helpers (UI)](#helpers-options-supported) | partial | 5 | 1 | RCG-034, RCG-035, RCG-036, RCG-037, RCG-038, RCG-039 |
| UI helpers | [Other config-entry helpers (UI)](#helpers-options-other) | partial | 2 | 0 | RCG-040, RCG-041, RCG-042 |
| UI helpers | [Helper types Engineering does not model](#helpers-not-modelled) | partial | 1 | 0 | RCG-043, RCG-044 |
| Dashboards | [Dashboard content (storage-mode Lovelace configuration)](#dashboards-content) | partial | 5 | 2 | RCG-045, RCG-046, RCG-047, RCG-093 |
| Dashboards | [Dashboard metadata (title/icon/visibility registry rows)](#dashboard-metadata) | partial | 1 | 1 | RCG-048, RCG-049 |
| Dashboards | [Dashboard resources (Lovelace resource list)](#dashboard-resources) | partial | 1 | 0 | RCG-050 |
| Dashboards | [YAML-mode dashboards (dashboard body in YAML files)](#yaml-dashboards) | unavailable | 1 | 0 | RCG-051, RCG-052 |
| Registries | [Entity registry (metadata, availability, exposure)](#entity-registry) | partial | 3 | 2 | RCG-053, RCG-054 |
| Registries | [Device registry](#device-registry) | partial | 2 | 0 | RCG-055, RCG-056 |
| Registries | [Area registry](#area-registry) | partial | 2 | 0 | RCG-057 |
| Registries | [Floor registry](#floor-registry) | partial | 1 | 0 | RCG-058 |
| Registries | [Label registry](#label-registry) | partial | 1 | 1 | RCG-059 |
| Registries | [Category registry](#category-registry) | partial | 1 | 1 | RCG-060 |
| Integrations, states and services | [Config entries and integrations](#config-entries-and-integrations) | partial | 5 | 0 | RCG-061, RCG-062 |
| Integrations, states and services | [Entity states, search, overview and templates](#entity-states) | complete | 8 | 2 | - |
| Integrations, states and services | [Service catalog](#services-catalog) | partial | 3 | 0 | RCG-063 |
| Integrations, states and services | [Zones, to-do lists, calendar events](#zones-todo-calendar) | partial | 3 | 0 | RCG-064 |
| Logs and traces | [Home Assistant Core logs](#logs-core) | partial | 3 | 0 | RCG-065, RCG-066, RCG-067 |
| Logs and traces | [Supervisor logs](#logs-supervisor) | partial | 1 | 0 | RCG-068, RCG-069 |
| Logs and traces | [Add-on logs](#logs-addon) | partial | 2 | 0 | RCG-070 |
| Logs and traces | [Host / OS logs](#logs-host) | partial | 1 | 0 | RCG-071, RCG-072 |
| Logs and traces | [Automation traces](#traces-automation) | partial | 6 | 2 | RCG-073 |
| Logs and traces | [Script traces](#traces-script) | partial | 2 | 0 | RCG-074 |
| History, recorder and statistics | [State history (recorder)](#history) | partial | 3 | 0 | RCG-075, RCG-076 |
| History, recorder and statistics | [Logbook](#logbook) | partial | 1 | 0 | RCG-077 |
| History, recorder and statistics | [Recorder settings](#recorder-settings) | partial | 1 | 0 | RCG-078 |
| History, recorder and statistics | [Long-term statistics](#statistics-long-term) | partial | 1 | 0 | RCG-079, RCG-080 |
| History, recorder and statistics | [Energy dashboard preferences](#energy-prefs) | unavailable | 0 | 0 | RCG-081 |
| Supervisor and system | [Add-ons](#addons) | partial | 2 | 1 | RCG-082, RCG-083, RCG-084 |
| Supervisor and system | [Backups](#backups) | partial | 1 | 1 | RCG-085, RCG-086 |
| Supervisor and system | [System health](#system-health) | partial | 3 | 0 | RCG-087 |
| Supervisor and system | [Repairs](#repairs) | partial | 1 | 0 | RCG-088 |
| Supervisor and system | [Updates](#updates) | partial | 1 | 0 | RCG-089, RCG-090 |
| Supervisor and system | [Home Assistant restart and reload](#home-assistant-restart-reload) | partial | 1 | 2 | RCG-091, RCG-092 |

### Matrix-wide notes

- Deployed Home Assistant Core and ha-mcp versions cannot be determined from source. Every admission statement here is conditional on the observed versions (ADR-021).
- The signed Core registry is disabled by default (ha_core_release_registry_enabled: false in hass_mcp_engineering_beta/config.yaml). Runtime-only Core profiles admit nothing until it is enabled and a signed envelope covers the observed Core release; the deployed option value is unknown from source.
- Signed registry sequence 15 (the journal tip in upstream-trust/ha-core-release-registry.json) carries three exact Core releases. 2026.10.0: 22 profiles, including core.entity_registry_metadata_operation. 2026.10.1: 26, adding core.configuration_delete_operation, core.entity_registry_availability_operation, core.entity_assistant_exposure_operation and core.label_registry_operation (first signed for 2026.10.1 in sequence 13; sequences 14 and 15 carry them, and add 2026.10.3). 2026.10.3: all 31, adding core.config_entry_helper_options_operation, core.typed_input_select_option_operation, core.script_trace_read, core.dashboard_metadata_operation and core.scene_configuration_operation (sequence 15). Runtime enablement and current Core authority still apply to every one. Each exact Core release, including each patch release, needs its own signed entry; Core 2026.11 needs sequence 16 to carry the 31 existing profiles forward (readiness map, Signing). Per-source version-coverage gaps are not repeated.
- core.scene_configuration_operation is already in sequence 15 for Core 2026.10.3. Text that says it awaits sequence 16 (for example the comment in governance/scene_configuration.py) is stale.
- Backup creation, add-on restart, Home Assistant restart and reload are admitted for ha-mcp 7.14.1 to 8.1.1 only and held at 8.2.0 to 8.6.0; ha-mcp 8.6.0 itself is on HOLD (HAMCP-156).
- ha-mcp tools classified mixed_or_requires_wrapper without an adapter (ha_get_logs, ha_manage_backup, ha_manage_energy_prefs, ha_manage_updates) are unreachable and appear only in gaps.
- Redaction and sanitization of each path are recorded as implemented; this matrix did not re-verify the generic gateway sanitizer.
- A pending_signed_registry_admission refusal does not by itself prove that a profile is unsigned: the same reason is also reported while Core authority is transiently retired for re-verification (HAMCP-275, architect report on beta.54). HAMCP-276 (options apply refused at dispatch_authority) and HAMCP-274 (input_select option planning refused) are separate apply and planning failures.
- Governed configuration writes (automation, script, helper, entity metadata, label and scene-edit families) and the operational helper writes share a known recovery limit: a dispatched F3 child under an already-terminal parent can stay non-terminal and keep its locks (HAMCP-271). Its correction is in a separate pull request (#340) and is not part of this source.

### Helper definition readability by type (former HAMCP-230)

| Helper type | Source | Definition store | UI definition read | YAML definition read | Definition completeness | Create | Update | Delete | State write | Gaps |
|---|---|---|---|---|---|---|---|---|---|---|
| `input_boolean` | [helpers_input_boolean_number](#helpers-input-boolean-number) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | governed | governed | none | on/off plan | RCG-021, RCG-022, RCG-023, RCG-024 |
| `input_number` | [helpers_input_boolean_number](#helpers-input-boolean-number) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | governed | governed | none | none | RCG-021, RCG-022, RCG-023, RCG-024 |
| `input_select` | [helpers_input_select](#helpers-input-select) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | select option plan | RCG-025, RCG-026, RCG-027, RCG-028, RCG-029 |
| `input_text` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `input_datetime` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `input_button` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `counter` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `timer` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `schedule` | [helpers_storage_other](#helpers-storage-other) | storage collection | delegated ha_config_list_helpers | held YAML reader only | partial | none | none | none | none | RCG-030, RCG-031, RCG-032, RCG-033 |
| `template` | [helpers_options_supported](#helpers-options-supported) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified. Options-flow baseline: governed planning only (OD-1). | configuration.yaml (allowlisted keys) | partial | none | options (sensor, binary_sensor) | none | none | RCG-034, RCG-035, RCG-036, RCG-037, RCG-038, RCG-039 |
| `filter` | [helpers_options_supported](#helpers-options-supported) | config entry or YAML | Native reads: entry metadata only. Delegated list: options returned (verified, HAMCP-230). Options-flow baseline: governed planning only (OD-1). | configuration.yaml (allowlisted keys) | partial | none | options | none | none | RCG-034, RCG-035, RCG-036, RCG-037, RCG-038, RCG-039 |
| `statistics` | [helpers_options_supported](#helpers-options-supported) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified. Options-flow baseline: governed planning only (OD-1). | configuration.yaml (allowlisted keys) | partial | none | options | none | none | RCG-034, RCG-035, RCG-036, RCG-037, RCG-038, RCG-039 |
| `derivative` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `integration` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `min_max` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `history_stats` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `threshold` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `trend` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `utility_meter` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `sql` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `group` | [helpers_options_other](#helpers-options-other) | config entry or YAML | Native reads: entry metadata only. Delegated list: options unverified.  | configuration.yaml (allowlisted keys) | partial | none | none | none | none | RCG-040, RCG-041, RCG-042 |
| `switch_as_x` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |
| `tod` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |
| `random` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |
| `generic_thermostat` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |
| `generic_hygrostat` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |
| `mold_indicator` | [helpers_not_modelled](#helpers-not-modelled) | config entry | Delegated list only (needs ha_mcp_tools); options unverified. | none | partial | none | none | none | none | RCG-043, RCG-044 |

### Automations, scripts, scenes, groups and blueprints

<a id="automations"></a>
#### Automations (UI-managed storage and YAML-defined) (`automations`)

Completeness: **partial**. Stored config readable one automation at a time for UI-managed automations with an id (get_automation_config, ha_config_get_automation); the list is state-based; YAML-defined automations without ids have no config route. Writes limited to governed create/update (+ delete when profile admitted).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_automations` | direct_ha_api | bounded_automation_inventory_read (DIRECT_HA_READ_POLICIES) | native (DIRECT_HA_TOOL_EXCEPTIONS); core.automation_configuration_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | GET /states filtered to automation.*; returns entity_id, friendly_name, state, last_triggered, id only - no stored config. Runtime state, not configuration; automations absent from the state machine are not listed. | `routing.py:434`<br>`compatibility.py:618`<br>`routes.py:256` |
| `get_automation_config` | direct_ha_api | exact_automation_config_read (DIRECT_HA_READ_POLICIES) | native (DIRECT_HA_TOOL_EXCEPTIONS); core.automation_configuration_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | GET /config/automation/config/{id} for one validated id: full stored UI-managed automation config. YAML-defined automations (no storage id) have no such config route. UNCERTAIN: the compatibility function returns via dump_json without visible redaction; check the response sanitizer before claiming secrets are screened. Delivery is truncated at MAX_CHARS (60,000) characters with a marker and no continuation argument, so the read is complete only below that bound. | `routing.py:440`<br>`compatibility.py:648`<br>`routes.py:257`<br>`compatibility.py:87` |
| `ha_config_get_automation` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_config_get_automation (automatic_read in stock policies 7.14.2 through 8.6.0; README reviewed healthy pairing 8.4.3/8.5.0; 8.6.0 adapter ha-mcp-8.6.0-public-reads-v1); core.automation_configuration_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | complete | Delegated exact-schema read of one automation config; same stored-config coverage as get_automation_config, via upstream. | `upstream_tool_policy_8_6_0.json:1`<br>`upstream_reads_8_6.py:11`<br>`routes.py:186` |
| `capture_automation_baseline` | direct_ha_api | loaded_automation_entities-v1 (ANALYTICAL_PROVIDER_POLICIES) | native (ENGINEERING_NATIVE); core.automation_baseline_metadata_read + basic_rest_read + basic_websocket_read + non_device_registry_read + direct_device_registry_read + automation_configuration_read | partial | Captures loaded automation entities and per-automation stored config via GET /config/automation/config/{verified_configuration_id}; evidence snapshot, not a general reader. | `routing.py:255`<br>`capture_contracts.py:254`<br>`routes.py:227` |
| `internal:dependency_index_loaded_config` | direct_ha_api | dependency index loaded source (HAMCP-212/227); WebSocket automation/config + search/related | native; batch_command_allowed allow-list; core.basic_websocket_read / automation_configuration_read | partial | Dependency tools (entity_dependency_analysis, change_impact_analysis, configuration_integrity_analysis) read loaded automation config per automation entity with an id; falls back to REST /config/automation/config/{id}. Bounded by index limits; truncation marks sources partial. | `provider.py:1023`<br>`provider.py:1103` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_change_plan` | create, update | RISK_ASSESSMENT capability; legacy ChangeOperation plan -> approve_change_plan -> apply_change_plan; core.dependency_helper_planning for planning reads | native; planning core.dependency_helper_planning (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)); apply via F3 core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | Legacy automation plan: GET /config/automation/config/{id}; create refused if id exists, update refused if absent; no_change when identical; the plan itself dispatches no write. UNCERTAIN: docs/CHANGE_GOVERNANCE.md says only create_automation/update_automation accepted, create_plan does ChangeOperation(operation) over the whole enum (verify tool-level input schema before relying on either). | `routing.py:180`<br>`routes.py:269`<br>`service.py:5331`<br>`models.py:30` |
| `create_configuration_plan` | create, update | RISK_ASSESSMENT; <=8 operations, resource_type automation\|script\|helper(input_boolean,input_number); actions create\|update only; non-atomic, stop on first failure, no auto rollback | native; planning core.dependency_helper_planning (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)); apply F3 core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | Generic governed configuration path: closed resource set {automation, script, helper(input_boolean\|input_number)} x {create, update}. Delete and scene edit are NOT covered by this profile; they need their own signed profiles. Group YAML, template YAML, scene create, blueprint files, packages are not resource types. | `routing.py:181`<br>`service.py:446`<br>`service.py:448`<br>`profiles.py:388`<br>`strategies.py:48` |
| `apply_change_plan` | create, update, delete | requires exact-hash external approval; F3 dispatch by f3_requirements(); downstream authority acquires the profile per step | native (CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY); per-step core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | f3_requirements() adds core.governed_configuration_operation for automation/script/input_boolean/input_number create\|update; delete adds core.configuration_delete_operation; scene adds core.scene_configuration_operation; any other identity gets core.unsupported_f3_operation (no lease). Dormant profile -> pending_signed_registry_admission. | `routes.py:949`<br>`routes.py:35`<br>`governance.py:122` |
| `create_configuration_delete_plan` | delete | OPERATIONAL_ADMINISTRATION; 1-8 automation/script targets; pre-image captured, consumers disclosed, rollback recreates | signed:core.configuration_delete_operation (state: compiled; signed for Core 2026.10.1 since registry sequence 13 and for 2026.10.3 since sequence 14, both carried by sequence 15; not signed for 2026.10.0 - repo data only, deployment unknown) | Only automation and script resource types can be deleted (CORE_CONFIGURATION_DELETE_PROFILES resource_types). Scenes, groups, templates, helpers, blueprint files are not deletable. | `routing.py:197`<br>`routes.py:107`<br>`profiles.py:598` |
| `upsert_automation` | create, update | PROHIBITED (LEGACY_AUTOMATION_WRITE); redirects to create_change_plan -> approve_change_plan -> apply_change_plan | prohibited (fails closed, GovernanceError PROVIDER_PROHIBITED); no write reachable | Negative path: raises PROVIDER_PROHIBITED with replacement create_change_plan; never dispatches. | `routing.py:169`<br>`compatibility.py:713` |
| `delete_automation` | delete | PROHIBITED (UNGOVERNED_PHYSICAL_ACTION); fallback none; use create_configuration_delete_plan | prohibited (operation_prohibited, fallback none) | Negative path only; legacy delete never dispatches. | `routing.py:176`<br>`compatibility.py:730` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-001 | YAML-defined automations (automations.yaml without ids / packages) stored config | new_reader_contract | The YAML adapter fixes the file to configuration.yaml, its L2 deny floor includes packages, and it returns !include as text without following it; a reader purpose only selects dotted paths inside that adapter, so a purpose alone reaches none of these files. Closure needs an explicitly reviewed extension or a new bounded file-reader contract (fixed allowlist, size bounds, !secret never resolved) with any adapter and admission work, then an audited purpose. No Core profile is implied. HAMCP-264 covers packages; automation YAML without ids is not named in any register row. | HAMCP-264 |

<a id="scripts"></a>
#### Scripts (`scripts`)

Completeness: **partial**. No Engineering-native client tool returns a script config; the delegated ha_config_get_script reads one script by id. Complete enumeration beyond state+registry inventory is deferred (HAMCP-264). Writes: governed create/update and delete via profiles.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_script` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_config_get_script (automatic_read in stock policies 7.14.2 through 8.6.0; reviewed pairing 8.4.3/8.5.0); core.basic_websocket_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | complete | Delegated exact read of one script's stored config. | `upstream_tool_policy_8_6_0.json:1`<br>`upstream_reads_8_6.py:16`<br>`routes.py:191` |
| `internal:dependency_script_loaded_reader` | direct_ha_api | script_sources LOADED_SCRIPT_SOURCE loaded_websocket (script/config + search/related) | native; core.basic_websocket_read | partial | Scripts read from loaded WebSocket config only for script entities present in state and not unavailable; MAX_SCRIPT_SOURCES=1000 and SCRIPT_SCAN_SECONDS=60 bound it. | `script_sources.py:39`<br>`script_sources.py:25`<br>`provider.py:1014` |
| `internal:script_dependency_reader` | upstream_read_gateway | script_dependency_reader (ha_config_get_script), STORED_SCRIPT_SOURCE stored_delegated [gateway_reader:script_dependency_reader] | upstream:ha_config_get_script (automatic_read in stock policies 7.14.2 through 8.6.0); core.basic_websocket_read | partial | Internal purpose-bound stored read used as fallback to the loaded read (HAMCP-212) with require_unmodified_script checks; no client-visible tool binds to it directly. | `script_sources.py:40`<br>`upstream_read_gateway.py:3834` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_configuration_plan` | create, update | RISK_ASSESSMENT; <=8 operations, resource_type automation\|script\|helper(input_boolean,input_number); actions create\|update only; non-atomic, stop on first failure, no auto rollback | native; planning core.dependency_helper_planning (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)); apply F3 core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | Generic governed configuration path: closed resource set {automation, script, helper(input_boolean\|input_number)} x {create, update}. Delete and scene edit are NOT covered by this profile; they need their own signed profiles. Group YAML, template YAML, scene create, blueprint files, packages are not resource types. | `routing.py:181`<br>`service.py:446`<br>`service.py:448`<br>`profiles.py:388`<br>`strategies.py:48` |
| `apply_change_plan` | create, update, delete | requires exact-hash external approval; F3 dispatch by f3_requirements(); downstream authority acquires the profile per step | native (CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY); per-step core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | f3_requirements() adds core.governed_configuration_operation for automation/script/input_boolean/input_number create\|update; delete adds core.configuration_delete_operation; scene adds core.scene_configuration_operation; any other identity gets core.unsupported_f3_operation (no lease). Dormant profile -> pending_signed_registry_admission. | `routes.py:949`<br>`routes.py:35`<br>`governance.py:122` |
| `create_configuration_delete_plan` | delete | OPERATIONAL_ADMINISTRATION; 1-8 automation/script targets; pre-image captured, consumers disclosed, rollback recreates | signed:core.configuration_delete_operation (state: compiled; signed for Core 2026.10.1 since registry sequence 13 and for 2026.10.3 since sequence 14, both carried by sequence 15; not signed for 2026.10.0 - repo data only, deployment unknown) | Only automation and script resource types can be deleted (CORE_CONFIGURATION_DELETE_PROFILES resource_types). Scenes, groups, templates, helpers, blueprint files are not deletable. | `routing.py:197`<br>`routes.py:107`<br>`profiles.py:598` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-002 | Complete script enumeration beyond state and registry inventory | feature_work | Enumeration source for stored scripts independent of loaded state (HAMCP-264); README/CHANGELOG state it is deferred | HAMCP-264 |
| RCG-003 | Script blueprint bodies not resolved in dependency analysis | feature_work | Resolve use_blueprint in scripts like automations. Unassigned candidate: HAMCP-264's scope names YAML, template, package and stored-script reads, not blueprint-body expansion. | - |

<a id="scenes"></a>
#### Scenes (`scenes`)

Completeness: **partial**. Single-scene delegated read plus loaded-membership references; no list of stored scene configs. Write is edit-only for existing UI scenes; scene create is absent.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_scene` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read); scene_id required | upstream:ha_config_get_scene (automatic_read in stock policies 7.14.2 through 8.6.0; reviewed pairing 8.4.3/8.5.0); core.basic_websocket_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | Delegated read of one scene by scene_id (scene_identity_required otherwise). No client-visible scene LIST tool in Engineering; listing is via ha_search / ha_get_state. UNCERTAIN: whether upstream returns YAML-defined scene config (upstream behavior not verified). | `upstream_tool_policy_8_6_0.json:1`<br>`upstream_reads_8_6.py:1017`<br>`routes.py:190` |
| `internal:dependency_scene_membership` | direct_ha_api | global_sources membership from loaded state entity_id attributes (HAMCP-227) | native; no extra request beyond states inventory | partial | Scenes' member entities come from loaded state attributes, labelled 'loaded membership (state attributes), not stored configuration'; MAX_SCENE_SOURCES=1000, MAX_MEMBERS_PER_SOURCE=256. Stored scene config is not read by the index. | `global_sources.py:49`<br>`global_sources.py:36`<br>`global_sources.py:252` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_scene_configuration_plan` | update | OPERATIONAL_ADMINISTRATION; exactly one existing UI-managed scene by exact config id; update only; saving never activates the scene | signed:core.scene_configuration_operation (state: compiled-runtime-only; signed in registry seq 15 for Core 2026.10.3 only; dormant on 2026.10.0/.1; docs still say planned seq 16 - stale vs registry data; deployment unknown) | Edits name/icon/entities of one existing UI scene. NEVER_SENT includes scene.create, scene.apply, scene.delete, scene.reload, scene.turn_on and DELETE config; YAML/integration/disabled scenes refused. Verification scope is stored_configuration_and_registry_identity; loaded state not verified. | `routing.py:204`<br>`scene_configuration.py:75`<br>`scene_configuration.py:63`<br>`routes.py:164` |
| `apply_change_plan` | create, update, delete | requires exact-hash external approval; F3 dispatch by f3_requirements(); downstream authority acquires the profile per step | native (CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY); per-step core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | f3_requirements() adds core.governed_configuration_operation for automation/script/input_boolean/input_number create\|update; delete adds core.configuration_delete_operation; scene adds core.scene_configuration_operation; any other identity gets core.unsupported_f3_operation (no lease). Dormant profile -> pending_signed_registry_admission. | `routes.py:949`<br>`routes.py:35`<br>`governance.py:122` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-004 | Scene create (new scene) | new_signed_profile | HAMCP-071 Part 3: a governed scene create (new config id, absence check, exact readback, never activated). scene.create is never sent and the scene profile covers update only, so create needs a new signed profile or profile version. | HAMCP-071 |
| RCG-005 | Scene delete | new_signed_profile | A signed scene delete (rollback of a create, or a delete plan): core.configuration_delete_operation covers automation and script only. Register 071 records this as a pending signed delete-profile decision. | HAMCP-071 |
| RCG-006 | Stored scene configuration in dependency index (membership only) | feature_work | Read stored scene config instead of loaded attributes | HAMCP-227 |
| RCG-007 | Loaded (runtime) scene state is not verified after a save; verification covers the stored configuration and registry identity only. | feature_work | HAMCP-262: a post-save reload signal correlated to the save, if pinned Core offers one; otherwise 'not verified' stays truthful. | HAMCP-262 |

<a id="groups"></a>
#### Groups (YAML group: and group helpers) (`groups`)

Completeness: **partial**. YAML group: readable only if defined directly in configuration.yaml; group helpers and groups in packages/includes are not stored-config readable; no group write path (helper type set is input_boolean/input_number).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_groups` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_config_list_groups (automatic_read in stock policies 7.14.2 through 8.6.0); core.basic_websocket_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | Delegated paged list (limit/offset) of groups. UNCERTAIN: upstream semantics - believed to list service-created groups, not YAML group: definitions or group helper config entries; verify upstream source. | `upstream_tool_policy_8_6_0.json:1`<br>`upstream_reads_8_6.py:18`<br>`routes.py:196` |
| `internal:dependency_group_membership` | direct_ha_api | global_sources membership from loaded state entity_id attributes (HAMCP-227) | native | partial | Old-style group.* members come from loaded state attribute entity_id (runtime membership); group helpers appear via state too. MAX_GROUP_SOURCES=1000. | `global_sources.py:37`<br>`global_sources.py:46` |
| `get_configuration_definitions` | engineering (admitted YAML reader + direct_ha_api) | configuration_definitions_read_v1; READER_PURPOSE configuration_definitions -> YAML_READER_PURPOSES (configuration_definitions_yaml_read) | native (ENGINEERING_NATIVE) over upstream:ha_config_get_yaml (8.4.3/8.5.0 admitted, 8.6.0 held) + config/entity_registry/list, entity/source, config_entries/get (signed core.basic_rest_read/basic_websocket_read/non_device_registry_read; compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | Projects group: YAML definitions at the fixed configuration.yaml path group (and group config entries only as 'definition_not_readable_without_flow'); group is one of 12 INTEGRATIONS. UI group helper options unreadable (config_entries/get omits data/options). | `routing.py:300`<br>`upstream_read_gateway.py:803`<br>`contracts.py:44` |
| `ha_config_get_yaml` | upstream_read_gateway (special adapter) | ha-mcp-{version}-yaml-fragment-read-v1; ALLOWED_FILE=configuration.yaml | upstream:ha_config_get_yaml (optional tool; ha-mcp 8.4.3 and 8.5.0 admitted, 8.6.0 held; needs ha_mcp_tools component + filesystem tools flag); core.basic_rest_read + core.basic_websocket_read | partial | Reads ONE dotted mapping-key path (<=8 segments, no list indices) of configuration.yaml only. All other files incl. secrets.yaml and packages/ refused before any upstream call. !include returned as text, never followed. !secret/!env_var -> {secret_ref/env_var_ref} markers, values never returned. Redaction is best-effort (capabilities matrix: best_effort_redaction_partial_disclosed). | `upstream_yaml_reader.py:63`<br>`upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:219`<br>`routes.py:194` |
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_config_list_helpers; core.basic_websocket_read | partial | Overlap with the helper research group: lists helper entities (includes group helpers). See research_helpers output for completeness. | `upstream_reads_8_6.py:19`<br>`routes.py:197` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_configuration_plan` | create, update | RISK_ASSESSMENT; <=8 operations, resource_type automation\|script\|helper(input_boolean,input_number); actions create\|update only; non-atomic, stop on first failure, no auto rollback | native; planning core.dependency_helper_planning (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)); apply F3 core.governed_configuration_operation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) + core.f3_mutation_verification (compiled-active, seq 15) | Generic governed configuration path: closed resource set {automation, script, helper(input_boolean\|input_number)} x {create, update}. Delete and scene edit are NOT covered by this profile; they need their own signed profiles. Group YAML, template YAML, scene create, blueprint files, packages are not resource types. | `routing.py:181`<br>`service.py:446`<br>`service.py:448`<br>`profiles.py:388`<br>`strategies.py:48` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-008 | Group helper (config entry) stored options | new_signed_profile | First an owner decision (HAMCP-232 OD-1: the options baseline is never a general read); if reversed, a new signed read-only options-flow profile (open, read suggested values, abort; no submit reachable). See helpers_options_supported. | HAMCP-232 |
| RCG-009 | Group YAML in packages/ or !include files | new_reader_contract | As RCG-016: The YAML adapter fixes the file to configuration.yaml, its L2 deny floor includes packages, and it returns !include as text without following it; a reader purpose only selects dotted paths inside that adapter, so a purpose alone reaches none of these files. Closure needs an explicitly reviewed extension or a new bounded file-reader contract (fixed allowlist, size bounds, !secret never resolved) with any adapter and admission work, then an audited purpose. No Core profile is implied. | HAMCP-264 |
| RCG-010 | No group create/update/delete write path | new_signed_profile | ha_config_set_group/remove_group are persistent_write upstream and unadmitted; a governed group operation would need a new profile (overlap with helper research) | - |

<a id="blueprints"></a>
#### Blueprints (`blueprints`)

Completeness: **partial**. Source and inventory readable for automation/script domains; no write path (no governed blueprint file operation); dependency index resolves automation blueprints only.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_blueprint` | engineering (mounted read-only config dir) | bounded_blueprint_source_read (DIRECT_HA_READ_POLICIES) | native; CORE_INDEPENDENT_STATIC_TOOLS basis mounted_read_only_source (no Core API call) | partial | Raw YAML of one blueprint from mounted /homeassistant/blueprints or /config/blueprints; realpath-confined, .yaml/.yml only. UNCERTAIN: no secret redaction visible in this function. Delivery is truncated at MAX_CHARS (60,000) characters with a marker and no continuation argument, so the read is complete only below that bound. | `routing.py:392`<br>`compatibility.py:683`<br>`routes.py:341`<br>`compatibility.py:708` |
| `list_blueprints` | direct_ha_api | bounded_blueprint_inventory_read (DIRECT_HA_READ_POLICIES) | native (TRANSITIONAL_DIRECT); core.basic_websocket_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | WebSocket blueprint/list for domain automation or script (inventory metadata, no source). Delivery is truncated at MAX_CHARS (60,000) characters with a marker and no continuation argument, so the read is complete only below that bound. | `routing.py:458`<br>`compatibility.py:672`<br>`routes.py:265`<br>`compatibility.py:87` |
| `ha_get_blueprint` | upstream_read_gateway (special adapter) | ha-mcp-8.6.0-blueprint-list-get-v1 (exposed name for upstream ha_manage_blueprints) | upstream:ha_manage_blueprints (mixed_or_requires_wrapper, present from policy 8.5.0; 8.5.0 adapter ha-mcp-8.5.0-blueprint-list-get-v1; 8.6.0 adapter ha-mcp-8.6.0-blueprint-list-get-v1); core.basic_websocket_read (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | complete | Only two constructed argument shapes (list by domain / get by path) reach upstream; mixed upstream tool never public. | `upstream_blueprint.py:13`<br>`upstream_tool_policy_8_6_0.json:1`<br>`routes.py:200` |
| `internal:dependency_blueprint_resolution` | engineering (mounted blueprint reader) | dependency index _read_blueprint_with_status; BLUEPRINT_SOURCE capability | native; bounds MAX_BLUEPRINT_SOURCE_BYTES=1,048,576, MAX_BLUEPRINT_RESOLUTION_NODES=10,000 | partial | Automation use_blueprint paths are resolved to blueprint source for dependency roles. Script blueprint bodies unresolved (script_call_blueprint_body_unresolved). | `provider.py:1284`<br>`script_calls.py:202` |
| `check_config` | direct_ha_api | configuration_validation_read (DIRECT_HA_READ_POLICIES) | native; core.configuration_validation (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | complete | POST /config/core/check_config: validates the whole on-disk configuration (YAML incl. packages/templates/groups) and returns pass/fail plus error text; does not return file content. Validity signal for YAML sources otherwise unreadable. | `routing.py:398`<br>`compatibility.py:739`<br>`routes.py:250` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-011 | Script blueprint body resolution in dependency analysis | feature_work | Extend blueprint resolution to scripts. Unassigned candidate: HAMCP-264's scope names YAML, template, package and stored-script reads, not blueprint-body expansion. | - |
| RCG-012 | Blueprint write/import | owner_decision | No blueprint write path exists or is planned in source; decide if required | - |

### YAML, packages, secrets and restore state

<a id="yaml-templates"></a>
#### YAML templates (`yaml_templates`)

Completeness: **partial**. get_configuration_definitions can project template: from configuration.yaml but the dependency index reports template rows unavailable (HAMCP-264); no reader for templates in packages/includes; UI template helpers unreadable without an options flow.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_configuration_definitions` | engineering (admitted YAML reader + direct_ha_api) | configuration_definitions_read_v1; READER_PURPOSE configuration_definitions -> YAML_READER_PURPOSES (configuration_definitions_yaml_read) | native (ENGINEERING_NATIVE) over upstream:ha_config_get_yaml (8.4.3/8.5.0 admitted, 8.6.0 held) + config/entity_registry/list, entity/source, config_entries/get (signed core.basic_rest_read/basic_websocket_read/non_device_registry_read; compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | template is one of 12 INTEGRATIONS: fixed configuration.yaml path 'template' (and sensor/binary_sensor platform lists) projected through a positive per-key allowlist: partial because credential text withheld, packages/includes not followed, UI template helpers reported as definition_not_readable_without_flow. UNCERTAIN: whether trigger-based template blocks are projected in full (schema allowlist not inspected). | `routing.py:300`<br>`upstream_read_gateway.py:803`<br>`contracts.py:44` |
| `ha_config_get_yaml` | upstream_read_gateway (special adapter) | ha-mcp-{version}-yaml-fragment-read-v1; ALLOWED_FILE=configuration.yaml | upstream:ha_config_get_yaml (optional tool; ha-mcp 8.4.3 and 8.5.0 admitted, 8.6.0 held; needs ha_mcp_tools component + filesystem tools flag); core.basic_rest_read + core.basic_websocket_read | partial | Reads ONE dotted mapping-key path (<=8 segments, no list indices) of configuration.yaml only. All other files incl. secrets.yaml and packages/ refused before any upstream call. !include returned as text, never followed. !secret/!env_var -> {secret_ref/env_var_ref} markers, values never returned. Redaction is best-effort (capabilities matrix: best_effort_redaction_partial_disclosed). | `upstream_yaml_reader.py:63`<br>`upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:219`<br>`routes.py:194` |
| `render_template` | direct_ha_api | bounded_template_render_read (DIRECT_HA_READ_POLICIES) | native; core.template_semantics (compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | Renders a caller-supplied Jinja template against live state; evaluates but does not read stored template: YAML/helper configuration. | `routing.py:374`<br>`routes.py:247` |
| `internal:dependency_template_row` | engineering | global_sources template_row: deferred (HAMCP-264); no template configuration read; no fallback | native; reports unavailable | unavailable | Template source row is always unavailable with TEMPLATE_DEFERRED_WARNING; only the template-platform registry entry count is reported as a lower bound. | `global_sources.py:138`<br>`global_sources.py:54` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_helper_options_plan` | update | options-flow edit for config-entry helper domains filter/statistics/template (template types binary_sensor and sensor) | signed:core.config_entry_helper_options_operation (state: compiled-runtime-only; signed seq 15 for Core 2026.10.3; deployment unknown) | Edits UI template helper options only. YAML template: blocks have no write path. See helper research group. | `profiles.py:631` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-013 | Template reference extraction (which entities template sensors reference) in the dependency index | new_reader_purpose | Template references in configuration.yaml: an audited reader purpose that reuses the existing adapter for fixed configuration.yaml paths (own audit event, bounded paths and sizes, !secret never resolved). Templates in packages and !include files need the reader contract in RCG-014 and RCG-016. | HAMCP-264 |
| RCG-014 | template: in packages/ and !include files | new_reader_contract | As RCG-016: The YAML adapter fixes the file to configuration.yaml, its L2 deny floor includes packages, and it returns !include as text without following it; a reader purpose only selects dotted paths inside that adapter, so a purpose alone reaches none of these files. Closure needs an explicitly reviewed extension or a new bounded file-reader contract (fixed allowlist, size bounds, !secret never resolved) with any adapter and admission work, then an audited purpose. No Core profile is implied. | HAMCP-264 |
| RCG-015 | No write path for YAML template definitions | owner_decision | Writing YAML files is outside governed resource types | - |

<a id="packages-and-yaml-files"></a>
#### Packages, configuration.yaml paths and !include files (`packages_and_yaml_files`)

Completeness: **partial**. Only fixed dotted paths of configuration.yaml; every other YAML file (packages/, includes, secrets.yaml, .storage, custom_components, blueprints) is in the L2 deny floor. No write path for any YAML file.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_yaml` | upstream_read_gateway (special adapter) | ha-mcp-{version}-yaml-fragment-read-v1; ALLOWED_FILE=configuration.yaml | upstream:ha_config_get_yaml (optional tool; ha-mcp 8.4.3 and 8.5.0 admitted, 8.6.0 held; needs ha_mcp_tools component + filesystem tools flag); core.basic_rest_read + core.basic_websocket_read | partial | Reads ONE dotted mapping-key path (<=8 segments, no list indices) of configuration.yaml only. All other files incl. secrets.yaml and packages/ refused before any upstream call. !include returned as text, never followed. !secret/!env_var -> {secret_ref/env_var_ref} markers, values never returned. Redaction is best-effort (capabilities matrix: best_effort_redaction_partial_disclosed). | `upstream_yaml_reader.py:63`<br>`upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:219`<br>`routes.py:194` |
| `get_configuration_definitions` | engineering (admitted YAML reader + direct_ha_api) | configuration_definitions_read_v1; READER_PURPOSE configuration_definitions -> YAML_READER_PURPOSES (configuration_definitions_yaml_read) | native (ENGINEERING_NATIVE) over upstream:ha_config_get_yaml (8.4.3/8.5.0 admitted, 8.6.0 held) + config_entries/get, entity registry (signed core.basic_rest_read/basic_websocket_read/non_device_registry_read; compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | Reads homeassistant.packages and homeassistant only to classify: packages state absent\|present\|unreadable; present packages yield definitions_in_packages_not_read gap. Package contents are disclosed but never followed or read. | `routing.py:300`<br>`upstream_read_gateway.py:803`<br>`contracts.py:44` |
| `internal:recorder_settings_yaml_reader` | upstream_read_gateway | YAML_READER_PURPOSES recorder_settings (recorder_settings_yaml_read); get_recorder_settings [yaml_reader:recorder_settings, gateway_reader:yaml_config_reader] | upstream:ha_config_get_yaml (8.4.3/8.5.0); core.basic_rest_read + basic_websocket_read | partial | Fixed recorder paths of configuration.yaml only (YAML_CONFIG_PATHS); separate purpose so it cannot read configuration-definition paths. | `upstream_read_gateway.py:804`<br>`upstream_read_gateway.py:810` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-016 | packages/ directory contents and !include targets | new_reader_contract | The YAML adapter fixes the file to configuration.yaml, its L2 deny floor includes packages, and it returns !include as text without following it; a reader purpose only selects dotted paths inside that adapter, so a purpose alone reaches none of these files. Closure needs an explicitly reviewed extension or a new bounded file-reader contract (fixed allowlist, size bounds, !secret never resolved) with any adapter and admission work, then an audited purpose. No Core profile is implied. | HAMCP-264 |
| RCG-017 | ha_config_get_yaml at ha-mcp 8.6.0 (held) | upstream_admission | Admit the 8.6.0 yaml-fragment-read adapter; today 8.4.3 and 8.5.0 only | HAMCP-156 |
| RCG-018 | Writing configuration.yaml or any YAML file | owner_decision | No governed YAML-file write; resource types exclude files | - |

<a id="secrets"></a>
#### secrets.yaml (presence only) (`secrets`)

Completeness: **unavailable**. secrets.yaml is in the L2 deny floor; Engineering can learn only the NAMES of secrets referenced from configuration.yaml via {secret_ref: name} markers (not whether the secret exists in secrets.yaml, not values). check_config failure text may indirectly reveal a missing secret (UNCERTAIN, not verified).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_yaml` | upstream_read_gateway (special adapter) | ha-mcp-{version}-yaml-fragment-read-v1; ALLOWED_FILE=configuration.yaml | upstream:ha_config_get_yaml (optional tool; ha-mcp 8.4.3 and 8.5.0 admitted, 8.6.0 held; needs ha_mcp_tools component + filesystem tools flag); core.basic_rest_read + core.basic_websocket_read | partial | Reads ONE dotted mapping-key path (<=8 segments, no list indices) of configuration.yaml only. All other files incl. secrets.yaml and packages/ refused before any upstream call. !include returned as text, never followed. !secret/!env_var -> {secret_ref/env_var_ref} markers, values never returned. Redaction is best-effort (capabilities matrix: best_effort_redaction_partial_disclosed). | `upstream_yaml_reader.py:63`<br>`upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:219`<br>`routes.py:194` |
| `get_configuration_definitions` | engineering (admitted YAML reader + direct_ha_api) | configuration_definitions_read_v1; READER_PURPOSE configuration_definitions -> YAML_READER_PURPOSES (configuration_definitions_yaml_read) | native (ENGINEERING_NATIVE) over upstream:ha_config_get_yaml (8.4.3/8.5.0 admitted, 8.6.0 held) + config_entries/get (signed core.basic_rest_read/basic_websocket_read/non_device_registry_read; compiled-active; signed in registry seq 15 for Core 2026.10.0/.1/.3 (deployed registry state unknown)) | partial | db_url withheld in every position (sql db_url fixed policy redaction); credential text withheld and the report marked partial. known_secrets passed in are used to scrub output. | `routing.py:300`<br>`upstream_read_gateway.py:803`<br>`contracts.py:44` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-019 | Whether secrets.yaml exists or defines a referenced name | owner_decision | Presence-only reader would be a new reader purpose and a policy decision; current design deliberately refuses secrets.yaml | - |

<a id="restore-state"></a>
#### .storage/core.restore_state (`restore_state`)

Completeness: **unavailable**. No path reads it: grep of hass_mcp_engineering_beta/ha_mcp_engineering finds no restore_state reference; .storage is in the ha_config_get_yaml L2 deny floor, and configuration_definitions states no .storage file is reachable. Restored state is visible only indirectly via GET /states.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| none | - | - | - | unavailable | - | - |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-020 | Stored restore state file | impossible_via_ha_api | Core has no REST/WebSocket command returning core.restore_state; would need filesystem access (.storage denied) or a custom component (component use ruled out by owner decision 2026-10-21) | - |

### UI helpers

<a id="helpers-input-boolean-number"></a>
#### input_boolean and input_number helpers (`helpers_input_boolean_number`)

Completeness: **partial**. Storage definitions readable through the delegated helper list and the governed plan baseline; YAML-defined definitions only through the held YAML reader. Governed create and update exist; delete does not.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | complete | Storage-collection (input_boolean/list, UI/API-created records only; YAML-defined entries are NOT listed). Returns id, entity_id, name, icon and type-specific settings; limit<=500, offset. settings: icon, initial, name. UI-created: complete stored config. Not Engineering-native; depends on admitted ha-mcp version. (Researched for: input_boolean, input_number.) | `upstream_reads_8_6.py:154`<br>`routes.py:197` |
| `internal:ConfigurationResourceGateway.get` | direct_ha_api (websocket) | governed configuration plan baseline / pre-image (f3 configuration strategy) | signed:core.governed_configuration_operation (compiled profile; per-Core-version signed presence not verified in this pass) | complete | Sends input_boolean/list and matches one record by id for plan baseline and structured diff. Internal only: no standalone client tool; the plan output discloses only the proposed/diff fields. (Researched for: input_boolean, input_number.) | `resources.py:753`<br>`resources.py:89` |
| `ha_config_get_yaml` | upstream_read_gateway (optional adapter) | ha-mcp-{version}-yaml-fragment-read-v1; file fixed to configuration.yaml; dotted mapping-key path <=8 segments; credential-shaped keys refused | upstream:ha_config_get_yaml; OPTIONAL_TOOL_DISPOSITIONS admitted 8.4.3/8.5.0, held 7.14.x/8.0.0-8.2.0/8.4.1/8.6.0 (so NOT reachable on 8.6.0); also depends on ha_mcp_tools custom component | partial | YAML-defined input_boolean: any dotted path in configuration.yaml (e.g. input_boolean). Best-effort redaction (partial disclosed); packages and !include content are not covered. (Researched for: input_boolean, input_number.) | `upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:1817`<br>`upstream_yaml_reader.py:1827` |
| `get_entity` | direct_ha_api | DIRECT_HA_READ_POLICIES exact entity state | native | partial | Current state and attributes of an exact input_boolean.* entity (runtime state, not the stored definition; attribute set is what the integration exposes). (Researched for: input_boolean, input_number.) | `capabilities.py:1114` |
| `search_entities` | direct_ha_api | BROAD_ENTITY_SEARCH bounded state search | native | partial | Bounded search of states by domain/name to enumerate input_boolean.* entities; state only. (Researched for: input_boolean, input_number.) | `capabilities.py:1103` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_configuration_plan` | create, update | governed_configuration_operation: immutable plan, exact-hash external approval, ordered governed apply, exact read-back; create preceded by name-subset check, GET /states absence check, generated-id identity match | signed:core.governed_configuration_operation (compiled profile) | operations[].resource_type=helper, helper_type=input_boolean; actions limited to create/update (SUPPORTED_ACTIONS). Rollback not supported for forward F3-C1 capabilities. Delete not available. (Researched for: input_boolean, input_number.) | `capabilities.py:662`<br>`governance.py:186`<br>`strategies.py:46`<br>`strategies.py:53`<br>`resources.py:882`<br>`resources.py:990`<br>`profiles.py:388` |
| `create_helper_state_plan` | state | governed_exact_input_boolean_state_proposal (on/off only; exact read-back; no redispatch) | native direct_home_assistant_state; operation set_exact_input_boolean_state (no core_capabilities listed for the tool); F3 verification under Core profile core.typed_helper_operation | Sets entity state on/off (ChangeOperation.SET_INPUT_BOOLEAN_STATE); not a definition edit. (Researched for: input_boolean, input_number.) | `capabilities.py:304`<br>`helper_state.py:21`<br>`routes.py:33`<br>`routes.py:33` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-021 | YAML-defined input_boolean and input_number definitions: not in the configuration_definitions YAML paths; only ha_config_get_yaml could read them, and it is held on ha-mcp 8.6.0 (admitted on 8.4.3 and 8.5.0 only). | new_reader_purpose | Add the helper domains to the configuration_definitions reader purpose (or a new purpose) with a pinned per-type key allowlist and its own audit coverage, and admit the YAML reader on the observed ha-mcp version; or record YAML helpers as out of scope. | HAMCP-230 |
| RCG-022 | Storage-collection input_boolean and input_number definitions are readable only through the delegated ha_config_list_helpers; get_configuration_definitions excludes storage helper domains. | feature_work | Either accept ha_config_list_helpers as the read contract with a per-type acceptance fixture, or add a native bounded {type}/list reader. Exposure is out of scope for HAMCP-265 (no runtime change). | HAMCP-230 |
| RCG-023 | No governed delete for input_boolean and input_number definitions: create_configuration_delete_plan accepts automation and script only. | new_signed_profile | Owner scope decision, then a helper delete strategy and a new signed Core profile (or a new version of core.configuration_delete_operation) with pre-image capture, consumer disclosure, recreate rollback and negative tests. | HAMCP-155 |
| RCG-024 | YAML-defined input_boolean and input_number cannot be written. | impossible_via_ha_api | Home Assistant has no API that edits configuration.yaml helpers; only a file-edit capability (owner decision, new security architecture) could do it. | HAMCP-230 |

<a id="helpers-input-select"></a>
#### input_select helpers (`helpers_input_select`)

Completeness: **partial**. Storage definition readable through the delegated helper list only; the only write is selecting an existing option (state), and set_options is never sent.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | complete | Storage-collection (input_select/list, UI/API-created records only; YAML-defined entries are NOT listed). Returns id, entity_id, name, icon and type-specific settings; limit<=500, offset. Settings include the options list and initial. UI-created: complete stored config. | `upstream_reads_8_6.py:154`<br>`routes.py:197` |
| `ha_config_get_yaml` | upstream_read_gateway (optional adapter) | ha-mcp-{version}-yaml-fragment-read-v1; file fixed to configuration.yaml; dotted mapping-key path <=8 segments; credential-shaped keys refused | upstream:ha_config_get_yaml; OPTIONAL_TOOL_DISPOSITIONS admitted 8.4.3/8.5.0, held 7.14.x/8.0.0-8.2.0/8.4.1/8.6.0 (so NOT reachable on 8.6.0); also depends on ha_mcp_tools custom component | partial | YAML-defined input_select. Best-effort redaction (partial disclosed); packages and !include content are not covered. | `upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:1817`<br>`upstream_yaml_reader.py:1827` |
| `get_entity` | direct_ha_api | DIRECT_HA_READ_POLICIES exact entity state | native | partial | Current state and attributes of an exact input_select.* entity (runtime state, not the stored definition; attribute set is what the integration exposes). | `capabilities.py:1114` |
| `search_entities` | direct_ha_api | BROAD_ENTITY_SEARCH bounded state search | native | partial | Bounded search of states by domain/name to enumerate input_select.* entities; state only. | `capabilities.py:1103` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_input_select_option_plan` | state | core.typed_input_select_option_operation: one input_select.select_option call, exact state read-back (options attribute checked) | signed:core.typed_input_select_option_operation (compiled; dormant until signed registry reference; signed in seq 15 for Core 2026.10.3 ONLY, not .0/.1) | Selects an existing option (state change). NEVER_SENT_SERVICES includes set_options, reload, select_first/last/next/previous. No create, no definition/options edit, no delete. | `capabilities.py:555`<br>`input_select_option.py:35`<br>`input_select_option.py:44`<br>`profiles.py:673`<br>`README.md:3` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-025 | No governed CREATE or definition UPDATE for input_select; governed helper create exists only for input_boolean and input_number. | new_signed_profile | Owner scope decision (helper-create candidate), then per-type create/update strategies with pre-create absence checks and exact readback, a new version of core.governed_configuration_operation covering the new {type}/create\|update commands, and signing for the target Core release. | HAMCP-266 |
| RCG-026 | Option plans are prohibited even for an unreferenced helper (helper dependency-risk coverage failure). | feature_work | Bound helper relevance by evidence that a consumer could reference the entity, without weakening protection for referenced helpers (fix lane). | HAMCP-274 |
| RCG-027 | YAML-defined input_select definitions: not in the configuration_definitions YAML paths; only ha_config_get_yaml could read them, and it is held on ha-mcp 8.6.0 (admitted on 8.4.3 and 8.5.0 only). | new_reader_purpose | Add the helper domains to the configuration_definitions reader purpose (or a new purpose) with a pinned per-type key allowlist and its own audit coverage, and admit the YAML reader on the observed ha-mcp version; or record YAML helpers as out of scope. | HAMCP-230 |
| RCG-028 | Storage-collection input_select definitions are readable only through the delegated ha_config_list_helpers; get_configuration_definitions excludes storage helper domains. | feature_work | Either accept ha_config_list_helpers as the read contract with a per-type acceptance fixture, or add a native bounded {type}/list reader. Exposure is out of scope for HAMCP-265 (no runtime change). | HAMCP-230 |
| RCG-029 | No governed delete for input_select definitions: create_configuration_delete_plan accepts automation and script only. | new_signed_profile | Owner scope decision, then a helper delete strategy and a new signed Core profile (or a new version of core.configuration_delete_operation) with pre-image capture, consumer disclosure, recreate rollback and negative tests. | HAMCP-155 |

<a id="helpers-storage-other"></a>
#### Other storage-collection helpers (`helpers_storage_other`)

Completeness: **partial**. Definitions readable through the delegated helper list only; no write path of any kind.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | complete | Storage-collection (input_text/list, UI/API-created records only; YAML-defined entries are NOT listed). Returns id, entity_id, name, icon and type-specific settings; limit<=500, offset. Typical settings: min, max, mode, pattern, initial (upstream-defined; exact field set not re-verified against the live tool). UI-created: stored config; YAML-defined not listed. (Researched for: input_text, input_datetime, input_button, counter, timer, schedule.) | `upstream_reads_8_6.py:154`<br>`routes.py:197` |
| `ha_config_get_yaml` | upstream_read_gateway (optional adapter) | ha-mcp-{version}-yaml-fragment-read-v1; file fixed to configuration.yaml; dotted mapping-key path <=8 segments; credential-shaped keys refused | upstream:ha_config_get_yaml; OPTIONAL_TOOL_DISPOSITIONS admitted 8.4.3/8.5.0, held 7.14.x/8.0.0-8.2.0/8.4.1/8.6.0 (so NOT reachable on 8.6.0); also depends on ha_mcp_tools custom component | partial | YAML-defined input_text. Best-effort redaction (partial disclosed); packages and !include content are not covered. (Researched for: input_text, input_datetime, input_button, counter, timer, schedule.) | `upstream_yaml_reader.py:71`<br>`upstream_yaml_reader.py:1817`<br>`upstream_yaml_reader.py:1827` |
| `get_entity` | direct_ha_api | DIRECT_HA_READ_POLICIES exact entity state | native | partial | Current state and attributes of an exact input_text.* entity (runtime state, not the stored definition; attribute set is what the integration exposes). (Researched for: input_text, input_datetime, input_button, counter, timer, schedule.) | `capabilities.py:1114` |
| `search_entities` | direct_ha_api | BROAD_ENTITY_SEARCH bounded state search | native | partial | Bounded search of states by domain/name to enumerate input_text.* entities; state only. (Researched for: input_text, input_datetime, input_button, counter, timer, schedule.) | `capabilities.py:1103` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-030 | No governed CREATE or definition UPDATE for input_text, input_datetime, input_button, counter, timer and schedule; governed helper create exists only for input_boolean and input_number. | new_signed_profile | Owner scope decision (helper-create candidate), then per-type create/update strategies with pre-create absence checks and exact readback, a new version of core.governed_configuration_operation covering the new {type}/create\|update commands, and signing for the target Core release. | HAMCP-266 |
| RCG-031 | YAML-defined input_text, input_datetime, input_button, counter, timer and schedule definitions: not in the configuration_definitions YAML paths; only ha_config_get_yaml could read them, and it is held on ha-mcp 8.6.0 (admitted on 8.4.3 and 8.5.0 only). | new_reader_purpose | Add the helper domains to the configuration_definitions reader purpose (or a new purpose) with a pinned per-type key allowlist and its own audit coverage, and admit the YAML reader on the observed ha-mcp version; or record YAML helpers as out of scope. | HAMCP-230 |
| RCG-032 | Storage-collection input_text, input_datetime, input_button, counter, timer and schedule definitions are readable only through the delegated ha_config_list_helpers; get_configuration_definitions excludes storage helper domains. | feature_work | Either accept ha_config_list_helpers as the read contract with a per-type acceptance fixture, or add a native bounded {type}/list reader. Exposure is out of scope for HAMCP-265 (no runtime change). | HAMCP-230 |
| RCG-033 | No governed delete for input_text, input_datetime, input_button, counter, timer and schedule definitions: create_configuration_delete_plan accepts automation and script only. | new_signed_profile | Owner scope decision, then a helper delete strategy and a new signed Core profile (or a new version of core.configuration_delete_operation) with pre-image capture, consumer disclosure, recreate rollback and negative tests. | HAMCP-155 |

<a id="helpers-options-supported"></a>
#### template, filter and statistics helpers (UI) (`helpers_options_supported`)

Completeness: **partial**. Three distinct reads. Native: get_configuration_definitions reads YAML definitions from configuration.yaml and only Core entry metadata for UI helpers. Delegated: ha_config_list_helpers can return ConfigEntry.options where the reviewed ha-mcp version and its component path support it (filter verified; template and statistics unverified). Governed planning: the options-flow baseline inside create_helper_options_plan, restricted to planning by HAMCP-232 OD-1. Options update is governed for the catalogued steps.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_configuration_definitions` | direct_ha_api + upstream_read_gateway | configuration_definitions_read_v1 / reader purpose configuration_definitions (closed INTEGRATIONS) [yaml_reader:configuration_definitions, gateway_reader:yaml_config_reader] | native (YAML half via upstream ha_config_get_yaml adapter: admitted 8.4.3/8.5.0, held 8.6.0, see ref) | partial | template: YAML entries (template: sensor/binary_sensor lists, legacy platform presence only) reported with TEMPLATE_ENTITY_KEYS (sensor, binary_sensor) allowlists. UI/config-entry half: entry metadata only (entity ids, count), readability=definition_not_readable_without_flow, capped at MAX_UI_ENTRIES=50; options/data never returned. YAML half: allowlisted keys from configuration.yaml paths in DEFINITION_YAML_PATHS only, capped MAX_DEFINITIONS=200. Other template entity types are not projected. (Researched for: template, filter, statistics.) | `capabilities.py:189`<br>`contracts.py:48`<br>`contracts.py:154`<br>`matching.py:125`<br>`upstream_read_gateway.py:796`<br>`upstream_yaml_reader.py:1827`<br>`schemas.py:49`<br>`schemas.py:95` |
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | partial | helper_type=<flow type> lists UI config-entry helpers through ha-mcp's component helper-list path. Requires the ha_mcp_tools custom component (>=1.1.0); otherwise COMPONENT_NOT_INSTALLED maps to capability_unavailable, and its install state is unknown from source. At the reviewed ha-mcp 8.5.0 source (311d6dc273fb4e9a5b8cde0de15f69472a64fe44, src/ha_mcp/tools/tools_config_helpers.py:3705-3728, records obtained through the component helper-list path from :4072), each flow-helper record preserves ConfigEntry.options; the 8.5.0 policy classifies the tool automatic_read without argument restrictions. ConfigEntry.options is not ConfigEntry.data and is not proof of a complete definition for every type. Engineering's own readers do not use component commands (owner decision 2026-10-21); this delegated read is a separate, conditional path. Options content: filter verified; template and statistics unverified; unverified means not yet evidenced, not impossible. Verified for filter: HA Admin observation recorded in HAMCP-230 (audit HAAC-E008, 2026-10-26), a UI-created filter helper returned its full options. (Researched for: template, filter, statistics.) | `upstream_reads_8_6.py:154`<br>`routes.py:197` |
| `internal:helper_options baseline (create_helper_options_plan prepare)` | direct_ha_api (options flow open, read suggested_value, abort) | core.config_entry_helper_options_operation (never exposed as a general read; OD-1 2026-10-26) | signed:core.config_entry_helper_options_operation (compiled; signed seq 15 for Core 2026.10.3 only) | partial | Reads the entry's current options only for the exact entries named in a plan; plan output shows baseline/proposed only for requested fields. template step catalog: sensor and binary_sensor only. (Researched for: template, filter, statistics.) | `helper_options.py:217`<br>`helper_options.py:256`<br>`profiles.py:631` |
| `get_entity` | direct_ha_api | DIRECT_HA_READ_POLICIES exact entity state | native | partial | Current state and attributes of the helper's own exact entity (for these helpers a sensor.* or binary_sensor.* entity, not a template.* domain); runtime state, not the stored definition. (Researched for: template, filter, statistics.) | `capabilities.py:1114` |
| `search_entities` | direct_ha_api | BROAD_ENTITY_SEARCH bounded state search | native | partial | Bounded state search by domain or name (sensor, binary_sensor and the other output domains); state alone does not identify the creating helper integration. (Researched for: template, filter, statistics.) | `capabilities.py:1103` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_helper_options_plan` | update | core.config_entry_helper_options_operation: one POST to options flow per plan entry; exact read-back; NEVER_SENT lists config-flow start, entry delete/reload, config_entries/update, disable, subentries, call_service | signed:core.config_entry_helper_options_operation (dormant until signed; signed seq 15 for Core 2026.10.3 only) | Updates options of existing UI template helpers of type sensor and binary_sensor. No create, no delete; other template types unsupported. (Researched for: template, filter, statistics.) | `capabilities.py:449`<br>`helper_options.py:53`<br>`helper_options.py:256`<br>`profiles.py:631`<br>`README.md:3` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-034 | No native or Engineering-native read of UI template, filter and statistics options: get_configuration_definitions reports definition_not_readable_without_flow (Core metadata only). The delegated ha_config_list_helpers list can return ConfigEntry.options where the reviewed ha-mcp version and component path support it: verified for filter (HAMCP-230), unverified for template and statistics. | feature_work | First, per-type source or fixture evidence of what the admitted delegated list returns and what stays absent, without opening or submitting an options flow. Only if a needed field is absent: an owner decision on HAMCP-232 OD-1 (the options baseline is never a general read) and, if reversed, a new signed read-only options profile (open, read suggested values, abort; no submit reachable; bounded redaction; own audit event; negative tests). The existing write-capable options profile must not be reused as a read path. This authorizes no new component or route. | HAMCP-230, HAMCP-232 |
| RCG-035 | Options apply is refused at dispatch_authority although core.config_entry_helper_options_operation is admitted on seq 15. | feature_work | Fix the F3 dispatch-authority binding so an approved plan dispatches exactly once with exact readback (fix lane, High). | HAMCP-276 |
| RCG-036 | No governed CREATE or definition UPDATE for template, filter and statistics (config-flow create); governed helper create exists only for input_boolean and input_number. | new_signed_profile | Owner scope decision (helper-create candidate), then per-type create/update strategies with pre-create absence checks and exact readback, a new version of core.governed_configuration_operation covering the new {type}/create\|update commands, and signing for the target Core release. | HAMCP-266 |
| RCG-037 | Template helper types other than sensor and binary_sensor are not in the options step catalog. | new_signed_profile | Extend the step catalog per template type with Core flow evidence; the profile's template types change, so a new profile version must be signed. | HAMCP-232 |
| RCG-038 | No governed delete of a UI template, filter or statistics helper (config-entry removal is never sent). | new_signed_profile | Owner scope decision, then a config-entry delete profile with pre-image capture and a recreate rollback. | - |
| RCG-039 | ha_config_list_helpers flow types need the ha_mcp_tools custom component; its availability is unverified, and options content is evidenced for filter only. | owner_decision | Record per-type, per-version evidence of what the delegated list returns (filter evidence exists in HAMCP-230). Whether the component becomes a supported dependency of Engineering's own readers is an owner decision (ruled out on 2026-10-21); nothing here changes that. | HAMCP-132, HAMCP-230 |

<a id="helpers-options-other"></a>
#### Other config-entry helpers (UI) (`helpers_options_other`)

Completeness: **partial**. Native: YAML definitions from configuration.yaml for the allowlisted platforms, and only Core entry metadata for UI helpers. Delegated: ha_config_list_helpers can return ConfigEntry.options where the reviewed ha-mcp version and its component path support it (unverified for these types). No options-flow baseline and no write path.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_configuration_definitions` | direct_ha_api + upstream_read_gateway | configuration_definitions_read_v1 / reader purpose configuration_definitions (closed INTEGRATIONS) [yaml_reader:configuration_definitions, gateway_reader:yaml_config_reader] | native (YAML half via upstream ha_config_get_yaml adapter: admitted 8.4.3/8.5.0, held 8.6.0, see ref) | partial | Derivative helper: derivative YAML platform entries under sensor:. UI/config-entry half: entry metadata only (entity ids, count), readability=definition_not_readable_without_flow, capped at MAX_UI_ENTRIES=50; options/data never returned. YAML half: allowlisted keys from configuration.yaml paths in DEFINITION_YAML_PATHS only, capped MAX_DEFINITIONS=200. derivative entries under sensor: platform list (Researched for: derivative, integration, min_max, history_stats, threshold, trend, utility_meter, sql, group.) | `capabilities.py:189`<br>`contracts.py:48`<br>`contracts.py:154`<br>`matching.py:125`<br>`upstream_read_gateway.py:796`<br>`upstream_yaml_reader.py:1827`<br>`schemas.py:61` |
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | partial | helper_type=<flow type> lists UI config-entry helpers through ha-mcp's component helper-list path. Requires the ha_mcp_tools custom component (>=1.1.0); otherwise COMPONENT_NOT_INSTALLED maps to capability_unavailable, and its install state is unknown from source. At the reviewed ha-mcp 8.5.0 source (311d6dc273fb4e9a5b8cde0de15f69472a64fe44, src/ha_mcp/tools/tools_config_helpers.py:3705-3728, records obtained through the component helper-list path from :4072), each flow-helper record preserves ConfigEntry.options; the 8.5.0 policy classifies the tool automatic_read without argument restrictions. ConfigEntry.options is not ConfigEntry.data and is not proof of a complete definition for every type. Engineering's own readers do not use component commands (owner decision 2026-10-21); this delegated read is a separate, conditional path. Options content: unverified for each of these types; unverified means not yet evidenced, not impossible. (Researched for: derivative, integration, min_max, history_stats, threshold, trend, utility_meter, sql, group.) | `upstream_reads_8_6.py:154`<br>`routes.py:197` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-040 | UI options of derivative, integration, min_max, history_stats, threshold, trend, utility_meter, sql and group helpers are not updatable. Native reads return entry metadata only; whether the delegated helper list returns their options is unverified. | new_signed_profile | Read: per-type evidence of the delegated list first. Update: owner scope decision, then step-catalog entries with Core flow evidence and a new version of the helper-options profile. A read-only options profile only if delegated reads prove insufficient and OD-1 is reversed (see RCG-034). | HAMCP-230, HAMCP-232 |
| RCG-041 | Group helpers other than sensor and binary_sensor groups (light, cover, fan, lock, media_player, switch) are not projected by the YAML allowlists. | feature_work | Extend the group schema allowlists per platform with Core evidence. | HAMCP-230 |
| RCG-042 | No governed CREATE or definition UPDATE for these config-entry helper types; governed helper create exists only for input_boolean and input_number. | new_signed_profile | Owner scope decision (helper-create candidate), then per-type create/update strategies with pre-create absence checks and exact readback, a new version of core.governed_configuration_operation covering the new {type}/create\|update commands, and signing for the target Core release. | - |

<a id="helpers-not-modelled"></a>
#### Helper types Engineering does not model (`helpers_not_modelled`)

Completeness: **partial**. switch_as_x, tod, random, generic_thermostat, generic_hygrostat and mold_indicator are outside Engineering's closed helper integration set, so no native read sees them. Only the delegated helper list names them, and it can return ConfigEntry.options where the reviewed ha-mcp version and its component path support it (unverified for these types). No write path.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_helpers` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (argument_restrictions, schema fingerprint 853fbabc); earlier policies 8a743a0f / c25b0b38 | upstream:ha_config_list_helpers automatic_read (7.14.x-8.6.0 policy JSONs); Core gating core.basic_websocket_read | partial | helper_type=<flow type> lists UI config-entry helpers through ha-mcp's component helper-list path. Requires the ha_mcp_tools custom component (>=1.1.0); otherwise COMPONENT_NOT_INSTALLED maps to capability_unavailable, and its install state is unknown from source. At the reviewed ha-mcp 8.5.0 source (311d6dc273fb4e9a5b8cde0de15f69472a64fe44, src/ha_mcp/tools/tools_config_helpers.py:3705-3728, records obtained through the component helper-list path from :4072), each flow-helper record preserves ConfigEntry.options; the 8.5.0 policy classifies the tool automatic_read without argument restrictions. ConfigEntry.options is not ConfigEntry.data and is not proof of a complete definition for every type. Engineering's own readers do not use component commands (owner decision 2026-10-21); this delegated read is a separate, conditional path. Options content: unverified for each of these types; unverified means not yet evidenced, not impossible. (Researched for: switch_as_x, tod, random, generic_thermostat, generic_hygrostat, mold_indicator.) | `upstream_reads_8_6.py:154`<br>`routes.py:197` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-043 | These helper domains are not in the closed INTEGRATIONS set, so get_configuration_definitions cannot see them. | feature_work | Add each domain to INTEGRATIONS and the schema allowlists with Core evidence, or document it as intentionally excluded. | HAMCP-230 |
| RCG-044 | UI options of these helpers are not updatable; whether the delegated helper list returns them is unverified. | new_signed_profile | As for the other config-entry helpers: per-type evidence of the delegated list first; for update, owner scope, step catalog and a signed profile version. | HAMCP-230, HAMCP-232 |

### Dashboards

<a id="dashboards-content"></a>
#### Dashboard content (storage-mode Lovelace configuration) (`dashboards_content`)

Completeness: **partial**. Metadata inventory is complete-or-truncated and small dashboards read exactly, but get_dashboard_config returns nothing above ~44 KB sanitized; only integrity analysis (findings only) can see larger dashboards. The same limit blocks governed update planning, readback and metadata planning fingerprints. The dependency index's own dashboard scan is bounded to 6 s and read 0 of 6 dashboards at live scale on beta.55 (RCG-093, HAMCP-277), so dashboard-only references were not established at that scale.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_dashboards` | upstream_dashboard (wraps ha-mcp ha_config_get_dashboard list_only=true, include_screenshot=false) | TOOL_CAPABILITY_POLICY DASHBOARD_INVENTORY; trust_profile ha_mcp_dashboard_read_v2 (reviewed_argument_constrained) | upstream:ha_config_get_dashboard mixed_or_requires_wrapper (no generic delegation) via Engineering-built arguments; signed:core.dashboard_configuration_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Bounded storage-mode dashboard metadata, limit 1-200; matrix completeness complete_or_explicitly_truncated. Metadata only (no config body). The dashboard-metadata planner caps at MAX_DASHBOARDS=500. YAML-mode dashboards: upstream docs say list_only lists YAML dashboards too, but whether Engineering's projection includes them was not verified. | `capabilities.py:635`<br>`upstream_dashboard.py:448`<br>`upstream_tool_policy_8_6_0.json:1`<br>`routes.py:266` |
| `get_dashboard_config` | upstream_dashboard (ha_config_get_dashboard by exact url_path) | DASHBOARD_CONFIGURATION_EVIDENCE; ha_mcp_dashboard_read_v2; dual hash (upstream-compatible config_hash + engineering_config_hash) | upstream:ha_config_get_dashboard wrapper; signed:core.dashboard_configuration_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Exact config body with hash verification, but refuses with response_too_large when len(json.dumps(sanitized, indent=2)) + RESPONSE_ENVELOPE_RESERVE(16_000) exceeds SETTINGS.response_size_limit (default 60_000), i.e. roughly >44 KB sanitized JSON. Error carries hashes and configuration_returned=False; no paging, streaming or partial return. Storage-mode only. | `capabilities.py:648`<br>`upstream_dashboard.py:539`<br>`upstream_dashboard.py:167`<br>`upstream_dashboard.py:612`<br>`configuration.py:42` |
| `dashboard_integrity_analysis` | engineering (upstream_dashboard.get_analysis_configuration, own read path) | ANALYTICAL_PROVIDER_POLICIES dashboard-integrity-v1 | native; signed:core.dashboard_configuration_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) plus core.basic_rest_read, core.non_device_registry_read | partial | Reads the dashboard through a separate larger budget (DASHBOARD_BYTES = 2 MiB, 32 KiB pages) so it can analyze dashboards that get_dashboard_config cannot return; output is findings, not the config body. Fails to a response_limit reason when the upstream response is too large. | `capabilities.py:95`<br>`upstream_dashboard.py:700`<br>`contracts.py:13`<br>`routes.py:224` |
| `ha_config_get_dashboard` | ha-mcp (internal provider tool; not client-exposed) | upstream_dashboard provider for list_dashboards, get_dashboard_config and dashboard analysis | ha-mcp required dashboard tool, reviewed per upstream release | partial | Backs the dashboard reads above; clients cannot call it directly. The same size limits apply (HAMCP-273). | `mcp.py:22` |
| `internal:dependency_dashboard_scan` | dependency_provider dashboard reader (one session, one list, then per-dashboard reads) | HAMCP-227 diagnostic reference store (dependency index build); not client-selectable | same upstream dashboard read admission as get_dashboard_config; started concurrently with the other dependency sources at scan start | partial | collect_dashboard_sources reads each listed dashboard once inside one DASHBOARD_SCAN_SECONDS = 6.0 budget covering session, list and reads; no read starts after the deadline, and unread dashboards are counted (dashboard_not_read, dashboard_scan_budget_exhausted). This bound is distinct from get_dashboard_config's response-size bound (RCG-045). Architect-reported live result (HAMCP-277, beta.55, 2026-11-01): 0 of 6 dashboards read, dashboard_not_read 6, dashboard_scan_budget_exhausted 1. The cause is not established from source (the collector starts its own deadline). | `global_sources.py:42`<br>`global_sources.py:368`<br>`global_sources.py:379`<br>`global_sources.py:502`<br>`provider.py:750` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_dashboard_update_plan` | update | bounded_existing_dashboard_update_non_atomic_v1 (F3 update_existing_dashboard) | native governed plan/approve/apply; reads via signed:core.dashboard_configuration_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | Existing storage-mode dashboard only, patch operations 1-16, MAX_PATCH_BYTES 16_384, raw and result config capped at 40_000 bytes, artifact 262_144. Preread uses list_dashboards(limit=200)+get_dashboard_config(force_reload=True), so dashboards over the 60k/44k read limit cannot be planned or read back. One non-retrying setter call; prepare_rollback returns None (no rollback). Dashboard create/delete and resource writes are not reachable. | `capabilities.py:681`<br>`governance.py:456`<br>`constants.py:32`<br>`constants.py:55`<br>`raw_evidence.py:113`<br>`gateway.py:25`<br>`adapter.py:763`<br>`upstream_dashboard.py:951` |
| `internal:ha_config_set_dashboard` | update | F3 dashboard provider behind create_dashboard_update_plan only | ha-mcp persistent_write, reachable only through the governed dashboard plan | Internal write tool; never client-exposed. | `provider.py:22` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-045 | Exact readback of large dashboards (config body >~44 KB; live example reported by owner) | feature_work | Bounded exact readback path (paged/streamed read with a verifiable full-config hash, or a reviewed larger cap) or a precise pre-dispatch refusal/disclosure so governed updates do not end unverified. | HAMCP-273 |
| RCG-046 | Dashboard update rollback | feature_work | prepare_rollback currently returns None for dashboard updates; a rollback needs a pre-image capture contract and verified restore. | - |
| RCG-047 | Dashboard create/delete and non-storage modes | owner_decision | Only existing storage-mode dashboard updates are reachable; lovelace/dashboards/create, lovelace/dashboards/delete are listed never_sent. Any addition is a new write path needing explicit scope and negative tests. | - |
| RCG-093 | Dashboard references in the dependency index at live scale. Architect-reported HAMCP-227 acceptance on beta.55 (2026-11-01): 0 of 6 dashboards read (dashboard_not_read 6, dashboard_scan_budget_exhausted 1, bound DASHBOARD_SCAN_SECONDS 6). Dashboard-only references were not established at that scale; the cause is not established. | feature_work | Establish the cause, then make the bounded scan read as many dashboards as truthfully fit and report the rest, without unbounding the scan or weakening counted partial reporting (HAMCP-277). Distinct from RCG-045 (large-body readback, HAMCP-273). | HAMCP-277 |

<a id="dashboard-metadata"></a>
#### Dashboard metadata (title/icon/visibility registry rows) (`dashboard_metadata`)

Completeness: **partial**. Metadata read is reachable via the compiled dashboards-list profile, but the write profile is runtime-only and the content-fingerprint dependency inherits the large-dashboard limit.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `internal:dashboard_metadata_planning.read_state (lovelace/dashboards/list)` | direct_ha_api WebSocket | core.dashboard_metadata_operation contract (LIST_COMMAND), planning-time read | signed:core.dashboard_metadata_operation dormant-until-signed: runtime-only profile (CORE_RUNTIME_CAPABILITY_PROFILES, not in CORE_CAPABILITY_PROFILES); signed in registry seq 15 for Core 2026.10.3 only (seq 13 and 14 do not carry it; 2026.10.0/2026.10.1 do not carry it); planning also needs core.dashboard_configuration_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | Lists dashboards (cap MAX_DASHBOARDS=500) and also requires the content fingerprint, taken from get_dashboard_config engineering_config_hash; the default 'lovelace' dashboard url_path is refused. A dashboard over the read limit yields the refusal dashboard_metadata_content_fingerprint_unavailable (inferred from code, not executed). | `dashboard_metadata.py:45`<br>`dashboard_metadata.py:93`<br>`dashboard_metadata_planning.py:246`<br>`runtime.py:43` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_dashboard_metadata_plan` | update | core.dashboard_metadata_operation (lovelace/dashboards/update only) | signed:core.dashboard_metadata_operation dormant-until-signed: runtime-only profile (CORE_RUNTIME_CAPABILITY_PROFILES, not in CORE_CAPABILITY_PROFILES); signed in registry seq 15 for Core 2026.10.3 only (seq 13 and 14 do not carry it; 2026.10.0/2026.10.1 do not carry it); until admitted the tool refuses with pending_signed_registry_admission | Only lovelace/dashboards/update. never_sent: lovelace/dashboards/create, lovelace/dashboards/delete, lovelace/config/save, lovelace/config/delete, resources create/update/delete, frontend/set_user_data, frontend/update_panel. YAML dashboards and resources out of scope (design doc). Whether the deployed Core is 2026.10.3 (the only signed release carrying it) is unknown. | `capabilities.py:482`<br>`dashboard_metadata.py:46`<br>`dashboard_metadata.py:66`<br>`profiles.py:747`<br>`ha-core-release-registry.json:1`<br>`routes.py:301` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-048 | Metadata plans for dashboards whose config exceeds the read limit | feature_work | Content fingerprint reader must work for large dashboards (same fix as HAMCP-273) or planner must accept a bounded alternative fingerprint. | HAMCP-273 |
| RCG-049 | Dashboard create/delete/resources writes | owner_decision | Deliberately never_sent in the dashboard metadata contract; needs a new signed profile and explicit owner scope if wanted. | - |

<a id="dashboard-resources"></a>
#### Dashboard resources (Lovelace resource list) (`dashboard_resources`)

Completeness: **partial**. Readable; writes unreachable by design.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_list_dashboard_resources` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_config_list_dashboard_resources 8.6.0 adapter ha-mcp-8.6.0-public-reads-v1; signed:core.dashboard_configuration_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Read-only list of resources via the generic delegated read adapter; per-result size and sanitization bounds of the gateway apply. Whether the 60k limit truncates or refuses was not verified. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:195`<br>`upstream_reads_8_6.py:17` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-050 | Dashboard resource create/update/delete | owner_decision | Upstream ha_config_set_dashboard_resource and ha_config_delete_dashboard_resource are persistent_write in the 8.6.0 policy; lovelace/resources/* are never_sent. A new governed write profile would be required. | - |

<a id="yaml-dashboards"></a>
#### YAML-mode dashboards (dashboard body in YAML files) (`yaml_dashboards`)

Completeness: **unavailable**. No reader for YAML dashboard bodies; no write path.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_yaml` | upstream_read_gateway (optional reviewed tool, upstream_yaml_reader) | hamcp162-yaml-fragment-read-arguments-v1; file=configuration.yaml only | upstream:ha_config_get_yaml optional binding for ha-mcp 8.4.3 and 8.5.0 only (adapter ha-mcp-{version}-yaml-fragment-read-v1); not present in 8.6.0 policy; core.basic_rest_read + core.basic_websocket_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | unavailable | Reads one dotted mapping-key path of configuration.yaml, redacted. Other files (dashboard YAML files, packages, secrets.yaml) are refused. A 'lovelace' key inside configuration.yaml may be readable in principle, but referenced dashboard YAML files are not. lovelace/dashboards/list may list YAML dashboards as metadata but the body is unreadable. Whether the reader is bound on the current 8.6.0 upstream is not established (no 8.6.0 entry in OPTIONAL_TOOL_BINDINGS captures). | `capabilities.py:1017`<br>`upstream_yaml_reader.py:60`<br>`upstream_yaml_reader.py:1796`<br>`upstream_yaml_reader.py:1764`<br>`upstream_read_gateway.py:789` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-051 | YAML dashboard body read (and any edit) | new_reader_purpose | A reviewed file-read purpose beyond configuration.yaml (path allowlist for dashboard YAML files referenced by lovelace.dashboards), with redaction and size bounds; writes would be a separate security architecture change (file writes). | - |
| RCG-052 | ha_config_get_yaml on current upstream 8.6.0 | upstream_admission | Add 8.6.0 capture and OPTIONAL_TOOL_BINDINGS entry (UNVERIFIED whether this is actually missing in the shipped runtime; bindings list only 8.4.3 and 8.5.0). | HAMCP-156 |

### Registries

<a id="entity-registry"></a>
#### Entity registry (metadata, availability, exposure) (`entity_registry`)

Completeness: **partial**. Direct list is a slim projection; full per-entity registry detail needs ha_get_entity. Mutation profiles are runtime-only and only signed for Core 2026.10.x.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_entity_registry` | direct_ha_api (config/entity_registry/list WebSocket) | DIRECT_HA_READ_POLICIES bounded_entity_registry_read | transitional_direct exception; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Slim projection only: entity_id, name, platform, device_id, area_id, disabled_by, hidden_by; substring filter and caller limit (default 100, no validated maximum in the function). Omits labels, categories, aliases, icon, entity_category, config_entry_id, unique_id. Not listed in CAPABILITY_PROVIDER_MATRIX. | `capabilities.py:44`<br>`compatibility.py:802`<br>`routing.py:452`<br>`routes.py:259` |
| `ha_get_entity` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 (automatic_read) | upstream:ha_get_entity 8.6.0; signed:core.delegated_device_effective_area compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Exact per-entity registry entry (labels, categories, aliases etc. as upstream returns) through the gateway; sanitization and size bound apply. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:203` |
| `ha_get_entity_exposure` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_entity_exposure 8.6.0; signed:core.delegated_device_effective_area compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Assistant exposure read for entities via ha-mcp (use in exposure planning not verified). | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:204` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_entity_metadata_plan` | update | core.entity_registry_metadata_operation: config/entity_registry/update with closed fields aliases, area_id, categories, hidden_by, icon, labels, name | signed:core.entity_registry_metadata_operation dormant-until-signed: runtime-only profile; signed first in seq 10 for 2026.10.1, seq 11 adds 2026.10.0; carried through seq 15 for 2026.10.0/10.1/10.3 only | Governed plan/approve/apply with exact readback. Registry list read fails when it exceeds MAX_REGISTRY_LIST_ITEMS=2_000 (entity_metadata.py line 434; households with more entities cannot plan, inference). This is also the only category write path (categories field). | `capabilities.py:327`<br>`profiles.py:485`<br>`ha-core-release-registry.json:1`<br>`entity_metadata.py:80`<br>`entity_metadata.py:119` |
| `create_entity_availability_exposure_plan` | state | core.entity_registry_availability_operation (enable/disable) and core.entity_assistant_exposure_operation (expose/unexpose for cloud.google_assistant and conversation) | signed:core.entity_registry_availability_operation and core.entity_assistant_exposure_operation dormant-until-signed: runtime-only profile; signed in registry seq 13 for Core 2026.10.1 and carried by seq 14/15 for 2026.10.1 and 2026.10.3 only (not 2026.10.0, not compiled releases) | Up to 8 entities per plan; narrowed per call to the one profile the operation selects. Assistants beyond cloud.google_assistant and conversation are not writable. | `capabilities.py:355`<br>`profiles.py:520`<br>`profiles.py:543`<br>`ha-core-release-registry.json:1` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-053 | Entity registry list richness (labels/categories/aliases/entity_category) in bulk | feature_work | list_entity_registry projects only 7 fields; bulk label/category/alias inventory needs a wider bounded projection or a delegated paged read. | - |
| RCG-054 | Entity registry delete/remove and platform-owned fields | owner_decision | Contract allows only the closed field allowlist via config/entity_registry/update; no remove path. | - |

<a id="device-registry"></a>
#### Device registry (`device_registry`)

Completeness: **partial**. Read-only; bulk list is slim; no write profile.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_devices` | direct_ha_api (config/device_registry/list WebSocket) | DIRECT_HA_READ_POLICIES bounded_device_registry_read | transitional_direct exception; signed:core.direct_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Projection id, name(name_by_user or name), manufacturer, model, area_id, disabled_by; substring filter; limit default 100 caller supplied. Omits labels, identifiers, connections, config entries, parent_device_id. Not in CAPABILITY_PROVIDER_MATRIX. Core 2026.8 composite-device shape handling is in the readmission profile (child reduced shape, parent integrity, effective area, MAX_DEVICE_RECORDS=4096). | `capabilities.py:43`<br>`compatibility.py:773`<br>`routing.py:446`<br>`device_registry.py:20`<br>`routes.py:258` |
| `ha_get_device` | upstream_read_gateway + HA 2026.8 composite device adapter | ha-mcp-8.6.0-public-reads-v1; adapter ha-get-device-composite-ha-2026.8-v1 / ha-get-device-composite-ha-2026.8.1-v1 | upstream:ha_get_device 8.6.0; signed:core.delegated_device_effective_area compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Exact device lookup. The 2026.8 composite adapter restores the entity join only for upstream 8.1.0/8.1.1/8.2.0 (REVIEWED_UPSTREAM_VERSIONS) and Core 2026.8.0/2026.8.1; for other upstream versions the payload passes through unadapted. Whether ha-mcp 8.6.0 still needs it on 2026.8.x is NOT established. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:202`<br>`ha_2026_8_device_compatibility.py:22`<br>`ha_2026_8_device_compatibility.py:14` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-055 | Device registry writes (rename, area assignment, disable, labels) | new_signed_profile | No device_registry/update command appears in any governance contract, strategy or profile (grep of runtime found none); needs a new typed profile, plan tool, negative tests and an owner-signed registry entry. | - |
| RCG-056 | Rich bulk device listing | feature_work | list_devices projection lacks identifiers/connections/labels/config entry linkage; delegated ha_get_device is per device only. | - |

<a id="area-registry"></a>
#### Area registry (`area_registry`)

Completeness: **partial**. The whole registry is acquired, but list_areas delivery truncates above 60,000 characters with no continuation; complete below that bound. No writes.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_areas` | direct_ha_api (config/area_registry/list WebSocket) | DIRECT_HA_READ_POLICIES complete_area_registry_read | transitional_direct exception; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Whole area registry dump (no filter/limit). Provider output passes the response-size limit machinery. Delivery is truncated at MAX_CHARS (60,000) characters with a marker and no continuation argument, so the read is complete only below that bound. | `capabilities.py:62`<br>`compatibility.py:767`<br>`routing.py:464`<br>`routes.py:264`<br>`compatibility.py:87` |
| `ha_list_floors_areas` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_list_floors_areas 8.6.0; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Floors with their areas via ha-mcp; gateway bounds apply. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:218` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-057 | Area create/update/delete | new_signed_profile | No area_registry create/update/delete command exists in any governance contract (only area_id assignment on entities via entity metadata). Needs a new typed profile, plan tool and signed admission; upstream ha_config_set_area/remove writes are not reachable. | - |

<a id="floor-registry"></a>
#### Floor registry (`floor_registry`)

Completeness: **partial**. Read via delegated tool only; no floor write.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_list_floors_areas` | upstream_read_gateway (floor_registry list inside the compiled non_device profile) | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_list_floors_areas 8.6.0; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) (websocket_floor_registry_list) | partial | Only read path for floors; no standalone Engineering floor tool. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:218`<br>`profiles.py:200` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-058 | Floor create/update/delete | new_signed_profile | No floor_registry write command in any contract; add a typed floor-registry operation profile (like label), plan tool, negative tests, signed admission. | - |

<a id="label-registry"></a>
#### Label registry (`label_registry`)

Completeness: **partial**. Readable and create/update writable only on signed Core 2026.10.1/10.3; delete absent by design.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_label` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_config_get_label 8.6.0; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Label list or lookup via ha-mcp; planner also reads config/label_registry/list directly (cap 5_000). | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:189`<br>`label_registry.py:107` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_label_plan` | create, update | core.label_registry_operation: config/label_registry/create\|update; delete never sent | signed:core.label_registry_operation dormant-until-signed: runtime-only profile; signed in registry seq 13 for Core 2026.10.1 and carried by seq 14/15 for 2026.10.1 and 2026.10.3 only (not 2026.10.0, not compiled releases) | Up to 8 children per plan; label_deletion False; rollback_available False. config/label_registry/delete is in never_sent by design. | `capabilities.py:388`<br>`profiles.py:570`<br>`ha-core-release-registry.json:1`<br>`label_registry.py:113`<br>`profiles.py:585` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-059 | Label delete | owner_decision | Absent by design (never_sent); enabling it is a new destructive write path needing explicit owner scope, pre-image, rollback design. | - |

<a id="category-registry"></a>
#### Category registry (`category_registry`)

Completeness: **partial**. Read delegated; only assignment (not category CRUD) is writable.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_config_get_category` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_config_get_category 8.6.0; signed:core.non_device_registry_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Category listing/lookup via ha-mcp. core.non_device_registry_read profile attests area, floor, label and entity lists only (no category_registry command listed in the compiled profile), so category attestation is thin. UNVERIFIED how the gateway handles that mismatch. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:188`<br>`profiles.py:201` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_entity_metadata_plan` | update | entity registry categories field only (per-scope assignment, MAX_STATE_CATEGORY_SCOPES=16) | signed:core.entity_registry_metadata_operation dormant-until-signed: runtime-only profile; signed first in seq 10 for 2026.10.1, seq 11 adds 2026.10.0; carried through seq 15 for 2026.10.0/10.1/10.3 only | Assigns existing categories to entities; categories themselves (category_registry create/update/delete) cannot be created, renamed or deleted. | `entity_metadata.py:77`<br>`profiles.py:485` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-060 | Category create/update/delete | new_signed_profile | No category_registry write command in any contract; needs a typed profile mirroring label_registry, plus signed admission. | - |

### Integrations, states and services

<a id="config-entries-and-integrations"></a>
#### Config entries and integrations (`config_entries_and_integrations`)

Completeness: **partial**. No generic config-entry or integration inspection beyond the alarmo allowlist and a windowed list; config entry options writes exist only in the helper domain (other register rows).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_integration_inspection` | engineering (integration_inspection provider) | ANALYTICAL_PROVIDER_POLICIES alarmo_allowlist_v1; adapter alarmo-inspection-v1 / profile alarmo-configuration-read-v1 | native; signed:core.integration_inspection_metadata_read dormant-until-signed: runtime-only profile; signed first in seq 4 (2026.9.4, later revoked/removed at seq 12), now in seq 15 for 2026.10.0, 2026.10.1 and 2026.10.3 | partial | Alarmo only: manifest/get for alarmo and config_entries/get domain=alarmo, allowlist projection; no other integration inspectable. | `capabilities.py:161`<br>`contracts.py:22`<br>`contracts.py:34`<br>`profiles.py:459`<br>`ha-core-release-registry.json:1` |
| `ha_get_integration` | upstream_read_gateway wrapper (upstream_platform_reads) | adapter ha-mcp-8.6.0-platform-integrations-v1 (mixed_or_requires_wrapper) | upstream:ha_get_integration 8.6.0 wrapper; core.basic_websocket_read + core.basic_rest_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | List mode only, fixed upstream window UPSTREAM_INTEGRATION_WINDOW=200 entries, public limit 1-100 and offset 0-200, no options, Engineering-side filtering by domain/query/state; truncation reported as upstream_window_exceeded (partial). Allowlist-projected. | `capabilities.py:1003`<br>`upstream_platform_reads.py:131`<br>`upstream_platform_reads.py:685`<br>`routes.py:214` |
| `get_configuration_definitions` | engineering (configuration_definitions) with YAML reader purpose | ANALYTICAL_PROVIDER_POLICIES configuration_definitions_read_v1; YAML_READER_PURPOSES configuration_definitions [yaml_reader:configuration_definitions, gateway_reader:yaml_config_reader] | native; core.basic_rest_read, core.basic_websocket_read, core.non_device_registry_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)); needs admitted YAML reader (ha_config_get_yaml bound only for 8.4.3/8.5.0) | partial | config_entries/get for a closed domain set, manifest/get ha_mcp_tools, entity registry and entity/source plus fixed definition YAML paths; allowlist projection, db_url/credentials never returned. | `capabilities.py:189`<br>`contracts.py:38`<br>`contracts.py:159`<br>`routes.py:242`<br>`upstream_read_gateway.py:796` |
| `get_unregistered_entities` | engineering (entity_reconciliation) | ANALYTICAL_PROVIDER_POLICIES unregistered_entities_read_v1 | native; core.basic_rest_read, core.basic_websocket_read, core.non_device_registry_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | GET /api/states against config/entity_registry/list plus entity/source to find states with no registry row; reports partial/inconclusive when source rows missing. | `capabilities.py:182`<br>`contracts.py:32`<br>`routes.py:239` |
| `ha_get_hacs_info` | upstream_read_gateway | none (generic delegated read) | ha-mcp automatic_read | partial | HACS store search and repository details; needs HACS installed. Covers HACS metadata only, not config entries. | `upstream_tool_policy.json:1016` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-061 | Config-entry/integration inspection beyond Alarmo | new_reader_purpose | Each additional integration needs a reviewed allowlist adapter, source-pinned and privacy-classified; generic config_entries/get is not exposed. | - |
| RCG-062 | ha_get_integration beyond first 200 upstream entries / no detail mode | feature_work | Window is capped and options/detail mode is not built; absence outside window is not definitive. | - |

<a id="entity-states"></a>
#### Entity states, search, overview and templates (`entity_states`)

Completeness: **complete**. Current state of any entity is exactly readable (get_entity, ha_get_state); bulk search is bounded/truncating by design.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_entity` | direct_ha_api (GET /api/states/{id}) | exact_entity_state_read | transitional_direct exception; signed:core.direct_entity_state_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | complete | Full state and all attributes for one entity; passes response_size_limit (60_000) so very large attribute sets fail rather than truncate (inference). | `capabilities.py:1114`<br>`compatibility.py:317`<br>`routing.py:422` |
| `search_entities` | direct_ha_api (GET /api/states) | bounded_entity_state_search | transitional_direct exception; signed:core.direct_entity_state_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Substring match on entity_id/friendly_name, optional domain; limit 1-100; returns entity_id, state, friendly_name only; matrix completeness complete_or_explicitly_truncated. | `capabilities.py:1103`<br>`compatibility.py:335`<br>`routing.py:428` |
| `ha_get_state` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_state 8.6.0; signed:core.direct_entity_state_read compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Delegated state read (single/multi per upstream schema), gateway bounds. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:215` |
| `ha_search` | upstream_read_gateway search adapter | ha-mcp-8.6.0-search-descriptor-pair-v1 | upstream:ha_search 8.6.0 exact descriptor pair; signed:core.delegated_device_effective_area compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Fuzzy entity/area/device search through the exact search descriptor pair adapter; bounded results. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:220`<br>`upstream_search_8_6.py:10` |
| `ha_get_overview` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_overview 8.6.0; signed:core.delegated_device_effective_area compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | System/entity overview summary from ha-mcp; gateway sanitization and size bound. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:208` |
| `render_template` | direct_ha_api (POST /api/template) | bounded_template_render_read | transitional_direct exception; signed:core.template_semantics compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Renders an arbitrary Jinja template against live state (read-only semantics); output is bounded by the provider policy. Template side effects are none in HA's template API. | `capabilities.py:32`<br>`compatibility.py:556`<br>`routing.py:349`<br>`routes.py:247` |
| `ha_eval_template` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_eval_template 8.6.0; signed:core.template_semantics compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Delegated template evaluation. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:198` |
| `ha_get_operation_status` | upstream_read_gateway | none (generic delegated read) | ha-mcp automatic_read; held_for_canary in later reviewed policies | partial | Status of device operations started through ha-mcp; not a general state read. | `upstream_tool_policy.json:1108` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `control_power` | state: light/switch on or off | authenticated_exact_light_switch_operation | Core profile core.typed_power_operation (signed) or exact legacy Core 2026.9.2 | One exact ON/OFF action; reported-state verification only, physical feedback not verified. | `capabilities.py:221`<br>`capabilities.py:953` |
| `control_fan` | state: fan on, off or percentage | authenticated_exact_fan_operation | Core profile core.typed_fan_operation (signed) or exact legacy Core 2026.9.2 | One exact fan action; state and percentage verification, physical feedback not verified. | `capabilities.py:230`<br>`capabilities.py:972` |

<a id="services-catalog"></a>
#### Service catalog (`services_catalog`)

Completeness: **partial**. Bounded discovery with explicit truncation; listing every service schema takes multiple domain-filtered calls.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_services` | direct_ha_api (GET /api/services) | bounded_service_schema_read | transitional_direct exception; signed:core.state_service_discovery compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Full field schemas, optional domain filter, hard cap maximum_services = 50 services per call with disclosure. | `capabilities.py:1147`<br>`compatibility.py:879`<br>`compatibility.py:886`<br>`routing.py:476` |
| `search_services` | direct_ha_api | bounded_service_catalog_search | transitional_direct exception; signed:core.state_service_discovery compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Substring discovery, maximum_limit 100, explicit truncated flag. | `capabilities.py:1136`<br>`compatibility.py:830`<br>`routing.py:470` |
| `ha_list_services` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_list_services 8.6.0; signed:core.state_service_discovery compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1) | partial | Delegated service listing. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:219` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-063 | Complete service catalog in one read | feature_work | list_services hard-caps at 50 services; a paged cursor would give complete enumeration. | - |

<a id="zones-todo-calendar"></a>
#### Zones, to-do lists, calendar events (`zones_todo_calendar`)

Completeness: **partial**. Reads delegated through the generic adapter with sanitization/size bounds; no writes.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_zone` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_zone 8.6.0; core.basic_rest_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | Zone read; zone writes (ha_set_zone/ha_remove_zone) are persistent_write and unreachable. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:217`<br>`upstream_tool_policy_8_6_0.json:1` |
| `ha_get_todo` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_todo 8.6.0; core.basic_websocket_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | To-do list/item read; item writes are not reachable. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:216` |
| `ha_config_get_calendar_events` | upstream_read_gateway | ha-mcp-8.6.0-public-reads-v1 | upstream:ha_config_get_calendar_events 8.6.0; core.basic_websocket_read (compiled-active (CORE_CAPABILITY_PROFILES; compiled_exact_authority for SUPPORTED_CORE_RELEASES 2026.7.2/8.0/8.1/9.0/9.1)) | partial | Calendar event read; event create/delete are not reachable. | `upstream_tool_policy_8_6_0.json:1`<br>`routes.py:187` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-064 | Zone/todo/calendar writes | owner_decision | Upstream write tools are persistent_write; no governed profile exists. | - |

### Logs and traces

<a id="logs-core"></a>
#### Home Assistant Core logs (`logs_core`)

Completeness: **partial**. Three native bounded readers exist (structured ring, Supervisor /core/logs tail, incident window) but each is capped, sanitized (credential lines withheld whole) and Core log retention is not controlled by Engineering; ha_get_logs is unreachable.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_error_log` | direct_ha_api | DIRECT_HA_READ_POLICIES structured_system_log_read | native | partial | WebSocket system_log/list; at most 200 entries and 40000 payload chars; Core's in-memory structured error ring only (not a file tail). Output sanitized and bounded. | `routing.py:415`<br>`compatibility.py:67`<br>`compatibility.py:431` |
| `get_core_log_history` | supervisor_core_logs | core_log_policy_allows_read / supervisor_api_only | native | partial | GET http://supervisor/core/logs via Supervisor API (role homeassistant admits it). limit 2..200, offset<=10000, 256 KiB download cap, 32 KiB evidence cap, 10 s deadline. HAOS/Supervised only; always partial; lines containing credential-like content withheld whole; no retry or fallback. | `core_logs.py:31`<br>`routing.py:505`<br>`config.yaml:12` |
| `get_incident_logs` | incident_logs (supervisor route core) | incident_logs_policy_allows_read; READABLE_ROUTES core | native | partial | Bounded multi-source window reader (max 4 sources, 6 h window, limit 200); source 'core' maps to /core/logs. calibration mode returns counts only. | `contracts.py:88`<br>`contracts.py:53` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-065 | Unbounded or full-history Core log (older than Supervisor journal retention / ring size) | impossible_via_ha_api | Retention is set by Core/Supervisor/journald; Engineering can only page what they still hold. State this in the matrix as a hard bound. | - |
| RCG-066 | Lines withheld whole because they match credential patterns (no redacted-line view) | owner_decision | Owner decides whether a field-level redaction mode may replace whole-line withholding; today fail-closed. | - |
| RCG-067 | ha_get_logs delegated logs (log_type/filters) | upstream_admission | New purpose-bound adapter + argument_restrictions token + reviewed response sanitizer for ha_get_logs, then a policy/registry revision; plus negative tests for the raw path. | - |

<a id="logs-supervisor"></a>
#### Supervisor logs (`logs_supervisor`)

Completeness: **partial**. Readable only through the host journal mount, bounded by incident-window limits; Supervisor-REST logs, info and stats are not reachable because Engineering holds hassio_role homeassistant.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_incident_logs` | incident_logs host_journal (source 'supervisor') | incident_logs_policy_allows_read; HOST_MATCHES / SUPERVISOR_IDENTIFIER | native | partial | Supervisor logs are read from the host journal mounted read-only via config journald: true, filtering SYSLOG_IDENTIFIER hassio_supervisor, through libsystemd ctypes. Source is reported unavailable with a reason if the mount is absent. Supervisor REST /supervisor/logs is NOT used (needs manager role). | `host_journal.py:114`<br>`host_journal.py:64`<br>`config.yaml:14` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-068 | Supervisor REST logs/info/stats (/supervisor/logs, /supervisor/info, /supervisor/stats) | owner_decision | Requires raising hassio_role to manager (config.yaml) which also widens add-on, host and backup control surface; owner must decide, then add a read-only reader purpose with route allowlist. | - |
| RCG-069 | Supervisor version/health beyond the hassio baseline keys | feature_work | Only baseline keys (supervisor_version, healthy, supported ...) arrive via ha_get_system_health; any more needs a new projection. | - |

<a id="logs-addon"></a>
#### Add-on logs (`logs_addon`)

Completeness: **partial**. Own add-on via Supervisor API; others via host journal mount only; both bounded and sanitized.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_incident_logs` | incident_logs transport (addon:self) | READABLE_ROUTES addon:self | native | partial | Own add-on log via Supervisor /addons/self/logs (role homeassistant admits /addons/self/*). | `contracts.py:88`<br>`config.yaml:12` |
| `get_incident_logs` | incident_logs host_journal (addon:<slug>) | host journal app_/addon_ identifiers | native | partial | Other add-on logs only through the read-only host journal mount (container identifiers app_<slug>/addon_<slug>); slug validated; unavailable if journal not mounted. | `host_journal.py:180`<br>`host_journal.py:64` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-070 | Other add-ons' logs via Supervisor REST (/addons/<slug>/logs) | owner_decision | Needs manager role; owner decision as for Supervisor logs. | - |

<a id="logs-host"></a>
#### Host / OS logs (`logs_host`)

Completeness: **partial**. Allowlisted journal units only; arbitrary units and Supervisor host REST are not reachable.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_incident_logs` | incident_logs host_journal | HOST_MATCHES closed allowlist | native | partial | Closed identifier allowlist: host, host:kernel, host:usb, host:network, host:storage, host:os, read from the journal mount; calibration mode returns counts only. Supervisor /host/logs not used (manager role). | `host_journal.py:129`<br>`config.yaml:14` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-071 | Additional journal units / unit-name queries | feature_work | Extend HOST_MATCHES with reviewed units plus negative tests; open filters rejected today. | - |
| RCG-072 | Supervisor host info/stats/services (/host/*) | owner_decision | Needs manager role; owner decision. | - |

<a id="traces-automation"></a>
#### Automation traces (`traces_automation`)

Completeness: **partial**. Live traces are limited to what Core still retains; snapshots preserve only what was captured in time.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_automation_traces` | direct_ha_api | DIRECT_HA_READ_POLICIES bounded_automation_trace_list_read | native; core.automation_trace_read compiled-active (CORE_CAPABILITY_PROFILES) | partial | WS trace/list domain automation, bounded. | `routing.py:379`<br>`compatibility.py:563` |
| `get_automation_trace` | direct_ha_api | DIRECT_HA_READ_POLICIES bounded_automation_trace_read | native; core.automation_trace_read compiled-active | partial | WS trace/get domain automation for one run_id; bounded/sanitized. | `routing.py:385`<br>`compatibility.py:577` |
| `ha_get_automation_traces` | upstream_read_gateway | adapter token ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_automation_traces 8.6.0 adapter public-reads-v1 | partial | Delegated trace read via reviewed adapter; Core requirement automation_trace_read. | `routes.py:199`<br>`upstream_reads_8_6.py:992` |
| `capture_automation_trace` | engineering trace_snapshots | automation-trace-snapshot-v1 | native; signed:core.automation_trace_read (compiled-active) | partial | Durable local snapshot of a trace (262144 B per snapshot, 32 MiB store, 90-day retention, max 5 captures per call). | `trace_snapshots.py:47`<br>`contracts.py:20` |
| `list_trace_snapshots` | engineering trace_snapshots | CORE_INDEPENDENT_STATIC_TOOLS (local state) | native | complete | Lists locally stored snapshots. | `trace_snapshots.py:72` |
| `get_trace_snapshot` | engineering trace_snapshots | CORE_INDEPENDENT_STATIC_TOOLS (local state) | native | complete | Reads one stored snapshot. | `trace_snapshots.py:89` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `set_automation_trace_watch` | state | Engineering-local watch (max 10 watches, 168 h, 30 s poll) | native | Local watch state only; no HA write. | `trace_snapshots.py:106`<br>`contracts.py:35` |
| `capture_automation_trace` | create | local snapshot store | native | Writes Engineering-local snapshot only; no HA write. | `trace_snapshots.py:47` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-073 | Traces already rotated out by Core (default per-automation trace cap) | impossible_via_ha_api | Core trace storage is bounded; only pre-captured snapshots/watches preserve history. | - |

<a id="traces-script"></a>
#### Script traces (`traces_script`)

Completeness: **partial**. Implemented and compiled but not reachable by default: needs signed registry admission for the running Core; signed only for 2026.10.3.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `list_script_traces` | direct_ha_api | script_trace_list_read_v1 | signed:core.script_trace_read (state: dormant-until-signed; owner-signed seq 15 covers Core 2026.10.3 only) | partial | WS trace/list domain script. Refuses with pending_signed_registry_admission unless the running Core release is admitted in an owner-signed registry; registry use is default-off (ha_core_release_registry_enabled false) and installed acceptance is not claimed. | `script_traces.py:36`<br>`script_traces.py:49`<br>`routes.py:181` |
| `get_script_trace` | direct_ha_api | script_trace_read_v1 | signed:core.script_trace_read (state: dormant-until-signed; seq 15, Core 2026.10.3 only) | partial | WS trace/get domain script for one run. | `script_traces.py:52`<br>`script_traces.py:47` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-074 | Script trace snapshots/watch (parity with automation capture) | feature_work | No capture_script_trace exists; new tool + store + profile reuse. | - |

### History, recorder and statistics

<a id="history"></a>
#### State history (recorder) (`history`)

Completeness: **partial**. Native window capped at 168 h; slice reader 7 days; recorder retention bounds everything.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_history` | direct_ha_api | bounded_entity_history_read | native; core.basic_rest_read compiled-active | partial | REST /history/period; hours<=168; minimal flag; bounded entities and size. | `routing.py:403`<br>`compatibility.py:397` |
| `get_history_slices` | upstream_read_gateway history_reader | history_slices_read_v1 [gateway_reader:history_reader] | upstream:ha_get_history 8.5.0/8.6.0 (REVIEWED_VERSIONS) | partial | Single entity, up to 7 days, 6 h slices, limit 1..100, max 12 upstream calls, cursor TTL 3600 s. | `contracts.py:33`<br>`contracts.py:35`<br>`upstream_read_gateway.py:3934` |
| `ha_get_history` | upstream_read_gateway | adapter token ha-mcp-8.6.0-public-reads-v1 | upstream:ha_get_history 8.6.0 adapter public-reads-v1 | partial | Delegated history; arguments pass through unchanged (source history\|statistics). | `upstream_reads_8_6.py:1010`<br>`routes.py:206` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-075 | History older than recorder purge_keep_days | impossible_via_ha_api | Recorder retention governs; only long-term statistics survive. | - |
| RCG-076 | Windows longer than 7 days and attribute-level history via slices | feature_work | Extend history_slices contracts and review ha_get_history args. | - |

<a id="logbook"></a>
#### Logbook (`logbook`)

Completeness: **partial**. Bounded window and record count.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_logbook` | direct_ha_api | bounded_logbook_read | native; core.basic_rest_read compiled-active | partial | REST /logbook via LogbookReader; 1 MiB, 10000 records, 168 h, 30 s caps. | `routing.py:409`<br>`logbook.py:175`<br>`logbook.py:20` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-077 | Logbook beyond 168 h or 10000 records | feature_work | Raise caps with review or add pagination; recorder retention still applies. | - |

<a id="recorder-settings"></a>
#### Recorder settings (`recorder_settings`)

Completeness: **partial**. Settings and per-entity options readable; DB size, purge status and engine health not read (unverified).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_recorder_settings` | direct_ha_api + upstream_read_gateway yaml reader | recorder_settings_read_v1; YAML_READER_PURPOSES recorder_settings [yaml_reader:recorder_settings, gateway_reader:yaml_config_reader] | native + upstream:ha_config_get_yaml adapter; core.basic_rest_read and core.basic_websocket_read compiled-active | partial | recorder/info, recorder/entity_options/get (max 50 entities) and configuration.yaml recorder section via fixed-path YAML purpose; db_url withheld. Entity filter port pinned to Core 2026.10.1. | `recorder_settings.py:15`<br>`contracts.py:30`<br>`upstream_read_gateway.py:804`<br>`entity_filter.py:24` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-078 | Recorder DB size / purge state / engine health | feature_work | New bounded reader + Core profile; not verified to be exposed by any command today. | - |

<a id="statistics-long-term"></a>
#### Long-term statistics (`statistics_long_term`)

Completeness: **partial**. Reachable only as a generic pass-through; no native bounded reader and no recorder/statistics_* command in Engineering.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_history` | upstream_read_gateway | adapter token ha-mcp-8.6.0-public-reads-v1 (source=statistics) | upstream:ha_get_history 8.6.0 adapter public-reads-v1 | partial | Only path: source enum includes 'statistics' (needs state_class). Arguments pass through unchanged; no statistics-specific projection or response-shape review exists (unreviewed). | `upstream_reads_8_6.py:1010`<br>`upstream_reads_8_6.py:508` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-079 | Native bounded long-term statistics reader (statistics_during_period, list_statistic_ids, validate_statistics, energy-grade periods) | feature_work | New purpose-bound reader + Core profile (basic_websocket_read or new), caps and tests. | - |
| RCG-080 | Reviewed output shape for ha_get_history source=statistics | upstream_admission | Review/projection of the upstream statistics response in the adapter. | - |

<a id="energy-prefs"></a>
#### Energy dashboard preferences (`energy_prefs`)

Completeness: **unavailable**. No client path.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| none | - | - | - | unavailable | - | - |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-081 | Energy prefs read (sources, grid, solar, devices) | new_reader_purpose | Purpose-bound reader for energy/get_prefs with Core profile, or adapter for ha_manage_energy_prefs. | - |

### Supervisor and system

<a id="addons"></a>
#### Add-ons (`addons`)

Completeness: **partial**. Inventory readable; only restart is writable and only on admitted upstream versions (deployed upstream version unknown from source).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_app` | upstream_read_gateway | adapter token ha-mcp-8.6.0-platform-apps-v1 | upstream:ha_get_app 8.6.0 adapter platform-apps-v1 | partial | Installed-app inventory (slug, name, version, state, installed, update_available, repository, description), cap 100. Renamed from ha_get_addon at 8.4.1. | `upstream_platform_reads.py:17`<br>`routes.py:213` |
| `internal:supervisor_self` | supervisor /addons/self/info | SupervisorSelfAddonIdentityResolver | native | partial | Own add-on identity only; used to refuse self-restart. | `supervisor_self.py:70` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_addon_restart_plan` | restart | governed plan, exact-hash external approval | upstream:ha_get_addon+ha_manage_addon, lifecycle disposition admitted for ha-mcp 7.14.1-8.1.1 only; held at 8.2.0-8.6.0 | Restart only; self-restart refused; tool names hard-coded to the pre-8.4.1 ha_get_addon/ha_manage_addon. | `governance.py:98`<br>`operational_lifecycle.py:53`<br>`operational_lifecycle.py:111` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-082 | Add-on restart at ha-mcp 8.2.0+ (lifecycle held; tool renamed to ha_manage_app) | upstream_admission | Review 8.x ha_manage_app surface, update hard-coded tool names, admit lifecycle disposition. | - |
| RCG-083 | Add-on options/config reads | owner_decision | Options may carry secrets; owner decision plus redaction review. | - |
| RCG-084 | Add-on start/stop/update/options writes | owner_decision | New write surface = security architecture change; explicit scope. | - |

<a id="backups"></a>
#### Backups (`backups`)

Completeness: **partial**. Inventory readable; create only on admitted upstream versions; deployed upstream version unknown from source.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `get_backup_inventory` | direct_ha_api | backup_inventory_read_v1 | native; core.basic_websocket_read compiled-active | partial | WS backup/info and backup/config/info; default 25, max 50, 500 records; encryption key never returned. | `backup_inventory.py:15`<br>`contracts.py:27` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_backup_plan` | create | governed plan, exact-hash approval | upstream:ha_manage_backup via upstream_operational_backup; disposition admitted 7.14.1-8.1.1, held 8.2.0-8.6.0 | Create full backup only; fixed args scope snapshot/action create; restore and delete disallowed. | `governance.py:78`<br>`operational_backup.py:34`<br>`operational_backup.py:371` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-085 | Backup creation at ha-mcp 8.2.0+ (held) | upstream_admission | Review 8.x ha_manage_backup contract and admit backup disposition. | - |
| RCG-086 | Restore/delete/download | owner_decision | Explicitly disabled; would be new destructive surface. | - |

<a id="system-health"></a>
#### System health (`system_health`)

Completeness: **partial**. Curated subset only.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_system_health` | upstream_read_gateway | adapter token ha-mcp-8.6.0-platform-health-v1 | upstream:ha_get_system_health 8.6.0 platform-health-v1 | partial | Projected sections with caps; excluded keys diagnostics/config_check/zha_network_full; baseline keys for homeassistant, hassio, cloud. | `upstream_platform_reads.py:46`<br>`routes.py:212` |
| `get_server_health` | direct_ha_api | CONDITIONAL_CORE_STATIC_TOOLS | native | partial | GET /config probe plus Engineering health. | `compatibility.py:232` |
| `server_info` | engineering | CONDITIONAL_CORE_STATIC_TOOLS | native | partial | Engineering info; probes HA only when check_ha=True. | `compatibility.py:130` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-087 | Raw system_health payload and diagnostics | owner_decision | Deliberately excluded; owner decides. | - |

<a id="repairs"></a>
#### Repairs (`repairs`)

Completeness: **partial**. Only through the health section; capped; no fix/ignore action.

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_system_health` | upstream_read_gateway | platform-health-v1 section repairs | upstream:ha_get_system_health 8.6.0 platform-health-v1 | partial | sections=['repairs'] only; cap 50; fields domain, issue_domain, issue_id, severity, is_fixable, translation_key, breaks_in_ha_version, created, ignored, dismissed_version; include_dismissed_repairs requires the section. | `upstream_platform_reads.py:420`<br>`upstream_platform_reads.py:42` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-088 | Repair issue descriptions, learn-more URLs and fix flows | feature_work | Extend projection; fix/ignore would be a write surface (owner_decision). | - |

<a id="updates"></a>
#### Updates (`updates`)

Completeness: **partial**. No update-integration reader: ha_manage_updates unreachable (mixed_or_requires_wrapper, no adapter); update.* entity states only via generic state reads (not verified here).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `ha_get_app` | upstream_read_gateway | platform-apps-v1 | upstream:ha_get_app 8.6.0 platform-apps-v1 | partial | update_available per add-on only. | `upstream_platform_reads.py:598` |

Write paths: none.

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-089 | Core/OS/Supervisor/integration update inventory and release notes | upstream_admission | Adapter for ha_manage_updates read action or native update/info reader. | - |
| RCG-090 | Installing updates | owner_decision | Write surface; not present. | - |

<a id="home-assistant-restart-reload"></a>
#### Home Assistant restart and reload (`home_assistant_restart_reload`)

Completeness: **partial**. Planned restarts/reloads only on admitted upstream; legacy reload_domain/call_service deprecated and routed to ORDINARY_SERVICE_EXECUTION (not fully verified).

| Read path | Provider | Purpose binding | Admission | Completeness | Detail | Code |
|---|---|---|---|---|---|---|
| `check_config` | direct_ha_api | bounded configuration check | native; configuration_validation profile | complete | Validates configuration without applying. | `routing.py:397`<br>`compatibility.py:739` |

| Write path | Operations | Policy | Admission | Detail | Code |
|---|---|---|---|---|---|
| `create_home_assistant_restart_plan` | restart | governed plan, exact-hash approval | upstream:ha_restart via lifecycle; admitted 7.14.1-8.1.1, held 8.2.0-8.6.0 | Requires check_config pass; ha_restart physical_or_high_risk_action with no adapter at 8.6.0. | `governance.py:99`<br>`operational_lifecycle.py:55` |
| `create_reload_plan` | reload | governed plan; RELOAD_TARGETS automation, script, input_boolean, input_number | upstream:ha_reload_core; same disposition as above | Four domains only. | `governance.py:79`<br>`operational_lifecycle.py:56` |

| Gap | Missing | Requirement type | Requirement to close | Register |
|---|---|---|---|---|
| RCG-091 | Restart/reload at ha-mcp 8.2.0+ (lifecycle held) | upstream_admission | Review and admit lifecycle disposition for current ha-mcp. | - |
| RCG-092 | Other reload domains | feature_work | Extend RELOAD_TARGETS with negative tests. | - |

### Tools that read no Home Assistant data source directly

| Tool | Why | Code |
|---|---|---|
| `server_info` | HA probe only if check_ha=True | `compatibility.py:130` |
| `list_capabilities` | Static catalog | `compatibility.py:173` |
| `get_audit_log` | Local audit store | `compatibility.py:933` |
| `get_change_plan` | Local plan store | `governance.py:496` |
| `list_change_plans` | Local plan store | `governance.py:527` |
| `get_execution_task` | Local task store | `governance.py:553` |
| `list_execution_tasks` | Local task store | `governance.py:575` |
| `compare_automation_baselines` | Compares stored baselines | `audit_baseline.py:55` |
| `list_trace_snapshots` | Local snapshots | `trace_snapshots.py:72` |
| `get_trace_snapshot` | Local snapshots | `trace_snapshots.py:89` |
| `run_held_read_canary` | Exercises held upstream reads (ha_get_operation_status held_for_canary at 8.6.0) | `canary.py:12` |
| `entity_dependency_analysis` | Compose-only analysis | `analysis.py:53` |
| `automation_reliability_analysis` | Compose-only analysis | `analysis.py:125` |
| `change_impact_analysis` | Compose-only analysis | `analysis.py:189` |
| `configuration_integrity_analysis` | Compose-only analysis | `analysis.py:303` |
| `incident_correlation` | Compose-only analysis | `analysis.py:438` |
| `handoff_generation` | Compose-only analysis | `analysis.py:526` |
| `cancel_execution_task` | Local task cancel; no HA write. | `governance.py:119` |
| `approve_change_plan` | Governance: records an external approval request for an existing plan; reads only Engineering plan state. | `governance.py:640` |
| `rollback_change` | Governance: replays a plan's own captured rollback through that plan's write path; no source of its own. | `governance.py:684` |
| `reverify_configuration_task` | Verification: re-reads a retained configuration task through the readers of the source it changed; writes Engineering evidence only. | `governance.py:708` |
| `call_service` | Deprecated; provider_unavailable, fails closed (no direct write, no fallback). | `capabilities.py:65` |
| `reload_domain` | Deprecated; provider_unavailable, fails closed. Governed reloads use create_reload_plan. | `capabilities.py:76` |
| `ha_get_skill_guide` | Delegated ha-mcp read of bundled best-practice guides; static upstream content, no Home Assistant data. | `upstream_tool_policy.json:1154` |

### Compiled Core profile index

| Core profile | Probe profile | Used by | Note |
|---|---|---|---|
| `core.automation_baseline_metadata_read` | no | automations | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.automation_configuration_read` | yes | automations | Probe profile: compiled into the startup Core probe. |
| `core.automation_trace_read` | yes | traces_automation | Probe profile: compiled into the startup Core probe. |
| `core.basic_rest_read` | yes | groups, yaml_templates, packages_and_yaml_files, secrets, dashboards_content, yaml_dashboards, config_entries_and_integrations, zones_todo_calendar, history, logbook, recorder_settings | Probe profile: compiled into the startup Core probe. |
| `core.basic_websocket_read` | yes | automations, scripts, scenes, groups, blueprints, yaml_templates, packages_and_yaml_files, secrets, helpers_input_boolean_number, helpers_input_select, helpers_storage_other, helpers_options_supported, helpers_options_other, helpers_not_modelled, yaml_dashboards, config_entries_and_integrations, zones_todo_calendar, recorder_settings, backups | Probe profile: compiled into the startup Core probe. |
| `core.config_entry_helper_options_operation` | no | yaml_templates, helpers_options_supported | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.configuration_delete_operation` | no | automations, scripts, scenes, helpers_input_boolean_number, helpers_input_select, helpers_storage_other | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.configuration_validation` | yes | blueprints | Probe profile: compiled into the startup Core probe. |
| `core.dashboard_configuration_read` | yes | dashboards_content, dashboard_metadata, dashboard_resources | Probe profile: compiled into the startup Core probe. |
| `core.dashboard_metadata_operation` | no | dashboard_metadata | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.delegated_device_effective_area` | yes | entity_registry, device_registry, entity_states | Probe profile: compiled into the startup Core probe. |
| `core.dependency_helper_planning` | yes | automations, scripts, groups | Probe profile: compiled into the startup Core probe. |
| `core.direct_device_registry_read` | yes | device_registry | Probe profile: compiled into the startup Core probe. |
| `core.direct_entity_state_read` | yes | entity_states | Probe profile: compiled into the startup Core probe. |
| `core.entity_assistant_exposure_operation` | no | entity_registry | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.entity_registry_availability_operation` | no | entity_registry | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.entity_registry_metadata_operation` | no | entity_registry, category_registry | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.f3_mutation_verification` | yes | automations, scripts, scenes, groups | Probe profile: compiled into the startup Core probe. |
| `core.governance_observability` | yes | engineering_internal | Probe profile for Engineering's own governance and health observability; reads no Home Assistant data source. |
| `core.governed_configuration_operation` | yes | automations, scripts, scenes, groups, helpers_input_boolean_number, helpers_input_select, helpers_storage_other, helpers_options_supported, helpers_options_other | Probe profile: compiled into the startup Core probe. |
| `core.integration_inspection_metadata_read` | no | config_entries_and_integrations | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.label_registry_operation` | no | label_registry | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.non_device_registry_read` | yes | dashboards_content, entity_registry, area_registry, floor_registry, label_registry, category_registry, config_entries_and_integrations | Probe profile: compiled into the startup Core probe. |
| `core.scene_configuration_operation` | no | automations, scripts, scenes | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.script_trace_read` | no | traces_script | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.state_service_discovery` | yes | services_catalog | Probe profile: compiled into the startup Core probe. |
| `core.template_semantics` | yes | yaml_templates, entity_states | Probe profile: compiled into the startup Core probe. |
| `core.typed_fan_operation` | no | entity_states | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.typed_helper_operation` | yes | helpers_input_boolean_number | Probe profile: compiled into the startup Core probe. |
| `core.typed_input_select_option_operation` | no | helpers_input_select | Runtime-only: needs signed Core registry admission for the observed Core release. |
| `core.typed_power_operation` | no | entity_states | Runtime-only: needs signed Core registry admission for the observed Core release. |

### Reader purpose index

| Reader | Used by | Note |
|---|---|---|
| `gateway_reader:history_reader` | history | Backs get_history_slices (ha_get_history on reviewed 8.5.0/8.6.0 only). |
| `gateway_reader:script_dependency_reader` | scripts | Backs dependency analysis of stored scripts (ha_config_get_script). |
| `gateway_reader:yaml_config_reader` | packages_and_yaml_files, helpers_options_supported, helpers_options_other, config_entries_and_integrations, recorder_settings | Wraps ha_config_get_yaml for one YAML_READER_PURPOSES purpose; admitted on ha-mcp 8.4.3 and 8.5.0, held on 8.6.0. |
| `yaml_reader:configuration_definitions` | helpers_options_supported, helpers_options_other, config_entries_and_integrations | Allowlisted configuration.yaml paths read for get_configuration_definitions. |
| `yaml_reader:recorder_settings` | packages_and_yaml_files, recorder_settings | configuration.yaml recorder key for get_recorder_settings. |

### Recorded inventory fingerprints

| Inventory | Items | Fingerprint |
|---|---|---|
| tool_catalog | 871 | `d8b63da79da9972d` |
| reader_purposes | 5 | `759281ffbf9c47e5` |
| core_profiles | 31 | `efb9a1de99d8a7da` |
| provider_registry | 199 | `b7beac319a361dd7` |

<!-- END GENERATED -->
