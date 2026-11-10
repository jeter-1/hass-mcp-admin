# HAMCP-264: YAML reader `dependency_index` purpose (design)

Status: design for review, written before code. Base `release/2.6.0` at
`4b8db82b`. Read-only analysis only.

## Objective (owner-approved, quoted)

> The dependency index can read YAML-defined template entities, packages and
> stored/package scripts through the reviewed YAML reader under its own,
> separately audited purpose (own audit event, bounded paths and sizes,
> !secret values never resolved or emitted), so template and script references
> appear in entity_dependency_analysis, change_impact_analysis and plan
> disclosures with truthful completeness. UI template helpers stay unavailable
> (their only read path is an options flow that can write) until a signed
> read-only profile exists.

## 1. Boundary check (no STOP condition)

`UpstreamReadGateway.yaml_config_reader(audit, purpose)`
(`providers/upstream_read_gateway.py:3891-3965`) already hosts per-purpose
readers: `YAML_READER_PURPOSES` (`:785-788`) maps a purpose to a fixed path
set and its own audit event. Every purpose dispatches the same single
admitted `ha_config_get_yaml` route with one Engineering-built argument
(`{"yaml_path": <fixed path>}`) through `execute`, with the reader's L1-L5
pipeline, Core gate and response seal unchanged. A new purpose is one more
table row. It adds no upstream tool, no route, no write, no fallback and no
forwarding. No new public tool is needed: the evidence joins the existing
`entity_dependency_analysis`, `change_impact_analysis` and plan disclosures.

## 2. Purpose and separation

- Purpose name `dependency_index`; audit event `dependency_index_yaml_read`.
- Paths `DEPENDENCY_YAML_PATHS = {"template", "script", "homeassistant.packages",
  "homeassistant"}`. `homeassistant` is read only when `homeassistant.packages`
  is not found, solely to classify packages (same rule as
  `configuration_definitions`).
- `sensor`/`binary_sensor` are not needed: in Core 2026.11.0 a legacy
  `platform: template` entry creates no entity
  (`components/template/helpers.py` `async_setup_template_platform`: with no
  discovery info it raises a repair issue and returns).
- Separation: `configuration_definitions` cannot read `script`; `dependency_index`
  cannot read `sql`, `group`, `utility_meter`, `sensor`, `binary_sensor` or
  `recorder`. Each refusal happens before dispatch, with zero upstream calls.
  The events differ, so audit consumers can tell the purposes apart.
- `YamlConfigReader` gains an additive `purpose` field set by the gateway. The
  dependency collector uses only a reader whose `purpose == "dependency_index"`.
  Any other reader (for example a miswired `configuration_definitions` reader)
  makes zero reads and is reported `unavailable` with reason
  `yaml_reader_purpose_mismatch`.

## 3. Audit

The unchanged gateway record per read: `event`, `request_id`,
`parent_request_id`, `provider`, `upstream_tool`, `yaml_path` (one of the four
constants), `upstream_version`, `contract_fingerprint`, `result_status`,
`provider_dispatch_count`, `fallback_occurred: false`. It never carries a
value. A refused path makes no record and no dispatch.

## 4. Bounds

- Reads: at most 4 per index build (`template`, `script`,
  `homeassistant.packages`, and conditionally `homeassistant`). Each read is
  bounded by a 5 s timeout inside a 15 s YAML phase deadline. The reader's own
  bounds apply first (`MAX_DEPTH` 24, `MAX_NODES` 4000, include-limitation cap
  16, string truncation).
