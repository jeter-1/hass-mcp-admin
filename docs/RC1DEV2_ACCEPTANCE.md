# 2.0.1-rc1-dev2 acceptance contract

Version: `2.0.1-rc1-dev2`

Status: development-candidate verification procedure only; not published,
deployed, or accepted

Scope is limited to repository and disposable-environment validation. Access to
a household Home Assistant, an upstream change, publication, release tagging,
merge, and deployment are outside this contract.

## Provenance and deterministic evidence

1. Require source ancestry from accepted baseline
   `2bdbdee51950bcc54635231001595f01a0eb0ba3`.
2. Validate the two exact registry entries and immutable source/image evidence
   recorded in the release notes.
3. Capture each exact image twice through reviewed protocol `2025-03-26`;
   require deterministic normalized artifacts and fingerprints.
4. Require 78 `unchanged_exact` comparison results and zero metadata-only,
   compatible, incompatible, new, removed, renamed, or classification-review
   results for the 7.14.1-to-7.14.2 comparison.
5. Reject duplicate versions, conflicting digests, incomplete contracts,
   unknown classifications, mismatched policy hashes, and generated
   `candidate_unapproved` entries.

## Admission, switching, and negative reachability

For both reviewed releases:

- require exact `ha-mcp` identity, exact version entry, supported protocol, 78
  advertised tools, catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
  26 reviewed automatic reads, and exact per-tool input, description,
  annotation, output, and runtime fingerprints;
- require 41 Engineering plus 26 delegated tools for 67 total after complete
  admission;
- prove one changed read is quarantined without removing other exact reads,
  new tools remain unreviewed and unavailable, and removed tools are reported;
- prove write, physical-action, mixed, prohibited, and unsupported tools are
  not registered;
- prove unknown versions fail closed even with a copied known catalog;
- prove version switching atomically replaces the dynamic registry and rollback
  to 7.14.1 restores its exact read set without an Engineering rebuild;
- prove upstream outage, recovery, reprobe, same-version drift, call-time
  revalidation, stale-route cleanup, and zero fallback; and
- validate the dashboard attestation independently, retaining `list_only=true`,
  exact URL-path reads, `include_screenshot=false`, no preference write, no
  screenshot, no mutation, and no generic mixed-tool route.

## Results, audit, and diagnostics

Require unchanged success and partial-search envelopes. Revalidate invalid
search input, missing state, missing automation, missing label, optional
capability unavailable, authentication, connection, timeout, internal
provider failure, hostile error text, redaction, audit attribution, health
counter semantics, and zero fallback.

Health and capability output must report the observed upstream version, all
locally reviewed versions, selected entry, source/image/protocol evidence,
comparison status, exact admitted and quarantined counts, missing reads, new
unreviewed tools, mismatch reason counts, dashboard attestation, last compatible
version, reconciliation state, and a truthful operator action.

For `ha_get_entity`, exercise a valid registry entity and a guaranteed missing
registry entity against both exact images. Until upstream supplies a stable
not-found discriminator, require the missing case to remain bounded
`provider_error`/`upstream_error`, without raw prose or credentials, with
operational accounting and zero fallback. Do not reclassify based on English
substrings.

## Required local and CI gates

Run focused registry, gateway, dashboard, observability, error-normalization,
capture/diff, and exact-image tests. Then run the complete Python suite,
compilation, strict dependency audit, metadata, YAML, secret scan, PowerShell,
protected-path, whitespace, Evidence, disposable Home Assistant, Engineering
image, retired stable-v1 packaging, amd64, arm64, and arm/v7 no-push gates.

The exact-image job must remain a digest-pinned matrix for both 7.14.1 and
7.14.2. It must not pull `latest`, publish an image, create a tag, or use live
Home Assistant credentials. Do not update expected fingerprints merely to make
a gate pass.

Confirm public schemas, Engineering tool registrations, policy classifications,
the 41-plus-26 composition, exact admission, governance, audit, no-fallback
enforcement, and historical stable-v1 files outside scope remain unchanged.

## Later operator-controlled runtime acceptance

Do not execute these steps as part of source review:

1. Deploy the reviewed Engineering image while upstream remains on exact
   7.14.1 and verify its selected registry entry, 26 reads, 67 total tools,
   dashboard provider, governance, audit, and zero fallback.
2. Preserve that Engineering image and exact upstream image as rollback points.
3. Update upstream to the exact reviewed 7.14.2 digest.
4. Verify identity, version, source, image, protocol, 78-tool catalog,
   fingerprint, selected registry entry, 26 reads, zero quarantine, zero
   generic writes, and zero fallback.
5. Exercise normal state/entity/history/configuration reads, invalid input,
   missing state/configuration, optional capability, bounded partial search,
   dashboard reads, and the documented `ha_get_entity` limitation.
6. Verify governance and audit persistence, restart Engineering, and repeat
   identity and admission checks.
7. If any boundary fails, restore exact 7.14.1 and verify that the same
   Engineering image automatically readmits its reviewed contract after fresh
   reconciliation.
