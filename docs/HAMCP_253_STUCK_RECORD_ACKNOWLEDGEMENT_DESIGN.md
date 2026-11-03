# HAMCP-253: inspect, then acknowledge stuck governance records

Source paths are relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`.
Line numbers refer to the base `13385ebc` (2.4.1-beta.60).

## Purpose

Health warns about governance records that can never progress: plans that
fail projection verification and execution tasks that ended in
`manual_review_required`. Until now an administrator could not see why one
of those records was stuck (the plan tools refuse projection-failed plans).
They also could not record that they had reviewed it, so the warning stayed
on for good.

This change adds three things:

1. **Inspect.** A bounded, read-only metadata view of one exact record, as
   the MCP tool `inspect_governance_record` and on an Ingress page.
2. **Acknowledge.** A one-step administrator decision on the Ingress
   approval panel. It is stored in a separate append-only ledger and takes
   effect only once its audit event is durably written.
3. **Health.** Health reports unacknowledged, acknowledged-historical and
   acknowledged-current-defect records separately, and warns only on
   unacknowledged ones.

Plans and tasks are never deleted, rewritten or re-validated. Inspection and
the ledger operations make no Home Assistant call and no provider dispatch.
The only Home Assistant call on the acknowledgement path is the Ingress
gate's read-only administrator-role lookup (`config/auth/list`). An
acknowledgement never makes a record executable.

## Part 1: how records get stuck (source map)

### Health categories

- **Plans.** `_build_health_summary` (`governance/service.py` 17494 and
  following) reports `projection_failure_count`, the
  `projection_failure_warning` string (17688-17692; until now the only
  warning string in governance health), and `projection_failures` rows
  `{plan_id, category}`. The rows are bounded to 8, with
  `projection_failures_truncated`.
  - Category vocabulary: `governance/projection_diagnostics.py`, 11 fixed
    categories.
- **Tasks.** `execution_tasks.tasks_manual_review` counts tasks in
  `manual_review_required`.
- **Core restart.** `restart_reconciliation.manual_review_terminalization_count`
  counts plan events and is unrelated to task state. This change leaves it
  unchanged.

### Detection

- **Projection-failed plans.** These are the register's "integrity-failed"
  plans; the two names mean the same category. A plan fails when
  `_projection_failure_for_plan` (967-978) raises one of
  `PLAN_PROJECTION_FAILURE_CODES` (460-467). The approval-bundle checks are
  in `_approval_bundle_integrity_error` (1341-1566).
- **Prohibited plans.** A prohibited plan passes projection only when
  `_is_effectively_prohibited_plan` holds. That requires the event
  allowlist in `_prohibited_plan_has_execution_evidence` (1656-1706) to
  accept every event.
- **Manual-review tasks.** These are tasks in
  `ExecutionTaskState.MANUAL_REVIEW_REQUIRED`, which is a terminal state.
  The F3 runtime or restart reconciliation sets it.

### Read tools before this change

- `get_change_plan` refuses projection-failed plans with the projection
  error.
- `list_change_plans` lists only `{plan_id, error_code}` for them.
- `get_execution_task` returns manual-review tasks normally.

### Live targets (source analysis only, no live access)

- **Plan `4cf748d13c1e452fb510d93c8a4e3a0c`**
  (`set_input_select_option`, prohibited).
  - Prohibited operational plans are created with a family-specific first
    event, `set_input_select_option_plan_created` (5049). The prohibited
    allowlist (1672-1706) accepts only `change_plan_created`. So the plan
    fails projection as `approval_sequence_inconsistent` as soon as it is
    stored.
  - This is likely a **current defect** that affects every prohibited
    contract-v3 operational family with its own creation event.
  - Fixing it would change projection verification, which is out of scope
    (brief D). It is reported as a follow-up.
- **Plans `2ab4764fe8304f429ea3b533f66caa4a` and
  `faba2fbdb10c4da0b33193718636e19c`** (`approval_sequence_inconsistent`).
  - Their cause cannot be derived without the records.
  - Inspection shows plan type, policy class, bundle state and event order
    so that the cause can be read.
- **Tasks `6896e7a1938144b788f29708f27ea20e` and
  `d63e3a87d7834236a585376180388609`** are terminal manual-review tasks.

## Design

### Inspect (`governance/record_inspection.py`)

`inspect_governance_record(record_kind, record_id)` reads the record from
its repository exactly as stored.

- It never resolves the lifecycle, expires, recovers or saves.
- It works on projection-failed plans.
- A stored record that cannot be decoded is reported as `readable: false`
  with `unreadable_reason`. Its fields are never guessed.

The view contains only:

- Identity: kind, ID, plan or task type and family.
- State: status, terminal outcome, and created/updated/expires/completed
  timestamps.
- Policy class and approval state: kind, bundle state, authority version
  and consumed flag.
- Failure: the projection reason code and category, the manual-review
  reason, and the last error category.
- Events: the ordered approval-related event names, and every event row as
  `(event, status, error code)` tokens.
- `unreadable_fields`, `acknowledgeable`, `record_fingerprint`, and the
  current acknowledgement state.

Stored metadata is untrusted (round-1 correction R1-M2):

- The view names the record by the validated requested ID only.
  `stored_identity` is the fixed code `matches_request` or
  `stored_identifier_mismatch`; a stored plan or task ID is never echoed.
- A field with a closed vocabulary (operation, plan status, task state,
  policy class, approval state and kind, error code, projection category)
  shows a member of it or `unrecognized_value`.
- Every other string (event names, family and type tokens, timestamps)
  must match a short token pattern and pass the shared sanitizer with the
  configured sensitive values unchanged; otherwise it is reported as
  `unrecognized_value`.
- Screening happens before the view is fingerprinted, rendered on Ingress
  or counted in health. The same rule covers the HAMCP-097 health rows
  (round-2 correction R2-M1): a stored projection-failure `plan_id` is
  shown only when it is in the 32-hex record format and the shared
  sanitizer, given the configured sensitive values, leaves it unchanged,
  the same screen as the acknowledgement ID lists. Anything else is
  `unrecognized_value`. Counts, order (by stored identifier), the bound of
  eight rows, truncation and categories are unchanged.

Because only allowlisted fields are copied, the view never contains
configuration, targets, notes, principals, approver tokens or free-form
error text.

Events are bounded to 64 rows. `event_count`, `events_truncated` and an
`event_sequence_digest` over all events report truncation. The approval
event sequence is taken from every event, not only the displayed rows, and
has its own bound of 64 with `approval_event_count` and
`approval_events_truncated`.

Inspection never mutates storage (round-1 correction R1-M1). It resolves
the exact record path by name, without reading, rebuilding or writing a
navigation index, and then does a non-quarantining read. A corrupt record
stays in place and reads as unreadable every time. Two files for one ID are
ambiguous and an ID whose file is already quarantined is a storage error;
both are reported as `record_unreadable`. The stuck-record list and the
acknowledgement eligibility checks follow the same rule: with a stale index
they read every record as stored instead of rebuilding the index, and skip
an unreadable one. Normal startup, `get`, and storage quarantine behavior
are unchanged.

`record_fingerprint` is the SHA-256 of the view without the acknowledgement
block. It changes whenever the inspected metadata changes.

### Acknowledgeable records

| Kind | Acknowledgeable when |
|---|---|
| `change_plan` | It fails projection; or its status is `validation_failed`, `failed`, `verification_failed`, `rollback_failed` or `rejected`; or it is terminal and prohibited. |
| `execution_task` | Its state is `manual_review_required`. |

Health tracks only the two warning sources: projection-failed plans and
manual-review tasks. A failed or rejected plan that passes projection never
warned, so it can be acknowledged for the record but does not appear in the
health counts.

### Acknowledge (Ingress only)

Josh decided on 2026-11-03 that acknowledgement is an Ingress page action,
not an MCP tool.

- MCP callers have no Home Assistant user identity: `caller_id` is a hash
  of the client address (`routing.py`).
- The only Home Assistant administrator check in the product is the
  Ingress gate `_require_administrator` (HAMCP-205). On every request it
  makes one read-only Core call, `config/auth/list`, to look up the
  forwarded user's role.
- The Engineering MCP connector therefore exposes no acknowledgement tool.
  An authenticated Home Assistant administrator browser session on Ingress
  can acknowledge. Josh's rule stands: each record is acknowledged
  individually, after inspection, by an administrator.

The routes are on the Ingress approval application:

| Route | Purpose |
|---|---|
| `GET /records` | Lists stuck records by acknowledgement state. |
| `GET /records/{kind}/{record_id}` | The escaped inspection view and, for acknowledgeable records only, a form. The form carries a one-shot CSRF token keyed by administrator principal, kind and ID and bound to the fingerprint. |
| `POST /records/{kind}/{record_id}/acknowledge` | Form-urlencoded only, at most 8192 bytes, a bounded read time and at most 8 fields. It pops the CSRF binding and requires the same principal and the same fingerprint, then calls `acknowledge_governance_record`. |

Inputs are the record kind, the full record ID, the action (`acknowledge` or
`revoke`), the classification (`current_defect` or `historical_record`;
none for revoke), a reason, and the fingerprint from inspection.

**Refusals**, checked in this order. Each refusal returns its reason and
a best-effort audit event, with `audit_recorded` stating whether it was
written.

1. `administrator_required`
2. `record_identifier_invalid`
3. `action_invalid`
4. `classification_invalid`
5. `reason_oversize` (more than 500 characters)
6. `reason_invalid` (empty, surrounding whitespace, or control or format
   characters)
7. `reason_contains_sensitive_value`: the shared sanitizer redacted the
   reason or failed closed. A refused reason is never stored.
8. `record_fingerprint_stale` (malformed fingerprint)
9. `record_kind_mismatch` or `record_not_found`
10. `record_unreadable`
11. `record_not_acknowledgeable`
12. `record_fingerprint_stale` (the record changed since inspection)
13. `audit_unavailable`: the audit log is missing, disabled or has no
    destination, or its destination is known to be unusable: the log or its
    `.lock` file exists but is not a regular file (a directory, for
    example), or cannot be opened for append (a missing parent directory,
    no write permission). Checked before the acknowledgement ledger is
    opened (round-2 correction R2-M2), so the acknowledgement namespace and
    ledger stay byte-unchanged; the check opens the two files as the audit
    logger does and writes nothing to them.
14. `record_not_acknowledged`: revoke when nothing was acknowledged
15. Ledger refusals: `acknowledgement_in_progress`,
    `acknowledgement_persistence_failed` (only when the decision write failed
    before the new ledger replaced the old one, so nothing was stored),
    `acknowledgement_capacity_exceeded`, `acknowledgement_audit_pending`
    (an earlier decision still has no audit event and cannot be completed
    now), and integrity or namespace codes.
16. `audit_write_failed`: the decision's audit event was not written. The
    decision is completed as `abandoned` and never takes effect.

A request rejected before its fields are trusted is audited as
`request_invalid_or_stale` (bad CSRF, body or encoding) or
`request_timeout`.

**Outcomes**

| Status | When | Ledger entry |
|---|---|---|
| `acknowledged` | The record had no effective acknowledgement. | One |
| `reclassified` | The classification or reason changed. History is kept. | One |
| `revoked` | A superseding entry. The warning returns. | One |
| `no_op` | An identical repeat, or a revoke after a revoke. | None |
| `pending` | The decision is or may be stored, and its completion is not known to be durable: the process failed between the steps, or a ledger write failed after the replacement (R2-M3). Not settled; it converges at start or with the next writer. Reported as `acknowledgement_audit_reconciliation_pending`. | Decision; its completion may also be present |

Every outcome reports `record_written: false`, `home_assistant_calls: 0`,
`execution_eligibility_changed: false` and `audit_recorded`.

**Audit coordination (round-1 correction R1-M3).**

- An effective decision (`acknowledged`, `reclassified`, `revoked`) is two
  ledger entries: the decision, then its audit event, then an
  `audit_completion` entry with outcome `recorded`. Only a recorded decision
  counts. The audit event's identifier is derived from the decision hash,
  so writing it again is a no-op in the audit log.
- If the audit write returns anything but one written event or raises, the
  completion is `abandoned` and the request is refused with
  `audit_write_failed`. An abandoned decision never acknowledges or revokes.
  This covers a destination that passed the `audit_unavailable` check and
  failed afterwards (a transient failure). The journal then keeps exactly
  two new entries for the request: the decision and its `audit_completion`
  with outcome `abandoned`. The audit log holds no event for the decision
  and, if the destination recovered, one uncorrelated refusal event.
- A decision with no completion (a crash between the steps) is pending. At
  service start and inside every acknowledgement transaction it is
  reconciled: its audit event is written (or found already written) and the
  completion `recorded` is appended. Until then health reports
  `audit_pending_count`, inspection reports `audit_pending`, and new
  decisions are refused with `acknowledgement_audit_pending`.
- **Persistence uncertainty (round-2 correction R2-M3).** A ledger write
  is a temporary file, `fsync`, `os.replace` and a directory `fsync`.
  `rename(2)` is atomic, so a failure before the replacement leaves the
  ledger unchanged. A failure after it (opening or syncing the directory)
  cannot exclude that the new entry persists: the new ledger is visible but
  not known to be durable, and a crash may keep it or lose it. The request
  is then never reported as refused or settled:

  | Failed write | Before the replacement | After the replacement |
  |---|---|---|
  | Decision | Refused, `acknowledgement_persistence_failed`. The journal is unchanged. | `pending`, with the decision's sequence. The journal keeps the decision without a completion, or loses it in a crash. A kept decision is completed `recorded` at start or by the next writer once its audit event is written; a lost one never existed. |
  | `recorded` completion | `pending`. The journal keeps the decision without a completion; it is completed `recorded` at reconciliation. | `pending`. The journal keeps the decision and its completion, or the decision only. Either way the decision's audit event is already written, so it converges to `recorded`; the completion may already be visible before it is durable. |
  | `abandoned` completion | `pending`. The journal keeps the decision without a completion; reconciliation writes its audit event and completes it `recorded`. | `pending`. The journal keeps the decision and `abandoned` (never effective), or the decision only, which reconciliation completes `recorded` once its audit event is written. |

  Reconciliation writes the decision's audit event under its derived
  identifier, so no path writes a second correlated audit event, and no
  decision is effective without one.
- `audit_recorded` is `true` only when an audit event for the request was
  durably written. A disabled audit log reports success without writing, so
  it is treated as not written.
- **Contract wording correction.** `no_op` and refusal outcomes change no
  ledger state and keep a single best-effort audit event. They are not
  blocked by an audit failure; `audit_recorded` reports truthfully whether
  their event was written. Earlier wording ("each refusal ... writes an
  audit event") is corrected to this.

### Ledger (`governance/record_acknowledgement.py`)

- **Location.** One JSON file at
  `<governance root>/hamcp253-record-acknowledgement-v1/acknowledgements.json`.
- **Format.** Append-only and hash-chained: each event carries its
  sequence, body, the previous hash and its own hash. A body is a
  `decision` or an `audit_completion` that names one earlier decision by
  sequence and audit identifier, exactly once.
- **Reading.** Reads use `O_NOFOLLOW` and refuse a symlinked namespace. Each
  read validates every entry and the chain. A token of (inode, size,
  mtime) memoizes the result.
- **Writing.** Writes use a non-blocking `flock`; a lock already held
  refuses instead of waiting. Each write goes to a temporary file with
  fsync, then `os.replace`, then fsync of the directory.
- **Bounds.** 2048 entries, 8 KiB per entry (enough for any valid
  500-character reason, stored ASCII-escaped) and 4 MiB per file.
- **Hash scope.** The hashes detect accidental damage, not an attacker who
  can rewrite both entries and hashes.

No other code path reads the ledger. Approve, apply, recovery, startup
validation and projection verification are unchanged and never consult it.

### Health

`_apply_acknowledgement_health` runs inside `_health_summary_overlay`,
which already applies per call over a deep copy of the cached build. The
cached build and its generation keys are unchanged.

- **Counts.** `record_acknowledgement` reports, for projection-failed
  plans and manual-review tasks separately, the `unacknowledged`,
  `acknowledged_historical`, `acknowledged_current_defect` and `stale`
  counts, ID lists bounded to 8, and `ids_truncated`. A stored ID that is
  not a screened record ID is never listed: it is counted as unacknowledged
  and in `unrecognized_identifier_count`. Health also reports
  `audit_pending_count` and `audit_abandoned_count`.
- **Warnings.** `projection_failure_warning` and the new
  `manual_review_warning` are present only when unacknowledged records
  exist. A stale acknowledgement counts as unacknowledged.
- **Raw counts.** `projection_failure_count`, `tasks_manual_review` and the
  other raw counts are unchanged.
- **Consistency.** If the acknowledgement view's record totals differ from
  the raw counts of the cached build, the warning stays on and
  `counts_consistent` is false.
- **Failure handling.**
  - If the ledger cannot be read, every record is reported as
    unacknowledged and `ledger_status` reports the code.
  - If the overlay itself fails, it falls back to the warnings computed
    without acknowledgement.
- **Startup validation pending.** Both new fields are history-derived, so
  they are listed in `_HEALTH_OVERLAY_FIELDS`. While historical validation
  is still running, a pending summary reports them as `None` and names them
  in `pending_fields`, like every other history-derived field.

### What does not change (brief D)

- Integrity and projection verification, startup validation, and the
  approve, apply and recovery paths.
- Persisted plan and task formats. The ledger is a new, separate file.
- Existing tool schemas. One read-only tool is added. Static tools go from
  79 to 80. The advertised catalog is 108, or 109 with the optional YAML
  reader.
- The new tool is Core-independent: no signed Core profile, upstream
  admission or Home Assistant request is needed.

## Security

- **New write path.** One append-only file under Engineering's own
  governance storage, reachable only through the Ingress acknowledge route.
  - The route sits behind the per-request Home Assistant administrator
    check and a one-shot CSRF token bound to the principal and the
    fingerprint.
  - There is no MCP write tool. Inspection and the ledger operations make
    no Home Assistant request and no provider dispatch; the gate's
    read-only `config/auth/list` role lookup is the only Core call.
  - An effective decision requires an enabled, writable audit destination
    and is never left silently unaudited (R1-M3).
- **No execution authority.** No approve, apply, recovery or verification
  path reads the ledger. An acknowledged failed plan is refused exactly as
  before.
- **Disclosure.** Inspection uses allowlisted metadata with closed token
  patterns, never echoes a stored identifier, and screens extensible
  metadata with the shared sanitizer and the configured sensitive values
  (R1-M2). Inspection never quarantines or re-indexes (R1-M1). Reasons are screened by the shared sanitizer and refused when
  it redacts. The audit event records the reason's length, never its text.
- **Bounded input.** The reason is at most 500 printable characters. Form
  bodies are at most 8192 bytes and 8 fields. The ledger has an entry cap
  and size bounds.
- **Integrity.** Hash-chain validation on every read. A damaged ledger is
  never trusted: health warns as if nothing were acknowledged, and new
  acknowledgements are refused.

## Follow-ups (not in this change)

- A stored plan whose `plan_id` is not 32 hex characters makes the plan
  navigation rebuild raise `ValueError` (`storage.py`
  `_identifier_signature`), at startup or on index invalidation. This is
  pre-existing and unchanged here; inspection and the HAMCP-253 health rows
  do not echo the value.

- Prohibited contract-v3 operational plans fail projection because the
  prohibited event allowlist does not accept family-specific creation
  events (see plan `4cf748d1…`). The fix changes projection verification
  and needs its own task and review.
