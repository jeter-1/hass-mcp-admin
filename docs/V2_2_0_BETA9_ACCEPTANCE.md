# 2.2.0-beta.9 acceptance contract

> Historical boundary: Beta 10 adds the separate legacy contract-v1
> expired-automation profile. Use `V2_2_0_BETA10_ACCEPTANCE.md` for current
> source and post-deployment acceptance.

Version: `2.2.0-beta.9`

Source baseline:
`28e021f9a63c58438e6cd53d100a6afc57b5958a`

Historical writer:
`5c7eebf962837f85f2309b1b5099401fb075cd6e`

This is the source and later operator-controlled acceptance contract for real
Beta 6 contract-v2 prohibited-plan compatibility. Source implementation and
validation must not access deployed Home Assistant, edit production records,
merge, publish, deploy, create a tag or release, or trigger an operational
action.

## Immutable boundaries

- Historical fixtures are produced by the exact shipped Beta 6 writer and
  supersession lifecycle, with provenance; they are not reconstructed from
  assumptions.
- Immutable plan and policy hashes are validated before compatibility
  recognition.
- The historical contract gate is exact version 2, not a range based on the
  mutable current contract constant.
- Prepared contract-v2 operations are accepted only in the exact historical
  no-execution state. Every adjacent authority or execution contradiction fails
  closed.
- Legacy status alone is never proof of a prohibited plan.
- No detail, listing, health, startup, Ingress, or handoff read rewrites a
  historical record.
- Per-record projection failures are bounded and visible; systemic storage
  failures remain top-level errors.
- Current prohibited semantics and intentionally status-based pre-F2
  actionability remain unchanged.
- Beta 7 provider-response receipt semantics, policy mapping, authority version
  3, task schema 1, same-administrator sequencing, one-task ownership,
  stale-state checks, and zero fallback remain fixed.
- Delta-aware safety-reducing policy is deferred to Beta 10, and F3 begins only
  after that milestone is accepted.

## Fixture provenance acceptance

Re-run the repository generator against a clean detached worktree at the exact
historical writer commit. Require the generated fixture and provenance files to
be byte-identical to the committed copies. For each fixture require:

- `contract_version == 2`;
- at least one operation;
- every operation has the exact prepared Beta 6 execution status;
- no operation receipt, response, provider identifier, readback, verification,
  failure, apply, or rollback evidence;
- fixture SHA-256 matches the provenance manifest; and
- source commit and generator SHA-256 match the reviewed generation boundary.

No raw production plan, household configuration, credential, administrator
identity, or live hash-bearing record may be committed. A production structural
manifest may be compared only from operator-provided local read-only copies and
must remain bounded to the approved compatibility fields.

## Source compatibility acceptance

For both historical fixtures require:

1. Storage deserialization, immutable plan hash, and policy validation succeed.
2. The pre-fix clause diagnostic contains
   `historical_contract_version_not_supported`; the corrected diagnostic is
   empty.
3. `get_change_plan` returns status and approval state `prohibited`, prohibited
   lifecycle and bundle state, `approval_actionable=false`, empty required
   acknowledgements, no challenge, `apply_allowed=false`, and no next operation.
4. Unfiltered and prohibited-filtered listings include the record with
   `partial=false`; awaiting-approval filtering excludes it.
5. Health counts it as prohibited, excludes it from pending/actionable counters,
   reports zero projection failures, and reconciles the policy-bucket sum to
   total plans.
6. Startup, Ingress, challenge expiry, reconciliation, and handoff treat it as
   terminal/non-actionable.
7. Approval and apply continue returning `prohibited_change`.
8. Every path preserves byte-identical storage and unchanged event count/order.
9. No challenge, task, provider call, retry, or fallback occurs.

Negative contract-v2 fixtures must reject nonempty acknowledgements, granted or
consumed authority, any challenge, approval state changes, task existence,
provider dispatch or response evidence, successful apply, verification,
rollback, invalid plan or policy hashes, disallowed or malformed events, and
unexpected operation execution state. Task-storage failure must propagate.

