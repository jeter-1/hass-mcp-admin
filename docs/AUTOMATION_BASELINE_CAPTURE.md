# Native loaded-automation baseline capture

This owner-approved source increment adds `capture_automation_baseline(limit=25,
cursor="", resume_capture_id="")`. HAMCP-228 adds the
[durable baseline store](#durable-baseline-store-hamcp-228),
[resume](#resume-hamcp-228) and
[`compare_automation_baselines`](#compare_automation_baselines-hamcp-228).
Release preparation, signed applicability and installed acceptance remain
separate. It consumes the [offline baseline contract](AUTOMATION_AUDIT_BASELINE.md);
captures are written as `automation-audit-baseline-v2`.

## Scope and identity

`loaded_automation_entities-v1` means all administrator-visible automation states
at the initial REST inventory, including operational `off`. It does not mean all
stored YAML, packages or registry-disabled automations. Registry-only discoveries
are supplemental partial evidence; the registry can silently omit serialization
failures. `ADDED` and `REMOVED` in a comparison mean membership in this named loaded
scope, not creation or deletion from disk. Full stored inventory remains open.

A canonical configuration ID must agree between the state `attributes.id` and the
matching automation-platform registry `unique_id`, without a duplicate ID or
disabled registry entry. Unmapped objects are omitted with a counted limitation,
not assigned synthetic canonical IDs. REST configuration failures remain explicit
unreadable records. There is no fallback to loaded/expanded WebSocket configuration,
so package-only configurations may remain unknown. Referenced blueprint bodies are
not part of the returned configuration's fingerprint.

`supervised_core_registry_lineage-v1` hashes a domain-separated tuple of the unique
persisted Hass.io Core device-registry ID and its uniquely loaded Hass.io
config-entry ID. Only the fingerprint is exported. This is registry-lineage
assurance, not a Core UUID, host identity or cross-clone discriminator. Ordinary
restart/update preserves it while those records survive. It changes when either
persisted device or config-entry ID changes. Core can restore a removed device
from its tombstone with the same ID; that restoration alone does not change
lineage. Recreating the config entry changes its ID even if Core restores the
device ID. Restored clones may share the lineage. Cross-clone comparisons are outside
this assurance. The owner approved that limitation for this first increment.

## Collection, authority and privacy

A capture uses one frozen configured origin/token pair and one authenticated
WebSocket. Both capture fences read, in order:

1. `auth/current_user`: retain only strict `is_admin=true`.
2. `config_entries/get` with fixed `domain=hassio`: retain only ID/domain/loaded state.
3. `config/device_registry/list`: select the unique `("hassio", "core")` anchor.
4. `GET /api/states`: inventory loaded automations, retaining entity/configuration
   IDs and operational on/off/unknown/unavailable state only.
5. `config/entity_registry/list`: retain only automation mapping fields.

The REST state reader is deliberate: exact Core's WebSocket `get_states` may
silently omit unserializable states. REST fails the response instead. An unreadable
initial inventory refuses capture. The same credential and admission bind all reads;
principal evidence from an unrelated connector or account is never substituted.

Between fences, four workers read `GET /api/config/automation/config/{verified_id}`.
No caller can supply an endpoint, command, path, identity, provider or credential.
The full returned JSON data object is hashed ephemerally under
`automation-audit-config-json-python-v1`. Raw configurations are never retained in
snapshots, exports, audit or diagnostics. No sanitized/truncated projection is
substituted for the full hash. The recomputed-hashes assertion refers only to
successfully readable records; unreadable/omitted records have no digest.

Before/after each transport operation, request-scoped Core authority must remain
valid at the original generation/version. The new semantic capability is
`core.automation_baseline_metadata_read`, in addition to the existing REST,
WebSocket, non-device registry, direct-device registry and configuration-read
profiles. Existing profile fingerprints and signed entries are unchanged. The
readiness builder reuses existing transport evidence; it does not issue new
readmission probes or admit itself. Old signed entries withhold this capability.

Changed/missing/ambiguous identity, privilege loss or authority drift refuses the
capture. If only the final bounded collection fails, retained useful records may
be exported with unknown fence continuity. HAMCP-235: the final fence reads the
installation identity first (principal, config entries, devices) and its
inventory second; when only the inventory reads fail, the identity re-verified
by the final fence still establishes the capture (`final_fence:
"identity_only"`), and when the identity reads fail too it is unestablished
(`final_fence: "unavailable"`). `diagnostics.final_fence_reason` keeps the
failed read's fixed reason (for example `timeout`), and
`diagnostics.first_fence_installation_id` keeps the opaque identity digest the
first fence observed. An automation's on/off state is volatile and is not
inventory drift; the fences compare each automation's entity, configuration id
and the unmapped and withheld counts. Changed
inventory/mappings stay explicit; comparison cannot use that capture as absence
authority. Equal before/after observations do not prove atomicity: changes and
reversions within the interval may be missed.

Sensitive metadata from auth, config entries and device records is discarded before
retention or hashing. Retained identifiers must pass the existing sanitizer unchanged,
even when optional redaction is disabled. HAMCP-228 also applies the HAMCP-226
default-mode credential screen (`untrusted_text.screen`: `credentials.screen`
in default mode plus the PEM private-key header check) to every retained
identifier (owner direction, Josh, 2026-10-26). An identifier the sanitizer
changes is unusable, so its object is omitted with a counted limitation. An
identifier the credential screen withholds is never stored either; it is
reported as a content-free stub (below). Fixed diagnostic reasons never reflect
rejected keys, exception text, payloads or cursor values. Audit records contain only
scope, bounded limit, continuation presence and bounded capture summaries; no raw
configuration, auth identity or cursor is recorded.

Storage-boundary screen (review 2, R1): after collection and before the
report is hashed (`artifact_sha256`) or stored, every baseline record and every
capture diagnostic is screened (`audit_baseline/storage_screen.py`), so no
unscreened Home Assistant text reaches disk, including a field added later. The
store screens the encoded report again before its transaction and refuses
anything unscreened (`redacted_identity`) without touching a file. The final
response screen stays as a backstop behind both. A credential hit is withheld,
not refused (owner decision, Josh, 2026-10-27):

- an optional field (a diagnostic leaf, a record field other than the
  automation configuration id, such as the nullable `entity_id`) becomes the
  withheld marker; a withheld diagnostic key drops its entry. The record keeps
  its place with `coverage.redacted: true`, one more warning and `partial`
  completeness, and the capture is `partial` with a warning;
- the required identity, the automation configuration id (or an entity id the
  projector cannot join), withholds its record as a content-free stub
  `{configuration_id, entity_id, reason}`, where `reason` is
  `configuration_id_withheld`, `entity_id_withheld` or
  `record_unrepresentable` (a withheld value the record schema cannot hold),
  and only a clean id is named. The inventory is `partial` and
  `omitted_count` grows by the stub count; the capture continues;
- the baseline header holds only fixed, validated fields, which a marker cannot
  represent, so a hit there refuses the capture (`redacted_identity`) before
  anything is hashed or stored.

`diagnostics.credential_screen: {withheld_field_count, withheld_record_count,
withheld_records}` lists the first 16 stubs. Nothing is derived from a withheld
value: a stub carries no digest of it (owner decision, 2026-10-22).

Capture failures preserve the existing error codes and response envelope.
Only `capacity_busy`, `timeout`, `source_unavailable` and (HAMCP-228)
`store_unavailable` advertise `retryable=true`; all other declared capture reasons are nonretryable.
Retryability guides a later caller decision. It never triggers an internal
retry, fallback or automatic refresh, and repeating unchanged access, identity,
configuration or limit failures is not a recovery strategy.

HTTP 401/403 remains `access_denied` for both fixed REST readers. Other non-200
responses reaching REST status handling report `source_unavailable` for states
and `configuration_unavailable` for an individual configuration. Redirects are
refused before that handling and remain `source_unavailable` for both readers.
Initial states failure still refuses capture; final states failure may preserve
partial evidence with unestablished identity and unknown continuity. An unavailable
individual configuration remains unreadable with no digest. Access denial aborts
capture at every stage; it is never reduced to an unreadable record.

## Bounds and frozen export

| Boundary | Limit |
|---|---:|
| Retained baseline records | 1,000 |
| Rows per global inventory input | 20,000 |
| WebSocket frame / REST state response | 4 MiB |
| Accepted authentication payload | 64 KiB |
| Each configuration response | 256 KiB |
| Total transport bytes | 32 MiB |
| JSON values / nesting / string bytes | 100,000 / 64 / 16 KiB |
| Collection / individual read deadlines | 45 s / 5 s |
| Concurrent configuration reads | 4 |
| Application reads, including auth | At most 1,011 |
| Stored baselines (HAMCP-228) | 12 × 2 MiB, 180 days; continuation cursors 600 s |
| Page records / response data | Default 25, maximum 100 / 48,000 bytes |

Configuration scheduling stops early enough for the final fence. HAMCP-235:
the reserve is the individual read deadline plus twice the measured first
fence, clamped to 10–25 s (`diagnostics.final_fence_reserve_seconds`); before,
a fixed ten seconds left a slow installation's final fence without time.
HAMCP-228: a record not scheduled before that reserve is `timed_out` (schema
v2), with no digest, instead of an indistinguishable unreadable record.
Network operations share the collection deadline; cancellation drains owned bounded
pure worker computation and closes transport before returning. There is no detached
capture job, retry, redirect, fallback or automatic refresh. Parsing, projection,
hashing, validation and page fitting run outside the event loop. The accepted auth
payload bound is checked before JSON decoding; the connection's wire-frame ceiling
remains 4 MiB. Unexpected/control frames are refused, not automatically ponged.

Reaching the record ceiling is explicitly partial even at exactly 1,000. Unmapped
and registry-only ID previews retain at most 16 IDs each; counts and their precision
remain separate. A byte/structural failure is never accepted as a complete inventory.

A continuation cursor is HMAC-bound to the caller and the stored capture.
Continuations make zero HA reads and page the stored, digest-verified copy.
Cursor expiration (600 s), restart or tampering fails explicitly; the stored
baseline itself remains available to `compare_automation_baselines` and resume
until its retention ends. During another capture, a new capture is refused. A
capture that fails or is cancelled before its first page is ready stores nothing.
There is no background watcher.

To export, retain the first `baseline_header` and append `records` from every page
in offset order. Verify every header/digest agrees, offsets are contiguous, the
last cursor is null, and collected count equals `total_records`. Last-page
`complete_export=true` means the server has no further records; it does not prove
that a client retained earlier pages. Construct `{**baseline_header, "records":
all_records}`, serialize sorted compact JSON with `ensure_ascii=True`, UTF-8, and
verify `artifact_sha256` before saving. Do not add diagnostics to baseline bytes.

Two distinct ordered exports can also be passed unchanged to the offline command:

```bash
python scripts/compare_automation_baselines.py earlier.json later.json --output comparison.json
```

Legacy October evidence remains unresolved and is never retroactively upgraded.

## Durable baseline store (HAMCP-228)

Owner decision S: each finished capture is committed to one bounded
Engineering-local store at `<governance_path>/audit-baselines-v1/` (the
Engineering governance disk boundary, beside the other governance records; not
`/data/audit_baselines/`; architect-accepted). It holds `index.json` and one
`records/<capture_id>.json` per baseline.

- At most 12 baselines of at most 2 MiB each, kept 180 days from commit. At
  capacity a new capture is refused with `store_full` before any Home Assistant
  read; the details carry only `stored_baselines`, `oldest_age_seconds` and
  `retention_seconds`. Nothing is evicted to make room.
- Only the validated baseline report is stored: identities, digests,
  statuses, outcomes and diagnostics, never a configuration body.
- Each baseline is bound to the capturing caller. Another caller's baseline is
  indistinguishable from a missing one (`baseline_not_found`); `anonymous` is
  refused.
- Files are mode 0600 in 0700 directories, read with `O_NOFOLLOW` as regular
  files only; writes use a temporary file, `fsync` and rename, record before
  index. One nonblocking `flock` owner at a time: a second owner is refused
  (`capacity_busy`), not queued. A commit whose outcome is unknown fails closed
  (`store_unavailable`) until restart.
- Every load checks the file size and SHA-256, the `artifact_sha256` and the
  baseline validator. A damaged record (`missing`, `unreadable`,
  `digest_mismatch`, `invalid_record`) is deleted and reported as
  `baseline_damaged`; the index keeps a content-free quarantine stub (at most
  32, older stubs counted as `dropped`). A symlinked namespace is refused.
- Each record is a self-binding envelope (`model`, `binding`: its capture id,
  caller, creation time and resume origin, `report_sha256`, `report`). A
  missing or unreadable index (including a symlinked index file, which is
  never followed, and an index present but unreadable, such as permission
  denied, review 2, R2) never deletes a committed baseline (review 1, H2): the index
  is rebuilt from the record files, each verified from its own envelope and
  its file name. A file that fails verification is quarantined as a stub
  (`unreadable` or `invalid_record`), never silently deleted; the stubs are
  committed before the file is removed. A verified record past retention is
  not re-indexed and is removed by the next sweep. More verified records than
  the store admits fails closed (`store_invalid`) with no file touched. Files
  that are not store names are never touched.
- A rebuild needs a readable and writable namespace. When the namespace or
  its records directory cannot be written, listed or read, or a record file
  cannot be read, nothing is deleted or overwritten: the operation reports
  `store_unavailable` (retryable) with a fixed `store_condition`
  (`namespace_not_writable`, `namespace_unreadable` or `record_unreadable`)
  in the error details, and the next operation after the permissions are
  repaired rebuilds normally.
- The rebuild is reported content-free as `store_status: {index_rebuilds,
  last_index_rebuild: {at_ms, reason: index_missing|index_unreadable,
  recovered, quarantined}}` (`{0, null}` before any rebuild) in the capture
  page `snapshot` and in every comparison. A store in first use (no index and
  no records) is not a rebuild.
- Each store operation sweeps expired entries, abandoned temporaries and
  unindexed records.
- Hashes detect accidental damage, not an attacker able to rewrite both a
  record and the index.

The capture page `snapshot` block reports `storage:
"durable_engineering_store"`, `retention_seconds`, `resumed_from` (the
resumed `capture_id`, or `null`) and `store_status`. Because a capture now writes this
Engineering-local record, the tool is classified as Engineering local evidence
capture with `readOnlyHint=false`; it still performs no Home Assistant write.

## Resume (HAMCP-228)

Owner decision T: `resume_capture_id` names one of the caller's stored
captures. Only its timeouts are retried: `timed_out` records and `unreadable`
records whose outcome is `timeout`. Any other failure is carried forward
unchanged; repeating it is not a recovery strategy.

Before any Home Assistant read, the base is loaded (same caller, digests
verified) and `resume_not_needed` is refused if it has nothing to retry, and
capacity is checked. The resume then runs both capture fences again and
compares only stable identity with the base (HAMCP-235): the installation
anchor (the base's established installation id, else the identity its first
fence observed), the set of configuration ids and each id's entity. Volatile
state never refuses a resume: not an automation's on/off state, not the Core
authority generation (a reconnect, registry refresh or Engineering restart),
and not the unmapped count. A refusal is `resume_inventory_changed` with
`details.resume_changes`, a sorted list of fixed categories:
`automation_added`, `automation_removed`, `mapping_changed`,
`installation_changed`. A restart can reuse the authority generation, so the
base's Core version binds the resume too: a different version is refused as
`resume_core_changed` with `resume_changes: ["core_version_changed"]` (not
retryable) before any configuration read (review 1, M1). The categories never
carry an identifier, count or Home Assistant text. Retried records are
collected fresh; all others are copied verbatim from the base. The result is a
new stored baseline with `resume: {resumed_from: <base baseline_id>,
resumed_count}`; its capture interval starts at the base's start, and drift in
the base is inherited. A new authority generation is recorded as
`authority_drift: "detected"`. A base that recorded its first-fence anchor and
whose own final fence failed is established by the resume's verified fences;
a base from an earlier release without that anchor stays unestablished. The
base is never changed.
`resume_capture_id` cannot be combined with `cursor`.

## `compare_automation_baselines` (HAMCP-228)

`compare_automation_baselines(earlier_capture_id, later_capture_id="",
recorded_fingerprints=None, fingerprint_model="", cursor="")` compares the caller's stored
capture with either a later stored capture or caller-recorded fingerprints. It
makes no Home Assistant read, is read-only and idempotent, and has a closed
argument contract; invalid arguments are refused without dispatch and never
echoed.

- Two stored captures use the [offline comparator](AUTOMATION_AUDIT_BASELINE.md#comparison-semantics)
  unchanged, including `same_baseline_id` and `capture_intervals_not_ordered`.
- `recorded_fingerprints` holds at most 1,000 unique `{configuration_id,
  fingerprint: "sha256:" + 16..64 lowercase hex}` rows and requires
  `fingerprint_model`. A full 64-character match is `UNCHANGED`. A shorter
  matching prefix is its own `PREFIX_MATCH` bucket with reason
  `prefix_match:<n>` and its own count; it is never counted as `UNCHANGED`
  (architect direction, Josh agrees). A differing prefix is `CHANGED`.
- Recorded fingerprints carry no installation identity or inventory
  authority, so an id on one side only is `UNKNOWN`
  (`absent_from_capture` / `absent_from_recorded_fingerprints`), never `ADDED`
  or `REMOVED`. A `fingerprint_model` other than the capture's makes every row
  `UNKNOWN` (`fingerprint_model_mismatch`). The capture's own row
  prerequisites (mapping, readability including `timed_out`, coverage and
  digest) also apply; installation identity and authority continuity are
  comparison-level caveats (below), with the recorded side always listed as
  unverified.
- HAMCP-235 (owner decision, Josh, option a): rows are classified by digest
  (`UNCHANGED` / `CHANGED` / `PREFIX_MATCH`; `ADDED` / `REMOVED` only where
  inventory authority allows). What the comparison cannot verify is stated
  once in `caveats`, a list of `{code, statement, sides}` with the codes
  `installation_identity_unverified`, `authority_continuity_unestablished` and
  `non_atomic_capture`; it is identical on every page and is never repeated as
  a row reason. `UNKNOWN` is kept for rows that are genuinely incomparable,
  each with its reason: a timed-out read (`timed_out`, from either capture's
  stored outcomes), a missing digest, an unreadable configuration, incomplete
  coverage, an unverified mapping, a fingerprint contract or model mismatch,
  an inventory scope mismatch, or two established, different installations
  (`installation_identity_mismatch`).
- Counts are `UNCHANGED`, `PREFIX_MATCH`, `CHANGED`, `ADDED`, `REMOVED`,
  `UNKNOWN` and `TOTAL`; stored/stored comparisons report `PREFIX_MATCH: 0`.
- Rows other than `UNCHANGED`/`PREFIX_MATCH` come first, then by
  configuration id. The counts always cover every row. Records are fully
  paged, never truncated (review 1, M2): each page holds at most 100 records
  within the page budget, and `pagination: {offset, returned, total_records,
  next_cursor, complete_export}` continues it. `next_cursor` is bound to this
  caller, the exact arguments and the offset (HMAC under a process secret);
  any other use is `invalid_cursor`. Repeat the same arguments with the
  cursor until it is `null`. `omitted_detail_count` is the number of records
  not on this page.
- The output is credential-screened (`untrusted_text.screen_tree`, default
  mode), and every response then passes a final-envelope screen as a backstop
  (review 1, H1), so a field added later cannot return Home Assistant text
  unscreened; `screened_withheld_count` makes a withheld value a partial
  result. A capture page is the stored, already-screened report (its header
  and records under `artifact_sha256`, which covers the baseline; its
  diagnostics as stored), so the backstop does not rewrite a page: a capture
  it would change is refused (`redacted_identity`) instead; because
  the storage-boundary screen runs first, that refusal can only follow a
  screen defect, and nothing unscreened has been stored.
- Audit records carry the mode, the counts, whether a cursor was supplied and
  the returned/total record counts, plus the withheld count; never capture
  ids, configuration ids, cursors or digests.

## Exact Core source evidence and validation stages

Semantic inspection uses Core commit
`9212531f40a0b7b23229a90d688dd79d9dfccff4` (2026.9.4), archive SHA-256
`c8d814b63c0b7043ad54980044f9824c9582450af9cac38ba376fadc37fa4056`:

- `components/api/__init__.py:206`: admin REST states, no serialization omission.
- `components/websocket_api/commands.py:370`: WebSocket omission boundary.
- `components/auth/__init__.py:481` and `auth/__init__.py:443`: actual connection
  principal and MFA read delegation. Built-in TOTP/notify setup readers load stored
  metadata but do not invoke setup flows, validation or save operations.
- `components/config/view.py:98`: administrator-only fixed configuration lookup.
- `components/config/entity_registry.py:39`: partial registry serialization.
- `components/hassio/coordinator.py:1173` and `helpers/device_registry.py:379,527`:
  persisted Core device identity and config-entry linkage.

Source-function tests verify the archive before executing isolated functions with
synthetic inputs. They do not substitute for an assembled disposable Core lane.
That lane must prove authenticated admin/nonadmin reads, actual Core device/entry
projection, lossless REST configuration and state behavior, fixed requests,
zero writes, artifact reconstruction and refusal controls before release readiness.
See [installed acceptance](AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md).
