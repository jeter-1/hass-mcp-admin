# HAMCP-130 — Backup Inventory (`get_backup_inventory`) — Design Note

- Register: HAMCP-130. Owner: Josh. Branch `agent/HAMCP-130-backup-inventory`.
- Base: `origin/main` `c205150ff429b6d5b47c8920132482238a4bfd72` (2.4.1-beta.22 plus
  HAMCP-203).
- Goal (owner): one **read-only** Engineering tool that answers "are my backups
  healthy?". It reports what backups exist, how recent and how complete they
  are, where they are stored, and whether automatic backups are working. It has
  no create, restore, delete, download or settings path, now or as a hidden
  option.

## 1. Part 1 — verification against pinned sources (ADR-021)

Pinned authorities:

- **Home Assistant Core 2026.10.1**, commit `abe1fdd7`. This is the support-window
  Core and the source already used by HAMCP-209.
- **Supervisor `c9e0008`**: `supervisor/api/middleware/security.py` and
  `supervisor/api/backups.py`.
- **ha-mcp 8.2.0 through 8.6.0**: the review records in
  `docs/evidence/upstream-read-compatibility/`.

Citations below are `file:line` in those trees.

### 1a. Core backup integration over WebSocket (chosen)

| Command | Handler | Authority | Returns |
|---|---|---|---|
| `backup/info` | `backup/websocket.py:43-76` `handle_info` | `@websocket_api.require_admin` (`:43`) | `agent_errors` {agent_id: str}, `backups` [ManagerBackup], `last_attempted_automatic_backup`, `last_completed_automatic_backup`, `last_action_event`, `next_automatic_backup`, `next_automatic_backup_additional`, `state` |
| `backup/config/info` | `backup/websocket.py:331-356` `handle_config_info` | `@websocket_api.require_admin` (`:331`) | `config` = `BackupConfigData.to_dict()` (`backup/config.py:132`) plus `next_automatic_backup` and `next_automatic_backup_additional` |

Field facts used by the projection:

- **`ManagerBackup`** (`backup/manager.py:102-109`, extending `AgentBackup` in
  `backup/models.py`) carries:
  - `addons` [{name, slug, version}], `backup_id`, `database_included`, `date`,
    `folders`, `homeassistant_included`, `homeassistant_version`, `name`,
    `extra_metadata`;
  - `agents` {agent_id: {protected, size}};
  - `failed_addons`, `failed_agent_ids`, `failed_folders`,
    `with_automatic_settings`.
- **Agent ids** are `{domain}.{unique_id}` (`backup/agent.py:34-39`).
  - Core's own local agent is `backup.local` (`backup/backup.py:33`).
  - Supervisor agents are `hassio.<mount or local>` (`hassio/backup.py:804`).
  - Home Assistant Cloud is `cloud.cloud`.
- **Folders** are closed to `share`, `addons/local`, `ssl` and `media`
  (`backup/models.py:19-25`).
- **Supervisor's full/partial `type` is not carried into Core.**
  `hassio/backup.py:128-160` (`_backup_details_to_agent_backup`) maps only
  `homeassistant_included`, `database_included`, `folders`, `protected` and
  `size`. The tool therefore derives a content scope (§3.2) instead of
  reporting `type`.
- **Automatic-backup timestamps.**
  - `last_attempted_automatic_backup` is set when an automatic backup starts
    (`backup/manager.py:1115`).
  - `last_completed_automatic_backup` is set only when that backup finished
    with **no agent error** (`backup/manager.py:1289-1293`).
  - An attempt that is newer than the last completion is therefore, truthfully,
    "not completed".
- **`agent_errors`** holds per-location read failures for that call
  (`backup/manager.py:678-732`, `async_get_backups`).
- **`backup/config/info` returns the plaintext encryption key.**
  `config.create_backup.password` is `str | None` (`backup/config.py:610,
  621`). See §3.4.

Authority:

