# Engineering 2.2.0-rc.7 acceptance

RC7 carries the independently reviewed RC6-RDY-1/2/3 reporting corrections.
Published RC6 base: `c82e5a2598c204dbc09d5d9e275239faefe30f81`.
Reviewed correction: `943d05e4bf8e10fcf28da3be3aa6d650044cff3a`, tree
`3d47e4ab968c0a4cb69887a1babda073832e6ac5`. Stable-v1 remains 1.1.2.

## Release authority and source preservation

Preserve the reviewed runtime, behavioral tests and behavioral documentation
byte-identically. Record the complete-PR comparison from published RC6 and the
release-only comparison from the reviewed correction to the final candidate.
Only version declarations, current README references, these version-specific
documents and necessary current-release test expectations may change.

Stage exactly `2.2.0-rc.7` plus a newline in `.release/next-version`. Require exact
staged RC7 document resolution, inspect the checked-in promotion-tool preview,
then materialize through that tool after authorization. Require three matching
RC7 declarations, consumed staging and exact active resolution of this document.
Validate the transition against published RC6. Preserve earlier unchanged-RC6
metadata failures and do not alter the validator or comparison to obtain a pass.

## Corrected behavior

- RC6-RDY-1 checks the complete public integrity envelope before retiring its
  snapshot. A page may shrink with `clamp_reason=response_size_limit` and the
  existing signed continuation cursor; its offset includes only delivered
  findings. The essential regression retrieves all 31 findings from one
  snapshot and one provider collection, without gaps or duplicates. Either a
  complete bounded page or a recoverable continuation satisfies this contract;
  the original diagnostic requiring truncation stays historical evidence.
  Preserve first/intermediate/final-page behavior, evidence references, totals,
  ordering, Unicode/UTF-8 limits, query/index/expiry/authenticity fences, coverage
  disclosures and the existing 300-second/16-entry snapshot bounds. A budget
  unable to contain one complete finding and disclosures fails explicitly;
  an existing cursor retains only its original bounded lifetime.
- RC6-RDY-2 identifies `execution_task.verification_summary` as verification
  authority for retained F3-backed plans, using the task reader's parent/child
  projection. The compact summary retains the pointer and target. Preserve
  legacy operational facts and taskless legacy authority. Created, observing,
  verified, failed and manual-review evidence remain distinct. Repeated reads
  must not alter stored plans/tasks/approvals or produce provider mutations.
- RC6-RDY-3 accounts once for entered held-canary transport work and finishes
  timing before response serialization, also on cancellation. Local pre-transport
  refusal records no attempt; refusal/error/timeout after entering transport can
  be an attempt without dispatch. Preserve exact request context, Core cleanup,
  cancellation propagation, and late/duplicate callback refusal. Timing
  settlement does not establish remote abort or permission to retry.

Preserve RC6's recovery/approval ownership, bounded JSON task receipts and
integrity coalescing, plus earlier device identifier, reconciliation, raw REST,
WebSocket, cancellation, dependency-build and source/post-lock fence corrections.
Shared builds retain the 300-second cooperative deadline and 600/3600-second
soft/hard evidence TTLs. No public schema, tool, route, provider, permission,
persistence, dependency, workflow or governed restart capability change belongs
to this release. Engineering remains the unified Nabu Casa endpoint; unavailable
providers grant no fallback authority. Retain exact reviewed ha-mcp 8.4.3 and the
compiled Core contract, including 2026.9.0 and 2026.9.1. The healthy pairing
requires all 17 Core capabilities and 51 static plus 25 delegated tools.
`ha_get_operation_status` remains absent from ordinary registration.

## Distinct validation stages

The reviewed correction has independent source review with no actionable findings,
20 independently passing new tests and three independent same-snapshot traversal
probes. Its full Evidence ran 3,423 tests, 20 skips and no test failures/errors;
14 of 15 steps passed, with only intentionally unchanged RC6 metadata failing.
These are correction-head results, not final RC7 candidate or live evidence.

Run focused release/context/materialization checks and the three reporting
regression modules. Commit the release candidate, then run complete Evidence
against published RC6 on the clean final head. Declare exactly the complete PR's
four changed runtime files, Engineering version.py and config.yaml as protected
validation paths. Require every applicable step, including the legitimate
RC6-to-RC7 metadata transition, to pass. Record commands, interpreter, counts,
skips, results and SHA. Obtain bounded independent release-delta review.

