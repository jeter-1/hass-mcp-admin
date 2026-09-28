# Supplementary configuration-task re-verification

This candidate adds `reverify_configuration_task(task_id, expected_plan_hash,
request_id)` to the Engineering endpoint. It records a dated observation of
saved configuration after an earlier configuration verification mismatch.
It does not resend configuration, operate a device, consume approval, reopen an
execution, or change the historical failed outcome. This source contract is not
release, installation, or live-test evidence.

## Eligible history and useful success

Inputs are required: a 32-character lowercase hex task ID, the exact 64-character
lowercase plan SHA-256, and a canonical lowercase UUID request ID. No caller
configuration, target, provider, override or success assertion is accepted.

The initial family is a retained F3 configuration task in terminal
`manual_review_required`, with 1–8 immutable operations. Its original plan,
consumed approval, task identity, child declarations, candidate hashes and complete
lock union must agree. The original approval grant is reconstructed using the
existing approved-copy semantics (consumption legitimately changes its state),
then bound to every declaration. Task and child consumption witnesses must match
the plan, policy, sequence, original request and consumption time. These witnesses
are included in the final source recheck; no approval is renewed or consumed.
Every child must be terminal, have exactly one dispatch,
and be either `succeeded_verified` or `verification_mismatch`; at least one must
have mismatched. Unknown dispatch, missing children, unsupported history,
corruption, unresolved original locks, conflict holds and concurrent Engineering
changes refuse. An old approval's expiry does not prohibit a new observation:
its consumption is checked as history, never reused as dispatch authority.

Useful success requires current signed Core authority, reads of **every** approved
object (including previously successful helper operations), matching resource
identity and semantic candidate configuration, a successful Core configuration
check, unchanged retained source records, valid concurrency ownership, and saved
audit/receipt evidence. The existing reviewed configuration gateway and signed
Core profiles supply those reads; no new upstream capability or fallback is
admitted. A standalone trigger condition's literal `id` remains behaviorally
significant as described in [trigger verification](TRIGGER_CONDITION_VERIFICATION.md).

The receipt status `current_configuration_verified` with `review_resolved=true`
resolves this configuration review only. The original task remains
`manual_review_required`, its original error remains visible, and original plan,
task, approval, child, event and dispatch records are unchanged. Household behavior,
physical outcomes, and later drift are not verified by this operation.

## Evidence, bounds and truthfulness

Collection has a 30-second asynchronous deadline, no waiting for conflicting
locks, at most eight object gateway reads and one configuration-check call.
Core identity reconciliation is separate from those counts. Existing helper reads
may fetch a helper collection to find the exact target; they are not represented
as single-object transport requests. The application attempt counts are not a
wire capture. During this operation only, REST and WebSocket sessions reject
redirects and disable the HTTP connection retry path. REST response bodies and
individual WebSocket frames are limited to 1 MiB. There is no application retry,
fallback, template evaluation or arbitrary forwarding. Existing unrelated
transport behavior is unchanged.

Receipts contain original-history fingerprints, approved and observed per-object
fingerprints, exact target identities, comparison results, configuration-check
status, timestamps, verifier source/version, current Core identity, provider
attribution and explicit zero mutation/approval/redispatch facts. They contain no
raw configuration, validation error body or provider exception text. Invalid-input
audits use only the three allowed argument names and an unknown-fields boolean;
arbitrary unknown names and values are excluded. Partial rows
are retained when available. Timeout/cancellation can interrupt collection before
it can finish a receipt; that attempt remains unresolved. The owned deadline sets
timeout telemetry; ordinary caller cancellation does not impersonate a timeout.
Observations across HA
objects are **non-atomic**. Engineering locks cannot fence an unrelated HA writer.
A stable receipt is not a guarantee of continuous correctness.

Completed requests replay the same dated receipt without any HA access. Reusing a
request ID with another binding refuses. Use a **new** request ID for a fresh
observation. A new attempt's pending, mismatching or incomplete evidence supersedes
the latest successful projection for that task. Refused invalid input does not
alter the previous dated receipt. `get_execution_task` adds `review_resolution`
only when supplementary evidence exists (or its store cannot be validated); it
does not initiate a readback. The projection explicitly reports that continuous
current configuration verification is false. Historical task/health counters
retain their original meanings. If an exact task/plan binding already has a receipt,
a fresh preflight refusal is recorded separately and supersedes that projection,
including after restart. Audit-unavailable refusals explicitly carry no audit ID;
they are never successful observations. Refusals cannot invent a task/plan binding.
Invalid or mismatched input does not supersede a prior receipt.

If a later refusal cannot be persisted, its response reports evidence uncertainty
and the current process withholds a resolved projection. The surviving disk receipt
has not thereby changed: after restart it remains only the original dated evidence,
and cannot prove the unrecorded later attempt. Inspect the storage failure before
claiming durable reconciliation; do not describe process-local invalidation as a
new durable receipt.

