# HA MCP Engineering Server Tool Audit

Date: 2026-07-10

## Automation trace snapshots (2.4.1-beta.39, HAMCP-225)

Four `engineering_native` tools, no delegated provider, no fallback, no Home
Assistant write. `capture_automation_trace` (risk
`engineering_evidence_write`) sends only `trace/list` and `trace/get` with
`domain: "automation"` and requires `core.automation_trace_read`; it writes
sanitized snapshots to the Engineering-local `trace-snapshots-v1` store.
`list_trace_snapshots` and `get_trace_snapshot` are core-independent local
reads with caller-bound cursors. `set_automation_trace_watch` (`add`,
`remove`, `list`) is core-independent local state: no plan or approval, an
audited add or remove, at most 10 watches; the separately gated poller
issues the same two read commands every 30 seconds. `get_automation_trace`
keeps its input schema and now passes the shared trace sanitizer, which
applies the shared `credentials.screen` to every string and key first. The
catalog is 72 static + 28 delegated = 100 (101 with the optional YAML
reader). See
[HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md](docs/HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md).

## Host journal incident-log sources (2.4.1-beta.38, HAMCP-224)

`get_incident_logs` keeps its routing (`engineering_native`), provider
(`supervisor_incident_logs`), policy (`incident_window_journal_read_v1`),
`risk=read`, writes `none` and input schema. `core` and `addon:self` still
use the two fixed Supervisor routes. `supervisor`, `host`, `host:<category>`
and `addon:<slug>` are no longer `authority_not_granted`: they are read from
the host journal the Supervisor mounts read-only with `journald: true`,
through a `ctypes` reader of 14 fixed `libsystemd` symbols and 9 fixed
fields. No `manager` route is called and no role changes. A
credential-looking journal field is withheld whole by the
configuration-definitions screen (`credentials.screen`), and the result is
partial; journald off or
unreadable is `unavailable` with its reason. Only the tool description and
the capability justification text change. The catalog is still 68 static +
28 delegated = 96 (97 with the optional YAML reader). See
[HAMCP_224_HOST_JOURNAL_DESIGN.md](docs/HAMCP_224_HOST_JOURNAL_DESIGN.md).

## Automation and script deletion, inactive (2.4.1-beta.35, HAMCP-155)

`create_configuration_delete_plan` is a native governed planning tool:
`engineering_native` routing, governed configuration, no delegated provider,
no fallback. It only creates a plan; dispatch is the existing external
approval plus `apply_change_plan`. 1-8 `{resource_type: automation|script,
target_id}` deletions, ordered and non-atomic, stopping on the first
failure; no free-text target field.

Each deletion requires the runtime-only Core profile
`core.configuration_delete_operation`, which the signed registry (sequence
12) does not reference. Every call is refused with
`CoreCapabilityUnavailable` / `pending_signed_registry_admission` before any
Home Assistant read, with no plan stored; capabilities and health show the
same status. Once admitted, the only write is one no-body config `DELETE`
per child, sent by the dedicated gateway method; the generic configuration
write and `create_configuration_plan` still refuse `delete`, legacy
`delete_automation` stays prohibited and upstream `ha_config_remove_*` stays
unrouted. Verification is proof of absence; rollback recreates the exact
pre-image under a new approved plan. The catalog is 68 static + 28
delegated = 96 (97 with the optional YAML reader). See
[HAMCP_155_GOVERNED_DELETION_DESIGN.md](docs/HAMCP_155_GOVERNED_DELETION_DESIGN.md).

## Label create, edit and assignment, inactive (2.4.1-beta.34, HAMCP-165)

`create_label_plan` is a native governed planning tool: `engineering_native`
routing, no delegated provider, no fallback. It only creates a plan; dispatch
is the existing external approval plus `apply_change_plan`. 1-8 ordered
`create` or `update` operations over a label's `name`, `color`, `icon` and
`description`, each optionally assigning the label to exact registered
entities through the signed HAMCP-069A `entity_registry_metadata` path.
Labels are never deleted (HAMCP-155).

