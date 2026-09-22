# Instruction-guidance reconciliation for RC2

This document reconciles the instruction/action-guidance audit against published
RC1 `df860eeffdc9031bd0596a9a9892b9e1c9c08f1e` and the previously reviewed RC2
candidate `5961cfb3cfcc01da6192b24d6be42cd8bd7a7440` in
[PR #207](https://github.com/jeter-1/hass-mcp-admin/pull/207). Josh requested this
bounded guidance correction before that PR merges. The original historical-read
runtime correction, fixtures, source provenance and materialized RC2 version are
preserved. This is not a new runtime/security acceptance verdict or authority to
merge, publish, deploy, change settings, or access Home Assistant.

## Finding disposition and verification

| Finding | Repository correction | Verification and remaining scope |
| --- | --- | --- |
| IA-1: stale default entry worktree | Root instructions and the playbook require a deliberate task worktree and an instruction-revision check; fetching is distinguished from updating the checkout/session. | The old root at `8ce20ae` lacks the newer owner-direction, review and Ready contracts. The reported desktop entry path, personal policy and loaded session chain were not independently inspected here. Start a fresh task in the intended local worktree and confirm its actual loaded instructions; this operational action remains pending. Preserve the historical branch and unrelated work. |
| IA-2: release-preparation template requires consumed staging | The template now distinguishes authoring, staged validation and materialized validation; RC2 uses exact active authority after consuming staging. | An exact staged candidate continues to preview/materialization; a materialized candidate with matching declarations and no staging continues to final validation. Missing or inconsistent acceptance authority blocks the dependent action. This corrects the reusable template, not an alleged absence of a working release process. |
| IA-3: contradictory publication continuity/ordering prose | One event-aware explanation distinguishes the handoff's captured main authority, manual recovery and protected push. The queue-order promise is removed. | An earlier retained release may qualify at initial handoff admission while its authority is current. Moving main after capture fails the handoff guard even if ancestry remains. Existing claims, pre-write checks and recovery remain unchanged; automatic publication is already implemented. |
| IA-4: historical architecture labeled current | The architecture page gains a short current source/contract entry point; retained snapshots and ADR-022's Beta 53/54 implementation observations are explicitly historical. | Locate current Engineering source and its single public connector without treating frozen v1 or the old generic-service diagram as current authority. Accepted ADR decisions and historical release evidence remain intact. |
| IA-5: old rollout/recovery instructions | The old receipt-check migration and Beta 53 recovery values are labeled historical. The active recovery procedure derives identities from the actual incident. | No repository or native-review setting is changed. The observed repository ruleset required `validate` from integration `15368`; native Codex installation settings were unavailable. Historical values do not authorize a new dispatch. |
| IA-6: offline fixture ambiguity | Test instructions explicitly distinguish synthetic disposable loopback fixtures from external/live dependencies and separately scoped container/CI acceptance. | Local sockets require the actual sandbox capability. No production endpoint, credential, external service dependency or sandbox bypass is authorized by the wording. |
| IA-7: review independence ambiguity | A separately tasked reviewer/session must not be the implementer; a worktree is optional isolation only. | Implementer self-review remains useful but is labeled self-review. Preserve the one full review plus bounded delta-review default without accepting serious unresolved defects. |

The execution-host guidance is now platform-neutral: desktop/Android is the user
interface, while Windows, Debian/Linux and cloud environments have separate
execution and authentication contexts. Existing instruction checks follow that
distinction rather than requiring Windows-only wording.

## Portable source evidence

The audit's repository-side claims can be inspected at immutable revisions:

- [Historical entry-root instructions](https://github.com/jeter-1/hass-mcp-admin/blob/8ce20ae2729fa5f733b73efb3428d9bc3f207bc9/AGENTS.md)
  and [RC1 root instructions](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/AGENTS.md).
- [RC1 release-preparation template and operational guidance](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/docs/CODEX_WORKFLOW.md)
  and [single-use staging contract](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/.release/README.md).
- [Handoff guard](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/scripts/publication_handoff.py#L229)
  and [event-specific publisher](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/.github/workflows/publish-rc-image.yml).
- [Mixed historical architecture](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/ARCHITECTURE.md)
  and [ADR-022](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/docs/architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md).
- [Original offline instructions](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/tests/AGENTS.md)
  and [disposable relay fixture](https://github.com/jeter-1/hass-mcp-admin/blob/df860eeffdc9031bd0596a9a9892b9e1c9c08f1e/tests/test_ha_mcp_850_candidate_lane.py#L115).

Only `review.md` was supplied from the private audit package. Its referenced
`entrypoint-agents.diff`, `context.json` and `focused-validation-complete.log`
were not supplied here. The audit's 47-test result and local preservation claims
remain attributed to that report, not independently reproduced audit receipts.
Do not invent those artifacts or substitute a later run for their original scope.

## Decisions and completion boundaries

Protected-surface authorization requirements, remote stop conditions, validation
gates, publication permissions and live-change approval remain unchanged.
Functional-scope authorization and more flexible stop conditions are separate
owner policy decisions; this correction does not silently adopt them. Sandbox
and automatic approval review remain independently enforced.

Validate the changed instruction assertions and existing context/handoff cases,
then run the required clean-head Evidence and exact-head CI for the revised RC2
candidate. Record actual results in the PR with their exact commit; earlier
review/CI remains valid only for its original head and unchanged reviewed scope.
Obtain a bounded independent review of this guidance delta before Josh's Ready
decision. No additional household canary or runtime operation is needed for this
documentation correction. The local entrypoint check above remains separate.

Recovery before merge is to retain the PR as draft or revert only this guidance
commit while preserving the reviewed RC2 runtime work. Reverting source after
publication is not package withdrawal or a deployed-system rollback.
