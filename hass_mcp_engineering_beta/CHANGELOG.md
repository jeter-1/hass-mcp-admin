# Changelog

## 2.4.1-beta.37 - Host journal sources for get_incident_logs (HAMCP-224)

Builds on 2.4.1-beta.36.

- `get_incident_logs` reads `supervisor`, `host`, `host:<category>` and
  `addon:<slug>` from the host journal, which the Supervisor mounts
  read-only with the new `journald: true`; `hassio_role` stays
  `homeassistant` and no `manager` route is called. Through beta.36 these
  sources were `authority_not_granted`.
- The reader binds 14 fixed `libsystemd.so.0` symbols (the base image's
  library) and 9 fixed fields, merges the runtime and persistent journals in
  one handle, and is bounded at 16 KiB per field, 4 MiB per call, 20,000
  entries per source and the call deadline less one second.
- Journal realtime timestamps; continuations hold a realtime and a keyed
  cursor digest, never the raw cursor.
- A credential-looking journal message is withheld whole
  (`credential_text_withheld`, partial) before the shared sanitizers run at
  the single exit.
- journald off or unreadable is `unavailable` with its reason, never an
  empty success.
- No input schema, routing, provider, policy, profile, fingerprint, signed
  registry, workflow, Dockerfile, dependency or stable-v1 change; static
  tools 68, catalog 96 (97).

## 2.4.1-beta.36 - Governed delete drift read in its own Core window, inactive (HAMCP-155)

Builds on 2.4.1-beta.35. Activates nothing new.

- The delete adapter's dispatch-time drift read ran before the dispatch
  authority was committed, so the real REST client refused it and every
  admitted delete failed pre-dispatch (`resource_read_unavailable`, zero
  writes). It now runs in its own short Core-authority window (acquire,
  consume, revalidate, finish; the preflight pattern), entered inside the
  dispatch window, single use, and refused once dispatch authority is
  committed. Missing or withdrawn authority is
  `dispatch_authority_unavailable`; drift is still `stale_target_state`;
  the dispatch commit is unchanged and still sends exactly one DELETE.
- Only the delete adapter (`PREDISPATCH_READS_HOME_ASSISTANT`) uses the
  window; every other adapter's pre-intent check is unchanged.
- Test fakes for the governance and F3 suites now enforce the real clients'
  Core-authority check (`tests/core_authority_fakes.py`).
- No tool, schema, profile, fingerprint, signed registry, workflow,
  permission, dependency or stable-v1 change; static tools 68, catalog 96
  (97).

## 2.4.1-beta.35 - Governed deletion of automations and scripts, inactive (HAMCP-155)

Builds on 2.4.1-beta.34. Activates nothing new.

- New tool `create_configuration_delete_plan` (1-8 automation or script
  deletions) and runtime-only Core profile
  `core.configuration_delete_operation`
  (`compiled-core-configuration-delete-operation-v1`, contract fingerprint
  `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d`).
  - Signed sequence 12 does not reference it: `pending_signed_registry_admission`.
  - The governance service refuses first, with zero reads and nothing
    stored; dispatch re-checks admission on every F3 path.
- Once admitted:
  - one no-body `DELETE /api/config/<automation|script>/config/<id>`, sent
    only by the new gateway method under `delete_automation_configuration`
    or `delete_script_configuration`;
  - drift against the bound pre-image immediately before the DELETE is
    `stale_target_state`;
  - verification proves absence (config 404, registry entry gone, state
    gone); partial absence is `manual_review_required`;
  - policy follows the removed protected effects (elevated with a
    consequence statement); a delete is never "no effect";
  - `rollback_change` recreates a verified deletion from its exact
    pre-image under a separately approved plan.
- The F3 executor stores every code of a pre-intent dispatch refusal; the
  outcome is unchanged for every adapter.
- Dormant seq-13 delete acceptance lane
  `scripts/configuration_delete_contract_acceptance.py`, not wired.
- Delete-lane dormant steps join the existing `REAL_HA_SEQ13_LANES` gate in
  `ci.yml`; no signed profile, signed registry, permission, add-on
  configuration, dependency or stable-v1 file changes; static tools 68,
  catalog 96 (97).

## 2.4.1-beta.34 - Label create, edit and assignment, inactive (HAMCP-165)

Builds on 2.4.1-beta.33.

- Add `create_label_plan`. It proposes 1-8 ordered `create` or `update`
  operations over a label's `name`, `color` (theme colour or `#RRGGBB`),
  `icon` and `description`. Each may assign the label to exact registered
  entities through the signed HAMCP-069A path. Labels are never deleted.
- Resource type `label_registry_entry`: `config/label_registry/create` or
  `config/label_registry/update`, each verified by exact
  `config/label_registry/list` readback. Duplicate names, slug collisions,
  unknown labels and no-op edits are refused before a plan is stored; stale
  state and namespace changes are refused before each write. Rollback is
  refused (a corrective edit is a new plan).
- New runtime-only Core profile `core.label_registry_operation`
  (`sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c`).
  The signed registry (sequence 12) does not reference it, so every call is
  refused with `CoreCapabilityUnavailable` /
  `pending_signed_registry_admission`, admission checked first at the
  service layer. Activates nothing new; switches on with the sequence-13
  signing.
- Review1 (H1): F3 re-checks Core admission of a dormant profile (labels and
  both 069B resource types) before every read and write, including a retained
  approved plan, recovery and private read-only reconciliation. No Core, a
  Core that cannot report status, or an unadmitted profile refuses with
  `core_authority_unavailable` or `pending_signed_registry_admission` and
  sends no Home Assistant command. Before this, an F3 runtime composed
  without Core would run a retained approved 069B plan in the legacy no-Core
  mode.
  The lowest configuration builder, including generic
  `create_configuration_plan` routing, refuses an unadmitted profile before
  any read or store (M1); the final pre-dispatch label read refuses any drift
  of the exact target as `stale_target_state` (M3).
- Entry admission (HAMCP-155 review finding, same pattern): an unadmitted
  first apply of a label or 069B plan refuses with `provider_unavailable`
  (`pending_signed_registry_admission` or `core_authority_unavailable`)
  before any persistence. The plan, task, child, manifest and lock stores
  stay byte-identical and no Home Assistant request is made, on every path:
  F3 with a withdrawn profile, F3 without Core, a Core that cannot report
  status, and a service with no F3 runtime (which used to read the registry
  and store a task before failing). The dispatch gate stays as the second
  barrier.
- HAMCP-069B: the two dormant profiles name `websocket_entity_registry_list`
  and `websocket_expose_new_entities_get`, the reads their gateway sends.
  New fingerprints: availability
  `sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2`,
  exposure
  `sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd`.
- CI: dormant seq-13 evidence lanes for 165 and 069B
  (`REAL_HA_SEQ13_LANES: dormant`); no existing step changes.
- Catalog: 67 static tools plus 28 delegated reads, 95 total (96 with the
  optional YAML reader). No existing descriptor, schema, permission, signed
  registry entry or signed fingerprint, or stable-v1 file changes.

## 2.4.1-beta.33 - Configuration definitions (HAMCP-132 slice 2)

Builds on 2.4.1-beta.32.

- Add the read-only `get_configuration_definitions` tool for up to 50
  `entity_ids` and/or whole `integrations` from template, sql, filter,
  derivative, statistics, group, integration, min_max, history_stats,
  threshold, trend and utility_meter.
- Project each `configuration.yaml` definition through a positive
  per-integration allowlist. The admitted YAML reader is used through a new
  fixed gateway purpose, `configuration_definitions`, with eight paths.
  `db_url`, credential keys, credential URIs in text and `!secret`/`!env_var`
  names are withheld in every position. Unrecognized keys are counted only.
- Match entities to definitions:
  - `exact_config_entry` (UI-defined: `definition_not_readable_without_flow`);
  - `exact_unique_id`;
  - `heuristic`, with `high`, `medium` or `low` confidence;
  - `ambiguous` (never resolved);
  - `unmatched` or `not_applicable`.
- Diagnose unavailability from Core's signals: disabled, config entry, or
  platform or integration not loaded, dependency unavailable, transient,
  entity level, not found or undetermined. Report the `ha_mcp_tools`
  component from Core's own reads only; its commands are not used (owner
  decision, 2026-10-21).
- Disclose `!include`, packages and labelled keys without following them. Any
  unreadable source, trim or redaction makes the report partial. The complete
  envelope is sanitized once with the known secrets.
- Corrections from the independent review: credential text is withheld from
  every string of the final report, config entry titles included (H1);
  ambiguity rules are identical from both sides (M1); a unique ID YAML 1.1
  could read differently is never exact (M2); a YAML source gap or an
  unchecked UI or template definition is `undetermined` (M3); a malformed
  source value invalidates only its item (M4).
- Corrections from the delta review: a config entry title is checked whole
  before any truncation and shown only through a printable-ASCII allowlist,
  so an ordinary non-ASCII title is withheld as `[withheld:unverifiable_text]`
  by design (H1, H2); YAML-projected text is screened through a bounded
  confusable fold (H2); an unexpanded template blueprint is a definition gap
  (M3); a malformed registry identity is `invalid_response` and never
  matched, while `null` stays a legitimate absence (M4).
- Corrections from the second delta review: a YAML-projected string or key
  with an invisible, format or control character, literal or
  percent-encoded, is withheld whole as `[withheld:unverifiable_text]`, never
  screened and shown (R2-H1); any withheld form anywhere in the final report
  makes it partial, naming each affected item (`withheld:definitions[3]`)
  (R2-M1).
- Correction from the final32 review (correction batch 4): every
  YAML-projected string is also screened with all whitespace and line-control
  characters removed, so a tab, line feed, carriage return or space inside a
  credential, literal or percent-encoded, withholds the whole value (partial).
  Ordinary multi-line templates stay shown and complete; text with a colon
  then `@` and no `/` between is withheld by design.
- Owner decision (Josh, 2026-10-22): a SQL definition's own `db_url` is a
  fixed policy redaction, not missing evidence. It is reported as
  `{"redacted_by_policy": "database_url"}` and, through an explicit
  allowlist inside the same final-envelope scan (SQL `fields.db_url` only),
  leaves the report complete. Every other withholding is still partial.
- Owner decision (Josh, 2026-10-22): nothing is derived from a withheld
  input. A withheld name, default-name input or key derives no entity ID
  (`derived_entity_id_withheld`); a withheld unique_id input generates no
  unique ID (`unique_id_withheld`); a withheld dependency value is not read
  (`dependencies_withheld`, diagnosis `undetermined`); a withheld key is not
  in the definition ID; a withheld legacy name is never a candidate. Each is
  listed as withheld, so the report is partial.
- Owner decision (Josh, 2026-10-22): the Core-observed entity ID of an exact
  registry match is Core's data and is not withheld. An entity that Home
  Assistant itself named from a credential-bearing string shows that entity
  ID (disclosed limitation; rename the entity in Home Assistant).
- Catalog: 66 static tools, 94 advertised (95 with the optional YAML reader).
  No existing descriptor, schema, stored format, dependency or workflow
  changes.

## 2.4.1-beta.32 - Incident-log entry times (HAMCP-220)

Builds on 2.4.1-beta.31.

- Fix `get_incident_logs` reading the Supervisor log time as UTC.
  - Supervisor 2026.10.3 renders that time in Home Assistant's local time
    without an offset, so recent windows were reported after the tail.
  - Home Assistant's configured `time_zone` is now read from Core
    `GET /api/config` (the HAMCP-200 `/config` route) before a call's
    journal reads, and once more after them. Each read runs under the
    tool's own `core.basic_rest_read` lease.
    `get_incident_logs` is `CORE_INDEPENDENT_DOWNSTREAM_AUTHORITY`.
  - A zone that changed between the two reads, or a second read that
    fails, withholds every source that read the journal
    (`zone_changed_during_read`, `zone_unverifiable_after_read`; partial,
    no times, the cursor kept).
  - If the zone cannot be read, Core and `addon:self` are unavailable
    (`time_zone_unavailable`), with zero journal requests. The add-on `TZ`
    and UTC are never substituted.
- Fail closed on any zone disagreement.
  - An add-on `TZ` whose offsets differ from Home Assistant's anywhere in
    the window makes every source partial with `zone_mismatch`.
  - So does a page's first entry whose `X-First-Cursor` realtime (`t=`)
    disagrees with its text.
  - Other entries are read from their text in the confirmed rendering, with
    `zoneinfo` DST rules. An explicit `+HH:MM` is read exactly.
- DST-ambiguous and nonexistent local times.
  - They are resolved exactly by single-entry realtime reads within the
    request budget.
  - Otherwise they are `ts_basis: uncertain`, with `ts: null` and every
    candidate.
  - Around an unresolved time, each source keeps its journal order and the
    rows up to its latest candidate are `ordering_uncertain`, on later pages
    too (carried in the signed cursor). The read is partial.
- Output.
  - Entries gain `ts_basis`, `time_zone` and `ordering_uncertain`.
  - Sources gain `timestamp_basis` and `zone_mismatch`.
  - The response gains `time_basis`, with the Home Assistant and add-on zone
    statuses.
  - The schema id is unchanged, and `ts` is nullable.
- No descriptor, input schema, Supervisor route, permission, add-on
  configuration, dependency or stable-v1 file changes; catalog 93 (94).

## 2.4.1-beta.31 - Entity enable/disable and voice exposure, inactive (HAMCP-069B)

Builds on 2.4.1-beta.30.

- Add `create_entity_availability_exposure_plan`. It proposes one operation
  (`disable`, `enable`, `expose`, `unexpose`) over 1-8 exact registered
  entities. `expose`/`unexpose` need `assistant` (`conversation` or
  `cloud.google_assistant`); Alexa is refused. There is no free-text field.
- Two resource types, `entity_registry_availability`
  (`config/entity_registry/update` with exactly `entity_id` and
  `disabled_by`) and `entity_assistant_exposure`
  (`homeassistant/expose_entity`, one assistant and one entity), each
  verified by exact `config/entity_registry/get` readback. A non-target
  `cloud.alexa` key going from absent to present is the one tolerated
  difference, reported as `tolerated_new_exposure_key_cloud_alexa`.
- Two new runtime-only Core capability profiles. The signed registry
  (sequence 12) does not reference them, so every call is refused with
  `CoreCapabilityUnavailable` / `pending_signed_registry_admission` before a
  plan is stored. `list_capabilities` and health report the same status.
  The governance service checks admission first, before any Home Assistant
  read or plan storage, on every entry point. Nothing new is active until an
  owner-signed sequence 13.
- Once admitted: protected entities need the existing elevated
  acknowledgement. A disable whose dependency evidence is incomplete for any
  reason is protected, with "impact could not be fully determined". An
  enable plan covers one config entry; unregistered
  entities, devices, config entries, Core-internal disables and
  already-applied states are refused; rollback is an inverse plan. The
  gateway never sends `cloud/status`, `homeassistant/expose_entity/list`,
  `homeassistant/expose_new_entities/set` or either cloud entity-update
  command.
- Catalog: 65 static tools plus 28 delegated reads, 93 total (94 with the
  optional YAML reader). No existing descriptor, schema, permission,
  workflow, signed registry entry or fingerprint, or stable-v1 file changes.

## 2.4.1-beta.30 - Incident-window logs and continuable history (HAMCP-131)

Builds on 2.4.1-beta.29.

- Add read-only `get_incident_logs`. It reads one window of at most 6 hours
  from the Core journal (`/core/logs`) and Engineering's own add-on journal
  (`/addons/self/logs`), through HAMCP-200 typed routes with a gatewayd
  `Range` header, and merges the entries in time order. The level filter and
  a literal text filter (at most 128 characters) report skip counts. A limit
  of 1-200 applies, with a signed cursor. Pages are strictly time-ordered
  across sources; a source that runs out of request budget holds the merged
  page at its earliest unread point and is reported `partial`
  (`request_budget_exhausted`). Wrong-typed arguments are refused as invalid
  arguments.
- Each source reports its coverage: earliest and latest retained entry,
  retention edge, scanned range, gaps with reasons, and filter and window
  skips. `supervisor`, `host` and other add-on sources need Supervisor role
  `manager`. They are listed `unavailable` with `authority_not_granted`, the
  required role and zero requests, also when they are the only sources
  selected; no role or permission is added. An add-on slug must be one
  validated segment, so a traversal is refused with zero requests.
- Add read-only `get_history_slices`. It reads entity history continuously
  through the admitted delegated `ha_get_history` route (ha-mcp 8.5.0 or
  8.6.0) using a new fixed gateway `history_reader`. Windows are at most
  7 days, read as fixed 6-hour slices, with per-page coverage whose
  `complete_until` is exclusive while tied states may remain. The cursor
  binds the query, the fixed end, the provider contract and a cumulative
  digest of all history the read has returned, which every continuation
  re-reads (at most 40 verification reads of 100 states; past that no cursor
  is issued, `continuation_verification_limit`). Any change to returned
  history on any earlier page, a purge, a late write, a changed slice-start
  state or a contract change is refused as `STALE_CURSOR`. The native `get_history` tool is not used.
- Each tool sanitizes its whole response once, at a single exit, with the
  known secrets; a redaction makes the result partial. `get_incident_logs` is
  Core-independent (Supervisor only); `get_history_slices` maps to
  `core.basic_rest_read`.
- Catalog: 64 static tools plus 28 delegated reads, 92 total (93 with the
  optional YAML reader). No existing descriptor, schema, permission,
  workflow, signed registry entry or stable-v1 file changes.

## 2.4.1-beta.29 - Safety-critical acknowledgement (HAMCP-213 PR 1)

Builds on 2.4.1-beta.28.

- Compare each automation or script create or update's protected physical
  effects before and after (`physical-effect-comparison-v1`): `none`,
  `equivalent`, `changed` or `unresolved`. Reference-only services never name
  an effect; run mode, abort points (conservative OD-10) and run occupancy
  count.
- Policy `f2-v3`: `equivalent` is `elevated_admin` with plan approval only;
  `changed` or `unresolved` adds the per-object
  `safety_critical_acknowledgement` (Ingress only, authority version 4).
  Unlock and disarm effects always need it (OD-6(b)), and no shortcut applies
  to them. An incomplete execution contract stays `prohibited`. Otherwise the
  Beta 33 retained proof, garage inverse and retry-transfer paths are
  unchanged.
- An effect-capable call with a templated, variable-driven or area, device,
  floor or label target, or a templated service name, is `unresolved`. A
  reference step that can run before an effect is compared in full; any
  change to it is `changed`.
- The comparison is an allowlist: only the keys in the design's
  ignorable-differences table (D2 item 8) are ignored. An `enabled` flag or
  an unknown key on an effect path is `changed`.
- A change past the review bounds (nesting depth, step count, called-script
  depth or count, or more than 32 acknowledged objects) is `prohibited` on
  every route, with
  `safety_critical_review_bounds_exceeded` or
  `too_many_protected_objects_to_review_split_the_change`. The unlock or
  disarm floor is computed first.
- Every called script's fingerprint is bound to the plan and re-read before
  approval is consumed and before each write. A changed or unreadable callee
  refuses as `stale_target_state` (reason `stale_called_script`) with no
  write, on the service path and in F3.