Its resource type `label_registry_entry` requires the runtime-only Core
profile `core.label_registry_operation`, which the signed registry
(sequence 12) does not reference. Every call is refused with
`CoreCapabilityUnavailable` / `pending_signed_registry_admission`, with no
plan stored and no provider contact; the governance service checks admission
first. Capabilities and health show the same status. The release activates
nothing new; it switches on with the sequence-13 signing. The catalog is 67
static + 28 delegated = 95 (96 with the optional YAML reader). See
[HAMCP_165_LABELS_DESIGN.md](docs/HAMCP_165_LABELS_DESIGN.md).

## Read-only configuration definitions (2.4.1-beta.33, HAMCP-132 slice 2)

`get_configuration_definitions` is native read-only evidence:
`engineering_native` routing, `risk=read`, policy
`configuration_definitions_read_v1`, no fallback.

For up to 50 entity IDs and/or whole integrations from a closed set of twelve,
it reads:
- `configuration.yaml` paths `template`, `sql`, `group`, `utility_meter`,
  `sensor` and `binary_sensor`, only those the request needs, plus
  `homeassistant.packages` and, only to classify a missing packages key,
  `homeassistant`. These go through the admitted YAML reader's new fixed
  `configuration_definitions` gateway purpose: attributed, audited as
  `configuration_definitions_yaml_read`, with no caller-selected path;
- REST `GET /api/states` (availability only) and `GET /api/config`
  (`components` only);
- WebSocket `config/entity_registry/list` and `entity/source`;
- `config_entries/get` for a closed domain;
- `manifest/get` for `ha_mcp_tools`.

It returns:
- each definition as a positive allowlist projection;
- the match method: `exact_config_entry`, `exact_unique_id`, `heuristic`
  with `high`/`medium`/`low` confidence, `ambiguous`, `unmatched` or
  `not_applicable`;
- a per-entity unavailability diagnosis;
- a Core-only capability block for the `ha_mcp_tools` component.

What it never does:
- return `db_url` (in any position), credential keys, credential URIs
  inside text, reference names or unrecognized keys;
- follow `!include`, packages or labelled keys (it discloses them and
  reports partial);
- read UI-created definitions. They are listed as
  `definition_not_readable_without_flow`;
- call any `ha_mcp_tools/*` command (owner decision, 2026-10-21).

A unique ID that Home Assistant's YAML 1.1 loader could read differently is
never an exact match, and ambiguity rules are identical from both sides.
Diagnosis never claims `definition_absent` after a YAML source gap or
`entity_level` without a dependency check, nor after an unexpanded template
blueprint. A malformed source value, a malformed registry identity included,
invalidates only its item and is never matched.

Config entry titles are shown through an allowlist: the whole original title,
never a truncated one, is shown only when it is printable ASCII within 255
characters with no `@`, `://`, password-like `key=value`, known secret or
credential text. Anything else is `[withheld:credential_text]` or
`[withheld:unverifiable_text]` and the report is partial; an ordinary
non-ASCII title is withheld by design.

A YAML-projected string or key with an invisible, format or control
character, literal or percent-encoded, is withheld whole as
`[withheld:unverifiable_text]`, never screened and shown. Every other
YAML-projected string is screened for credential text in two forms
(correction batch 4): its canonical form and that form with every
whitespace and line-control character removed, so whitespace inside a
credential, literal or percent-encoded, cannot hide it. Either form matching
withholds the original value whole as credential text. Tab, line feed and
carriage return stay displayable, so an ordinary multi-line template is
shown and the report stays complete. Any withheld form
anywhere in the final envelope makes the report partial, with a bounded
`withheld:<item>` omission per affected item. The one exception is a fixed
policy redaction, not missing evidence (owner decision, Josh, 2026-10-22): a
SQL definition's own `db_url` is always replaced with
`{"redacted_by_policy": "database_url"}` and, through an explicit allowlist
inside that same scan (SQL `fields.db_url` only), does not make the report
partial. A `db_url` anywhere else, a credential in any other SQL field, or a
policy-shaped value from configuration is still withheld and partial.
Nothing is derived from a withheld input (owner decision, Josh,
2026-10-22): a withheld name, key or unique_id input yields no derived entity
ID, generated unique ID, dependency, definition-ID key or legacy candidate,
and is listed as withheld, so the report is partial. The Core-observed entity
ID of an exact registry match is Core's data and is not withheld (owner
decision, 2026-10-22): an entity that Home Assistant itself named from a
credential-bearing string shows that entity ID; rename the entity in Home
Assistant.