- Engineering's WebSocket is `ws://supervisor/core/websocket` with the
  add-on's `SUPERVISOR_TOKEN` (`configuration.py:74, 187-188`).
- The Supervisor proxy authenticates as the Supervisor system user.
- Core's hassio integration creates and keeps that user in the admin group
  (`hassio/__init__.py:196-214`), so `require_admin` is satisfied.
- Engineering already relies on exactly this authority for `backup/info` in
  the governed backup-verification path (`governance/operational.py:84`).
- Cost: two WebSocket round trips, no new client and no new permission.

### 1b. Supervisor backups API (rejected)

- The add-on runs with `hassio_role: homeassistant` (`config.yaml:13`).
- In `security.py:121-127` that role may reach only `/.+/info`, `/core/.+` and
  `/homeassistant/.+`.
  - `/backups/info` and `/backups/{slug}/info` match `/.+/info`.
  - `/backups` itself, and everything else under it, needs `ROLE_BACKUP` or
    higher (`security.py:128-133`).
- `/backups/info` **excludes cloud backups**
  (`backups.py:216`, `if backup.location != LOCATION_CLOUD_BACKUP`).
- It has no automatic-backup schedule, retention, last attempt or last
  completion. Those live in Core's backup store, not in Supervisor.
- Using it would add a second administrative authority, the same reason the
  direct Supervisor path was rejected for snapshot creation
  (`docs/OPERATIONAL_ADMINISTRATION.md:168-170`).
- It covers less than 1a and gives no gain, so it is rejected.

### 1c. Delegated ha-mcp backup tools (rejected; none admitted read-only)

- `ha_manage_backup` is classified `mixed_or_requires_wrapper` in every
  reviewed release, for example
  `ha-mcp-8.5.0-contract-review.json` `changed_runtime_descriptors.ha_manage_backup`.
- It carries `destructiveHint`.
- Under HAMCP-184 F-05 its provider disposition is **held**
  (`docs/HAMCP_184_AUDIT_HARDENING_DESIGN.md`).
- Its only Engineering use is the governed snapshot-create contract
  (`providers/operational_backup.py:34`).
- No admitted read-only ha-mcp backup tool exists, so nothing is delegated.

### 1d. Decision

**Source: Core WebSocket `backup/config/info`, then `backup/info`.**

- It is the only source that covers local, network-storage, cloud and
  integration agents together with automatic-backup status.
- It uses existing authority only.
- No new permission, add-on option or `config.yaml` permission changes, so no
  stop condition applies.

## 2. Tool contract

- **Name:** `get_backup_inventory`.
- **Input:** `{"limit": int}`, with 1 ≤ limit ≤ 50 and a default of 25.
  - `additionalProperties: false`.
  - A strict integer: booleans, strings and floats are refused.
- **Annotations:** `readOnlyHint=true`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.
- **Capability metadata:** `risk=read`, `operation_class=read`,
  `routing=engineering_native`, `fallback=none`,
  `policy=backup_inventory_read_v1`, `direct_write_allowed=false`.
- **Provider matrix:** `trust_mode=reviewed_native_read` and
  `trust_profile=backup-inventory-read-v1`.
- **Core gate (HAMCP-164):**
  `STATIC_TOOL_CORE_REQUIREMENTS["get_backup_inventory"] = ("core.basic_websocket_read",)`.
  The tool is refused whenever that route is not attested for the running Core.
- **Catalog:** static Engineering tools go from 59 to **60**, and the advertised
  catalog from 87 to **88** (60 native and 28 delegated). With the optional
  HAMCP-162 YAML reader admitted it goes from 88 to **89** (60 + 29).
- **Raw-argument validation:**
  - The gateway validates arguments before the SDK (`routing.py`).
  - Invalid input returns `InvalidBackupInventoryRequest` / `invalid_request`
    and echoes nothing.
  - Invalid input is excluded from generic invalid-argument audit capture.
  - `audit_parameters` records only `{profile, limit, provider, fallback}`, with
    empty `resource_ids`.