- Collector: `MAX_YAML_PACKAGES` 200, `MAX_YAML_TEMPLATE_SOURCES` 1000,
  `MAX_YAML_SCRIPT_SOURCES` 1000, `MAX_YAML_FINDINGS` 10000. A bound hit makes
  the row partial and adds a `GlobalBoundedTerminal` with reason code
  `yaml_template_source_limit_exceeded`, `yaml_script_source_limit_exceeded`
  or `yaml_finding_limit_exceeded` (each classified in
  `bounded_sources.bound_table`; the `yaml_` prefix keeps them distinct from the
  obligation ledger's existing `template_source_limit_exceeded`). A per-source
  obligation `coverage_failure` forwards its own reason code as a terminal.

## 5. `!secret` and `!env_var`

The reader never resolves either (it reads `configuration.yaml` only; L2
refuses `secrets.yaml`). It renders them as `{"secret_ref": name}` and
`{"env_var_ref": name, ...}`. Before any extraction, the collector replaces
every such mapping with the constant `"<reference_withheld>"` and keeps only a
count. The reference name is not emitted, logged, audited, fingerprinted or
hashed, so no hash can be reversed to it. Values already redacted by the
reader (`[REDACTED:...]`) stay redacted. A redacted read is partial (the
reader's HAMCP-191 rule), so completeness stays truthful.

## 6. Packages, as the reader supports them

The reader reads one key path of `configuration.yaml` and does not follow
`!include*`. Supported: `homeassistant.packages` as an inline mapping of
package name to inline package mapping. The collector reads `template` and
`script` from each inline package. Unsupported, reported as partial with a
count (never as absence): `packages: !include_dir_named ...`, a package whose
value is an include, labeled top-level keys (`template foo:`, `script bar:`;
the reader's path pattern cannot name them), and `script: !include
scripts.yaml` (where UI scripts live). Following other files is a separate
L1 widening and is out of scope.

## 7. Extraction and identities

- Template: each `template:` section (a list or one mapping, Core
  `EnsureList`) and each platform list in it (Core 2026.11.0 template
  `CONFIG_SECTION_SCHEMA`: alarm_control_panel, binary_sensor, button,
  climate, cover, device_tracker, event, fan, image, light, lock,
  media_player, number, select, sensor, switch, update, vacuum, water_heater,
  weather, plus legacy `sensors`/`binary_sensors`) is one document. The
  section's `triggers`/`trigger`, `conditions`/`condition`,
  `actions`/`action` and `variables` are analysed with the entity through the
  shared obligation extractor. `source_type` is `template`; `source_id` is
  positional, for example `template[0].sensor[2]` (configuration.yaml) or
  `packages.<name>.template[0].light[0]` (`packages[<i>]` when the package name
  is not a safe label). A package's `template` and labeled `template <x>`
  sections are numbered as one list, as Core merges them. A `use_blueprint`
  section is partial.
- `source_entity_id` comes from the registry only when the registry is complete
  and exactly one registry row with platform `template` and no config entry
  has the unique id Core builds (`"<section unique_id>-<unique_id>"` or
  `<unique_id>`; Core `template/helpers.py`). Otherwise it is null.
- Script: each key under `script` (Core merges `script` as a dict; the key is
  the object id). `source_type` is `script`; `source_id` is the key;
  `source_entity_id` is mapped from a registry unique id when unambiguous.
  In `entity_dependency_analysis`, a YAML script that the loaded-script
  diagnostic already analysed is dropped (the loaded form is authoritative).
  The remaining YAML-only scripts carry provider `upstream_read_gateway`.

## 8. Truthful completeness

- Reader absent (not admitted, for example ha-mcp 9.1.x with its filesystem
  flag off, where `ha_config_get_yaml` is not in the catalog), not current, or
  purpose mismatch: template and the YAML script row are `unavailable` with the
  reason, provider `none`. Never `complete`.
- Template and the YAML script row are at most `partial`, even when every read
  succeeded: labeled top-level keys (`template x:`, `script x:`) cannot be named
  by the reader's path pattern, so their absence cannot be proven. Each row
  warns about that limit and lists counted gaps (includes, truncation,
  blueprint sections, non-inline packages, bounds, incomplete registry, UI
  template helpers, withheld references). No path or value is emitted.
- In `entity_dependency_analysis` the YAML script row merges with the
  loaded-script diagnostic (also partial by design).
- Read failure, timeout or authority change mid-phase: `unavailable` for the
  failed row; the findings already gathered in a phase that lost authority are
  dropped.

## 9. UI template helpers stay unavailable

UI template helpers are config entries (registry rows with platform
`template` and a config entry). Their only configuration read path is an
options flow that can write, so they are never read. They are counted from the
registry, and a non-zero count keeps the template row partial with the warning
"UI template helpers not read; their only configuration path is an options
flow that can write." No config-entry or options-flow call is added.

## 10. Consumers

- Evidence joins the HAMCP-227 diagnostic partition (`GlobalSourceDiagnostics`):
  outside the governance-bound snapshot findings and fingerprint, so no
  approval binding changes. Its fingerprint gains the YAML evidence ids and row
  states, which changes only query/cursor identity.
- The partition gains a fifth row, `script` (YAML scripts). `visible()` also
  withholds YAML template and script evidence when the YAML reader is no longer
  current, as it does for dashboards (withheld reason
  `yaml_provider_authority_changed`).
- `configuration_integrity_analysis` does not consume template or YAML script
  findings (its partition filter stays scene, group, dashboard). Its template
  row stays `not_supported` with a HAMCP-264 warning that names the consuming
  analyses, so the partition's template row cannot overstate integrity
  coverage (`integrity/provider.py`, one analysis-tool module edit).
- `entity_dependency_analysis`: merged as today plus the YAML script row.
- `change_impact_analysis`: `PARTITION_SOURCE_TYPES = (*GLOBAL_SOURCE_TYPES,
  "script")` replaces `GLOBAL_SOURCE_TYPES` in its three partition checks, so
  the template and YAML script rows show their real provider and completeness.
  Loaded scripts are still not consumed there (unchanged).
- Plan disclosures (`governance/global_reference_disclosure.py`):
  `REFERENCE_SOURCE_TYPES` adds `template` and `script`, and `SOURCE_TYPES`
  adds `script`. These are additive keys in an informational disclosure that
  is outside every plan hash. Interpretation: this module is one of "the
  analysis tools' modules you must touch", because the objective names plan
  disclosures. `CONSUMED_SOURCE_TYPES` (integrity, incident, handoff) is not
  changed.

## 11. Negative reachability

No new public tool, upstream tool, route, write, service call, reload,
restart, deletion, fallback, direct-HA config read, include following,
`secrets.yaml` read, options-flow or config-entry call, or forwarding. No
caller-selected path. No change to governance hashes, plan schemas or approval
meaning. `governance/service.py` and `approval_web.py` are not touched.

## 12. Wiring and limits

- `application.py` wiring (`DEPENDENCY_ANALYSIS...provider.yaml_reader_factory =
  lambda: UPSTREAM_READ_GATEWAY.yaml_config_reader(audit=audit,
  purpose="dependency_index")`) sits next to the script and dashboard factories.
  `application.py` is not in this task's authorized protected surfaces, so the
  exact wiring is recorded in `.artifacts/pending-wiring.md` for the
  orchestrator. Until it is wired, runtime behavior is the truthful
  `unavailable` row.
- Limits: includes, package directories, labeled keys and `scripts.yaml` are
  not read. YAML-only scripts get no script-call graph edges. Template
  entities without a unique id get no registry identity.