If draft delivery is separately authorized, record exact-head CI run/attempts,
checkout, conclusions, skips and artifacts, including synthetic merge parents
and tree relationship. Do not transfer earlier CI to RC7. Publication/build
provenance, installed-image binding, fresh public catalog and live acceptance
remain separately evidenced and authorized stages.

## Bounded corrected-build acceptance

Completed RC6 acceptance remains valid for that release. Preserve those records
and all earlier failures unchanged; they do not establish RC7 acceptance or
current installation state. No household identifiers, observed states or local
operator evidence paths belong in public release material.

After separately authorized deployment and read-only access:

1. Verify Engineering source/build/dirty identity and actual Core, Supervisor,
   HAOS and ha-mcp versions. Bind the running image architecture/configuration
   digest through its platform manifest to the published RC7 OCI index.
2. Capture a fresh raw initialized MCP session and every public tools/list page
   using the approved private procedure. Require 76 unique complete descriptors,
   exact source/dependency comparison, uninterrupted cursor continuity and no
   ordinary operation-status capability. Bracket with identity/health.
3. Require exact ha-mcp admission, 17 Core capabilities, REST/WebSocket identity
   agreement, dashboard authority, healthy storage/audit and F3 readiness. Record
   settled execution/authority and fallback counters, attributing historical
   values rather than assuming they arose during this acceptance.
4. Exercise bounded existing entity/device/effective-area reads and useful
   unrelated reads, services discovery, automation configuration/existing traces,
   configuration validation, read-only templates and complete dashboard reads.
   Preserve provider attribution, coverage and canonical errors; no forced faults
   or fixture mutations belong to read-only acceptance.
5. Read bounded integrity pages from existing evidence. Follow one snapshot to
   completion where feasible; check totals, evidence, disclosures and cursor
   continuity. Do not manufacture household findings. Keep the synthetic 31-item
   response-budget proof separate if household data does not exercise it.
6. Reconcile existing F3 plans/tasks read-only. Compare the public authority
   pointer with actual task/child verification and preserve legacy values. Do
   not create an action simply to manufacture a verified task.
7. Require natural dependency replacement after the initiating ordinary request
   returns: age a valid snapshot past soft expiry below hard expiry, issue one
   `refresh_index=false` query, observe health without retry-starting queries,
   and require a newer completed build plus fresh replacement evidence. An
   unchanged fingerprint is permissible. Preserve stale/coverage disclosure.
8. Finish with a useful unrelated read, identity/health continuity and settled
   resources. Require zero fallback and no unexplained retained authority,
   execution, locks or holds. A monitoring deadline is not remote cancellation.

Any held-read canary needs separate exact entry/argument authorization. Verify
canonical missing-resource outcome, attempt-versus-dispatch evidence, finished
timing, unchanged registration/admission, settled authority and an unrelated
successful read. Do not create an operation to obtain a positive ID.

Helper/dashboard canaries and any necessary recovery require fresh target/state
and consequence evidence, exact scope authorization, authenticated approval per
plan, one apply, authoritative task/readback and verified restoration. An
already restored helper must not be toggled to recreate an incident. Never reuse
failed approvals/plans or blindly repeat uncertain mutations. Dashboard edits
require complete configuration/hashes and a no-concurrent-edit window; they are
non-atomic against outside editors. Notifications/navigation, backups, restarts
and Core updates retain their own exact authorization and recovery requirements.
This release adds no governed restart capability and requests no Core update.

Report PASS, FAIL, BLOCKED or NOT RUN separately for source, CI/build, image,
public catalog, reads, reporting, natural refresh, canaries/restoration and
settlement. Stop dependent mutations for identity drift, lost authority,
fallback, uncertain dispatch, failed verification/restoration or persistent
unexplained retention; continue unaffected authorized read-only work.

Published RC6 stays immutable and contains the known reporting limitations.
Returning to it reintroduces those limitations; it is not a complete recovery
procedure. Account for compatible Core/configuration/database/backup state and
surviving access. Before delivery, release-only changes may remain unused or be
reverted without rewriting the reviewed correction or its evidence. Josh's
later Ready decision, merge/publication and deployment remain distinct stages
under repository policy. Stable promotion is not authorized by this document.
