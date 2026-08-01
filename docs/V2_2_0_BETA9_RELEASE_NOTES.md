# HA MCP Engineering Server 2.2.0-beta.9

Version `2.2.0-beta.9` is a narrow persisted-format compatibility correction on
the accepted Beta 8 runtime. It recognizes the real contract-v2
prohibited/superseded record written by Beta 6, contains individual governance
projection failures, and reconciles plan policy accounting. It does not
implement delta-aware safety-reducing policy; that milestone is deferred to
Beta 10.

## Beta 8 regression corrected

Shipped Beta 6 declared `CONFIGURATION_PLAN_CONTRACT_VERSION = 2` and assigned
that value when creating every configuration plan. Beta 8 instead gated its
historical branch on a plan version lower than the current contract version.
Its manually reconstructed test fixture omitted `contract_version`, so model
deserialization defaulted the incomplete fixture to contract v1. That fixture
entered the branch, while real Beta 6 contract-v2 records did not.

Beta 9 uses the fixed historical constant
`BETA6_PROHIBITED_COMPAT_CONTRACT_VERSION = 2` and exact equality. It does not
use the mutable current contract version as a historical upper bound.

## Historical writer and fixture authority

Neutral fixed-time fixtures are generated through the exact Beta 6
`ChangeGovernanceService` creation and same-target supersession lifecycle at
source commit `5c7eebf962837f85f2309b1b5099401fb075cd6e`. Values are neutral before
historical code computes hashes. The committed provenance records the source
commit, generator command and hash, fixture hashes, contract version, operation
count, exact prepared operation states, and fixed generation time.

The generated contract-v2 records contain a nonempty `operations` list. A
prepared, never-dispatched operation has execution status `pending`, no receipt,
provider operation identifier, response, readback, verification, or failure
evidence, and a top-level `not_started` execution outcome. This exact state is
compatible. Dispatching, response receipt, provider identifiers, verified or
applied operation state, and every other adjacent execution fact remain
contradictions.

No raw production record is committed. No local operator-provided record copy
was available during source implementation, so structural comparison with the
two deployed records remains a post-deployment acceptance boundary rather than
claimed source evidence.

## Strict structural recognition

Historical recognition continues to require:

- validated immutable plan and policy hashes;
- policy class `prohibited`, authority version 3, no required acknowledgement,
  and `apply_allowed=false`;
- contract version exactly 2 with the Beta 6 configuration-plan target and
  prepared operation shape;
- legacy plan status `superseded`, approval and bundle state `invalidated`, and
  exact source-consistent created/superseded event evidence;
- no challenge, approval grant or consumption, execution task, provider
  dispatch or response, apply, verification, rollback, or successful work.

Clause-level diagnostics are private, deterministic, bounded names used by
tests and review. They expose no hashes, configuration, or record body. Task
repository failure propagates and cannot masquerade as absence of a task.
Legacy status alone never proves prohibition.

## Partial inventory and reconciled health

One successfully loaded plan that fails bounded governance projection no longer
breaks every plan listing or hide the failure. Inventory returns valid plans and
an explicit bounded `projection_failures` list with opaque plan IDs and error
codes, a total count, truncation flag, and `partial=true`. Filtering still uses
the effective projected status for valid plans. Systemic plan or task storage
failure remains a top-level failure.

Health assigns every loaded plan to exactly one policy bucket. An unprojectable
record enters `plans_by_policy_class.projection_failed`, increments
`projection_failure_count`, and emits `projection_failure_warning` while
`policy_class_accounting_valid` verifies that the bucket sum equals the total.
Projection failures remain fail-closed and excluded from every actionable or
pending approval counter.

## Read-side immutability

Detail, inventory, health, startup rehydration, Ingress queue generation, and
handoff generation preserve persisted bytes, events, hashes, timestamps, and
schema. They create no migration, challenge, task, approval event, provider
call, retry, or fallback. Current prohibited-plan behavior and intentionally
status-based pre-F2 actionability remain unchanged.

## Preserved boundaries

Beta 9 preserves:

- Beta 7 configuration-provider response truthfulness, including empty success
  and received error responses;
- F2 policy mapping, same-administrator authority-v3 sequencing, and immutable
  plan/policy binding;
- task schema version 1, one-task ownership, and no blind redispatch;
- 25 canonical, 23 Engineering-native, and 48 locally registered tools;
- 26 configured delegated reads and 74 configured total tools;
- `ha-mcp` 7.14.2, protocol `2025-03-26`, compatibility entry
  `ha-mcp-v7.14.2-7917b2d3`, and catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`;
- stable v1.1.2; and
- zero fallback.

Beta 9 adds no tool, resource, provider, policy class, approval mode,
configuration action, update/recovery behavior, persisted migration, or
fallback. F3 begins only after the separate Beta 10 policy milestone is
accepted.
