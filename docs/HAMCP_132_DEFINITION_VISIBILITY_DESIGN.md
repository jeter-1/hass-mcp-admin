# HAMCP-132 Effective Recorder Settings and Definition Visibility: Design Note

Register: HAMCP-132. Related: HAMCP-162 (`ha_config_get_yaml`, the YAML
reader), HAMCP-130 (the native read-only tool pattern), HAMCP-191 (the shared
strict sanitizer), HAMCP-200 (the closed REST `Route` registry), HAMCP-164
(the Core gate).

Status: **Part 1: sources report and design. Owner decisions recorded
(section 8, Josh, 2026-10-20): every decision as recommended.** This document
makes no runtime change. Slice 1 (A + C) is implemented in a separate pull
request; slice 2 (B + D) is later.

Base: `origin/main` `e42fa152` (Engineering `2.4.1-beta.25`; HAMCP-215 →
beta.26 and HAMCP-213 → beta.27 are pending). The release number is taken at
materialization: the next free beta.

## 1. Problem

The owner's Home Assistant audit cannot currently see:

1. **What the recorder keeps:** retention, purge and repack behaviour, the
   include and exclude filters, and whether a given entity is recorded.
2. **The definitions behind template helpers.**
3. **Two database (SQL) sensors** that appear in the state machine but were
   not found in the entity registry.
4. **Why `ha_config_list_helpers` reports `capability_unavailable`** for flow
   helpers.

A related trap: a SQL sensor reporting about 45 MB of "free space" must not
be read as host disk space. Engineering never interprets that value. It shows
the sensor's definition (the query) where it can be read, and that definition
says what is being measured.

## 2. Part 1: verification against the pinned Core source (ADR-021)

Authority: `ci/support-window.yaml`. The candidate lane is `none`.

| Lane | Tag | Commit | Archive sha256 (codeload.github.com tarball) |
| --- | --- | --- | --- |
| current | `2026.10.1` | `abe1fdd7cd6b4de1715c87b6fd87d92699514ef9` | `a4305046e34fb6bad4669915c64dfaec0f2b22ff56b6baefca9e8890cabd81c4` |
| rollback | `2026.10.0` | `6a811d3359c7b2076dc9e1cf900843a129c044af` | `96778e8c130c16e86619e82741d308e41c76d2963da649f0748f7b51fb9703b9` |

Every Core file cited below is byte-identical between the two tags:

- `components/recorder/`, `components/sql/`, `components/template/`,
  `components/config/`, `components/system_health/`;
- `helpers/entityfilter.py`, `helpers/config_entry_flow.py`,
  `helpers/entity_registry.py`, `helpers/check_config.py`;
- `config_entries.py`, `config.py`, `util/yaml/`.

Paths are relative to `homeassistant/`. Line numbers refer to `2026.10.1`.

Delegated `ha-mcp` facts come from the admitted `ha-mcp` 8.5.0 source (commit
`311d6dc2`, the admitted versions are 8.4.3 and 8.5.0; 8.6.0 is held). That
commit bundles the `ha_mcp_tools` custom component manifest `2.2.0`.

**Engineering credential.** The credential Engineering already uses satisfies
Core's `require_admin`. Today's reads already depend on it: `GET
/api/config/automation/config/{id}` is `@require_admin`
(`components/config/view.py:85`). So no source below needs a new permission
or an add-on configuration change.

## 3. Sources report

For each item: the source, Engineering's authority to read it, what it can
and cannot show, and its side effects.

### A. Effective recorder settings

#### A1. Configured values: the existing YAML reader (`ha_config_get_yaml`, `yaml_path: recorder`)