### 2.1 Output (`schema: backup-inventory-v1`)

A sample generated from synthetic fixtures is in
`docs/evidence/hamcp130/sample-output.json`; `generate_sample.py` regenerates
it, and a test checks for drift. Top-level keys:

| Key | Meaning |
|---|---|
| `completeness` | `complete` or `partial` (§3.1) |
| `omissions` [{reason, count}] | Why the report is partial (≤ 40 reasons) |
| `omission_reason_count` | Count of omission reasons |
| `redaction` | Sanitizer tally, or null |
| `sources` [{source_id, command, status, reason, duration_ms}] | One per read |
| `thresholds` | `full_backup_max_age_days: 7`, `automatic_backup_max_age_days: {daily: 2, custom_days: 8}` |
| `definitions` | The meaning of `full` and `off_site` |
| `installation` | `supervisor`, `core` or `unknown`, from agent naming |
| `summary` | backup_count, listed_count, listed_limit, more_backups_not_listed, newest_backup, oldest_backup, newest_full_backup (each: id, name, date, age_days, content_scope, storage_classes), newest_backup_offsite_copy, newest_full_backup_offsite_copy (`confirmed`, `off_host_not_confirmed_off_site` or `none`), storage_locations [{location_id, storage_class, backup_count, newest_backup_date, read_error, selected_for_automatic_backups}], manager_state, last_action |
| `automatic_backups` | configured, schedule {recurrence, days, time}, retention {copies, days}, location_ids, location_overrides, includes {all_addons, addons, database, folders}, encryption_configured (flag), next_scheduled, next_scheduled_additional, last_attempted, last_completed, last_completed_age_days, last_result (`completed`, `in_progress`, `not_completed`, `never_attempted` or `unknown`) |
| `recent_failures` (≤ 20) and `recent_failure_count` | Kinds: `last_backup_action_failed`, `automatic_backup_not_completed`, `storage_location_unreadable`, `backup_partially_failed` |
| `health_signals` | Nine signals (§3.3) |
| `backups` (newest `limit`) | backup_id, name, date, age_days, size_bytes, content_scope, homeassistant_included, homeassistant_version, database_included, addons (names), folders, protected (flag), automatic, locations [{location_id, storage_class, size_bytes, protected}], failed_location_ids, failed_addons, failed_folders |
| `observed_at` | Report time |

Storage classes are derived from agent ids:

| Agent id | Storage class |
|---|---|
| `hassio.local` or `backup.local` | `local` |
| other `hassio.*` (Supervisor mounts) | `network_storage` |
| `cloud.*` | `home_assistant_cloud` |
| anything else | `integration_agent` |

## 3. Semantics

### 3.1 Truthful completeness

The report is `complete` only when all of the following hold:

- both reads succeeded;
- no omission was recorded;
- the sanitizer redacted nothing.

Any of the following makes it **partial**, with a reason:

- an unreadable source;
- a per-location `agent_errors` entry;
- a malformed or out-of-contract field;
- a dropped record;
- more than 500 backup records;
- a list cap reached;
- a response-budget trim;
- redaction.

If one source fails, the other still answers and the report is partial; signals
that depend on the missing source become `unknown` with
`evidence_complete: false`. If **both** fail, the tool returns
`analysis_unavailable`. An **authorization failure** on either read fails
closed (`authorization_failure`) and never produces a partial report.

Authority is checked again before and after each read, using the Core
generation, the version and the dispatch authorizer. A change in between fails
closed. An anonymous caller is refused before any dispatch.

### 3.2 Content scope (`full`, `partial` or `unknown`)

Core does not report Supervisor's `type` (§1a), so the scope is derived:

- **`partial`** if Home Assistant or the database is excluded, or any add-on or
  folder failed.
