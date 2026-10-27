# HAMCP-225, HAMCP-226, HAMCP-228 Audit Tooling: Design Note

Registers: HAMCP-225 (trace snapshots, audit HAAC-E001), HAMCP-226
(unavailable references and named bound overruns, HAAC-E002 and E002a),
HAMCP-228 (baseline diff and resume, HAAC-E004 and E014). The register rows
are authoritative. The audit sheet `08 HA Automation Audit - Cycle 2026-10`,
tab `Engineering Gaps`, is background only.

Base: `origin/main` `06a28134f219244f258ab26307c1dfcb9e5d18cb`
(2.4.1-beta.37). Part 1 of this note changed no runtime file. Part 2
implements the decisions in section 8 in two pull requests (decision P).

Owner decisions in force (Josh, 2026-10-26):

- **O1.** HAMCP-225 covers **automation** traces only. A script-trace read
  profile goes to the November signing batch (HAMCP-232/HAMCP-145 session). It
  is not built here.
- **O2.** The trace-read part of HAMCP-194 is folded into HAMCP-225.
  `get_automation_trace`, and every automation-trace read that snapshot capture
  uses, go through the shared sanitizer: the HAMCP-191 single exit point plus
  the HAMCP-132 credential and known-secret withholding. Withheld content makes
  the result `partial`. Capture sanitizes before anything is stored. The rest of
  HAMCP-194 stays in its backlog (recorded on the HAMCP-194 row).
- **O3.** Register rows HAMCP-225, HAMCP-226 and HAMCP-228 define scope.

Part 2 owner decisions (Josh, 2026-10-26): **all section 8 recommendations
accepted**, plus one addition.

- **W1.** Bounded read-only poller: at most 10 watched automations, polled
  every 30 seconds.
- **E1 + E2.** At capacity, new manual captures are refused (`store_full`).
  Each watch has a 20-snapshot quota and evicts only its **own** oldest
  snapshot when that quota is full. This eviction is new behavior and is
  disclosed in the tool description and the capture result. Manual snapshots
  are never evicted.
- **R1.** Strict re-scan on read-back. A string that was partly redacted at
  capture is withheld whole on read.
- **S2.** A durable bounded baseline store: 12 baselines, 180 days.
- **T.** Resume retries timed-out records only (`timed_out`, plus `unreadable`
  with outcome `timeout`).
