# HAMCP-225, HAMCP-226, HAMCP-228 Audit Tooling: Design Note

Registers: HAMCP-225 (trace snapshots, audit HAAC-E001), HAMCP-226
(unavailable references and named bound overruns, HAAC-E002 and E002a),
HAMCP-228 (baseline diff and resume, HAAC-E004 and E014). The register rows
are authoritative. The audit sheet `08 HA Automation Audit - Cycle 2026-10`,
tab `Engineering Gaps`, is background only.

Base: `origin/main` `06a28134f219244f258ab26307c1dfcb9e5d18cb`
(2.4.1-beta.37). This note changes no runtime file. Part 2 implements the
decisions in section 8, in the next free beta.

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

Namespace `/data/trace_snapshots/`. It follows the
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
trigger. That requires Engineering to start Core reads on its own schedule,
which no Engineering feature does today. Options:

- **W0:** on-demand only in this PR; watch becomes a follow-up task.
- **W1 (recommended): bounded poller.** Tool
  `set_automation_trace_watch(automation_id, mode, duration_hours)`, risk
  `engineering_evidence_write`, plus `clear` and list through the same tool.
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
  `REMOVED`, `UNKNOWN`) with reasons, rendered within the response bound with
  the existing omitted-detail count.
- Recorded fingerprints may be prefixes of at least 16 hex characters (the
  register records 16). A prefix match is reported as
  `UNCHANGED (prefix_match: 16)`, never as a full-digest match. A
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
| Static tools | 68 to 72 (`capture_automation_trace`, `list_trace_snapshots`, `get_trace_snapshot`, `compare_automation_baselines`), or 73 with W1 (`set_automation_trace_watch`). About 29 tests assert 68 static and 96 total. |
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

| ID | Question | Options | Recommendation |
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