At a single exit point every key and string of the final envelope (titles,
metadata, reasons, omissions included) is screened for credential text and
withheld as `[withheld:credential_text]`, making the report partial (the
checks read a bounded confusable fold of the canonical text); the
envelope is then sanitized once, with the known secrets. It is mapped in the HAMCP-164 Core gate (`core.basic_rest_read`,
`core.basic_websocket_read`, `core.non_device_registry_read`). Arguments are
validated before the SDK, and the audit carries counts only. There is no
write, reload, flow or service-call path and no new batch-allowlist shape.
The catalog is 66 static + 28 delegated = 94 (95 with the optional YAML
reader).

## Entity enable/disable and voice exposure, inactive (2.4.1-beta.31, HAMCP-069B)

`create_entity_availability_exposure_plan` is a native governed planning tool:
`engineering_native` routing, operational administration, no delegated
provider, no fallback. It only creates a plan; dispatch is the existing
external approval plus `apply_change_plan`. One operation (`disable`,
`enable`, `expose`, `unexpose`) over 1-8 exact registered entities; `assistant`
is `conversation` or `cloud.google_assistant` (Alexa refused); no free-text
field.

Its resource types `entity_registry_availability` and
`entity_assistant_exposure` require the runtime-only Core profiles
`core.entity_registry_availability_operation` and
`core.entity_assistant_exposure_operation`, which the signed registry
(sequence 12) does not reference. Every call is refused with
`CoreCapabilityUnavailable` / `pending_signed_registry_admission`, with no
plan stored and no provider contact; capabilities and health show the same
status. The catalog is 65 static + 28 delegated = 93 (94 with the optional
YAML reader). See
[HAMCP_069B_ENABLE_EXPOSE_DESIGN.md](docs/HAMCP_069B_ENABLE_EXPOSE_DESIGN.md).

## Read-only incident logs and continuable history (2.4.1-beta.30, HAMCP-131)

`get_incident_logs` and `get_history_slices` are native read-only evidence:
`engineering_native` routing, `risk=read`, writes `none`, no fallback.

`get_incident_logs` reads only two fixed Supervisor routes through HAMCP-200
typed routes: `GET /core/logs` and `GET /addons/self/logs`, with a gatewayd
`Range: entries=...` header. The window is at most 6 hours. Per call it makes
at most 24 requests (16 per source) and downloads at most 1 MiB.
`supervisor`, `host` and other add-on sources need Supervisor role `manager`.
They are reported `unavailable` with `authority_not_granted`, the pinned role
pattern and zero requests. An add-on slug must be one validated path segment.
The tool is classified Core-independent (Supervisor only), beside
`get_core_log_history`.

`get_history_slices` reads only through the admitted delegated
`ha_get_history` route (ha-mcp 8.5.0 or 8.6.0), using a new fixed gateway
`history_reader`. Engineering builds every argument: one entity, absolute UTC
slice bounds, ascending, minimal, offset and limit. The window is at most
7 days, and each call makes at most 12 upstream calls. It never uses the native
`get_history` tool. It is mapped to `core.basic_rest_read` in the HAMCP-164
Core gate.

Each tool sanitizes its complete response once, at a single exit point, with
the known secrets; a redaction makes the result partial. Cursors are
HMAC-signed and process-keyed, and continuity breaks are refused as stale. The
catalog is 64 static + 28 delegated = 92 (93 with the optional YAML reader).
See [HAMCP_131_INCIDENT_LOGS.md](docs/HAMCP_131_INCIDENT_LOGS.md).

## Read-only recorder settings and unregistered entities (2.4.1-beta.27, HAMCP-132)

`get_recorder_settings` and `get_unregistered_entities` are native read-only
evidence: `engineering_native` routing, `risk=read`, no fallback.

