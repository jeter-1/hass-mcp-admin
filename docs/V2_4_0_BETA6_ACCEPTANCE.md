# Engineering 2.4.0-beta.6 acceptance

Standalone lock persistence/recovery repair.
Published source base: `34f96a90548f72d36f6f5a9a428920a3a7d14de9` (beta.5).
Implementation review checkpoint: `83d6bdb386b421936637913321bac823409c1d33`.
Bind final release-delta review, Full/Evidence and CI to the actual final PR head;
the checkpoint is not a substitute for that final evidence. Bind publication and
installation separately to the protected merge and produced artifact.

No gate is asserted passed by this document. Preserve earlier acceptance
packages and their qualifications. Deployment, live recovery and household
actions each require separate authorization.

## 1. Final source and delivery gates

All three authoritative version declarations must equal `2.4.0-beta.6`,
`.release/next-version` must be consumed, and `scripts/codex-context.py` must
resolve exactly to this acceptance document and the matching release notes.
Retain the accepted implementation/security review, its corrected-head binding,
and a focused independent release-only review. Confirm that runtime behavior
apart from the approved lock repair is unchanged, including provider admission,
no fallback, approval binding, tool inputs/count and stable-v1.

Run clean final-head Full/Evidence with every changed protected path declared,
using the approved locked test environment. No metadata bypass or reuse of an
earlier head's full receipt is permitted. Record passed/failed/skipped counts.
The existing focused proving command is:

```sh
python -m unittest -v \
  tests.test_f3_tokenless_lock_recovery \
  tests.test_f3_lock_manager \
  tests.test_f3_execution_persistence \
  tests.test_f3_fault_injection \
  tests.test_f3_orphan_child_recovery \
  tests.test_f3_configuration_sequence \
  tests.test_f3_configuration_runtime_invariants \
  tests.test_f3_runtime_integration \
  tests.test_f3_packaging_boundaries
```

Require useful execution and safe failure evidence:

| Case | Required result |
| --- | --- |
| 16, 17, 18 and 256 locks | Full union persisted, one dispatch, exact verification, full settlement; duplicate execution does not dispatch again. |
| Real synthetic configuration sequence | Eight operations produce eighteen locks; configuration adapters succeed with exact readback and complete lock release. |
| 257 locks | Refuse before acquisition, preflight, approval consumption or provider dispatch; never truncate locks. |
| Token validation/I/O failure | Original failure preserved; exact fenced cleanup attempted and its outcome separately reported. |
| Terminal-write and release/audit failures | Test first and second terminal writes, successful/failed cleanup and healthy/failed/throwing audit; preserve the primary error, exact retained generations and zero dispatch. Exclude raw exception text. |
| Process loss | Cover acquisition-before-token-persistence, binding-before-release, release-before-runtime-marker, partial/failed audit, append-before-cursor and cursor-before-final-marker. Restart converges when proof is complete. |
| Concurrent execution and audit | Both actual settlement and failed-acquisition cleanup finish with competing conflict audit; callbacks cannot invert transaction order. Claim replacement, intent and hold promotion still fence cleanup. |
| Historical tokenless child | Generate/capture with the exact shipped beta.5 writer and verify source/fixture provenance. Preserve event history, failed outcome, zero dispatch and original non-executable plan; bind exact tokens and settle without provider calls. |
| Incomplete or conflicting proof | Every owner/task/plan/operation/attempt/key/mode/generation/hash mismatch, partial union, active claim/lease, renewal, later acquisition, hold and concurrent replacement retains records and surfaces the truthful unresolved/retry state. |
| Audit retry and diagnostics | Pending work remains visible after locks disappear; replay exports one effective binding audit entry; failed/cancelled outcome remains unchanged. Bound details and disclose omissions; do not reset historical counters. |
| Old-reader compatibility | Exact beta.5 accepts a sixteen-token record and rejects eighteen/256-token records. Do not claim an unchanged schema number means backward compatibility. |

The fixture generation/provenance contract is in
[`tests/fixtures/f3_lock_recovery/README.md`](../tests/fixtures/f3_lock_recovery/README.md).
Do not manufacture historical serialized records or commit raw production data.

Candidate CI must pass the aggregate `validate` and every required job family,
including existing disposable Core 2026.9.3 / ha-mcp 8.5.0, image/build,
exact-image gateway and add-on lanes. Bind their actual checkout/merge-tree to
the final candidate and base. Existing integration lanes preserve baseline
behavior; they do not independently prove every new synthetic fault case above.
No real publication or household action is needed to test this source repair.
Leave the PR draft for Josh's Ready decision. Ready may authorize protected
merge/publication under repository policy; it never authorizes installation.

## 2. Required preparation before deployment approval

This release changes internal durable state automatically during normal recovery.
Before approving a particular deployment, identify the approved protected merge,
expected version, both platform artifacts, image index/manifest/configuration
chain, build inputs, provenance and SBOM. Preserve a compatible recovery artifact
and the current durable execution/lock state through an explicitly authorized
operator route. No production credentials or records belong in public evidence.