- **`unknown`** if any of those fields is missing.
- **`full`**:
  - on a Core installation, when Home Assistant and the database are included;
  - on a Supervisor installation, when, in addition, every Core-known folder is
    included.
- Otherwise `partial` on Supervisor and `unknown` when the installation is
  unknown.

Disclosed limit: Core does not say whether *every installed add-on* was
included, so `full` cannot attest that. The definition is stated in every
response.

`newest_full_backup` is the newest dated backup whose scope is `full`. A newer
`partial` or `unknown` backup never stands in for it; mutations N1 to N6 cover
this.

### 3.3 Health signals (facts, not alarms)

Each signal is `{signal, status, threshold, fact, evidence_complete}`, where
`status` is `observed`, `not_observed`, `not_applicable` or `unknown`.

| Signal | Observed when | Threshold |
|---|---|---|
| `no_backups` | The readable listing is empty | — |
| `no_full_backup_within_threshold` | The newest full backup is older than 7 days, or there is none. It is `unknown` if a recent backup has unknown scope | 7 days |
| `newest_backup_only_on_this_host` | Every copy of the newest backup is `local` | — |
| `newest_backup_no_confirmed_offsite_copy` | The newest backup has no Home Assistant Cloud copy | — |
| `last_automatic_backup_not_completed` | `last_result == not_completed` | — |
| `automatic_backups_not_scheduled` | Recurrence is `never`, or `custom_days` with no days | — |
| `last_completed_automatic_backup_stale` | The last completion is older than the threshold for the recurrence, or there has been none | daily 2 days; custom_days 8 days |
| `storage_location_unreadable` | Any `agent_errors` entry | — |
| `newest_backup_incomplete` | The newest backup records failed locations, add-ons or folders | — |

Off-site is **confirmed only for Home Assistant Cloud**. Network storage and
other agents are off-host, but Core does not attest where they are.

### 3.4 The encryption key

- `backup/config/info` is read **first**.
- If `create_backup.password` is a non-empty string, its value is added as a
  `known_secret` to this response's sanitizer. That sanitizer is the shared
  strict one from HAMCP-191, with exactly two call sites: `Projector.identifier`
  and `Projector.text`.
- The value is deleted from local scope right afterwards.
- The projection reads only whether the key is present, and reports that as
  `encryption_configured`.
- Effect: a backup name, error text or identifier that contains the key is
  redacted or withheld, and the report becomes partial.

Disclosed trade-off:

- A very short user-chosen key could over-redact ordinary names.
- The Home Assistant frontend generates long random keys. Over-redaction errs
  toward non-disclosure, and it is reported (`redaction`, `partial`).

The add-on access secret and the Core token are also known secrets, through
`BackupInventoryRuntime.configure`.

### 3.5 Bounds

| Limit | Value |
|---|---|
| Backup records read | 500 |
| Listed backups | 50 |
| Locations | 32 |
| Add-ons per backup | 50 |
| Folders | 16 |
| Failures | 20 |
| Name length | 160 |
| Error text length | 200 |
| Report JSON budget | 50,000 characters, trimmed from the oldest listed backups with the omission `backups_not_listed_response_bytes` |
| Served envelope | `min(60,000, response_size_limit)` |

Identifiers (backup id, location id, add-on slug, version, stage and reason
codes) must match closed patterns and survive sanitization unchanged; otherwise
they are withheld.

## 4. No write path

- The package's only Core dispatch is `self.client.command(dict(command))` in
  `service.py`.
- It is called only with `c.CONFIG_COMMAND` and `c.INFO_COMMAND`, which are
  read-only `MappingProxyType` values.
- There is no HTTP client, Supervisor, ha-mcp, governance, upstream or
  service-call import.
- Tests check all of this statically with the AST, and check at runtime that
  dispatch is exactly `[backup/config/info, backup/info]`. Mutations W1 and W2
  are killed.

## 5. Validation