`get_recorder_settings` reads:
- `configuration.yaml` paths `recorder`, `homeassistant.packages` and,
  only to classify a missing packages path, `homeassistant`, through the
  admitted YAML reader's new fixed `yaml_config_reader` gateway route (at
  most three delegated reads, attributed and audited, no caller-selected
  path);
- Core WebSocket `recorder/info`;
- for up to 50 entities, a batch of `recorder/entity_options/get`.

It reports each setting with its origin. A Core default is used only when
every source that could set the key is complete; otherwise the setting is
`unknown` with a reason. It also reports the filters with Core's filter case,
the runtime state, and Core's per-entity answer beside the computed filter,
with `filter_drift` on disagreement. `db_url`, reference names and
unrecognized keys are never returned. A value whose YAML 1.1 readings would
change Core's verdict is `parser_dependent`, never `fails_core_schema`.
A plain integer is definite, with the standing numeric-spelling disclosure,
and a quoted string is a definite string.
Without the YAML reader admitted, the tool still reports runtime and Core's
entity answers, as partial. Each tool sanitizes its complete response once,
with the known secrets, at a single exit point.

`get_unregistered_entities` makes zero delegated calls. It reads:
- REST `GET /api/states`;
- `config/entity_registry/list`;
- `entity/source`;
- a batch of `search/related` with `item_type: entity`.

It reports the platform, a directly written state, the config entry
(`ambiguous` when more than one) and `definition_location:
not_determinable`.

Both tools are mapped in the HAMCP-164 Core gate. The batch allowlist adds
exactly two shapes, each with a strict entity-ID pattern. There is no write,
purge, reload, flow or service-call path. The catalog is 62 static + 28
delegated = 90 (91 with the optional YAML reader). See the
[implementation notes](docs/HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md).

## Read-only backup inventory (2.4.1-beta.24, HAMCP-130)

`get_backup_inventory` is native read-only evidence: `engineering_native`
routing, `direct_ha_api` data, `risk=read`, no fallback and zero delegated
calls. It makes exactly two fixed Core WebSocket reads, `backup/config/info`
then `backup/info`, under the existing admin authority and the HAMCP-164
`core.basic_websocket_read` gate. It reports backups, the newest full backup,
storage locations, confirmed off-site copies, automatic-backup schedule,
retention and last result, recent failures and nine threshold-stated health
signals, and is `partial` with per-field reasons when anything cannot be
read. Each signal names the inputs it depends on and is `unknown` when any of
them is unreadable. The encryption key is never returned; when it cannot be
positively read, every name and error text is withheld and free-form
identifiers are aliased. An `unknown` signal always has
`evidence_complete: false`, invalid lists are `null`, and the omission ledger
is exactly bounded with a full reason count. A transformed (encoded or split)
copy of a known key is an accepted residual (`docs/SECURITY.md`). There is no
create, restore, delete, download or settings path. The catalog is 60 static + 28 delegated = 88
(89 with the optional YAML reader). See the
[design](docs/HAMCP_130_BACKUP_INVENTORY_DESIGN.md).

## Optional reviewed YAML configuration reader (2.4.1-beta.16, HAMCP-162)

`ha_config_get_yaml` is a delegated, read-only, closed Engineering wrapper
over ha-mcp's tool of the same name. It reads one mapping-key path of
`configuration.yaml` only:
- Engineering sends a fixed argument shape with `include_content: false`.
- Every other file and every credential-shaped path is refused before any
  upstream call.
- Values are projected through key-path, connection-string and
  credential-text redaction, with typed `!secret`/`!env_var` reference-name
  markers. Redaction markers are trusted by registry identity only, and
  marker-shaped upstream text is neutralized (H3).

It is an optional reviewed tool:
- admitted only on an exact complete-descriptor binding for ha-mcp 8.4.3
  and 8.5.0, with an explicit disposition for every registry version;
- excluded from the stock order fingerprint, stock admission and signed
  selections.

`ha_list_files` and `ha_read_file` are reviewed and never exposed. The
catalog is 59 static + 29 delegated = 88. There are no plans, approvals,
Engineering writes, service calls or physical execution. The one accepted
dependency (K1) is that the first call may make the ha-mcp component call
`POST ha_mcp_tools/get_caller_token` and persist the admin caller token. See the
[design](docs/HAMCP_162_YAML_READER_DESIGN.md) and the
[beta.16 notes](docs/V2_4_1_BETA16_RELEASE_NOTES.md).