Capture a current, bounded, privacy-preserving receipt for each affected parent,
child, immutable declaration and complete retained lock union. Bind the receipt
to its exact capture time/source, record hashes and current installed identity.
Verify the actual terminal-zero-dispatch classification, absence of provider
attempt/intent/preflight, expired claim, exact plan/hash/child membership,
capability/operation/attempt/owner, union hash, acquisition lifetime and current
generations. Observe unrelated owners/holds and pending work as controls.
A retained incident summary or matching opaque ID is not this proof.

If records are missing, damaged, partial, active, held, renewed or ambiguous,
retain the blocker; do not normalize them, manufacture authority or remove locks.
Do not mutate the old plan to make acceptance possible. Agree a bounded observation
window from actual backlog, claim expiry and retry timing before deployment.

Deployment approval must explicitly include expected automatic token binding,
exact lock settlement and audit completion for records meeting the reviewed proof.
It authorizes no provider command, old-plan replay, approval renewal or household
canary. Global lock deletion, manual storage edits and forced recovery are not
part of this contract. Keep existing household remediation holds intact.

**Recovery limit:** beta.5 cannot read persisted token lists above sixteen.
A binary downgrade alone is insufficient. Preserve the repaired reader and
compatible durable history; restoring an older execution snapshot can erase
newer dispatch evidence. If recovery is needed, stop dependent operations,
preserve the current failure state and obtain the exact recovery decision.
Do not repeatedly restart or blindly reinstall to clear the backlog.

## 3. Installed identity and read continuity

After separate deployment and bounded read authorization, verify:

- Running beta.6 version, exact protected-merge source SHA, build time and clean
  flag. Bind the running platform/configuration digest through verified manifest
  bytes to the published OCI index; labels alone are insufficient.
- Core REST/WebSocket identity agreement at 2026.9.3, exact admitted ha-mcp 8.5.0,
  current accepted/cache-valid signed registry and nineteen compatible Core
  profiles. No new withheld/quarantined authority or fallback.
- Storage/audit health and fixed recovery diagnostics. Capture current task/plan/
  event counts, locks/holds, pending approvals/challenges, applies/rollbacks,
  active Core leases/commits and error/failure counters before interpreting deltas.
  Post-startup observation alone cannot reconstruct a missing pre-deployment receipt.
- One useful native read and one admitted delegated read of an established,
  freshly identified harmless fixture, with truthful provider, completeness,
  truncation and no-fallback facts. Do not operate the fixture.
- Existing successful task/approval/dispatch-history continuity and dependency
  state. Allow normal prewarm; at most one justified `refresh_index=false` request,
  no forced rebuild loop. Preserve partial coverage and historical qualifications.

Retain one separate fresh public `initialize` → `notifications/initialized` →
all paginated `tools/list` session containing zero `tools/call`. Compare all
79 descriptors (54 static + 25 delegated reads) and contemporaneous runtime
brackets. Cached connector schemas and supported-inventory counts do not replace
raw protocol evidence. A stale connector label alone is not an installed defect.
Obtain a timestamped render-only observation of the existing approval panel;
do not create, approve or reject anything for this gate.

## 4. Actual retained-state settlement gate

Observe ordinary startup/periodic recovery only; the cadence is thirty seconds,
with existing bounded work and retry budgets. Do not add a force-refresh/restart
loop. Within the agreed window, reconcile each captured affected child:

1. It remains terminal with the original failed/cancelled outcome and zero
   dispatch. Original plan execution eligibility is not revived; later children
   and approvals acquire no execution authority.
2. Bound tokens exactly match the captured and transaction-verified generations.
   Only that proven lock union is removed. No unrelated owner, later generation,
   held lock or current active execution is released.
3. Durable binding history and its existing audit event ID agree. The effective
   audit stream contains the binding once; audit cursor and final reconciliation
   marker converge. A zero lock count with audit pending is not complete recovery.
4. Observe a later ordinary sweep for idempotence: no second binding/release or
   provider dispatch, no new unexplained recovery/storage/audit failure or fallback.
   Historical counters and unrelated concurrent authorized work are attributed
   explicitly, not reset or treated as acceptance actions.

Where proof fails, require visible bounded `ownership_proof_unavailable`/
manual-intervention or the appropriate storage/retry category and preserved
records. That can demonstrate correct refusal, but it does not close recovery of
an incident that remains blocked. Stop dependent household applies and report
partial/unverified status rather than deleting records or claiming success.
If no affected retained records exist at capture time, report this live recovery
case not observed; synthetic success and a healthy empty backlog are distinct.

Finish with a no-probe health observation and explain every count delta. Do not
repeat earlier dashboard canaries, lifecycle campaigns or device cycles.
A subsequent household repair requires freshly read configuration, a new exact
plan and its own approval. Any new eighteen-lock live canary must be separately
specified and authorized; none is granted by this acceptance document.

## 5. Verdict and evidence

Report source/CI, publication, installed image, authority/read continuity, raw
catalog, panel rendering, retained-state recovery and final settlement separately.
Identify receipts by exact revision, timestamps, request/event IDs and hashes.
Preserve old packages and write a new manifest; no verifier may rewrite earlier
accepted evidence. Mark unavailable evidence explicitly. Do not promote a
partial recovery or a failed audit projection into complete installed acceptance.
