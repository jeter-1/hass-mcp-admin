# Engineering 2.2.0-beta.41 acceptance

Beta 41 is the staged large-blueprint dependency-evidence candidate. This
contract authorizes neither promotion nor publication, deployment, live Home
Assistant access, helper mutation, or HAMCP-089.

## Source and release authority

1. Fetch GitHub authority and record the exact `origin/main`, feature head,
   merge base, branch, status, commit count, and diff count.
2. Require advertised Engineering `2.2.0-beta.40`, staged
   `2.2.0-beta.41`, stable `1.1.2`, and 51 registered tools.
3. Require exact staged document resolution to these Beta 41 release notes and
   acceptance criteria.
4. Confirm the feature diff does not change stable v1, public schemas, MCP
   registration, provider routes or admission, fallback, helper-write
   authorization, workflows, Dockerfile, `repository.yaml`, advertised version,
   or deployment metadata.
5. Validate the candidate in an isolated copy. The feature branch must leave
   `config.yaml`, `version.py`, and the metadata validator advertised at Beta
   40. Only a separately authorized protected promotion may materialize Beta
   41 and consume `.release/next-version`.

## Fixture provenance gate

Require the committed provenance declaration to identify:

- Home Assistant Core motion-light control at commit
  `53998d7710b4ac280658511c24a2a3e2651f9873`, blob
  `11900708b19a23e627ad46e20a58621be56bfc7e`, and SHA-256
  `e07ac35fae7270131f118da767b036e7f7776672077691d9fbcd026e5a7e3f9c`;
- Sensor Light 8.7 at immutable commit
  `c710556d02f6d37052efaf98c1baf5b4380e7d48`, blob
  `1563956f6ee7231b0037a98aa093d706424a0579`, and SHA-256
  `eb378157e063d531523a3658690b50dbeed61c75e8f9bad4a4222288496fe9eb`;
- a deterministic synthetic fixture containing no upstream source text, live
  observations, or real entity IDs.

The large witness must record 701,076 source bytes, 18,568 YAML nodes, 11,442
resolved values, maximum depth 31, 620 blueprint-input occurrences, and 140
root variables. The committed generator must record 10,648 resolved values,
140 root variables, 3,000 template obligations, and 4,500 padding values.

## Bounded extraction acceptance

Require explicit tests at boundary-minus-one, exact-bound, and
boundary-plus-one for every applicable source-size, YAML-node, YAML-depth,
resolution-node, analysis-node, context-member, terminal-count, and deadline
limit.

The accepted bounds are:

- source: 1 MiB;
- YAML: 32,768 nodes and depth 64;
- input resolution: 16,384 values and depth 64;
- configuration analysis: 16,384 values and depth 64;
- context evidence: 256 members;
- terminal evidence: 8,192 obligations;
- analysis deadline: 60 monotonic seconds.

The legitimate large fixture must complete inside these bounds. A genuine
over-bound fixture must produce explicit coverage failure without partial
success or silent truncation. The delegated public response limit must remain
unchanged.

## Obligation attribution acceptance

Every obligation identity must bind consumer automation, blueprint source path,
source SHA-256, configuration path, obligation type, relation, expression
fingerprint, and configuration fingerprint.

For two automations sharing the large fixture, require exactly:

- one source read in the scan;
- 3,000 blueprint obligations for each consumer;
- 6,000 total independently identified blueprint obligations;
- disjoint consumer identity sets;
- zero unresolved diagnostics;
- complete blueprint coverage and zero failed obligations.

For a shared source with one safe consumer and one unsupported dynamic consumer,
retain both consumers independently. The unsafe consumer must have an
attributable `unsupported_dynamic_entity_lookup` diagnostic while the safe
consumer remains attributable and unfailed.

Multiple simultaneous failures must have one unique internal diagnostic per
unresolved obligation, and the public failed-obligation count must equal the
authoritative diagnostic-record count. Coverage must remain incomplete while
any required obligation is unresolved.

