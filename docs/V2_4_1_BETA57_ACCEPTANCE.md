# Engineering 2.4.1-beta.57 acceptance

This contract covers HAMCP-276. An F3 apply whose plan predates a Home
Assistant Core authority change is refused before dispatch with the
truthful reason `core_authority_changed_since_plan`. An admitted helper
options plan created under current authority dispatches once and verifies
by exact readback. It follows 2.4.1-beta.56 and includes it.

See also the release notes:
[`V2_4_1_BETA57_RELEASE_NOTES.md`](V2_4_1_BETA57_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.57`, following 2.4.1-beta.56 (#340,
HAMCP-271). The release is materialized in all three version authorities
with no staged declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-276 direction:
  - admitted helper options must pass, dispatch once and verify by exact
    readback;
  - withdrawn or unadmitted profiles refuse with a truthful reason;
  - the same gap is fixed in other capabilities;
- the stop conditions: no weakening of Core authority, signed admission,
  approval binding, at-most-once dispatch or exact readback; no signed
  profile or fingerprint change; no persisted-format migration;
- the non-goals: HAMCP-274, HAMCP-271, HAMCP-275 (the retirement cause),
  new capabilities, signing and `ci.yml`;
- `2.4.1-beta.57` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **No new reachability.** No write, service call, reload, restart,
  route, provider, fallback or upstream dispatch is added. The change runs
  only after Core has refused a lease, and only selects which closed
  refusal code is reported.
- **Authority unchanged.** `CoreRuntime.acquire` remains the only
  decision. `plan_is_current` and `route_status` are read under the
  runtime lock and decide nothing. The stale-plan cutoff, lease
  semantics, approval binding and the entry check are unchanged.
- **Truthful classification.** The stale code is reported only when every
  requirement is available now and the plan predates the cutoff. A
  withdrawn profile keeps the generic code even when the plan is also
  stale, because a fresh plan is not the remedy.
- **Persisted compatibility.** No persisted format changes. The new code
  is a value of the existing closed child diagnostic and reason fields. An
  older reader drops unknown reason codes.

Required offline evidence, `tests/test_hamcp276_dispatch_authority.py`.
Every test uses the real `CoreRuntime` behind a test-signed registry (not
seq 15), the real F3 runtime, closed registry, Core wrappers and lease
code, and synthetic Home Assistant transports.
- **Reproduction.** A helper options plan is approved, Core authority is
  retired and restored at the full compiled set, and apply is refused
  `failed_pre_dispatch` with child code `core_authority_changed_since_plan`,
  reason check `core_authority_freshness`, `stale_plan_rejections` 1 and
  no Home Assistant call. The same change planned afterwards reaches
  `succeeded_verified` with one submit. The real lease refuses the restored
  plan and admits a current one.
- **Helper options end to end.** Change → `succeeded_verified` with
  `exact_readback_verified` and one submit → rollback plan → approve →
  apply → the options entry equals the original exactly → a no-op plan is
  refused with no submit. No fallback.
- **Withdrawn and unadmitted.** A withdrawn helper options profile refuses
  before any task with `pending_signed_registry_admission`; an unadmitted
  one cannot plan. No Home Assistant call.
- **Other capabilities.** Entity availability and configuration delete:
  a stale plan is refused with the new code and zero writes; a fresh plan
  writes once and verifies.
- **Matrix over the 25 activated identities** through the real wrappers
  and guard, each with a counting provider adapter:
  - admitted: authorized, one provider preflight;
  - its own operation profiles withheld: `dispatch_authority_unavailable`,
    no provider call; re-admitted: authorized;
  - plan predating a retirement and restoration:
    `core_authority_changed_since_plan`, no provider call; a current plan:
    authorized;
  - an unmapped identity: `core_requirement_unmapped`;
  - a current, admitted plan refused because Core's issued-lease capacity
    is exhausted: `dispatch_authority_unavailable`, no provider call and
    no stale rejection; after release: authorized;
  - the executor's guard returns no authority for a refusal that is not
    stale, the contract `test_core_update_continuity` pins.
- **Activated set.** 25 identities, 20 adapters. Every identity's
  requirements are compiled. The 18 compiled profiles no identity
  requires each have a pinned justification.
- **Vocabulary.** The code is reported verbatim, maps to its own check and
  matches the evidence pattern.
- **Unchanged suites.** The F3, Core readmission, HAMCP-249, helper
  options, configuration delete, availability, dashboard metadata and
  input select suites pass.
- **Mutation testing** on the changed code in a copied tree, with
  survivors reported.
- **The complete repository suite** in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.56 → beta.57 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

Deployment authorizes neither phase. Phase (a) is read-only. Phase (b)
writes and needs Josh's explicit approval before it starts.

### Phase (a): read-only

The architect checks with read-only tools.

1. **Version and health.** Engineering reports 2.4.1-beta.57. Core
   authority is admitted with `compatible_count` 31 and
   `fallback_count` 0. F3 reports `activated_capability_count` 25 and
   `registered_adapter_count` 20; the difference from 31 is the pinned
   non-F3 set in the release notes.
2. **Live cause, confirmed or not.** Read plan
   `244fb63094434f06af6a855691d714f0` and its execution task. Record the
   plan's `created_at` against the Core retirement (14:37:05Z) and
   restoration (14:37:26Z) events, and the Core counters
   `stale_plan_rejections` and `lease_failures`. The beta.56 task keeps its
   recorded `dispatch_authority_unavailable`; this release does not rewrite
   it. If the plan postdates the restoration, the diagnosis does not
   explain the live refusal: stop and record it in HAMCP-276.
3. **Dashboard metadata and scene.** Record the `created_at` of the plans
   that applied live and confirm they postdate the restoration.
4. **Smoke and regression.** The read-only smoke and regression checks
   pass.

### Phase (b): writes, only with Josh's approval

1. **Helper options.** Plan a fresh helper options change on a template
   helper Josh names, approve it and apply it. It reaches
   `succeeded_verified` with exact readback and one dispatch.
2. **Restore.** Roll it back through the governed rollback plan and verify
   the original options exactly.
3. **No-op.** Planning the original options again is refused.

Do not force a Core retirement on the live system to observe the new
code; the offline reproduction covers it. If a retirement happens on its
own while a plan is pending, record the apply's refusal: it must name
`core_authority_changed_since_plan` with no dispatch.

If a step fails, record the task, the child diagnostics, the Core health
counters and the audit events in HAMCP-276. Whether to roll back is
Josh's decision.
