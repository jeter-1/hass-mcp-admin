# Engineering 2.2.0-rc.6 acceptance

RC6 carries the independently reviewed RC5 recovery/approval, bounded-response
and integrity-finding corrections, including resolved review finding
RC5-REVIEW-1. Final materialized source must declare `2.2.0-rc.6` in all three
authoritative locations, consume `.release/next-version`, and resolve this
document exactly. Stable remains 1.1.2. Source validation and release delivery
do not establish publication, deployment or live incident resolution.

## Source, review and release authority

Published RC5 / complete-PR release-validation base:
`10a84027f2ef32763aa1f1eb5e44f72227e95c44`.
Independently reviewed remediation head:
`aa28dbcfa28e8814e7eb82cf61d172ebee8fa26b`.
Reviewed tree: `508aa787aa13a92822bdef663fc04a69562dd9f0`.

Record the final RC6 head/tree and both comparisons: published RC5 to final
candidate, and reviewed remediation to final candidate. Keep the reviewed
runtime files, behavioral tests and
[recovery contract](GOVERNED_EXECUTION_RECOVERY.md) byte-identical. Release
preparation changes only version declarations, current README references,
version-specific documents and necessary release-test expectations.

Use `python scripts/codex-context.py --format json`. Require exact staged RC6
acceptance and release-note resolution before the promotion preview and local
materialization; afterward require exact active resolution, three matching
versions and consumption of the declaration. Validate the RC5-to-RC6 transition
against the published RC5 base. Preserve prior failed unchanged-RC5 metadata
records; do not change the comparison baseline or gate rules to obtain a pass.

The completed independent review of the remediation and focused rereview of
RC5-REVIEW-1 are source evidence. The latter reproduced the independent task
receipt test against the corrected head (2 passing tests), ran the three
remediation modules (28 passing tests), and found no new actionable delta
finding. It inspected the implementer's 3,403-test result with 20 skips; that
historical Evidence passed 14/15 steps, failing only unchanged RC5 metadata.
These results must not be represented as final RC6 validation or live recovery.

## Required corrected behavior

### Recovery and approval ownership

Recovery defers progress projection to an active execution owner. Before any
child has durable dispatch intent, nonterminal projection preserves a valid
approved plan instead of changing it to `verification_required`. An approved
apply overlapping recovery must dispatch at most once. Genuine owner-loss
recovery and terminal-outcome projection remain available. After durable
intent, recovery is readback-only and cannot redispatch.

Retain approval expiry, principal, hash and policy binding, current-state and
Core authority, and durable ownership checks. `verification_required` is not
approval authority. No TTL extension, blind retry or disabled recovery is part
of this correction. Prove the real recovery/executor overlap at the pre-dispatch
Core await using deterministic synthetic providers; do not force household
races. Include no-overlap success, owner loss, invalid/expired/revoked approvals,
terminal parents, cancellation, post-intent recovery and settled resources.

### Bounded JSON and reconciliation receipts

Oversized responses remain valid JSON within the configured character and
UTF-8 bound. Small responses retain their existing representation; lossless
compact JSON precedes explicit partial projections. Serialization after a
persisted action must preserve its classified outcome and available request,
plan/task identities, hashes, provider attribution and dispatch/verification
facts. Omission never means the action did not occur.

RC5-REVIEW-1 is resolved for both flat `get_execution_task` results and nested
apply receipts. At the minimum 1,024-character budget with a 128-character
request ID, the independent observing-task regression retains authoritative
state, `provider_attempt_count`, `dispatched_at` and available verification
status in 999 bytes. The 60,000-character control remains correct. Attempt
accounting is not independently proof of dispatch. Missing, null and zero facts
remain distinct; no false, zero or verified outcome may be inferred.