| Aspect | Finding |
| --- | --- |
| Source | Delegated `ha-mcp` `ha_config_get_yaml`. It calls the `ha_mcp_tools/read_file` custom-component service over the HAMCP-200 REST route and parses `configuration.yaml` from disk as YAML 1.2. |
| Authority | Already admitted (HAMCP-162). `validate_arguments` (`providers/upstream_yaml_reader.py:259-277`) accepts `recorder`, `recorder.exclude`, `homeassistant.packages`, `template` and `sql` as `yaml_path` (checked locally against the current source). `file` is fixed to `configuration.yaml`. `secrets.yaml`, `.storage` and every other L2 path stay refused. |
| Shows | The `recorder:` mapping exactly as written in `configuration.yaml`: `purge_keep_days`, `commit_interval`, `auto_purge`, `auto_repack`, `include`/`exclude` (`domains`, `entity_globs`, `entities`, `event_types`). |
| Cannot show | (1) **`!include` targets.** The reader returns the tag text and does not follow it. So `recorder: !include recorder.yaml`, or an `exclude: !include ...`, leaves those values unknown. (2) **Packages.** `merge_packages_config` (`config.py:675-806`) merges package `recorder:` keys recursively into the loaded config. The reader does not read package files (`packages` is an L2 deny segment), so it can only detect that packages exist, via `yaml_path: homeassistant.packages`. (3) **The loaded configuration.** The reader reads the file on disk. The recorder applies YAML only at startup, so an edit since the last restart makes the file and the running settings differ. Core keeps no copy of the raw recorder config (`components/recorder/__init__.py:139-170`). (4) **`!secret` values.** These are returned only as `{"secret_ref": name}`. |
| `db_url` | Connection keys are redacted whole by the reader (`CONNECTION_URI_SUFFIX`). A `!secret` reference returns only the secret's name. Section 6 adds a second, contract-level redaction because the shared sanitizer does not strip userinfo from non-http URLs. |
| Side effects | None. It is a file read through the custom component. Each call is audited. |

#### A2. Core's defaults for unspecified keys (pinned `CONFIG_SCHEMA`)

`components/recorder/__init__.py:100-133` (`extra=ALLOW_EXTRA`):

| Key | Default | Notes |
| --- | --- | --- |
| `auto_purge` | `true` | |
| `auto_repack` | `true` | |
| `purge_keep_days` | `10` | integer ≥ 1 |
| `commit_interval` | `5` (seconds) | |
| `db_max_retries` / `db_retry_wait` | `10` / `3` | |
| `purge_interval`, `db_integrity_check` | deprecated | reported as deprecated if present; never applied as defaults |
| `db_url` | none: `conf.get(CONF_DB_URL) or get_default_url(hass)` (`:147`, `:199-201`), giving SQLite at `<config>/home-assistant_v2.db` | never returned (section 6) |
| `include` / `exclude` | `domains`, `entity_globs`, `entities` default `[]`; `exclude.event_types` default `[]` | no filter means every entity is recorded (A4, case 1) |

**Source:** these defaults are compiled into Engineering from the pinned
schema. Each one is labelled as such in the result (for example,
`origin: "core_default"`, with the pin).

**Rule:** a key is reported as `core_default` only when the configured
source is *complete* for it. That means:

- the `recorder:` mapping was read;
- no `!include` tag appears at or above that key; and
- `homeassistant.packages` was read and is absent.

Otherwise the key is `unknown`, with the exact reason (`include_not_followed`,
`packages_present`, `packages_unreadable`, `yaml_unavailable`). Treating
packages as making a key `unknown` is owner decision (v).

**Side effects:** none.

#### A3. Runtime status: WebSocket `recorder/info`

