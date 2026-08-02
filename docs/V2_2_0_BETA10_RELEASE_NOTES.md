# HA MCP Engineering Server 2.2.0-beta.10

Version `2.2.0-beta.10` is a narrow persisted-format compatibility correction.
It does not change policy classification, approval authority, execution-task
ownership, provider routing, tool registration, or fallback behavior.

## Corrected historical shape

Beta 9 correctly recognized the contract-v2 prohibited configuration-plan
records written through Beta 6's `create_configuration_plan` path. The two
remaining deployed records were instead written through Beta 6's older
`create_plan` path and later expired. Their serialized form omits
`contract_version` and `operations`, deserializes as contract version 1, and
uses `update_automation` with an automation target.

Beta 10 adds a separate compatibility profile for that exact historical form.
It requires:

- contract and plan version 1;
- `update_automation`, an automation target, and a nonempty target ID distinct
  from the plan ID;
- no operations;
- expired plan state with invalidated approval and bundle state;
- authority version 3 and approval kind `apply`;
- an intact prohibited policy snapshot, matching policy hashes, no required
  acknowledgements, and `apply_allowed=false`; and
- no challenge, granted or consumed authority, task, provider dispatch or
  receipt, apply, verification, or rollback evidence.

The existing current and contract-v2 prohibited profiles are unchanged. A
partial match never falls through to another profile.

## Exact lifecycle evidence

Only two complete event sequences are accepted:

1. successful `change_plan_created`, then rejected `change_plan_expired` with
   `change_plan_expired`; or
2. successful `change_plan_created`, rejected `policy_approval_rejected` and
   `change_apply_rejected` with `prohibited_change`, then rejected
   `change_plan_expired` with `change_plan_expired`.

Missing, additional, duplicated, reordered, successful, or differently coded
events remain unsupported. `change_plan_expired` is not added to the generic
no-execution event allowlist.

## Historical fixture authority

Both compatibility fixtures were generated through the exact shipped Beta 6
commit, using its real `ChangeGovernanceService.create_plan` writer and
expiration lifecycle. Neutral inputs were supplied before Beta 6 computed
hashes. The generator refuses a different or dirty historical worktree and
asserts that fixture generation performs no provider write.

The committed provenance binds the historical commit, generator hash, fixed
generation time, fixture hashes, writer, terminal lifecycle, and expected event
sequences. CI now checks out full history, creates a detached Beta 6 worktree,
regenerates both contract-v1 and contract-v2 fixture families, and compares all
fixtures and provenance byte-for-byte.

## Read-only projection

Exact matching records project as terminal and non-actionable through detail,
listing, health, startup rehydration, Ingress, and handoff paths. Reads do not
migrate or rewrite records, append events, synthesize authority, create a task,
or call a provider. Task-repository errors continue to propagate.

Beta 9's bounded per-record listing failures and reconciled
`projection_failed` health accounting remain unchanged for records that do not
match a reviewed profile. Systemic storage failures remain top-level failures.

## Unchanged boundaries

Beta 10 preserves:

- 25 canonical, 23 Engineering-native, and 48 local registered tools;
- 26 configured delegated reads and 74 configured total tools;
- task schema version 1 and approval authority version 3;
- Beta 7 provider-response truthfulness;
- Beta 9 contract-v2 compatibility, partial listing, and health reconciliation;
- exact `ha-mcp` 7.14.1 and 7.14.2 reviewed admission;
- stable v1.1.2; and
- zero fallback.

It adds no tool, resource type, provider, approval mode, execution route,
recovery action, or live Home Assistant access. Delta-aware safety-reducing
policy is deferred to Beta 11, and F3 does not begin in this release.
