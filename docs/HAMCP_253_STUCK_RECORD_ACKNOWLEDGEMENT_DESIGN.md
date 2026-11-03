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
   approval panel. It is stored in a separate append-only ledger with an
   audit event.
3. **Health.** Health reports unacknowledged, acknowledged-historical and
   acknowledged-current-defect records separately, and warns only on
   unacknowledged ones.

Plans and tasks are never deleted, rewritten or re-validated. Nothing calls
Home Assistant. An acknowledgement never makes a record executable.

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

Every string value must match a closed token pattern. Anything else is
reported as `unrecognized_value`. Because only allowlisted fields are
copied, the view never contains configuration, targets, notes, principals,
approver tokens or free-form error text.

Events are bounded to 64 rows. `event_count`, `events_truncated` and an
`event_sequence_digest` over all events report truncation. The approval
event sequence is taken from every event, not only the displayed rows, and
has its own bound of 64 with `approval_event_count` and
`approval_events_truncated`.

Inspection reads through a side-effect-free repository read: it never
quarantines a corrupt record and never updates a navigation index, so a
corrupt record stays in place and reads as unreadable every time.

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
  Ingress gate `_require_administrator` (HAMCP-205), which asks Core for the
  forwarded user's role on every request.
- The AI therefore cannot acknowledge a record.

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
writes an audit event.

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
13. `record_not_acknowledged`: revoke when nothing was acknowledged
14. Ledger refusals: `acknowledgement_in_progress`,
    `acknowledgement_persistence_failed`,
    `acknowledgement_capacity_exceeded`, and integrity or namespace codes.

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

Every outcome reports `record_written: false`, `home_assistant_calls: 0` and
`execution_eligibility_changed: false`.

### Ledger (`governance/record_acknowledgement.py`)

- **Location.** One JSON file at
  `<governance root>/hamcp253-record-acknowledgement-v1/acknowledgements.json`.
- **Format.** Append-only and hash-chained: each event carries its
  sequence, body, the previous hash and its own hash.
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
  counts, ID lists bounded to 8, and `ids_truncated`.
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
  - There is no MCP write tool, no Home Assistant request and no provider
    dispatch.
- **No execution authority.** No approve, apply, recovery or verification
  path reads the ledger. An acknowledged failed plan is refused exactly as
  before.
- **Disclosure.** Inspection uses allowlisted metadata with closed token
  patterns. Reasons are screened by the shared sanitizer and refused when
  it redacts. The audit event records the reason's length, never its text.
- **Bounded input.** The reason is at most 500 printable characters. Form
  bodies are at most 8192 bytes and 8 fields. The ledger has an entry cap
  and size bounds.
- **Integrity.** Hash-chain validation on every read. A damaged ledger is
  never trusted: health warns as if nothing were acknowledged, and new
  acknowledgements are refused.

## Follow-ups (not in this change)

- Prohibited contract-v3 operational plans fail projection because the
  prohibited event allowlist does not accept family-specific creation
  events (see plan `4cf748d1…`). The fix changes projection verification
  and needs its own task and review.
