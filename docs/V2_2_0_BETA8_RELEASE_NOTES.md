# HA MCP Engineering Server 2.2.0-beta.8

Version `2.2.0-beta.8` is a narrow persistence-compatibility correction on the
accepted Beta 7 runtime. It restores reads for a validated prohibited-plan
representation persisted by Beta 6. It does not implement delta-aware
safety-reducing policy; that milestone is deferred to Beta 9.

## Deployment regression corrected

Beta 7 correctly projected newly created prohibited plans, but ordinary
approval-sequence validation rejected two Beta 6 records before the terminal
projection could run. Detail and inventory returned
`approval_sequence_failure`, and health omitted those plans from policy-class
totals.

Beta 6 could create the affected shape when a later plan targeted the same
automation. Its supersession path changed only legacy lifecycle fields:

- persisted status became `superseded`;
- approval state and bundle state became `invalidated`;
- the immutable authority-v3 policy snapshot remained `prohibited`;
- required acknowledgements remained empty; and
- no challenge, approval, task, provider dispatch, apply, verification, or
  rollback authority existed.

Beta 8 recognizes that exact source-established structure before approval
sequence processing. It does not recognize prohibition from legacy status
alone.

## Strict compatibility predicate

The centralized effective-prohibited predicate accepts either the current
prohibited representation or the validated Beta 6 superseded representation.
Historical compatibility requires an intact policy snapshot, `apply_allowed`
false, authority version 3, empty acknowledgements, the exact bounded
supersession lifecycle, and no authority or execution evidence.

Any nonempty acknowledgement, granted or consumed authority, challenge,
execution task, provider event, apply or rollback fact, successful work, or
policy-hash mismatch fails closed under the existing governance error contract.
Compatibility is structural and contains no plan-ID special case.

## Read-only terminal projection

Validated records are returned as:

- `status: prohibited`;
- `approval.state: prohibited`;
- prohibited approval lifecycle and bundle state;
- `approval_actionable: false`;
- no required acknowledgement or challenge;
- `apply_allowed: false`; and
- no next required operation.

Detail, unfiltered inventory, prohibited-status filtering, health, startup
rehydration, Ingress, and handoff use the same effective status. Pending and
authorization-required views exclude these records; prohibited-policy totals
include them. Reads do not rewrite the plan, event history, hashes, timestamps,
or storage schema.

`status_is_legacy` describes the returned status projection, not record age.
Pre-F2 records without a policy snapshot intentionally retain their existing
status-based actionability behavior.

## Preserved boundaries

Beta 8 preserves:

- Beta 7 configuration-provider response truthfulness;
- the current prohibited-plan representation and F2 policy mapping;
- same-administrator authority-v3 sequencing;
- task schema version 1, one-task ownership, and no blind redispatch;
- 25 canonical, 23 Engineering-native, and 48 locally registered tools;
- 26 configured delegated reads and 74 configured total tools;
- `ha-mcp` 7.14.2, protocol `2025-03-26`, compatibility entry
  `ha-mcp-v7.14.2-7917b2d3`, and catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`;
- stable v1.1.2; and
- zero fallback.

It adds no tool, resource type, provider, policy class, approval mode,
configuration action, update/recovery behavior, storage migration, or fallback.
F3 begins only after the separate Beta 9 policy milestone is accepted.
