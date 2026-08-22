# Engineering 2.2.0-beta.40 acceptance

Beta 40 is the corrective F3 orphan-child recovery candidate. This procedure
authorizes neither publication nor live Home Assistant access. Promotion,
deployment, and any live-system validation require separate decisions.

## Source and release gates

1. Resolve the feature base to current `main`. Require advertised Engineering
   `2.2.0-beta.39`, staged `2.2.0-beta.40`, stable `1.1.2`, and exact staged
   document resolution to these Beta 40 release notes and acceptance criteria.
2. Require public tool accounting to remain 51 and confirm no stable-v1,
   public-schema, MCP-registration, provider-route, workflow, Dockerfile,
   repository-metadata, or advertised deployment-version change in the feature
   diff.
3. Require the exact PR head and merge base to be recorded. Review the complete
   base-to-head diff and retain the PR as a draft until every gate below is
   green.
4. Validate the staged candidate with the repository promotion-candidate
   validator. The feature PR must leave the three advertised Engineering
   version files at Beta 39; only a separately authorized protected promotion
   may materialize Beta 40 and consume `.release/next-version`.

## F139-1: persisted lifecycle grammar and reachability

Closed classification must require nonempty matching lock evidence and the
exact writer-produced pre-intent event order and multiplicity. The accepted
post-intent grammar contains one dispatch, lock acquisition before completed
preflight, one completed-preflight event, and one intent after preflight. The
record fields and events must agree.

Construct authentic control records through the current writer API. For each
negative case, deliberately persist a contradiction only after the writer has
produced the authentic record. Cover empty intent locks, missing lifecycle
events, reordered events, duplicated events, and inconsistent lifecycle fields.
Each impossible terminal success must classify as corrupt.

Persist a multi-operation plan whose first child is such an impossible success.
Recovery must not project it as a satisfied dependency. Preparation, execution,
provider calls, and dispatch for the second child must remain unreachable, and
no public success projection may be created from the corrupt first child.

## F139-2: checkpoint bound and fresh-candidate priority

Persist 16 deferred post-intent children with retry backoff plus one fresh
post-intent child. One recovery pass must not raise, must never compose or
persist more than 16 checkpoint entries, and must not redispatch any child. The
fresh child must receive its timely observation/readback attempt even though
all retained entries are deferred.

Selection and displacement must be deterministic. A deferred entry omitted
from the bounded navigation checkpoint remains discoverable from authoritative
persistence and becomes eligible again when its retry time arrives; cursor and
checkpoint rotation must not strand it.

## F139-3: corrupt nonauthoritative navigation

Exercise startup and periodic recovery separately with malformed recovery
declaration cursor, active-recovery cursor, and active-recovery checkpoint
JSON. These three navigation artifacts are nonauthoritative. Each case must
emit a diagnostic, durably reset only the affected navigation evidence, and
continue bounded authoritative discovery. Listener startup and subsequent
recovery must remain reachable, with zero newly created dispatch authority and
zero provider calls unless an independently valid authoritative record permits
them.

Repeat the boundary with malformed authoritative task, manifest, and execution
records. Those records must continue to fail closed, remain unmodified, and
remain non-executable. Navigation recovery must not delete, quarantine, repair,
rewrite, or bypass them.

## F139-4: bounded audit idempotency

Replay multiple unaudited persisted events in one recovery batch while both
retained audit files exist. Instrument retained-log loading and require one
locked scan for the batch rather than one scan per event. Newly appended
idempotency keys must suppress duplicates within the same batch.

Inject append failure after a successful prefix. The persisted audit cursor may
advance only through that contiguous acknowledged prefix. A later retry must
remain idempotent and must append the unaudited suffix without duplicating the
prefix.

## F139-5: deterministic batch and time limits

Batch-cap and fairness tests must use an injected monotonic clock so an exact
16-transition assertion cannot depend on host speed. Prove that work beyond the
first bounded batch remains reachable in deterministic order. Test the real
five-second boundary separately with a controlled clock progression and avoid
an exact transition-count assumption in that time-limit test.

## Required validation

Run the following from the repository root and record exact counts and results:

```powershell
python -m unittest `
  tests.test_f3_execution_persistence `
  tests.test_f3_orphan_child_recovery `
  tests.test_execution_tasks `
  tests.test_f3_packaging_boundaries

.\scripts\check.ps1 -Tier Full `
  -AuthorizedProtectedPath @(
    'hass_mcp_engineering_beta/ha_mcp_engineering/',
    '.release/next-version'
  )

python scripts/validate_promotion_candidate.py --repo-root .
```

Required GitHub Actions must be green at the exact head. The validate job must
pass metadata validation and execute, rather than skip, the Engineering unit
tests and build. Require all supported disposable Home Assistant lanes,
immutable add-on acceptance lanes, and exact `ha-mcp` image lanes to pass.

Windows compatibility shims are application evidence only. Linux CI must
provide the authoritative `flock`, directory-open, directory-fsync, symlink,
fork, and POSIX permission evidence. Any skipped required CI step is a failed
acceptance gate.

## Compatibility, rollback, and non-actions

Confirm stable v1.1.2 is byte-for-byte unchanged from the merge base. Confirm
the public tool count, schemas, registration, provider boundaries, execution
event vocabulary, and absence of fallback remain unchanged. No live Home
Assistant or deployed MCP endpoint may be accessed for this acceptance.

Before promotion, rollback is deletion of the staged declaration and reversion
of the corrective source commit; no deployed system changes. Promotion and
deployment require separate authorization and their own rollback decision.
Because Beta 39 contains the defects corrected here, it must not be selected as
an automatic runtime rollback without an explicit fail-closed risk decision.

Do not merge, mark ready, approve, release, tag, publish, promote, or deploy as
part of satisfying this document. The next human decision after all required
checks pass is whether to mark PR #139 ready for review.
