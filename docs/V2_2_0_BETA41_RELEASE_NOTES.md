# Engineering 2.2.0-beta.41 release notes

Beta 41 stages the HAMCP-089.R1 correction for target-specific helper-risk
discrimination. Beta 40 remains the advertised add-on version until a separate
protected promotion. This pull request performs no live acceptance, canary,
promotion, release, or deployment.

## Target-specific dependency outcomes

Helper governance now projects each authoritative dependency obligation against
the exact helper before aggregating downstream consequence. The projection
uses complete evidence in a fail-closed order:

- Coverage failure takes precedence over candidate matches, exclusions, and
  neutrality.
- A complete finite candidate set either establishes an exact dependency or
  proves that the target is excluded.
- Complete entity-domain evidence that excludes `input_boolean` proves the
  exact helper cannot be selected, including for an otherwise opaque terminal.
- Proven non-entity template content remains dependency-neutral.
- Missing or incomplete candidates or domains, arbitrary run-variable
  overrides, helper-capable dynamic selectors, clipped evidence, and
  contradictory provenance remain opaque or non-actionable.

This prevents unrelated opaque safety-critical automations from elevating every
helper when complete candidate or domain evidence proves the target impossible.
It does not equate zero static references with safety, eliminate dynamic
opacity globally, or weaken conservative treatment of genuinely arbitrary
entity selection.

## Governance discrimination

A helper with complete evidence and no actual or possible consequential
dependency can now retain physical consequence `none`, risk `low`, and policy
class `standard_admin`. An exact dependency on a safety-critical automation
continues to produce physical consequence `safety_critical`, risk `high`, and
policy class `elevated_admin`.

An arbitrary selector capable of resolving to the helper remains relevant and
therefore elevated or non-actionable according to evidence completeness.
Incomplete inventory, stale evidence, overflow, clipping, and configuration
drift remain fail-closed. Only automation profiles relevant to the exact helper
participate in physical-consequence aggregation.

## Helper-risk model v4

New helper plans bind `helper-dependency-risk-v4`, including the corrected
target outcome in deterministic fingerprints, lock projection, and approval
drift protection. Beta 40 `helper-dependency-risk-v3` bindings remain readable
for compatibility and review, but cannot authorize approval, lock acquisition,
provider dispatch, or mutation. They require fresh planning under v4.

Fresh v4 plans preserve exact and conservative helper locks, fenced
pre-dispatch dependency readback, duplicate suppression, provider evidence, and
no-fallback behavior. This release adds no provider call, helper operation, or
write path.

## Compatibility and security

Stable v1.1.2 is unchanged. Public schemas, MCP registration, the registered
tool count of 51, provider routing and admission, fallback behavior, workflows,
Dockerfile, repository metadata, and deployment configuration are unchanged.
The advertised Engineering version remains Beta 40 until separately promoted.

No direct-Home-Assistant bypass, arbitrary forwarding, service-call expansion,
new helper authorization, or storage-format change is introduced. No live Home
Assistant access, planning, approval, dispatch, canary, promotion, release, or
deployment is performed by this pull request.

## Validation and rollback

Acceptance requires the focused obligation-governance, exact-helper-state, and
F3 operational-adapter suites; broader helper-risk, policy, approval, locking,
entity-dependency, routing, and no-fallback coverage; metadata and isolated
promotion-candidate validation; the protected Full and Evidence gates; and all
required exact-head CI jobs.

Before promotion or deployment, rollback is a coherent revert of the Beta 41
feature and staging commits. Because the change performs no migration and
mutates no live state, no persisted-record repair or operational restoration is
required.