## Source-only Alarmo inspector addition (2026-09-27)

`get_integration_inspection` is native read-only evidence, orchestrated by
Engineering with `direct_ha_api` data, zero delegated feature calls and no
fallback. Eight closed command variants permit nine sequential reads. The
source catalog on the beta.10 baseline is 56 static + 25 delegated = 81; previous descriptors are
unchanged. Existing signed authority withholds the added metadata read profile.
At that release, `ha_get_integration` remained mixed/requires-wrapper and
unexposed. Since 2.4.1-beta.7 (HAMCP-129) it is exposed only through the closed
Engineering platform wrapper: list mode, `include_options=false`, no options
flow, and a positive output allowlist. The Alarmo inspector does not use it.
This adds no plans, approval or physical execution. See [contract](docs/INTEGRATION_INSPECTION.md).

## Purpose

This document classifies every tool currently exposed by the custom HA MCP Engineering Server against the project's intended role: engineering analysis, governance, verification, and documentation that complements the standard `ha-mcp` server.

## Phase 3A provider routing overlay

Lifecycle classification and provider routing answer different questions. The existing
`native`, `transitional`, `delegated`, and `deprecated` labels remain unchanged. The
central routing policy maps all 40 beta tools to these execution/evidence routes:

| Route | Existing tools/capabilities |
| --- | --- |
| `engineering_native` | server/capability diagnostics, audit, plan creation/risk, plan reads/list/approval |
| `standard_mcp_preferred` | ordinary execution/reload pending exact upstream coverage |
| `direct_ha_required` | automation config, traces, blueprint source, config check, governed apply/verification/rollback |
| `transitional_direct` | bounded entity search, exact entity/area/service-catalog reads, template/history/logbook/error log, list automations/devices/entity registry/blueprints; legacy upsert is classified here but denied without a read policy |
| `upstream_dashboard` | read-only dashboard inventory and exact dashboard configuration evidence through one fixed upstream tool |
| `prohibited` | ungoverned destructive automation deletion in the target architecture and secret-bearing diagnostics |

Beta 8 preserves every public schema but enforces the routing overlay at runtime.
Delegated calls return a structured provider-unavailable result while Standard MCP is
unavailable. `delete_automation` fails closed as prohibited; `call_service` and
`reload_domain` cannot silently execute through direct HA. Transitional calls use only
the explicit direct-HA allowlist and record their provider.

Beta 9 corrects the remaining capability-truth mismatch. `get_entity`, `list_areas`,
`search_services`, and `list_services` are lifecycle `transitional`, route
`transitional_direct`, and identify `direct_ha_api`. Standard HA MCP's Assist surface
does not provide their exact semantics; `GetLiveContext` is not substituted. The four
policies are read-only and do not authorize service execution, reload, deletion, or any
physical action.

RC2 adds only `search_entities` to that explicit direct-read set after deployed
acceptance exposed an immediate `provider_unavailable` routing failure. Policy
`bounded_entity_state_search` permits one read-only `/states` inventory, validated
query/domain/limit input, deterministic slim results, and explicit truncation. It
does not enable Standard HA MCP transport or any fallback.

RC3A adds `list_dashboards` and `get_dashboard_config` as beta-native read tools
routed only to `upstream_dashboard`. That adapter allowlists
`ha_config_get_dashboard` and validates exact identity/version, the complete
input schema, reviewed safety annotations, and a security-relevant contract
projection before constructing either approved non-screenshot call shape.
Exact configuration evidence verifies the upstream-compatible 16-character
optimistic-lock hash and separately returns a full 64-character Engineering
evidence hash calculated from complete raw JSON before sanitization or response
omission.
No set/delete/backup/service/physical-action or arbitrary upstream tool can
dispatch. The generic Standard HA MCP gateway remains unavailable.

