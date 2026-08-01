# 2.2.0-beta.8 acceptance contract

Version: `2.2.0-beta.8`

Source baseline:
`1fe2f02d708212c7d157afdda57f698db52d7523`

This is the source and later operator-controlled acceptance contract for the
persisted prohibited-plan compatibility correction. Source implementation and
validation must not access deployed Home Assistant, merge, publish, deploy,
create a tag or release, or trigger an operational action.

## Immutable boundaries

- Immutable plan and policy hashes are validated before compatibility
  recognition.
- Legacy status alone is never proof of a prohibited plan.
- Contradictory authority or execution evidence fails closed.
- No read, listing, health call, startup, Ingress request, or handoff generation
  rewrites a historical record.
- Current Beta 7 prohibited plans retain their exact semantics.
- Pre-F2 actionability remains status-based intentionally.
- Beta 7 provider-response receipt semantics remain unchanged.
- Policy mapping, authority version 3, task schema 1, same-administrator
  sequencing, one-task ownership, stale-state checks, and zero fallback remain
  fixed.
- Delta-aware safety-reducing policy is deferred to Beta 9, and F3 begins only
  after that milestone is accepted.

## Exact historical fixture

Generate a sanitized fixed-time fixture with Beta 6's own constructors and
serializer. The fixture must contain a valid authority-v3 prohibited policy
snapshot, empty required acknowledgements, no challenge or authority, no task
or provider activity, and the exact same-target supersession projection:

- `status: superseded`;
- `approval.state: invalidated`;
- `approval.bundle_state: invalidated`; and
- one bounded `change_plan_superseded` event.

The fixture uses generic IDs and no deployed storage, credentials,
administrator identities, or arbitrary production content.

## Source compatibility acceptance

Require all of the following:

1. Storage deserialization and immutable policy validation succeed.
2. `get_change_plan` returns the prohibited, non-actionable public projection.
3. Unfiltered listing includes the record.
4. `list_change_plans(status="prohibited")` includes it.
5. `list_change_plans(status="awaiting_approval")` excludes it.
6. Health counts it under both the prohibited policy class and prohibited
   decision counter while all pending counters remain unchanged.
7. Restart after the plan lifetime produces no challenge, expiry, sequence
   event, task, or storage write.
8. Ingress exposes no row, approval control, or direct review page.
9. Handoff reports terminal prohibited history, not authorization-required
   work.
10. Approval and apply return `prohibited_change` with no task, provider call,
    or fallback.

Mixed inventory must retain truthful standard pending, elevated pending,
current prohibited, terminal applied, and legacy projections.

Negative fixtures must reject nonempty acknowledgements, granted or consumed
approval, an execution task, provider dispatch evidence, invalid policy hashes,
and successful apply evidence. None may become actionable when compatibility
recognition refuses it.

## Beta 7 regression coverage

Retain response-evidence tests proving that HTTP success (including an empty
success), received HTTP error, and WebSocket success/error frames record
receipt, while a timeout or pre-response connection loss does not. A later
readback mismatch cannot erase a recorded response, and historical task
evidence remains byte-preserved.

## Disposable and exact-image acceptance

The disposable Home Assistant contract must start the Beta 8 service over the
source-established Beta 6 fixture and prove detail, listing, prohibited
filtering, awaiting exclusion, health, rehydration, and pending-approval
inventory without a storage mutation, task, provider write, or fallback.
Existing standard, elevated, current prohibited, same-administrator,
different-administrator, duplicate/no-redispatch, and physical non-actuation
scenarios remain required.

Exact-image lanes for `ha-mcp` 7.14.1 and 7.14.2 must each report 78 advertised
tools, 26 exact-admitted delegated reads, and zero missing reviewed reads,
schema mismatches, unreviewed tools, or fallback attempts. Stable packaging
must remain 1.1.2. The Engineering image must report `2.2.0-beta.8`, the exact
head SHA, and `dirty=false` for amd64, arm64, and arm/v7.

## Post-deployment live handoff

Live acceptance requires separate authorization. Begin with read-only smoke
checks and require Beta 8, the exact merge SHA, `dirty=false`, connected Home
Assistant, valid configuration, 25/23/48 plus 26/74 tools, task schema 1,
authority version 3, exact upstream admission, healthy storage, and zero
fallback.

Then read the two deployment-regression records:

- `b2bdaad198ee4e82a33feb53f6d404f2`;
- `e07274ab13084d51b845423d6941eb8d`.

Both must return prohibited/non-actionable projections, empty
acknowledgements, no challenge, no execution task, and disallowed apply.
Unfiltered and prohibited-filtered listings must succeed. Health must account
for every plan, include both in prohibited totals, and leave pending counters
at zero.

Only after those checks pass, continue the separately authorized Beta 7 live
acceptance: rerun elevated Test 3, confirm truthful response receipt and
timestamp, leave the elevated configuration installed, then run Test 5 as a
separate governed baseline-restoration plan without rollback between them.
Finish with the inert fixture restored, helper off, and trace count zero.

## Required validation

Run focused compatibility, storage, rehydration, policy, approval, task,
observability, Ingress, handoff, and disposable tests twice. Run two buffered
and one verbose full unittest discovery, Full and exact-head Evidence tiers,
compilation, metadata, YAML, dependency consistency, strict dependency audit,
secret, PowerShell, protected-path, whitespace, compatibility-registry, and
deterministic-regeneration gates. Run Docker-backed disposable, exact-image,
stable/Beta packaging, and architecture lanes locally only through the normal
repository boundary; otherwise require exact-head CI.
