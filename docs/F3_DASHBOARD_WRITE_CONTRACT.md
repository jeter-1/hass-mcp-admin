# Governed existing-dashboard update contract

Status: approved bounded MVP implementation contract (2026-08-08)

This contract supersedes the Beta 17 execution deferral for one narrowly
defined operation: updating an existing Home Assistant storage-mode dashboard.
The deferral correctly identified that the upstream hash check and save are not
atomic. Josh has explicitly accepted that residual risk under the operator
condition below. The decision does not authorize any other dashboard write.

## Required completion boundary

Engineering may update one existing storage-mode dashboard identified by its
exact canonical `url_path`. The public proposal accepts a non-empty ordered
JSON Pointer patch of at most 16 `add`, `replace`, or `remove` operations.
Engineering compiles the patch locally against a complete exact preread and
binds the resulting full configuration to the governed plan.

The lifecycle is:

```text
exact preread and storage identity proof
  -> bounded local patch compilation and semantic diff
  -> immutable private artifact and governed plan
  -> external administrator approval
  -> task-bound F3 locks
  -> exact compatibility, identity, and stale-hash preflight
  -> durable dispatch intent
  -> exactly one ha_config_set_dashboard call
  -> exact reread and full-result verification
  -> truthful terminal or manual-review outcome
```

The proposal tool cannot write Home Assistant. Only `apply_change_plan` may
cross the write boundary after the existing approval, integrity, policy,
sequence, locking, and F3 checks pass.

## Exact upstream admission

The executable provider contract is admitted only for an exact reviewed
`ha-mcp` release, protocol `2025-03-26`, and the corresponding exact
compatibility entry. Current compiled support covers 8.1.1, 8.2.0, and 8.4.1.
The 8.4.1 entry is `ha-mcp-v8.4.1-7823b365`, source tag `v8.4.1`, commit
`701a7c26ac0e2309c7883a627d31873ab1510077`, and immutable image index
`sha256:7823b36587a6e62efed271f26f3f72380b49f47364e5385580584e7ab2c60722`.
The known existing-hyphenless-target pre-plan rejection remains exact to
8.1.1. Exact 8.2.0 accepts such a target only after its upstream registry read
proves the exact `url_path` already exists; new hyphenless creation remains
rejected.

Before planning or dispatch, Engineering requires the complete observed
upstream catalog to match the exact reviewed release. It separately requires:

- `ha_config_get_dashboard` with its reviewed read contract;
- `ha_get_skill_guide` with its reviewed automatic-read contract; and
- `ha_config_set_dashboard` with its reviewed persistent-write contract.

Unknown releases, compatibility entries, protocols, tools, schemas,
annotations, security metadata, output contracts, runtime contracts, or policy
classifications fail closed. There is no fallback and no generic forwarding.
The setter is not registered as a public or dynamically delegated tool.

## Transformation representation

The target path must match `^[a-z0-9_-]{1,256}$` and must resolve to exactly
one explicit inventory row with `mode == "storage"`. Title lookup, fuzzy
matching, case normalization, internal dashboard IDs, YAML dashboards,
implicit/unlisted defaults, absent targets, and ambiguous identities are
rejected. A missing target is never treated as permission to create it.

The patch representation is `f3-dashboard-json-pointer-patch-v1`:

- only canonical RFC 6901 pointers are accepted;
- the empty root pointer, wildcard and predicate selectors, executable
  expressions, `move`, and `copy` are prohibited;
- for `add` only, a final `-` appends to an existing array and a canonical
  numeric index from zero through the current array length inserts at that
  exact position;
- negative, leading-zero, nonnumeric, intermediate-`-`, and out-of-range array
  selectors are prohibited;
- `replace` and `remove` require an existing exact path;
- `add` requires an existing unambiguous parent and cannot overwrite;
- recursive semantic leaf changes must fit the 16-change approval projection;
- the result is built from a deep copy of the complete raw configuration; and
- undeclared structure, including custom-card fields, must remain equal.

The full input and result are private artifacts. The authenticated approval
review shows the complete bounded before/after value for every declared patch
operation, rendered as inert HTML-escaped JSON. That projection is hash-bound
to the exact preread, canonical patch, result, and plan; missing, malformed,
protected, tampered, or oversized projections cannot create or consume
approval authority. The ordinary MCP plan surface never exposes the complete
dashboard or compiled setter payload. Audit, health, and errors expose only
bounded paths, change kinds, counts, hashes, typed categories, and reviewed
provider identity.

One authenticated owner plan approval authorizes a fresh exact f2-v2 dashboard
operation. High or uncertain frontend consequence remains disclosed and
elevated, but classifier severity alone does not create a second acknowledgement.
Historical approval bundles retain their original interpretation.

## Canonical operational provider identity

Inventory, configuration, planning, approval, F3 locks, dispatch, and readback
use one immutable provider identity. It binds the compiled release entry,
upstream identity and version, protocol, source and image, exact catalog,
dashboard attestation and constraints, exact getter and setter contracts,
provider generation, fresh-session validation model, target storage identity,
and baseline configuration hashes. Lifecycle health and version strings cannot
be reconstructed into dashboard write authority.