## Cache, fingerprint, and public projection acceptance

Source parsing may be cached only within one scan. Every new scan must re-read
the source. A source SHA-256 change must change obligation identities and index
fingerprints and must never reuse the previous analysis as current evidence.
Consumer-specific resolution must not be cached across configurations.

When detailed compatibility results exceed the public cap, require the public
overflow count and fingerprint while retaining the complete authoritative
ledger within its declared internal bound. Public truncation must never make
coverage complete or helper execution eligible.

## Negative reachability and compatibility acceptance

Require deterministic offline tests proving:

- unreadable, partial, truncated, expired, drifted, or opaque source evidence
  never means no dependency;
- unsupported dynamic entity lookup remains fail-closed;
- non-entity template text does not contaminate helper risk;
- small-blueprint and target-specific Jinja behavior remain exact;
- Beta 39 selector semantics and mapping method/key precedence remain intact;
- only reviewed GET and registry-list operations are reached;
- fallback count remains zero;
- no Home Assistant mutation, provider write, service call, helper execution,
  or new dispatch authority is reached.

## Required repository validation

Run the focused Task 1 suite:

```powershell
python -m unittest `
  tests.test_beta39_obligation_ledger `
  tests.test_beta39_obligation_resource_bounds `
  tests.test_beta39_obligation_pipeline `
  tests.test_entity_dependency_analysis
```

Run the broader dependency/governance/routing selection covering obligation
metamorphic behavior, governance, fenced refresh, Home Assistant version
admission, Beta 37/38 helper dependency behavior, and canonical provider
routing. Record exact test counts and results.

Then run:

```powershell
python scripts/codex-context.py --format markdown
python scripts/validate_addon_metadata.py --repo-root . --base-ref origin/main
python scripts/validate_promotion_candidate.py --repo-root .
$authorizedProtectedPaths = @(
  '.release/next-version'
  'hass_mcp_engineering_beta/ha_mcp_engineering/dependency/models.py'
  'hass_mcp_engineering_beta/ha_mcp_engineering/dependency/extraction.py'
  'hass_mcp_engineering_beta/ha_mcp_engineering/dependency/provider.py'
  'hass_mcp_engineering_beta/ha_mcp_engineering/dependency/index.py'
)
.\scripts\check.ps1 -Tier Full `
  -AuthorizedProtectedPath $authorizedProtectedPaths
python scripts/pr-evidence.py --base origin/main --head HEAD --output .artifacts/pr-evidence.md
git diff --check
```

Run Evidence validation when the environment supports its complete contract.
Windows dependency shims, locking substitutes, path behavior, and other
environment limitations are not Linux or CI evidence. A failed Full or
Evidence step remains a failure and must not be summarized as a pass.

Require exact-head PR and push CI to pass completely before independent review.

## Pull-request evidence

The draft PR must record before/after bound behavior, fixture provenance, exact
shared-source obligation output, exact tests and counts, local Full/Evidence
limitations, CI links, public-versus-authoritative bounding, unchanged security
and compatibility surfaces, known limitations, and coherent rollback. State
explicitly that no live canary was run.

## Separately authorized post-deployment acceptance

After independent review, merge, promotion, and deployment are each separately
authorized, run read-only dependency-index generation. Both live Sensor Light
automation IDs must be independently accounted for; blueprint and overall
coverage must be complete; direct and indirect references to the intended
helper must both be zero; and evidence must be fresh and fingerprinted.

Stop without mutation if any condition fails. Only after that read-only result
may separate authorization for HAMCP-089 be requested.

## Rollback

Before promotion, remove or revert the complete staged Task 1 unit. After merge,
revert the pull-request merge as one coherent unit. Do not retain extraction
changes without attribution and regression coverage, or retain release staging
without the corresponding runtime unit. No persistence migration or live-system
repair is required.