- A called script the same plan updates or deletes is analyzed with its
  current and proposed configurations together, so callers are judged
  against the post-plan callee. Before each caller write the live callee must
  match its pre-plan fingerprint (callee not yet written) or its proposed
  fingerprint (callee already written); otherwise the caller refuses as
  stale with no write, including on F3 resume and recovery.
- Dispatch rechecks the exact acknowledged object set and comparison hash
  independently of the bundle state; plan approval alone never dispatches.
- E013 precision: logbook mentions are not targets, notify routed by name has
  no omitted target, no retry warning outside a retry candidate, recipient
  reason only when recipients change.
- OD-4: `principal_separation_status.reason` is `no_decision_recorded` or
  `not_approvable`.
- New audit events and the stable error codes
  `safety_critical_acknowledgement_required` and
  `safety_critical_acknowledgement_mismatch`.
- Pending `f2-v2` plans must be replanned. An applied `f2-v2` plan is
  admitted only as read-only evidence: as an F3 rollback source, and when
  reverifying its own consumed task.
- Rollback to beta.28 quarantines every plan record written under `f2-v3`,
  terminal ones included (beta.28, like beta.27, accepts only `f2-v1` and
  `f2-v2`; its plan storage is unchanged from beta.27). If the quarantine
  move fails, beta.28 deletes the record; the backup is the protection.
  Procedure: take a fresh Home Assistant backup (it includes the Engineering
  add-on data), finish or reject pending plans, then roll back.
  Execution-task records stay readable.
- On startup, beta.29 restores plan records an older version quarantined,
  before cleanup and recovery. Only records whose canonical bytes, plan
  identifier, namespace and content screen verify are restored. Restoration
  never overwrites, is idempotent and is logged. Anything else stays
  quarantined and is reported in health (`quarantine_restoration`).

## 2.4.1-beta.28 - Core authority recovery and visibility (HAMCP-215)

Builds on 2.4.1-beta.27.

- Retry a failed Core verification with bounded exponential backoff and ±20%
  jitter: 20 s, doubling, capped at 240 s. This replaces the fixed 300 s
  `failure_retry`.
- Pass every non-admitted state (transient retirement, incompatible,
  unavailable, not initialized at boot, changed version or registry
  authority, re-initialization) through one restoration gate:
  `CORE_RECOVERY_CONFIRMATIONS = 2` consecutive complete background
  verifications by the supervisor, spaced about 15 s apart, with the same Core
  identity, version, selected signed-registry authority and admitted reviewed
  contracts. A retirement within 600 s of a restoration needs 3, at doubled
  spacing. Any failure or connection move, and any background or on-demand
  observation of a changed identity, version, registry authority or contract
  set, restarts the count. On-demand verification never confirms or restores
  from any state; it is refused and counted in `recovery_on_demand_refusals`.
- Make the boot exception a one-shot latch per process: only the boot
  sequence's first admission, while the process has never admitted, retired
  or failed, is exempt. Any admission, failure, retirement or non-boot check
  spends it, and only a new process re-arms it, so an application rebuild or
  runtime reconfiguration restores through the full gate. A failed first
  startup check ends the boot retries; the supervisor then recovers on
  backoff pacing.
- Keep a complete but unadmitted observation retired
  (`recovery_state: incompatible`). Re-check it every reconciliation interval
  without backoff; once compatible it passes the same gate.
- Log `core_authority_verification_failed`, `core_authority_retired`,
  `core_authority_capabilities_withdrawn`, `core_catalog_tools_withdrawn`,
  `core_catalog_tools_restored`, `core_authority_recovery_attempt`,
  `core_authority_recovery_confirming`, `core_authority_recovery_reset` and
  `core_authority_restored` at WARNING, and `core_authority_recovery_scheduled`
  and `core_authority_recovery_on_demand_refused` at INFO. Each carries a bounded reason or detail and an `at` timestamp. Probe
  failures are classified into closed `core_probe_*` details.
- Add `home_assistant_core_authority.recovery` to health, with recovery state,
  failure reason, detail and time, retirement, attempt, next-attempt and
  restoration times, and confirmations. Add the counters `recovery_attempts`,
  `recovery_confirmations` (accepted confirmations only),
  `recovery_on_demand_refusals` and `authority_restorations`.
- A lifecycle-socket movement, including the reconnect after a Core restart,
  now waits one confirmation interval (about 15–18 s) before authority returns.
  Core-backed tools refuse during it. Josh accepted this on 2026-10-20, and
  the same wait (about 15 s) after a Core version change, a moved
  signed-registry authority, an incompatible Core becoming compatible, or a
  Core not initialized at boot (which first waits one backoff delay).
- No tool, schema, descriptor, permission, workflow or stored-format change.

## 2.4.1-beta.27 - Recorder settings and unregistered entities (HAMCP-132)

Builds on 2.4.1-beta.26.

- Add `get_recorder_settings`, a read-only tool that reports the recorder's
  effective settings. Each setting carries an `origin` (`configured`,
  `core_default` with the Core 2026.10.1 pin, or `unknown` with a reason).
  The report also covers include and exclude filters with Core's filter case,
  live `recorder/info` runtime state, and, for up to 50 entities, Core's own
  recorded answer next to the filter computed from configuration. A
  disagreement is `filter_drift`.
- Report unwritten settings as `unknown`, never as defaults, when packages,
  `!include`, truncation or an unreadable source could supply them (owner
  decision (v)). `unknown_command` from `recorder/info` means the recorder is
  not set up, never "not recording".
- Never return `db_url`, a `!secret` or `!env_var` reference name, or an
  unrecognized recorder key: the projection is a positive allowlist.
- Sanitize each tool's complete response once, with the known secrets, at a
  single exit point. Nothing is appended afterwards, and a redaction makes
  the report partial.
- Report a written value as `parser_dependent`, not `fails_core_schema`,
  when its YAML 1.1 readings change Core's verdict. Examples are unquoted
  `off` or `yes`, `1:20` and exponent floats. A plain integer is definite,
  and `coverage.yaml_numeric_spelling` discloses that a leading-zero spelling
  such as `010` is not observed. Quoted strings, and `y` or `n`, are definite
  strings.
- Add `get_unregistered_entities`, a read-only tool that lists entities with a
  state but no registry entry. It reports the platform (from
  `entity/source`), whether the state was written directly, and the config
  entry from `search/related` (`ambiguous` when more than one is found). The
  definition location is always `not_determinable`. A truncation is partial.
- Read configuration through the admitted YAML reader for three fixed paths
  (`recorder`, `homeassistant.packages`, `homeassistant`) via a new gateway
  `yaml_config_reader`. The batch allowlist adds exactly
  `recorder/entity_options/get` and `search/related` with `item_type: entity`,
  both with a strict entity-ID pattern. Map both tools in the HAMCP-164 Core
  gate.
- Catalog 90 tools (91 with the optional YAML reader). No existing tool,
  schema, descriptor, permission, workflow or dependency change.

## 2.4.1-beta.26 - certifi 2026.10.19 dependency security release (HAMCP-217)

Builds on 2.4.1-beta.25. Split from HAMCP-215 by Josh's decision so that
other pull requests can merge; HAMCP-215 receives it through `main`.

- Bump `certifi` 2026.7.22 → 2026.10.19 in both hashed locks and
  `build-inputs.json` for GHSA-6q48-6c7w-9gw2 (PYSEC-2026-4320). The bundle
  loses only the `emSign ECC Root CA - G3` root. There are no transitive
  changes, and `requirements.txt` is unchanged.
  `tests/test_hamcp217_certifi_20261019_contract.py` pins the Engineering
  httpx trust.
- No runtime code, tool, schema, descriptor, permission, workflow or
  stored-format change. Stable v1 does not pin certifi.

## 2.4.1-beta.25 - Loaded script configuration (HAMCP-212)

Builds on 2.4.1-beta.24.

- Read script configuration from Home Assistant's loaded configuration. Each
  available, identity-verified script adds the fixed read-only commands
  `script/config` and `search/related` (`item_type: script`) to the dependency
  index's single WebSocket batch (`loaded_config_batch`, renamed from
  `loaded_automation_config_batch`), replacing one delegated
  `ha_config_get_script` read per script. No configuration file is read.
- Raise the batch bound `MAX_BATCH_COMMANDS` from 2,048 to 4,096: two commands
  per source at the provider's 1,000 automation and 1,000 script limits need up
  to 4,000. The bound is still refused before dispatch; 16 stay in flight.
- Use the existing delegated stored read only for a counted, closed set of
  reasons: `blueprint_instance`, `blueprint_reference_nested`,
  `loaded_entity_unavailable`, `pending_write_reload`,
  `loaded_source_unsupported`, `loaded_config_unreadable` and
  `loaded_source_unavailable`. The last two are a failed loaded read: script
  coverage is partial with `loaded_config_unavailable: N`. A self-calling or
  cyclic script fails its search (Core has no cycle guard) and is
  `loaded_config_unreadable`.
- Journal governed script writes before dispatch until Home Assistant's
  reload lands; written-but-not-loaded scripts are `pending_write_unreconciled`.
- Attribute each script's evidence to its source (`direct_ha_api` loaded,
  `upstream_read_gateway` stored).
- Add `script_config_source`, `script_config_source_counts`,
  `script_stored_config_fallback_reasons`, `script_loaded_config_unavailable`,
  `script_pending_write_journal` and `loaded_script_command_count` to the build
  profile. `loaded_config_command_count` now includes script commands.
- Script-aware query fingerprints change once (per-script attribution); the
  DependencyIndex snapshot fingerprint is unchanged. No tool, schema,
  descriptor, permission, workflow or dependency change.

## 2.4.1-beta.24 - Read-only backup inventory (HAMCP-130)

Builds on 2.4.1-beta.23.

- Add `get_backup_inventory`, a read-only tool that reports Home Assistant
  backups: per backup, its date, size, content scope, protection flag, Home
  Assistant version, add-ons, folders, database flag and storage locations;
  a summary with the newest full backup and confirmed off-site copies; the
  automatic-backup schedule, retention, last attempt, last completion and
  result; recent failures; and nine health signals with stated thresholds.
- Read only Core's `backup/config/info` and `backup/info` over the existing
  admin WebSocket authority. No create, restore, delete, download or settings
  path exists.
- Report `partial`, with per-field reasons, whenever a read, location or
  field cannot be read, is out of range or truncated, or text is redacted.
  Each health signal names the inputs it depends on and is `unknown` when any
  of them is unreadable.
- Show an invalid field or list as `null`, never as an empty list, validate
  the schedule time strictly, and keep `omissions` to exactly 40 entries
  with an `omissions_not_listed` marker and a full reason count. A signal is
  `unknown` only when an input is unreadable, and then never claims complete
  evidence; a last action event without its required state is unreadable.
- Never return the encryption key; it is a per-response known secret for the
  shared sanitizer. When the key cannot be positively read, withhold every
  name and error text and alias free-form identifiers. A transformed copy of
  a known key (encoded or split across names) is an accepted residual; see
  `docs/SECURITY.md`.
- Map the tool to `core.basic_websocket_read` in the Core gate. Static tools
  60 (was 59); advertised catalog 88 (was 87), or 89 with the optional YAML
  reader. Every existing descriptor, signed registry, HOLD, support window,
  persisted format, workflow and stable v1 is unchanged.

## 2.4.1-beta.23 - Dependency index from Home Assistant's loaded automation config (HAMCP-209)

Builds on 2.4.1-beta.22.

- Build the dependency index from the loaded automation configuration over
  one authenticated WebSocket connection: two fixed, read-only commands per
  automation (`automation/config`, `search/related`), at most 16 in flight,
  enforced by an allowlist in the client. No per-automation
  `automation_config` REST read on the normal path (HAMCP-171 D3; no config
  file read, D4).
- Keep the stored REST read for blueprint instances, unavailable,
  unreadable or mismatched entries, writes not yet reloaded and a failed
  loaded source, and report every such fallback with its reason in the
  coverage policy and build profile. When the loaded configuration could
  not be read (unreadable, mismatched or a failed loaded source) the index
  is partial (`loaded_config_unavailable`).
- Journal governed automation writes before dispatch (cap 256). A write the
  build cannot account for, or any journal overflow, keeps every build
  partial (`pending_write_unreconciled`); overflow is never cleared by a
  build.
- Show helper and entity-metadata effect-projection values verbatim only
  when Home Assistant's registries admit them. A target must be a registered
  entity, device, area, floor or label ID. A service or domain must be in the
  service registry. A data key must be in that service's schema. Numbers,
  booleans and fixed enum words need no registry. Every value must also pass
  the shared sanitizer and the known-secret check, numbers and enums
  included. Anything else is a `redacted_sha256:` digest, in responses and
  persisted plan files. When registry data is unavailable, values are
  digested and the profile is marked partial for display. The registry
  reads are display-only and use reads Engineering already makes (one
  `GET /services` per build; target registries only when needed).
- The raw effect, profile and index fingerprints are unchanged. The
  aggregate helper evidence fingerprint covers the displayed values, so it
  changes when a value is digested, including when registry availability
  changes. A plan bound to the earlier evidence is then stale and must be
  re-planned.
- Add additive build-profile fields. No tool, input schema, description,
  provider route, signed registry, HOLD, support window, persisted format,
  workflow or stable-v1 change.

## 2.4.1-beta.22 - aiohttp 3.14.4 dependency security release (HAMCP-211)

Builds on 2.4.1-beta.21, which was superseded before publication and is not
re-tagged or re-published. beta.22 is the first published beta with
HAMCP-205's administrator verification.

- Upgrade `aiohttp` from 3.14.3 to 3.14.4 for GHSA-2g87-pp6c-29x5,
  CVE-2026-111063 and CVE-2026-111064, and `yarl` from 1.24.5 to 1.25.1 as
  3.14.4 requires. `requirements.txt`, both hash locks and
  `build-inputs.json` change; no other package changes.
- Pin the inherited library behaviour on Engineering's transports: header
  whitespace stripping on the identity check, the WebSocket byte ceiling and
  fragment rules, single-attempt reads, and HAMCP-200 route guarding under
  yarl 1.25.1's query parsing (`tests/test_hamcp211_aiohttp_3144_contract.py`).
- No runtime code change. Preserve the catalog, input schemas, signed
  registries, HOLD, support window, persisted formats, workflows and stable
  v1 (which still pins `aiohttp==3.9.5`, reported to Josh).

## 2.4.1-beta.21 - Administrator verification for Ingress approval pages (HAMCP-205)

Builds on 2.4.1-beta.20. Superseded before publication; first published in
2.4.1-beta.22.

- Require a Home Assistant administrator on all ten Ingress approval routes
  (plan, F3 and typed inboxes, reviews and decisions). One shared gate needs
  exactly one well-formed `X-Remote-User-Id` and reads the fixed, read-only
  `config/auth/list` command on every request, with no caching. A missing
  identity or non-administrator is refused with 403, and a failed or
  ambiguous lookup with 503, before any governance, F3, typed-service, CSRF
  or device access. Absorbs HAMCP-199.
- Treat an inactive user, the owner included, and a system-generated account
  as not an administrator (review L1).
- Remove the anonymous default approver. Governance refuses a missing or
  anonymous principal with `external_approval_invalid`, and a stored
  anonymous approval is refused at apply and rollback with
  `external_approval_required` and reason
  `anonymous_approval_requires_reapproval`; re-plan and approve as a
  verified administrator.
- Add `approval_identity` to `get_server_health` (refusal counts by reason,
  no user IDs).
- Preserve the catalog, input schemas, signed registries, HOLD, support
  window, persisted formats, workflows and stable v1.

## 2.4.1-beta.20 - Shared sanitizer with identity-only redaction markers (HAMCP-191)

Builds on 2.4.1-beta.19.

- Use one strict sanitizer for every tool. Every rule runs first, then any
  string that still looks like a redaction marker (including keys and
  encoded or folded spellings) is replaced as a whole by
  `[REDACTED:redaction_marker_collision]`. This closes the P1 (secret
  wrapped in a forged marker) and P2 (anchor split by a forged marker) leaks.
- Keep only the markers Engineering produced in the same request, checked by
  object identity against a per-request record (approval review, plan views,
  impact, integrity, reliability, gateway-withheld evidence and the audit
  sink). A byte-identical upstream copy collides.
- Keep beta.19's marker tolerance only for the re-scan of stored plans (D11),
  through one allow-listed entry point. Every plan family (configuration,
  legacy automation, dashboard, backup, helper state, reload, restart) runs
  the strict detector before persisting or returning a new plan, and refuses
  flagged text, the automation identifier included. Follow-up: HAMCP-207.
- Write every governance plan record through one storage chokepoint
  (`ChangePlanRepository.save`). It strict-sanitizes all new strings,
  identifiers included, refuses on creation and in identity, configuration
  and dispatch fields, and redacts in place in evidence, receipts, failures
  and events. Dispatch and recovery state stays byte-exact in 72 exempt
  schema paths whose formats cannot carry free text. Old stored content is
  written back unchanged. Static and runtime writer-enumeration tests guard
  the chokepoint, and flagged identifiers are withheld from response
  metadata (review findings H1g, H2).
- Screen the inbound request ID once at request start and replace a
  token-shaped or known-secret ID with an Engineering-generated one,
  counted in health as `request_id_replacements` (review finding H4).
  Screen every caller-supplied governance-tool identifier at the tool
  boundary before any tool work, refusing with `INVALID_REQUEST` and no echo
  (review finding H1g-residual). Gate dashboard `url_path` and the caller ID
  before any read, and write the dashboard artifact only after the plan
  passes the strict gate (review finding M2). Run the D11 stored-plan
  re-check for contract-v1 records too (review finding L3).
- Add `sanitization` to `get_server_health` (`strict_marker_mode`,
  `marker_collisions`, `marker_collision_refusals`). Look-alike text can now
  block a governed edit, and each refusal is counted.
- Report `completeness: partial` with a bounded `metadata.redaction` summary
  when redaction applied anywhere in the response (including warnings, hints
  and server identity) on the generic delegated read, the held-read canary,
  the YAML reader and dashboard get/list; the delegated read also warns that
  the result is not complete.
- Sanitize Core log history in two passes: every credential rule runs over
  the whole window first (an LF or CRLF credential split across lines is
  still redacted, and the line count is kept), then a marker-like line
  collapses only that line (a CRLF line keeps its `\r`), with bounded `redacted_line_count`, `collided_line_count` and
  `redaction`. Structured-log redaction counters include the first pass.
- Scan or withhold a key whose scan failed (never write the raw key back),
  count evidence truncation from a structural flag, and show
  `(authorization header withheld)` in incident correlation.
- Ordinary data is byte-identical to beta.19 (85-sample corpus: 68
  identical, 17 changed by decision, 0 unexpected). Preserve the catalog,
  input schemas, signed registries, HOLD, support window, persisted formats,
  workflows and stable v1.

## 2.4.1-beta.19 - Prompt verification of slow typed fan and power devices (HAMCP-192)

Builds on 2.4.1-beta.18.

- Poll the entity inline for about 10 seconds after a typed fan or power
  dispatch (reads at about 0, 1, 2, 4, 6, 8 and 10 s, at most 7) and verify
  on the first match. A device that reports a few seconds late is now
  `succeeded_verified` in the original call instead of `observing`.
