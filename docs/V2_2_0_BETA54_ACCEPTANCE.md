# Engineering 2.2.0-beta.54 acceptance

Beta 54 stages the runtime implementation of ADR-022 on exact base
`4cb6fcac9f6c94f64110ae5d467787935d234d26`. Engineering continues to
advertise 2.2.0-beta.53; stable remains 1.1.2. This document is source and
promotion-candidate authority only. Materialization, merge, publication,
deployment, live Home Assistant access, plan approval or application,
HAMCP-089, and the bathroom-vanity change remain outside this boundary.

Public Engineering tools remain 51, task schema remains 1, approval authority
remains 3, and provider fallback remains absent. Beta 54 adds no public input,
tool, provider, route, fallback, workflow, container, deployment metadata, or
stable-v1 change.

## Falsification authority

The immutable sanitized replay remains
`tests/fixtures/dependency/hamcp089_beta52_standard_helper_replay_v1.json`,
with SHA-256
`144e194992f3d50d72cd978b9975647c9492c0f2056d6b33eb11360c3db831bd`
and self-fingerprint
`c501621df35e5f3ee2c44528b87bd56b58dcbfa4bb7983228647caa48da52a22`.
No raw capture is used.

Before the policy change, exact Beta 53 source reproduces the sanitized
standard-helper case with zero exact dependencies, 24 target-capable opaque
obligations, two downstream profiles, source split 22/2, path split 14/10,
incomplete consequence evidence, unknown physical consequence, high risk,
`elevated_admin`, non-actionable approval, conservative downstream locks, and
zero provider dispatch. Exact safety-relevant existing-automation updates also
remain prohibited or require consequence-only duplicate acknowledgement.
Existing stale-state, provider, approval, drift, lock, dispatch, verification,
and recovery refusals remain the negative control.

## Two authority dimensions

Fresh helper bindings use `helper-dependency-risk-v13` and persist canonical,
bounded, sorted fields that distinguish:

- `execution_contract_complete` and `execution_block_reason_codes`, which
  describe whether the exact provider operation can execute safely; and
- `consequence_evidence_complete` and
  `consequence_uncertainty_reason_codes`, which describe the limits of
  downstream household-effect analysis.

`owner_decision_required` records when material or uncertain consequences need
an explicit decision. `coverage_complete`, `evidence_complete`,
`semantic_precision`, `physical_consequence`, obligation/profile diagnostics,
lock projections, and all existing selector evidence remain truthful. These
values and their evidence fingerprint are plan-, policy-, approval-, and
preflight-bound.

Consequence evidence does not independently prohibit an otherwise exact
operation; it informs the owner's bound decision.

For v13, `execution_eligible` follows the technical execution contract. Opaque
obligations, incomplete action semantics, incomplete label or registry
membership, dynamic downstream behavior, and high, direct, safety-critical, or
unknown consequence do not independently defeat an exact helper dispatch.
They remain high/elevated review evidence and require an owner decision.

Invalid targets or arguments, missing or drifted provider contracts, unreadable
current state, stale baselines, hash or approval mismatch, inability to build a
bounded lock graph, unsafe concurrency, missing durable intent, duplicate
dispatch, inconclusive readback, verification failure, unsupported providers,
arbitrary forwarding, and fallback remain hard stops. Owner acknowledgement
cannot override them.

## Policy and approval

Fresh plans use `f2-v2`. Exact owner-authoritative helper operations and exact
typed updates of existing automations use one authenticated, external owner
decision bound to the exact plan and policy hashes, provider identity and
contract, target and arguments, current-state/configuration baseline,
consequence evidence fingerprint, expiry, principal, and CSRF authority.
Their required acknowledgement tuple is exactly `plan_approval`.

Low/no-consequence operations remain `standard_admin`. Material or uncertain
consequences remain high and `elevated_admin`, including direct and
safety-critical automation effects; they are not relabeled as harmless. The
second elevated-risk acknowledgement remains present only when the stored
historical or non-owner-authoritative acknowledgement tuple requires it.

Approval authority remains version 3. Rejection invalidates authority without
dispatch. Planning, review, pagination, challenge creation, and approval issue
no provider write.