- **B.** The bounds in sections 2.4, 2.5 and 4.1 as written.
- **P.** Two pull requests: HAMCP-225 alone first, with its own review; then
  HAMCP-226 and HAMCP-228 together. Consecutive betas, each taking the next
  free number at materialization (`2.4.1-beta.38` is staged by #313).
- **W1 addition (watch-list management).** Adding or removing a watched
  automation needs **no change plan and no external approval**: it is
  read-only toward Home Assistant and has no effect on the house. It is still
  an owner-visible, audited action: every add and remove writes an audit row
  with caller attribution, the list is bounded at 10, and the managing tool
  returns the current watch list with every response (section 2.5).

## 1. Part 0: signed coverage (ADR-021)

Authority: `upstream-trust/ha-core-release-registry.json`, last envelope
sequence 14 (generated 2026-10-25T10:29:39Z, expires 2027-01-23). It admits
Core `2026.10.0` (22 capabilities), `2026.10.1` (26) and `2026.10.3` (26). All
three entries include every capability this note uses:

| Capability | Commands used here | Used by |
| --- | --- | --- |
| `core.automation_trace_read` | WebSocket `trace/list`, `trace/get`, `domain: "automation"` only | 225 |
| `core.basic_rest_read`, `core.direct_entity_state_read` | `GET /states` | 226, 228 |
| `core.non_device_registry_read` | `config/entity_registry/list` | 226, 228 |
| `core.automation_configuration_read`, `core.automation_baseline_metadata_read` | `GET /config/automation/config/{id}` | 228 |
| `core.dependency_helper_planning` | `automation/config`, `script/config`, `search/related` inside the dependency index build | 226 |

**Not admitted:** `trace/list` and `trace/get` with `domain: "script"`. The
`core.automation_trace_read` contract and its probes
(`ha_core_readmission/profiles.py:259-280`) are automation-only, and no code
sends `domain: "script"`. Per O1 nothing here sends it. Also unused:
`trace/contexts` and every `subscribe_*` command. A watched capture (section
2.5) therefore polls; it does not subscribe.

**Result:** no new Core capability, no new command shape and no signing change.

### 1.1 Core trace facts (pinned source)

Source: Core `2026.10.1` (`abe1fdd`) and `2026.10.0` (`6a811d3`), verified
byte-identical to GitHub for `components/trace/*` and
`components/automation/trace.py`; `2026.10.3` is identical to `2026.10.1` for
`trace/`.

- `DEFAULT_STORED_TRACES = 5` per automation (`trace/const.py`). An automation
  can raise it with `trace: stored_traces: N`.
- Runs and not-triggered traces are separate `LimitedSizeDict` buckets
  (`trace/models.py` `TraceBuckets`).
- A trigger that fires and then fails its conditions is a **run**, with
  `script_execution: "failed_conditions"`
  (`automation/__init__.py:763`). A per-minute time trigger whose condition is
  false therefore evicts a real run from the 5-slot run bucket within about
  five minutes. This is HAAC-E001: the 2026-10-18 garage departure trace was
  gone before review.
- Other `script_execution` values: `finished`, `aborted`, `cancelled`, `error`
  (`helpers/script.py:514-540`), `failed_single`, `failed_max_runs`,
  `disallowed_recursion_detected` (`:1930-1970`), `not_triggered`
  (`automation/__init__.py:949`).
- The `trace/list` short form carries `run_id`, `state`, `script_execution`,
  `last_step`, start and finish timestamps, and `error` when set
  (`trace/models.py:148-178`). `trace/get` adds `config`, `blueprint_inputs`,
  `context` and the step trace with variables.
- Core writes traces to `.storage/trace.saved_traces` only at Home Assistant
  stop. Engineering does not read that file.

### 1.2 Current Engineering trace paths

| Path | Sanitized today |
| --- | --- |
| `list_automation_traces` via `trace_normalization.fetch_normalized_trace_list` (`:63-78`) | Yes (`max_string=2_000`) |
| Reliability and incident `trace/get` (`reliability/provider.py:371-385`) | Yes |
| `get_automation_trace` (`tools/compatibility.py:531-544`), a `DIRECT_HA_TOOL_EXCEPTIONS` route through `providers/dispatch.py:248` `_direct` | **No.** Raw `trace/get`, only the 60,000-character `dump` cut |

The last row is the HAMCP-194 trace-read defect that O2 moves here.

## 2. HAMCP-225: automation trace snapshots

### 2.1 `get_automation_trace` under the shared sanitizer (O2)

- Same input schema, same command, same `data` shape (the trace object).
- One sanitized exit: `trace/get` result, then
  `sanitize_untrusted_data(value, known_secrets=(ACCESS_SECRET, HA_TOKEN),
  max_string=2_000)`. Nothing is added after that call. If a later step must
  add text (for example a bound omission), the envelope is rebuilt and
  sanitized again, as in HAMCP-132 slice 1 note 12.
- Any redaction, truncation, marker collision or `failed_closed` field makes
  provider completeness `partial`, with a fixed warning naming the categories.
  The signal travels through the dispatcher's existing payload and metadata
  contract (`_normalize_payload`). `providers/dispatch.py` changes only if that
  contract cannot carry `partial` without changing the trace object's shape.
- The new call site is added to the HAMCP-191 `SANITIZER_SITES` AST inventory
  (`tests/test_hamcp191_shared_sanitizer.py`).
- Known limitation, unchanged from today's `get_automation_config`: Core
  returns `config` with `!secret` values already resolved. A resolved secret in
  a field that is neither credential-named nor credential-shaped, and is not
  one of Engineering's known secrets, is not detectable. This is disclosed in
  the tool description.

### 2.2 New tools

| Tool | Inputs | Core reads | Risk |
| --- | --- | --- | --- |
| `capture_automation_trace` | `automation_id` (configuration id, existing pattern); `run_id` (optional); `include_condition_failures` (default `false`); `note` (optional, at most 200 characters) | `trace/list`, then `trace/get` for each selected run | `engineering_evidence_write` (Engineering-local storage only; no Home Assistant write) |
| `list_trace_snapshots` | `automation_id` (optional), `limit` (at most 50), `cursor` | none | `read` |
| `get_trace_snapshot` | `snapshot_id`, `cursor` | none | `read` |

- With `run_id`, exactly that run is captured. If Core no longer holds it, the
  result is `trace_not_retained`, not an empty success.
- Without `run_id`, every retained run is captured except
  `failed_conditions` (unless `include_condition_failures`) and
  `not_triggered`. At most 5 runs, plus not-triggered traces only when
  explicitly requested.
- A run whose `state` is still `running` is not captured. It is reported as
  `run_in_progress`.
- Every result lists each selected run with an outcome:
  `captured`, `already_captured` (same `automation_id` and `run_id`; the stored
  copy is kept), `trace_not_retained`, `run_in_progress`, `over_size_bound`,
  `store_full` or `read_failed`.
- `get_trace_snapshot` pages through steps. Page 1 holds the header, the run
  summary, `blueprint_inputs`, `context` and the bounded `config`. Later pages
  hold the step trace in Core's own path order. The cursor is
  HMAC-bound to the caller, as in `audit_baseline/capture_service.py`. Each
  page fits the existing 60,000-byte response bound.

Snapshot tools are core-independent (`engineering_local_state`) for reads.
`capture_automation_trace` requires `core.automation_trace_read` in
`ha_core_readmission/routes.py` `STATIC_TOOL_CORE_REQUIREMENTS` and fails
closed when Core authority is unavailable or the version is not admitted.

### 2.3 Sanitize at capture, before storage

1. Read through the sanitized `fetch_normalized_trace_list` and the section 2.1
   `trace/get` path. No unsanitized value reaches the store.
2. Build the complete record: identity (`automation_id`, `run_id`, entity id
   from the trace), Core version and authority generation, `captured_at`,
   caller attribution, `note`, sanitizer summary and the sanitized trace.
3. Run one strict pass over the complete record. If that pass changes
   anything, set `completeness: "partial"` with its categories and counts,
   rebuild, and sanitize again. The stored record is the last pass's output.
4. Check size against the per-snapshot bound only after sanitization.

**Read path (decision R, section 8).** The read tool also has one exit: the
envelope (stored record plus page metadata) passes the strict sanitizer once,
with the current known secrets. The HAMCP-191 strict pass replaces stored
marker text with `MARKER_COLLISION`. A string that was partly redacted at
capture is then withheld whole on read. The snapshot is already `partial`, so
this loses fidelity but cannot leak. HAMCP-207 (persisted marker identity)
would restore fidelity later. Recommended: **R1**, accept this. **R2**, extend
the HAMCP-191 D11 stored-plan marker tolerance to snapshots, is not
recommended, because D11 is a deferred exception and this would widen it.

### 2.4 Store

Namespace `trace-snapshots-v1/` under the governance disk boundary
(`settings.governance_path`, on `/data`; section 10). It follows the
`governance/reverification_storage.py` precedent: `flock` on a lock file,
`O_NOFOLLOW`, mode `0600`, temporary file plus `fsync` plus `rename`, a fixed
diagnostic with no payload reflection, and quarantine of a record whose stored
SHA-256 does not match (`governance/storage.py` precedent).

| Bound | Proposed |
| --- | --- |
| Snapshots | 200 |
| Bytes per snapshot (canonical JSON, after sanitization) | 262,144 |
| Store bytes | 32 MiB |
| Age | 90 days, removed by a sweep on each capture and list |
| Captures per call | 5 runs |

Expiry by age is existing precedent (governance retention cleanup). Records
are sanitized evidence only. `/data` is included in add-on backups, so
snapshots go into Home Assistant backups too. That is acceptable for sanitized
evidence and is documented.

**Eviction at capacity (decision E, section 8).** No Engineering store evicts
by count today; all of them refuse at capacity. Options:

- **E1 (recommended for manual captures): refuse at capacity.** The capture
  returns `store_full` with the oldest snapshot's age. Nothing is lost
  silently. Age expiry frees space. 200 manual captures is far above audit
  practice (dozens per cycle).
- **E2 (recommended only with watch W1): oldest-first eviction inside a watch
  quota.** Each watch owns a quota (20 snapshots). When the quota is full, its
  own oldest snapshot is evicted. An audit row records the eviction. Manual
  snapshots are never evicted. This is new behavior and needs explicit
  approval.
- **E3: oldest-first eviction across the store.** Not recommended. A burst of
  watch captures could remove incident evidence someone captured by hand.

A `delete_trace_snapshot` tool is not proposed. It would add another
Engineering-local write tool. Age expiry is the release valve.

### 2.5 Bounded trigger: watched automations (decision W, section 8)

On-demand capture alone does not fix HAAC-E001. Someone must call it within
about five minutes of the run. The register outcome allows an optional bounded
trigger. That requires Engineering to start Core reads on its own schedule.
The only precedent is the dependency-index prewarm (`dependency/runtime.py`
`start_prewarm`), which owns its own Core authority lease
(`dependency/authority.py`) and retries every 300 seconds until one build
succeeds; no Engineering feature polls periodically today. Options:

- **W0:** on-demand only in this PR; watch becomes a follow-up task.
- **W1 (recommended; decided): bounded poller.** Tool
  `set_automation_trace_watch(action, automation_id, mode, duration_hours)`
  with `action` one of `add`, `remove` or `list`. Risk
  `engineering_evidence_write` with `external_approval_required: false`: no
  change plan and no approval (W1 addition), because it is read-only toward
  Home Assistant. Every `add` and `remove` writes an audit row with caller
  attribution, and every response (including refusals) returns the current
  watch list. `add` for an automation already watched replaces its mode and
  expiry and keeps its snapshots.
  - At most 10 watches, each expiring after at most 168 hours. Watches persist
    in the store, so a restart resumes them until expiry.
  - Every 30 seconds the poller sends one `trace/list` per watched automation
    (at most 20 commands a minute). It sends `trace/get` only for new, stopped
    runs that match the mode:
    - `failures`: `error`, `aborted`, `cancelled`, `failed_single`,
      `failed_max_runs`, `disallowed_recursion_detected`, or an `error` field;
    - `executed`: every run except `failed_conditions` and `not_triggered`.
  - Captures follow section 2.3 and are attributed to the watch's creating
    caller with origin `trace_watch`.
  - The poller pauses while Core authority is unavailable or the version is
    not admitted. Missed runs are reported as a gap count, never backfilled.
    No subscription and no new command shape are used.
- **W2:** watches configured in add-on options. Not recommended: it changes
  `hass_mcp_engineering_beta/config.yaml`, a protected deployment file, and a
  change requires an add-on restart.

Owner operational alternative that needs no code: raise `stored_traces` on the
noisy automation through a governed change plan.

## 3. HAMCP-226: unavailable references and named bound overruns

### 3.1 `unavailable_entity_reference`

Today `integrity/rules.py:42-56` checks only that a state exists. A present
state is never a finding, whatever its value. This is documented as intended at
`docs/CONFIGURATION_INTEGRITY_ANALYSIS.md:129` and pinned by
`tests/test_configuration_integrity_analysis.py:210-216`. HAAC-E002: the dead
target `light.office_tv_lights` passed the global scan with zero findings.

Design:

- New finding type `unavailable_entity_reference`, added to `FINDING_TYPES`
  (`integrity/models.py:13-19`) and to the `finding_types` literal
  (`tools/analysis.py:280-288`). This is an additive change to the input
  schema. Default `finding_types=[]` means "all", so existing callers will see
  the new findings. That behavior change is the fix.
- Precedence: disabled registry entry (existing), then state present and
  `unavailable` or `unknown` (new), then present and available (no finding),
  then the existing registry-only and missing rules.
- Severity: `unavailable` is `medium`; `unknown` is `low` (many entities report
  `unknown` legitimately before their first value). The existing rule lowers
  severity when the source automation is disabled.
- Evidence: the state value and `last_changed`. `last_changed` is already in
  the `GET /states` payload. `integrity/provider.py:261-283` projects it away
  today and will keep it as a bounded string. No new Core read.
- `entity_dependency_analysis` gains `target_availability`
  (`available`/`unavailable`/`unknown`/`absent`, plus `last_changed`) for the
  analysed entity.
- The integrity documentation and the pinning test change deliberately: a
  present available state is still not a finding; a present unavailable one
  now is.

### 3.2 Name every bound overrun (HAAC-E002a)

Today the dependency build knows the source ids but reports a count only:
`dependency/provider.py:1656-1661` computes them and `:1730-1738` emits
"N automation configuration(s) exceeded dependency-analysis coverage bounds".
`DynamicReference` drops `reason_code` (`dependency/models.py:359-374`);
`helper_dependency.py:1105-1106` reports reason codes and a count; the
`entity_dependency_analysis` dynamic list is capped at 10
(`dependency/service.py:169`) and warnings at 20 (`:262`).

Design:

- A bounded `bounded_sources` list on the coverage output of
  `configuration_integrity_analysis`, `entity_dependency_analysis` and the
  helper analysis: `{source_type, source_id, source_entity_id, reason_code,
  bound, bound_value}`. At most 50 entries, then `bounded_sources_omitted`.
  The order is deterministic.
- `bound` names the limit from a fixed table, for example
  `configuration_obligation_limit_exceeded` maps to
  `MAX_DOCUMENT_OBLIGATIONS = 2000` (`dependency/extraction.py:91`), and the
  context and template limits at `:73-94` map likewise. Unknown reason codes
  map to `bound: "unclassified"` and never fail open.
- `DynamicReference` keeps `reason_code`.
- In `entity_dependency_analysis` and the helper analysis, every over-bound
  source is listed **whatever the target**. Any of them could reference the
  target, so the output says the analysis could not rule them out. This is
  the F066 case (lighting-mode helper, 2026-10-26): today the hidden writer is
  anonymous.
- Identifiers pass the existing registry-backed display allowlist and the final
  sanitizer pass, like every other source id in these outputs.

## 4. HAMCP-228: baseline compare and resume

### 4.1 Storage choice (decision S, section 8)

Today `audit_baseline/capture_service.py` keeps at most 2 captures
(`MAX_SNAPSHOTS`) for 600 seconds (`SNAPSHOT_TTL`), in process memory only,
bound to the caller by an HMAC cursor. The audit compares captures days apart
(2026-10-21 and later), so a comparator over that store could compare only two
captures taken within ten minutes. `docs/AUTOMATION_AUDIT_BASELINE.md:294-296`
already names a durable store as an open prerequisite and lists what it must
define.

- **S1: keep the memory store.** Compare against fingerprints the caller
  supplies (section 4.2) for cross-cycle work. Raise the time-to-live for
  resume only. Smallest change, but "compare two captures" works only inside
  the window.
- **S2 (recommended): a durable bounded baseline store**,
  `/data/audit_baselines/`, with the same storage discipline as section 2.4,
  answering each point in the prerequisite list:
  - contents: only the validated baseline artifact (identities, digests,
    statuses, outcomes; no configuration bodies, as today) and its
    `artifact_sha256`, verified on every load;
  - bounds: 12 baselines, 2 MiB each (the existing `SNAPSHOT_BYTES`), 180-day
    age expiry; refuse at capacity (no eviction), reporting the oldest
    capture's age;
  - ownership: each baseline records its creating caller; reads, compare and
    resume require the same caller, matching today's cursor rule;
  - concurrency: one capture at a time (the existing `active` flag), and a
    store lock for writes;
  - cancellation: a capture that does not finish writes nothing;
  - cleanup: an age sweep on each capture and list; quarantine on a digest
    mismatch.

  Pagination of a fresh capture keeps working as today; it reads the stored
  copy instead of the memory copy.

### 4.2 `compare_automation_baselines`

A new read tool exposes the existing offline comparator
(`audit_baseline/comparison.py` `compare_baselines`, `:123`, and
`render_bounded_report`, `:301`). It performs **no Home Assistant read**.

- Inputs: `earlier_capture_id`, plus either `later_capture_id` or
  `recorded_fingerprints`: a list (at most 1,000) of
  `{configuration_id, fingerprint}` with `fingerprint_model`.
- Output: the existing classifications (`UNCHANGED`, `CHANGED`, `ADDED`,
  `REMOVED`, `UNKNOWN`) with reasons, plus `PREFIX_MATCH` (below). As built,
  every record is paged with a bound continuation cursor instead of an
  omitted-detail cut (HAMCP-226/228 review 1, M2).
- Recorded fingerprints may be prefixes of at least 16 hex characters (the
  register records 16). As built, a prefix match is its own `PREFIX_MATCH`
  classification with reason `prefix_match:<n>` and its own count, never
  `UNCHANGED` and never a full-digest match (architect direction, Josh
  agrees; this supersedes the earlier `UNCHANGED (prefix_match: 16)` draft). A
  `fingerprint_model` that differs from the baseline's
  (`automation-audit-config-json-python-v1`) makes every record `UNKNOWN`
  with reason `fingerprint_model_mismatch`.
- The loaders are file-only today (`validation.py:1082`). The store loader
  reuses the same validation (`_new_baseline`, `:474`), so stored and offline
  artifacts are validated identically.

### 4.3 Dedicated `timed_out` status and resume

Today scheduling stops 10 seconds before `COLLECTION_SECONDS = 45`
(`capture_provider.py:184-186`). Unscheduled records keep
`configuration.status: "omitted"`, and only `record_outcomes` says
`timeout`. HAAC-E014: 38 and 42 of 113 records timed out on 2026-10-21.

- New `configuration.status: "timed_out"` for a record that was not attempted
  before the bound. `"omitted"` keeps its other meanings. A read that started
  and exceeded `READ_SECONDS` stays `unreadable` with outcome `timeout`. Resume
  retries both kinds; section 8 decision T asks whether it should also retry
  other `unreadable` outcomes (recommended: no).
- The validator accepts `{readable, partial, unreadable, omitted}` only
  (`validation.py:403`). Older offline validators would reject a new status,
  so this is a **schema change**: `automation-audit-baseline-v2`. The v2
  validator still accepts v1 and the legacy schema unchanged. The comparator
  treats `timed_out` as `UNKNOWN` with reason `timed_out`.
- Persisted-fixture rule: the v1 compatibility fixture is generated by the
  shipped beta.37 capture code through the disposable harness, with
  provenance. It is not written by hand.
- `capture_automation_baseline` gains an optional `resume_capture_id`
  (an additive input change):
  1. Load the base capture (same caller, digest verified).
  2. Re-establish authority and re-read the inventory (`GET /states` and the
     entity registry, as a full capture does). If the Core authority
     generation, the installation identity or the automation inventory
     differs, refuse with `resume_inventory_changed` and recommend a full
     capture.
  3. Re-collect only `timed_out` records (plus `unreadable` with outcome
     `timeout`) under the same bounds.
  4. Write a **new** baseline: base records unchanged with their original
     `collected_at`, re-collected records with new times. The header records
     `resumed_from` and the resumed count. The non-atomic window disclosure
     spans base and resume. The base capture is never changed.
  Records that time out again stay `timed_out`; the caller may resume again.

## 5. Registration and compatibility

| Surface | Change |
| --- | --- |
| Static tools | HAMCP-225 PR: 68 to 72 (`capture_automation_trace`, `list_trace_snapshots`, `get_trace_snapshot`, `set_automation_trace_watch`). HAMCP-226 + 228 PR: 72 to 73 (`compare_automation_baselines`). About 29 tests assert 68 static and 96 total. |
| Input schemas | `get_automation_trace` unchanged. Additive: `finding_types` value, `resume_capture_id`. New tools add new schemas. |
| Output | `get_automation_trace` data keeps its shape; completeness can now be `partial`. New fields: `bounded_sources`, `target_availability`, `last_changed`, `timed_out`. |
| Persisted formats | New: trace snapshot store and baseline store. Changed: baseline schema v2 (v1 still read). |
| Core | No new capability, no signing, no new command shape. |
| `ha-mcp` | Untouched. No delegated provider and no fallback. |
| Stable v1 | Untouched. |

Registration touches, per tool: `tools/registry.py`, `capabilities.py`
(catalog and risk), `providers/models.py`, `providers/routing.py` (name sets
and the direct-HA read policy for the trace capture), and
`ha_core_readmission/routes.py` (`STATIC_TOOL_CORE_REQUIREMENTS` or
`CORE_INDEPENDENT_STATIC_TOOLS`).

## 6. Security

- **No Home Assistant write.** New writes are Engineering-local (`/data`)
  only and carry the existing `engineering_evidence_write` risk. No service
  call, reload, restart or deletion becomes reachable. Negative tests assert
  that each new tool sends only the admitted command shapes:
  `trace/list` and `trace/get` with `domain: "automation"`, and no
  `domain: "script"`.
- **Sanitization.** O2 is implemented by sections 2.1 and 2.3. Synthetic
  needles (`tests/hamcp162_synthetic.py`) are planted in every trace field
  (`config`, variables, `context`, `error`, step results and `note`). They are
  checked against the stored file, every page of the read tool, the
  `get_automation_trace` response and the audit row. Controls without the
  needle show that each planted path reaches the output.
- **Storage.** Symlink refusal, `0600`, size checks before the write,
  digest-checked loads, quarantine, and no payload in diagnostics. Caller
  binding for reads, cursors, compare and resume.
- **Watch (W1).** The only new unattended network activity: bounded, read-only,
  admitted-capability polling that pauses when authority is unavailable.
  Watch creation is attributed and audited. It cannot widen the command set.
- **Partial truth.** Redaction, truncation, size refusal, gaps and missed runs
  are reported, never shown as complete.

## 7. Tests and mutations (Part 2)

- 225: success capture by run id and by selection; each outcome
  (`trace_not_retained`, `run_in_progress`, `already_captured`,
  `over_size_bound`, `store_full`); expiry; quarantine; symlink refusal;
  caller binding; cursor tampering; Core unavailable refusal; the needle
  matrix; the `SANITIZER_SITES` inventory; with W1, poller cadence, mode
  filters, pause and gap count, watch expiry, quota eviction (E2), and a manual
  snapshot surviving a full watch quota.
- 226: one finding per state (`unavailable`, `unknown`, available, absent,
  disabled) and precedence; the HAAC-E002 shape; named bound sources for each
  reason code; the `unclassified` mapping; bound omission at 50; target-
  independent listing in the dependency and helper analyses.
- 228: compare stored against stored, against full fingerprints, against
  16-character prefixes, and with a model mismatch; `timed_out` emitted by the
  real scheduler bound; resume success; resume refused on inventory,
  authority and caller changes; v1 fixture still loads; base capture
  unchanged after resume.
- Mutation targets: the sanitizer call before the write, the partial flag,
  the domain literal, prefix-versus-full reporting, the resume record filter,
  the inventory fence, the eviction quota boundary and the caller check.
- Final validation: Evidence tier only.

## 8. Decisions for Josh

Every recommendation below was accepted by Josh on 2026-10-26 (see the decision
block at the top of this note).

| ID | Question | Options | Recommendation (accepted) |
| --- | --- | --- | --- |
| W | Bounded trigger for HAMCP-225 | W0 on-demand only; W1 bounded poller via a tool; W2 via add-on options | **W1.** Without it HAAC-E001 recurs whenever no one calls capture within about five minutes. |
| E | Trace store at capacity | E1 refuse; E2 oldest-first inside each watch's quota, manual never evicted; E3 global oldest-first | **E1 for manual, plus E2 if W1.** E2 is new eviction behavior. |
| R | Reading a stored, already-redacted snapshot | R1 strict re-scan (withheld strings stay withheld whole); R2 extend the D11 marker tolerance | **R1.** HAMCP-207 can restore fidelity later. |
| S | HAMCP-228 baseline storage | S1 memory store plus caller fingerprints; S2 durable bounded store | **S2.** The audit compares captures days apart. |
| T | Which records resume retries | `timed_out` and read timeouts only; or all `unreadable` | **Timeouts only.** Other failures are not transient by evidence. |
| B | Bounds | Section 2.4 and 4.1 values | As proposed. |
| P | PR split | One PR (one beta); or PR-A = 225 (sanitizer, store, watch), PR-B = 226 plus 228 | **Two PRs, two consecutive betas.** PR-A carries the storage, poller and sanitizer security review on its own; PR-B is analysis and baseline work. |

## 9. Limitations and non-goals

- Script traces (O1): November signing batch.
- The rest of HAMCP-194: stays in its backlog.
- A resolved `!secret` value in an ordinary field is not detectable (section
  2.1); this is the same as `get_automation_config` today.
- Traces Core stopped retaining before a capture or watch existed cannot be
  recovered.
- No change to `COLLECTION_SECONDS`, `READ_SECONDS` or capture concurrency.
  Resume addresses HAAC-E014 without widening the bound.

## 10. HAMCP-225 implementation note (Part 2, PR A)

What the HAMCP-225 pull request built, and where it refines sections 2.1 to
2.5. Bounds are the section 2.4 and 2.5 values (decision B) and live in
`trace_snapshots/contracts.py`.

- **Modules.** `trace_snapshots/contracts.py` (closed input checks, bounds,
  outcomes), `store.py` (namespace, index, records, quarantine), `service.py`
  (capture, list, get, watch), `poller.py` (W1), `runtime.py` (composition;
  no import-time I/O), and `tools/trace_snapshots.py` (the four tools).
  `application.py` configures the runtime and starts and stops the poller
  with the server lifespan.
- **Store location.** `<governance_path>/trace-snapshots-v1/` with
  `index.json`, `records/` and `quarantine/`, and a nonblocking `flock`
  (a second owner is refused, not queued). A corrupt or over-bound index
  fails closed (`store_invalid`); a record whose SHA-256 does not match is
  quarantined. A write whose commit cannot be confirmed sets
  `commit_unknown` and refuses further store writes until restart.
- **`get_automation_trace` (2.1).** The output passes
  `sanitize_trace_detail` once (one exit). Withheld or shortened content
  makes completeness `partial` with a fixed warning naming the categories.
  The tool description gains one sanitization disclosure; the input schema
  is unchanged. Since review 1 (H1) the screen is the shared one (see
  "Review 1 corrections" below); a private key with its
  `-----BEGIN ... PRIVATE KEY-----` header is withheld whole as credential
  text. A header-less key body is not detected, and the trace/list header
  path keeps the HAMCP-191 sanitizer only.
- **Read tools.** `list_trace_snapshots` and `get_trace_snapshot` are open to
  any authenticated caller, like every other Engineering read. Their cursors
  are HMAC-bound to the caller identity with a per-process key, so a cursor
  is refused for another caller and ends at a server restart. Pages are at
  most 40,000 bytes of sanitized content, inside the 60,000-byte response
  bound. Read-back re-sanitizes the whole page envelope (R1).
- **Watch tool (W1 addition).** `set_automation_trace_watch(action,
  automation_id, mode, duration_hours)`: no plan, no approval. `add` and
  `remove` write a `trace_watch_change` audit row with the caller; the write
  is required, so an unavailable audit refuses the change. Every response,
  including refusals, returns the current list. Watch captures write
  `trace_watch_capture` rows (snapshot ids and evicted ids) and expiry writes
  `trace_watch_expired`. A removed watch's snapshots stay until their age
  expiry.
- **Poller.** Every 30 seconds (a 20-second per-tick bound), one `trace/list`
  per active watch, then `trace/get` for new stopped runs matching the mode.
  It holds its own Core authority lease (`caller_id: trace_watch`) and never
  borrows a caller's. It pauses while authority is unavailable or the
  version is not admitted, and counts missed runs as a gap (no backfill). It
  is the only periodic Core read Engineering makes. Server health output is
  unchanged.
- **Authority drift.** If the admitted Core authority changes while a
  capture (manual) is reading, the capture is refused with `authority_drift`
  before anything is stored.
- **Core routes.** `capture_automation_trace` requires
  `core.automation_trace_read`. The other three tools are
  `CORE_INDEPENDENT_LOCAL_STATE`; the watch tool is classed this way because
  the tool itself makes no Core call (the poller is separately gated).
- **Catalog.** Static tools 68 to 72; catalog 96 to 100 (101 with the YAML
  reader). Four `BETA_NATIVE_CAPABILITIES` and four provider-matrix entries.
- **Tests.** `tests/test_hamcp225_trace_snapshots.py` (section 7 matrix,
  needle planting in every trace field, negative command-shape checks) and a
  new `SANITIZER_SITES` entry in `tests/test_hamcp191_shared_sanitizer.py`.
  Historical descriptor baselines strip only the reviewed
  `get_automation_trace` disclosure text before comparing.

### Review 1 corrections (PR #315)

The independent review of `e2f981f1` found H1 to H3 and M1 to M3. The
architect's direction (owner approved) and what changed:

- **H1, one shared screen.** `sanitize_trace_detail` first applies
  `credentials.screen(..., labelled=True)`, the pipeline HAMCP-132 and
  HAMCP-224 use, to every complete original string and every dictionary key
  at every depth; then the HAMCP-191 strict sanitizer; truncation last. A
  flagged string becomes its marker whole; a flagged key drops its entry, key
  and value; containers deeper than 64 levels become the unverifiable marker.
  The earlier PEM regex is gone. Capture screens before anything is written;
  read-back re-screens (R1 strict). `fetch_normalized_trace_list` is shared
  with the reliability analysis and keeps the HAMCP-191 sanitizer only. The
  HAMCP-191 `SANITIZER_SITES` inventory is unchanged (no new call site).
  Tests: `tests/test_hamcp225_cross_path_credentials.py` runs every
  HAMCP-132 and HAMCP-224 reviewer credential case through both paths.
- **H2, identifiers.** An automation or run ID the same screen flags is
  replaced by `withheld_` plus 32 hex of an HMAC keyed by the process secret,
  in the index, records, watch list, audit rows, results and refusals. The
  raw automation ID is held only in memory to poll Core. The reserved form
  is refused as input. After a restart such a watch ends with an
  `identifier_unavailable` audit row. The gateway audit records a withheld ID
  as `null` with `identifiers_withheld: true`. (Correction batch 2 below
  narrows which identifiers the screen flags.)
- **H3, bounded quarantine.** A damaged record is deleted; the index keeps
  at most `QUARANTINE_MAX = 20` content-free stubs (identity, observed
  length, digest), aged on the 90-day rule; the oldest is dropped and
  counted.
- **M1.** The first poll captures runs that finished after the watch was
  created; earlier runs are history.
- **M2.** Each watch gets an equal share of the time left in the tick, and
  the first watch rotates every tick.
- **M3.** Before a watch's reads are committed, the tick's lease and exact
  Core generation are re-checked; after retirement nothing is stored, the
  rest of the tick is skipped and the poller pauses.

### Correction batch 2 (PR #315)

The delta review of `d5e28a29` found two Medium issues. Josh's decision and
what changed:

- **R1, identifier mode (owner decision).** Home Assistant's own identifiers
  and names use `credentials.screen`'s default mode (`labelled=False`), as in
  HAMCP-226; trace values keep the labelled mode. In `screen_trace_value` a
  string directly under one of `IDENTITY_KEYS` (`automation_id`,
  `script_id`, `item_id`, `run_id`, `id`, `alias`, `entity_id`,
  `friendly_name`), or a string in a list under one, is screened in default
  mode; every other string and every dictionary key stays labelled. Because
  the decision is made by the shared screen, it holds at every pass: capture,
  the whole-record pass, read-back and the identifier decision
  (`identifier_cleared`, which screens `{"automation_id": value}`) used for
  the index, records, watch list, audit rows, refusals and the gateway audit.
  `rotate_api_token`, `run-token-1` and the alias `Token refresh` are shown;
  a credential URL, `password=` form or known secret in an identifier or name
  is still withheld (as a keyed digest for an automation or run ID). The same
  text in a non-identity value (for example a variable) is still withheld.
  Residual: default mode does not flag a PEM block or a bare credential label
  in an identity field. The strict sanitizer still redacts the value after a
  `Bearer`, `token:` or `api_key=` label; a PEM block in an `alias`, `id`,
  `entity_id` or `friendly_name` is shown.
- **R2, authority under the store lock.** The pre-lock check stays. In
  addition, `_commit` asks again after it holds the service lock and the
  store `flock`, immediately before its first write. The store worker thread
  asks the event loop (which owns the Core runtime) through
  `call_soon_threadsafe` with the caller's context and waits at most 5
  seconds; no answer, an exception or a non-`True` answer is a refusal.
  Refused: the commit writes nothing (no record, index, watch state or
  eviction) and releases the lock. A manual capture is then refused with
  `authority_drift` and audits nothing; a watch commit, including the
  audit-failure fallback commit, pauses the poller, and the unconsumed run is
  read again once authority returns. The remaining watches in that tick are
  counted as skipped polls, as before.