- `tests/test_hamcp130_backup_inventory.py`: 48 tests.
  - Six fixture scenarios: local only; local + cloud (+ network storage);
    failed automatic backup; encrypted; partial backups; empty.
  - Also: in progress, never attempted or unscheduled, custom days, Core
    installation, unknown installation, mixed protection, ordering and limit.
  - Partial and failure paths: config failure, info failure (three error
    types), both failing, permission failure, unreadable location, 13
    malformed-field cases, the record cap, the response budget, and telemetry
    restore.
  - Authority: route unavailable, anonymous caller, generation change, missing
    authorizer, unconfigured runtime, invalid limits.
  - Sanitizer: forged and colliding markers, credential shapes, known secrets
    in identifiers, redaction alone making the report partial, and the
    encryption key never reaching output or audit.
  - No write path (static and runtime), Core-gate mapping, capability metadata,
    annotations and schema, served-tool raw-argument rejection, gateway
    pre-SDK rejection with audit privacy, gateway positive audit, and sample
    drift.
- `tests/test_hamcp191_shared_sanitizer.py`: the two new `SANITIZER_SITES`.
- Count and catalog assertions in existing suites: 59 → 60, 87 → 88 and
  88 → 89.
- Mutations (`docs/evidence/hamcp130-mutations/`): **17 of 17 killed**, none
  by an import error, against a 48-test baseline (`results.json`).
  - F1 to F7: a failed read reported as complete.
  - N1 to N6: newest-full picks a partial.
  - S1 and S2: sanitizer.
  - W1 and W2: fixed reads.

## 6. Protected-file list (finalized)

Runtime, under `hass_mcp_engineering_beta/ha_mcp_engineering/`. The task
authorizes a new tool module plus registration, capability, catalog and
Core-gate files.

| File | Change |
|---|---|
| `backup_inventory/__init__.py`, `contracts.py`, `projection.py`, `report.py`, `service.py` | New |
| `tools/backup_inventory.py` | New |
| `tools/registry.py` | Registration |
| `application.py` | Runtime composition |
| `capabilities.py` | Capability and provider matrix |
| `providers/models.py`, `providers/routing.py` | Capability, routing policy |
| `routing.py` | Pre-SDK validation, audit parameters |
| `ha_core_readmission/routes.py` | Core gate mapping |

Release, at materialization through `promote_next_release.py --apply`, with
owner authorization:

- `hass_mcp_engineering_beta/config.yaml`
- `hass_mcp_engineering_beta/ha_mcp_engineering/version.py`
- `.release/next-version` (consumed)

`scripts/validate_addon_metadata.py` is changed by the same script.

Additions beyond the brief's list are `application.py`, `providers/models.py`,
`providers/routing.py` and `routing.py`. They are the existing registration
surfaces every native tool touches.

Not changed:

- workflows;
- the signed registry;
- support windows;
- `hass_mcp_admin/` (stable v1);
- `.coderabbit.yaml`;
- `Dockerfile`;
- `repository.yaml`.

## 7. Overlap with PR #283 (HAMCP-209, beta.23)

- `tests/test_hamcp191_shared_sanitizer.py`: both PRs add `SANITIZER_SITES`
  entries.
- Release files: `config.yaml`, `version.py`, `validate_addon_metadata.py`,
  `CHANGELOG.md`, `README.md` and `.release/`. Whichever PR merges second needs
  a merge from `main`, using a merge commit, not a rebase.

This PR stages **beta.24** on the assumption that #283 takes beta.23 first. If
#283 does not land first, the version must be re-materialized.

## 8. Known limitations

- `full` cannot attest that every installed add-on was included (§3.2).
- Off-site is confirmed only for Home Assistant Cloud (§3.3).
- Over-redaction is possible with a very short user encryption key (§3.4).
- Signals reflect what Core records. Archive integrity is not verified, and no
  download or restore test is performed.
- Thresholds are fixed constants, stated in every response, and not
  configurable.

Rollback: revert the PR. The tool is additive, with no persisted state and no
schema migration.
