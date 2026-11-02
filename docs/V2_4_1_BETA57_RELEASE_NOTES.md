# Engineering 2.4.1-beta.57 release notes

Engineering 2.4.1-beta.57 (HAMCP-276) gives an F3 apply refused because
its plan predates a Home Assistant Core authority change a truthful
reason: `core_authority_changed_since_plan`. Before this release, the
refusal was reported as the generic `dispatch_authority_unavailable`,
with a detail pointing at Core admission, although every required profile
was admitted. It builds on 2.4.1-beta.56 (#340, HAMCP-271). No tool,
input schema, route, provider, signed profile, fingerprint, persisted
format, Home Assistant write or authority decision changes.

Acceptance: [`V2_4_1_BETA57_ACCEPTANCE.md`](V2_4_1_BETA57_ACCEPTANCE.md).

## Why

Live, an approved helper options plan
(`244fb63094434f06af6a855691d714f0`) failed at `dispatch_authority` with
zero dispatches, while seq 15 admitted
`core.config_entry_helper_options_operation` (31 compatible, 0 withheld).
Dashboard metadata and scene edit applied.

A reproduction with real wiring finds the cause. It uses the real
`CoreRuntime` under a test-signed registry, the real F3 runtime, closed
adapter registry, Core wrappers and lease code, and a synthetic Home
Assistant transport.

1. The plan's approval was requested at 14:36:04Z. Core authority
   retired at 14:37:05Z (cause tracked in HAMCP-275). The approval was
   granted at 14:37:10Z, with the elevated acknowledgement at 14:37:15Z.
   Core authority was restored at 14:37:26Z, and the apply ran at
   14:40:46Z. The architect confirmed this sequence.
2. Retirement and a changed restoration advance the Core
   material-change cutoff (`ha_core_readmission/runtime.py`: connection
   retirement, changed reconcile publication).
3. At apply, `CoreRuntime.acquire` compares the plan's `created_at` with
   that cutoff (`_plan_is_current_locked`). The plan predates it, so the
   lease is refused, `stale_plan_rejections` and `lease_failures` each
   increase by one, and nothing is sent.
4. The F3 Core wrappers (`_CoreVerificationAdapter.preflight` and
   `_CoreDispatchAuthorityGuard.acquire`) reported every refused lease as
   `dispatch_authority_unavailable`.

The reproduction's counters match the live ones (`lease_failures` 1). The
same change planned after the restoration applies, verifies by exact
readback and submits once. It is not a helper options binding defect, and
the gap is the same for every F3 family: all 25 activated identities pass
through the same wrappers.

The refusal itself is correct. A plan approved under one Core authority
generation must not bind a later one.

## The change

- New closed refusal code `core_authority_changed_since_plan`
  (`ha_core_readmission/routes.py`), accepted by the executor as a
  dispatch-authority refusal and mapped by the HAMCP-249 pre-dispatch
  vocabulary to the new check `core_authority_freshness`. Its fixed
  detail is: "Home Assistant Core authority changed after this plan was
  created, so its approval cannot bind it. Nothing was sent. Create and
  approve a fresh plan." It fits the HAMCP-249 160-character detail bound.
- `CoreRuntime.acquire_decision` (and `acquire_f3_decision`) is `acquire`
  with the same precedence and outcome, plus the reason for a refusal,
  decided under the same lock and authority snapshot: the plan was stale
  with every requirement admitted, the requirements were unavailable, or
  another refusal. It also returns the cutoff, admission state and
  generation it used. `acquire` and `acquire_f3` return its lease
  unchanged.
- Both F3 wrappers report from that decision and never re-read Core
  authority, so an expiry, retirement or capacity change after the
  decision cannot reclassify it. The stale code is named only for a
  stale refusal. Otherwise the generic code is kept:
  - a withdrawn or unadmitted profile, also when the plan is stale,
    because a fresh plan would not help;
  - a current, admitted plan that Core refuses for another reason, such
    as exhausted issued-lease capacity;
  - a Core runtime without `acquire_f3_decision`, or an unrecognized
    decision, which is a refusal.
- The executor's dispatch guard raises only the stale code. For any other
  refused lease it still returns no authority, which the executor reports
  as `dispatch_authority_unavailable` exactly as before.
- `core_requirement_unmapped` is unchanged.

The authority decision is unchanged: Core decides and classifies in one
step, and a granted lease is always returned.

## Activated identities and compatible profiles

The live `activated_capability_count` 25, `compatible_count` 31 and
`registered_adapter_count` 20 count different things:

- 25 is the number of F3 identities in the closed registry. Each
  identity's Core requirements are compiled profiles.
- Those identities require 13 Core profiles, including the shared
  `core.f3_mutation_verification`.
- The other 18 compiled profiles are not F3 operations by design:
  - 12 reads;
  - template semantics, configuration validation and dependency helper
    planning;
  - governance observability;
  - typed fan and power, which are direct typed tools (`control_fan`,
    `control_power`) with their own executor.
- 20 adapters are 18 configuration adapters, one shared operational
  adapter and one dashboard adapter.

A test pins each of the 18 with its justification. A new compiled profile
must be classified before the suite passes.

## Not changed

- The stale-plan cutoff, lease semantics and approval binding.
- A plan that predates an authority change still cannot apply. Letting it
  apply would require relaxing the cutoff or persisting Core authority
  material in plans (a persisted-format change). Both are outside this
  release and are Josh's decision.
- The cause of the 14:37:05Z retirement (HAMCP-275).
- The entry check: a runtime-only profile that is not admitted still
  refuses before any task with `pending_signed_registry_admission`.

## Rollback

Reinstall 2.4.1-beta.56. No persisted format changed. Under beta.56, the
HAMCP-249 vocabulary rejects the stored pre-dispatch summary of a task
recorded with `core_authority_changed_since_plan`, and the parent
projection shows the child's reason as `unclassified_pre_dispatch_refusal`.
The task outcome stays `failed_pre_dispatch`, the approval stays
unconsumed and nothing was dispatched.
