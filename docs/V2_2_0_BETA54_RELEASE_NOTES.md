# Engineering 2.2.0-beta.54 release notes

Beta 54 stages owner-authoritative exact execution for exact
`SET_INPUT_BOOLEAN_STATE` plans and typed updates of existing automations.
Engineering remains advertised as 2.2.0-beta.53 until separately authorized
promotion. Stable v1.1.2, 51 public tools, task schema 1, approval authority 3,
provider routing, fallback, workflows, containers, and deployment metadata are
unchanged.

## Exact execution versus consequence evidence

Fresh helper plans use `helper-dependency-risk-v13`. The plan now states
separately whether its exact provider execution contract is complete and
whether its downstream consequence evidence is complete. High, direct,
safety-critical, unknown, opaque, or incomplete downstream consequences remain
visible in plan review, risk, policy, audit, evidence fingerprints, and locks.
They no longer independently prohibit a technically exact operation.

Technical ambiguity still fails closed. Invalid targets or arguments, provider
or capability drift, stale state/configuration, hash or approval mismatch,
unbounded lock graphs, concurrency conflict, duplicate intent, inconclusive
readback, unverifiable completion, unsupported providers, arbitrary forwarding,
and fallback cannot be overridden by an owner acknowledgement.

## One exact owner decision

Fresh policy decisions use `f2-v2`. Exact high or uncertain operations remain
`elevated_admin` but require one `plan_approval`, bound to the plan and policy
hashes, provider contract, target, arguments, baseline, consequence evidence,
expiry, authenticated principal, and CSRF authority. Approval authority remains
version 3.

Historical f2-v1 decisions and helper-risk v2-v12 bindings remain readable and
hash-stable but cannot acquire current execution authority. Nonterminal old
plans require replanning; terminal and post-intent recovery records remain
immutable and readback-first. Existing f2-v1 two-step acknowledgement bundles
retain their original interpretation.

## Conservative execution controls

Consequence-incomplete helper plans retain exact helper and availability locks,
helper reload and exact dependency stability locks, the unconditional shared
stability fence, known downstream automation locks, the unconstrained
dependency guard, and conservative custom-template reload locking when needed.
An unrepresentable lock graph is a technical refusal.

Final preflight refreshes target, provider, dependency, and consequence evidence
while holding the full fence. Any approved evidence-fingerprint change requires
a fresh plan and owner decision. Durable intent, at-most-once dispatch,
authoritative readback, verification, audit, and recovery behavior are
unchanged.

## Existing automation updates

An exact typed update of an existing automation may now be owner-actionable even
when its stored behavior can turn off a light or switch, close a cover, invoke a
safety-relevant service, or contain unresolved future consequence semantics.
Those facts remain high/elevated or unknown disclosures. Exact target and
normalized configurations, validation, bounded diff, optimistic baseline,
typed provider attribution, complete locks, one-dispatch ownership,
authoritative reread, and exact verification remain mandatory.

Beta 54 does not add automation creation/deletion, generic service forwarding,
registry writes, arbitrary YAML/filesystem writes, shell access, a new public
tool or input, a provider, route, or fallback.

## Release boundary

`.release/next-version` stages exactly `2.2.0-beta.54`; advertised metadata
remains Beta 53. This change does not materialize, merge, publish, deploy,
access live Home Assistant, approve/apply a plan, or run HAMCP-089. Those remain
separate authority boundaries after exact-head validation and review.