Beta 7 moves `entity_dependency_analysis` from planned to additive `beta_native`,
category `analysis`, risk `read`, routed `engineering_native`. Beta 12 likewise moves
`automation_reliability_analysis` into additive `beta_native`. Beta 15 adds
`change_impact_analysis`, and Beta 17 adds the read-only
`configuration_integrity_analysis`; Beta 19 adds `incident_correlation`, and Beta
21 adds `handoff_generation`. Beta 22 stabilizes that tool and Beta 23 corrects
shared provider failure accounting without changing the catalog or schemas.
Beta 24 makes the compatibility-visible legacy upsert and every missing direct
policy fail closed, while preserving the same catalog. No planned feature capability remains. All existing public
schemas remain unchanged.

Beta 25 keeps all 38 tools and their schemas. RC3A intentionally raises the
registered count to 40 while preserving every prior schema.
`approve_change_plan` remains
registered but requests an external Home Assistant administrator review; it does
not grant authority. Only the separate internal Ingress application can record
authority-version-2 approval. This is a governance semantic correction, not a
new MCP capability or provider route.

Classifications:

- **Keep** — uniquely useful or strategically important to the Engineering server.
- **Redesign** — valuable capability, but the public contract or implementation should change.
- **Delegate** — standard `ha-mcp` should normally provide this capability.
- **Remove** — does not fit the Engineering server's long-term mission and adds avoidable risk or maintenance.

## Summary

| Classification | Count |
|---|---:|
| Keep | 6 |
| Redesign | 10 |
| Delegate | 4 |
| Remove | 3 |
| **Total** | **23** |

The server's strongest current capabilities are automation traces, blueprint inspection, configuration validation, and its own audit trail. The greatest overlap and risk are concentrated in generic service execution, direct automation replacement/deletion, and domain reloads.

## Tool-by-tool classification