- Shorten the typed fan/power entity lease from 120 s to 30 s, renewed every
  10 s, so a task that does yield `observing` is re-read by the first sweep
  after about 40-70 s.
- Make one final read at or after the 180 s evidence deadline before any
  manual-review decision (`final_read_at_deadline`, opt-in, used only by the
  typed services). F3 governed executions are unchanged.
- Keep `lock_conflict` with no superseding, and name the blocking task in the
  receipt (`blocking_tasks`, `blocking_tasks_observed_at`, `lease_status`).
  The hints and the lock check share one predicate
  (`DurableLockStore.holder_blocks`), so an expired lease that recovery has
  not yet reconciled is named as `expired_pending_recovery`.
- Add derived, additive receipt hints: `observation_status` and, for manual
  review, `hold_release`.
- Add a governed hold release for a manual-review typed task on the Ingress
  page `/typed`. It shows the requested and freshly read state side by
  side, needs the Ingress principal and a single-use token bound to the reviewed
  facts and readback, refuses if the state changed after review, records a
  fresh readback in an additive sidecar and exactly one audit entry (keyed by
  a stable `hold_release_id`, retried by recovery after a crash or failed
  write, `audit_recorded` set only after an enabled audit logger confirms the
  write, otherwise `false` with `audit_disabled` or `audit_write_failed` and
  backfilled by recovery once auditing is enabled), and never dispatches. The outcome stays `manual_review_required`, reported as
  `hold_released_state_matches` or `hold_released_state_differs`. The
  administrator-role check for every approval page, including `/typed`, is
  deferred to HAMCP-205.
- Preserve the catalog, input schemas, signed registries, HOLD, support
  window, existing persisted formats, workflows and stable v1.

## 2.4.1-beta.18 - Health verdict cache and sweep parent reads (HAMCP-171)

Builds on 2.4.1-beta.17's typed, registered REST routes (HAMCP-200). Josh's 2026-10-18 decisions D1–D6 apply.

- Memoize the pure per-segment sanitizer scan within one health validation
  job (D5). The memo is bounded, bound to the job's sensitive values and
  discarded when the job ends.
- Keep health safety verdicts in process memory between calls for unchanged
  records only (D1). Key: canonical filename, `st_ino`, `st_size`,
  `st_mtime_ns` (from `fstat` of the parsed handle), the SHA-256 of the
  parsed bytes and the detector context. Any change, a new record, a failed
  `fstat` or a key mismatch validates fresh. Any failed scan, storage error,
  quarantine or unexpected worker failure discards every verdict. Verdicts
  are never persisted, are empty at start and are never used by
  authoritative paths.
- Add the optional `get_server_health(deep_storage_audit=true)` input, which
  bypasses and repopulates the cache. It and two description lines are the
  only public descriptor change; the catalog stays at 88. The internal `deep_audit_plan_store` also
  discards every verdict.
- Report cache hits, misses, deep re-scans and invalidations under
  `plan_store_scaling.safety_verdict_cache`. `health_cache_hit_count` now
  counts async calls that reused verdicts.
- Read a parent task in the periodic recovery sweep only for orphan cleanup
  (no dispatch intent) or a terminal child with a recorded readiness fault.
  Dispatch, redispatch, expiry and cancel behavior is unchanged.
- No persisted-format (D6), registry, workflow or stable-v1 change.

## 2.4.1-beta.17 - Typed, registered routes for every Home Assistant REST call (HAMCP-200)

Builds on 2.4.1-beta.16.

- Accept only a typed `Route` in the REST transport. `rest_path` is the only
  way to obtain one, and only for a reviewed template in the closed
  `ROUTE_TEMPLATES` registry. A `Route` cannot be constructed, subclassed,
  mutated, cloned or pickled, and a plain string, including a fixed one, is
  refused as `unbound_request_path` before any session, credential or
  request exists.
- Bind every value through one guard. It refuses the dot segments, `/`,
  `\`, `?`, `#`, NUL and control characters, plus their percent- and
  multiply-encoded forms, and otherwise encodes the value as one segment.
  Before attaching the token, the transport re-encodes the route from its
  binding and requires the normalized URL to equal the template.
- Never follow redirects. The REST transport, the Core re-admission reader
  and the Supervisor self-information reader send `allow_redirects=False`,
  and a 3xx response is a failure.