Completeness metadata discloses omitted details. Use the existing read-only
`get_execution_task` or `get_change_plan` detail retrieval, following supported
sections/cursors through completion for approval disclosures. A primary receipt
is not complete approval evidence or a substitute for authenticated panel
approval. Arbitrary omitted provider payloads/task histories are not promised
complete retrieval, and an inadequately small custom budget can leave a detail
gap. Never repeat a mutation to recover a response. Test boundary sizes,
Unicode, success/failure, completed synthetic actions, task states, truthful
null/zero facts, nested receipts and repeated reconciliation without mutation.

### Integrity findings

Low RC5-OBS-1 coalesces identical findings before analysis caps, totals and
pagination. Equality includes finding fields and evidence; differing sources,
paths or evidence remain distinct. Raw dynamic-reference counts remain separate
from visible finding counts. Preserve deterministic ordering, page continuation,
incomplete coverage and manual-review disclosures. Repeated IDs alone must not
discard differing evidence.

## Retained compatibility and validation

Engineering remains the unified client-facing Nabu Casa endpoint. Suitable
reviewed ha-mcp capabilities remain internal providers; no provider unavailability
grants fallback authority. The exact reviewed ha-mcp 8.4.3 pairing and compiled
Core contract, including 2026.9.0 and 2026.9.1, remain unchanged. The healthy
pairing requires all 17 Core capabilities and 51 static plus 25 delegated tools,
76 total. `ha_get_operation_status` remains held, absent from ordinary registration.