| Tool | Class | Current purpose | Main issue | Recommended destination |
|---|---|---|---|---|
| `get_entity` | Delegate | Read one entity's state and attributes. | Standard `ha-mcp` already provides richer structured state retrieval and projections. | Use standard `ha_get_state`; retain only as an internal primitive if an analysis tool needs direct HA access. |
| `search_entities` | Redesign | Search state machine by ID/friendly name/domain. | Reads one state inventory and returns deterministic bounded slim results with explicit truncation; it still lacks pagination, registry metadata, and fuzzy matching. | Replace publicly with analysis-oriented entity discovery or an internal indexed evidence provider. |
| `get_history` | Redesign | Fetch recent recorder history for one entity. | String refusal, no explicit pagination, no end time, no statistics source, and full HA payload shape. | Keep internally for incident analysis; expose through `analyze_incident` or structured evidence retrieval. |
| `get_logbook` | Redesign | Fetch recent logbook events. | No result limit/pagination or compact schema; output may be noisy. | Internal evidence source for incident timelines and behavioral verification. |
| `get_error_log` | Transitional | Return bounded structured HA System Log warning/error entries. | The System Log buffer is deduplicated and is not the complete raw Core journal. | Recursively sanitize the complete upstream result before reduction; keep fail-closed redaction, truncation, source attribution, untrusted marking, and correlation mandatory. |
| `render_template` | Keep | Evaluate Jinja against live HA state. | Useful and materially supports safe analysis, though output/error schema needs improvement. | Retain as a focused evidence/validation tool; add typed result and timeout/error classification. |
| `list_automation_traces` | Keep | List recent automation traces. | Requires internal ID and returns no pagination metadata. | Retain; normalize identifiers, add limits/pagination, and make it a building block for reliability analysis. |
| `get_automation_trace` | Keep | Retrieve a full execution trace. | Raw, potentially large payload with no compact/section controls. | Retain; add section projection and structured summaries while preserving raw escape hatch. |
| `list_automations` | Redesign | List automation states and internal IDs. | State-machine-only view; no config summary, category, labels, mode, blueprint status, or pagination. | Internal catalog feeding reliability/dependency audits; public output should be structured and pageable. |
| `get_automation_config` | Redesign | Read one automation's stored config. | Uses internal ID only and returns raw config without source metadata or hash. | Retain as internal evidence provider; add canonical ID resolution, source, timestamp, and stable config hash. |
| `list_blueprints` | Delegate | List automation/script blueprints. | Standard `ha-mcp` exposes blueprint discovery and details. | Delegate except where local-file provenance is needed for engineering analysis. |
| `get_blueprint` | Keep | Read installed blueprint source from a read-only mount. | Unique direct-source visibility is useful; raw YAML is untrusted and may contain embedded instructions. | Retain with provenance metadata, content hash, size, and explicit untrusted-content marking. |
| `upsert_automation` | Redesign | Compatibility-visible automation replacement schema; runtime refuses it. | Ungoverned full replacement would bypass immutable plan approval, stale-state checks, snapshots, verification, and rollback. | Fails closed with `governance_required`; use governed change plans. |
| `delete_automation` | Remove | Compatibility-visible deletion schema; runtime refuses it. | Boolean confirmation is not plan-bound approval and provides no dependency check, backup, or rollback receipt. | Fails closed; any future deletion requires a separately reviewed governed design. |
| `check_config` | Keep | Run HA configuration validation. | Good read-only/idempotent safety control; response could be normalized. | Retain as a verification primitive and include in change-verification workflows. |
| `call_service` | Remove | Compatibility-visible generic execution schema; runtime refuses it. | Broad physical-action surface; a denylist or caller Boolean is not sufficient authority. | Use standard `ha-mcp` execution tools where supported; no direct fallback. |
| `reload_domain` | Remove | Compatibility-visible reload schema; runtime refuses it. | Infrastructure/behavioral change with no immutable plan, preflight, or verification. | Use an approved external/general HA workflow; no direct fallback. |
| `list_areas` | Delegate | List area registry. | Standard `ha-mcp` provides richer floor/area topology. | Delegate; retain only as an internal dependency-analysis primitive if necessary. |
| `list_devices` | Redesign | Search device registry. | Limited fields, first-match truncation, no pagination, identifiers/connections omitted despite docstring, and no orphan analysis. | Replace with `analyze_devices` / `find_orphaned_devices` backed by a complete pageable registry client. |
| `list_entity_registry` | Redesign | Search entity registry. | Limited metadata, first-match truncation, no labels/categories/config entry/unique ID, and no actual orphan/reference analysis. | Replace with dependency and configuration-debt tools. |
| `search_services` | Delegate | Search HA services and field names. | Standard `ha-mcp` provides structured service discovery and full schemas. | Delegate to `ha_list_services`. |
| `list_services` | Redesign | Return full HA service catalog. | Potentially huge raw response and largely redundant; useful only for policy classification and compatibility testing. | Make internal to policy/schema validation; do not expose broad full-catalog dumps by default. |
| `get_audit_log` | Keep | Read this server's audit log. | Returns raw JSONL, records attempts rather than verified tool outcomes, hardcodes client as Claude, and may include sensitive arguments. | Retain but redesign storage/events to include request IDs, outcome, duration, client identity, redaction, and execution receipts. |

## Keep: required improvements

### `render_template`

- Return a structured success/error envelope.
- Add a bounded timeout.
- Mark rendered content as data, not instructions.
- Include evaluation timestamp and HA source.

### Automation trace tools

- Accept either entity ID or internal automation ID.
- Add pagination and response-size controls.
- Support selected sections such as trigger, conditions, actions, and errors.
- Produce an optional compact engineering summary without discarding raw evidence.

### `get_blueprint`

- Return file path, content hash, size, source type, and content separately.
- Explicitly label blueprint content as untrusted input.
- Detect and report path/source mismatches.

### `check_config`

- Normalize into `valid`, `errors`, `timestamp`, and `duration_ms`.
- Use as an automatic verification step after approved structural changes.

### `get_audit_log`

- Record tool outcome rather than only HTTP status.
- Add correlation/request IDs.
- Replace hardcoded `user: claude` with neutral client metadata.
- Redact secrets and sensitive fields recursively.
- Record risk class, approval/change ID, delegated server/tool, and verification result.

## Redesign themes

### 1. Raw evidence should become internal providers

The following tools are valuable, but primarily as inputs to higher-level engineering analysis:

- `search_entities`
- `get_history`
- `get_logbook`
- `get_error_log`
- `list_automations`
- `get_automation_config`
- `list_devices`
- `list_entity_registry`
- `list_services`

