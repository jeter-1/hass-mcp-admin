# 2.2.0-beta.10 acceptance contract

Version: `2.2.0-beta.10`

This document is the source and post-deployment acceptance boundary for the
legacy expired-automation compatibility correction. Source and CI acceptance
must complete before independent review, merge, publication, or deployment.

## Fixed source boundary

- Base: `f6223d453bfa38860eaebb0042163adb1770068f`
- Historical Beta 6 source:
  `5c7eebf962837f85f2309b1b5099401fb075cd6e`
- Stable version: `1.1.2`
- Task schema: `1`
- Approval authority: `3`

The implementation branch and exact head must be recorded in the draft pull
request. A changed base or head requires a new review boundary.

## Private live diagnostic boundary

The two production record IDs are acceptance identifiers only:

- `b2bdaad198ee4e82a33feb53f6d404f2`
- `e07274ab13084d51b845423d6941eb8d`

Their read-only private input hashes are:

- `e49c1b2b0920706a5e6a9b5b0cf68e4b2d28fad0469ae700981c4980ac7b8f45`
- `7a5c2056bc4fb6b2fcc1707c9fa38a406154c11c21f488e81411fb25b5891161`

Raw records must remain outside Git and external services. Source tests and CI
must use only neutral fixtures generated through the exact historical writer.

## Source-generated fixture checks

Run the generator twice against a clean detached worktree at the historical
Beta 6 commit. Require byte-identical outputs. The legacy fixtures must:

- omit the `contract_version` and `operations` keys exactly as Beta 6 did;
- deserialize to contract version 1 and plan version 1;
- use `update_automation` and an automation target;
- be expired with invalidated approval and bundle state;
- contain an intact authority-v3 prohibited policy snapshot; and
- contain exactly the source-generated event sequence for their case.

CI must regenerate and compare both contract-v1 expired-automation and
contract-v2 superseded configuration-plan fixtures and provenance files.

## Focused source acceptance

Both generated legacy fixtures must pass:

- model deserialization, plan integrity, and policy-snapshot validation;
- the exact legacy-profile failure accumulator with zero clauses;
- detail projection;
- unfiltered, prohibited, and awaiting-approval listings;
- health accounting;
- startup rehydration;
- Ingress queue generation; and
- handoff generation.

Each projected record must report:

```text
status = prohibited
approval.state = prohibited
approval_lifecycle = prohibited
approval_bundle_state = prohibited
approval_actionable = false
required_acknowledgements = []
approval_challenge_created = false
apply_allowed = false
next_required_operation = null
```

For two fixtures in an isolated store, require prohibited counts of two,
projection-failure counts of zero, valid policy-class accounting, and zero
pending/actionable counters. Require no challenge, task, provider call, or
fallback and byte-identical storage after every read path.

## Required refusal matrix

Derive each negative case from a valid source-generated fixture. Reject changed
contract or plan versions; another operation or target type; empty/self target;
nonempty operations; non-expired/non-invalidated lifecycle; another authority
version or approval kind; non-prohibited policy; acknowledgements;
`apply_allowed=true`; invalid plan/policy evidence; challenge, grant,
consumption, acknowledgement, binding, task, provider, receipt, apply,
verification, or rollback evidence; and every missing, additional, duplicate,
reordered, successful, or differently coded event.

Task-store lookup failures must remain fatal. Every refusal must preserve bytes
and create no challenge, task, provider call, or fallback.

## Regression contract

Require:

- Beta 8 and Beta 9 persisted compatibility tests;
- current prohibited, standard-admin, and elevated-admin tests;
- Beta 9 partial-list containment and reconciled health;
- Beta 7 response-receipt truthfulness;
- disposable standard, elevated, prohibited, contract-v2 upgrade, and legacy
  expired upgrade scenarios;
- exact-image `ha-mcp` 7.14.1 and 7.14.2 lanes;
- stable and Engineering packaging; and
- linux/amd64, linux/arm64, and linux/arm/v7 builds.

The contract remains 25 canonical / 23 Engineering-native / 48 local, with 26
configured delegated reads and 74 configured total tools, task schema 1,
authority version 3, stable v1.1.2, and zero fallback.

## Post-deployment read-only handoff

After independent review, protected merge, publication, and operator-controlled
deployment, first verify `server_info`, capabilities, health, configuration,
one delegated read, task inventory, and bounded audit output. Require version
`2.2.0-beta.10`, the exact merge SHA, `dirty=false`, connected Home Assistant,
healthy stores, exact upstream admission, and the unchanged tool/schema/
authority/fallback contract.

Before any governed write, read the two historical IDs listed above. Each must
be readable and non-actionable with the exact prohibited projection and no
execution task. Require:

- unfiltered listing succeeds with `partial=false`;
- prohibited filtering includes both IDs;
- awaiting-approval filtering excludes both IDs;
- `projection_failure_count=0` and valid policy-class accounting;
- both records counted as prohibited; and
- all pending/actionable counters remain zero.

Stop before Test 3 or Test 5 if either record is unreadable or actionable,
listing is partial, any projection failure remains, accounting is invalid, or
storage health changes. Deferred governed acceptance may resume only after the
read-only compatibility boundary passes.

## Non-actions

Beta 10 source acceptance does not merge, publish, tag, deploy, access live
Home Assistant, create or approve a plan, create a task, restart anything,
implement delta-aware policy, or begin F3.
