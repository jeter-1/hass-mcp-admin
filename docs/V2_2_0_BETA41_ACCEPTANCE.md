# Engineering 2.2.0-beta.41 acceptance

Beta 41 is the staged HAMCP-089.R1 candidate for target-specific helper-risk
discrimination. Beta 40 remains the advertised Engineering version until a
separately authorized protected promotion. This procedure authorizes neither
live Home Assistant access nor a canary, promotion, release, or deployment.

## Source and release gates

1. Resolve the feature base to current `origin/main`. Require advertised
   Engineering `2.2.0-beta.40`, staged `2.2.0-beta.41`, stable `1.1.2`, and
   exact staged-document resolution to these Beta 41 acceptance criteria and
   release notes.
2. Require public tool accounting to remain 51. Confirm the complete feature
   diff changes no stable-v1 source, public schema, MCP registration, provider
   route or admission rule, fallback behavior, workflow, Dockerfile,
   `repository.yaml`, advertised Engineering version, or deployment metadata.
3. Require metadata validation and isolated promotion-candidate validation to
   pass at the exact feature head. The feature change must leave advertised
   version authorities at Beta 40; only a separately authorized promotion may
   materialize Beta 41 and consume `.release/next-version`.
4. Keep the pull request draft and blocked until exact-head CI and a fresh
   independent review are complete.

## Target-specific obligation projection

For each dependency obligation, require the exact helper projection to apply
the following precedence without hard-coded entities, automation identifiers,
blueprints, or domains:

1. A coverage failure or exceeded limit remains a coverage failure, even when
   diagnostic material retains a candidate matching the helper.
2. A complete finite candidate set containing the helper is an exact
   dependency.
3. A complete finite candidate set excluding the helper is a proven target
   exclusion.
4. Complete authoritative entity-domain evidence excluding `input_boolean` is
   a proven target exclusion, including when the source obligation is otherwise
   semantically opaque.
5. Proven non-entity content is dependency-neutral.
6. Every remaining incomplete, missing, contradictory, clipped, or arbitrary
   case remains opaque or becomes a coverage failure as required by the
   authoritative evidence contract.

Candidate and domain exclusions must be target-specific. They may prevent an
unrelated automation profile from contributing consequence only when the
evidence proves the exact helper impossible. Zero observed static references,
display names, and enabled state are not exclusion proof. Notification or log
text without entity-selection semantics must not contaminate helper risk.

## Governance controls

Exercise all three policy controls with deterministic repository fixtures:

- Negative control: complete coverage and evidence prove no actual or possible
  consequential dependency for the helper. Require target-relevant opacity
  zero, physical consequence `none`, execution eligibility true, risk `low`,
  policy class `standard_admin`, and apply allowed.
- Positive control: an exact helper dependency reaches a safety-critical
  automation action. Require physical consequence `safety_critical`, risk
  `high`, policy class `elevated_admin`, and no reachable standard-admin
  classification.
- Arbitrary selector control: an incomplete selector or
  `automation.trigger` run-variable override can supply an arbitrary helper.
  Require it to remain target-relevant and opaque, with elevated governance or
  execution refusal according to evidence completeness. It must never become
  standard merely because the current configuration did not name the target.

Coverage failures must remain non-actionable. Incomplete inventory, stale
evidence, overflow, clipping, contradictory provenance, or configuration drift
must not become complete coverage or evidence. Target relevance must be
calculated before downstream action-effect aggregation, so only relevant
automation profiles contribute physical consequence.

## Helper-risk model and execution authority

Require newly planned helper operations to bind
`helper-dependency-risk-v4`. The binding fingerprint must include the current
target-specific result and continue to protect dependency evidence, lock
projection, approval drift checks, and the fenced pre-dispatch read.

Persisted Beta 40 `helper-dependency-risk-v3` bindings remain readable for
review and compatible recovery. Readability is not execution authority. Such a
binding must require a fresh plan and must be rejected before approval, lock
authority, provider dispatch, or helper mutation. A fresh v4 plan must preserve
deterministic fingerprints, exact and conservative locks, approval binding,
duplicate suppression, and no-fallback evidence.

An edit that changes an obligation from excluded to relevant must change the
binding fingerprint and invalidate the prior plan. Unrelated display-only
changes must not manufacture authorization drift.

## Required validation

Run the focused helper-governance and execution-authority suites, the broader
helper-risk, policy, approval, locking, entity-dependency, routing, and
no-fallback gate, followed by the complete repository Full and Evidence tiers.
Declare both changed protected paths to the protected-scope gate:

```powershell
python -m unittest `
  tests.test_beta39_obligation_governance `
  tests.test_beta37_exact_helper_state `
  tests.test_f3_operational_adapter

$authorized = @(
  'hass_mcp_engineering_beta/ha_mcp_engineering/governance/helper_dependency.py',
  '.release/next-version'
)
./scripts/check.ps1 -Tier Full -AuthorizedProtectedPath $authorized
./scripts/check.ps1 -Tier Evidence -AuthorizedProtectedPath $authorized

python scripts/validate_addon_metadata.py --repo-root . --base-ref origin/main
python scripts/promote_next_release.py `
  --repo-root . `
  --validate-authority 2.2.0-beta.41
python scripts/validate_promotion_candidate.py --repo-root .
git diff --check origin/main...HEAD
```

Record exact test counts and distinguish local environment limitations from
passing evidence. Required exact-head GitHub Actions must be green. Linux and
container lanes remain authoritative for behavior unavailable on Windows.

## Compatibility, rollback, and non-actions

Stable v1.1.2, public schemas, MCP registration, the 51-tool count, provider
routing and admission, fallback policy, and existing helper write authority
must remain unchanged. This candidate must introduce no direct-Home-Assistant
bypass, arbitrary forwarding, service-call expansion, or additional write
reachability.

Before promotion or deployment, rollback is a coherent revert of the Beta 41
feature and staging commits. No storage migration or live restoration is
required because this feature changes projection and execution eligibility,
not persisted authoritative records or deployed state.

Do not mark ready, approve, merge, promote, tag, release, deploy, access live
Home Assistant, create or approve a live plan, dispatch, or run HAMCP-089 as
part of satisfying this document. Deployment acceptance and the eventual live
canary require separate explicit authorization.