## Inventory and health failure acceptance

With one valid plan and one individually unprojectable loaded plan, unfiltered
inventory must return the valid projection and a bounded failure item, total
failure count, and `partial=true`. Prohibited filtering retains recognized
prohibited plans and reports the failure separately. Awaiting filtering excludes
recognized prohibited plans and still reports the unclassified failure. Multiple
failures are deterministic and bounded. A systemic repository or task-storage
failure must fail the call, not become partial success.

Health must place every loaded record into one policy bucket. Projection
failures increment `projection_failed` and `projection_failure_count`, populate
a bounded warning, keep `policy_class_accounting_valid=true`, and remain absent
from all pending, challenge, external-approval, and actionable counters. The
reusable invariant is the policy-bucket sum equaling total plans; live numbers
are acceptance evidence, not a hardcoded source test.

## Beta 7 regression coverage

Retain response-evidence tests proving that HTTP success, including an empty
success, received HTTP error, and WebSocket success/error frames record receipt,
while a timeout or pre-response connection loss does not. A later readback
mismatch cannot erase a recorded response, and historical task evidence remains
byte-preserved. Require no source diff under the runtime `clients/` directory.

## Disposable and exact-image acceptance

The disposable upgrade contract must generate the exact Beta 6 contract-v2
record through shipped Beta 6 behavior, start Beta 9 over the same storage, and
exercise detail, unfiltered/prohibited/awaiting listings, health, startup,
Ingress, and handoff. Require prohibited/non-actionable projection, zero
projection failures, reconciled accounting, byte-identical storage, and no
challenge, task, provider call, or fallback. Existing standard, elevated,
current prohibited, same-administrator, different-administrator,
duplicate/no-redispatch, response-truthfulness, and physical non-actuation
scenarios remain required.

Exact-image lanes for `ha-mcp` 7.14.1 and 7.14.2 must each report 78 advertised
tools, 26 exact-admitted delegated reads, and zero missing reviewed reads,
schema mismatches, unreviewed tools, or fallback attempts. Stable packaging
must remain 1.1.2. The Engineering image must report `2.2.0-beta.9`, the exact
head SHA, and `dirty=false` for amd64, arm64, and arm/v7.

## Post-deployment live handoff

Live acceptance requires separate authorization. Begin with read-only smoke and
require Beta 9, the exact merge SHA, `dirty=false`, connected Home Assistant,
valid configuration, 25/23/48 plus 26/74 tools, task schema 1, authority version
3, exact upstream admission, healthy storage, and zero fallback.

Then read the two known deployment-regression records:

- `b2bdaad198ee4e82a33feb53f6d404f2`;
- `e07274ab13084d51b845423d6941eb8d`.

Both must return all eight prohibited/non-actionable fields and no execution
task. Unfiltered listing must succeed with `partial=false`; prohibited filtering
must include both; awaiting filtering must exclude both. In the unchanged live
environment, health should report 92 total plans, two prohibited decisions,
zero projection failures, reconciled policy accounting, and zero pending
counters. Reusable source tests must use invariant- or delta-based assertions.

Only after those checks pass may the separately authorized Beta 7 elevated Test
3 and separate Test 5 resume.

## Required validation

Run focused compatibility, storage, rehydration, policy, approval, task,
observability, Ingress, handoff, and disposable tests twice. Run two buffered
and one verbose full unittest discovery, Full and exact-head Evidence tiers,
compilation, metadata, YAML, dependency consistency, strict dependency audit,
secret, PowerShell, protected-path, whitespace, compatibility-registry, and
deterministic-regeneration gates. Run Docker-backed historical upgrade,
disposable, exact-image, stable/Beta packaging, and architecture lanes locally
only through the normal repository boundary; otherwise require exact-head CI.