The getter may remain usable when setter authority is unavailable. In that
state its bounded read metadata carries no actionable dashboard provider
identity, and governed planning fails before approval or setter dispatch.

## Exact provider evidence boundary

The sole mutating invocation is exact `ha_config_set_dashboard` with:

- exact `url_path`;
- the locally compiled full `config`;
- the immediately reread upstream `config_hash`;
- `MandatoryBPS=false`;
- `return_screenshot=false`; and
- an ephemeral best-practices receipt only when the reviewed upstream contract
  requires it.

The receipt is acquired only through fixed
`ha_get_skill_guide(skill="home-assistant-best-practices",
file="references/dashboard-guide.md")`. It is untrusted public read evidence,
not authorization. It is never persisted, logged, audited, or returned.

Caller Python, generated Python, metadata fields, title, icon, sidebar/admin
settings, `view_path`, screenshots, resources, unknown arguments, and repeated
setter invocation are prohibited. Provider success is evidence only and never
establishes verified success by itself.

## Non-atomic operator policy

The upstream implementation reads and validates `config_hash`, then performs a
separate save. Home Assistant does not provide an authoritative compare-and-save
or a lock covering every dashboard writer. Engineering's exclusive
`dashboard:<url_path>` lock coordinates Engineering operations only.

The operation is therefore explicitly `operator_accepted_non_atomic` and is
permitted only under this operating condition:

> Do not edit the target dashboard in Home Assistant UI or through another
> client while the approved Engineering task is executing.

The approver sees this condition and the residual lost-update risk. Exact
readback detects a conflicting final state but cannot detect an external edit
that was overwritten inside the read/save window when the final state happens
to equal the approved result. The server must never claim atomicity.

## F3 authority and locking

Persisted plan, approval, task, and F3 records remain authorization authority.
The immutable private dashboard artifact is hash-bound to the plan and is
revalidated before use. Missing, malformed, substituted, or tampered artifacts
fail closed before dispatch.

The operation requests:

- exclusive `dashboard:<url_path>` resource lock;
- shared `home_assistant:core` availability lock; and
- shared lock for the exact authoritative `ha-mcp` add-on identity.

Preflight while holding those locks repeats the exact inventory/configuration
read, compatibility-entry and provider checks, storage identity proof, and both
upstream and Engineering hash comparisons. Stale state prevents durable intent
and produces zero setter calls.

Durable dispatch intent is written before the setter call. Once intent exists,
timeout, transport loss, invalid provider response, or process loss is treated
as possibly dispatched. The setter is never retried. Recovery reacquires the
locks and performs readback only.

## Verification and outcomes

Verified success requires an exact complete reread proving:

- the same single storage-mode target;
- full configuration equality with the approved compiled result;
- the expected upstream and Engineering hashes;
- every declared patch effect; and
- no undeclared change.

A lost provider response followed by matching readback may become
`succeeded_verified`. A stale preflight is `failed_pre_dispatch` with no write.
An upstream failure after intent, incomplete readback, or a mismatching result
is reported as failed-post-dispatch or manual-review-required according to the
existing F3 state machine. None of those outcomes permits redispatch.

Rollback is unavailable. The upstream best-effort backup is not governed
rollback authority. Recovery is exact readback and classification only.

## Audit and observability

Audit and telemetry distinguish dashboard preread, best-practices read, setter
dispatch, and verification. Bounded records may include the operation, target
hash, compatibility entry, upstream identity/version, provider, dispatch
count/status, result category, verification status, and fallback count.

They must not contain raw configurations, changed values, best-practices keys,
unrestricted provider payloads or exception strings, tokens, or credentials.
Fallback must remain `none` and direct Home Assistant mutation is prohibited.

## Explicit exclusions

This contract does not authorize:

- dashboard creation or deletion;
- resource creation, update, or deletion;
- dashboard registry metadata, title, icon, sidebar, or admin-setting writes;
- screenshots or rendering;
- YAML-mode dashboards;
- arbitrary full-replacement proposals;
- Python or any caller-supplied executable transform;
- service calls, physical actions, reloads, or restarts;
- cross-dashboard transactions; or
- rollback.

Each is a separate product and security decision.

## Acceptance invariants

Acceptance must prove the positive path and the safety boundary:

- exact 8.1.1, 8.2.0, and 8.4.1 admission, one setter call, exact reread, and
  verified result;
- stale hash, missing target, YAML target, schema drift, contract drift,
  unreviewed release, invalid patch, and artifact tamper all fail closed;
- no setter dispatch occurs before durable intent;
- no setter redispatch occurs after any possibly-dispatched outcome;
- restart/recovery uses readback only;
- the setter and guide are absent from normal dynamic delegation;
- existing dashboard reads and admitted delegated reads remain unchanged; and
- stable v1, provider routing, zero fallback, approval authority, and unrelated
  F3 semantics remain unchanged.

## Historical decision record

Beta 17's deferral remains the accurate record of why this operation was not
previously executable. The 2026-08-08 decision changes only the conclusion that
atomic external-writer exclusion is mandatory: for this bounded home-scale
operation, Josh accepts the documented residual risk and operator condition.