| Aspect | Finding |
| --- | --- |
| Source | `components/recorder/basic_websocket_api.py:21-54`. |
| Authority | Not admin. Registered only when the recorder is set up; otherwise Core returns `unknown_command`. It waits for `db_connected` before replying (bounded by Engineering's command timeout). |
| Shows | `backlog`, `max_backlog`, `migration_in_progress`, `migration_is_live`, `recording`, `thread_running`, `db_in_default_location`. The last one says whether a custom `db_url` is in use without revealing it. |
| Cannot show | Retention, filters, or `db_url`. Also, `recording` means the event listener exists. It does **not** reflect the `recorder.disable`/`recorder.enable` services; Core has no read surface for that flag. |
| Side effects | None. |

`system_health/info` (database engine, version and estimated size) was
**rejected**. It is a subscription (initial, update and finish events), it
runs outbound probes for other integrations, and its recorder callback raises
before the database is ready. It cannot be expressed as one fixed `command()`.

#### A4. Is this entity recorded?

**Core's direct answer:** WebSocket `recorder/entity_options/get`
(`components/recorder/entity_options.py:50-68`). It is `@require_admin` and
takes one `entity_id` per command. It returns `{"recording_disabled_by": null
| "user"}`, computed by `is_entity_recorded` (`:16`):
`instance.entity_filter is None or instance.entity_filter(entity_id)`.

- It applies the **loaded** filter, the one built at startup, so it reflects
  what is running, not what is on disk.
- It does **not** reflect disable/enable or `exclude.event_types`.
- It is a read with no side effects.
- Engineering would send one command per entity through the batch path. That
  needs a new `_BATCH_COMMAND_KEYS` entry,
  `"recorder/entity_options/get": {"type", "entity_id"}`, with a strict
  entity-ID pattern and a bound (owner decision (iv)).

**Engineering's computed answer:** a port of `helpers/entityfilter.py:184-284`
(`_convert_globs_to_pattern` and `_generate_filter_from_sets_and_pattern_lists`),
evaluated over the A1 configured filter. Precedence must match Core exactly:

1. No include or exclude: everything is recorded.
2. Include only: recorded if in `include.entities`, in `include.domains`, or
   matching `include.entity_globs`.
3. Exclude only: recorded unless in `exclude.entities`, in
   `exclude.domains`, or matching `exclude.entity_globs`.
4. **Include domains or globs present (case 4a):**
   `entity in include.entities or (entity not in exclude.entities and
   (include glob matches or (domain in include.domains and no exclude glob
   matches)))`. **`exclude.domains` is ignored in this case.**
5. **Exclude domains or globs present, with no include domains or globs
   (case 4b):** if the domain is excluded or an exclude glob matches, the
   entity is recorded only if it is in `include.entities`; otherwise it is
   recorded if it is not in `exclude.entities`.
6. Only `include.entities`: recorded if listed.

Globs are matched with `fnmatch.translate`, as in Core.

**Proposal:** report Core's answer as primary and the computed answer as a
cross-check. A disagreement is reported as `filter_drift` (for example, the
file has changed since restart). It is never silently resolved. When the
configured filter is `unknown` (A2 rule), the computed answer is `unknown`
too.

### B. Template-helper and SQL-sensor definitions (slice 2)

#### B1. YAML-defined definitions: the YAML reader

- **`template:`** (`components/template/config.py`). The reader can read
  `yaml_path: template`. Labelled keys (`template <label>:`) contain a space,
  which `YAML_PATH_PATTERN` rejects, so they **cannot be addressed**. They are
  reported as `key_not_addressable`. Changing the pattern is a HAMCP-162
  contract change and is out of scope unless Josh adds it. Legacy `sensor:
  platform: template` is unsupported in this Core: it only raises a repair
  issue.
- **`sql:`** is a list of `query`, `column`, `name`, `unique_id`,
  `value_template` and optionally `db_url`. The reader redacts `db_url`.
  Legacy `sensor: platform: sql` is unsupported
  (`components/sql/sensor.py:64-80` creates an issue when `discovery_info`
  is `None`).
- **Packages and `!include`:** same limits as A1.
- **Side effects:** none.

#### B2. UI-defined definitions: config entries

| Option | Mechanism | Shows | Side effects and risk |
| --- | --- | --- | --- |
| (a) Options flow open and abort | `POST /api/config/config_entries/options/flow {handler: entry_id}` (`components/config/config_entries.py:287`, `@require_admin(... POLICY_EDIT)`), read the form's `suggested_value`s, then `DELETE /.../options/flow/{flow_id}`. | Template: the `init` step routes to the template-type step with suggested values equal to the stored options (`tests/components/template/test_config_flow.py:906-916`). SQL: query, column and `additional_options`; the form excludes `db_url`. | **A new write-class REST route pair** (POST and DELETE) under an edit permission, which is a security architecture change under AGENTS.md. Abort persists nothing and fires no events (options change only on `CREATE_ENTRY`). However: loading the flow handler may install requirements; an unknown entry raises `UnknownEntry` (HTTP 500); the template preview step registers `template/start_preview` for the life of the process; the SQL options flow debug-logs the raw `db_url`. Whether `suggested_value` survives schema serialization is **unverified** in this Core (the serializer was not in the extracted source set). |
| (b) `ha_mcp_tools/config_entries` (custom component WebSocket) | `@require_admin`; schema `{type, entry_id?, domain?}`. | Entry metadata plus `unique_id`, `options` and subentry identities. It **never reads `entry.data`** (so never the SQL `db_url` when stored in data). It scrubs resolved `!secret` values on a best-effort basis and sets `secret_scrub_degraded` when `secrets.yaml` cannot be read. | A read with no flow. However, it is a **new trust surface**: a third-party custom component command that Engineering would call directly rather than through an admitted `ha-mcp` tool. The owner runs component **2.2.1** (`docs/HAMCP_187_HA_MCP_870_REVIEW.md:7-18`), but the exact 2.2.1 source has not been established: it first appears at `efa34849`, which is not an ancestor of 8.5.0, and the HAMCP-187 capture used 2.2.0 and 2.2.2. Admission would need an exact-source review and a version pin. SQL `options` may contain a `db_url` key in some versions, so the section 6 redaction applies. |
| (c) Not readable | Report UI-defined entries as `definition_not_readable_without_flow`, with entry identity (from `config_entries/get`), state and title only. | Identity only. | None. |

`config_entries/get` (not admin; `config_entries.py:677-703`
`as_json_fragment`) **never** includes `data` or `options`. Diagnostics exist
for none of `recorder`, `sql` or `template`. Core has no other read-only
source of entry options.

**Recommendation:** (c) for slice 2 by default. Use (b) only if Josh approves
admitting the component command after an exact-source review. Do not use (a).
This is owner decisions (i) and (iii).

### C. State-vs-registry reconciliation (slice 1)

| Aspect | Finding |
| --- | --- |
| Sources | `GET /api/states` (already routed); `config/entity_registry/list` (already used); WebSocket `entity/source` (`components/websocket_api/commands.py:853-884`; not admin; returns `{entity_id: {"domain": platform}}` for every platform entity, registered or not, as set in `helpers/entity.py:1564-1570`); WebSocket `search/related` (already batch-allowlisted; not admin), which resolves a registry-less entity's config entry through `entity_sources`. |
| Shows | Entities in state but absent from the registry, with the platform that created each one (for example `sql` or `template`). Where `search/related` resolves one, it also shows the config entry. An entity with no `entity/source` row is a state written directly (for example via `POST /api/states` or `hass.states.async_set`), not created by a platform. |
| Cannot show | The YAML location. Registry-less platform entities almost always come from YAML without a `unique_id`. Core does not record which file or package defined them. A `name` match against B1 YAML is offered only as `candidate_definition` and never asserted. |
| Side effects | None. All are reads. |

Engineering already has the inverse rule (registry entry with no state) in
`integrity/rules.py`.

### D. Diagnosing `capability_unavailable` from helpers (slice 2)

Today, every one of these upstream conditions collapses into
`COMPONENT_NOT_INSTALLED`, which is public `unsupported_operation` with
`details.failure_category: capability_unavailable`
(`providers/upstream_read_gateway.py:4160-4196`, `:6303-6345`):

- the component is absent;
- the component is older than 1.1.0, or lacks `helpers_list`;
- a transient capability-probe failure (30 s negative cache);
- a `schema_version` mismatch;
- the client has no credentials;
- the component replies `unknown_command`;
- `covered_types` omits the type.

The reason can be told apart through read-only signals:

| Signal | Source | Authority | Distinguishes |
| --- | --- | --- | --- |
| Component config entry `state`, `reason` and `disabled_by` | `config_entries/get` with `domain: ha_mcp_tools` (Core, not admin) | already routed for other domains | not installed; installed but `setup_error`/`setup_retry`/`not_loaded`; disabled |
| Integration manifest version | `manifest/get` with `integration: ha_mcp_tools` (Core; `not_found` if absent) | basic WebSocket read | missing compared with present, and the installed version |
| Component capability info | `ha_mcp_tools/info` (component WebSocket): `component_version`, `schema_version`, `capabilities` | new component command; same trust question as B2(b) | version too old, capability missing, schema mismatch |
| WebSocket error code | Core codes (`unknown_command`, `unauthorized`, `not_found`, `timeout`, …) | already observed | not registered compared with permission compared with transient |

**The owner's live cause is not established by this report.** Live access
is out of scope. The diagnosis tool reports which signals it read, what each
showed, and a classification: `upstream_feature_gap`, `deployment_dependency`,
`permission`, `transient` or `undetermined`. It never infers more than the
signals support.

## 4. Proposed tools

All proposed tools are native, read-only and `reviewed_native_read`, with
outputs passed through `sanitize_untrusted_data` plus the section 6
redaction. Every result carries:

- `observed_at`;
- `sources[]`: each with `source`, `status` (`complete` | `partial` |
  `unavailable` | `refused`), `reason` and `observed_at`;
- `coverage`;
- `omissions[]`;
- `complete: bool`, which is true only when every source is complete.

### Slice 1 (A + C)

**`get_recorder_settings`**

Input:

```json
{"entity_ids": ["sensor.example"]}
```

`entity_ids` is optional, with 0–50 strict entity IDs (the bound is owner
decision (iv)).

Output (shape):

```json
{
  "settings": {
    "purge_keep_days": {"value": 10, "origin": "core_default", "pin": "2026.10.1"},
    "auto_purge":      {"value": true, "origin": "configured"},
    "commit_interval": {"value": null, "origin": "unknown", "reason": "packages_present"},
    "db_url":          {"origin": "configured", "value_withheld": true, "default_location": false}
  },
  "filters": {"origin": "configured", "include": {...}, "exclude": {...},
              "filter_case": "4a", "exclude_domains_ignored": true},
  "runtime": {"recording": true, "thread_running": true, "backlog": 0,
              "migration_in_progress": false, "db_in_default_location": false,
              "disable_service_state": "not_observable"},
  "entities": [{"entity_id": "sensor.example",
                "core_recorded": true, "computed_recorded": true,
                "agreement": "agree"}],
  "sources": [...], "omissions": [...], "complete": false
}
```

Sources: delegated `ha_config_get_yaml` with `yaml_path` `recorder` and
`homeassistant.packages` (the HAMCP-209 precedent for native tools composing
an admitted delegated read); `recorder/info`; batch
`recorder/entity_options/get`.

**`get_unregistered_entities`**

Input:

```json
{"domains": ["sensor"], "limit": 200}
```

Both fields are optional.

Output: `entities[]`, each with:

- `entity_id`;
- `state_present: true`;
- `registry_present: false`;
- `platform` (from `entity/source`, or `null`, which means a directly written
  state);
- `config_entry_id` (from `search/related`, or `null`);
- `definition_location` (`"not_determinable"` plus optional
  `candidate_definition` notes).

Also `counts`, `truncated`, `sources` and `complete`.

### Slice 2 (B + D)

**`get_helper_definitions`**

Input:

```json
{"kind": "template" | "sql", "entity_ids": [...]}
```

`entity_ids` is optional.

Output: `definitions[]`, each with:

- `origin`: `yaml` | `ui_entry`;
- the definition fields (template text, or `query`/`column`/`name`), with
  `db_url` withheld;
- the `yaml_path`, or the entry identity;
- `readability`: `read` | `definition_not_readable_without_flow` |
  `key_not_addressable` | `include_not_followed`.

The result also has a `capability` block (D), filled in whenever the
upstream helper listing is unavailable.

The two slice 2 tools could be folded into one; this is owner decision (vi).
If D stays separate, it is `diagnose_helper_capability`, which takes no
input and returns the D signals and classification.

### Core gate mapping (`ha_core_readmission/routes.py STATIC_TOOL_CORE_REQUIREMENTS`)

| Tool | Requirements |
| --- | --- |
| `get_recorder_settings` | `core.basic_rest_read`, `core.basic_websocket_read` |
| `get_unregistered_entities` | `core.basic_rest_read`, `core.basic_websocket_read`, `core.non_device_registry_read` |
| `get_helper_definitions` | `core.basic_rest_read`, `core.basic_websocket_read` |
| `diagnose_helper_capability` | `core.basic_websocket_read` |

Each tool is added to `tests/test_hamcp164_core_gate_fail_closed.py`.

## 5. Protected surfaces Part 2 would touch

These need Josh's explicit authorization in the Part 2 task.

- `hass_mcp_engineering_beta/ha_mcp_engineering/`: new `recorder_settings/`
  and `entity_reconciliation/` packages (slice 1); registration in
  `tools/registry.py`, `application.py`, `capabilities.py`,
  `providers/models.py`, `providers/routing.py`, `routing.py` and
  `ha_core_readmission/routes.py`.
- `clients/websocket.py`: new `_BATCH_COMMAND_KEYS` entry
  `recorder/entity_options/get`. The single `command()` path, with fixed
  contract payloads, carries `recorder/info` and `entity/source`.
- No new `ROUTE_TEMPLATES` entries for slice 1. `/api/states` and the YAML
  reader's service route already exist. Option B2(a) would add
  `POST`/`DELETE` options-flow routes; that is not recommended.
- Release (owner-authorized at materialization):
  `hass_mcp_engineering_beta/config.yaml`, `version.py`,
  `scripts/validate_addon_metadata.py` and `.release/next-version`, via
  `scripts/promote_next_release.py --apply`.

Not touched: workflows, `.coderabbit.yaml`, the signed upstream registry, the
support window, stable v1, add-on options or permissions.

## 6. Security

- **Read-only.** No service call, flow, reload or `.storage` access. An AST
  test (the HAMCP-130 `NoWritePathTests` pattern) proves the new modules
  reach only the fixed read commands and routes above.
- **`db_url` is never returned.** The shared sanitizer redacts URL userinfo
  only for `http(s)`, and has no `db_url` key rule. So a database URL with
  userinfo under another scheme would pass through it. The new contracts
  therefore withhold every `db_url` value (and any value under a
  connection-shaped key, using the YAML reader's `is_connection_key`)
  *before* sanitization. They emit only `value_withheld: true` and, from
  `recorder/info`, `default_location`. A `secret_ref` name is also withheld.
  Mutation tests prove that no fixture credential string appears anywhere in
  the serialized output.
- **YAML reader refusals stay in force.** The tool never passes `file`, never
  requests `secrets.yaml`, and never follows `!include`.
- **No literal secret-shaped text** in fixtures. Credential-bearing fixture
  strings are assembled at test time from harmless fragments.
- **Bounded output.** `entity_ids` ≤ 50; reconciliation `limit` ≤ 500 with
  `truncated`. The YAML reader's own node and depth caps apply.
- **Admin command.** `recorder/entity_options/get` is admin-only but
  read-only (it has no side effects). It is added only to the batch
  allowlist with an exact key set and a strict entity-ID pattern.
- **Leak guard.** HAMCP-206 `PROBES` cover the new modules' test processes
  unchanged.

## 7. Tests and mutations (Part 2)

Fixtures are built from pinned Core shapes (`recorder/info` reply keys,
`entity_options/get` reply, `entity/source` reply, `CONFIG_SCHEMA` defaults).

| Test | Mutation that it must kill |
| --- | --- |
| Defaults are labelled `core_default` only when the source is complete; otherwise `unknown` with a reason | A default reported as `configured`; a default applied when packages are present |
| `db_url` withheld, including non-http URLs with userinfo and `secret_ref` | `db_url` leaked (redaction removed) |
| A failing YAML, `recorder/info` or `entity_options` source makes the result `partial` with an exact reason | A failed source reported as complete |
| Filter evaluation for cases 1–6, including 4a ignoring `exclude.domains` and the 4b `include.entities` override; each checked against a vendored copy of Core's truth table | Wrong filter precedence (the 4a/4b branches swapped; exclude domains honoured in 4a) |
| Core and computed answers disagree, giving `filter_drift` | Drift silently resolved |
| `recorder/info` returns `unknown_command`, so `runtime` is `unavailable` (the recorder is not set up) | Treated as "not recording" |
| Reconciliation: platform present or absent; `search/related` failure gives partial | Registry-less entity dropped; location asserted |
| Static: no write reachability; Core-gate mapping; batch allowlist exact keys | A new command reachable outside the allowlist |

Validation: focused suites plus mutations; PR CI fully green; Evidence tier
with `-Parallel 4` on the final head (log path and pass line recorded).

## 8. Owner decisions requested

| # | Decision | Recommendation |
| --- | --- | --- |
| (i) | Is opening and aborting an options flow acceptable for reading UI-defined definitions? | **No.** Report `definition_not_readable_without_flow`. It needs new POST/DELETE edit-permission routes, has process-lifetime and logging side effects, and its serialization is unverified. |
| (ii) | Slicing | **Slice 1 = A + C** (`get_recorder_settings`, `get_unregistered_entities`); **slice 2 = B + D**. |
| (iii) | Admit the `ha_mcp_tools/config_entries` (and `ha_mcp_tools/info`) component WebSocket commands as a read-only no-flow source for UI options and helper diagnosis? | **Defer to slice 2.** This needs an exact-source review of the component version the owner runs (2.2.1, whose source is not yet established) and a version pin. Until then, use (c). |
| (iv) | Per-entity `recorder/entity_options/get` bound | **50 entities per call**, sent through the batch path. |
| (v) | When `homeassistant.packages` exists (or `!include` is used), report unspecified recorder keys as `unknown` rather than as Core defaults? | **Yes, `unknown`**, because packages can supply recorder keys that the reader cannot see. |
| (vi) | Two tools per slice, or one tool per slice with a `scope` mode? | **Two tools in slice 1** for schema clarity; **one tool in slice 2** (`get_helper_definitions` with an embedded `capability` block). |
| (vii) | Should `template <label>:` keys stay unaddressable (a HAMCP-162 pattern change is out of scope)? | **Keep them unaddressable** and report `key_not_addressable`; a pattern change would be a separate task. |

### Recorded decisions (Josh, 2026-10-20)

All seven decisions were taken as recommended:

- (i) **No** options-flow open and abort. UI-defined definitions are reported
  as `definition_not_readable_without_flow`.
- (ii) Slice 1 = A + C now; slice 2 = B + D later.
- (iii) The `ha_mcp_tools` component commands are deferred to slice 2,
  pending an exact-source review of component 2.2.1 and a version pin.
- (iv) `recorder/entity_options/get`: at most 50 entities per call, through
  the batch path (a new `_BATCH_COMMAND_KEYS` entry with a strict entity-ID
  pattern).
- (v) With `homeassistant.packages` or `!include` present, unspecified
  recorder keys are `unknown`, not defaults.
- (vi) Two tools in slice 1: `get_recorder_settings` and
  `get_unregistered_entities`.
- (vii) `template <label>:` keys stay unaddressable (`key_not_addressable`).

## 9. Limitations

- These reads cannot observe the `recorder.disable`/`recorder.enable` state.
- Configured values come from disk. They can differ from the running recorder
  until the next restart. The Core answer from `entity_options/get` and the
  `filter_drift` cross-check surface this but cannot fully close it.
- `!include` targets and package contents remain unread, by design.
- Registry-less YAML entities cannot be traced to a file.
- The owner's actual `capability_unavailable` cause is not established in
  this report; that would need a live read, which is out of scope.
- The 45 MB "free space" SQL sensor is not interpreted. Its query is shown
  only once B is readable for its origin.
