# F2 Historical Policy Projection Compatibility

## Scope

This document records the isolated Beta 34 workstream B correction for
historical governance projection. It is not a Beta 34 release declaration or
acceptance document, and it does not change the active policy, approval,
execution, provider, or task contracts.

## Source-reviewed facts

- Exact Beta 32 source commit
  `f9d660499a05edef6af7fd9a590d7827b5983e3a` classified a retained
  safety-critical automation effect with an appended condition as
  `prohibited / high / safety_critical`, using
  `safety_critical_effect_not_reviewed`.
- Initial Beta 33 policy commit
  `5b149b04cb12ee42abf19fc6a37ec2017c8bb0bf` classified the same bounded
  subject as `elevated_admin / moderate / safety_critical`, using
  `risk_reducing_condition_guard_added`.
- Corrected Beta 33 policy commit
  `2c57d72789f49b458e30cb6db20faf42018a8f71` retained the authority class,
  risk delta, and consequence while replacing that reason with the accurate
  `non_risk_increasing_condition_guard_added`.
- All three implementations persisted `policy_version: f2-v1`. As a result,
  recomputing current policy against intact older records reports a mismatch
  even though the stored decision accurately represents its writer.
- Exact-source synthetic fixtures prove both historical shapes without an
  approval challenge, execution task, or provider write. Their generator and
  provenance are committed with the tests.

## Record diagnosis boundary

The live health observation supplied to this workstream reports two policy
snapshot mismatches and two projection failures, but it does not include the
two persisted records. Source work therefore cannot truthfully identify their
live plan IDs, stored hashes, approval history, or exact failure clauses. This
branch does not access deployed add-on storage or Home Assistant and does not
infer those facts from the aggregate counters.

The committed source-authentic diagnostic records are deliberately synthetic:

| Profile | Synthetic plan ID | Created by | Stored decision | Lifecycle |
| --- | --- | --- | --- | --- |
| Beta 32 prohibited | `b3420000000000000000000000000001` | exact `f9d660499a05edef6af7fd9a590d7827b5983e3a` | `cb9d74780042707c4d724984134ba48dadba52c75cedafa56032aa1a25451e1e` | prohibited bundle; never approved, consumed, applied, or dispatched |
| Initial Beta 33 reason | `b3430000000000000000000000000001` | exact `5b149b04cb12ee42abf19fc6a37ec2017c8bb0bf` | `0e8cade1d65db626ef1fd95f4dc8ce773641a235a014fedf20d5429e9f780c22` | superseded; never approved, consumed, applied, or dispatched |

Both carry `policy_version: f2-v1`. Current projection recomputes the bounded
subject as `elevated_admin / moderate / safety_critical` with
`non_risk_increasing_condition_guard_added`; the exact Beta 32 record instead
contains the older prohibited decision, while the initial Beta 33 record uses
`risk_reducing_condition_guard_added`. Both therefore fail current
`policy_snapshot_matches` even though their independently recomputed stored
subject and decision hashes are intact.

A third exact-source fixture,
`b3431000000000000000000000000001`, proves the consumed-approval case. It was
created, externally approved, consumed, applied, and verified by the exact
initial Beta 33 source against a synthetic gateway. Its single synthetic
provider mutation and execution task exist only during fixture generation; no
live system is involved. Current read-only projection preserves its approval
and execution bytes, while every fresh approve or apply attempt still fails
current-policy validation before a new task or provider call.

Before claiming that the two live failures are corrected, a separately
authorized operator must compare sanitized copies of those exact records to
the reviewed profiles. A mismatch remains a real projection failure; this
branch does not add a plan-ID exception or arbitrary reason-code acceptance.

## Decision

Read-only governance projection recognizes two exact historical profiles:

1. `beta32_retained_effect_prohibited`;
2. `beta33_initial_retained_effect_reason`.

Recognition requires all of the following:

- a terminal contract-v2 configuration plan;
- exactly one automation update operation;
- the canonical configuration-plan identity and structure;
- a stored policy-subject hash that matches the immutable plan subject;
- a stored policy-decision hash that matches every stored decision field;
- one exact source-reviewed historical decision tuple;
- current classification of the immutable subject as the corrected bounded
  retained-effect policy family; and
- a complete, internally consistent persisted approval bundle.

The record is projected with its original stored policy class and evidence.
It is not rewritten or upgraded to current policy.

Health reports accepted records separately under
`historical_policy_snapshot_compatibility`, including the fixed compatibility
model, compatible count, exact-profile counts, and
`authorization_effect: none_projection_only`. They no longer inflate
`projection_failure_count` or `policy_snapshot_mismatches`. Current snapshots
remain ordinary current-policy records, records without a snapshot remain in
`legacy_without_policy_snapshot`, and corrupt, contradictory, nonterminal, or
unreviewed historical records remain projection failures.

## Authority boundary

Historical compatibility is used only by plan reads, bounded plan listings,
health accounting, deep audit, and read-only handoff projection. The ordinary
governance loader remains authoritative for approval, apply, rollback, and
recovery and continues to require an exact current-policy snapshot.

Consequently, an old active record, a changed subject, an unrecognized old
decision, or a contradictory approval bundle remains fail-closed. Historical
projection cannot create a challenge or task, consume approval, invoke a
provider, write Home Assistant state, or enable fallback.

## Validation contract

The focused tests require:

- exact fixture and generator provenance;
- both historical snapshots to fail current-policy equality while passing
  independent stored-hash integrity;
- exact terminal projection without persisted-byte mutation;
- `projection_failure_count: 0` and `policy_snapshot_mismatches: 0` for the
  two valid historical fixtures;
- deterministic restart and deep-audit behavior;
- separate accounting for current, legacy-without-snapshot, exact historical,
  and invalid records;
- byte-immutable consumed approval and execution evidence generated by exact
  historical source;
- strict refusal of approval and apply with zero task and provider mutations;
- rejection of a nonterminal old snapshot; and
- rejection of subject, decision, and approval-bundle contradictions.

## Limitations and integration

This correction does not infer that every historical `f2-v1` decision is
valid. Any additional policy transition requires its own exact-source review,
fixture provenance, and bounded profile. Beta 34 version staging, release
metadata, acceptance evidence, and aggregate release fingerprints belong to
the later integration branch after both concurrent workstreams are reviewed.