Fixed outcomes distinguish retained-evidence integrity failure/read unavailability
from new-evidence persistence failure, plus configuration mismatch, read
unavailability/timeout,
authority unavailable/changed, concurrency conflict, unsupported history,
interrupted read, audit failure, receipt capacity and storage failure. The public
structured envelope describes processing of the request; callers must inspect
its receipt `status`, `review_resolved` and `receipt_persisted`, rather than
interpreting envelope success as configuration verification. If observations
matched but audit/persistence failed, the response retains `observed_result` and
partial evidence with `review_resolved=false` and no successful receipt claim.

## Storage, concurrency and recovery

The optional `configuration-reverification-v1/` namespace is created lazily beneath
the existing F3 persistence root. It uses an append-only sequence of start/finish
events, plus terminal preflight-refusal events, in an atomically replaced,
hash-chained ledger: maximum 512 events (up to 256 complete read requests;
refusal events also consume that shared budget), 256 KiB per event, and 16 MiB
overall. Capacity exhaustion
refuses before a new read. One remaining event slot may record that bound refusal;
with no remaining slot, the response reports evidence uncertainty and the current
process withholds resolution. It does not claim a durable refusal or delete older
evidence to make room. There is no automatic pruning, migration, retention
extension or silent deletion. A future reviewed retention decision is required
before extending that bound. Hashes detect accidental damage, not a disk writer
able to replace both evidence and hashes. The existing trusted persistence
boundary applies; this is not signed human approval.

A nonblocking cross-process receipt lock serializes explicit observations. A start
event records the exact read-owner identity and complete lock request union before
resource locks are acquired. These observation-only locks use a 60-second lease,
longer than the collection deadline. A later explicit call that owns the exclusive
receipt lock can establish that no earlier reader is still active. It may then
release only the exact interrupted read owner's complete matching lock union,
with current generation tokens. Foreign locks, differing ownership, incomplete
unions and conflict holds remain untouched. Recovery records `interrupted_read`
and performs no HA reads. A new request ID is needed to try a fresh observation.
No startup/background recovery or execution-task reconciliation is added.

Audit append precedes receipt commit. Their atomicity is not claimed: an audit
entry says `receipt_commit=not_yet_committed` and carries a deterministic audit ID
also stored in the receipt. If final receipt persistence fails, that audit entry
alone proves no resolution. Failure after the file replacement but before directory
sync reports uncertain commit and prevents further projection/replay through that
store instance. An operator must investigate the disk failure; after a later
process restart, surviving evidence is independently validated before use. Do not
retry configuration writes or delete evidence to recover a read receipt.

Older binaries continue to read the untouched original task/plan formats and
ignore the optional namespace. Preserve that namespace during downgrade; older
code cannot display its supplementary resolution. Binary downgrade does not
replace a consistent backup or establish that other newer formats are compatible.

## Tool and release boundaries

The tool is Engineering-native. Metadata uses `readOnlyHint=false` because it
writes Engineering evidence, `destructiveHint=false`, `idempotentHint=true` for an
exact request binding, and `openWorldHint=false`. HA access is `read_and_validate`;
mutation and action dispatch remain forbidden. No external-human approval,
principal separation or renewed execution permission is implied by this call.

This isolated source candidate adds one static tool: expected healthy inventory
80 = 55 static + 25 delegated. It does not include the separate Alarmo candidate.
This implementation is included in the materialized 2.4.0-beta.7 candidate; source
metadata does not establish publication, deployment or installed acceptance.
Existing descriptors, admission, provider fallback, stable-v1, package construction,
signed registries and workflows are preserved. See the
[beta.7 acceptance contract](V2_4_0_BETA7_ACCEPTANCE.md) for the distinct release,
installation and exact historical-task observation gates.

## Validation and later acceptance

Offline regression fixtures are exact bytes generated by the shipped beta.6 writer,
with writer/generator/record provenance under
`tests/fixtures/configuration_reverification/`. Tests prove positive re-verification,
restart/replay, unchanged original record bytes and one-dispatch history; all-object
mismatch, wrong binding, expired original approval, authority/source drift, lock
conflict, interruption, audit/storage/capacity failure and input redaction. Dedicated
loopback tests prove single attempts, redirect refusal and response bounds. Full
validation and separately tasked security review must cover the final candidate.

A later installed test needs separate deployment/live authorization and a freshly
verified exact task/plan binding. Capture current authority and settlement, execute
one bounded re-verification, read back the supplementary resolution and verify no
new configuration write, approval consumption, execution task or dispatch. Preserve
historical failure counters. No device command, helper-state change, plan/apply,
restoration, restart or manufactured household event is part of this acceptance.
