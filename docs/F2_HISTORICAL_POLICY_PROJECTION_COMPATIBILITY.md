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
- Exact Beta 6 source commit
  `5c7eebf962837f85f2309b1b5099401fb075cd6e` and exact Beta 32 source also
  prove the two contract-v1 lifecycle containers observed in persisted
  history: an expired legacy automation plan and a terminal prohibited
  automation plan. Both were written through
  `ChangeGovernanceService.create_plan` with zero provider writes and zero
  execution tasks in the source-generated fixtures.

## Sanitized live-record certification

The operator acquired byte-exact read-only copies of the two records from the
Engineering add-on data boundary on 2026-08-11. Source and copy SHA-256 values
matched. The private JSON remains outside Git; no challenge identifiers,
nonces, CSRF material, credentials, provider bodies, or raw configurations are
recorded here.

| Plan | Created | Immutable identity | Lifecycle | Stored policy | Stored hashes | Authority and execution | Certification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `4bbc541d0a5046b0baac3e2b5e03faf4` | `2026-08-05T04:02:01.255322+00:00` | contract 1, plan version 1, `update_automation`, automation `1737583374684` | `awaiting_approval`, terminal prohibited bundle | `f2-v1`; `prohibited / high / safety_critical`; `safety_critical_effect_not_reviewed`, `supported_configuration_change` | subject `66bc3be8a84e3b1ab9f4b5041f2cc43eff0907055b7488858791a5cba00fc923`; decision `99eb92f5a516fba7656a704addeadbbde056168d923e08c9b3ef821980d3483d` | approval required but prohibited; no challenge, grant, consumption, elevated acknowledgement, task, dispatch, apply, or verification | `matches_reviewed_beta32_historical_profile` |
| `b2bdaad198ee4e82a33feb53f6d404f2` | `2026-08-01T04:39:42.810192+00:00` | contract 1, plan version 1, `update_automation`, automation `1737583374684` | `expired`, invalidated approval and bundle | `f2-v1`; `prohibited / high / safety_critical`; `safety_critical_effect_not_reviewed`, `supported_configuration_change` | subject `174b22d10dff4c59f5cb7023fd95eaa02cdba27be5115cd5374f28693268c371`; decision `eb2ff58546731bed77ee15712c3b70a34ff7f7a5804e3a8d6a5092cdf7c51473` | no challenge, grant, consumption, elevated acknowledgement, task, dispatch, apply, or verification | `matches_reviewed_beta32_historical_profile` |

For each record, the canonical policy-subject hash recomputed exactly from the
immutable plan subject, and the canonical policy-decision hash recomputed
exactly from every stored decision field. The approval policy hash and class
match the stored decision. Current evaluation retains the same subject hash
and classifies the subject as the corrected
`non_risk_increasing_condition_guard_added` retained-effect family. The
records differ from current policy only in the reviewed historical decision.

The first reviewed implementation assumed these records were contract-v2
configuration plans. The live evidence disproved that structural assumption.
The correction does not add another decision profile: it accepts only the two
already-reviewed contract-v1 writer/lifecycle containers, exact inert
authority and execution state, and the existing Beta 32 decision profile.
Unknown reason codes and every other contract-v1 shape remain rejected.

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

No production record or plan-ID exception is committed. Deterministic
source-generated fixtures reproduce the two observed contract-v1 shapes with
synthetic identifiers and values before their historical writers compute the
policy hashes.

## Decision

Read-only governance projection recognizes two exact historical profiles:

1. `beta32_retained_effect_prohibited`;
2. `beta33_initial_retained_effect_reason`.

Recognition requires all of the following:

- either a terminal contract-v2 configuration plan with exactly one canonical
  automation update operation, or one exact source-reviewed contract-v1
  `update_automation` container;
- for contract-v1, plan version 1, a distinct nonempty automation target, no
  operations array or operational plan, and either the exact terminal
  prohibited lifecycle or exact Beta 6 expired lifecycle and event sequence;
- a stored policy-subject hash that matches the immutable plan subject;
- a stored policy-decision hash that matches every stored decision field;
- one exact source-reviewed historical decision tuple;
- current classification of the immutable subject as the corrected bounded
  retained-effect policy family; and
- a complete, internally consistent persisted approval bundle;
- no challenge, grant, consumption, acknowledgement, execution, verification,
  rollback, provider, or other authority evidence for contract-v1; and
- no execution task in the authoritative task store.

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

The observed Beta 33 accounting before this correction was two projection
failures and two policy-snapshot mismatches, with two prohibited records and
two `projection_failed` records. Replaying the two source-authentic certified
shapes yields zero projection failures, zero policy-snapshot mismatches, four
prohibited records, and two historical-compatible records under
`beta32_retained_effect_prohibited`. This is deterministic source/test
evidence, not a claim that undeployed live health has already changed.

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
- exact Beta 6 and Beta 32 contract-v1 writer provenance;
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
- exact projection of both certified contract-v1 containers with startup and
  deep-audit accounting unchanged;
- rejection of a nonterminal old snapshot; and
- rejection of subject, decision, and approval-bundle contradictions.

## Limitations and integration

This correction does not infer that every historical `f2-v1` decision is
valid. Any additional policy transition requires its own exact-source review,
fixture provenance, and bounded profile. Beta 34 version staging, release
metadata, acceptance evidence, and aggregate release fingerprints belong to
the later integration branch after both concurrent workstreams are reviewed.