Public tool schemas, registration, routing/admission, permissions, persistence
formats, dependencies, workflows and stable-v1 remain unchanged. Preserve
[RC5's identifier contract](V2_2_0_RC5_ACCEPTANCE.md), earlier display/reconciliation
corrections, raw REST validation, capability-local failures and timeouts,
WebSocket replacement and authority cleanup. Shared builds retain manager-owned
authority, per-read revalidation, source/post-lock fences, and the 300-second
cooperative deadline. Soft/hard evidence TTLs remain 600/3600 seconds.
Already-dispatched reads may finish after cancellation; no remote-abort guarantee
is established. Failed recurring reconciliation can take its existing
300-second interval plus probe time to recover; this is not a global rate limit.

| Evidence stage | Required result |
| --- | --- |
| Preserved implementation review | Exact reviewed head/tree, correction hashes, original review and focused finding closure. |
| Final local RC6 candidate | Clean committed head, legitimate materialization, exact documents and all applicable Evidence steps passing against published RC5. |
| Release-delta review | Bounded independent assessment of release-only scope, versions, documentation, preservation, validation and security; prior implementation review remains separate. |
| Candidate CI/build | Exact run/attempt, tested checkout and conclusions, skips and artifacts; bind synthetic merge parents/tree. Old CI is historical. |
| Publication and installation | Separately authorized published source, OCI index/platform/configuration digests, provenance and installed clean-build identity. |
| Live acceptance/recovery | Fresh observations and separately approved operations, with authoritative verification and truthful recovery status. |

Run release/context/materialization tests and existing remediation regressions,
then complete Evidence on the clean final committed candidate. Full discovery
must retain earlier Core, dependency, fence, held-read, routing and governance
tests. Record commands, interpreter, counts, skips, failures and tested SHA.
Keep forced faults, oversized synthetic responses and race stress disposable.

## Operational stop and corrected-build acceptance

The helper `input_boolean.mcp_beta22_smoke_flag` was last recorded **ON**.
This document establishes no fresh state. The failed RC5 restoration remains
failed, and dashboard canaries remain paused. Preserve the failed execution,
reproductions and original receipts. Prior RC5 smoke, refresh, trace, catalog
and installed-image evidence do not prove RC6 acceptance.

After separately authorized publication/deployment and bounded read-only access:

1. Read fresh Engineering, Core, Supervisor, HAOS and ha-mcp identities through
   the existing Engineering connector. Core was last reported as 2026.9.1;
   resolve drift before dependent work. Bind the running RC6 container/image to
   its published index, architecture manifest and configuration digest, source,
   timestamp and clean labels. Version text alone is insufficient.
2. Capture one fresh complete paginated public MCP catalog through the same
   endpoint, preserving raw descriptors and cursor continuity. Compare against
   exact RC6 source/dependencies. Require 76 unique tools, matching schemas,
   no missing/duplicate pages and no ordinary operation-status tool. Missing
   supported capture/image facilities remains BLOCKED.
3. Verify REST/WebSocket identity agreement, all 17 Core capabilities, exact
   provider admission, dashboard authority, storage/audit and F3 readiness.
   Read current helper state and reconcile failed plan/task history without
   applying it. Account for active tasks/claims/locks and baseline projection
   failures. Distinguish old failures from new ones.
4. Exercise bounded useful reads, including the five device/effective-area
   dependent delegated reads and unrelated entity/state, services, automation,
   existing traces and dashboard configuration. Preserve attribution,
   completeness and zero fallback. Do not trigger an automation for evidence.
5. Prove natural dependency refresh after the initiating request returns:
   record generation/build time, TTLs/expiry and settled resources; age past
   soft expiry below hard expiry; issue one ordinary `refresh_index=false`
   query; observe health without repeated queries starting retries; require a
   later completed build and fresh replacement evidence. Preserve source fences
   and explicit stale disclosure. An unchanged fingerprint is permissible.
6. Require settled authority/resources, no new exhaustion/fallback and an
   unrelated successful read. Stop dependent mutations for identity drift,
   lost authority, uncertain dispatch, unverified outcomes or persistent
   unexplained retention. Continue unaffected authorized read-only investigation.

## Later helper restoration and remaining gates

Only after corrected-build identity, authority and current-state reconciliation,
obtain separate concrete authorization for restoration if it is still required.
Inspect consumers and disclose incomplete consequence coverage. If the helper
is already OFF, record that current condition without toggling it to recreate
the incident.

If still ON and restoration is authorized, create a **fresh exact OFF plan** for
`input_boolean.mcp_beta22_smoke_flag`. Record the new plan/hash and request its
authenticated panel approval; `approve_change_plan` requests approval and does
not grant it. After Josh approves, apply once, inspect the execution task, and
independently read back OFF. Require reconciliation facts and settled execution
resources. Never reuse the failed restoration plan or its approval.

If dispatch, outcome or response completeness is uncertain, inspect existing
task/plan details and authoritative state before further mutation. Do not
blindly repeat apply, infer dispatch solely from an attempt count, or overwrite
unexpected external changes. Failed verification/restoration keeps canaries
paused. Offline success does not erase the RC5 failure receipt.

Dashboard canaries may resume only after restoration/current-state reconciliation
and execution integrity are verified, with separate exact target, patch, complete
pre-state/hashes, no-concurrent-edit window, authenticated approvals, one apply,
readback/rendering and governed reverse-plan restoration. They remain non-atomic
against external editors. Held-read, helper, notification/Android navigation,
backup and restart tests each retain their bounded authorization and approvals.
Restart requires the exact component/count, verified recovery and no unfinished
mutation; check fresh identity, catalog, storage/F3, authority, prewarm and reads.

Any backup/recovery plan must identify selected contents, Engineering/ha-mcp and
external database data, completion/size/time, off-host availability, emergency
kit without key disclosure, and access surviving connector failure. Account for
restored Core/configuration/database compatibility and overwrite consequences.
No additional Core update or stable promotion is requested. A future Core update
requires exact-target review, backup/recovery and separate authorization.

Published RC5 remains immutable and contains the known recovery/response defects;
returning to it does not resolve them. Older artifacts and stable-v1 are not a
complete household recovery procedure. Before merge, leave the draft unused or
revert only release preparation while preserving implementation and evidence.

Report PASS, FAIL, BLOCKED or NOT RUN separately for source, CI/build, installed
identity, catalog, reads/refresh, restoration, canaries, navigation and restart.
Record UTC time, sanitized arguments, request/plan/task IDs and hashes, actual
provider, completeness, authoritative readback and recovery status. Leave the
PR draft for Josh. His later Ready action authorizes the repository's protected
merge/publication sequence; deployment and live recovery remain separate.
