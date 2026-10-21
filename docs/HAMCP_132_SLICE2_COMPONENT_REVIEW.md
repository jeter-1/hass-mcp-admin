# HAMCP-132 Slice 2, Part 0: `ha_mcp_tools` 2.2.1 Exact-Source Review

Status: **review complete; owner decision pending (decision (iii)).** Until
Josh records a decision, slice 2 does not call any `ha_mcp_tools/*`
WebSocket command or service. UI-defined definitions are reported as
`definition_not_readable_without_flow` (decision (i)), and capability
diagnosis uses Core-only reads.

This review complements the design note `docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md`
(PR #291), sections 3 B2(b), 3 D and 8 (iii).

Scope and method:

- Static reading only. No live Home Assistant, deployed endpoint or
  credential was accessed.
- Inputs:
  - the upstream `homeassistant-ai/ha-mcp` repository and its HACS mirror
    `homeassistant-ai/ha-mcp-integration`, with tags;
  - Home Assistant Core 2026.10.1 (`abe1fdd7cd6b4de1715c87b6fd87d92699514ef9`,
    tarball sha256 `a4305046e34fb6bad4669915c64dfaec0f2b22ff56b6baefca9e8890cabd81c4`);
  - this repository at `162200ee0356fc26e944b899770da3d2862ccdf8`.
- Labels: **Verified** means read in the exact source. **Not verified**
  means inferred, or needing live observation.

## 1. Version identity: "2.2.1" is not one source

The string `2.2.1` (`manifest.json`, `const.py:28 COMPONENT_VERSION`) appears
in **11 commits with 10 distinct component trees**. Verified.

- **Range:** from `efa34849` (2026-09-18, #2481) through upstream v8.6.0.
  - 2.2.0 shipped in v8.5.0.
  - 2.2.2 shipped in v8.7.0.
- **The only released 2.2.1** is the HACS mirror tag `v2.2.1`:
  - tag object `b68a55b2a905055830ac849127d0d488be1c0ff3`, commit
    `49f67785785e9d8eb47b885aea472a611bf5abaf`, published
    2026-09-30T16:32:03Z;
  - "Synced from ha-mcp v8.6.0";
  - component tree `a2b8b98375c709d47360cd1ed2ca0f65d81c67c7`, identical to
    upstream `70e71d9efdc842453579cccffa65aa76f173cc76` (v8.6.0).
- **The mirror's default branch** also holds snapshots of all seven mainline
  2.2.1 trees. A default-branch or development install during
  2026-09-18..30 could therefore be any of them.
- **No read-only distinction is possible.** Core `manifest/get` and the
  component's own `info` command report only the string `2.2.1`.
  Distinguishing the trees would require hashing files on the host, which has
  no read-only Engineering path.
- **The owner's installed tree is not verified.**

### Reviewed pin (released 2.2.1)

| Identity | Value |
| --- | --- |
| Component tree (content ID) | `a2b8b98375c709d47360cd1ed2ca0f65d81c67c7` |
| Upstream commit | `70e71d9efdc842453579cccffa65aa76f173cc76` |
| Mirror tag / commit | `v2.2.1` / `49f67785785e9d8eb47b885aea472a611bf5abaf` |
| 54-file manifest digest | `dd439a82279b241419044e7eab4f5a019980b8bea779634d6535f7802add39cd` |
| `websocket_api.py` sha256 | `3d45a0e54bf39d7d4db62e86de51e2306883ed720406e23c679279fe3e4c97b8` |
| `__init__.py` sha256 | `a6ae8b1c2aea6ca0163efdfe477eee52aaf0d6a3837fe544f88cbfad4772c5e1` |
| `manifest.json` sha256 | `65be388f556bc6619fe93182110a9b32d7f05db96ab988568b9aa98875fd33b1` |

How the manifest digest was computed:

```text
cd <extracted tree>
find custom_components/ha_mcp_tools -type f -print0 | LC_ALL=C sort -z \
  | xargs -0 sha256sum | sha256sum
```

A `git archive` digest is not used as the identity because it embeds the
commit, so the same tree under different commits gives different bytes.

## 2. Commands and services (tree `a2b8b983`)

### WebSocket commands

Verified in `websocket_api.py`:

- There are 25 commands, all named `ha_mcp_tools/*`.
- Every command passes through `_build_handler` (`:676`), which applies
  `@websocket_command` + `@require_admin` + `@async_response`.
- Commands are never unregistered.

| Class | Commands |
| --- | --- |
| Reads | `info`, `search`, `overview`, `states`, `blueprint_get`, `device_get`, `device_list`, `entity_enrich`, `exposure`, `registry_lookup`, `system_snapshot`, `entity_lookup`, `registries`, `dashboards`, `services_list`, `reference_data`, `server_entry`, `helpers_list`, `config_entries` |
| Credential-returning read | `backup_prep`: returns the backup manager's `default_password` |
| Template execution | `template_diagnose` (new in 2.2.1): renders a caller-supplied template in-process, bounded only by its timeout |
| **Writes** | `call_service`, `bulk_call_service` (arbitrary `hass.services.async_call`); `dashboard_edit` (Lovelace storage); `server_entry_update` (`async_update_entry`, which reloads the entry) |

### Services

The component registers 12 services, all `SupportsResponse.ONLY`. Verified.

- **Caller token.** `get_caller_token` checks admin only and returns the
  caller token, which is a credential.
- **File services.** `list_files`, `read_file`, `write_file`, `delete_file`
  and `edit_yaml_config` check the token only, not admin.
  - They deny `.storage` and `approval_pin.json`.
  - They return a masked root `secrets.yaml`.
- **`set_*` services.** These check the token and admin, and they widen the
  file and YAML scope.
- **Calls without a user context count as admin** (`_caller_is_admin`,
  `__init__.py:599-616`).

## 3. Can any command read UI-defined definitions?

### `config_entries` (`_do_config_entries` `:3792`, `_config_entry_row` `:3897`)

- **Arguments.** The schema is `{type, entry_id?, domain?}`.
  **With no filter it returns every config entry.**
- **Returned per entry:**
  - Core's identity and state fields;
  - `unique_id`, which Core's `config_entries/get` withholds;
  - `options`, passed through `_scrub_secret_values`;
  - subentry identities.
- **Never returned.** `entry.data` is never read. Subentry `data` is never
  returned in 2.2.1. Verified.

### `helpers_list`

- **Domain list.** It uses a hard-coded 19-domain `FLOW_HELPER_DOMAINS`
  (`:523`). The list **excludes `sql`**: Core 2026.10.1 classifies `sql` as
  an `integration`, not a `helper`.
- **Flow helpers.** It returns their scrubbed `options`.

### Flows

No command opens a config, options, reconfigure or subentry flow. Verified.

### Net

For helper flows whose definition lives in `entry.options` (template and most
helpers; SQL's `query` and `column`), the component can return the
definition. Anything in `entry.data` is unreachable.

## 4. Secret exposure

### SQL `db_url`

Core 2026.10.1's SQL flow is version 3 (`sql/config_flow.py:152`):

- It stores `db_url` in `entry.data` (`:185-186`, `:237-241`).
- `async_migrate_entry` (`sql/__init__.py:107-150`) moves a version-1
  `options.db_url` into `data`.

So `config_entries` does not return it for a migrated or new entry. Verified.

Not verified: a version-1 entry that was never set up since the migration may
still hold `db_url` in `options`, and the component would return it.

### The component's scrub

The scrub is best-effort:

- It replaces a scalar leaf only when the leaf **exactly equals** a
  `secrets.yaml` value.
- It misses credentials typed in the UI, substrings (for example a password
  in URL userinfo), and dict keys.
- With `secrets.yaml` missing, it scrubs nothing.
- Unfiltered `config_entries` returns the options of every integration.

### Engineering's position

Engineering's own withholding of `db_url` and connection keys before
sanitization (design note section 6) must remain authoritative whatever the
component does.

## 5. `ha_mcp_tools/info`

The command is admin-only and has no side effects. Verified. It returns:

- `schema_version` (1);
- `component_version` (the hard-coded `2.2.1`);
- `capabilities`;
- `limits`;
- `timezone`;
- `tools_services`.

## 6. Engineering authority

- **Credential scope.** Engineering's credential satisfies `require_admin`, so
  it could also reach every write command and `backup_prep` above.
- **Current use.** Engineering sends no `ha_mcp_tools/*` command today:
  - `git grep 'ha_mcp_tools/'` finds descriptive text only;
  - `clients/websocket.py _BATCH_COMMAND_KEYS` admits none.
- **The component is already an indirect dependency.**
  - Delegated ha-mcp 8.5.0 tools call `helpers_list`, `config_entries` and
    `info`.
  - The admitted `ha_config_get_yaml` route goes through the component's
    services (`upstream_yaml_reader.py:121`; `ha_core_readmission/routes.py:83`
    records those service routes as unattested).
- **Direct dispatch would be a new surface.** Dispatching component commands
  from Engineering's own WebSocket client would be a **new Engineering-native
  dispatch surface into third-party code**. That needs its own allowlist entry
  with exact keys, a source gate, negative tests and focused review.

## 7. Version deltas around 2.2.1

### 2.2.0 to 2.2.1

Unchanged:

- `config_entries`;
- `helpers_list`;
- the scrub;
- `FLOW_HELPER_DOMAINS`;
- `info`, apart from the capability list.

Added:

- `template_diagnose`;
- the `search_unified` capability;
- `approval_pin.json` in the read deny list.

### 2.2.1 to 2.2.2

- `websocket_api.py` becomes a package.
- `config_entries` gains an opt-in `include_subentry_data`, which returns
  subentry `data`. **This is a new exposure.**
- The helper domain list follows Core's generated list, which still excludes
  `sql`.
- The scrub and the `info` field set are unchanged.

## 8. Recommendation

### (a) UI-definition reads: **do not use** the component in slice 2

Keep `definition_not_readable_without_flow`. Reasons:

1. **Ambiguous source.** `2.2.1` names ten trees, and no read-only
   observation distinguishes them.
2. **New trust surface.** The same admin credential reaches `call_service`,
   `bulk_call_service`, `dashboard_edit`, `server_entry_update` and
   `backup_prep`.
3. **Weak scrub.** It works on exact leaves and covers `secrets.yaml` values
   only, while unfiltered `config_entries` returns every integration's
   options.
4. **The gap stays open.** `entry.data`, which holds the SQL `db_url`
   connection, and subentry data stay unreachable, so the main gap remains.

If Josh decides to use it anyway, the minimum conditions are:

- **Reviewed pin.** Record tree `a2b8b983` as the reviewed source. At runtime
  only the string `2.2.1` is observable, so report it as
  `version_only_unverified_tree`.
- **Exact dispatch.** Send exactly
  `{"type": "ha_mcp_tools/config_entries", "domain": <one reviewed helper domain>}`.
  - Never unfiltered.
  - Never `entry_id` alone.
  - Never `include_subentry_data`.
- **Engineering keeps control.** Engineering's withholding and sanitizer stay
  authoritative.
- **No flows of any kind.**

### (b) Capability diagnosis: **Core-only signals**

| Read | Gives |
| --- | --- |
| `config_entries/get {"domain": "ha_mcp_tools"}` | presence, `state`, `disabled_by`, whether a reason is present |
| `manifest/get {"integration": "ha_mcp_tools"}` | presence and the version string |
| Core component list (`GET /api/config` `components`) | whether `ha_mcp_tools` is loaded |

`ha_mcp_tools/info` is not dispatched. The ha-mcp server already probes it
internally.

### Fail-closed rule

- **Reviewed version:** only the manifest version string `2.2.1` is reviewed.
  - Any other version (2.2.0, 2.2.2, a development build, missing or
    unparsable) is `component_version_unreviewed`.
  - With such a version, nothing component-dependent is attempted.
- **Absence:** an absent or disabled entry, an entry that is not `loaded`, a
  `manifest/get` `not_found`, or an `unknown_command` maps to the
  deployment-dependency classification.
  - There is no fallback to component commands or options flows.
- **Even `2.2.1` unlocks nothing.** It is reported as
  `version_only_unverified_tree` and never unlocks a definition read.

## 9. Implemented behaviour in slice 2 (pending the decision)

`get_configuration_definitions` follows (b) and the fail-closed rule:

- **Capability block.** It reports `manifest_version`, `version_review`
  (`reviewed_version_only_unverified_tree`, `component_version_unreviewed` or
  `not_installed`), the config-entry state and the loaded status.
- **No component commands.** It sends no `ha_mcp_tools/*` command and reports
  `component_commands: not_used_pending_owner_review`.
- **UI-defined definitions** are always reported as
  `definition_not_readable_without_flow`.

## 10. Not verified

- Which of the ten trees the owner has installed.
- Whether the owner has a component "server" entry.
- Whether any unmigrated version-1 SQL entry exists.
- The component's HTTP, OAuth and webhook views, the embedded server, and the
  event-bus approval path. These were not reviewed in depth.

## 11. Decision requested

Josh: approve, reject or defer the use of `ha_mcp_tools/config_entries` for
UI-defined helper definitions under the conditions in section 8 (a). The
recommendation is **reject for slice 2**. The diagnosis in section 8 (b)
needs no component command and is implemented regardless.