They should move behind reusable HA client and evidence-provider interfaces. Selected raw tools may remain as diagnostic escape hatches, but the primary public surface should answer engineering questions rather than expose API-shaped data dumps.

### 2. Writes should be plan-bound

`upsert_automation` remains registered only for public-schema compatibility and
fails closed. The implemented automation write workflow requires:

1. Inspect current config and dependencies.
2. Create an immutable change plan.
3. Produce a dry-run/diff.
4. Bind approval to the exact plan hash.
5. Revalidate source hashes immediately before execution.
6. Execute through the narrow governed direct configuration provider.
7. Verify stored config, explicit automation identity, and configuration validity.
8. Record rollback information and an execution receipt.

## Remove/delegate sequence

Do not remove tools immediately. Preserve compatibility while transitioning.

### Stage 1: Mark transitional

Update descriptions and capability reporting to identify:

- `call_service`
- `delete_automation`
- `reload_domain`
- direct `upsert_automation`

as transitional and not preferred for new workflows.

### Stage 2: Add safer replacements

Implement engineering-specific analysis first:

1. `analyze_entity_dependencies`
2. `analyze_automation_reliability`
3. `analyze_change_impact`
4. `analyze_incident`

Then implement plan-bound governance.

### Stage 3: Fail closed (implemented)

Broad direct-write and execution tools remain schema-visible but have no
compatibility escape hatch. Missing direct policy means deny.

### Stage 4: Remove in a major version

Remove obsolete tools only with a documented migration path and major-version release.

## Missing high-value tools

### `server_info`

Reports server identity, version, Git commit, build time, schema version, HA connectivity, and enabled capability groups.

### `list_capabilities`

Identifies each capability as native, delegated, transitional, or unavailable, including risk class.

### `analyze_entity_dependencies`

Finds direct and probable consumers across automations, scripts, scenes, helpers, groups, dashboards, and blueprint inputs.

### `automation_reliability_analysis`

Beta 12 implements a deliberately smaller deterministic first slice: disabled status,
missing/unavailable/unknown/registry-disabled dependencies, repeated trace/action
errors, repeated condition stops, explicit concurrency rejections, correlated sanitized
System Log errors, dynamic-reference gaps, missing blueprint evidence, and absent trace
evidence. It does not speculate about trigger events, delays, retries, thresholds,
manual overrides, or physical safety without direct evidence.

### `analyze_change_impact`

Evaluates proposed renames, removals, integration changes, and automation edits before execution.

### `analyze_incident`

Correlates config, traces, history, logbook, logs, availability, and recent changes into a timeline with evidence and uncertainty.

### `find_configuration_debt`

Finds orphaned helpers, dead dashboard references, disabled entities still referenced, duplicate logic, missing descriptions, and fragile templates.

### Governance tools

- `create_change_plan`
- `approve_change`
- `execute_approved_change`
- `verify_change`
- `rollback_change`
- `generate_handoff`
- `get_change_history`

## Recommended first implementation change

Add `server_info` and `list_capabilities` before altering existing tools.

Why first:

- Establishes reliable identity between the two installed MCP servers.
- Makes architectural boundaries machine-readable.
- Allows existing tools to be marked native, delegated, or transitional without breaking compatibility.
- Provides a stable place to publish version, build, schema, HA connection, and security-mode information.
- Creates the basis for regression tests and future server comparison.

This should be implemented as a small, read-only change with no Home Assistant behavioral impact.

## Verification requirements for the first change

- Both tools return structured JSON-compatible objects, not JSON strings.
- Server name and version match packaged add-on metadata.
- Build provenance is present or explicitly `unknown`.
- HA connectivity is tested without changing HA state.
- Capability classifications match this audit.
- Transitional tools are clearly identified.
- Secrets, endpoint credentials, and tokens are absent.
- Unit tests cover response shape and redaction.

## Decision

The existing public surface should be preserved temporarily for compatibility, but future development should prioritize engineering analysis and governance. Direct execution and generic CRUD should migrate to standard `ha-mcp` or an approved, auditable delegation workflow.