Persisted `f2-v1` policy decisions and helper-risk v2-v12 bindings remain
readable and hash-verifiable. Terminal records remain immutable. A nonterminal
old plan projects `policy_replan_required` or helper replanning and cannot gain
v13/f2-v2 authority. No historical plan is recomputed, migrated, or silently
upgraded. Post-intent recovery remains authoritative-readback-first and never
redispatches.

## Helper-state acceptance

A fresh complete, consequence-free helper plan is exact, low,
`standard_admin`, actionable, and requires `plan_approval`. The sanitized
24-obligation/two-profile case is technically exact but consequence-incomplete,
unknown/high/`elevated_admin`, actionable, and also requires only
`plan_approval`. The seven-profile guest-mode positive control remains
safety-critical/high/`elevated_admin` and actionable.

Every actionable helper plan has a complete bounded lock graph. It contains the
exact helper lock, Home Assistant availability, matching helper reload lock,
the exact helper-dependency stability lock, and the unconditional shared
unconstrained stability fence. Known bounded downstream automation locks remain
present. External-template evidence, or bounded uncertainty that can include
it, adds the custom-template reload lock. Malformed or unbounded resource
identities never enter the graph; graph overflow is technical execution failure.

Final preflight occurs while the complete fence is held. It rereads provider
capability and target state, refreshes dependency/consequence evidence, requires
v13/f2-v2, and requires exact plan, policy, provider, target, arguments,
baseline, consequence fingerprint, approval, and expiry matches before durable
intent. Any evidence change after approval—including apparently safer
consequence evidence—requires a fresh plan and decision. At most one dispatch
can occur.

## Existing-automation update acceptance

Beta 54 applies f2-v2 only to typed updates of existing automations. The exact
existing target, normalized current and proposed configurations, bounded diff,
typed provider, optimistic baseline, Home Assistant validation, lock set,
durable dispatch ownership, authoritative reread, and verification criteria
must all be established.

A synthetic bathroom-vanity restart-reconciliation control retains its startup
trigger, bounded delay, exact presence conditions, exact vanity-switch-off
action, direct consequence and elevated disclosure. One owner decision is
sufficient. Successful execution performs one typed configuration write,
rereads the exact automation, and reports `succeeded_verified`; duplicate apply
does not write again. A dynamic future automation effect remains unknown and
elevated but is owner-actionable only while the configuration write itself is
exact and verifiable.

Creation, deletion, generic services, registry writes, arbitrary YAML or
filesystem writes, shell access, new providers, and fallback remain outside
Beta 54.

## Validation and release boundary

Acceptance requires the focused Beta 54 helper, policy, approval, automation,
locking, drift, refusal, and recovery controls; Beta 37-53 helper compatibility;
configuration and historical-policy suites; governance persistence and
observability; operational/configuration F3 and recovery; complete discovery;
Fast Instructions and Validation; protected Full; clean-head Evidence; isolated
promotion-candidate validation; compilation and data/shell parsing; dependency
and vulnerability checks; secret and whitespace checks; stable-v1 comparison;
and exact public/tool/provider/fallback/workflow/container/deployment
comparisons.

The staged release is exactly `2.2.0-beta.54`. `config.yaml` remains
2.2.0-beta.53 until a separately authorized promotion materializes the
candidate. Exact-head code and security review must independently verify that
no technical-integrity refusal was reclassified as consequence uncertainty,
that v2 cannot authorize v1 plans, and that no provider, fallback, duplicate
dispatch, recovery, schema, stable, workflow, container, or deployment boundary
expanded.

This staged source does not materialize, promote, publish, deploy, or mutate a
live Home Assistant system.

## Post-deployment boundary

Deployment and live acceptance are separate work. A future authorized sequence
must first verify the exact Beta 54 runtime, 51 tools, task schema 1, approval
authority 3, exact upstream admission, zero fallback, F3 health, and clean
runtime state. Planning-only acceptance then creates fresh v13/f2-v2 standard
and guest-mode plans and traverses their persisted evidence without dispatch.
Only a later explicit mutation authorization can run the reversible HAMCP-089
two-dispatch canary. The bathroom-vanity update requires its own later fresh
plan and authorization.
