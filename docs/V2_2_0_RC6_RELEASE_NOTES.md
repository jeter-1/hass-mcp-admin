# Engineering 2.2.0-rc.6 release notes

RC6 prepares the independently reviewed RC5 remediation candidate
`aa28dbcfa28e8814e7eb82cf61d172ebee8fa26b` against published RC5
`10a84027f2ef32763aa1f1eb5e44f72227e95c44`. Materialized source declares
`2.2.0-rc.6` in all three authoritative locations and consumes
`.release/next-version`. Stable remains 1.1.2. These notes do not establish
publication, installation or live recovery.

## Corrections

- Recovery defers projection to an active executor and preserves valid
  pre-intent approval. An approved apply overlapping recovery retains at-most-once
  dispatch. Genuine owner-loss recovery and terminal projection remain; recovery
  after durable intent stays readback-only. Existing approval, state, authority
  and ownership checks are unchanged.
- Oversized responses use valid bounded JSON instead of cutting serialized JSON
  and appending text. They retain the classified operation outcome, identities
  and available reconciliation facts, with explicit incomplete-response metadata.
  Small responses remain compatible. A completed action is not reported absent
  merely because its details exceed the output budget.
- RC5-REVIEW-1 is resolved: flat `get_execution_task` and nested apply receipts
  preserve authoritative state, attempt accounting, dispatch timestamp and
  available bounded verification outcome. The independent maximum-request-ID
  case now fits 999 bytes within 1,024; the 60,000-character control remains
  correct. Missing/null/zero facts stay distinct. Attempt count alone is not
  proof of dispatch. Use supported bounded detail retrieval for reconciliation
  and complete approval disclosures; never repeat a mutation to recover output.
- Low RC5-OBS-1 coalesces identical integrity findings before caps, totals and
  pagination. Different sources, paths and evidence remain distinct. Raw-reference
  counts, incomplete coverage, manual-review disclosures and continuation retain
  their meanings.

The release-only delta preserves the reviewed recovery behavior and response-detail
limitations without modifying runtime or behavioral tests. Public tool schemas,
registration, routing/admission, provider boundaries, permissions, persistence,
TTLs, dependencies, workflows and stable-v1 remain unchanged. No new tool,
unreviewed dispatch, write route or fallback is introduced.

## Evidence and compatibility

The completed independent implementation review and focused rereview close
RC5-REVIEW-1 without a new actionable delta finding. The rereview executed two
independent receipt tests and 28 remediation tests successfully. Earlier full
Evidence on the reviewed head ran 3,403 tests with 20 skips and no test failures;
its sole failed gate was unchanged RC5 metadata. Preserve those historical
records. Final RC6 Evidence must pass the legitimate RC5-to-RC6 version
transition and full suite on the clean committed release candidate.

Record complete-PR and release-only comparisons, exact final head/tree, scoped
release review, local commands/counts/skips and exact-head CI run/attempts,
tested checkout, merge parents/tree and artifacts. Source review, final candidate
validation, publication/provenance, installed-image binding, fresh public catalog
and live acceptance are separate evidence stages. Prior RC5 evidence cannot be
transferred to RC6.

Engineering remains the unified Nabu Casa endpoint with reviewed internal ha-mcp
8.4.3 providers. The unchanged healthy pairing requires all 17 Core capabilities
and 51 static plus 25 delegated tools, 76 total. `ha_get_operation_status`
remains held. The Core contract, including 2026.9.0/2026.9.1, and earlier
identifier, reconciliation, dependency, transport and cancellation corrections
remain intact. The 300-second cooperative build deadline and 600/3600-second
evidence TTLs are unchanged; no remote-abort guarantee is established.

## Deployment acceptance and recovery

Live acceptance and any restoration require fresh deployment evidence and
separate authorization. Preserve prior execution and recovery evidence in the
operator's private records. This release preparation establishes no current
deployment state and does not authorize a Core update or stable promotion.

Follow [RC6 acceptance](V2_2_0_RC6_ACCEPTANCE.md): separately authorize deployment,
verify fresh installed identity/image, catalog, authority and state, useful reads,
natural dependency refresh and settled resources. Before restoration, reconcile
the operator-designated test helper and any prior execution. Inspect consumers
and disclose incomplete consequence coverage. If it is already OFF, record that
condition without toggling it. If an OFF transition is required and authorized,
create a fresh exact plan, obtain its authenticated panel approval, apply once,
inspect the task and independently verify OFF. Require settled execution
resources. Never reuse a failed plan or approval, or blindly retry an uncertain
operation. Reconcile uncertain dispatch, verification or external changes before
further mutation. Source success does not establish live recovery.

Dependent dashboard canaries require verified restoration/current-state
reconciliation and execution integrity before their separately approved tests.
Dashboard, held-read/helper canaries, notification/navigation, backup, restart
and Core-update testing retain their separate exact scopes and approvals. None
runs during release preparation. Published RC5 remains immutable; returning to
it restores known defects. Any deployment recovery must account for compatible
Core, configuration, database, backup and surviving access rather than assuming
an older Engineering artifact restores the household.

Before merge, release preparation can remain unused or be reverted while keeping
the reviewed correction/history. Leave the PR draft for Josh. His later Ready
decision authorizes the controlled merge/publication sequence under repository
policy; it does not authorize deployment or live recovery.