- Check identifiers at the tool boundary for `get_automation_config`,
  `get_entity`, `get_history` and `automation_reliability_analysis`. An
  invalid value is refused as `invalid_request` ("The identifier is
  invalid.", reason `invalid_identifier`), audited and counted, with no Home
  Assistant request. Handoff generation skips unsafe IDs from Home Assistant
  and reports partial coverage. Governed writes report the refusal as not
  dispatched.
- Narrow automation configuration IDs to `[A-Za-z0-9_:-]`: dotted IDs are no
  longer accepted by `automation_reliability_analysis` or
  `capture_automation_baseline`.
- Remove the compatibility module's standalone ASGI gateway and `main()`
  entry point. The add-on still starts `python -m ha_mcp_engineering`.
- Add a secondary source scan that fails on raw, concatenated or formatted
  request paths, route literals outside `rest_path`, computed templates,
  `.request` aliases and websocket URLs other than the configured one.
- Disclose the corrected past exposure: a crafted identifier could make
  Engineering send token-bearing GET requests to other HA/Supervisor routes,
  including any GET-enabled webhook (which can trigger automations); no
  POST/PUT/DELETE/PATCH was reachable.
- Preserve the catalog, input schemas, signed registries, HOLD, support
  window, persisted formats, workflows and stable v1.

## 2.4.1-beta.16 - Optional reviewed YAML configuration reader (HAMCP-162)

Builds on 2.4.1-beta.15's rollback Core lease, cancel telemetry, expired
pre-intent deadline and gateway audit reason corrections (HAMCP-189 PR B + C)
and 2.4.1-beta.14's Core-authority, unmapped-refusal and durable-cancel
corrections (HAMCP-189 PR A).

- Add the delegated read-only tool `ha_config_get_yaml` for one mapping-key
  path of `configuration.yaml`. Engineering sends a fixed upstream shape
  (`include_content: false`, `include_parsed: true`).
- Refuse every other file (including `secrets.yaml`, `packages/`,
  `configuration.yml` and case variants) and every credential-shaped path
  before route acquisition, Core acquisition and any upstream call.
- Redact on absolute canonical key paths:
  - credential-shaped keys and containers;
  - connection-shaped values, except one strict safe SQLite shape;
  - credential text: userinfo, 13 words and the `pass`/`key` token rule;
  - percent-encoding up to four rounds, NFKC and casefold;
  - PEM, opaque blobs and the existing sanitizer patterns. Opaque blobs
    are also sought with all whitespace removed, so a wrapped header-less
    key body is redacted (A1).

  `!secret`/`!env_var` become typed reference-name markers, trusted only by
  the identity of a marker the same read minted. Values and `!env_var`
  defaults are never returned, and `!include` is never followed.
- Apply the 4,000-node cap to the upstream tree before redaction, so a
  truncated mapping is judged in its returned shape; marker-shaped mappings
  are withheld. Pass every other client-visible field (limitations, include
  and path descriptions, warnings, metadata, error details) through a final
  shared-sanitizer pass with the configured known secrets.
- Trust redaction markers by registry identity only (H3). Upstream text
  that could read as a marker, including encoded, look-alike and
  byte-identical forms, is replaced whole as `redaction_marker_collision`,
  and a mapping with such a key is withheld. Every upstream string runs
  through every rule. The shared sanitizer's default mode for other tools
  is unchanged.
- Screen every emitted string through one function, `screen`, with the
  complete rule set in every one of 21 output positions (H4). Canonical
  keys get the PEM and opaque-blob scanners; path segments are screened
  under the segments above them. Values and list items keep the D12
  exception for key text. A 798-cell rule × position matrix and a
  coverage test pin it.
- Build every HAMCP-162 synthetic secret at runtime from fragments, and
  store the D1 captures and fixtures through a reversible encoding
  (`docs/evidence/hamcp162/codec.py`, `DECODED.sha256`). A test fails if a
  committed HAMCP-162 test, fixture or evidence file matches the
  repository secret-pattern scan.
- Disclose on every success the YAML 1.2 parse (K7), key conversion and
  collapse (K5) and best-effort redaction (D12). Disclose the first-call
  admin caller-token dependency (K1: the component's
  `POST ha_mcp_tools/get_caller_token`) in the description; Engineering makes
  no write or service call.
- Map upstream errors to fixed codes, with no upstream text. HTTP 400 is
  reported as "upstream validation failed or component unavailable" (K2);
  non-UTF-8 and other 500 errors as "upstream read failed" (K4).
- Admit the tool as optional only on the exact complete-descriptor binding
  for ha-mcp 8.4.3 (78 -> 81 upstream tools) and 8.5.0 (77 -> 80):
  - explicit dispositions for every registry version, with no default;
  - excluded from the stock order fingerprint, aggregate admission and signed
    selections;
  - a drift, removal or disposition change between admission and dispatch
    gives zero dispatch;
  - Core mapping: basic REST and WebSocket reads.
- `ha_list_files` and `ha_read_file` stay reviewed and never exposed. The
  catalog grows from 87 to 88 tools (29 delegated).
- Preserve signed registries, HOLD, support window, policy JSON, persisted
  formats, workflows and stable v1.

## 2.4.1-beta.15 - Rollback Core lease, cancel telemetry, expired pre-intent deadline, gateway audit reason (HAMCP-189)

Builds on 2.4.1-beta.14.

- Acquire and consume a Core lease inside `rollback_change` before any fresh
  rollback planning read (configuration, garage inverse and entity metadata),
  run every planning read under an authorizer that revalidates it, and require
  a current lease where a configuration or garage-inverse rollback plan is
  saved, after its last planning read (the configuration builder gains a
  private, default-off save hook for this). Under degraded Core,
  fresh planning is refused before any Home Assistant read as
  `provider_unavailable` / `core_capability_unavailable` and no plan is
  created. Existing and cached rollback plans are still returned locally.
- Begin an observed operational read's upstream attempt only after the Core
  dispatch authorizer passes, so a refused read reports zero upstream
  requests, and refuse a retained-session dispatch whose caller stopped
  waiting.
- Terminate a contract-v3 task whose dispatch authority expired before its
  dispatch intent: bounded retries continue for 600 s after expiry, then the
  F3 runtime ends the child `preflight_rejected` / `plan_expired`, the task
  `failed_pre_dispatch` and the plan `FAILED`, releases its exact locks and
  restores readiness. Only children with no intent, no dispatch count, no live
  claim, no recorded cancel and only their own recorded locks qualify. A task
  saved before its first child record gets the same terminal outcome after the
  same deadline, written locally with no provider contact; cancel and a
  competing first claim keep precedence. Past the deadline the terminal
  write happens locally before any provider or provider-identity contact.
  No new stored string; nothing is dispatched.
- Audit an empty tool name refused as `core_requirement_unmapped` with the
  full unmapped-refusal record and metric, counted once, and audit every
  non-string tool name (including `null`, `false`, `0`, `[]` and `{}`) as
  one generic request with one error count instead of failing the audit.
  Diagnostic logs record only `non_string_tool_name:<type>` for such a name,
  and the MCP SDK's validation warnings keep only a bounded summary.
- Correct the repository's beta.13 release notes: three allowlisted tools are
  not "local state only", and rollback planning read without Core authority.
  The published beta.13 GitHub Release is unchanged.
- Preserve the 87-tool catalog, input schemas, signed registries, HOLD,
  support window, persisted formats, workflows and stable v1.

## 2.4.1-beta.14 - Core authority before "already satisfied", exact unmapped refusal, durable cancel (HAMCP-189)

Builds on 2.4.1-beta.13's fail-closed Core gateway.

- Acquire and consume a Core lease before any governed F3 preflight read, run
  every preflight read under an authorizer that revalidates it, and revalidate
  after the reads. Under degraded Core, a step whose target is already reached
  is refused before any Home Assistant read as
  `provider_unavailable_pre_dispatch` / `dispatch_authority_unavailable`, with
  no verified outcome and no approval consumption. Under admitted Core the
  HAMCP-181 `succeeded_verified` / `desired_state_already_reached` outcome is
  unchanged. Fan and power steps are unchanged.
- Admit the final dispatch call only after the pre-dispatch checks consume the
  dispatch authority, and only while it revalidates.
- Refuse an F3 operation with no Core route as `core_requirement_unmapped`
  before any read, lease or approval consumption; refusal codes are a closed
  set. Every registered F3 operation has a Core route today.
- Record an operator cancel durably (`operator_cancel_requested`) before any
  provider contact, from the stored provider identity, so cancel succeeds
  during an ha-mcp outage or identity drift. Every recovery pass checks for a
  recorded cancel before and after each await that precedes execution and
  never dispatches a cancelled task; a restart between the child cancel and
  the public event still projects `cancelled_pre_dispatch`. The cancel result
  adds `provider_contacted: false`. Cancel after a dispatch intent is still
  refused.
- Preserve the 87-tool catalog, input schemas, signed registries, HOLD,
  support window, persisted formats, workflows and stable v1.

## 2.4.1-beta.13 - Core gateway fails closed for unmapped tools (HAMCP-164)

Builds on 2.4.1-beta.12's audit hardening.

- Refuse any tool name with no Core requirement mapping at the gateway,
  before Core acquisition and before any provider, upstream or Home Assistant
  call: `CoreRequirementUnmapped`, `unsupported_operation`, reason
  `core_requirement_unmapped`, not retryable, with zero-attempt telemetry and
  a value-free audit record. A missing mapping no longer implies Core
  independence.
- Declare the 16 Core-independent native tools in a closed, immutable,
  exact-name allowlist, each with a basis and justification; `server_info` and
  `get_server_health` remain conditional on `check_ha`. Ambiguous route tables
  refuse to import.
- Recognize delegated reads only while currently published as `delegated`
  read-gateway capabilities; their Core requirements and withholding are
  unchanged.
- No working tool changes behavior. Unknown or withdrawn names now receive
  `CoreRequirementUnmapped` instead of the MCP SDK's `Unknown tool` error.
- Correct stale MCP SDK version comments and test wording (comments and
  documentation only).
- Preserve the 87-tool catalog, signed registries, HOLD, support window,
  persisted formats, workflows and stable v1.

## 2.4.1-beta.12 - Audit hardening (HAMCP-184)

Builds on 2.4.1-beta.11's MCP SDK 1.30.0 security upgrade.

- Fail closed on provider dispositions (F-05): an absent surface or unknown
  value reads `held`, and the registry loader refuses a reviewed entry without
  explicit dispositions for all four provider surfaces. The five legacy
  entries (7.14.1-8.2.0) now declare their dispositions explicitly. ha-mcp
  8.2.0 backup and lifecycle become `held` (no capability-specific review);
  every other effective disposition is unchanged, and the production 8.5.0 and
  rollback 8.4.3 entries are byte-identical.
- Validate the caller's `expected_plan_hash` on F3 re-apply of an existing
  task (F-01), refusing an empty or wrong hash with `approval_hash_mismatch`
  before any dispatch or state change. A matching replay is unchanged.
- Report principal separation truthfully (F-02): an approval or rejection by
  the anonymous Ingress fallback principal stores and displays
  `principal_separation_enforced: false`, with a `principal_separation_status`
  reason (`anonymous_fallback_principal`); identified principals keep `true`.
  Older records are projected, not rewritten. Approval eligibility, flow and
  elevated-class behavior are unchanged.
- Check the generated add-on matrix runner labels, not a declared constant, in
  the CI runner guard (F-06); `ci.yml` is unchanged.
- Preserve the 87-tool catalog, signed registries, HOLD, support window,
  persisted formats, workflows and stable v1.

## 2.4.1-beta.11 - MCP SDK 1.30.0 security upgrade (HAMCP-188)

Builds on 2.4.1-beta.10's truthful dispatch facts.

- Upgrade `mcp` from 1.28.1 to 1.30.0 for GHSA-84m7-p3x7-pcfv /
  CVE-2026-59951 (stateful Streamable HTTP sessions never reclaimed). Only
  `mcp` changes: requirements, both locks with the reviewed wheel hash, the
  build-input inventory and the reviewed SDK version guard.
- Inherit the SDK fix: in stateful mode, refused opening requests, DELETE and
  idle sessions are reclaimed, and idle now means no request in flight. The
  SDK's own 4 MiB body and 10,000-session limits sit behind Engineering's
  2,000,000-byte gateway and 1,024-session caps. The outbound client follows
  only method-preserving redirects that stay on the same origin or are a
  same-host, default-port HTTP→HTTPS upgrade.
- Add offline contract tests for batch refusal, protocol negotiation, session
  reclamation and outbound redirects.
- Preserve the 87-tool catalog and every descriptor, fingerprints, signed
  registries, HOLD, support window, storage formats, workflows and stable v1
  (which still pins `mcp==1.9.0`; a separate owner decision).

## 2.4.1-beta.10 - Truthful dispatch facts on every surface (HAMCP-181 PR1)

Builds on 2.4.1-beta.9's truthful entity-registry error results.

- Derive the dispatch facts once, from the stored F3 records, and publish
  them on every apply, plan, task, receipt, refusal, audit, health,
  reconciliation and admin surface and on typed fan and power receipts:
  `dispatch_phase`, `provider_dispatch_occurred`, `changed_by_engineering`
  where proven, `attempt_counts_basis` beside every stored counter, and
  `dispatched_at_basis` with `dispatch_intent_recorded_at` and
  `provider_response_recorded_at`.
- Keep fan and power `dispatched_at: null`; publish `null` with
  `call_not_sent` for a governed task whose provider call was proven not
  sent; keep every other stored value.
- Claim "rejected, not changed" only for automation or script REST 4xx and
  entity-registry `unauthorized` with permission denied; every other
  confirmed failure with a response stays mutation-unproven, and a dispatch
  result with an uncertainty code (including beta.9's registry error result
  after send) stays send-uncertain through readback.
- Make duplicate-apply (F-03) and cancellation wording follow the phase, stop
  labelling a local configuration refusal as a provider response, project the
  stale F3 `plan.operational.dispatch` block from the facts, and relabel the admin
  "Dispatch count" as "Dispatch intents".
- Preserve every status, state, terminal outcome, stored counter and
  timestamp, retry and eligibility rules, persisted formats, signed
  registries, HOLD, support window, workflows and stable v1. The backup,
  lifecycle and helper-state gateway audit labels are unchanged (disclosed
  limitation).

## 2.4.1-beta.9 - Truthful entity-registry error results (HAMCP-183)

Builds on 2.4.1-beta.8's dependency-scoped dashboard write admission.

- Record a sent `config/entity_registry/update` answered with an error result
  other than `unauthorized`/`forbidden` as uncertain (`dispatch_indeterminate`,
  `provider_error_after_send`) instead of confirmed non-mutation, because Core
  can assign the entry and still answer `unknown_error` (OD-15).
- Settle it by the existing registry readback: the planned state is
  `succeeded_verified` with rollback offered, the prior state is
  `verification_mismatch`, any other state is `manual_review_required`
  (`readback_matches_neither_plan_nor_prior`), and an unavailable readback
  stays `observing` until manual review. Recovery applies the same mapping from
  the persisted dispatch diagnostics; nothing is redispatched.
- Keep authorization refusals confirmed non-mutation and Engineering's pre-send
  refusals unchanged; correct the gateway comment.
- Preserve task states, terminal outcome strings, durable formats, the signed
  registries, HOLD, support window, workflows and stable v1. This fix gates
  HAMCP-182 publication.

## 2.4.1-beta.8 - Dashboard-write dependency-scoped admission (HAMCP-177)

Builds on 2.4.1-beta.7's delegated platform reads.

- Admit dashboard write authority on exactly `ha_config_get_dashboard`,
  `ha_get_skill_guide` and `ha_config_set_dashboard`, each present once and
  matching every reviewed contract component, with server identity, release,
  protocol, attestation, revocation, HOLD, the `admitted` dashboard
  disposition, Core authority and same-session revalidation preserved. Other
  advertised tools grant nothing and stay unexposed and undispatchable; the
  authority evidence is unchanged for the 77- and 80-tool catalogs.
- Refuse `create_dashboard_update_plan` without admitted write dependencies as
  non-retryable `provider_unavailable` (`dashboard_provider_not_admitted`).
- Record a Core authority refusal immediately before the setter call as
  confirmed no dispatch instead of an uncertain outcome: failed after the
  saved-intent boundary (`failed_post_dispatch`); no setter message sent; no
  change made; never redispatched. The evidence, apply result, task read,
  replay refusal and audit state `setter_call_not_sent` true,
  `provider_dispatch_occurred` and `dispatch_performed` false,
  `changed_by_engineering` false and `intent_recorded` true; the truthful
  pre-send phase is HAMCP-181. Every other transport error stays
  conservatively dispatched.
- Preserve the whole-catalog validator for the read gateway, backups and
  lifecycle; the signed registries, HOLD, support window, durable formats,
  workflows and stable v1.

## 2.4.1-beta.7 - Delegated platform health, add-on and integration reads (HAMCP-129)

Builds on 2.4.1-beta.6's typed power and fan admission repair.

- Admit `ha_get_system_health`, `ha_get_app` and `ha_get_integration` at
  ha-mcp 8.4.3, 8.5.0 and 8.6.0 through Engineering-owned wrappers bound to
  each exact input-schema fingerprint and source commit.
- Close the public schemas and build the only upstream arguments; apply
  `slug`, `domain` and `entry_id` as Engineering-side filters without sending
  their upstream modes; refuse `include_options`, `include_schema`,
  diagnostics, config checks, the full ZHA dump, the add-on store and
  `repository` before dispatch.
- Project every result through a positive output allowlist with list and
  string caps; mask e-mail-like title substrings; withhold upstream error
  text and warnings; refuse `include_stats` when a stats-free snapshot shows
  more than 32 started add-ons (a sampled bound); report unavailable stats as
  `partial` with `stats_unavailable`.
- Keep signed-only ha-mcp reuse at the 25 base delegated reads and withhold
  only the three unbound platform wrappers, reported as
  `platform_read_binding_absent`. Signed entries that bind the earlier 8.4.3
  policy digest keep that exact base policy through one enumerated,
  digest-verified legacy binding; no other digest is an alias.
- Catalog 84 to 87 tools (28 delegated reads); regenerate the compiled
  ha-mcp release registry evidence identities for the new counts.
- Preserve the owner-signed Core registry, HOLD, support window, durable
  formats, workflows and stable v1.

## 2.4.1-beta.6 - Typed power and fan admission repair (HAMCP-175)

- Admit typed power and fan sessions on their three required upstream tools
  (each matched against every reviewed contract component) instead of the
  whole catalog, so unrelated ha-mcp additions no longer refuse them; any
  other tool name is refused and the generic read gateway is unchanged.
- Surface pre-dispatch admission refusals as bounded, non-retryable
  `provider_unavailable` across MCP SDK exception groups.
- Count and finish every provider exchange so `upstream_attempted` is truthful.
- Return no task pointer when a failure or cancellation happens before the
  declaration is saved; keep it once a declaration exists or its state is
  unknown.
- Preserve tools, public descriptions, schemas, routing, registries, durable
  formats, workflows and stable v1.

## 2.4.1-beta.5 - Deep health survives the idle recovery sweep (HAMCP-168)

- Defer only the idle historical recovery-cursor write while a deep-health
  scan holds its fence, for at most three consecutive fenced sweeps, so an idle
  scan longer than the 30-second cadence no longer fails as superseded.
- Keep every real recovery, child, lock, task and plan write superseding the
  scan; the public error code, message, details and cause are unchanged.
- Defer the refusal diagnostic warning to a separate logging-isolation task;
  no logging change is included.
- Preserve tools, schemas, routing, admission, registries, durable formats,
  workflows and stable v1.

## 2.4.1-beta.4 - Governed entity-registry metadata administration (dormant)

- Add `create_entity_metadata_plan` for one entity's registry name, icon,
  aliases, area, user-hidden flag, existing labels and categories, approved and
  applied through the existing governed plan tools with exact readback.
- Require the new Core profile `core.entity_registry_metadata_operation`; no
  signed registry entry selects it, so every supported Core fails closed with
  no Home Assistant request until a separate owner-signed admission.
- Refuse unsupported fields and undeclared arguments before any read; dispatch
  only a closed registry update; skip the YAML configuration check for this
  registry-only write.
- Report an already-present final state as not dispatched and keep that plan
  terminal across replay, state reversion, restart and rollback attempts.
- Preserve the 21 existing Core contracts, probe fingerprints, signed
  registries, beta.3 public descriptors, stable v1, durable formats and
  workflow permissions. Label creation, enable/disable and exposure remain
  later work.

## 2.4.1-beta.3 - Exact ha-mcp 8.6 pairing, cooperative health and diagnostics

- Add exact 8.6 reference/component catalogs, closed read adapters and skill-guide
  migration, preserving 25 reads and existing governed fan/power/dashboard routes.
- Bind Core 2026.10.0/8.6 to beta.2's unchanged October probe and fingerprints;
  retain September and 8.5 behavior, signed authority and withheld capabilities.
- Add bounded native-architecture CI coverage for both Core versions, profiles
  and packaging forms, with immutable inputs and owned cleanup.
- Collect explicit asynchronous deep health once per record with cooperative
  yields and validated record reuse; preserve storage-fault attribution,
  canonical parent identity, cancellation and inverse-lock reporting.
- Record bounded Core reconciliation failure reasons and successful failure
  retries without changing signed authority or the 300-second retry policy.
- Preserve the distinction between synthetic responsiveness evidence and
  installed behavior; the historical October disconnect's cause remains unproved.
- Preserve stable v1, durable ownership/recovery, dependency locks, workflow
  permissions and previous evidence. Upstream installation, HACS startup effects,
  household acceptance, Govee and HAMCP-151 remain separate decisions.

## 2.4.1-beta.2 - Exact October Core device-registry compatibility

- Add the separately selected October `next_name_part` schema and probe without
  changing historical schemas, signed fingerprints or effective-area semantics.
- Preserve all 83 public descriptors, 21 capability contracts, exact ha-mcp
  8.5.0, signed journals, dependency locks, durable formats and stable v1.
- Retain independently reconciled actual-Core 2026.10.0 native-amd64 evidence
  for all 21 profiles, both providers, Alarmo, baseline capture and garage rules.
- Keep production registry signing and installed October acceptance separate;
  neither a beta.2 installation nor a signature alone completes both steps.
- Preserve completed garage recovery evidence; no new household drill, Govee,
  HAMCP-151, ha-mcp 8.6.x or Core upgrade is included.

## 2.4.1-beta.1 - Governed garage configuration recovery

- Add the reviewed exact inverse of a durably verified HAMCP-135 forward prefix,
  with fresh elevated approval, whole-bundle checks and script-last restoration.
- Retain continuous lock ownership between inverse children, check deadlines
  inside the transfer transaction and preserve recovered cancellation without
  masking genuine storage faults or repeating dispatch.
- Preserve partial/unknown outcomes, historical prohibited plans, all 83 public
  descriptors, 21 Core profiles, signed authority, dependencies and stable v1.
- Continue Josh's development track directly from RC1 to the next patch beta.1;
  retain release sequencing checks and all owner publication/deployment controls.
- Keep D2 separate: original-script restoration restores its known retry weakness;
  activation, active-run settlement and household consequences need acceptance.
- Exclude Govee, HAMCP-151 and ha-mcp 8.6.0. Carry the unresolved RC1 projection
  warning forward; this release makes no claim to repair that separate record.

## 2.4.0-rc.1 - Release candidate from accepted beta.16

- Promote the accepted beta.16 Engineering implementation and update only the
  multidict dependency from 6.8.0 to 6.9.1 for CVE-2026-104874. Both hash locks
  and the build inventory carry the reviewed correction; all other pins remain.
- Preserve Engineering runtime logic, providers, storage and workflows. This
  security correction changes the dependency payload and requires fresh validation.
- Preserve all 83 descriptors, 21 Core profiles, exact ha-mcp 8.5.0 admission,
  signed applicability and the existing technical Beta installation identity.
- Carry forward bounded analysis, baseline capture, verification, F3 and exact
  garage forward-eligibility behavior with their disclosed limitations.
- Keep garage governed rollback/D2, Govee administration, HAMCP-151 restart and
  ha-mcp 8.6.0 work separate. RC/GA acceptance does not authorize household work.
- Require exact-source release review/CI, publication and bounded installed
  acceptance before the separate 2.4.0 GA transition. Stable v1.1.2 is unchanged.

## 2.4.0-beta.16 - Bounded garage configuration eligibility

- Admit only the proved minimal retry-removal or complete caller-owned retry
  transformation through the existing externally approved configuration route.
- Preserve all 11 positive guards in the full design, its response protocol,
  policy rechecks, wait durations and accepted caller modes.
- Require active F3, whole-baseline and prefix checks, final-bundle verification
  and existing one-dispatch ownership; no legacy-writer fallback is available.
- Keep prohibited script restoration unavailable and caller-only recovery
  explicitly partial. D2 still gates household application; no live action is
  authorized by installing this capability.
- Preserve all 83 descriptors, 21 Core profiles, signed applicability, provider
  admission, durable formats, dependencies, workflows and stable v1.1.2.
- Govee package administration and HAMCP-151 restart work remain separate.
  Source/disposable proof, release validation and installed read-only acceptance
  are distinct from a future household configuration change.

## 2.4.0-beta.15 - Bounded readiness and dashboard integrity analysis

- Remove retained-history scans from ordinary F3 request readiness; keep reads
  available after startup while execution faults still refuse new F3 applies.
- Reconcile exact terminal child completion faults, including no-dispatch cases,
  without replay, consumed approvals or weakened audit/storage/hold checks.
- Add one bounded dashboard analyzer with literal references, configured controls,
  explicit partial coverage, private frozen continuations and accurate telemetry.
- Preserve the previous 82 descriptors and all Core profiles; the catalog is
  83 = 58 native + 25 delegated. No new signed capability is required.
- Correct baseline capture retryability and source-versus-configuration errors.
- Preserve durable formats, stable v1, approval policy and dependency pins. The
  separate F3 namespace-capacity issue remains open; no retention cleanup is added.
- Require combined source/CI, disposable dashboard and installed acceptance
  evidence. This entry does not assert deployment or authorize household actions.

## 2.4.0-beta.14 - Native automation-baseline capture

- Add one bounded read-only capture tool for loaded automations, including off
  objects, with canonical IDs, exact ephemeral configuration hashes and explicit
  partial/non-atomic coverage. Continuations read a frozen in-memory snapshot.
- Preserve the offline fingerprint/comparison contract and all legacy unknowns.
  Registry lineage distinguishes recreated anchors but not restored clones.
- Add one signed-gated Core profile; keep the journal unchanged. Source catalog
  is 82 = 57 static + 25 delegated; the preceding descriptors/profiles remain.
- Extend the existing exact-Core disposable lane with synthetic capture/export/
  comparison and observed read intervals; preserve workflow permissions/pins.
- No writes, automatic captures, fallback, approval changes or stable-v1 changes.
  Publication, signed activation, deployment and installed acceptance stay separate.

## 2.4.0-beta.13 - Offline audit comparison and validation isolation

- Carry forward the reviewed offline automation-baseline validator/comparator,
  including chronological/non-duplicate capture checks and explicit unknowns.
  It adds no MCP tool, live collector or automatic inventory capture.
- Supersede the unpublished beta.12 candidate with a new release source that
  includes the Alarmo responsiveness-test isolation correction. Preserve its
  fixture, default natural GC and timing limits; no production latency fix is
  claimed. Earlier failed publication evidence remains unchanged.
- Preserve all 81 descriptors, 20 Core profiles, signed applicability, providers,
  approval/execution behavior, dependencies, workflows and stable v1.1.2.
- Separate offline utility validation, publication and installed acceptance;
  no new signing/activation or repeat household write canary is required.

## 2.4.0-beta.11 - Bounded Alarmo configuration inspection

- Add one native read-only inspector with positively selected evidence, exact
  panel scope, explicit partial/non-atomic coverage and bounded cached pagination.
- Preserve all prior descriptors and 19 Core contracts; the healthy catalog is
  81 = 56 static + 25 delegated, with one additional semantic Core read profile.
- Add reviewed capability-extension preparation and required disposable
  Core/Alarmo integration; production signing/activation remains separate.
- Update PyJWT 2.14.0 to 2.15.0 for GHSA-42vr-xj54-vc7v, preserving all other
  dependency pins, base image and architecture policy.
- Preserve household operation, approval, recovery, storage and stable-v1
  boundaries. No Alarmo actions or generic integration forwarding are exposed.
- Require installed source assurance, signed admission and useful read acceptance
  separately from source validation and release publication.

## 2.4.0-beta.10 - Bounded logbook reads and response processing

- Honor explicit requested intervals through 168 hours with one bounded native
  GET; cap acquisition and JSON processing, retain whole sanitized partial
  evidence, and preserve HTTP failures without retry, redirect or fallback.
- Classify concurrent reads as retryable logbook_busy (409) with zero additional
  requests. Preserve the single worker, cancellation lifetime and no-backlog rule.
- Avoid repeated whole-response serialization while preserving action receipt
  facts. Truncation metadata adds omission count/completeness fields and can
  remove an additional optional field near the limit.
- Disclose the changed get_logbook description and partial data-object shape;
  input signatures, annotations and 80-tool count remain unchanged. Preserve
  beta.9 health, provider/authority/approval boundaries and frozen stable-v1.
- Separate synthetic timings from installed behavior. Publication, assembled
  Core integration, deployment and installed acceptance retain their own gates.


## 2.4.0-beta.9 - Cold governance-health responsiveness

- Validate detached persisted-plan records in one pure worker per service during
  public cold health reads; yield owner-loop policy/lifecycle work between records.
- Preserve fail-closed validation, generation checks, concurrent owner approvals,
  expiry and current provider/authority observations. Serialize readers and drain
  canceled validation before new work; no background cache or lifecycle publisher.
- Retain beta.8 warm reuse and report remaining synchronous costs honestly.
  Source tests prove responsiveness, not production heartbeat causality or
  installed approval-to-dispatch continuity; test that cold interval separately.
- Keep 80 tools, schemas, authority, persisted formats, dependencies, workflows,
  installation settings and frozen stable-v1 unchanged. No new namespace,
  retry or fallback; preserve inherited recovery limitations and completed F028.
  Alarmo is excluded. Publication, deployment and installed acceptance are separate.

## 2.4.0-beta.8 - Governance health-cache correctness

- Reuse history aggregates under their captured plan/task generations; reject
  observed mixed-generation builds, including task saves during metadata refresh.
- Preserve expiry/restart deadlines, clock rollback, live locks/provider health,
  failure visibility and independent returned data. Evaluate F3 health once per
  public call; retain existing approval, dispatch and recovery authority.
- Reduce repeated synthetic history work without claiming installed latency,
  a heartbeat/authority remedy, or elimination of synchronous cold/F3 scans.
- Keep all 80 tools, schemas, stored formats, installation settings, dependencies,
  workflows and frozen stable-v1 unchanged. No new namespace or migration. Retain
  beta.6/beta.7 recovery limitations and completed acceptance; Alarmo is excluded.
  Publication, deployment and installed acceptance remain separate.

## 2.4.0-beta.7 - Configuration verification and dated re-verification

- Preserve literal standalone trigger-condition IDs in verification; refuse
  unsupported comparisons before that operation consumes approval or dispatches.
- Add native configuration-task re-verification with exact original history,
  reconstructed approval/consumption witnesses, current read authority, all-object
  readback/check and bounded durable supplementary receipts. No configuration
  resend, device action, renewed approval or original-outcome rewriting.
- Preserve dated replay and truthful partial/refusal/uncertain-persistence results;
  no automatic re-verification, retries, fallback or evidence pruning. Keep the
  optional receipt namespace during rollback and preserve beta.6 recovery limits.
- Catalog: 80 tools (55 static + 25 delegated). Preserve existing input contracts,
  admission, installation settings, dependencies, workflows and frozen stable-v1.
  Alarmo is excluded. Release preparation does not establish installed acceptance.

## 2.4.0-beta.6 - F3 lock persistence and exact recovery

- Separate a 256-lock capacity from unchanged evidence limits; support complete
  configuration unions above sixteen locks and refuse excess before acquisition,
  approval consumption or provider dispatch.
- Preserve failures and exact fenced cleanup when token recording fails. Recover
  only proven terminal zero-dispatch tokenless configuration children by binding
  exact generations before release; ambiguous ownership remains unresolved.
- Prevent conflict-audit transaction inversion, retain binding-audit work across
  restart and report fixed cleanup diagnostics without masking primary failures.
  Surface bounded current retained/expired/pending diagnostics.
- Deployment can automatically settle qualifying internal records. Preserve
  current evidence and compatible recovery before separate deployment approval.
  Beta.5 cannot read expanded token records above sixteen; an older binary or
  stale execution-state restore is not a sufficient recovery plan.
- Preserve the 79-tool catalog, providers/approval boundaries, installation
  settings, dependencies, workflows and frozen stable-v1. The separate Alarmo
  inspector is excluded. Source preparation is not installed acceptance.

## 2.4.0-beta.5 - Bounded automation lifecycle analysis

- Add static interruption-hazard findings for supported trigger holds and inline
  delays/waits to the existing automation reliability analysis, with exact
  configuration paths, conditional consequences and bounded review notes.
- Exclude disabled/proven-unreachable steps, preserve conditional findings under
  unresolved error continuation, and report unsupported/dynamic syntax,
  stored-script/blueprint gaps and truncation explicitly. Freeze lifecycle
  evidence across continuation pages without changing legacy accounting.
- Preserve tool inputs, provider collection, foundational error behavior,
  the 79-tool inventory, execution authority, stored formats and stable-v1.
  This first inline increment does not prove observed execution or restart
  recovery. Source preparation does not establish publication or deployment.

## 2.4.0-beta.3 - Dashboard capacity and bounded diagnostics

- Raise existing-dashboard patch capacity from 16 to 256 semantic leaves with
  unchanged array-suffix accounting, operation/byte/depth/node bounds and full
  approval projection. Retain exact targets, stale-state checks, one-dispatch
  execution and authoritative readback.
- Report the actual count, limit and fixed constraint/stage for oversized
  patches while preserving public error identity and excluding configuration
  content. Keep unrelated compiler errors unchanged.
- Preserve the 79-tool inventory, provider contracts, authority, stored formats,
  installation settings and frozen stable-v1. The provider read/save interval
  remains non-atomic; restoration needs a separately approved exact plan.
  Source preparation does not establish publication or installed acceptance.

## 2.4.0-beta.1 - Bounded script dependency evidence

- Add canonical script configuration evidence to existing dependency diagnostics,
  including renamed and disabled identities through the admitted internal reader.
  Report partial inventory, source-local failures and dynamic/blueprint opacity.
- Isolate script evidence from the shared helper execution/consequence ledger.
  Preserve provider authority retirement, bounded collection and zero fallback.
- Preserve the 78-tool catalog, public schemas, stored formats, installation
  identity and frozen stable-v1. No script execution or new write is included.
  Source materialization does not establish publication or installed acceptance.

## 2.3.0 - Stable Engineering release

- Promote the accepted RC2 implementation through a version/documentation-only
  transition. Preserve runtime behavior, all 78 tool contracts, signed Core
  authority, provider boundaries and historical projection compatibility.
- Keep the existing technical Beta installation, options, data formats, image
  path and dependencies. No migration or new device cycle is required.
- Carry RC2 acceptance with its original attribution and limitations; require
  fresh stable candidate validation, publication and installed artifact binding.
  Frozen historical stable-v1 1.1.2 remains unchanged.

## 2.3.0-rc.2 - Terminal historical plan readability

- Restore supported reads of reviewed expired-helper and rejected automation
  writer families with original terminal status, policy and immutable hashes.
- Preserve refusal of damaged or unsupported records, task/approval/dispatch
  evidence and persistence-unsafe proposals. Bound malformed-history failures
  without interrupting unrelated reads.
- Count these three compatibility profiles separately from projection failures,
  with no authorization effect, persisted-record rewrite or new execution path.
- Preserve the 78-tool catalog, providers, signed Core authority, installation
  configuration, controlled dependencies and frozen stable-v1. Require RC2
  publication/image binding and separately authorized original-record readback
  before stable promotion; no new household device cycle is required.

## Unreleased - Configuration metadata separation

- Treat Home Assistant automation/script `category` as bounded read-only
  entity-registry metadata rather than REST configuration. Exclude it and
  top-level `id` from provider binding, dispatch, behavioral diffs, and exact
  verification while warning reviewers that configuration writes preserve it.
- Reject unknown automation top-level fields and category-bearing creates
  before approval or dispatch. Advance the automation normalization contract to
  3 and configuration-resource normalization contract to 2 so older pending
  plans cannot silently gain authority under the corrected semantics.
- Classify exact configuration-endpoint 4xx responses as received provider
  rejections with zero provider mutations while retaining indeterminate,
  readback-only handling for transport loss, timeouts, 5xx, and other uncertain
  outcomes.
- Preserve Beta 26 plan-store scaling, Beta 22 approval projection, Beta 20 F3,
  Beta 25 exact 8.1.1 provider/tool behavior, zero fallback, public schemas,
  and stable v1.1.2 unchanged.

## 2.2.0-beta.26 - Plan-store/query/recovery scaling

- Add rebuildable navigation for active plans, approval challenges, recovery
  work, and execution tasks while retaining persisted plan/task/approval/F3
  records as the only authorization and execution authority.
- Apply list limits before plan deserialization, resolve approval detail
  directly, restrict periodic recovery to active candidates, and use bounded
  task aggregates plus a generation-bound governance health cache.
- Add deterministic 130, 1,000, and 10,000-terminal-plan acceptance with
  record-touch and latency evidence, derived-state rebuild/tamper/expiry tests,
  and bounded hot-path observability.
- Preserve Beta 11 restart protections, Beta 22 approval projection, Beta 20
  F3 semantics, Beta 25 exact 8.1.1 provider/tool behavior, zero fallback, and
  stable v1.1.2 unchanged.

## 2.2.0-beta.25 - ha_search automatic-read promotion

- Promote only `ha_search` to `automatic_read` for exact reviewed `ha-mcp`
  8.1.1 after its successful Beta 24 positive-path live canary. Preserve every
  reviewed input, description, annotation/security, output, and runtime
  contract and route ordinary calls through `upstream_read_gateway` with no
  fallback.
- Keep `ha_get_operation_status` as the only `held_for_canary` tool. Exact
  8.1.1 now accounts for 78 advertised tools, 25 automatic reads, one held
  read, 49 Engineering-local tools, and 74 total runtime tools.
- Retain exact 8.0.0 and 8.1.0 classifications, the held-read canary
  implementation, governance and Dashboard behavior, strict exact-release
  admission, secure dependency pins, and stable v1.1.2 unchanged.

## 2.2.0-beta.24 - Evidence-only held-read live canary

- Add one read-only Engineering-native `run_held_read_canary` tool. It is bound
  to the caller-supplied active exact compatibility-entry ID and can dispatch
  only a tool that remains classified `held_for_canary`.
- Revalidate the reviewed input, description, annotation/security, output, and
  runtime contracts before same-session upstream dispatch. Reject automatic,
  mixed, write, high-risk, prohibited, unsupported, unreviewed, mismatched, or
  quarantined tools without fallback.
- Return sanitized bounded evidence with truthful success, partial, and failure
  status; validate declared output contracts; and audit only bounded routing
  and outcome fields without caller payloads or upstream result content.
- Preserve 24 automatic delegated reads and Dashboard provider behavior.
  `ha_search` and `ha_get_operation_status` remain held and absent from the
  dynamic catalog. Canary execution never promotes a tool or mutates policy,
  governance, execution-task, configuration, service, or Home Assistant state.

## 2.2.0-beta.23 - Evidence-bound compatibility-family admission

- Add a strict review-time compatibility-family compiler that emits one exact
  runtime entry only after two deterministic captures and immutable source,
  standalone, add-on, policy, provider, and decision evidence agree. Runtime
  authority remains exact-entry-only; no wildcard, latest, or semver inheritance
  exists.
- Admit exact `ha-mcp` 8.1.1 at `ha-mcp-v8.1.1-e1d76a6e`. Preserve all 78
  8.1.0 tool contracts and classifications, 24 automatic reads, two held reads,
  provider contracts, complete accounting, and zero fallback.
- Add selective automatic-read and provider holds, exact release-specific
  revocation with historical audit visibility, explicit descriptor-normalization
  rules, and strict family-decision/digest validation.
- Verify the 8.1.1 private `websockets==17.0.1` tree and scoped custom-component
  packaging against disposable pinned Home Assistant without importing or
  replacing its shared websocket distribution.
- Preserve Beta 22 approval review, Beta 20 F3 behavior, stable 1.1.2, public
  tools, task schema 1, approval authority 3, and exact 8.1.0 behavior.

## 2.2.0-beta.22 - Complete approval review correctness

- Persist a deterministic, versioned semantic projection for every supported
  configuration operation before a plan can create an external approval
  challenge. Reject unreviewable, non-serializable, incomplete, or genuinely
  oversized proposals with a stable domain outcome and no approval or F3 task.
- Bind the complete projection digest into F2 policy authority, the exact plan
  hash, and transitively the unchanged F3 prepared-operation hash. Tampered
  targets, order, changes, values, schema, completeness, prepared configuration,
  or digests fail closed before dispatch.
- Replace the bounded 200-character/action-count review reconstruction with the
  persisted authoritative before/after projection. Render all one-to-eight
  ordered operations, complete long values in expandable escaped sections, and
  field-aware redaction state without exposing protected values.
- Keep pre-Beta-22 records readable for audit but non-approvable and
  non-dispatchable when they lack a complete trusted projection. Preserve F3,
  lock, intent, recovery, rollback, upstream 8.1.0 admission, protocol, secure
  dependency pins, stable v1.1.2, public tools, and zero fallback unchanged.

## 2.2.0-beta.21 - Exact ha-mcp 8.1.0 compatibility

- Add a separately reviewed exact 8.1.0 release entry for the immutable
  standalone and Home Assistant add-on OCI identities. Exclude the GitHub
  release executables and MCPB because their embedded application identity is
  8.0.0.
- Classify all 78 runtime-advertised tools: 24 automatic reads, two held, 13
  mixed/wrapper-required, 33 persistent writes, four physical/high-risk
  actions, one prohibited, and one unsupported. Keep `ha_search` and
  `ha_get_operation_status` held and uncallable.
- Bind exact 8.1.0 `ha_get_hacs_info` to a strict top-level-success response
  projector. Reclassify the whole expanded `ha_manage_hacs` tool as persistent
  write after upstream adds `remove`; expose no HACS write action.
- Retain exact Dashboard and operational argument contracts. Reuse the existing
  lifecycle structured-content model because captured 8.0.0 and 8.1.0 add-on
  inventory/detail envelopes are byte-identical. Keep Supervisor inventory as
  installed-version authority and reject the tagged tree's stale 8.0.0 value.
- Add digest-pinned 8.1.0 standalone/add-on acceptance, complete delegated-read
  exercise, stable/corrupt/loopback settings-sidecar checks, shutdown worker
  cancellation, and Engineering disconnect/readmission evidence.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting,
  protocol `2025-03-26`, 48 Engineering-local tools, Dashboard and provider
  boundaries, the activated F3 runtime, secure dependency pins, stable v1.1.2,
  and zero fallback. Production canary acceptance remains separate.

## 2.2.0-beta.20 - F3 runtime integration and recovery

- Activate the accepted shared executor and durable cross-process locks for
  eight governed configuration and four governed operational capabilities
  through one closed 12-capability registry.
- Bind one schema-1 task to deterministic durable children with journal
  recovery, exact authority, duplicate prevention, and no legacy/F3 fallback.
- Install one startup and 30-second periodic coordinator with bounded batches/
  backoff, immutable deadlines, fenced transfer, and observation-only
  post-intent recovery.
- Add selective non-expiring holds and private authenticated, CSRF/hash/
  generation-bound reconciliation without a public MCP tool.
- Migrate supported configuration rollback to a separately approved reverse F3
  update plan using the same locks, intent, fixed write, and exact readback.
- Keep dashboard execution deferred and unreachable; add no dashboard planning
  tool, setter, persisted operation, transform, or public tool-count change.
- Preserve 48 Engineering-local tools, exact 7.14.2 78/26/74 and 8.0.0
  78/24/72 accounting, task schema 1, plan contracts 2/3, F2 policy, approval
  authority, secure pins, stable 1.1.2, and zero fallback.

## 2.2.0-beta.19 - F3-C2 operational-adapter conformance

- Add runtime-inert strategies for governed full backup, controlled reload,
  exact installed add-on restart, and Home Assistant restart. The strategies
  consume the canonical shipped Beta 17/18 F3 contracts and merged executor;
  no current route imports or activates them.
- Keep final locked preflight before caller-owned idempotent approval
  consumption, durable F3 intent immediately before one exact provider
  mutation, and post-intent reconstruction readback-only. Operation evidence
  is a bounded projection of the authoritative F3 child record; C2 adds no
  independent recovery ledger.
- Correct independent rereview findings by deterministically binding every
  prepared target, plan, policy, provider, argument, baseline, verification,
  reporting, recovery, deadline, and hold field under
  `f3-operational-prepared-authority-v1`. Recompute that authority at every
  adapter boundary and compare it with the durable child's independently stored
  expected hash during final preflight.
- Require the merged executor's configured evidence duration to match the exact
  prepared operation before claim: 86,400 seconds for backup, 900 for reload,
  and 1,800 for either restart. Require durable child evidence and recovery to
  preserve the exact UTC intent/deadline delta; expiry is inclusive, enters
  manual review, and never releases a hold automatically.
- Bind exact resource, Home Assistant core, and reviewed provider locks,
  including the Beta 18 `reload:<domain>` conflict key. Manual-review holds
  select only the affected resource key and never expire automatically;
  promotion, authenticated reconciliation, and release authority remain F3-D.
- Require truthful effect evidence: readiness alone cannot verify a lost-
  response reload, unchanged running state cannot verify an add-on restart,
  and Home Assistant restart requires persisted outage/reconnect evidence.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, held
  tools `ha_search` and `ha_get_operation_status`, 48 Engineering-local tools,
  task schema 1, current plan contracts, F2 policy, secure dependency pins,
  stable v1.1.2, and zero fallback.

## 2.2.0-beta.18 - F3-C1 configuration-adapter conformance

- Add internal `f3-operation-adapter-v1` configuration strategies for
  automation, script, `input_boolean`, and `input_number` create/update using
  existing normalization, validation, fingerprint, risk, and policy evidence.
- Consume the canonical shipped Beta 17 contract and executor API. Order exact
  durable locks, final stale-state preflight, idempotent approval consumption,
  durable intent, and one fixed gateway write.
- Add exact resource, matching reload-domain, and Home Assistant restart locks;
  authoritative readback verification; bounded observability; and readback-
  only reconstruction.
- Model ordered 1–8 operation plans as deterministic future child descriptors
  without persistence or dispatch. Reject post-intent task cancellation and
  declare all eight forward rollback capabilities unavailable.
- Prove response/process loss, external-writer limitations, and migration
  equivalence through deterministic offline fixtures.
- Keep the package disconnected from application startup, governance routes,
  provider routing, and MCP registration. Single-operation F3-A executor/lock
  conformance is isolated; durable child ownership and activation remain F3-D.
- Preserve task schema 1, current plan vocabulary, F2 policy, approval
  authority, 48 Engineering-local tools, exact 7.14.2 and 8.0.0 accounting,
  secure dependency pins, stable v1.1.2, and zero fallback.

## 2.2.0-beta.17 - Canonical F3 contracts and dashboard deferral

- Ship `ha_mcp_engineering.f3.contracts` as the sole runtime definition of
  `f3-operation-adapter-v1`; retain repository-root `f3_contracts` only as an
  object-identical compatibility and test facade.
- Retain exact raw dashboard planning, bounded JSON Pointer compilation,
  semantic diff, risk evidence, immutable artifacts, stale checks, and exact
  verification while formally deferring executable dashboard mutation.
- Prove through the merged F3-A executor that the complete dashboard lock set
  normalizes and acquires atomically, then reject the unresolved external-writer
  atomicity gate before durable dispatch intent with zero setter invocations
  and zero dashboard-fixture mutations.
- Add built-image import closure and repository-wide F3 import-boundary tests;
  no shipped module depends on repository-root contracts or test support.
- Add no MCP tool, persisted `update_dashboard` operation, provider transport,
  fallback, task/plan schema change, current-route integration, rollback, or
  production execution authority.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, held
  tools `ha_search` and `ha_get_operation_status`, 48 Engineering-local tools,
  `aiohttp==3.14.3`, `cryptography==50.0.0`, stable v1.1.2, and zero fallback.

## 2.2.0-beta.16 - F3-A durable execution infrastructure

- Add the internal `f3-operation-adapter-v1` shared executor with exact
  prepared-operation validation, preflight, durable dispatch intent,
  observation, verification, readback-only recovery, cancellation, and stable
  normalized outcomes.
- Add versioned cross-process resource and provider locks using one stable
  POSIX `flock` transaction inode, atomic state replacement, canonical
  multi-lock ordering, shared/exclusive modes, bounded leases, renewal,
  owner/task binding, fencing generations, and explicit stale recovery.
- Prove through deterministic synthetic adapters and injected process/response
  loss that intent persistence precedes provider invocation, persistence
  failure invokes the provider zero times, and no reconstructed attempt can
  dispatch or mutate more than once.
- Keep the new package disconnected from application startup and every current
  adapter. Add no MCP tool, provider behavior, dashboard write, task/plan
  schema, fallback, or production signing/execution authority.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, held
  tools `ha_search` and `ha_get_operation_status`, 48 Engineering-local tools,
  `aiohttp==3.14.3`, `cryptography==50.0.0`, stable v1.1.2, and zero fallback.

## 2.2.0-beta.15 - Live lifecycle add-on response compatibility

- Reproduce exact `ha-mcp` 8.0.0 add-on detail at a 71,986-byte compact
  source-derived minimum and prove Beta 14's generic 60,000-byte lifecycle text
  decoder rejected it before plan persistence or dispatch.
- Bind exact 8.0.0 and protocol `2025-03-26` to a reviewed direct structured
  lifecycle response model while retaining the bounded text model for exact
  7.14.1 and 7.14.2. Keep the complete MCP exchange and parallel text
  representations independently bounded.
- Project only the exact add-on identity fields required for lifecycle
  planning, require inventory/detail agreement, and reject malformed,
  incomplete, duplicate, ambiguous, unknown-model, or drifting responses with
  bounded value-free diagnostics.
- Extend immutable 8.0.0 add-on runtime acceptance with a secret-free
  source-shaped Supervisor detail fixture at the source-derived minimum
  response cardinality.
  Require four disposable proposals, zero provider dispatch, zero fixture
  mutation, and zero fallback.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, the two
  held reads, Dashboard and backup behavior, model-aware full-catalog
  validation, secure dependency pins, strict audit, stable v1.1.2, and zero
  fallback. Production acceptance remains pending a separate live canary.

## 2.2.0-beta.14 - Exact special-provider runtime admission

- Replace stale raw whole-catalog admission equality in the backup and
  lifecycle providers with one exact-release, model-aware validator over all
  78 reviewed tools. Raw standalone and add-on catalog fingerprints remain
  bounded diagnostic evidence rather than authority.
- Derive one deterministic normalized aggregate identity from sorted,
  reviewed per-tool classification, schema, security annotation, output, and
  release-model runtime results. Exact tool-set, protocol, release, model, and
  accounting mismatches still fail closed.
- Make Dashboard v3 reuse the reviewed four-key policy-state validator and
  bounded projection for `deployment`, `enabled`, `live`, and `rules`, while
  retaining exact coverage of every other security and argument surface.
- Preserve typed dashboard contract categories through nested exception
  groups with deterministic security-first precedence and bounded sanitized
  diagnostics.
- Execute the immutable 8.0.0 add-on runtime by reviewed digest in a distinct
  CI job, reproduce the exact
  `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`
  catalog and
  `3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53`
  normalized aggregate, and exercise automatic reads, dashboard reads, backup
  planning, and lifecycle planning against a synthetic non-secret Home
  Assistant boundary.
- Preserve exact 7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, the two
  held 8.0.0 reads, protocol `2025-03-26`, provider arguments,
  authority-v3/task-schema-1 governance, zero dispatch during planning,
  `aiohttp==3.14.3`, `cryptography==50.0.0`, strict dependency auditing,
  stable-v1.1.2, and zero fallback. Production acceptance remains pending.

## 2.2.0-beta.13 - Dependency security remediation

- Advance the Engineering-only `aiohttp` pin from 3.14.2 to 3.14.3 and
  `cryptography` from 48.0.1 to 50.0.0, the lowest stable combination that
  resolves CVE-2026-69244, CVE-2026-69247, CVE-2026-69248, and
  CVE-2026-69249.
- Retain strict `pip-audit` enforcement without an advisory exception,
  package ignore, prerelease, Git dependency, or fallback. Add an exact
  stable-version guard alongside the synchronized runtime-pin test.
- Preserve all Beta 12 runtime behavior: exact-release and protocol admission,
  48 Engineering-local tools, governance, approval, provider arguments,
  dispatch separation, held-tool policy, and zero fallback.
- Leave stable v1.1.2 unchanged. The `ha-mcp` 8.0.0 dashboard, backup, and
  lifecycle special-provider correction is not included and moves to Beta 14;
  production 8.0.0 acceptance remains blocked until that release.

## 2.2.0-beta.12 - Exact ha-mcp 8.0.0 add-on runtime admission

- Reproduce the Beta 11 production canary's exact
  `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`
  catalog from the immutable amd64 Home Assistant add-on artifact using MCP
  protocol `2025-03-26` and synthetic non-secret settings.
- Correct the 8.0.0 runtime fingerprint model to validate the exact dynamic
  policy metadata shape, JSON types, deployment allowlist and rule-count bound
  before normalizing only its environment-dependent values. All other tool
  descriptor fields remain exact, and malformed policy metadata quarantines.
- Publish separate ordinary and runtime mismatch fingerprints, the explicit
  runtime model, bounded changed-field pointers and summary, reviewed and
  observed catalog identities, operational and strict models, strict observed
  evidence, and catalog-level changed-field counts.
- Preserve 7.14.2 at 78 advertised, 26 admitted reads and 74 total tools. The
  exact 8.0.0 add-on surface has 78 advertised, 24 admitted reads and 72 total
  tools while `ha_search` and `ha_get_operation_status` remain held.
- Preserve exact-release selection, protocol `2025-03-26`, unknown-8.x
  refusal, dashboard, backup, lifecycle, F1/F2, authority-v3, task-schema-1,
  stable-v1.1.2 and zero-fallback boundaries. No production deployment or
  post-correction canary is claimed by this source release.

## 2.2.0-beta.11 - Bounded restart recovery and exact ha-mcp 8.0.0 compatibility

- Bound restart reconciliation by the original persisted post-dispatch
  deadline. Expired or structurally ineligible restart records terminalize
  without Home Assistant, Supervisor, or lifecycle-provider probes and can
  never redispatch.
- Persist restart reconciliation attempt timing and capped backoff
  (`1m`, `2m`, `5m`, then `15m`) across Engineering restarts, enforce
  task-level single flight and bounded batches, and expose bounded active,
  pending, avoided-probe, expiry, and failure evidence in health.
- Preserve exact reviewed `ha-mcp` 7.14.2 admission and add a separate exact
  reviewed 8.0.0 release entry, contract snapshot, immutable artifact
  evidence, and complete 78-tool policy.
- Under 8.0.0, admit exactly 24 delegated reads and hold `ha_search` and
  `ha_get_operation_status` for a later production canary. Unknown later 8.x
  releases remain untrusted and expose no delegated reads.
- Preserve the constrained dashboard list/get wrapper, bounded backup and
  lifecycle providers, the current MCP protocol policy, 48 Engineering-local
  tools, task schema 1, approval authority 3, stable source version 1.1.2, and
  zero fallback.
- Retain the existing operational admission fingerprint model separately from
  the stricter full-contract evidence model. Static and exact-image evidence
  do not claim production architecture, runtime add-on digest, or live
  dashboard, backup, lifecycle, outage, reconnection, or held-tool behavior.
- Defer delta-aware safety-reducing policy beyond Beta 11; this release adds no
  policy classification, provider, Engineering-local tool, write, protocol,
  or fallback authority.

## 2.2.0-beta.10 - Legacy expired-automation compatibility

- Recognize the exact contract-v1 prohibited/expired `update_automation`
  records written by Beta 6's legacy `create_plan` path, separately from the
  existing contract-v2 superseded configuration-plan profile.
- Generate both legacy fixtures through the exact shipped Beta 6 writer and
  expiration lifecycle, with neutral pre-hash inputs and committed source,
  generator, fixture, and event-sequence provenance.
- Admit only the two source-established complete event sequences. The expiry
  event is not added to the generic no-execution allowlist, and every
  additional, missing, reordered, successful, or differently coded event
  remains fail-closed.
- Require exact plan, target, lifecycle, authority-v3, prohibited-policy, hash,
  empty-operation, and no-authority/no-execution evidence. Task-store failures
  continue to propagate.
- Preserve byte-identical records across detail, listing, health, startup,
  Ingress, and handoff reads; no record migration, challenge, task, provider
  call, fallback, or execution path is added.
- Regenerate both Beta 6 compatibility fixture families in CI from the exact
  historical source and compare every fixture and provenance file byte for
  byte.
- Retain Beta 7 provider-response truthfulness and Beta 9 partial-list and
  reconciled-health behavior, authority version 3, task schema 1, the
  25/23/48 plus configured 26/74 tool contract, stable v1.1.2, and zero
  fallback.
- Defer delta-aware safety-reducing policy to Beta 11; Beta 10 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.9 - Real Beta 6 prohibited-plan compatibility

- Correct the historical compatibility gate to recognize exactly the
  contract-v2 prohibited/superseded representation written by shipped Beta 6,
  rather than the contract-v1 default produced by an incomplete manual test
  fixture.
- Generate neutral compatibility fixtures through the exact Beta 6 writer and
  supersession lifecycle, with committed source, generator, and fixture
  provenance; contract-v2 prepared operations remain non-execution evidence.
- Retain strict immutable plan and policy validation and reject any authority,
  task, dispatch, response, apply, verification, rollback, operation-state, or
  event contradiction.
- Return bounded partial plan listings when an individual loaded plan cannot be
  projected, while preserving top-level failure for systemic storage errors.
- Reconcile every stored plan into a health policy-class bucket, including the
  fail-closed `projection_failed` bucket and an explicit bounded warning.
- Preserve byte-identical records on detail, listing, health, startup, Ingress,
  and handoff reads; add no migration, challenge, task, provider call, or
  fallback.
- Retain Beta 7 provider-response truthfulness, current prohibited semantics,
  authority version 3, task schema 1, the 25/23/48 plus configured 26/74 tool
  contract, stable v1.1.2, and zero fallback.
- Defer delta-aware safety-reducing policy to Beta 10; Beta 9 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.8 - Persisted prohibited-plan compatibility

- Historical correction: Beta 8's manually reconstructed fixture omitted
  `contract_version` and defaulted to contract v1, so its intended historical
  branch did not recognize the real contract-v2 records written by Beta 6.

- Recognize the exact validated Beta 6 authority-v3 prohibited-plan shape in
  which same-target supersession changed only legacy lifecycle fields to
  `superseded` and `invalidated`.
- Project those records as terminal and non-actionable across detail, listing,
  health, Ingress, startup rehydration, and handoff paths without rewriting
  their persisted fields, events, hashes, or timestamps.
- Count validated historical records as prohibited while excluding them from
  awaiting, required, pending-challenge, and authorization-required work.
- Fail closed when a purported historical record has nonempty
  acknowledgements, authority, a task, provider or apply evidence, successful
  work, or an invalid policy snapshot.
- Retain Beta 7 transport-response truthfulness, current prohibited semantics,
  authority version 3, task schema 1, the 25/23/48 plus configured 26/74 tool
  contract, stable v1.1.2, and zero fallback.
- Defer delta-aware safety-reducing policy to Beta 9; Beta 8 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.7 - Acceptance evidence and projection corrections

- Record configuration-provider HTTP responses and WebSocket frames truthfully
  before readback, including empty successes and received error responses,
  while retaining false for a timeout or connection failure with no known
  response.
- Keep provider response receipt, write outcome, readback, and semantic
  verification as separate durable facts; a later mismatch cannot erase a
  received response and readback cannot manufacture one.
- Project validated F2 prohibited plans as terminal and non-actionable in plan
  detail, inventory, handoff evidence, Ingress, and health instead of claiming
  that they await or require an approval that cannot exist.
- Preserve authority-v3 persisted enum fields and historical schema-v1 task
  evidence without migration or startup backfill.
- Preserve F2 policy mapping, same-administrator approval, one-task ownership,
  task schema 1, 48 local tools, 26 configured delegated reads, stable v1.1.2,
  and zero fallback.

## 2.2.0-beta.6 - Policy, risk, and elevated approval semantics

- Add deterministic, server-derived `risk_delta`, independent
  `physical_consequence`, and `standard_admin`, `elevated_admin`, or
  `prohibited` policy decisions to every new immutable governed plan.
- Bind the complete F2 policy snapshot and policy-decision hash into the plan
  hash; validation, approval, apply, and startup recovery fail closed on
  missing or mismatched authority.
- Advance new approvals to authority version 3. Standard plans require one
  Home Assistant administrator approval. Elevated plans require a separate
  plan approval followed by an elevated-risk acknowledgement from the same
  authenticated administrator; this is not two-person control.
- Recheck policy and the complete approval bundle immediately before dispatch,
  create or reserve the durable F1 task before consumption, and retain bounded
  approval evidence without changing task schema version 1.
- Keep historical authority-v2 records readable but non-actionable, prohibit
  unknown, unsupported, destructive, critical, safety-critical, or evasive
  plans, and preserve one-task ownership, no blind redispatch, provider-response
  truthfulness, exact provider routing, and zero fallback.
- Prohibit the reviewed `lock.unlock` and
  `alarm_control_panel.alarm_disarm` service names independently of entity,
  device, area, data-based, broad, templated, unresolved, mixed, or omitted
  target representation while retaining existing mappings for other high-risk
  services.
- Add disposable pinned-Core contracts for standard, elevated, and prohibited
  configuration policies. Planning never triggers the configured future
  physical action, and duplicate apply performs no second write.
- Verify accepted automation readback through a narrow schema-aware
  `service`/`action` alias normalization while retaining raw fingerprints,
  meaningful-field mismatches, unchanged plan/policy hashes, and truthful
  provider-response evidence.
- Require every verification action step to match exactly one reviewed family;
  identical unknown mappings, ambiguous families, malformed control flow, and
  unreviewed extra fields fail closed with bounded evidence.
- Preserve 48 local Engineering tools, the configured 26 exact-admitted
  delegated reads, stable v1.1.2, and the existing upstream fingerprints.

## 2.2.0-beta.5 - Update and recovery preflight foundation

- Add immutable, deterministic models for explicit update targets, installed
  and candidate versions, explicit caller-supplied version direction,
  compatibility, current repairs and errors, backup, storage, power, recovery,
  disruption, and post-update verification evidence.
- Preserve separate ordered blockers, warnings, and unknowns with exact
  `ready_for_governed_planning`, `blocked`, `manual_review_required`, and
  `unsupported` advisory verdicts.
- Fail closed when authoritative candidate or compatibility evidence, required
  backup evidence, storage, power, recovery, disruption, or verification facts
  are missing or conflict with the selected target policy.
- Permit readiness only for confirmed upgrades; require manual review for
  downgrades, unknown direction, direction contradictions, and HIGH unresolved
  repairs or errors; block same-version candidates and CRITICAL issues.
- Keep E1 pure and runtime-inert: no evidence collection, startup loading,
  network or Home Assistant access, tool, health, provider, plan, approval,
  task, update, backup, restart, restore, downgrade, safe-mode action, write, or
  fallback is added.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.4 - Knowledge provenance foundation

- Add strict, deterministic local knowledge-manifest parsing with closed fields,
  bounded values, exact classifications, duplicate rejection, and explicit
  malformed-manifest failures.
- Add provenance-only trust classes, independent Engineering, Home Assistant,
  and integration version scopes, explicit expiry handling, and
  `unknown` relevance that never implies compatibility or recommendation.
- Restrict content to canonical child paths and bounded UTF-8 text formats;
  reject traversal, absolute paths, escaping symlinks, oversized content, and
  SHA-256 mismatches.
- Preserve exact source, document, version, path, digest, and bounded citation
  provenance while treating all embedded text as instruction-inert data.
- Keep K1 runtime-inert: no startup loading, remote retrieval, MCP tools,
  recommendation or plan authority, Home Assistant access, provider or signed
  registry integration, write path, or fallback is added.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.3 - Signed compatibility registry foundation

- Add strict, closed signed-registry envelope, reviewed-release, and
  revocation models that preserve the compiled registry's exact contract
  evidence.
- Add deterministic canonical serialization and content digests, Ed25519
  verification through configured public trust anchors, and typed fail-closed
  validation results.
- Reject unknown keys, invalid signatures, payload mutation, rollback, replay
  conflicts, broken digest chains, expiration, future generation timestamps,
  duplicate identities, and entry/revocation contradictions.
- Keep the compiled reviewed-release registry authoritative. This release adds
  no runtime loading, retrieval, admission, tool, health, provider, execution,
  write, fallback, or production signing-key behavior.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.2 - F1 recovery evidence and counters

- Preserve a lost original provider response as
  `response_received=false` when startup/readback verification later proves
  successful completion.
- Emit `provider_response_recorded` only for an actual provider return; recovery
  and process-identity verification remain separate durable evidence.
- Include terminal-task duplicate applies exactly once in operation-specific
  apply and no-blind-redispatch counters without increasing provider dispatch.
- Preserve task schema v1, immutable plans, external approval, 48 Engineering
  tools, 26 reviewed delegated reads, and zero fallback.

## 2.2.0-beta.1 - Durable execution tasks

- Separate the immutable hash-bound change plan and existing external approval
  from one mutable, versioned execution task for each newly executed plan.
- Persist a materialized task together with ordered append-only lifecycle
  events in the isolated `execution-tasks-v1` namespace using atomic
  flush/fsync/replace writes and fail-closed consistency validation.
- Make `apply_change_plan` create or reuse one authoritative task before
  preflight, project existing dispatch and operation-specific verification
  evidence, and additively return the task identifier and state.
- Preserve the exact-once boundary across duplicate callers, client timeout,
  provider response loss, and Engineering process restart. Startup task
  rehydration performs no provider action; existing operational reconciliation
  remains readback-only.
- Add bounded `get_execution_task`, `list_execution_tasks`, and
  pre-dispatch-only `cancel_execution_task`. Cancellation after dispatch is
  refused and is never represented as rollback or compensation.
- Set the immutable maximum recovery deadline to 24 hours after first dispatch.
  Unresolved tasks then enter `manual_review_required` without redispatch.
- Keep historical taskless plans readable as legacy records without fabricating
  execution evidence or changing any plan hash.
- Increase the complete catalog to 48 Engineering tools plus 26 reviewed
  delegated reads, 74 total. Upstream admission, provider routes, public
  schemas outside the three additive tools, stable v1, and zero fallback remain
  unchanged.

## 2.1.1-beta.3 - Home Assistant restart evidence reconciliation

- Accumulate authoritative post-dispatch Home Assistant Core outage and
  reconnection evidence across bounded verification attempts, repeated apply
  calls, process restarts, and startup or periodic reconciliation.
- Retain the approximately 15-second initial probe budget while binding new
  outage observations to an independent immutable 180-second interval derived
  from the original persisted dispatch timestamp. Recovery may finish later
  after a qualified outage, but a future unrelated outage cannot verify an old
  plan.
- Validate persisted outage evidence as one complete record; incomplete or
  malformed raw outage flags fail closed and cannot skip the Core probe or
  satisfy terminal verification.
- Require confirmed dispatch, a qualified Core-unavailable observation, and a
  later successful identity read with explicit `reconnected_at` evidence
  before a Home Assistant restart can reach
  `applied_verified`; current availability or provider acknowledgement alone
  remains insufficient.
- Preserve the exact-once dispatch boundary: reconciliation is readback-only,
  old plans without authoritative outage evidence remain pending, and no
  optional uptime entity is required.
- Retain 45 Engineering tools plus 26 delegated reads, 71 total, with no
  public schema, reviewed fingerprint, provider policy, stable-v1, or fallback
  change.

## 2.1.1-beta.2 - Beta 2 self-restart identity correction

- Resolve the running Engineering add-on through Supervisor's exact
  caller-relative self metadata rather than comparing an installed,
  repository-prefixed slug with the source add-on slug.
- Bind new restart plans to the requested and resolved add-on identity.
  Exact self-targets retain `process_identity`; reviewed upstream restarts
  retain `upstream_readmission`; ordinary add-ons retain the weaker
  `provider_acknowledgement` proof grade.
- Normalize a missing installed add-on to non-retryable `addon_not_found`.
  This expected domain outcome does not degrade an otherwise exact provider
  contract or increment operational provider failures.
- Retain the complete Beta 2 surface of 45 Engineering tools plus 26
  delegated reads, 71 total, with no schema, reviewed fingerprint, provider
  policy, write reachability, or fallback change.

## 2.1.0-beta.2 - 2.1A governed operational lifecycle

- Add proposal-only `create_reload_plan`, `create_addon_restart_plan`, and
  `create_home_assistant_restart_plan` tools while retaining the shared
  contract-v3 external approval, immutable hash, exact-once apply, persistence,
  audit, and readback lifecycle.
- Constrain exact reviewed `ha_reload_core`, `ha_manage_addon`, and `ha_restart`
  contracts to four reload domains, one exact installed add-on restart, and
  one confirmed Home Assistant restart. Generic mixed-tool exposure, arbitrary
  service data, provider arguments, and fallback remain prohibited.
- Reuse strict full configuration validation at reload and Home Assistant
  restart planning and immediately before dispatch; record planning and
  apply-time evidence without claiming a whole-configuration fingerprint.
- Persist dispatch intent before every action and add bounded background and
  startup reconciliation. Lost responses, server restarts, and expected
  connection loss resume verification only and never blindly redispatch.
- Verify reload state readability, exact add-on identity and restart evidence,
  Engineering self-restart process identity, upstream `ha-mcp` readmission,
  Home Assistant identity, tool restoration, governance and audit persistence,
  dependency recovery, and zero fallback.
- Normalize the reviewed valid single-entity-ID `ha_get_entity`
  missing-registry outcome to bounded, non-retryable `entity_not_found`;
  resolver, bulk, malformed, and unknown combinations remain fail closed.
- Classify proposal audit records as `access=proposal` with
  `operation_class=proposal`; approval and apply remain writes.
- Increase the complete catalog to 45 Engineering tools plus 26 delegated
  reads, for 71 total. The upstream catalog and all reviewed fingerprints,
  classifications, automatic-read decisions, and dashboard attestations remain
  unchanged.

## 2.1.0-beta.1 - 2.1A operational administration Dev1

- Add the proposal-only `create_backup_plan` tool and contract-v3 operational
  plans while reusing exact external approval, apply, storage, and audit
  authority.
- Add an Engineering-owned constrained provider for only
  `ha_manage_backup(scope="snapshot", action="create", name=...)` under the
  exact reviewed 7.14.1 and 7.14.2 contracts. Generic mixed-tool exposure,
  arbitrary arguments, restore, deletion, and fallback remain prohibited.
- Persist dispatch intent and consume approval before the one permitted
  provider invocation. Ambiguous transport outcomes enter verification-only
  recovery and never trigger blind creation retries.
- Verify completion independently through bounded `backup/info` evidence,
  distinguishing operation completion and inventory readback from unsupported
  archive-content integrity validation.
- Formalize strict reusable configuration-check evidence for later reload and
  restart milestones without changing the read-only `check_config` surface.
- Add operational audit and health evidence with explicit persistent,
  process-cumulative, and active-state counter sources.
- Increase the complete catalog truthfully to 42 Engineering tools plus 26
  delegated reads, for 68 total. Existing upstream fingerprints and policies
  remain unchanged.

## 2.0.1 - GA promotion

- Promote the accepted `2.0.1-rc1-dev2` runtime without changing public
  schemas, reviewed fingerprints, compatibility policy, admission, routing,
  tool registration, governance, audit, dashboard constraints, or fallback
  enforcement.
- Retain the MCP SDK upgrade from 1.9.0 to 1.28.1, strict dependency auditing,
  fail-closed private SDK registry integration, transactional dynamic-registry
  replacement and restoration, server capability behavior, and the prohibition
  on generic upstream write delegation.
- Retain source-controlled compatibility entries for exact `ha-mcp` 7.14.1 and
  7.14.2. Each entry has 78 release-specific tool-contract records, for 156
  total, and admits only its 26 explicitly reviewed automatic reads. Unknown
  versions fail closed; new or changed tools are not automatically admitted;
  per-tool quarantine remains available; and direct Home Assistant fallback
  remains prohibited.
- Retain SHA-256-bound canonical captures, complete per-tool fingerprint
  regeneration, deterministic registry drift validation, separately
  human-owned classifications and automatic-read decisions, dashboard
  decisions bound to exact built-in attestations, and a registry-derived
  exact-image CI matrix that verifies image-index, platform, OCI version, and
  OCI revision evidence before startup.
- Keep runtime-observed MCP identity separate from reviewed image and source
  evidence. The runtime does not claim to observe the live upstream container
  digest; deployment artifact verification remains an operator responsibility.
- Record operator-provided acceptance with reviewed 7.14.1 and 7.14.2,
  including `7.14.1 -> 7.14.2 -> 7.14.1 -> 7.14.2` transition testing without
  rebuilding Engineering.
- Preserve 41 Engineering tools plus 26 delegated reads for 67 total under
  complete admission, the catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
  zero fallback, and stable v1.1.2 historical-source isolation.

## 2.0.1-rc1-dev2 - reviewed upstream release updateability

- Replace the single reviewed generic-read release assumption with a compiled,
  source-controlled registry containing exact `ha-mcp` 7.14.1 and 7.14.2
  identity, provenance, image, protocol, catalog, per-tool contract, policy,
  dashboard, and review evidence.
- Admit only an exact reviewed version and exact per-tool contract; quarantine
  changed reads independently, keep new tools unreviewed and unexposed, report
  removed tools, and fail closed for unknown versions.
- Reconcile version changes atomically so one Engineering image can move
  between the two reviewed upstream releases without stale routes or a rebuild.
- Add deterministic capture, normalization, fingerprint, diff, validation,
  report, and candidate-generation tooling. Generated candidates remain
  unapproved until a reviewed source change authorizes them.
- Extend pinned-image CI across exact 7.14.1 and 7.14.2 image digests while
  retaining the original gate.
- Record that both reviewed upstream versions return the ambiguous
  `SERVICE_CALL_FAILED` envelope for a missing `ha_get_entity` registry entry.
  Engineering preserves the bounded generic operational failure rather than
  classifying untrusted English prose as a not-found outcome.
- Preserve 41 Engineering tools, 26 delegated automatic reads when either
  exact catalog is fully admitted, 67 total tools, generic-write prohibition,
  public schemas, governance, audit, redaction, and zero fallback.

## 2.0.1-rc1-dev1 - MCP SDK and dependency hardening

- Centralize the pinned MCP SDK's private FastMCP tool-registry access behind
  one fail-closed compatibility adapter with startup shape validation,
  read-only mapping snapshots, exact removal, cached version validation, and
  transactional replacement with restoration on post-check failure.
- Upgrade `mcp` from 1.9.0 to 1.28.1, the newest compatible stable 1.x release,
  while preserving Streamable HTTP, the exact reviewed `ha-mcp` 7.14.1
  admission contract, and the 41 plus 26 tool composition.
- Upgrade the directly used Engineering web and security dependencies to
  `aiohttp` 3.14.2, `uvicorn` 0.51.0, `starlette` 1.3.1, and `cryptography`
  48.0.1; retain the already-current PyYAML and jsonschema pins.
- Add a pinned `pip-audit` pull-request gate to the reusable validation
  workflow required by release promotion.
- Retain the one-second Engineering transport floor as explicit policy while
  removing the obsolete MCP 1.9.0 fractional-timeout rationale.
- Preserve exact `ha-mcp` 7.14.1 admission by explicitly requesting reviewed
  upstream protocol `2025-03-26` instead of broadening to the SDK's newer
  default.
- Implement MCP 1.28's delegated result-conversion callback while preserving
  the established bounded JSON text response and reviewed output contract.
- Preserve the normal MCP 1.28 `ClientSession` initialization state, including
  the public server-capabilities accessor, while requesting the exact reviewed
  protocol.
- Record CVE-2025-66416 as a mitigated, deferred production configuration risk
  tracked by issue #62; do not infer loopback Host protection for the
  production `0.0.0.0` bind.
- Retire stable v1.1.2 as an operational rollback target and scope dependency
  audit and behavioral tests to the Engineering server. Historical v1 source
  and packaging remain unchanged.
- Preserve public schemas, fingerprints, provider policy, audit semantics,
  governance, dashboard behavior, and zero fallback.

## 2.0.0 - GA promotion

- Promote the accepted RC2dev16 runtime without changing functional behavior,
  public schemas, reviewed fingerprints, provider policy, admission, routing,
  tool registration, governance, dashboard reads, or fallback enforcement.
- Retain the existing `hass_mcp_engineering_beta` slug, port `8100`, Ingress,
  image repository, options, `/data` persistence, connector identity, and
  runtime/display name to avoid migration or contract drift.
- Preserve 41 Engineering tools plus 26 exact reviewed `ha-mcp` 7.14.1 reads
  for 67 total tools when fully admitted.
- Add exact GA release, in-place upgrade, rollback, provenance, and acceptance
  guidance. Stable v1.1.2 was retained unchanged at release time but is now
  operationally retired and is not a supported rollback installation.

## 2.0.0-rc2-dev16 - delegated error normalization

- Classify only explicitly allowlisted structured ha-mcp 7.14.1 error codes,
  preserving caller validation, optional-capability, authentication,
  connection, timeout, and internal-provider distinctions.
- Keep arbitrary, malformed, oversized, and unknown upstream error content on
  the bounded generic provider-error path without reflecting upstream prose,
  metadata, or retryability.
- Record caller validation and capability-unavailable answers as completed
  provider dispatches rather than operational provider failures, while
  retaining truthful failure audit outcomes.
- Document how clients should choose filtered entity discovery, exact
  dependency analysis, known-configuration reads, and broad configuration-text
  search with truthful partial coverage.
- Preserve the 41 Engineering plus 26 delegated catalog, exact upstream
  admission, public schemas, zero fallback, governed writes, dashboard
  behavior, and stable v1.1.2.
- Defer the separately planned generic signed-registry milestone; Dev16 adds no
  registry lifecycle, search implementation, deployment, or publication path.

## 2.0.0-rc2-dev15 - contract-level upstream compatibility

- Require the exact compiled generic release/profile, currently ha-mcp 7.14.1,
  before admitting each reviewed automatic read by its exact input, output,
  safety, full-runtime-description fingerprint, behavior, identity, and
  protocol contract.
- Separate exact upstream wire-annotation presence/value fingerprints from the
  stricter Engineering-owned annotations published after admission; never
  normalize omitted optional hints into invented defaults.
- Bind each reviewed read to the pinned generic object output-schema
  fingerprint at discovery and immediately before dispatch.
- Keep unchanged reads available, quarantine only changed reads, remove missing
  reads, and block new, mixed, write, action, and unsupported tools without
  collapsing the complete delegated catalog.
- Evaluate the argument-constrained dashboard family independently and require
  an exact built-in or verified signed release attestation before applying its
  compiled contract checks.
- Preserve last-known-good routes across transient discovery failures, bind
  calls to the admitted catalog generation, and separate fast startup recovery
  from slow compatibility reprobes.
- Keep process liveness available but return bounded readiness and HTTP 503 for
  authenticated MCP traffic until configured upstream reconciliation publishes
  its first stable or terminal catalog result.
- Let equivalent reviewed-catalog observations publish under busy exact calls,
  while coalescing repeated stale mismatches to one immediate retry and then
  the bounded slow cadence.
- Revalidate exact release/profile authority and the selected target's contract
  through same-session `tools/list` before `tools/call`; fail closed on
  unreviewed version movement while retiring a missing, duplicate, or changed
  target independently.
- Use immutable route snapshots and short leases so delegated network I/O is
  concurrent; a retired route cannot dispatch or be revived by a finishing
  in-flight call.
- Keep unrelated malformed or duplicate unreviewed descriptors unavailable as
  bounded reconciliation anomalies without blocking an exact selected target.
- Preserve stable v1.1.2, public schemas, governed configuration writes,
  `ha_search` completeness truth, and the no-fallback boundary.

## 2.0.0-rc2-dev14 - practical governed configuration plans

- Add one bounded `create_configuration_plan` entry point for ordered
  automation, script, `input_boolean`, and `input_number` create/update
  operations under one exact Home Assistant administrator approval.
- Apply approved operations in dependency order with all-target and immediate
  per-operation stale checks, typed write adapters, exact readback, one final
  configuration check, and truthful partial or uncertain outcomes.
- Keep raw writers, deletion, reload, restart, deployment, fallback, automatic
  rollback, and every unsupported resource or action unreachable.
- Preserve the existing six governance tool schemas, all 25 canonical
  capabilities, the 26 exact reviewed delegated reads, and stable v1.1.2.

## 2.0.0-rc2-dev13 - reboot reconciliation and completeness truth

- Start the native Engineering listeners without blocking on ha-mcp readiness,
  then supervise exact fail-closed read-gateway admission with capped retry
  delays until the reviewed catalog becomes available.
- Preserve all 40 statically registered tools (25 canonical plus 15
  Engineering-native) during retry and automatically make all 26 exact delegated
  reads available to subsequent `tools/list` calls without a manual Engineering
  restart.
- Preserve `ha_search`'s reviewed top-level `partial` signal in Engineering
  response metadata, provider metrics, and request telemetry;
  missing or malformed completeness fails closed as partial.
- Preserve all public schemas, provider policy/trust data, no-fallback and
  no-write boundaries, stable v1.1.2, governance, and deployment behavior.

## 2.0.0-rc2-dev10 - selected-attestation observability truth

- Derive retained raw-schema, reviewed-security, fixture-runtime, and
  published-runtime expectations from the exact built-in or signed attestation
  selected for the observed upstream release.
- Keep those informational fingerprints distinct from the normalized
  input/security/output/runtime fingerprints that remain the admission gate.
- Report `ha_mcp_dashboard_read_v2` as the active dashboard trust profile and
  describe exact-release attestation instead of a globally pinned version.
- Preserve routing, the one-tool allowlist, exact non-screenshot arguments,
  hashes, no-fallback enforcement, public schemas, and governance storage.

## 2.0.0-rc2-dev9 - signed upstream contract admission

- Replace the one-version dashboard profile with the compiled
  `ha_mcp_dashboard_read_v2` family and exact built-in attestations for
  ha-mcp 7.13.0, 7.14.0, and 7.14.1.
- Add an optional Ed25519-verified, fixed-location release-attestation registry
  with atomic last-known-good caching, expiry, revocation, and sequence rollback
  protection. Registry data cannot define tools, argument builders, or families.
- Preserve the one-tool dashboard allowlist and exact non-screenshot inventory
  and configuration call shapes; all upstream write and generic delegation
  paths remain unavailable.

## 2.0.0-rc2-dev8 - pre-validation enforcement and audit truth

- Intercept `call_service`, `reload_domain`, `upsert_automation`, and
  `delete_automation` at the authenticated Streamable HTTP boundary before
  FastMCP/Pydantic argument coercion so their fixed fail-closed policy result is
  independent of caller argument shape.
- Return the existing canonical `provider_unavailable` or
  `provider_prohibited` Engineering envelope without provider, Home Assistant,
  upstream, governance, or fallback dispatch.
- Inspect bounded MCP responses for structured Engineering failures,
  FastMCP validation failures, and JSON-RPC errors so HTTP 200 no longer causes
  failed tool calls to be audited as successful.
- Preserve all 40 public tool schemas, the 25 canonical classifications, zero
  planned tools, RC2dev7 authentication/audit filtering, governance storage,
  dependency-index behavior, and the pinned ha-mcp 7.13.0 dashboard profile.

## 2.0.0-rc2-dev7 - exact audit-event filtering

- Parse bounded audit JSONL records and compare only the exact, case-sensitive
  top-level `event` field instead of searching serialized lines.
- Keep routed `get_audit_log` calls self-audited as `tool_call` without allowing
  their nested filter argument to contaminate `auth_failure`,
  `auth_failure_throttled`, or `rate_limited` evidence.
- Skip malformed, non-object, blank, and oversized historical records while
  preserving the existing bounded JSONL response, ordering, redaction, public
  schemas, authentication controls, provider routing, governance storage, and
  stable v1.1.2 behavior.

## 2.0.0-rc2-dev6 - throttled-authentication audit correction

- Distinguish ordinary authentication rejection (`auth_failure`) from
  authentication-failure limiter exhaustion (`auth_failure_throttled`) while
  preserving the existing 404/429 responses, error codes, limiter behavior,
  secret redaction, and pre-dispatch enforcement.
- Keep authenticated general rate limiting separately audited as
  `rate_limited`; no provider, governance, dependency-index, dashboard, or
  stable-v1 behavior changes in this release.

## 2.0.0-rc2-dev5 - live-acceptance corrections

- Replace the five-minute hard dependency-index expiry with configurable
  10-minute soft and 60-minute hard bounds, single-flight background refresh,
  explicit stale-evidence metadata, and nonblocking delayed startup prewarm.
- Normalize the reviewed ha-mcp 7.13 missing-dashboard envelope as the
  non-retryable `dashboard_not_found` domain outcome without degrading provider
  reachability or contract health.
- Return deduplicated root-cause groups in reliability summary mode and keep
  intentional unavailable-state triggers as informational notes.
- Treat Home Assistant webhook IDs as sensitive identifiers in relayed logs.
- Repair deterministic annotated-tag identity and partial-promotion reporting;
  this change does not backfill the missing RC2dev4 tag.

## 2.0.0-rc2-dev4 - release hardening

- Removed every reachable legacy direct-write implementation and made the
  capability catalog report governed redirects, prohibitions, and unavailable
  providers truthfully.
- Distinguished plans that merely require approval from plans with a pending
  external Ingress challenge; pre-approval principal separation is now
  explicitly not evaluated.
- Added single-flight dependency-index builds, cold-build profiling, explicit
  build-state/expiry semantics, a bounded eight-read pool, optional one-shot
  connectivity-gated prewarming, and expected-outcome metrics.
- Hardened relayed System Log and structured-log redaction, dashboard not-found
  handling, upstream health freshness, summary response size, and reliability
  root-cause deduplication.
- Added a fixture-only transport bake harness and an RC2dev4 acceptance guide.

## 2.0.0-rc2-dev3

- Reproduce the exact public `ha-mcp` 7.13.0 add-on runtime descriptor and
  record its immutable artifact and dependency evidence.
- Separate the complete input-schema and reviewed security-contract blocking
  fingerprints from the complete runtime-descriptor observability fingerprint.
- Permit only the proven presentation-only `_meta.ha_mcp` exposure/pinning
  delta while preserving exact identity, version, protocol, input schema,
  safety annotations, output contract, argument builders, and tool allowlist.
- Report expected/observed component fingerprints and bounded descriptive
  versus semantic runtime drift without exposing schemas or endpoint data.
- Publish `readOnlyHint=true` on both Engineering dashboard tools and prove the
  raw server-side MCP `tools/list` contains all 40 tools even while the provider
  is unavailable; clients may need connector refresh after schema caching.
- Stage dev3 in `.release/next-version` while Home Assistant metadata remains
  at published dev2 until the image is built, anonymously verified, and
  atomically promoted.

## 2.0.0-rc2-dev2

- Add the reviewed `ha_mcp_7_13_dashboard_read_v1` trust profile for upstream
  `ha-mcp` 7.13.0 at commit
  `f4eb53621ccb814cb7123d2811e06eda3577129c`.
- Distinguish contract-level read-only trust from the explicit
  `reviewed_argument_constrained` exception for the mixed-operation dashboard
  tool.
- Pin initialize identity, version, protocol, exact annotations, input schema,
  and the complete canonical tool contract.
- Construct only non-screenshot inventory and exact-configuration argument
  shapes; reject rendering, preferences, unknown arguments, and other tools
  before transport dispatch.
- Preserve the verified upstream 16-character optimistic-lock hash and
  independent 64-character Engineering evidence hash.
- Stage dev2 in `.release/next-version` while leaving Home Assistant metadata at
  dev1 until the image is built, anonymously verified, and atomically promoted.
- Replace manual tag-first publication with one main-only, repository-locked
  promotion transaction that publishes and verifies before pushing the release
  commit and annotated tag.
- Preserve 40 registered/25 canonical/zero planned capabilities, public input
  schemas, governance, external approval, audit, direct-HA policies,
  unavailable Standard HA MCP delegation, and production v1.1.2.

## 2.0.0-rc2-dev1

- Add the RC3A read-only `upstream_dashboard` provider using the maintained
  `mcp==1.9.0` streamable-HTTP client already present in the image.
- Add bounded `list_dashboards` and `get_dashboard_config` tools backed only by
  upstream `ha_config_get_dashboard`.
- Verify the upstream 16-character dashboard optimistic-lock hash against an
  exact local recomputation and expose a distinct 64-character Engineering
  evidence hash calculated from complete raw JSON before sanitization.
- Classify rejected endpoint/secret paths, connection failures, and genuine
  timeouts deterministically without exposing endpoint details.
- Use AwesomeVersion 25.8.0 to select the installable RC3A development version
  `2.0.0-rc2-dev1`; the rejected `2.0.0-rc.2.rc3a.1` form is incomparable.
- Validate the upstream read annotation and minimum input schema, record
  sanitized identity and schema/catalog fingerprints, and fail closed on
  missing or incompatible capabilities.
- Treat the complete upstream URL as a password-style secret and exclude it
  from responses, health, logs, audit records, errors, and tracebacks.
- Preserve all 25 canonical tool contracts, Standard HA MCP unavailability,
  direct-HA policies, governance, external approval authority version 2, and
  production v1.1.2.
- Add no dashboard write, delete, backup, service, physical-action, generic
  upstream forwarding, or live deployment path.

## 2.0.0-rc.2

- Supersede immutable RC1 because Home Assistant could continue resolving the
  original `2.0.0-rc.1` image after the reviewed source correction retained the
  same add-on version and image tag.
- Publish the already reviewed `search_entities` correction under a distinct
  installable version: explicit `transitional_direct` routing to
  `direct_ha_api`, policy `bounded_entity_state_search`, one bounded read-only
  `/states` inventory, deterministic slim results, and no fallback.
- Preserve the Beta 26 public schemas and enums, 38 registered tools, 25
  canonical tools, zero planned capabilities, schema version 1, external
  approval authority version 2, and production v1.1.2 unchanged.
- Retain deterministic multi-architecture GHCR provenance and publication for
  amd64, arm64, and arm/v7 with immutable version and source-commit tags.

## 2.0.0-rc.1

- Freeze the accepted Beta 26 public catalog, schemas, governance semantics, and
  external authority version 2 for release candidate validation.
- Add deterministic image-build provenance for the existing `build_sha` and
  `build_time` fields, with safe `unknown` fallbacks for local development.
- Add clean-install and persisted Beta 26 upgrade compatibility coverage plus
  the RC1 deployment, acceptance, soak, and rollback procedures.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, the Beta/RC ports and slug, and production v1.1.2 unchanged.

## 2.0.0-beta.26

- Make plan expiration a single terminal transition; repeated plan, list,
  health, Ingress, and handoff reads no longer rewrite an expired record or
  duplicate lifecycle events, audit entries, logs, or `updated_at` changes.
- Resolve external-challenge expiry through one governance lifecycle path so
  public reads, health, Ingress, approval requests, apply, and rollback agree on
  the effective current state.
- Exclude expired challenges from actionable plan views, the Ingress inbox, and
  `pending_challenge_count`; eligible plans may request one fresh challenge
  bounded by the plan expiry while the old challenge remains unusable.
- Preserve fail-closed apply/rollback enforcement, external authority version 2,
  distinct apply/rollback approvals, and all Beta 25 principal-separation rules.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, and every Beta 25 public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.25

- Make `approve_change_plan` request external review without granting approval;
  the public input schema is unchanged.
- Add an administrator-only Home Assistant Ingress approval panel on unmapped
  internal port 8110 with escaped HTML, POST-only decisions, one-time CSRF and
  strict bounds/security headers.
- Persist 15-minute authority-version-2 challenges bound to the exact plan hash,
  kind, target, operation and risk; preserve idempotency, expiry, invalidation,
  replay resistance and restart recovery.
- Require separate external apply and rollback approvals and make rejection a
  terminal historical state.
- Fail active Beta 24 caller approvals closed; never silently migrate authority.
- Update handoff, audit and health contracts for external-pending, approved,
  consumed, rejected, expired and invalidated states.
- Add disposable, digest-pinned Home Assistant Core 2026.7.2 REST/WebSocket,
  id-less automation, configuration-validation and trace contract tests.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1 and every existing public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.24

- Treat top-level automation `id` as explicitly verified identity metadata,
  outside behavioral normalization, fingerprints, and plan hashes.
- Fail closed for legacy `upsert_automation` before provider dispatch and require
  the governed plan/approval/apply workflow.
- Require an explicit matching policy for every direct Home Assistant exception.
- Make forwarded client identity untrusted by default and add bounded trusted
  proxy CIDR configuration for validated IPv4/IPv6 forwarding.
- Replace whole-store rate-bucket resets with atomic bounded LRU eviction.
- Exclude known-unavailable pre-dispatch providers from provider request/failure
  counters and clamp audit-log reads to 1–500 records.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  and all public schemas. Production v1.1.2 remains unchanged.

## 2.0.0-beta.23

- Require an explicit provider-dispatch assertion before global provider request,
  success, partial, or failure counters change.
- Keep request/cursor validation, authentication, rate limiting, policy rejection,
  and snapshot-only continuation out of provider failure accounting.
- Preserve one attributable failure for real Engineering/direct provider errors
  and timeouts; keep successful partial coverage non-failing.
- Retain all Beta 22 handoff corrections, 38 registered/25 canonical tools, an
  empty planned list, all public schemas, and production v1.1.2 unchanged.

## 2.0.0-beta.22

- Normalize shared handoff evidence to one effective coverage record per logical
  source, removing synthetic dependency failures while preserving real failures.
- Treat expired, superseded, rolled-back, and validation-only terminal plans as
  retained history rather than active pending work or authorization requirements.
- Resolve automation internal IDs through one bounded state inventory and freeze
  successful automation entity IDs into structured, Markdown, and cursor scope.
- Define `risk_count` as the number of handoff items in the `risks` section and
  keep open/authorization counts limited to current actionable work.
- Retain 38 registered/25 canonical tools, all public schemas, an empty planned
  list, the read-only boundary, and production v1.1.2 unchanged.

## 2.0.0-beta.21

- Add read-only Engineering-native `handoff_generation`, increasing the beta
  catalog to 38 registered/25 canonical tools with no existing schema changes.
- Add system-status, focused-review, incident, and change handoffs with explicit
  fact/inference/recommendation/limitation and authorization contracts.
- Require apply plus verification evidence before work is called completed;
  preserve pending, failed, and rolled-back lifecycle truth.
- Add deterministic structured/Markdown output, five-minute signed sanitized
  pagination, bounded audit, dedicated health counters, and Beta 20 coverage
  semantics without any write capability.
- Remove `handoff_generation` from planned capabilities; no planned feature
  capability remains. Production v1.1.2 is unchanged.

## 2.0.0-beta.20

- Correct incident-correlation coverage so a successfully built, usable but
  incomplete dependency index has `failure_category=null` rather than
  `provider_upstream_error`.
- Distinguish complete, partial usable, failed, item-failed, unsupported,
  truncated/retention-limited, and not-requested evidence through shared coverage
  normalization.
- Separate hypothesis `missing_evidence` from stable bounded
  `coverage_limitations` while retaining supporting dependency references.
- Count only actual failed sources or source operations in health and provider
  failure telemetry; preserve partial assessment and cursor snapshot behavior.
- Reject non-canonical signed cursor encodings fail-closed without changing valid
  cursor behavior.
- Retain 37 registered/25 canonical tools, all existing public schemas, the
  read-only boundary, planned `handoff_generation`, and production v1.1.2.

## 2.0.0-beta.19

- Add read-only Engineering-native `incident_correlation`, increasing the beta
  tool count to 37 while retaining 25 canonical tools and all existing schemas.
- Correlate bounded automation, entity, trace, history, logbook, structured log,
  registry, dependency, integrity, and reliability evidence into deterministic
  ranked hypotheses with supporting and contradicting evidence.
- Add explicit confidence, causal-status, severity, coverage, event-normalization,
  clustering, pagination, security, audit, and health-counter contracts.
- Preserve the Beta 16–18 signed snapshot lifecycle; continuation performs no
  upstream collection, index work, classification, or recorrelation.
- Add no write capability; production v1.1.2 remains unchanged and
  `handoff_generation` remains planned.

## 2.0.0-beta.18

- Replace broad dotted-token template scanning with context-aware extraction from
  explicit entity-bearing fields and recognized Home Assistant template helpers.
- Reject numeric fragments, versions, IP addresses, URLs, hostnames, service
  names, member access, and arbitrary dotted prose as exact entity references.
- Preserve exact literal references for `states`, `is_state`, `is_state_attr`,
  `state_attr`, and `expand`, while reporting dynamic arguments separately and
  without an invented target.
- Harden the shared canonical entity-ID validator and retain deterministic
  deduplication, evidence IDs, index fingerprints, pagination, health, and audit
  contracts.
- Preserve all 36 public tools, all existing input schemas, the read-only safety
  model, and production v1.1.2.

## 2.0.0-beta.17

- Add the Engineering-native, read-only `configuration_integrity_analysis`
  tool, increasing the beta manifest from 35 to 36 tools while retaining 25
  canonical tools.
- Detect exact missing entity references, references to disabled and
  registry-only entities, conservative orphan-registry candidates, and
  unresolved dynamic references as distinct evidence-backed findings.
- Reuse the shared dependency index plus one bounded state inventory and one
  entity-registry inventory; unsupported coverage remains explicit.
- Reuse Beta 16 signed, immutable pagination snapshots with upstream-free
  continuation, fixed provenance, whole-analysis totals, and fail-closed
  cursor validation.
- Add deterministic deduplication, bounded detail levels, field-level
  validation, safe auditing, and dedicated health counters.
- Add no write capability and no automatic cleanup, reference rewrite,
  incident correlation, handoff generation, or RC stabilization.
- Preserve production v1.1.2 and all existing public tool input schemas.

## 2.0.0-beta.16

- Bind refreshed-index pagination snapshots and signed cursors to the final
  committed dependency-index generation, allowing immediate upstream-free
  continuation while retaining fail-closed expiry, tamper, query-change, and
  index-replacement checks.
- Separate impact findings from unique affected objects and root-cause groups in
  both results and health telemetry; retain corrected Beta 15 compatibility
  aliases with explicit deprecation metadata.
- Report confirmed target-related, unresolved requested-scope, and out-of-scope
  dynamic references separately, requiring manual review when requested coverage
  remains unresolved.
- Return stable field/reason validation details without provider activity,
  dependency-index access, or pagination state, and correct generated article
  wording without changing rule IDs.
- Preserve the 35-tool manifest, every public input schema, the read-only impact
  policy, governance boundaries, and production v1.1.2.

## 2.0.0-beta.15

- Add the read-only `change_impact_analysis` Engineering-native tool for one
  entity and a proposed rename, remove, or disable operation, increasing the
  callable beta manifest from 34 to 35 tools.
- Reuse the existing dependency index for bounded direct and indirect impact
  evidence; no second dependency graph or write authority was introduced.
- Add 22 deterministic, evidence-backed rule IDs, stable findings, affected-object
  grouping, operation-specific consequences, advisory remediation, and four
  conservative assessment states without an opaque risk score.
- Add exact state and entity-registry evidence, honest static-source coverage,
  bounded retained trace headers, and sanitized exact System Log correlation.
- Add signed evidence-bound pagination snapshots, detail-dependent payload caps,
  truthful cache/timing provenance, safe audit summaries, and identity-free health
  metrics.
- Preserve every Beta 14 tool name and input schema, governance and provider
  boundaries, and production v1.1.2.

## 2.0.0-beta.14

- Fixed the Beta 13 null analysis timestamp by accepting injected timezone-aware clock instants and capturing one UTC request instant.
- Unified `list_automation_traces` and reliability analysis behind one sanitized trace-list transport and normalization contract.
- Added Home Assistant `{start, finish}` trace interval support alongside offset ISO strings and permitted epoch timestamps.
- Made the lookback cutoff inclusive, timezone-aware, fixed for the request, and bound to pagination fingerprints.
- Added truthful trace coverage states and bounded counts for upstream, parsed, eligible, selected, retrieved, failed, malformed, duplicate, and truncated runs.
- Restricted `no_recent_execution_evidence` to trustworthy empty trace results; source defects now return partial or failure truthfully.
- Added bounded sanitized pagination snapshots so cursor pages do not repeat HA trace collection or inflate aggregate counters.
- Preserved Beta 13 correlation, chronology, root-cause, timing, cache-truth, redaction, routing, and write-boundary protections.
- Preserved all 34 tool names and input schemas; production v1.1.2 remains unchanged.

## 2.0.0-beta.13

- Corrected reliability observation chronology using timezone-aware UTC ordering independent of Home Assistant source order.
- Replaced broad System Log substring correlation with exact identifier and trace-signature bases, with safe confidence metadata.
- Added deterministic root-cause groups so overlapping trace and action findings are not presented as independent incidents.
- Standardized reliability timestamps as RFC 3339 UTC strings and trace intervals as `started_at`/`finished_at` objects.
- Distinguished the bounded System Log snapshot from unverifiable lookback retention without discarding independent findings.
- Separated cumulative Home Assistant request effort from upstream wall-clock span and concurrency.
- Marked reliability-result caching honestly unavailable and prevented pagination from inflating finding/root-cause counters.
- Preserved all 34 tool names and input schemas; production v1.1.2 is unchanged.

## 2.0.0-beta.12

- Add the read-only `automation_reliability_analysis` tool for one internal
  automation ID, increasing the beta manifest to 34 callable tools.
- Compose bounded configuration, state, blueprint, trace, referenced-entity,
  registry, and sanitized System Log evidence behind an engineering facilitator
  provider; no tool handler calls Home Assistant directly.
- Add 13 deterministic rules with stable findings, confidence/status, evidence
  references, fingerprint-bound pagination, three detail levels, and honest partial
  coverage. No opaque reliability score is produced.
- Bound lookback, traces, entity reads, concurrency, findings, evidence, log
  correlation, response size, and total duration; independent source failures retain
  useful confirmed findings.
- Add safe analysis health counters without exposing configuration or evidence.
- Collapse duplicate adjacent Matter setup-payload redaction markers while preserving
  detection and sanitizer idempotence.
- Preserve the original 25 names and input schemas, all eight prior beta-native tools,
  governance and Phase 3C boundaries, and production v1.1.2.

## 2.0.0-beta.11

- Sanitize the complete recursive Home Assistant System Log result before any
  entry selection, field bounding, normalization, formatting, or serialization.
- Add key-aware and free-text redaction for authentication material, auth flows,
  webhook secrets, Matter commissioning values, credential-bearing URLs,
  serialized Python/JSON representations, cookies, and known runtime secrets.
- Use stable category markers without exposing secret fragments, lengths, hashes,
  encodings, prefixes, or suffixes.
- Fail closed per field when sanitation raises, preserve existing markers
  idempotently, and keep prompt-like log text inert untrusted evidence.
- Report only bounded redaction categories, field counts, and fail-closed state.
- Preserve 33 tools, all original 25 input schemas, Phase 3C routing, governance,
  dependency behavior, and production v1.1.2.

## 2.0.0-beta.10

- Replace the conditionally registered `/api/error_log` REST read with Home
  Assistant's supported admin-only `system_log/list` WebSocket command.
- Preserve the `tail_lines` input while returning bounded, newest-first,
  structured warning/error entries with explicit truncation and untrusted-data
  metadata.
- Redact access secrets, Supervisor tokens, authorization material,
  credential-bearing URLs, webhook secrets, and session identifiers from log
  content before it reaches responses, application logs, or audit output.
- Classify pre-upstream request validation as `request_validation` source
  coverage with zero Home Assistant time.
- Count `recent_error_counts` once per terminal public tool failure instead of
  once at each REST, structured-response, and facilitator propagation layer.
- Preserve all 33 tools, Phase 3C's four direct administrative-read policies,
  dependency behavior, governance boundaries, and production v1.1.2.

## 2.0.0-beta.9

- Align capability truth for `get_entity`, `list_areas`, `search_services`, and
  `list_services`: lifecycle `transitional`, route `transitional_direct`, and provider
  `direct_ha_api` under four specific read-only policies.
- Preserve facilitator dispatch, normalized envelopes, source coverage, timing, audit
  correlation, and provider counters for all four administrative reads.
- Document the verified stateless Home Assistant `/api/mcp` endpoint and reject
  approximate `GetLiveContext` mappings for exact entity, area, and service semantics.
- Honor dependency-analysis limits through 100 with explicit requested/effective limit
  metadata; separate current lookup/request timing from original build provenance.
- Clarify dependency health counters as cumulative truncation events versus current
  unresolved-reference index state.
- Keep production v1.1.2 and all write, physical-action, reload, delete, and governance
  boundaries unchanged.

## 2.0.0-beta.8

- Route canonical delegated, transitional, direct-required, and prohibited tools
  through the facilitator dispatcher while preserving all 33 tool input schemas.
- Fail delegated calls with a structured provider error when the Standard HA MCP
  gateway is unavailable; never silently invoke the legacy direct-HA implementation.
- Normalize routed responses and attribute provider request, success, failure,
  partial-result, and prohibited-fallback metrics.
- Enforce a reviewed tool-specific allowlist for direct Home Assistant exceptions and
  verify that `entity_dependency_analysis` is present and serializable in `tools/list`.

## 2.0.0-beta.7

- Add the read-only `entity_dependency_analysis` tool; the beta manifest now exposes
  33 tools.
- Build a bounded in-memory dependency index from automation configuration, blueprint
  input/source roles, entity state, and entity registry evidence.
- Add exact structured/template extraction, partial source coverage, cautious stale
  assessment, stable cursors, cache/refresh/invalidation, and bounded detail levels.
- Report unsupported source families and standard-MCP delegation honestly as
  unavailable while preserving all prior schemas and production v1.1.2.

## 2.0.0-beta.6

- Establish the Engineering MCP facilitator architecture, deterministic provider
  routing policy, and transport-independent evidence-provider contracts.
- Represent standard Home Assistant MCP delegation honestly as unavailable until a
  supported nested client transport is configured and verified.
- Add bounded, paginated, deduplicated response and evidence models for future
  analytical tools, plus safe provider-routing health counters.
- Replace free-text safety keyword matching with structured action, service, target,
  entity-domain, and blueprint-input risk evidence; harmless descriptive text no
  longer produces high-risk plans.
- Preserve all 32 beta tools, the original 25 schemas, the seven beta-native schemas,
  governance persistence compatibility, and production v1.1.2.

## 2.0.0-beta.5

- Map missing or invalid change-plan lookups to `change_plan_not_found` while
  reserving storage failures for real I/O, corruption, serialization,
  permission, and atomic-write failures.
- Treat the expected create-automation availability 404 as a successful probe
  branch so client responses, logs, plan events, and tool-call audits agree.
- Reject existing automation IDs as `configuration_conflict` and malformed or
  failed HA probe responses as real upstream failures.
- Replace transport-lifetime request latency with separate MCP operation, tool,
  and Home Assistant latency summaries; open stream lifetime is excluded.
- Preserve all 32 beta tools and all original 25 compatibility schemas.

## 2.0.0-beta.4

- Add approval-based change plans for creating and updating Home Assistant
  automations, with deterministic dry-run diffs and risk classification.
- Add hash-bound approval, stale-state protection, per-target concurrency,
  controlled apply, read-back verification, and separately approved rollback.
- Add atomic beta-only governance persistence, retention, corrupt-record
  quarantine, restart recovery, safe audit events, and bounded health metrics.
- Expose six governance tools for 32 total callable beta MCP tools while
  preserving all 25 production-compatible tool schemas.

## 2.0.0-beta.3

- Add fail-closed beta deployment and metadata validation for Windows development.
- Add a repeatable beta release checklist, optional health check, and cache-delay
  troubleshooting guidance.
- Keep the production v1.1.2 add-on and runtime unchanged.

## 2.0.0-beta.2

- Explicitly register `get_server_health` with the served FastMCP registry and
  verify its `tools/list`/`tools/call` exposure.
- Correlate upstream HA 4xx/5xx failures across structured tool responses,
  logs, and audit records; entity 404s now use `entity_not_found`.
- Add typed success and failure response contracts and a stable error taxonomy.
- Add request correlation, structured logging, bounded audit records, timing,
  and safe runtime metrics.
- Add beta-native `get_server_health` and migrate `server_info`,
  `list_capabilities`, and `get_error_log` to structured responses.

## 2.0.0-beta.1

- Add an isolated, parallel-installable v2 beta add-on.
- Introduce modular application, gateway, client, model, audit, capability, and
  version boundaries.
- Preserve the v1.1.2 25-tool catalog and public argument schemas.
